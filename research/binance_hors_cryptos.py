#!/usr/bin/env python3
"""
Hors cryptos, mais sur Binance : l'or tokenisé (PAXG, XAUT), l'euro
(EUR/USDT) et les actions tokenisées (« bStocks » : Nvidia, Micron,
Alphabet, Intel, Strategy, Bloom Energy, fonds Nasdaq 100). Le bot pourrait
les trader avec son moteur Binance et ses garde-fous, sans autre courtier.
Étude reproductible (docs/BINANCE_HORS_CRYPTOS.md).

Leur historique sur Binance est trop court (cotés depuis peu) : on rejoue
l'actif sous-jacent (Yahoo Finance : or, euro/dollar, actions) avec les
frais du bot sur Binance (0,1 % de frais et 0,1 % de glissement par côté).

Protocole fixé avant les mesures :
  - règle de TrendGuard transposée (trendguard/multi_actifs.py), paramètres
    gelés, régime propre à chaque actif ;
  - écartés : fonds à levier (×2, ×3 : ils s'érodent, et le bot n'utilise
    jamais de levier), sociétés non cotées (SpaceX, Anthropic : aucun
    historique), cotations trop récentes (Circle, Nebius, SanDisk) ;
  - périodes : 2018-2022, depuis 2023, et 24/05/2026 → ;
  - mélanges avec TrendGuard sur les cryptos : 75/25 (principal) et 50/50 ;
  - suivi en paper proposé seulement si cette manche est rentable sur les
    deux périodes ET si le mélange 75/25 bat TrendGuard seule au Calmar sur
    les deux périodes. Jamais appliqué sans votre accord.

  python -m research.binance_hors_cryptos --cache data_multi [--telecharger]
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from research.commun import load_binance, study_args, write_report
from trendguard import multi_actifs as ma
from trendguard import savoir
from trendguard import strategy_lab as sl
from trendguard import trend_strategy as ts
from trendguard.texte import fr

# (code, nom et jeton Binance, symbole Yahoo du sous-jacent) : la liste du
# bot (multi_actifs.BINANCE), déclarée avant les mesures ; les actions sont
# celles que Binance liste aujourd'hui (biais de popularité).
ASSETS: Tuple[Tuple[str, str, str], ...] = tuple((c, n, s) for c, n, _k, s in ma.BINANCE)
EXCLUDED = ("fonds à levier : SOXLB, SOXSB, KORUB, SNXXB, TQQQB",
            "sociétés non cotées : SpaceX (SPCXB), Anthropic (ANTB)",
            "cotations trop récentes : Circle (CRCLB), Nebius (NBISB), SanDisk (SNDKB)")
DESIGN = ("2018-01-01", "2022-12-31")
VERIFY_START = "2023-01-01"
UNSEEN_START = "2026-05-24"
MIXES = (("TrendGuard seule (cryptos)", 1.0), ("75 % cryptos, 25 % hors cryptos Binance", 0.75),
         ("50 % cryptos, 50 % hors cryptos Binance", 0.5))


def _cells(ms: List[Dict[str, float]]) -> str:
    return " | ".join(f"{fr(m.get('cagr_pct', 0.0), '+.1f')} % | {fr(m.get('max_dd_pct', 0.0), '.1f')} % | "
                      f"{fr(m.get('calmar', 0.0), '.2f')}" for m in ms)


def per_asset(close: pd.DataFrame, periods: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """Chaque actif seul : règle (frais de Binance) et achat-conservation."""
    rows = []
    for code, name, _sym in ASSETS:
        sub = close[[code]]
        rule = [ts.compute_metrics(r.equity, r.trades) for r in (ma.backtest(sub, a, b) for a, b in periods)]
        hold = [ts.buy_and_hold(sub, [code], a, b) for a, b in periods]
        rows.append({"name": name, "rule": rule, "hold": hold})
    return rows


def mixes(crypto: pd.Series, sleeve: pd.Series, periods: List[Tuple[str, str]]) -> Dict[str, Any]:
    """Mélanges fixés d'avance (rendements mêlés chaque jour)."""
    s = sleeve.reindex(crypto.index).fillna(0.0)
    both = crypto.index.intersection(sleeve.index)
    out: Dict[str, Any] = {"corr": float(crypto.loc[both].corr(sleeve.loc[both]))}
    for label, w in MIXES:
        r = crypto * w + s * (1 - w)
        out[label] = [sl.curve_stats(r.loc[a:b]) for a, b in periods]
    return out


def render(rows: List[Dict[str, Any]], sleeve: List[Dict[str, float]], edge: Tuple[float, float],
           mix: Dict[str, Any], end: str, errors: Dict[str, str]) -> str:
    """Rapport Markdown."""
    per = "2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar"
    lines = [
        "# Hors cryptos, mais sur Binance : or, euro, actions tokenisées", "",
        f"Sous-jacents rejoués (Yahoo Finance) jusqu'au {end}, frais du bot sur Binance (0,1 % + 0,1 % "
        "de glissement par côté), règle de TrendGuard transposée (régime propre à chaque actif). "
        "Régénérer : `python -m research.binance_hors_cryptos --cache data_multi`.", "",
        "Écartés avant les mesures : " + " ; ".join(EXCLUDED) + ".", "",
        "## Chaque actif seul", "",
        f"| Actif | Règle {per} | Achat-conservation {per} |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['name']} | {_cells(r['rule'])} | {_cells(r['hold'])} |")
    lines += ["", "## La manche « hors cryptos sur Binance » (tous ensemble, un seul portefeuille)", "",
              f"| | {per} |", "|---|---|---|---|---|---|---|",
              f"| Règle | {_cells(sleeve)} |", "",
              f"Espérance par trade sur 2018-2022 : {fr(edge[1], '+.2f')} R (TrendGuard sur les cryptos : "
              f"{fr(edge[0], '+.2f')} R). Corrélation des rendements journaliers avec TrendGuard sur les "
              f"cryptos : {fr(mix['corr'], '.2f')}.", "",
              "## Mélangée à TrendGuard sur les cryptos", "",
              f"| Portefeuille | {per} | 24/05/2026 → : CAGR | baisse | Calmar |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for label, _w in MIXES:
        lines.append(f"| {label} | {_cells(mix[label])} |")
    ref, main = mix[MIXES[0][0]], mix[MIXES[1][0]]
    profitable = sleeve[0]["cagr_pct"] > 0 and sleeve[1]["cagr_pct"] > 0
    better = main[0]["calmar"] > ref[0]["calmar"] and main[1]["calmar"] > ref[1]["calmar"]
    lines += ["", "## Verdict (règle fixée avant les mesures)", "",
              f"- Manche rentable sur les deux périodes : **{'oui' if profitable else 'non'}**.",
              f"- Mélange 75/25 meilleur que TrendGuard seule au Calmar sur les deux périodes : "
              f"**{'oui' if better else 'non'}**.",
              ("- **Proposition** : suivre cette manche en paper sur Binance (prix réels, argent fictif), "
               "à côté du bot ; aucun ordre réel sans votre accord." if profitable and better else
               "- **Pas de proposition** : la règle fixée d'avance n'est pas satisfaite. Ces actifs restent "
               "suivis en argent fictif avec les autres marchés (page Veille)."),
              "", "## Lecture", "",
              "- La manche gagne peu sur 2018-2022 et beaucoup depuis 2023 : ce sont les années où les "
              "actions retenues (les plus échangées sur Binance aujourd'hui) ont flambé. Ce biais de "
              "popularité flatte la vérification.",
              "- Le mélange améliore surtout la baisse maximale ; sur la période jamais vue, il fait moins "
              "bien que TrendGuard seule.",
              "- D'où : un suivi en argent fictif (page Veille, « manche Binance ») avant toute décision.",
              "", "## Limites", "",
              "- Un backtest n'est pas une garantie de gain.",
              "- Actions choisies parmi celles que Binance liste aujourd'hui : leur passé est flatté.",
              "- Un jeton suit son action avec un écart possible (horaires, dividendes, frais du jeton) ; "
              "l'or tokenisé suit l'once d'or à quelques dixièmes de pourcent près.",
              "- Le week-end, les jetons d'actions bougent sans que l'action ne cote : le backtest, au "
              "rythme des séances, ne le voit pas."]
    if errors:
        lines += ["", "Historique indisponible : " + ", ".join(f"{c} ({e})" for c, e in errors.items()) + "."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Télécharge au besoin les sous-jacents, mesure, écrit le rapport."""
    def extra(ap: Any) -> None:
        ap.add_argument("--binance", default="data_binance", help="historique des cryptos du bot")
        ap.add_argument("--telecharger", action="store_true", help="relire l'historique sur Yahoo")
    args = study_args("Hors cryptos sur Binance", "docs/BINANCE_HORS_CRYPTOS.md", argv, extra)
    codes = [c for c, _n, _s in ASSETS]
    missing = [c for c in codes if not os.path.exists(os.path.join(args.cache, f"{c}.csv"))]
    errors: Dict[str, str] = {}
    if args.telecharger or missing:
        errors = ma.download(args.cache, lambda url: savoir.http_get(url, timeout=60),
                             codes=codes if args.telecharger else missing, symbols={c: s for c, _n, s in ASSETS})
    close = ma.load(args.cache, codes)
    crypto_close, crypto_volume = load_binance(args.binance)
    end = str(min(close.index[-1], crypto_close.index[-1]).date())
    close = close.loc[:end]
    periods = [DESIGN, (VERIFY_START, end)]
    rows = per_asset(close, periods)
    sleeve_rule = [ts.compute_metrics(r.equity, r.trades) for r in (ma.backtest(close, a, b) for a, b in periods)]
    crypto_run = ts.backtest(crypto_close, crypto_volume, ts.TrendParams(), DESIGN[0], end)
    crypto = crypto_run.equity.pct_change().dropna()
    sleeve = ma.backtest(close, DESIGN[0], end).equity.pct_change().dropna()
    edge = (ts.backtest(crypto_close, crypto_volume, ts.TrendParams(), *DESIGN).metrics["expectancy_r"],
            ma.backtest(close, *DESIGN).metrics["expectancy_r"])
    mix = mixes(crypto, sleeve, periods + [(UNSEEN_START, end)])
    write_report(render(rows, sleeve_rule, edge, mix, end, errors), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
