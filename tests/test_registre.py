"""Registre des expériences (docs/PLATEFORME.md, prompt maître §41-42 et
§50) : la version du code (empreinte git) est lue par l'accès système
partagé (`systeme.git`), comme les autres modules qui lisent l'état de git
(controle.py, cyber.py, ingenierie.py, maintenance.py, report_security.py),
au lieu d'un appel direct à `subprocess` propre à ce module."""

from trendguard import registre, systeme


def test_git_version_strips_the_output_of_systeme_git(monkeypatch):
    monkeypatch.setattr(systeme, "git", lambda deps, root, *args: "abc1234\n")
    assert registre.git_version("/depot") == "abc1234"


def test_git_version_keeps_the_dirty_suffix(monkeypatch):
    monkeypatch.setattr(systeme, "git", lambda deps, root, *args: "abc1234-dirty\n")
    assert registre.git_version("/depot") == "abc1234-dirty"


def test_git_version_unknown_when_systeme_git_fails(monkeypatch):
    monkeypatch.setattr(systeme, "git", lambda deps, root, *args: None)
    assert registre.git_version("/pas-un-depot") == "inconnue"


def test_git_version_unknown_on_empty_output(monkeypatch):
    monkeypatch.setattr(systeme, "git", lambda deps, root, *args: "  \n")
    assert registre.git_version("/depot") == "inconnue"


def test_git_version_passes_the_describe_arguments_and_root(monkeypatch):
    calls = []

    def fake_git(deps, root, *args):
        calls.append((root, args))
        return "abc1234"

    monkeypatch.setattr(systeme, "git", fake_git)
    registre.git_version("/mon/depot")
    assert calls == [("/mon/depot", ("describe", "--always", "--dirty", "--abbrev=7"))]
