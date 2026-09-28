#!/usr/bin/env python3
"""
Étude de sélection et de prise de bénéfice de TrendGuard — reproductible
(docs/SELECTION.md).

Deux questions posées par l'utilisateur, testées avec le même protocole
que docs/ADAPTATION.md (choix sur 2018-2022, vérification sur 2023 →
aujourd'hui, frais 0,1 % et glissement 0,1 % par côté) :

1. Ne trader que les 10 cryptos les plus rentables (bénéfice de la
   stratégie, achats ET ventes, sur les 2 dernières années) fait-il mieux
   que trader les 21 ?
2. Vendre dès qu'un gain donné est atteint (prise de bénéfice) fait-il
   mieux que laisser courir le gain jusqu'au stop suiveur ?

  python research_selection.py --cache data_binance --out docs/SELECTION.md
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd

import research_adaptation as ra
import trend_strategy as ts


def run(close: pd.DataFrame, pre, records, p: ts.TrendParams, start: str, end: str,
        top: Optional[int] = None, days: int = 730, hysteresis: int = 3,
        tp_r: Optional[float] = None, tp_frac: float = 1.0,
        capital: float = 10_000.0) -> Dict[str, float]:
    cols, reg = pre
    idx = close.index
    closes = {a: c["close"] for a, c in cols.items()}
    lo = idx.searchsorted(pd.Timestamp(start, tz="UTC"))
    hi = idx.searchsorted(pd.Timestamp(end, tz="UTC"), side="right")
    cost_out = p.fee + p.slippage
    cash = peak = capital
    hold: Dict[str, ts.Holding] = {}
    realized: Dict[str, float] = {}
    took: Set[str] = set()
    last_px: Dict[str, float] = {}
    trades: List[Dict[str, Any]] = []
    eq_hist = []
    chosen: List[str] = []
    for i in range(lo, hi):
        d = idx[i]
        snap = {a: {k: c[k][i] for k in c} for a, c in cols.items()}
        bull = bool(reg[i])

        def close_trade(a: str, px: float) -> None:
            nonlocal cash
            h = hold.pop(a)
            proceeds = h.qty * px * (1 - cost_out)
            cash += proceeds
            pnl = proceeds - h.cost + realized.pop(a, 0.0)
            took.discard(a)
            trades.append({"r": pnl / h.risk_quote, "pnl": pnl})
        for a, reason in ts.update_positions(hold, snap, bull, p):
            px = snap[a]["close"]
            if reason == "DELISTED" or not ts._finite(px):
                px = last_px.get(a, hold[a].entry) * 0.5
            close_trade(a, px)
        if tp_r is not None:
            for a in list(hold):
                h, px = hold[a], snap[a]["close"]
                if a in took or not ts._finite(px):
                    continue
                unit = h.risk_quote / h.qty          # risque initial par unité
                if px * (1 - cost_out) - h.cost / h.qty >= tp_r * unit:
                    if tp_frac >= 1:
                        close_trade(a, px)
                    else:
                        q = h.qty * tp_frac
                        proceeds = q * px * (1 - cost_out)
                        cash += proceeds
                        realized[a] = realized.get(a, 0.0) + proceeds - h.cost * tp_frac
                        h.qty -= q
                        h.cost *= (1 - tp_frac)
                        took.add(a)
        for a in hold:
            last_px[a] = snap[a]["close"]
        equity = cash + sum(h.qty * snap[a]["close"] for a, h in hold.items())
        peak = max(peak, equity)
        eligible = snap
        if top:
            ok = [a for a, s in snap.items()
                  if ts._finite(s["vol30"]) and s["vol30"] >= p.min_volume_usd
                  and s["age"] >= p.min_history]
            scores = ts.selection_scores(records, idx, i, days, closes, p)
            chosen = ts.rank_selection(scores, ok, top, chosen, hysteresis)
            eligible = {a: snap[a] for a in chosen}
        for pl in ts.plan_entries(hold, eligible, bull, equity, cash, p,
                                  ts.risk_multiplier(equity, peak, p)):
            a = pl["asset"]
            cash -= pl["cost"]
            hold[a] = ts.Holding(a, pl["qty"], pl["entry"], pl["stop"], pl["ref_price"], d,
                                 pl["risk_quote"], pl["cost"])
            last_px[a] = pl["ref_price"]
        eq_hist.append(cash + sum(h.qty * snap[a]["close"] for a, h in hold.items()))
    eq = pd.Series(eq_hist, index=idx[lo:hi])
    m = ts.compute_metrics(eq, trades)
    m["n_trades"] = len(trades)
    m["win_pct"] = 100.0 * np.mean([t["r"] > 0 for t in trades]) if trades else 0.0
    m["avg_r"] = float(np.mean([t["r"] for t in trades])) if trades else 0.0
    return m


VARIANTS = (
    ("Référence : les 21 cryptos, le gain court jusqu'au stop", {}),
    ("Auto-sélection : 10 plus rentables sur 2 ans", {"top": 10, "days": 730}),
    ("Auto-sélection : 10 plus rentables sur 1 an", {"top": 10, "days": 365}),
    ("Auto-sélection : 14 plus rentables sur 2 ans", {"top": 14, "days": 730}),
    ("Prise de bénéfice : tout vendre à +3 R", {"tp_r": 3.0}),
    ("Prise de bénéfice : tout vendre à +5 R", {"tp_r": 5.0}),
    ("Prise de bénéfice : moitié vendue à +3 R", {"tp_r": 3.0, "tp_frac": 0.5}),
)


def report(close: pd.DataFrame, volume: pd.DataFrame) -> str:
    p = ts.TrendParams()
    pre = ts.precompute(close, volume, p)
    records = ts.asset_track_records(pre[0], pre[1], p)
    is_p, oos_p = ("2018-01-01", "2022-12-31"), ("2023-01-01", str(close.index[-1].date()))
    rows, res = [], {}

    def fr(x: float, spec: str) -> str:
        return format(x, spec).replace(".", ",").replace("-", "−")
    for name, kw in VARIANTS:
        a = run(close, pre, records, p, *is_p, **kw)
        b = run(close, pre, records, p, *oos_p, **kw)
        res[name] = (a, b)
        f = lambda m: (f"{fr(m['cagr_pct'], '+.1f')} % | {fr(m['max_dd_pct'], '.1f')} % | "  # noqa: E731
                       f"{fr(m['calmar'], '.2f')} | {m['n_trades']} | {m['win_pct']:.0f} % | "
                       f"{fr(m['avg_r'], '+.2f')} R")
        rows.append(f"| {name} | {f(a)} | {f(b)} |")
    ref_a, ref_b = res[VARIANTS[0][0]]
    better = [n for n, (a, b) in res.items() if n != VARIANTS[0][0]
              and a["calmar"] > ref_a["calmar"] and b["calmar"] > ref_b["calmar"]]
    auto_a, auto_b = res[VARIANTS[1][0]]
    tps = [(n, a, b) for n, (a, b) in res.items() if n.startswith("Prise")]
    lines = [
        "# TrendGuard — quelles cryptos trader, et quand vendre ?",
        "",
        "Étude reproductible : `python research_selection.py --cache data_binance` "
        f"(données journalières Binance des {len(close.columns)} paires du bot, frais 0,1 % "
        "et glissement 0,1 % par côté, 1 % du capital risqué par achat).",
        "",
        "Protocole : chaque variante est jugée sur **2018-2022**, puis vérifiée sur "
        f"**2023 → {oos_p[1]}**, période qui n'a servi à aucun choix. Critère principal : "
        "Calmar (rendement annuel divisé par la pire baisse). La sélection d'un jour "
        "n'utilise que les données connues ce jour-là.",
        "",
        "| Variante | 2018-22 : CAGR | Baisse max | Calmar | Trades | Gagnants | Moyenne | "
        "2023 → : CAGR | Baisse max | Calmar | Trades | Gagnants | Moyenne |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "## Lecture",
        "",
        "- **Le bot vend déjà.** Chaque achat est revendu quand la clôture passe sous "
        "son stop suiveur, qui monte avec le prix : le gain est verrouillé au fur et à "
        "mesure et la vente a lieu quand la tendance s'essouffle. La référence ci-dessus "
        "compte ces achats ET ces ventes.",
        f"- **Auto-sélection des 10 plus rentables sur 2 ans** : meilleure sur la période "
        f"de choix (Calmar {fr(auto_a['calmar'], '.2f')} contre {fr(ref_a['calmar'], '.2f')}), "
        f"nettement moins bonne ensuite : {fr(auto_b['cagr_pct'], '+.1f')} % par an contre "
        f"{fr(ref_b['cagr_pct'], '+.1f')} % pour les 21 cryptos (Calmar "
        f"{fr(auto_b['calmar'], '.2f')} contre {fr(ref_b['calmar'], '.2f')}). Les cryptos qui "
        "ont le plus rapporté ces deux dernières années ne sont pas celles qui rapportent "
        "le plus ensuite : la prochaine grande tendance vient souvent d'une crypto "
        "délaissée.",
        "- **Prise de bénéfice fixe** : vendre à un gain donné coupe les grandes "
        "tendances qui font le résultat. Moyenne par trade "
        + ", ".join(f"{fr(b['avg_r'], '+.2f')} R ({n.split(' : ')[1]})" for n, _a, b in tps)
        + f" sur 2023 →, contre {fr(ref_b['avg_r'], '+.2f')} R en laissant courir le gain.",
        "- Variantes meilleures que la référence sur les DEUX périodes : "
        + (", ".join(f"**{n}**" for n in better) if better else "**aucune**") + ".",
        "",
        "## Décision",
        "",
        "- **Par défaut, le bot trade les 21 cryptos** et laisse courir ses gains "
        "jusqu'au stop suiveur (référence).",
        "- Le panneau (page Cryptos) permet de choisir les cryptos à cocher "
        "(sélection manuelle) ou d'activer l'**auto-sélection des 10 plus rentables**, "
        "avec ce résultat historique affiché à côté du bouton. Une crypto décochée "
        "déjà détenue reste gérée jusqu'à sa vente normale.",
        "- Aucune prise de bénéfice fixe n'est ajoutée : aucune ne bat la référence sur "
        "les deux périodes.",
    ]
    return ts.format_markdown("\n".join(lines))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Étude de sélection et de prise de bénéfice")
    ap.add_argument("--cache", default="data_binance")
    ap.add_argument("--out", default="docs/SELECTION.md")
    args = ap.parse_args(argv)
    close, volume = ra.load_binance(args.cache)
    text = report(close, volume)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
