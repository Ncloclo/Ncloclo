"""Démarrage et arrêt propre du bot depuis le panneau.

- En marche ? Le verrou d'instance du bot est tenu (v29.ProcessLock) : la
  seule information fiable, même si le bot a été lancé ailleurs (VS Code,
  terminal, tâche planifiée).
- Démarrer (AUTO) : `python trendguard_bot.py supervise` dans un process
  détaché, qui continue si le panneau est fermé. Le superviseur lance le
  bot et le relance s'il plante ou se bloque (autonomy.py). Mode (paper ou
  réel) : celui du .env, avec ses garde-fous habituels.
- Arrêter : fichiers « .off » et « .stop » à côté du verrou ; le bot les
  voit en moins d'une seconde entre deux cycles et s'arrête proprement
  (état enregistré, stops Binance laissés en place), le superviseur ne le
  relance pas et il ne redémarre pas avec l'ordinateur. Même mécanisme
  sous Windows, Linux, macOS.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import v29
from trendguard import autonomy
from trendguard.systeme import power_source

BOT_SCRIPT = autonomy.BOT_SCRIPT
START_GRACE_SEC = 20        # démarrage en cours : le verrou n'est pas sondé


class BotControl:
    def __init__(self, gcfg: Any, popen: Callable[..., Any] = subprocess.Popen,
                 python: str = sys.executable, autostart: Any = None,
                 power: Callable[[], Optional[Dict[str, Any]]] = power_source):
        self.g = gcfg
        self.popen = popen
        self.python = python
        self.power = power
        self._autostart = autostart
        self._starting_until = 0.0
        self._proc: Optional[Any] = None

    def _lock_held(self) -> bool:
        return autonomy.lock_held(self.g.lock_file)

    def state(self) -> str:
        """running | stopped | starting | stopping | restarting (le
        superviseur relance le bot après une erreur)."""
        if time.time() < self._starting_until:
            if self._proc is not None and self._proc.poll() is not None:
                self._starting_until = 0.0          # le process s'est arrêté tout de suite
            else:
                return "starting"
        if self._lock_held():
            stopping = self.g.stop_file and os.path.exists(self.g.stop_file)
            return "stopping" if stopping else "running"
        sup = autonomy.supervisor_status(self.g)
        if sup.get("running"):
            if sup.get("off"):
                return "stopping"
            return "restarting" if sup.get("state") == "waiting" else "starting"
        return "stopped"

    def start(self) -> Tuple[bool, str]:
        st = self.state()
        if st in ("running", "starting"):
            return False, "Le bot est déjà en marche."
        if st == "restarting":
            return False, "Relance automatique en cours : le bot redémarre dans quelques instants."
        if st == "stopping":
            return False, "Arrêt en cours : réessayez dans quelques secondes."
        autonomy.allow_start(self.g)                # lève l'arrêt voulu et un .stop périmé
        env = dict(os.environ, RUN_MODE=self.g.run_mode, PYTHONIOENCODING="utf-8")
        kwargs: Dict[str, Any] = {"cwd": v29.APP_DIR, "env": env,
                                  "stdin": subprocess.DEVNULL,
                                  "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
                                       | getattr(subprocess, "DETACHED_PROCESS", 0x8))
        else:
            kwargs["start_new_session"] = True
        cmd: List[str] = [self.python, BOT_SCRIPT, "supervise"]
        self._proc = self.popen(cmd, **kwargs)
        self._starting_until = time.time() + START_GRACE_SEC
        mode = "RÉEL" if self.g.run_mode == "live" else "paper"
        return True, (f"Automatisation démarrée (mode {mode}) : le bot sera relancé "
                      f"automatiquement en cas de plantage.")

    def stop(self) -> Tuple[bool, str]:
        st = self.state()
        if st == "stopped":
            return False, "Le bot est déjà arrêté."
        if not autonomy.request_stop(self.g, "panneau"):
            return False, "Arrêt impossible : fichier de verrou non défini."
        self._starting_until = 0.0
        return True, ("Arrêt demandé : le bot termine son cycle puis s'arrête, sans relance "
                      "automatique. Les stops posés sur Binance restent actifs.")

    # ---------- Autonomie ----------

    def _auto(self) -> Any:
        if self._autostart is None:
            self._autostart = autonomy.Autostart()
        return self._autostart

    def autonomy(self) -> Dict[str, Any]:
        sup = autonomy.supervisor_status(self.g)
        try:
            auto: Optional[bool] = self._auto().enabled()
        except Exception:                           # registre ou dossier illisible
            auto = None
        return {"supervisor": {k: sup.get(k) for k in ("running", "state", "restarts",
                                                        "last_exit", "since", "next_start")},
                "off": bool(sup.get("off")), "autostart": auto, "os": self._auto().kind,
                "keep_awake": bool(getattr(self.g, "keep_awake", False)),
                # Portable sur batterie : il s'endort capot fermé, puis s'éteint.
                "power": self.power()}

    def set_autostart(self, enabled: bool) -> Tuple[bool, str]:
        try:
            return self._auto().enable() if enabled else self._auto().disable()
        except Exception as e:
            return False, f"Réglage impossible : {type(e).__name__} : {str(e)[:160]}"
