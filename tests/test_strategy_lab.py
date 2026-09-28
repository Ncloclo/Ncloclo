"""Laboratoire de stratégies : tournoi, méta-apprentissage sans
look-ahead, probabilité de gain par durée, intégration au diagnostic."""

import logging

import numpy as np
import pandas as pd
import pytest

from trendguard import diagnostics as dg
from trendguard import strategy_lab as sl
from trendguard import trend_strategy as ts
from test_trendguard import SIM_FROM, synthetic_market


@pytest.fixture(scope="module")
def market():
    return synthetic_market()


@pytest.fixture(scope="module")
def lab(market):
    close, volume = market
    return sl.Lab(close, volume)


def _period(close):
    return str(close.index[SIM_FROM].date()), str(close.index[-1].date())


def test_reference_is_exactly_trendguard(market, lab):
    close, volume = market
    start, end = _period(close)
    eq, trades = lab.run(sl.REF, start, end)
    res = ts.backtest(close, volume, ts.TrendParams(), start, end)
    pd.testing.assert_series_equal(eq, res.equity)
    assert [t["pnl"] for t in trades] == [t["pnl"] for t in res.trades]


@pytest.mark.parametrize("name", sl.NAMES)
def test_every_candidate_risks_about_one_percent(market, lab, name):
    close, _ = market
    eq, trades = lab.run(name, *_period(close))
    assert len(eq) == len(close) - SIM_FROM
    assert eq.iloc[0] == pytest.approx(10_000.0, rel=0.02)
    losses = [t["r"] for t in trades if t["reason"] == "STOP" and t["r"] < 0]
    # Un stop perdant coûte ≈ 1 R (frais, slippage et écart de clôture
    # compris), jamais plus de 3 R. (Un stop remonté peut sortir en gain.)
    assert all(r > -3.0 for r in losses)
    if len(losses) >= 5:
        assert np.median(losses) == pytest.approx(-1.0, abs=0.35)


def test_no_trade_without_regime(market):
    close, volume = market
    lab = sl.Lab(close, volume)
    lab.btc = np.zeros(len(close), dtype=bool)
    for name in ("Rotation momentum", "Retour à la moyenne"):
        eq, trades = lab.run(name, *_period(close))
        assert trades == [] and eq.nunique() == 1


def test_mean_reversion_rules():
    p = ts.TrendParams()
    base = {"close": 100.0, "vol": 2.0, "mom": -0.5, "age": 400, "vol30": 1e9,
            "sma150": 90.0, "rsi2": 5.0}
    lab_entry = sl.eligible(base, p) and base["close"] > base["sma150"] and base["rsi2"] < 10
    assert lab_entry
    assert not sl.eligible(dict(base, vol30=1e5), p)          # illiquide
    assert not sl.eligible(dict(base, age=100), p)            # trop récent


def test_rsi_and_breadth():
    up = pd.Series(np.arange(1.0, 50.0))
    assert sl.rsi(up, 2).iloc[-1] > 99
    idx = pd.date_range("2024-01-01", periods=300, freq="D", tz="UTC")
    close = pd.DataFrame({"a": np.linspace(1, 2, 300), "b": np.linspace(2, 1, 300)},
                         index=idx)
    b = sl.breadth(close)
    assert b.iloc[:149].isna().all()
    assert b.iloc[-1] == pytest.approx(0.5)


def test_meta_weights_use_only_the_past():
    idx = pd.date_range("2018-01-01", "2024-12-31", freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    rets = pd.DataFrame(rng.normal(0.001, 0.02, (len(idx), 3)), index=idx,
                        columns=["a", "b", "c"])
    w1, picks1 = sl.meta_weights(rets, ["a", "b", "c"], "2020-01-01")
    changed = rets.copy()
    cut = pd.Timestamp("2022-07-01", tz="UTC")
    changed.loc[changed.index >= cut, "c"] = 0.05       # « futur » radieux pour c
    w2, picks2 = sl.meta_weights(changed, ["a", "b", "c"], "2020-01-01")
    before = w1.index < cut
    pd.testing.assert_frame_equal(w1[before], w2[before])
    assert [p for p in picks1 if p[0] < "2022-07-01"] == \
        [p for p in picks2 if p[0] < "2022-07-01"]
    assert np.allclose(w1.sum(axis=1).loc["2020-01-01":], 1.0)
    assert (w1.loc[:"2019-12-31"] == 0).all().all()
    soft, _ = sl.meta_weights(rets, ["a", "b", "c"], "2020-01-01", mode="soft")
    assert soft.loc["2020-01-01":].sum(axis=1).round(9).eq(1.0).all()


def test_blend_equal_weights():
    idx = pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC")
    rets = pd.DataFrame({"a": [0.02] * 4, "b": [0.0] * 4}, index=idx)
    assert sl.blend(rets, ["a", "b"]).tolist() == pytest.approx([0.01] * 4)


def test_horizon_success():
    idx = pd.date_range("2019-01-01", periods=1500, freq="D", tz="UTC")
    rising = pd.Series(np.linspace(100, 300, 1500), index=idx)
    hs = sl.horizon_success(rising, months=(1, 12), n_boot=500)
    assert hs["hist_gain_pct"].tolist() == [100.0, 100.0]
    assert hs["boot_gain_pct"].tolist() == [100.0, 100.0]
    flat = pd.Series(100.0, index=idx)
    hs = sl.horizon_success(flat, months=(3,), n_boot=500)
    assert hs.loc[0, "hist_gain_pct"] == 0 and hs.loc[0, "hist_loss_pct"] == 0


def test_curve_stats():
    idx = pd.date_range("2024-01-01", periods=366, freq="D", tz="UTC")
    r = pd.Series(0.0, index=idx)
    r.iloc[10] = -0.1
    st = sl.curve_stats(r)
    assert st["max_dd_pct"] == pytest.approx(-10.0)
    assert st["cagr_pct"] < 0


def test_tournament_recent_ranks_all(lab):
    board = sl.tournament_recent(lab, days=200)
    assert set(board["name"]) == set(sl.NAMES)
    assert board["sharpe"].is_monotonic_decreasing


def test_report_builds(market):
    close, volume = market
    text = sl.lab_report(close, volume, "test", is_period=("2024-09-01", "2025-03-31"),
                         oos_start="2025-04-01", meta_start="2025-03-01",
                         horizon_start="2024-09-01")
    for part in ("### 1. Tournoi", "### 2. Méta-apprentissage", "### 3. Probabilité"):
        assert part in text
    assert "Retour à la moyenne" in text


def test_diagnosis_includes_alternatives_and_horizons(market):
    close, volume = market
    findings = dg.check_alternatives(close, volume, ts.TrendParams(), days=200)
    assert findings[0].section == "Alternatives" and "rang" in findings[0].message
    health, _ = dg.strategy_health(close, volume, ts.TrendParams(), start="2024-01-01")
    assert any("Probabilité historique de finir en gain" in f.message for f in health)


def test_alternatives_warning_when_reference_collapses(market, monkeypatch):
    close, volume = market
    board = pd.DataFrame({"name": sl.NAMES,
                          "sharpe": [-0.5] + [1.0] * (len(sl.NAMES) - 1),
                          "total_pct": 0.0, "trades": 10, "win_rate_pct": 40.0,
                          "cagr_pct": 0.0, "max_dd_pct": -10.0, "calmar": 0.0})
    board = board.sort_values("sharpe", ascending=False).reset_index(drop=True)
    monkeypatch.setattr(sl, "tournament_recent", lambda lab, days=730: board)
    out = dg.check_alternatives(close, volume, ts.TrendParams())
    assert out[-1].level == "ATTENTION" and "trendguard_bot.py lab" in out[-1].reco
    logging.getLogger("x").debug(out)
