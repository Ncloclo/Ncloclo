"""Le bot TrendGuard : décision quotidienne, exécution, surveillance des stops.

Partie du bot TrendGuard (paquet trendguard, point d'entrée : trendguard_bot.py).
"""
from __future__ import annotations

import faulthandler
import logging
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import ccxt
import pandas as pd

import v29

from . import anticipation, autonomy, evolution, learning, uptime
from . import diagnostics as dg
from . import market_watch as mw
from . import trend_strategy as ts
from .config import DAY_MS, GuardConfig
from .explain import explain_decision
from .selection import read_selection


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
        self._last_anticipation = 0.0
        # Disponibilité (uptime.py) : relevée seulement en marche continue
        # (run_forever), pas pour un cycle isolé ni un rejeu.
        self.track_uptime = False
        self._tries_since: Optional[float] = None
        self._last_book_sample = 0.0       # apprentissage libre des carnets (learning.py)
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
        if self.state.get("pending_entries"):
            try:
                self._retry_pending(now)
            except Exception as e:
                self.logger.warning(f"[RUSE] nouvel essai d'achat impossible : {e}")
        try:
            if self.track_uptime:
                self._note_downtime()
            self._keep_alert_status()
        except Exception as e:           # un simple relevé : jamais bloquant
            self.logger.warning(f"[REPRISE] disponibilité non relevée : {e}")
        try:
            self._sample_books()
            self._learn_forecast(now)
        except Exception as e:           # apprendre ne bloque jamais le trading
            self.logger.warning(f"[APPRENTISSAGE] relevé impossible : {e}")
        # Horloge réelle (et non `now`, simulé en rejeu) : sert au contrôle
        # de santé du conteneur.
        self.state["last_cycle_ts"] = time.time()
        self._tries_since = None
        self._save_state()
        self._heartbeat(now)
        self._anticipate(now)
        self._auto_diagnose(now, day)
        self._daily_watch(now, day)

    # ---------- Disponibilité (uptime.py) et alertes ----------

    def _note_downtime(self) -> None:
        """Fin d'un cycle réussi : un trou de plus d'une heure depuis le
        cycle réussi précédent est un arrêt (PC éteint ou en veille, bot
        figé, Internet coupé, arrêt demandé). Il est gardé pour le panneau
        et signalé s'il n'a pas été demandé. Première fois : les arrêts
        passés sont reconstitués d'après le journal."""
        now = time.time()
        last = self.state.get("last_cycle_ts")
        up = self.state.get("uptime")
        if not isinstance(up, dict):
            up = uptime.from_log(self.g.log_file, last) if self.g.log_file else {}
            up["since"] = up.get("since") or last or now
            up.setdefault("events", [])
            self.state["uptime"] = up
        if not last or now - float(last) <= uptime.GAP_SEC:
            return
        ev = {"start": float(last), "end": now,
              "cause": uptime.cause_of_gap(float(last), now, self._started_at,
                                           self._tries_since, self.state.get("stopped_at"))}
        uptime.add(up, ev, now)
        text = uptime.describe(ev)
        if ev["cause"] == uptime.USER:
            self.logger.info(f"[REPRISE] le bot a été {text}")
            return
        late = uptime.crossed_close(ev, self.g.decision_delay_sec)
        self.logger.warning(f"[REPRISE] le bot a été {text}"
                            + (" ; décision de clôture prise en retard" if late else ""))
        self.notifier(
            f"⚠️ TrendGuard a été {text}. Il a repris et a rattrapé les contrôles manqués"
            + (" ; la décision de la clôture quotidienne a été prise en retard" if late else "")
            + f". {uptime.ADVICE.get(ev['cause'], '')}".rstrip(),
            dedup_key=f"tg-downtime-{int(ev['start'])}", critical=True)

    # ---------- Évolution encadrée (evolution.py) ----------

    def _apply_evolution(self) -> None:
        """Réglages choisis par l'évolution encadrée, pris en compte au
        démarrage et juste avant la décision quotidienne, jamais en cours de
        journée. Seuls cassure, stops et lecture du marché peuvent changer."""
        if not self.g.evolution:
            return
        p = evolution.params_for(self.g)
        if p == self.p:
            return
        changed = {k: getattr(p, k) for k in evolution.SPACE if getattr(p, k) != getattr(self.p, k)}
        self.logger.info(f"[ÉVOLUTION] réglages en vigueur : {evolution.describe(self.p, changed)}")
        self.p = p

    def _launch_evolution(self, day: str) -> None:
        """Routine quotidienne de l'évolution (épreuves), une fois par jour
        après la décision, dans un processus séparé : les stops restent
        surveillés pendant qu'elle calcule."""
        if not (self.g.evolution and self.track_uptime) or self.state.get("evolution_day") == day:
            return
        self.state["evolution_day"] = day
        peak, eq = self.state.get("peak_equity"), self.state.get("last_equity")
        storm = bool(self.state.get("halted")) or bool(
            peak and eq and float(eq) < float(peak) * (1 - evolution.STORM_DD))
        kw: Dict[str, Any] = {"cwd": autonomy.ROOT, "stdin": subprocess.DEVNULL,
                              "stderr": subprocess.STDOUT,
                              "env": dict(os.environ, RUN_MODE=self.g.run_mode,
                                          PYTHONIOENCODING="utf-8")}
        if os.name == "nt":
            kw["creationflags"] = autonomy.CREATE_NO_WINDOW
        log = autonomy.sidecar(self.g.lock_file, ".evolution.log") or os.devnull
        try:
            with open(log, "a", encoding="utf-8") as out:
                subprocess.Popen([sys.executable, autonomy.BOT_SCRIPT, "evolution", "quotidien"]
                                 + (["--tempete"] if storm else []), stdout=out, **kw)
            self.logger.info("[ÉVOLUTION] épreuves du jour lancées"
                             + (" (tempête : aucun changement permis)" if storm else ""))
        except OSError as e:
            self.logger.warning(f"[ÉVOLUTION] épreuves du jour impossibles : {e}")

    def _note_stop(self) -> None:
        """Arrêt propre (bouton ARRÊTER, Ctrl+C) : noté, pour que la reprise
        ne le compte pas comme une panne."""
        try:
            self.state["stopped_at"] = time.time()
            self._save_state()
        except Exception as e:
            self.logger.warning(f"[ARRÊT] état non enregistré : {e}")

    def _keep_alert_status(self) -> None:
        """Résultat du dernier envoi de chaque canal d'alerte (e-mail,
        WhatsApp), gardé pour le centre de sécurité du panneau."""
        last = getattr(self.notifier, "last", None)
        if isinstance(last, dict) and last:
            self.state["alerts_last"] = {k: dict(v) for k, v in list(last.items())}

    # ---------- Veille (market_watch.py) ----------

    def _vetoed(self, a: str) -> Optional[Dict[str, Any]]:
        """Veto officiel actif sur cette crypto (annonce de retrait Binance)."""
        v = (self.state.get("vetoes") or {}).get(a.lower())
        if v and v.get("until", "") >= self._now.date().isoformat():
            return v
        return None

    def _refresh_vetoes(self, now: datetime, max_age: float = 3600) -> None:
        """Annonces officielles de Binance, lues sans IA toutes les heures,
        et juste avant chaque décision si la dernière lecture a plus de
        10 minutes. Binance injoignable : les vetos précédents restent en
        place et la décision a lieu quand même."""
        if not self.g.watch or time.time() - self._last_veto_refresh < max_age:
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

    # ---------- Anticipation (vente ou achat probables ce soir) ----------

    ANTICIPATION_WINDOW_H = 3.0      # alerte dans les 3 h avant la clôture
    ANTICIPATION_EVERY_SEC = 900     # au plus un calcul toutes les 15 min
    ANTICIPATION_THRESHOLD = 0.6     # probabilité à partir de laquelle on prévient

    def holdings_view(self) -> List[Dict[str, Any]]:
        """Positions au format de l'anticipation (stop de clôture, stop
        catastrophe, risque initial, coût)."""
        out = []
        book = (self.state.get("paper") or {}).get("holdings") or {}
        for a, h in self._holdings().items():
            disaster = None
            if self.live:
                s = self.slots.get(a.upper())
                disaster = s.ctx.position.sl_price if s else None
            else:
                disaster = (book.get(a) or {}).get("disaster")
            out.append({"asset": a, "qty": h.qty, "entry": h.entry, "stop": h.stop,
                        "disaster": disaster, "risk": h.risk_quote, "cost": h.cost})
        return out

    def forecast(self, now: datetime, assets: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        basis = self.state.get("anticipation")
        if not basis:
            return None
        holdings = self.holdings_view()
        wanted = set(assets or []) | {h["asset"] for h in holdings} | {"btc"}
        if assets is None:
            # Candidats proches de leur niveau d'achat (moins de 10 %).
            wanted |= {a for a, b in (basis.get("assets") or {}).items()
                       if b.get("close") and b.get("buy_trigger")
                       and b["buy_trigger"] / b["close"] - 1 <= 0.10}
        prices: Dict[str, float] = {}
        for a in sorted(wanted):
            s = self.slots.get(a.upper())
            if s is None:
                continue
            try:
                prices[a] = float(s.ex.get_ticker()["last"])
            except Exception:
                continue
        return anticipation.forecast(
            basis, prices, holdings, now, self.p,
            float(self.state.get("last_equity") or self.g.paper_capital),
            float(self.state.get("risk_mult", 1.0) or 1.0), self.active_now(),
            (self.state.get("vetoes") or {}).keys(), bool(self.state.get("halted")),
            calibrate=learning.calibrator(self.state.get("learning")))

    # ---------- Apprentissage libre (learning.py) ----------

    BOOK_SAMPLE_EVERY_SEC = 3600
    BOOK_SAMPLE_MAX_SEC = 20

    def _learning(self) -> Dict[str, Any]:
        self.state["learning"] = learning.ensure(self.state.get("learning"))
        return self.state["learning"]

    def _sample_books(self) -> None:
        """Toutes les heures, en marche continue : écart achat/vente et
        profondeur de chaque carnet, pour apprendre la normale de chaque
        crypto. Au plus 20 s ; une erreur réseau arrête le relevé (nouvel
        essai dans une heure)."""
        if not self.track_uptime or time.time() - self._last_book_sample < self.BOOK_SAMPLE_EVERY_SEC:
            return
        self._last_book_sample = time.time()
        L, t0 = self._learning(), time.time()
        for s in list(self.slots.values()):
            fetch = getattr(s.ex.exchange, "fetch_order_book", None)
            if fetch is None or time.time() - t0 > self.BOOK_SAMPLE_MAX_SEC:
                return
            try:
                st = learning.book_stats(fetch(s.symbol, limit=100), self.BOOK_DEPTH_BAND)
            except Exception:
                return
            if st is not None:
                learning.observe_book(L, s.base.lower(), *st)

    def _learn_forecast(self, now: datetime) -> None:
        """Relève les probabilités du modèle 12, 6, 3 et 1 heure avant la
        clôture ; elles seront comparées à ce qui s'est passé."""
        basis = self.state.get("anticipation")
        if not (self.track_uptime and basis):
            return
        close_at = pd.Timestamp(basis["next_close"]).to_pydatetime()
        bucket = learning.forecast_bucket((close_at - now).total_seconds() / 3600)
        for_day = (pd.Timestamp(basis["next_close"]) - pd.Timedelta(days=1)).date().isoformat()
        L = self._learning()
        if bucket is None or learning.has_snapshot(L, for_day, bucket):
            return
        f = self.forecast(now)
        if f:
            learning.record_forecast(L, f, for_day, bucket)

    def _learn_close(self, day: str, snap: Dict[str, Dict[str, float]], bull: bool,
                     exits: List[Tuple[str, str]]) -> None:
        """À la décision : chaque prévision relevée pour cette clôture
        devient une leçon (vendu ? cassure ? marché baissier ?)."""
        sold = {a for a, reason in exits if reason == "STOP"}
        signals = {a for a, s in snap.items()
                   if ts._finite(s.get("close"), s.get("prior_high"), s.get("mom"))
                   and s["close"] > s["prior_high"] and s["mom"] > 0}
        n = learning.evaluate(self._learning(), day, sold, signals, not bull)
        if n:
            self.logger.info(f"[APPRENTISSAGE] {n} prévision(s) comparée(s) à la clôture du {day}")

    def _anticipate(self, now: datetime) -> None:
        """Dans les 3 h avant la clôture : prévient une fois par soir quand
        une vente ou un achat deviennent probables (les règles, elles, ne
        changent pas : c'est la clôture qui décide)."""
        basis = self.state.get("anticipation")
        if not self.g.anticipation_alerts or not basis:
            return
        close_at = pd.Timestamp(basis["next_close"]).to_pydatetime()
        hours = (close_at - now).total_seconds() / 3600
        if not 0 < hours <= self.ANTICIPATION_WINDOW_H:
            return
        if time.time() - self._last_anticipation < self.ANTICIPATION_EVERY_SEC:
            return
        self._last_anticipation = time.time()
        f = self.forecast(now)
        if not f:
            return
        sent = self.state.get("anticipation_sent") or {}
        keys = sent.get("keys", []) if sent.get("close") == basis["next_close"] else []
        for al in anticipation.alerts_to_send(f, keys, self.ANTICIPATION_THRESHOLD):
            self.logger.info(f"[ANTICIPATION] {al['text']}")
            self.notifier(al["text"], dedup_key=f"anticipation-{basis['next_close']}-{al['key']}")
            keys.append(al["key"])
        self.state["anticipation_sent"] = {"close": basis["next_close"], "keys": keys}

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
        for a in snap:
            v = self._vetoed(a)
            if v and a not in holdings:
                notes[a] = ("veto", f"Achats bloqués par la veille : {v['reason']}")
            elif allowed is not None and a not in allowed and a not in holdings:
                notes[a] = ("unselected", "Non cochée dans la sélection manuelle : "
                                          "le bot ne l'achète pas")
        notes.update(self._entry_notes)
        r = explain_decision(day, bull, self._btc_gap(close, day), snap, holdings, exits,
                             [e["asset"] for e in entries], notes,
                             bool(self.state.get("halted")), mult, self.p,
                             float(self.state.get("last_equity") or 0.0) or None)
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
            if not (ob.get("bids") or []) or not (ob.get("asks") or []):
                return "carnet d'ordres vide"
            stats = learning.book_stats(ob, self.BOOK_DEPTH_BAND)
            if stats is None:
                return None
            spread, depth = stats
            # Ruse apprise (learning.py) : limite réglée sur la normale de
            # CETTE crypto, jamais plus large que le seuil fixe.
            a, L = s.base.lower(), self._learning()
            limit = learning.spread_limit(L, a, self.g.max_spread)
            drained = learning.depth_drained(L, a, depth, self.g.quote)
            learning.observe_book(L, a, spread, depth)
            if spread > limit:
                return (f"écart achat/vente anormal ({spread * 100:.2f} %, limite "
                        f"{limit * 100:.2f} %"
                        + (", réglée sur la normale de cette crypto" if limit < self.g.max_spread else "")
                        + ")")
            if depth < self.BOOK_DEPTH_MULT * notional:
                return (f"carnet d'ordres trop mince ({depth:,.0f} {self.g.quote} à moins "
                        f"de 1 % du prix pour un achat de {notional:,.0f})")
            if drained:
                return drained
        except Exception:
            return None
        return None

    def _defer_entry(self, plan: Dict[str, Any], equity: float, now: datetime,
                     reason: str, price: Optional[float] = None) -> None:
        a = plan["asset"]
        book = self._pending()
        t = now.timestamp()
        e = book.get(a)
        if e is not None:
            e.update(reason=reason, tries=int(e.get("tries", 1)) + 1,
                     next=t + self.RETRY_EVERY_SEC)
            # Le tableau de bord affiche la raison du DERNIER essai.
            self._note_asset(a, "deferred", f"Achat différé : {reason}. Nouvel essai "
                                            f"toutes les 5 min")
            return
        if self.g.entry_retry_hours <= 0:
            self.logger.warning(f"[RUSE] achat de {a.upper()} annulé : {reason}")
            self._entry_notes[a] = ("cancelled", f"Achat annulé : {reason}")
            return
        book[a] = {"plan": plan, "equity": float(equity), "reason": reason, "tries": 1,
                   "since": t, "until": t + self.g.entry_retry_hours * 3600,
                   "next": t + self.RETRY_EVERY_SEC, "price": price}
        learning.note_deferral(self._learning(), "deferred")
        self.logger.warning(
            f"[RUSE] achat de {a.upper()} différé : {reason} → nouvel essai toutes "
            f"les 5 min pendant {self.g.entry_retry_hours:g} h")
        self._entry_notes[a] = ("deferred", f"Achat différé : {reason}. Nouvel essai "
                                            f"toutes les 5 min")
        self._note_asset(a, *self._entry_notes[a])

    def _mark_prices(self, holdings: Dict[str, ts.Holding]) -> Dict[str, float]:
        """Cours actuels des positions détenues (capital réel du moment) ; un
        cours illisible est remplacé par le prix d'achat."""
        prices: Dict[str, float] = {}
        for a, h in holdings.items():
            s = self.slots.get(a.upper())
            try:
                prices[a] = float(s.ex.get_ticker()["last"]) if s else h.entry
            except Exception:
                prices[a] = h.entry
        return prices

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
                learning.note_deferral(self._learning(), "abandoned")
                self.logger.warning(f"[RUSE] achat de {a.upper()} abandonné : {e['reason']} "
                                    f"pendant {self.g.entry_retry_hours:g} h")
                self._note_asset(a, "cancelled", f"Achat abandonné : {e['reason']} pendant "
                                                 f"{self.g.entry_retry_hours:g} h")
                continue
            holdings = self._holdings()
            eq, cash = self._equity_and_cash(self._mark_prices(holdings))
            mult = float(self.state.get("risk_mult", 1.0) or 1.0)
            open_risk = sum(h.risk_quote for h in holdings.values())
            if (self.state.get("halted") or not self.state.get("last_regime_bull")
                    or a in holdings or not self._can_enter(a)
                    or a not in self.active_now()
                    or len(holdings) >= p.max_positions
                    or open_risk + e["plan"]["risk_quote"] > p.max_total_risk * eq * mult + 1e-9):
                book.pop(a, None)
                learning.note_deferral(self._learning(), "cancelled")
                self.logger.info(f"[RUSE] achat différé de {a.upper()} abandonné : la situation "
                                 f"a changé depuis la décision")
                self._note_asset(a, "cancelled", "Achat abandonné : la situation a changé "
                                                 "depuis la décision")
                continue
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
            self._defer_entry(plan, equity, now, anomaly, px_now)
            return None
        waited = self._pending().pop(a, None)
        deferred = waited is not None
        # Bilan de la ruse : prix obtenu par rapport au premier essai (+ = moins cher).
        first = (waited or {}).get("price")
        ruse_gain = (first - px_now) / first if first else None
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
            if deferred:
                learning.note_deferral(self._learning(), "bought", ruse_gain)
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
            if deferred:
                learning.note_deferral(self._learning(), "bought", ruse_gain)
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
