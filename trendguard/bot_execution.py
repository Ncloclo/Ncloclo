"""L'exécution du bot TrendGuard : protection des positions entre deux
clôtures (réel et paper), ventes, stops posés chez Binance, et ruse à
l'achat (carnet d'ordres, achats différés, taille recalculée au prix réel).

Partie de la classe TrendGuardBot (bot.py), qui en hérite.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import ccxt

import v29

from . import learning
from . import trend_strategy as ts
from .bot_types import Slot
from .texte import fr


class ExecutionMixin:
    """Ordres et protections du bot ; état et réglages : ceux de TrendGuardBot."""

    # ---------- Protection des positions entre deux clôtures ----------

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
                return (f"écart achat/vente anormal ({fr(spread * 100, '.2f')} %, limite "
                        f"{fr(limit * 100, '.2f')} %"
                        + (", réglée sur la normale de cette crypto" if limit < self.g.max_spread else "")
                        + ")")
            if depth < self.BOOK_DEPTH_MULT * notional:
                return (f"carnet d'ordres trop mince ({fr(depth, ',.0f')} {self.g.quote} à moins "
                        f"de 1 % du prix pour un achat de {fr(notional, ',.0f')})")
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
            f"les 5 min pendant {fr(self.g.entry_retry_hours, 'g')} h")
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
                                    f"pendant {fr(self.g.entry_retry_hours, 'g')} h")
                self._note_asset(a, "cancelled", f"Achat abandonné : {e['reason']} pendant "
                                                 f"{fr(self.g.entry_retry_hours, 'g')} h")
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
                f"[ENTRY] {a.upper()} annulée : prix {fr(px_now, '.6g')} "
                f"({fr(drift * 100, '+.1f')} % vs clôture) trop proche du stop "
                f"{fr(plan['stop'], '.6g')} ou taille sous le minimum")
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
                f"[ENTRY] {a.upper()} : prix {fr(px_now, '.6g')} ({fr(drift * 100, '+.1f')} % "
                f"vs clôture) → quantité {fr(plan['qty'], '.6g')} → {fr(adj['qty'], '.6g')} "
                f"(risque {fr(adj['risk_quote'], '.2f')} {self.g.quote})")
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
