"""Accès au système, partagé par le rapport quotidien (report.py) et la
maintenance autonome (maintenance.py) : commandes sans fenêtre, git,
réglages d'alimentation de Windows, état du bot en lecture seule, et forme
commune des constats. Tout accès extérieur passe par `Deps`, remplaçable
dans les tests.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import autonomy

Check = Dict[str, Any]
GB = 2 ** 30


def chk(label: str, ok: Optional[bool], detail: str, reco: str = "", action: str = "") -> Check:
    """Un constat : ok = True (conforme), False (à corriger), None (information) ;
    reco : ce qu'il faut faire ; action : ce que le bot a fait seul."""
    return {"label": label, "ok": ok, "detail": detail, "reco": reco, "action": action}


def run(cmd: List[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
        timeout: int = 60) -> subprocess.CompletedProcess:
    """Commande système sans fenêtre, sortie texte capturée."""
    if cmd and os.path.basename(cmd[0]).lower() in ("powershell", "powershell.exe"):
        env = autonomy.powershell_env(env)
    kw: Dict[str, Any] = {"cwd": cwd, "env": env, "capture_output": True, "text": True,
                          "timeout": timeout, "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kw["creationflags"] = autonomy.CREATE_NO_WINDOW
    return subprocess.run(cmd, **kw)


@dataclass
class Deps:
    """Accès au monde extérieur, remplaçables dans les tests."""
    run: Callable[..., Any] = run
    http_json: Optional[Callable[[str], Any]] = None
    panel: Optional[Callable[[int, str], Dict[str, Any]]] = None
    binance: Optional[Callable[[Dict[str, str], bool], Check]] = None
    diagnose: Optional[Callable[[Any], Tuple[List[Any], str]]] = None
    platform: str = sys.platform
    now: Optional[datetime] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def git(deps: Deps, root: str, *args: str) -> Optional[str]:
    """Sortie d'une commande git, ou None si elle échoue (git absent…)."""
    try:
        r = deps.run(["git", *args], cwd=root)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def ps_lines(deps: Deps, script: str) -> Optional[List[str]]:
    """Lignes écrites par un script PowerShell, ou None s'il échoue."""
    try:
        r = deps.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.splitlines() if r.returncode == 0 else None


def power_ac(deps: Deps, sub: str, setting: str) -> Optional[int]:
    """Valeur sur secteur d'un réglage d'alimentation de Windows (secondes ou
    index), ou None si le réglage n'existe pas sur ce PC (capot d'un PC fixe).
    /qh lit aussi les réglages que Windows cache : sur bien des portables,
    l'action du capot n'apparaît pas avec /query."""
    try:
        r = deps.run(["powercfg", "/qh", "SCHEME_CURRENT", sub, setting])
    except (OSError, subprocess.SubprocessError):
        return None
    vals = re.findall(r"0x([0-9a-fA-F]{8})", r.stdout or "")
    return int(vals[-2], 16) if r.returncode == 0 and len(vals) >= 2 else None


def power_source(deps: Optional[Deps] = None) -> Optional[Dict[str, Any]]:
    """Alimentation du PC sous Windows : {"ac": sur secteur ?, "battery_pct":
    charge de la batterie, None sans batterie}. None ailleurs, ou si Windows
    ne le dit pas."""
    if deps is not None:
        if "power" in deps.extra:
            return deps.extra["power"]
        if deps.run is not run or not deps.platform.startswith("win"):
            return None                 # commandes simulées (tests) : rien de réel n'est lu
    elif not sys.platform.startswith("win"):
        return None
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("pct", ctypes.c_ubyte),
                        ("saver", ctypes.c_ubyte), ("left", ctypes.c_ulong), ("full", ctypes.c_ulong)]
        s = Status()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return None
    except (OSError, AttributeError):
        return None
    if s.ac not in (0, 1):
        return None
    battery = s.flag not in (128, 255) and s.pct != 255     # 128 : pas de batterie
    return {"ac": s.ac == 1, "battery_pct": int(s.pct) if battery else None}


def pc_resources(deps: Optional[Deps] = None, root: str = "") -> Optional[Dict[str, Any]]:
    """Place sur le disque du bot et mémoire du PC, en Go comme Windows les
    affiche (1 Go = 2^30 octets) : {"disk_free", "disk_total", "memory_used",
    "memory_limit"} ; la mémoire est celle que Windows a réservée aux
    programmes et le plus qu'il peut leur réserver (None ailleurs). None avec
    des commandes simulées (tests)."""
    if deps is not None:
        if "resources" in deps.extra:
            return deps.extra["resources"]
        if deps.run is not run:
            return None
    disk = shutil.disk_usage(root or autonomy.ROOT)
    out: Dict[str, Any] = {"disk_free": disk.free / GB, "disk_total": disk.total / GB,
                           "memory_used": None, "memory_limit": None}
    if not sys.platform.startswith("win"):
        return out
    try:
        import ctypes

        class Memory(ctypes.Structure):
            _fields_ = [("size", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ("phys", "phys_free", "limit", "limit_free",
                                                        "virtual", "virtual_free", "extended")]
        m = Memory()
        m.size = ctypes.sizeof(m)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) and m.limit:
            out["memory_used"], out["memory_limit"] = (m.limit - m.limit_free) / GB, m.limit / GB
    except (OSError, AttributeError):
        pass
    return out


def installed_versions(deps: Deps, names: List[str]) -> Optional[Dict[str, Optional[str]]]:
    """Version installée de chaque bibliothèque (None : absente) dans le
    Python qui fait tourner le bot. None avec des commandes simulées (tests) :
    rien de réel n'est lu."""
    if "installed" in deps.extra:
        return {n: deps.extra["installed"].get(n) for n in names}
    if deps.run is not run:
        return None
    from importlib import metadata
    out: Dict[str, Optional[str]] = {}
    for n in names:
        try:
            out[n] = metadata.version(n)
        except metadata.PackageNotFoundError:
            out[n] = None
    return out


def requirements_of(deps: Deps) -> Optional[Dict[str, Tuple[str, List[str]]]]:
    """Bibliothèques installées dans le Python du bot : nom → (version,
    exigences déclarées). None avec des commandes simulées (tests)."""
    if "requirements" in deps.extra:
        return deps.extra["requirements"]
    if deps.run is not run:
        return None
    from importlib import metadata
    out: Dict[str, Tuple[str, List[str]]] = {}
    for d in metadata.distributions():
        name = (d.metadata["Name"] or "").lower()
        if name:
            out[name] = (d.version, list(d.requires or []))
    return out


def pypi_json(name: str) -> Any:
    """Dernière version publiée d'une bibliothèque sur PyPI (lecture seule)."""
    req = urllib.request.Request(f"https://pypi.org/pypi/{name}/json",
                                 headers={"User-Agent": "TrendGuard"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def github_json(url: str) -> Any:
    """API publique de GitHub (lecture seule, sans jeton)."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "TrendGuard"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def repo_slug(deps: Deps, root: str) -> str:
    """« propriétaire/dépôt » d'origine sur GitHub, ou "" s'il est inconnu."""
    remote = (git(deps, root, "remote", "get-url", "origin") or "").strip()
    if "github.com/" not in remote and "github.com:" not in remote:
        return ""
    return re.sub(r"(\.git)?/?$", "", re.split(r"github\.com[/:]", remote)[-1])


def ci_status(gh: Callable[[str], Any], slug: str, sha: str) -> Tuple[Optional[bool], List[str]]:
    """Contrôles GitHub d'une version : True (tous au vert), False (au moins
    un en échec, noms), None (en cours ou absents)."""
    runs = gh(f"https://api.github.com/repos/{slug}/actions/runs?head_sha={sha}&per_page=20") or {}
    done = [r for r in runs.get("workflow_runs", []) if r.get("status") == "completed"]
    if not done:
        return None, []
    bad = [r.get("name", "?") for r in done if r.get("conclusion") not in ("success", "skipped")]
    return (not bad), bad


def read_state(db_file: str) -> Dict[str, Any]:
    """État du bot, en lecture seule (le bot garde la main sur sa base)."""
    if not db_file or db_file == ":memory:" or not os.path.exists(db_file):
        return {}
    uri = pathlib.Path(os.path.abspath(db_file)).as_uri() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error:
        return {}
    try:
        row = con.execute("SELECT value FROM kv WHERE key=?", ("trendguard",)).fetchone()
        return json.loads(row[0]) if row else {}
    except (sqlite3.Error, ValueError):
        return {}
    finally:
        con.close()
