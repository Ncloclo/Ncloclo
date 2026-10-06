"""Prompt maître, étape 7 (docs/FINANCE.md) : cœur d'intelligence financière
(référentiel des instruments, qualité des données, indicateurs versionnés et
valeurs de référence, anti-fuite, prévisions de fréquence et scénarios,
calibration, liens entre cryptos, signal, classement, « pas de trade »,
analyse complète) ; consultatif : il ne change aucun trade."""

import json
import logging
import math
import sqlite3

import numpy as np
import pandas as pd
import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import make_bot, synthetic_market
from trendguard import donnees, finance, regimes
from trendguard import trend_strategy as ts
from trendguard.contrats import (
    ContractError,
    Feature,
    FinancialAnalysis,
    FinancialSignal,
    Forecast,
    Instrument,
    Scenario,
    ScenarioSet,
)

P = ts.TrendParams()


@pytest.fixture
def logger():
    lg = logging.getLogger("test.finance")
    lg.setLevel(logging.ERROR)
    return lg


@pytest.fixture(scope="module")
def market():
    close, volume = synthetic_market()
    return close, volume


def long_market(days=1400, seed=5):
    """Quatre ans de marché synthétique, hausses et baisses alternées : assez
    de périodes comparables pour des prévisions de fréquence."""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(250, 0.004), np.full(150, -0.004), np.full(250, 0.004), np.full(150, -0.004),
                            np.full(250, 0.004), np.full(150, -0.003), np.full(200, 0.003)])[:days]
    btc = 30_000 * np.exp(np.cumsum(drift + rng.normal(0, 0.025, days)))
    lb = np.diff(np.log(btc), prepend=np.log(btc[0]))
    out = {"btc": btc}
    for k, (name, px) in enumerate((("eth", 2000.0), ("xrp", 0.6))):
        out[name] = px * np.exp(np.cumsum(1.2 * lb + rng.normal(0.0003 * k, 0.03, days)))
    dates = pd.date_range("2022-01-01", periods=days, freq="D", tz="UTC")
    close = pd.DataFrame(out, index=dates)
    return close, pd.DataFrame(1e8, index=dates, columns=close.columns)


def _day(close, k):
    return str(close.index[k].date())


# ---------- Référentiel et qualité des données ----------

def test_instrument_master_states_only_what_is_known(market):
    close, _v = market
    inst = {i.base: i for i in finance.instruments(close, vetoed=["doge"])}
    assert inst["eth"].instrument_id == "binance:spot:ETH-USDT"
    btc = inst["btc"]
    assert btc.symbol == "BTC/USDT" and btc.calendar == "24/7" and btc.status == "TRADING"
    assert btc.listing_date == str(close["btc"].first_valid_index().date()) and btc.tick_size is None
    assert inst["doge"].status == "VETO"
    with pytest.raises(ContractError):
        Instrument("BTC", "BTC/USDT", "btc", "USDT", "Binance", "CRYPTO", "USDT", "UTC", "24/7", None, "TRADING")


def test_data_quality_engine_scores_each_dimension(market):
    close, _v = market
    day = _day(close, 600)
    good = finance.asset_quality(close["eth"], day)
    assert good["quality"] == 1.0
    s = close["eth"].copy()
    s.iloc[598:601] = np.nan                                                # trois jours sans bougie
    assert finance.asset_quality(s, day)["freshness"] == 0.0
    s = close["eth"].copy()
    s.iloc[400:420] = np.nan
    assert finance.asset_quality(s, day)["completeness"] < 1
    s = close["eth"].copy()
    s.iloc[500] = 0.0
    assert finance.asset_quality(s, day)["consistency"] < 1
    s = close["eth"].copy()
    s.iloc[550:556] = s.iloc[549]                                           # cours figé
    assert finance.asset_quality(s, day)["reliability"] < 1


# ---------- Indicateurs : valeurs de référence (§45) et anti-fuite (§22) ----------

def test_feature_reference_values():
    """Jeu de référence : chaque calcul critique, entrée et sortie attendue
    (version FEATURE_VERSION) ; un changement de calcul doit changer la
    version et ces valeurs."""
    assert finance.FEATURE_VERSION == "1.0.0"
    idx = pd.date_range("2024-01-01", periods=400, freq="D", tz="UTC")
    up = pd.Series(100 * 1.01 ** np.arange(400), index=idx)
    f = finance.features(up, up * 3)
    last = finance.snapshot(f, str(idx[-1].date()))
    assert last["ret_1d"] == pytest.approx(math.log(1.01), abs=1e-6)
    assert last["momentum_90d"] == pytest.approx(90 * math.log(1.01), abs=1e-6)
    assert last["breakout_gap_30d"] == pytest.approx(0.01, abs=1e-6)
    assert last["volatility_30d"] == pytest.approx(0.0, abs=1e-6)
    assert last["rsi_14"] == pytest.approx(100.0) and last["drawdown_365d"] == pytest.approx(0.0)
    assert last["beta_btc_90d"] is None and last["sharpe_365d"] is None          # 0/0 : non mesurable, jamais 0
    rng = np.random.default_rng(42)
    walk = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, 600))), index=pd.date_range(
        "2023-01-01", periods=600, freq="D", tz="UTC"))
    btc = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, 600))), index=walk.index)
    vol = pd.Series(1e7 * np.exp(rng.normal(0, 0.3, 600)), index=walk.index)
    ref = finance.snapshot(finance.features(walk, btc, vol), "2024-08-22")
    assert ref == pytest.approx(REFERENCE, abs=1e-6)


REFERENCE = {       # version 1.0.0 des indicateurs : marche au hasard de graine 42, au 2024-08-22
    "ret_1d": -0.052249, "momentum_90d": -0.16278, "breakout_gap_30d": -0.16125, "volatility_30d": 0.658509,
    "ema_ratio_50_200": -0.029293, "rsi_14": 39.791677, "drawdown_365d": -0.310504, "volume_zscore_30d": -0.412208,
    "beta_btc_90d": -0.055844, "corr_btc_90d": -0.03896, "sharpe_365d": 0.163543, "sortino_365d": 0.230984,
    "skew_365d": -0.043322, "kurtosis_365d": -0.125294, "var95_1d": -0.050989, "cvar95_1d": -0.062533}


def test_no_indicator_looks_into_the_future(market):
    close, volume = market
    days = [close.index[k] for k in (300, 450, 600, 650)]
    assert finance.leakage_violations(lambda d: finance.features(d["eth"], d["btc"]), close, days) == []
    assert finance.leakage_violations(lambda d: ts.asset_features(d["eth"], P, volume["eth"].loc[:d.index[-1]]),
                                      close, days) == []
    assert finance.leakage_violations(lambda d: regimes.regime_frame(d)[["tendance", "volatilite", "appetit",
                                                                          "phase"]], close, days) == []

    def cheat(d):
        return pd.DataFrame({"next": d["eth"].shift(-1)})                 # utilise la bougie de demain
    assert finance.leakage_violations(cheat, close, days)                     # SIGNAL_INVALID
    with pytest.raises(ContractError) as e:
        Feature("binance:spot:ETH-USDT", "2026-10-05", "rsi_14", 55.0, "1.0.0", "2026-10-07T00:00:00+00:00")
    assert e.value.code == "LOOK_AHEAD"


# ---------- Prévisions, scénarios, calibration ----------

def test_forecasts_and_scenarios_use_only_the_past():
    close, _v = long_market()
    day = _day(close, 1300)
    reg = finance.btc_regime(close, P)
    full = finance.forecast("eth", close["eth"], reg, day)
    cut = finance.forecast("eth", close["eth"].loc[:day], reg.loc[:day], day)
    assert full is not None and full == cut                                   # l'avenir n'y change rien
    assert full.ci_low <= full.p_up <= full.ci_high and full.cases >= finance.MIN_CASES
    sc = finance.scenarios("eth", close["eth"], reg, day)
    assert abs(sum(s.probability for s in sc.scenarios) - 1) < 1e-9 and sc.cases == full.cases
    assert finance.forecast("eth", close["eth"], reg, _day(close, 300)) is None   # trop peu de cas
    with pytest.raises(ContractError):
        ScenarioSet("binance:spot:ETH-USDT", day, 30, sc.scenarios[:-1], sc.cases, "x")
    with pytest.raises(ContractError):
        Forecast("binance:spot:ETH-USDT", day, 30, 0.9, 0.1, 0.1, 0.1, 0.0, -0.1, 0.1, 0.2, 0.5, 20, "x")
    with pytest.raises(ContractError):
        Scenario("PANIQUE", 0.1, 1, None, None, None, "")


def test_forecast_evaluation_and_calibration():
    assert finance.evaluate_forecast(0.8, 0.05) == {"hit": 1.0, "brier": pytest.approx(0.04)}
    assert finance.evaluate_forecast(0.8, -0.05)["hit"] == 0.0
    good = finance.calibration([(0.9, 1.0)] * 20 + [(0.1, 0.0)] * 20)
    assert good["brier"] == pytest.approx(0.01) and good["hit_ratio"] == 1.0 and not good["degraded"]
    bad = finance.calibration([(0.9, 0.0)] * 30)
    assert bad["degraded"] and bad["bias"] == pytest.approx(0.9)
    assert finance.calibration([]) == {"n": 0}


# ---------- Liens, signal, classement, « pas de trade », analyse ----------

def test_cross_asset_and_regime_vocabulary(market):
    close, _v = market
    x = finance.cross_asset(close, _day(close, 600))
    assert -1 <= x["avg_corr_90d"] <= 1 and set(x["corr_btc"]) == set(close.columns) - {"btc"}
    assert finance.regime_labels({"tendance": "haussière", "volatilite": "forte", "appetit": "risk-off",
                                  "phase": "crise"}) == ["BULL", "HIGH_VOLATILITY", "RISK_OFF", "CRISIS"]


def test_decision_support_says_no_trade_with_reasons(market):
    close, volume = market
    reg = finance.btc_regime(close, P)
    bull_day = next(_day(close, k) for k in range(400, 700) if pd.notna(reg.iloc[k]) and bool(reg.iloc[k]))
    a = finance.analyze("eth", close, volume, bull_day, P)
    assert a.details["btc_bull"] and a.recommendation in ("BUY_SIGNAL", "WATCH", "NO_TRADE") and not a.authorized
    bear_day = next(_day(close, k) for k in range(200, 700) if pd.notna(reg.iloc[k]) and not bool(reg.iloc[k]))
    b = finance.analyze("eth", close, volume, bear_day, P)
    assert b.recommendation == "NO_TRADE" and "POLICY_BLOCK" in b.reasons
    vetoed = finance.analyze("eth", close, volume, bull_day, P, {"vetoed": {"eth"}})
    assert vetoed.recommendation == "NO_TRADE" and any("Binance" in t for t in vetoed.reason_texts)
    split = finance.analyze("eth", close, volume, bull_day, P, {"committee": {"eth": {"disagreement": "HIGH"}}})
    assert "MODEL_DISAGREEMENT" in split.reasons
    with pytest.raises(ContractError):
        FinancialAnalysis("binance:spot:ETH-USDT", bull_day, "NO_TRADE", ("LOW_CONFIDENCE",), ("x",), None, {}, {},
                          (), (), (), ())
    with pytest.raises(ContractError):
        FinancialAnalysis("binance:spot:ETH-USDT", bull_day, "WATCH", (), (), None, {}, {}, (), (), (), (),
                          authorized=True)                                       # jamais une autorisation
    with pytest.raises(ContractError):
        FinancialSignal("S", "binance:spot:ETH-USDT", "LONG", "BREAKOUT", 1.5, None, "MEDIUM_TERM", "", "", None,
                        None, (), "v", "CANDIDATE", bull_day)


def test_full_analysis_of_one_crypto(market):
    close, volume = market
    day = _day(close, 650)
    a = finance.analyze("eth", close, volume, day, P, {"events": ["inflation américaine (CPI)"]})
    d = a.details
    for key in ("quality", "features", "regime", "regime_labels", "cross_asset", "forecast", "scenarios", "signal",
                "opportunity", "fundamental", "data_cutoff_at"):
        assert key in d, key
    assert "sans objet" in d["fundamental"] and d["feature_version"] == finance.FEATURE_VERSION
    assert set(a.confidence) == {"DATA_CONFIDENCE", "MODEL_CONFIDENCE", "SIGNAL_CONFIDENCE", "FORECAST_CONFIDENCE",
                                 "DECISION_CONFIDENCE"}
    assert a.confidence["MODEL_CONFIDENCE"] is None                            # rien d'évalué : non mesurée
    assert a.evidence and a.invalidating and any("CPI" in r for r in a.risks)
    assert d["signal"]["expected_return"] is None and d["signal"]["status"] == "CANDIDATE"
    opp = d["opportunity"]
    assert "sentiment" in opp["not_measured"] and opp["version"] == finance.OPPORTUNITY_VERSION
    text = finance.render(a)
    assert "Recommandation" in text and "Sans objet" in text and json.dumps(a.as_dict(), default=str)


# ---------- Journal financier ----------

def test_the_journal_keeps_features_forecasts_and_their_evaluation():
    j = donnees.Journal(":memory:")
    assert max(j.applied()) == 4
    fc = {"horizon_days": 30, "p_up": 0.6, "ci_low": 0.45, "ci_high": 0.74, "expected_return": 0.03, "cases": 25,
          "version": finance.FORECAST_VERSION}
    j.record_finance("2026-09-01", "eth", {"rsi_14": 55.0, "momentum_90d": None}, "1.0.0", "2026-09-02T00:00:00+00:00",
                     {"recommendation": "WATCH", "reasons": [], "opportunity": 0.5, "version": "analyse-1.0.0"},
                     fc, 2000.0, "2026-10-01")
    j.record_finance("2026-09-01", "eth", {"rsi_14": 99.0}, "1.0.0", "2026-09-02T00:00:00+00:00",
                     {"recommendation": "WATCH", "reasons": [], "opportunity": 0.5, "version": "analyse-1.0.0"},
                     fc, 2000.0, "2026-10-01")                                  # jamais remplacé
    c = j.counts()
    assert c["fin_features"] == 2 and c["fin_forecasts"] == 1 and c["fin_analyses"] == 1
    assert j.conn.execute("SELECT value FROM fin_features WHERE name='rsi_14'").fetchone()[0] == 55.0
    assert j.due_forecasts("2026-09-30") == [] and len(j.due_forecasts("2026-10-01")) == 1
    j.record_forecast_evaluation(j.due_forecasts("2026-10-01")[0]["id"], 0.08, 1, 0.16)
    assert j.forecast_outcomes() == [(0.6, 1.0)] and j.due_forecasts("2026-10-05") == []
    with pytest.raises(sqlite3.IntegrityError):
        j.conn.execute("UPDATE fin_features SET value = 0")                    # en ajout seulement
    with pytest.raises(sqlite3.IntegrityError):
        j.conn.execute("INSERT INTO fin_features (day, asset, name, value, version, data_cutoff_at, created_at) "
                       "VALUES ('2026-09-01', 'eth', 'x', 1, '1.0.0', '2026-09-03T00:00:00+00:00', 'now')")
    assert j.verify()["ok"] and j.rollback(3) == [4] and j.migrate() == [4]


# ---------- Dans le bot : consultatif ----------

def test_the_finance_core_never_changes_a_trade(logger):
    close, volume = long_market()
    start = 1100
    bot, fb = make_bot("paper", close, logger, finance=True)
    assert bot.boot()
    _run(bot, fb, close, volume, start, start + 45)
    fi = bot.state["finance"]
    assert fi["assets"] and set(fi["assets"]) == set(close.columns)
    assert bot.journal.counts()["fin_features"] > 0 and bot.journal.counts()["fin_forecast_evaluations"] > 0
    assert bot.journal.verify()["ok"]
    ref, fr_ = make_bot("paper", close, logger)
    assert ref.boot()
    _run(ref, fr_, close, volume, start, start + 45)
    key = lambda b: [(t["asset"], t["date"], round(t["pnl"], 8)) for t in b.state["trades"]]  # noqa: E731
    assert key(bot) == key(ref) and sorted(bot.state["paper"]["holdings"]) == sorted(ref.state["paper"]["holdings"])


def test_rachelle_reports_the_financial_analysis():
    ctx = {"finance": {"day": "2026-10-05", "top": ["eth"], "assets": {
        "aave": {"reco": "NO_TRADE", "reasons": ["BTC sous sa moyenne de 150 jours : la règle n'achète pas"],
                 "opportunity": 0.2, "p_up": 0.41, "cases": 33},
        "eth": {"reco": "WATCH", "reasons": [], "opportunity": 0.6, "p_up": 0.55, "cases": 30}}}}
    r = asst.local_answer("Analyse de AAVE", ctx)["answer"]
    assert "AAVE : pas de trade" in r and "41 %" in r and "ne décide pas" in r
    assert "python trendguard_bot.py finance" in asst.local_answer("analyse financiere", {})["answer"]
