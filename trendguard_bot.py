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
  python trendguard_bot.py supervise # bot relancé seul en cas de plantage
  python trendguard_bot.py stop      # arrêt propre de l'automatisation
  python trendguard_bot.py autostart on|off   # démarrage avec l'ordinateur
  python trendguard_bot.py panel     # panneau de contrôle (navigateur)
  python trendguard_bot.py set-panel-password   # accès depuis un téléphone
"""

from __future__ import annotations

import argparse
import dataclasses
import faulthandler
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
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import ccxt
import pandas as pd

import alerts
import autonomy
import diagnostics as dg
import market_watch as mw
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
    "BINANCE_TESTNET": "true : Binance Spot testnet (live uniquement ; le paper suit le vrai marché)",
    "TG_UNIVERSE": "Actifs tradés (CSV de bases, cotées en USDT)",
    "TG_RISK_PCT": "Risque par trade en fraction d'equity (0.01 = 1 %)",
    "TG_MAX_POSITIONS": "Nombre maximum de positions simultanées",
    "TG_MAX_TOTAL_RISK": "Risque initial cumulé maximum (0.06 = 6 %)",
    "TG_KILL_DRAWDOWN": "Drawdown depuis le pic qui bloque les entrées (0.40)",
    "TG_HEARTBEAT_MIN": "Intervalle du battement de cœur dans le journal (min, 0 = off)",
    "TG_MAX_CAPITAL": "Capital max géré par le bot en USDT (0 = tout le compte)",
    "TG_DD_THROTTLE": "Profil prudent : baisse:multiplicateur (ex. 0.10:0.5) ; vide = off",
    "TG_AUTO_DIAGNOSE_DAYS": "Auto-diagnostic tous les N jours (0 = désactivé)",
    "TG_CLASSEMENT": "true : classement quotidien des cryptos par bénéfice (panneau, auto-sélection)",
    "TG_KEEP_AWAKE": "true : l'ordinateur ne se met pas en veille tout seul pendant que le bot tourne",
    "TG_MAX_SPREAD": "Ruse : achat différé si l'écart achat/vente dépasse ce seuil (0.005 = 0,5 %)",
    "TG_ENTRY_RETRY_HOURS": "Ruse : durée des nouveaux essais d'un achat différé (heures)",
    "TG_VEILLE": "true : annonces officielles Binance lues chaque jour (retrait = achats bloqués)",
    "TG_VEILLE_IA": "true : rapport quotidien des IA (conseil seulement, clés dans .env)",
    "TG_VEILLE_DB": "Base de la veille (mémoire des IA et des annonces)",
    "PANEL_HOST": "Panneau : 127.0.0.1 (ce PC) ou 0.0.0.0 (téléphone, mot de passe requis)",
    "PANEL_PORT": "Panneau : port web (8765)",
    "TG_ALLOW_RECOVERY": "true : adopter les ordres du bot inconnus de la base (base perdue)",
    "TG_PAPER_CAPITAL": "Capital initial du mode paper (USDT)",
    "TG_DB_FILE": "Base SQLite du bot",
    "TG_LOG_FILE": "Fichier de log",
    "TG_LOCK_FILE": "Fichier de verrou (une seule instance)",
    "TG_HEALTH_MAX_AGE_SEC": "Âge max du dernier cycle réussi pour `health` (s)",
    "TELEGRAM_TOKEN": "Token bot Telegram",
    "TELEGRAM_CHAT_ID": "Chat ID destination",
    "ALERT_LEVEL": "E-mail et WhatsApp : critical (alertes critiques) ou all (tout)",
    "SMTP_HOST": "E-mail : serveur SMTP (ex. smtp.gmail.com)",
    "SMTP_PORT": "E-mail : port (587 STARTTLS, 465 SSL)",
    "SMTP_USER": "E-mail : identifiant SMTP",
    "SMTP_PASSWORD": "E-mail : mot de passe (Gmail : mot de passe d'application)",
    "ALERT_EMAIL_TO": "E-mail : adresse qui reçoit les alertes",
    "WHATSAPP_PROVIDER": "WhatsApp : callmebot (gratuit) ou twilio",
    "WHATSAPP_PHONE": "WhatsApp : numéro international (+225…)",
    "CALLMEBOT_APIKEY": "WhatsApp CallMeBot : clé reçue de callmebot.com",
    "TWILIO_ACCOUNT_SID": "WhatsApp Twilio : Account SID",
    "TWILIO_AUTH_TOKEN": "WhatsApp Twilio : Auth Token",
    "TWILIO_WHATSAPP_FROM": "WhatsApp Twilio : numéro expéditeur",
    "PANEL_PASSWORD": "Panneau : mot de passe (obligatoire avec PANEL_HOST=0.0.0.0) ; set-panel-password",
    "PANEL_ASSISTANT_IA": "Assistant du panneau : true = réponses rédigées par l'IA de la veille si une clé existe",
    "PANEL_ASSISTANT_MODEL": "Assistant du panneau : modèle Claude (claude-sonnet-5 par défaut)",
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
    clock_resync_min: int = 60          # bot remis à l'heure de Binance
    auto_diagnose_days: int = 7         # 0 = désactivé
    # Veille (market_watch.py) : désactivée par défaut dans le code (tests,
    # rejeu hors ligne), activée par l'environnement (TG_VEILLE=true).
    watch: bool = False                 # annonces officielles → veto d'achat
    watch_ai: bool = False              # rapport quotidien des IA (conseil)
    # Classement des cryptos par bénéfice de la stratégie (affiché dans le
    # panneau, utilisé par l'auto-sélection) ; activé par l'environnement.
    rank_cryptos: bool = False
    watch_db: str = ""
    max_capital: float = 0.0            # 0 = tout le compte
    keep_awake: bool = False            # anti-veille (activé par l'environnement)
    max_spread: float = 0.005           # ruse : carnet anormal → achat différé
    entry_retry_hours: float = 6.0      # achat différé : nouveaux essais pendant 6 h
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
        if not (0 < self.max_spread < 0.2):
            raise ValueError("TG_MAX_SPREAD doit être dans ]0, 0.2[.")
        if self.entry_retry_hours < 0:
            raise ValueError("TG_ENTRY_RETRY_HOURS doit être >= 0.")
        self.params.validate()
        for name, default in (
                ("db_file", os.path.join(v29.APP_DIR, f"trendguard_{self.run_mode}.db")),
                ("log_file", os.path.join(v29.APP_DIR, f"trendguard_{self.run_mode}.log")),
                ("lock_file", os.path.join(v29.APP_DIR, f"trendguard_{self.run_mode}.lock"))):
            if not getattr(self, name):
                object.__setattr__(self, name, default)

    @property
    def stop_file(self) -> str:
        """Demande d'arrêt déposée par le panneau de contrôle, à côté du
        verrou (fonctionne pareil sous Windows, Linux et macOS)."""
        return autonomy.sidecar(self.lock_file, ".stop")

    @property
    def alive_file(self) -> str:
        """Signe de vie du bot, surveillé par le superviseur."""
        return autonomy.sidecar(self.lock_file, ".alive")


def parse_dd_throttle(raw: str) -> Tuple[Tuple[float, float], ...]:
    """'0.10:0.5,0.20:0.25' → ((0.10, 0.5), (0.20, 0.25)) ; vide → ()."""
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            thr, mult = part.split(":")
            out.append((float(thr), float(mult)))
        except ValueError:
            raise ValueError(f"TG_DD_THROTTLE invalide : {part!r} "
                             f"(format baisse:multiplicateur, ex. 0.10:0.5)")
    return tuple(sorted(out))


def load_guard_config_from_env() -> GuardConfig:
    uni = tuple(a.strip().upper() for a in
                v29._env_s("TG_UNIVERSE", ",".join(LIVE_UNIVERSE_DEFAULT)).split(",")
                if a.strip())
    params = dataclasses.replace(
        ts.TrendParams(),
        risk_pct=v29._env_f("TG_RISK_PCT", 0.01),
        max_positions=v29._env_i("TG_MAX_POSITIONS", 8),
        max_total_risk=v29._env_f("TG_MAX_TOTAL_RISK", 0.06),
        dd_throttle=parse_dd_throttle(v29._env_s("TG_DD_THROTTLE", "")))
    return GuardConfig(
        run_mode=v29._env_s("RUN_MODE", "paper").lower(),
        universe=uni, params=params,
        kill_drawdown=v29._env_f("TG_KILL_DRAWDOWN", 0.40),
        heartbeat_min=v29._env_i("TG_HEARTBEAT_MIN", 15),
        auto_diagnose_days=v29._env_i("TG_AUTO_DIAGNOSE_DAYS", 7),
        watch=v29._env_b("TG_VEILLE", True),
        watch_ai=v29._env_b("TG_VEILLE_IA", True),
        rank_cryptos=v29._env_b("TG_CLASSEMENT", True),
        watch_db=v29._env_s("TG_VEILLE_DB", os.path.join(v29.APP_DIR, "trendguard_veille.db")),
        max_capital=v29._env_f("TG_MAX_CAPITAL", 0.0),
        keep_awake=v29._env_b("TG_KEEP_AWAKE", True),
        max_spread=v29._env_f("TG_MAX_SPREAD", 0.005),
        entry_retry_hours=v29._env_f("TG_ENTRY_RETRY_HOURS", 6.0),
        allow_recovery=v29._env_b("TG_ALLOW_RECOVERY", False),
        paper_capital=v29._env_f("TG_PAPER_CAPITAL", 10_000.0),
        enable_live_trading=v29._env_b("ENABLE_LIVE_TRADING", False),
        live_confirmation=v29._env_s("LIVE_TRADING_CONFIRMATION", ""),
        binance_testnet=v29._env_b("BINANCE_TESTNET", False),
        db_file=v29._env_s("TG_DB_FILE", ""),
        log_file=v29._env_s("TG_LOG_FILE", ""),
        lock_file=v29._env_s("TG_LOCK_FILE", ""))


class ExchangeTimeFormatter(logging.Formatter):
    """Horodatage des journaux à l'heure de Binance (heure du PC corrigée
    de l'écart mesuré par v29.sync_exchange_clock), millisecondes
    comprises."""

    def formatTime(self, record, datefmt=None):
        t = record.created + v29.clock_offset_ms() / 1000
        s = time.strftime(datefmt or self.default_time_format, time.localtime(t))
        if not datefmt:
            s = self.default_msec_format % (s, int((t % 1) * 1000))
        return s


def build_guard_logger(log_file: str) -> logging.Logger:
    lg = logging.getLogger("trendguard")
    lg.handlers.clear()
    lg.setLevel(logging.INFO)
    fmt = ExchangeTimeFormatter("%(asctime)s [%(levelname)s] %(message)s")
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


class DecisionDeferred(RuntimeError):
    """Données insuffisantes pour décider sans risque : la décision
    quotidienne est retentée au cycle suivant (jamais de vente sur une
    simple panne réseau)."""


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
        self._last_clock_sync = 0.0
        self._last_veto_refresh = 0.0
        self._last_equity_log = 0.0
        self._stop_flag = False
        self._started_at = time.time()
        self._last_alive = 0.0
        self._last_selection_try = 0.0
        # Notes d'exécution du jour (achat différé, annulé) pour le raisonnement.
        self._entry_notes: Dict[str, Tuple[str, str]] = {}
        self._last_close: Optional[pd.DataFrame] = None   # apprentissage de la veille

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
        self._sync_clock(force=True)       # heure de Binance avant toute décision
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

    BUYS_KEPT = 500

    def _record_buy(self, a: str, when: datetime, price: float, qty: float, cost: float,
                    risk: float, note: str = "") -> None:
        """Journal des achats, écrit à l'instant de l'achat : le panneau les
        affiche en temps réel sur les graphiques (marqueurs « Achat »)."""
        buys = self.state.setdefault("buys", [])
        buys.append({"asset": a, "date": when.isoformat(), "price": float(price),
                     "qty": float(qty), "cost": float(cost), "risk": float(risk),
                     "mode": self.g.run_mode, "note": note})
        self.state["buys"] = buys[-self.BUYS_KEPT:]

    def _harvest_live_trade(self, s: Slot, n_before: int, reason: str,
                            opened_at: Optional[str] = None, buy_price: float = 0.0) -> None:
        pf = s.ctx.portfolio
        if pf.stats_wins + pf.stats_losses > n_before and pf.last_trades:
            t = pf.last_trades[-1]
            trade = {"asset": s.base.lower(), "date": self._now.isoformat(),
                     "pnl": float(t.get("pnl", 0.0)), "r": float(t.get("r", 0.0)),
                     "reason": t.get("reason") or reason}
            entry = float(t.get("entry_price") or buy_price or 0.0)
            exit_px = float(t.get("exit_price") or 0.0)
            if entry:
                trade["entry"] = entry
            if exit_px:
                trade["exit"] = exit_px
            opened = v29._parse_iso(opened_at) if opened_at else None
            if opened:
                trade["entry_date"] = opened.isoformat()
                trade["days"] = (self._now - opened).days
            self._record_trade(trade)

    def _log_equity(self, equity: float, cash: float) -> None:
        """Point d'historique du capital pour les graphiques du panneau, au
        plus un toutes les 10 minutes."""
        now = time.time()
        if now - self._last_equity_log < 600:
            return
        self._last_equity_log = now
        self.store.log_equity(float(equity), float(cash), 0.0, 0.0,
                              bool(self.state.get("halted")), self.g.run_mode)

    def stop_requested(self) -> bool:
        """Arrêt propre demandé depuis le panneau (fichier .stop). Une
        demande déposée plus d'une minute avant le démarrage du bot est
        périmée : ignorée."""
        if self._stop_flag:
            return True
        path = self.g.stop_file
        if path and os.path.exists(path):
            stale = os.path.getmtime(path) < self._started_at - 60
            try:
                os.remove(path)
            except OSError:
                pass
            if not stale:
                self._stop_flag = True
                self.logger.info("[ARRÊT] demandé depuis le panneau de contrôle : arrêt propre")
        return self._stop_flag

    ALIVE_EVERY_SEC = 30

    def _touch_alive(self, force: bool = False) -> None:
        """Signe de vie pour le superviseur : sans lui pendant 30 min, le
        bot est considéré comme bloqué et relancé."""
        path = self.g.alive_file
        now = time.time()
        if not path or (not force and now - self._last_alive < self.ALIVE_EVERY_SEC):
            return
        self._last_alive = now
        try:
            with open(path, "a", encoding="utf-8"):
                pass
            os.utime(path, None)
        except OSError:
            pass

    def waiting(self) -> bool:
        """Appelé chaque seconde pendant les attentes : signe de vie, puis
        demande d'arrêt éventuelle."""
        self._touch_alive()
        return self.stop_requested()

    @staticmethod
    def _closed_count(s: Slot) -> int:
        return s.ctx.portfolio.stats_wins + s.ctx.portfolio.stats_losses

    # ---------- Cycle ----------

    def run_cycle(self, now: Optional[datetime] = None) -> None:
        self._sync_clock()
        now = now or v29._utcnow()          # heure de Binance
        self._now = now
        if self.live:
            self._maintain_live()
        else:
            self._maintain_paper()
        day = last_closed_day(now, self.g.decision_delay_sec)
        if self.state.get("last_decision_day") != day:
            self._refresh_vetoes(now)
            try:
                self.daily_decision(now, day)
                self.state.pop("decision_deferred_since", None)
            except DecisionDeferred as e:
                self._decision_deferred(day, str(e))
        self._refresh_selection(now)
        if self.state.get("pending_entries"):
            try:
                self._retry_pending(now)
            except Exception as e:
                self.logger.warning(f"[RUSE] nouvel essai d'achat impossible : {e}")
        # Horloge réelle (et non `now`, simulé en rejeu) : sert au contrôle
        # de santé du conteneur.
        self.state["last_cycle_ts"] = time.time()
        self._save_state()
        self._heartbeat(now)
        self._auto_diagnose(now, day)
        self._daily_watch(now, day)

    # ---------- Veille (market_watch.py) ----------

    def _vetoed(self, a: str) -> Optional[Dict[str, Any]]:
        """Veto officiel actif sur cette crypto (annonce de retrait Binance)."""
        v = (self.state.get("vetoes") or {}).get(a.lower())
        if v and v.get("until", "") >= self._now.date().isoformat():
            return v
        return None

    def _refresh_vetoes(self, now: datetime) -> None:
        """Annonces officielles de Binance, lues sans IA avant chaque
        décision (au plus une fois par heure si la décision est reportée).
        Binance injoignable : les vetos précédents restent en place et la
        décision a lieu quand même."""
        if not self.g.watch or time.time() - self._last_veto_refresh < 3600:
            return
        self._last_veto_refresh = time.time()
        memory = None
        try:
            memory = mw.WatchMemory(self.g.watch_db)
            vetoes, monitoring = mw.refresh_official(
                [s.base.lower() for s in self.slots.values()], now, memory)
        except Exception as e:
            self.logger.warning(f"[VEILLE] annonces Binance indisponibles "
                                f"({mw.friendly_error(e)}) : vetos précédents conservés")
            return
        finally:
            if memory is not None:
                memory.close()
        held = set(self._holdings())
        for a, v in vetoes.items():
            if a not in (self.state.get("vetoes") or {}):
                warn = (" ; position DÉTENUE : le stop reste actif, vendre avant la date du "
                        "retrait est conseillé" if a in held else "")
                self.logger.warning(f"[VEILLE] {v['reason']} : nouveaux achats bloqués{warn} — {v['url']}")
                self.notifier(f"⚠️ TrendGuard : {v['reason']}, nouveaux achats bloqués{warn}.\n{v['url']}",
                              critical=a in held, dedup_key=f"tg-veto-{a}-{v['date']}")
        for m in monitoring:
            self.logger.info(f"[VEILLE] Binance place {', '.join(a.upper() for a in m['assets'])} "
                             f"sous surveillance (Monitoring Tag, {m['date']}) — {m['url']}")
        self.state["vetoes"] = vetoes

    def _daily_watch(self, now: datetime, day: str) -> None:
        """Rapport quotidien des IA (conseil seulement : aucun effet sur les
        ordres). Une panne des IA ou du réseau n'affecte jamais le trading."""
        if not (self.g.watch and self.g.watch_ai) or self.state.get("last_watch_day") == day:
            return
        self.state["last_watch_day"] = day
        self._save_state()
        memory = None
        try:
            memory = mw.WatchMemory(self.g.watch_db)
            report = mw.daily_report([s.base.lower() for s in self.slots.values()],
                                     list(self._holdings()), now, memory,
                                     close=self._last_close)
        except Exception as e:
            self.logger.warning(f"[VEILLE] rapport impossible : {mw.friendly_error(e)}")
            return
        finally:
            if memory is not None:
                memory.close()
        c = report["consensus"]
        self.state["last_watch"] = {
            "day": report["day"], "sentiment": c["sentiment"], "providers": c["providers"],
            "providers_total": len(report["providers"]),
            "alerts": [a["text"] for a in report["alerts"][:6]]}
        self._save_state()
        self.logger.info("[VEILLE]\n" + mw.render(report))
        urgent = [a for a in report["alerts"] if a["level"] >= 2]
        if urgent:
            self.notifier("🔎 TrendGuard veille " + day + "\n" + "\n".join(
                f"• {a['text']}" for a in urgent[:6]),
                critical=any(a["level"] >= 3 for a in urgent), dedup_key=f"tg-veille-{day}")

    def _decision_deferred(self, day: str, why: str) -> None:
        """Décision reportée au cycle suivant ; alerte si cela dure plus
        d'une heure."""
        since = self.state.setdefault("decision_deferred_since", time.time())
        waited = time.time() - float(since)
        self.logger.warning(f"[DECISION] {day} reportée ({why}) — en attente "
                            f"depuis {waited / 60:.0f} min")
        if waited >= 3600 and not self.state.get("decision_deferred_notified") == day:
            self.state["decision_deferred_notified"] = day
            self.notifier(f"⚠️ TrendGuard : décision du {day} bloquée depuis "
                          f"{waited / 3600:.1f} h ({why}). Vérifier la connexion "
                          f"à Binance.", critical=True)

    def _sync_clock(self, force: bool = False) -> None:
        """Met le bot à l'heure de Binance (au démarrage puis toutes les
        `clock_resync_min` minutes, en paper comme en réel) : décisions,
        clôture des bougies, dates, journaux et horodatage des ordres
        signés. Le PC peut dériver de plusieurs secondes par jour (service
        de temps Windows arrêté) ; au-delà de 10 s, Binance refuserait
        tous les ordres, y compris les stops (-1021)."""
        every = self.g.clock_resync_min * 60
        if every <= 0 or (not force
                          and time.time() - self._last_clock_sync < every):
            return
        self._last_clock_sync = time.time()
        sync = v29.sync_exchange_clock(self.exchange)
        if sync is None:
            if callable(getattr(self.exchange, "fetch_time", None)):
                self._last_clock_sync -= max(every - 300, 0)   # nouvel essai dans 5 min
                self.logger.warning("[CLOCK] heure de Binance indisponible (réseau) : "
                                    "dernier écart conservé, nouvel essai dans 5 min")
            return
        previous = self.state.get("clock_offset_ms")
        self.state["clock_offset_ms"] = round(sync.offset_ms)
        self.state["clock_uncertainty_ms"] = round(sync.uncertainty_ms)
        self.state["clock_synced_at"] = v29._utcnow_iso()
        if force or previous is None or abs(sync.offset_ms - float(previous)) >= 500:
            self.logger.info(f"[CLOCK] {v29.describe_clock(sync.offset_ms, sync.uncertainty_ms)}"
                             f" → le bot utilise l'heure de Binance")

    def holdings_for_diagnosis(self) -> List[Dict[str, Any]]:
        return [{"asset": a, "qty": h.qty, "entry": h.entry, "stop": h.stop,
                 "risk_quote": h.risk_quote} for a, h in self._holdings().items()]

    def _auto_diagnose(self, now: datetime, day: str) -> None:
        """Auto-diagnostic périodique (lecture seule) : données, marché,
        portefeuille, santé de la stratégie, réel vs attendu. Il alerte
        (journal + notification) mais ne modifie jamais la stratégie. Un
        échec du diagnostic n'affecte jamais le trading."""
        every = self.g.auto_diagnose_days
        last = self.state.get("last_auto_diag_day")
        if every <= 0 or (last and (pd.Timestamp(day) - pd.Timestamp(last)).days < every):
            return
        self.state["last_auto_diag_day"] = day
        self._save_state()
        try:
            findings = dg.run_diagnosis(
                self.exchange, self.p, [s.base for s in self.slots.values()],
                self.state, self.holdings_for_diagnosis(),
                float(self.state.get("last_equity") or 0.0), day, now,
                quote=self.g.quote, kill_drawdown=self.g.kill_drawdown,
                sections=("data", "market", "portfolio", "strategy", "live",
                          "alternatives", "watch"))
        except Exception as e:
            self.logger.warning(f"[DIAG] auto-diagnostic impossible : {e}")
            return
        v = dg.verdict(findings)
        self.state["last_auto_diag_verdict"] = v
        self._save_state()
        self.logger.info("[DIAG]\n" + dg.render(findings, f"(auto, {day})"))
        if v in ("ATTENTION", "ALERTE"):
            points = [f"• {f.message}" for f in findings
                      if f.level in ("ATTENTION", "ALERTE")]
            self.notifier(f"{dg.ICONS[v]} TrendGuard auto-diagnostic {day} : {v}\n"
                          + "\n".join(points[:6]),
                          dedup_key=f"tg-diag-{day}", critical=(v == "ALERTE"))

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
            equity, cash = self._equity_and_cash(prices)
            self._log_equity(equity, cash)
            start = self.state.get("start_equity") or equity
            parts = [f"{a.upper()} {(prices.get(a, h.entry) / h.entry - 1) * 100:+.1f} %"
                     for a, h in sorted(holdings.items())]
            # Décision du jour à 00:00 UTC + délai : entre 00:00 et 00:02,
            # elle est encore à venir aujourd'hui, pas demain.
            nxt = (datetime.combine(now.date(), datetime.min.time(),
                                    tzinfo=now.tzinfo)
                   + timedelta(seconds=self.g.decision_delay_sec))
            if nxt <= now:
                nxt += timedelta(days=1)
            h_left, rem = divmod(int((nxt - now).total_seconds()), 3600)
            day = last_closed_day(now, self.g.decision_delay_sec)
            when = (f"décision du {day} en attente (nouvel essai à chaque cycle)"
                    if self.state.get("last_decision_day") != day
                    else f"prochaine décision dans {h_left} h {rem // 60:02d}")
            bull = self.state.get("last_regime_bull")
            regime = "?" if bull is None else ("HAUSSIER" if bull else "BAISSIER")
            self.logger.info(
                f"[HEARTBEAT] equity {equity:,.2f} {self.g.quote} "
                f"({(equity / start - 1) * 100:+.2f} %) | régime BTC {regime} | "
                f"{len(holdings)} position(s)"
                + (f" : {', '.join(parts)}" if parts else "")
                + f" | {when}"
                + (f" | heure Binance ({v29.describe_clock(v29.clock_offset_ms())})"
                   if self.state.get("clock_synced_at") else "")
                + (" | 🛑 KILL-SWITCH" if self.state.get("halted") else ""))
        except Exception as e:
            self.logger.warning(f"[HEARTBEAT] indisponible : {e}")

    def _maintain_live(self) -> None:
        """Protection de chaque position ; une paire en erreur (réseau) ne
        bloque jamais la surveillance des autres."""
        for s in self.slots.values():
            if not (s.ctx.position.in_position or s.ctx.pending_order):
                continue
            before = self._closed_count(s)
            opened, bought = s.ctx.position.opened_at, s.ctx.position.buy_price
            try:
                s.eng.resolve_pending(s.ctx)
                if s.ctx.position.in_position:
                    px = s.ex.get_ticker()["last"]
                    s.eng.maintain_protection(s.ctx, px)
            except ccxt.NetworkError as e:
                self.logger.warning(f"[PROT] {s.symbol} : réseau ({type(e).__name__}) "
                                    f"→ nouvel essai au prochain cycle")
            except Exception as e:
                self.logger.exception(f"[PROT] {s.symbol} : {e}")
            finally:
                self._harvest_live_trade(s, before, "EXCHANGE_STOP", opened, bought)
                self._save_slot(s)

    def _maintain_paper(self) -> None:
        """Paper : simulation du stop catastrophe exchange en intrajournalier."""
        book = self.state["paper"]["holdings"]
        for a in list(book):
            h = book[a]
            s = self.slots.get(a.upper())
            if s is None:
                continue
            try:
                px = s.ex.get_ticker()["last"]
            except Exception as e:
                self.logger.warning(f"[PAPER] {s.symbol} : prix indisponible "
                                    f"({type(e).__name__}) → nouvel essai au prochain cycle")
                continue
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

    def _load_market(self, now: datetime
                     ) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame], pd.Series]:
        """Clôtures journalières FERMÉES à `now`, indicateurs par actif et
        régime BTC, calculés une seule fois pour tous les jours demandés."""
        now_ms = int(now.timestamp() * 1000)
        closes, vols = {}, {}
        failed: List[str] = []
        # BTC d'abord : sans lui (régime), inutile d'interroger les autres.
        for base, s in sorted(self.slots.items(), key=lambda kv: kv[0] != "BTC"):
            df = None
            for k in range(3):
                try:
                    df = s.ex.fetch_ohlcv_htf(s.symbol, "1d", limit=self.g.ohlcv_limit)
                    break
                except Exception as e:
                    if k == 2:
                        self.logger.warning(f"[DATA] {s.symbol} OHLCV KO après 3 "
                                            f"essais : {type(e).__name__}")
                    else:
                        self.sleep(2 * (k + 1))
            if df is None:
                failed.append(base.lower())
                # Connexion coupée : inutile d'attendre les délais des autres
                # paires (la surveillance des stops passerait après).
                if base.upper() == "BTC":
                    raise DecisionDeferred("clôtures BTC indisponibles")
                if len(failed) >= 3 and len(failed) > len(closes):
                    raise DecisionDeferred("connexion à Binance instable "
                                           f"({len(failed)} paires sans données)")
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
            raise DecisionDeferred("clôtures BTC indisponibles")
        held_missing = sorted(set(self._holdings()) & set(failed))
        if held_missing:
            # Sans donnée, une position détenue serait traitée comme retirée
            # de la cote et vendue : on attend plutôt le retour des données
            # (le stop catastrophe posé sur Binance reste actif).
            raise DecisionDeferred("données indisponibles pour des positions "
                                   "détenues : " + ", ".join(a.upper() for a in held_missing))
        if failed:
            self.logger.warning("[DATA] décision prise sans (données indisponibles) : "
                                + ", ".join(a.upper() for a in failed))
        close = pd.DataFrame(closes).sort_index()
        volume = pd.DataFrame(vols).reindex(close.index)
        feats: Dict[str, pd.DataFrame] = {}
        for a in close.columns:
            first_valid = close[a].first_valid_index()
            if first_valid is None:
                continue
            f = ts.asset_features(close[a].loc[first_valid:], self.p,
                                  volume[a].loc[first_valid:])
            feats[a] = f.reindex(close.index)
        return close, feats, ts.btc_regime(close["btc"], self.p)

    @staticmethod
    def _snapshot_at(close: pd.DataFrame, feats: Dict[str, pd.DataFrame],
                     regime: pd.Series, day: str
                     ) -> Tuple[Dict[str, Dict[str, float]], bool,
                                Dict[str, float]]:
        d = pd.Timestamp(day, tz="UTC")
        if d not in close.index:
            raise DecisionDeferred(f"bougie du {day} absente")
        i = close.index.get_loc(d)
        snap: Dict[str, Dict[str, float]] = {}
        for a, f in feats.items():
            row = f.iloc[i]
            if pd.isna(row["close"]):
                continue
            snap[a] = {k: float(row[k]) for k in
                       ("close", "vol", "prior_high", "mom", "age", "vol30")}
        prices = {a: s["close"] for a, s in snap.items()}
        return snap, bool(regime.iloc[i]), prices

    def _market_snapshot(self, now: datetime, day: str
                         ) -> Tuple[Dict[str, Dict[str, float]], bool,
                                    Dict[str, float]]:
        close, feats, regime = self._load_market(now)
        return self._snapshot_at(close, feats, regime, day)

    @staticmethod
    def _missed_days(index: pd.Index, last: Optional[str], day: str) -> List[str]:
        """Clôtures postérieures à la dernière décision et antérieures à
        `day` : jours où le bot était arrêté."""
        if not last or last >= day:
            return []
        lo, hi = pd.Timestamp(last, tz="UTC"), pd.Timestamp(day, tz="UTC")
        return [str(d.date()) for d in index if lo < d < hi]

    def _catch_up(self, close: pd.DataFrame, feats: Dict[str, pd.DataFrame],
                  regime: pd.Series, missed: List[str],
                  prices_now: Dict[str, float]) -> List[Tuple[str, str]]:
        """Bot arrêté pendant `missed` : les stops sont réévalués sur chacune
        de ces clôtures, dans l'ordre (plus hauts et trailing compris). Un
        stop franchi pendant l'arrêt est exécuté maintenant, au prix actuel :
        c'est tout ce qu'un bot en réel pourrait faire. Aucune entrée n'est
        prise sur un jour passé."""
        self.logger.warning(
            f"[RATTRAPAGE] {len(missed)} clôture(s) non traitée(s) "
            f"({missed[0]} → {missed[-1]}), bot arrêté ? Stops réévalués sur "
            f"ces clôtures ; sorties éventuelles au prix actuel.")
        holdings = self._holdings()
        late: List[Tuple[str, str]] = []
        for d in missed:
            snap, bull, _ = self._snapshot_at(close, feats, regime, d)
            # Bougie absente ce jour-là ≠ radiation : l'actif est laissé tel quel.
            present = {a: h for a, h in holdings.items() if a in snap}
            for a, reason in ts.update_positions(present, snap, bull, self.p):
                holdings.pop(a, None)
                self._execute_exit(a, f"{reason}_LATE", prices_now.get(a))
                late.append((a, f"{reason}_LATE"))
            self._write_back(holdings)
        return late

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
        close, feats, regime = self._load_market(now)
        self._last_close = close
        snap, bull, prices = self._snapshot_at(close, feats, regime, day)
        # Cryptos que le bot a le droit d'ACHETER aujourd'hui (sélection du
        # panneau) ; les positions détenues restent toutes gérées.
        allowed = self._update_selection(close, feats, regime, day, snap, now)
        # La décision du jour remplace les achats différés de la veille.
        stale = self.state.pop("pending_entries", None)
        if stale:
            self.logger.info("[RUSE] achats différés remplacés par la décision du jour : "
                             + ", ".join(a.upper() for a in sorted(stale)))
        self._entry_notes = {}
        missed = self._missed_days(close.index,
                                   self.state.get("last_decision_day"), day)
        late = (self._catch_up(close, feats, regime, missed, prices)
                if missed else [])
        holdings = self._holdings()
        exits = ts.update_positions(holdings, snap, bull, p)
        for a, reason in exits:
            holdings.pop(a, None)
            self._execute_exit(a, reason, prices.get(a))
        self._write_back(holdings)
        if self.live:
            self._raise_exchange_stops(holdings, snap, now)
        else:
            self._raise_paper_disaster(holdings, snap)
        equity, cash = self._equity_and_cash(prices)
        self.state.setdefault("start_equity", equity)
        peak = max(float(self.state.get("peak_equity") or 0.0), equity)
        self.state["peak_equity"] = peak
        self.state["last_equity"] = equity
        self._log_equity(equity, cash)
        self.state["last_regime_bull"] = bull
        if not self.state.get("halted") and equity < peak * (1 - self.g.kill_drawdown):
            self.state["halted"] = True
            self.state["halt_reason"] = (f"Drawdown {(equity/peak-1)*100:.1f} % "
                                         f"> {self.g.kill_drawdown*100:.0f} %")
            self.logger.critical(f"[KILL] {self.state['halt_reason']} → entrées bloquées")
            self.notifier(f"🛑 TrendGuard : {self.state['halt_reason']}", critical=True)
        entries: List[Dict[str, Any]] = []
        mult = ts.risk_multiplier(equity, peak, p)
        self.state["risk_mult"] = mult
        if mult < 1.0:
            self.logger.warning(
                f"[PRUDENT] baisse de {(1 - equity / peak) * 100:.1f} % depuis le "
                f"pic → risque par trade × {mult:g}")
        if not self.state.get("halted"):
            eligible = {a: s for a, s in snap.items()
                        if self._can_enter(a) and a in allowed}
            for a in sorted(snap):
                v = self._vetoed(a)
                if v and a not in holdings and ts.entry_signal(snap[a], p):
                    self.logger.warning(f"[VEILLE] achat de {a.upper()} bloqué : {v['reason']}")
            cash_left = cash
            for plan in ts.plan_entries(holdings, eligible, bull, equity, cash,
                                        p, mult):
                done = self._execute_entry(plan, equity, now, cash_left)
                if done is not None:
                    entries.append(done)
                    cash_left -= done["cost"]
        self.state["last_decision_day"] = day
        self._explain(day, bull, close, snap, late + exits, entries, mult, now, allowed)
        self._summary(day, bull, equity, late + exits, entries, prices)

    # ---------- Raisonnement (affiché dans le panneau) ----------

    def _btc_gap(self, close: pd.DataFrame, day: str) -> Optional[float]:
        """Écart de BTC à sa moyenne du régime, en %."""
        try:
            c = close["btc"]
            sma = c.rolling(self.p.regime_sma, min_periods=self.p.regime_sma).mean()
            d = pd.Timestamp(day, tz="UTC")
            v = (float(c.loc[d]) / float(sma.loc[d]) - 1) * 100
        except (KeyError, ZeroDivisionError, TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _explain(self, day: str, bull: bool, close: pd.DataFrame,
                 snap: Dict[str, Dict[str, float]], exits: List[Tuple[str, str]],
                 entries: List[Dict[str, Any]], mult: float, now: datetime,
                 allowed: Optional[Set[str]] = None) -> None:
        holdings = self._holdings()
        notes: Dict[str, Tuple[str, str]] = {}
        sel = self.state.get("selection") or {}
        ranks = {r["asset"]: r for r in sel.get("ranking") or []}
        for a in snap:
            v = self._vetoed(a)
            if v and a not in holdings:
                notes[a] = ("veto", f"Achats bloqués par la veille : {v['reason']}")
            elif allowed is not None and a not in allowed and a not in holdings:
                rk = ranks.get(a)
                notes[a] = ("unselected", (
                    f"Hors auto-sélection : rang {rk['rank']} sur 2 ans "
                    f"({rk['total_r']:+.1f} R), le bot ne l'achète pas".replace(".", ",")
                    if sel.get("mode") == "auto" and rk else
                    "Décochée dans la sélection : le bot ne l'achète pas"))
        notes.update(self._entry_notes)
        r = explain_decision(day, bull, self._btc_gap(close, day), snap, holdings, exits,
                             [e["asset"] for e in entries], notes,
                             bool(self.state.get("halted")), mult, self.p)
        r["at"] = now.isoformat()
        self.state["reasoning"] = r
        hist = self.state.get("reasoning_log") or []
        hist.append({"day": day, "text": " ".join(r["lines"][:2])})
        self.state["reasoning_log"] = hist[-30:]

    # ---------- Sélection des cryptos (panneau) ----------

    AUTO_SELECT_N = 10            # auto-sélection : les 10 plus rentables…
    AUTO_SELECT_DAYS = 730        # … sur les 2 dernières années (achats ET ventes)
    AUTO_SELECT_HYSTERESIS = 3    # une crypto choisie ne sort qu'au-delà du rang 13

    def selection_request(self) -> Dict[str, Any]:
        return read_selection(self.g)

    def _update_selection(self, close: pd.DataFrame, feats: Dict[str, pd.DataFrame],
                          regime: pd.Series, day: str, snap: Dict[str, Dict[str, float]],
                          now: datetime) -> Set[str]:
        req = self.selection_request()
        prev = self.state.get("selection") or {}
        auto: List[str] = list(prev.get("auto") or [])
        ranking = prev.get("ranking") or []
        if self.g.rank_cryptos or req["mode"] == "auto":
            idx = close.index
            end_i = idx.get_loc(pd.Timestamp(day, tz="UTC"))
            lo = max(0, end_i - self.AUTO_SELECT_DAYS - 5)
            cols = {a: {k: f[k].values[lo:end_i + 1] for k in
                        ("close", "vol", "prior_high", "mom", "age", "vol30")}
                    for a, f in feats.items()}
            records = ts.asset_track_records(cols, regime.values[lo:end_i + 1], self.p)
            scores = ts.selection_scores(records, idx[lo:end_i + 1], end_i - lo,
                                         self.AUTO_SELECT_DAYS)
            eligible = [a for a, s in snap.items()
                        if ts._finite(s.get("vol30")) and s["vol30"] >= self.p.min_volume_usd
                        and s.get("age", 0) >= self.p.min_history and not self._vetoed(a)]
            auto = ts.rank_selection(scores, eligible, self.AUTO_SELECT_N, auto,
                                     self.AUTO_SELECT_HYSTERESIS)
            order = sorted(scores, key=lambda a: (-scores[a]["total_r"], -scores[a]["trades"], a))
            ranking = [{"asset": a, "rank": k + 1, "total_r": round(scores[a]["total_r"], 2),
                        "trades": scores[a]["trades"], "win_rate": round(scores[a]["win_rate"], 3),
                        "eligible": a in eligible} for k, a in enumerate(order)]
        active = auto if req["mode"] == "auto" else req["manual"]
        if req["mode"] == "auto" and not auto:
            active = [b.lower() for b in self.g.universe]     # classement indisponible
        if set(active) != set(prev.get("active") or []) and prev:
            self.logger.info(f"[SÉLECTION] {'auto' if req['mode'] == 'auto' else 'manuelle'} : "
                             f"{len(active)} crypto(s) achetable(s) : "
                             + ", ".join(a.upper() for a in active))
        self.state["selection"] = {"mode": req["mode"], "active": active, "auto": auto,
                                   "ranking": ranking, "day": day, "at": now.isoformat()}
        return set(active)

    def active_now(self) -> Set[str]:
        """Cryptos achetables à cet instant : le choix du panneau s'applique
        aussi aux achats différés, sans attendre la décision suivante."""
        req = self.selection_request()
        if req["mode"] == "manual":
            return set(req["manual"])
        auto = (self.state.get("selection") or {}).get("auto")
        return set(auto) if auto else {b.lower() for b in self.g.universe}

    def _refresh_selection(self, now: datetime) -> None:
        """Premier classement sans attendre la décision de 00:02 UTC (une
        fois, après une mise à jour du bot)."""
        if (not self.g.rank_cryptos or self.state.get("selection")
                or time.time() - self._last_selection_try < 1800):
            return
        self._last_selection_try = time.time()
        day = self.state.get("last_decision_day")
        if not day:
            return
        try:
            close, feats, regime = self._load_market(now)
            snap, _bull, _prices = self._snapshot_at(close, feats, regime, day)
            self._update_selection(close, feats, regime, day, snap, now)
            self._save_state()
        except Exception as e:
            self.logger.warning(f"[SÉLECTION] classement reporté : {e}")

    def _note_asset(self, a: str, status: str, text: str) -> None:
        """Met à jour le raisonnement du jour après un achat différé."""
        r = self.state.get("reasoning") or {}
        row = (r.get("assets") or {}).get(a)
        if row is not None:
            row.update(status=status, text=text)

    def _can_enter(self, a: str) -> bool:
        s = self.slots.get(a.upper())
        if s is None or self._vetoed(a):
            return False
        if not self.live:
            return True
        c = s.ctx
        return not (c.position.in_position or c.pending_order
                    or c.orphan_balance or c.risk.halted)

    def _execute_exit(self, a: str, reason: str, close_px: Optional[float]) -> None:
        s = self.slots.get(a.upper())
        if not self.live:
            # Prix réellement disponible (≈ clôture à 00:02 UTC ; différent
            # si la décision est tardive), clôture en repli.
            px = None
            if s is not None:
                try:
                    px = float(s.ex.get_ticker()["last"])
                except Exception:
                    px = None
            px = px or close_px
            if px:
                self._paper_exit(a, px, reason)
            return
        if s is None or not s.ctx.position.in_position:
            return
        before = self._closed_count(s)
        opened, bought = s.ctx.position.opened_at, s.ctx.position.buy_price
        ref = s.ex.get_ticker()["bid"]
        s.eng.close_position(s.ctx, f"TREND_{reason}", ref)
        self._harvest_live_trade(s, before, reason, opened, bought)
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

    def _raise_paper_disaster(self, holdings: Dict[str, ts.Holding],
                              snap: Dict[str, Dict[str, float]]) -> None:
        """Paper : le stop catastrophe simulé suit le trailing exactement
        comme le STOP_LOSS posé sur Binance en réel (_raise_exchange_stops).
        Sans cela, un krach intrajournalier était simulé au niveau du stop
        d'origine, bien plus bas qu'en réel."""
        book = self.state["paper"]["holdings"]
        for a, h in holdings.items():
            b = book.get(a)
            s = self.slots.get(a.upper())
            vol = snap.get(a, {}).get("vol")
            if b is None or s is None or not ts._finite(vol):
                continue
            target = h.stop - self.g.catastrophe_atr * vol
            if target <= b["disaster"] * (1 + self.g.stop_raise_min_pct):
                continue
            try:
                last = float(s.ex.get_ticker()["last"])
            except Exception:
                last = snap.get(a, {}).get("close") or 0.0
            if target >= last * 0.99:
                continue   # sortie gérée par le stop de clôture
            b["disaster"] = target

    # ---------- Ruse : exécution des achats ----------

    BOOK_DEPTH_MULT = 3.0       # carnet : 3 × le montant de l'achat…
    BOOK_DEPTH_BAND = 0.01      # … proposé à moins de 1 % du meilleur prix
    RETRY_EVERY_SEC = 300       # achat différé : nouvel essai toutes les 5 min

    def _pending(self) -> Dict[str, Any]:
        return self.state.setdefault("pending_entries", {})

    def _book_anomaly(self, s: Slot, notional: float) -> Optional[str]:
        """Raison de ne PAS acheter maintenant, ou None. Carnet illisible :
        None (décision inchangée, comme sans cette vérification)."""
        fetch = getattr(s.ex.exchange, "fetch_order_book", None)
        if fetch is None:
            return None
        try:
            ob = fetch(s.symbol, limit=100)
            bids, asks = ob.get("bids") or [], ob.get("asks") or []
            if not bids or not asks:
                return "carnet d'ordres vide"
            bid, ask = float(bids[0][0]), float(asks[0][0])
            if bid <= 0 or ask <= 0:
                return None
            spread = (ask - bid) / ((ask + bid) / 2)
            if spread > self.g.max_spread:
                return (f"écart achat/vente anormal ({spread * 100:.2f} %, limite "
                        f"{self.g.max_spread * 100:.2f} %)")
            depth = sum(float(p) * float(q) for p, q, *_ in asks
                        if float(p) <= ask * (1 + self.BOOK_DEPTH_BAND))
            if depth < self.BOOK_DEPTH_MULT * notional:
                return (f"carnet d'ordres trop mince ({depth:,.0f} {self.g.quote} à moins "
                        f"de 1 % du prix pour un achat de {notional:,.0f})")
        except Exception:
            return None
        return None

    def _defer_entry(self, plan: Dict[str, Any], equity: float, now: datetime,
                     reason: str) -> None:
        a = plan["asset"]
        book = self._pending()
        t = now.timestamp()
        e = book.get(a)
        if e is not None:
            e.update(reason=reason, tries=int(e.get("tries", 1)) + 1,
                     next=t + self.RETRY_EVERY_SEC)
            return
        if self.g.entry_retry_hours <= 0:
            self.logger.warning(f"[RUSE] achat de {a.upper()} annulé : {reason}")
            self._entry_notes[a] = ("cancelled", f"Achat annulé : {reason}")
            return
        book[a] = {"plan": plan, "equity": float(equity), "reason": reason, "tries": 1,
                   "since": t, "until": t + self.g.entry_retry_hours * 3600,
                   "next": t + self.RETRY_EVERY_SEC}
        self.logger.warning(
            f"[RUSE] achat de {a.upper()} différé : {reason} → nouvel essai toutes "
            f"les 5 min pendant {self.g.entry_retry_hours:g} h")
        self._entry_notes[a] = ("deferred", f"Achat différé : {reason}. Nouvel essai "
                                            f"toutes les 5 min")
        self._note_asset(a, *self._entry_notes[a])

    def _retry_pending(self, now: datetime) -> None:
        """Nouvel essai des achats différés, tant que la décision du jour
        tient (régime, plafonds, pas de veto, pas d'arrêt d'urgence)."""
        book = self._pending()
        t = now.timestamp()
        p = self.p
        for a in sorted(book):
            e = book.get(a)
            if e is None or t < e["next"]:
                continue
            if t >= e["until"]:
                book.pop(a, None)
                self.logger.warning(f"[RUSE] achat de {a.upper()} abandonné : {e['reason']} "
                                    f"pendant {self.g.entry_retry_hours:g} h")
                self._note_asset(a, "cancelled", f"Achat abandonné : {e['reason']} pendant "
                                                 f"{self.g.entry_retry_hours:g} h")
                continue
            holdings = self._holdings()
            eq = float(e["equity"])
            mult = float(self.state.get("risk_mult", 1.0) or 1.0)
            open_risk = sum(h.risk_quote for h in holdings.values())
            if (self.state.get("halted") or not self.state.get("last_regime_bull")
                    or a in holdings or not self._can_enter(a)
                    or a not in self.active_now()
                    or len(holdings) >= p.max_positions
                    or open_risk + e["plan"]["risk_quote"] > p.max_total_risk * eq * mult + 1e-9):
                book.pop(a, None)
                self.logger.info(f"[RUSE] achat différé de {a.upper()} abandonné : la situation "
                                 f"a changé depuis la décision")
                self._note_asset(a, "cancelled", "Achat abandonné : la situation a changé "
                                                 "depuis la décision")
                continue
            _eq, cash = self._equity_and_cash({})
            done = self._execute_entry(e["plan"], eq, now, cash)
            if done is not None:
                self.logger.info(f"[RUSE] {a.upper()} acheté au {e['tries'] + 1}e essai : "
                                 f"carnet d'ordres redevenu normal")
                self._note_asset(a, "bought", "Achetée après un achat différé : carnet "
                                              "d'ordres redevenu normal")
                self.notifier(f"↗ TrendGuard : achat différé de {a.upper()} exécuté "
                              f"(carnet d'ordres redevenu normal)")

    def _execute_entry(self, plan: Dict[str, Any], equity: float,
                       now: datetime, cash_left: float
                       ) -> Optional[Dict[str, Any]]:
        """Exécute une entrée planifiée au prix réellement disponible :
        taille recalculée pour ne jamais risquer plus que prévu (décision
        tardive), entrée annulée si le prix est retombé près du stop.
        Retourne le plan exécuté, ou None."""
        a = plan["asset"]
        s = self.slots.get(a.upper())
        if s is None:
            return None
        try:
            t = s.ex.get_ticker()
            px_now = float(t["ask"] if self.live else t["last"])
        except Exception as e:
            self._defer_entry(plan, equity, now, f"prix indisponible ({type(e).__name__})")
            return None
        # Cash disponible pour le bot : plafonné (TG_MAX_CAPITAL) et diminué
        # des achats déjà faits dans cette décision.
        cash = float(cash_left)
        if not self.live:
            cash = min(cash, float(self.state["paper"]["cash"]))
        adj = ts.reprice_entry(plan, px_now, equity, max(cash, 0.0), self.p)
        drift = px_now / plan["ref_price"] - 1
        if adj is None:
            self.logger.warning(
                f"[ENTRY] {a.upper()} annulée : prix {px_now:.6g} "
                f"({drift * 100:+.1f} % vs clôture) trop proche du stop "
                f"{plan['stop']:.6g} ou taille sous le minimum")
            self._pending().pop(a, None)
            self._entry_notes[a] = ("cancelled", "Achat annulé : le prix est retombé près du "
                                                 "stop depuis la clôture (cassure invalidée)")
            self._note_asset(a, *self._entry_notes[a])
            return None
        # Ruse : pas d'achat dans un carnet d'ordres anormal (écart achat /
        # vente très large, carnet vide ou trop mince : krach éclair,
        # manipulation, maintenance). Nouvel essai plus tard dans la journée.
        anomaly = self._book_anomaly(s, adj["cost"])
        if anomaly:
            self._defer_entry(plan, equity, now, anomaly)
            return None
        deferred = self._pending().pop(a, None) is not None
        if abs(drift) > 0.01:
            self.logger.info(
                f"[ENTRY] {a.upper()} : prix {px_now:.6g} ({drift * 100:+.1f} % "
                f"vs clôture) → quantité {plan['qty']:.6g} → {adj['qty']:.6g} "
                f"(risque {adj['risk_quote']:.2f} {self.g.quote})")
        plan = adj
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
            self._record_buy(a, now, plan["entry"], plan["qty"], plan["cost"],
                             plan["risk_quote"], "différé" if deferred else "")
            return plan
        res = s.eng.enter_planned(
            s.ctx, plan["qty"], plan["exec_price"], sl_abs=disaster,
            tp_abs=plan["ref_price"] * 100,
            meta={"module": "trendguard", "soft_stop": plan["stop"],
                  "risk_per_unit": plan["risk_quote"] / plan["qty"],
                  "equity": equity, "eff_risk_pct": self.p.risk_pct * 100,
                  "score": int(plan["mom"] * 10)},
            candle_ts=int(now.timestamp() * 1000))
        if res == v29.EntryResult.OPENED:
            s.ctx.position.highest_close = plan["ref_price"]
            s.ctx.position.soft_stop = plan["stop"]
            p = s.ctx.position
            self._record_buy(a, now, p.buy_price or plan["exec_price"], p.amount_held or plan["qty"],
                             (p.cost_basis or p.buy_price) * (p.amount_held or plan["qty"]),
                             plan["risk_quote"], "différé" if deferred else "")
        self._save_slot(s)
        return plan if res == v29.EntryResult.OPENED else None

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
        for a, e in sorted((self.state.get("pending_entries") or {}).items()):
            lines.append(f"  ⏳ achat différé {a.upper()} : {e['reason']}")
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

    STALL_DUMP_SEC = 20 * 60

    def run_forever(self) -> None:
        # Cycle bloqué plus de 20 min (appel réseau figé, débogueur en
        # pause…) : la pile de chaque thread est écrite dans
        # <journal>.blocage.txt, pour savoir OÙ le bot s'est arrêté.
        hang = None
        if self.g.log_file and self.g.log_file != os.devnull:
            try:
                hang = open(self.g.log_file + ".blocage.txt", "a", encoding="utf-8")
            except OSError:
                hang = None
        backoff = 5
        try:
            while _running and not self.stop_requested():
                self._touch_alive(force=True)
                if hang is not None:
                    faulthandler.dump_traceback_later(self.STALL_DUMP_SEC, file=hang)
                try:
                    self.run_cycle()
                    backoff = 5
                    wait = self.g.loop_interval_sec
                except ccxt.NetworkError as e:
                    self.logger.warning(f"[CYCLE] réseau: {e}")
                    wait, backoff = backoff, min(backoff * 2, 300)
                except Exception as e:
                    self.logger.exception(f"[CYCLE] KO: {e}")
                    wait, backoff = backoff, min(backoff * 2, 300)
                finally:
                    if hang is not None:
                        faulthandler.cancel_dump_traceback_later()
                _sleep(wait, self.waiting)
        finally:
            if hang is not None:
                faulthandler.cancel_dump_traceback_later()
                hang.close()


# ══════════════════════════════════════════════════════════════════════
# SÉLECTION DES CRYPTOS (écrite par le panneau, lue par le bot)
# ══════════════════════════════════════════════════════════════════════

def read_selection(gcfg: GuardConfig) -> Dict[str, Any]:
    """Choix enregistré par le panneau : auto-sélection des 10 plus
    rentables, ou sélection manuelle (cryptos cochées). Sans choix
    enregistré : sélection manuelle des 21 cryptos (réglage de référence)."""
    path = autonomy.sidecar(gcfg.lock_file, ".selection.json")
    data = autonomy._read_json(path) if path else {}
    universe = [b.lower() for b in gcfg.universe]
    mode = "auto" if data.get("mode") == "auto" else "manual"
    manual = data.get("manual")
    if not isinstance(manual, list):
        manual = universe
    wanted = {str(x).lower() for x in manual}
    return {"mode": mode, "manual": [a for a in universe if a in wanted],
            "saved": bool(data)}


def write_selection(gcfg: GuardConfig, mode: str, manual: List[str]) -> Dict[str, Any]:
    universe = [b.lower() for b in gcfg.universe]
    if mode not in ("auto", "manual"):
        raise ValueError("mode de sélection inconnu")
    unknown = [a for a in manual if str(a).lower() not in universe]
    if unknown:
        raise ValueError("crypto inconnue : " + ", ".join(map(str, unknown))[:80])
    path = autonomy.sidecar(gcfg.lock_file, ".selection.json")
    if not path:
        raise ValueError("sélection impossible : fichier de verrou non défini")
    autonomy._write_json(path, {"mode": mode, "manual": [a for a in universe if a in
                                                         {str(x).lower() for x in manual}],
                                "updated": v29._utcnow_iso()})
    return read_selection(gcfg)


# ══════════════════════════════════════════════════════════════════════
# RAISONNEMENT DU JOUR, EN CLAIR
# ══════════════════════════════════════════════════════════════════════

EXIT_WHY = {"STOP": "clôture sous son stop suiveur, la tendance s'essouffle",
            "STOP_LATE": "stop franchi pendant l'arrêt du bot",
            "DELISTED": "plus cotée sur Binance", "DELISTED_LATE": "plus cotée sur Binance",
            "EXCHANGE_STOP": "stop catastrophe, chute brutale entre deux clôtures"}
WATCH_BAND_PCT = 5.0        # « sous surveillance » : à moins de 5 % de la cassure


def _pc(x: float, d: int = 1) -> str:
    """0.021 → « +2,1 % » (format français)."""
    return f"{x * 100:+.{d}f} %".replace(".", ",")


def _explain_asset(a: str, s: Dict[str, float], gap: Optional[float], bull: bool,
                   holdings: Dict[str, ts.Holding], sold: Dict[str, str], bought: List[str],
                   notes: Dict[str, Tuple[str, str]], halted: bool,
                   p: ts.TrendParams) -> Tuple[str, str]:
    if a in sold:
        return "sold", "Vendue : " + EXIT_WHY.get(sold[a], sold[a].lower())
    if a in bought:
        return "bought", ("Achetée : cassure de son plus haut de 30 jours, tendance de fond "
                          "positive, 1 % du capital risqué")
    if a in holdings:
        h, close = holdings[a], s.get("close")
        if ts._finite(close) and close > 0:
            return "held", (f"En portefeuille ({_pc(close / h.entry - 1)}) : la tendance "
                            f"tient, stop à {_pc(h.stop / close - 1)} du cours")
        return "held", "En portefeuille : la tendance tient"
    if a in notes:
        return notes[a]
    if not s or not ts._finite(s.get("close"), s.get("prior_high"), s.get("vol"), s.get("mom")):
        return "nodata", "Pas encore assez de données"
    if s.get("age", 0) < p.min_history:
        return "young", f"Historique trop court (moins de {p.min_history} jours de cotation)"
    v30 = s.get("vol30")
    if not ts._finite(v30) or v30 < p.min_volume_usd:
        traded = f"{v30 / 1e6:.1f}".replace(".", ",") + " M$" if ts._finite(v30) else "inconnu"
        return "illiquid", (f"Pas assez échangée sur Binance ({traded} par jour, minimum "
                            f"{p.min_volume_usd / 1e6:.0f} M$)")
    if s["mom"] <= 0:
        return "weak", "Tendance de fond (90 jours) négative"
    if s["close"] <= s["prior_high"]:
        need = _pc((gap or 0.0) / 100)
        if gap is not None and gap <= WATCH_BAND_PCT:
            return "watch", f"Sous surveillance : encore {need} pour casser son plus haut de 30 jours"
        return "wait", f"Pas de cassure : il lui faut {need} pour dépasser son plus haut de 30 jours"
    if not bull:
        return "bear", ("Signal d'achat, mais marché baissier : le bot attend le retour de BTC "
                        "au-dessus de sa moyenne")
    if halted:
        return "halted", "Signal d'achat, mais arrêt d'urgence actif"
    return "full", "Signal d'achat, mais plafond atteint (positions, risque total ou liquidités)"


def explain_decision(day: str, bull: bool, btc_gap: Optional[float],
                     snap: Dict[str, Dict[str, float]], holdings: Dict[str, ts.Holding],
                     exits: List[Tuple[str, str]], bought: List[str],
                     notes: Dict[str, Tuple[str, str]], halted: bool, mult: float,
                     p: ts.TrendParams) -> Dict[str, Any]:
    """Raisonnement de la décision du jour, actif par actif : ce que le bot
    a fait, pourquoi il n'a pas acheté les autres, et ce qu'il guette.
    Mêmes règles que la décision elle-même (trend_strategy.entry_signal)."""
    sold = dict(exits)
    assets: Dict[str, Dict[str, Any]] = {}
    for a in sorted(set(snap) | set(holdings) | set(sold)):
        s = snap.get(a) or {}
        close, hi = s.get("close"), s.get("prior_high")
        gap = (hi / close - 1) * 100 if ts._finite(close, hi) and close > 0 else None
        status, text = _explain_asset(a, s, gap, bull, holdings, sold, bought, notes, halted, p)
        assets[a] = {"status": status, "text": text,
                     "breakout_gap_pct": round(gap, 2) if gap is not None else None}
    radar = sorted((a for a, x in assets.items() if x["status"] == "watch"),
                   key=lambda a: assets[a]["breakout_gap_pct"])
    btc = f" ({_pc(btc_gap / 100)})" if btc_gap is not None else ""
    lines = [f"Marché haussier : BTC au-dessus de sa moyenne 150 jours{btc}, achats autorisés."
             if bull else
             f"Marché baissier : BTC sous sa moyenne 150 jours{btc}, aucun achat et stops "
             f"resserrés pour protéger les gains."]
    acts = []
    if bought:
        acts.append(f"{len(bought)} achat(s) : {', '.join(a.upper() for a in bought)}")
    if sold:
        acts.append(f"{len(sold)} vente(s) : {', '.join(a.upper() for a in sold)}")
    if acts:
        lines.append("Aujourd'hui : " + " ; ".join(acts) + ".")
    elif holdings:
        lines.append(f"Aujourd'hui : aucun changement, {len(holdings)} position(s) conservée(s).")
    else:
        lines.append("Aujourd'hui : aucun achat, capital à l'abri en USDT.")
    deferred = [a.upper() for a, (st, _t) in sorted(notes.items()) if st == "deferred"]
    if deferred:
        lines.append(f"Ruse : achat de {', '.join(deferred)} différé (conditions d'achat "
                     f"anormales), nouvel essai toutes les 5 min.")
    if radar:
        lines.append("Sous surveillance : " + ", ".join(
            f"{a.upper()} ({_pc(assets[a]['breakout_gap_pct'] / 100)})" for a in radar[:3])
            + " avant la cassure.")
    if mult < 1:
        lines.append(f"Profil prudent actif : risque par trade × {mult:g}.")
    if halted:
        lines.append("Arrêt d'urgence actif : aucun achat.")
    return {"day": day, "bull": bull,
            "btc_gap_pct": round(btc_gap, 2) if btc_gap is not None else None,
            "lines": lines, "assets": assets, "radar": radar[:5]}


_running = True


def _stop(sig, frame):
    global _running
    _running = False


def _sleep(seconds: float, should_stop: Optional[Callable[[], bool]] = None) -> None:
    """Attente interrompue par Ctrl+C, SIGTERM ou une demande d'arrêt
    (fichier .stop déposé par le panneau), vérifiée chaque seconde."""
    end = time.time() + seconds
    while _running and time.time() < end:
        if should_stop is not None and should_stop():
            return
        time.sleep(max(0.0, min(1.0, end - time.time())))


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
                    log_file=os.devnull, lock_file=os.devnull,
                    auto_diagnose_days=0)
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
    # Le testnet ne sert qu'au mode réel : en paper, les prix du testnet
    # (marché artificiel) fausseraient les décisions.
    exchange = v29.make_binance(os.environ.get("BINANCE_API_KEY", "").strip(),
                                os.environ.get("BINANCE_API_SECRET", "").strip(),
                                gcfg.binance_testnet and gcfg.run_mode == "live")
    store = v29.Store(gcfg.db_file, logger)
    # Telegram, e-mail et WhatsApp (alerts.py, réglages dans .env).
    notifier = alerts.build_notifier(logger)
    return TrendGuardBot(gcfg, logger, exchange, store, notifier)


# Le .env lu par le bot (v29 : load_dotenv depuis le dossier du projet), et
# non celui du dossier courant : lancé d'ailleurs, set-keys écrirait des
# clés que le bot ne lirait jamais.
ENV_FILE = os.path.join(v29.APP_DIR, ".env")


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


def cmd_set_secret(env_path: str = ENV_FILE) -> int:
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


def panel_password_problem(pw: str) -> Optional[str]:
    if len(pw) < 10:
        return "10 caractères minimum"
    if pw != pw.strip():
        return "pas d'espace au début ni à la fin"
    if any(c in pw for c in "'\r\n"):
        return "l'apostrophe (') n'est pas acceptée"
    if len(set(pw)) < 5 or pw.lower() in ("motdepasse", "password12", "1234567890", "azertyuiop"):
        return "mot de passe trop simple"
    return None


def cmd_set_panel_password(env_path: str = ENV_FILE,
                           ask: Optional[Callable[[str], str]] = None) -> int:
    """Accès au panneau depuis un téléphone : mot de passe saisi MASQUÉ, deux
    fois, écrit dans .env ; accès Wi-Fi (PANEL_HOST=0.0.0.0) sur demande."""
    import getpass
    ask = ask or input
    print("Mot de passe du panneau (accès depuis un téléphone), 10 caractères minimum.")
    print("(Rien ne s'affiche pendant la saisie : c'est normal.)")
    try:
        pw = getpass.getpass("Mot de passe : ")
        if getpass.getpass("Confirmez : ") != pw:
            print("❌ Les deux saisies diffèrent. Rien n'a été modifié.")
            return 1
        problem = panel_password_problem(pw)
        if problem:
            print(f"❌ Mot de passe refusé : {problem}. Rien n'a été modifié.")
            return 1
        answer = ask("Autoriser l'accès depuis un téléphone sur le même Wi-Fi ? (o/N) ")
        lan = answer.strip().lower() in ("o", "oui", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        print("\nAnnulé. Rien n'a été modifié.")
        return 1
    set_env_var(env_path, "PANEL_PASSWORD", f"'{pw}'")     # guillemets simples : texte exact
    if lan:
        set_env_var(env_path, "PANEL_HOST", "0.0.0.0")
    print(f"✅ Mot de passe enregistré dans {env_path} (fichier privé, jamais commité).")
    if lan:
        print("✅ Accès Wi-Fi activé au prochain démarrage du panneau (redémarrage de "
              "l'ordinateur). Tout de suite : python trendguard_bot.py panel --host 0.0.0.0 "
              "--port 8766 (tâche VS Code « Panneau — accès téléphone »).")
    print("Changer le mot de passe puis redémarrer le panneau déconnecte tous les appareils.")
    return 0


def _auth_hint(msg: str) -> str:
    """Traduit un refus d'authentification Binance."""
    ip = re.search(r"request ip:\s*([0-9A-Fa-f.:]+)", msg)
    if "-2015" in msg:
        return ("clé inconnue sur ce compte, adresse IP non autorisée"
                + (f" (votre adresse IP vue par Binance : {ip.group(1)})" if ip else "")
                + " ou lecture du compte non autorisée")
    if "-1022" in msg:
        return "API Key reconnue mais Secret Key incorrecte"
    if "-2014" in msg:
        return "format d'API Key refusé"
    return msg[:160]


def check_api_keys(key: str, secret: str, testnet: bool,
                   factory: Callable[..., Any] = v29.make_binance
                   ) -> Tuple[bool, str, bool]:
    """Lecture du compte (aucun ordre). Retourne (acceptées, raison,
    refus d'authentification) ; une panne réseau n'est pas un refus."""
    ex = factory(key, secret, testnet)
    try:
        v29.sync_exchange_clock(ex, samples=3)
        ex.fetch_balance()
        return True, "", False
    except ccxt.AuthenticationError as e:
        return False, _auth_hint(str(e)), True
    except ccxt.NetworkError as e:
        return False, f"Binance injoignable ({type(e).__name__})", False
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:160]}", False


def cmd_set_keys(env_path: str = ENV_FILE, ask: Optional[Callable[[str], str]] = None,
                 read: Optional[Callable[[str], str]] = None,
                 factory: Callable[..., Any] = v29.make_binance, out=None) -> int:
    """Enregistre API Key + Secret Key (saisie MASQUÉE) après les avoir fait
    accepter par Binance (lecture du compte, aucun ordre). Corrige seul les
    deux erreurs courantes : clés inversées, clés du testnet déclarées
    réelles (ou l'inverse). Rien n'est écrit si Binance refuse."""
    import getpass
    ask = ask or getpass.getpass
    read = read or input
    out = out or sys.stdout
    say = lambda msg="": print(msg, file=out)       # noqa: E731
    say("Enregistrement des clés API Binance (rien ne s'affiche pendant la "
        "saisie : c'est normal).")
    try:
        choice = read("Compte : 1 = testnet (conseillé pour commencer), "
                      "2 = compte réel [1] : ").strip() or "1"
        testnet = choice != "2"
        key = clean_api_secret(ask("API Key    : "))
        secret = clean_api_secret(ask("Secret Key : "))
    except ValueError as e:
        say(f"❌ Clé refusée : {e}. Rien n'a été modifié.")
        return 1
    except (EOFError, KeyboardInterrupt):
        say("\nAnnulé. Rien n'a été modifié.")
        return 1
    if key == secret:
        say("❌ API Key et Secret Key identiques : ce sont deux valeurs "
            "différentes sur Binance. Rien n'a été modifié.")
        return 1
    where = lambda t: "testnet" if t else "compte réel"   # noqa: E731
    say(f"Vérification auprès de Binance ({where(testnet)}), sans aucun ordre…")
    attempts = [(key, secret, testnet, ""),
                (secret, key, testnet, "API Key et Secret Key étaient inversées"),
                (key, secret, not testnet,
                 f"ces clés sont celles du {where(not testnet)}"),
                (secret, key, not testnet,
                 f"clés inversées et appartenant au {where(not testnet)}")]
    refusals: List[Tuple[str, bool]] = []
    for k, sec, tn, fix in attempts:
        ok, reason, auth = check_api_keys(k, sec, tn, factory)
        if ok:
            set_env_var(env_path, "BINANCE_API_KEY", k)
            set_env_var(env_path, "BINANCE_API_SECRET", sec)
            set_env_var(env_path, "BINANCE_TESTNET", "true" if tn else "false")
            if fix:
                say(f"ℹ️  Corrigé automatiquement : {fix}.")
            say(f"✅ Clés acceptées par Binance ({where(tn)}) et enregistrées dans "
                f"{env_path} (fichier privé, jamais commité).")
            say("Étape suivante : python trendguard_bot.py verify "
                "(aucun ordre n'est passé).")
            return 0
        if not auth:
            # Panne réseau : les autres essais n'ont pas eu lieu, un refus
            # ne peut pas être conclu.
            say(f"❌ Vérification impossible : {reason}. Rien n'a été modifié ; "
                f"relancer une fois la connexion rétablie.")
            return 1
        refusals.append((reason, tn))
    # API Key reconnue (seul le secret est faux) : c'est le diagnostic le
    # plus précis. Sinon, le premier refus (compte choisi, adresse IP).
    reason, tn = next(((r, t) for r, t in refusals if "Secret Key incorrecte" in r),
                      refusals[0])
    say(f"❌ Binance refuse ces clés ({where(tn)}) : {reason}. Rien n'a été modifié.")
    if "adresse IP" in reason:
        # Binance n'indique pas toujours l'adresse vue (« request ip »).
        ip = ("l'adresse IP ci-dessus" if "vue par Binance" in reason
              else "l'adresse IP publique de ce PC")
        say("   Sur Binance ▸ Gestion des API : vérifier que la clé est active, que "
            f"« Activer la lecture » est coché, et autoriser {ip} "
            "(ou retirer la restriction IP le temps du test).")
    return 1


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
            say("ℹ️  Pas de clé API dans .env : vérification publique seulement "
                "(droits, soldes et order/test demandent une clé ; pour les "
                "enregistrer : python trendguard_bot.py set-keys).\n")
            return cmd_verify_public(gcfg, now=now, out=out)
        exchange = v29.make_binance(key, secret, gcfg.binance_testnet)
    _forbid_orders(exchange)
    say(f"Vérification {'TESTNET' if gcfg.binance_testnet else 'BINANCE RÉEL'} "
        f"— aucun ordre ne sera passé")

    say("\n── 1. Droits de la clé API")
    try:
        r = exchange.sapi_get_account_apirestrictions()
    except ccxt.AuthenticationError as e:
        say(f"  ❌ Clé refusée par Binance : {_auth_hint(str(e))}")
        say("     → python trendguard_bot.py set-keys vérifie les clés auprès de "
            "Binance et corrige les clés inversées ou du mauvais compte.")
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
        try:
            snap, bull, prices = bot._market_snapshot(now, day)
        except DecisionDeferred as e:
            say(f"  ❌ Décision du jour impossible : {e}")
            return 1
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


def cmd_verify_public(gcfg: GuardConfig, exchange: Any = None,
                      now: Optional[datetime] = None, out=None) -> int:
    """Vérification sur le VRAI Binance SANS clé API ni ordre : connexion,
    horloge, règles de marché des paires, puis construction complète des
    ordres que le bot passerait aujourd'hui (quantités arrondies aux pas
    Binance, montants minimums, stop catastrophe, requêtes ccxt préparées
    mais jamais envoyées). Retourne 0 si tout est conforme."""
    out = out or sys.stdout
    say = lambda msg="": print(msg, file=out)       # noqa: E731
    ok = True
    exchange = exchange or v29.make_binance(testnet=gcfg.binance_testnet)
    _forbid_orders(exchange)
    say(f"Vérification {'TESTNET' if gcfg.binance_testnet else 'BINANCE RÉEL'} "
        f"— mode public, aucun ordre")

    say("\n── 1. Connexion")
    if callable(getattr(exchange, "fetch_time", None)):
        clock = v29.sync_exchange_clock(exchange)
        if clock is None:
            say("  ❌ Binance injoignable (heure du serveur illisible)")
            return 1
        say(f"  Binance joignable ✓ (latence {clock.latency_ms:.0f} ms)")
        say(f"  Heure : {v29.describe_clock(clock.offset_ms, clock.uncertainty_ms)}"
            f" → le bot utilise l'heure de Binance")
    capital = gcfg.max_capital or gcfg.paper_capital
    sim = dataclasses.replace(gcfg, run_mode="paper", paper_capital=capital,
                              db_file=":memory:", log_file=os.devnull,
                              lock_file=os.devnull, auto_diagnose_days=0,
                              heartbeat_min=0)
    quiet = logging.getLogger("trendguard.verify")
    reasons = logging.StreamHandler(out)
    reasons.setLevel(logging.WARNING)
    reasons.setFormatter(logging.Formatter("  ⚠️  %(message)s"))
    quiet.handlers[:] = [reasons]
    quiet.setLevel(logging.WARNING)
    quiet.propagate = False
    store = v29.Store(":memory:", quiet)
    bot = TrendGuardBot(sim, quiet, exchange, store,
                        v29.Notifier("", "", logger=quiet))
    t0 = time.time()
    try:
        if not bot.boot():
            say("  ❌ Chargement des marchés impossible (causes ci-dessus).")
            return 1
        say(f"  Marchés chargés en {time.time() - t0:.1f} s ✓")

        say("\n── 2. Règles Binance des paires")
        missing = [b for b in gcfg.universe if b not in bot.slots]
        if missing:
            ok = False
            say(f"  ❌ Paires absentes ou suspendues : {', '.join(missing)}")
        for base, sl in sorted(bot.slots.items()):
            r = sl.ex.rules
            try:
                stop = sl.ex.stop_order_type
            except Exception as e:
                ok = False
                say(f"  ❌ {base:<5} aucun ordre stop disponible ({e})")
                continue
            say(f"  ✓ {base:<5} stop {stop:<15} minimum {r.min_cost:g} USDT, "
                f"pas de quantité {r.step_size:g}, pas de prix {r.tick_size:g}")

        now = now or v29._utcnow()
        day = last_closed_day(now, sim.decision_delay_sec)
        try:
            snap, bull, _prices = bot._market_snapshot(now, day)
        except DecisionDeferred as e:
            say(f"\n  ❌ Décision du jour impossible : {e}")
            return 1
        plans = ts.plan_entries({}, snap, bull, capital, capital, sim.params)
        say(f"\n── 3. Ordres que le bot passerait aujourd'hui (capital simulé "
            f"{capital:,.2f} USDT, bougie du {day}, régime BTC "
            f"{'HAUSSIER' if bull else 'BAISSIER'})")
        if capital < MIN_LIVE_CAPITAL:
            ok = False
            say(f"  ❌ Capital simulé < {MIN_LIVE_CAPITAL:.0f} USDT : la plupart des "
                f"ordres seraient sous le minimum de Binance.")
        build = getattr(exchange, "create_order_request", None)
        cash = capital
        for plan in plans:
            a = plan["asset"].upper()
            sl = bot.slots[a]
            t = sl.ex.get_ticker()
            adj = ts.reprice_entry(plan, float(t["ask"]), capital, cash, sim.params)
            if adj is None:
                say(f"  ↷ {a:<5} achat annulé : prix actuel trop proche du stop")
                continue
            errors: List[str] = []
            qty = sl.ex.round_amount(adj["qty"])
            notional = qty * float(t["ask"])
            if qty <= 0 or notional < sl.ex.min_notional() * 1.05:
                errors.append(f"achat de {notional:.2f} USDT sous le minimum "
                              f"({sl.ex.min_notional():g} USDT)")
            net = sl.ex.round_amount(qty * (1 - sim.params.fee))  # frais en base
            disaster = adj["stop"] - gcfg.catastrophe_atr * adj["vol"]
            if disaster <= 0:
                disaster = adj["stop"] * 0.5
            stop_px = sl.ex.round_price(disaster, "down")
            if not 0 < stop_px < float(t["last"]):
                errors.append(f"stop {stop_px:g} au-dessus du prix actuel")
            try:
                stop_type = sl.ex.stop_order_type
                if callable(build):
                    build(sl.symbol, "market", "buy", qty, None,
                          {"newClientOrderId": "TGVERIFY"})
                    params: Dict[str, Any] = {"stopPrice": stop_px}
                    price = None
                    if stop_type == "STOP_LOSS_LIMIT":
                        price = sl.ex.round_price(
                            stop_px * (1 - sl.cfg.stop_limit_offset_pct), "down")
                        params["timeInForce"] = "GTC"
                    build(sl.symbol, stop_type, "sell", net, price, params)
            except Exception as e:
                errors.append(f"requête refusée : {type(e).__name__}: {str(e)[:100]}")
            if errors:
                ok = False
                say(f"  ❌ {a:<5} " + " ; ".join(errors))
            else:
                say(f"  ✓ {a:<5} achat {qty:g} ≈ {notional:,.2f} USDT, puis stop "
                    f"{stop_type} de {net:g} à {stop_px:g} (stop de clôture "
                    f"{adj['stop']:.6g}, risque {adj['risk_quote']:.2f} USDT)")
            cash -= adj["cost"]
        if not plans:
            say("  Aucun achat prévu aujourd'hui.")
    finally:
        store.close()
    say("\n── 4. Reste à vérifier avec une clé API (python trendguard_bot.py verify)")
    say("  Droits de la clé (retrait interdit), soldes réels et validation des "
        "ordres signés par Binance (order/test).")
    say("\n" + ("✅ Tout est conforme côté marché." if ok else
                "❌ Points à corriger (voir ci-dessus)."))
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


def cmd_diagnose(gcfg: GuardConfig, out_path: Optional[str] = None,
                 exchange: Any = None, now: Optional[datetime] = None) -> int:
    """Diagnostic complet en lecture seule (aucun ordre, bot arrêté ou non)."""
    if exchange is None:
        # Historique public (data-api.binance.vision) : sans la liste des
        # marchés (4,7 Mo) et accessible depuis n'importe quel serveur.
        exchange = v29.PublicKlines()
    _forbid_orders(exchange)
    if now is None:
        v29.sync_exchange_clock(exchange, samples=3)     # heure de Binance
        now = v29._utcnow()
    quiet = logging.getLogger("trendguard.diagnose")
    quiet.handlers[:] = [logging.NullHandler()]
    quiet.propagate = False
    state: Dict[str, Any] = {}
    holdings: List[Dict[str, Any]] = []
    if gcfg.db_file != ":memory:" and os.path.exists(gcfg.db_file):
        store = v29.Store(gcfg.db_file, quiet)
        try:
            state = store.get_kv(TrendGuardBot.STATE_KEY) or {}
            if gcfg.run_mode == "live":
                for base in gcfg.universe:
                    ctx = store.load_context(key=f"ctx:{base}/{gcfg.quote}")
                    if ctx and ctx.position.in_position:
                        p = ctx.position
                        holdings.append({"asset": base.lower(), "qty": p.amount_held,
                                         "entry": p.buy_price,
                                         "stop": p.soft_stop or p.sl_price,
                                         "risk_quote": p.risk_quote_initial})
        finally:
            store.close()
    if gcfg.run_mode == "paper" and "paper" in state:
        holdings = [{"asset": a, "qty": h["qty"], "entry": h["entry"],
                     "stop": h["stop"], "risk_quote": h["risk_quote"]}
                    for a, h in state["paper"]["holdings"].items()]
    running: Optional[bool] = None
    probe = v29.ProcessLock(gcfg.lock_file)
    try:
        probe.acquire()
        probe.release()
        running = False
    except SystemExit as e:
        running = "déjà" in str(e)
    equity = float(state.get("last_equity") or (
        gcfg.paper_capital if gcfg.run_mode == "paper" else 0.0))
    day = last_closed_day(now, gcfg.decision_delay_sec)
    print(f"Analyse en cours ({len(gcfg.universe)} paires, historique Binance "
          f"depuis 2018)…", flush=True)
    findings = dg.run_diagnosis(exchange, gcfg.params, list(gcfg.universe), state,
                                holdings, equity, day, now, db_file=gcfg.db_file,
                                running=running, quote=gcfg.quote,
                                kill_drawdown=gcfg.kill_drawdown)
    text = dg.render(findings, f"({gcfg.run_mode.upper()}, {day})")
    print(text)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"\nRapport enregistré : {out_path}")
    return 1 if dg.verdict(findings) == "ALERTE" else 0


def main(argv: Optional[List[str]] = None) -> int:
    v29.ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="TrendGuard Bot (Binance Spot)")
    ap.add_argument("cmd", choices=["run", "once", "status", "resume", "docs",
                                    "health", "replay", "set-secret",
                                    "set-keys", "verify", "diagnose", "panel",
                                    "supervise", "stop", "autostart",
                                    "set-panel-password"])
    ap.add_argument("action", nargs="?", default="status", choices=["on", "off", "status"],
                    help="(autostart) on = activer, off = désactiver, status = état")
    ap.add_argument("--out", default=None, help="(diagnose) fichier du rapport")
    ap.add_argument("--data", default="data", help="(replay) dossier Coin Metrics")
    ap.add_argument("--start", default="2025-06-01", help="(replay) début")
    ap.add_argument("--end", default=None, help="(replay) fin")
    ap.add_argument("--capital", type=float, default=10_000.0,
                    help="(replay) capital initial USDT")
    ap.add_argument("--host", default=v29._env_s("PANEL_HOST", "127.0.0.1"),
                    help="(panel) 127.0.0.1 = ce PC ; 0.0.0.0 = réseau local")
    ap.add_argument("--port", type=int, default=v29._env_i("PANEL_PORT", 8765),
                    help="(panel) port web")
    ap.add_argument("--demo", action="store_true", help="(panel) données fictives")
    ap.add_argument("--no-open", action="store_true", help="(panel) sans ouvrir le navigateur")
    ap.add_argument("--login", action="store_true",
                    help="(supervise, panel) lancé à l'ouverture de session")
    args = ap.parse_args(argv)
    if args.login:
        os.chdir(v29.APP_DIR)            # clé Run de Windows : dossier courant quelconque
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
    # Saisie des clés avant la lecture de la configuration : une autre
    # variable invalide dans .env ne doit pas empêcher de les enregistrer.
    if args.cmd == "set-secret":
        return cmd_set_secret()
    if args.cmd == "set-keys":
        return cmd_set_keys()
    if args.cmd == "autostart":
        return autonomy.cmd_autostart(args.action)
    if args.cmd == "set-panel-password":
        return cmd_set_panel_password()
    try:
        gcfg = load_guard_config_from_env()
    except ValueError as e:
        print(f"Configuration invalide : {e}", file=sys.stderr)
        return 2
    if args.cmd == "panel":
        from panel import server as panel_server
        return panel_server.main(gcfg, args.host, args.port, args.demo,
                                 not (args.no_open or args.login))
    if args.cmd == "supervise":
        return autonomy.run_supervisor(gcfg, login=args.login)
    if args.cmd == "stop":
        return autonomy.cmd_stop(gcfg)
    if args.cmd == "verify":
        return cmd_verify(gcfg)
    if args.cmd == "diagnose":
        return cmd_diagnose(gcfg, args.out)
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
    awake: Optional[autonomy.KeepAwake] = None
    try:
        bot.logger.info(f"TrendGuard — {gcfg.run_mode.upper()}"
                        f"{' TESTNET' if gcfg.binance_testnet and gcfg.run_mode == 'live' else ''} — "
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
        if gcfg.keep_awake:
            awake = autonomy.KeepAwake(bot.logger)
            awake.start()
        bot._touch_alive(force=True)
        delay = 30
        while _running and not bot.stop_requested() and not bot.boot():
            bot.logger.warning(f"[BOOT] nouvelle tentative dans {delay} s")
            _sleep(delay, bot.waiting)
            delay = min(delay * 2, 600)
        if _running and not bot.stop_requested():
            bot.run_forever()
        return 0
    finally:
        if awake is not None:
            awake.stop()
        bot.store.close()
        bot.notifier.close()
        v29.release_locks(locks)


if __name__ == "__main__":
    sys.exit(main())
