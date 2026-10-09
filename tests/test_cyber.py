"""Prompt maître, étape 20 (docs/CYBERSECURITE.md) : cybersécurité et
autodéfense. Inventaire sans secret ; chaque adresse Internet du code sur la
liste blanche ; intégrité du code et versions des bibliothèques ;
événements et réponses prévues ; jamais offensif ; examen AC-001 à AC-070
et son contrat."""

import dataclasses
from datetime import datetime, timezone

import pytest

from panel import assistant as asst
from trendguard import contrats, cyber, report_health, systeme
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, SecurityPostureReport

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)


def _g(tmp_path):
    return GuardConfig(lock_file=str(tmp_path / "trendguard_paper.lock"), db_file=str(tmp_path / "trendguard_paper.db"))


def _run(out):
    def run(cmd, cwd=None, **k):
        text = out.get(cmd[1], "")
        return type("R", (), {"returncode": 0, "stdout": text})()
    return run


# ---------- Inventaire, sorties (§6, §17) ----------

def test_the_inventory_names_every_asset_without_a_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "cle-de-test-tres-secrete")
    monkeypatch.setenv("BINANCE_API_SECRET", "secret-de-test-tres-secret")
    inv = cyber.inventory(_g(tmp_path), {"alerts_last": {"telegram": {}}})
    names = {a["asset"] for a in inv}
    assert {"code", "base du bot", "journal d'audit", "fichier des secrets (.env)", "clé API Binance"} <= names
    text = repr(inv)
    assert "cle-de-test" not in text and "secret-de-test" not in text           # présence, jamais la valeur
    assert next(a for a in inv if a["asset"] == "clé API Binance")["status"] == "présente"
    monkeypatch.setattr(cyber, "is_exposed", lambda v: v == "cle-de-test-tres-secrete")
    assert next(a for a in cyber.inventory(_g(tmp_path), {}) if a["asset"] == "clé API Binance")["status"] == "EXPOSÉE"


def test_every_outbound_host_is_on_the_allowlist(tmp_path):
    eg = cyber.egress()
    assert eg["ok"] and eg["hosts"] and not eg["unknown"], eg["unknown"]
    assert {"api.binance.com", "api.github.com"} & set(eg["hosts"]) and "courtier" in eg["categories"]
    for d in cyber.SCANNED:
        (tmp_path / d).mkdir()
    (tmp_path / "trendguard" / "fuite.py").write_text('URL = "https://exfiltration.example.net/x"\n', encoding="utf-8")
    bad = cyber.egress(tmp_path)
    assert not bad["ok"] and bad["unknown"] == ["exfiltration.example.net"]
    assert bad["where"]["exfiltration.example.net"] == ["trendguard/fuite.py"]


# ---------- Intégrité, bibliothèques (§23) ----------

def test_code_integrity_and_libraries(tmp_path):
    clean = cyber.integrity(systeme.Deps(run=_run({"rev-parse": "abc1234\n", "status": ""})))
    assert clean["ok"] and clean["commit"] == "abc1234"
    dirty = cyber.integrity(systeme.Deps(run=_run({"rev-parse": "abc1234\n", "status": " M trendguard/porte.py\n"})))
    assert dirty["ok"] is False and dirty["modified"] == ["trendguard/porte.py"]

    def broken(cmd, cwd=None, **k):
        raise OSError("git absent")

    assert cyber.integrity(systeme.Deps(run=broken))["ok"] is None             # non mesuré, jamais une réussite
    (tmp_path / "requirements-docker.txt").write_text("numpy==1.26.4\npandas==2.2.2\n", encoding="utf-8")
    ok = cyber.supply_chain(systeme.Deps(extra={"installed": {"numpy": "1.26.4", "pandas": "2.2.2"}}), tmp_path)
    assert ok["ok"] and not ok["drift"] and ok["pinned"] == 2
    drift = cyber.supply_chain(systeme.Deps(extra={"installed": {"numpy": "2.0.0", "pandas": None}}), tmp_path)
    assert drift["drift"] == ["numpy"] and drift["missing"] == ["pandas"] and drift["ok"] is False


# ---------- Événements, réponses, jamais offensif (§24-33, §47) ----------

def test_events_and_responses():
    report = {"sections": [{"title": "Sécurité", "checks": [
        {"label": "Clé API Binance", "ok": False, "detail": "refusée : adresse 192.0.2.7"},
        {"label": "Fichier des secrets", "ok": True, "detail": ""}]}]}
    ev = cyber.events({"halted": True, "halt_reason": "UNKNOWN_BOT_ORDERS"}, report)
    assert {e["severity"] for e in ev} == {"P0", "P1"} and "192.0.2.7" not in repr(ev)   # jamais le détail
    assert all(len(r) == 3 and r[1] and r[2] for r in cyber.RESPONSES)
    assert any("mode sûr" in r[2] or "mode-sur" in r[2] for r in cyber.RESPONSES)


def test_never_offensive():
    assert cyber.offensive_capabilities() == []
    assert all(cat != "attaque" for _u, cat in cyber.ALLOWLIST.values())


# ---------- Examen AC-001 à AC-070, contrat (§44-45, §49) ----------

def test_the_examination_and_its_contract(tmp_path):
    deps = systeme.Deps(run=_run({"rev-parse": "abc1234\n", "status": ""}), extra={"installed": {}})
    r = cyber.evaluate(_g(tmp_path), {}, NOW, deps, report={})
    assert len(r["rows"]) == 70 and len({x["id"] for x in r["rows"]}) == 70
    assert not r["p0_failures"], r["p0_failures"]
    assert (r["status"] == "READY_FOR_PRODUCTION_SECURITY") == (r["score"] >= 95)
    rep = cyber.report_of(r)
    assert contrats.validate("SecurityPostureReport", rep.as_dict()).valid and not rep.offensive
    text = cyber.render(r)
    assert "## Actifs" in text and "## Réponse prévue" in text and "AC-070" in text
    good = dataclasses.asdict(rep)
    for change in ({"offensive": True}, {"passed": rep.passed - 1}, {"band": "NOT_READY"}):
        with pytest.raises(ContractError):
            SecurityPostureReport(**{**good, **change})
    if rep.status != "NOT_READY":
        for change in ({"unknown_hosts": ("x.example.net",)}, {"events": (("ordre inconnu", "P0"),)}):
            with pytest.raises(ContractError):
                SecurityPostureReport(**{**good, **change})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    r = asst.local_answer("la cybersecurite du bot", {"cyber": {"text": "11 actifs"}})["answer"]
    assert "Cybersécurité" in r and "jamais offensif" in r and "je n'agis pas" in r
    line = next(x for x in report_health.analysis_checks(_g(tmp_path), {}) if x["label"] == "Cybersécurité")
    assert "adresses Internet" in line["detail"]
    from trendguard import config
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: _g(tmp_path))
    monkeypatch.setattr(systeme, "read_state", lambda path: {})
    code = cyber.main([])
    assert code in (0, 1) and "## Sorties vers Internet" in capsys.readouterr().out
