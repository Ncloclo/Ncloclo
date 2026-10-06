"""
Qualité des données (prompt maître §11, docs/PLATEFORME.md) : avant chaque
décision, le bot note les clôtures qu'il va utiliser, sur 100.

Vérifications, sur l'année écoulée de chaque crypto (depuis sa cotation) :
  - dates : dernière bougie = jour de la décision, une bougie par jour, sans
    doublon ni trou ;
  - valeurs : aucun prix manquant, nul ou négatif ;
  - prix figés : pas de cours identique trois jours de suite (cotation
    arrêtée ou flux bloqué) ;
  - mouvements extrêmes : plus de 50 % en un jour, à vérifier (souvent réels
    en crypto, parfois une donnée aberrante).

Une information pour le rapport et le raisonnement ; la garde « NO TRADE »
(garde.py) bloque déjà les achats quand trop de clôtures manquent le jour même.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from .texte import fr

YEAR = 365
EXTREME = 0.5           # mouvement d'un jour « à vérifier »
STALE_DAYS = 3          # cours identique ce nombre de jours de suite
OHLCV_DAYS = 90         # bougies vérifiées (contrat OHLCV.v1) : la fenêtre des indicateurs


def _stale_runs(s: pd.Series, n: int = STALE_DAYS) -> int:
    """Nombre de séries de `n` clôtures identiques ou plus."""
    v = s.dropna().values
    runs, k = 0, 1
    for i in range(1, len(v)):
        if v[i] == v[i - 1]:
            k += 1
            if k == n:
                runs += 1
        else:
            k = 1
    return runs


def quality(close: pd.DataFrame, day: str, ohlcv_bad: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Note sur 100 des clôtures, problèmes trouvés en clair ; `ohlcv_bad` :
    cryptos écartées du jour pour bougies incohérentes (contrat OHLCV.v1)."""
    issues: List[str] = []
    score = 100.0
    if ohlcv_bad:
        score -= min(30, 10 * len(ohlcv_bad))
        issues.append(f"{sum(ohlcv_bad.values())} bougie(s) incohérente(s) (plus haut, plus bas ou volume) : "
                      f"{', '.join(a.upper() for a in sorted(ohlcv_bad))} écartée(s) du jour")
    idx = close.index
    if idx.has_duplicates:
        score -= 30
        issues.append(f"{int(idx.duplicated().sum())} bougie(s) en double")
    if not idx.is_monotonic_increasing:
        score -= 30
        issues.append("bougies dans le désordre")
    last = str(idx[-1].date()) if len(idx) else None
    if last != day:
        score -= 30
        issues.append(f"dernière bougie du {last}, attendue du {day}")
    recent = close.iloc[-YEAR:]
    gaps = int((recent.index.to_series().diff().dt.days.fillna(1) > 1).sum())
    if gaps:
        score -= min(20, 5 * gaps)
        issues.append(f"{gaps} jour(s) sans bougie sur l'année")
    missing = bad = stale = extreme = 0
    extreme_assets = []
    for a in recent.columns:
        s = recent[a]
        listed = s.first_valid_index()
        if listed is None:
            continue
        s = s.loc[listed:]
        missing += int(s.isna().sum())
        bad += int((s <= 0).sum())
        stale += _stale_runs(s)
        moves = s.pct_change().abs()
        n = int((moves > EXTREME).sum())
        if n:
            extreme += n
            extreme_assets.append(a.upper())
    if missing:
        score -= min(20, missing)
        issues.append(f"{missing} clôture(s) manquante(s) sur l'année")
    if bad:
        score -= min(30, 10 * bad)
        issues.append(f"{bad} prix nul(s) ou négatif(s)")
    if stale:
        score -= min(10, 2 * stale)
        issues.append(f"{stale} cours figé(s) {STALE_DAYS} jours de suite")
    if extreme:
        issues.append(f"{extreme} mouvement(s) de plus de {fr(EXTREME * 100, '.0f')} % en un jour, à vérifier "
                      f"({', '.join(sorted(set(extreme_assets))[:5])})")
    score = max(0.0, score)
    text = (f"{fr(score, '.0f')}/100" + (" : " + " ; ".join(issues) if issues else
                                          " : dates, valeurs et cours de l'année sans défaut"))
    return {"day": day, "score": round(score, 1), "issues": issues, "text": text}
