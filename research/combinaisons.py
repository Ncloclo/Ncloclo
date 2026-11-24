#!/usr/bin/env python3
"""
Combiner TrendGuard avec d'autres stratégies fait-il mieux que TrendGuard
seule ? Étude reproductible (docs/COMBINAISONS.md).

Sur les 24 derniers mois, la cassure de Bollinger et les Tortues font mieux
que TrendGuard (diagnostic). Plutôt que d'en changer, ce qui a échoué dans le
laboratoire (docs/STRATEGIES.md, section 2), on teste des portefeuilles de
« manches » : chaque stratégie gère une part fixe du capital, avec 1 % de
risque par trade sur sa part (rendements mêlés chaque jour, comme dans le
laboratoire).

Protocole fixé avant de regarder les résultats :
  - combinaisons déclarées à l'avance (liste COMBOS), sans en chercher
    d'autres après coup ;
  - choix sur 2018-2022 (meilleur Calmar), vérification depuis 2023, et la
    période jamais vue au gel des paramètres (24/05/2026 →) ;
  - une combinaison n'est proposée que si elle bat TrendGuard seule sur les
    DEUX périodes ; elle n'est jamais appliquée sans votre accord.

  python -m research.combinaisons --cache data_binance
"""

from __future__ import annotations

import sys
from typing import Dict, List, Optional, Tuple

import pandas as pd

from research.commun import load_binance, study_args, write_report
from trendguard import strategy_lab as sl
from trendguard.texte import fr

TURTLE = "Tortues, système 1 (R. Dennis)"
BOLL = "Cassure de Bollinger (J. Bollinger)"
FAST = "Tendance rapide"
# Nom affiché → manches (parts égales). Déclarées avant toute mesure.
COMBOS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("TrendGuard seule (référence)", (sl.REF,)),
    ("TrendGuard + Tortues", (sl.REF, TURTLE)),
    ("TrendGuard + Bollinger", (sl.REF, BOLL)),
    ("TrendGuard + Tortues + Bollinger", (sl.REF, TURTLE, BOLL)),
    ("TrendGuard + Tendance rapide", (sl.REF, FAST)),
)
DESIGN = ("2018-01-01", "2022-12-31")
VERIFY_START = "2023-01-01"
UNSEEN_START = "2026-05-24"


def sleeves(close: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    """Rendements journaliers de chaque stratégie utilisée, de 2018 à la
    dernière bougie (une seule simulation par stratégie)."""
    lab = sl.Lab(close, volume)
    last = str(close.index[-1].date())
    names = sorted({n for _label, pool in COMBOS for n in pool})
    curves = {n: lab.run(n, DESIGN[0], last)[0] for n in names}
    return pd.DataFrame(curves).pct_change().fillna(0.0)


def measure(rets: pd.DataFrame, periods: List[Tuple[str, str]]) -> Dict[str, List[Dict[str, float]]]:
    """Statistiques de chaque combinaison sur chaque période."""
    out: Dict[str, List[Dict[str, float]]] = {}
    for label, pool in COMBOS:
        r = sl.blend(rets, list(pool))
        out[label] = [dict(sl.curve_stats(r.loc[a:b]), total_pct=float(((1 + r.loc[a:b]).prod() - 1) * 100))
                      for a, b in periods]
    return out


def verdict(stats: Dict[str, List[Dict[str, float]]]) -> Tuple[str, List[str]]:
    """(choisie sur la période de conception, combinaisons qui battent la
    référence sur conception ET vérification, au Calmar)."""
    ref = COMBOS[0][0]
    chosen = max(stats, key=lambda k: stats[k][0]["calmar"])
    both = [k for k in stats if k != ref and stats[k][0]["calmar"] > stats[ref][0]["calmar"]
            and stats[k][1]["calmar"] > stats[ref][1]["calmar"]]
    return chosen, both


def render(stats: Dict[str, List[Dict[str, float]]], corr: pd.DataFrame, end: str) -> str:
    """Rapport Markdown : tableau, corrélations, verdict et limites."""
    head = ("| Combinaison | 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | "
            "24/05/2026 → : rendement | baisse |")
    lines = [
        "# Combiner TrendGuard avec d'autres stratégies ?", "",
        f"Données Binance des 21 paires du bot jusqu'au {end}, frais et glissement de 0,1 % par "
        "côté, 1 % de risque par trade dans chaque manche. Régénérer : `python -m "
        "research.combinaisons --cache data_binance`.", "",
        "Protocole fixé avant les mesures : combinaisons déclarées à l'avance, choix sur 2018-2022, "
        "vérification depuis 2023 ; une combinaison n'est proposée que si elle bat TrendGuard seule "
        "sur les deux périodes.", "",
        head, "|---|---|---|---|---|---|---|---|---|",
    ]
    for label, (d, v, u) in stats.items():
        lines.append(f"| {label} | {fr(d['cagr_pct'], '+.1f')} % | {fr(d['max_dd_pct'], '.1f')} % | "
                     f"{fr(d['calmar'], '.2f')} | {fr(v['cagr_pct'], '+.1f')} % | "
                     f"{fr(v['max_dd_pct'], '.1f')} % | {fr(v['calmar'], '.2f')} | "
                     f"{fr(u['total_pct'], '+.1f')} % | "
                     f"{fr(u['max_dd_pct'], '.1f')} % |")
    lines += ["", "Corrélation des rendements journaliers des manches (2018 →) :", "",
              "| | " + " | ".join(corr.columns) + " |", "|---" * (len(corr.columns) + 1) + "|"]
    for name, row in corr.iterrows():
        lines.append(f"| {name} | " + " | ".join(fr(x, ".2f") for x in row) + " |")
    chosen, both = verdict(stats)
    lines += ["", "## Verdict", "",
              f"- Choisie sur 2018-2022 (meilleur Calmar) : **{chosen}**.",
              "- Battent TrendGuard seule sur les deux périodes : "
              + (", ".join(f"**{k}**" for k in both) if both else "**aucune**") + "."]
    if chosen == COMBOS[0][0] or chosen not in both:
        lines.append("- **Aucune combinaison ne justifie de changer** : la règle fixée d'avance n'en "
                     "retient aucune. TrendGuard reste seule.")
    else:
        lines.append(f"- **{chosen}** passe la règle fixée d'avance : c'est une proposition, à "
                     "valider par vous avant toute application, après une période en paper.")
    lines += ["", "## Limites", "",
              "- Un backtest n'est pas une garantie de gain.",
              "- Période jamais vue : portefeuilles lancés en 2018 et mesurés depuis le 24/05/2026, "
              "positions déjà ouvertes comprises ; un départ à zéro ce jour-là donne des chiffres "
              "un peu différents (`docs/PETIT_CAPITAL.md`).",
              "- Les stratégies de suivi de tendance sont corrélées : elles perdent souvent "
              "ensemble, ce qui limite le gain de la diversification.",
              "- Rendements des manches mêlés chaque jour : en réel, chaque manche aurait sa part "
              "du capital, rééquilibrée moins souvent ; avec 100 USDT, chaque manche achèterait "
              "des positions plus petites (minimum de Binance, `docs/PETIT_CAPITAL.md`)."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Mesure les combinaisons et écrit docs/COMBINAISONS.md."""
    args = study_args("Combinaisons de stratégies", "docs/COMBINAISONS.md", argv)
    close, volume = load_binance(args.cache)
    end = str(close.index[-1].date())
    rets = sleeves(close, volume)
    periods = [DESIGN, (VERIFY_START, end), (UNSEEN_START, end)]
    stats = measure(rets, periods)
    write_report(render(stats, rets.corr(), end), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
