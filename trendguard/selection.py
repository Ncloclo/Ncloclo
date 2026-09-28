"""Sélection des cryptos achetables (écrite par le panneau, lue par le bot).

Partie du bot TrendGuard (paquet trendguard, point d'entrée : trendguard_bot.py).
"""
from __future__ import annotations

from typing import Any, Dict, List

import v29

from . import autonomy
from .config import GuardConfig


def read_selection(gcfg: GuardConfig) -> Dict[str, Any]:
    """Choix enregistré par le panneau :
    - « auto » (Sélection auto, recommandée) : le bot peut acheter les 21 cryptos
      (meilleur résultat historique, docs/SELECTION.md) ;
    - « manual » (Sélection manuelle) : seulement les cryptos cochées
      (aucune au départ).
    Sans choix enregistré : la sélection auto."""
    path = autonomy.sidecar(gcfg.lock_file, ".selection.json")
    data = autonomy._read_json(path) if path else {}
    universe = [b.lower() for b in gcfg.universe]
    mode = "manual" if data.get("mode") == "manual" else "auto"
    manual = data.get("manual")
    if not isinstance(manual, list):
        manual = []
    wanted = {str(x).lower() for x in manual}
    return {"mode": mode, "manual": [a for a in universe if a in wanted],
            "saved": bool(data)}


def write_selection(gcfg: GuardConfig, mode: str, manual: List[str]) -> Dict[str, Any]:
    universe = [b.lower() for b in gcfg.universe]
    if mode not in ("auto", "manual"):
        raise ValueError("mode de sélection inconnu")
    unknown = [a for a in manual if str(a).lower() not in universe]
    if unknown:
        raise ValueError("crypto inconnue : " + ", ".join(map(str, unknown))[:80])
    path = autonomy.sidecar(gcfg.lock_file, ".selection.json")
    if not path:
        raise ValueError("sélection impossible : fichier de verrou non défini")
    autonomy._write_json(path, {"mode": mode, "manual": [a for a in universe if a in
                                                         {str(x).lower() for x in manual}],
                                "updated": v29._utcnow_iso()})
    return read_selection(gcfg)
