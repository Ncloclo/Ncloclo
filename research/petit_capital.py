#!/usr/bin/env python3
"""
Petit capital : le bot fait-il avec 100 USDT ce qu'il fait avec 10 000 ?
Étude reproductible (docs/PETIT_CAPITAL.md).

Avec 1 % de risque par achat et un stop initial à 4,7-14,1 % du prix (P10 à
P90 des entrées depuis 2018), 100 USDT achètent 7 à 20 USDT par position
(25 au plus). La boucle du bot (trend_strategy.backtest), paramètres gelés,
est rejouée avec plusieurs capitaux de départ et deux règles de minimum :

  1. l'ancienne (jusqu'au 24/11/2026) : aucun achat sous 10 USDT ;
  2. l'actuelle (trend_strategy.size_position) : le minimum de Binance
     (5 USDT, filtre NOTIONAL) + 5 %, compté sur la valeur de la position
     revendue à son stop, car la vente est soumise au même minimum.

Le minimum vient des règles de Binance, pas des résultats : rien n'est réglé
ici. Comparaison à BTC conservé sur les mêmes périodes.

  python -m research.petit_capital --cache data_binance
"""

from __future__ import annotations

import contextlib
import sys
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from research.commun import load_binance, study_args, write_report
from trendguard import trend_strategy as ts
from trendguard.texte import fr, fr_plain

CAPITALS = (10_000.0, 1_000.0, 100.0, 50.0, 30.0, 20.0)
REFERENCE = 10_000.0
SMALL = 100.0
OLD_MINIMUM = 10.0
Sizer = Callable[..., Optional[Tuple[float, float]]]


def old_size_position(entry: float, unit_risk: float, risk_quote: float, equity: float,
                      cash: float, p: ts.TrendParams) -> Optional[Tuple[float, float]]:
    """La taille d'avant le 24/11/2026, recopiée pour la comparaison : aucun
    achat sous 10 USDT, sans regarder la revente au stop."""
    qty = min(risk_quote / unit_risk, p.max_position_pct * equity / entry)
    cost = qty * entry * (1 + p.fee)
    if cost > cash:
        qty = cash / (entry * (1 + p.fee))
        cost = qty * entry * (1 + p.fee)
    if qty * entry < OLD_MINIMUM:
        return None
    return qty, cost


RULES: Tuple[Tuple[str, Sizer], ...] = (
    ("ancienne (10 USDT à l'achat)", old_size_position),
    ("actuelle (5 USDT + 5 % au stop)", ts.size_position),
)


@contextlib.contextmanager
def sizing(fn: Sizer) -> Iterator[List[int]]:
    """Le temps du bloc, la boucle du bot dimensionne ses achats avec `fn`
    (plan_entries lit size_position dans son module). Rend un compteur des
    achats refusés, un par jour et par crypto signalée."""
    refused = [0]
    current = ts.size_position

    def counted(*args: Any) -> Optional[Tuple[float, float]]:
        out = fn(*args)
        if out is None:
            refused[0] += 1
        return out

    ts.size_position = counted
    try:
        yield refused
    finally:
        ts.size_position = current


def measure(close: Any, volume: Any, eras: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Rendement total, baisse maximale, trades et achats refusés de chaque
    capital et de chaque règle, période par période."""
    p = ts.TrendParams()
    pre = ts.precompute(close, volume, p)
    rows = []
    for capital in CAPITALS:
        for rule, fn in RULES:
            cells = []
            for a, b in eras:
                with sizing(fn) as refused:
                    r = ts.backtest(close, volume, p, a, b, capital=capital, pre=pre)
                m = r.metrics
                cells.append({"total": m["total_return_pct"], "dd": m["max_dd_pct"],
                              "trades": m["trades"], "refused": refused[0]})
            rows.append({"capital": capital, "rule": rule, "cells": cells})
    return rows


def _gap(rows: List[Dict[str, Any]], capital: float, rule: str) -> float:
    """Plus grand écart de rendement total (en points) avec 10 000 USDT et la
    même règle, sur les périodes étudiées."""
    ref = next(r for r in rows if r["capital"] == REFERENCE and r["rule"] == rule)
    row = next(r for r in rows if r["capital"] == capital and r["rule"] == rule)
    return max(abs(c["total"] - x["total"]) for c, x in zip(row["cells"], ref["cells"]))


def render(rows: List[Dict[str, Any]], eras: List[Tuple[str, str]],
           btc: List[Dict[str, float]], end: str) -> str:
    """Rapport Markdown : tableau, lecture et limites."""
    names = ["2018-2022 (conception)", f"2023 → {end}", f"24/05/2026 → {end} (jamais vue au gel)"]
    head = " | ".join(f"{n} : rendement | baisse | trades" for n in names)
    lines = [
        "# Petit capital : 100 USDT comme 10 000 ?", "",
        f"Données Binance des 21 paires du bot jusqu'au {end}, paramètres gelés, frais et "
        "glissement de 0,1 % par côté. Régénérer : `python -m research.petit_capital --cache "
        "data_binance`.", "",
        "## Le problème", "",
        "Le bot risque 1 % du capital par achat jusqu'au stop initial, placé à 4,7-14,1 % du "
        "prix (P10 à P90 des entrées depuis 2018). Avec 100 USDT, cela fait 7 à 20 USDT par "
        "position (25 au plus). L'ancienne règle refusait tout achat sous 10 USDT : avec un "
        "petit capital, les cryptos les plus agitées n'étaient jamais achetées. Binance "
        "n'exige que 5 USDT par ordre (1 pour DOGE). Mais la revente est soumise au même minimum : "
        "une position qui vaut moins de 5 USDT à son stop ne peut plus être vendue.", "",
        "Règle actuelle (`trend_strategy.size_position`) : pas d'achat si la position, "
        "revendue à son stop (frais et glissement déduits), vaudrait moins de 5 USDT + 5 %. "
        "Le minimum vient des règles de Binance, pas des résultats.", "",
        "## Résultats", "",
        f"| Capital | Règle | {head} | achats refusés |",
        "|---|---|" + "---|---|---|" * len(eras) + "---|",
    ]
    for r in rows:
        cells = " | ".join(f"{fr(c['total'], '+.1f')} % | {fr(c['dd'], '.1f')} % | {c['trades']}"
                           for c in r["cells"])
        refused = " / ".join(str(c["refused"]) for c in r["cells"])
        lines.append(f"| {fr_plain(r['capital'])} USDT | {r['rule']} | {cells} | {refused} |")
    bh = " | ".join(f"{fr(m['total_return_pct'], '+.1f')} % | {fr(m['max_dd_pct'], '.1f')} % | —"
                    for m in btc)
    lines += [f"| BTC conservé | sans rien faire | {bh} | — |", "",
              "Achats refusés : un par jour et par crypto signalée dont la taille passe sous le "
              "minimum, période par période, y compris faute d'argent disponible quand le "
              "portefeuille est déjà investi (d'où les refus à 10 000 USDT).", "", "## Lecture", ""]
    old, new = RULES[0][0], RULES[1][0]
    lines.append(f"- **100 USDT, règle actuelle** : à {fr(_gap(rows, SMALL, new), '.1f')} points au "
                 f"plus du rendement de 10 000 USDT sur chaque période (ancienne règle : "
                 f"{fr(_gap(rows, SMALL, old), '.1f')} points).")
    small = next(r for r in rows if r["capital"] == SMALL and r["rule"] == new)
    before = next(r for r in rows if r["capital"] == SMALL and r["rule"] == old)
    for name, c, b in zip(names, small["cells"], before["cells"]):
        if b["total"] > c["total"] + 0.5:
            lines.append(f"- **{name}** : l'ancienne règle faisait mieux avec 100 USDT "
                         f"({fr(b['total'], '+.1f')} % contre {fr(c['total'], '+.1f')} %). Les "
                         "achats qu'elle refusait ont, sur cette période, plutôt perdu. C'est de la "
                         "chance, pas une règle : ce n'est plus la stratégie validée avec 10 000 USDT.")
    floor = REFERENCE
    for capital in sorted(CAPITALS, reverse=True):
        if _gap(rows, capital, new) > 10.0:
            break
        floor = capital
    none = [r["capital"] for r in rows if r["rule"] == new and not any(c["trades"] for c in r["cells"])]
    lines += [f"- **Capital plancher** : de 10 000 à {fr_plain(floor)} USDT, l'écart avec 10 000 "
              "USDT reste sous 10 points sur chaque période. En dessous, de plus en plus d'achats "
              "sont refusés" + (f", et à {fr_plain(max(none))} USDT plus aucun ne passe" if none else "")
              + ".", "",
              "## Limites", "",
              "- Un backtest n'est pas une garantie de gain, et le passé ne se répète pas.",
              "- Le backtest n'arrondit pas la quantité au pas du lot de chaque paire. Le bot le fait à "
              "l'exécution et revérifie le minimum, à l'achat comme au stop (achat paper au "
              "carnet, porte du réel).",
              "- Les frais de Binance sont proportionnels au montant : ils pèsent autant, en "
              "pourcentage, sur 100 USDT que sur 10 000."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Rejoue les capitaux et les règles, puis écrit docs/PETIT_CAPITAL.md."""
    args = study_args("Petit capital de TrendGuard", "docs/PETIT_CAPITAL.md", argv)
    close, volume = load_binance(args.cache)
    end = str(close.index[-1].date())
    eras = [("2018-01-01", "2022-12-31"), ("2023-01-01", end), ("2026-05-24", end)]
    rows = measure(close, volume, eras)
    btc = [ts.buy_and_hold(close, ["btc"], a, b) for a, b in eras]
    write_report(render(rows, eras, btc, end), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
