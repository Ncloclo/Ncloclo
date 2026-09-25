#!/usr/bin/env python3
"""
TrendGuard Bot — portefeuille de suivi de tendance sur Binance Spot.

Stratégie : trend_strategy.py (validée hors échantillon, voir
docs/TRENDGUARD_REPORT.md). Les décisions quotidiennes appellent EXACTEMENT
les mêmes fonctions que le backtest (update_positions / plan_entries).

Exécution : moteur V29.6 (v29.py) par paire — intention persistée avant
chaque ordre, frais en base, annulation sûre, reprise après crash.

Protection à deux niveaux :
  - stop de CLÔTURE (celui de la stratégie, évalué chaque jour après 00:00 UTC)
    → définit le risque de 1 % ;
  - stop CATASTROPHE sur l'exchange (STOP_LOSS), placé 1 × volatilité sous
    le stop de clôture et remonté avec lui → protège contre un krach entre
    deux clôtures, sans provoquer de sorties sur de simples mèches.

Commandes :
  python trendguard_bot.py run       # boucle continue (paper par défaut)
  python trendguard_bot.py once      # un seul cycle (cron)
  python trendguard_bot.py status    # état du portefeuille
  python trendguard_bot.py resume    # lève le kill-switch après audit
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional, Tuple

import ccxt
import pandas as pd

import trend_strategy as ts
import v29

DAY_MS = 86_400_000

# Actifs du backtest encore cotés en USDT sur Binance (XMR, FTT, EOS exclus).
LIVE_UNIVERSE_DEFAULT: Tuple[str, ...] = (
    "BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "TRX", "LINK", "LTC", "BCH",
    "XLM", "ETC", "ZEC", "DASH", "NEO", "XTZ", "ALGO", "DOT", "UNI", "AAVE",
    "ICP")

TG_ENV_DOC: Dict[str, str] = {
    "RUN_MODE": "paper | live",
    "ENABLE_LIVE_TRADING": "true pour autoriser le live",
    "LIVE_TRADING_CONFIRMATION": "Doit valoir I_UNDERSTAND_RISK",
    "BINANCE_API_KEY": "Clé API Binance (trading autorisé, retraits INTERDITS)",
    "BINANCE_API_SECRET": "Secret API Binance",
    "BINANCE_TESTNET": "true : Binance Spot testnet",
    "TG_UNIVERSE": "Actifs tradés (CSV de bases, cotées en USDT)",
    "TG_RISK_PCT": "Risque par trade en fraction d'equity (0.01 = 1 %)",
    "TG_MAX_POSITIONS": "Nombre maximum de positions simultanées",
    "TG_MAX_TOTAL_RISK": "Risque initial cumulé maximum (0.06 = 6 %)",
    "TG_KILL_DRAWDOWN": "Drawdown depuis le pic qui bloque les entrées (0.40)",
    "TG_PAPER_CAPITAL": "Capital initial du mode paper (USDT)",
    "TG_DB_FILE": "Base SQLite du bot",
    "TG_LOG_FILE": "Fichier de log",
    "TG_LOCK_FILE": "Fichier de verrou (une seule instance)",
    "TELEGRAM_TOKEN": "Token bot Telegram",
    "TELEGRAM_CHAT_ID": "Chat ID destination",
}


# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class GuardConfig:
    run_mode: str = "paper"
    quote: str = "USDT"
    universe: Tuple[str, ...] = LIVE_UNIVERSE_DEFAULT
    params: ts.TrendParams = ts.TrendParams()
    catastrophe_atr: float = 1.0        # stop exchange = stop clôture - k × vol
    stop_raise_min_pct: float = 0.01    # seuil de remontée du stop exchange
    decision_delay_sec: int = 120       # attente après 00:00 UTC
    loop_interval_sec: int = 60
    ohlcv_limit: int = 1000
    kill_drawdown: float = 0.40
    paper_capital: float = 10_000.0
    enable_live_trading: bool = False
    live_confirmation: str = ""
    binance_testnet: bool = False
    db_file: str = ""
    log_file: str = ""
    lock_file: str = ""

    def __post_init__(self):
        if self.run_mode not in ("paper", "live"):
            raise ValueError("RUN_MODE doit être paper ou live.")
        if self.run_mode == "live":
            if not self.enable_live_trading:
                raise ValueError("LIVE refusé: ENABLE_LIVE_TRADING=true requis.")
            if self.live_confirmation != "I_UNDERSTAND_RISK":
                raise ValueError("LIVE refusé: LIVE_TRADING_CONFIRMATION requis.")
        if "BTC" not in self.universe:
            raise ValueError("BTC doit faire partie de l'univers (régime).")
        if not (0 < self.kill_drawdown < 1):
            raise ValueError("TG_KILL_DRAWDOWN doit être dans ]0, 1[.")
        if self.catastrophe_atr <= 0:
            raise ValueError("catastrophe_atr > 0 requis.")
        self.params.validate()
        for name, default in (("db_file", f"trendguard_{self.run_mode}.db"),
                              ("log_file", f"trendguard_{self.run_mode}.log"),
                              ("lock_file", f"trendguard_{self.run_mode}.lock")):
            if not getattr(self, name):
                object.__setattr__(self, name, default)


def load_guard_config_from_env() -> GuardConfig:
    uni = tuple(a.strip().upper() for a in
                v29._env_s("TG_UNIVERSE", ",".join(LIVE_UNIVERSE_DEFAULT)).split(",")
                if a.strip())
    params = dataclasses.replace(
        ts.TrendParams(),
        risk_pct=v29._env_f("TG_RISK_PCT", 0.01),
        max_positions=v29._env_i("TG_MAX_POSITIONS", 8),
        max_total_risk=v29._env_f("TG_MAX_TOTAL_RISK", 0.06))
    return GuardConfig(
        run_mode=v29._env_s("RUN_MODE", "paper").lower(),
        universe=uni, params=params,
        kill_drawdown=v29._env_f("TG_KILL_DRAWDOWN", 0.40),
        paper_capital=v29._env_f("TG_PAPER_CAPITAL", 10_000.0),
        enable_live_trading=v29._env_b("ENABLE_LIVE_TRADING", False),
        live_confirmation=v29._env_s("LIVE_TRADING_CONFIRMATION", ""),
        binance_testnet=v29._env_b("BINANCE_TESTNET", False),
        db_file=v29._env_s("TG_DB_FILE", ""),
        log_file=v29._env_s("TG_LOG_FILE", ""),
        lock_file=v29._env_s("TG_LOCK_FILE", ""))


def build_guard_logger(log_file: str) -> logging.Logger:
    lg = logging.getLogger("trendguard")
    lg.handlers.clear()
    lg.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    lg.addHandler(fh)
    lg.addHandler(sh)
    lg.propagate = False
    return lg


# ══════════════════════════════════════════════════════════════════════
# BOT
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Slot:
    base: str
    symbol: str
    cfg: v29.Config
    ex: v29.ExchangeAdapter
    eng: v29.ExecutionEngine
    ctx: v29.BotContext


def last_closed_day(now: datetime, delay_sec: int = 0) -> str:
    """Date (UTC) de la dernière bougie journalière clôturée depuis au
    moins `delay_sec` secondes."""
    t = now - timedelta(seconds=delay_sec)
    return (t - timedelta(days=1)).date().isoformat()


class TrendGuardBot:
    STATE_KEY = "trendguard"

    def __init__(self, gcfg: GuardConfig, logger: logging.Logger,
                 exchange: Any, store: v29.Store, notifier: v29.Notifier):
        self.g = gcfg
        self.p = gcfg.params
        self.logger = logger
        self.exchange = exchange
        self.store = store
        self.notifier = notifier
        self.slots: Dict[str, Slot] = {}
        self.state: Dict[str, Any] = {}
        self.sleep = time.sleep
        self._now: datetime = v29._utcnow()

    @property
    def live(self) -> bool:
        return self.g.run_mode == "live"

    # ---------- Démarrage ----------

    def _slot_config(self, base: str) -> v29.Config:
        g = self.g
        return v29.Config(
            symbol=f"{base}/{g.quote}", base=base, quote=g.quote,
            run_mode=g.run_mode, enable_live_trading=g.enable_live_trading,
            live_confirmation=g.live_confirmation,
            binance_testnet=g.binance_testnet,
            use_oco=False, stop_only_protection=True,
            partial_exit_enabled=False, trailing_enabled=False,
            break_even_enabled=False, time_exit_enabled=False,
            htf_bias_enabled=False, btc_bias_enabled=False,
            btc_vol_filter_enabled=False, adaptive_enabled=False,
            fee_rate=self.p.fee, db_file=g.db_file, log_file=g.log_file,
            lock_file=g.lock_file, heartbeat_log_file=os.devnull)

    def boot(self) -> bool:
        self.exchange.load_markets()
        for base in self.g.universe:
            sym = f"{base}/{self.g.quote}"
            if sym not in self.exchange.markets:
                self.logger.warning(f"[BOOT] {sym} absent de l'exchange → ignoré")
                continue
            cfg = self._slot_config(base)
            ex = v29.ExchangeAdapter(cfg, self.logger, exchange=self.exchange)
            ex.sleep = self.sleep
            ex.load_markets()
            eng = v29.ExecutionEngine(cfg, self.logger, ex, self.store,
                                      self.notifier, v29.RiskEngine(cfg))
            eng.context_key = f"ctx:{sym}"
            ctx = self.store.load_context(key=eng.context_key) or v29.BotContext()
            ex.bind_paper_context(ctx)
            self.slots[base] = Slot(base, sym, cfg, ex, eng, ctx)
        if "BTC" not in self.slots:
            self.logger.critical("[BOOT] BTC/USDT indisponible (régime) → arrêt")
            return False
        self.state = self.store.get_kv(self.STATE_KEY) or {}
        self.state.setdefault("last_decision_day", None)
        self.state.setdefault("peak_equity", None)
        self.state.setdefault("halted", False)
        self.state.setdefault("halt_reason", None)
        self.state.setdefault("trades", [])
        self.state.setdefault("started_at", v29._utcnow_iso())
        if not self.live:
            self.state.setdefault("paper", {"cash": self.g.paper_capital,
                                            "holdings": {}})
        else:
            btc = self.slots["BTC"]
            if not btc.ex.self_test_conditional_orders():
                self.logger.critical("[BOOT] self-test ordres KO → arrêt")
                return False
            for s in self.slots.values():
                try:
                    v29.reconcile(s.ctx, s.cfg, s.ex, self.logger, s.eng)
                except Exception as e:
                    self.logger.critical(f"[BOOT] reconcile {s.symbol} KO: {e}")
                    return False
                self._save_slot(s)
        self._save_state()
        n_pos = len(self._holdings())
        self.logger.info(f"[BOOT] TrendGuard {self.g.run_mode.upper()} — "
                         f"{len(self.slots)} paires, {n_pos} position(s), "
                         f"risque {self.p.risk_pct*100:.2f} %/trade")
        return True

    # ---------- Persistance ----------

    def _save_slot(self, s: Slot) -> None:
        self.store.save_context(s.ctx, self.g.run_mode, key=s.eng.context_key)

    def _save_state(self) -> None:
        self.state["trades"] = self.state.get("trades", [])[-500:]
        self.store.set_kv(self.STATE_KEY, self.state)

    # ---------- Positions (vue commune live / paper) ----------

    def _holdings(self) -> Dict[str, ts.Holding]:
        out: Dict[str, ts.Holding] = {}
        if self.live:
            for base, s in self.slots.items():
                p = s.ctx.position
                if not p.in_position:
                    continue
                out[base.lower()] = ts.Holding(
                    asset=base.lower(), qty=p.amount_held, entry=p.buy_price,
                    stop=p.soft_stop or p.sl_price,
                    high=p.highest_close or p.buy_price,
                    entry_date=p.opened_at, risk_quote=p.risk_quote_initial,
                    cost=p.cost_basis * p.amount_held)
        else:
            for a, h in self.state["paper"]["holdings"].items():
                out[a] = ts.Holding(a, h["qty"], h["entry"], h["stop"],
                                    h["high"], h["entry_date"],
                                    h["risk_quote"], h["cost"])
        return out

    def _write_back(self, holdings: Dict[str, ts.Holding]) -> None:
        if self.live:
            for a, h in holdings.items():
                s = self.slots.get(a.upper())
                if s and s.ctx.position.in_position:
                    s.ctx.position.soft_stop = h.stop
                    s.ctx.position.highest_close = h.high
        else:
            book = self.state["paper"]["holdings"]
            for a, h in holdings.items():
                if a in book:
                    book[a]["stop"] = h.stop
                    book[a]["high"] = h.high

    def _record_trade(self, trade: Dict[str, Any]) -> None:
        self.state.setdefault("trades", []).append(trade)
        self.logger.info(
            f"[TRADE] {trade['asset'].upper()} {trade['reason']} "
            f"pnl={trade['pnl']:+.2f} {self.g.quote} R={trade['r']:+.2f}")

    def _harvest_live_trade(self, s: Slot, n_before: int, reason: str) -> None:
        pf = s.ctx.portfolio
        if pf.stats_wins + pf.stats_losses > n_before and pf.last_trades:
            t = pf.last_trades[-1]
            self._record_trade({"asset": s.base.lower(),
                                "date": self._now.isoformat(),
                                "pnl": float(t.get("pnl", 0.0)),
                                "r": float(t.get("r", 0.0)),
                                "reason": t.get("reason") or reason})

    @staticmethod
    def _closed_count(s: Slot) -> int:
        return s.ctx.portfolio.stats_wins + s.ctx.portfolio.stats_losses

    # ---------- Cycle ----------

    def run_cycle(self, now: Optional[datetime] = None) -> None:
        now = now or v29._utcnow()
        self._now = now
        if self.live:
            self._maintain_live()
        else:
            self._maintain_paper()
        day = last_closed_day(now, self.g.decision_delay_sec)
        if self.state.get("last_decision_day") != day:
            self.daily_decision(now, day)
        self._save_state()

    def _maintain_live(self) -> None:
        for s in self.slots.values():
            if not (s.ctx.position.in_position or s.ctx.pending_order):
                continue
            before = self._closed_count(s)
            s.eng.resolve_pending(s.ctx)
            if s.ctx.position.in_position:
                px = s.ex.get_ticker()["last"]
                s.eng.maintain_protection(s.ctx, px)
            self._harvest_live_trade(s, before, "EXCHANGE_STOP")
            self._save_slot(s)

    def _maintain_paper(self) -> None:
        """Paper : simulation du stop catastrophe exchange en intrajournalier."""
        book = self.state["paper"]["holdings"]
        for a in list(book):
            h = book[a]
            s = self.slots.get(a.upper())
            if s is None:
                continue
            px = s.ex.get_ticker()["last"]
            if px <= h["disaster"]:
                self._paper_exit(a, min(px, h["disaster"]), "EXCHANGE_STOP")

    def _paper_exit(self, a: str, px: float, reason: str) -> None:
        book = self.state["paper"]
        h = book["holdings"].pop(a)
        proceeds = h["qty"] * px * (1 - self.p.fee - self.p.slippage)
        book["cash"] += proceeds
        pnl = proceeds - h["cost"]
        self._record_trade({"asset": a, "date": self._now.isoformat(),
                            "entry_date": h["entry_date"], "entry": h["entry"],
                            "exit": px, "pnl": pnl,
                            "r": pnl / h["risk_quote"], "reason": reason})

    # ---------- Décision journalière ----------

    def _market_snapshot(self, now: datetime, day: str
                         ) -> Tuple[Dict[str, Dict[str, float]], bool,
                                    Dict[str, float]]:
        now_ms = int(now.timestamp() * 1000)
        closes, vols = {}, {}
        for base, s in self.slots.items():
            try:
                df = s.ex.fetch_ohlcv_htf(s.symbol, "1d", limit=self.g.ohlcv_limit)
            except Exception as e:
                self.logger.warning(f"[DATA] {s.symbol} OHLCV KO: {e}")
                continue
            if df.empty:
                continue
            df = df[df["ts"].astype("int64") + DAY_MS <= now_ms]
            if df.empty:
                continue
            idx = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
            c = pd.Series(df["close"].astype(float).values, index=idx)
            closes[base.lower()] = c
            vols[base.lower()] = pd.Series(
                (df["volume"].astype(float) * df["close"].astype(float)).values,
                index=idx)
        if "btc" not in closes:
            raise RuntimeError("Clôtures BTC indisponibles : décision reportée")
        close = pd.DataFrame(closes).sort_index()
        volume = pd.DataFrame(vols).reindex(close.index)
        d = pd.Timestamp(day, tz="UTC")
        if d not in close.index:
            raise RuntimeError(f"Bougie du {day} absente : décision reportée")
        i = close.index.get_loc(d)
        snap: Dict[str, Dict[str, float]] = {}
        for a in close.columns:
            col = close[a]
            first_valid = col.first_valid_index()
            if first_valid is None:
                continue
            f = ts.asset_features(col.loc[first_valid:], self.p,
                                  volume[a].loc[first_valid:])
            f = f.reindex(close.index)
            row = f.iloc[i]
            if pd.isna(row["close"]):
                continue
            snap[a] = {k: float(row[k]) for k in
                       ("close", "vol", "prior_high", "mom", "age", "vol30")}
        bull = bool(ts.btc_regime(close["btc"], self.p).iloc[i])
        prices = {a: s["close"] for a, s in snap.items()}
        return snap, bull, prices

    def _equity_and_cash(self, prices: Dict[str, float]) -> Tuple[float, float]:
        if not self.live:
            book = self.state["paper"]
            mtm = sum(h["qty"] * prices.get(a, h["entry"])
                      for a, h in book["holdings"].items())
            return book["cash"] + mtm, book["cash"]
        bal = self.exchange.fetch_balance()
        total = bal.get("total") or {}
        free = bal.get("free") or {}
        eq = float(total.get(self.g.quote, 0.0) or 0.0)
        for base in self.slots:
            qty = float(total.get(base, 0.0) or 0.0)
            px = prices.get(base.lower())
            if qty > 0 and px:
                eq += qty * px
        return eq, float(free.get(self.g.quote, 0.0) or 0.0)

    def daily_decision(self, now: datetime, day: str) -> None:
        p = self.p
        snap, bull, prices = self._market_snapshot(now, day)
        holdings = self._holdings()
        exits = ts.update_positions(holdings, snap, bull, p)
        for a, reason in exits:
            holdings.pop(a, None)
            self._execute_exit(a, reason, prices.get(a))
        self._write_back(holdings)
        if self.live:
            self._raise_exchange_stops(holdings, snap, now)
        equity, cash = self._equity_and_cash(prices)
        peak = max(float(self.state.get("peak_equity") or 0.0), equity)
        self.state["peak_equity"] = peak
        self.state["last_equity"] = equity
        self.state["last_regime_bull"] = bull
        if not self.state.get("halted") and equity < peak * (1 - self.g.kill_drawdown):
            self.state["halted"] = True
            self.state["halt_reason"] = (f"Drawdown {(equity/peak-1)*100:.1f} % "
                                         f"> {self.g.kill_drawdown*100:.0f} %")
            self.logger.critical(f"[KILL] {self.state['halt_reason']} → entrées bloquées")
            self.notifier(f"🛑 TrendGuard : {self.state['halt_reason']}", critical=True)
        entries: List[Dict[str, Any]] = []
        if not self.state.get("halted"):
            eligible = {a: s for a, s in snap.items() if self._can_enter(a)}
            entries = ts.plan_entries(holdings, eligible, bull, equity, cash, p)
            for plan in entries:
                self._execute_entry(plan, equity, now)
        self.state["last_decision_day"] = day
        self._summary(day, bull, equity, exits, entries, prices)

    def _can_enter(self, a: str) -> bool:
        s = self.slots.get(a.upper())
        if s is None:
            return False
        if not self.live:
            return True
        c = s.ctx
        return not (c.position.in_position or c.pending_order
                    or c.orphan_balance or c.risk.halted)

    def _execute_exit(self, a: str, reason: str, close_px: Optional[float]) -> None:
        s = self.slots.get(a.upper())
        if not self.live:
            px = close_px if close_px else (
                s.ex.get_ticker()["last"] if s else None)
            if px:
                self._paper_exit(a, px, reason)
            return
        if s is None or not s.ctx.position.in_position:
            return
        before = self._closed_count(s)
        ref = s.ex.get_ticker()["bid"]
        s.eng.close_position(s.ctx, f"TREND_{reason}", ref)
        self._harvest_live_trade(s, before, reason)
        self._save_slot(s)

    def _raise_exchange_stops(self, holdings: Dict[str, ts.Holding],
                              snap: Dict[str, Dict[str, float]],
                              now: datetime) -> None:
        for a, h in holdings.items():
            s = self.slots.get(a.upper())
            vol = snap.get(a, {}).get("vol")
            if s is None or not s.ctx.position.in_position or not ts._finite(vol):
                continue
            target = h.stop - self.g.catastrophe_atr * vol
            p = s.ctx.position
            if target <= p.sl_price * (1 + self.g.stop_raise_min_pct):
                continue
            last = s.ex.get_ticker()["last"]
            if target >= last * 0.99:
                continue   # sortie gérée par le stop de clôture
            new_sl = s.ex.round_price(target, "down")
            s.eng._modify_stop(s.ctx, new_sl, int(now.timestamp() * 1000),
                               "TG_TRAIL")
            self._save_slot(s)

    def _execute_entry(self, plan: Dict[str, Any], equity: float,
                       now: datetime) -> None:
        a = plan["asset"]
        disaster = plan["stop"] - self.g.catastrophe_atr * plan["vol"]
        if disaster <= 0:
            disaster = plan["stop"] * 0.5
        if not self.live:
            book = self.state["paper"]
            book["cash"] -= plan["cost"]
            book["holdings"][a] = {
                "qty": plan["qty"], "entry": plan["entry"], "stop": plan["stop"],
                "high": plan["ref_price"], "entry_date": now.isoformat(),
                "risk_quote": plan["risk_quote"], "cost": plan["cost"],
                "disaster": disaster}
            self.logger.info(
                f"[ENTRY] {a.upper()} qty={plan['qty']:.6f} @ "
                f"{plan['entry']:.6f} stop={plan['stop']:.6f} "
                f"risque={plan['risk_quote']:.2f} {self.g.quote}")
            return
        s = self.slots[a.upper()]
        res = s.eng.enter_planned(
            s.ctx, plan["qty"], plan["ref_price"], sl_abs=disaster,
            tp_abs=plan["ref_price"] * 100,
            meta={"module": "trendguard", "soft_stop": plan["stop"],
                  "risk_per_unit": plan["risk_quote"] / plan["qty"],
                  "equity": equity, "eff_risk_pct": self.p.risk_pct * 100,
                  "score": int(plan["mom"] * 10)},
            candle_ts=int(now.timestamp() * 1000))
        if res == v29.EntryResult.OPENED:
            s.ctx.position.highest_close = plan["ref_price"]
            s.ctx.position.soft_stop = plan["stop"]
        self._save_slot(s)

    def _summary(self, day: str, bull: bool, equity: float,
                 exits: List[Tuple[str, str]], entries: List[Dict[str, Any]],
                 prices: Dict[str, float]) -> None:
        holdings = self._holdings()
        lines = [f"TrendGuard {day} — equity {equity:,.2f} {self.g.quote} "
                 f"— régime BTC {'HAUSSIER' if bull else 'BAISSIER (pas d entrée)'}"]
        for a, r in exits:
            lines.append(f"  ↘ sortie {a.upper()} ({r})")
        for pl in entries:
            lines.append(f"  ↗ entrée {pl['asset'].upper()} risque "
                         f"{pl['risk_quote']:.2f}")
        for a, h in holdings.items():
            px = prices.get(a, h.entry)
            lines.append(f"  • {a.upper():<5} {((px / h.entry) - 1) * 100:+6.1f} % "
                         f"stop {h.stop:.6g}")
        if self.state.get("halted"):
            lines.append(f"  🛑 {self.state.get('halt_reason')}")
        text = "\n".join(lines)
        self.logger.info("[DAILY]\n" + text)
        self.notifier(text, dedup_key=f"tg-daily-{day}")

    # ---------- Boucle ----------

    def run_forever(self) -> None:
        backoff = 5
        while _running:
            try:
                self.run_cycle()
                backoff = 5
                _sleep(self.g.loop_interval_sec)
            except ccxt.NetworkError as e:
                self.logger.warning(f"[CYCLE] réseau: {e}")
                _sleep(backoff)
                backoff = min(backoff * 2, 300)
            except Exception as e:
                self.logger.exception(f"[CYCLE] KO: {e}")
                _sleep(backoff)
                backoff = min(backoff * 2, 300)


_running = True


def _stop(sig, frame):
    global _running
    _running = False


def _sleep(seconds: float) -> None:
    end = time.time() + seconds
    while _running and time.time() < end:
        time.sleep(min(1.0, end - time.time()))


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def _build(gcfg: GuardConfig) -> TrendGuardBot:
    logger = build_guard_logger(gcfg.log_file)
    opts = {"enableRateLimit": True, "options": {"defaultType": "spot",
                                                 "adjustForTimeDifference": True}}
    key = os.environ.get("BINANCE_API_KEY", "").strip()
    secret = os.environ.get("BINANCE_API_SECRET", "").strip()
    if key and secret:
        opts.update({"apiKey": key, "secret": secret})
    exchange = ccxt.binance(opts)
    if gcfg.binance_testnet:
        exchange.set_sandbox_mode(True)
    store = v29.Store(gcfg.db_file, logger)
    notifier = v29.Notifier(os.environ.get("TELEGRAM_TOKEN", ""),
                            os.environ.get("TELEGRAM_CHAT_ID", ""), logger=logger)
    return TrendGuardBot(gcfg, logger, exchange, store, notifier)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="TrendGuard Bot (Binance Spot)")
    ap.add_argument("cmd", choices=["run", "once", "status", "resume", "docs"])
    args = ap.parse_args(argv)
    if args.cmd == "docs":
        for k, v in sorted(TG_ENV_DOC.items()):
            print(f"  {k:<28} {v}")
        return 0
    try:
        gcfg = load_guard_config_from_env()
    except ValueError as e:
        print(f"Configuration invalide : {e}", file=sys.stderr)
        return 2
    if args.cmd in ("status", "resume"):
        store = v29.Store(gcfg.db_file, logging.getLogger("trendguard.cli"))
        state = store.get_kv(TrendGuardBot.STATE_KEY) or {}
        if args.cmd == "resume":
            state["halted"] = False
            state["halt_reason"] = None
            state["peak_equity"] = state.get("last_equity")
            store.set_kv(TrendGuardBot.STATE_KEY, state)
            print("✅ Kill-switch levé (pic d'equity réinitialisé).")
        trades = state.get("trades", [])
        wins = [t for t in trades if t["pnl"] > 0]
        print(f"Dernière décision : {state.get('last_decision_day')}")
        print(f"Equity            : {state.get('last_equity')}")
        print(f"Pic               : {state.get('peak_equity')}")
        print(f"Halt              : {state.get('halted')} {state.get('halt_reason') or ''}")
        print(f"Trades clos       : {len(trades)} (gagnants {len(wins)})")
        if trades:
            print(f"R moyen           : {sum(t['r'] for t in trades)/len(trades):+.2f}")
        if "paper" in state:
            print(f"Paper cash        : {state['paper']['cash']:.2f} | positions : "
                  f"{', '.join(a.upper() for a in state['paper']['holdings']) or '-'}")
        store.close()
        return 0
    lock = v29.ProcessLock(gcfg.lock_file)
    lock.acquire()
    bot = _build(gcfg)
    try:
        bot.logger.info(f"TrendGuard — {gcfg.run_mode.upper()}"
                        f"{' TESTNET' if gcfg.binance_testnet else ''} — "
                        f"{len(gcfg.universe)} actifs, risque "
                        f"{gcfg.params.risk_pct*100:.2f} %/trade")
        if not bot.boot():
            return 1
        if args.cmd == "once":
            bot.run_cycle()
            return 0
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        bot.run_forever()
        return 0
    finally:
        bot.store.close()
        bot.notifier.close()
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
