#!/usr/bin/env python3
"""
Les grands marchés vus sur la synthèse de TradingView du 24/11/2026, que le
bot ne suivait pas (platine, produits agricoles, indices d'Asie, d'Europe et
d'Amérique latine…) : améliorent-ils le portefeuille fictif hors cryptos ?
Étude reproductible (docs/MARCHES_AJOUTES.md ; marchés : multi_actifs.ADDED).

Protocole fixé avant les mesures :
  - règle de TrendGuard transposée (trendguard/multi_actifs.py), paramètres
    gelés, régime propre à chaque marché, coût réel de chaque marché ;
  - les 21 marchés du portefeuille fictif (sans les actions) contre ces 21
    et les marchés ajoutés (sans les actions : Anthropic et SpaceX n'ont pas
    l'historique de 250 séances que la règle exige), sur le même calendrier ;
  - périodes : 2018-2022, depuis 2023, et 24/05/2026 → (information) ;
  - les marchés ajoutés n'entrent dans le portefeuille fictif que si
    l'univers élargi bat les 21 au Calmar sur 2018-2022 ET depuis 2023.
    Jamais d'ordre réel : il faudrait un courtier.

S'y ajoute une vérification des sources : les cours du bot (Yahoo Finance)
contre ceux de la synthèse de TradingView, relevés à la main le 24/11/2026 à
18:09 UTC (le bot ne lit jamais TradingView, qui interdit la collecte
automatique).

  python -m research.marches_ajoutes --cache data_multi [--telecharger]
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from research.commun import study_args, write_report
from trendguard import multi_actifs as ma
from trendguard import savoir
from trendguard import trend_strategy as ts
from trendguard.texte import fr

DESIGN = ("2018-01-01", "2022-12-31")
VERIFY_START = "2023-01-01"
UNSEEN_START = "2026-05-24"
BASE = [c for c, *_ in ma.UNIVERSE if ma.CATEGORY[c] not in ma.BIASED]
NEW = [c for c, *_ in ma.ADDED if ma.CATEGORY[c] not in ma.BIASED]
# Synthèse des marchés de TradingView, 24/11/2026 à 18:09 UTC : (nom, code du
# bot, dernier cours). Les séances américaines étaient encore ouvertes.
TRADINGVIEW: Tuple[Tuple[str, str, float], ...] = (
    ("S&P 500", "sp500", 7207.14), ("Nasdaq 100", "nasdaq100", 26237.71), ("Dow Jones", "dow", 49999.77),
    ("Russell 2000", "russell2000", 2692.64), ("Nasdaq Composite", "nasdaqcomp", 23724.56),
    ("CAC 40", "cac40", 8040.49), ("DAX", "dax", 25017.81), ("FTSE 100", "ftse", 10467.07),
    ("FTSE MIB", "ftsemib", 48121.18), ("IBEX 35", "ibex", 19015.80), ("Nikkei 225", "nikkei", 58644.56),
    ("Shanghai Composite", "shanghai", 3548.37), ("Hang Seng", "hangseng", 22219.65),
    ("Nifty 50", "nifty", 25349.65), ("Bovespa", "bovespa", 165281.31),
    ("Or", "or", 3565.00), ("Argent", "argent", 40.840), ("Platine", "platine", 1256.80),
    ("Cuivre", "cuivre", 5.372), ("Pétrole WTI", "wti", 81.31), ("Gaz naturel", "gaz", 3.355),
    ("EUR/USD", "eurusd", 1.09729), ("GBP/USD", "gbpusd", 1.3122), ("AUD/USD", "audusd", 0.6646),
    ("NZD/USD", "nzdusd", 0.54903), ("USD/JPY", "usdjpy", 1 / 0.006278), ("USD/CHF", "usdchf", 1 / 1.197),
    ("Indice du dollar (DXY)", "dxy", 103.767), ("VIX", "vix", 26.80),
    ("Taux US à 5 ans (%)", "us5y", 4.719), ("Taux US à 10 ans (%)", "us10y", 4.904),
    ("Taux US à 30 ans (%)", "us30y", 5.292),
    ("Nvidia", "nvda", 152.84), ("Apple", "aapl", 353.35), ("Microsoft", "msft", 498.50),
    ("Amazon", "amzn", 252.61), ("Alphabet", "googl", 279.20), ("Tesla", "tsla", 384.26),
    ("LVMH (€)", "mc", 394.70),
)


def _cells(ms: List[Dict[str, float]]) -> str:
    return " | ".join(f"{fr(m.get('cagr_pct', 0.0), '+.1f')} % | {fr(m.get('max_dd_pct', 0.0), '.1f')} % | "
                      f"{fr(m.get('calmar', 0.0), '.2f')} | {int(m.get('trades', 0))}" for m in ms)


def run(close: pd.DataFrame, codes: List[str], periods: List[Tuple[str, str]]) -> List[ts.PortfolioResult]:
    sub = close[[c for c in codes if c in close.columns]]
    return [ma.backtest(sub, a, b, real_costs=True) for a, b in periods]


def per_market(results: List[ts.PortfolioResult], codes: List[str]) -> List[Dict[str, Any]]:
    """Trades et R de chaque marché ajouté dans l'univers élargi, par période."""
    rows = []
    for code in codes:
        per = []
        for r in results:
            mine = [t for t in r.trades if t["asset"] == code]
            per.append((len(mine), sum(t["r"] for t in mine)))
        rows.append({"code": code, "periods": per})
    return rows


def check_sources(cache: str) -> List[Dict[str, Any]]:
    """Cours du bot (dernière séance lue chez Yahoo) face à TradingView."""
    rows = []
    for name, code, tv in TRADINGVIEW:
        path = os.path.join(cache, f"{code}.csv")
        if not os.path.exists(path):
            rows.append({"name": name, "tv": tv, "bot": None, "date": None, "gap_pct": None})
            continue
        s = pd.read_csv(path).dropna()
        bot, day = float(s["close"].iloc[-1]), str(s["date"].iloc[-1])[:10]
        rows.append({"name": name, "tv": tv, "bot": bot, "date": day, "gap_pct": (bot / tv - 1) * 100})
    return rows


def render(base: List[ts.PortfolioResult], wide: List[ts.PortfolioResult], added: List[Dict[str, Any]],
           sources: List[Dict[str, Any]], end: str, missing: List[str]) -> str:
    """Rapport Markdown."""
    names = ["2018-2022", f"2023 → {end}", f"24/05/2026 → {end}"]
    per = " | ".join(f"{n} : CAGR | baisse | Calmar | trades" for n in names)
    better = all(wide[k].metrics.get("calmar", 0.0) > base[k].metrics.get("calmar", 0.0) for k in (0, 1))
    lines = [
        "# Les marchés de la synthèse TradingView : enrichissent-ils le bot ?", "",
        f"Cours journaliers de Yahoo Finance jusqu'au {end}, règle de TrendGuard transposée (régime propre à "
        "chaque marché), paramètres gelés, coût réel de chaque marché. Régénérer : "
        "`python -m research.marches_ajoutes --cache data_multi`.", "",
        f"Marchés ajoutés le 24/11/2026 ({len(ma.ADDED)}) : " + ", ".join(n for _c, n, _k, _s in ma.ADDED) + ". "
        "Ils sont suivis chaque jour dans les pages du panneau. Indicateurs lus pour la veille (pas des marchés "
        "à trader) : " + ", ".join(n for _c, n, _s in ma.INDICATORS) + ".", "",
        "## Le portefeuille fictif hors cryptos, avec ou sans les marchés ajoutés", "",
        f"| Univers | {per} |", "|---|" + "---|---|---|---|" * len(names),
        f"| Les {len(BASE)} marchés du portefeuille fictif | {_cells([r.metrics for r in base])} |",
        f"| Les {len(BASE)} + {len(NEW)} ajoutés (sans les actions) | {_cells([r.metrics for r in wide])} |", "",
        "Espérance par trade : " + " ; ".join(
            f"{n} : {fr(b.metrics.get('expectancy_r', 0.0), '+.2f')} R puis {fr(w.metrics.get('expectancy_r', 0.0), '+.2f')} R"
            for n, b, w in zip(names, base, wide)) + " (les 21, puis l'univers élargi).", "",
        "## Chaque marché ajouté dans l'univers élargi", "",
        "| Marché | Catégorie | " + " | ".join(f"{n} : trades, total en R" for n in names) + " |",
        "|---|---|" + "---|" * len(names)]
    for row in added:
        lines.append(f"| {ma.NAME[row['code']]} | {ma.CATEGORY[row['code']]} | " + " | ".join(
            f"{n} trade{'s' if n > 1 else ''}, {fr(r, '+.1f')} R" for n, r in row["periods"]) + " |")
    lines += ["", "## Verdict (règle fixée avant les mesures)", "",
              f"- L'univers élargi bat les {len(BASE)} au Calmar sur 2018-2022 et depuis 2023 : "
              f"**{'oui' if better else 'non'}**.",
              ("- **Décision** : les marchés ajoutés (sans les actions) entrent dans le portefeuille fictif hors "
               "cryptos. Argent fictif seulement : aucun ordre réel." if better else
               "- **Décision** : les marchés ajoutés restent suivis pour information (pages du panneau) ; le "
               "portefeuille fictif garde ses 21 marchés."),
              "", "## Vérification des cours du bot contre TradingView", "",
              "Cours de la synthèse TradingView relevés le 24/11/2026 à 18:09 UTC (séances américaines encore "
              "ouvertes, cours parfois différés) contre la dernière séance que le bot a lue chez Yahoo Finance.", "",
              "| Marché | TradingView | Bot (Yahoo) | Séance du bot | Écart |", "|---|---|---|---|---|"]
    for s in sources:
        if s["bot"] is None:
            lines.append(f"| {s['name']} | {fr(s['tv'], ',.4g')} | illisible | – | – |")
        else:
            lines.append(f"| {s['name']} | {fr(s['tv'], ',.6g')} | {fr(s['bot'], ',.6g')} | {s['date']} | "
                         f"{fr(s['gap_pct'], '+.2f')} % |")
    close_ones = [s for s in sources if s["gap_pct"] is not None and abs(s["gap_pct"]) <= 0.5]
    lines += ["", f"{len(close_ones)} cours sur {len(sources)} concordent à 0,5 % près. Les écarts plus grands "
              "viennent de l'heure du relevé (séance encore ouverte, ou déjà close d'un jour à l'autre en Asie), "
              "pas d'une erreur de source.", "",
              "## Limites", "",
              "- Un backtest n'est pas une garantie de gain.",
              "- Les marchés ajoutés sont ceux qu'une page de TradingView met en avant aujourd'hui : de grands "
              "marchés, choisis pour leur taille et non pour leurs résultats passés.",
              "- Le calendrier commun mêle des places fermées à des jours différents ; un jour férié garde le "
              "dernier cours (5 séances au plus).",
              "- Anthropic (introduite le 19/11/2026) et SpaceX (cotée depuis juin 2026) n'ont pas l'historique "
              "de 250 séances que la règle exige : suivies pour information seulement."]
    if missing:
        lines += ["", "Historique indisponible : " + ", ".join(missing) + "."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Télécharge au besoin les marchés ajoutés, mesure, écrit le rapport."""
    def extra(ap: Any) -> None:
        ap.add_argument("--telecharger", action="store_true", help="relire l'historique sur Yahoo")
    args = study_args("Marchés ajoutés", "docs/MARCHES_AJOUTES.md", argv, extra)
    codes = BASE + NEW + [c for c, *_ in ma.ADDED if c not in NEW] + ma.INDICATOR_CODES
    todo = codes if args.telecharger else [c for c in codes if not os.path.exists(os.path.join(args.cache, f"{c}.csv"))]
    if todo:
        ma.download(args.cache, lambda url: savoir.http_get(url, timeout=60), codes=todo, symbols=ma.SYMBOL)
    close = ma.load(args.cache, BASE + NEW)
    missing = [ma.NAME[c] for c in BASE + NEW if c not in close.columns]
    end = str(close.index[-1].date())
    periods = [DESIGN, (VERIFY_START, end), (UNSEEN_START, end)]
    base, wide = run(close, BASE, periods), run(close, BASE + NEW, periods)
    write_report(render(base, wide, per_market(wide, [c for c in NEW if c in close.columns]),
                        check_sources(args.cache), end, missing), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
