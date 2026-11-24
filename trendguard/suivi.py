"""Journal du bot en direct dans un terminal : les dernières lignes, puis
chaque nouvelle ligne dès qu'elle est écrite (comme `tail -f`).

Le bot tourne en arrière-plan, sans fenêtre (superviseur, bouton AUTO du
panneau) : il écrit son journal dans un fichier, pas à l'écran. Pour le
regarder travailler : `python trendguard_bot.py journal`. Lancés depuis un
terminal, `supervise` et `run` l'affichent aussi quand le bot tourne déjà.
Ctrl+C ferme l'affichage ; le bot continue. Lecture seule : ce module
n'écrit rien.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, List, Optional

POLL_SEC = 0.5
LINES = 30                      # lignes déjà écrites affichées d'abord


def interactive() -> bool:
    """Sortie vers un terminal (et non un fichier, ni rien sous pythonw)."""
    try:
        return sys.stdout is not None and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def last_lines(path: str, n: int) -> List[str]:
    """Les `n` dernières lignes du fichier, en ne lisant que sa fin."""
    if n <= 0 or not os.path.exists(path):
        return []
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        data = b""
        while size > 0 and data.count(b"\n") <= n:
            step = min(65536, size)
            size -= step
            fh.seek(size)
            data = fh.read(step) + data
    return [line.decode("utf-8", errors="replace").rstrip("\r") for line in data.split(b"\n")
            if line.strip()][-n:]


def follow(path: str, lines: int = LINES, write: Callable[[str], None] = print,
           stop: Optional[threading.Event] = None, poll: float = POLL_SEC,
           sleep: Callable[[float], None] = time.sleep) -> None:
    """Affiche les `lines` dernières lignes, puis les nouvelles, ligne
    complète par ligne complète. Suit le journal quand il est renouvelé
    (devenu trop gros, il est renommé et un nouveau commence) et attend
    qu'il existe. S'arrête quand `stop` est levé."""
    for line in last_lines(path, lines):
        write(line)
    try:
        pos = os.path.getsize(path)
    except OSError:
        pos = 0
    rest = b""
    while not (stop is not None and stop.is_set()):
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size < pos:                       # journal renouvelé : nouveau fichier
            pos, rest = 0, b""
        if size > pos:
            try:
                with open(path, "rb") as fh:
                    fh.seek(pos)
                    chunk = fh.read(size - pos)
            except OSError:
                chunk = b""
            pos += len(chunk)
            *done, rest = (rest + chunk).split(b"\n")
            for line in done:
                write(line.decode("utf-8", errors="replace").rstrip("\r"))
        sleep(poll)


def show(path: str, lines: int = LINES) -> int:
    """`python trendguard_bot.py journal` : le journal en direct jusqu'à
    Ctrl+C."""
    print(f"Journal du bot en direct ({path})")
    print("Ctrl+C ferme l'affichage : le bot continue.\n", flush=True)
    try:
        follow(path, lines, write=lambda s: print(s, flush=True))
    except KeyboardInterrupt:
        print("\nAffichage fermé : le bot continue.")
    return 0


def in_background(path: str, lines: int = 10) -> threading.Event:
    """Le journal affiché pendant qu'un autre travail occupe le terminal
    (superviseur) ; lever l'événement rendu pour l'arrêter."""
    stop = threading.Event()
    threading.Thread(target=follow, args=(path, lines),
                     kwargs={"write": lambda s: print(s, flush=True), "stop": stop},
                     name="journal-en-direct", daemon=True).start()
    return stop
