"""Prompt maître, étape 27 (docs/OBJECTIFS.md) : objectifs et
planification. Mission décomposée en objectifs mesurables (critère, état,
qui agit, dépendances) ; chemin critique ; estimation en fourchette, jamais
une promesse ; vos actions ; aucune étape critique hors de vos mains ;
qualité et son contrat."""

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from panel import assistant as asst
from trendguard import autorisation, contrats, objectifs, report_health
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, PlanReport

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
GATE = {"open": False, "items": [("Portes 1 à 7", True, "franchies"),
                                 ("Essai paper : durée", False, "20 jour(s) sur 60 au moins"),
                                 ("Essai paper : trades clos", False, "3 sur 10 au moins"),
                                 ("Alertes configurées", True, "au moins un canal"),
                                 ("Vérification sans ordre", False, "à faire : python trendguard_bot.py verify")]}
STATE = {"started_at": (NOW - timedelta(days=20)).isoformat(), "trades": [{}] * 3}


def test_goals_are_measurable():
    gs = {g["id"]: g for g in objectifs.goals(GATE, STATE, NOW)}
    assert gs["Portes 1 à 7"]["status"] == "COMPLETED" and gs["Essai paper : durée"]["status"] == "EXECUTING"
    assert abs(gs["Essai paper : durée"]["progress"] - 20 / 60) < 1e-3 and gs["Essai paper : trades clos"]["progress"] == 0.3
    assert gs["Vérification sans ordre"]["status"] == "BLOCKED" and gs["Vérification sans ordre"]["who"] == "vous"
    assert gs["porte du réel"]["status"] == "PLANNING" and gs["réel simulé (testnet)"]["status"] == "PLANNING"
    assert all(g["criterion"] and g["who"] and g["status"] in objectifs.STATES for g in gs.values())
    opened = {g["id"]: g for g in objectifs.goals({**GATE, "open": True, "items": [("Portes 1 à 7", True, "ok")]},
                                                  STATE, NOW)}
    assert opened["porte du réel"]["status"] == "COMPLETED" and opened["réel simulé (testnet)"]["status"] == "APPROVAL_PENDING"
    acc = {g["id"]: g for g in objectifs.goals(GATE, STATE, NOW, {"status": "BLOCKED", "missing": ["observation"]})}
    assert acc["paper accepté (AC-001 à AC-044)"]["status"] == "EXECUTING"


def test_the_estimate_is_a_range_never_a_promise():
    e = objectifs.eta(STATE, NOW)
    assert e["kind"] == "PREDICTION" and e["guarantee"] is False
    assert e["days_left"] == 40.0 and e["trades_left"] == 7
    lo, mid, hi = e["range_days"]
    assert 40 <= lo <= mid <= hi and hi > 2 * 40 and e["critical"] == "trades clos de l'essai paper"
    done = objectifs.eta({"started_at": (NOW - timedelta(days=70)).isoformat(), "trades": [{}] * 12}, NOW)
    assert done["range_days"] == (0, 0, 0)
    slow = objectifs.eta({"started_at": (NOW - timedelta(days=1)).isoformat(), "trades": [{}] * 9}, NOW)
    assert slow["critical"] == "durée de l'essai paper"


def test_critical_path_and_owner_actions():
    gs = objectifs.goals(GATE, STATE, NOW)
    path = objectifs.critical_path(gs)
    assert path[-1] == "réel contrôlé" and "porte du réel" in path and path[0] in (
        "Essai paper : durée", "Essai paper : trades clos", "Vérification sans ordre")
    acts = objectifs.owner_actions(gs)
    assert acts == ["Vérification sans ordre : à faire : python trendguard_bot.py verify"]


def test_no_plan_takes_a_critical_step(monkeypatch):
    ps = objectifs.plan_safety()
    assert ps["ok"] and ps["auto_execute"] is False and all(h == ["vous"] for _l, _a, h in ps["steps"])
    monkeypatch.setitem(autorisation.ROLES, "EVOLUTION", {**autorisation.ROLES["EVOLUTION"], "ARM_LIVE": ()})
    assert not objectifs.plan_safety()["ok"]


def test_the_quality_and_its_contract():
    r = objectifs.evaluate(GuardConfig(), STATE, NOW, gate=GATE)
    q = r["quality"]
    assert q["status"] == "READY_FOR_AUTONOMOUS_PLANNING" and q["score"] >= 95, q
    rep = objectifs.report_of(r)
    assert contrats.validate("PlanReport", rep.as_dict()).valid and not rep.auto_execute
    text = objectifs.render(r)
    assert "## Chemin critique" in text and "pas une garantie" in text and "Vérification sans ordre" in text
    good = dataclasses.asdict(rep)
    for change in ({"auto_execute": True}, {"eta_days": (50, 40, 60)}, {"goals": (("x", "MAGIQUE", "vous"),)},
                   {"p0_failures": ("plan",)}, {"mission": ""}):
        with pytest.raises(ContractError):
            PlanReport(**{**good, **change})
    broken = objectifs.evaluate(GuardConfig(), STATE, NOW, gate={"items": "pas une liste"})
    assert broken["goals"]


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    r = asst.local_answer("quand le bot pourra passer au reel", {"objectifs": {"text": "porte du réel : 7 sur 10"}})
    assert "Mission et objectifs" in r["answer"] and "pas une promesse" in r["answer"]
    g = GuardConfig(lock_file=str(tmp_path / "x.lock"), db_file=str(tmp_path / "x.db"))
    line = next(x for x in report_health.analysis_checks(g, {"started_at": STATE["started_at"]})
                if x["label"] == "Mission et objectifs")
    assert "porte du réel" in line["detail"]
    from trendguard import acceptation, chantiers, config, systeme
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: g)
    monkeypatch.setattr(systeme, "read_state", lambda path: STATE)
    monkeypatch.setattr(chantiers, "live_gate", lambda *a, **k: GATE)
    monkeypatch.setattr(acceptation, "evaluate", lambda *a, **k: {"status": "BLOCKED", "missing": ["observation"]})
    assert objectifs.main([]) == 0 and "Mission" in capsys.readouterr().out
