"""
Régimes de marché (prompt maître §8, docs/PLATEFORME.md) : chaque jour,
l'état du marché crypto en quatre lectures, toutes causales (seules les
clôtures jusqu'au jour lu servent) :

  - tendance : BTC au-dessus (ou au-dessous) de sa moyenne 150 jours, et
    cette moyenne qui monte (ou descend) sur 20 jours ; sinon « sans
    tendance » (marché en range) ;
  - volatilité : volatilité de BTC sur 30 jours, comparée à celle des trois
    années précédentes (forte au-dessus du 75e centile, faible sous le 25e) ;
  - appétit pour le risque : part des cryptos au-dessus de leur moyenne 150
    jours (« risk-on » à 60 % au moins, « risk-off » à 40 % au plus) ;
  - phase : « crise » quand BTC est à 40 % ou plus sous son plus haut d'un an,
    « reprise » quand il remonte au-dessus de sa moyenne 50 jours en étant
    encore à 20 % ou plus sous ce plus haut.

Le bot ne décide rien sur ces lectures : sa règle de marché reste BTC au-dessus
de sa moyenne 150 jours. Elles servent à savoir dans quelles conditions la
stratégie gagne ou échoue (research/regimes.py, docs/REGIMES.md), au journal
des trades et au raisonnement affiché.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List

import numpy as np
import pandas as pd

from .texte import fr

DIMENSIONS = ("tendance", "volatilite", "appetit", "phase")
LABELS = {"tendance": "Tendance de BTC", "volatilite": "Volatilité de BTC",
          "appetit": "Appétit pour le risque", "phase": "Phase du marché"}


def regime_frame(close: pd.DataFrame, sma_n: int = 150) -> pd.DataFrame:
    """Les quatre lectures du marché, jour par jour."""
    btc = close["btc"].astype(float)
    sma = btc.rolling(sma_n, min_periods=sma_n).mean()
    slope = sma.pct_change(20)
    trend = np.where((btc > sma) & (slope > 0), "haussière",
                     np.where((btc < sma) & (slope < 0), "baissière", "sans tendance"))
    trend = pd.Series(trend, index=close.index).where(sma.notna())
    vol = btc.pct_change().rolling(30, min_periods=30).std() * math.sqrt(365)
    rank = vol.rolling(1095, min_periods=365).rank(pct=True)
    volat = pd.Series(np.where(rank >= 0.75, "forte", np.where(rank <= 0.25, "faible", "normale")),
                      index=close.index).where(rank.notna())
    above = close.gt(close.rolling(sma_n, min_periods=sma_n).mean())
    counted = close.rolling(sma_n, min_periods=sma_n).mean().notna()
    breadth = above.where(counted).sum(axis=1) / counted.sum(axis=1).replace(0, np.nan)
    appetite = pd.Series(np.where(breadth >= 0.6, "risk-on", np.where(breadth <= 0.4, "risk-off", "mitigé")),
                         index=close.index).where(breadth.notna())
    dd = 1 - btc / btc.rolling(365, min_periods=1).max()
    above50 = btc > btc.rolling(50, min_periods=50).mean()
    phase = pd.Series(np.where(dd >= 0.4, "crise", np.where((dd >= 0.2) & above50, "reprise", "normale")),
                      index=close.index)
    return pd.DataFrame({"tendance": trend, "volatilite": volat, "appetit": appetite, "phase": phase,
                         "breadth": breadth, "vol": vol, "drawdown": dd})


def at(frame: pd.DataFrame, day: str) -> Dict[str, Any]:
    """Les lectures d'un jour (vide s'il manque)."""
    try:
        row = frame.loc[pd.Timestamp(day, tz="UTC")]
    except KeyError:
        return {}
    out = {k: row[k] for k in DIMENSIONS if isinstance(row[k], str)}
    if out:
        out["texte"] = describe(out)
    return out


def describe(r: Dict[str, Any]) -> str:
    """« tendance haussière, volatilité normale, risk-on » (+ crise/reprise)."""
    parts = []
    if r.get("tendance"):
        parts.append(f"tendance {r['tendance']}")
    if r.get("volatilite"):
        parts.append(f"volatilité {r['volatilite']}")
    if r.get("appetit"):
        parts.append(r["appetit"])
    if r.get("phase") in ("crise", "reprise"):
        parts.append(r["phase"])
    return ", ".join(parts)


def by_regime(trades: Iterable[Dict[str, Any]], frame: pd.DataFrame) -> Dict[str, List[Dict[str, Any]]]:
    """Résultats des trades selon le régime du jour de l'achat, pour chaque
    lecture : nombre, part de gagnants, R moyen et total."""
    rows = []
    for t in trades:
        d = pd.Timestamp(t["entry_date"])
        d = d.tz_localize("UTC") if d.tzinfo is None else d.tz_convert("UTC")
        day = d.normalize()
        if day not in frame.index:
            continue
        rows.append({**{k: frame.at[day, k] for k in DIMENSIONS}, "r": float(t["r"])})
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not rows:
        return out
    df = pd.DataFrame(rows)
    for k in DIMENSIONS:
        stats = []
        for value, g in df.dropna(subset=[k]).groupby(k):
            stats.append({"regime": value, "trades": int(len(g)), "win_pct": round(float((g["r"] > 0).mean() * 100), 1),
                          "avg_r": round(float(g["r"].mean()), 2), "total_r": round(float(g["r"].sum()), 1)})
        out[k] = sorted(stats, key=lambda s: -s["trades"])
    return out


def failures(stats: Dict[str, List[Dict[str, Any]]], min_trades: int = 10) -> List[str]:
    """Les conditions où la stratégie a perdu en moyenne (au moins
    `min_trades` trades), en clair."""
    out = []
    for k, rows in stats.items():
        for s in rows:
            if s["trades"] >= min_trades and s["avg_r"] < 0:
                out.append(f"{LABELS[k].lower()} {s['regime']} : {s['trades']} trades, "
                           f"{fr(s['avg_r'], '+.2f')} R en moyenne")
    return out
