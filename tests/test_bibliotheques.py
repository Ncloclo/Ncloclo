"""Bibliothèques du bot : son environnement propre (dossier .venv, où toute
commande est relancée), le démarrage avec l'ordinateur qui n'en dépend pas,
et les deux contrôles du rapport quotidien (versions testées, failles
connues)."""

import json
import os
import runpy
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import trendguard_bot as tg
import v29
from trendguard import autonomy, environnement
from trendguard import report as rp
from trendguard import report_security as rps

PINS = {"ccxt": "4.5.84", "pandas": "3.0.6"}


def _own_env(root, windows, libraries=environnement.CORE_LIBRARIES):
    """Un environnement propre factice : ses Python et ses bibliothèques."""
    folder = root / ".venv" / ("Scripts" if windows else "bin")
    folder.mkdir(parents=True)
    for name in (("python.exe", "pythonw.exe") if windows else ("python",)):
        (folder / name).write_text("")
    site = root / ".venv" / ("Lib" if windows else "lib/python3.12") / "site-packages"
    for lib in libraries:
        (site / lib).mkdir(parents=True)
    return folder


def test_own_python_is_found_next_to_the_code(tmp_path):
    root, base = str(tmp_path), str(tmp_path / "Python313")
    assert environnement.own_python(root, r"C:\Python313\python.exe", base, windows=True) is None
    scripts = _own_env(tmp_path, windows=True)
    found = environnement.own_python(root, r"C:\Python313\python.exe", base, windows=True)
    assert found == str(scripts / "python.exe")
    # Sans fenêtre au départ, sans fenêtre à l'arrivée.
    quiet = environnement.own_python(root, r"C:\Python313\PYTHONW.EXE", base, windows=True)
    assert quiet == str(scripts / "pythonw.exe")
    # Déjà dedans : rien à relancer.
    assert environnement.inside(root, str(tmp_path / ".venv"))
    assert environnement.own_python(root, str(scripts / "python.exe"), str(tmp_path / ".venv"),
                                    windows=True) is None


def test_own_python_on_linux_and_macos(tmp_path):
    folder = _own_env(tmp_path, windows=False)
    assert environnement.own_python(str(tmp_path), "/usr/bin/python3", "/usr",
                                    windows=False) == str(folder / "python")


@pytest.mark.parametrize("windows", [True, False])
def test_incomplete_own_environment_is_not_used(tmp_path, windows):
    """Installation interrompue : il manque des bibliothèques. Le bot garde
    celles du PC au lieu de ne plus démarrer."""
    _own_env(tmp_path, windows, libraries=("numpy", "pandas"))
    assert not environnement.complete(str(tmp_path), windows)
    assert environnement.own_python(str(tmp_path), "python", "/usr", windows=windows) is None
    assert environnement.relaunch(str(tmp_path), ["bot.py", "run"], {}, windows=windows,
                                  popen=None, execv=None) is None


def test_bot_processes_reserve_memory_for_one_math_thread_only():
    env = {}
    environnement.limit_math_threads(env)
    assert env == {"OPENBLAS_NUM_THREADS": "1"}
    chosen = {"OPENBLAS_NUM_THREADS": "4"}              # un choix déjà fait est gardé
    environnement.limit_math_threads(chosen)
    assert chosen == {"OPENBLAS_NUM_THREADS": "4"}
    source = open(os.path.join(v29.APP_DIR, "trendguard_bot.py"), encoding="utf-8").read()
    assert source.index("limit_math_threads()") < source.index("from trendguard.bot import")


def test_autostart_does_not_depend_on_the_own_environment(tmp_path):
    """Au démarrage de l'ordinateur, c'est le Python de l'installation qui
    lance le bot : sans le dossier .venv, il démarre quand même."""
    root, own = str(tmp_path), str(tmp_path / ".venv")
    inside = environnement.launcher_python(root, os.path.join(own, "Scripts", "pythonw.exe"), own,
                                           r"C:\Python313\pythonw.exe")
    assert inside == r"C:\Python313\pythonw.exe"
    outside = environnement.launcher_python(root, r"C:\Python313\python.exe", r"C:\Python313",
                                            r"C:\Python313\python.exe")
    assert outside == r"C:\Python313\python.exe"
    # Un autre environnement (celui d'un développeur) reste le sien.
    other = environnement.launcher_python(root, "/home/moi/env/bin/python", "/home/moi/env",
                                          "/usr/bin/python3")
    assert other == "/home/moi/env/bin/python"
    assert autonomy.Autostart("linux").python == environnement.launcher_python(autonomy.ROOT)


class Child:
    def __init__(self, code, interrupts=0):
        self.code, self.interrupts = code, interrupts

    def wait(self):
        if self.interrupts:
            self.interrupts -= 1
            raise KeyboardInterrupt
        return self.code


def test_command_is_relaunched_in_the_own_environment_on_windows(tmp_path):
    launched = []

    def popen(cmd, **kw):
        launched.append(cmd)
        return Child(75, interrupts=2)          # Ctrl+C deux fois : on attend la fin du bot
    env = {}
    code = environnement.relaunch(str(tmp_path), ["bot.py", "run"], env, python="venv-python",
                                  windows=True, popen=popen)
    assert code == 75 and launched == [["venv-python", "bot.py", "run"]]
    assert env == {environnement.MARK: "1"}
    # Un processus lancé par le bot reste dans l'environnement de son parent.
    assert environnement.relaunch(str(tmp_path), ["bot.py", "run"], env, python="venv-python",
                                  windows=True, popen=popen) is None and len(launched) == 1


def test_command_replaces_its_process_elsewhere(tmp_path):
    replaced = []
    out = environnement.relaunch(str(tmp_path), ["bot.py", "status"], {}, python="/bot/.venv/bin/python",
                                 windows=False, execv=lambda path, cmd: replaced.append((path, cmd)))
    assert out is None                          # jamais atteint en vrai : le processus est remplacé
    assert replaced == [("/bot/.venv/bin/python", ["/bot/.venv/bin/python", "bot.py", "status"])]


def test_command_stays_here_without_own_environment_or_when_it_cannot_start(tmp_path):
    def broken(*a, **kw):
        raise OSError("introuvable")
    env = {}
    assert environnement.relaunch(str(tmp_path), ["bot.py"], env, windows=True, popen=broken) is None
    assert env == {environnement.MARK: "1"}     # pas de dossier .venv : le choix est fait quand même
    for windows in (True, False):
        assert environnement.relaunch(str(tmp_path), ["bot.py"], {}, python="absent", windows=windows,
                                      popen=broken, execv=broken) is None


def test_entry_point_chooses_its_environment_before_anything_else(monkeypatch):
    calls = []
    monkeypatch.setattr(environnement, "relaunch",
                        lambda root, command: calls.append((root, command)) or 7)
    with pytest.raises(SystemExit) as stop:
        runpy.run_path(os.path.join(v29.APP_DIR, "trendguard_bot.py"), run_name="__main__")
    assert stop.value.code == 7
    assert calls == [(v29.APP_DIR, sys.orig_argv[1:])]
    assert tg.main is not None                  # importé comme module : rien n'est relancé


# ---------- Rapport quotidien ----------

def proc(stdout="", code=0, stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def _audit(*vulnerable, n=3):
    libs = [{"name": f"lib{i}", "version": "1.0", "vulns": []} for i in range(n - len(vulnerable))]
    libs += [{"name": name, "version": version,
              "vulns": [{"id": "GHSA-x", "fix_versions": fixes}, {"id": "GHSA-y", "fix_versions": []}]}
             for name, version, fixes in vulnerable]
    return proc(json.dumps({"dependencies": libs, "fixes": []}), 1 if vulnerable else 0)


def _deps(answer, installed=None, own=True):
    return rp.Deps(run=lambda cmd, **kw: answer, platform="linux",
                   extra={"installed": PINS if installed is None else installed, "own_env": own})


@pytest.fixture
def root(tmp_path):
    (tmp_path / rps.PINS_FILE).write_text("# Versions testées.\nccxt==4.5.84\npandas==3.0.6  # tableaux\n"
                                         "\nsans-version\n", encoding="utf-8")
    return str(tmp_path)


def test_tested_versions_are_read_from_the_pins_file(root, tmp_path):
    assert rps.read_pins(root) == PINS
    assert rps.read_pins(str(tmp_path / "absent")) == {}
    here = rps.read_pins(v29.APP_DIR)            # la liste du dépôt
    assert {"ccxt", "numpy", "pandas", "python-dotenv"} <= set(here)


def test_report_confirms_tested_libraries_without_known_flaw(root):
    versions, flaws = rps.library_checks(root, _deps(_audit()))
    assert versions["label"] == "Bibliothèques du bot" and versions["ok"] is True
    assert "environnement propre" in versions["detail"] and "ccxt 4.5.84, pandas 3.0.6" in versions["detail"]
    assert flaws["ok"] is True and flaws["detail"] == "aucune dans les 3 bibliothèques installées"
    shared, _ = rps.library_checks(root, _deps(_audit(), own=False))
    assert shared["ok"] is True and "environnement propre" not in shared["detail"]


def test_report_flags_untested_versions(root):
    old = {"ccxt": "4.5.44"}                    # pandas absente
    versions, _ = rps.library_checks(root, _deps(_audit(), installed=old, own=False))
    assert versions["ok"] is False and versions["reco"] == rps.LIBRARY_RECO
    assert "ccxt 4.5.44 au lieu de 4.5.84, pandas absente au lieu de 3.0.6" in versions["detail"]
    assert "bibliothèques du PC" in versions["detail"]
    own, _ = rps.library_checks(root, _deps(_audit(), installed=old))
    assert own["ok"] is False and own["detail"].endswith("dans l'environnement propre du bot")


def test_report_names_libraries_with_a_known_flaw(root):
    answer = _audit(("aiohttp", "3.13.3", ["3.9.9", "3.14.3"]), ("pyjwt", "2.12.1", []), n=9)
    _, flaws = rps.library_checks(root, _deps(answer))
    assert flaws["ok"] is False and flaws["reco"] == rps.LIBRARY_RECO
    assert flaws["detail"] == "2 sur 9 : aiohttp 3.13.3 (corrigée en 3.14.3), pyjwt 2.12.1"


def test_audit_that_cannot_run_is_information_only(root):
    missing = proc("", 1, "C:\\python.exe: No module named pip_audit")
    assert rps.library_checks(root, _deps(missing))[1]["detail"] == "pip-audit non installé"
    offline = rps.library_checks(root, _deps(proc("", 1, "ConnectionError")))[1]
    assert offline["ok"] is None and "réseau" in offline["detail"]

    def slow(cmd, **kw):
        raise rps.subprocess.TimeoutExpired(cmd, kw["timeout"])
    deps = rp.Deps(run=slow, extra={"installed": PINS, "own_env": True})
    late = rps.library_checks(root, deps)[1]
    assert late["ok"] is None and not late["reco"]
    # Sans liste des versions testées : dit, sans alarme.
    (versions, _) = rps.library_checks(os.path.join(root, "absent"), _deps(_audit()))
    assert versions["ok"] is None and rps.PINS_FILE in versions["detail"]


def test_library_checks_are_in_the_security_section(root, tmp_path):
    g = tg.GuardConfig(db_file=":memory:", log_file=os.devnull, lock_file=str(tmp_path / "tg.lock"))
    deps = rp.Deps(run=lambda cmd, **kw: _audit() if "pip_audit" in cmd else proc("", 1),
                   http_json=lambda url: {}, platform="linux", panel=lambda port, pw: {},
                   binance=lambda env, testnet: rp.chk("Clé API Binance", None, "absente"),
                   diagnose=lambda gc: ([], "2026-09-29"),
                   now=datetime(2026, 9, 30, 0, 31, tzinfo=timezone.utc),
                   extra={"root": root, "installed": {"ccxt": "4.5.44", "pandas": "3.0.6"},
                          "own_env": False})
    r = rp.build(g, {}, deps, backups=False)
    security = next(s for s in r["sections"] if s["title"] == "Sécurité")
    labels = [c["label"] for c in security["checks"]]
    assert labels[-2:] == ["Bibliothèques du bot", "Failles connues des bibliothèques"]
    assert rps.LIBRARY_RECO in r["recommendations"] and "ccxt 4.5.44 au lieu de 4.5.84" in r["text"]


def test_simulated_commands_read_nothing_real(root):
    assert rps.library_checks(root, rp.Deps(run=lambda cmd, **kw: proc(""))) == []


def test_readme_explains_what_the_report_recommends():
    readme = open(os.path.join(v29.APP_DIR, "README.md"), encoding="utf-8").read()
    assert "### Bibliothèques du bot" in readme and "« Bibliothèques du bot »" in rps.LIBRARY_RECO
    assert rps.PINS_FILE in readme and environnement.OWN_DIR in readme
