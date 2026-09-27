"""Démarrage et arrêt propre du bot depuis le panneau.

- En marche ? Le verrou d'instance du bot est tenu (v29.ProcessLock) : la
  seule information fiable, même si le bot a été lancé ailleurs (VS Code,
  terminal, tâche planifiée).
- Démarrer : `python trendguard_bot.py run` dans un process détaché, qui
  continue si le panneau est fermé. Mode (paper ou réel) : celui du .env,
  avec ses garde-fous habituels.
- Arrêter : fichier « .stop » à côté du verrou ; le bot le voit en moins
  d'une seconde entre deux cycles et s'arrête proprement (état enregistré,
  stops Binance laissés en place). Même mécanisme sous Windows, Linux, macOS.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import v29

BOT_SCRIPT = os.path.join(v29.APP_DIR, "trendguard_bot.py")
START_GRACE_SEC = 20        # démarrage en cours : le verrou n'est pas sondé


class BotControl:
    def __init__(self, gcfg: Any, popen: Callable[..., Any] = subprocess.Popen,
                 python: str = sys.executable):
        self.g = gcfg
        self.popen = popen
        self.python = python
        self._starting_until = 0.0
        self._proc: Optional[Any] = None

    def _lock_held(self) -> bool:
        if not self.g.lock_file or self.g.lock_file == os.devnull:
            return False
        probe = v29.ProcessLock(self.g.lock_file)
        try:
            probe.acquire()
        except SystemExit:
            return True
        probe.release()
        return False

    def state(self) -> str:
        """running | stopped | starting | stopping."""
        if time.time() < self._starting_until:
            if self._proc is not None and self._proc.poll() is not None:
                self._starting_until = 0.0          # le process s'est arrêté tout de suite
            else:
                return "starting"
        running = self._lock_held()
        if running and self.g.stop_file and os.path.exists(self.g.stop_file):
            return "stopping"
        return "running" if running else "stopped"

    def start(self) -> Tuple[bool, str]:
        st = self.state()
        if st in ("running", "starting"):
            return False, "Le bot est déjà en marche."
        if st == "stopping":
            return False, "Arrêt en cours : réessayez dans quelques secondes."
        if self.g.stop_file and os.path.exists(self.g.stop_file):
            os.remove(self.g.stop_file)             # demande d'arrêt périmée
        env = dict(os.environ, RUN_MODE=self.g.run_mode, PYTHONIOENCODING="utf-8")
        kwargs: Dict[str, Any] = {"cwd": v29.APP_DIR, "env": env,
                                  "stdin": subprocess.DEVNULL,
                                  "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
                                       | getattr(subprocess, "DETACHED_PROCESS", 0x8))
        else:
            kwargs["start_new_session"] = True
        cmd: List[str] = [self.python, BOT_SCRIPT, "run"]
        self._proc = self.popen(cmd, **kwargs)
        self._starting_until = time.time() + START_GRACE_SEC
        mode = "RÉEL" if self.g.run_mode == "live" else "paper"
        return True, f"Automatisation démarrée (mode {mode})."

    def stop(self) -> Tuple[bool, str]:
        st = self.state()
        if st == "stopped":
            return False, "Le bot est déjà arrêté."
        if not self.g.stop_file:
            return False, "Arrêt impossible : fichier de verrou non défini."
        with open(self.g.stop_file, "w", encoding="utf-8") as fh:
            fh.write(v29._utcnow_iso())
        self._starting_until = 0.0
        return True, ("Arrêt demandé : le bot termine son cycle puis s'arrête. "
                      "Les stops posés sur Binance restent actifs.")
