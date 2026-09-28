#!/usr/bin/env python3
"""
Étude d'adaptation de TrendGuard — reproductible (docs/ADAPTATION.md).

Compare la stratégie de référence à quatre familles d'adaptation du risque,
sur les données Binance des paires tradées par le bot :
  dd     réduction du risque selon la baisse de la stratégie depuis son pic
  perf   réduction du risque si l'espérance des N derniers trades est < 0
  corr   budget de risque de portefeuille ajusté des corrélations
  daily  nombre maximum de nouvelles entrées par jour
Protocole : sélection sur 2018-2022 (IS), vérification 2023 → aujourd'hui
(OOS). Une adaptation n'est adoptée par défaut que si elle gagne sur les DEUX.

  python -m research.adaptation --cache data_binance
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import os
import sys
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from trendguard import diagnostics as dg
from trendguard import trend_strategy as ts
from trendguard.config import LIVE_UNIVERSE_DEFAULT
import v29


def load_binance(cache: str) -> tuple:
    """Clôtures/volumes Binance, mis en cache au format Coin Metrics."""
    bases = [a.lower() for a in LIVE_UNIVERSE_DEFAULT]
    if not all(os.path.exists(os.path.join(cache, f"{a}.csv")) for a in bases):
        os.makedirs(cache, exist_ok=True)
        # Données publiques (data-api.binance.vision) : accessibles partout,
        # y compris depuis un serveur aux États-Unis.
        close, volume, errors = dg.fetch_daily_history(
            v29.PublicKlines(), [a.upper() for a in bases], since="2017-07-01")
        if errors:
            print(f"Erreurs de téléchargement : {errors}")
        for a in close.columns:
            pd.DataFrame({"time": close.index.strftime("%Y-%m-%d"),
                          "PriceUSD": close[a].values,
                          "volume_reported_spot_usd_1d": volume[a].values}
                         ).dropna(subset=["PriceUSD"]).to_csv(
                os.path.join(cache, f"{a}.csv"), index=False)
    return ts.load_coinmetrics(cache, bases)


def run_variant(close: pd.DataFrame, volume: pd.DataFrame, pre, p: ts.TrendParams,
                start: str, end: str, perf: Optional[tuple] = None,
                corr: Optional[float] = None, daily: Optional[int] = None,
                capital: float = 10_000.0) -> Dict[str, float]:
    """Backtest de trend_strategy (même logique) + adaptations optionnelles
    perf / corr / daily. L'adaptation `dd` passe par p.dd_throttle."""
    cols, reg = pre
    idx = close.index
    logret = np.log(close / close.shift(1))
    lo = idx.searchsorted(pd.Timestamp(start, tz="UTC"))
    hi = idx.searchsorted(pd.Timestamp(end, tz="UTC"), side="right")
    cost_out = p.fee + p.slippage
    cash = peak = capital
    hold: Dict[str, ts.Holding] = {}
    last_px: Dict[str, float] = {}
    trades: List[Dict[str, Any]] = []
    eq_hist = []
    for i in range(lo, hi):
        d = idx[i]
        snap = {a: {k: c[k][i] for k in c} for a, c in cols.items()}
        bull = bool(reg[i])
        for a, reason in ts.update_positions(hold, snap, bull, p):
            h = hold.pop(a)
            px = snap[a]["close"]
            if reason == "DELISTED" or not ts._finite(px):
                px = last_px.get(a, h.entry) * 0.5
            proceeds = h.qty * px * (1 - cost_out)
            cash += proceeds
            trades.append({"r": (proceeds - h.cost) / h.risk_quote,
                           "pnl": proceeds - h.cost})
        for a in hold:
            last_px[a] = snap[a]["close"]
        equity = cash + sum(h.qty * snap[a]["close"] for a, h in hold.items())
        peak = max(peak, equity)
        mult = ts.risk_multiplier(equity, peak, p)
        if perf and len(trades) >= perf[0] and \
                np.mean([t["r"] for t in trades[-perf[0]:]]) < 0:
            mult = min(mult, perf[1])
        plans = ts.plan_entries(hold, snap, bull, equity, cash, p, mult)
        if daily:
            plans = plans[:daily]
        if corr and plans:
            window = logret.iloc[max(0, i - 89):i + 1]
            names, risks, chosen = list(hold), [h.risk_quote for h in hold.values()], []
            for pl in plans:
                n2, r2 = names + [pl["asset"]], np.array(risks + [pl["risk_quote"]])
                c = window[n2].corr().fillna(0).to_numpy(copy=True)   # pandas 3 : .values en lecture seule
                np.fill_diagonal(c, 1.0)
                if math.sqrt(max(float(r2 @ c @ r2), 0.0)) <= corr * equity:
                    chosen.append(pl)
                    names, risks = n2, list(r2)
            plans = chosen
        for pl in plans:
            a = pl["asset"]
            cash -= pl["cost"]
            hold[a] = ts.Holding(a, pl["qty"], pl["entry"], pl["stop"],
                                 pl["ref_price"], d, pl["risk_quote"], pl["cost"])
            last_px[a] = pl["ref_price"]
        eq_hist.append(cash + sum(h.qty * snap[a]["close"] for a, h in hold.items()))
    eq = pd.Series(eq_hist, index=idx[lo:hi])
    m = ts.compute_metrics(eq, trades)
    monthly = eq.resample("ME").last().pct_change().dropna()
    m["worst_month_pct"] = float(monthly.min() * 100)
    return m


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Étude d'adaptation TrendGuard")
    ap.add_argument("--cache", default="data_binance")
    args = ap.parse_args(argv)
    close, volume = load_binance(args.cache)
    base = ts.TrendParams()
    pre = ts.precompute(close, volume, base)
    is_p, oos_p = ("2018-01-01", "2022-12-31"), ("2023-01-01", str(close.index[-1].date()))
    V = {"référence": (base, {})}
    for thr in (0.10, 0.15, 0.20):
        V[f"dd ≥ {int(thr * 100)} % → risque × 0.5"] = (
            dataclasses.replace(base, dd_throttle=((thr, 0.5),)), {})
    V["dd 10 %/20 % → × 0.5/× 0.25"] = (
        dataclasses.replace(base, dd_throttle=((0.10, 0.5), (0.20, 0.25))), {})
    for n in (10, 20, 30):
        V[f"espérance {n} derniers < 0 → × 0.5"] = (base, {"perf": (n, 0.5)})
    for c in (0.03, 0.04, 0.05):
        V[f"risque corrélé ≤ {int(c * 100)} %"] = (base, {"corr": c})
    for k in (2, 3):
        V[f"max {k} entrées / jour"] = (base, {"daily": k})
    for r in (0.005, 0.007):
        V[f"risque fixe {r * 100:.1f} %"] = (
            dataclasses.replace(base, risk_pct=r, max_total_risk=6 * r), {})

    def f(m):
        return (f"{m['cagr_pct']:+6.1f} % | {m['max_dd_pct']:6.1f} % | "
                f"{m['sharpe']:4.2f} | {m['calmar']:4.2f} | {m['worst_month_pct']:6.1f} %")
    print("| Variante | IS CAGR | IS DD | IS Sharpe | IS Calmar | IS pire mois | "
          "OOS CAGR | OOS DD | OOS Sharpe | OOS Calmar | OOS pire mois |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, (p, kw) in V.items():
        a = run_variant(close, volume, pre, p, *is_p, **kw)
        b = run_variant(close, volume, pre, p, *oos_p, **kw)
        print(f"| {name} | {f(a)} | {f(b)} |")
    print(f"\nIS = {is_p[0]} → {is_p[1]} ; OOS = {oos_p[0]} → {oos_p[1]} (Binance, "
          f"{len(close.columns)} paires)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
