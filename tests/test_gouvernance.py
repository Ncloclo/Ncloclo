"""Documents transverses du prompt maître (docs/GOUVERNANCE.md) : RACI
normalisée (exactement un responsable), frontières « X ≠ Y », une source de
vérité par donnée, frontières critiques vérifiées dans le code, zéro échec
silencieux, dix portes de qualité, note et son contrat."""

import dataclasses
from datetime import datetime, timezone

import pytest

from panel import assistant as asst
from trendguard import contrats, gouvernance, perception, porte_examen, report_health
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, GovernanceReport

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)


def _g(tmp_path):
    return GuardConfig(lock_file=str(tmp_path / "x.lock"), db_file=str(tmp_path / "x.db"))


def test_raci_exactly_one_accountable_and_separations():
    r = gouvernance.raci()
    assert r["ok"], r["problems"]
    assert r["count"] >= 30 and all(r["plans"][p] for p in gouvernance.PLANS)       # plans A à K, tous remplis
    rows = list(gouvernance.RACI)
    dup = gouvernance.raci(rows + [rows[0]])
    assert any("doublon" in p for p in dup["problems"])
    no_a = [(p, n, "" if i == 0 else a, rr, c, ii) for i, (p, n, a, rr, c, ii) in enumerate(rows)]
    assert any("exactement un A" in p for p in gouvernance.raci(no_a)["problems"])
    no_r = [(p, n, a, () if i == 0 else rr, c, ii) for i, (p, n, a, rr, c, ii) in enumerate(rows)]
    assert any("aucun R" in p for p in gouvernance.raci(no_r)["problems"])
    merged = [(p, n, "trendguard/politique.py" if n == "Autorité et permissions" else a, rr, c, ii)
              for p, n, a, rr, c, ii in rows]
    assert any("même responsable" in p for p in gouvernance.raci(merged)["problems"])   # politique ≠ autorisation
    ghost = rows + [("A", "Fantôme", "trendguard/absent.py", ("trendguard/absent.py",), (), ())]
    assert any("module absent" in p for p in gouvernance.raci(ghost)["problems"])


def test_one_system_of_record_per_data(tmp_path):
    so = gouvernance.system_of_record()
    assert so["ok"], so["problems"]
    twice = gouvernance.SYSTEM_OF_RECORD + (("Journal d'audit", "trendguard/donnees.py", "ailleurs"),)
    assert any("deux sources de vérité" in p for p in gouvernance.system_of_record(twice)["problems"])
    for d in ("trendguard", "panel"):
        (tmp_path / d).mkdir()
    (tmp_path / "trendguard" / "donnees.py").write_text("SQL = 'SELECT * FROM fin_trades'\n", encoding="utf-8")
    (tmp_path / "trendguard" / "intrus.py").write_text("SQL = 'DELETE FROM fin_trades'\n", encoding="utf-8")
    bad = gouvernance.system_of_record((("Journal financier", "trendguard/donnees.py", "base"),), tmp_path)
    assert bad["problems"] == ["trendguard/intrus.py touche aux tables du journal financier"]


def test_critical_boundaries_are_checked_in_the_code(monkeypatch):
    b = gouvernance.boundaries()
    assert b["ok"] and b["bypasses"] == 0, [r for r in b["rows"] if not r[1]]
    names = [n for n, _ok, _d in b["rows"]]
    assert "tout le code → Binance (achat)" in names and "risque → porte → autorisation" in names
    real = perception._imports
    monkeypatch.setattr(perception, "_imports", lambda p: real(p) | ({"bot_execution"} if p.name == "monde.py" else set()))
    monkeypatch.setattr(porte_examen, "routes", lambda root: {"ok": False, "calls": ["a", "b"], "ordered": False})
    bad = gouvernance.boundaries()
    assert bad["bypasses"] == 2 and not bad["ok"]
    assert ("monde → ordres", False, "importe bot_execution") in bad["rows"]


def test_zero_silent_failure(tmp_path):
    assert gouvernance.silent_failures() == []
    for d in ("trendguard", "panel"):
        (tmp_path / d).mkdir()
    (tmp_path / "trendguard" / "a.py").write_text(
        "try:\n    x()\nexcept Exception:\n    pass\n"
        "try:\n    x()\nexcept Exception:  # raison écrite\n    pass\n"
        "try:\n    x()\nexcept OSError:\n    pass\n"
        "for i in ():\n    try:\n        x()\n    except:\n        continue\n", encoding="utf-8")
    assert gouvernance.silent_failures(tmp_path) == ["trendguard/a.py:3", "trendguard/a.py:16"]


def test_quality_gates_and_readiness(tmp_path):
    every = {k: ("PASS", "") for k in gouvernance.GATES}
    assert gouvernance.readiness(every) == {"score": 100.0, "p0": [], "status": "READY"}
    waived = dict(every, PERFORMANCE=("WAIVED", ""), RELIABILITY=("FAIL", ""))
    assert gouvernance.readiness(waived)["score"] == round(800 / 9, 1)
    assert gouvernance.readiness(waived)["status"] == "VALIDATING"
    p0 = dict(every, SECURITY=("FAIL", ""))
    assert gouvernance.readiness(p0)["status"] == "BLOCKED"                     # un P0 : bloqué, quelle que soit la note
    r = gouvernance.evaluate(_g(tmp_path), {}, NOW)
    assert list(r["gates"]) == list(gouvernance.GATES)
    assert r["gates"]["RELIABILITY"][0] == "WAIVED" and r["gates"]["PERFORMANCE"][0] == "WAIVED"
    assert not r["readiness"]["p0"], r["gates"]
    up = {"uptime": {"since": NOW.timestamp() - 8 * 86400,
                     "events": [{"start": NOW.timestamp() - 86400, "end": NOW.timestamp(), "cause": "off"}]},
          "last_cycle_ts": NOW.timestamp()}
    low = gouvernance.gates(_g(tmp_path), up, NOW)
    assert low["RELIABILITY"][0] == "FAIL" and "objectif 99" in low["RELIABILITY"][1]


def test_the_report_contract(tmp_path):
    r = gouvernance.evaluate(_g(tmp_path), {}, NOW)
    rep = gouvernance.report_of(r)
    assert contrats.validate("GovernanceReport", rep.as_dict()).valid
    assert "## Les dix portes de qualité" in gouvernance.render(r) and "## Qui peut quoi" in gouvernance.render(r)
    good = dataclasses.asdict(rep)
    gates = tuple((k, "PASS") for k in contrats.QUALITY_GATES)
    base = {**good, "gates": gates, "readiness_score": 100.0, "p0_failures": (), "status": "READY", "bypasses": 0}
    assert GovernanceReport(**base)
    for change in ({"gates": gates[1:]}, {"gates": gates[:-1] + (("GOVERNANCE", "MAYBE"),)},
                   {"status": "CANDIDATE"}, {"bypasses": 1}, {"p0_failures": ("SECURITY",), "status": "BLOCKED"},
                   {"raci_violations": -1}, {"readiness_score": 101.0}):
        with pytest.raises(ContractError):
            GovernanceReport(**{**base, **change})
    failed = gates[:2] + (("SECURITY", "FAIL"),) + gates[3:]
    assert GovernanceReport(**{**base, "gates": failed, "p0_failures": ("SECURITY",), "status": "BLOCKED",
                               "readiness_score": 90.0})
    assert GovernanceReport(**{**base, "bypasses": 1, "status": "BLOCKED"})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    r = asst.local_answer("qui est responsable de quoi dans le bot", {})["answer"]
    assert "Gouvernance" in r and "un seul responsable" in r and "je n'agis pas" in r
    g = _g(tmp_path)
    line = next(x for x in report_health.analysis_checks(g, {}) if x["label"] == "Gouvernance")
    assert "portes de qualité" in line["detail"]
    from trendguard import config, systeme
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: g)
    monkeypatch.setattr(systeme, "read_state", lambda path: {})
    code = gouvernance.main([])
    assert code in (0, 1) and "## RACI normalisée" in capsys.readouterr().out
