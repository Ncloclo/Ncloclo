#!/usr/bin/env python3
"""
Au-delà des cryptos : la règle de TrendGuard sur l'or, le pétrole, les
devises, les indices boursiers et les obligations (et, à part, les actions).
Étude reproductible (docs/MULTI_ACTIFS.md ; règle : trendguard/multi_actifs.py).

Protocole fixé avant les mesures :
  - règle de TrendGuard transposée, paramètres gelés, régime propre à chaque
    actif (au-dessus de sa moyenne de 150 séances) ;
  - périodes : 2018-2022 (conception), depuis 2023 (vérification), et la
    période jamais vue au gel des paramètres (24/05/2026 →) ;
  - comparaison à l'achat-conservation de chaque catégorie (parts égales) ;
  - les actions sont mesurées à part (biais du survivant) ;
  - deux modèles de coûts : les frais de Binance, puis le coût réel de
    chaque marché (multi_actifs.COSTS), car des frais de cryptos mangent le
    tiers du risque d'un trade sur les devises (poids des frais) ;
  - suivi en argent fictif proposé seulement si (1) les marchés hors cryptos
    (sans les actions) sont rentables sur les deux périodes et (2) moitié
    cryptos, moitié hors cryptos bat TrendGuard seule au Calmar sur les deux
    périodes. Jamais d'ordre réel : il faudrait un autre courtier.

  python -m research.multi_actifs --cache data_multi            # historique gardé
  python -m research.multi_actifs --cache data_multi --telecharger
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from research.commun import load_binance, study_args, write_report
from trendguard import multi_actifs as ma
from trendguard import savoir
from trendguard import strategy_lab as sl
from trendguard import trend_strategy as ts
from trendguard.texte import fr

DESIGN = ("2018-01-01", "2022-12-31")
VERIFY_START = "2023-01-01"
UNSEEN_START = "2026-05-24"
CRYPTO = "TrendGuard seule (cryptos)"
MULTI = "Hors cryptos (sans les actions)"
HALF = "Moitié cryptos, moitié hors cryptos"
MODELS = (("Frais de Binance (0,1 % + 0,1 % de glissement par côté)", False),
          ("Coût réel de chaque marché (devises 0,01 %, indices et obligations 0,02 %, "
           "matières premières et actions 0,05 % par côté)", True))


def _cells(ms: List[Dict[str, float]]) -> str:
    return " | ".join(f"{fr(m.get('cagr_pct', 0.0), '+.1f')} % | {fr(m.get('max_dd_pct', 0.0), '.1f')} % | "
                      f"{fr(m.get('calmar', 0.0), '.2f')}" for m in ms)


def by_category(close: pd.DataFrame, periods: List[Tuple[str, str]], real: bool) -> Dict[str, Any]:
    """Règle et achat-conservation (parts égales) de chaque catégorie."""
    out = {}
    for cat in ma.CATEGORIES:
        sub = ma.of_category(close, cat)
        rule = []
        for a, b in periods:
            r = ma.backtest(sub, a, b, real_costs=real)
            rule.append(ts.compute_metrics(r.equity, r.trades))
        hold = [ts.buy_and_hold(sub, list(sub.columns), a, b) for a, b in periods]
        out[cat] = {"n": len(sub.columns), "rule": rule, "hold": hold}
    return out


def combined(crypto: pd.Series, multi: pd.Series, periods: List[Tuple[str, str]]) -> Dict[str, Any]:
    """Cryptos seules, hors cryptos seuls et moitié-moitié (rendements mêlés
    chaque jour, comme le laboratoire) ; corrélation des séances communes."""
    m = multi.reindex(crypto.index).fillna(0.0)
    both = crypto.index.intersection(multi.index)
    rets = pd.DataFrame({"crypto": crypto, "multi": m})
    out: Dict[str, Any] = {"corr": float(crypto.loc[both].corr(multi.loc[both]))}
    for label, r in ((CRYPTO, rets["crypto"]), (MULTI, rets["multi"]), (HALF, sl.blend(rets, ["crypto", "multi"]))):
        out[label] = [sl.curve_stats(r.loc[a:b]) for a, b in periods]
    return out


def verdict(mix: Dict[str, Any]) -> Tuple[bool, bool]:
    """(hors cryptos rentable sur les deux périodes, moitié-moitié meilleure
    que TrendGuard seule au Calmar sur les deux périodes)."""
    ref, multi, half = mix[CRYPTO], mix[MULTI], mix[HALF]
    return (multi[0]["cagr_pct"] > 0 and multi[1]["cagr_pct"] > 0,
            half[0]["calmar"] > ref[0]["calmar"] and half[1]["calmar"] > ref[1]["calmar"])


def render(results: List[Tuple[str, Dict[str, Any], Dict[str, Any]]], end: str, errors: Dict[str, str]) -> str:
    """Rapport Markdown : par modèle de coûts, catégories, portefeuilles et
    verdict ; puis les limites."""
    per = "2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar"
    lines = [
        "# Au-delà des cryptos : or, pétrole, devises, indices, obligations", "",
        f"Historique journalier Yahoo Finance jusqu'au {end} ; règle de TrendGuard transposée "
        "(`trendguard/multi_actifs.py`) : paramètres gelés, régime propre à chaque actif (au-dessus de "
        "sa moyenne de 150 séances), achats seulement, sans levier. Régénérer : `python -m "
        "research.multi_actifs --cache data_multi`.", "",
        "Pourquoi deux modèles de coûts : avec les frais de Binance, le coût d'un aller-retour mange 23 à "
        "45 % du risque de chaque trade sur les devises, les indices et les obligations (stop médian à "
        "0,9-1,8 % du prix), contre 4,6 % pour TrendGuard sur les cryptos. Ces marchés coûtent 10 à 20 "
        "fois moins chez un courtier.", ""]
    for title, cats, mix in results:
        lines += [f"## {title}", "",
                  f"| Catégorie | Actifs | Règle {per} | Achat-conservation {per} |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for cat, c in cats.items():
            label = f"{cat} (biais du survivant)" if cat in ma.BIASED else cat
            lines.append(f"| {label} | {c['n']} | {_cells(c['rule'])} | {_cells(c['hold'])} |")
        lines += ["", f"Corrélation des rendements journaliers entre TrendGuard sur les cryptos et la règle hors "
                      f"cryptos : **{fr(mix['corr'], '.2f')}** (0,87 à 0,93 entre stratégies de cryptos).", "",
                  "| Portefeuille | " + per + " | 24/05/2026 → : CAGR | baisse | Calmar |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for label in (CRYPTO, MULTI, HALF):
            lines.append(f"| {label} | {_cells(mix[label])} |")
        profitable, better = verdict(mix)
        lines += ["", f"Espérance par trade sur 2018-2022 : hors cryptos {fr(mix['edge'][1], '+.2f')} R, "
                      f"TrendGuard sur les cryptos {fr(mix['edge'][0], '+.2f')} R.", "",
                  f"- Hors cryptos rentable sur les deux périodes : **{'oui' if profitable else 'non'}**.",
                  f"- Moitié-moitié meilleure que TrendGuard seule au Calmar sur les deux périodes : "
                  f"**{'oui' if better else 'non'}**.",
                  ("- **Proposition** : suivre ce portefeuille en argent fictif, à côté du bot ; aucun ordre "
                   "réel sans courtier." if profitable and better else
                   "- **Pas de proposition** : la règle fixée d'avance n'est pas satisfaite."), ""]
    passed = profitable and better          # verdict au coût réel (dernier modèle)
    lines += ["## Lecture", "",
              "- La règle est calculée sur les séances réelles de chaque marché (correctif du 24/11/2026). "
              "Avant, les jours où une place était fermée comptaient comme des jours sans variation : ils "
              "diluaient la volatilité et rapprochaient les stops, et une fermeture de plus de 5 séances "
              "(semaine d'or en Chine) passait pour un retrait de la cote.",
              ("- Avec les coûts réels, la règle fixée d'avance est satisfaite : le portefeuille "
               "moitié-moitié doit son avance à la diversification (corrélation proche de zéro avec les "
               "cryptos)." if passed else
               "- Avec les coûts réels, la règle fixée d'avance n'est pas satisfaite : hors cryptos, la "
               "règle gagne peu, et le mélange moitié-moitié ne bat pas TrendGuard seule sur les deux "
               "périodes."),
              "- Conclusion : ces marchés restent suivis en argent fictif pour information (pages du "
              "panneau, page Veille), sans argent réel tant que les preuves en direct ne montrent pas "
              "d'avantage.", "",
              "## Limites", "",
              "- Un backtest n'est pas une garantie de gain.",
              "- Coûts réels supposés : typiques d'un compte de particulier, à confirmer chez un courtier.",
              "- Rendements en monnaie locale (change supposé couvert) ; contrats à terme en continu "
              "(Yahoo), sans le coût de leur renouvellement.",
              "- Actions : choisies parmi les plus grandes d'aujourd'hui, leur passé est flatté ; elles ne "
              "comptent dans aucun verdict.",
              "- Le franc CFA (XOF) a une parité fixe avec l'euro : rien à suivre."]
    if errors:
        lines += ["", "Historique indisponible : " + ", ".join(
            f"{ma.NAME.get(c, c)} ({e})" for c, e in errors.items()) + "."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Télécharge au besoin l'historique hors cryptos, mesure, écrit le rapport."""
    def extra(ap: Any) -> None:
        ap.add_argument("--binance", default="data_binance", help="historique des cryptos du bot")
        ap.add_argument("--telecharger", action="store_true", help="relire tout l'historique sur Yahoo")
    args = study_args("Au-delà des cryptos", "docs/MULTI_ACTIFS.md", argv, extra)
    close = ma.load(args.cache)
    errors: Dict[str, str] = {}
    if args.telecharger or close.empty:
        errors = ma.download(args.cache, lambda url: savoir.http_get(url, timeout=60))
        close = ma.load(args.cache)
    crypto_close, crypto_volume = load_binance(args.binance)
    end = str(min(close.index[-1], crypto_close.index[-1]).date())
    close = close.loc[:end]
    periods = [DESIGN, (VERIFY_START, end)]
    crypto = ts.backtest(crypto_close, crypto_volume, ts.TrendParams(), DESIGN[0], end).equity.pct_change().dropna()
    crypto_edge = ts.backtest(crypto_close, crypto_volume, ts.TrendParams(), *DESIGN).metrics["expectancy_r"]
    results = []
    for title, real in MODELS:
        multi = ma.backtest(ma.unbiased(close), DESIGN[0], end, real_costs=real).equity.pct_change().dropna()
        mix = combined(crypto, multi, periods + [(UNSEEN_START, end)])
        mix["edge"] = (crypto_edge, ma.backtest(ma.unbiased(close), *DESIGN, real_costs=real).metrics["expectancy_r"])
        results.append((title, by_category(close, periods, real), mix))
    write_report(render(results, end, errors), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
