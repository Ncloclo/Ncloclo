"""Le bot TrendGuard : décision quotidienne, exécution, surveillance des stops.

Partie du bot TrendGuard (paquet trendguard, point d'entrée : trendguard_bot.py).
"""
from __future__ import annotations

import faulthandler
import logging
import math
import os
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import ccxt
import pandas as pd

import v29

from . import (
    anticipation,
    audit,
    autonomy,
    comite,
    contrats,
    donnees,
    evolution,
    garde,
    learning,
    moteur_portefeuille,
    moteur_risque,
    politique,
    porte,
    postmortem,
    qualite,
    regimes,
    risque,
    savoir,
    stress,
)
from . import trend_strategy as ts
from .bot_execution import ExecutionMixin
from .bot_routines import RoutinesMixin
from .bot_types import DecisionDeferred, Slot, last_closed_day  # noqa: F401  (réexportés)
from .config import DAY_MS, GuardConfig
from .explain import EXIT_SHORT, explain_decision
from .selection import read_selection
from .texte import fr


class TrendGuardBot(RoutinesMixin, ExecutionMixin):
    """Le bot : démarrage, cycle, décision quotidienne, sélection et
    boucle. Routines (bot_routines.py) et exécution (bot_execution.py)
    en sont les deux autres parties."""
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
        self._restart_flag = False          # redémarrage prévu (nouvelle version)
        # Journal d'audit (audit.py) : le bot en est le seul écrivain.
        self.audit = audit.AuditLog(audit.path_for(gcfg))
        # Comité d'agents (comite.py) : registre gardé tout le temps du
        # processus (quarantaine, disjoncteurs, mesures de chaque agent).
        self.agents = comite.registry()
        # Journal financier (donnees.py) : décisions, signaux, contrôles du
        # risque, ordres, exécutions, trades, dans la base du bot.
        try:
            self.journal: Optional[donnees.Journal] = donnees.Journal(donnees.path_for(gcfg),
                                                                       donnees.code_version())
        except Exception as e:           # un journal illisible ne bloque jamais le bot
            logger.error(f"[DONNÉES] journal financier indisponible : {e}")
            self.journal = None
        self._started_at = time.time()
        self._last_alive = 0.0
        self._last_selection_try = 0.0
        self._last_anticipation = 0.0
        # Disponibilité (uptime.py) : relevée seulement en marche continue
        # (run_forever), pas pour un cycle isolé ni un rejeu.
        self.track_uptime = False
        self._tries_since: Optional[float] = None
        self._last_book_sample = 0.0       # apprentissage libre des carnets (learning.py)
        # Notes d'exécution du jour (achat différé, annulé) pour le raisonnement.
        self._entry_notes: Dict[str, Tuple[str, str]] = {}
        self._last_close: Optional[pd.DataFrame] = None   # apprentissage de la veille
        # Palier de risque choisi par l'évolution encadrée (1 à 2 × TG_RISK_PCT).
        self.risk_step = 1.0
        self._last_power = 0.0              # alimentation du portable relue

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
        """Démarrage : marchés de Binance chargés, une place (`Slot`) par paire
        cotée de l'univers, état repris de la base ; en réel, test des ordres
        conditionnels et réconciliation avec Binance. False si le bot ne doit
        pas démarrer (BTC absent, ordres inconnus…)."""
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
        # Dernier envoi connu de chaque canal d'alerte : la cause à corriger
        # (mot de passe refusé…) reste affichée après un redémarrage.
        last = getattr(self.notifier, "last", None)
        if isinstance(last, dict) and not last and isinstance(self.state.get("alerts_last"), dict):
            last.update({k: dict(v) for k, v in self.state["alerts_last"].items() if isinstance(v, dict)})
        self._apply_evolution()
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
        self._capital_basis()
        self._save_state()
        n_pos = len(self._holdings())
        self.logger.info(f"[BOOT] TrendGuard {self.g.run_mode.upper()} — "
                         f"{len(self.slots)} paires, {n_pos} position(s), "
                         f"risque {fr(self.p.risk_pct*100, '.2f')} %/trade")
        return True

    # ---------- Garde et risque d'un jour ----------

    def _disk_free_gb(self) -> Optional[float]:
        """Place libre sur le disque du bot, en Go (None si illisible)."""
        try:
            import shutil
            return shutil.disk_usage(v29.APP_DIR).free / 2 ** 30
        except OSError:
            return None

    def _day_risk(self, day: str, close: pd.DataFrame, equity: float) -> None:
        """Risque du portefeuille après la décision : VaR et CVaR d'un jour
        (risque.py) et tests de résistance (stress.py) ; une mesure ratée ne
        bloque jamais la décision."""
        r, rows = None, []
        try:
            last = close.iloc[-1]
            held = {a: h for a, h in self._holdings().items()
                    if a in close.columns and math.isfinite(float(last[a]))}
            values = {a: h.qty * float(last[a]) for a, h in held.items()}
            r = risque.var_cvar(close, values, equity)
            rows = stress.scenarios({a: {"qty": h.qty, "price": float(last[a]), "stop": h.stop}
                                     for a, h in held.items()}, equity - sum(values.values()), equity)
        except Exception as e:
            self.logger.warning(f"[RISQUE] mesure impossible : {e}")
        self.state["risque_jour"] = dict(r, day=day) if r else {}
        peak = float(self.state.get("peak_equity") or equity)
        dd = max(0.0, 1 - equity / peak) if peak > 0 else 0.0
        self.state["stress"] = ({"day": day, "rows": rows, "text": stress.describe(rows, self.g.kill_drawdown, dd)}
                                if rows else {})

    # ---------- Capital confié au bot ----------

    def _capital_basis(self) -> None:
        """Capital confié au bot (TG_MAX_CAPITAL, et TG_PAPER_CAPITAL en paper)
        changé depuis le dernier démarrage. En paper : nouvel essai avec le
        nouveau capital, l'ancien portefeuille fictif archivé (ses positions,
        taillées pour l'ancien capital, n'ont plus de sens). Dans tous les cas :
        le plus haut repart du nouveau capital. Sans cela, l'arrêt d'urgence
        comparerait le nouveau capital au plus haut de l'ancien et se
        déclencherait à tort (le 4 octobre : 10 074 USDT contre 100)."""
        basis = {"max_capital": float(self.g.max_capital),
                 "paper_capital": None if self.live else float(self.g.paper_capital)}
        old = self.state.get("capital_basis")
        if not isinstance(old, dict):
            # Suivi absent (version précédente) : le capital du paper se lit
            # dans start_equity ; le plafond, lui, n'était pas suivi.
            old = {"max_capital": basis["max_capital"],
                   "paper_capital": None if self.live else float(self.state.get("start_equity")
                                                                 or basis["paper_capital"])}
        self.state["capital_basis"] = basis
        if old == basis:
            return
        if not self.live and old.get("paper_capital") != basis["paper_capital"]:
            self._restart_paper(old.get("paper_capital"))
            return
        self.state["peak_equity"] = None          # repart du capital actuel à la décision
        self.logger.warning(f"[CAPITAL] plafond confié au bot changé : {fr(old.get('max_capital') or 0, ',.0f')} → "
                            f"{fr(basis['max_capital'], ',.0f')} {self.g.quote} ; le plus haut repart du "
                            "capital actuel (arrêt d'urgence mesuré sur ce capital)")

    def _restart_paper(self, old_capital: Any) -> None:
        """Nouvel essai paper au capital de TG_PAPER_CAPITAL ; l'ancien
        (portefeuille, trades clos, dates) est gardé dans paper_archive."""
        st, book = self.state, self.state.get("paper") or {}
        st.setdefault("paper_archive", []).append({
            "capital": old_capital, "started_at": st.get("started_at"), "ended_at": v29._utcnow_iso(),
            "cash": book.get("cash"), "holdings": book.get("holdings") or {}, "trades": st.get("trades") or [],
            "realized_pnl_total": st.get("realized_pnl_total", 0.0),
            "halted": bool(st.get("halted")), "halt_reason": st.get("halt_reason")})
        st["paper"] = {"cash": float(self.g.paper_capital), "holdings": {}}
        st.update(start_equity=float(self.g.paper_capital), peak_equity=None, last_equity=None,
                  trades=[], realized_pnl_total=0.0, started_at=v29._utcnow_iso(), halted=False,
                  halt_reason=None, halted_at=None, resume_note=None, risk_mult=1.0)
        for k in ("pending_entries", "auto_resumed_at", "auto_resumes"):
            st.pop(k, None)
        held = ", ".join(a.upper() for a in sorted(book.get("holdings") or {})) or "aucune position"
        msg = (f"capital du paper changé : {fr(float(old_capital or 0), ',.0f')} → "
               f"{fr(self.g.paper_capital, ',.0f')} {self.g.quote}. Nouvel essai paper avec ce capital ; "
               f"l'ancien ({held}) est archivé. Un arrêt d'urgence venu de ce changement, et non d'une "
               "perte, est levé.")
        self.logger.warning(f"[CAPITAL] {msg}")
        self.notifier(f"ℹ️ TrendGuard : {msg}", dedup_key=f"tg-capital-{self.g.paper_capital}")
        self._audit("capital.nouvel_essai", "paper", "appliqué", reason=msg,
                    before={"capital": old_capital}, after={"capital": float(self.g.paper_capital)})

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
        trace = trade.get("trace") or {}
        self._journal("record_trade", trade, float(trade.get("qty") or 0.0), self.g.run_mode, self.p.fee)
        self._audit("ordre.vente", trade["asset"], "exécuté", reason=str(trade.get("reason") or ""),
                    authorization=trace.get("authorization_id", ""), correlation_id=trace.get("decision_id", ""),
                    after={"exit": contrats.money_or_none(trade.get("exit"), self.g.quote),
                           "pnl": contrats.money_or_none(trade.get("pnl"), self.g.quote),
                           "r": round(float(trade.get("r") or 0.0), 4), "lesson": trade.get("lesson")})

    def _audit(self, action: str, resource: str, result: str, actor: str = "bot",
               now: Optional[datetime] = None, **kw: Any) -> bool:
        """Une ligne de plus au journal d'audit (audit.py). False si elle
        n'a pas pu être écrite (la porte refuse alors l'achat) ; jamais
        d'exception."""
        try:
            self.audit.append(actor, action, resource, result, now=now or self._now, **kw)
            return True
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.logger.error(f"[AUDIT] écriture impossible ({action}) : {e}")
            return False

    def _journal(self, method: str, *args: Any, **kw: Any) -> Any:
        """Écriture dans le journal financier (donnees.py) ; une erreur est
        notée, jamais bloquante (la trace qui bloque un achat est l'audit)."""
        if self.journal is None:
            return None
        try:
            return getattr(self.journal, method)(*args, **kw)
        except Exception as e:
            self.logger.warning(f"[DONNÉES] {method} non enregistré : {e}")
            return None

    def _journal_signals(self, day: str, snap: Dict[str, Dict[str, float]],
                         exits: List[Tuple[str, str]]) -> None:
        """Signaux du jour (achats signalés, ventes) et leur issue, lue dans
        le raisonnement."""
        assets = (self.state.get("reasoning") or {}).get("assets") or {}
        rows = []
        for a, s in sorted(snap.items()):
            if ts.entry_signal(s, self.p):
                row = assets.get(a) or {}
                rows.append({"asset": a, "direction": "LONG", "close": s.get("close"),
                             "breakout_level": s.get("prior_high"), "momentum": s.get("mom"),
                             "volatility": s.get("vol"), "outcome": row.get("status") or "signal",
                             "detail": row.get("text")})
        for a, reason in exits:
            rows.append({"asset": a, "direction": "EXIT", "close": (snap.get(a) or {}).get("close"),
                         "outcome": "sold", "detail": reason})
        self._journal("record_signals", f"D-{day}", rows)

    def _watch_safe_mode(self) -> "porte.SafeModeState":
        """Mode sûr en vigueur (commande mode-sur) ; activé ou levé depuis
        le cycle précédent : noté dans le journal du bot et l'audit."""
        st = porte.safe_mode(self.g)
        prev = self.state.get("mode_sur") or {}
        if st.active != bool(prev.get("active")):
            if st.active:
                self.logger.warning(f"[MODE SÛR] activé ({st.reason}) : plus aucun achat ; positions "
                                    "toujours protégées et vendues selon leurs règles")
            else:
                self.logger.info("[MODE SÛR] levé : les achats reprennent à la prochaine décision")
            self._audit("mode_sur.active" if st.active else "mode_sur.leve", "bot", "appliqué",
                        actor=st.activated_by or "vous", reason=st.reason)
        self.state["mode_sur"] = {"active": st.active, "reason": st.reason, "since": st.activated_at}
        return st

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

    def _live_info(self, s: Slot) -> Optional[Dict[str, Any]]:
        """Ce qu'il faut garder d'une position réelle avant sa vente, pour
        l'analyse après trade (postmortem.py)."""
        p = s.ctx.position
        if not p.in_position:
            return None
        return {"qty": p.amount_held, "risk": p.risk_quote_initial, "stop": p.soft_stop or p.sl_price,
                "regime": (self.state.get("entry_regimes") or {}).get(s.base.lower()),
                "trace": (self.state.get("entry_traces") or {}).get(s.base.lower())}

    def _harvest_live_trade(self, s: Slot, n_before: int, reason: str,
                            opened_at: Optional[str] = None, buy_price: float = 0.0,
                            info: Optional[Dict[str, Any]] = None) -> None:
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
            info = info or {}
            if info.get("trace"):
                trade["trace"] = info["trace"]
            if info.get("qty"):
                trade["qty"] = float(info["qty"])
            close = getattr(self, "_last_close", None)
            series = close[s.base.lower()] if close is not None and s.base.lower() in close else None
            trade = postmortem.enrich(trade, series, float(info.get("qty") or 0.0), float(info.get("risk") or 0.0),
                                      info.get("stop"), info.get("regime"))
            (self.state.get("entry_regimes") or {}).pop(s.base.lower(), None)
            (self.state.get("entry_traces") or {}).pop(s.base.lower(), None)
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
        for path, restart in ((self.g.stop_file, False),
                              (autonomy.sidecar(self.g.lock_file, ".restart"), True)):
            if not path or not os.path.exists(path):
                continue
            stale = os.path.getmtime(path) < self._started_at - 60
            try:
                os.remove(path)
            except OSError:
                pass
            if stale:
                continue
            self._stop_flag = True
            self._restart_flag = restart
            self.logger.info("[MISE À JOUR] nouvelle version installée : redémarrage du bot"
                             if restart else
                             "[ARRÊT] demandé depuis le panneau de contrôle : arrêt propre")
            break
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
        """Un cycle de la boucle : heure de Binance, stops entretenus, veille des
        annonces, décision quotidienne après la clôture (ou reportée), achats
        différés, disponibilité, apprentissage, rapport de 00:30, heartbeat,
        anticipation, auto-diagnostic et veille du jour."""
        if self._tries_since is None:
            self._tries_since = time.time()
        self._sync_clock()
        now = now or v29._utcnow()          # heure de Binance
        self._now = now
        if self.live:
            self._maintain_live()
        else:
            self._maintain_paper()
        day = last_closed_day(now, self.g.decision_delay_sec)
        # Veille rapide : annonces officielles de Binance relues toutes les
        # heures, pas seulement avant la décision.
        self._refresh_vetoes(now)
        if self.state.get("last_decision_day") != day:
            self._apply_evolution()
            self._refresh_vetoes(now, max_age=600)
            try:
                self.daily_decision(now, day)
                self.state.pop("decision_deferred_since", None)
                self._launch_evolution(day)
            except DecisionDeferred as e:
                self._decision_deferred(day, str(e))
        self._refresh_selection(now)
        self._watch_safe_mode()
        if self.state.get("pending_entries"):
            try:
                self._retry_pending(now)
            except Exception as e:
                self.logger.warning(f"[RUSE] nouvel essai d'achat impossible : {e}")
        try:
            if self.track_uptime:
                self._note_downtime()
                self._watch_power()
            self._keep_alert_status()
        except Exception as e:           # un simple relevé : jamais bloquant
            self.logger.warning(f"[REPRISE] disponibilité non relevée : {e}")
        try:
            self._sample_books()
            self._learn_forecast(now)
        except Exception as e:           # apprendre ne bloque jamais le trading
            self.logger.warning(f"[APPRENTISSAGE] relevé impossible : {e}")
        self._launch_report(now)
        self._launch_expert(now)
        self._launch_savoir()
        # Horloge réelle (et non `now`, simulé en rejeu) : sert au contrôle
        # de santé du conteneur.
        self.state["last_cycle_ts"] = time.time()
        self._tries_since = None
        self._save_state()
        self._heartbeat(now)
        self._anticipate(now)
        self._auto_diagnose(now, day)
        self._daily_watch(now, day)

    def _load_market(self, now: datetime
                     ) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame], pd.Series]:
        """Clôtures journalières FERMÉES à `now`, indicateurs par actif et
        régime BTC, calculés une seule fois pour tous les jours demandés."""
        now_ms = int(now.timestamp() * 1000)
        closes, vols = {}, {}
        failed: List[str] = []
        self._ohlcv_bad: Dict[str, int] = {}
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
            # Contrat OHLCV.v1 : une bougie incohérente (plus haut sous la
            # clôture, volume négatif…) rend la crypto douteuse aujourd'hui ;
            # traitée comme une donnée absente (BTC : décision reportée).
            bad = contrats.ohlcv_violations(df.tail(qualite.OHLCV_DAYS))
            if bad:
                self.logger.warning(f"[DATA] {s.symbol} : {bad} bougie(s) incohérente(s) sur "
                                    f"{qualite.OHLCV_DAYS} jours : écartée aujourd'hui")
                self._ohlcv_bad[base.lower()] = bad
                if base.upper() == "BTC":
                    raise DecisionDeferred("bougies BTC incohérentes")
                failed.append(base.lower())
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
        self._last_volume = volume                 # volumes en USD, pour le cœur financier
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
        compte. L'arrêt d'urgence porte alors sur ce capital, pas sur le compte."""
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

    # Arrêt d'urgence : une seule reprise automatique par an, puis risque
    # par achat divisé par deux pendant 90 jours (reprise en douceur).
    RESUME_ONCE_DAYS = 365
    GENTLE_DAYS = 90

    def _gentle(self, day: str) -> float:
        """0.5 pendant la reprise en douceur qui suit une reprise automatique
        après un arrêt d'urgence, 1.0 sinon."""
        last = self.state.get("auto_resumed_at")
        if last and (pd.Timestamp(day) - pd.Timestamp(last)).days < self.GENTLE_DAYS:
            return 0.5
        return 1.0

    def _auto_resume(self, day: str, equity: float, bull: bool) -> bool:
        """Arrêt d'urgence déclenché : reprise automatique et prudente après
        TG_KILL_RESUME_DAYS jours, si le marché est redevenu haussier, que
        le dernier auto-diagnostic n'a pas conclu à la perte de l'avantage
        de la stratégie et pas plus d'une fois par an. Le plus haut est remis
        au capital actuel (comme la commande resume) et le risque par achat
        reste divisé par deux pendant 90 jours. Sinon, la raison de
        l'attente est gardée pour le panneau et le rapport. True si levé."""
        if not self.state.get("halted"):
            return False
        since = self.state.get("halted_at") or day
        self.state["halted_at"] = since
        wait, d = self.g.kill_resume_days, pd.Timestamp(day)
        last = self.state.get("auto_resumed_at")
        waited = (d - pd.Timestamp(since)).days
        if wait <= 0:
            note = "levé seulement par la commande resume (TG_KILL_RESUME_DAYS=0)"
        elif last and (d - pd.Timestamp(last)).days < self.RESUME_ONCE_DAYS:
            note = (f"deuxième arrêt en moins d'un an (reprise automatique le {last}) : levé "
                    f"seulement par la commande resume")
        elif self.state.get("edge_alert"):
            note = ("le dernier auto-diagnostic conclut que la stratégie a perdu son avantage : "
                    "levé seulement par la commande resume")
        elif waited < wait:
            note = (f"reprise automatique possible à partir du "
                    f"{(pd.Timestamp(since) + pd.Timedelta(days=wait)).date()} si le marché est "
                    f"haussier")
        elif not bull:
            note = "délai écoulé : reprise automatique dès que le marché redevient haussier"
        else:
            until = (d + pd.Timedelta(days=self.GENTLE_DAYS)).date().isoformat()
            self.state.update(halted=False, halt_reason=None, halted_at=None, resume_note=None,
                              peak_equity=equity, auto_resumed_at=day)
            text = (f"reprise automatique après {waited} jours d'arrêt, marché redevenu haussier ; "
                    f"plus haut remis au capital actuel ({fr(equity, ',.0f')} {self.g.quote}), risque "
                    f"par achat divisé par deux jusqu'au {until}")
            self.logger.critical(f"[KILL] {text}")
            self.notifier(f"✅ TrendGuard, arrêt d'urgence levé : {text}.", critical=True)
            self._audit("arret_urgence.leve", "portefeuille", "achats repris", reason=text,
                        correlation_id=f"D-{day}", after={"equity": round(float(equity), 2)})
            return True
        self.state["resume_note"] = note
        return False

    def _risk_step(self, day: str, equity: float, peak: float, bull: bool) -> float:
        """Palier de risque du jour : celui choisi par l'évolution encadrée,
        ramené aussitôt au premier à la moindre alerte (arrêt d'urgence,
        baisse de 10 % ou plus, marché baissier, reprise en douceur), sans
        attendre la routine de la nuit."""
        step = float(self.risk_step or 1.0)
        dd = 1.0 - equity / peak if peak > 0 else 0.0
        if (step <= 1.0 or self.state.get("halted") or dd >= evolution.RISK_DOWN_DD or not bull
                or self._gentle(day) < 1.0):
            return 1.0
        return step

    def daily_decision(self, now: datetime, day: str) -> None:
        """Décision quotidienne sur la bougie close `day` : jours manqués
        rattrapés, ventes sur stop, stops remontés, reprise prudente ou
        déclenchement de l'arrêt d'urgence, puis achats du jour (cryptos
        sélectionnées, sans veto, au palier de risque du jour), expliqués et
        résumés."""
        p = self.p
        close, feats, regime = self._load_market(now)
        self._last_close = close
        snap, bull, prices = self._snapshot_at(close, feats, regime, day)
        # Cryptos que le bot a le droit d'ACHETER aujourd'hui (sélection du
        # panneau) ; les positions détenues restent toutes gérées.
        allowed = self._update_selection(close, feats, regime, day, snap, now)
        # Niveaux de la PROCHAINE décision (anticipation dans le panneau).
        self.state["anticipation"] = anticipation.basis_from_market(close, feats, day, self.p)
        # La décision du jour remplace les achats différés de la veille.
        stale = self.state.pop("pending_entries", None)
        if stale:
            self.logger.info("[RUSE] achats différés remplacés par la décision du jour : "
                             + ", ".join(a.upper() for a in sorted(stale)))
            for _a in stale:
                learning.note_deferral(self._learning(), "cancelled")
        self._entry_notes = {}
        missed = self._missed_days(close.index,
                                   self.state.get("last_decision_day"), day)
        late = (self._catch_up(close, feats, regime, missed, prices)
                if missed else [])
        holdings = self._holdings()
        exits = ts.update_positions(holdings, snap, bull, p)
        try:
            self._learn_close(day, snap, bull, exits)
        except Exception as e:           # apprendre ne bloque jamais la décision
            self.logger.warning(f"[APPRENTISSAGE] leçon du jour impossible : {e}")
        for a, reason in exits:
            holdings.pop(a, None)
            self._execute_exit(a, reason, prices.get(a))
        self._write_back(holdings)
        if self.live:
            self._raise_exchange_stops(holdings, snap, now)
        else:
            self._raise_paper_disaster(holdings, snap)
        equity, cash = self._equity_and_cash(prices)
        prev_equity = self.state.get("last_equity")
        self.state.setdefault("start_equity", equity)
        peak = max(float(self.state.get("peak_equity") or 0.0), equity)
        self.state["peak_equity"] = peak
        self.state["last_equity"] = equity
        # Régime de marché du jour (information, journal des trades).
        try:
            reg = regimes.at(regimes.regime_frame(close), day)
        except Exception as e:              # une lecture ratée ne bloque jamais la décision
            self.logger.warning(f"[RÉGIME] lecture impossible : {e}")
            reg = {}
        self.state["regime_detail"] = dict(reg, day=day)
        self._log_equity(equity, cash)
        self.state["last_regime_bull"] = bull
        if self._auto_resume(day, equity, bull):
            peak = equity
        if not self.state.get("halted") and equity < peak * (1 - self.g.kill_drawdown):
            self.state["halted"] = True
            self.state["halted_at"] = day
            self.state["halt_reason"] = (f"baisse de {fr((1 - equity / peak) * 100, '.1f')} % depuis "
                                         f"le plus haut (limite {fr(self.g.kill_drawdown * 100, '.0f')} %)")
            self.logger.critical(f"[KILL] arrêt d'urgence : {self.state['halt_reason']} → plus "
                                 "aucun achat")
            later = (f" Reprise automatique au plus tôt dans {self.g.kill_resume_days} jours, si le "
                     "marché est redevenu haussier ; sinon : commande resume."
                     if self.g.kill_resume_days > 0 else " Pour le lever : commande resume.")
            self.notifier(f"🛑 TrendGuard, arrêt d'urgence : {self.state['halt_reason']}. Plus aucun "
                          "achat ; les positions restent protégées par leurs stops." + later,
                          critical=True)
            self._audit("arret_urgence.declenche", "portefeuille", "achats arrêtés",
                        reason=self.state["halt_reason"], correlation_id=f"D-{day}",
                        before={"peak": round(float(peak), 2)}, after={"equity": round(float(equity), 2)})
            self._auto_resume(day, equity, bull)      # raison de l'attente, pour le panneau
        entries: List[Dict[str, Any]] = []
        mult = ts.risk_multiplier(equity, peak, p)
        step, gentle = self._risk_step(day, equity, peak, bull), self._gentle(day)
        self.state["risk_mult"] = mult * step * gentle
        self.state["risk_step"] = step
        if mult < 1.0:
            self.logger.warning(
                f"[PRUDENT] baisse de {fr((1 - equity / peak) * 100, '.1f')} % depuis le "
                f"pic → risque par trade × {fr(mult, 'g')}")
        if step > 1.0:
            self.logger.info(f"[PALIER] risque par achat {fr(p.risk_pct * step * 100, 'g')} % "
                             "(palier choisi par l'analyse du bot)")
        if gentle < 1.0:
            self.logger.info("[REPRISE] reprise en douceur après l'arrêt d'urgence : risque par "
                             "achat × 0,5")
        # Qualité des données du jour (information) ; la garde bloque déjà
        # les achats quand trop de clôtures manquent.
        try:
            self.state["qualite"] = qualite.quality(close, day, getattr(self, "_ohlcv_bad", None))
        except Exception as e:
            self.logger.warning(f"[DONNÉES] qualité illisible : {e}")
            self.state["qualite"] = {}
        # Garde « NO TRADE » : un seul « non » et aucun achat aujourd'hui.
        gate = garde.checks(day, close, equity, float(prev_equity) if prev_equity else None, self._disk_free_gb())
        blocked = garde.blocking(gate)
        self.state["garde"] = {"day": day, "checks": gate, "blocked": blocked}
        if blocked and not self.state.get("halted"):
            self.logger.warning("[GARDE] aucun achat aujourd'hui : " + " ; ".join(blocked))
        safe = self._watch_safe_mode()
        if safe.active and not self.state.get("halted"):
            self.logger.warning(f"[MODE SÛR] aucun achat aujourd'hui ({safe.reason})")
        self._journal("record_decision", day, self.g.run_mode, donnees.params_of(self.p), bull,
                      (self.state.get("regime_detail") or {}).get("texte"), equity,
                      bool(self.state.get("halted")), safe.active, blocked,
                      (self.state.get("qualite") or {}).get("score"),
                      (close.index[-1] + pd.Timedelta(days=1)).isoformat() if len(close.index) else None)
        held_before = {a: float(h.risk_quote) for a, h in holdings.items()}
        self._risk_engine("PRE", day, close, peak, now, prices, equity, cash)
        if not self.state.get("halted") and not blocked and not safe.active:
            eligible = {a: s for a, s in snap.items()
                        if self._can_enter(a) and a in allowed}
            # Savoir du bot : une crypto qu'il voit nettement en baisse, sur
            # l'avis de sources PROUVÉES, n'est pas achetée aujourd'hui.
            for a, h in self._savoir_holds(day, close).items():
                if a in eligible and a not in holdings:
                    eligible.pop(a)
                    self._entry_notes[a] = ("savoir", savoir.hold_text(a, h))
                    if ts.entry_signal(snap[a], p):
                        self.logger.info(f"[SAVOIR] achat de {a.upper()} reporté : avis du bot "
                                         f"{fr(h['value'], '+.2f')} ({', '.join(h['sources'])})")
            for a in sorted(snap):
                v = self._vetoed(a)
                if v and a not in holdings and ts.entry_signal(snap[a], p):
                    self.logger.warning(f"[VEILLE] achat de {a.upper()} bloqué : {v['reason']}")
            cash_left = cash
            for plan in ts.plan_entries(holdings, eligible, bull, equity, cash,
                                        p, mult * step * gentle):
                plan["day"] = day                     # décision d'origine (porte d'exécution)
                done = self._execute_entry(plan, equity, now, cash_left)
                if done is not None:
                    entries.append(done)
                    cash_left -= done["cost"]
        self._committee(day, close, snap, held_before, equity, blocked, safe.active, allowed)
        self.state["last_decision_day"] = day
        self._day_risk(day, close, equity)
        self._risk_engine("POST", day, close, peak, now, prices)
        self._portefeuille(day, close, prices)
        self._events_day(day, close, now)
        self._finance(day, close, feats)
        self._strategie(day, snap, bull, set(held_before), {a for a, _r in late + exits}, entries, equity)
        self._libre_step(day, close, snap)
        self._explain(day, bull, close, snap, late + exits, entries, mult, now, allowed)
        self._journal_signals(day, snap, exits)
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
        for a in snap:
            v = self._vetoed(a)
            if v and a not in holdings:
                notes[a] = ("veto", f"Achats bloqués par la veille : {v['reason']}")
            elif allowed is not None and a not in allowed and a not in holdings:
                notes[a] = ("unselected", "Non cochée dans la sélection manuelle : "
                                          "le bot ne l'achète pas")
        notes.update(self._entry_notes)
        step, gentle = float(self.state.get("risk_step") or 1.0), self._gentle(day)
        extra = []
        reg = self.state.get("regime_detail") or {}
        if reg.get("day") == day and reg.get("texte"):
            extra.append(f"Régime de marché : {reg['texte']} (information ; la règle reste BTC au-dessus "
                         f"de sa moyenne {self.p.regime_sma} jours).")
        ms = self.state.get("mode_sur") or {}
        if ms.get("active") and not self.state.get("halted"):
            extra.append(f"Mode sûr actif : aucun achat ({ms.get('reason') or 'demandé'}) ; les positions "
                         "restent protégées et peuvent être vendues. Pour le lever : commande mode-sur off.")
        pt = self.state.get("porte") or {}
        if pt.get("day") == day and pt.get("refused"):
            extra.append(f"Porte d'exécution : {pt['refused']} achat(s) refusé(s) ("
                         + " ; ".join(pt.get("reasons") or []) + ").")
        g = self.state.get("garde") or {}
        if g.get("day") == day and g.get("blocked") and not self.state.get("halted"):
            extra.append("Garde « NO TRADE » : aucun achat aujourd'hui (" + " ; ".join(g["blocked"]) + ").")
        rj = self.state.get("risque_jour") or {}
        if rj.get("day") == day:
            extra.append(risque.describe(rj))
        sr = self.state.get("stress") or {}
        if sr.get("day") == day and sr.get("text"):
            extra.append(sr["text"])
        mr = self.state.get("moteur_risque") or {}
        if mr.get("day") == day:
            extra.append(moteur_risque.describe(mr.get("post") or mr.get("pre")))
        pol = self.state.get("politique") or {}
        if pol.get("day") == day and pol.get("checked"):
            extra.append("Politiques : " + politique.describe(pol) + ".")
        pf = self.state.get("portefeuille") or {}
        if pf.get("day") == day:
            extra.append("Portefeuille : " + moteur_portefeuille.describe(pf) + ".")
        q = self.state.get("qualite") or {}
        if q.get("day") == day and q.get("score", 100) < 100:
            extra.append(f"Qualité des données : {q['text']}.")
        ev = self.state.get("evenements") or {}
        if ev.get("day") == day and ev.get("line"):
            extra.append(ev["line"])
        if step > 1.0:
            extra.append(f"Palier de risque : {fr(self.p.risk_pct * step * 100, 'g')} % par achat, "
                         f"choisi par l'analyse du bot ; retour immédiat à "
                         f"{fr(self.p.risk_pct * 100, 'g')} % à la première alerte.")
        if gentle < 1.0:
            until = (pd.Timestamp(self.state["auto_resumed_at"])
                     + pd.Timedelta(days=self.GENTLE_DAYS)).date().isoformat()
            extra.append(f"Reprise en douceur après l'arrêt d'urgence : risque par achat divisé "
                         f"par deux jusqu'au {until}.")
        if self.state.get("halted") and self.state.get("resume_note"):
            extra.append(f"Arrêt d'urgence : {self.state['resume_note']}.")
        cm = self.state.get("comite") or {}
        if cm.get("day") == day and cm.get("views"):
            extra.append("Comité d'agents (consultatif, la règle décide) : "
                         + " ; ".join(v["text"].split(" — ")[0] for v in cm["views"].values()) + ".")
        sv = self.state.get("savoir") or {}
        if sv.get("day") == day and sv.get("line"):
            extra.append(sv["line"])
        if sv.get("libre_day") == day and sv.get("libre_line"):
            extra.append(sv["libre_line"])
        r = explain_decision(day, bull, self._btc_gap(close, day), snap, holdings, exits,
                             [e["asset"] for e in entries], notes,
                             bool(self.state.get("halted")), mult, self.p,
                             float(self.state.get("last_equity") or 0.0) or None,
                             boost=step * gentle, extra=extra)
        r["at"] = now.isoformat()
        self.state["reasoning"] = r
        hist = self.state.get("reasoning_log") or []
        hist.append({"day": day, "text": " ".join(r["lines"][:2])})
        self.state["reasoning_log"] = hist[-30:]

    # ---------- Sélection des cryptos (panneau) ----------

    # Classement affiché dans la page Cryptos : bénéfice de la stratégie sur
    # chaque crypto (achats ET ventes) sur les 2 dernières années. Il informe,
    # il ne choisit pas : la sélection auto achète parmi les 21 cryptos.
    RANK_DAYS = 730

    def selection_request(self) -> Dict[str, Any]:
        return read_selection(self.g)

    def _update_selection(self, close: pd.DataFrame, feats: Dict[str, pd.DataFrame],
                          regime: pd.Series, day: str, snap: Dict[str, Dict[str, float]],
                          now: datetime) -> Set[str]:
        req = self.selection_request()
        prev = self.state.get("selection") or {}
        ranking = prev.get("ranking") or []
        if self.g.rank_cryptos:
            idx = close.index
            end_i = idx.get_loc(pd.Timestamp(day, tz="UTC"))
            lo = max(0, end_i - self.RANK_DAYS - 5)
            cols = {a: {k: f[k].values[lo:end_i + 1] for k in
                        ("close", "vol", "prior_high", "mom", "age", "vol30")}
                    for a, f in feats.items()}
            records = ts.asset_track_records(cols, regime.values[lo:end_i + 1], self.p)
            scores = ts.selection_scores(records, idx[lo:end_i + 1], end_i - lo,
                                         self.RANK_DAYS)
            eligible = [a for a, s in snap.items()
                        if ts._finite(s.get("vol30")) and s["vol30"] >= self.p.min_volume_usd
                        and s.get("age", 0) >= self.p.min_history and not self._vetoed(a)]
            order = sorted(scores, key=lambda a: (-scores[a]["total_r"], -scores[a]["trades"], a))
            ranking = [{"asset": a, "rank": k + 1, "total_r": round(scores[a]["total_r"], 2),
                        "trades": scores[a]["trades"], "win_rate": round(scores[a]["win_rate"], 3),
                        "eligible": a in eligible} for k, a in enumerate(order)]
        active = ([b.lower() for b in self.g.universe] if req["mode"] == "auto"
                  else req["manual"])
        if set(active) != set(prev.get("active") or []) and prev:
            self.logger.info(f"[SÉLECTION] {'auto' if req['mode'] == 'auto' else 'manuelle'} : "
                             f"{len(active)} crypto(s) achetable(s) : "
                             + (", ".join(a.upper() for a in active) or "aucune"))
            self._audit("selection.changee", "cryptos achetables", "appliqué", actor="vous (panneau)",
                        before=sorted(prev.get("active") or []), after=sorted(active),
                        correlation_id=f"D-{day}")
        self.state["selection"] = {"mode": req["mode"], "active": active,
                                   "ranking": ranking, "day": day, "at": now.isoformat()}
        return set(active)

    def active_now(self) -> Set[str]:
        """Cryptos achetables à cet instant : le choix du panneau s'applique
        aussi aux achats différés, sans attendre la décision suivante."""
        req = self.selection_request()
        if req["mode"] == "manual":
            return set(req["manual"])
        return {b.lower() for b in self.g.universe}

    def _refresh_selection(self, now: datetime) -> None:
        """Premier classement et premiers niveaux d'anticipation sans
        attendre la décision de 00:02 UTC (une fois, après une mise à jour du
        bot)."""
        day = self.state.get("last_decision_day")
        need_rank = self.g.rank_cryptos and not self.state.get("selection")
        need_basis = (self.state.get("anticipation") or {}).get("day") != day
        if (not day or not (need_rank or need_basis)
                or time.time() - self._last_selection_try < 1800):
            return
        self._last_selection_try = time.time()
        try:
            close, feats, regime = self._load_market(now)
            if need_basis:
                self.state["anticipation"] = anticipation.basis_from_market(
                    close, feats, day, self.p)
            if need_rank:
                snap, _bull, _prices = self._snapshot_at(close, feats, regime, day)
                self._update_selection(close, feats, regime, day, snap, now)
            self._save_state()
        except Exception as e:
            self.logger.warning(f"[SÉLECTION] classement ou anticipation reportés : {e}")

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

    def _summary(self, day: str, bull: bool, equity: float,
                 exits: List[Tuple[str, str]], entries: List[Dict[str, Any]],
                 prices: Dict[str, float]) -> None:
        holdings = self._holdings()
        lines = [f"TrendGuard, clôture du {day} — capital {fr(equity, ',.2f')} {self.g.quote} "
                 f"— marché {'haussier' if bull else 'baissier (aucun achat)'}"]
        for a, r in exits:
            lines.append(f"  ↘ vente {a.upper()} ({EXIT_SHORT.get(r, r.lower())})")
        for pl in entries:
            lines.append(f"  ↗ achat {pl['asset'].upper()}, risque "
                         f"{fr(pl['risk_quote'], '.2f')} {self.g.quote}")
        for a, e in sorted((self.state.get("pending_entries") or {}).items()):
            lines.append(f"  ⏳ achat différé {a.upper()} : {e['reason']}")
        for a, h in holdings.items():
            px = prices.get(a, h.entry)
            lines.append(f"  • {a.upper():<5} {fr(((px / h.entry) - 1) * 100, '+.1f')} %, "
                         f"stop {fr(h.stop, '.6g')}")
        if self.state.get("halted"):
            lines.append(f"  🛑 {self.state.get('halt_reason')}")
        text = "\n".join(lines)
        self.logger.info("[DAILY]\n" + text)
        self.notifier(text, dedup_key=f"tg-daily-{day}")

    # ---------- Boucle ----------

    STALL_DUMP_SEC = 20 * 60

    def run_forever(self) -> None:
        """Boucle principale : un cycle après l'autre jusqu'à l'arrêt demandé,
        avec une attente croissante après une erreur."""
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
        self.track_uptime = True
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
            self._note_stop()


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
