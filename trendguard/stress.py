"""
Tests de résistance du portefeuille (prompt maître §35, docs/PLATEFORME.md) :
à chaque décision, le bot calcule ce que coûteraient des chocs extrêmes à ses
positions du moment, en % du capital.

Scénarios (sur les cryptos détenues ; l'argent en USDT ne bouge pas, sauf le
dernier) :
  - krach de 20 %, 35 % et 50 % sans que les stops puissent s'exécuter
    (cours qui sautent par-dessus, comme en mars 2020, ou Binance en panne
    pendant la chute) ;
  - crise de liquidité : toutes les positions vendues 10 % sous leur stop ;
  - pic de volatilité : tous les stops touchés à la fois, 2 % de glissement ;
  - retrait de la cote de la plus grosse position : −60 % sur elle seule ;
  - décrochage de l'USDT de 10 % : l'argent en USDT perd 10 % de sa valeur.

Une mesure, pas une prévision : pour savoir ce qui peut arriver de pire, et
si l'arrêt d'urgence (−40 % depuis le plus haut) se déclencherait.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .texte import fr

Position = Dict[str, float]      # {"qty", "price", "stop"}


def scenarios(positions: Dict[str, Position], cash: float, equity: float) -> List[Dict[str, Any]]:
    """Perte de chaque scénario, en USDT et en % du capital."""
    if not equity or equity <= 0:
        return []
    value = {a: p["qty"] * p["price"] for a, p in positions.items()}
    at_stop = {a: p["qty"] * min(p["price"], p.get("stop") or p["price"]) for a, p in positions.items()}
    rows = []

    def add(name: str, loss: float) -> None:
        rows.append({"name": name, "loss_usdt": round(loss, 2), "loss_pct": round(loss / equity * 100, 2)})
    for drop in (0.20, 0.35, 0.50):
        add(f"Krach des cryptos de {int(drop * 100)} %, stops sautés", sum(value.values()) * drop)
    add("Crise de liquidité : ventes 10 % sous les stops",
        sum(value[a] - at_stop[a] * 0.90 for a in positions))
    add("Pic de volatilité : tous les stops touchés, 2 % de glissement",
        sum(value[a] - at_stop[a] * 0.98 for a in positions))
    if value:
        big = max(value, key=lambda a: value[a])
        add(f"Retrait de la cote de la plus grosse position, {big.upper()} (−60 %)", value[big] * 0.60)
    add("Décrochage de l'USDT de 10 %", max(0.0, cash) * 0.10)
    return sorted(rows, key=lambda r: -r["loss_pct"])


def describe(rows: List[Dict[str, Any]], kill: float = 0.40, dd: float = 0.0) -> str:
    """Le pire scénario, et ce qu'il ferait de l'arrêt d'urgence (`dd` :
    baisse actuelle depuis le plus haut)."""
    if not rows:
        return ""
    worst = rows[0]
    after = 1 - (1 - dd) * (1 - worst["loss_pct"] / 100)
    tail = ("il déclencherait l'arrêt d'urgence" if after >= kill else
            f"il resterait {fr((kill - after) * 100, '.1f')} points avant l'arrêt d'urgence "
            f"(−{fr(kill * 100, '.0f')} % depuis le plus haut)")
    return (f"Pire test de résistance : {worst['name'][:1].lower() + worst['name'][1:]}, "
            f"−{fr(worst['loss_pct'], '.1f')} % du capital ; {tail}.")
