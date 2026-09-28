"""Autonomie : superviseur (relance après plantage ou blocage, arrêt voulu
respecté), démarrage avec l'ordinateur (Windows, Linux, macOS), anti-veille."""

import json
import logging
import os
import plistlib
import subprocess

import pytest

import autonomy
import trendguard_bot as tg
import v29


def _cfg(tmp_path, **kw):
    base = dict(run_mode="paper", db_file=str(tmp_path / "tg.db"), log_file=str(tmp_path / "tg.log"),
                lock_file=str(tmp_path / "tg.lock"), auto_diagnose_days=0)
    base.update(kw)
    return tg.GuardConfig(**base)


@pytest.fixture
def logger():
    lg = logging.getLogger("test.autonomy")
    lg.setLevel(logging.INFO)
    return lg


class Clock:
    """Horloge simulée : sleep() avance le temps sans attendre."""

    def __init__(self):
        self.t = 1_800_000_000.0
        self.hooks = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.001)
        for h in list(self.hooks):
            h(self.t)


class Proc:
    """Bot simulé : s'arrête avec `code` après `life` secondes (None = jamais
    seul), ou dès qu'une demande d'arrêt est déposée."""

    def __init__(self, clock, life, code, stop_file=None):
        self.clock, self.code, self.stop_file = clock, code, stop_file
        self.end = None if life is None else clock() + life
        self.pid = 4242
        self.rc = None
        self.terminated = False

    def poll(self):
        if self.rc is None:
            if self.stop_file and os.path.exists(self.stop_file):
                os.remove(self.stop_file)
                self.rc = 0
            elif self.end is not None and self.clock() >= self.end:
                self.rc = self.code
        return self.rc

    def terminate(self):
        self.terminated = True
        self.rc = -15

    def kill(self):
        self.rc = -9


def _supervisor(g, logger, clock, plan, notes=None, **kw):
    """plan : liste de (durée de vie, code de sortie) des bots lancés."""
    launched = []

    def popen(cmd, **pk):
        life, code = plan[len(launched)]
        p = Proc(clock, life, code, stop_file=g.stop_file)
        launched.append((cmd, pk, p))
        return p
    notify = (lambda msg, **k: notes.append((msg, k))) if notes is not None else None
    sup = autonomy.Supervisor(g, logger, notify, popen=popen, python="python", clock=clock,
                              sleep=clock.sleep, bot_running=kw.pop("bot_running", lambda: False))
    return sup, launched


def test_supervisor_restarts_after_crash_then_stops_with_the_bot(tmp_path, logger):
    g, clock, notes = _cfg(tmp_path), Clock(), []
    # 1er bot : plante au bout d'une minute ; 2e : arrêt propre (code 0).
    sup, launched = _supervisor(g, logger, clock, [(60, 1), (500, 0)], notes)
    assert sup.run() == 0
    assert len(launched) == 2 and sup.restarts == 1
    cmd, kw, _p = launched[0]
    assert cmd[-2].endswith("trendguard_bot.py") and cmd[-1] == "run"
    assert kw["env"]["RUN_MODE"] == "paper" and kw["cwd"] == autonomy.ROOT
    assert notes and "relance automatique dans 10 s" in notes[0][0]
    st = json.load(open(autonomy.sidecar(g.lock_file, ".superviseur.json"), encoding="utf-8"))
    assert st["state"] == "stopped" and st["restarts"] == 1 and st["last_exit"]["code"] == 0


def test_windows_exit_code_and_crash_trace_are_reported(tmp_path, logger):
    g, clock, notes = _cfg(tmp_path), Clock(), []
    sup, launched = _supervisor(g, logger, clock, [(5, 4294967295), (5, 0)], notes)
    orig = sup._start_bot

    def start():
        orig()
        if len(launched) == 1:                          # le 1er bot plante avec une trace
            with open(g.log_file + ".console.txt", "w", encoding="utf-8") as fh:
                fh.write("ligne ordinaire\nTraceback (most recent call last):\n"
                         "  File x\nValueError: boum\n")
    sup._start_bot = start
    tails = []
    orig_tail = sup._console_tail
    sup._console_tail = lambda: tails.append(orig_tail()) or tails[-1]
    sup.run()
    assert "code de sortie -1" in notes[0][0]          # Windows : 4294967295 = -1
    assert tails[0].startswith("Traceback") and tails[0].endswith("ValueError: boum")


def test_supervisor_backoff_grows_and_alerts_after_three_crashes(tmp_path, logger):
    g, clock, notes = _cfg(tmp_path), Clock(), []
    sup, launched = _supervisor(g, logger, clock, [(5, 1)] * 4 + [(5, 0)], notes)
    starts = []
    orig = sup._start_bot
    sup._start_bot = lambda: (starts.append(clock()), orig())[1]
    sup.run()
    gaps = [round(b - a) for a, b in zip(starts, starts[1:])]
    assert gaps[0] < gaps[1] < gaps[2] < gaps[3]       # 10 s, 30 s, 1 min, 2 min (+ vie du bot)
    assert [k["critical"] for _m, k in notes] == [False, False, True, True]


def test_user_stop_is_respected_no_restart(tmp_path, logger):
    g, clock = _cfg(tmp_path), Clock()
    sup, launched = _supervisor(g, logger, clock, [(None, 0)])
    off = autonomy.sidecar(g.lock_file, ".off")
    clock.hooks.append(lambda t: t > 1_800_000_100 and not os.path.exists(off)
                       and open(off, "w").close())   # bouton ARRÊTER après 100 s
    assert sup.run() == 0
    assert len(launched) == 1 and launched[0][2].rc == 0 and sup.restarts == 0


def test_stalled_bot_is_killed_and_restarted(tmp_path, logger):
    g, clock, notes = _cfg(tmp_path), Clock(), []
    sup, launched = _supervisor(g, logger, clock, [(None, 0), (10, 0)], notes)
    sup.run()
    first = launched[0][2]
    assert first.terminated and len(launched) == 2      # bloqué 30 min : relancé
    assert sup.last_exit["code"] == 0 and sup.restarts == 1
    assert "bloqué" in notes[0][0]


def test_bot_alive_signal_prevents_the_stall_kill(tmp_path, logger):
    g, clock = _cfg(tmp_path), Clock()
    alive = autonomy.sidecar(g.lock_file, ".alive")
    sup, launched = _supervisor(g, logger, clock, [(3 * 3600, 0)])

    def beat(t):                                       # le bot écrit son signe de vie
        open(alive, "a").close()
        os.utime(alive, (t, t))
    clock.hooks.append(beat)
    sup.run()
    assert not launched[0][2].terminated and sup.restarts == 0


def test_external_bot_is_watched_then_taken_over(tmp_path, logger):
    g, clock = _cfg(tmp_path), Clock()
    t0 = clock()
    sup, launched = _supervisor(g, logger, clock, [(10, 0)],
                                bot_running=lambda: clock() < t0 + 120)
    sup.run()
    assert len(launched) == 1                          # lancé seulement après l'arrêt de l'autre
    assert json.load(open(autonomy.sidecar(g.lock_file, ".superviseur.json"),
                          encoding="utf-8"))["state"] == "stopped"


def test_run_supervisor_login_respects_user_stop_and_single_instance(tmp_path, logger):
    g = _cfg(tmp_path)
    calls = []

    def popen(cmd, **kw):
        calls.append(cmd)
        raise AssertionError("aucun bot ne doit démarrer")
    assert autonomy.cmd_stop(g) == 0                    # ARRÊTER : automatisation arrêtée
    assert autonomy.run_supervisor(g, login=True, logger=logger, notify=lambda *a, **k: None,
                                   popen=popen) == 0
    assert calls == [] and autonomy.automation_off(g)
    # Un superviseur tourne déjà : le second ne fait rien.
    lock = v29.ProcessLock(autonomy.sidecar(g.lock_file, ".superviseur.lock"))
    lock.acquire()
    try:
        assert autonomy.run_supervisor(g, logger=logger, notify=lambda *a, **k: None,
                                       popen=popen) == 0
    finally:
        lock.release()
    assert calls == [] and not autonomy.automation_off(g)   # lancement manuel : relance voulue
    st = autonomy.supervisor_status(g)
    assert st["running"] is False and st["off"] is False


def test_simultaneous_probes_never_see_a_free_lock_as_held(tmp_path):
    import threading
    path = str(tmp_path / "libre.lock")
    seen = []

    def probe():
        for _ in range(20):
            seen.append(autonomy.lock_held(path))
    threads = [threading.Thread(target=probe) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen and not any(seen)                      # personne ne tient ce verrou
    lock = v29.ProcessLock(path)
    lock.acquire()
    try:
        assert autonomy.lock_held(path) is True
    finally:
        lock.release()


# ---------- Démarrage avec l'ordinateur ----------

class FakeRegistry:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, cmd):
        self.values[name] = cmd

    def delete(self, name):
        self.values.pop(name, None)


def test_autostart_windows_run_key(tmp_path):
    folder = tmp_path / "Python"
    folder.mkdir()
    (folder / "pythonw.exe").write_text("")
    reg = FakeRegistry()
    a = autonomy.Autostart("win32", python=str(folder / "python.exe"),
                           script=r"C:\Mon Bot\trendguard_bot.py", registry=reg)
    assert a.enabled() is False
    ok, msg = a.enable()
    assert ok and a.enabled() and "Windows" in msg
    bot = reg.values["TrendGuard"]
    assert "pythonw.exe" in bot and '"C:\\Mon Bot\\trendguard_bot.py"' in bot
    assert bot.endswith("supervise --login") and reg.values["TrendGuard-panneau"].endswith("panel --login")
    assert a.disable()[0] and reg.values == {} and not a.enabled()


def test_autostart_linux_systemd_user_units(tmp_path):
    runs = []

    def run(cmd, **kw):
        runs.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    a = autonomy.Autostart("linux", python="/usr/bin/python3", script="/opt/bot/trendguard_bot.py",
                           home=str(tmp_path), run=run)
    ok, msg = a.enable()
    unit = open(a.unit_path("TrendGuard"), encoding="utf-8").read()
    assert ok and a.enabled() and "systemd" in msg
    assert "ExecStart=/usr/bin/python3 /opt/bot/trendguard_bot.py supervise --login" in unit
    assert "WorkingDirectory=/opt/bot" in unit and "WantedBy=default.target" in unit
    assert ["systemctl", "--user", "enable", "trendguard.service",
            "trendguard-panneau.service"] in runs
    assert a.disable()[0] and not os.path.exists(a.unit_path("TrendGuard"))


def test_autostart_linux_without_systemd_leaves_nothing(tmp_path):
    def run(cmd, **kw):
        raise FileNotFoundError("systemctl")
    a = autonomy.Autostart("linux", python="python3", script="/opt/bot/trendguard_bot.py",
                           home=str(tmp_path), run=run)
    ok, msg = a.enable()
    assert not ok and "crontab" in msg and not a.enabled()
    assert not os.path.exists(a.unit_path("TrendGuard"))


def test_autostart_macos_launch_agents(tmp_path):
    a = autonomy.Autostart("darwin", python="/usr/local/bin/python3",
                           script="/Users/moi/bot/trendguard_bot.py", home=str(tmp_path))
    assert a.enable()[0] and a.enabled()
    with open(a.plist_path("TrendGuard"), "rb") as fh:
        pl = plistlib.load(fh)
    assert pl["ProgramArguments"][-2:] == ["supervise", "--login"] and pl["RunAtLoad"] is True
    assert pl["WorkingDirectory"] == "/Users/moi/bot"
    assert a.disable()[0] and not a.enabled()


def test_cmd_autostart_prints_status(tmp_path, capsys):
    a = autonomy.Autostart("win32", python="python.exe", script="bot.py", registry=FakeRegistry())
    assert autonomy.cmd_autostart("on", a) == 0
    assert autonomy.cmd_autostart("status", a) == 0
    out = capsys.readouterr().out
    assert "Démarrage automatique activé" in out and "démarre avec l ordinateur" in out


# ---------- Anti-veille ----------

def test_keep_awake_windows_blocks_idle_sleep_then_releases():
    calls = []

    class K:
        def SetThreadExecutionState(self, flags):
            calls.append(flags)
            return 0x80000000
    ka = autonomy.KeepAwake(platform="win32", kernel32=K())
    assert ka.start() is True
    ka.stop()
    assert calls == [0x80000001, 0x80000000]           # ES_CONTINUOUS | ES_SYSTEM_REQUIRED, puis retour


def test_keep_awake_linux_and_missing_tools():
    started = []

    class P:
        def terminate(self):
            started.append("fin")
    ka = autonomy.KeepAwake(platform="linux", which=lambda n: "/usr/bin/" + n,
                            popen=lambda cmd, **kw: started.append(cmd) or P())
    assert ka.start() is True and started[0][0] == "systemd-inhibit" and "--what=idle" in started[0]
    ka.stop()
    assert started[-1] == "fin"
    assert autonomy.KeepAwake(platform="linux", which=lambda n: None).start() is False


def test_bot_writes_alive_signal(tmp_path, logger):
    from test_trendguard import make_bot, synthetic_market
    close, _volume = synthetic_market()
    bot, _fb = make_bot("paper", close, logger)
    import dataclasses
    bot.g = dataclasses.replace(bot.g, lock_file=str(tmp_path / "tg.lock"))
    alive = bot.g.alive_file
    assert alive.endswith("tg.alive") and not os.path.exists(alive)
    assert bot.waiting() is False and os.path.exists(alive)
