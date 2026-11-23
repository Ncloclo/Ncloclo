#!/usr/bin/env python3
"""
Coûts réels d'exécution : la stratégie tient-elle avec les vrais carnets
d'ordres de Binance, et jusqu'à quel capital ? Étude reproductible
(docs/COUTS_REELS.md).

Les backtests du dépôt comptent 0,1 % de glissement pour toutes les cryptos
et toutes les tailles d'ordre. Ici, chaque crypto a son propre coût, mesuré
dans son carnet d'ordres réel (demi-écart achat/vente plus profondeur
traversée), et ce coût grandit avec le montant de l'ordre. Ensuite :

  1. le backtest est refait avec ces coûts réels, puis 2 et 3 fois plus
     chers (les carnets étaient plus minces en 2018-2020 qu'aujourd'hui) ;
  2. la capacité est mesurée : rendement quand le capital passe de
     10 000 à 5 millions d'USDT, les gros ordres coûtant plus cher.

Les carnets sont relevés une fois puis gardés en cache
(data_binance/couts_carnet.json) : l'étude se refait à l'identique.
--remesurer relit les carnets du moment.

  python -m research.couts --cache data_binance
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import statistics
import sys
import textwrap
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from research.commun import load_binance, study_args, write_report
from trendguard import realisme
from trendguard import trend_strategy as ts
from trendguard.config import LIVE_UNIVERSE_DEFAULT
from trendguard.texte import fr

SIZES = (100.0, 1_000.0, 2_500.0, 10_000.0, 25_000.0, 100_000.0, 250_000.0, 1_000_000.0)
SAMPLES = 3                 # relevés par crypto (médiane : un relevé aberrant pèse peu)
SAMPLE_GAP_SEC = 20
BOOK_LEVELS = 1000          # niveaux lus de chaque côté (le plus profond permis)
CACHE_NAME = "couts_carnet.json"
CAPITALS = (10_000.0, 100_000.0, 1_000_000.0, 5_000_000.0)
# Règle du bot (bot_execution._book_anomaly) : pas d'achat si le carnet
# propose moins de 3 fois le montant de l'achat à moins de 1 % du prix.
DEPTH_BAND = 0.01
DEPTH_MULT = 3.0


# ---------- Mesure des carnets ----------

def measure_book(book: Dict[str, Any], sizes=SIZES) -> Optional[Dict[str, Any]]:
    """Coût, en fraction du prix, d'un achat et d'une vente au marché de
    chaque montant (en USDT), mesuré par rapport au milieu du carnet."""
    f = realisme.fill(book, "buy", 1e-12)
    if f is None:
        return None
    mid = f.mid
    ask, bid = f.best, 2 * mid - f.best
    depth_ask = sum(float(p) * float(q) for p, q, *_ in book["asks"]
                    if float(p) <= ask * (1 + DEPTH_BAND))
    out: Dict[str, Any] = {"mid": mid, "spread_pct": 2 * f.cost_pct, "depth_1pct": depth_ask,
                           "bid": bid, "buy": {}, "sell": {}}
    for usd in sizes:
        qty = usd / mid
        for side in ("buy", "sell"):
            g = realisme.fill(book, side, qty)
            if g is None:
                return None
            out[side][str(int(usd))] = {"cost": g.cost_pct, "complete": g.complete}
    return out


def measure_market(exchange: Any, assets: List[str], samples: int = SAMPLES,
                   gap: float = SAMPLE_GAP_SEC, sleep=time.sleep) -> Dict[str, Any]:
    """Relevés des carnets des paires USDT ; médiane des relevés par taille."""
    raw: Dict[str, List[Dict[str, Any]]] = {a: [] for a in assets}
    for k in range(samples):
        for a in assets:
            try:
                book = exchange.fetch_order_book(f"{a.upper()}/USDT", limit=BOOK_LEVELS)
            except Exception:
                continue
            m = measure_book(book)
            if m is not None:
                raw[a].append(m)
        if k < samples - 1:
            sleep(gap)
    out: Dict[str, Any] = {}
    for a, ms in raw.items():
        if not ms:
            continue
        row: Dict[str, Any] = {"n": len(ms),
                               "spread_pct": statistics.median(m["spread_pct"] for m in ms),
                               "depth_1pct": statistics.median(m["depth_1pct"] for m in ms),
                               "buy": {}, "sell": {}}
        for side in ("buy", "sell"):
            for usd in ms[0][side]:
                row[side][usd] = statistics.median(m[side][usd]["cost"] for m in ms)
                row.setdefault("short", {})[f"{side}:{usd}"] = not all(
                    m[side][usd]["complete"] for m in ms)
        out[a.lower()] = row
    return out


def load_or_measure(cache: str, remeasure: bool = False, exchange: Any = None) -> Dict[str, Any]:
    path = os.path.join(cache, CACHE_NAME)
    if not remeasure and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    if exchange is None:
        import ccxt
        exchange = ccxt.binance({"enableRateLimit": True})
    data = {"date": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "samples": SAMPLES, "levels": BOOK_LEVELS,
            "assets": measure_market(exchange, list(LIVE_UNIVERSE_DEFAULT))}
    os.makedirs(cache, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    return data


# ---------- Modèle de coût ----------

def cost_model(measured: Dict[str, Any], stress: float = 1.0,
               fallback: float = 0.001) -> Callable[[str, float, str], float]:
    """(crypto, montant USDT, sens) → coût en fraction du prix : interpolé
    entre les tailles mesurées (échelle logarithmique), en racine carrée
    du montant au-delà de la plus grande, multiplié par `stress`. Crypto
    jamais mesurée : le glissement fixe du dépôt."""
    tables: Dict[Tuple[str, str], Tuple[List[float], List[float]]] = {}
    for a, row in measured.items():
        for side in ("buy", "sell"):
            pts = sorted((float(k), float(v)) for k, v in row[side].items())
            tables[(a, side)] = ([math.log(x) for x, _ in pts], [y for _, y in pts])

    def cost(asset: str, notional: float, side: str) -> float:
        t = tables.get((asset, side))
        if t is None or notional <= 0:
            return fallback * stress
        xs, ys = t
        x = math.log(max(notional, 1e-9))
        if x <= xs[0]:
            c = ys[0]
        elif x >= xs[-1]:
            c = ys[-1] * math.sqrt(notional / math.exp(xs[-1]))
        else:
            j = bisect.bisect_right(xs, x)
            w = (x - xs[j - 1]) / (xs[j] - xs[j - 1])
            c = ys[j - 1] + w * (ys[j] - ys[j - 1])
        return max(0.0, c) * stress

    return cost


def thin_book_filter(measured: Dict[str, Any]) -> Callable[..., List[Dict[str, Any]]]:
    """Crochet filter_plans : la règle du bot appliquée au backtest, avec la
    profondeur mesurée aujourd'hui (pas d'achat dans un carnet trop mince)."""
    def keep(i: int, plans: List[Dict[str, Any]], holdings: Any, equity: float) -> List[Dict[str, Any]]:
        out = []
        for plan in plans:
            depth = (measured.get(plan["asset"]) or {}).get("depth_1pct")
            if depth is None or depth >= DEPTH_MULT * plan["cost"]:
                out.append(plan)
        return out
    return keep


class Ledger:
    """Coût d'exécution réellement payé par le backtest (moyenne pondérée
    par les montants), pour le rapport."""

    def __init__(self, model: Callable[[str, float, str], float]):
        self.model, self.paid, self.volume = model, 0.0, 0.0

    def __call__(self, asset: str, notional: float, side: str) -> float:
        c = self.model(asset, notional, side)
        self.paid += c * notional
        self.volume += notional
        return c

    @property
    def mean_pct(self) -> float:
        return self.paid / self.volume * 100 if self.volume > 0 else 0.0


# ---------- Rapport ----------

def wrap_markdown(lines: List[str], width: int = 80) -> str:
    """Paragraphes et puces repliés à `width` colonnes (règle markdownlint du
    dépôt) ; titres et tableaux laissés tels quels."""
    out: List[str] = []
    for line in lines:
        if len(line) <= width or line.startswith(("|", "#")):
            out.append(line)
        elif line.startswith("- "):
            out.extend(textwrap.wrap(line[2:], width, initial_indent="- ", subsequent_indent="  ",
                                     break_long_words=False, break_on_hyphens=False))
        else:
            out.extend(textwrap.wrap(line, width, break_long_words=False, break_on_hyphens=False))
    return "\n".join(out)


def _cell(row: Dict[str, Any], usd: str) -> str:
    """Coût moyen achat/vente d'un montant, ou « carnet trop mince »."""
    short = row.get("short", {})
    if short.get(f"buy:{usd}") or short.get(f"sell:{usd}"):
        return "carnet trop mince"
    return f"{fr((row['buy'][usd] + row['sell'][usd]) / 2 * 100, '.3f')} %"


def _row(label: str, m: Dict[str, float], paid: Optional[float]) -> str:
    cost = "0,10 % (fixe)" if paid is None else f"{fr(paid, '.3f')} %"
    return (f"| {label} | {fr(m['cagr_pct'], '+.1f')} % | {fr(m['max_dd_pct'], '.1f')} % | "
            f"{fr(m['calmar'], '.2f')} | {m['trades']} | {cost} |")


def report(close, volume, measured: Dict[str, Any], date: str) -> str:
    """Rapport Markdown : coûts mesurés au carnet, backtests à coût fixe et à
    coût réel par période, puis capacité selon le capital."""
    p = ts.TrendParams()
    pre = ts.precompute(close, volume, p)
    end = str(close.index[-1].date())
    eras = [("2018-01-01", "2022-12-31", "2018-2022 (conception)"),
            ("2023-01-01", end, f"2023 → {end}")]
    L = ["# Coûts réels d'exécution sur Binance", "",
         f"Étude reproductible : `python -m research.couts --cache data_binance` "
         f"(carnets relevés le {date[:10]}, {measured and len(measured)} paires, médiane de "
         f"{SAMPLES} relevés de {BOOK_LEVELS} niveaux par côté).", "",
         "Les backtests du dépôt comptent 0,1 % de glissement par ordre pour toutes les cryptos. "
         "Ici, chaque crypto paie le coût mesuré dans son vrai carnet d'ordres (demi-écart plus "
         "profondeur traversée), qui grandit avec le montant de l'ordre. Les frais Binance "
         "(0,1 % par ordre) s'ajoutent dans les deux cas.", "",
         "## Coût mesuré d'un ordre au marché", "",
         "| Crypto | Écart achat/vente | Profondeur à 1 % | 1 000 USDT | 10 000 USDT | 100 000 USDT "
         "| 1 000 000 USDT |",
         "| --- | --- | --- | --- | --- | --- | --- |"]
    order = sorted(measured, key=lambda a: measured[a]["buy"].get("10000", 0))
    for a in order:
        row = measured[a]
        L.append(f"| {a.upper()} | {fr(row['spread_pct'] * 100, '.3f')} % | "
                 f"{fr(row.get('depth_1pct', 0), ',.0f')} USDT | {_cell(row, '1000')} | "
                 f"{_cell(row, '10000')} | {_cell(row, '100000')} | {_cell(row, '1000000')} |")
    L += ["", "Coût moyen d'un achat et d'une vente au marché, par ordre, en plus des frais "
          "Binance. Profondeur à 1 % : montant proposé à la vente à moins de 1 % du meilleur "
          "prix. « carnet trop mince » : les 1 000 niveaux lus ne suffisent pas pour ce "
          "montant.", ""]

    L += ["## La stratégie avec les coûts réels (capital 10 000 USDT)", "",
          "| Période et coûts | Rendement par an | Baisse max | Calmar | Trades | Coût moyen payé par ordre |",
          "| --- | --- | --- | --- | --- | --- |"]
    for a, b, name in eras:
        base = ts.backtest(close, volume, p, a, b, pre=pre).metrics
        L.append(_row(f"{name}, glissement fixe du dépôt", base, None))
        for k in (1.0, 2.0, 3.0):
            led = Ledger(cost_model(measured, k))
            m = ts.backtest(close, volume, p, a, b, pre=pre,
                            hooks=ts.BacktestHooks(exec_cost=led)).metrics
            tag = "coûts réels" if k == 1 else f"coûts réels × {int(k)}"
            L.append(_row(f"{name}, {tag}", m, led.mean_pct))
    L += ["", "## Capacité : jusqu'à quel capital ?", "",
          f"Même stratégie de 2018 à {end}, coûts réels qui grandissent avec la taille des "
          "ordres, et la règle du bot : pas d'achat quand le carnet propose moins de 3 fois "
          "le montant de l'achat à moins de 1 % du prix (profondeur mesurée aujourd'hui).", "",
          "| Capital de départ | Rendement par an | Baisse max | Calmar | Trades | Coût moyen payé par ordre |",
          "| --- | --- | --- | --- | --- | --- |"]
    keep = thin_book_filter(measured)
    for cap in CAPITALS:
        for k in (1.0, 2.0):
            led = Ledger(cost_model(measured, k))
            m = ts.backtest(close, volume, p, "2018-01-01", end, capital=cap, pre=pre,
                            hooks=ts.BacktestHooks(exec_cost=led, filter_plans=keep)).metrics
            tag = "" if k == 1 else " (coûts × 2)"
            L.append(_row(f"{fr(cap, ',.0f')} USDT{tag}", m, led.mean_pct))
    L += ["", "## Lecture", "",
          "- À 10 000 USDT, un ordre du bot coûte en moyenne bien moins que les 0,1 % de "
          "glissement comptés par les backtests du dépôt : cette hypothèse est prudente, et "
          "les résultats tiennent même avec des coûts trois fois plus élevés.",
          "- Un écart d'environ un point entre deux lignes proches (coûts × 2 un peu "
          "meilleurs que × 1, par exemple) vient de l'enchaînement des trades : un prix "
          "d'achat différent change la taille des positions, puis les achats suivants.",
          "- Quand le capital grandit, les petites cryptos (NEO, XTZ, ALGO, ETC…) ne peuvent "
          "plus absorber les ordres : la règle du bot les écarte, il y a moins de trades et "
          "le rendement baisse, mais les coûts payés restent faibles.",
          "- Les coûts mesurés sont ceux d'un instant : un carnet change d'heure en heure. "
          "Le bot décide à 00:02 UTC, une heure plutôt liquide.",
          "- Les carnets étaient plus minces en 2018-2020 qu'aujourd'hui : les lignes « × 2 » "
          "et « × 3 » couvrent ce risque.",
          "- La capacité ne compte que le coût des ordres. Au-delà de quelques millions "
          "d'USDT, la stratégie déplacerait elle-même les prix des petites cryptos, ce que "
          "ce modèle ne capture pas entièrement.",
          "- Une étude mesure ; elle ne change aucune règle du bot. En paper, le bot paie déjà "
          "les coûts réels de chaque ordre (TG_PAPER_REEL, trendguard/realisme.py)."]
    return wrap_markdown(L)


def _options(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--remesurer", action="store_true", help="relire les carnets du moment")


def main(argv: Optional[List[str]] = None) -> int:
    args = study_args("Coûts réels d'exécution de TrendGuard", "docs/COUTS_REELS.md", argv,
                      extra=_options)
    close, volume = load_binance(args.cache)
    data = load_or_measure(args.cache, args.remesurer)
    write_report(report(close, volume, data["assets"], data["date"]), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
