#!/usr/bin/env python3
"""
Garde « NO TRADE » : quels seuils, sans dégrader la stratégie ? Étude
reproductible (prompt maître §44, docs/PLATEFORME.md, trendguard/garde.py).

Deux règles rejouées sur la boucle du bot (données Binance, profil prudent) :
aucun achat le jour où le capital a perdu X % depuis la veille, ou le jour
où BTC a bougé de Y % ou plus. Une règle n'est gardée que si elle ne change
rien de mesurable à la stratégie : c'est un garde-fou contre l'anormal, pas
une nouvelle règle de trading.

  python -m research.garde --cache data_binance
"""

from __future__ import annotations

import dataclasses
import sys
from typing import List, Optional

import numpy as np

from research.commun import load_binance, study_args
from trendguard import evolution
from trendguard import trend_strategy as ts
from trendguard.texte import fr

PROFILE = ((0.10, 0.5),)


class DailyLoss:
    """Aucun achat le jour où le capital a perdu `limit` depuis la veille."""

    def __init__(self, limit: float):
        self.limit, self.prev, self.blocked = limit, None, 0

    def scale(self, i: int, equity: float, peak: float) -> float:
        out = 1.0
        if self.limit and self.prev and equity < self.prev * (1 - self.limit):
            out, self.blocked = 0.0, self.blocked + 1
        self.prev = equity
        return out


class BtcMove:
    """Aucun achat le jour où BTC a bougé de `limit` ou plus."""

    def __init__(self, limit: float, btc_ret: np.ndarray):
        self.limit, self.ret, self.blocked = limit, btc_ret, 0

    def scale(self, i: int, equity: float, peak: float) -> float:
        r = self.ret[i]
        if self.limit and np.isfinite(r) and abs(r) >= self.limit:
            self.blocked += 1
            return 0.0
        return 1.0


def main(argv: Optional[List[str]] = None) -> int:
    """Tableau Markdown des seuils étudiés."""
    args = study_args("Garde NO TRADE de TrendGuard", None, argv)
    close, volume = load_binance(args.cache)
    end = str(close.index[-1].date())
    base = dataclasses.replace(ts.TrendParams(), dd_throttle=PROFILE)
    pre = ts.precompute(close, volume, base)
    eras = [("2018-01-01", "2022-12-31"), ("2023-01-01", end)]
    btc_ret = close["btc"].pct_change().values
    print(f"Données Binance jusqu'au {end}, profil prudent du bot.\n")
    print("| Règle | 2018-22 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar | "
          "hasard 1 fois sur 20 | jours sans achat |")
    print("|---|---|---|---|---|---|---|---|---|")
    rules = [("bot sans garde", lambda: DailyLoss(0))]
    rules += [(f"perte du jour ≥ {int(x * 100)} %", lambda x=x: DailyLoss(x)) for x in (0.03, 0.05, 0.08)]
    rules += [(f"BTC bouge de ≥ {int(x * 100)} % en un jour", lambda x=x: BtcMove(x, btc_ret))
              for x in (0.10, 0.15, 0.20)]
    for label, make in rules:
        cells, blocked = [], 0
        for a, b in eras:
            h = make()
            m = ts.backtest(close, volume, base, a, b, pre=pre, hooks=ts.BacktestHooks(risk_scale=h.scale)).metrics
            blocked += h.blocked
            cells.append(f"{fr(m['cagr_pct'], '+.1f')} % | {fr(m['max_dd_pct'], '.1f')} % | {fr(m['calmar'], '.2f')}")
        h = make()
        full = ts.backtest(close, volume, base, eras[0][0], end, pre=pre,
                           hooks=ts.BacktestHooks(risk_scale=h.scale)).equity
        print(f"| {label} | {' | '.join(cells)} | −{fr(evolution.block_luck(full)[1], '.0f')} % | {blocked} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
