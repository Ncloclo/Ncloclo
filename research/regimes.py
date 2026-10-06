#!/usr/bin/env python3
"""
Dans quelles conditions la stratégie gagne-t-elle ou échoue-t-elle ? Étude
reproductible (prompt maître §8 et §45, docs/REGIMES.md).

Les trades de TrendGuard (réglages du bot, profil prudent, données Binance
depuis 2018) sont classés selon le régime de marché du jour de l'achat
(trendguard/regimes.py) : tendance de BTC, volatilité, appétit pour le
risque, phase (crise, reprise). Pour chaque régime : nombre de trades, part
de gagnants, R moyen et total.

  python -m research.regimes --cache data_binance
"""

from __future__ import annotations

import dataclasses
import sys
from typing import List, Optional

from research.commun import load_binance, study_args, write_report
from trendguard import regimes
from trendguard import trend_strategy as ts
from trendguard.texte import fr

PROFILE = ((0.10, 0.5),)          # profil prudent du bot (TG_DD_THROTTLE=0.10:0.5)
INTRO = """# Régimes de marché : où la stratégie gagne, où elle échoue

Étude reproductible : `python -m research.regimes --cache data_binance`
(prompt maître, §8 et §45 : « dans quelles conditions cette stratégie a-t-elle
échoué ? »). Les trades de TrendGuard, rejoués avec les réglages du bot sur
les données Binance, sont classés selon le régime du jour de l'achat. Le bot
ne décide rien sur ces lectures : sa règle de marché reste BTC au-dessus de
sa moyenne 150 jours ; elles servent à comprendre et à expliquer.
"""


def report(close, volume, start: str = "2018-01-01") -> str:
    """Le rapport Markdown de l'étude."""
    p = dataclasses.replace(ts.TrendParams(), dd_throttle=PROFILE)
    end = str(close.index[-1].date())
    res = ts.backtest(close, volume, p, start, end)
    frame = regimes.regime_frame(close)
    stats = regimes.by_regime(res.trades, frame)
    L: List[str] = [INTRO, f"## Données : {len(close.columns)} cryptos, {start} → {end}, "
                           f"{len(res.trades)} trades\n"]
    for k in regimes.DIMENSIONS:
        rows = stats.get(k) or []
        if not rows:
            continue
        L.append(f"### {regimes.LABELS[k]} le jour de l'achat\n")
        L.append("| Régime | Trades | Gagnants | R moyen | R total |")
        L.append("| --- | --- | --- | --- | --- |")
        for s in rows:
            L.append(f"| {s['regime']} | {s['trades']} | {fr(s['win_pct'], '.0f')} % | "
                     f"{fr(s['avg_r'], '+.2f')} | {fr(s['total_r'], '+.1f')} |")
        L.append("")
    bad = regimes.failures(stats)
    L.append("## Ce qu'il faut en retenir\n")
    if bad:
        L.append("Conditions où la stratégie a perdu en moyenne (10 trades au moins) :\n")
        L.extend(f"- {b}" for b in bad)
    else:
        L.append("Dans aucun régime (10 trades au moins), la stratégie n'a perdu en moyenne.")
    weak = sorted((s for rows in stats.values() for s in rows if s["trades"] >= 10),
                  key=lambda s: s["avg_r"])[:2]
    if weak:
        L.append("Les régimes où elle gagne le moins : " + ", ".join(
            f"{s['regime']} ({fr(s['avg_r'], '+.2f')} R par trade, {fr(s['win_pct'], '.0f')} % de "
            f"gagnants)" for s in weak) + ".")
    L.append("")
    L.append("Le régime ne suffit pas à décider : un trade gagnant sur trois ou quatre "
             "paie les autres, dans tous les régimes où la stratégie achète. Le filtre "
             "qui compte déjà est la tendance de BTC (aucun achat en marché baissier).")
    return "\n".join(L)


def main(argv: Optional[List[str]] = None) -> int:
    """Étude des régimes ; rapport écrit dans docs/REGIMES.md."""
    args = study_args("Régimes de marché de TrendGuard", "docs/REGIMES.md", argv)
    close, volume = load_binance(args.cache)
    write_report(ts.format_markdown(report(close, volume)), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
