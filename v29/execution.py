"""Moteur d'exécution : entrées, protections, sorties (commun à TrendGuard).

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import ccxt
import pandas as pd

from .config import Config
from .constants import (
    CID_ENTRY_LIMIT,
    CID_ENTRY_MARKET,
    CID_EXIT_MARKET,
    CID_OCO_SL,
    CID_STOP,
    PROTECTION_CID_PREFIXES,
)
from .exchange import AmbiguousOrder, ExchangeAdapter, OrderResult, _fnum
from .infra import Notifier
from .models import (
    AUTO_CLEARABLE_HALTS,
    BotContext,
    BotState,
    CancelResult,
    EntryResult,
    Fill,
    HaltKind,
    Position,
    ProtectionMode,
    ProtectionSnapshot,
    ProtectionState,
    Signal,
    clear_halt,
    halt_ctx,
    is_frozen,
)
from .risk import RR_BY_MODULE, RiskEngine, break_even_stop, record_closed_trade, trailing_stop
from .store import Store
from .utils import _isnan, _parse_iso, _row_get, _utcnow, _utcnow_iso

#
# Invariants :
#   I1. En live, une position ouverte est protégée par un ordre exchange
#       (OCO natif ou stop) ; sinon elle est liquidée (panic) — jamais nue.
#   I2. Aucun ordre de vente « marché » n'est envoyé tant que la protection
#       n'est pas confirmée terminale (le solde serait bloqué).
#   I3. Chaque fill est comptabilisé une seule fois (recorded_fills).
#   I4. Tout ordre d'entrée/sortie marché est précédé d'une intention
#       persistée (pending_order) résolue par client-id.
#   I5. Les quantités vendues/protégées sont plafonnées au solde libre du
#       bot (hors EXTERNAL_BASE_RESERVE).

def update_extremes(p: Position, df: pd.DataFrame, live_price: float) -> None:
    if p.high_since_entry is None:
        p.high_since_entry = p.buy_price
    if p.low_since_sl_update is None:
        p.low_since_sl_update = p.buy_price
    if len(df) >= 2:
        closed = df.iloc[-2]
        closed_ts = int(closed["ts"])
        entry_ts = p.entry_candle_ts
        sl_ts = p.sl_update_candle_ts or entry_ts
        if entry_ts is not None and closed_ts > entry_ts:
            p.high_since_entry = max(float(p.high_since_entry),
                                     float(closed["high"]))
        if sl_ts is not None and closed_ts > sl_ts:
            p.low_since_sl_update = min(float(p.low_since_sl_update),
                                        float(closed["low"]))
    p.high_since_entry = max(float(p.high_since_entry), live_price)
    p.low_since_sl_update = min(float(p.low_since_sl_update), live_price)


_update_extremes = update_extremes

_LEG_REASON = {"TP": "BARRIER_TP", "SL": "BARRIER_SL", "STOP": "BARRIER_SL"}


class ExecutionEngine:
    def __init__(self, cfg: Config, logger: logging.Logger,
                 exchange: ExchangeAdapter, store: Optional[Store],
                 notifier: Notifier, risk: RiskEngine):
        self.cfg = cfg
        self.logger = logger
        self.ex = exchange
        self.store = store
        self.notifier = notifier
        self.risk = risk
        self.context_key = "context"

    # ---------- Utilitaires ----------

    @property
    def live(self) -> bool:
        return self.cfg.run_mode == "live"

    def _persist(self, ctx: BotContext) -> None:
        if self.store is not None:
            self.store.save_context(ctx, self.cfg.run_mode, force=True,
                                    key=self.context_key)

    def _event(self, name: str, severity: str,
               payload: Dict[str, Any]) -> None:
        if self.store is not None:
            self.store.log_event(name, severity, payload, self.cfg.run_mode,
                                 durable=(severity == "CRITICAL"))

    def _halt(self, ctx: BotContext, reason: str,
              kind: str = HaltKind.INTEGRITY, orphan: bool = False,
              payload: Optional[Dict[str, Any]] = None) -> None:
        halt_ctx(ctx, reason, kind, orphan=orphan)
        self.logger.critical(f"[HALT] {reason} ({kind})")
        self._event("halt", "CRITICAL",
                    {"reason": reason, "kind": kind, **(payload or {})})
        self.notifier(f"🛑 HALT {reason}", critical=True)
        self._persist(ctx)

    # ---------- Paper ----------

    def _paper_buy(self, ctx: BotContext, amount: float,
                   ref_price: float) -> OrderResult:
        px = ref_price * (1 + self.cfg.paper_slippage_pct)
        fee_rate = self.cfg.paper_fee_rate
        cash = float(ctx.portfolio.paper_cash)
        if amount * px * (1 + fee_rate) > cash:
            amount = self.ex.round_amount(cash / (px * (1 + fee_rate)))
        cost = amount * px
        fee = cost * fee_rate
        ctx.portfolio.paper_cash -= cost + fee
        ctx.portfolio.paper_base += amount
        self.ex.invalidate_balances()
        return OrderResult("PAPER", "", "closed", amount, px, cost,
                           0.0, fee, 0.0, True, "MARKET", "buy")

    def _paper_sell(self, ctx: BotContext, amount: float, price: float,
                    maker: bool = False) -> OrderResult:
        amount = min(float(amount), float(ctx.portfolio.paper_base))
        px = price if maker else price * (1 - self.cfg.paper_slippage_pct)
        proceeds = amount * px
        fee = proceeds * self.cfg.paper_fee_rate
        ctx.portfolio.paper_cash += proceeds - fee
        ctx.portfolio.paper_base = max(0.0, ctx.portfolio.paper_base - amount)
        self.ex.invalidate_balances()
        return OrderResult("PAPER", "", "closed", amount, px, proceeds,
                           0.0, fee, 0.0, True, "MARKET", "sell")

    # ---------- Entrée ----------

    def enter(self, ctx: BotContext, closed: Any, sig: Signal,
              live_price: float, current_candle_ts: int,
              equity: Optional[float], btc_vol_mult: float = 1.0,
              freshness_max: Optional[int] = None) -> EntryResult:
        cfg = self.cfg
        if ctx.position.in_position or ctx.pending_order:
            return EntryResult.SKIPPED
        fresh_mult, stale = self.risk.signal_freshness_mult(
            int(closed["ts"]), freshness_max)
        if stale:
            self.logger.info(f"[ENTRY] rejet: {stale}")
            return EntryResult.SKIPPED
        if equity is None or equity <= 0:
            return EntryResult.SKIPPED
        quote_free = (self.ex.get_free_balance(cfg.quote, ctx, force=True)
                      if self.live else float(ctx.portfolio.paper_cash))
        max_notional = quote_free * (1 - cfg.min_cash_reserve_pct)
        if max_notional <= self.ex.min_notional() * 1.05:
            self.logger.info("[ENTRY] cash disponible insuffisant")
            return EntryResult.SKIPPED
        expected_slip = cfg.entry_slippage_buffer_pct
        if cfg.use_smart_buy:
            expected_slip = max(expected_slip, cfg.chaser_max_slippage_pct)
        sizing_price = live_price * (1 + expected_slip)
        sl_dist = self.risk.sl_distance(sig.module, float(closed["atr"]),
                                        sizing_price, closed, cfg)
        if sl_dist is None:
            return EntryResult.SKIPPED
        risk_pct = self.risk.base_risk(ctx, sig.module,
                                       _row_get(closed, "realized_vol"))
        tier_mult = cfg.tier_mult_map.get(sig.tier, 1.0)
        regime_mult = self.risk.regime_multiplier(sig.regime)
        dd_mult = self.risk.dd_derisk_multiplier(ctx, equity)
        effective_risk = min(
            risk_pct * tier_mult * regime_mult * dd_mult * fresh_mult
            * btc_vol_mult, cfg.risk_absolute_max_pct)
        if effective_risk <= 0:
            return EntryResult.SKIPPED
        amount = self.ex.round_amount(self.risk.position_size(
            sizing_price, sl_dist, equity, effective_risk, max_notional))
        if amount <= 0 or amount * sizing_price < self.ex.min_notional() * 1.05:
            self.logger.info("[ENTRY] taille sous le minimum négociable")
            return EntryResult.SKIPPED
        obi_used: Optional[float] = None
        if cfg.use_l2_filter:
            micro, obi, spread = self.ex.get_l2_state(force=True)
            obi_used = obi
            if (micro is None or spread > cfg.max_spread_tolerance
                    or obi < cfg.obi_toxic_threshold):
                self.logger.info(
                    f"[ENTRY] L2 block micro={micro} spread={spread:.5f} "
                    f"obi={obi:.3f}")
                return EntryResult.SKIPPED
        entry_adx = _row_get(closed, "adx")
        intent = {
            "kind": "entry", "cids": [], "ts": _utcnow_iso(),
            "ts_epoch": time.time(), "amount": amount, "sl_dist": sl_dist,
            "rr": RR_BY_MODULE.get(sig.module, 2.0), "module": sig.module,
            "regime": sig.regime, "tier": sig.tier, "score": int(sig.score),
            "entry_adx": None if _isnan(entry_adx) else float(entry_adx),
            "entry_obi": obi_used, "candle_ts": int(current_candle_ts),
            "eff_risk_pct": effective_risk * 100, "equity": equity,
            "mults": {"tier": tier_mult, "regime": regime_mult,
                      "dd": dd_mult, "fresh": fresh_mult,
                      "btc_vol": btc_vol_mult}}
        if not self.live:
            res = self._paper_buy(ctx, amount, live_price)
            return (EntryResult.OPENED if self._open_from_fill(ctx, intent, [res])
                    else EntryResult.ORDER_SENT)
        ctx.pending_order = intent
        ctx.state = BotState.OPENING.value
        self._persist(ctx)
        try:
            if cfg.use_smart_buy:
                results = self._smart_buy(ctx, intent, amount)
            else:
                cid = self.ex.new_client_id(CID_ENTRY_MARKET)
                intent["cids"].append(cid)
                self._persist(ctx)
                results = [self.ex.market_buy(amount, cid)]
        except AmbiguousOrder as e:
            self._halt(ctx, "AMBIGUOUS_BUY",
                       payload={"error": str(e), "cid": e.client_id})
            return EntryResult.ORDER_SENT
        except ValueError as e:
            self.logger.info(f"[ENTRY] ordre non envoyé: {e}")
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return EntryResult.SKIPPED
        except (ccxt.InsufficientFunds, ccxt.InvalidOrder,
                ccxt.BadRequest) as e:
            self.logger.warning(f"[ENTRY] ordre rejeté: {e}")
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return EntryResult.ORDER_SENT
        opened = self._open_from_fill(ctx, intent, results)
        return EntryResult.OPENED if opened else EntryResult.ORDER_SENT

    def enter_planned(self, ctx: BotContext, amount: float, ref_price: float,
                      sl_abs: float, tp_abs: float, meta: Dict[str, Any],
                      candle_ts: int) -> EntryResult:
        """Entrée dont la taille et les stops sont décidés par une stratégie
        externe (TrendGuard), exécutée avec les mêmes garanties que enter() :
        intention persistée, frais en base, protection immédiate."""
        if ctx.position.in_position or ctx.pending_order:
            return EntryResult.SKIPPED
        amount = self.ex.round_amount(amount)
        if amount <= 0 or amount * ref_price < self.ex.min_notional() * 1.05:
            self.logger.info(f"[ENTRY] {self.cfg.symbol}: taille sous le minimum")
            return EntryResult.SKIPPED
        intent = {"kind": "entry", "cids": [], "ts": _utcnow_iso(),
                  "ts_epoch": time.time(), "amount": amount,
                  "sl_abs": sl_abs, "tp_abs": tp_abs,
                  "candle_ts": int(candle_ts), "ref_price": ref_price,
                  "module": meta.get("module", "planned"),
                  "regime": meta.get("regime", ""), "tier": meta.get("tier", ""),
                  "score": int(meta.get("score", 0)),
                  "soft_stop": meta.get("soft_stop"),
                  "risk_per_unit": meta.get("risk_per_unit"),
                  "equity": meta.get("equity"),
                  "eff_risk_pct": meta.get("eff_risk_pct")}
        if not self.live:
            res = self._paper_buy(ctx, amount, ref_price)
            return (EntryResult.OPENED if self._open_from_fill(ctx, intent, [res])
                    else EntryResult.ORDER_SENT)
        ctx.pending_order = intent
        ctx.state = BotState.OPENING.value
        cid = self.ex.new_client_id(CID_ENTRY_MARKET)
        intent["cids"].append(cid)
        self._persist(ctx)
        try:
            results = [self.ex.market_buy(amount, cid)]
        except AmbiguousOrder as e:
            self._halt(ctx, "AMBIGUOUS_BUY",
                       payload={"error": str(e), "cid": e.client_id})
            return EntryResult.ORDER_SENT
        except ValueError as e:
            self.logger.info(f"[ENTRY] ordre non envoyé: {e}")
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return EntryResult.SKIPPED
        except (ccxt.InsufficientFunds, ccxt.InvalidOrder,
                ccxt.BadRequest) as e:
            self.logger.warning(f"[ENTRY] ordre rejeté: {e}")
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return EntryResult.ORDER_SENT
        opened = self._open_from_fill(ctx, intent, results)
        return EntryResult.OPENED if opened else EntryResult.ORDER_SENT

    def _smart_buy(self, ctx: BotContext, intent: Dict[str, Any],
                   amount: float) -> List[OrderResult]:
        """Chaser limit : chaque ordre est annulé puis relu une seule fois
        (état final) → aucun double comptage."""
        cfg = self.cfg
        results: List[OrderResult] = []
        start_bid = self.ex.get_ticker()["bid"]
        max_price = start_bid * (1 + cfg.chaser_max_slippage_pct)
        filled_total = 0.0
        for attempt in range(cfg.chaser_max_attempts):
            bid = start_bid if attempt == 0 else self.ex.get_ticker()["bid"]
            if bid > max_price:
                self.logger.warning(f"[CHASE] prix évadé ({bid} > {max_price})")
                break
            remaining = self.ex.round_amount(amount - filled_total)
            if remaining <= 0 or remaining * bid < self.ex.min_notional():
                break
            cid = self.ex.new_client_id(CID_ENTRY_LIMIT)
            intent["cids"].append(cid)
            self._persist(ctx)
            try:
                self.ex.limit_buy(remaining, bid, cid)
            except (ccxt.InsufficientFunds, ccxt.InvalidOrder,
                    ccxt.BadRequest, ValueError) as e:
                self.logger.warning(f"[CHASE] limit rejeté: {e}")
                break
            self.ex.sleep(cfg.chaser_wait_sec)
            final = self._finalize_order(cid)
            results.append(final)
            filled_total += final.filled
            if filled_total >= amount * 0.999:
                break
        return results

    def _finalize_order(self, cid: str) -> OrderResult:
        last_exc: Optional[Exception] = None
        for i in range(4):
            try:
                r = self.ex.fetch_order_result(client_id=cid)
                if not r.is_open:
                    return r
                self.ex.cancel_order(client_id=cid)
            except Exception as e:
                last_exc = e
            self.ex.sleep(0.5 * (i + 1))
        raise AmbiguousOrder(f"état final inconnu: {cid} ({last_exc})", cid)

    def _open_from_fill(self, ctx: BotContext, intent: Dict[str, Any],
                        results: List[OrderResult]) -> bool:
        cfg = self.cfg
        results = [r for r in results if r.filled > 0]
        filled = sum(r.filled for r in results)
        if filled <= 0:
            self.logger.info("[ENTRY] aucun fill")
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return False
        cost = sum(r.cost if r.cost > 0 else r.filled * r.average
                   for r in results)
        avg = cost / filled
        fee_base = sum(r.fee_base for r in results)
        fee_quote = sum(r.fee_quote for r in results)
        fee_unknown = any(not r.fee_known for r in results)
        fee_other = any(r.fee_other > 0 for r in results)
        net = filled - fee_base
        if fee_unknown and self.live:
            # Frais non communiqués : on suppose (prudemment) un prélèvement
            # en base → on ne protège jamais plus que ce qui est détenu.
            net = filled * (1 - cfg.fee_rate)
        if self.live:
            sellable = self.ex.bot_free_base(ctx, force=True)
            amount_held = self.ex.round_amount(min(net, sellable))
        else:
            amount_held = self.ex.round_amount(net)
        if amount_held * avg < self.ex.min_notional():
            self.logger.warning(
                f"[ENTRY] fill {filled:.8f} trop petit pour être protégé "
                f"→ laissé en poussière")
            ctx.portfolio.dust_base += max(0.0, net)
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return False
        ctx.portfolio.dust_base += max(0.0, net - amount_held)
        extra_fee = cost * cfg.fee_rate if (fee_other or (
            fee_unknown and not self.live)) else 0.0
        cost_basis = (cost + fee_quote + extra_fee) / amount_held
        sl_dist = float(intent.get("sl_dist") or avg * 0.02)
        rr = float(intent.get("rr") or 2.0)
        if intent.get("sl_abs"):
            sl = self.ex.round_price(float(intent["sl_abs"]), "down")
            sl_dist = avg - sl
        else:
            sl = self.ex.round_price(avg - sl_dist, "down")
        if intent.get("tp_abs"):
            tp = self.ex.round_price(float(intent["tp_abs"]), "up")
        else:
            tp = self.ex.round_price(avg + sl_dist * rr, "up")
        if not (0 < sl < avg < tp):
            self.logger.critical(
                f"[ENTRY] géométrie SL/TP invalide ({sl}/{avg}/{tp}) → défaut")
            sl = self.ex.round_price(avg * (1 - cfg.max_sl_dist_pct), "down")
            tp = self.ex.round_price(avg * (1 + cfg.max_sl_dist_pct * rr), "up")
        risk_quote = amount_held * (cost_basis - sl * (1 - cfg.fee_rate))
        if intent.get("risk_per_unit"):
            # Risque planifié par la stratégie (stop de clôture) : c'est lui
            # qui définit le R, pas le stop de protection exchange.
            risk_quote = amount_held * float(intent["risk_per_unit"])
        if risk_quote <= 0:
            risk_quote = amount_held * max(avg - sl, avg * 0.001)
        candle_ts = int(intent.get("candle_ts") or int(time.time() * 1000))
        ctx.position = Position(
            in_position=True, buy_price=avg, cost_basis=cost_basis,
            sl_price=sl, tp_price=tp, amount_held=amount_held,
            initial_amount=amount_held, risk_per_unit=avg - sl,
            risk_quote_initial=risk_quote, rr_used=rr,
            module=str(intent.get("module") or ""),
            regime=str(intent.get("regime") or ""),
            tier=str(intent.get("tier") or ""),
            entry_score=int(intent.get("score") or 0),
            entry_adx=intent.get("entry_adx"),
            entry_obi=intent.get("entry_obi"),
            entry_order_id=results[-1].order_id or "PAPER",
            entry_timestamp_ms=candle_ts, opened_at=_utcnow_iso(),
            entry_candle_ts=candle_ts, sl_update_candle_ts=candle_ts,
            low_since_sl_update=avg, high_since_entry=avg,
            eff_risk_pct=intent.get("eff_risk_pct"),
            entry_equity=float(intent.get("equity") or 0.0),
            soft_stop=float(intent.get("soft_stop") or 0.0),
            highest_close=float(intent.get("ref_price") or avg))
        ctx.pending_order = None
        ctx.state = BotState.OPEN.value
        self._persist(ctx)
        self.logger.info(
            f"[ENTRY] {ctx.position.module} {ctx.position.regime} "
            f"{ctx.position.tier} qty={amount_held:.8f} @ {avg:.8f} "
            f"(coût {cost_basis:.8f}) SL={sl:.8f} TP={tp:.8f} "
            f"risque={float(intent.get('eff_risk_pct') or 0):.3f}% "
            f"({risk_quote:.4f} {cfg.quote})")
        self._event("entry", "INFO", {
            "module": ctx.position.module, "tier": ctx.position.tier,
            "score": ctx.position.entry_score, "entry": avg,
            "cost_basis": cost_basis, "sl": sl, "tp": tp,
            "amount": amount_held, "gross_filled": filled,
            "fee_base": fee_base, "fee_quote": fee_quote,
            "eff_risk_pct": intent.get("eff_risk_pct"),
            "mults": intent.get("mults")})
        if self.live:
            status = self.sync_protection(ctx)
            if ctx.position.in_position and status == "FAILED":
                self.panic_flatten(ctx, "protection impossible après achat")
            self._persist(ctx)
        return True

    # ---------- Intentions en attente ----------

    def resolve_pending(self, ctx: BotContext) -> None:
        """Résout une intention d'ordre persistée (crash, timeout réseau)
        en interrogeant l'exchange par client-id."""
        intent = ctx.pending_order
        if not intent:
            return
        if not self.live:
            ctx.pending_order = None
            return
        cids = [c for c in (intent.get("cids") or []) if c]
        results: List[OrderResult] = []
        unknown = False
        for cid in cids:
            try:
                r = self.ex.fetch_order_result(client_id=cid)
                if r.is_open:
                    self.ex.cancel_order(client_id=cid)
                    r = self.ex.fetch_order_result(client_id=cid)
                    if r.is_open:
                        unknown = True
                        continue
                results.append(r)
            except ccxt.OrderNotFound:
                continue
            except Exception as e:
                self.logger.warning(f"[PENDING] lecture {cid} KO: {e}")
                unknown = True
        if unknown:
            return
        age = time.time() - float(intent.get("ts_epoch") or 0)
        if cids and not results and age < self.cfg.pending_order_timeout_sec:
            return
        kind = intent.get("kind")
        filled = sum(r.filled for r in results)
        self.logger.warning(
            f"[PENDING] résolution {kind}: {len(results)} ordre(s) trouvé(s), "
            f"filled={filled:.8f}")
        if kind == "entry":
            if filled > 0 and not ctx.position.in_position:
                self._open_from_fill(ctx, intent, results)
            else:
                ctx.pending_order = None
                if not ctx.position.in_position:
                    ctx.state = BotState.FLAT.value
        elif kind == "exit":
            ctx.pending_order = None
            self._record_order_fills(ctx, results,
                                     str(intent.get("reason") or "EXIT"))
        else:
            ctx.pending_order = None
        if ctx.risk.halted and ctx.risk.halt_reason in AUTO_CLEARABLE_HALTS:
            self.logger.warning(
                f"[PENDING] ambiguïté résolue → levée du halt "
                f"{ctx.risk.halt_reason}")
            self.notifier(f"✅ Ambiguïté résolue ({ctx.risk.halt_reason})")
            clear_halt(ctx)
        self._persist(ctx)

    # ---------- Protection : lecture ----------

    @staticmethod
    def _leg_list(p: Position) -> List[Tuple[str, str]]:
        legs: List[Tuple[str, str]] = []
        if p.oco_tp_order_id:
            legs.append(("TP", p.oco_tp_order_id))
        if p.oco_sl_order_id:
            legs.append(("SL", p.oco_sl_order_id))
        if p.standalone_stop_order_id:
            legs.append(("STOP", p.standalone_stop_order_id))
        return legs

    def _hydrate_oco_legs(self, p: Position) -> bool:
        """Complète les IDs de jambes d'un OCO (contexte hérité / réponse
        partielle) via GET orderList. False si illisible."""
        if not p.oco_order_id or (p.oco_tp_order_id and p.oco_sl_order_id):
            return True
        try:
            resp = self.ex.query_order_list(list_id=p.oco_order_id)
        except ccxt.OrderNotFound:
            self.logger.warning(f"[PROT] orderList {p.oco_order_id} inconnue")
            p.oco_order_id = None
            return True
        except Exception as e:
            self.logger.warning(f"[PROT] orderList illisible: {e}")
            return False
        parsed = self.ex.parse_order_list(resp, p.oco_tp_client_id,
                                          p.oco_sl_client_id)
        tp, sl = parsed["tp_order_id"], parsed["sl_order_id"]
        for oid in parsed["unclassified_order_ids"]:
            try:
                r = self.ex.fetch_order_result(order_id=oid)
            except Exception as e:
                self.logger.warning(f"[PROT] jambe {oid} illisible: {e}")
                return False
            if r.order_type.startswith("STOP"):
                sl = sl or oid
            else:
                tp = tp or oid
        p.oco_tp_order_id = p.oco_tp_order_id or tp
        p.oco_sl_order_id = p.oco_sl_order_id or sl
        return bool(p.oco_tp_order_id or p.oco_sl_order_id)

    def protection_snapshot(self, ctx: BotContext) -> ProtectionSnapshot:
        p = ctx.position
        if not self._hydrate_oco_legs(p):
            return ProtectionSnapshot(ProtectionState.UNKNOWN,
                                      detail="orderList illisible")
        legs = self._leg_list(p)
        if not legs:
            return ProtectionSnapshot(ProtectionState.NONE)
        fills: List[Fill] = []
        open_ids: List[str] = []
        open_kinds: List[str] = []
        unknown = False
        for kind, oid in legs:
            try:
                r = self.ex.fetch_order_result(order_id=oid)
            except ccxt.OrderNotFound:
                continue
            except Exception as e:
                self.logger.warning(f"[PROT] lecture {kind} {oid} KO: {e}")
                unknown = True
                continue
            if r.filled > 0:
                fee = r.fee_quote if (r.fee_known and r.fee_other <= 0) else None
                fills.append(Fill(r.filled, r.average, oid, kind, fee))
            if r.is_open:
                open_ids.append(oid)
                open_kinds.append(kind)
        if fills:
            state = ProtectionState.FILLED
        elif unknown:
            state = ProtectionState.UNKNOWN
        elif any(k in ("SL", "STOP") for k in open_kinds):
            state = ProtectionState.ACTIVE
        else:
            state = ProtectionState.CANCELED
        return ProtectionSnapshot(state, fills, open_ids)

    # ---------- Protection : annulation sûre ----------

    def cancel_protection(self, ctx: BotContext) -> CancelResult:
        """Annule toute la protection puis RELIT chaque jambe : les fills
        survenus entre-temps sont retournés (jamais perdus)."""
        p = ctx.position
        if not p.has_protection_ids():
            return CancelResult(True, [])
        # Jamais d'annulation à l'aveugle : si l'état de la protection est
        # illisible, on ne pourrait pas confirmer l'annulation ni reposer un
        # stop → une panne de lecture deviendrait une position sans stop.
        pre = self.protection_snapshot(ctx)
        if pre.state == ProtectionState.UNKNOWN:
            self.logger.error("[PROT] protection illisible → annulation "
                              "reportée, ordres conservés")
            return CancelResult(False, pre.fills)
        if p.oco_order_id:
            self.ex.cancel_order_list(p.oco_order_id)
        for _kind, oid in self._leg_list(p):
            self.ex.cancel_order(order_id=oid)
        snap = self.protection_snapshot(ctx)
        if pre.fills:
            snap.fills = pre.fills + snap.fills
        if snap.state == ProtectionState.UNKNOWN or snap.open_ids:
            for oid in snap.open_ids:
                self.ex.cancel_order(order_id=oid)
            self.ex.sleep(0.5)
            snap = self.protection_snapshot(ctx)
            if snap.state == ProtectionState.UNKNOWN or snap.open_ids:
                self.logger.error("[PROT] annulation non confirmée")
                return CancelResult(False, snap.fills)
        p.clear_protection_ids()
        self.ex.invalidate_balances()
        return CancelResult(True, snap.fills)

    def _apply_fills(self, ctx: BotContext, fills: List[Fill]) -> None:
        merged: Dict[str, Fill] = {}
        for f in fills:
            cur = merged.get(f.order_id)
            if cur is None or f.qty > cur.qty:
                merged[f.order_id] = f
        for f in merged.values():
            p = ctx.position
            if not p.in_position:
                return
            done = float(p.recorded_fills.get(f.order_id, 0.0))
            delta = f.qty - done
            if delta <= 1e-12:
                continue
            p.recorded_fills[f.order_id] = f.qty
            fee = (f.fee_quote * delta / f.qty
                   if f.fee_quote is not None and f.qty > 0 else None)
            self._record_exit(ctx, f.price, delta,
                              _LEG_REASON.get(f.leg, f.leg or "BARRIER"),
                              fee_quote=fee, order_id=f.order_id)

    # ---------- Protection : synchronisation ----------

    def sync_protection(self, ctx: BotContext) -> str:
        """Retourne ACTIVE | UNCERTAIN | FAILED | CLOSED."""
        if not self.live:
            return "ACTIVE" if ctx.position.in_position else "CLOSED"
        p = ctx.position
        if not p.in_position:
            return "CLOSED"
        snap = self.protection_snapshot(ctx)
        if snap.state == ProtectionState.FILLED:
            res = self.cancel_protection(ctx)
            self._apply_fills(ctx, snap.fills + res.fills)
            self._persist(ctx)
            if not ctx.position.in_position:
                return "CLOSED"
            if not res.all_terminal:
                return "UNCERTAIN"
            return self._place_or_status(ctx)
        if snap.state == ProtectionState.ACTIVE:
            p.oco_misses = 0
            p.oco_uncertain_since = None
            p.protection_confirmed_ts = time.time()
            return "ACTIVE"
        if snap.state == ProtectionState.UNKNOWN:
            p.oco_misses = int(p.oco_misses or 0) + 1
            if p.oco_uncertain_since is None:
                p.oco_uncertain_since = _utcnow_iso()
            since = _parse_iso(p.oco_uncertain_since) or _utcnow()
            elapsed = (_utcnow() - since).total_seconds()
            # Les ordres de protection sont CONSERVÉS : les annuler sans pouvoir
            # lire leur état laisserait la position sans stop pendant la panne.
            # Le stop logiciel reste armé (statut UNCERTAIN).
            if (p.oco_misses == self.cfg.protection_unknown_max
                    or (p.oco_misses > self.cfg.protection_unknown_max
                        and p.oco_misses % 60 == 0)):
                self.logger.critical(
                    f"[PROT] protection illisible depuis {elapsed:.0f} s "
                    f"({p.oco_misses} lectures KO) : ordres conservés, stop "
                    f"logiciel armé")
                self.notifier("⚠️ Protection illisible (API Binance) : ordres "
                              "conservés, surveillance renforcée",
                              critical=True, dedup_key="prot_unknown")
            return "UNCERTAIN"
        if snap.open_ids:
            # Jambe TP seule restante (SL annulé hors bot) → on repart à neuf.
            res = self.cancel_protection(ctx)
            self._apply_fills(ctx, res.fills)
            if not ctx.position.in_position:
                self._persist(ctx)
                return "CLOSED"
            if not res.all_terminal:
                return "UNCERTAIN"
        elif p.has_protection_ids():
            self.logger.warning(
                "[PROT] protection annulée/expirée hors bot → re-protection")
            self.notifier("⚠️ Protection annulée hors bot : re-pose",
                          dedup_key="prot_canceled")
            p.clear_protection_ids()
        return self._place_or_status(ctx)

    def _place_or_status(self, ctx: BotContext) -> str:
        ok = self._place_protection(ctx)
        self._persist(ctx)
        if not ctx.position.in_position:
            return "CLOSED"
        return "ACTIVE" if ok else "FAILED"

    def _place_or_panic(self, ctx: BotContext, reason: str) -> None:
        if ctx.position.in_position and not self._place_protection(ctx):
            if ctx.position.in_position:
                self.panic_flatten(ctx, reason)

    # ---------- Protection : pose ----------

    def _place_protection(self, ctx: BotContext) -> bool:
        p = ctx.position
        self.ex.invalidate_balances()
        sellable = self.ex.bot_free_base(ctx, force=True)
        if sellable < p.amount_held * 0.98:
            # Relecture après un court délai : un solde peut être publié
            # avec retard juste après une annulation.
            self.ex.sleep(1.0)
            sellable = self.ex.bot_free_base(ctx, force=True)
        if sellable < p.amount_held * 0.98:
            self._reconcile_missing_base(ctx, sellable)
            if not ctx.position.in_position:
                return True
            p = ctx.position
        t = self.ex.get_ticker()
        last = t["last"]
        qty = self.ex.round_amount(min(p.amount_held, sellable))
        if qty * last < self.ex.min_notional():
            self._finalize_close(ctx, last, "DUST_UNPROTECTABLE")
            self._persist(ctx)
            return True
        if last >= p.tp_price or last <= p.sl_price:
            reason = "BARRIER_TP" if last >= p.tp_price else "BARRIER_SL"
            self.logger.warning(
                f"[PROT] prix {last} hors barrières [{p.sl_price}, "
                f"{p.tp_price}] → sortie marché")
            self._market_exit(ctx, qty, reason, t["bid"])
            return not ctx.position.in_position
        if self.cfg.use_oco:
            try:
                placed = self.ex.place_oco(qty, p.tp_price, p.sl_price)
                p.oco_order_id = placed["order_list_id"]
                p.oco_client_id = placed.get("list_client_order_id")
                p.oco_tp_client_id = placed.get("tp_client_order_id")
                p.oco_sl_client_id = placed.get("sl_client_order_id")
                p.oco_tp_order_id = placed.get("tp_order_id")
                p.oco_sl_order_id = placed.get("sl_order_id")
                p.protection_mode = ProtectionMode.OCO.value
                p.oco_misses = 0
                p.oco_uncertain_since = None
                if not (p.oco_tp_order_id and p.oco_sl_order_id):
                    self._hydrate_oco_legs(p)
                self.logger.info(
                    f"[PROT] OCO qty={qty} tp={p.tp_price} sl={p.sl_price} "
                    f"list={p.oco_order_id}")
                return True
            except AmbiguousOrder as e:
                self.logger.error(f"[PROT] OCO ambigu: {e}")
                if self._adopt_open_protection(ctx):
                    return True
            except Exception as e:
                self.logger.error(f"[PROT] OCO KO: {e}")
                if isinstance(e, ccxt.InsufficientFunds) \
                        and self._adopt_open_protection(ctx):
                    return True
        try:
            res = self.ex.place_stop_loss(qty, p.sl_price,
                                          self.ex.new_client_id(CID_STOP))
            p.standalone_stop_order_id = res.order_id
            p.protection_mode = ProtectionMode.STOP_ONLY.value
            if self.cfg.use_oco:
                self.logger.warning("[PROT] fallback STOP seul (TP géré en logiciel)")
                self.notifier("⚠️ Protection en STOP seul", dedup_key="stop_only")
            else:
                self.logger.info(f"[PROT] STOP qty={qty} sl={p.sl_price}")
            return True
        except AmbiguousOrder as e:
            self.logger.error(f"[PROT] STOP ambigu: {e}")
            return self._adopt_open_protection(ctx)
        except Exception as e:
            self.logger.critical(f"[PROT] STOP KO: {e}")
            if isinstance(e, ccxt.InsufficientFunds):
                return self._adopt_open_protection(ctx)
        return False

    def _adopt_open_protection(self, ctx: BotContext) -> bool:
        """Retrouve sur l'exchange nos ordres de protection ouverts
        (préfixes de client-id) et les rattache à la position."""
        try:
            orders = self.ex.fetch_open_orders()
        except Exception as e:
            self.logger.warning(f"[PROT] open orders KO: {e}")
            return False
        p = ctx.position
        found = False
        for o in orders:
            try:
                r = self.ex._parse_order(o)
            except Exception:
                continue
            if r.side != "sell" or not r.client_id.startswith(
                    PROTECTION_CID_PREFIXES):
                continue
            found = True
            is_stop = r.order_type.startswith("STOP") \
                or r.client_id.startswith((CID_OCO_SL, CID_STOP))
            if r.list_id:
                p.oco_order_id = r.list_id
                p.protection_mode = ProtectionMode.OCO.value
                if is_stop:
                    p.oco_sl_order_id = r.order_id
                else:
                    p.oco_tp_order_id = r.order_id
            elif is_stop:
                p.standalone_stop_order_id = r.order_id
                p.protection_mode = ProtectionMode.STOP_ONLY.value
        if found:
            self.logger.warning("[PROT] protection existante adoptée")
        return found

    def _reconcile_missing_base(self, ctx: BotContext, sellable: float) -> None:
        """Le solde du bot est inférieur à la position : ventes hors bot
        (manuelles) ou fills non vus. On les comptabilise au prix réel si
        possible, sinon au prix courant."""
        p = ctx.position
        missing = p.amount_held - sellable
        if missing <= 0:
            return
        opened = _parse_iso(p.opened_at) or _utcnow()
        try:
            trades = self.ex.fetch_my_trades(
                int(opened.timestamp() * 1000) - 60_000)
        except Exception as e:
            self.logger.warning(f"[PROT] my_trades KO: {e}")
            trades = []
        by_order: Dict[str, List[float]] = {}
        order_seq: List[str] = []
        for t in trades:
            if t.get("side") != "sell":
                continue
            oid = str(t.get("order") or t.get("id") or "")
            if not oid or oid in p.recorded_fills:
                continue
            if oid not in by_order:
                by_order[oid] = [0.0, 0.0]
                order_seq.append(oid)
            by_order[oid][0] += _fnum(t.get("amount"))
            by_order[oid][1] += _fnum(t.get("cost")) or (
                _fnum(t.get("amount")) * _fnum(t.get("price")))
        left = missing
        for oid in order_seq:
            q, c = by_order[oid]
            if q <= 0 or left <= 0 or not ctx.position.in_position:
                continue
            take = min(q, left)
            ctx.position.recorded_fills[oid] = q
            self.logger.critical(
                f"[PROT] vente hors bot détectée: {take:.8f} @ {c / q:.8f}")
            self._record_exit(ctx, c / q, take, "EXTERNAL_SELL", order_id=oid)
            left -= take
        if left > max(self.ex.rules.step_size, 1e-12) \
                and ctx.position.in_position:
            last = self.ex.get_ticker()["last"]
            self.logger.critical(
                f"[PROT] {left:.8f} {self.cfg.base} manquants sans vente "
                f"identifiée → sortie comptable au prix courant")
            self.notifier(f"🚨 {left:.6f} {self.cfg.base} manquants (hors bot)",
                          critical=True)
            self._record_exit(ctx, last, left, "EXTERNAL_ADJUST")

    # ---------- Comptabilité ----------

    def _record_order_fills(self, ctx: BotContext, results: List[OrderResult],
                            reason: str) -> None:
        """Comptabilise des ordres de sortie en ignorant la part déjà
        enregistrée (même ordre vu par plusieurs chemins)."""
        for r in results:
            p = ctx.position
            if not p.in_position or r.filled <= 0 or not r.order_id:
                continue
            delta = r.filled - float(p.recorded_fills.get(r.order_id, 0.0))
            if delta <= 1e-12:
                continue
            p.recorded_fills[r.order_id] = r.filled
            fee = None
            if r.fee_known and r.fee_other <= 0 and r.filled > 0:
                fee = r.fee_quote * delta / r.filled
            self._record_exit(ctx, r.average, delta, reason, fee_quote=fee,
                              order_id=r.order_id)

    def _record_exit(self, ctx: BotContext, price: float, qty: float,
                     reason: str, fee_quote: Optional[float] = None,
                     order_id: Optional[str] = None) -> None:
        p = ctx.position
        if not p.in_position or qty <= 0 or price <= 0:
            return
        if order_id and order_id != "PAPER":
            p.recorded_fills.setdefault(order_id, qty)
        qty = min(qty, p.amount_held) if p.amount_held > 0 else qty
        fee = fee_quote if fee_quote is not None else qty * price * self.cfg.fee_rate
        pnl = qty * price - fee - qty * p.cost_basis
        p.realized_pnl += pnl
        p.amount_held = max(0.0, float(Decimal(str(p.amount_held))
                                       - Decimal(str(qty))))
        if reason in ("R1", "R2"):
            p.partial_exit_count += 1
        r_leg = pnl / p.risk_quote_initial if p.risk_quote_initial > 0 else 0.0
        p.legs.append({"ts": _utcnow_iso(), "reason": reason, "qty": qty,
                       "price": price, "pnl": pnl, "r": r_leg,
                       "order_id": order_id})
        if p.amount_held > 0 and p.amount_held * price >= self.ex.min_notional():
            ctx.state = BotState.OPEN.value
            total_r = (p.realized_pnl / p.risk_quote_initial
                       if p.risk_quote_initial > 0 else 0.0)
            if self.store:
                self.store.log_trade({
                    "entry": p.buy_price, "exit": price, "amount": qty,
                    "pnl": pnl, "pnl_pct": (price / p.cost_basis - 1) * 100,
                    "r_mult": r_leg, "cumulative_pnl": p.realized_pnl,
                    "cumulative_r": total_r, "barrier": f"{reason}_PARTIAL",
                    "module": p.module, "tier": p.tier,
                    "score": p.entry_score, "entry_obi": p.entry_obi,
                    "entry_order_id": p.entry_order_id,
                    "exit_order_id": order_id, "regime": p.regime,
                    "eff_risk_pct": p.eff_risk_pct}, self.cfg.run_mode)
            self.logger.info(
                f"[CLOSE] {reason}_PARTIAL qty={qty:.8f} @ {price:.8f} "
                f"pnl={pnl:.6f} R={r_leg:+.3f} reste={p.amount_held:.8f}")
            return
        self._finalize_close(ctx, price, reason,
                             last_leg=(qty, pnl, r_leg, order_id))

    def _finalize_close(self, ctx: BotContext, price: float, reason: str,
                        last_leg: Optional[Tuple[float, float, float,
                                                 Optional[str]]] = None) -> None:
        p = ctx.position
        if not p.in_position:
            return
        dust = p.amount_held
        if dust > 0:
            dust_pnl = dust * price * (1 - self.cfg.fee_rate) - dust * p.cost_basis
            p.realized_pnl += dust_pnl
            ctx.portfolio.dust_base += dust
            self.logger.info(
                f"[CLOSE] reliquat {dust:.8f} {self.cfg.base} < min_notional "
                f"→ poussière valorisée au marché")
        total_pnl = p.realized_pnl
        total_r = (total_pnl / p.risk_quote_initial
                   if p.risk_quote_initial > 0 else 0.0)
        qty, pnl, r_leg, oid = last_leg or (0.0, 0.0, 0.0, None)
        if self.store:
            self.store.log_trade({
                "entry": p.buy_price, "exit": price, "amount": qty,
                "pnl": pnl,
                "pnl_pct": (price / p.cost_basis - 1) * 100 if p.cost_basis else 0,
                "r_mult": r_leg, "cumulative_pnl": total_pnl,
                "cumulative_r": total_r, "barrier": reason,
                "module": p.module, "tier": p.tier, "score": p.entry_score,
                "entry_obi": p.entry_obi, "entry_order_id": p.entry_order_id,
                "exit_order_id": oid, "regime": p.regime,
                "eff_risk_pct": p.eff_risk_pct}, self.cfg.run_mode)
        record_closed_trade(ctx, self.cfg, self.risk, {
            "pnl": total_pnl, "r": total_r, "module": p.module,
            "tier": p.tier, "reason": reason, "entry_adx": p.entry_adx,
            "risk_quote": p.risk_quote_initial, "entry_price": p.buy_price,
            "exit_price": price, "legs": len(p.legs),
            "ret": total_pnl / p.entry_equity if p.entry_equity > 0 else 0.0},
            logger=self.logger)
        self.logger.info(
            f"[CLOSE] {reason} {p.module}/{p.tier} exit={price:.8f} "
            f"pnl={total_pnl:+.6f} R={total_r:+.3f} jambes={len(p.legs)}")
        self._event("close", "INFO", {
            "reason": reason, "module": p.module, "tier": p.tier,
            "pnl": total_pnl, "r": total_r, "legs": p.legs})
        ctx.position = Position()
        ctx.state = BotState.FLAT.value

    # ---------- Sorties ----------

    def _market_exit(self, ctx: BotContext, qty: float, reason: str,
                     ref_price: float) -> bool:
        """Vente marché. Précondition live : protection terminale (I2)."""
        p = ctx.position
        if not p.in_position:
            return False
        full = qty >= p.amount_held * 0.999
        if not self.live:
            res = self._paper_sell(ctx, min(qty, p.amount_held), ref_price)
            self._record_exit(ctx, res.average, res.filled, reason,
                              fee_quote=res.fee_quote, order_id="PAPER")
            return True
        sellable = self.ex.bot_free_base(ctx, force=True)
        qty = self.ex.round_amount(min(qty, sellable, p.amount_held))
        if qty * ref_price < self.ex.min_notional():
            if full:
                self._finalize_close(ctx, ref_price, f"{reason}_DUST")
                self._persist(ctx)
                return True
            self.logger.warning(f"[SELL] {reason}: quantité sous le minimum")
            return False
        cid = self.ex.new_client_id(CID_EXIT_MARKET)
        ctx.pending_order = {"kind": "exit", "cids": [cid], "ts": _utcnow_iso(),
                             "ts_epoch": time.time(), "reason": reason,
                             "amount": qty}
        self._persist(ctx)
        try:
            res = self.ex.market_sell(qty, cid, ref_price)
        except AmbiguousOrder as e:
            self._halt(ctx, "AMBIGUOUS_SELL",
                       payload={"error": str(e), "cid": cid, "reason": reason})
            return False
        except (ccxt.InsufficientFunds, ccxt.InvalidOrder, ccxt.BadRequest,
                ValueError) as e:
            ctx.pending_order = None
            self.logger.error(f"[SELL] {reason} rejeté: {e}")
            self._persist(ctx)
            return False
        ctx.pending_order = None
        self._record_order_fills(ctx, [res], reason)
        self._persist(ctx)
        return res.filled > 0

    def close_position(self, ctx: BotContext, reason: str,
                       ref_price: float) -> bool:
        """Sortie totale : annulation sûre → vente → re-protection si reste."""
        if not ctx.position.in_position:
            return True
        if not self.live:
            return self._market_exit(ctx, ctx.position.amount_held, reason,
                                     ref_price)
        cancel = self.cancel_protection(ctx)
        self._apply_fills(ctx, cancel.fills)
        if not ctx.position.in_position:
            self._persist(ctx)
            return True
        if not cancel.all_terminal:
            self.logger.error(f"[{reason}] annulation protection non confirmée "
                              f"→ sortie reportée")
            self._persist(ctx)
            return False
        self._market_exit(ctx, ctx.position.amount_held, reason, ref_price)
        if ctx.position.in_position and not ctx.pending_order:
            self._place_or_panic(ctx, f"{reason}: re-protection impossible")
        self._persist(ctx)
        return not ctx.position.in_position

    def _reduce_position(self, ctx: BotContext, qty: float, reason: str,
                         ref_price: float) -> bool:
        if not self.live:
            return self._market_exit(ctx, qty, reason, ref_price)
        cancel = self.cancel_protection(ctx)
        self._apply_fills(ctx, cancel.fills)
        if not ctx.position.in_position:
            self._persist(ctx)
            return False
        if not cancel.all_terminal:
            self.logger.error(f"[{reason}] annulation protection non confirmée")
            self._persist(ctx)
            return False
        if cancel.fills:
            # La protection a exécuté entre-temps : on re-protège le reste
            # et on réévalue au cycle suivant.
            self._place_or_panic(ctx, f"{reason}: re-protection impossible")
            self._persist(ctx)
            return False
        ok = self._market_exit(ctx, qty, reason, ref_price)
        if ctx.position.in_position and not ctx.pending_order:
            self._place_or_panic(ctx, f"{reason}: re-protection impossible")
        self._persist(ctx)
        return ok

    def panic_flatten(self, ctx: BotContext, reason: str) -> bool:
        if ctx.flatten_in_progress:
            self.logger.error("[PANIC] réentrant → ignoré")
            return False
        ctx.flatten_in_progress = True
        try:
            self.logger.critical(f"[PANIC] {reason}")
            if self.store:
                self.store.log_panic_sequence(
                    reason, {"symbol": self.cfg.symbol, "state": ctx.state,
                             "amount_held": ctx.position.amount_held},
                    self.cfg.run_mode)
            self.notifier(f"🚨 PANIC FLATTEN: {reason}", critical=True)
            if not ctx.position.in_position:
                return True
            if not self.live:
                return self._market_exit(ctx, ctx.position.amount_held,
                                         "PANIC", self.ex.get_ticker()["bid"])
            for _attempt in range(3):
                cancel = self.cancel_protection(ctx)
                self._apply_fills(ctx, cancel.fills)
                if not ctx.position.in_position:
                    return True
                if cancel.all_terminal:
                    bid = self.ex.get_ticker()["bid"]
                    self._market_exit(ctx, ctx.position.amount_held, "PANIC", bid)
                    if not ctx.position.in_position:
                        return True
                    if ctx.pending_order:
                        return False  # vente ambiguë : resolve_pending
                self.ex.sleep(1.0)
            self._halt(ctx, "PANIC_FAILED")
            return False
        except Exception as e:
            self.logger.exception(f"[PANIC] exception: {e}")
            self._halt(ctx, "PANIC_EXCEPTION", payload={"error": str(e)})
            return False
        finally:
            ctx.flatten_in_progress = False
            self._persist(ctx)

    # ---------- Gestion de position (chaque cycle) ----------

    def manage_position(self, ctx: BotContext, df: pd.DataFrame, closed: Any,
                        live_price: float, current_candle_ts: int) -> None:
        if not ctx.position.in_position:
            return
        update_extremes(ctx.position, df, live_price)
        if self.live:
            if not self.maintain_protection(ctx, live_price):
                return
        else:
            if self._paper_barriers(ctx, df, live_price):
                return
        if is_frozen(ctx):
            return
        self.check_partial_exits(ctx, live_price)
        if not ctx.position.in_position:
            return
        if self.should_time_exit(ctx, live_price):
            self.execute_time_exit(ctx, live_price)
            if not ctx.position.in_position:
                return
        self.apply_break_even(ctx, live_price, current_candle_ts)
        if not ctx.position.in_position:
            return
        self.apply_trailing(ctx, closed, live_price, current_candle_ts)

    def maintain_protection(self, ctx: BotContext, live_price: float) -> bool:
        """Live : synchronise la protection exchange, applique le stop
        logiciel de dernier recours. Retourne False si la position a été
        clôturée ou liquidée (rien d'autre à faire ce cycle)."""
        if not ctx.position.in_position:
            return False
        status = self.sync_protection(ctx)
        if not ctx.position.in_position:
            return False
        if status == "FAILED":
            self.logger.critical("[PROT] position sans protection → liquidation")
            self.panic_flatten(ctx, "protection impossible")
            return False
        p = ctx.position
        if self.cfg.software_stop_enabled:
            if status == "UNCERTAIN" and live_price <= p.sl_price:
                self.panic_flatten(ctx, "stop logiciel (protection incertaine)")
                return False
            if live_price <= p.sl_price * (1 - self.cfg.stop_limit_offset_pct):
                # Stop déclenché mais non exécuté (ex. STOP_LOSS_LIMIT
                # dépassé par un gap) : sortie marché de dernier recours.
                self.panic_flatten(ctx, "stop logiciel (stop exchange non exécuté)")
                return False
        if (self.cfg.use_oco
                and p.protection_mode == ProtectionMode.STOP_ONLY.value
                and live_price >= p.tp_price and not is_frozen(ctx)):
            self.close_position(ctx, "BARRIER_TP", live_price)
            return False
        return True

    def _paper_barriers(self, ctx: BotContext, df: pd.DataFrame,
                        live_price: float) -> bool:
        """Simulation des ordres de protection en paper : uniquement les
        barres POSTÉRIEURES à l'entrée / au dernier déplacement du stop,
        puis le prix courant. SL prioritaire (hypothèse prudente)."""
        p = ctx.position
        gate = int(p.sl_update_candle_ts or p.entry_candle_ts or 0)
        fill_px: Optional[float] = None
        reason = ""
        post = df[df["ts"] > gate] if len(df) else df
        for row in post.itertuples(index=False):
            if float(row.low) <= p.sl_price:
                fill_px = min(p.sl_price, float(row.open)) \
                    * (1 - self.cfg.paper_slippage_pct)
                reason = "BARRIER_SL"
                break
            if float(row.high) >= p.tp_price:
                fill_px, reason = p.tp_price, "BARRIER_TP"
                break
        if fill_px is None:
            if live_price <= p.sl_price:
                fill_px = p.sl_price * (1 - self.cfg.paper_slippage_pct)
                reason = "BARRIER_SL"
            elif live_price >= p.tp_price:
                fill_px, reason = p.tp_price, "BARRIER_TP"
        if fill_px is None:
            return False
        # fill_px inclut déjà le slippage (SL) ; le TP est un ordre maker.
        res = self._paper_sell(ctx, p.amount_held, fill_px, maker=True)
        self._record_exit(ctx, res.average, res.filled, reason,
                          fee_quote=res.fee_quote, order_id="PAPER")
        return not ctx.position.in_position

    def check_partial_exits(self, ctx: BotContext, live_price: float) -> None:
        cfg = self.cfg
        p = ctx.position
        if (not cfg.partial_exit_enabled or not p.in_position
                or p.risk_per_unit <= 0):
            return
        levels = [(cfg.partial_exit_r1, cfg.partial_exit_pct1),
                  (cfg.partial_exit_r2, cfg.partial_exit_pct2)]
        while p.in_position and p.partial_exit_count < len(levels):
            idx = p.partial_exit_count
            r_level, pct = levels[idx]
            if live_price < p.buy_price + p.risk_per_unit * r_level:
                return
            qty = self.ex.round_amount(min(p.initial_amount * pct, p.amount_held))
            remaining = p.amount_held - qty
            mn = self.ex.min_notional()
            if (qty * live_price < mn
                    or remaining * live_price < mn * cfg.min_remaining_notional_mult):
                self.logger.info(f"[R5] partiel R{idx + 1} ignoré (taille)")
                p.partial_exit_count += 1
                continue
            self.logger.info(f"[R5] partiel R{idx + 1}: {qty:.8f}")
            self._reduce_position(ctx, qty, f"R{idx + 1}", live_price)
            return

    def apply_break_even(self, ctx: BotContext, live_price: float,
                         current_candle_ts: int) -> bool:
        p = ctx.position
        if p.break_even_done or not self.cfg.break_even_enabled:
            return False
        if float(p.high_since_entry or 0) < p.buy_price * (
                1 + self.cfg.break_even_trigger):
            return False
        new_sl = self.ex.round_price(break_even_stop(p, self.cfg), "up")
        if new_sl <= p.sl_price:
            p.break_even_done = True
            return False
        if new_sl >= live_price * 0.999:
            return False
        ok = self._modify_stop(ctx, new_sl, current_candle_ts, "BE")
        if ok and ctx.position.in_position:
            ctx.position.break_even_done = True
            ctx.position.low_since_sl_update = live_price
        return ok

    def apply_trailing(self, ctx: BotContext, closed: Any, live_price: float,
                       current_candle_ts: int) -> bool:
        p = ctx.position
        if not self.cfg.trailing_enabled or not p.break_even_done:
            return False
        atr = _row_get(closed, "atr")
        if _isnan(atr):
            return False
        if float(p.high_since_entry or 0) <= p.buy_price + p.risk_per_unit:
            return False
        if time.time() - float(p.last_trailing_ts or 0) \
                < self.cfg.trailing_min_interval_sec:
            return False
        raw = trailing_stop(p, float(atr), self.cfg)
        if raw is None:
            return False
        new_sl = self.ex.round_price(raw, "down")
        if new_sl <= p.sl_price * (1 + self.cfg.trailing_min_raise_pct):
            return False
        if new_sl >= live_price * 0.999:
            return False
        return self._modify_stop(ctx, new_sl, current_candle_ts, "TRAIL")

    def _modify_stop(self, ctx: BotContext, new_sl: float,
                     current_candle_ts: int, reason: str) -> bool:
        p = ctx.position
        if new_sl <= p.sl_price:
            return False
        if not self.live:
            self.logger.info(f"[{reason}] SL {p.sl_price:.8f} → {new_sl:.8f}")
            p.sl_price = new_sl
            p.sl_update_candle_ts = current_candle_ts
            p.last_trailing_ts = time.time()
            return True
        cancel = self.cancel_protection(ctx)
        self._apply_fills(ctx, cancel.fills)
        if not ctx.position.in_position:
            self._persist(ctx)
            return False
        if not cancel.all_terminal:
            self.logger.error(f"[{reason}] annulation non confirmée → SL inchangé")
            self._persist(ctx)
            return False
        if cancel.fills:
            self._place_or_panic(ctx, f"{reason}: re-protection impossible")
            self._persist(ctx)
            return False
        old = p.sl_price
        p.sl_price = new_sl
        if self._place_protection(ctx):
            if ctx.position.in_position:
                p.sl_update_candle_ts = current_candle_ts
                p.last_trailing_ts = time.time()
                self.logger.info(f"[{reason}] SL {old:.8f} → {new_sl:.8f}")
            self._persist(ctx)
            return ctx.position.in_position
        if not ctx.position.in_position:
            self._persist(ctx)
            return False
        p.sl_price = old
        if self._place_protection(ctx):
            self.logger.warning(f"[{reason}] nouveau SL KO, ancien restauré")
            self._persist(ctx)
            return False
        self.panic_flatten(ctx, f"{reason}: aucune protection")
        return False

    def should_time_exit(self, ctx: BotContext, live_price: float) -> bool:
        p = ctx.position
        if not self.cfg.time_exit_enabled:
            return False
        cd = _parse_iso(p.time_exit_cooldown_until)
        if cd is not None and _utcnow() < cd:
            return False
        if not p.opened_at or p.break_even_done:
            return False
        if time.time() - float(p.time_exit_last_attempt_ts or 0) \
                < self.cfg.time_exit_min_retry_sec:
            return False
        opened = _parse_iso(p.opened_at)
        if opened is None:
            return False
        age_h = (_utcnow() - opened).total_seconds() / 3600
        if age_h < self.cfg.max_trade_age_hours:
            return False
        return live_price < p.buy_price + p.risk_per_unit * self.cfg.time_exit_min_r_mult

    def execute_time_exit(self, ctx: BotContext, live_price: float) -> bool:
        p = ctx.position
        p.time_exit_last_attempt_ts = time.time()
        if p.time_exit_attempts >= self.cfg.time_exit_max_attempts:
            p.time_exit_attempts = 0
            p.time_exit_cooldown_until = (_utcnow() + timedelta(
                hours=self.cfg.time_exit_cooldown_hours)).isoformat()
            return False
        ok = self.close_position(ctx, "BARRIER_TIME", live_price)
        if not ok and ctx.position.in_position:
            ctx.position.time_exit_attempts += 1
        return ok

    # ---------- À plat : orphelins et poussière ----------

    def check_orphan(self, ctx: BotContext, last_price: float,
                     force: bool = False) -> None:
        if not self.live or ctx.position.in_position or ctx.pending_order:
            return
        now = time.time()
        if not force and now - ctx.last_orphan_check_ts < self.cfg.orphan_recheck_sec:
            return
        ctx.last_orphan_check_ts = now
        total = self.ex.bot_total_base(ctx, force=True)
        ctx.portfolio.dust_base = min(float(ctx.portfolio.dust_base), total)
        unexplained = max(0.0, total - ctx.portfolio.dust_base)
        if 0 < unexplained * last_price < 2 * self.ex.min_notional():
            # Reliquats de frais/arrondis : traités comme poussière du bot
            # (revendue par sweep_dust dès qu'elle est négociable).
            ctx.portfolio.dust_base = total
            unexplained = 0.0
        if unexplained * last_price >= 2 * self.ex.min_notional():
            if not ctx.orphan_balance:
                self.logger.critical(
                    f"[ORPHAN] {unexplained:.8f} {self.cfg.base} non suivis "
                    f"→ entrées bloquées (vendre/déclarer EXTERNAL_BASE_RESERVE "
                    f"puis `resume`)")
                self.notifier(f"⚠️ Solde orphelin {unexplained:.6f} "
                              f"{self.cfg.base}", critical=True)
                ctx.orphan_balance = True
                ctx.orphan_balance_since = _utcnow_iso()
        elif ctx.orphan_balance:
            self.logger.warning("[ORPHAN] résolu (solde revenu sous le minimum)")
            ctx.orphan_balance = False
            ctx.orphan_balance_since = None

    def sweep_dust(self, ctx: BotContext, last_price: float) -> None:
        """Revend la poussière accumulée par le bot dès qu'elle devient
        négociable (live uniquement)."""
        if not self.live or ctx.position.in_position or ctx.pending_order:
            return
        dust = float(ctx.portfolio.dust_base)
        if dust * last_price < self.ex.min_notional() * 1.2:
            return
        free = self.ex.bot_free_base(ctx, force=True)
        qty = self.ex.round_amount(min(dust, free))
        if qty * last_price < self.ex.min_notional():
            return
        cid = self.ex.new_client_id(CID_EXIT_MARKET)
        try:
            res = self.ex.market_sell(qty, cid, last_price)
        except Exception as e:
            self.logger.warning(f"[DUST] revente KO: {e}")
            return
        ctx.portfolio.dust_base = max(0.0, dust - res.filled)
        self.logger.info(f"[DUST] {res.filled:.8f} revendus @ {res.average:.8f}")
        self._persist(ctx)
