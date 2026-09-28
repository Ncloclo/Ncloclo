"""Chemins de secours des ordres RÉELS de TrendGuard (jamais exercés en
paper), contre le simulateur Binance Spot : achat planifié au statut
ambigu, refusé ou trop petit, et remontée du stop catastrophe refusée par
Binance. Invariant vérifié partout : une position n'est jamais laissée sans
protection (soit protégée, soit vendue)."""

import time

import ccxt
import pytest

import v29

PRICE = 0.10


def _planned(env, amount=1000.0, sl=0.09):
    env.fb.set_price(PRICE)
    return env.eng.enter_planned(
        env.ctx, amount, PRICE, sl_abs=sl, tp_abs=PRICE * 100,
        meta={"module": "trendguard", "soft_stop": sl * 1.05, "risk_per_unit": PRICE - sl},
        candle_ts=int(time.time() * 1000))


def _protected(env):
    return [o for o in env.fb.open_protection_orders() if o["type"].startswith("STOP_LOSS")]


def _never_naked(env):
    """Invariant de sécurité : en position ⇒ un stop est posé chez Binance."""
    if env.ctx.position.in_position:
        assert _protected(env), "position sans stop chez Binance"


def test_planned_entry_opens_a_protected_position(live_env):
    """Métadonnées minimales : un champ facultatif absent (risque effectif)
    ne doit jamais faire échouer l'ouverture après l'achat (défaut trouvé
    par l'audit : journal formaté avec None, protection non posée)."""
    env = live_env
    assert _planned(env) == v29.EntryResult.OPENED
    p = env.ctx.position
    assert p.in_position and p.amount_held > 0 and p.sl_price == pytest.approx(0.09)
    assert _protected(env) and env.ctx.pending_order is None
    assert not env.ctx.risk.halted


def test_planned_entry_ambiguous_buy_halts_then_is_adopted(live_env):
    env = live_env
    env.fb.create_faults = ["after", "after", "after"]      # achat passé, réponse perdue
    env.ex._lookup_by_cid = lambda cid, attempts=3: None    # Binance ne le « voit » pas encore
    assert _planned(env) == v29.EntryResult.ORDER_SENT
    assert env.ctx.risk.halted and env.ctx.risk.halt_reason == "AMBIGUOUS_BUY"
    assert env.ctx.pending_order and env.ctx.pending_order["cids"]
    del env.ex._lookup_by_cid
    env.fb.create_faults = []
    env.eng.resolve_pending(env.ctx)                        # cycle suivant
    assert env.ctx.position.in_position and env.ctx.pending_order is None
    assert not env.ctx.risk.halted
    _never_naked(env)


def test_planned_entry_rejected_by_binance_leaves_the_bot_flat(live_env):
    env = live_env
    env.fb.free["USDT"] = 5.0                               # solde insuffisant
    res = _planned(env)
    assert res in (v29.EntryResult.ORDER_SENT, v29.EntryResult.SKIPPED)
    assert not env.ctx.position.in_position and env.ctx.pending_order is None
    assert env.ctx.state == v29.BotState.FLAT.value
    assert not env.ctx.risk.halted


def test_planned_entry_below_binance_minimum_sends_nothing(live_env):
    env = live_env
    before = len(env.fb.calls)
    assert _planned(env, amount=10.0) == v29.EntryResult.SKIPPED   # 1 USDT
    assert not [c for c in env.fb.calls[before:] if c.startswith("create_order")]
    assert not env.ctx.position.in_position


def test_planned_entry_timeout_before_sending_is_forgotten(live_env):
    env = live_env
    env.fb.create_faults = ["before"]                       # jamais transmis
    assert _planned(env) == v29.EntryResult.ORDER_SENT
    env.ctx.pending_order["ts_epoch"] -= env.cfg.pending_order_timeout_sec + 1
    env.eng.resolve_pending(env.ctx)
    assert env.ctx.pending_order is None and not env.ctx.position.in_position


def test_raising_the_stop_refused_keeps_the_old_stop(live_env):
    env = live_env
    assert _planned(env) == v29.EntryResult.OPENED
    old = env.ctx.position.sl_price
    env.fb.set_price(PRICE * 1.3)
    # Le nouveau stop est refusé (OCO puis stop seul) ; l'ancien doit revenir.
    env.fb.create_faults = ["before", "before"]
    moved = env.eng._modify_stop(env.ctx, 0.11, int(time.time() * 1000), "TG_TRAIL")
    assert moved is False
    assert env.ctx.position.in_position and env.ctx.position.sl_price == pytest.approx(old)
    assert [o for o in _protected(env) if o["stop"] == pytest.approx(old)]
    _never_naked(env)


def test_no_stop_possible_triggers_an_emergency_sale(live_env):
    env = live_env
    assert _planned(env) == v29.EntryResult.OPENED
    env.fb.set_price(PRICE * 1.3)
    env.fb.create_faults = ["before"] * 4                   # nouveau ET ancien stop refusés
    assert env.eng._modify_stop(env.ctx, 0.11, int(time.time() * 1000), "TG_TRAIL") is False
    assert not env.ctx.position.in_position                 # vendu plutôt que sans stop
    sells = [o for o in env.fb.orders.values() if o["side"] == "sell" and o["type"] == "MARKET"]
    assert sells


def test_stop_raise_when_the_old_stop_fills_during_cancel(live_env):
    """Le stop s'exécute pendant son annulation : la vente est comptée, et
    aucun nouveau stop n'est posé sur une base qui n'existe plus."""
    env = live_env
    assert _planned(env) == v29.EntryResult.OPENED
    sl = env.ctx.position.sl_price
    state = {"done": False}

    def hook(name):
        if name.startswith("privateDelete") or name.startswith("cancel"):
            if not state["done"]:
                state["done"] = True
                env.fb.set_price(sl * 0.99)
    env.fb.on_call = hook
    env.eng._modify_stop(env.ctx, sl * 1.2, int(time.time() * 1000), "TG_TRAIL")
    env.fb.on_call = None
    assert state["done"]
    _never_naked(env)
    if not env.ctx.position.in_position:
        assert env.ctx.portfolio.last_trades                # vente comptabilisée


@pytest.mark.parametrize("err", [ccxt.InsufficientFunds, ccxt.InvalidOrder])
def test_planned_entry_exchange_errors_never_halt_for_nothing(live_env, err, monkeypatch):
    env = live_env

    def boom(*a, **k):
        raise err("binance refus simulé")
    monkeypatch.setattr(env.ex, "market_buy", boom)
    assert _planned(env) == v29.EntryResult.ORDER_SENT
    assert not env.ctx.position.in_position and env.ctx.pending_order is None
    assert not env.ctx.risk.halted
