#!/usr/bin/env python3
"""
Étude de robustesse de TrendGuard — reproductible (docs/ROBUSTESSE.md).

Diagnostic, pas une optimisation : aucune règle n'est changée d'après ces
tests. La question est : les résultats tiennent-ils quand les conditions se
dégradent ? Mêmes données et même protocole que les autres études (Binance,
2018-2022 puis 2023 → aujourd'hui, frais 0,1 % et glissement 0,1 % par côté).

1. Coûts : frais et glissement doublés, puis triplés.
2. Réglages voisins : 27 combinaisons autour des réglages du bot (cassure,
   stop initial, stop suiveur) ; forment-elles un plateau ou un pic isolé ?
3. Dépendance aux gagnants : sans la crypto qui a le plus rapporté, puis
   sans les trois meilleures.
4. Hasard (Monte-Carlo par blocs de jours) : baisses à prévoir sur 3 ans,
   positions simultanées comprises ; séries de trades perdants.
5. Année par année, et pire mois.
6. Profil prudent (risque divisé par 2 après 10 % de baisse).

  python -m research.robustness --cache data_binance --out docs/ROBUSTESSE.md
"""

from __future__ import annotations

import dataclasses
import itertools
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.commun import load_binance, study_args, write_report
from trendguard import trend_strategy as ts
from trendguard.texte import fr

GRID = {"breakout_n": [20, 30, 40], "init_stop_atr": [2.5, 3.0, 3.5], "trail_atr": [4.0, 5.0, 6.0]}


def cell(m: Dict[str, float]) -> str:
    return (f"{fr(m['cagr_pct'], '+.1f')} % | {fr(m['max_dd_pct'], '.1f')} % | "
            f"{fr(m['sharpe'], '.2f')} | {fr(m['calmar'], '.2f')}")


def periods(close: pd.DataFrame) -> Tuple[Tuple[str, str], Tuple[str, str]]:
    return ("2018-01-01", "2022-12-31"), ("2023-01-01", str(close.index[-1].date()))


def both(close, volume, p, pre, universe=None) -> Tuple[Dict[str, float], Dict[str, float]]:
    is_p, oos_p = periods(close)
    return tuple(ts.backtest(close, volume, p, *per, pre=pre, universe=universe).metrics
                 for per in (is_p, oos_p))


def stress(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, pre) -> List[Tuple[str, Any]]:
    """Coûts et profil prudent : mêmes indicateurs, seuls les frais ou le
    risque changent."""
    rows = [("Référence (réglages du bot)", both(close, volume, p, pre))]
    for k, label in ((2, "Frais et glissement × 2 (0,4 % par côté)"),
                     (3, "Frais et glissement × 3")):
        q = dataclasses.replace(p, fee=p.fee * k, slippage=p.slippage * k)
        rows.append((label, both(close, volume, q, pre)))
    q = dataclasses.replace(p, dd_throttle=((0.10, 0.5),))
    rows.append(("Profil prudent (risque ÷ 2 après −10 %)", both(close, volume, q, pre)))
    return rows


def neighbours(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams) -> pd.DataFrame:
    """27 réglages voisins, sur les deux périodes."""
    out = []
    pres = {}
    for combo in itertools.product(*GRID.values()):
        kw = dict(zip(GRID, combo))
        q = dataclasses.replace(p, **kw)
        if q.breakout_n not in pres:
            pres[q.breakout_n] = ts.precompute(close, volume, q)
        a, b = both(close, volume, q, pres[q.breakout_n])
        out.append({**kw, "is_calmar": a["calmar"], "is_cagr": a["cagr_pct"],
                    "oos_calmar": b["calmar"], "oos_cagr": b["cagr_pct"],
                    "ref": all(getattr(p, k) == v for k, v in kw.items())})
    return pd.DataFrame(out)


def winners(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, pre
            ) -> Tuple[List[Tuple[str, float]], List[Tuple[str, Any]]]:
    """Bénéfice de chaque crypto sur toute la période, puis résultats sans
    la meilleure et sans les trois meilleures."""
    full = ts.backtest(close, volume, p, "2018-01-01", str(close.index[-1].date()), pre=pre)
    pnl: Dict[str, float] = {}
    for t in full.trades:
        pnl[t["asset"]] = pnl.get(t["asset"], 0.0) + t["pnl"]
    ranked = sorted(pnl.items(), key=lambda x: -x[1])
    universe = list(pre[0])
    rows = []
    for n in (1, 3):
        drop = {a for a, _v in ranked[:n]}
        label = "Sans " + ", ".join(a.upper() for a, _v in ranked[:n])
        rows.append((label, both(close, volume, p, pre, [a for a in universe if a not in drop])))
    return ranked, rows


def monte_carlo(equity: pd.Series, trades: List[Dict[str, Any]], days: int = 1095,
                block: int = 30, sims: int = 5000, seed: int = 7) -> Dict[str, float]:
    """Trois ans rejoués au hasard par blocs de 30 jours de la courbe réelle
    du capital : les positions simultanées et leurs corrélations restent
    dans chaque bloc. Séries de trades perdants : trades tirés au hasard."""
    rets = equity.pct_change().dropna().to_numpy()
    rng = np.random.default_rng(seed)
    n_blocks = -(-days // block)
    starts = rng.integers(0, len(rets) - block, size=(sims, n_blocks))
    paths = np.empty((sims, n_blocks * block))
    for k in range(n_blocks):
        idx = starts[:, k:k + 1] + np.arange(block)
        paths[:, k * block:(k + 1) * block] = rets[idx]
    paths = np.cumprod(1 + paths[:, :days], axis=1)
    peaks = np.maximum.accumulate(np.hstack([np.ones((sims, 1)), paths]), axis=1)[:, 1:]
    mdd = ((peaks - paths) / peaks).max(axis=1)
    fin = paths[:, -1] - 1
    rs = np.array([t["r"] for t in trades], dtype=float)
    draws = rng.choice(rs, size=(2000, 100), replace=True)
    losing = []
    for row in draws:
        best = cur = 0
        for r in row:
            cur = cur + 1 if r <= 0 else 0
            best = max(best, cur)
        losing.append(best)
    return {"ret_p5": np.percentile(fin, 5) * 100, "ret_p50": np.percentile(fin, 50) * 100,
            "prob_loss": (fin < 0).mean() * 100, "dd_p50": np.percentile(mdd, 50) * 100,
            "dd_p95": np.percentile(mdd, 95) * 100, "p_dd20": (mdd > 0.20).mean() * 100,
            "p_dd30": (mdd > 0.30).mean() * 100, "p_dd40": (mdd > 0.40).mean() * 100,
            "streak_p50": float(np.percentile(losing, 50)),
            "streak_p95": float(np.percentile(losing, 95)), "pool": len(rs)}


def yearly(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, pre
           ) -> Tuple[Any, Dict[int, float], float, str]:
    """Courbe du capital 2018 → aujourd'hui, rendement par année (à partir
    du premier achat) et pire mois."""
    res = ts.backtest(close, volume, p, "2018-01-01", str(close.index[-1].date()), pre=pre)
    first = min(t["entry_date"] for t in res.trades).year
    years = {y: v for y, v in ts.yearly_returns(res.equity).items() if y >= first}
    months = res.equity.resample("ME").last().pct_change().dropna()
    return res, years, float(months.min() * 100), months.idxmin().strftime("%m/%Y")


def _verdict_costs(x2, x3) -> str:
    ok2 = x2[0]["cagr_pct"] > 0 and x2[1]["cagr_pct"] > 0
    ok3 = x3[0]["cagr_pct"] > 0 and x3[1]["cagr_pct"] > 0
    if ok3:
        return "rentable sur les deux périodes même avec des frais triplés"
    if ok2:
        return "rentable avec des frais doublés, pas avec des frais triplés"
    return "sensible aux frais : l'exécution doit rester soignée"


def report(close: pd.DataFrame, volume: pd.DataFrame) -> str:
    """Étude de robustesse complète (docs/ROBUSTESSE.md) : coûts et profil
    prudent, paramètres voisins, gagnants retirés, années une à une et
    Monte-Carlo des trades."""
    p = ts.TrendParams()
    pre = ts.precompute(close, volume, p)
    _is_p, oos_p = periods(close)
    head = ("| Test | 2018-22 : CAGR | Baisse max | Sharpe | Calmar | "
            "2023 → : CAGR | Baisse max | Sharpe | Calmar |")
    sep = "|---|---|---|---|---|---|---|---|---|"
    st = stress(close, volume, p, pre)
    (ref_a, ref_b), x2, x3, prudent = st[0][1], st[1][1], st[2][1], st[3][1]
    grid = neighbours(close, volume, p)
    ranked, lo = winners(close, volume, p, pre)
    res, years, worst_m, worst_when = yearly(close, volume, p, pre)
    mc = monte_carlo(res.equity, res.trades)
    both_ok = int(((grid.is_cagr > 0) & (grid.oos_cagr > 0)).sum())
    ref_rank_is = int((grid.is_calmar > ref_a["calmar"]).sum()) + 1
    ref_rank_oos = int((grid.oos_calmar > ref_b["calmar"]).sum()) + 1
    gains = sum(v for _a, v in ranked if v > 0)
    top_share = ranked[0][1] / gains * 100
    top3_share = sum(v for _a, v in ranked[:3]) / gains * 100
    losers = [(a, v) for a, v in ranked if v < 0]
    no_top3 = lo[-1][1]
    survives = no_top3[0]["cagr_pct"] > 0 and no_top3[1]["cagr_pct"] > 0
    down_years = [y for y, v in years.items() if v < 0]
    plateau = both_ok >= 22
    money = lambda v: fr(v, "+,.0f") + " $"  # noqa: E731
    lines = [
        "# TrendGuard — les résultats tiennent-ils quand tout se dégrade ?",
        "",
        "Étude reproductible : `python -m research.robustness --cache data_binance` "
        f"(données journalières Binance des {len(close.columns)} paires du bot, frais 0,1 % "
        "et glissement 0,1 % par côté, 1 % du capital risqué par achat). C'est un "
        "diagnostic : **aucune règle n'est changée d'après ces tests.**",
        "",
        f"Périodes : **2018-2022** et **2023 → {oos_p[1]}** (l'historique Binance ne "
        "permet les premiers achats qu'en 2019). CAGR : rendement annuel ; Calmar : "
        "rendement annuel divisé par la pire baisse.",
        "",
        "## 1. Coûts plus élevés et profil prudent",
        "",
        head, sep,
        *[f"| {name} | {cell(a)} | {cell(b)} |" for name, (a, b) in st],
        "",
        f"- Frais et glissement doublés : {fr(x2[0]['cagr_pct'], '+.1f')} % puis "
        f"{fr(x2[1]['cagr_pct'], '+.1f')} % par an ; triplés : "
        f"{fr(x3[0]['cagr_pct'], '+.1f')} % puis {fr(x3[1]['cagr_pct'], '+.1f')} % par an. "
        f"Le bot reste {_verdict_costs(x2, x3)}.",
        f"- Profil prudent : pire baisse de {fr(prudent[1]['max_dd_pct'], '.1f')} % au lieu "
        f"de {fr(ref_b['max_dd_pct'], '.1f')} % depuis 2023, pour "
        f"{fr(prudent[1]['cagr_pct'], '+.1f')} % par an au lieu de "
        f"{fr(ref_b['cagr_pct'], '+.1f')} % (`docs/ADAPTATION.md`).",
        "",
        "## 2. Réglages voisins (27 combinaisons)",
        "",
        "Cassure de 20, 30 ou 40 jours ; stop initial à 2,5, 3 ou 3,5 fois la "
        "volatilité ; stop suiveur à 4, 5 ou 6 fois.",
        "",
        "| Mesure | 2018-22 | 2023 → |",
        "|---|---|---|",
        f"| Combinaisons rentables | {int((grid.is_cagr > 0).sum())} / 27 | "
        f"{int((grid.oos_cagr > 0).sum())} / 27 |",
        f"| Calmar : le plus faible | {fr(grid.is_calmar.min(), '.2f')} | "
        f"{fr(grid.oos_calmar.min(), '.2f')} |",
        f"| Calmar : médiane | {fr(grid.is_calmar.median(), '.2f')} | "
        f"{fr(grid.oos_calmar.median(), '.2f')} |",
        f"| Calmar : le plus fort | {fr(grid.is_calmar.max(), '.2f')} | "
        f"{fr(grid.oos_calmar.max(), '.2f')} |",
        f"| Rang des réglages du bot | {ref_rank_is} / 27 | {ref_rank_oos} / 27 |",
        "",
        f"- {both_ok} combinaisons sur 27 sont rentables sur les deux périodes : "
        + ("les résultats forment un plateau, pas un pic isolé trouvé par hasard."
           if plateau else "le résultat dépend nettement des réglages, à surveiller.")
        + " Choisir la meilleure combinaison après coup serait du sur-ajustement.",
        "",
        "## 3. Dépendance aux plus grands gagnants",
        "",
        "Bénéfice de chaque crypto de 2018 à aujourd'hui (réglages du bot) : "
        + ", ".join(f"{a.upper()} {money(v)}" for a, v in ranked[:5])
        + "… ; en perte : " + ", ".join(f"{a.upper()} {money(v)}" for a, v in losers[-4:])
        + f". La meilleure fait {fr(top_share, '.0f')} % des gains, les trois meilleures "
        f"{fr(top3_share, '.0f')} %.",
        "",
        head, sep,
        f"| Toutes les cryptos | {cell(ref_a)} | {cell(ref_b)} |",
        *[f"| {name} | {cell(a)} | {cell(b)} |" for name, (a, b) in lo],
        "",
        "- C'est le principe du suivi de tendance : quelques grandes tendances font "
        "l'essentiel du résultat, et on ne sait pas à l'avance lesquelles. "
        + ("Même sans ses trois meilleures cryptos, retirées après coup (test "
           "sévère), le bot reste rentable sur les deux périodes : c'est aussi pourquoi "
           "la sélection auto garde les 21." if survives else
           "Sans ses trois meilleures cryptos, le bot n'est plus rentable sur les deux "
           "périodes : les résultats reposent sur peu de gagnants."),
        "",
        "## 4. Hasard (Monte-Carlo)",
        "",
        "Trois ans rejoués 5 000 fois au hasard, par blocs de 30 jours de la vraie "
        "courbe du capital : les positions ouvertes en même temps et leurs "
        "corrélations sont conservées.",
        "",
        "| Mesure sur 3 ans | Valeur |",
        "|---|---|",
        f"| Résultat médian | {fr(mc['ret_p50'], '+.0f')} % |",
        f"| Mauvais cas (1 fois sur 20) | {fr(mc['ret_p5'], '+.0f')} % |",
        f"| Chance de finir en perte | {fr(mc['prob_loss'], '.1f')} % |",
        f"| Pire baisse médiane | −{fr(mc['dd_p50'], '.0f')} % |",
        f"| Pire baisse, 1 fois sur 20 | −{fr(mc['dd_p95'], '.0f')} % |",
        f"| Baisse de plus de 20 % / 30 % / 40 % | {fr(mc['p_dd20'], '.0f')} % / "
        f"{fr(mc['p_dd30'], '.0f')} % / {fr(mc['p_dd40'], '.0f')} % des cas |",
        f"| Série de trades perdants de suite, sur 100 trades (médiane ; 1 fois sur 20) | "
        f"{mc['streak_p50']:.0f} ; {mc['streak_p95']:.0f} |",
        "",
        f"- Des séries de {mc['streak_p50']:.0f} à {mc['streak_p95']:.0f} trades perdants "
        "de suite sont normales avec 35 à 50 % de trades gagnants : ce n'est pas le "
        "signe d'une panne.",
        "- Le passé rejoué au hasard n'est pas une prévision : un marché inédit peut "
        "faire pire.",
        "",
        "## 5. Année par année",
        "",
        "| Année | " + " | ".join(str(y) for y in years) + " |",
        "|---|" + "---|" * len(years),
        "| Rendement | " + " | ".join(f"{fr(v, '+.0f')} %" for v in years.values()) + " |",
        "",
        f"- Pire mois : {fr(worst_m, '.1f')} % ({worst_when}). "
        + (f"Année en perte : {', '.join(str(y) for y in down_years)} (marché baissier : "
           "le bot n'achète plus et resserre ses stops, la perte reste limitée)."
           if down_years else "Aucune année en perte."),
        "",
        "## Conclusion",
        "",
        f"- **Coûts** : {_verdict_costs(x2, x3)}.",
        f"- **Réglages** : {both_ok} combinaisons voisines sur 27 rentables sur les deux "
        "périodes" + (" : plateau." if plateau else "."),
        f"- **Gagnants** : les trois meilleures cryptos font {fr(top3_share, '.0f')} % des "
        "gains" + (" ; sans elles, le bot reste rentable." if survives else " ; sans elles, "
                   "il ne l'est plus sur les deux périodes."),
        f"- **À prévoir sur 3 ans** : une pire baisse autour de −{fr(mc['dd_p50'], '.0f')} %, "
        f"jusqu'à −{fr(mc['dd_p95'], '.0f')} % une fois sur 20, et des séries de "
        f"{mc['streak_p95']:.0f} trades perdants. Aucune règle n'est changée d'après ces "
        "tests.",
    ]
    return ts.format_markdown("\n".join(lines))


def main(argv: Optional[List[str]] = None) -> int:
    args = study_args("Étude de robustesse de TrendGuard", "docs/ROBUSTESSE.md", argv)
    write_report(report(*load_binance(args.cache)), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
