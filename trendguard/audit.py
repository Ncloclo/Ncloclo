"""
Journal d'audit (prompt maître, étape 2 ; docs/CONTRATS.md, AuditEvent.v1) :
chaque opération critique du bot laisse une ligne qu'aucun module ne peut
modifier sans que cela se voie.

Une ligne JSON par événement : numéro, date, acteur, action, objet, avant,
après, raison, autorisation, résultat, identifiant de corrélation (la
décision du jour, D-AAAA-MM-JJ). Chaque ligne porte l'empreinte de la
précédente : modifier, retirer ou intercaler une ligne casse la chaîne, et
`verify` le dit (rapport quotidien, `python trendguard_bot.py audit`).

Fichier <bot>.audit.jsonl à côté du bot, jamais publié. Seul le processus du
bot y écrit, et seulement en ajoutant : ce module n'offre ni modification ni
effacement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import v29

from . import autonomy

GENESIS = "0" * 64
TAIL_BYTES = 65_536


def path_for(gcfg: Any) -> str:
    """Journal d'audit du bot, à côté de son verrou ("" sans verrou : tests, rejeu)."""
    return autonomy.sidecar(getattr(gcfg, "lock_file", ""), ".audit.jsonl")


def digest(event: Dict[str, Any]) -> str:
    """Empreinte d'un événement (sans son propre champ « hash »)."""
    body = {k: v for k, v in event.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _last_line(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - TAIL_BYTES))
        lines = [x for x in fh.read().decode("utf-8", "replace").splitlines() if x.strip()]
    return json.loads(lines[-1]) if lines else None


class AuditLog:
    """Écrivain du journal (le bot seul) ; sans fichier, il ne fait rien."""

    def __init__(self, path: str):
        self.path = path
        self._tail: Optional[Tuple[int, str]] = None

    @property
    def enabled(self) -> bool:
        return bool(self.path)

    def append(self, actor: str, action: str, resource: str, result: str, reason: str = "",
               before: Any = None, after: Any = None, authorization: str = "", correlation_id: str = "",
               now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """Ajoute un événement au bout de la chaîne et le renvoie (None sans
        fichier). Une écriture ratée lève OSError : à l'appelant de décider
        (un achat sans trace est refusé)."""
        if not self.path:
            return None
        if self._tail is None:
            last = _last_line(self.path)
            self._tail = (int(last["audit_id"]), last["hash"]) if last else (0, GENESIS)
        n, prev = self._tail
        event = {"audit_id": n + 1, "timestamp": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
                 "actor": actor, "action": action, "resource": resource, "before": before, "after": after,
                 "reason": reason, "authorization": authorization, "result": result,
                 "correlation_id": correlation_id, "prev": prev, "version": 1}
        event["hash"] = digest(event)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._tail = (n + 1, event["hash"])
        return event


def verify(path: str) -> Dict[str, Any]:
    """Relit toute la chaîne : intacte, ou la première ligne modifiée."""
    out: Dict[str, Any] = {"ok": True, "events": 0, "bad_line": None, "last": None}
    if not path or not os.path.exists(path):
        return out
    prev, n = GENESIS, 0
    with open(path, encoding="utf-8") as fh:
        for k, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                good = (e.get("prev") == prev and e.get("hash") == digest(e) and int(e.get("audit_id")) == n + 1)
            except (ValueError, TypeError):
                good = False
            if not good:
                return dict(out, ok=False, bad_line=k, events=n)
            prev, n = e["hash"], n + 1
            out["last"] = e.get("timestamp")
    return dict(out, events=n)


def read(path: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Les derniers événements (les plus récents à la fin)."""
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = [line for line in fh if line.strip()]
    out = []
    for line in rows[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def describe(v: Dict[str, Any]) -> str:
    """Le bilan de `verify`, en clair."""
    if not v["events"] and v["ok"]:
        return "vide pour l'instant"
    if v["ok"]:
        return f"intact : {v['events']} événement(s), dernier le {str(v['last'])[:16].replace('T', ' à ')}"
    return (f"MODIFIÉ : la ligne {v['bad_line']} ne suit plus la chaîne ({v['events']} événement(s) "
            "intacts avant elle)")


def main(argv: Optional[List[str]] = None) -> int:
    """Commande `audit` : vérifier la chaîne (défaut) ou lire les derniers événements."""
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Journal d'audit de TrendGuard")
    ap.add_argument("action", nargs="?", default="verifier", choices=["verifier", "dernier"])
    ap.add_argument("--nombre", type=int, default=30, help="(dernier) nombre d'événements")
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    path = path_for(load_guard_config_from_env())
    v = verify(path)
    print(f"Journal d'audit : {describe(v)}")
    if args.action == "dernier":
        for e in read(path, args.nombre):
            print(f"  {e.get('audit_id')} · {str(e.get('timestamp'))[:19]} · {e.get('action')} · "
                  f"{e.get('resource')} · {e.get('result')}" + (f" · {e['reason']}" if e.get("reason") else ""))
    return 0 if v["ok"] else 1
