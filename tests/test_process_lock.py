"""Verrou d'instance unique (ProcessLock) et verrous attachés à la base."""

import errno
import os
import subprocess
import sys
import textwrap
import types

import pytest

import v29

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_second_acquire_refused_with_holder_identity(tmp_path):
    a = v29.ProcessLock(str(tmp_path / "bot.lock"))
    a.acquire()
    try:
        with pytest.raises(SystemExit, match=f"pid={os.getpid()}"):
            v29.ProcessLock(str(tmp_path / "bot.lock")).acquire()
    finally:
        a.release()
    b = v29.ProcessLock(str(tmp_path / "bot.lock"))
    b.acquire()                                   # libéré → reprenable
    b.release()


def test_lock_released_when_process_dies(tmp_path):
    path = str(tmp_path / "bot.lock")
    code = textwrap.dedent(f"""
        import os, v29
        v29.ProcessLock({path!r}).acquire()
        os._exit(9)                               # mort brutale, sans release
    """)
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=False,
                   env={**os.environ, "PYTHONPATH": ROOT})
    lk = v29.ProcessLock(path)
    lk.acquire()                                  # aucun verrou périmé
    lk.release()


def test_relative_path_is_absolute_and_cwd_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lk = v29.ProcessLock("rel.lock")
    assert lk.path == str(tmp_path / "rel.lock")
    cfg = v29.Config()
    assert os.path.isabs(cfg.db_file) and os.path.dirname(cfg.db_file) == v29.APP_DIR


def test_unsupported_filesystem_is_not_reported_as_running_instance(tmp_path, monkeypatch):
    import fcntl

    def nolock(fd, op):
        raise OSError(errno.ENOLCK, "No locks available")
    monkeypatch.setattr(fcntl, "flock", nolock)
    with pytest.raises(SystemExit, match="ne gère pas les verrous"):
        v29.ProcessLock(str(tmp_path / "x.lock")).acquire()


def test_windows_msvcrt_path(tmp_path, monkeypatch):
    calls = []
    held = {"locked": False}
    fake = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0)

    def locking(fd, mode, n):
        calls.append(mode)
        if mode == fake.LK_NBLCK:
            if held["locked"]:
                raise OSError(errno.EACCES, "Permission denied")
            held["locked"] = True
        else:
            held["locked"] = False
    fake.locking = locking
    monkeypatch.setitem(sys.modules, "fcntl", None)       # pas de fcntl
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    a = v29.ProcessLock(str(tmp_path / "w.lock"))
    a.acquire()
    with pytest.raises(SystemExit, match="autre instance"):
        v29.ProcessLock(str(tmp_path / "w.lock")).acquire()
    a.release()
    assert calls == [2, 2, 0] and not held["locked"]


def test_instance_locks_bind_the_database(tmp_path):
    db = str(tmp_path / "state.db")
    first = v29.acquire_instance_locks(str(tmp_path / "a.lock"), db)
    try:
        with pytest.raises(SystemExit, match="autre instance"):
            v29.acquire_instance_locks(str(tmp_path / "b.lock"), db)   # autre LOCK_FILE
        assert not os.path.exists(tmp_path / "b.lock.held")
        v29.acquire_instance_locks(str(tmp_path / "c.lock"),
                                   str(tmp_path / "other.db"))[0].release()
    finally:
        v29.release_locks(first)
    assert len(v29.acquire_instance_locks(os.devnull, ":memory:")) == 0
