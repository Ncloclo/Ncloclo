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
import sqlite3
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import autonomy

Check = Dict[str, Any]


def chk(label: str, ok: Optional[bool], detail: str, reco: str = "", action: str = "") -> Check:
    """Un constat : ok = True (conforme), False (à corriger), None (information) ;
    reco : ce qu'il faut faire ; action : ce que le bot a fait seul."""
    return {"label": label, "ok": ok, "detail": detail, "reco": reco, "action": action}


def run(cmd: List[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
        timeout: int = 60) -> subprocess.CompletedProcess:
    """Commande système sans fenêtre, sortie texte capturée."""
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
    index), ou None si le réglage n'existe pas sur ce PC (capot d'un PC fixe)."""
    try:
        r = deps.run(["powercfg", "/query", "SCHEME_CURRENT", sub, setting])
    except (OSError, subprocess.SubprocessError):
        return None
    vals = re.findall(r"0x([0-9a-fA-F]{8})", r.stdout or "")
    return int(vals[-2], 16) if r.returncode == 0 and len(vals) >= 2 else None


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
