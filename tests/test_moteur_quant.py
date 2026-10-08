"""Prompt maître, étape 8 (docs/MOTEUR_QUANT.md) : moteur quantitatif. Lois
écrites sans scipy éprouvées contre des valeurs connues ; rendements,
descriptives, lois et normalité, tests, séries temporelles, stationnarité,
persistance, volatilité (EWMA, GARCH), covariance rétrécie, composantes
principales, régression, cointégration, études de la règle, anomalies,
ruptures, régimes cachés ; laboratoire reproductible, contrat, rapport."""

import math
import re

import numpy as np
import pandas as pd
import pytest

from test_trendguard import synthetic_market
from trendguard import contrats
from trendguard import moteur_quant as mq
from trendguard import trend_strategy as ts
from trendguard.contrats import ContractError, QuantResult

RNG = np.random.default_rng(11)


@pytest.fixture(scope="module")
def market():
    return synthetic_market()


@pytest.fixture(scope="module")
def lab(market):
    close, volume = market
    return mq.run(close, volume, ts.TrendParams())


# ---------- Lois écrites ici (sans scipy) ----------

def test_distributions_match_known_values():
    assert mq.chi2_sf(3.841459, 1) == pytest.approx(0.05, abs=1e-5)
    assert mq.chi2_sf(18.307038, 10) == pytest.approx(0.05, abs=1e-5)
    assert mq.chi2_sf(0.0, 3) == 1.0 and mq.chi2_sf(200.0, 2) < 1e-40
    assert mq.t_pvalue(2.228139, 10) == pytest.approx(0.05, abs=1e-5)
    assert mq.t_quantile(0.975, 10) == pytest.approx(2.228139, abs=1e-4)
    assert mq.t_cdf(0.0, 5) == pytest.approx(0.5) and mq.t_cdf(-1.0, 1) == pytest.approx(0.25)
    assert mq.beta_inc(2, 3, 0.4) == pytest.approx(0.5248, abs=1e-4)
    assert mq.gamma_q(1.0, 2.0) == pytest.approx(math.exp(-2.0))
    n = 10_000
    assert mq.ks_pvalue(1.358 / math.sqrt(n), n) == pytest.approx(0.05, abs=0.005)


# ---------- Rendements et descriptives (§7-8) ----------

def test_returns_and_annualization_are_explicit():
    px = pd.Series([100.0, 110.0, 99.0])
    assert list(mq.returns(px, "simple")) == pytest.approx([0.10, -0.10])
    assert list(mq.returns(px, "log")) == pytest.approx([math.log(1.1), math.log(0.9)])
    assert mq.cumulative([0.10, -0.10]) == pytest.approx(-0.01)
    assert mq.annualized(1.0, 730) == pytest.approx(math.sqrt(2) - 1)
    for bad in (lambda: mq.returns(pd.Series([1.0, 0.0])), lambda: mq.returns(px, "relatif"),
                lambda: mq.annualized(0.1, 0)):
        with pytest.raises(ValueError):
            bad()
    d = mq.describe([1.0, 2.0, 3.0, 4.0, 100.0])
    assert d["median"] == 3.0 and d["mad"] == 1.0 and d["iqr"] == 2.0 and d["skew"] > 0
    assert mq.describe([1.0])["status"] == "INSUFFICIENT_DATA"


# ---------- Lois, normalité, tests (§9-12) ----------

def test_fat_tails_are_found_and_normality_is_never_assumed():
    normal = np.random.default_rng(1).normal(0.0, 0.02, 3000)
    fat = np.random.default_rng(2).standard_t(3, 3000) * 0.02
    assert mq.normality(normal)["status"] == "NORMAL"
    assert mq.normality(fat)["status"] == "NON_NORMAL"
    assert mq.fit_distributions(fat)["best"] in ("student", "laplace")
    fits = mq.fit_distributions(normal)
    assert fits["fits"]["normal"]["aic"] < fits["fits"]["laplace"]["aic"]
    assert fits["fits"]["student"]["params"]["nu"] >= 10
    assert mq.normality([0.1] * 10)["status"] == "INSUFFICIENT_DATA"


def test_hypothesis_tests_say_what_they_test():
    x = RNG.normal(0.5, 1.0, 200)
    t = mq.t_test(x)
    assert t["decision"] == "REJECT_H0" and t["effect_size"] == pytest.approx(0.5, abs=0.2)
    assert set(t) == {"test_name", "null_hypothesis", "alternative_hypothesis", "statistic", "p_value", "alpha",
                      "decision", "effect_size", "sample_size"}
    y = RNG.normal(0.0, 3.0, 300)
    assert mq.welch(x, y)["p_value"] < 0.05
    assert mq.mann_whitney(x, x - 1.0)["p_value"] < 0.001
    assert mq.mann_whitney(x, x)["p_value"] > 0.5
    assert mq.adjust([0.01, 0.02, 0.04], "holm") == pytest.approx([0.03, 0.04, 0.04])


# ---------- Séries temporelles, stationnarité, persistance (§13-15) ----------

def _ar1(phi, n=4000, seed=5):
    e = np.random.default_rng(seed).normal(0, 1, n)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + e[t]
    return x


def test_autocorrelation_and_partial_autocorrelation():
    x = _ar1(0.6)
    assert mq.acf(x, 2)[0] == pytest.approx(0.6, abs=0.05)
    p = mq.pacf(x, 3)
    assert p[0] == pytest.approx(0.6, abs=0.05) and abs(p[1]) < 0.05 and abs(p[2]) < 0.05
    assert mq.ljung_box(x, 10)["p_value"] < 1e-6
    assert mq.ljung_box(RNG.normal(0, 1, 2000), 10)["p_value"] > 0.001


def test_stationarity_needs_adf_and_kpss_to_agree():
    walk = np.cumsum(np.random.default_rng(8).normal(0, 1, 2000))
    noise = np.random.default_rng(9).normal(0, 1, 2000)
    assert mq.stationarity(walk)["status"] == "NON_STATIONARY"
    assert mq.stationarity(noise)["status"] == "STATIONARY"
    a = mq.adf(walk)
    assert not a["reject_unit_root"] and a["critical"]["5%"] == pytest.approx(-2.86, abs=0.01)
    assert mq.kpss(walk)["reject_stationarity"] and not mq.kpss(noise)["reject_stationarity"]
    assert mq.stationarity([1.0] * 10)["status"] == "INSUFFICIENT_DATA"


def test_variance_ratio_sees_trends_and_mean_reversion():
    trend = _ar1(0.3, 5000)
    revert = _ar1(-0.3, 5000)
    noise = np.random.default_rng(4).normal(0, 1, 5000)
    assert mq.variance_ratio(trend, 10)["vr"] > 1.3 and mq.variance_ratio(trend, 10)["z"] > 3
    assert mq.variance_ratio(revert, 10)["vr"] < 0.8 and mq.variance_ratio(revert, 10)["z"] < -3
    assert abs(mq.variance_ratio(noise, 10)["z"]) < 3
    assert mq.variance_ratio(noise[:20], 10)["status"] == "INSUFFICIENT_DATA"


# ---------- Volatilité (§16) ----------

def _garch_sample(omega=2e-5, alpha=0.10, beta=0.85, n=4000, seed=6):
    rng = np.random.default_rng(seed)
    r, s2 = np.empty(n), omega / (1 - alpha - beta)
    for t in range(n):
        r[t] = math.sqrt(s2) * rng.normal()
        s2 = omega + alpha * r[t] ** 2 + beta * s2
    return r


def test_garch_recovers_its_parameters_and_forecasts_are_compared_out_of_sample():
    r = _garch_sample()
    g = mq.garch11(r)
    assert g["alpha"] == pytest.approx(0.10, abs=0.04) and g["beta"] == pytest.approx(0.85, abs=0.06)
    assert g["persistence"] < 1 and g["half_life_days"] > 0
    f = mq.vol_forecasts(r)
    assert f["status"] == "OK" and set(f["models"]) == {"GARCH(1,1)", "EWMA", "historique 30 jours"}
    assert f["models"]["GARCH(1,1)"]["qlike"] <= f["models"]["historique 30 jours"]["qlike"] + 0.01
    assert len(mq.ewma_variance(r)) == len(r)
    assert mq.garch11(r[:100])["status"] == "INSUFFICIENT_DATA"
    xmin, fmin = mq.nelder_mead(lambda v: (v[0] - 1) ** 2 + (v[1] + 2) ** 2, [0.0, 0.0])
    assert xmin == pytest.approx([1.0, -2.0], abs=1e-3) and fmin < 1e-6


# ---------- Corrélation, covariance, composantes principales (§17-19, §24) ----------

def test_covariance_shrinkage_and_principal_components():
    common = RNG.normal(0, 0.03, (500, 1))
    X = common + RNG.normal(0, 0.01, (500, 8))
    lw = mq.ledoit_wolf(X)
    assert 0 <= lw["shrinkage"] <= 1 and lw["shrunk_health"]["psd"] and lw["shrunk_health"]["symmetric"]
    assert lw["shrunk_health"]["condition"] <= lw["sample_health"]["condition"]
    p = mq.pca(X, [f"a{i}" for i in range(8)])
    assert p["explained"][0] > 0.8 and p["effective_bets"] < 2 and sum(p["explained"]) == pytest.approx(1)
    assert all(v > 0 for v in p["pc1_loadings"].values())
    assert not mq.matrix_health(np.array([[1.0, 2.0], [2.0, 1.0]]))["psd"]
    btc = RNG.normal(0, 0.03, 1500)
    alt = 1.2 * btc + RNG.normal(0, 0.02, 1500)
    d = mq.dependence(alt, btc)
    assert d["pearson"] > 0.8 and d["spearman"] > 0.8 and d["kendall"] > 0.5 and d["crash_days"] > 0


# ---------- Régression, facteurs, cointégration (§20-27) ----------

def test_regression_with_its_diagnostics():
    n = 800
    x1 = RNG.normal(0, 1, n)
    x2 = x1 + RNG.normal(0, 0.05, n)                     # presque la même : multicolinéarité
    y = 1.0 + 2.0 * x1 + RNG.normal(0, 1, n) * np.exp(0.7 * x1)
    res = mq.ols(y, np.column_stack([np.ones(n), x1, x2]), ("const", "x1", "x2"))
    assert res["vif"]["x1"] > 50 and res["breusch_pagan"]["p_value"] < 0.01
    simple = mq.ols(y, np.column_stack([np.ones(n), x1]), ("const", "x1"))
    lo, hi = simple["coefficients"]["x1"]["ci95"]
    assert lo < 2.0 < hi and simple["r2"] > 0.3 and 1.7 < simple["durbin_watson"] < 2.3
    hac = mq.ols(y, np.column_stack([np.ones(n), x1]), ("const", "x1"), hac_lags=5)
    assert hac["coefficients"]["x1"]["se"] > 0 and hac["hac_lags"] == 5
    btc = RNG.normal(0, 0.03, 1000)
    f = mq.factor_model(1.5 * btc + RNG.normal(0, 0.01, 1000), btc)
    assert f["beta"] == pytest.approx(1.5, abs=0.05) and f["r2"] > 0.9


def test_cointegration_is_found_only_when_it_exists():
    rng = np.random.default_rng(12)
    x = np.cumsum(rng.normal(0, 1, 1500))
    y = 0.8 * x + _ar1(0.5, 1500, seed=13)
    assert mq.engle_granger(y, x)["cointegrated"]
    assert mq.engle_granger(y, x)["half_life_days"] == pytest.approx(1.0, abs=0.5)
    z = np.cumsum(rng.normal(0, 1, 1500))
    assert not mq.engle_granger(z, x)["cointegrated"]


# ---------- Études de la règle (§28, §43, §51-52) ----------

def test_rule_studies_are_honest_about_their_evidence(market):
    close, volume = market
    p = ts.TrendParams()
    b = mq.breakout_study(close, volume, p, regime=False)
    ok = [d for d in b["horizons"].values() if d["status"] == "OK"]
    assert ok and all({"events", "excess", "ci95", "p_value", "p_holm"} <= set(d) for d in ok)
    assert all(d["ci95"][0] <= d["excess"] <= d["ci95"][1] for d in ok)
    m = mq.momentum_study(close, volume, p, horizon=10)
    assert m["status"] in ("OK", "INSUFFICIENT_DATA")
    t = mq.trade_study(close, volume, p, start=str(close.index[0].date()))
    assert t["status"] in ("OK", "INSUFFICIENT_DATA")
    pos = mq.cluster_bootstrap(np.full(60, 1.0) + RNG.normal(0, 0.1, 60), np.repeat(np.arange(12), 5))
    assert pos["ci95"][0] > 0 and pos["p_value"] < 0.01


# ---------- Anomalies, ruptures, régimes (§39-41) ----------

def test_anomalies_are_classified_not_corrected():
    idx = pd.date_range("2022-01-01", periods=400, freq="D", tz="UTC")
    rng = np.random.default_rng(2)
    base = np.exp(np.cumsum(rng.normal(0, 0.01, (400, 6)), axis=0))
    close = pd.DataFrame(base * 100, index=idx, columns=["btc", "eth", "xrp", "ada", "doge", "dot"])
    close.iloc[200, 2] *= 3.0                           # pic qui revient le lendemain : erreur probable
    close.iloc[300:, :] *= 0.5                          # krach de tout le marché
    an = mq.anomalies(close)
    kinds = {(d["asset"], d["day"]): d["kind"] for d in an["top"]}
    assert kinds.get(("xrp", str(idx[200].date()))) == "DATA_ERROR"
    assert kinds.get(("btc", str(idx[300].date()))) == "MARKET_EVENT"
    assert an["count"] == sum(an["by_kind"].values())


def test_variance_breaks_and_hidden_regimes():
    rng = np.random.default_rng(21)
    r = np.concatenate([rng.normal(0, 0.01, 600), rng.normal(0, 0.04, 600)])
    s = pd.Series(r, index=pd.date_range("2020-01-01", periods=1200, freq="D", tz="UTC"))
    br = mq.variance_breaks(s)
    assert br and any(abs((pd.Timestamp(b["day"], tz="UTC") - s.index[600]).days) < 30 for b in br)
    h = mq.hmm2(r)
    assert h["status"] == "OK" and h["vol_annual"][0] < h["vol_annual"][1]
    assert h["vol_annual"][0] == pytest.approx(0.01 * math.sqrt(365), rel=0.25)
    assert h["vol_annual"][1] == pytest.approx(0.04 * math.sqrt(365), rel=0.25)
    assert mq.hmm2(r[:50])["status"] == "INSUFFICIENT_DATA"


# ---------- Laboratoire, manifeste, contrat, rapport (§63-67, §89) ----------

def test_lab_is_reproducible_and_follows_its_contract(market, lab):
    close, volume = market
    again = mq.run(close, volume, ts.TrendParams())
    assert lab["result_hash"] == again["result_hash"]
    assert lab["manifest"]["run_id"] != again["manifest"]["run_id"]
    res = mq.result_of(lab)
    assert isinstance(res, QuantResult) and res.status in contrats.QUANT_STATUSES
    assert contrats.validate("QuantResult", res.as_dict()).valid
    assert contrats.PAST_NOT_PROMISE in res.limitations and lab["findings"]
    text = mq.render(lab)
    for section in ("## Conclusions", "## 5. La règle a-t-elle un pouvoir prédictif ?", "## 7. Manifeste",
                    "## 8. Limites"):
        assert section in text
    assert not re.search(r"bnanb|binfb", text)


def test_quant_result_contract_refuses_what_it_must():
    good = dict(run_id="2b1f4f3c-6c2d-4f6a-9d7e-0c1b2a3d4e5f", status="VALIDATED", engine_version="quant-1.0.0",
                dataset_hash="a", code_hash="b", result_hash="c", seed=7, assets=("btc", "eth"), findings=("x",),
                warnings=(), limitations=(contrats.PAST_NOT_PROMISE,), created_at="2026-10-08T12:00:00+00:00")
    QuantResult(**good)
    for change in ({"assets": ("eth",)}, {"warnings": ("section incomplète",)}, {"limitations": ()},
                   {"authorized": True}, {"dataset_hash": ""}, {"status": "PROVEN"}):
        with pytest.raises(ContractError):
            QuantResult(**{**good, **change})


def test_quant_command(tmp_path, monkeypatch, market):
    close, volume = market
    from trendguard import evolution
    monkeypatch.setattr(evolution, "load_history", lambda *a, **k: (close, volume))
    out = tmp_path / "QUANT.md"
    assert mq.main(["--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Laboratoire quantitatif de TrendGuard")


def test_rachelle_reads_the_lab_conclusions():
    from panel import assistant as asst
    r = asst.local_answer("que dit le laboratoire quantitatif ?", {})["answer"]
    assert "python trendguard_bot.py quant" in r and "Laboratoire quantitatif" in r
    assert asst.doc_points("QUANT.md") == [] or len(asst.doc_points("QUANT.md")) >= 3
    assert asst.doc_points("ABSENT.md") == []
