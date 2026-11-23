"""Coûts réels d'exécution (research/couts.py) : mesure des carnets, modèle
de coût selon la taille, crochet exec_cost du backtest."""

import math

import pytest

from research import couts
from test_trendguard import SIM_FROM, synthetic_market
from trendguard import trend_strategy as ts

MEASURED = {"btc": {"buy": {"1000": 0.001, "10000": 0.002},
                    "sell": {"1000": 0.0005, "10000": 0.001}, "depth_1pct": 50_000.0}}


def test_the_cost_grows_with_the_order_size():
    cost = couts.cost_model(MEASURED)
    assert cost("btc", 1000, "buy") == pytest.approx(0.001)
    assert cost("btc", 100, "buy") == pytest.approx(0.001)            # sous la plus petite taille
    assert cost("btc", math.sqrt(1000 * 10000), "buy") == pytest.approx(0.0015)
    assert cost("btc", 40_000, "buy") == pytest.approx(0.002 * 2)     # racine carrée au-delà
    assert cost("btc", 1000, "sell") == pytest.approx(0.0005)
    assert cost("eth", 1000, "buy") == pytest.approx(0.001)           # jamais mesurée : fixe
    assert couts.cost_model(MEASURED, stress=3)("btc", 1000, "buy") == pytest.approx(0.003)


def test_a_book_is_measured_against_its_mid_price():
    book = {"bids": [[99.0, 100.0]], "asks": [[101.0, 5.0], [103.0, 100.0]]}
    m = couts.measure_book(book, sizes=(101.0, 1010.0))
    assert m["mid"] == pytest.approx(100.0)
    assert m["spread_pct"] == pytest.approx(0.02)
    assert m["buy"]["101"]["cost"] == pytest.approx(0.01)
    assert m["buy"]["1010"]["cost"] > 0.01                            # plus loin dans le carnet
    assert m["depth_1pct"] == pytest.approx(101.0 * 5.0)              # 103 est à plus de 1 %


def test_a_constant_cost_equal_to_the_fixed_slippage_changes_nothing():
    close, volume = synthetic_market()
    p = ts.TrendParams()
    start, end = str(close.index[SIM_FROM].date()), str(close.index[-1].date())
    base = ts.backtest(close, volume, p, start, end)
    same = ts.backtest(close, volume, p, start, end,
                       hooks=ts.BacktestHooks(exec_cost=lambda a, n, s: p.slippage))
    assert [round(t["pnl"], 9) for t in same.trades] == [round(t["pnl"], 9) for t in base.trades]
    assert same.equity.iloc[-1] == pytest.approx(base.equity.iloc[-1], rel=1e-12)


def test_cheaper_executions_never_raise_the_risk_of_a_trade():
    close, volume = synthetic_market()
    p = ts.TrendParams()
    start, end = str(close.index[SIM_FROM].date()), str(close.index[-1].date())
    dear = ts.backtest(close, volume, p, start, end,
                       hooks=ts.BacktestHooks(exec_cost=lambda a, n, s: 0.01))
    base = ts.backtest(close, volume, p, start, end)
    assert dear.trades and base.trades
    assert dear.equity.iloc[-1] < base.equity.iloc[-1]                # 1 % par ordre coûte cher
    assert all(t["r"] > -2.5 for t in dear.trades)


def test_the_bot_rule_skips_purchases_in_thin_books():
    keep = couts.thin_book_filter(MEASURED)
    plans = [{"asset": "btc", "cost": 10_000.0}, {"asset": "btc", "cost": 20_000.0},
             {"asset": "eth", "cost": 1e9}]
    kept = keep(0, plans, {}, 0.0)
    assert [p["cost"] for p in kept] == [10_000.0, 1e9]               # 50 000 < 3 × 20 000 ; eth inconnue
