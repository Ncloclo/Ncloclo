"""Prompt maître, étape 10 (docs/MOTEUR_BACKTEST.md) : moteur de backtest.
Manifeste et reproductibilité, données validées avant de simuler,
statistique (Sharpe probabiliste et dégonflé, tests multiples, PBO), achats
en retard, capacité, références, comptabilité, verdict et « prêt pour le
moteur de risque », contrat du résultat, rapport et ses limites."""

import dataclasses

import numpy as np
import pandas as pd
import pytest

from panel import assistant as asst
from trendguard import moteur_backtest as mb
from trendguard import trend_strategy as ts
from trendguard.contrats import NO_GUARANTEE, BacktestResult, ContractError

P = ts.TrendParams()


def decade_market(seed=11, days=3500):
    """Marché synthétique de 2017 à 2026 (les deux époques du protocole) :
    régimes haussiers et baissiers alternés, trois cryptos liées à BTC."""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(n, mu) for _ in range(8) for n, mu in ((300, 0.004), (200, -0.004))])[:days]
    btc = 1000 * np.exp(np.cumsum(drift + rng.normal(0, 0.03, days)))
    lb = np.diff(np.log(btc), prepend=np.log(btc[0]))
    out = {"btc": btc}
    for k, name in enumerate(("eth", "xrp", "ada")):
        out[name] = (10.0 + k) * np.exp(np.cumsum(1.2 * lb + rng.normal(0.0004 * (k - 1), 0.035, days)))
    dates = pd.date_range("2017-01-01", periods=days, freq="D", tz="UTC")
    close = pd.DataFrame(out, index=dates)
    return close, pd.DataFrame(1e8, index=dates, columns=close.columns)


@pytest.fixture(scope="module")
def market():
    return decade_market()


@pytest.fixture(scope="module")
def quick(market):
    close, volume = market
    return mb.run(close, volume, P, quick=True)


# ---------- Configuration, manifeste, reproductibilité (§5-6, §73) ----------

def test_the_manifest_makes_a_run_reproducible(market):
    close, volume = market
    a = ts.backtest(close, volume, P, "2018-01-01", "2026-07-01")
    b = ts.backtest(close, volume, P, "2018-01-01", "2026-07-01")
    assert mb.result_hash(a) == mb.result_hash(b)                        # même entrée, même résultat
    cfg = mb.config(P, "2018-01-01", "2026-07-01")
    other = mb.config(dataclasses.replace(P, trail_atr=6.0), "2018-01-01", "2026-07-01")
    assert mb.digest(cfg) != mb.digest(other) and cfg["strategy_id"] == "trendguard.cassure"
    m = mb.manifest(cfg, close, volume, a)
    assert {"run_id", "git", "dataset", "config_hash", "code_hash", "environment_hash", "seed", "result_hash"} <= set(m)
    assert m["result_hash"] == mb.result_hash(a) and m["config_hash"] == mb.digest(cfg) and len(m["code_hash"]) == 16
    touched = close.copy()
    touched.iloc[100, 1] *= 1.01
    assert mb.manifest(cfg, touched, volume, a)["dataset"]["hash"] != m["dataset"]["hash"]


# ---------- Données validées avant de simuler (§7-9, §71) ----------

def test_data_are_validated_before_simulating(market, monkeypatch):
    close, volume = market
    ok = mb.validate_data(close, volume, P)
    assert ok["status"] == "PASS" and ok["score"] == 95.0 and not ok["critical"]   # survivants : réserve permanente
    dup = pd.concat([close, close.iloc[[10]]]).sort_index()
    assert "Dates" in mb.validate_data(dup, volume, P)["critical"]
    neg = close.copy()
    neg.iloc[50, 2] = -1.0
    assert mb.validate_data(neg, volume, P)["status"] == "FAIL"
    gaps = close.drop(close.index[200:240])
    g = mb.validate_data(gaps, volume, P)
    assert any(n == "Jours manquants" and not k for n, k, _c, _d in g["checks"]) and g["score"] < ok["score"]
    flat = close.copy()
    flat.iloc[300:310, 1] = flat.iloc[300, 1]
    assert any(n == "Cours figés" and not k for n, k, _c, _d in mb.validate_data(flat, volume, P)["checks"])
    naive = close.tz_localize(None)
    assert mb.validate_data(naive, volume, P)["critical"] == ["Schéma"]
    assert any(n == "Volumes" and not k for n, k, _c, _d in mb.validate_data(close, None, P)["checks"])
    honest = ts.asset_features

    def leaky(c, p, v=None):
        f = honest(c, p, v)
        f["mom"] = c.shift(-5) / c - 1                                     # regarde dans cinq jours
        return f
    monkeypatch.setattr(ts, "asset_features", leaky)
    bad = mb.validate_data(close, volume, P)
    assert bad["status"] == "FAIL" and "Fuite vers le futur" in bad["critical"]


# ---------- Statistique (§39-40, §38) ----------

def test_sharpe_statistics_follow_their_formulas():
    rng = np.random.default_rng(3)
    x = rng.normal(0.001, 0.02, 2000)
    s = mb.sharpe_stats(x)
    m, sd, skew, kurt = mb.moments(x)
    sr = m / sd
    se = np.sqrt((1 - skew * sr + (kurt - 1) / 4 * sr ** 2) / (len(x) - 1))
    assert s["sharpe"] == pytest.approx(sr * np.sqrt(365)) and s["t"] == pytest.approx(sr * np.sqrt(2000))
    assert s["psr"] == pytest.approx(mb._N.cdf(sr / se)) and s["p_value"] == pytest.approx(1 - mb._N.cdf(s["t"]))
    many = mb.sharpe_stats(x, trials=50, trials_var=(0.5 / np.sqrt(365)) ** 2)
    few = mb.sharpe_stats(x, trials=5, trials_var=(0.5 / np.sqrt(365)) ** 2)
    assert many["dsr"] < few["dsr"] < s["psr"] and many["sr0_annual"] > few["sr0_annual"] > 0
    assert mb.sharpe_stats(x[:10]) == {"n": 10}
    lo, hi = mb.block_bootstrap(x, lambda y: y.mean() / y.std() * np.sqrt(365), sims=300)
    assert lo < s["sharpe"] < hi and (lo, hi) == mb.block_bootstrap(x, lambda y: y.mean() / y.std() * np.sqrt(365),
                                                                     sims=300)


def test_multiple_testing_corrections_on_a_known_case():
    p = [0.01, 0.04, 0.03, 0.005]
    assert mb.adjust_pvalues(p, "bonferroni") == pytest.approx([0.04, 0.16, 0.12, 0.02])
    assert mb.adjust_pvalues(p, "holm") == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert mb.adjust_pvalues(p, "bh") == pytest.approx([0.02, 0.04, 0.04, 0.02])
    with pytest.raises(ValueError):
        mb.adjust_pvalues(p, "au hasard")


def test_the_probability_of_backtest_overfitting():
    runs = [mb.pbo(np.random.default_rng(s).normal(0, 0.01, (2000, 27)), blocks=10) for s in range(20)]
    assert all(x["combos"] == 252 for x in runs)
    assert 0.35 <= np.mean([x["pbo"] for x in runs]) <= 0.65                 # sans talent : une chance sur deux
    noise = np.random.default_rng(5).normal(0, 0.01, (2000, 27))
    flat = noise - noise.mean(axis=0)                                          # mêmes moyennes : le meilleur
    assert mb.pbo(flat, blocks=10)["pbo"] == 1.0                               # d'un côté est perdant de l'autre
    real = noise.copy()
    real[:, 0] += 0.004                                                        # un réglage vraiment meilleur
    assert mb.pbo(real, blocks=10)["pbo"] < 0.1 and mb.pbo(real, blocks=10, embargo=20)["pbo"] < 0.1
    assert mb.pbo(noise[:, :1])["pbo"] is None


# ---------- Exécution éprouvée, capacité, références (§19, §41-49) ----------

def test_entries_a_day_late_and_on_time(market):
    close, volume = market
    pre = ts.precompute(close, volume, P)
    ref = ts.backtest(close, volume, P, "2018-01-01", "2026-07-01", pre=pre)
    same = ts.backtest(close, volume, P, "2018-01-01", "2026-07-01", pre=pre,
                       hooks=mb.delayed_entries(close, P, 0, pre))
    assert mb.result_hash(same) == mb.result_hash(ref)                       # sans retard : rien ne change
    late = ts.backtest(close, volume, P, "2018-01-01", "2026-07-01", pre=pre,
                       hooks=mb.delayed_entries(close, P, 1, pre))
    first = min(t["entry_date"] for t in ref.trades)
    assert late.trades and min(t["entry_date"] for t in late.trades) > first


def test_capacity_grows_impact_with_capital(market):
    close, volume = market
    pre = ts.precompute(close, volume, P)
    rows = mb.capacity(close, volume, P, "2023-01-01", "2026-07-01", pre)
    assert [r["capital"] for r in rows] == list(mb.CAPITALS)
    parts = [r["participation_median"] for r in rows]
    assert all(b == pytest.approx(a * 10) for a, b in zip(parts, parts[1:]))
    assert all(b["impact_pct"] > a["impact_pct"] for a, b in zip(rows, rows[1:]))
    assert rows[0]["participation_p95"] <= mb.PARTICIPATION_MAX < rows[-1]["participation_p95"]
    assert rows[-1]["cagr_pct"] < rows[0]["cagr_pct"]


def test_references_regimes_and_accounting(market):
    close, volume = market
    pre = ts.precompute(close, volume, P)
    res = ts.backtest(close, volume, P, "2018-01-01", "2026-07-01", pre=pre)
    same = mb.benchmark(res.equity, res.equity)
    assert same["beta"] == pytest.approx(1) and same["correlation"] == pytest.approx(1)
    assert same["alpha_pct"] == pytest.approx(0, abs=1e-9)
    rows = {r["regime"]: r for r in mb.regimes(res, close, P)}
    assert rows["BTC haussier"]["days"] + rows["BTC baissier"]["days"] == len(res.equity) - 1
    assert rows["BTC baissier"]["trades"] == 0                               # la règle n'achète qu'en régime haussier
    acc = mb.accounting(res, 10_000.0)
    assert acc["ok"] and acc["checked"] and abs(acc["gap"]) < 1e-6
    rate = mb.signal_rate(pre, P)
    assert 0 < rate < 0.2
    rnd = ts.backtest(close, volume, P, "2018-01-01", "2026-07-01", pre=pre,
                      hooks=mb.random_entries(len(close.index), list(pre[0]), rate, 1))
    again = ts.backtest(close, volume, P, "2018-01-01", "2026-07-01", pre=pre,
                        hooks=mb.random_entries(len(close.index), list(pre[0]), rate, 1))
    assert rnd.trades and mb.result_hash(rnd) == mb.result_hash(again)        # hasard à graine fixée
    mc = mb.bootstrap_paths(res.equity.pct_change().dropna().values, sims=300)
    assert 0 <= mc["p_ruin"] <= mc["p_dd30"] <= mc["p_dd20"] <= 1 and mc["dd_p95"] >= mc["dd_p50"]


# ---------- Verdict, prêt pour le risque, contrat, rapport (§51-57, §77, §81) ----------

def test_a_quick_report_is_never_ready_for_risk(quick):
    v = quick["verdict"]
    assert quick["reproducible"] and quick["accounting"]["ok"] and quick["strategy"]["status"] == "READY_FOR_BACKTEST"
    assert v["readiness"] != "READY_FOR_RISK" and any(x == "NON MESURÉ" for _n, x in v["checklist"])
    assert set(quick["quality"]["missing"]) == {"robustness", "statistics"}
    res = quick["result"]
    assert isinstance(res, BacktestResult) and NO_GUARANTEE in res.limitations and res.readiness == v["readiness"]
    text = mb.render(quick)
    for part in ("## 1. Résumé", "## 9. Stress", "## 10. Capacité", "## 16. Limites", NO_GUARANTEE,
                 "Walk-forward : non mesuré", "Non mesurée (rapport rapide)."):
        assert part in text, part


def test_the_verdict_rejects_and_invalidates_with_reasons(quick):
    r = dict(quick)
    r["oos"] = dict(quick["oos"], cagr_pct=-3.0)
    v = mb.verdict(r)
    assert v["status"] == "REJECTED" and v["readiness"] == "REJECTED" and "perdante hors échantillon (depuis 2023)" \
        in v["rejections"]
    r = dict(quick, reproducible=False)
    assert mb.verdict(r)["status"] == "INVALID"
    r = dict(quick, data=dict(quick["data"], critical=["Fuite vers le futur"], status="FAIL"))
    assert "regard vers le futur détecté" in mb.verdict(r)["rejections"]


def test_the_result_contract_refuses_inconsistent_verdicts(quick):
    res = quick["result"]
    for change in ({"status": "REJECTED", "rejection_reasons": ()},
                   {"status": "INVALID", "readiness": "READY_FOR_RISK"},
                   {"limitations": ("rien",)}, {"run_id": "R-1"}, {"quality_score": 1.5}, {"result_hash": ""}):
        with pytest.raises(ContractError):
            dataclasses.replace(res, **change)


def test_the_full_validation_measures_everything(market):
    close, volume = market
    r = mb.run(close, volume, P)
    v = r["verdict"]
    assert not any(x == "NON MESURÉ" for _n, x in v["checklist"]) and not r["quality"]["missing"]
    nb = r["neighbours"]
    assert nb["n"] == 27 and 0 <= nb["plateau"] <= 1 and 0 <= nb["pbo"] <= 1 and r["stats"]["dsr"] <= r["stats"]["psr"]
    assert r["stats"]["dsr_prudent"] <= r["stats"]["dsr"] and r["walk_forward"]["windows"] >= 10
    assert v["readiness"] in ("READY_FOR_RISK", "RESEARCH_ONLY", "REJECTED")
    assert "## 7. Robustesse : réglages voisins" in mb.render(r) and "probabilité de sur-ajustement" in mb.render(r)


def test_rachelle_explains_the_validation():
    a = asst.local_answer("le backtest est il valide ? validation du backtest", {})["answer"]
    assert "python trendguard_bot.py validation" in a and "jamais une garantie" in a
