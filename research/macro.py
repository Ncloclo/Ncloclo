#!/usr/bin/env python3
"""
Les indicateurs de marché de la synthèse TradingView (volatilité du S&P 500,
dollar, taux américains) auraient-ils aidé TrendGuard sur les cryptos ?
Étude reproductible (docs/MACRO.md).

Filtres déclarés avant les mesures, aux seuils d'usage courant et sans
réglage (aucun nouvel achat de crypto ce jour-là si) :
  - « Stress » : VIX au-dessus de 30 ;
  - « Dollar fort » : indice du dollar (DXY) au-dessus de sa moyenne de 150
    séances ;
  - « Choc de taux » : taux US à 10 ans en hausse de plus de 0,5 point en
    60 séances ;
  - « Courbe inversée » : taux à 10 ans sous le taux à 3 mois.
Chaque indicateur est calculé sur ses propres séances. La valeur connue à la
clôture des cryptos (00:00 UTC) est celle de la séance américaine du même
jour (close à 21:00 ou 22:00 UTC), sinon la précédente : aucun regard vers le
futur.

Un filtre n'est « à étudier » que s'il améliore le Calmar sur 2018-2022 ET
depuis 2023 ; il ne serait appliqué qu'avec votre accord. Sinon, les
indicateurs restent une information (page Veille, agent macro du comité).

  python -m research.macro --cache data_binance --multi data_multi
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.commun import load_binance, study_args, write_report
from trendguard import multi_actifs as ma
from trendguard import savoir
from trendguard import trend_strategy as ts
from trendguard.texte import fr

DESIGN = ("2018-01-01", "2022-12-31")
VERIFY_START = "2023-01-01"
UNSEEN_START = "2026-05-24"
CODES = ("vix", "dxy", "us10y", "us3m")
Cond = Callable[[pd.DataFrame], pd.Series]
FILTERS: Tuple[Tuple[str, Cond], ...] = (
    ("Stress : VIX au-dessus de 30", lambda m: m["vix"] > 30),
    ("Dollar fort : DXY au-dessus de sa moyenne de 150 séances",
     lambda m: m["dxy"] > m["dxy"].rolling(150, min_periods=150).mean()),
    ("Choc de taux : taux US à 10 ans en hausse de plus de 0,5 point en 60 séances",
     lambda m: m["us10y"].diff(60) > 0.5),
    ("Courbe inversée : taux à 10 ans sous le taux à 3 mois", lambda m: m["us10y"] < m["us3m"]),
)


def own(series: pd.Series, cond: Callable[[pd.Series], pd.Series]) -> pd.Series:
    """Condition calculée sur les seules séances de l'indicateur."""
    return cond(ma.sessions(series))


def condition(macro: pd.DataFrame, rule: Cond) -> pd.Series:
    """La condition d'un filtre, séance par séance (chaque indicateur sur ses
    propres séances, alignés sur leurs dates communes)."""
    frame = pd.DataFrame({c: ma.sessions(macro[c]) for c in CODES if c in macro.columns})
    return rule(frame.ffill()).fillna(False).astype(bool)


def known_at_close(cond: pd.Series, index: pd.DatetimeIndex) -> np.ndarray:
    """La condition connue à chaque clôture de crypto : la séance américaine
    du même jour, sinon la dernière avant."""
    s = cond.astype(float).sort_index()
    return s.reindex(index, method="ffill").fillna(0.0).to_numpy() > 0.5


def runs(close: pd.DataFrame, volume: pd.DataFrame, blocked: Optional[np.ndarray],
         periods: List[Tuple[str, str]]) -> List[ts.PortfolioResult]:
    p = ts.TrendParams()
    pre = ts.precompute(close, volume, p)
    hooks = None if blocked is None else ts.BacktestHooks(choose=lambda i, snap: {} if blocked[i] else snap)
    return [ts.backtest(close, volume, p, a, b, pre=pre, hooks=hooks) for a, b in periods]


def split(trades: List[Dict[str, Any]], cond: pd.Series) -> Tuple[Tuple[int, float], Tuple[int, float]]:
    """Trades de la stratégie sans filtre selon la condition le jour de
    l'achat : (nombre, R moyen) si vraie, puis si fausse."""
    on: List[float] = []
    off: List[float] = []
    idx = cond.index
    for t in trades:
        d = pd.Timestamp(t["entry_date"])
        k = idx.searchsorted(d, side="right") - 1
        (on if k >= 0 and bool(cond.iloc[k]) else off).append(float(t["r"]))
    mean = lambda xs: float(np.mean(xs)) if xs else 0.0          # noqa: E731
    return (len(on), mean(on)), (len(off), mean(off))


def _cells(rs: List[ts.PortfolioResult], blocked: Optional[np.ndarray], index: pd.DatetimeIndex,
           periods: List[Tuple[str, str]]) -> str:
    out = []
    for r, (a, b) in zip(rs, periods):
        m = r.metrics
        if blocked is None:
            share = 0.0
        else:
            span = (index >= pd.Timestamp(a, tz="UTC")) & (index <= pd.Timestamp(b, tz="UTC"))
            share = float(blocked[span].mean() * 100) if span.any() else 0.0
        out.append(f"{fr(m.get('cagr_pct', 0.0), '+.1f')} % | {fr(m.get('max_dd_pct', 0.0), '.1f')} % | "
                   f"{fr(m.get('calmar', 0.0), '.2f')} | {int(m.get('trades', 0))} | {fr(share, '.0f')} %")
    return " | ".join(out)


def render(close: pd.DataFrame, base: List[ts.PortfolioResult], tested: List[Dict[str, Any]],
           periods: List[Tuple[str, str]], end: str, macro_end: str) -> str:
    """Rapport Markdown."""
    names = ["2018-2022", f"2023 → {end}", f"24/05/2026 → {end}"]
    head = " | ".join(f"{n} : CAGR | baisse | Calmar | trades | jours bloqués" for n in names)
    lines = [
        "# Les indicateurs de marché auraient-ils aidé TrendGuard ?", "",
        f"Historique Binance des 21 cryptos du bot jusqu'au {end} ; VIX, indice du dollar et taux américains "
        f"(Yahoo Finance) jusqu'au {macro_end}. Règle, paramètres et régime BTC inchangés. Régénérer : "
        "`python -m research.macro --cache data_binance --multi data_multi`.", "",
        "## Filtres déclarés avant les mesures", "",
        "Aucun nouvel achat de crypto ce jour-là si :", ""]
    lines += [f"- {name} ;" for name, _rule in FILTERS]
    lines += ["", "Seuils d'usage courant, sans réglage. Valeur connue à la clôture des cryptos (00:00 UTC) : la "
              "séance américaine du même jour, sinon la précédente.", "",
              "## Résultats", "",
              f"| Stratégie | {head} |", "|---|" + "---|---|---|---|---|" * len(names),
              f"| TrendGuard sans filtre | {_cells(base, None, close.index, periods)} |"]
    for t in tested:
        lines.append(f"| {t['name']} | {_cells(t['runs'], t['blocked'], close.index, periods)} |")
    lines += ["", "## Trades selon l'indicateur le jour de l'achat (stratégie sans filtre)", "",
              "| Indicateur | Période | Condition vraie : trades, R moyen | Condition fausse : trades, R moyen |",
              "|---|---|---|---|"]
    for t in tested:
        for n, ((k_on, r_on), (k_off, r_off)) in zip(names[:2], t["split"]):
            lines.append(f"| {t['name'].split(' :')[0]} | {n} | {k_on}, {fr(r_on, '+.2f')} R | "
                         f"{k_off}, {fr(r_off, '+.2f')} R |")
    kept = [t["name"].split(" :")[0] for t in tested if t["better"]]
    lines += ["", "## Verdict (règle fixée avant les mesures)", ""]
    lines += [f"- {t['name'].split(' :')[0]} : Calmar meilleur sur 2018-2022 et depuis 2023 : "
              f"**{'oui' if t['better'] else 'non'}**." for t in tested]
    lines += [("- **À étudier** : " + ", ".join(kept) + ". Rien n'est appliqué sans votre accord ; une étude en "
               "direct dira si l'avantage tient." if kept else
               "- **Aucun filtre retenu** : la règle actuelle tient. Les indicateurs restent une information."),
              "", "## Lecture", ""]
    word = lambda d: "mieux" if d > 0 else "moins bien" if d < 0 else "pareil"     # noqa: E731
    for t in tested:
        a, b = (t["runs"][k].metrics.get("calmar", 0.0) for k in (0, 1))
        a0, b0 = (base[k].metrics.get("calmar", 0.0) for k in (0, 1))
        line = (f"- {t['name'].split(' :')[0]} : {word(a - a0)} sur 2018-2022 (Calmar {fr(a0, '.2f')} → "
                f"{fr(a, '.2f')}), {word(b - b0)} depuis 2023 ({fr(b0, '.2f')} → {fr(b, '.2f')})")
        if t["split"][1][0][0] == 0:
            line += " ; aucun achat n'a eu lieu sous cette condition depuis 2023 : l'effet vu sur 2018-2022 reste à vérifier"
        lines.append(line + ".")
    lines += ["", "## Ce que le bot en fait", "",
              "- Il lit chaque jour, après la clôture de New York, le VIX, l'indice du dollar et les taux "
              "américains (3 mois, 5, 10 et 30 ans) : page Veille du panneau, et une ligne pour l'agent macro "
              "du comité, qui informe sans voter.",
              "", "## Limites", "",
              "- Un backtest n'est pas une garantie de gain.",
              "- Une seule paire de périodes, quatre filtres : un filtre qui passerait pourrait encore le "
              "devoir au hasard ; d'où l'étude en direct avant toute application.",
              "- Les marchés américains ferment le week-end : le samedi et le dimanche, la valeur est celle "
              "du vendredi."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Lit les historiques gardés (télécharge les indicateurs au besoin),
    mesure, écrit le rapport."""
    def extra(ap: Any) -> None:
        ap.add_argument("--multi", default="data_multi", help="indicateurs de marché (Yahoo)")
    args = study_args("Indicateurs de marché", "docs/MACRO.md", argv, extra)
    todo = [c for c in CODES if not os.path.exists(os.path.join(args.multi, f"{c}.csv"))]
    if todo:
        ma.download(args.multi, lambda url: savoir.http_get(url, timeout=60), codes=todo, symbols=ma.SYMBOL)
    macro = ma.load(args.multi, list(CODES))
    close, volume = load_binance(args.cache)
    end = str(close.index[-1].date())
    periods = [DESIGN, (VERIFY_START, end), (UNSEEN_START, end)]
    base = runs(close, volume, None, periods)
    tested = []
    for name, rule in FILTERS:
        cond = condition(macro, rule)
        blocked = known_at_close(cond, close.index)
        rs = runs(close, volume, blocked, periods)
        better = all(rs[k].metrics.get("calmar", 0.0) > base[k].metrics.get("calmar", 0.0) for k in (0, 1))
        tested.append({"name": name, "runs": rs, "blocked": blocked, "better": better,
                       "split": [split(r.trades, cond) for r in base[:2]]})
    write_report(render(close, base, tested, periods, end, str(macro.index[-1].date())), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
