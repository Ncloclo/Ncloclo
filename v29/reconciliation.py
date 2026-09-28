"""Réconciliation au démarrage avec l'exchange.

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import logging
import time
from typing import List

from .config import Config
from .constants import PROTECTION_CID_PREFIXES
from .exchange import ExchangeAdapter, OrderResult, _fnum
from .execution import ExecutionEngine
from .models import BotContext, BotState, HaltKind, Position, ProtectionMode, halt_ctx
from .utils import _utcnow_iso


def reconcile(ctx: BotContext, cfg: Config, ex: ExchangeAdapter,
              logger: logging.Logger,
              exec_engine: ExecutionEngine) -> BotContext:
    """Aligne le contexte sur l'exchange au démarrage. Fail-closed : toute
    erreur d'API lève (le boot est interrompu, rien n'est effacé)."""
    if cfg.run_mode == "paper":
        ctx.pending_order = None
        return ctx
    ex.refresh_balances(ctx, force=True)
    open_orders = ex.fetch_open_orders()
    exec_engine.resolve_pending(ctx)
    if ctx.pending_order:
        # Résolu automatiquement aux cycles suivants (halt auto-levable).
        logger.critical("[RECON] intention d'ordre non résolue → HALT")
        halt_ctx(ctx, "PENDING_ORDER_UNRESOLVED", HaltKind.INTEGRITY)
    if ctx.position.in_position:
        # La protection est TOUJOURS vérifiée, même avec une intention en
        # attente (les fills sont dédupliqués par order_id).
        status = exec_engine.sync_protection(ctx)
        if ctx.position.in_position and status == "FAILED":
            exec_engine.panic_flatten(ctx, "protection impossible au boot")
        state = "maintenue" if ctx.position.in_position else "clôturée"
        logger.info(f"[RECON] position {state} (protection={status})")
        return ctx
    if ctx.pending_order:
        return ctx
    ours = []
    for o in open_orders:
        try:
            r = ex._parse_order(o)
        except Exception:
            continue
        if r.side == "sell" and r.client_id.startswith(PROTECTION_CID_PREFIXES):
            ours.append(r)
    if ours:
        if not cfg.recovery_adopt_orders:
            # Des ordres du bot existent mais cette base ne les connaît pas :
            # soit la base a été perdue, soit UNE AUTRE INSTANCE gère ce
            # compte. Les adopter ferait gérer la même position par deux bots.
            ids = ", ".join(o.client_id for o in ours[:4])
            logger.critical(
                f"[RECON] {len(ours)} ordre(s) du bot inconnu(s) de cette base "
                f"({ids}) : une autre instance gère peut-être ce compte → HALT. "
                f"Base perdue ? Relancer une fois avec RECOVERY_ADOPT_ORDERS=true.")
            halt_ctx(ctx, "UNKNOWN_BOT_ORDERS", HaltKind.MANUAL)
            return ctx
        _recover_position(ctx, cfg, ex, logger, exec_engine, ours)
        return ctx
    last = ex.get_ticker()["last"]
    exec_engine.check_orphan(ctx, last, force=True)
    return ctx


def _recover_position(ctx: BotContext, cfg: Config, ex: ExchangeAdapter,
                      logger: logging.Logger, exec_engine: ExecutionEngine,
                      orders: List[OrderResult]) -> None:
    """Ordres de protection du bot présents alors que le contexte est à plat
    (perte du contexte) : reconstruction à partir des ordres et des achats."""
    sl = next((o.stop_price for o in orders
               if o.order_type.startswith("STOP") and o.stop_price > 0), None)
    tp = next((o.price for o in orders
               if not o.order_type.startswith("STOP") and o.price > 0), None)
    qty_orders = max((o.amount - o.filled) for o in orders)
    amount = ex.round_amount(min(qty_orders, ex.bot_total_base(ctx, force=True)))
    since = int((time.time() - cfg.reconcile_trades_window_hours * 3600) * 1000)
    try:
        trades = ex.fetch_my_trades(since)
    except Exception as e:
        logger.critical(f"[RECON] historique d'achats illisible: {e}")
        halt_ctx(ctx, "RECOVERY_TRADES_UNAVAILABLE", HaltKind.MANUAL)
        return
    buys = [t for t in trades if t.get("side") == "buy"]
    if buys:
        acc_q = acc_c = 0.0
        for t in reversed(buys):
            q = _fnum(t.get("amount"))
            take = min(q, amount - acc_q)
            if take <= 0:
                break
            acc_q += take
            acc_c += take * _fnum(t.get("price"))
        buy_px = acc_c / acc_q if acc_q > 0 else ex.get_ticker()["last"]
    else:
        if cfg.recovery_require_verified_entry:
            logger.critical("[RECON] protection orpheline sans achat vérifié → HALT")
            halt_ctx(ctx, "RECOVERY_ENTRY_UNKNOWN", HaltKind.MANUAL, orphan=True)
            return
        buy_px = ex.get_ticker()["last"]
    if sl is None:
        sl = ex.round_price(buy_px * (1 - cfg.max_sl_dist_pct), "down")
    risk_unit = max(buy_px - sl, buy_px * 0.005)
    if tp is None or tp <= sl:
        tp = ex.round_price(buy_px + 2 * risk_unit, "up")
    if amount * buy_px < ex.min_notional():
        logger.warning("[RECON] protection orpheline sur quantité négligeable")
        return
    now_ms = int(time.time() * 1000)
    cost_basis = buy_px * (1 + cfg.fee_rate)
    p = Position(
        in_position=True, buy_price=buy_px, cost_basis=cost_basis,
        sl_price=sl, tp_price=tp, amount_held=amount, initial_amount=amount,
        risk_per_unit=risk_unit, risk_quote_initial=amount * risk_unit,
        rr_used=(tp - buy_px) / risk_unit, module="recovered",
        break_even_done=sl >= buy_px, opened_at=_utcnow_iso(),
        entry_timestamp_ms=now_ms, entry_candle_ts=now_ms,
        sl_update_candle_ts=now_ms, low_since_sl_update=buy_px,
        high_since_entry=buy_px)
    for o in orders:
        is_stop = o.order_type.startswith("STOP")
        if o.list_id:
            p.oco_order_id = o.list_id
            p.protection_mode = ProtectionMode.OCO.value
            if is_stop:
                p.oco_sl_order_id = o.order_id
            else:
                p.oco_tp_order_id = o.order_id
        elif is_stop:
            p.standalone_stop_order_id = o.order_id
            p.protection_mode = ProtectionMode.STOP_ONLY.value
    ctx.position = p
    ctx.state = BotState.OPEN.value
    logger.warning(f"[RECON] position récupérée {amount:.8f} @ {buy_px:.8f} "
                   f"SL={sl} TP={tp}")
    exec_engine.sync_protection(ctx)
