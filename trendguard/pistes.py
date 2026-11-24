"""
Le bot teste lui-même les pistes de sa bibliothèque de recherche
(bibliotheque.py, docs/BIBLIOTHEQUE.md).

Un travail lu pose une question (« ce stop réduit-il la baisse maximale ? »).
Le bot la pose à ses propres données : chaque thème a sa famille de variantes,
déclarée à l'avance, rejouée sur l'historique Binance des 21 paires avec la
boucle de backtest du bot (2018-2022, puis depuis 2023). Ce n'est pas la
méthode exacte du travail : c'est sa question, posée aux données du bot.

Verdict par thème :
  - « la règle actuelle tient » : aucune variante ne fait mieux (Calmar) sur
    les deux périodes ;
  - « à étudier » : une variante fait mieux sur les deux périodes. C'est une
    proposition, jamais appliquée seule : elle passe par une étude complète
    (research/) et votre accord ;
  - coûts : épreuve de résistance (frais et glissement doublés).

Une fois par semaine, dans un processus séparé ; verdicts gardés dans le
noyau de savoir (méta « bibliotheque_verdicts »), montrés avec les pistes.

    python trendguard_bot.py bibliotheque tester
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from . import trend_strategy as ts
from .texte import fr

KEY = "bibliotheque_verdicts"       # méta du noyau de savoir (à part : jamais écrasée par une lecture)
EVERY_DAYS = 7                      # une fois par semaine : l'historique change peu
DESIGN = ("2018-01-01", "2022-12-31")
VERIFY_START = "2023-01-01"

# Thème → variantes (libellé, réglages changés). Déclarées avant toute mesure.
FAMILIES: Dict[str, Tuple[Tuple[str, Dict[str, Any]], ...]] = {
    "tendance": (("cassure de 20 jours, momentum de 60 jours", {"breakout_n": 20, "mom_n": 60}),
                 ("cassure de 55 jours, momentum de 180 jours", {"breakout_n": 55, "mom_n": 180})),
    "momentum": (("momentum de 60 jours", {"mom_n": 60}), ("momentum de 180 jours", {"mom_n": 180})),
    "risque": (("profil prudent : risque divisé par 2 au-delà de −10 %", {"dd_throttle": ((0.10, 0.5),)}),
               ("stop initial à 2 volatilités", {"init_stop_atr": 2.0}),
               ("stop initial à 4 volatilités", {"init_stop_atr": 4.0})),
    "regime": (("régime : BTC au-dessus de sa moyenne de 100 jours", {"regime_sma": 100}),
               ("régime : BTC au-dessus de sa moyenne de 200 jours", {"regime_sma": 200})),
    "couts": (("frais et glissement doublés", {"fee": 0.002, "slippage": 0.002}),),
}
STRESS = {"couts"}                  # épreuve de résistance, pas une amélioration possible
# Thèmes déjà mesurés ailleurs, en continu : leur verdict renvoie à la mesure.
STANDING = {
    "crypto": "les 21 paires du bot, liquidité d'au moins 5 M$ par jour ; chaque crypto est jugée par "
              "son bénéfice sur la stratégie (docs/SELECTION.md)",
    "sentiment": "chaque source d'opinion est jugée chaque semaine sur les cours réels par le noyau de "
                 "savoir ; aucune n'influence le bot sans 20 semaines de preuve (docs/SAVOIR.md)",
    "methode": "paramètres gelés depuis le 23/05/2026, choix sur 2018-2022 et vérification depuis 2023, "
               "épreuves de robustesse (docs/ROBUSTESSE.md)",
}


def _calmar(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, start: str, end: str) -> float:
    r = ts.backtest(close, volume, p, start, end)
    return float(ts.compute_metrics(r.equity, r.trades).get("calmar", 0.0))


def evaluate(close: pd.DataFrame, volume: pd.DataFrame, base: Optional[ts.TrendParams] = None
             ) -> Dict[str, Dict[str, Any]]:
    """Verdict de chaque thème testable : la règle actuelle et ses variantes,
    Calmar sur la période de conception puis de vérification."""
    base = base or ts.TrendParams()
    end = str(close.index[-1].date())
    periods = (DESIGN, (VERIFY_START, end))
    ref = [_calmar(close, volume, base, a, b) for a, b in periods]
    out: Dict[str, Dict[str, Any]] = {}
    for theme, variants in FAMILIES.items():
        rows = []
        for label, change in variants:
            p = dataclasses.replace(base, **change)
            rows.append({"label": label, "calmar": [_calmar(close, volume, p, a, b) for a, b in periods]})
        out[theme] = verdict(theme, ref, rows)
    return out


def verdict(theme: str, ref: List[float], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verdict d'un thème d'après les Calmar (conception, vérification) de la
    règle actuelle et de ses variantes."""
    def pair(c: List[float]) -> str:
        return f"Calmar {fr(c[0], '.2f')} puis {fr(c[1], '.2f')}"
    base = f"règle actuelle : {pair(ref)}"
    if theme in STRESS:
        r = rows[0]
        holds = min(r["calmar"]) > 0
        return {"status": "tient" if holds else "fragile", "rows": rows, "ref": ref,
                "text": (f"{r['label']} : {pair(r['calmar'])} ({base}) — "
                         + ("la stratégie reste rentable." if holds else "la stratégie ne résiste pas."))}
    better = [r for r in rows if r["calmar"][0] > ref[0] and r["calmar"][1] > ref[1]]
    if better:
        best = max(better, key=lambda r: min(r["calmar"]))
        return {"status": "a_etudier", "rows": rows, "ref": ref,
                "text": (f"à étudier : « {best['label']} » fait mieux sur les deux périodes "
                         f"({pair(best['calmar'])} ; {base}). Jamais appliqué seul : étude complète, "
                         "puis votre accord.")}
    best = max(rows, key=lambda r: min(r["calmar"]))
    return {"status": "tient", "rows": rows, "ref": ref,
            "text": (f"la règle actuelle tient : aucune variante ne fait mieux sur 2018-2022 et depuis 2023 "
                     f"(la plus proche : « {best['label']} », {pair(best['calmar'])} ; {base}).")}


def store(memory: Any, results: Dict[str, Dict[str, Any]], today: str) -> Dict[str, Any]:
    """Garde les verdicts (et ceux des thèmes mesurés ailleurs) dans le
    noyau de savoir."""
    verdicts = {k: dict(v, day=today) for k, v in results.items()}
    for k, text in STANDING.items():
        verdicts[k] = {"status": "mesure_continue", "text": text, "day": today}
    book = {"day": today, "verdicts": verdicts}
    memory.put(KEY, book)
    return book


def due(book: Any, today: str) -> bool:
    """À refaire : jamais fait, ou fait il y a au moins EVERY_DAYS jours."""
    day = (book or {}).get("day") if isinstance(book, dict) else None
    if not day:
        return True
    try:
        return (datetime.fromisoformat(today) - datetime.fromisoformat(day)).days >= EVERY_DAYS
    except ValueError:
        return True


def run(memory: Any, cache: str, universe: List[str]) -> Dict[str, Any]:
    """`bibliotheque tester` : charge l'historique (celui de l'évolution
    encadrée, rafraîchi s'il date), teste chaque thème, garde les verdicts."""
    from .evolution import load_history
    close, volume = load_history(cache, universe)
    today = datetime.now(timezone.utc).date().isoformat()
    return store(memory, evaluate(close, volume), today)


def render(book: Any) -> str:
    """Les verdicts, un thème par ligne."""
    from .bibliotheque import LABEL
    if not isinstance(book, dict) or not book.get("verdicts"):
        return "Pistes : pas encore testées."
    lines = [f"Pistes testées sur les données du bot le {book['day']} :"]
    for k, v in book["verdicts"].items():
        lines.append(f"- {LABEL.get(k, k)} : {v['text']}")
    return "\n".join(lines)
