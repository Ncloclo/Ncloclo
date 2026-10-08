"""L'exécution du bot TrendGuard : protection des positions entre deux
clôtures (réel et paper), ventes, stops posés chez Binance, et ruse à
l'achat (carnet d'ordres, achats différés, taille recalculée au prix réel).

Partie de la classe TrendGuardBot (bot.py), qui en hérite.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import ccxt

import v29

from . import chantiers, learning, moteur_risque, porte, postmortem
from . import trend_strategy as ts
from .bot_types import Slot, last_closed_day
from .contrats import ContractError, Money, OrderIntent
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
            info = self._live_info(s)
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
                self._harvest_live_trade(s, before, "EXCHANGE_STOP", opened, bought, info)
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
        trade = {"asset": a, "date": self._now.isoformat(), "days": (self._now - opened).days,
                 "entry_date": h["entry_date"], "entry": h["entry"], "exit": px, "pnl": pnl,
                 "r": pnl / h["risk_quote"], "reason": reason}
        if h.get("trace"):
            trade["trace"] = h["trace"]
        trade["qty"] = h["qty"]
        close = getattr(self, "_last_close", None)
        series = close[a] if close is not None and a in close else None
        self._record_trade(postmortem.enrich(trade, series, h["qty"], h["risk_quote"], h.get("stop"),
                                             h.get("regime")))

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
        info = self._live_info(s)
        ref = s.ex.get_ticker()["bid"]
        s.eng.close_position(s.ctx, f"TREND_{reason}", ref)
        self._harvest_live_trade(s, before, reason, opened, bought, info)
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
        # Porte d'exécution : aucun achat sans contrôle du risque ni autorisation.
        trace = self._gate(plan, equity, cash, now)
        if trace is None:
            return None
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
                "disaster": disaster, "regime": (self.state.get("regime_detail") or {}).get("texte"),
                "trace": trace}
            self.logger.info(
                f"[ENTRY] {a.upper()} qty={plan['qty']:.6f} @ "
                f"{plan['entry']:.6f} stop={plan['stop']:.6f} "
                f"risque={plan['risk_quote']:.2f} {self.g.quote}")
            self._record_buy(a, now, plan["entry"], plan["qty"], plan["cost"],
                             plan["risk_quote"], "différé" if deferred else "")
            self._bought(a, plan, trace, now)
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
            self.state.setdefault("entry_regimes", {})[a] = (self.state.get("regime_detail") or {}).get("texte")
            self.state.setdefault("entry_traces", {})[a] = trace
            s.ctx.position.highest_close = plan["ref_price"]
            s.ctx.position.soft_stop = plan["stop"]
            p = s.ctx.position
            self._bought(a, plan, trace, now, qty=p.amount_held or plan["qty"],
                         price=p.buy_price or plan["exec_price"],
                         fees=max(0.0, ((p.cost_basis or p.buy_price or 0.0) - (p.buy_price or 0.0))
                                  * (p.amount_held or 0.0)))
            self._record_buy(a, now, p.buy_price or plan["exec_price"], p.amount_held or plan["qty"],
                             (p.cost_basis or p.buy_price) * (p.amount_held or plan["qty"]),
                             plan["risk_quote"], "différé" if deferred else "")
            if deferred:
                learning.note_deferral(self._learning(), "bought", ruse_gain)
        else:
            self._audit("ordre.achat", a, f"non exécuté ({getattr(res, 'name', res)})", now=now,
                        authorization=trace["authorization_id"], correlation_id=trace["decision_id"],
                        causation_id=trace["risk_check_id"])
            self._journal("record_order", trace["key"], a, "BUY", plan["qty"], plan["exec_price"],
                          self.g.run_mode, "SENT" if res == v29.EntryResult.ORDER_SENT else "REJECTED",
                          decision_id=trace["decision_id"], risk_check_id=trace["risk_check_id"],
                          authorization_id=trace["authorization_id"], stop=plan["stop"],
                          reason=str(getattr(res, "name", res)), at=now.isoformat())
        self._save_slot(s)
        return plan if res == v29.EntryResult.OPENED else None

    # ---------- Porte d'exécution (porte.py) ----------

    def _production(self, day: str) -> Tuple[bool, str]:
        """Porte du réel (chantiers.py, porte 8), mesurée une fois par jour :
        fermée, aucun achat réel (les ventes restent permises). Une mesure
        impossible la ferme."""
        cached = self.state.get("mise_en_production") or {}
        if cached.get("day") != day:
            try:
                g8 = chantiers.live_gate(self.g, self.state)
                cached = {"day": day, "open": bool(g8["open"]), "missing": g8["missing"][:6]}
            except Exception as e:
                cached = {"day": day, "open": False, "missing": [f"évaluation impossible ({type(e).__name__})"]}
            self.state["mise_en_production"] = cached
            if not cached["open"]:
                self.logger.warning("[PORTE DU RÉEL] fermée : aucun achat réel ; " + " ; ".join(cached["missing"]))
        return bool(cached["open"]), ("ouverte" if cached["open"] else
                                      "fermée : " + " ; ".join(cached["missing"][:3]))

    def _gate(self, plan: Dict[str, Any], equity: float, cash: float,
              now: datetime) -> Optional[Dict[str, str]]:
        """Contrôle du risque et autorisation d'un achat (porte.py), tracés
        dans le journal d'audit : identifiants de la décision, du contrôle et
        de l'autorisation, ou None si l'achat est refusé (raison dans le
        journal du bot et le raisonnement). Un achat qu'on ne peut pas
        tracer est refusé."""
        a = plan["asset"]
        day = last_closed_day(now, self.g.decision_delay_sec)
        decision_id = f"D-{plan.get('day') or day}"
        holdings = self._holdings()
        g = self.g
        garde = self.state.get("garde") or {}
        pf = porte.Portfolio(
            equity=float(equity), cash=float(cash), invested=sum(h.cost for h in holdings.values()),
            held_risk=tuple((x, float(h.risk_quote)) for x, h in holdings.items()),
            risk_mult=float(self.state.get("risk_mult", 1.0) or 1.0), expected_day=day,
            universe=frozenset(b.lower() for b in g.universe), allowed=frozenset(self.active_now()),
            vetoed=frozenset(x for x in (self.state.get("vetoes") or {}) if self._vetoed(x)),
            bought_today=frozenset((self.state.get("porte") or {}).get("keys") or ())
            if (self.state.get("porte") or {}).get("day") == day else frozenset(),
            halted=bool(self.state.get("halted")),
            garde_blocked=tuple(garde.get("blocked") or ()) if garde.get("day") == day else (),
            safe_mode=porte.safe_mode(g), live=self.live,
            data_quality=(self.state.get("qualite") or {}).get("score")
            if (self.state.get("qualite") or {}).get("day") == day else None,
            live_armed=bool(g.enable_live_trading and g.live_confirmation == "I_UNDERSTAND_RISK"),
            production=self._production(day) if self.live and g.release_gate else None,
            risk_engine=moteur_risque.gate((self.state.get("moteur_risque") or {}).get("pre"), day, now)
            if g.risk_engine else None)
        try:
            intent: Optional[OrderIntent] = OrderIntent.from_plan(plan, day)
            decision = porte.check(intent, pf, self.p, now)
        except ContractError as e:
            intent, decision = None, porte.refusal(e, a, now)
        auth = porte.authorize(decision, pf, now)
        self._journal("record_risk_check", decision_id, a, decision, auth)
        log = self.state.setdefault("porte", {})
        if log.get("day") != day:
            log.clear()
            log.update(day=day, approved=0, refused=0, keys=[], reasons=[])
        text = porte.describe(decision, auth)
        ok = self._audit("porte.controle", a, decision.status, reason=text, now=now,
                         after={"risk_check_id": decision.risk_check_id, "qty": decision.approved_size,
                                "expected_loss": decision.expected_loss, "open_risk_pct": decision.open_risk_pct,
                                "warnings": list(decision.warnings)},
                         authorization=auth.authorization_id, correlation_id=decision_id,
                         causation_id=decision_id)
        if auth.valid_at(now) and ok and intent is not None:
            log["approved"] += 1
            return {"decision_id": decision_id, "risk_check_id": decision.risk_check_id,
                    "authorization_id": auth.authorization_id, "key": intent.idempotency_key}
        if auth.authorized and not ok:
            text = "achat refusé : journal d'audit impossible à écrire (un achat sans trace n'est pas permis)"
        log["refused"] += 1
        log["reasons"] = (log["reasons"] + [f"{a.upper()} : {text}"])[-10:]
        self.logger.warning(f"[PORTE] {a.upper()} : {text}")
        self._pending().pop(a, None)
        self._entry_notes[a] = ("cancelled", f"Achat refusé par la porte d'exécution : {text}")
        self._note_asset(a, *self._entry_notes[a])
        return None

    def _bought(self, a: str, plan: Dict[str, Any], trace: Dict[str, str], now: datetime,
                qty: Optional[float] = None, price: Optional[float] = None,
                fees: Optional[float] = None) -> None:
        """Achat exécuté : clé d'unicité gardée pour la journée, ordre et
        exécution dans le journal financier, trace dans le journal d'audit."""
        self.state.setdefault("porte", {}).setdefault("keys", []).append(trace["key"])
        qty = float(qty if qty is not None else plan["qty"])
        price = float(price if price is not None else plan["entry"])
        self._journal("record_order", trace["key"], a, "BUY", qty, price, self.g.run_mode, "FILLED",
                      decision_id=trace["decision_id"], risk_check_id=trace["risk_check_id"],
                      authorization_id=trace["authorization_id"], stop=plan["stop"],
                      fees=fees if fees is not None else qty * price * self.p.fee, at=now.isoformat())
        q = self.g.quote
        self._audit("ordre.achat", a, "exécuté", now=now, authorization=trace["authorization_id"],
                    correlation_id=trace["decision_id"], causation_id=trace["risk_check_id"],
                    after={"qty": plan["qty"], "entry": Money.of(plan["entry"], q).as_dict(),
                           "stop": Money.of(plan["stop"], q).as_dict(), "cost": Money.of(plan["cost"], q).as_dict(),
                           "risk": Money.of(plan["risk_quote"], q).as_dict(), "mode": self.g.run_mode})
