"""Scénarios live de bout en bout contre le simulateur Binance Spot.
Chaque test verrouille la correction d'un défaut du diagnostic V29.5."""

import time

import ccxt
import pytest

import v29
from conftest import build_env, fresh_closed, open_live_position
from fake_binance import FakeBinance


def _prot_orders(fb):
    return fb.open_protection_orders()


def test_entry_is_protected_not_panic_sold(live_env):
    """V29.5 : cache de solde périmé → 'balance insuffisante' → panic sell
    systématique ; frais en base → OCO refusé (quantité > solde)."""
    env = live_env
    res = open_live_position(env)
    p = env.ctx.position
    assert res == v29.EntryResult.OPENED
    assert p.in_position
    assert p.protection_mode == v29.ProtectionMode.OCO.value
    # Quantité protégée = net reçu (frais prélevés en base), jamais plus.
    assert p.amount_held <= env.fb.free["TRX"] + env.fb.locked["TRX"] + 1e-9
    legs = _prot_orders(env.fb)
    assert {o["type"] for o in legs} == {"LIMIT_MAKER", "STOP_LOSS"}
    assert all(abs(o["amount"] - p.amount_held) < 1e-9 for o in legs)
    assert "create_order:market:sell" not in env.fb.calls
    assert not env.ctx.risk.halted
    assert env.ctx.pending_order is None


def test_invalid_spot_order_types_rejected_by_simulator():
    fb = FakeBinance()
    with pytest.raises(ccxt.InvalidOrder):
        fb.create_order("TRX/USDT", "STOP_LOSS_MARKET", "sell", 100, None,
                        {"stopPrice": 0.09})


def test_tp_exit_is_recorded_without_false_orphan(live_env):
    """V29.5 : chaque sortie OCO déclenchait un faux 'double-sell' et
    orphan_balance=True permanent."""
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    env.fb.set_price(p.tp_price * 1.001)
    df = _df_stub(env)
    env.eng.manage_position(env.ctx, df, fresh_closed(env.cfg),
                            env.fb.last, int(time.time() * 1000))
    assert not env.ctx.position.in_position
    assert env.ctx.orphan_balance is False
    assert env.ctx.portfolio.stats_wins == 1
    assert env.ctx.portfolio.last_trades[-1]["reason"] == "BARRIER_TP"
    assert not env.ctx.risk.halted


def test_sl_exit_is_recorded_as_loss(live_env):
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    env.fb.set_price(p.sl_price * 0.999)
    env.eng.manage_position(env.ctx, _df_stub(env), fresh_closed(env.cfg),
                            env.fb.last, int(time.time() * 1000))
    assert not env.ctx.position.in_position
    t = env.ctx.portfolio.last_trades[-1]
    assert t["reason"] == "BARRIER_SL"
    assert t["pnl"] < 0
    assert -1.3 < t["r"] < -0.8          # ≈ -1R (frais/slippage inclus)
    assert env.ctx.orphan_balance is False


def test_partial_exit_live_cancels_then_reprotects(live_env):
    """V29.5 : vente partielle refusée (OCO bloquant 100 % de la base)."""
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    initial = p.initial_amount
    r1 = p.buy_price + p.risk_per_unit * env.cfg.partial_exit_r1
    env.fb.set_price(r1 * 1.001)
    env.eng.check_partial_exits(env.ctx, env.fb.last)
    p = env.ctx.position
    assert p.in_position and p.partial_exit_count == 1
    assert abs(p.amount_held - (initial - round(initial * 0.4 / 0.1) * 0.1)) < 0.2
    legs = _prot_orders(env.fb)
    assert len(legs) == 2
    assert all(abs(o["amount"] - p.amount_held) < 1e-9 for o in legs)
    assert p.realized_pnl > 0


def test_break_even_covers_costs(live_env):
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    p.high_since_entry = p.buy_price * 1.012
    env.fb.set_price(p.buy_price * 1.012)
    ok = env.eng.apply_break_even(env.ctx, env.fb.last, int(time.time() * 1000))
    p = env.ctx.position
    assert ok and p.break_even_done
    net_exit = p.sl_price * (1 - env.cfg.fee_rate - env.cfg.exit_slippage_buffer_pct)
    assert net_exit >= p.cost_basis - 1e-9
    sl_leg = [o for o in _prot_orders(env.fb) if o["type"] == "STOP_LOSS"][0]
    assert abs(sl_leg["stop"] - p.sl_price) < 1e-9


def test_time_exit_closes_without_typeerror(live_env):
    """V29.5 : TypeError (kwarg inexistant) APRÈS l'annulation de l'OCO →
    position laissée sans stop."""
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    p.opened_at = (v29._utcnow() - v29.timedelta(hours=49)).isoformat()
    env.fb.set_price(p.buy_price * 0.995)
    assert env.eng.should_time_exit(env.ctx, env.fb.last)
    assert env.eng.execute_time_exit(env.ctx, env.fb.last)
    assert not env.ctx.position.in_position
    assert _prot_orders(env.fb) == []
    assert env.ctx.portfolio.last_trades[-1]["reason"] == "BARRIER_TIME"


def test_position_without_protection_ids_is_reprotected(live_env):
    """V29.5 : poll_protection ne faisait rien sans IDs → position nue."""
    env = live_env
    open_live_position(env)
    env.eng.cancel_protection(env.ctx)
    assert _prot_orders(env.fb) == []
    assert not env.ctx.position.has_protection_ids()
    status = env.eng.sync_protection(env.ctx)
    assert status == "ACTIVE"
    assert len(_prot_orders(env.fb)) == 2


def test_protection_cancelled_outside_bot_is_replaced(live_env):
    env = live_env
    open_live_position(env)
    old_list = env.ctx.position.oco_order_id
    env.fb.privateDeleteOrderList({"symbol": "TRXUSDT", "orderListId": old_list})
    assert env.eng.sync_protection(env.ctx) == "ACTIVE"
    assert env.ctx.position.oco_order_id != old_list
    assert len(_prot_orders(env.fb)) == 2


def test_fill_during_cancel_is_not_lost(live_env):
    """V29.5 (FIX #36) : un fill entre vérification et annulation était
    perdu (cancel → OrderNotFound → True)."""
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    env.fb.set_price(p.sl_price * 0.999)      # le stop s'exécute côté exchange
    res = env.eng.cancel_protection(env.ctx)
    assert res.all_terminal
    assert res.fills and res.fills[0].leg == "SL"
    env.eng._apply_fills(env.ctx, res.fills)
    assert not env.ctx.position.in_position
    # Idempotence : ré-appliquer les mêmes fills ne double-compte pas.
    trades_before = len(env.ctx.portfolio.last_trades)
    env.eng._apply_fills(env.ctx, res.fills)
    assert len(env.ctx.portfolio.last_trades) == trades_before


def test_ambiguous_buy_is_resolved_and_adopted(live_env):
    env = live_env
    env.fb.create_faults = ["after", "after", "after"]  # ordre passé, réponse perdue
    env.ex._lookup_by_cid = lambda cid, attempts=3: None  # l'exchange « ne voit » pas encore
    res = open_live_position(env)
    assert res == v29.EntryResult.ORDER_SENT
    assert env.ctx.risk.halted and env.ctx.risk.halt_reason == "AMBIGUOUS_BUY"
    assert env.ctx.pending_order and env.ctx.pending_order["cids"]
    del env.ex._lookup_by_cid
    env.fb.create_faults = []
    env.eng.resolve_pending(env.ctx)
    assert env.ctx.position.in_position
    assert env.ctx.pending_order is None
    assert not env.ctx.risk.halted
    assert len(_prot_orders(env.fb)) == 2


def test_ambiguous_buy_never_placed_times_out(live_env):
    env = live_env
    env.fb.create_faults = ["before"]
    res = open_live_position(env)
    assert res == v29.EntryResult.ORDER_SENT
    assert env.ctx.pending_order is not None
    env.eng.resolve_pending(env.ctx)          # trop tôt : on attend
    assert env.ctx.pending_order is not None
    env.ctx.pending_order["ts_epoch"] -= env.cfg.pending_order_timeout_sec + 1
    env.eng.resolve_pending(env.ctx)
    assert env.ctx.pending_order is None
    assert not env.ctx.position.in_position
    assert not env.ctx.risk.halted


def test_reconcile_fails_closed_on_balance_error(live_env):
    """V29.5 : un timeout de fetch_balance au boot effaçait la position."""
    env = live_env
    open_live_position(env)
    env.fb.fail_balance = True
    with pytest.raises(ccxt.NetworkError):
        v29.reconcile(env.ctx, env.cfg, env.ex, env.logger, env.eng)
    assert env.ctx.position.in_position


def test_reconcile_records_oco_fill_while_down(live_env):
    env = live_env
    open_live_position(env)
    saved = v29.BotContext.from_dict(env.ctx.to_dict())
    env.fb.set_price(env.ctx.position.tp_price * 1.002)   # TP pendant l'arrêt
    fresh = build_env("live", fb=env.fb, logger=env.logger)
    fresh.ctx = saved
    v29.reconcile(saved, fresh.cfg, fresh.ex, fresh.logger, fresh.eng)
    assert not saved.position.in_position
    assert saved.portfolio.stats_wins == 1
    assert saved.orphan_balance is False


def test_reconcile_recovers_lost_context(live_env):
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    fresh = build_env("live", fb=env.fb, logger=env.logger)
    v29.reconcile(fresh.ctx, fresh.cfg, fresh.ex, fresh.logger, fresh.eng)
    q = fresh.ctx.position
    assert q.in_position and q.module == "recovered"
    assert abs(q.amount_held - p.amount_held) < 1e-9
    assert abs(q.sl_price - p.sl_price) < 1e-9
    assert q.oco_order_id == p.oco_order_id


def test_panic_flatten_sells_everything(live_env):
    env = live_env
    open_live_position(env)
    assert env.eng.panic_flatten(env.ctx, "test")
    assert not env.ctx.position.in_position
    assert _prot_orders(env.fb) == []
    assert env.fb.free["TRX"] * env.fb.last < env.fb.min_notional_value


def test_external_base_reserve_is_never_sold(logger):
    fb = FakeBinance(base_balance=5000.0)
    env = build_env("live", fb=fb, logger=logger, external_base_reserve=5000.0)
    open_live_position(env)
    held = env.ctx.position.amount_held
    assert held < 5000
    env.eng.panic_flatten(env.ctx, "test")
    assert not env.ctx.position.in_position
    assert fb.free["TRX"] >= 5000.0 - 1e-6


def test_orphan_detected_then_cleared(live_env):
    env = live_env
    env.fb.free["TRX"] = 1000.0                   # 100 USDT non suivis
    env.eng.check_orphan(env.ctx, env.fb.last, force=True)
    assert env.ctx.orphan_balance
    env.fb.free["TRX"] = 1.0
    env.eng.check_orphan(env.ctx, env.fb.last, force=True)
    assert not env.ctx.orphan_balance


def test_stop_limit_market_fallback_and_software_stop(logger):
    """Marché sans STOP_LOSS : STOP_LOSS_LIMIT + stop logiciel si un gap
    dépasse la limite (ordre déclenché mais non exécuté)."""
    fb = FakeBinance(order_types=["LIMIT", "LIMIT_MAKER", "MARKET",
                                  "STOP_LOSS_LIMIT", "TAKE_PROFIT_LIMIT"])
    env = build_env("live", fb=fb, logger=logger)
    assert env.ex.stop_order_type == "STOP_LOSS_LIMIT"
    open_live_position(env)
    p = env.ctx.position
    assert {o["type"] for o in _prot_orders(fb)} == {"LIMIT_MAKER", "STOP_LOSS_LIMIT"}
    fb.set_price(p.sl_price * 0.97)               # gap sous la limite
    assert env.ctx.position.in_position
    env.eng.manage_position(env.ctx, _df_stub(env), fresh_closed(env.cfg),
                            fb.last, int(time.time() * 1000))
    assert not env.ctx.position.in_position
    assert env.ctx.portfolio.last_trades[-1]["reason"] == "PANIC"


def test_self_test_uses_order_test_endpoint(live_env):
    env = live_env
    before = [c for c in env.fb.calls if c.startswith("create_order")]
    assert env.ex.self_test_conditional_orders()
    after = [c for c in env.fb.calls if c.startswith("create_order")]
    assert before == after                      # aucun ordre réel
    assert "order_test" in env.fb.calls


def test_self_test_fails_without_stop_type(logger):
    fb = FakeBinance(order_types=["LIMIT", "MARKET", "LIMIT_MAKER"])
    env = build_env("live", fb=fb, logger=logger)
    assert not env.ex.self_test_conditional_orders()


def test_order_list_query_sends_no_symbol(live_env):
    env = live_env
    open_live_position(env)
    resp = env.ex.query_order_list(list_id=env.ctx.position.oco_order_id)
    assert resp["listOrderStatus"] == "EXECUTING"


def test_legacy_context_without_leg_ids_is_hydrated(live_env):
    """Contexte V29.5 : seul l'orderListId est connu."""
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    p.oco_tp_order_id = None
    p.oco_sl_order_id = None
    snap = env.eng.protection_snapshot(env.ctx)
    assert snap.state == v29.ProtectionState.ACTIVE
    assert p.oco_tp_order_id and p.oco_sl_order_id


def test_smart_buy_counts_fills_once(logger):
    env = build_env("live", logger=logger, use_smart_buy=True)
    # Le marché descend légèrement pendant l'attente : l'ordre passif au bid
    # est exécuté (maker).
    env.ex.sleep = lambda s: env.fb.set_price(env.fb.last * 0.9997)
    res = open_live_position(env)
    assert res == v29.EntryResult.OPENED
    p = env.ctx.position
    bought = sum(o["filled"] for o in env.fb.orders.values() if o["side"] == "buy")
    assert p.amount_held <= bought * (1 - env.cfg.fee_rate) + 1e-9
    assert len(_prot_orders(env.fb)) == 2


def _df_stub(env):
    import pandas as pd
    now_ms = int(time.time() * 1000)
    tf = v29._timeframe_ms(env.cfg.timeframe)
    px = env.fb.last
    return pd.DataFrame([
        {"ts": now_ms - 2 * tf, "open": px, "high": px, "low": px, "close": px,
         "volume": 1.0},
        {"ts": now_ms - tf, "open": px, "high": px, "low": px, "close": px,
         "volume": 1.0}])


def test_ambiguous_sell_is_not_double_counted(live_env):
    """Une vente ambiguë retrouvée à la fois par la reconciliation du solde
    et par la résolution d'intention n'est comptée qu'une fois."""
    env = live_env
    open_live_position(env)
    env.fb.create_faults = ["after"]
    env.ex._lookup_by_cid = lambda cid, attempts=3: None
    assert env.eng.close_position(env.ctx, "BARRIER_TIME", env.fb.last) is False
    assert env.ctx.risk.halt_reason == "AMBIGUOUS_SELL"
    assert env.ctx.pending_order and env.ctx.position.in_position
    del env.ex._lookup_by_cid
    env.eng.sync_protection(env.ctx)          # vente vue via le solde / trades
    assert not env.ctx.position.in_position
    env.eng.resolve_pending(env.ctx)          # même ordre vu par client-id
    assert len(env.ctx.portfolio.last_trades) == 1
    assert env.ctx.pending_order is None
    assert not env.ctx.risk.halted


def test_small_residue_absorbed_as_dust_then_swept(live_env):
    env = live_env
    env.fb.free["TRX"] = 80.0                     # ≈ 8 USDT < 2 × min_notional
    env.eng.check_orphan(env.ctx, env.fb.last, force=True)
    assert not env.ctx.orphan_balance
    assert env.ctx.portfolio.dust_base == pytest.approx(80.0)
    env.eng.sweep_dust(env.ctx, env.fb.last)
    assert env.fb.free["TRX"] < 1.0
    assert env.ctx.portfolio.dust_base < 1.0
