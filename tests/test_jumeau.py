"""Prompt maître, étape 28 (docs/JUMEAU.md) : jumeau numérique et
simulation. Le paper rejoué par la boucle de backtest ; Monte-Carlo dont la
convergence est vérifiée ; crises rejouées ; panne de données injectée ;
sensibilité aux réglages ; données synthétiques dites ; isolation de la
production ; qualité et son contrat."""

import dataclasses
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from panel import assistant as asst
from test_trendguard import synthetic_market
from trendguard import contrats, evolution, jumeau, report_health
from trendguard import trend_strategy as ts
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, TwinReport

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
START = "2024-07-01"
CRISES = [("Chute synthétique", "2024-09-01", "2024-12-31", "crise"),
          ("Avant les données", "2018-01-01", "2018-12-31", "crise")]


def _paper(close, volume, first="2025-03-01"):
    """Un essai paper qui a acheté exactement ce que la boucle achète."""
    bought = []

    def record(i, plans, _h, _eq):
        bought.extend((pl["asset"], close.index[i]) for pl in plans)
        return plans

    ts.backtest(close, volume, ts.TrendParams(), first, str(close.index[-1].date()), capital=100.0,
                hooks=ts.BacktestHooks(filter_plans=record))
    started = datetime.fromisoformat(first).replace(tzinfo=timezone.utc) - timedelta(minutes=5)
    trades = [{"asset": a, "entry_date": (d + timedelta(days=1)).isoformat(), "reason": "TRAIL"} for a, d in bought]
    return {"started_at": started.isoformat(), "start_equity": 100.0, "trades": trades}, len(bought)


@pytest.fixture(scope="module")
def market():
    return synthetic_market()


def test_the_twin_of_the_paper_account(market):
    close, volume = market
    state, n = _paper(close, volume)
    assert n
    s = jumeau.sync(state, close, volume, ts.TrendParams())
    assert s["status"] == "OK" and s["ratio"] == 1.0 and s["matched"] == n and not s["unexplained"]
    drift = dict(state, trades=state["trades"][1:])
    d = jumeau.sync(drift, close, volume, ts.TrendParams())
    assert d["ratio"] < 1.0 and d["unexplained"]                       # un achat que le paper n'a pas fait : vu
    assert jumeau.sync({}, close, volume, ts.TrendParams())["status"] == "UNKNOWN"
    assert jumeau.sync(state, None, None, ts.TrendParams())["status"] == "UNKNOWN"


def test_monte_carlo_convergence_is_checked_and_reproducible(market):
    close, volume = market
    eq = ts.backtest(close, volume, ts.TrendParams(), START, str(close.index[-1].date())).equity
    mc = jumeau.monte_carlo(eq)
    assert mc == jumeau.monte_carlo(eq)                                   # même graine : même résultat
    assert mc["seed"] == jumeau.SEED and [n for n, _m, _d in mc["levels"]] == list(jumeau.MC_LEVELS)
    assert mc["status"] in ("CONVERGED", "MONTE_CARLO_NOT_CONVERGED")
    if mc["converged"]:
        i = [n for n, _m, _d in mc["levels"]].index(mc["at"])
        d1, d2 = mc["levels"][i - 1][2], mc["levels"][i][2]
        assert abs(d2 - d1) / abs(d1) < jumeau.MC_TOLERANCE
    flat = jumeau.monte_carlo(pd.Series([100.0] * 10))
    assert flat["status"] == "MONTE_CARLO_NOT_CONVERGED"                 # pas assez de données : non convergé, dit


def test_crises_sensitivity_and_an_injected_fault(market, monkeypatch):
    close, volume = market
    p = ts.TrendParams()
    monkeypatch.setattr(evolution, "CRISES", CRISES)
    cr = jumeau.crises(close, volume, p)
    assert cr[0]["covered"] and "max_dd_pct" in cr[0] and not cr[1]["covered"]      # hors des données : dit, jamais inventé
    sens = jumeau.sensitivity(close, volume, p, START)
    assert len(sens) == 2 * len(jumeau.SENSITIVITY)
    assert all(isinstance(x["value"], int) for x in sens if x["param"] in jumeau.INT_PARAMS)
    assert {x["factor"] for x in sens} == {0.9, 1.1}
    f = jumeau.fault_data_gap(close, volume, p, START)
    assert f["survived"] and f["days"] == 3


def test_isolation_from_production():
    iso = jumeau.isolation()
    assert iso["ok"] and not iso["network"] and not iso["production_write_path"]
    src = open(jumeau.__file__, encoding="utf-8").read()
    for forbidden in ("enter_planned", "create_order", "write_state", "autonomy.write_json", "requests."):
        assert forbidden not in src


def test_the_quality_and_its_contract(market, monkeypatch, tmp_path):
    close, volume = market
    monkeypatch.setattr(evolution, "CRISES", CRISES)
    g = GuardConfig(lock_file=str(tmp_path / "x.lock"), db_file=str(tmp_path / "x.db"))
    state, _n = _paper(close, volume)
    r = jumeau.evaluate(g, state, close, volume, NOW, synthetic=True, start=START)
    assert r["data"].startswith("synthétiques ") and r["reproducible"]
    q = r["quality"]
    expected = "READY" if r["monte_carlo"]["converged"] else "REJECTED"
    assert q["status"] == expected and not q["p0"], q
    rep = jumeau.report_of(r)
    assert contrats.validate("TwinReport", rep.as_dict()).valid and rep.synthetic_data and not rep.production_write
    text = jumeau.render(r)
    assert "## Monte-Carlo (blocs de 30 jours, trois ans)" in text and "synthétiques" in text
    blind = jumeau.evaluate(g, {}, None, None, NOW)
    assert blind["quality"]["status"] == "REJECTED" and blind["monte_carlo"] is None
    assert jumeau.report_of(blind).monte_carlo == "NOT_RUN"
    light = jumeau.evaluate(g, state, close, volume, NOW, light=True)
    assert light["sync"]["status"] == "OK" and light["monte_carlo"] is None
    good = dataclasses.asdict(rep)
    for change in ({"status": "READY", "monte_carlo": "MONTE_CARLO_NOT_CONVERGED", "readiness_score": 100.0},
                   {"status": "READY", "monte_carlo": "CONVERGED", "p0_failures": ("isolation",)},
                   {"production_write": True}, {"sync_ratio": 1.5}, {"data": " "}, {"readiness_score": 101.0}):
        with pytest.raises(ContractError):
            TwinReport(**{**good, **change})
    assert TwinReport(**{**good, "status": "READY", "monte_carlo": "CONVERGED", "readiness_score": 100.0,
                         "p0_failures": ()})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path, market):
    r = asst.local_answer("parle moi du jumeau numerique", {})["answer"]
    assert "Jumeau numérique" in r and "Monte-Carlo" in r and "je n'agis pas" in r
    g = GuardConfig(lock_file=str(tmp_path / "x.lock"), db_file=str(tmp_path / "x.db"))
    line = next(x for x in report_health.analysis_checks(g, {}) if x["label"] == "Jumeau numérique")
    assert "jumeau du paper" in line["detail"]
    from trendguard import config, systeme
    close, volume = market
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: g)
    monkeypatch.setattr(systeme, "read_state", lambda path: {})
    monkeypatch.setattr(evolution, "load_history", lambda *a, **k: (close, volume))
    monkeypatch.setattr(evolution, "CRISES", CRISES)
    code = jumeau.main([])
    assert code in (0, 1) and "## Monte-Carlo (blocs de 30 jours, trois ans)" in capsys.readouterr().out
