"""Journal du bot en direct (trendguard/suivi.py) : dernières lignes, puis
les nouvelles ; journal renouvelé ; affiché quand le bot ou le superviseur
tourne déjà et que la commande est lancée depuis un terminal."""

import logging
import threading

import pytest

import trendguard_bot as tg
import v29
from trendguard import autonomy, cli, suivi


def _cfg(tmp_path):
    return tg.GuardConfig(run_mode="paper", db_file=str(tmp_path / "tg.db"), log_file=str(tmp_path / "tg.log"),
                          lock_file=str(tmp_path / "tg.lock"), auto_diagnose_days=0)


def test_last_lines_reads_the_end_only(tmp_path):
    path = tmp_path / "bot.log"
    path.write_text("".join(f"ligne {i}\r\n" for i in range(5000)), encoding="utf-8")
    assert suivi.last_lines(str(path), 3) == ["ligne 4997", "ligne 4998", "ligne 4999"]
    assert suivi.last_lines(str(path), 0) == []
    assert suivi.last_lines(str(tmp_path / "absent.log"), 5) == []


def test_follow_shows_new_lines_whole_and_survives_a_renewed_log(tmp_path):
    """Les lignes déjà écrites, puis les nouvelles, entières (une ligne à
    moitié écrite attend sa fin) ; un journal renouvelé (plus petit) est
    relu depuis son début."""
    path = tmp_path / "bot.log"
    path.write_text("ancienne 1\nancienne 2\n", encoding="utf-8")
    shown, stop = [], threading.Event()
    steps = iter([
        lambda: path.open("a", encoding="utf-8").write("nouvelle 1\nmoitié"),
        lambda: path.open("a", encoding="utf-8").write(" finie\n"),
        lambda: path.write_text("après renouvellement\n", encoding="utf-8"),
        lambda: path.open("a", encoding="utf-8").write("é accentué\n"),
        stop.set,
    ])

    def sleep(_s):
        next(steps)()
    suivi.follow(str(path), lines=1, write=shown.append, stop=stop, sleep=sleep)
    assert shown == ["ancienne 2", "nouvelle 1", "moitié finie", "après renouvellement", "é accentué"]


def test_follow_waits_for_a_log_that_does_not_exist_yet(tmp_path):
    path = tmp_path / "bot.log"
    shown, stop = [], threading.Event()
    steps = iter([lambda: None, lambda: path.write_text("démarrage\n", encoding="utf-8"), stop.set])
    suivi.follow(str(path), write=shown.append, stop=stop, sleep=lambda _s: next(steps)())
    assert shown == ["démarrage"]


def test_journal_command_is_known():
    args = cli._parser().parse_args(["journal", "--lignes", "5"])
    assert args.cmd == "journal" and args.lignes == 5


def test_run_while_the_bot_runs_shows_its_log_in_a_terminal(tmp_path, monkeypatch):
    """`run` lancé depuis un terminal alors que le bot tourne déjà (bouton
    AUTO) : son journal en direct, au lieu d'un message puis plus rien. Hors
    d'un terminal (superviseur), le refus reste tel quel."""
    g = _cfg(tmp_path)
    shown = []
    monkeypatch.setattr(suivi, "show", lambda path, lines=suivi.LINES: shown.append(path) or 0)
    lock = v29.ProcessLock(g.lock_file)
    lock.acquire()
    try:
        monkeypatch.setattr(suivi, "interactive", lambda: True)
        assert cli._run(g, once=False) == 0 and shown == [g.log_file]
        monkeypatch.setattr(suivi, "interactive", lambda: False)
        with pytest.raises(SystemExit):
            cli._run(g, once=False)
    finally:
        lock.release()


def test_supervisor_already_running_shows_the_log_in_a_terminal(tmp_path, monkeypatch):
    g = _cfg(tmp_path)
    shown = []
    monkeypatch.setattr(suivi, "show", lambda path, lines=suivi.LINES: shown.append(path) or 0)
    monkeypatch.setattr(suivi, "interactive", lambda: True)
    lock = v29.ProcessLock(autonomy.sidecar(g.lock_file, ".superviseur.lock"))
    lock.acquire()
    try:
        assert autonomy.run_supervisor(g, logger=logging.getLogger("test.suivi"),
                                       notify=lambda *a, **k: None) == 0
    finally:
        lock.release()
    assert shown == [g.log_file]


def test_the_supervisor_starts_the_bot_with_the_console_python(tmp_path):
    """Lancé sans console (pythonw), le superviseur lance le bot avec
    python.exe sans fenêtre : sa sortie va dans <journal>.console.txt."""
    folder = tmp_path / "Scripts"
    folder.mkdir()
    for name in ("python.exe", "pythonw.exe"):
        (folder / name).write_text("")
    assert autonomy.console_python(str(folder / "pythonw.exe")) == str(folder / "python.exe")
    assert autonomy.console_python(str(folder / "python.exe")) == str(folder / "python.exe")
    assert autonomy.gui_python(str(folder / "python.exe")) == str(folder / "pythonw.exe")
