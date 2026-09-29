"""Étude de robustesse (research/robustness.py) : Monte-Carlo par blocs de
jours sur la vraie courbe du capital, séries de trades perdants."""

import numpy as np
import pandas as pd

from research import robustness as rb
from test_trendguard import SIM_FROM, synthetic_market
from trendguard import trend_strategy as ts


def test_block_monte_carlo_keeps_real_drawdowns():
    close, volume = synthetic_market()
    p = ts.TrendParams()
    start, end = str(close.index[SIM_FROM].date()), str(close.index[-1].date())
    res = ts.backtest(close, volume, p, start, end)
    mc = rb.monte_carlo(res.equity, res.trades, days=365, sims=500)
    assert 0 <= mc["prob_loss"] <= 100 and mc["dd_p95"] >= mc["dd_p50"] >= 0
    assert mc["p_dd20"] >= mc["p_dd30"] >= mc["p_dd40"]
    assert 0 <= mc["streak_p50"] <= mc["streak_p95"] <= 100 and mc["pool"] == len(res.trades)


def test_block_monte_carlo_on_a_known_curve():
    # Courbe qui perd 1 % par jour : chaque tirage perd, baisse proche de 97 %.
    eq = pd.Series(np.cumprod(np.full(400, 0.99)), index=pd.date_range("2024-01-01", periods=400))
    mc = rb.monte_carlo(eq, [{"r": -1.0}] * 30, days=365, sims=200)
    assert mc["prob_loss"] == 100 and mc["dd_p50"] > 95 and mc["streak_p50"] == 100
    assert rb.fr(-1.5, ".1f") == "−1,5"
