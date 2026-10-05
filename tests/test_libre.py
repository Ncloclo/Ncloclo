"""Bot libre : il apprend de ses sources, se fait son avis, agit seul et
révise ses propres règles, dans un portefeuille fictif à part ; le bot
principal n'en est jamais changé."""

import logging

import numpy as np
import pandas as pd
import pytest
from test_market_watch import _run

from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import libre, savoir


def test_sources_gain_weight_when_right_and_lose_it_when_wrong():
    views = {"Juste": {"btc": 0.8, "eth": -0.6}, "Fausse": {"btc": -0.9}, "Tiède": {"btc": 0.1}}
    w = libre.learn({}, views, {"btc": 0.03, "eth": -0.01})
    assert w["Juste"] > 1 > w["Fausse"] and "Tiède" not in w
    for _ in range(200):
        w = libre.learn(w, views, {"btc": 0.03, "eth": -0.01})
    assert w["Juste"] == libre.WEIGHT_MAX and w["Fausse"] == libre.WEIGHT_MIN


def test_own_opinion_weighs_every_source_by_what_it_learned():
    op = libre.opinion({"A": 3.0, "B": 1.0}, {"A": {"btc": 1.0}, "B": {"btc": -1.0, "eth": 0.5}, "C": {"xrp": 0.05}})
    assert op["btc"] == 0.5 and op["eth"] == 0.5 and "xrp" not in op


def test_a_day_of_decisions_sells_then_buys():
    book = libre.new(1000.0, "2026-10-01")
    acts = libre.trade_day(book, "2026-10-01", {"btc": 100.0, "eth": 10.0, "xrp": 1.0},
                           {"btc": 0.9, "eth": 0.4, "xrp": 0.1}, libre.DEFAULT_RULES)
    assert acts == ["achète BTC (avis +0,90)", "achète ETH (avis +0,40)"]       # XRP sous le seuil
    assert book["cash"] == pytest.approx(600.0) and set(book["holdings"]) == {"btc", "eth"}
    acts = libre.trade_day(book, "2026-10-02", {"btc": 90.0, "eth": 10.5}, {"btc": 0.9, "eth": -0.5},
                           libre.DEFAULT_RULES)
    assert acts == ["vend BTC (stop)", "vend ETH (avis baissier)"]
    assert book["holdings"] == {} and [t["reason"] for t in book["trades"]] == ["stop", "avis baissier"]
    assert book["trades"][0]["pnl"] < 0 < book["trades"][1]["pnl"]


def _market(days=60):
    idx = pd.date_range("2026-01-01", periods=days, freq="D", tz="UTC")
    up = 100 * np.exp(np.cumsum(np.full(days, 0.01)))
    down = 100 * np.exp(np.cumsum(np.full(days, -0.01)))
    return pd.DataFrame({"hausse": up, "baisse": down}, index=idx)


def test_it_improves_its_own_rules_on_its_past_opinions():
    close = _market()
    # Ses avis : la crypto qui baisse est vue à +0,25, celle qui monte à +0,6.
    ops = {str(d.date()): {"hausse": 0.6, "baisse": 0.25} for d in close.index}
    rules = libre.tune(ops, close, {"buy": 0.2, "sell": -0.2, "stop": 0.12})
    assert rules["buy"] > 0.25                                  # il cesse d'acheter celle qui baisse
    assert libre.replay(ops, close, rules) > libre.replay(ops, close, {"buy": 0.2, "sell": -0.2, "stop": 0.12})


def test_step_learns_acts_records_and_revises_weekly():
    close = _market(40)
    book = libre.new(1000.0, str(close.index[0].date()))
    views = {"Fiable": {"hausse": 0.7, "baisse": -0.7}, "Menteuse": {"hausse": -0.7, "baisse": 0.7}}
    for d in close.index:
        libre.step(book, str(d.date()), close, views, views)
    assert book["weights"]["Fiable"] > book["weights"]["Menteuse"]
    assert "hausse" in book["holdings"] and "baisse" not in book["holdings"]
    assert len(book["equity"]) == len(close) and book["equity"][-1][1] > 1000.0
    assert book["tuned"] is not None
    n = len(book["equity"])
    assert libre.step(book, str(close.index[-1].date()), close, views, views) == [] and len(book["equity"]) == n
    s = libre.summary(book, main_equity=101.0, main_start=100.0)
    assert s["pct"] > 0 and s["main_pct"] == 1.0 and "Bot libre (argent fictif)" in s["text"]
    assert libre.summary(None) == {}


@pytest.fixture
def logger():
    lg = logging.getLogger("test.libre")
    lg.setLevel(logging.WARNING)
    return lg


def test_the_free_bot_runs_beside_the_main_bot_without_changing_it(logger, tmp_path):
    close, volume = synthetic_market()
    db = str(tmp_path / "savoir.db")
    m = savoir.Memory(db)
    m.put_views([(str(d.date()), "Presse", a, 0.5 if i % 3 else -0.5)
                 for i, d in enumerate(close.index[SIM_FROM - 2:SIM_FROM + 60]) for a in close.columns])
    m.close()
    ref, fr_ = make_bot("paper", close, logger)
    bot, fb = make_bot("paper", close, logger, savoir=True, savoir_db=db, libre=True)
    assert ref.boot() and bot.boot()
    _run(ref, fr_, close, volume, SIM_FROM, SIM_FROM + 60)
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 60)
    trades = lambda b: [(t["asset"], t["date"]) for t in b.state["trades"]]  # noqa: E731
    assert trades(ref) == trades(bot) and sorted(ref.state["paper"]["holdings"]) == sorted(bot.state["paper"]["holdings"])
    m = savoir.Memory(db)
    book = m.get("libre")
    m.close()
    assert book["capital"] == 10_000.0 and len(book["equity"]) == 60 and book["trades"]
    assert any(line.startswith("Bot libre (argent fictif)") for line in bot.state["reasoning"]["lines"])
