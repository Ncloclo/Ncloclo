"""
Garde « NO TRADE » (prompt maître §29, §44, §53, docs/PLATEFORME.md) : avant
les achats du jour, le bot vérifie que tout est sain. Une seule réponse
« non » suffit : aucun achat ce jour-là, raison dite dans le raisonnement.
La garde ne vend jamais et ne touche pas aux stops ; ne pas acheter est
toujours une décision permise.

Vérifications :
  - données du jour : au moins 80 % des cryptos ont une clôture valable ;
  - mouvement de BTC : moins de 15 % en un jour (krach, emballement ou
    donnée aberrante) ;
  - perte du jour : le capital n'a pas perdu 8 % ou plus depuis la décision
    précédente ;
  - place sur le disque : au moins 1 Go libre (sinon la base du bot risque de
    ne plus s'écrire).

Seuils choisis par l'étude du 6 octobre (docs/PLATEFORME.md) : rejoués sur
2018 → 2026, ils ne changent aucun résultat de la stratégie (des seuils plus
stricts, 3 à 5 % de perte du jour ou 10 % de mouvement de BTC, faisaient
moins bien). Ce sont des garde-fous contre l'anormal, pas de nouvelles règles
de trading.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import pandas as pd

from .texte import fr

DATA_MIN_SHARE = 0.8
BTC_MOVE_MAX = 0.15
DAILY_LOSS_MAX = 0.08
DISK_MIN_GB = 1.0

Check = Dict[str, Any]


def _check(label: str, ok: Optional[bool], detail: str) -> Check:
    return {"label": label, "ok": ok, "detail": detail}


def checks(day: str, close: pd.DataFrame, equity: Optional[float], prev_equity: Optional[float],
           disk_free_gb: Optional[float]) -> List[Check]:
    """Les vérifications du jour, chacune conforme (True), en défaut
    (False) ou impossible à faire (None, sans effet)."""
    out = []
    try:
        i = close.index.get_loc(pd.Timestamp(day, tz="UTC"))
    except KeyError:
        return [_check("Données du jour", False, f"aucune clôture du {day}")]
    row = close.iloc[i]
    valid = sum(1 for v in row.values if isinstance(v, (int, float)) and math.isfinite(v) and v > 0)
    share = valid / max(1, len(row))
    out.append(_check("Données du jour", share >= DATA_MIN_SHARE,
                      f"{valid} cryptos sur {len(row)} ont une clôture valable"))
    if i > 0:
        b0, b1 = float(close["btc"].iloc[i - 1]), float(close["btc"].iloc[i])
        if b0 > 0 and math.isfinite(b0) and math.isfinite(b1):
            move = b1 / b0 - 1
            out.append(_check("Mouvement de BTC", abs(move) < BTC_MOVE_MAX,
                              f"{fr(move * 100, '+.1f')} % en un jour (limite ±{fr(BTC_MOVE_MAX * 100, '.0f')} %)"))
    if equity and prev_equity and prev_equity > 0:
        change = equity / prev_equity - 1
        out.append(_check("Perte du jour", change > -DAILY_LOSS_MAX,
                          f"capital {fr(change * 100, '+.1f')} % depuis la décision précédente "
                          f"(limite −{fr(DAILY_LOSS_MAX * 100, '.0f')} %)"))
    if disk_free_gb is not None:
        out.append(_check("Place sur le disque", disk_free_gb >= DISK_MIN_GB,
                          f"{fr(disk_free_gb, '.1f')} Go libres (au moins {fr(DISK_MIN_GB, '.0f')} Go)"))
    return out


def blocking(found: List[Check]) -> List[str]:
    """Raisons d'un « NO TRADE » (vide : achats permis)."""
    return [f"{c['label'].lower()} : {c['detail']}" for c in found if c["ok"] is False]
