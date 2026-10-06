"""
Risque d'un jour du portefeuille (prompt maître §13, §23, docs/PLATEFORME.md) :
valeur à risque (VaR) et perte moyenne au-delà (CVaR), par simulation
historique. Chaque jour de l'année écoulée est rejoué avec les positions
d'aujourd'hui : « 1 jour sur 20, le portefeuille perdrait plus de X % ; ces
jours-là, Y % en moyenne ». Sans tenir compte des stops (le pire cas), et sans
promettre l'avenir : une mesure, pas une prévision.
"""

from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .texte import fr

LEVEL = 0.95
DAYS = 365


def var_cvar(close: pd.DataFrame, values: Dict[str, float], equity: float,
             level: float = LEVEL, days: int = DAYS) -> Optional[Dict[str, float]]:
    """VaR et CVaR d'un jour, en % du capital, des positions `values`
    (valeur en USDT de chaque crypto détenue) ; None sans position ou sans
    historique suffisant."""
    held = [a for a, v in values.items() if v > 0 and a in close.columns]
    if not held or not equity or equity <= 0:
        return None
    rets = close[held].pct_change().iloc[-days:].dropna(how="all").fillna(0.0)
    if len(rets) < 60:
        return None
    weights = np.array([values[a] / equity for a in held])
    port = rets.values @ weights
    q = float(np.percentile(port, (1 - level) * 100))
    tail = port[port <= q]
    var = -q * 100
    cvar = -float(tail.mean()) * 100 if len(tail) else var
    if not (math.isfinite(var) and math.isfinite(cvar)):
        return None
    return {"var_pct": round(max(0.0, var), 2), "cvar_pct": round(max(0.0, cvar), 2),
            "invested_pct": round(float(weights.sum()) * 100, 1), "days": int(len(rets))}


def describe(r: Optional[Dict[str, float]]) -> str:
    if not r:
        return ""
    return (f"Risque d'un jour (année écoulée rejouée, positions actuelles, sans les stops) : 1 jour sur 20, "
            f"perte de plus de {fr(r['var_pct'], '.1f')} % du capital ; ces jours-là, "
            f"{fr(r['cvar_pct'], '.1f')} % en moyenne.")
