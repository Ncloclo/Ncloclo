"""Prompt maître, étapes 30 et 31 (docs/INGENIERIE.md) : ingénierie et
exploitation. Changements tracés (git), tests et couverture par module,
chaîne de contrôle GitHub, versions figées contre installées, retour
arrière, auto-modification interdite (seule la mise à jour validée par vous
écrit), qualité et son contrat."""

import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from panel import assistant as asst
from trendguard import contrats, ingenierie, report_health, report_security, systeme
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, EngineeringReport

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
LOG = ("abc1234\x1fÉtape 30 du prompt : ingénierie et exploitation\x1fClovis\x1f2026-10-09T11:00:00Z\x1f"
       "- détail\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n\x1e"
       "def5678\x1fCorrection du rapport de la nuit (alertes)\x1fClovis\x1f2026-10-08T10:00:00Z\x1f\x1e"
       "0123abc\x1fx\x1fClovis\x1f2026-10-07T10:00:00Z\x1f\x1e")


def _deps(root, log=LOG, status="", env_tracked=""):
    def run(cmd, cwd=None):
        out = {"log": log, "rev-parse": "abc1234\n", "status": status, "ls-files": env_tracked}.get(cmd[1], "")
        return SimpleNamespace(returncode=0, stdout=out)
    return systeme.Deps(run=run, extra={"installed": report_security.read_pins(str(root))})


def test_changes_are_read_from_git():
    c = ingenierie.changes(_deps(ingenierie.ROOT))
    assert c["ok"] and len(c["commits"]) == 3 and c["ai"] == 1 and c["rollback"]
    assert c["traced"] == 2                                              # « x » n'explique rien
    assert c["steps"] == [30] and c["commits"][0]["commit"] == "abc1234"

    def broken(cmd, cwd=None):
        raise OSError("git absent")
    assert ingenierie.changes(systeme.Deps(run=broken))["ok"] is None   # non lu, jamais une réussite


def test_tests_and_the_github_pipeline():
    t = ingenierie.tests_inventory()
    assert t["functions"] > 500 and t["files"] > 50 and t["coverage"] >= 0.9
    assert not t["components_without_tests"]                             # hors composants en attente
    p = ingenierie.pipeline()
    assert p["ok"] and p["steps"]["style (ruff)"] and p["steps"]["tests (pytest)"] and p["timeout"] >= 30
    assert p["pins"]["total"] and not p["pins"]["loose"]                 # bibliothèques figées


def test_self_modification_is_refused(tmp_path):
    real = ingenierie.self_modification()
    assert real["ok"] and real["update_gated"] and not real["found"]
    assert set(real["writes"]) == {"trendguard/maintenance.py"}          # seule la mise à jour validée écrit
    (tmp_path / "trendguard").mkdir()
    (tmp_path / "trendguard" / "pirate.py").write_text(
        'def f(deps, root):\n    git(deps, root, "push", "origin")\n    run(["pip", "install", "x"])\n'
        '    open("trendguard/bot.py", "w")\n', encoding="utf-8")
    bad = ingenierie.self_modification(tmp_path)
    assert not bad["ok"] and not bad["update_gated"]
    text = " ; ".join(bad["found"])
    assert "git push" in text and "installation" in text and "écriture d'un fichier .py" in text


def test_the_quality_and_its_contract(tmp_path):
    g = GuardConfig(lock_file=str(tmp_path / "x.lock"), db_file=str(tmp_path / "x.db"))
    r = ingenierie.evaluate(g, _deps(ingenierie.ROOT), now=NOW)
    q = r["quality"]
    assert q["status"] == "READY" and not q["p0"], q["checks"]
    rep = ingenierie.report_of(r)
    assert contrats.validate("EngineeringReport", rep.as_dict()).valid and not rep.production_authority
    assert "## La chaîne de contrôle et la mise à jour" in ingenierie.render(r)
    dirty = ingenierie.evaluate(g, _deps(ingenierie.ROOT, status=" M trendguard/bot.py\n"), now=NOW)
    assert not dirty["quality"]["checks"]["security"][0]                 # code modifié hors Pull Request : vu
    good = dataclasses.asdict(rep)
    for change in ({"self_modification": ("trendguard/x.py : git push",)}, {"production_authority": True},
                   {"traced_share": 1.5}, {"status": "VALIDATING"}, {"p0_failures": ("correctness",)},
                   {"commits": -1}):
        with pytest.raises(ContractError):
            EngineeringReport(**{**good, **change})
    assert EngineeringReport(**{**good, "p0_failures": ("correctness",), "status": "NOT_READY",
                                "self_modification": ("trendguard/x.py : git push",)})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    r = asst.local_answer("comment le code du bot change", {})["answer"]
    assert "Ingénierie" in r and "Pull Request" in r and "je n'agis pas" in r
    g = GuardConfig(lock_file=str(tmp_path / "x.lock"), db_file=str(tmp_path / "x.db"))
    line = next(x for x in report_health.analysis_checks(g, {}) if x["label"] == "Ingénierie et exploitation")
    assert "tests" in line["detail"]
    from trendguard import config
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: g)
    code = ingenierie.main([])
    assert code in (0, 1) and "## Les tests" in capsys.readouterr().out
