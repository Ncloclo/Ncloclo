"""Exécution paper au plus près du réel (trendguard/realisme.py) : carnet
d'ordres traversé niveau par niveau, frais de Binance, règles de la paire,
stops surveillés sur les plus bas de chaque minute."""

import logging
from datetime import timedelta

import pytest

from test_trendguard import DAY, SIM_FROM, feed, make_bot, synthetic_market
from trendguard import realisme


@pytest.fixture
def logger():
    lg = logging.getLogger("test.realisme")
    lg.setLevel(logging.WARNING)
    return lg


BOOK = {"bids": [[99.0, 1.0], [98.0, 2.0], [97.0, 5.0]],
        "asks": [[101.0, 1.0], [102.0, 2.0], [103.0, 5.0]]}


# ---------- Carnet d'ordres ----------

def test_a_buy_walks_the_sellers_level_by_level():
    f = realisme.fill(BOOK, "buy", 2.0)
    assert f.price == pytest.approx((101.0 + 102.0) / 2)
    assert f.best == 101.0 and f.mid == 100.0 and f.complete
    assert f.cost_pct == pytest.approx(f.price / 100.0 - 1)


def test_a_sell_walks_the_buyers_level_by_level():
    f = realisme.fill(BOOK, "sell", 3.0)
    assert f.price == pytest.approx((99.0 + 2 * 98.0) / 3)
    assert f.best == 99.0 and f.cost_pct > 0


def test_a_book_too_short_is_extended_prudently():
    f = realisme.fill(BOOK, "buy", 10.0)            # 8 disponibles
    assert not f.complete
    seen = (101.0 + 2 * 102.0 + 5 * 103.0) / 8
    assert f.price > seen                            # le reste coûte plus cher que le carnet lu


def test_an_empty_or_crossed_book_gives_no_fill():
    assert realisme.fill({"bids": [], "asks": [[1, 1]]}, "buy", 1) is None
    assert realisme.fill({"bids": [[2, 1]], "asks": [[1, 1]]}, "buy", 1) is None


def test_binance_fees_with_and_without_bnb():
    assert realisme.fee_rate(0.001, False) == pytest.approx(0.001)
    assert realisme.fee_rate(0.001, True) == pytest.approx(0.00075)


def test_a_triggered_stop_sells_from_its_trigger_price():
    f = realisme.fill(BOOK, "sell", 3.0)
    assert realisme.sell_price(None, f) == pytest.approx(f.price)
    assert realisme.sell_price(90.0, f) == pytest.approx(90.0 * f.price / 99.0)


# ---------- Stops à la minute ----------

def test_a_wick_inside_a_minute_triggers_the_stop():
    bars = [[0, 100, 101, 99, 100, 1], [60_000, 100, 100, 94, 99, 1]]
    assert realisme.stop_touch(bars, 95.0, 0) == (60_000, 95.0)


def test_a_minute_opening_below_the_stop_sells_at_its_open():
    bars = [[0, 100, 101, 99, 100, 1], [60_000, 92, 93, 91, 92, 1]]
    assert realisme.stop_touch(bars, 95.0, 0) == (60_000, 92.0)


def test_minutes_before_the_watch_are_ignored():
    bars = [[0, 100, 100, 90, 100, 1], [60_000, 100, 101, 99, 100, 1]]
    assert realisme.stop_touch(bars, 95.0, 60_000) is None


# ---------- Bot paper complet ----------

def _first_position(bot, fb, close, volume):
    feed(fb, close, volume)
    for d in close.index[SIM_FROM:]:
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        now = d.to_pydatetime() + DAY + timedelta(minutes=5)
        bot.run_cycle(now=now)
        held = bot.state["paper"]["holdings"]
        if held:
            return next(iter(held)), now, d
    raise AssertionError("aucune position ouverte")


def test_paper_buys_at_the_sellers_price_with_binance_fees(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, paper_reel=True)
    assert bot.boot()
    a, _now, _d = _first_position(bot, fb, close, volume)
    h = bot.state["paper"]["holdings"][a]
    sym = f"{a.upper()}/USDT"
    ask = fb.fakes[sym].ask
    assert h["execution"]["modele"] == "carnet"
    assert h["entry"] == pytest.approx(ask)            # carnet profond : tout au meilleur prix
    assert h["cost"] == pytest.approx(h["qty"] * ask * 1.001)
    assert h["qty"] == pytest.approx(round(h["qty"] / 0.00001) * 0.00001)   # pas de la paire
    assert h["watch_ms"] > 0


def test_a_thin_book_costs_more_and_never_raises_the_risk(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, paper_reel=True)
    assert bot.boot()
    planned = {}
    orig = bot._paper_fill_entry

    def spy(s, plan, cash):
        planned[plan["asset"]] = plan["risk_quote"]
        last = fb.fakes[s.symbol].last
        # Carnet mince : 1 % plus cher par niveau, 0,5 unité par niveau.
        lv = [[last * (1 + 0.01 * (k + 1)), 0.5 * 2000 / last] for k in range(50)]
        fb.fakes[s.symbol].fetch_order_book = lambda symbol, limit=20: {
            "bids": [[last * 0.999, 1e9]], "asks": lv}
        return orig(s, plan, cash)

    bot._paper_fill_entry = spy
    a, _now, _d = _first_position(bot, fb, close, volume)
    h = bot.state["paper"]["holdings"][a]
    best = h["execution"]["meilleur_prix"]
    assert h["entry"] > best                           # plusieurs niveaux traversés
    assert h["risk_quote"] <= planned[a] * (1 + 1e-9)  # jamais plus de risque que prévu


def test_a_wick_between_two_checks_triggers_the_paper_stop(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, paper_reel=True)
    assert bot.boot()
    a, now, _d = _first_position(bot, fb, close, volume)
    h = bot.state["paper"]["holdings"][a]
    sym = f"{a.upper()}/USDT"
    stop = h["disaster"]
    last = fb.fakes[sym].last
    t0 = h["watch_ms"]
    # Une mèche sous le stop pendant la minute, revenue au-dessus ensuite :
    # le dernier prix ne la voit pas, le plus bas de la minute oui.
    fb.ohlcv[(sym, "1m")] = [[t0, last, last, stop * 0.98, last, 1.0],
                             [t0 + 60_000, last, last, last, last, 1.0]]
    bot._now = now + timedelta(minutes=2)
    bot._maintain_paper()
    assert a not in bot.state["paper"]["holdings"]
    t = bot.state["trades"][-1]
    assert t["reason"] == "EXCHANGE_STOP"
    bid = fb.fakes[sym].bid
    assert t["exit"] == pytest.approx(stop * bid / bid)  # carnet profond : au prix de déclenchement
    assert t["execution"]["modele"] == "carnet"


def test_without_the_real_mode_the_wick_goes_unseen(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)          # ancien modèle
    assert bot.boot()
    a, now, _d = _first_position(bot, fb, close, volume)
    sym = f"{a.upper()}/USDT"
    h = bot.state["paper"]["holdings"][a]
    last = fb.fakes[sym].last
    fb.ohlcv[(sym, "1m")] = [[0, last, last, h["disaster"] * 0.98, last, 1.0]]
    bot._now = now + timedelta(minutes=2)
    bot._maintain_paper()
    assert a in bot.state["paper"]["holdings"]           # dernier prix au-dessus : rien vu


def test_a_daily_exit_sells_at_the_buyers_price(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, paper_reel=True)
    assert bot.boot()
    a, _now, _d = _first_position(bot, fb, close, volume)
    sym = f"{a.upper()}/USDT"
    h = bot.state["paper"]["holdings"][a]
    qty, cash0 = h["qty"], bot.state["paper"]["cash"]
    bot._execute_exit(a, "STOP", None)
    t = bot.state["trades"][-1]
    bid = fb.fakes[sym].bid
    assert t["exit"] == pytest.approx(bid)
    assert bot.state["paper"]["cash"] == pytest.approx(cash0 + qty * bid * (1 - 0.001))


def test_a_purchase_under_the_binance_minimum_is_cancelled(logger, caplog):
    caplog.set_level(logging.WARNING, logger="test.realisme")
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, paper_reel=True)
    assert bot.boot()
    for f in fb.fakes.values():
        f.min_notional_value = 1e9                       # minimum de la paire inatteignable
    for s in bot.slots.values():
        s.ex.rules.min_cost = 1e9
    feed(fb, close, volume)
    for d in close.index[SIM_FROM:SIM_FROM + 200]:
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert bot.state["paper"]["holdings"] == {}
    assert "annulée : montant sous le minimum de Binance" in caplog.text
