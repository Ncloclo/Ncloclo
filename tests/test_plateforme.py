"""Prompt maître appliqué (docs/PLATEFORME.md) : régimes de marché, garde
« NO TRADE », analyse après trade, risque d'un jour (VaR, CVaR)."""

import logging

import numpy as np
import pandas as pd
import pytest
from test_market_watch import _run

from test_trendguard import N_DAYS, SIM_FROM, make_bot, synthetic_market
from trendguard import garde, postmortem, regimes, risque


@pytest.fixture
def logger():
    lg = logging.getLogger("test.plateforme")
    lg.setLevel(logging.WARNING)
    return lg


# ---------- Régimes de marché ----------

def test_regimes_are_read_from_the_past_only():
    close, _ = synthetic_market()
    frame = regimes.regime_frame(close)
    early = regimes.regime_frame(close.iloc[:600])
    pd.testing.assert_frame_equal(frame.iloc[:600], early)          # l'avenir ne change rien au passé
    day = str(close.index[500].date())
    r = regimes.at(frame, day)
    assert set(r) >= {"tendance", "volatilite", "appetit", "phase", "texte"}
    assert r["tendance"] in ("haussière", "baissière", "sans tendance") and r["texte"].startswith("tendance ")
    assert regimes.at(frame, "1990-01-01") == {}


def test_strategy_results_by_regime_and_failures():
    idx = pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC")
    frame = pd.DataFrame({"tendance": ["haussière", "sans tendance", "haussière"],
                          "volatilite": ["normale"] * 3, "appetit": ["risk-on"] * 3,
                          "phase": ["normale"] * 3}, index=idx)
    trades = ([{"entry_date": idx[0].isoformat(), "r": 3.0}] * 6 + [{"entry_date": idx[1].isoformat(), "r": -1.0}] * 12)
    stats = regimes.by_regime(trades, frame)
    rows = {s["regime"]: s for s in stats["tendance"]}
    assert rows["haussière"]["trades"] == 6 and rows["haussière"]["avg_r"] == 3.0
    assert rows["sans tendance"]["win_pct"] == 0.0
    bad = regimes.failures(stats)
    assert bad == ["tendance de btc sans tendance : 12 trades, −1,00 R en moyenne"]


# ---------- Garde « NO TRADE » ----------

def _close(btc_move=0.01, missing=0):
    idx = pd.date_range("2026-10-01", periods=3, freq="D", tz="UTC")
    data = {"btc": [100.0, 100.0, 100.0 * (1 + btc_move)]}
    for k in range(9):
        data[f"c{k}"] = [10.0, 10.0, np.nan if k < missing else 10.0]
    return pd.DataFrame(data, index=idx)


def test_the_gate_says_no_trade_with_reasons():
    ok = garde.checks("2026-10-03", _close(), 10_000.0, 10_100.0, 50.0)
    assert [c["label"] for c in ok] == ["Données du jour", "Mouvement de BTC", "Perte du jour", "Place sur le disque"]
    assert all(c["ok"] for c in ok) and garde.blocking(ok) == []
    bad = garde.checks("2026-10-03", _close(btc_move=-0.18, missing=3), 9_000.0, 10_000.0, 0.4)
    reasons = garde.blocking(bad)
    assert len(reasons) == 4 and "−18,0 % en un jour" in reasons[1] and "−10,0 %" in reasons[2]
    assert garde.blocking(garde.checks("1990-01-01", _close(), None, None, None)) == ["données du jour : aucune clôture du 1990-01-01"]
    assert [c["label"] for c in garde.checks("2026-10-03", _close(), None, None, None)] == ["Données du jour", "Mouvement de BTC"]


def test_the_gate_blocks_the_bot_s_buys_but_never_its_sales(logger, monkeypatch):
    close, volume = synthetic_market()
    ref, fr_ = make_bot("paper", close, logger)
    bot, fb = make_bot("paper", close, logger)
    assert ref.boot() and bot.boot()
    monkeypatch.setattr(type(bot), "_disk_free_gb", lambda self: 0.5 if self is bot else 50.0)
    _run(ref, fr_, close, volume, SIM_FROM, N_DAYS)
    _run(bot, fb, close, volume, SIM_FROM, N_DAYS)
    assert ref.state["trades"] and not bot.state["trades"] and not bot.state["paper"]["holdings"]
    assert bot.state["garde"]["blocked"] and "place sur le disque" in bot.state["garde"]["blocked"][0]
    assert any(line.startswith("Garde « NO TRADE »") for line in bot.state["reasoning"]["lines"])
    assert ref.state["garde"]["blocked"] == []


# ---------- Analyse après trade ----------

def test_excursions_and_lessons():
    idx = pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC")
    close = pd.Series([100, 104, 112, 125, 118, 105, 99, 96, 95, 95], index=idx, dtype=float)
    ex = postmortem.excursions(close, 100.0, 1.0, 5.0, "2026-01-01T00:02:00+00:00", "2026-01-08T00:02:00+00:00")
    assert ex == {"mfe_r": 5.0, "mae_r": -0.2}                       # clôtures du 1er au 7 janvier
    assert postmortem.lesson({"r": 3.0}) == "tendance"
    assert postmortem.lesson({"r": 0.2, "mfe_r": 5.0}) == "gain_rendu"
    assert postmortem.lesson({"r": -1.0, "mfe_r": 0.2}) == "faux_depart"
    assert postmortem.lesson({"r": -1.4, "reason": "EXCHANGE_STOP"}) == "urgence"
    t = postmortem.enrich({"asset": "aave", "entry": 100.0, "exit": 95.0, "r": -1.0, "reason": "STOP",
                           "entry_date": "2026-01-01T00:02:00+00:00", "date": "2026-01-09T00:02:00+00:00"},
                          close, 1.0, 5.0, stop=96.0, regime="tendance haussière")
    assert t["slippage_pct"] == 1.04 and t["regime"] == "tendance haussière" and t["lesson"] == "gain_rendu"
    s = postmortem.summary([t, dict(t, lesson="faux_depart", slippage_pct=None)])
    assert s["trades"] == 2 and "1 gain rendu en partie" in s["text"] and "glissement moyen" in s["text"]
    assert postmortem.summary([])["trades"] == 0


def test_the_bot_s_closed_trades_carry_their_lesson_and_regime(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, N_DAYS)
    trades = bot.state["trades"]
    assert trades and all(t["lesson"] in postmortem.LESSONS for t in trades)
    assert any(t.get("mfe_r") is not None for t in trades) and any(t.get("regime") for t in trades)
    assert any(line.startswith("Régime de marché :") for line in bot.state["reasoning"]["lines"])


# ---------- Risque d'un jour ----------

def test_var_and_cvar_of_the_portfolio():
    idx = pd.date_range("2025-01-01", periods=201, freq="D", tz="UTC")
    rets = np.tile([0.01, -0.02, 0.005, -0.04, 0.0], 40)
    px = 100 * np.cumprod(np.concatenate([[1.0], 1 + rets]))
    close = pd.DataFrame({"btc": px, "eth": px}, index=idx)
    r = risque.var_cvar(close, {"btc": 500.0}, 1000.0)
    assert r["invested_pct"] == 50.0 and r["var_pct"] == pytest.approx(2.0, abs=0.01)
    assert r["cvar_pct"] == pytest.approx(2.0, abs=0.01) and "1 jour sur 20" in risque.describe(r)
    assert risque.var_cvar(close, {}, 1000.0) is None and risque.var_cvar(close.iloc[:30], {"btc": 1.0}, 10.0) is None
    assert risque.describe(None) == ""
