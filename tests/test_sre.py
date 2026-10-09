"""Prompt maître, étape 32 (docs/SRE.md) : fiabilité et autoréparation
encadrée. Anomalies mesurées avec leur référence ; prévisions qui restent des
prévisions ; capacité ; rayon d'impact ; remédiations classées LOW à
CRITICAL, seules les LOW automatiques ; historique d'une mesure par jour ;
qualité et son contrat."""

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from panel import assistant as asst
from trendguard import contrats, controle, report_health, sre, systeme
from trendguard.bot_types import last_closed_day
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, SREReport

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
DAY = last_closed_day(NOW, 120)
OK_RES = {"disk_free": 120.0, "disk_total": 500.0, "memory_used": 8.0, "memory_limit": 32.0}


def _deps(**extra):
    return systeme.Deps(extra={"resources": OK_RES, "panel_up": True, **extra})


def _g(tmp_path):
    return GuardConfig(lock_file=str(tmp_path / "trendguard_paper.lock"), db_file=str(tmp_path / "trendguard_paper.db"))


def _state(**kw):
    st = {"last_cycle_ts": NOW.timestamp() - 30, "last_decision_day": DAY,
          "clock_synced_at": (NOW - timedelta(minutes=10)).isoformat(), "clock_offset_ms": 200,
          "qualite": {"day": DAY, "score": 100.0}, "report_day": NOW.date().isoformat(),
          "alerts_last": {"telegram": {"at": NOW.timestamp()}}, "last_watch_day": DAY, "savoir": {"day": DAY}}
    st.update(kw)
    return st


def _stops(n, hour=6, days_back=0, hours_long=2.0):
    out = []
    for k in range(n):
        start = (NOW - timedelta(days=days_back + k)).replace(hour=hour, minute=0)
        out.append({"start": start.timestamp(), "end": start.timestamp() + hours_long * 3600, "cause": "off"})
    return out


def test_anomalies_have_a_baseline_and_evidence():
    st = sre.stops(_state(uptime={"events": _stops(6) + _stops(1, days_back=10)}), NOW)
    assert st["week"] == 6 and st["anomaly"] and st["top_hour"] == 6
    an = sre.anomalies(_state(clock_offset_ms=2500, qualite={"score": 60, "text": "bougies manquantes"}),
                       {"disk_free": 3.0}, {"rpo_h": 40.0}, st)
    metrics = {a["metric"] for a in an}
    assert {"arrêts imprévus sur 7 jours", "écart d'horloge avec Binance", "place libre sur le disque",
            "âge de la dernière sauvegarde", "note des données du jour"} == metrics
    assert all(a["baseline"] and a["observed"] and a["severity"] in sre.LEVELS for a in an)
    calm = sre.anomalies(_state(), OK_RES, {"rpo_h": 5.0}, sre.stops(_state(), NOW))
    assert calm == []                                                    # rien de mesuré d'anormal : rien d'inventé
    user = sre.stops(_state(uptime={"events": [dict(e, cause="user") for e in _stops(6)]}), NOW)
    assert user["week"] == 0                                             # arrêts demandés : jamais comptés


def test_predictions_stay_predictions():
    rows = [{"day": (NOW.date() - timedelta(days=k)).isoformat(), "disk_free_gb": 10.0 + k * 0.5, "db_mb": 1.0}
            for k in range(10, -1, -1)]
    st = sre.stops(_state(uptime={"events": _stops(5)}), NOW)
    pr = sre.predictions(rows, st)
    disk = next(p for p in pr if p["failure"].startswith("disque plein"))
    assert disk["horizon_days"] == 16 and disk["kind"] == "PREDICTION"   # (10 − 2) / 0,5 jour
    stop = next(p for p in pr if p["failure"].startswith("arrêt imprévu"))
    assert 0 < stop["probability"] <= 1 and stop["kind"] == "PREDICTION"
    assert sre.predictions(rows[:2], sre.stops(_state(), NOW)) == []      # trop peu de mesures : aucune prévision


def test_remediations_never_self_authorize_beyond_low(tmp_path):
    g = _g(tmp_path)
    h = controle.health(g, _state(last_cycle_ts=NOW.timestamp() - 3600), NOW, _deps(resources={**OK_RES,
                                                                                               "disk_free": 1.0}),
                        {"count": 0, "rpo_h": None, "rto_s": None, "restore": "aucune sauvegarde", "ok": None})
    rem = sre.remediations(h)
    by = {x["service"]: x for x in rem}
    assert by["bot"]["level"] == "LOW" and by["bot"]["automatic"]        # le superviseur relance déjà le bot
    assert by["pc"]["level"] == "HIGH" and not by["pc"]["automatic"] and by["pc"]["authorized_by"] == "vous"
    assert all(not x["automatic"] for x in rem if x["level"] != "LOW")
    assert all(x["runbook"] and x["root_cause"] == "SUSPECTED" for x in rem)
    assert set(sre.REMEDIATION) == {s.sid for s in controle.SERVICES}
    br = sre.blast_radius("base")
    assert br["level"] == "CRITICAL" and "bot" in br["affected"]


def test_history_and_capacity(tmp_path):
    g = _g(tmp_path)
    for k in range(3):
        rows = sre.remember(g, {"disk_free_gb": 50.0 - k, "db_mb": 1.0 + k}, NOW + timedelta(days=k))
    assert len(rows) == 3 and sre.remember(g, {"disk_free_gb": 47.0, "db_mb": 3.5}, NOW + timedelta(days=2))[-1][
        "disk_free_gb"] == 47.0                                          # une mesure par jour, la dernière gagne
    cap = sre.capacity(rows, OK_RES)
    assert cap["db_growth_mb_day"] == 1.0 and cap["headroom_gb"] == 115.0


def test_the_quality_and_its_contract(tmp_path):
    g = _g(tmp_path)
    r = sre.evaluate(g, _state(uptime={"events": _stops(5)}), NOW, _deps())
    q = r["quality"]
    assert q["status"] == "READY" and not q["p0"], q["checks"]
    rep = sre.report_of(r)
    assert contrats.validate("SREReport", rep.as_dict()).valid
    assert "## Remédiations proposées" in sre.render(r)
    good = dataclasses.asdict(rep)
    for change in ({"predictions": (("disque plein", "CERTAIN"),)}, {"remediations": (("base", "CRITICAL", True),)},
                   {"remediations": (("pc", "MEDIUM", True),)}, {"anomalies": -1}, {"status": "VALIDATING"}):
        with pytest.raises(ContractError):
            SREReport(**{**good, **change})
    assert SREReport(**{**good, "remediations": (("bot", "LOW", True),)})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    r = asst.local_answer("pourquoi le pc s arrete et que prevois tu", {})["answer"]
    assert "Fiabilité" in r and "prévision" in r and "je n'agis pas" in r
    g = _g(tmp_path)
    line = next(x for x in report_health.analysis_checks(g, {}) if x["label"] == "Fiabilité (SRE)")
    assert "arrêt" in line["detail"]
    from trendguard import config
    from trendguard import systeme as sy
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: g)
    monkeypatch.setattr(sy, "read_state", lambda path: _state())
    code = sre.main([])
    assert code in (0, 1) and "## Capacité" in capsys.readouterr().out
