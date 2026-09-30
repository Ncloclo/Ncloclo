"""Environnement propre du bot : le dossier .venv, à côté du code, contient
ses bibliothèques aux versions testées (requirements-docker.txt), séparées
de celles des autres logiciels du PC, qui gardent les leurs.

Le point d'entrée (trendguard_bot.py) y relance toute commande lancée avec
un autre Python. Sans ce dossier, ou s'il est incomplet, le bot tourne avec
les bibliothèques du PC, comme avant : il démarre toujours. Un processus
lancé par le bot reste avec lui (variable TRENDGUARD_ENV, posée par le
premier et héritée). Chaque processus du bot n'utilise qu'un fil de calcul :
il réserve ainsi trois fois moins de mémoire.

Bibliothèque standard seulement : ce module est lu avant tous les autres.
"""

from __future__ import annotations

import glob
import ntpath
import os
import subprocess
import sys
from typing import Any, Callable, List, MutableMapping, Optional

OWN_DIR = ".venv"
MARK = "TRENDGUARD_ENV"         # le choix de l'environnement est fait : on n'y revient pas
CORE_LIBRARIES = ("ccxt", "numpy", "pandas", "dotenv")     # sans elles, le bot ne démarre pas
MATH_THREADS = "OPENBLAS_NUM_THREADS"


def limit_math_threads(environ: Optional[MutableMapping[str, str]] = None) -> None:
    """Un seul fil de calcul pour numpy. Dès son chargement, il réserve
    environ 30 Mo de mémoire par fil : huit fils, 240 Mo par processus, que
    le bot n'utilise pas (ses calculs sont des moyennes sur quelques
    centaines de bougies). Une valeur déjà choisie est gardée."""
    (os.environ if environ is None else environ).setdefault(MATH_THREADS, "1")


def own_dir(root: str) -> str:
    return os.path.join(root, OWN_DIR)


def complete(root: str, windows: Optional[bool] = None) -> bool:
    """Le dossier .venv contient-il les bibliothèques sans lesquelles le bot
    ne démarre pas ? Faux après une installation interrompue : le bot garde
    alors les bibliothèques du PC."""
    windows = os.name == "nt" if windows is None else windows
    if windows:
        folders = [os.path.join(own_dir(root), "Lib", "site-packages")]
    else:
        folders = glob.glob(os.path.join(own_dir(root), "lib", "python*", "site-packages"))
    return any(all(os.path.isdir(os.path.join(folder, lib)) for lib in CORE_LIBRARIES)
               for folder in folders)


def inside(root: str, prefix: Optional[str] = None) -> bool:
    """Ce processus tourne-t-il dans l'environnement propre du bot ?"""
    here, own = (os.path.normcase(os.path.realpath(p))
                 for p in (sys.prefix if prefix is None else prefix, own_dir(root)))
    return here == own


def own_python(root: str, executable: Optional[str] = None, prefix: Optional[str] = None,
               windows: Optional[bool] = None) -> Optional[str]:
    """Python de l'environnement propre du bot, ou None s'il n'existe pas,
    s'il est incomplet ou si ce processus y tourne déjà. Sous Windows, une
    commande sans fenêtre (pythonw) le reste."""
    if inside(root, prefix):
        return None
    windows = os.name == "nt" if windows is None else windows
    executable = sys.executable if executable is None else executable
    if windows:
        gui = ntpath.basename(executable or "").lower() == "pythonw.exe"
        path = os.path.join(own_dir(root), "Scripts", "pythonw.exe" if gui else "python.exe")
    else:
        path = os.path.join(own_dir(root), "bin", "python")
    return path if os.path.isfile(path) and complete(root, windows) else None


def launcher_python(root: str, executable: Optional[str] = None, prefix: Optional[str] = None,
                    base: Optional[str] = None) -> str:
    """Python inscrit pour le démarrage avec l'ordinateur. Depuis
    l'environnement propre, c'est celui de l'installation : il existe
    toujours, et le point d'entrée repasse dans l'environnement propre ; si
    ce dossier disparaît, le bot démarre quand même."""
    executable = sys.executable if executable is None else executable
    base = getattr(sys, "_base_executable", "") if base is None else base
    return base if base and inside(root, prefix) else executable


def _stream(stream: Any) -> Any:
    """Flux standard à transmettre tel quel (None sans console : pythonw)."""
    try:
        return stream if stream is not None and stream.fileno() >= 0 else None
    except (AttributeError, OSError, ValueError):
        return None


def relaunch(root: str, command: List[str],
             environ: Optional[MutableMapping[str, str]] = None,
             python: Optional[str] = None, windows: Optional[bool] = None,
             popen: Callable[..., Any] = subprocess.Popen,
             execv: Callable[[str, List[str]], Any] = os.execv) -> Optional[int]:
    """Relance la commande (arguments d'origine de Python) dans
    l'environnement propre du bot et renvoie son code de sortie. None si
    elle continue ici : déjà dedans, pas d'environnement propre, processus
    lancé par le bot, ou lancement impossible."""
    environ = os.environ if environ is None else environ
    if environ.get(MARK):
        return None
    environ[MARK] = "1"
    windows = os.name == "nt" if windows is None else windows
    python = own_python(root, windows=windows) if python is None else python
    if not python:
        return None
    cmd = [python, *command]
    if not windows:
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.flush()
        try:
            execv(python, cmd)              # remplace ce processus : même numéro, aucun intermédiaire
        except OSError:
            pass
        return None
    try:
        # Windows ne sait pas remplacer un processus : celui-ci attend la fin
        # de la commande relancée et transmet son code de sortie.
        child = popen(cmd, stdin=_stream(sys.stdin), stdout=_stream(sys.stdout),
                      stderr=_stream(sys.stderr))
    except OSError:
        return None
    while True:
        try:
            return child.wait()
        except KeyboardInterrupt:           # Ctrl+C : la commande le reçoit aussi et s'arrête proprement
            continue
