#!/usr/bin/env python3
"""
Autonomie de TrendGuard : le bot tourne seul, jour et nuit.

1. Superviseur — python trendguard_bot.py supervise
   Lance le bot et le relance s'il s'arrête sur une erreur (attente 10 s,
   30 s, 1 min… jusqu'à 10 min) ou s'il ne donne plus signe de vie pendant
   30 min (appel réseau figé). Il s'arrête avec le bot quand vous arrêtez
   l'automatisation : bouton ARRÊTER du panneau ou
   python trendguard_bot.py stop.
2. Démarrage avec l'ordinateur — python trendguard_bot.py autostart on|off
   Windows : clé « Run » de l'utilisateur (sans droits administrateur).
   Linux : services systemd de l'utilisateur. macOS : LaunchAgents.
   Le superviseur et le panneau démarrent à l'ouverture de session ; le bot
   ne repart pas si vous aviez arrêté l'automatisation.
3. Anti-veille — tant que le bot tourne, l'ordinateur ne se met pas en
   veille tout seul (l'écran peut s'éteindre ; fermer le capot met toujours
   le PC en veille). TG_KEEP_AWAKE=false pour désactiver.

Fichiers posés à côté du verrou du bot (ex. trendguard_paper.lock) :
  .stop               demande d'arrêt, lue par le bot chaque seconde
  .off                automatisation arrêtée par l'utilisateur
  .alive              signe de vie du bot (date de modification)
  .superviseur.json   état du superviseur, lu par le panneau
  .superviseur.lock   une seule instance du superviseur
  .superviseur.log    journal du superviseur
"""

from __future__ import annotations

import json
import logging
import os
import plistlib
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Dict, List, Optional, Tuple

import v29

ROOT = v29.APP_DIR
BOT_SCRIPT = os.path.join(ROOT, "trendguard_bot.py")
CREATE_NO_WINDOW = 0x08000000        # Windows : process sans fenêtre de console


# ══════════════════════════════════════════════════════════════════════
# FICHIERS PARTAGÉS : BOT, SUPERVISEUR, PANNEAU
# ══════════════════════════════════════════════════════════════════════

def sidecar(lock_file: str, ext: str) -> str:
    """Fichier à côté du verrou du bot (même dossier, même nom, autre
    extension) ; "" si le bot n'a pas de verrou (tests, rejeu)."""
    if not lock_file or lock_file in (os.devnull, "/dev/null"):
        return ""
    return os.path.splitext(lock_file)[0] + ext


_PROBE_LOCK = threading.Lock()


def lock_held(path: str) -> bool:
    """Le verrou est-il tenu par un process vivant ? Sonde sans attente.
    Deux sondes simultanées se gêneraient (l'une verrait le verrou pris par
    l'autre) : dans un même process elles passent l'une après l'autre, et
    un verrou vu pris est revérifié un instant plus tard (sonde d'un autre
    process, comme le superviseur)."""
    if not path or path in (os.devnull, "/dev/null"):
        return False
    with _PROBE_LOCK:
        for attempt in range(2):
            probe = v29.ProcessLock(path)
            try:
                probe.acquire()
            except SystemExit:
                if attempt == 0:
                    time.sleep(0.05)
                    continue
                return True
            probe.release()
            return False
    return True


def _touch(path: str, text: str = "") -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text or v29._utcnow_iso())


def _rm(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def automation_off(gcfg: Any) -> bool:
    off = sidecar(gcfg.lock_file, ".off")
    return bool(off) and os.path.exists(off)


def request_stop(gcfg: Any, reason: str = "") -> bool:
    """Arrête l'automatisation : le bot finit son cycle puis s'arrête, le
    superviseur ne le relance pas, et il ne redémarre pas avec l'ordinateur
    tant que l'automatisation n'est pas relancée (bouton AUTO)."""
    off, stop = sidecar(gcfg.lock_file, ".off"), sidecar(gcfg.lock_file, ".stop")
    if not off:
        return False
    _touch(off, reason)
    _touch(stop)
    return True


def allow_start(gcfg: Any) -> None:
    """Relance voulue par l'utilisateur : lève l'arrêt et efface une
    demande d'arrêt restée en place."""
    for ext in (".off", ".stop"):
        path = sidecar(gcfg.lock_file, ext)
        if path:
            _rm(path)


def supervisor_status(gcfg: Any) -> Dict[str, Any]:
    st = _read_json(sidecar(gcfg.lock_file, ".superviseur.json")) if gcfg.lock_file else {}
    st["running"] = lock_held(sidecar(gcfg.lock_file, ".superviseur.lock"))
    st["off"] = automation_off(gcfg)
    return st


def build_logger(path: str) -> logging.Logger:
    lg = logging.getLogger("trendguard.superviseur")
    lg.handlers.clear()
    lg.setLevel(logging.INFO)
    lg.propagate = False
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    if path:
        fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(fmt)
        lg.addHandler(fh)
    if sys.stderr is not None:                 # pythonw : pas de console
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        lg.addHandler(sh)
    return lg


def _fdur(sec: float) -> str:
    return f"{int(sec)} s" if sec < 90 else f"{round(sec / 60)} min"


# ══════════════════════════════════════════════════════════════════════
# SUPERVISEUR
# ══════════════════════════════════════════════════════════════════════

class Supervisor:
    BACKOFF_SEC = (10, 30, 60, 120, 300, 600)
    STALL_SEC = 30 * 60          # sans signe de vie du bot : bloqué
    HEALTHY_SEC = 60 * 60        # 1 h sans incident : l'attente repart de 10 s
    POLL_SEC = 2.0
    EXTERNAL_POLL_SEC = 30.0
    FINAL_WAIT_SEC = 60.0

    def __init__(self, gcfg: Any, logger: logging.Logger,
                 notify: Optional[Callable[..., Any]] = None,
                 popen: Callable[..., Any] = subprocess.Popen,
                 python: str = sys.executable,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 bot_running: Optional[Callable[[], bool]] = None):
        self.g = gcfg
        self.log = logger
        self.notify = notify or (lambda *a, **k: None)
        self.popen = popen
        self.python = python
        self.clock = clock
        self.sleep = sleep
        self.bot_running = bot_running or (lambda: lock_held(gcfg.lock_file))
        self.child: Any = None
        self.restarts = 0
        self.crashes = 0                     # plantages consécutifs
        self.last_exit: Optional[Dict[str, Any]] = None
        self.terminating = False             # Ctrl+C, arrêt du service
        self.since = v29._utcnow_iso()
        side = lambda ext: sidecar(gcfg.lock_file, ext)      # noqa: E731
        self.f_off, self.f_stop = side(".off"), side(".stop")
        self.f_alive, self.f_status = side(".alive"), side(".superviseur.json")
        log_file = gcfg.log_file if gcfg.log_file not in ("", os.devnull, "/dev/null") else ""
        # Sortie console du bot (erreurs fatales), réécrite à chaque lancement.
        self.f_console = log_file + ".console.txt" if log_file else os.devnull

    def stop_wanted(self) -> bool:
        return self.terminating or os.path.exists(self.f_off)

    def _status(self, state: str, **extra: Any) -> None:
        data = {"pid": os.getpid(), "state": state, "since": self.since,
                "restarts": self.restarts, "last_exit": self.last_exit,
                "updated": v29._utcnow_iso()}
        data.update(extra)
        try:
            _write_json(self.f_status, data)
        except OSError:
            pass

    def _start_bot(self) -> None:
        _rm(self.f_stop)                     # demande d'arrêt antérieure : périmée
        env = dict(os.environ, RUN_MODE=self.g.run_mode, PYTHONIOENCODING="utf-8")
        kw: Dict[str, Any] = {"cwd": ROOT, "env": env, "stdin": subprocess.DEVNULL,
                              "stderr": subprocess.STDOUT}
        if os.name == "nt":
            kw["creationflags"] = CREATE_NO_WINDOW
        out = open(self.f_console, "w", encoding="utf-8", errors="replace")
        try:
            self.child = self.popen([self.python, BOT_SCRIPT, "run"], stdout=out, **kw)
        finally:
            out.close()

    def _console_tail(self, n: int = 20) -> str:
        """Erreur fatale écrite par le bot (dernière trace Python), sinon ses
        dernières lignes."""
        if self.f_console == os.devnull:
            return ""
        try:
            with open(self.f_console, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return ""
        starts = [i for i, line in enumerate(lines) if line.startswith("Traceback")]
        return "\n".join(lines[starts[-1]:][-n:] if starts else lines[-3:])

    def _silence(self, started: float) -> float:
        """Secondes depuis le dernier signe de vie du bot."""
        try:
            last = os.path.getmtime(self.f_alive)
        except OSError:
            last = 0.0
        return self.clock() - max(started, last)

    def _kill(self) -> None:
        try:
            self.child.terminate()
        except OSError:
            pass
        for _ in range(10):
            if self.child.poll() is not None:
                return
            self.sleep(self.POLL_SEC)
        try:
            self.child.kill()
        except OSError:
            pass

    def _watch(self, started: float) -> Optional[int]:
        """Attend la fin du bot : code de sortie, ou None s'il était bloqué
        (arrêté de force)."""
        asked = False
        while True:
            code = self.child.poll()
            if code is not None:
                # Windows : code non signé (process tué → 4294967295 = -1).
                return code - (1 << 32) if code >= (1 << 31) else code
            if self.stop_wanted() and not asked:
                asked = True
                _touch(self.f_stop)          # arrêt propre à la fin du cycle en cours
                self.log.info("[SUPERVISEUR] arrêt demandé → le bot termine son cycle")
            silent = self._silence(started)
            if silent > self.STALL_SEC:
                self.log.critical(f"[SUPERVISEUR] aucun signe de vie du bot depuis "
                                  f"{silent / 60:.0f} min → arrêt forcé")
                self._kill()
                return None
            self.sleep(self.POLL_SEC)

    def _wait(self, seconds: float) -> bool:
        """Attente interrompue par une demande d'arrêt (True dans ce cas)."""
        end = self.clock() + seconds
        while self.clock() < end:
            if self.stop_wanted():
                return True
            self.sleep(min(self.POLL_SEC, max(0.0, end - self.clock())))
        return self.stop_wanted()

    def run(self) -> int:
        self.log.info(f"[SUPERVISEUR] démarré (pid {os.getpid()}) : le bot est relancé "
                      f"automatiquement s'il s'arrête sur une erreur")
        try:
            while not self.stop_wanted():
                if self.bot_running():
                    # Bot lancé ailleurs (terminal, VS Code) : on le laisse
                    # tourner et on prend le relais s'il s'arrête.
                    self._status("external")
                    self._wait(self.EXTERNAL_POLL_SEC)
                    continue
                started = self.clock()
                self._start_bot()
                pid = getattr(self.child, "pid", None)
                self.log.info(f"[SUPERVISEUR] bot lancé (pid {pid})")
                self._status("running", bot_pid=pid)
                code = self._watch(started)
                self.child = None
                self.last_exit = {"code": code, "at": v29._utcnow_iso(), "stalled": code is None}
                if code == 0 or self.stop_wanted():
                    self.log.info("[SUPERVISEUR] bot arrêté proprement → fin de la supervision")
                    break
                if self.clock() - started >= self.HEALTHY_SEC:
                    self.crashes = 0
                wait = self.BACKOFF_SEC[min(self.crashes, len(self.BACKOFF_SEC) - 1)]
                self.crashes += 1
                self.restarts += 1
                why = "bloqué, arrêté de force" if code is None else f"code de sortie {code}"
                tail = self._console_tail()
                self.log.error(f"[SUPERVISEUR] le bot s'est arrêté sur une erreur ({why}) "
                               f"→ relance dans {_fdur(wait)}" + (f"\n{tail}" if tail else ""))
                self.notify(f"⚠️ TrendGuard s'est arrêté sur une erreur ({why}) : relance "
                            f"automatique dans {_fdur(wait)}.",
                            critical=self.crashes >= 3,
                            dedup_key=f"tg-supervisor-{min(self.crashes, 3)}")
                nxt = datetime.now(timezone.utc) + timedelta(seconds=wait)
                self._status("waiting", next_start=nxt.isoformat())
                self._wait(wait)
        finally:
            self._final_stop()
            self._status("stopped")
        return 0

    def _final_stop(self) -> None:
        """Superviseur interrompu pendant que le bot tourne (Ctrl+C, arrêt du
        service) : arrêt propre du bot, sans le tuer au milieu d'un ordre."""
        if self.child is None or self.child.poll() is not None:
            if self.f_stop:
                _rm(self.f_stop)
            return
        _touch(self.f_stop)
        end = self.clock() + self.FINAL_WAIT_SEC
        while self.clock() < end and self.child.poll() is None:
            self.sleep(self.POLL_SEC)
        if self.child.poll() is None:
            self.log.warning("[SUPERVISEUR] le bot termine encore son cycle : il "
                             "s'arrêtera seul (demande d'arrêt déposée)")


def run_supervisor(gcfg: Any, login: bool = False, logger: Optional[logging.Logger] = None,
                   notify: Optional[Callable[..., Any]] = None, **kw: Any) -> int:
    """python trendguard_bot.py supervise [--login]. Avec --login (démarrage
    avec l'ordinateur), rien ne démarre si l'automatisation a été arrêtée ;
    sans --login, lancer le superviseur relance l'automatisation."""
    if not sidecar(gcfg.lock_file, ".off"):
        print("Superviseur impossible : le bot n'a pas de fichier de verrou (TG_LOCK_FILE).",
              file=sys.stderr)
        return 2
    log = logger or build_logger(sidecar(gcfg.lock_file, ".superviseur.log"))
    if login and automation_off(gcfg):
        log.info("[SUPERVISEUR] automatisation arrêtée par l'utilisateur : le bot ne démarre "
                 "pas avec l'ordinateur (bouton AUTO du panneau pour la relancer)")
        return 0
    if not login:
        _rm(sidecar(gcfg.lock_file, ".off"))
    lock = v29.ProcessLock(sidecar(gcfg.lock_file, ".superviseur.lock"))
    try:
        lock.acquire()
    except SystemExit:
        log.info("[SUPERVISEUR] déjà en marche : rien à faire")
        return 0
    own_notify = notify is None
    if own_notify:
        import alerts
        notify = alerts.build_notifier(log)
    sup = Supervisor(gcfg, log, notify, **kw)

    def _term(sig: int, frame: Any) -> None:
        sup.terminating = True
    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.signal(sig, _term)
        except (ValueError, OSError):         # hors du thread principal
            pass
    try:
        return sup.run()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if own_notify and hasattr(notify, "close"):
            notify.close()
        lock.release()


def cmd_stop(gcfg: Any) -> int:
    """python trendguard_bot.py stop."""
    if not request_stop(gcfg, "commande stop"):
        print("❌ Arrêt impossible : le bot n'a pas de fichier de verrou (TG_LOCK_FILE).",
              file=sys.stderr)
        return 1
    running = lock_held(gcfg.lock_file)
    print("✅ Automatisation arrêtée. "
          + ("Le bot termine son cycle puis s'arrête (moins d'une minute en général). "
             if running else "Le bot n'était pas en marche. ")
          + "Il ne redémarrera pas avec l'ordinateur tant que vous ne relancez pas "
            "l'automatisation (bouton AUTO du panneau ou python trendguard_bot.py supervise).")
    return 0


# ══════════════════════════════════════════════════════════════════════
# DÉMARRAGE AVEC L'ORDINATEUR
# ══════════════════════════════════════════════════════════════════════

AUTOSTART = (("TrendGuard", "bot (superviseur)", ["supervise", "--login"]),
             ("TrendGuard-panneau", "panneau de contrôle", ["panel", "--login"]))


def gui_python(python: str) -> str:
    """Windows : pythonw.exe (aucune fenêtre de console) s'il existe."""
    folder, name = os.path.split(python)
    if name.lower() == "python.exe":
        w = os.path.join(folder, "pythonw.exe")
        if os.path.exists(w):
            return w
    return python


class WindowsRunKey:
    """HKCU\\...\\CurrentVersion\\Run : programmes lancés à l'ouverture de
    session de l'utilisateur, sans droits administrateur."""
    KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def __init__(self, winreg: Any = None):
        if winreg is None:
            import winreg as _winreg
            winreg = _winreg
        self.w = winreg

    def get(self, name: str) -> Optional[str]:
        try:
            with self.w.OpenKey(self.w.HKEY_CURRENT_USER, self.KEY) as k:
                return self.w.QueryValueEx(k, name)[0]
        except OSError:
            return None

    def set(self, name: str, command: str) -> None:
        with self.w.CreateKey(self.w.HKEY_CURRENT_USER, self.KEY) as k:
            self.w.SetValueEx(k, name, 0, self.w.REG_SZ, command)

    def delete(self, name: str) -> None:
        try:
            with self.w.OpenKey(self.w.HKEY_CURRENT_USER, self.KEY, 0, self.w.KEY_SET_VALUE) as k:
                self.w.DeleteValue(k, name)
        except OSError:
            pass


class Autostart:
    def __init__(self, platform: str = sys.platform, python: str = sys.executable,
                 script: str = BOT_SCRIPT, home: Optional[str] = None,
                 run: Callable[..., Any] = subprocess.run, registry: Any = None):
        self.platform = platform
        self.python = python
        self.script = script
        self.home = home or os.path.expanduser("~")
        self.run = run
        self._registry = registry

    @property
    def kind(self) -> str:
        if self.platform.startswith("win"):
            return "windows"
        return "macos" if self.platform == "darwin" else "linux"

    def commands(self) -> List[Tuple[str, str, List[str]]]:
        py = gui_python(self.python) if self.kind == "windows" else self.python
        return [(name, label, [py, self.script] + args) for name, label, args in AUTOSTART]

    def _reg(self) -> Any:
        if self._registry is None:
            self._registry = WindowsRunKey()
        return self._registry

    def unit_path(self, name: str) -> str:
        return os.path.join(self.home, ".config", "systemd", "user", f"{name.lower()}.service")

    def plist_path(self, name: str) -> str:
        return os.path.join(self.home, "Library", "LaunchAgents",
                            f"com.trendguard.{name.lower()}.plist")

    def status(self) -> Dict[str, bool]:
        out = {}
        for name, _label, _cmd in self.commands():
            if self.kind == "windows":
                out[name] = bool(self._reg().get(name))
            elif self.kind == "macos":
                out[name] = os.path.exists(self.plist_path(name))
            else:
                out[name] = os.path.exists(self.unit_path(name))
        return out

    def enabled(self) -> bool:
        st = self.status()
        return bool(st) and all(st.values())

    def enable(self) -> Tuple[bool, str]:
        cmds = self.commands()
        root = os.path.dirname(self.script) or "."
        if self.kind == "windows":
            for name, _label, cmd in cmds:
                self._reg().set(name, subprocess.list2cmdline(cmd))
            return True, ("Démarrage automatique activé : le bot et le panneau démarreront "
                          "à chaque ouverture de session Windows.")
        if self.kind == "macos":
            for name, _label, cmd in cmds:
                path = self.plist_path(name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    plistlib.dump({"Label": f"com.trendguard.{name.lower()}",
                                   "ProgramArguments": cmd, "WorkingDirectory": root,
                                   "RunAtLoad": True, "ProcessType": "Background",
                                   "EnvironmentVariables": {"PYTHONIOENCODING": "utf-8"}}, fh)
            return True, ("Démarrage automatique activé : le bot et le panneau démarreront "
                          "à chaque ouverture de session macOS.")
        written = []
        for name, label, cmd in cmds:
            path = self.unit_path(name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"[Unit]\nDescription=TrendGuard — {label}\n\n"
                         f"[Service]\nType=simple\nWorkingDirectory={root}\n"
                         f"ExecStart={' '.join(shlex.quote(c) for c in cmd)}\n"
                         f"Environment=PYTHONIOENCODING=utf-8\n"
                         f"Restart=on-failure\nRestartSec=30\n\n"
                         f"[Install]\nWantedBy=default.target\n")
            written.append(path)
        try:
            for args in (["daemon-reload"], ["enable"] + [os.path.basename(p) for p in written]):
                r = self.run(["systemctl", "--user"] + args, capture_output=True, text=True,
                             timeout=30)
                if r.returncode != 0:
                    raise OSError((r.stderr or r.stdout or "erreur").strip()[:200])
        except (OSError, subprocess.SubprocessError) as e:
            for p in written:
                _rm(p)
            sup = " ".join(shlex.quote(c) for c in cmds[0][2])
            return False, (f"systemd indisponible ({e}). Autre solution : ajoutez la ligne "
                           f"« @reboot cd {shlex.quote(root)} && {sup} » avec crontab -e.")
        extra = ""
        try:
            r = self.run(["loginctl", "enable-linger"], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                extra = " Ils démarrent aussi sans ouverture de session, dès l'allumage."
        except (OSError, subprocess.SubprocessError):
            pass
        return True, ("Démarrage automatique activé : services systemd trendguard et "
                      "trendguard-panneau." + extra)

    def disable(self) -> Tuple[bool, str]:
        cmds = self.commands()
        if self.kind == "windows":
            for name, _label, _cmd in cmds:
                self._reg().delete(name)
        elif self.kind == "macos":
            for name, _label, _cmd in cmds:
                _rm(self.plist_path(name))
        else:
            units = [os.path.basename(self.unit_path(n)) for n, _l, _c in cmds]
            try:
                self.run(["systemctl", "--user", "disable"] + units, capture_output=True,
                         text=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
            for name, _label, _cmd in cmds:
                _rm(self.unit_path(name))
            try:
                self.run(["systemctl", "--user", "daemon-reload"], capture_output=True,
                         text=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
        return True, ("Démarrage automatique désactivé : le bot ne démarrera plus avec "
                      "l'ordinateur (le bot en marche continue).")


def cmd_autostart(action: str, auto: Optional[Autostart] = None) -> int:
    """python trendguard_bot.py autostart on|off|status."""
    auto = auto or Autostart()
    if action in ("on", "off"):
        ok, msg = auto.enable() if action == "on" else auto.disable()
        print(("✅ " if ok else "❌ ") + msg)
        return 0 if ok else 1
    st = auto.status()
    for name, label, cmd in auto.commands():
        print(f"{'✅' if st[name] else '—'} {label} : "
              f"{'démarre avec l ordinateur' if st[name] else 'démarrage manuel'}")
    print("Activer : python trendguard_bot.py autostart on — désactiver : autostart off")
    return 0


# ══════════════════════════════════════════════════════════════════════
# ANTI-VEILLE
# ══════════════════════════════════════════════════════════════════════

class KeepAwake:
    """Empêche la mise en veille AUTOMATIQUE de l'ordinateur tant que le bot
    tourne. L'écran peut s'éteindre ; une mise en veille demandée (menu,
    capot fermé) reste possible."""
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __init__(self, logger: Optional[logging.Logger] = None, platform: str = sys.platform,
                 popen: Callable[..., Any] = subprocess.Popen,
                 which: Callable[[str], Optional[str]] = shutil.which, kernel32: Any = None):
        self.log = logger
        self.platform = platform
        self.popen = popen
        self.which = which
        self.kernel32 = kernel32
        self._win = False
        self._proc: Any = None

    def _set_state(self, flags: int) -> int:
        k = self.kernel32
        if k is None:
            import ctypes
            k = ctypes.windll.kernel32           # type: ignore[attr-defined]
            fn = k.SetThreadExecutionState
            fn.argtypes = [ctypes.c_uint32]
            fn.restype = ctypes.c_uint32
        return int(k.SetThreadExecutionState(flags))

    def start(self) -> bool:
        try:
            if self.platform.startswith("win"):
                self._win = self._set_state(self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED) != 0
                ok = self._win
            else:
                pid = os.getpid()
                if self.platform == "darwin" and self.which("caffeinate"):
                    cmd = ["caffeinate", "-i", "-w", str(pid)]
                elif self.which("systemd-inhibit"):
                    cmd = ["systemd-inhibit", "--what=idle", "--who=TrendGuard",
                           "--why=Bot de trading en marche", "--mode=block", "sh", "-c",
                           f"while kill -0 {pid} 2>/dev/null; do sleep 60; done"]
                else:
                    return False
                self._proc = self.popen(cmd, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ok = True
        except Exception as e:                   # confort : jamais bloquant
            if self.log:
                self.log.warning(f"[AUTONOMIE] anti-veille indisponible : {e}")
            return False
        if ok and self.log:
            self.log.info("[AUTONOMIE] mise en veille automatique de l'ordinateur bloquée "
                          "tant que le bot tourne (TG_KEEP_AWAKE=false pour l'autoriser)")
        return ok

    def stop(self) -> None:
        if self._win:
            try:
                self._set_state(self.ES_CONTINUOUS)
            except Exception:
                pass
            self._win = False
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
