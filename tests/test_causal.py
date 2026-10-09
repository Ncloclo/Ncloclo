"""Prompt maître, étape 26 (docs/CAUSAL.md) : intelligence causale. Graphe
causal de la règle écrit, versionné, sans cycle ; interventions do() dans le
simulateur, époque par époque, avec témoin négatif ; paradoxe de Simpson ;
niveaux de preuve plafonnés sans expérience réelle ; causes racines ;
qualité et son contrat."""

import dataclasses
from datetime import datetime, timezone

import pytest

from panel import assistant as asst
from test_trendguard import synthetic_market
from trendguard import causal, contrats, report_health
from trendguard import trend_strategy as ts
from trendguard.config import GuardConfig
from trendguard.contrats import CausalReport, ContractError

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
EPOCHS = (("2024-10-01", "2025-03-31"), ("2025-04-01", ""))


@pytest.fixture(scope="module")
def measured():
    close, volume = synthetic_market()
    return causal.interventions(close, volume, ts.TrendParams(), EPOCHS)


def test_the_causal_graph_is_written_versioned_and_acyclic(monkeypatch):
    assert causal.acyclic() and len(causal.EDGES) >= 8
    assert all(i in causal.INTERVENTIONS or i == "" for _a, _b, _m, i in causal.EDGES)
    v = causal.graph_version()
    assert v == causal.graph_version() and len(v) == 12
    loop = causal.EDGES + (("résultat net", "tendance de BTC", "boucle", ""),
                           ("permission d'acheter", "résultat net", "", ""))
    assert not causal.acyclic(loop)
    monkeypatch.setattr(causal, "EDGES", causal.EDGES[:-1])
    assert causal.graph_version() != v                                    # un lien changé : nouvelle version


def test_interventions_in_the_simulator(measured):
    assert measured["reproducible"]
    placebo = measured["effects"]["temoin"]["per_epoch"]
    assert all(all(v[m] == 0 for m in causal.METRICS) for v in placebo.values())     # témoin négatif : effet nul
    fees = measured["effects"]["frais_doubles"]["per_epoch"]
    assert all(v["cagr_pct"] <= 1e-9 for v in fees.values())            # payer plus ne rend jamais plus
    assert set(measured["effects"]) == set(causal.INTERVENTIONS)
    assert len(measured["base"]) == 2


def test_evidence_levels_are_capped_without_a_real_experiment(measured):
    ev = causal.evidence(measured["effects"])
    assert all(e["level"] <= causal.MAX_SIM_LEVEL for e in ev) and all(e["why"] for e in ev)
    unproven = [e for e in ev if not e["intervention"]]
    assert unproven and all(e["level"] == 3 for e in unproven)           # écrit, pas éprouvé : hypothèse
    fake = {"frais_doubles": {"label": "do(frais)", "per_epoch": {
        "a": {"cagr_pct": -1.0, "_simpson": {"reversal": False}}, "b": {"cagr_pct": -2.0, "_simpson": {"reversal": False}}}}}
    lvl = {e["intervention"]: e["level"] for e in causal.evidence(fake)}
    assert lvl["frais_doubles"] == 4
    unstable = {"frais_doubles": {"label": "do(frais)", "per_epoch": {
        "a": {"cagr_pct": -1.0, "_simpson": {"reversal": False}}, "b": {"cagr_pct": 0.5, "_simpson": {"reversal": False}}}}}
    assert {e["intervention"]: e["level"] for e in causal.evidence(unstable)}["frais_doubles"] == 3
    flipped = {"frais_doubles": {"label": "do(frais)", "per_epoch": {
        "a": {"cagr_pct": -1.0, "_simpson": {"reversal": True}}, "b": {"cagr_pct": -2.0, "_simpson": {"reversal": False}}}}}
    assert {e["intervention"]: e["level"] for e in causal.evidence(flipped)}["frais_doubles"] == 3


def test_simpson_paradox_is_found():
    control = [("2024", 0.0)] + [("2025", 2.0)] * 9
    treated = [("2024", 0.5)] * 9 + [("2025", 2.5)]
    s = causal.simpson(control, treated)
    assert s["reversal"] and s["pooled"] < 0 and s["years"] == 2
    assert not causal.simpson([("2024", 1.0)], [("2024", 2.0)])["reversal"]


def test_root_causes_of_incidents(tmp_path):
    g = GuardConfig(lock_file=str(tmp_path / "x.lock"), db_file=str(tmp_path / "x.db"))
    roots = causal.root_causes(g, {"halted": True, "halt_reason": "UNKNOWN_BOT_ORDERS"})
    assert any(x["root"] == "urgence" and x["severity"] == "P0" for x in roots)
    assert all({"incident", "root", "affected", "runbook"} <= set(x) for x in roots)


def test_the_quality_and_its_contract(tmp_path):
    close, volume = synthetic_market()
    g = GuardConfig(lock_file=str(tmp_path / "x.lock"), db_file=str(tmp_path / "x.db"))
    r = causal.evaluate(g, {}, close, volume, NOW, EPOCHS)
    assert r["quality"]["status"] == "READY" and not r["quality"]["p0"], r["quality"]
    rep = causal.report_of(r)
    assert contrats.validate("CausalReport", rep.as_dict()).valid and not rep.real_experiment
    assert "## Le graphe causal de la règle et ses preuves" in causal.render(r)
    blind = causal.evaluate(g, {}, None, None, NOW)
    assert blind["quality"]["status"] == "REJECTED" and blind["interventions"] is None
    good = dataclasses.asdict(rep)
    for change in ({"links": (("a", "b", 6),)}, {"links": (("a", "b", 9),)}, {"readiness_score": 101.0},
                   {"p0_failures": ("graph",)}):
        with pytest.raises(ContractError):
            CausalReport(**{**good, **change})
    assert CausalReport(**{**good, "links": (("a", "b", 6),), "real_experiment": True})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    r = asst.local_answer("qu est ce qui cause les pertes", {})["answer"]
    assert "Intelligence causale" in r and "corrélation" in r and "je n'agis pas" in r
    g = GuardConfig(lock_file=str(tmp_path / "x.lock"), db_file=str(tmp_path / "x.db"))
    line = next(x for x in report_health.analysis_checks(g, {}) if x["label"] == "Intelligence causale")
    assert "liens causaux" in line["detail"]
    from trendguard import config, evolution, systeme
    close, volume = synthetic_market()
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: g)
    monkeypatch.setattr(systeme, "read_state", lambda path: {})
    monkeypatch.setattr(evolution, "load_history", lambda *a, **k: (close, volume))
    monkeypatch.setattr(causal, "EPOCHS", EPOCHS)
    code = causal.main([])
    assert code in (0, 1) and "## Interventions dans le simulateur" in capsys.readouterr().out
