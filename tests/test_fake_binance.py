"""Le simulateur doit refuser ce que Binance refuse : sinon les tests du bot
donnent une fausse confiance."""

import ccxt
import pytest

from fake_binance import FakeBinance


def oco_params(**kw):
    p = {"symbol": "TRXUSDT", "side": "SELL", "quantity": "100",
         "aboveType": "LIMIT_MAKER", "abovePrice": "0.11",
         "belowType": "STOP_LOSS", "belowStopPrice": "0.09",
         "listClientOrderId": "L1", "aboveClientOrderId": "T1",
         "belowClientOrderId": "S1"}
    p.update(kw)
    return {k: v for k, v in p.items() if v is not None}


def total(fb, asset):
    return fb.free[asset] + fb.locked[asset]


def test_rejects_invalid_spot_types_and_missing_params():
    fb = FakeBinance(base_balance=1000)
    with pytest.raises(ccxt.InvalidOrder):
        fb.create_order("TRX/USDT", "STOP_LOSS_MARKET", "sell", 100, None,
                        {"stopPrice": 0.09})
    with pytest.raises(ccxt.BadRequest, match="-1102"):
        fb.create_order("TRX/USDT", "STOP_LOSS_LIMIT", "sell", 100, None,
                        {"stopPrice": 0.09})
    with pytest.raises(ccxt.BadRequest, match="-1102"):
        fb.privatePostOrderListOco(oco_params(belowType="STOP_LOSS_LIMIT"))


def test_raw_oco_enforces_lot_size_and_price_filter():
    fb = FakeBinance(base_balance=1000)
    with pytest.raises(ccxt.BadRequest, match="LOT_SIZE"):
        fb.privatePostOrderListOco(oco_params(quantity="100.37"))
    with pytest.raises(ccxt.BadRequest, match="PRICE_FILTER"):
        fb.privatePostOrderListOco(oco_params(abovePrice="0.1100037"))
    assert fb.privatePostOrderListOco(oco_params())["listOrderStatus"] == "EXECUTING"


def test_create_order_rounds_like_ccxt():
    fb = FakeBinance(base_balance=1000)
    o = fb.create_order("TRX/USDT", "STOP_LOSS", "sell", 100.37, None,
                        {"stopPrice": 0.0900049})
    assert o["amount"] == pytest.approx(100.3)
    assert o["stopPrice"] == pytest.approx(0.09)


def test_duplicate_open_client_id_rejected():
    fb = FakeBinance(base_balance=1000)
    o = fb.create_order("TRX/USDT", "STOP_LOSS", "sell", 100, None,
                        {"stopPrice": 0.09, "newClientOrderId": "DUP"})
    with pytest.raises(ccxt.InvalidOrder, match="Duplicate"):
        fb.create_order("TRX/USDT", "STOP_LOSS", "sell", 100, None,
                        {"stopPrice": 0.08, "newClientOrderId": "DUP"})
    fb.cancel_order(o["id"], "TRX/USDT")
    fb.create_order("TRX/USDT", "STOP_LOSS", "sell", 100, None,
                    {"stopPrice": 0.08, "newClientOrderId": "DUP"})


def test_my_trades_since_returns_oldest_first():
    fb = FakeBinance(quote_balance=10_000)
    for i in range(5):
        fb.create_order("TRX/USDT", "market", "buy", 100 + i, None, {})
    assert [t["amount"] for t in fb.fetch_my_trades("TRX/USDT", since=0, limit=2)] \
        == [100.0, 101.0]
    assert [t["amount"] for t in fb.fetch_my_trades("TRX/USDT", limit=2)] \
        == [103.0, 104.0]


def test_bnb_fee_mode_on_both_sides():
    fb = FakeBinance(quote_balance=1000, fee_mode="bnb")
    b = fb.create_order("TRX/USDT", "market", "buy", 1000, None, {})
    s = fb.create_order("TRX/USDT", "market", "sell", 1000, None, {})
    assert b["fees"][0]["currency"] == "BNB" and s["fees"][0]["currency"] == "BNB"
    assert fb.free["TRX"] == pytest.approx(0.0)


def test_oco_partial_fill_expires_sibling_and_conserves_balances():
    fb = FakeBinance(base_balance=100)
    r = fb.privatePostOrderListOco(oco_params())
    tp_id, sl_id = (str(o["orderId"]) for o in r["orders"])
    fb.partial_fill(tp_id, 40)
    tp, sl = fb.orders[tp_id], fb.orders[sl_id]
    assert tp["status"] == "PARTIALLY_FILLED" and sl["status"] == "EXPIRED"
    assert fb.lists[tp["list_id"]]["status"] == "EXECUTING"
    assert fb.locked["TRX"] == pytest.approx(60) and total(fb, "TRX") == pytest.approx(60)
    fb.cancel_order(tp_id, "TRX/USDT")                 # annule toute la liste
    assert fb.locked["TRX"] == pytest.approx(0) and fb.free["TRX"] == pytest.approx(60)
    assert fb.lists[tp["list_id"]]["status"] == "ALL_DONE"


def test_limit_buy_partial_fill_releases_only_used_quote():
    fb = FakeBinance(quote_balance=1000)
    o = fb.create_order("TRX/USDT", "limit", "buy", 1000, 0.09, {})
    assert fb.locked["USDT"] == pytest.approx(90)
    fb.partial_fill(o["id"], 400)
    assert fb.locked["USDT"] == pytest.approx(54)
    fb.cancel_order(o["id"], "TRX/USDT")
    assert fb.locked["USDT"] == pytest.approx(0)
    assert fb.free["USDT"] == pytest.approx(1000 - 400 * 0.09)


@pytest.mark.parametrize("fault,exc,placed", [
    ("before", ccxt.RequestTimeout, False),
    ("ratelimit", ccxt.RateLimitExceeded, False),
    ("after", ccxt.RequestTimeout, True),
    ("internal_after", ccxt.OperationFailed, True),
    ("unavailable_after", ccxt.ExchangeNotAvailable, True),
])
def test_fault_injection(fault, exc, placed):
    fb = FakeBinance(quote_balance=1000)
    fb.create_faults = [fault]
    with pytest.raises(exc):
        fb.create_order("TRX/USDT", "market", "buy", 100, None,
                        {"newClientOrderId": "C1"})
    assert bool(fb.orders) is placed


# ---------- Liquidité, filtres, rappels, identifiants ----------

def test_market_order_walks_thin_book_and_expires_remainder():
    fb = FakeBinance(quote_balance=10_000, book_depth=1000, book_levels=3,
                     level_step=0.01)
    o = fb.create_order("TRX/USDT", "market", "buy", 5000, None, {})
    assert o["filled"] == pytest.approx(3000)            # 3 niveaux × 1000
    assert o["status"] == "expired"
    assert o["average"] == pytest.approx(fb.ask * 1.01)   # niveaux +0 / +1 / +2 %
    assert fb.free["TRX"] == pytest.approx(3000 * 0.999)


def test_resting_orders_fill_in_slices():
    fb = FakeBinance(base_balance=1000, book_depth=300)
    r = fb.privatePostOrderListOco(oco_params(quantity="1000"))
    tp_id = str(r["orders"][0]["orderId"])
    fb.set_price(0.111)
    assert fb.orders[tp_id]["filled"] == pytest.approx(300)
    fb.set_price(0.112)
    assert fb.orders[tp_id]["filled"] == pytest.approx(600)
    assert total(fb, "TRX") == pytest.approx(400)


def test_stop_in_thin_book_slips_below_stop():
    fb = FakeBinance(base_balance=1000, book_depth=200, level_step=0.005)
    o = fb.create_order("TRX/USDT", "STOP_LOSS", "sell", 1000, None, {"stopPrice": 0.09})
    fb.set_price(0.0899)
    done = fb.orders[o["id"]]
    assert done["status"] == "FILLED"
    assert done["cost"] / done["filled"] < 0.0899 * 0.99      # glissement ≈ 1 %


def test_percent_price_and_algo_order_limits():
    fb = FakeBinance(base_balance=10_000)
    with pytest.raises(ccxt.BadRequest, match="PERCENT_PRICE"):
        fb.privatePostOrderListOco(oco_params(abovePrice="0.6"))   # TP à +500 %
    for i in range(5):
        fb.create_order("TRX/USDT", "STOP_LOSS", "sell", 100, None, {"stopPrice": 0.09})
    with pytest.raises(ccxt.BadRequest, match="MAX_NUM_ALGO_ORDERS"):
        fb.create_order("TRX/USDT", "STOP_LOSS", "sell", 100, None, {"stopPrice": 0.09})


def test_on_call_hook_runs_before_each_api_call():
    fb = FakeBinance(base_balance=1000)
    seen = []
    fb.on_call = seen.append
    fb.fetch_ticker("TRX/USDT")
    fb.fetch_balance()
    fb.create_order("TRX/USDT", "STOP_LOSS", "sell", 100, None, {"stopPrice": 0.09})
    assert seen == ["fetch_ticker", "fetch_balance", "create_order"]


def test_multi_order_ids_collide_across_symbols_like_binance():
    from fake_binance import FakeBinanceMulti
    fm = FakeBinanceMulti({"TRX/USDT": 0.1, "ADA/USDT": 0.5})
    a = fm.create_order("TRX/USDT", "market", "buy", 100, None, {})
    b = fm.create_order("ADA/USDT", "market", "buy", 100, None, {})
    assert a["id"] == b["id"]
    assert fm.fetch_order(a["id"], "ADA/USDT")["amount"] == pytest.approx(100)
