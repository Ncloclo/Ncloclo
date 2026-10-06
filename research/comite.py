#!/usr/bin/env python3
"""
Le comité d'agents aurait-il aidé ? Étude reproductible (prompt maître,
étape 5 ; docs/AGENTS.md, trendguard/comite.py).

Deux mesures sur l'historique Binance, avec la boucle du bot (profil prudent) :
1. chaque trade de la stratégie, classé selon l'avis que le comité aurait
   donné le jour de l'achat (avec les seules données connues ce jour-là) :
   nombre, part de gagnants, R moyen ;
2. la stratégie rejouée en n'achetant QUE ce que le comité recommande
   (« achat »), contre la stratégie seule, sur 2018-2022 et depuis 2023.

Le comité ne deviendrait une règle que s'il faisait nettement mieux sur les
deux époques (principe du dépôt : seul ce qui est validé est exécuté).
Sans historique de sentiment, l'agent sentiment reste neutre dans l'étude.

  python -m research.comite --cache data_binance
"""

from __future__ import annotations

import dataclasses
import sys
from typing import Any, Dict, List, Optional

import pandas as pd

from research.commun import load_binance, study_args, write_report
from trendguard import comite, regimes
from trendguard import trend_strategy as ts
from trendguard.texte import fr

PROFILE = ((0.10, 0.5),)
KEYS = ("close", "vol", "prior_high", "mom", "age", "vol30")
POLICY = {"halted": False, "safe_mode": False, "garde_blocked": []}


class Committee:
    """Avis du comité sur un plan d'achat de la boucle de backtest, avec les
    données connues ce jour-là seulement (régime et indicateurs causaux)."""

    def __init__(self, close: pd.DataFrame, cols: Dict[str, Dict[str, Any]], frame: pd.DataFrame,
                 p: ts.TrendParams):
        self.close, self.cols, self.frame, self.p = close, cols, frame, p
        self.reg = comite.registry()
        self.cache: Dict[Any, str] = {}

    def view(self, i: int, asset: str, held: Dict[str, float], equity: float) -> str:
        key = (i, asset, tuple(sorted(held)))
        if key not in self.cache:
            day = str(self.close.index[i].date())
            snap = {k: float(self.cols[asset][k][i]) for k in KEYS}
            data = comite.board(asset, day, self.close, snap, self.p, held, equity, 1.0, POLICY,
                                regime=regimes.at(self.frame, day) or {})
            self.cache[key] = comite.evaluate(asset, data, self.reg)[0].recommendation
        return self.cache[key]

    def filter(self, i: int, plans: List[Dict[str, Any]], holdings: Dict[str, ts.Holding],
               equity: float) -> List[Dict[str, Any]]:
        held = {a: h.risk_quote for a, h in holdings.items()}
        return [pl for pl in plans if self.view(i, pl["asset"], held, equity) == "ACHAT"]


def main(argv: Optional[List[str]] = None) -> int:
    """Rapport Markdown de l'étude."""
    args = study_args("Comité d'agents de TrendGuard : aurait-il aidé ?", "docs/COMITE_ETUDE.md", argv)
    close, volume = load_binance(args.cache)
    end = str(close.index[-1].date())
    p = dataclasses.replace(ts.TrendParams(), dd_throttle=PROFILE)
    pre = ts.precompute(close, volume, p)
    cols = pre[0]
    frame = regimes.regime_frame(close, p.regime_sma)
    com = Committee(close, cols, frame, p)
    base = ts.backtest(close, volume, p, "2018-01-01", end, pre=pre)
    by: Dict[str, List[float]] = {}
    for t in base.trades:
        i = close.index.get_loc(t["entry_date"])
        rec = com.view(i, t["asset"], {}, 10_000.0)
        by.setdefault(rec, []).append(float(t["r"]))
    lines = [f"# Le comité d'agents aurait-il aidé ? (données Binance jusqu'au {end})", "",
             "Étude reproductible : `python -m research.comite --cache data_binance`. Boucle du bot, profil "
             "prudent ; avis du comité calculé avec les seules données connues le jour de chaque achat ; "
             "agent sentiment neutre (pas d'historique).", "",
             "## 1. Les trades de la stratégie, selon l'avis qu'aurait donné le comité", "",
             "| Avis du comité | Trades | Gagnants | R moyen | R total |", "| --- | --- | --- | --- | --- |"]
    for rec in comite.RECOMMENDATIONS:
        rs = by.get(rec)
        if rs:
            lines.append(f"| {comite.LABELS[rec]} | {len(rs)} | {fr(sum(r > 0 for r in rs) / len(rs) * 100, '.0f')} % | "
                         f"{fr(sum(rs) / len(rs), '+.2f')} | {fr(sum(rs), '+.1f')} |")
    lines += ["", "## 2. La stratégie seule, puis n'achetant que sur un avis « achat » du comité", "",
              "| Variante | 2018-22 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar | trades |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    verdict = []
    for label, hooks in (("stratégie seule", None),
                         ("achats filtrés par le comité", ts.BacktestHooks(filter_plans=com.filter))):
        cells, n = [], 0
        for a, b in (("2018-01-01", "2022-12-31"), ("2023-01-01", end)):
            m = ts.backtest(close, volume, p, a, b, pre=pre, hooks=hooks).metrics
            cells.append(f"{fr(m['cagr_pct'], '+.1f')} % | {fr(m['max_dd_pct'], '.1f')} % | {fr(m['calmar'], '.2f')}")
            verdict.append(m["calmar"])
            n += m["trades"]
        lines.append(f"| {label} | {' | '.join(cells)} | {n} |")
    better = verdict[2] > verdict[0] * 1.1 and verdict[3] > verdict[1] * 1.1
    lines += ["", "**Conclusion** : " + (
        "le comité fait nettement mieux sur les deux époques (Calmar +10 % au moins) : une règle pourra être "
        "proposée, puis éprouvée comme toute évolution." if better else
        "le comité ne fait pas nettement mieux que la stratégie seule sur les deux époques : il reste "
        "consultatif, et ses avis sont mesurés sur les trades réels (journal financier).")]
    write_report("\n".join(lines), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
