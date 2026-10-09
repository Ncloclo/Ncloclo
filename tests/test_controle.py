"""Prompt maître, étape 18 (docs/PLAN_DE_CONTROLE.md) : plan de contrôle de
la production. Santé de chaque service mesurée, jamais supposée ; graphe des
dépendances et points uniques de défaillance ; une panne, un incident, avec
sa procédure ; cycle de vie des incidents ; objectifs de service et budgets
d'erreur ; RPO et RTO mesurés ; superviseur borné ; examen AC-001 à AC-060
et son contrat."""

import dataclasses
import os
from datetime import datetime, timedelta, timezone

import pytest

from panel import assistant as asst
from trendguard import autorisation, contrats, controle, report_health, systeme
from trendguard.bot_types import last_closed_day
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, ControlPlaneReport

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)
DAY = last_closed_day(NOW, 120)
OK_RES = {"disk_free": 120.0, "disk_total": 500.0, "memory_used": 8.0, "memory_limit": 32.0}


def _deps(**extra):
    return systeme.Deps(extra={"resources": OK_RES, "panel_up": True, **extra})


def _g(tmp_path, **kw):
    return GuardConfig(lock_file=str(tmp_path / "trendguard_paper.lock"), db_file=str(tmp_path / "trendguard_paper.db"),
                       **kw)


def _state(**kw):
    st = {"last_cycle_ts": NOW.timestamp() - 30, "last_decision_day": DAY,
          "clock_synced_at": (NOW - timedelta(minutes=10)).isoformat(),
          "qualite": {"day": DAY, "score": 100.0}, "report_day": NOW.date().isoformat(),
          "alerts_last": {"telegram": {"at": NOW.timestamp()}}, "last_watch_day": DAY, "savoir": {"day": DAY}}
    st.update(kw)
    return st


NO_DR = {"count": 0, "rpo_h": None, "rto_s": None, "restore": "aucune sauvegarde", "ok": None}


# ---------- Santé (§7) ----------

def test_health_of_each_service_is_measured_never_assumed(tmp_path):
    g = _g(tmp_path)
    h = controle.health(g, _state(), NOW, _deps(), NO_DR)
    assert set(h) == {s.sid for s in controle.SERVICES}
    assert h["bot"]["status"] == "HEALTHY" and h["pc"]["status"] == "HEALTHY" and h["binance"]["status"] == "HEALTHY"
    assert h["sauvegarde"]["status"] == "UNKNOWN" and h["journal"]["status"] == "UNKNOWN"   # rien à mesurer : jamais OK
    stale = controle.health(g, _state(last_cycle_ts=NOW.timestamp() - 3600), NOW, _deps(), NO_DR)
    assert stale["bot"]["status"] == "DOWN" and not stale["bot"]["live"]
    late = controle.health(g, _state(last_decision_day="2026-10-01"), NOW, _deps(), NO_DR)
    assert late["bot"]["status"] == "DEGRADED" and late["bot"]["live"] and late["bot"]["semantic"] is False
    full = controle.health(g, _state(), NOW, _deps(resources={**OK_RES, "disk_free": 1.0}), NO_DR)
    assert full["pc"]["status"] == "DOWN"
    blind = controle.health(g, _state(), NOW, _deps(resources=None, panel_up=False), NO_DR)
    assert blind["pc"]["status"] == "UNKNOWN" and blind["panneau"]["status"] == "DOWN"
    mail = controle.health(g, _state(alerts_last={"email": {"error": "mot de passe refusé"}}), NOW, _deps(), NO_DR)
    assert mail["alertes"]["status"] == "DEGRADED" and "email" in mail["alertes"]["detail"]
    assert "mot de passe" not in mail["alertes"]["detail"]                # la cause reste dans le panneau
    bad = controle.health(g, _state(politique={"day": DAY, "mismatch": ["eth"]}), NOW, _deps(), NO_DR)
    assert bad["porte"]["status"] == "DEGRADED"


# ---------- Dépendances, incidents (§6, §11-13) ----------

def test_the_dependency_graph_and_single_points():
    gr = controle.graph()
    assert not gr["cycles"] and not gr["unknown"]
    assert {"pc", "base", "binance", "internet"} <= set(gr["closure"]["bot"])
    assert gr["spof"][0] == "pc" and "internet" in gr["spof"] and set(gr["external"]) == {"binance", "internet"}
    loop = (controle.Service("a", "A", 1, ("b",)), controle.Service("b", "B", 1, ("a",)),
            controle.Service("c", "C", 2, ("z",)))
    bad = controle.graph(loop)
    assert bad["cycles"] and bad["unknown"] == ["z"]


def test_one_failure_one_incident_with_its_procedure(tmp_path):
    g = _g(tmp_path)
    h = controle.health(g, _state(), NOW, _deps(), NO_DR)
    h["binance"] = {"status": "DOWN", "detail": "injoignable", "live": False, "ready": False, "semantic": False}
    for sid in ("bot", "donnees", "risque"):
        h[sid] = {**h[sid], "status": "DEGRADED"}
    inc = controle.incidents(h, {}, NOW)
    services = [i["service"] for i in inc]
    assert "binance" in services and not {"bot", "donnees", "risque"} & set(services)     # une panne, un incident
    b = next(i for i in inc if i["service"] == "binance")
    assert b["severity"] == "P1" and {"bot", "donnees", "risque"} <= set(b["affected"])
    assert b["runbook"] in controle.RUNBOOKS
    h["base"] = {**h["base"], "status": "DOWN", "detail": "abîmée"}
    inc = controle.incidents(h, {"halted": True, "halt_reason": "UNKNOWN_BOT_ORDERS"}, NOW)
    assert inc[0]["severity"] == "P0" and {i["service"] for i in inc if i["severity"] == "P0"} == {"base", "urgence"}
    assert all(i["runbook"] in controle.RUNBOOKS and i["id"].startswith("INC-") for i in inc)


def test_incidents_open_and_close(tmp_path):
    g = _g(tmp_path)
    one = [{"id": "INC-1", "service": "bot", "severity": "P1", "what": "bot en panne", "detail": "", "runbook": "RB-BOT-ARRETE"}]
    first = controle.record(g, one, NOW)
    assert first["items"]["INC-1"]["detected_at"] and not first["items"]["INC-1"].get("closed_at")
    later = controle.record(g, [], NOW + timedelta(hours=1))
    assert later["items"]["INC-1"]["closed_at"] == (NOW + timedelta(hours=1)).isoformat(timespec="seconds")
    assert os.path.exists(controle.incidents_path(g))
    again = controle.record(g, one, NOW + timedelta(hours=2))
    assert not again["items"]["INC-1"].get("closed_at")                   # rouvert


def test_runbooks_are_versioned_and_cover_every_service():
    assert set(controle.SERVICE_RUNBOOK.values()) <= set(controle.RUNBOOKS)
    for sid in ("bot", "urgence", "audit", "journal", "base", "sauvegarde", "binance", "donnees", "porte", "risque"):
        assert sid in controle.SERVICE_RUNBOOK, sid
    v = controle.runbook_version("RB-BOT-ARRETE")
    assert v == controle.runbook_version("RB-BOT-ARRETE") and len(v) == 12
    saved = controle.RUNBOOKS["RB-BOT-ARRETE"]
    try:
        controle.RUNBOOKS["RB-BOT-ARRETE"] = {**saved, "steps": saved["steps"] + ("une étape de plus",)}
        assert controle.runbook_version("RB-BOT-ARRETE") != v              # procédure changée : nouvelle version
    finally:
        controle.RUNBOOKS["RB-BOT-ARRETE"] = saved


# ---------- Objectifs de service, reprise (§8-9, §25-26) ----------

def test_service_objectives_and_error_budgets(tmp_path):
    g = _g(tmp_path)
    t = NOW.timestamp()
    up = {"since": t - 8 * 86400, "events": [{"start": t - 86400, "end": t - 86400 + 3 * 3600, "cause": "off"}]}
    st = _state(uptime=up)
    rows = controle.slos(g, st, controle.health(g, st, NOW, _deps(), NO_DR), NOW)
    dispo = next(r for r in rows if r["id"] == "SLO-DISPONIBILITE")
    assert dispo["value"] < 99 and not dispo["ok"] and dispo["burn"] > 1 and "geler" in dispo["decision"]
    calm = controle.slos(g, _state(), controle.health(g, _state(), NOW, _deps(), NO_DR), NOW)
    assert next(r for r in calm if r["id"] == "SLO-DECISION")["ok"]
    assert next(r for r in calm if r["id"] == "SLO-SAUVEGARDE")["value"] is None      # pas de sauvegarde : non mesuré


def test_recovery_point_and_time_are_measured(tmp_path):
    g = _g(tmp_path)
    assert controle.recovery(g, NOW.timestamp())["count"] == 0
    (tmp_path / "sauvegardes").mkdir()
    f = tmp_path / "sauvegardes" / "trendguard_paper-2026-10-08.db"
    f.write_bytes(b"")
    os.utime(f, (NOW.timestamp() - 3600, NOW.timestamp() - 3600))
    r = controle.recovery(g, NOW.timestamp(), lambda p: "restauration essayée")
    assert r["count"] == 1 and abs(r["rpo_h"] - 1.0) < 0.01 and r["rto_s"] is not None and r["ok"]
    assert not controle.recovery(g, NOW.timestamp(), lambda p: "ÉCHEC de la restauration d'essai")["ok"]
    assert not controle.recovery(g, NOW.timestamp() + 2 * 86400, lambda p: "restauration essayée")["ok"]   # trop vieille
    light = controle.recovery(g, NOW.timestamp(), None)
    assert light["rto_s"] is None and light["ok"]


# ---------- Superviseur borné, changements (§16-20, §44) ----------

def test_the_supervisor_is_bounded():
    sup = controle.supervisor_bounds(NOW)
    assert sup["ok"] and sup["no_l5"] and {"RESTART_BOT", "SAFE_MODE_ON"} <= set(sup["allowed"])
    for act in ("CHANGE_RISK", "KILL_RESET", "SAFE_MODE_OFF", "AUTHORIZE_BUY", "TRADE_LIVE", "ARM_LIVE", "MERGE_CODE"):
        assert not autorisation.decide("superviseur", act, now=NOW).allowed, act
    assert autorisation.separation_of_duties() == []
    assert set(controle.AUTONOMY) >= set(autorisation.PRINCIPALS) - {"vous"} and controle.AUTONOMY["vous"] == "décide"


def test_changes_and_configuration_are_fingerprinted(tmp_path):
    g = _g(tmp_path)
    assert controle.config_fingerprint(g) == controle.config_fingerprint(_g(tmp_path))
    assert controle.config_fingerprint(g) != controle.config_fingerprint(dataclasses.replace(g, kill_drawdown=0.3))

    def fake_run(cmd, cwd=None, **k):
        out = "abc1234\n" if "rev-parse" in cmd else ""
        return type("R", (), {"returncode": 0, "stdout": out})()

    ch = controle.changes(g, systeme.Deps(run=fake_run))
    assert ch["commit"] == "abc1234" and ch["dirty"] is False and ch["versions"]["porte"].startswith("porte.")
    assert controle.changes(g, systeme.Deps(run=fake_run), light=True)["commit"] is None


# ---------- Examen AC-001 à AC-060, contrat (§45-47, §52) ----------

def test_the_examination_and_its_contract(tmp_path):
    g = _g(tmp_path)
    dr = {"count": 3, "rpo_h": 2.0, "rto_s": 0.2, "restore": "restauration essayée", "ok": True, "last": "x.db"}
    up = {"since": NOW.timestamp() - 8 * 86400, "events": []}
    r = controle.evaluate(g, _state(uptime=up), NOW, _deps(), dr)
    assert len(r["rows"]) == 60 and len({x["id"] for x in r["rows"]}) == 60
    assert r["status"] == "READY_FOR_PRODUCTION_CONTROLLED_OPERATIONS" and not r["p0_failures"], r["p0_failures"]
    rep = controle.report_of(r)
    assert contrats.validate("ControlPlaneReport", rep.as_dict()).valid and not rep.unrestricted_autonomy
    text = controle.render(r)
    assert "## Verdict" in text and "## Incidents" in text and "AC-060" in text and "il n'agit pas" in text
    good = dataclasses.asdict(rep)
    for change in ({"unrestricted_autonomy": True}, {"passed": rep.passed - 1}, {"band": "NOT_READY"},
                   {"incidents": (("INC-1", "P0"),)}, {"services_down": rep.services + 1}):
        with pytest.raises(ContractError):
            ControlPlaneReport(**{**good, **change})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    view = {"text": "17 service(s) sur 18 en bonne santé", "incidents": [
        {"severity": "P2", "what": "Alertes : dégradé", "detail": "email en pause"}]}
    r = asst.local_answer("le plan de controle", {"controle": view})["answer"]
    assert "Plan de contrôle" in r and "P2" in r and "je n'agis pas" in r
    line = next(x for x in report_health.analysis_checks(_g(tmp_path), _state()) if x["label"] == "Plan de contrôle")
    assert "service(s)" in line["detail"]
    from trendguard import config
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: _g(tmp_path))
    monkeypatch.setattr(systeme, "read_state", lambda path: _state())
    code = controle.main([])
    out = capsys.readouterr().out
    assert code in (0, 1) and "## Services" in out and os.path.exists(controle.incidents_path(_g(tmp_path)))
