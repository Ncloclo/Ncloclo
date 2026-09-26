"""Scénarios live de bout en bout contre le simulateur Binance Spot.
Chaque test verrouille la correction d'un défaut du diagnostic V29.5."""

import logging
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


# ---------- Exécutions partielles (Binance : l'autre jambe de l'OCO expire) ----------

def _leg(fb, otype):
    return next(o for o in fb.orders.values()
                if o["type"] == otype and o["status"] in ("NEW", "PARTIALLY_FILLED"))


def _assert_fully_protected(env):
    p = env.ctx.position
    legs = _prot_orders(env.fb)
    assert any(o["type"] == "STOP_LOSS" for o in legs)
    for o in legs:
        assert abs((o["amount"] - o["filled"]) - p.amount_held) < 1e-9
    assert env.fb.free["TRX"] + env.fb.locked["TRX"] == pytest.approx(p.amount_held, abs=0.2)
    assert not env.ctx.risk.halted


@pytest.mark.parametrize("leg,frac", [("LIMIT_MAKER", 0.4), ("STOP_LOSS", 0.5)])
def test_oco_partial_fill_is_booked_and_remainder_reprotected(live_env, leg, frac):
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    initial = p.amount_held
    px = p.tp_price if leg == "LIMIT_MAKER" else p.sl_price * 0.999
    env.fb.partial_fill(_leg(env.fb, leg)["id"], round(initial * frac, 1), price=px)
    # Juste après : l'autre jambe a expiré → le reliquat n'a plus de stop.
    assert not any(o["type"] == "STOP_LOSS" for o in _prot_orders(env.fb)) \
        or leg == "STOP_LOSS"
    assert env.eng.sync_protection(env.ctx) == "ACTIVE"
    p = env.ctx.position
    assert p.in_position
    assert p.amount_held == pytest.approx(initial - round(initial * frac, 1))
    assert (p.realized_pnl > 0) is (leg == "LIMIT_MAKER")
    _assert_fully_protected(env)


def test_stop_only_partial_fill_reprotected(logger):
    env = build_env("live", logger=logger, use_oco=False, stop_only_protection=True)
    open_live_position(env)
    p = env.ctx.position
    initial = p.amount_held
    env.fb.partial_fill(_leg(env.fb, "STOP_LOSS")["id"], round(initial * 0.3, 1),
                        price=p.sl_price * 0.999)
    assert env.eng.sync_protection(env.ctx) == "ACTIVE"
    assert env.ctx.position.amount_held == pytest.approx(initial - round(initial * 0.3, 1))
    _assert_fully_protected(env)


def test_smart_buy_partial_fill_opens_on_filled_quantity(logger):
    env = build_env("live", logger=logger, use_smart_buy=True)

    def half_fill(_seconds):
        for o in list(env.fb.orders.values()):
            if o["side"] == "buy" and o["status"] == "NEW":
                env.fb.partial_fill(o["id"], round(o["amount"] * 0.5, 1))
    env.ex.sleep = half_fill
    assert open_live_position(env) == v29.EntryResult.OPENED
    assert env.fb.locked["USDT"] == pytest.approx(0)       # reliquat annulé
    _assert_fully_protected(env)


# ---------- Statuts inconnus Binance (-1001, 503) ----------

def test_internal_error_after_buy_is_resolved_immediately(live_env):
    """-1001 : ccxt lève OperationFailed (hors NetworkError). L'ordre a pu
    passer : le bot doit le rechercher, pas laisser la base sans stop."""
    env = live_env
    env.fb.create_faults = ["internal_after"]
    assert open_live_position(env) == v29.EntryResult.OPENED
    assert env.ctx.pending_order is None
    _assert_fully_protected(env)


def test_unavailable_after_oco_is_found_by_list_client_id(live_env):
    env = live_env
    env.fb.create_faults = ["none", "unavailable_after"]    # achat OK, OCO 503
    assert open_live_position(env) == v29.EntryResult.OPENED
    assert len(env.fb.lists) == 1                           # pas de doublon
    assert env.ctx.position.oco_order_id == next(iter(env.fb.lists))
    _assert_fully_protected(env)


def test_ambiguous_oco_adopts_existing_orders(live_env, monkeypatch):
    env = live_env
    env.fb.create_faults = ["none", "after"]
    monkeypatch.setattr(env.ex, "_lookup_list_by_cid", lambda cid, attempts=3: None)
    assert open_live_position(env) == v29.EntryResult.OPENED
    assert len(env.fb.lists) == 1 and len(_prot_orders(env.fb)) == 2
    p = env.ctx.position
    assert p.oco_order_id and p.oco_sl_order_id and not p.standalone_stop_order_id
    _assert_fully_protected(env)


def test_rate_limited_buy_is_not_counted(live_env):
    env = live_env
    env.fb.create_faults = ["ratelimit"]
    env.ex._lookup_by_cid = lambda cid, attempts=3: None
    assert open_live_position(env) == v29.EntryResult.ORDER_SENT
    env.ctx.pending_order["ts_epoch"] -= env.cfg.pending_order_timeout_sec + 1
    del env.ex._lookup_by_cid
    env.eng.resolve_pending(env.ctx)
    assert not env.ctx.position.in_position and not env.ctx.risk.halted
    assert not env.fb.orders


# ---------- Panne de lecture : ne jamais annuler une protection illisible ----------

def test_read_outage_never_cancels_protection(live_env, caplog):
    """Si seules les lectures d'ordres échouent, les stops existants doivent
    rester en place (V29.6 initiale : purge au 6e échec → position nue)."""
    env = live_env
    open_live_position(env)
    env.fb.fail_fetch_order_n = 10_000
    with caplog.at_level(logging.CRITICAL):
        for _ in range(20):
            assert env.eng.sync_protection(env.ctx) == "UNCERTAIN"
            assert len(_prot_orders(env.fb)) == 2
    alerts = [r for r in caplog.records if "protection illisible depuis" in r.getMessage()]
    assert len(alerts) == 1
    env.fb.fail_fetch_order_n = 0
    assert env.eng.sync_protection(env.ctx) == "ACTIVE"
    assert env.ctx.position.oco_misses == 0
    _assert_fully_protected(env)


def test_stop_move_during_read_outage_keeps_old_stop(live_env):
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    old_sl = p.sl_price
    env.fb.fail_fetch_order_n = 10_000
    assert env.eng._modify_stop(env.ctx, p.buy_price, int(time.time() * 1000), "BE") is False
    assert env.ctx.position.sl_price == old_sl
    assert len(_prot_orders(env.fb)) == 2
    env.fb.fail_fetch_order_n = 0
    _assert_fully_protected(env)


# ---------- Courses : exécution ENTRE deux appels API du bot ----------

def _once(fb, method, action):
    state = {"done": False}

    def hook(name):
        if name == method and not state["done"]:
            state["done"] = True
            action()
    fb.on_call = hook
    return state


def test_stop_fills_between_snapshot_and_cancel_no_double_sell(live_env):
    """Time-exit : le SL s'exécute juste avant l'annulation. Le bot doit
    comptabiliser le SL, sans vendre au marché une base qui n'existe plus."""
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    st = _once(env.fb, "privateDeleteOrderList",
               lambda: env.fb.set_price(p.sl_price * 0.998))
    env.eng.close_position(env.ctx, "BARRIER_TIME", env.fb.last)
    assert st["done"] and not env.ctx.position.in_position
    assert env.ctx.portfolio.last_trades[-1]["reason"] == "BARRIER_SL"
    assert not [o for o in env.fb.orders.values()
                if o["side"] == "sell" and o["type"] == "MARKET"]
    assert not env.ctx.risk.halted


def test_tp_fills_during_stop_move_no_new_protection(live_env):
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    _once(env.fb, "privateDeleteOrderList",
          lambda: env.fb.set_price(p.tp_price * 1.001))
    assert env.eng._modify_stop(env.ctx, p.buy_price * 1.004,
                                int(time.time() * 1000), "BE") is False
    assert not env.ctx.position.in_position
    assert env.ctx.portfolio.last_trades[-1]["reason"] == "BARRIER_TP"
    assert _prot_orders(env.fb) == []


def test_price_crosses_stop_while_protection_is_placed(live_env):
    """Le prix passe sous le stop entre la lecture du prix et la pose de
    l'OCO : Binance refuse (le stop déclencherait tout de suite). Le bot ne
    doit pas rester sans stop : il liquide."""
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    env.eng.cancel_protection(env.ctx)
    _once(env.fb, "privatePostOrderListOco",
          lambda: env.fb.set_price(p.sl_price * 0.995))
    assert env.eng.maintain_protection(env.ctx, env.fb.last) is False
    assert not env.ctx.position.in_position
    assert env.fb.free["TRX"] * env.fb.last < env.fb.min_notional_value


# ---------- Carnet peu profond ----------

def test_thin_book_entry_uses_real_average_price(logger):
    fb = FakeBinance(book_depth=1000, book_levels=5, level_step=0.002)
    env = build_env("live", fb=fb, logger=logger)
    assert open_live_position(env) == v29.EntryResult.OPENED
    p = env.ctx.position
    buy = next(o for o in fb.orders.values() if o["side"] == "buy")
    assert buy["filled"] > 1000                          # plusieurs niveaux
    assert p.buy_price == pytest.approx(buy["cost"] / buy["filled"])
    assert p.buy_price > fb.ask                          # slippage réel
    _assert_fully_protected(env)


def test_thin_book_entry_partially_expired_is_protected(logger):
    fb = FakeBinance(book_depth=500, book_levels=2)
    env = build_env("live", fb=fb, logger=logger)
    assert open_live_position(env) == v29.EntryResult.OPENED
    buy = next(o for o in fb.orders.values() if o["side"] == "buy")
    assert buy["status"] == "EXPIRED" and buy["filled"] == pytest.approx(1000)
    _assert_fully_protected(env)


def test_thin_book_tp_fills_over_several_cycles(logger):
    fb = FakeBinance(book_depth=800)
    env = build_env("live", fb=fb, logger=logger)
    open_live_position(env)
    p = env.ctx.position
    for k in range(6):
        fb.set_price(p.tp_price * (1.0005 + k * 0.0001))
        env.eng.sync_protection(env.ctx)
        if not env.ctx.position.in_position:
            break
        _assert_fully_protected(env)
    assert not env.ctx.position.in_position
    t = env.ctx.portfolio.last_trades[-1]
    assert t["pnl"] > 0 and t["legs"] >= 2
    assert fb.free["TRX"] + fb.locked["TRX"] < 1.0


def test_many_stop_moves_never_exceed_algo_order_limit(live_env):
    env = live_env
    open_live_position(env)
    p = env.ctx.position
    for k in range(1, 12):
        env.fb.set_price(p.buy_price * (1 + 0.002 * k))
        env.eng._modify_stop(env.ctx, p.sl_price * 1.001,
                             int(time.time() * 1000), "TRAIL")
        algo = [o for o in env.fb.orders.values()
                if o["status"] in ("NEW", "PARTIALLY_FILLED") and o["type"] == "STOP_LOSS"]
        assert len(algo) == 1
    _assert_fully_protected(env)
