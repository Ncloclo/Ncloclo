"""Apprentissage libre (trendguard/learning.py) : normale des carnets,
ruse réglée sur elle (jamais plus large que le seuil fixe), prévisions
comparées à la clôture puis corrigées, bilan des achats différés ; branché
au bot et au panneau."""

import logging
import os
from datetime import datetime, timezone

import pytest
from test_panel import _cfg

import trendguard_bot as tg
import v29
from panel import assistant as pa
from panel import server as ps
from trendguard import anticipation
from trendguard import learning as lg
from trendguard import trend_strategy as ts


@pytest.fixture
def logger():
    lg_ = logging.getLogger("test.learning")
    lg_.setLevel(logging.WARNING)
    return lg_


def _book(bid, ask, qty):
    return {"bids": [[bid, qty]], "asks": [[ask, qty], [ask * 1.02, qty * 100]]}


def test_book_normal_is_robust_and_only_tightens_the_ruse():
    L = lg.new()
    assert lg.spread_limit(L, "eth", 0.005) == 0.005               # rien appris : seuil fixe
    for k in range(lg.BOOK_MIN_N):
        lg.observe_book(L, "eth", 0.0001 if k != 3 else 0.05, 10_000.0)   # un krach éclair
    assert L["books"]["eth"]["spread"] == pytest.approx(0.0001)    # médiane : le pic ne compte pas
    assert lg.spread_limit(L, "eth", 0.005) == lg.SPREAD_FLOOR       # 0,2 % : plus strict que 0,5 %
    lg.observe_book(L, "eth", 0.05, 10_000.0)                        # pic plafonné à 5 × la normale
    assert L["books"]["eth"]["spread"] < 0.00013
    for _ in range(lg.BOOK_MIN_N):
        lg.observe_book(L, "neo", 0.004, 50_000.0)
    assert lg.spread_limit(L, "neo", 0.005) == 0.005                 # jamais plus large que le seuil fixe
    assert lg.depth_drained(L, "neo", 40_000.0) is None
    assert "carnet d'ordres vidé" in lg.depth_drained(L, "neo", 5_000.0)
    assert lg.book_stats({"bids": [], "asks": [[1, 1]]}) is None
    spread, depth = lg.book_stats(_book(99.95, 100.05, 100))
    assert spread == pytest.approx(0.001) and depth == pytest.approx(10_005)


def test_forecasts_are_compared_to_the_close_then_corrected():
    L = lg.new()
    assert lg.forecast_bucket(13) is None and lg.forecast_bucket(0) is None
    assert [lg.forecast_bucket(h) for h in (11, 5, 2.5, 0.5)] == [12.0, 6.0, 3.0, 1.0]
    assert lg.calibrator(L) is None                                  # aucune expérience
    for day in range(12):
        d = f"2026-10-{day + 1:02d}"
        f = {"sells": [{"asset": "ltc", "prob": 0.35}], "buys": [{"asset": "dot", "prob": 0.05}],
             "regime": {"prob_bear": 0.02, "bull_now": True}}
        lg.record_forecast(L, f, d, 3.0)
        assert lg.has_snapshot(L, d, 3.0) and not lg.has_snapshot(L, d, 1.0)
        # Le modèle annonçait 35 % : LTC a été vendue 11 soirs sur 12.
        assert lg.evaluate(L, d, sold={"ltc"} if day else set(), signals=set(), bear=False) == 3
    cal = lg.calibrate(L, "sell", 0.35)
    assert cal > 0.6                                                 # l'expérience corrige vers le haut
    assert lg.calibrate(L, "sell", 0.95) == 0.95                     # tranche sans expérience : modèle
    s = lg.summary(L)
    assert s["forecasts"] > 30 and s["brier_cal"] < s["brier_raw"]
    assert "comparées à la clôture" in s["text"]
    lg.record_forecast(L, {"sells": [], "buys": [], "regime": None}, "2026-10-20", 1.0)
    assert lg.evaluate(L, "2026-10-22", set(), set(), False) == 0 and L["forecast"] == {}


def test_calibration_changes_probabilities_not_decisions():
    basis = {"day": "2026-10-01", "next_close": "2026-10-03T00:00:00+00:00", "btc_threshold": 50_000.0,
             "assets": {"btc": {"close": 60_000.0, "vol": 1_000.0, "buy_trigger": 70_000.0,
                                "mom_ref": 1.0, "liquid": True},
                        "ltc": {"close": 70.0, "vol": 3.0, "buy_trigger": 90.0, "mom_ref": 1.0,
                                "liquid": True}}}
    holdings = [{"asset": "ltc", "qty": 10.0, "entry": 60.0, "stop": 68.0, "risk": 100.0, "cost": 600.0}]
    now = datetime(2026, 10, 2, 21, tzinfo=timezone.utc)
    p = ts.TrendParams()
    raw = anticipation.forecast(basis, {"btc": 60_000.0, "ltc": 70.0}, holdings, now, p, 10_000.0)
    cal = anticipation.forecast(basis, {"btc": 60_000.0, "ltc": 70.0}, holdings, now, p, 10_000.0,
                                calibrate=lambda kind, q: min(1.0, q * 2))
    assert not raw["calibrated"] and cal["calibrated"]
    assert cal["sells"][0]["prob_model"] == raw["sells"][0]["prob"]
    assert cal["sells"][0]["prob"] == pytest.approx(min(1.0, raw["sells"][0]["prob"] * 2), abs=1e-3)
    assert cal["sells"][0]["stop"] == raw["sells"][0]["stop"]       # la règle, elle, ne bouge pas


def test_deferral_outcomes_are_counted():
    L = lg.new()
    lg.note_deferral(L, "deferred")
    lg.note_deferral(L, "bought", 0.004)
    lg.note_deferral(L, "deferred")
    lg.note_deferral(L, "abandoned")
    s = lg.summary(L)
    assert s["ruse"] == {"deferred": 2, "bought": 1, "abandoned": 1, "cancelled": 0}
    assert s["ruse_gain_pct"] == pytest.approx(0.4) and "+0,40 %" in s["text"]
    assert lg.ensure({"books": "abîmé"})["books"] == {}


class FakeEx:
    def __init__(self, books):
        self.books, self.calls = books, 0

    def fetch_order_book(self, symbol, limit=100):
        self.calls += 1
        return self.books[symbol]


def test_bot_samples_books_hourly_and_defers_on_its_learned_normal(tmp_path, logger):
    g = tg.GuardConfig(run_mode="paper", universe=("BTC",), db_file=":memory:", log_file=os.devnull,
                       lock_file=os.devnull, auto_diagnose_days=0)
    bot = tg.TrendGuardBot(g, logger, None, v29.Store(":memory:", logger), lambda *a, **k: True)
    ex = FakeEx({"ETH/USDT": _book(99.99, 100.01, 1_000)})          # écart 0,02 %
    slot = type("S", (), {"symbol": "ETH/USDT", "base": "ETH",
                          "ex": type("E", (), {"exchange": ex})()})()
    bot.slots = {"ETH": slot}
    bot._sample_books()
    assert ex.calls == 0                                             # cycle isolé : rien
    bot.track_uptime = True
    for _ in range(lg.BOOK_MIN_N):
        bot._last_book_sample = 0.0
        bot._sample_books()
    assert bot.state["learning"]["books"]["eth"]["spread"] == pytest.approx(0.0002)
    bot._sample_books()
    assert ex.calls == lg.BOOK_MIN_N                                 # une fois par heure
    ex.books["ETH/USDT"] = _book(99.8, 100.2, 1_000)                 # écart 0,4 % : sous 0,5 %
    why = bot._book_anomaly(slot, 1_000.0)
    assert "écart achat/vente anormal" in why and "normale de cette crypto" in why
    fresh = tg.TrendGuardBot(g, logger, None, v29.Store(":memory:", logger), lambda *a, **k: True)
    assert fresh._book_anomaly(slot, 1_000.0) is None               # sans apprentissage : seuil fixe


def test_bot_learns_from_each_close(tmp_path, logger):
    g = tg.GuardConfig(run_mode="paper", universe=("BTC",), db_file=":memory:", log_file=os.devnull,
                       lock_file=os.devnull, auto_diagnose_days=0)
    bot = tg.TrendGuardBot(g, logger, None, v29.Store(":memory:", logger), lambda *a, **k: True)
    bot.track_uptime = True
    bot.state["anticipation"] = {"day": "2026-10-01", "next_close": "2026-10-03T00:00:00+00:00"}
    bot.forecast = lambda now, assets=None: {
        "sells": [{"asset": "ltc", "prob": 0.4}], "buys": [{"asset": "dot", "prob": 0.3}],
        "regime": {"prob_bear": 0.1, "bull_now": True}}
    bot._learn_forecast(datetime(2026, 10, 2, 20, tzinfo=timezone.utc))      # 4 h avant : tranche 6 h
    bot._learn_forecast(datetime(2026, 10, 2, 20, 5, tzinfo=timezone.utc))   # déjà relevée
    bot._learn_forecast(datetime(2026, 10, 2, 23, 30, tzinfo=timezone.utc))  # tranche 1 h
    assert sorted(bot.state["learning"]["forecast"]["snaps"]) == ["1.0", "6.0"]
    snap = {"ltc": {"close": 60.0, "prior_high": 70.0, "mom": 1.0},
            "dot": {"close": 5.0, "prior_high": 4.0, "mom": 0.5}}
    bot._learn_close("2026-10-02", snap, True, [("ltc", "STOP")])
    br = bot.state["learning"]["brier"]
    assert br["sell"]["n"] == 2 and br["buy"]["n"] == 2 and br["bear"]["n"] == 2
    assert bot.state["learning"]["forecast"] == {}


def test_panel_and_rachelle_show_what_the_bot_learned(tmp_path):
    app = ps.build_app(_cfg(tmp_path), demo=True)
    s = app.status()
    assert "carnets" in s["learning"]["text"]
    ans = pa.local_answer("Le bot apprend-il de son expérience ?", {"status": s})["answer"]
    assert "Apprentissage libre" in ans and "évolution encadrée" in ans
