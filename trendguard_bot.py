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
import math
import os
import re
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
    "TG_HEARTBEAT_MIN": "Intervalle du battement de cœur dans le journal (min, 0 = off)",
    "TG_MAX_CAPITAL": "Capital max géré par le bot en USDT (0 = tout le compte)",
    "TG_ALLOW_RECOVERY": "true : adopter les ordres du bot inconnus de la base (base perdue)",
    "TG_PAPER_CAPITAL": "Capital initial du mode paper (USDT)",
    "TG_DB_FILE": "Base SQLite du bot",
    "TG_LOG_FILE": "Fichier de log",
    "TG_LOCK_FILE": "Fichier de verrou (une seule instance)",
    "TG_HEALTH_MAX_AGE_SEC": "Âge max du dernier cycle réussi pour `health` (s)",
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
    heartbeat_min: int = 15
    max_capital: float = 0.0            # 0 = tout le compte
    allow_recovery: bool = False        # adopter les ordres du bot inconnus
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
        if self.max_capital < 0:
            raise ValueError("TG_MAX_CAPITAL doit être >= 0.")
        if not (0 < self.kill_drawdown < 1):
            raise ValueError("TG_KILL_DRAWDOWN doit être dans ]0, 1[.")
        if self.catastrophe_atr <= 0:
            raise ValueError("catastrophe_atr > 0 requis.")
        self.params.validate()
        for name, default in (
                ("db_file", os.path.join(v29.APP_DIR, f"trendguard_{self.run_mode}.db")),
                ("log_file", os.path.join(v29.APP_DIR, f"trendguard_{self.run_mode}.log")),
                ("lock_file", os.path.join(v29.APP_DIR, f"trendguard_{self.run_mode}.lock"))):
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
        heartbeat_min=v29._env_i("TG_HEARTBEAT_MIN", 15),
        max_capital=v29._env_f("TG_MAX_CAPITAL", 0.0),
        allow_recovery=v29._env_b("TG_ALLOW_RECOVERY", False),
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
        self._last_heartbeat = 0.0

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
            recovery_adopt_orders=g.allow_recovery,
            partial_exit_enabled=False, trailing_enabled=False,
            break_even_enabled=False, time_exit_enabled=False,
            htf_bias_enabled=False, btc_bias_enabled=False,
            btc_vol_filter_enabled=False, adaptive_enabled=False,
            fee_rate=self.p.fee, db_file=g.db_file, log_file=g.log_file,
            lock_file=g.lock_file, heartbeat_log_file=os.devnull)

    def boot(self) -> bool:
        try:
            self.exchange.load_markets()
        except Exception as e:
            self.logger.error(f"[BOOT] marchés indisponibles ({type(e).__name__}): "
                              f"{str(e)[:160]}")
            return False
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
            self.state.setdefault("start_equity", self.g.paper_capital)
        else:
            btc = self.slots["BTC"]
            if not btc.ex.self_test_conditional_orders():
                self.logger.critical("[BOOT] self-test ordres KO → arrêt")
                return False
            foreign = []
            for s in self.slots.values():
                try:
                    v29.reconcile(s.ctx, s.cfg, s.ex, self.logger, s.eng)
                except Exception as e:
                    self.logger.critical(f"[BOOT] reconcile {s.symbol} KO: {e}")
                    return False
                if s.ctx.risk.halt_reason == "UNKNOWN_BOT_ORDERS":
                    foreign.append(s.symbol)
                    continue                    # base inchangée : rien d'adopté
                self._save_slot(s)
            if foreign:
                msg = (f"Ordres du bot inconnus de cette base sur "
                       f"{', '.join(foreign)} : une autre instance de TrendGuard "
                       f"gère peut-être ce compte. Démarrage refusé. Base perdue ? "
                       f"Relancer une fois avec TG_ALLOW_RECOVERY=true.")
                self.logger.critical(f"[BOOT] {msg}")
                self.notifier(f"🛑 {msg}", critical=True, dedup_key="tg-foreign")
                return False
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
        self.state["realized_pnl_total"] = (
            float(self.state.get("realized_pnl_total", 0.0)) + float(trade["pnl"]))
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
        # Horloge réelle (et non `now`, simulé en rejeu) : sert au contrôle
        # de santé du conteneur.
        self.state["last_cycle_ts"] = time.time()
        self._save_state()
        self._heartbeat(now)

    def _heartbeat(self, now: datetime) -> None:
        """Une ligne de journal toutes les `heartbeat_min` minutes : le bot
        est visiblement vivant entre deux décisions quotidiennes."""
        every = self.g.heartbeat_min * 60
        if every <= 0 or time.time() - self._last_heartbeat < every:
            return
        self._last_heartbeat = time.time()
        try:
            holdings = self._holdings()
            prices: Dict[str, float] = {}
            for a in holdings:
                s = self.slots.get(a.upper())
                if s is not None:
                    prices[a] = s.ex.get_ticker()["last"]
            equity, _cash = self._equity_and_cash(prices)
            start = self.state.get("start_equity") or equity
            parts = [f"{a.upper()} {(prices.get(a, h.entry) / h.entry - 1) * 100:+.1f} %"
                     for a, h in sorted(holdings.items())]
            nxt = (datetime.combine(now.date() + timedelta(days=1),
                                    datetime.min.time(), tzinfo=now.tzinfo)
                   + timedelta(seconds=self.g.decision_delay_sec)) - now
            h_left, rem = divmod(int(nxt.total_seconds()), 3600)
            bull = self.state.get("last_regime_bull")
            regime = "?" if bull is None else ("HAUSSIER" if bull else "BAISSIER")
            self.logger.info(
                f"[HEARTBEAT] equity {equity:,.2f} {self.g.quote} "
                f"({(equity / start - 1) * 100:+.2f} %) | régime BTC {regime} | "
                f"{len(holdings)} position(s)"
                + (f" : {', '.join(parts)}" if parts else "")
                + f" | prochaine décision dans {h_left} h {rem // 60:02d}"
                + (" | 🛑 KILL-SWITCH" if self.state.get("halted") else ""))
        except Exception as e:
            self.logger.warning(f"[HEARTBEAT] indisponible : {e}")

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
        opened = v29._parse_iso(h["entry_date"]) or self._now
        self._record_trade({"asset": a, "date": self._now.isoformat(),
                            "days": (self._now - opened).days,
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
            return self._apply_capital_cap(book["cash"] + mtm, book["cash"], prices)
        bal = self.exchange.fetch_balance()
        total = bal.get("total") or {}
        free = bal.get("free") or {}
        eq = float(total.get(self.g.quote, 0.0) or 0.0)
        for base in self.slots:
            qty = float(total.get(base, 0.0) or 0.0)
            px = prices.get(base.lower())
            if qty > 0 and px:
                eq += qty * px
        return self._apply_capital_cap(
            eq, float(free.get(self.g.quote, 0.0) or 0.0), prices)

    def _apply_capital_cap(self, equity: float, cash: float,
                           prices: Dict[str, float]) -> Tuple[float, float]:
        """Plafond de capital (TG_MAX_CAPITAL) : le bot gère un « sous-compte
        virtuel » = plafond + ses gains et pertes, quel que soit le solde du
        compte. Le kill-switch porte alors sur ce capital, pas sur le compte."""
        cap = self.g.max_capital
        if cap <= 0:
            return equity, cash
        holdings = self._holdings()
        invested = sum(h.qty * prices.get(a, h.entry) for a, h in holdings.items())
        cost = sum(h.cost for h in holdings.values())
        bot_equity = (cap + float(self.state.get("realized_pnl_total", 0.0))
                      + invested - cost)
        return (max(0.0, min(equity, bot_equity)),
                max(0.0, min(cash, bot_equity - invested)))

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
        self.state.setdefault("start_equity", equity)
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
# REJEU PAPER SUR HISTORIQUE RÉEL
# ══════════════════════════════════════════════════════════════════════

class HistoricalExchange:
    """Exchange de rejeu pour le mode paper : sert des bougies journalières
    reconstruites à partir des clôtures historiques, en ne révélant JAMAIS
    une bougie non clôturée à l'instant simulé."""

    ORDER_TYPES = ["LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS",
                   "STOP_LOSS_LIMIT"]

    def __init__(self, close: pd.DataFrame, volume: pd.DataFrame,
                 quote: str = "USDT"):
        self.close = close
        self.volume = volume
        self.quote = quote
        self.symbols = {f"{a.upper()}/{quote}": a for a in close.columns}
        self.markets: Dict[str, Any] = {}
        self.now_ms = 0
        # Conversion indépendante de la résolution de l'index (ns/us/s) :
        # une erreur d'unité révélerait des prix futurs.
        self._ms = close.index.as_unit("ms").asi8.astype("int64")

    def set_now(self, now: datetime) -> None:
        self.now_ms = int(now.timestamp() * 1000)

    def load_markets(self):
        self.markets = {s: self.market(s) for s in self.symbols}
        return self.markets

    def market(self, symbol: str) -> Dict[str, Any]:
        return {"id": symbol.replace("/", ""), "symbol": symbol, "spot": True,
                "active": True,
                "limits": {"amount": {"min": 0.0}, "cost": {"min": 5.0}},
                "info": {"filters": [], "orderTypes": self.ORDER_TYPES,
                         "ocoAllowed": True}}

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        return f"{math.floor(float(amount) * 1e8) / 1e8:.8f}"

    def price_to_precision(self, symbol: str, price: float) -> str:
        return f"{float(price):.10g}"

    def _visible(self, symbol: str) -> pd.Series:
        a = self.symbols[symbol]
        mask = self._ms + DAY_MS <= self.now_ms
        return self.close[a][mask].dropna()

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d",
                    since: Optional[int] = None, limit: int = 1000):
        c = self._visible(symbol)
        v = self.volume[self.symbols[symbol]].reindex(c.index) \
            if self.symbols[symbol] in self.volume else None
        rows = []
        for d, px in c.iloc[-limit:].items():
            vol_usd = float(v.loc[d]) if v is not None and pd.notna(v.loc[d]) \
                else float("nan")
            rows.append([int(d.timestamp() * 1000), px, px, px, px,
                         vol_usd / px if px > 0 else 0.0])
        return rows

    def fetch_ticker(self, symbol: str) -> Dict[str, float]:
        c = self._visible(symbol)
        if c.empty:
            raise ccxt.BadSymbol(f"pas de cotation pour {symbol}")
        px = float(c.iloc[-1])
        return {"last": px, "bid": px, "ask": px}


def replay(data_dir: str, start: str, end: Optional[str] = None,
           capital: float = 10_000.0, verbose: bool = True,
           out=None) -> Dict[str, Any]:
    """Fait tourner le VRAI bot en mode paper, jour après jour, sur les
    clôtures historiques réelles (Coin Metrics)."""
    out = out or sys.stdout
    close, volume = ts.load_coinmetrics(data_dir, ts.DEFAULT_UNIVERSE)
    days = close.loc[start:end].index if end else close.loc[start:].index
    days = days[close["btc"].reindex(days).notna().values]
    if len(days) == 0:
        raise ValueError("Aucune donnée sur la période demandée.")
    hx = HistoricalExchange(close, volume)
    g = GuardConfig(run_mode="paper",
                    universe=tuple(a.upper() for a in close.columns),
                    paper_capital=capital, db_file=":memory:",
                    log_file=os.devnull, lock_file=os.devnull)
    lg = logging.getLogger("trendguard.replay")
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    store = v29.Store(":memory:", lg)
    bot = TrendGuardBot(g, lg, hx, store, v29.Notifier("", "", logger=lg))
    bot.sleep = lambda s: None
    hx.set_now(days[0].to_pydatetime() + timedelta(days=1, minutes=5))
    if not bot.boot():
        raise RuntimeError("Démarrage du bot impossible")
    curve = []
    last_month = None
    for d in days:
        now = d.to_pydatetime() + timedelta(days=1, minutes=5)
        hx.set_now(now)
        book = bot.state["paper"]
        before_h = set(book["holdings"])
        n_tr = len(bot.state["trades"])
        bot.run_cycle(now=now)
        px = close.loc[d]
        for t in bot.state["trades"][n_tr:]:
            if verbose:
                print(f"{d.date()}  ↘ VENTE  {t['asset'].upper():<5} "
                      f"{t['reason']:<13} PnL {t['pnl']:+9.2f} USDT "
                      f"({t['r']:+.2f} R)", file=out)
        for a in set(book["holdings"]) - before_h:
            h = book["holdings"][a]
            if verbose:
                print(f"{d.date()}  ↗ ACHAT  {a.upper():<5} @ {h['entry']:.6g} "
                      f"stop {h['stop']:.6g}  risque {h['risk_quote']:.2f} USDT",
                      file=out)
        eq = book["cash"] + sum(h["qty"] * float(px[a])
                                for a, h in book["holdings"].items())
        curve.append(eq)
        month = d.strftime("%Y-%m")
        if verbose and month != last_month and last_month is not None:
            print(f"── {last_month} clôturé : equity {curve[-2]:,.2f} USDT", file=out)
        last_month = month
    equity = pd.Series(curve, index=days)
    metrics = ts.compute_metrics(equity, bot.state["trades"])
    return {"equity": equity, "trades": bot.state["trades"],
            "holdings": bot.state["paper"]["holdings"], "metrics": metrics,
            "regime_bull": bot.state.get("last_regime_bull"),
            "last_day": str(days[-1].date()), "prices": close.loc[days[-1]]}


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


def set_env_var(path: str, name: str, value: str) -> None:
    """Écrit NAME=value dans le fichier .env (remplace la ligne active
    existante, sinon ajoute) ; fichier lisible par son seul propriétaire."""
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == name and not line.lstrip().startswith("#"):
            if not done:
                out.append(f"{name}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{name}={value}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.chmod(path, 0o600)


def clean_api_secret(raw: str) -> str:
    """Nettoie un secret collé (espaces, guillemets) et vérifie son format :
    Binance délivre des secrets HMAC de 64 caractères alphanumériques."""
    s = raw.strip().strip("\"'").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{64}", s):
        raise ValueError(f"format inattendu ({len(s)} caractères ; attendu : "
                         f"64 lettres et chiffres)")
    return s


def cmd_set_secret(env_path: str = ".env") -> int:
    """Saisie MASQUÉE du secret API (rien ne s'affiche à l'écran ni dans
    l'historique du terminal), puis écriture dans .env."""
    import getpass
    print("Collez votre clé SECRÈTE Binance puis appuyez sur Entrée.")
    print("(Rien ne s'affiche pendant la saisie : c'est normal.)")
    try:
        secret = clean_api_secret(getpass.getpass("Secret : "))
    except ValueError as e:
        print(f"❌ Secret refusé : {e}. Rien n'a été modifié.")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nAnnulé. Rien n'a été modifié.")
        return 1
    set_env_var(env_path, "BINANCE_API_SECRET", secret)
    print(f"✅ Secret enregistré dans {env_path} (fichier privé, jamais commité).")
    return 0


MIN_LIVE_CAPITAL = 100.0     # USDT : en dessous, la plupart des ordres < minimum

_ORDER_METHODS = ("create_order", "cancel_order", "privatePostOrderListOco",
                  "private_post_orderlist_oco", "privateDeleteOrderList",
                  "private_delete_orderlist")


def _forbid_orders(exchange: Any) -> None:
    """Garde-fou de `verify` : toute tentative d'ordre lève une erreur."""
    def forbidden(name):
        def _raise(*a, **k):
            raise RuntimeError(f"ordre interdit pendant la vérification ({name})")
        return _raise
    for name in _ORDER_METHODS:
        setattr(exchange, name, forbidden(name))


def cmd_verify(gcfg: GuardConfig, exchange: Any = None,
               now: Optional[datetime] = None, out=None) -> int:
    """Vérifications SANS AUCUN ORDRE avant le passage en réel : droits de
    la clé, soldes, validation des types d'ordres (order/test) et
    simulation de la décision du jour. Retourne 0 si tout est prêt."""
    out = out or sys.stdout
    say = lambda msg="": print(msg, file=out)       # noqa: E731
    ok = True
    if exchange is None:
        key = os.environ.get("BINANCE_API_KEY", "").strip()
        secret = os.environ.get("BINANCE_API_SECRET", "").strip()
        if not key or not secret:
            say("❌ BINANCE_API_KEY ou BINANCE_API_SECRET absent de .env "
                "(secret : python trendguard_bot.py set-secret)")
            return 1
        exchange = ccxt.binance({"apiKey": key, "secret": secret,
                                 "enableRateLimit": True,
                                 "options": {"defaultType": "spot",
                                             "adjustForTimeDifference": True}})
        if gcfg.binance_testnet:
            exchange.set_sandbox_mode(True)
    _forbid_orders(exchange)
    say(f"Vérification {'TESTNET' if gcfg.binance_testnet else 'BINANCE RÉEL'} "
        f"— aucun ordre ne sera passé")

    say("\n── 1. Droits de la clé API")
    try:
        r = exchange.sapi_get_account_apirestrictions()
    except ccxt.AuthenticationError as e:
        msg = str(e)
        hint = (" → adresse IP non autorisée, clé supprimée ou droits manquants"
                if "-2015" in msg else " → secret incorrect" if "-1022" in msg else "")
        say(f"  ❌ Clé refusée par Binance{hint} ({msg[:120]})")
        return 1
    except Exception as e:
        r = None
        say(f"  (lecture des droits impossible : {type(e).__name__})")
    if r is not None:
        withdraw = bool(r.get("enableWithdrawals"))
        trading = bool(r.get("enableSpotAndMarginTrading"))
        say(f"  Retrait autorisé      : {'OUI ❌ à désactiver sur Binance' if withdraw else 'non ✓'}")
        say(f"  Trading Spot autorisé : {'oui ✓' if trading else 'NON ❌ à activer sur Binance'}")
        say(f"  Restriction IP        : {'oui ✓' if r.get('ipRestrict') else 'non (conseillé)'}")
        ok = ok and not withdraw and trading

    say("\n── 2. Soldes")
    bal = exchange.fetch_balance()
    total = {a: float(q or 0) for a, q in (bal.get("total") or {}).items()
             if float(q or 0) > 0}
    value = 0.0
    for asset, qty in sorted(total.items()):
        px = 1.0 if asset in ("USDT", "USDC", "FDUSD") else 0.0
        if not px:
            try:
                px = float(exchange.fetch_ticker(f"{asset}/USDT")["last"] or 0)
            except Exception:
                px = 0.0
        value += qty * px
        say(f"  {asset:<8} {qty:>18.8f}  ≈ {qty * px:>12,.2f} USDT")
    say(f"  Valeur totale estimée : {value:,.2f} USDT")

    say("\n── 3. Validation des ordres + 4. décision du jour (simulation)")
    live = dataclasses.replace(gcfg, run_mode="live", enable_live_trading=True,
                               live_confirmation="I_UNDERSTAND_RISK",
                               db_file=":memory:", log_file=os.devnull,
                               lock_file=os.devnull)
    quiet = logging.getLogger("trendguard.verify")
    reasons = logging.StreamHandler(out)             # affiche les causes d'échec
    reasons.setLevel(logging.WARNING)
    reasons.setFormatter(logging.Formatter("  ⚠️  %(message)s"))
    quiet.handlers[:] = [reasons]
    quiet.setLevel(logging.WARNING)
    quiet.propagate = False
    store = v29.Store(":memory:", quiet)
    bot = TrendGuardBot(live, quiet, exchange, store,
                        v29.Notifier("", "", logger=quiet))
    try:
        if not bot.boot():
            say("  ❌ Démarrage impossible (causes ci-dessus).")
            if r is not None and not r.get("enableSpotAndMarginTrading"):
                say("     → la clé n'a pas le droit de trader : Binance ▸ Gestion "
                    "des API ▸ Modifier ▸ cocher « Activer le trading Spot et sur "
                    "marge ».")
            return 1
        say("  Validation des types d'ordres (order/test) : OK ✓")
        now = now or v29._utcnow()
        day = last_closed_day(now, live.decision_delay_sec)
        snap, bull, prices = bot._market_snapshot(now, day)
        equity, cash = bot._equity_and_cash(prices)
        orphans = [b for b, sl in bot.slots.items() if sl.ctx.orphan_balance]
        eligible = {a: x for a, x in snap.items() if bot._can_enter(a)}
        plans = ts.plan_entries(bot._holdings(), eligible, bull, equity, cash,
                                live.params)
    finally:
        store.close()
    say(f"  Bougie du {day} | régime BTC : "
        f"{'HAUSSIER' if bull else 'BAISSIER (aucun achat)'}")
    say(f"  Capital géré : {equity:,.2f} USDT | USDT disponible : {cash:,.2f}")
    if equity < MIN_LIVE_CAPITAL:
        ok = False
        say(f"  ❌ Capital insuffisant : Binance impose ~5 USDT minimum par ordre ; "
            f"avec 1 % de risque par trade, il faut au moins "
            f"{MIN_LIVE_CAPITAL:.0f} USDT pour que les positions dépassent ce "
            f"minimum.")
    if orphans:
        say(f"  ⚠️  Cryptos détenues hors bot (achats bloqués sur ces paires) : "
            f"{', '.join(orphans)}")
    spent = 0.0
    for p in plans:
        spent += p["cost"]
        say(f"  ↗ achat prévu {p['asset'].upper():<5} {p['qty']:.6g} ≈ "
            f"{p['cost']:,.2f} USDT | stop {p['stop']:.6g} | "
            f"risque {p['risk_quote']:.2f} USDT")
    if plans:
        say(f"  Total : {spent:,.2f} USDT ({spent / max(equity, 1e-9) * 100:.0f} % "
            f"du capital géré)")
    else:
        say("  Aucun achat prévu aujourd'hui.")
    say("\n" + ("✅ Prêt pour le mode réel." if ok else
                "❌ À corriger avant le mode réel (voir ci-dessus)."))
    return 0 if ok else 1


def health_check(gcfg: GuardConfig, max_age_sec: int) -> int:
    """0 si le dernier cycle réussi date de moins de `max_age_sec` et que le
    kill-switch n'est pas déclenché ; 1 sinon (contrôle de santé Docker)."""
    try:
        store = v29.Store(gcfg.db_file, logging.getLogger("trendguard.health"))
        try:
            state = store.get_kv(TrendGuardBot.STATE_KEY) or {}
        finally:
            store.close()
    except Exception as e:
        print(f"KO : base illisible ({e})")
        return 1
    last = state.get("last_cycle_ts")
    if not last:
        print("KO : aucun cycle réussi")
        return 1
    age = time.time() - float(last)
    if age > max_age_sec:
        print(f"KO : dernier cycle il y a {age:.0f} s (> {max_age_sec} s)")
        return 1
    if state.get("halted"):
        print(f"KO : kill-switch — {state.get('halt_reason')}")
        return 1
    print(f"OK : dernier cycle il y a {age:.0f} s, décision du "
          f"{state.get('last_decision_day')}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="TrendGuard Bot (Binance Spot)")
    ap.add_argument("cmd", choices=["run", "once", "status", "resume", "docs",
                                    "health", "replay", "set-secret",
                                    "verify"])
    ap.add_argument("--data", default="data", help="(replay) dossier Coin Metrics")
    ap.add_argument("--start", default="2025-06-01", help="(replay) début")
    ap.add_argument("--end", default=None, help="(replay) fin")
    ap.add_argument("--capital", type=float, default=10_000.0,
                    help="(replay) capital initial USDT")
    args = ap.parse_args(argv)
    if args.cmd == "replay":
        res = replay(args.data, args.start, args.end, args.capital)
        m = res["metrics"]
        print("\n" + "═" * 64)
        print(f"REJEU PAPER {args.start} → {res['last_day']} (prix réels)")
        print("═" * 64)
        print(f"Capital : {args.capital:,.2f} → {res['equity'].iloc[-1]:,.2f} USDT "
              f"({m['total_return_pct']:+.1f} %)")
        print(f"Max drawdown : {m['max_dd_pct']:.1f} %  |  Sharpe : {m['sharpe']:.2f}")
        print(f"Trades clos : {m['trades']}  |  gagnants : {m['win_rate_pct']:.0f} %"
              f"  |  gain moy. {m['avg_win_r']:+.2f} R  |  perte moy. "
              f"{m['avg_loss_r']:+.2f} R")
        print(f"Régime BTC au dernier jour : "
              f"{'HAUSSIER' if res['regime_bull'] else 'BAISSIER (aucune entrée)'}")
        if res["holdings"]:
            print("Positions ouvertes :")
            for a, h in res["holdings"].items():
                px = float(res["prices"][a])
                print(f"  {a.upper():<5} entrée {h['entry']:.6g} → {px:.6g} "
                      f"({(px / h['entry'] - 1) * 100:+.1f} %)  stop {h['stop']:.6g}")
        else:
            print("Positions ouvertes : aucune (100 % USDT)")
        return 0
    if args.cmd == "docs":
        for k, v in sorted(TG_ENV_DOC.items()):
            print(f"  {k:<28} {v}")
        return 0
    try:
        gcfg = load_guard_config_from_env()
    except ValueError as e:
        print(f"Configuration invalide : {e}", file=sys.stderr)
        return 2
    if args.cmd == "set-secret":
        return cmd_set_secret()
    if args.cmd == "verify":
        return cmd_verify(gcfg)
    if args.cmd == "health":
        return health_check(gcfg, v29._env_i("TG_HEALTH_MAX_AGE_SEC", 600))
    locks: List[v29.ProcessLock] = []
    if args.cmd == "resume":
        # resume modifie l'état : interdit pendant que le bot tourne (il
        # réécrirait son propre état au cycle suivant).
        try:
            locks = v29.acquire_instance_locks(gcfg.lock_file, gcfg.db_file)
        except SystemExit as e:
            print(f"❌ {e}\nArrêtez d'abord le bot, puis relancez resume "
                  f"(Docker : docker compose stop && docker compose run --rm "
                  f"trendguard resume && docker compose start).")
            return 1
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
        v29.release_locks(locks)
        return 0
    locks = v29.acquire_instance_locks(gcfg.lock_file, gcfg.db_file)
    bot = _build(gcfg)
    try:
        bot.logger.info(f"TrendGuard — {gcfg.run_mode.upper()}"
                        f"{' TESTNET' if gcfg.binance_testnet else ''} — "
                        f"{len(gcfg.universe)} actifs, risque "
                        f"{gcfg.params.risk_pct*100:.2f} %/trade")
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        if args.cmd == "once":
            if not bot.boot():
                print("❌ Démarrage impossible (réseau / exchange) : voir le log.",
                      file=sys.stderr)
                return 1
            try:
                bot.run_cycle()
            except Exception as e:
                bot.logger.error(f"[ONCE] cycle KO: {e}")
                return 1
            return 0
        delay = 30
        while _running and not bot.boot():
            bot.logger.warning(f"[BOOT] nouvelle tentative dans {delay} s")
            _sleep(delay)
            delay = min(delay * 2, 600)
        if _running:
            bot.run_forever()
        return 0
    finally:
        bot.store.close()
        bot.notifier.close()
        v29.release_locks(locks)


if __name__ == "__main__":
    sys.exit(main())
