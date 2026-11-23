#!/usr/bin/env python3
"""
Sentiment et effet de levier : le financement des contrats à terme et
l'indice de peur et d'avidité auraient-ils aidé TrendGuard ? Étude
reproductible (docs/SENTIMENT.md).

Question : faut-il éviter d'acheter quand les spéculateurs sont trop
acheteurs (financement élevé) ou euphoriques (indice d'avidité élevé) ?

  1. Résultat des trades de la stratégie selon ces indicateurs le jour de
     l'achat, séparément en conception (jusqu'en 2022) et hors échantillon
     (depuis 2023).
  2. Filtres « pas d'achat au-dessus d'un seuil » : le seuil est choisi sur
     2018-2022 seulement, puis vérifié sur 2023 → aujourd'hui. Un filtre
     n'est retenu que s'il améliore les deux périodes.

Données sans regard vers le futur : financement moyen des 7 jours connus à
la clôture de la bougie de l'achat, indice publié le jour de la bougie.
Historiques téléchargés une fois, puis gardés en cache (data_binance/).

  python -m research.sentiment --cache data_binance
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.commun import load_binance, study_args, write_report
from research.couts import wrap_markdown
from trendguard import derives
from trendguard import market_watch as mw
from trendguard import trend_strategy as ts
from trendguard.config import LIVE_UNIVERSE_DEFAULT
from trendguard.texte import fr

FNG_URL = "https://api.alternative.me/fng/?limit=0&format=json"
FUNDING_START_MS = 1_567_296_000_000        # 2019-09-01 : premiers contrats perpétuels USDT
FUNDING_WINDOW_DAYS = 7
START = "2018-01-01"
DESIGN_END = "2022-12-31"
FUNDING_LIMITS = (0.20, 0.30, 0.50, 1.00)    # financement annualisé au-dessus duquel on n'achète pas
GREED_LIMITS = (70, 75, 80, 90)               # indice au-dessus duquel on n'achète pas
MIN_GAIN = 0.05                               # gain de Calmar exigé dans chaque période

Edges = Sequence[Tuple[str, float, float]]
FUNDING_EDGES: Edges = (("négatif (les vendeurs paient)", -math.inf, 0.0),
                        ("0 à 10 % par an", 0.0, 0.10),
                        ("10 à 30 % par an", 0.10, 0.30),
                        ("plus de 30 % par an", 0.30, math.inf))
GREED_EDGES: Edges = (("0-24 : peur extrême", 0, 25), ("25-49 : peur", 25, 50),
                      ("50-74 : avidité", 50, 75), ("75-100 : avidité extrême", 75, 101))


# ---------- Données ----------

def load_funding(cache: str, bases: Sequence[str], fetch: Callable[[str], Any] = mw.http_json,
                 end_ms: Optional[int] = None, sleep: Callable[[float], None] = time.sleep
                 ) -> Dict[str, pd.Series]:
    """Taux de financement (par 8 h) de chaque crypto, en cache CSV. Un
    téléchargement raté n'est pas mis en cache : il sera retenté."""
    folder = os.path.join(cache, "financement")
    os.makedirs(folder, exist_ok=True)
    end_ms = end_ms or int(time.time() * 1000)
    out: Dict[str, pd.Series] = {}
    for b in bases:
        path = os.path.join(folder, f"{b.lower()}.csv")
        if not os.path.exists(path):
            try:
                rows = derives.funding_history(fetch, f"{b.upper()}USDT", FUNDING_START_MS, end_ms)
            except Exception as e:
                print(f"Financement {b.upper()} indisponible ({type(e).__name__}) : "
                      "achats permis comme sans filtre.", file=sys.stderr)
                continue
            pd.DataFrame(rows, columns=["ms", "rate"]).to_csv(path, index=False)
            sleep(0.3)
        df = pd.read_csv(path)
        if df.empty:
            continue
        s = pd.Series(df["rate"].astype(float).values,
                      index=pd.to_datetime(df["ms"].astype("int64"), unit="ms", utc=True))
        s = s.sort_index()
        out[b.lower()] = s[~s.index.duplicated(keep="last")]
    return out


def load_fear_greed(cache: str, fetch: Callable[[str], Any] = mw.http_json) -> pd.Series:
    """Indice de peur et d'avidité quotidien (0 à 100), en cache CSV."""
    path = os.path.join(cache, "peur_avidite.csv")
    if not os.path.exists(path):
        data = fetch(FNG_URL)["data"]
        pd.DataFrame([(int(d["timestamp"]), int(d["value"])) for d in data],
                     columns=["ts", "value"]).to_csv(path, index=False)
    df = pd.read_csv(path)
    s = pd.Series(df["value"].astype(float).values,
                  index=pd.to_datetime(df["ts"].astype("int64"), unit="s", utc=True)).sort_index()
    return s[~s.index.duplicated(keep="last")]


def daily_funding(rates: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    """Financement annualisé moyen des 7 jours connus à la clôture de chaque
    bougie journalière (clôture = jour suivant 00:00 UTC). NaN tant que
    l'historique couvre moins de 7 jours."""
    closes = days + pd.Timedelta(days=1)
    window = rates.rolling(f"{FUNDING_WINDOW_DAYS}D").mean()
    at = window.reindex(window.index.union(closes)).sort_index().ffill().reindex(closes)
    first = rates.index.min() + pd.Timedelta(days=FUNDING_WINDOW_DAYS)
    at[closes < first] = np.nan
    return pd.Series(at.values * derives.PAYMENTS_PER_DAY * 365, index=days)


def daily_greed(fng: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    """Indice publié le jour de la bougie (00:00 UTC), donc connu à sa
    clôture ; NaN avant le premier indice."""
    return fng.reindex(fng.index.union(days)).sort_index().ffill().reindex(days)


# ---------- Mesures ----------

def buckets(values: Sequence[float], r: Sequence[float], edges: Edges
            ) -> List[Tuple[str, int, float, float]]:
    """(libellé, trades, R moyen, gagnants %) par tranche [lo, hi[ ; une
    valeur inconnue (NaN) n'entre dans aucune tranche."""
    out = []
    for label, lo, hi in edges:
        sel = [x for v, x in zip(values, r) if not math.isnan(v) and lo <= v < hi]
        if sel:
            out.append((label, len(sel), float(np.mean(sel)),
                        sum(1 for x in sel if x > 0) / len(sel) * 100))
        else:
            out.append((label, 0, math.nan, math.nan))
    return out


def skip_filter(feature: Dict[str, pd.Series], index: pd.DatetimeIndex, limit: float
                ) -> Callable[[int, List[Dict[str, Any]], Any, float], List[Dict[str, Any]]]:
    """Crochet filter_plans : pas d'achat quand l'indicateur du jour dépasse
    `limit`. Indicateur inconnu : achat permis, comme sans le filtre. La clé
    « * » vaut pour toutes les cryptos (indice de marché)."""
    def keep(i: int, plans: List[Dict[str, Any]], holdings: Any, equity: float
             ) -> List[Dict[str, Any]]:
        d = index[i]
        out = []
        for p in plans:
            s = feature.get(p["asset"], feature.get("*"))
            v = s.get(d) if s is not None else None
            if v is None or math.isnan(float(v)) or float(v) <= limit:
                out.append(p)
        return out
    return keep


def _value_at(s: Optional[pd.Series], d: pd.Timestamp) -> float:
    if s is None or d not in s.index:
        return math.nan
    return float(s.loc[d])


def _metrics_cells(m: Dict[str, float]) -> str:
    return (f"{fr(m['cagr_pct'], '+.1f')} % | {fr(m['max_dd_pct'], '.1f')} % | "
            f"{fr(m['calmar'], '.2f')} | {m['trades']}")


def _bucket_cell(n: int, r: float, w: float) -> str:
    return "0 | — | —" if n == 0 else f"{n} | {fr(r, '+.2f')} R | {fr(w, '.0f')} %"


def report(close: pd.DataFrame, volume: pd.DataFrame, funding: Dict[str, pd.Series],
           fng: pd.Series, start: str = START, design_end: str = DESIGN_END) -> str:
    """Rapport Markdown : trades par tranche d'indicateur, filtres à seuil
    choisis sur la conception et jugés hors échantillon, verdict."""
    p = ts.TrendParams()
    pre = ts.precompute(close, volume, p)
    end = str(close.index[-1].date())
    later_start = str((pd.Timestamp(design_end) + pd.Timedelta(days=1)).date())
    days = close.index
    fund_day = {a: daily_funding(s, days) for a, s in funding.items()}
    greed_day = {"*": daily_greed(fng, days)}
    cut = pd.Timestamp(design_end, tz="UTC")
    base = ts.backtest(close, volume, p, start, end, pre=pre)
    rows = [(pd.Timestamp(t["entry_date"]), float(t["r"]),
             _value_at(fund_day.get(t["asset"]), pd.Timestamp(t["entry_date"])),
             _value_at(greed_day["*"], pd.Timestamp(t["entry_date"])))
            for t in base.trades]
    design = [x for x in rows if x[0] <= cut]
    later = [x for x in rows if x[0] > cut]
    y0, y1 = start[:4], design_end[:4]
    L = ["# Sentiment et effet de levier", "",
         f"Étude reproductible : `python -m research.sentiment --cache data_binance` "
         f"(données jusqu'au {end}).", "",
         "Faut-il éviter d'acheter quand les spéculateurs sont trop acheteurs (taux de "
         "financement des contrats à terme élevé) ou euphoriques (indice de peur et "
         "d'avidité élevé) ? Les trades sont ceux de la stratégie, paramètres gelés ; "
         "les indicateurs sont ceux connus à la clôture de la bougie de l'achat.", ""]

    def table(title: str, col: int, edges: Edges) -> None:
        L.extend([f"## {title}", "",
                  f"| Tranche à l'achat | Trades {y0}-{y1} | R moyen | Gagnants "
                  f"| Trades depuis {later_start[:4]} | R moyen | Gagnants |",
                  "| --- | --- | --- | --- | --- | --- | --- |"])
        a = buckets([x[col] for x in design], [x[1] for x in design], edges)
        b = buckets([x[col] for x in later], [x[1] for x in later], edges)
        for (lab, n1, r1, w1), (_lab, n2, r2, w2) in zip(a, b):
            L.append(f"| {lab} | {_bucket_cell(n1, r1, w1)} | {_bucket_cell(n2, r2, w2)} |")
        unknown = sum(1 for x in rows if math.isnan(x[col]))
        L.extend(["", f"Trades sans indicateur connu à l'achat : {unknown} sur {len(rows)}.", ""])

    table("Résultat des trades selon le financement (annualisé, moyenne de 7 jours)", 2,
          FUNDING_EDGES)
    table("Résultat des trades selon l'indice de peur et d'avidité", 3, GREED_EDGES)

    eras = [(start, design_end), (later_start, end)]

    def run(keep: Optional[Callable[..., List[Dict[str, Any]]]] = None) -> List[Dict[str, float]]:
        hooks = ts.BacktestHooks(filter_plans=keep) if keep else None
        return [ts.backtest(close, volume, p, a, b, pre=pre, hooks=hooks).metrics for a, b in eras]

    ref = run()
    hold = [ts.buy_and_hold(close, ["btc"], a, b) for a, b in eras]
    L.extend(["## Filtres testés : pas d'achat au-dessus d'un seuil", "",
              f"Seuil choisi sur {y0}-{y1} (meilleur Calmar), puis vérifié depuis "
              f"{later_start[:4]}. Un filtre n'est retenu que s'il améliore le Calmar de "
              f"plus de {fr(MIN_GAIN, '.2f')} dans les deux périodes. Rendement annuel, "
              "baisse maximale, Calmar (rendement ÷ baisse) et nombre de trades.", "",
              f"| Règle | {y0}-{y1} rendement | baisse | Calmar | trades "
              f"| depuis {later_start[:4]} rendement | baisse | Calmar | trades |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
              f"| BTC acheté et conservé | {_metrics_cells(hold[0])} | {_metrics_cells(hold[1])} |",
              f"| stratégie sans filtre | {_metrics_cells(ref[0])} | {_metrics_cells(ref[1])} |"])
    verdicts = []
    for name, feat, limits, show in (
            ("financement", fund_day, FUNDING_LIMITS,
             lambda x: f"{fr(x * 100, '.0f')} % par an"),
            ("avidité", greed_day, GREED_LIMITS, lambda x: f"{fr(x, '.0f')}")):
        results = [(lim, run(skip_filter(feat, days, lim))) for lim in limits]
        best = max(results, key=lambda r: r[1][0]["calmar"])
        for lim, (m1, m2) in results:
            mark = f" (choisi sur {y0}-{y1})" if lim == best[0] else ""
            L.append(f"| pas d'achat si {name} > {show(lim)}{mark} | {_metrics_cells(m1)} | "
                     f"{_metrics_cells(m2)} |")
        m1, m2 = best[1]
        d1, d2 = m1["calmar"] - ref[0]["calmar"], m2["calmar"] - ref[1]["calmar"]
        verdicts.append((name, show(best[0]), d1 > MIN_GAIN and d2 > MIN_GAIN, d1, d2))
    L.extend(["", "## Verdict", ""])
    for name, lim, better, d1, d2 in verdicts:
        L.append(f"- Filtre {name} (seuil {lim}, choisi sur {y0}-{y1}) : Calmar "
                 f"{fr(d1, '+.2f')} en conception, {fr(d2, '+.2f')} depuis {later_start[:4]}. "
                 + ("**Améliore les deux périodes** : à proposer comme règle, après revue."
                    if better else "N'améliore pas les deux périodes : **non retenu**, "
                                   "l'indicateur reste une information."))
    L.extend(["", "## Lecture", "",
              "- Huit variantes ont été essayées (quatre seuils par indicateur) : un gain "
              "isolé peut venir du hasard. C'est pourquoi le seuil est choisi sur la "
              "première période et jugé sur la seconde.",
              "- Le financement n'existe que depuis l'ouverture des contrats perpétuels "
              "(septembre 2019 pour BTC, plus tard pour d'autres) : avant, le filtre "
              "laisse acheter, comme la stratégie sans filtre.",
              "- Une étude mesure ; elle ne change aucune règle du bot. Les indicateurs sont "
              "affichés dans la veille et transmis aux IA de la veille "
              "(trendguard/derives.py). Résultats passés : aucune garantie pour l'avenir."])
    return wrap_markdown(L)


def _options(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--retelecharger", action="store_true",
                    help="relire les historiques de financement et de l'indice")


def _clear_cache(cache: str) -> None:
    path = os.path.join(cache, "peur_avidite.csv")
    if os.path.exists(path):
        os.remove(path)
    folder = os.path.join(cache, "financement")
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            os.remove(os.path.join(folder, f))


def main(argv: Optional[List[str]] = None) -> int:
    args = study_args("Sentiment et effet de levier pour TrendGuard", "docs/SENTIMENT.md", argv,
                      extra=_options)
    close, volume = load_binance(args.cache)
    if args.retelecharger:
        _clear_cache(args.cache)
    funding = load_funding(args.cache, list(LIVE_UNIVERSE_DEFAULT))
    fng = load_fear_greed(args.cache)
    write_report(report(close, volume, funding, fng), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
