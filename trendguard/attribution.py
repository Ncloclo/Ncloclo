"""
Attribution de la performance (prompt maître §36, docs/PLATEFORME.md) : d'où
viennent les gains et les pertes du bot, et pourquoi.

À partir des trades clos (avec leur leçon et leur régime, postmortem.py) et
des positions ouvertes : résultat par crypto, par régime du jour de l'achat,
par type de sortie et par leçon ; part du gain apportée par les trois
meilleurs trades (en suivi de tendance, quelques gros gains paient les
petites pertes) ; réponses en clair à « pourquoi ai-je gagné, pourquoi ai-je
perdu ? ».
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .postmortem import LESSONS
from .texte import fr

EXIT_LABELS = {"STOP": "stop de clôture", "EXCHANGE_STOP": "stop de secours", "DELISTED": "retrait de la cote",
               "STOP_LATE": "stop (rattrapage)"}


def _group(trades: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    acc: Dict[str, List[float]] = {}
    for t in trades:
        k = t.get(key)
        if k:
            acc.setdefault(str(k), []).append(float(t.get("pnl") or 0.0))
    return sorted(({"key": k, "trades": len(v), "pnl": round(sum(v), 2)} for k, v in acc.items()),
                  key=lambda r: -r["pnl"])


def attribution(trades: Iterable[Dict[str, Any]], open_pnl: Dict[str, float]) -> Dict[str, Any]:
    """Résultat par crypto (réalisé et en cours), par régime, par sortie,
    par leçon, et les réponses en clair."""
    closed = list(trades)
    realized = sum(float(t.get("pnl") or 0.0) for t in closed)
    unrealized = sum(open_pnl.values())
    by_asset: Dict[str, Dict[str, float]] = {}
    for t in closed:
        r = by_asset.setdefault(t["asset"], {"realized": 0.0, "unrealized": 0.0, "trades": 0})
        r["realized"] += float(t.get("pnl") or 0.0)
        r["trades"] += 1
    for a, v in open_pnl.items():
        by_asset.setdefault(a, {"realized": 0.0, "unrealized": 0.0, "trades": 0})["unrealized"] += v
    assets = sorted(({"asset": a, **{k: round(x, 2) if isinstance(x, float) else x for k, x in r.items()},
                      "total": round(r["realized"] + r["unrealized"], 2)} for a, r in by_asset.items()),
                    key=lambda r: -r["total"])
    why: List[str] = []
    wins = sorted((float(t.get("pnl") or 0) for t in closed if float(t.get("pnl") or 0) > 0), reverse=True)
    if len(wins) > 3:
        share = sum(wins[:3]) / sum(wins) * 100
        why.append(f"gains : les 3 meilleurs trades font {fr(share, '.0f')} % des gains réalisés "
                   "(en suivi de tendance, quelques gros gains paient les petites pertes)")
    elif wins:
        why.append(f"gains : {len(wins)} trade{'s' if len(wins) > 1 else ''} gagnant{'s' if len(wins) > 1 else ''}, "
                   f"{fr(sum(wins), '+.2f')} USDT")
    lessons = _group(closed, "lesson")
    losses = [r for r in lessons if r["pnl"] < 0]
    if losses:
        why.append("pertes : " + ", ".join(f"{LESSONS.get(r['key'], r['key']).split(' :')[0]} ({r['trades']} trade"
                                            f"{'s' if r['trades'] > 1 else ''}, {fr(r['pnl'], '+.2f')} USDT)"
                                            for r in losses))
    if not closed:
        why.append("aucun trade clos : seules les positions ouvertes comptent pour l'instant")
    text = (f"réalisé {fr(realized, '+.2f')} USDT sur {len(closed)} trade(s), en cours {fr(unrealized, '+.2f')} USDT"
            + (" ; " + " ; ".join(why) if why else ""))
    return {"realized": round(realized, 2), "unrealized": round(unrealized, 2), "assets": assets,
            "regimes": _group(closed, "regime"),
            "exits": [dict(r, key=EXIT_LABELS.get(r["key"], r["key"])) for r in _group(closed, "reason")],
            "lessons": [dict(r, label=LESSONS.get(r["key"], r["key"])) for r in lessons], "why": why, "text": text}
