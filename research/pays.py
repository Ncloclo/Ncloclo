#!/usr/bin/env python3
"""
Les pays du monde, classés (docs/PAYS.md ; données : trendguard/pays.py) :
le classement MSCI de chaque pays, ce que le bot en suit chaque jour, et une
question : la règle de tendance du bot marche-t-elle mieux sur les marchés
développés, émergents, frontières ou autonomes ?

Protocole fixé avant les mesures :
  - un portefeuille par groupe MSCI (frontières avancés et frontières
    ensemble), fait des indices que Yahoo Finance fournit avec leur
    historique (indice local, sinon fonds coté à New York en dollars) ;
  - règle de TrendGuard transposée (multi_actifs : séances réelles de chaque
    marché, régime propre), paramètres gelés, coût réel des indices ;
  - comparée à l'achat-conservation à parts égales du même groupe ;
  - périodes : 2018-2022, depuis 2023, et 24/05/2026 → (information) ;
  - la règle « tient » sur un groupe si elle bat l'achat-conservation au
    Calmar sur 2018-2022 ET depuis 2023. Information seulement : rien n'entre
    dans un portefeuille, aucun ordre réel.

  python -m research.pays --cache data_multi
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from research.commun import study_args, write_report
from trendguard import multi_actifs as ma
from trendguard import pays
from trendguard import trend_strategy as ts
from trendguard.texte import fr

DESIGN = ("2018-01-01", "2022-12-31")
VERIFY_START = "2023-01-01"
UNSEEN_START = "2026-05-24"
PLURAL = {"Développé": "développés", "Émergent": "émergents", "Frontière avancé": "frontières avancés",
          "Frontière": "frontières", "Autonome": "autonomes"}
GROUPS = (("Développés", ("Développé",)), ("Émergents", ("Émergent",)),
          ("Frontières", ("Frontière avancé", "Frontière")), ("Autonomes", ("Autonome",)))


def _m(m: Dict[str, float]) -> str:
    return (f"{fr(m.get('cagr_pct', 0.0), '+.1f')} % | {fr(m.get('max_dd_pct', 0.0), '.1f')} % | "
            f"{fr(m.get('calmar', 0.0), '.2f')}")


def measure(close: pd.DataFrame, codes: List[str], periods: List[Tuple[str, str]]) -> Dict[str, Any]:
    """Règle et achat-conservation d'un groupe, sur chaque période."""
    sub = close[codes]
    rule = [ma.backtest(sub, a, b, real_costs=True) for a, b in periods]
    hold = [ts.buy_and_hold(sub, codes, a, b) for a, b in periods]
    return {"rule": [r.metrics for r in rule], "hold": hold}


def render(close: pd.DataFrame, results: Dict[str, Dict[str, Any]], end: str) -> str:
    """Rapport Markdown."""
    names = ["2018-2022", f"2023 → {end}", f"24/05/2026 → {end}"]
    count = pays.classes_count()
    lines = [
        "# Les pays du monde, classés", "",
        f"Classement MSCI de juin 2026 (revue d'accessibilité des marchés, annexe II, et résultats de la revue de "
        f"classement du 23/06/2026) ; cours de Yahoo Finance jusqu'au {end}. Régénérer : "
        "`python -m research.pays --cache data_multi`. Le panneau les montre dans la page « Pays du monde », avec "
        "les chiffres de la Banque mondiale (API ouverte, relue chaque semaine).", "",
        f"{len(pays.COUNTRIES)} pays : " + ", ".join(f"{n} {PLURAL[k] if n > 1 else k.lower()}"
                                                  for k, n in count.items()) + ".", "",
        "## Le classement, par région", ""]
    for region in pays.REGIONS:
        rows = [c for c in pays.COUNTRIES if c[2] == region]
        lines += [f"### {region}", "", "| Pays | Classement MSCI | Indice suivi | Devise | Remarque |",
                  "|---|---|---|---|---|"]
        for iso, name, _r, msci, note, code, cur in rows:
            index = ma.NAME.get(code, "") if code and code in close.columns else "aucun (pas d'historique chez Yahoo)"
            lines.append(f"| {pays.flag(iso)} {name} | {msci} | {index} | {cur} | {note} |")
        lines.append("")
    lines += ["## La règle du bot par groupe de marchés", "",
              "| Groupe | Indices | " + " | ".join(f"{n} : règle CAGR | baisse | Calmar | achat-conservation CAGR | "
                                                   f"baisse | Calmar" for n in names) + " |",
              "|---|---|" + "---|---|---|---|---|---|" * len(names)]
    verdicts = []
    for label, res in results.items():
        cells = " | ".join(f"{_m(r)} | {_m(h)}" for r, h in zip(res["rule"], res["hold"]))
        lines.append(f"| {label} | {res['n']} | {cells} |")
        holds = all(res["rule"][k].get("calmar", 0.0) > res["hold"][k].get("calmar", 0.0) for k in (0, 1))
        verdicts.append((label, holds))
    lines += ["", "## Verdict (règle fixée avant les mesures)", ""]
    lines += [f"- {label} : la règle bat l'achat-conservation au Calmar sur 2018-2022 et depuis 2023 : "
              f"**{'oui' if ok else 'non'}**"
              + (f" (échantillon de {results[label]['n']} indice{'s' if results[label]['n'] > 1 else ''} : "
                 "trop petit pour conclure)" if results[label]["n"] < 5 else "") + "."
              for label, ok in verdicts]
    lines += ["- Information seulement : ces marchés restent suivis pour la connaissance du bot (pages Indices et "
              "Pays du monde) ; rien n'entre dans un portefeuille, aucun ordre réel.",
              "", "## Limites", "",
              "- Un backtest n'est pas une garantie de gain.",
              "- Indices choisis par disponibilité chez Yahoo : 49 pays sur 87 ; les marchés frontières et "
              "autonomes sont peu représentés (la bourse régionale BRVM, par exemple, n'y est pas).",
              "- Les fonds cotés à New York sont en dollars : ils mêlent la bourse du pays et sa devise ; les "
              "indices locaux sont en monnaie locale.",
              "- Le classement MSCI change : Grèce développée et Bulgarie frontière en mai 2027 ; Indonésie et "
              "Turquie sous surveillance à la revue de novembre 2026."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Mesure chaque groupe sur l'historique gardé, écrit le rapport."""
    args = study_args("Pays du monde", "docs/PAYS.md", argv)
    codes = [c[5] for c in pays.COUNTRIES if c[5]]
    close = ma.load(args.cache, codes)
    end = str(close.index[-1].date())
    periods = [DESIGN, (VERIFY_START, end), (UNSEEN_START, end)]
    results = {}
    for label, classes in GROUPS:
        group = [c[5] for c in pays.COUNTRIES if c[3] in classes and c[5] and c[5] in close.columns]
        if group:
            results[label] = dict(measure(close, group, periods), n=len(group))
    write_report(render(close, results, end), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
