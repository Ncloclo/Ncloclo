"""Registre des expériences (docs/PLATEFORME.md) : empreinte de version du
code (`git_version`), lue comme tous les autres accès git du bot, par
`systeme.run` (sans fenêtre de console sous Windows)."""

import subprocess
from types import SimpleNamespace

from trendguard import registre, systeme


def test_git_version_reads_the_real_repository():
    version = registre.git_version()
    assert version != "inconnue" and len(version) >= 7


def test_git_version_delegates_to_systeme_run(monkeypatch):
    seen = {}

    def fake_run(cmd, cwd=None, timeout=None):
        seen["cmd"], seen["cwd"], seen["timeout"] = cmd, cwd, timeout
        return SimpleNamespace(stdout="abc1234-dirty\n")

    monkeypatch.setattr(systeme, "run", fake_run)
    assert registre.git_version("/un/depot") == "abc1234-dirty"
    assert seen["cmd"] == ["git", "describe", "--always", "--dirty", "--abbrev=7"]
    assert seen["cwd"] == "/un/depot" and seen["timeout"] == 10


def test_git_version_unknown_outside_a_repository(monkeypatch, tmp_path):
    def fails(cmd, cwd=None, timeout=None):
        raise subprocess.SubprocessError("pas un dépôt git")

    monkeypatch.setattr(systeme, "run", fails)
    assert registre.git_version(str(tmp_path)) == "inconnue"

    def missing(cmd, cwd=None, timeout=None):
        raise OSError("git absent")

    monkeypatch.setattr(systeme, "run", missing)
    assert registre.git_version(str(tmp_path)) == "inconnue"
