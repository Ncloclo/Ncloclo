"""Anticipation : niveaux exacts de la prochaine décision (mêmes règles que
la stratégie), probabilités d'ici la clôture, risque, conseils et alertes
envoyées une seule fois par soir."""

import dataclasses
import logging
from datetime import timedelta

import pandas as pd
import pytest

from test_trendguard import DAY, SIM_FROM, make_bot, run_days, synthetic_market
from trendguard import anticipation as an
from trendguard import trend_strategy as ts

P = ts.TrendParams()


@pytest.fixture
def logger():
    lg = logging.getLogger("test.anticipation")
    lg.setLevel(logging.INFO)
    return lg


def test_basis_levels_are_exactly_those_of_the_next_decision():
    """Le niveau d'achat annoncé la veille est celui que la stratégie
    utilisera : une clôture juste au-dessus déclenche le signal, juste en
    dessous non. Idem pour le seuil du régime BTC."""
    close, volume = synthetic_market()
    i = SIM_FROM + 50
    day = str(close.index[i].date())
    feats = {a: ts.asset_features(close[a], P, volume[a]) for a in close.columns}
    basis = an.basis_from_market(close.iloc[:i + 1], {a: f.iloc[:i + 1] for a, f in feats.items()}, day, P)
    for a, b in basis["assets"].items():
        for bump, expected in ((1.0001, True), (0.9999, False)):
            nxt = close.iloc[:i + 2].copy()
            level = max(b["buy_trigger"], b["mom_ref"] or 0)
            nxt.iloc[i + 1, nxt.columns.get_loc(a)] = level * bump
            f = ts.asset_features(nxt[a], P, volume[a].iloc[:i + 2]).iloc[-1]
            breakout = bool(f["close"] > f["prior_high"] and f["mom"] > 0)
            assert breakout is expected, (a, bump)
    btc = close["btc"].iloc[:i + 2].copy()
    for bump, bull in ((1.0001, True), (0.9999, False)):
        btc.iloc[-1] = basis["btc_threshold"] * bump
        assert bool(ts.btc_regime(btc, P).iloc[-1]) is bull


def _basis(hours_left=6.0):
    from datetime import datetime, timezone
    now = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)
    close_at = now + timedelta(hours=hours_left)
    day = str((close_at - timedelta(days=2)).date())
    return now, {"day": day, "next_close": close_at.isoformat(), "btc_threshold": 70_000.0,
                 "assets": {"btc": {"close": 84_000, "buy_trigger": 86_000, "mom_ref": 60_000, "vol": 1500, "liquid": True},
                            "eth": {"close": 2600, "buy_trigger": 2650, "mom_ref": 2000, "vol": 60, "liquid": True},
                            "dot": {"close": 4.0, "buy_trigger": 4.02, "mom_ref": 3.0, "vol": 0.12, "liquid": True},
                            "ada": {"close": 0.25, "buy_trigger": 0.30, "mom_ref": 0.2, "vol": 0.008, "liquid": True},
                            "neo": {"close": 9.0, "buy_trigger": 9.05, "mom_ref": 8.0, "vol": 0.3, "liquid": False}}}


HOLD = [{"asset": "ada", "qty": 4000.0, "entry": 0.26, "stop": 0.245, "disaster": 0.23, "risk": 100.0, "cost": 1041.0}]


def test_forecast_sells_buys_regime_risk_and_advice():
    now, basis = _basis()
    prices = {"btc": 84_000.0, "eth": 2640.0, "dot": 4.05, "ada": 0.2465, "neo": 9.04}
    f = an.forecast(basis, prices, HOLD, now, P, equity=10_000.0, allowed=["btc", "eth", "dot", "ada", "neo"])
    s = f["sells"][0]
    assert s["asset"] == "ada" and 0.2 < s["prob"] < 0.8          # à 0,6 % de son stop
    assert s["locked_pct"] == pytest.approx(-5.77, abs=0.01)
    buys = {b["asset"]: b for b in f["buys"]}
    assert buys["dot"]["prob"] > 0.5 and not buys["dot"]["blocked"]   # déjà au-dessus
    assert "trop peu échangée" in buys["neo"]["blocked"]
    assert f["regime"]["bull_now"] and f["regime"]["prob_bear"] < 0.01
    assert f["risk"]["slots"] == 5 and f["risk"]["open_risk_pct"] == pytest.approx(1.0)
    assert any("DOT" in t for t in f["advice"])


def test_full_risk_budget_blocks_every_buy():
    now, basis = _basis()
    six = [dict(HOLD[0], asset=f"x{k}") for k in range(6)]
    f = an.forecast(basis, {"dot": 4.05, "btc": 84_000.0}, six, now, P, equity=10_000.0)
    assert f["risk"]["slots"] == 0
    assert all("plafond de risque atteint" in b["blocked"] for b in f["buys"])
    assert any("Plafond de risque atteint" in t for t in f["advice"])


def test_bear_market_and_probability_grow_as_the_close_nears():
    now, basis = _basis(hours_left=12)
    far = an.forecast(basis, {"ada": 0.2465}, HOLD, now, P, equity=10_000.0)["sells"][0]["prob"]
    now2, basis2 = _basis(hours_left=0.5)
    near = an.forecast(basis2, {"ada": 0.2465}, HOLD, now2, P, equity=10_000.0)["sells"][0]["prob"]
    assert near < far                                         # au-dessus du stop : moins de temps, moins de risque
    bear = an.forecast(basis, {"btc": 65_000.0, "dot": 4.05}, [], now, P, equity=10_000.0)
    assert not bear["regime"]["bull_now"]
    assert all("marché baissier" in b["blocked"] for b in bear["buys"])


def test_alerts_are_sent_once_per_evening():
    now, basis = _basis(hours_left=1)
    prices = {"ada": 0.2440, "dot": 4.10, "btc": 84_000.0}      # ADA sous son stop, DOT au-dessus
    f = an.forecast(basis, prices, HOLD, now, P, equity=10_000.0)
    first = an.alerts_to_send(f, [])
    assert {a["key"] for a in first} == {"vente:ada", "achat:dot"}
    assert "Vente probable ce soir : ADA" in first[0]["text"]
    assert an.alerts_to_send(f, [a["key"] for a in first]) == []


def test_bot_records_the_basis_and_warns_before_the_close(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, anticipation_alerts=True)
    sent = []
    bot.notifier = lambda msg, **k: sent.append(msg)
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 120)
    basis = bot.state["anticipation"]
    assert basis["day"] == bot.state["last_decision_day"] and basis["assets"]
    holdings = bot.holdings_view()
    assert holdings, "le scénario doit détenir une position"
    a = holdings[0]["asset"]
    close_at = pd.Timestamp(basis["next_close"]).to_pydatetime()
    fb.set_price(f"{a.upper()}/USDT", holdings[0]["stop"] * 0.99)     # sous son stop
    bot._last_anticipation = 0.0
    bot._anticipate(close_at - timedelta(hours=1))
    assert any(f"Vente probable ce soir : {a.upper()}" in m for m in sent)
    n = len(sent)
    bot._last_anticipation = 0.0
    bot._anticipate(close_at - timedelta(minutes=30))
    assert len(sent) == n                                     # une seule fois par soir
    bot._last_anticipation = 0.0
    bot._anticipate(close_at - timedelta(hours=10))           # trop tôt : rien
    assert len(sent) == n
    f = bot.forecast(close_at - timedelta(hours=1))
    assert f["sells"] and f["risk"]["positions"] == len(holdings)
    # Bot mis à jour en cours de journée : niveaux calculés sans attendre 00:02.
    saved = bot.state.pop("anticipation")
    bot._last_selection_try = 0.0
    bot._refresh_selection(close_at - timedelta(hours=1))
    again = bot.state["anticipation"]
    assert again["day"] == saved["day"] and again["next_close"] == saved["next_close"]
    assert {a: b["buy_trigger"] for a, b in again["assets"].items()} == \
        {a: b["buy_trigger"] for a, b in saved["assets"].items()}
    assert dataclasses.is_dataclass(bot.g)
    assert DAY == timedelta(days=1)
