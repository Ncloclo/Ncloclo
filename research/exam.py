#!/usr/bin/env python3
"""
Examen de TrendGuard : le bot est-il intelligent et rusé ? — reproductible
(docs/EXAMEN.md).

1. Intelligence : chaque règle du bot est retirée à tour de rôle sur
   l'historique Binance (2018-2022 puis 2023 → aujourd'hui). Une règle utile
   fait mieux avec elle que sans elle. Le bot est aussi comparé à des achats
   au hasard (mêmes stops, même risque) et à l'achat conservé.
2. Ruse : pièges joués contre le vrai code du bot sur un Binance simulé
   (tests automatiques) : carnet d'ordres anormal, panne pendant la
   décision, krach entre deux clôtures, PC éteint, horloge fausse, retrait
   d'une crypto, rumeur, ordres ambigus, second bot sur le même compte…
3. Carnets d'ordres réels de Binance au moment de l'examen : la ruse
   d'achat laisserait-elle acheter maintenant ?

Diagnostic : aucune règle n'est changée d'après cet examen.

  python -m research.exam --cache data_binance --out docs/EXAMEN.md
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import v29
from research import adaptation as ra
from research.robustness import cell, fr, periods
from trendguard import trend_strategy as ts
from trendguard.bot import TrendGuardBot
from trendguard.config import GuardConfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDS = 10
Pair = Tuple[Dict[str, float], Dict[str, float]]

# ══════════════════════════════════════════════════════════════════════
# 1. Intelligence : chaque règle retirée à tour de rôle
# ══════════════════════════════════════════════════════════════════════


Periods = Tuple[Tuple[str, str], Tuple[str, str]]


def run(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, pre,
        hooks: Optional[ts.BacktestHooks] = None, pers: Optional[Periods] = None) -> Pair:
    return tuple(ts.backtest(close, volume, p, *per, pre=pre, hooks=hooks).metrics
                 for per in pers or periods(close))


def ablations(p: ts.TrendParams) -> List[Tuple[str, str, ts.TrendParams, bool]]:
    """(variante, règle retirée, réglages, lecture du marché conservée)."""
    return [
        ("Sans lecture du marché", "n'acheter que si BTC est au-dessus de sa moyenne 150 jours",
         p, False),
        ("Sans stops resserrés en marché baissier", "resserrer les stops quand le marché baisse",
         dataclasses.replace(p, bear_trail_atr=0.0), True),
        ("Sans stop suiveur (vente au seul stop initial)", "le stop suiveur qui monte avec le prix",
         dataclasses.replace(p, trail_atr=1e6, bear_trail_atr=0.0), True),
        ("Sans filtres de liquidité et d'ancienneté", "éviter les cryptos peu échangées ou trop récentes",
         dataclasses.replace(p, min_volume_usd=0.0, min_history=30), True),
        ("Sans plafonds (8 positions, 6 % de risque cumulé)", "limiter le nombre de positions et le risque total",
         dataclasses.replace(p, max_positions=21, max_total_risk=0.21), True),
    ]


def _positive(x: Any) -> bool:
    return x is not None and np.isfinite(x) and x > 0


def shuffled_momentum(seed: int) -> ts.BacktestHooks:
    """Mêmes signaux, mais l'élan (momentum) remplacé par un tirage au hasard :
    quand la place manque, le bot ne choisit plus les plus fortes tendances."""
    rng = np.random.default_rng(seed)

    def choose(_i: int, snap: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        return {a: dict(s, mom=float(rng.random()) + 1e-6) if _positive(s.get("mom")) else s
                for a, s in snap.items()}
    return ts.BacktestHooks(choose=choose)


def random_entries(seed: int, q: float) -> ts.BacktestHooks:
    """Achats au hasard (probabilité q par crypto et par jour) à la place des
    cassures ; stops, risque, plafonds et lecture du marché inchangés."""
    rng = np.random.default_rng(seed)

    def choose(_i: int, snap: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        out = {}
        for a, s in snap.items():
            pick = rng.random() < q
            out[a] = dict(s, prior_high=0.0 if pick else np.inf,
                          mom=float(rng.random()) + 1e-6 if pick else -1.0)
        return out
    return ts.BacktestHooks(choose=choose)


def signal_rate(pre, p: ts.TrendParams) -> float:
    """Part des jours (crypto achetable, marché haussier) où le bot voit une
    cassure : même fréquence pour les achats au hasard."""
    cols, reg = pre
    hits = seen = 0
    for c in cols.values():
        with np.errstate(invalid="ignore"):
            ok = (reg & np.isfinite(c["close"]) & np.isfinite(c["vol"]) & (c["age"] >= p.min_history)
                  & np.isfinite(c["vol30"]) & (c["vol30"] >= p.min_volume_usd))
            sig = ok & (c["close"] > c["prior_high"]) & (c["mom"] > 0)
        hits += int(sig.sum())
        seen += int(ok.sum())
    return hits / max(seen, 1)


def median_pair(pairs: List[Pair]) -> Pair:
    keys = ("cagr_pct", "max_dd_pct", "sharpe", "calmar")
    return tuple({k: float(np.median([pr[i][k] for pr in pairs])) for k in keys} for i in (0, 1))


def verdict(ref: Pair, var: Pair) -> Tuple[Tuple[bool, bool], str]:
    """Périodes où la règle améliore le rapport rendement / pire baisse
    (Calmar), et verdict en clair."""
    helps = tuple(ref[i]["calmar"] > var[i]["calmar"] + 0.02 for i in (0, 1))
    if all(helps):
        return helps, "✓ utile sur les deux périodes"
    if helps[0]:
        return helps, "± utile en 2018-2022, pas depuis 2023"
    if helps[1]:
        return helps, "± utile depuis 2023, pas en 2018-2022"
    return helps, "✗ n'améliore pas ce rapport ici"


def intelligence(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams,
                 pers: Optional[Periods] = None) -> Dict[str, Any]:
    pers = pers or periods(close)
    pre = ts.precompute(close, volume, p)
    ref = run(close, volume, p, pre, pers=pers)
    rows = []
    for name, rule, q, regime in ablations(p):
        use = pre if regime else (pre[0], np.ones_like(pre[1], dtype=bool))
        var = run(close, volume, q, use, pers=pers)
        rows.append((name, rule, var, *verdict(ref, var)))
    shuffled = [run(close, volume, p, pre, shuffled_momentum(s), pers) for s in range(SEEDS)]
    med = median_pair(shuffled)
    rows.append((f"Élan ignoré : cryptos choisies au hasard (médiane de {SEEDS} tirages)",
                 "acheter en priorité les plus fortes tendances", med, *verdict(ref, med)))
    rate = signal_rate(pre, p)
    rand = [run(close, volume, p, pre, random_entries(s, rate), pers) for s in range(SEEDS)]
    beaten = sum(1 for r in rand if all(ref[i]["calmar"] > r[i]["calmar"] for i in (0, 1)))
    bh = {}
    for label, assets in (("BTC seul", ["btc"]), (f"les {len(close.columns)} cryptos à parts égales",
                                                  list(close.columns))):
        bh[label] = tuple(ts.buy_and_hold(close, assets, *per) for per in pers)
    return {"ref": ref, "rows": rows, "random": median_pair(rand), "beaten": beaten,
            "rate": rate, "bh": bh}


# ══════════════════════════════════════════════════════════════════════
# 2. Ruse : pièges joués contre le vrai code du bot (Binance simulé)
# ══════════════════════════════════════════════════════════════════════

TRAPS: List[Tuple[str, str, List[str]]] = [
    ("Carnet d'ordres anormal au moment d'acheter (écart trop grand, carnet trop mince)",
     "achat différé, nouvel essai toutes les 5 min, abandon après 6 h",
     ["test_intelligence.py::test_abnormal_order_book_defers_the_entry_then_buys",
      "test_intelligence.py::test_thin_book_and_expiry_abandon_the_entry"]),
    ("Prix qui a filé depuis la clôture, ou retombé près du stop",
     "quantité recalculée : jamais plus de risque que prévu",
     ["test_trendguard.py::test_reprice_entry_never_risks_more_than_planned",
      "test_trendguard.py::test_late_paper_decision_buys_at_current_price"]),
    ("Panne de cours ou d'Internet pendant la décision",
     "décision reportée ; jamais de vente sur une simple panne",
     ["test_intelligence.py::test_price_outage_defers_instead_of_losing_the_entry",
      "test_real_conditions.py::test_held_asset_without_data_is_never_sold",
      "test_real_conditions.py::test_network_outage_defers_quickly_without_waiting_every_pair"]),
    ("Krach entre deux clôtures",
     "stop catastrophe : vente sans attendre la clôture",
     ["test_trendguard.py::test_live_catastrophe_stop_between_closes",
      "test_trendguard.py::test_paper_crash_exits_at_raised_catastrophe_stop"]),
    ("PC éteint pendant une ou plusieurs clôtures",
     "stops et décisions manqués rattrapés au redémarrage ; arrêt signalé",
     ["test_trendguard.py::test_stops_hit_while_bot_stopped_are_caught_up",
      "test_trendguard.py::test_live_catch_up_closes_position_and_its_exchange_stop",
      "test_uptime.py::test_bot_notes_an_unrequested_stop_and_warns"]),
    ("Marché qui se retourne à la baisse",
     "plus aucun achat, stops resserrés",
     ["test_trendguard.py::test_no_entry_in_bear_regime_and_caps",
      "test_trendguard.py::test_trailing_stop_only_rises_and_tightens_in_bear"]),
    ("Binance annonce le retrait d'une crypto",
     "achats bloqués ; les ventes restent décidées par les stops",
     ["test_market_watch.py::test_official_vetoes_and_memory",
      "test_market_watch.py::test_veto_blocks_new_buys_but_never_sells"]),
    ("Rumeur relayée par une seule IA",
     "ignorée : il faut deux IA d'accord, et une IA ne bloque jamais seule",
     ["test_market_watch.py::test_alert_needs_two_ais_and_ai_never_vetoes"]),
    ("Tentation de regarder l'avenir (bougie du jour pas encore close)",
     "décision sur les seules bougies closes",
     ["test_trendguard.py::test_bot_ignores_future_candles",
      "test_trendguard.py::test_features_are_causal",
      "test_selection.py::test_scores_never_look_into_the_future"]),
    ("Horloge du PC fausse",
     "le bot se cale sur l'heure de Binance",
     ["test_real_conditions.py::test_bot_decides_on_binance_time_not_pc_time",
      "test_real_conditions.py::test_order_retried_after_clock_resync"]),
    ("Réponse de Binance perdue pendant un ordre",
     "ordre retrouvé : jamais d'achat ni de vente en double",
     ["test_live_execution.py::test_ambiguous_buy_is_resolved_and_adopted",
      "test_live_execution.py::test_ambiguous_sell_is_not_double_counted",
      "test_live_execution.py::test_stop_fills_between_snapshot_and_cancel_no_double_sell"]),
    ("Stop annulé hors du bot, ou lecture impossible du compte",
     "stop reposé ; aucune protection retirée sur une panne de lecture",
     ["test_live_execution.py::test_protection_cancelled_outside_bot_is_replaced",
      "test_live_execution.py::test_read_outage_never_cancels_protection"]),
    ("Second bot sur le même compte",
     "démarrage refusé, positions de l'autre jamais adoptées",
     ["test_trendguard.py::test_live_boot_refuses_foreign_bot_orders",
      "test_live_execution.py::test_second_instance_does_not_adopt_foreign_position"]),
    ("Chute du capital de 40 %",
     "arrêt d'urgence : plus aucun achat",
     ["test_trendguard.py::test_kill_switch_blocks_entries"]),
]


class _Outcomes:
    """Greffon pytest : résultat de chaque test, par nom."""

    def __init__(self) -> None:
        self.by_name: Dict[str, str] = {}

    def pytest_runtest_logreport(self, report: Any) -> None:
        name = report.nodeid.split("::")[-1]
        if report.when == "call" or report.outcome != "passed":
            if self.by_name.get(name) != "failed":
                self.by_name[name] = report.outcome


def run_traps(runner: Optional[Callable[[List[str]], Dict[str, str]]] = None
              ) -> Optional[List[Tuple[str, str, int, int]]]:
    """(piège, réaction attendue, tests réussis, tests) ; None sans pytest."""
    if runner is None:
        try:
            import pytest
        except ImportError:
            return None

        def runner(ids: List[str]) -> Dict[str, str]:
            out = _Outcomes()
            pytest.main(["-q", "-p", "no:cacheprovider", "--rootdir", ROOT, *ids], plugins=[out])
            return out.by_name
    ids = [os.path.join(ROOT, "tests", t) for _n, _r, tests in TRAPS for t in tests]
    res = runner(ids)
    return [(name, react, sum(1 for t in tests if res.get(t.split("::")[-1]) == "passed"), len(tests))
            for name, react, tests in TRAPS]


# ══════════════════════════════════════════════════════════════════════
# 3. Carnets d'ordres réels : la ruse laisserait-elle acheter ?
# ══════════════════════════════════════════════════════════════════════

def book_check(book: Dict[str, Any], symbol: str, notional: float, max_spread: float
               ) -> Optional[str]:
    """Le vrai contrôle du bot (TrendGuardBot._book_anomaly) sur un carnet donné."""
    bot = SimpleNamespace(g=SimpleNamespace(max_spread=max_spread, quote="USDT"),
                          BOOK_DEPTH_MULT=TrendGuardBot.BOOK_DEPTH_MULT,
                          BOOK_DEPTH_BAND=TrendGuardBot.BOOK_DEPTH_BAND)
    slot = SimpleNamespace(symbol=symbol, ex=SimpleNamespace(
        exchange=SimpleNamespace(fetch_order_book=lambda *_a, **_k: book)))
    return TrendGuardBot._book_anomaly(bot, slot, notional)


def book_stats(book: Dict[str, Any]) -> Tuple[float, float]:
    """(écart achat/vente en %, montant proposé à la vente à moins de 1 %)."""
    bid, ask = float(book["bids"][0][0]), float(book["asks"][0][0])
    depth = sum(float(px) * float(q) for px, q, *_ in book["asks"]
                if float(px) <= ask * (1 + TrendGuardBot.BOOK_DEPTH_BAND))
    return (ask - bid) / ((ask + bid) / 2) * 100, depth


def live_books(assets: List[str], notional: float, exchange: Any = None
               ) -> Tuple[str, List[Dict[str, Any]]]:
    ex = exchange or v29.make_binance()
    max_spread = GuardConfig.__dataclass_fields__["max_spread"].default
    rows = []
    for a in assets:
        sym = f"{a.upper()}/USDT"
        try:
            book = ex.fetch_order_book(sym, limit=100)
            spread, depth = book_stats(book)
        except Exception as e:
            rows.append({"asset": a, "error": type(e).__name__})
            continue
        rows.append({"asset": a, "spread": spread, "depth": depth,
                     "max_buy": depth / TrendGuardBot.BOOK_DEPTH_MULT,
                     "why": book_check(book, sym, notional, max_spread)})
    return datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC"), rows


# ══════════════════════════════════════════════════════════════════════
# Rapport
# ══════════════════════════════════════════════════════════════════════

def _money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ") + " $"


def report(close: pd.DataFrame, volume: pd.DataFrame,
           traps: Optional[List[Tuple[str, str, int, int]]],
           books: Optional[Tuple[str, List[Dict[str, Any]]]], notional: float,
           pers: Optional[Periods] = None) -> str:
    p = ts.TrendParams()
    pers = pers or periods(close)
    it = intelligence(close, volume, p, pers)
    oos_p = pers[1]
    ref, rand = it["ref"], it["random"]
    head = ("| Variante | 2018-22 : CAGR | Baisse max | Sharpe | Calmar | "
            "2023 → : CAGR | Baisse max | Sharpe | Calmar |")
    sep = "|---|---|---|---|---|---|---|---|---|"
    useful = [r for r in it["rows"] if all(r[3])]
    insurance = [r for r in it["rows"] if r[3][0] and not r[3][1]]
    lines = [
        "# TrendGuard — examen : le bot est-il intelligent et rusé ?",
        "",
        "Étude reproductible : `python -m research.exam --cache data_binance`. C'est un "
        "diagnostic : **aucune règle n'est changée d'après cet examen.**",
        "",
        "## 1. Intelligence : chaque règle compte-t-elle ?",
        "",
        f"Chaque règle est retirée à tour de rôle, sur l'historique Binance des "
        f"{len(close.columns)} cryptos du bot : 2018-2022, puis 2023 → {oos_p[1]} (frais et "
        "glissement 0,1 % par côté, 1 % du capital risqué par achat). Une règle est "
        "utile si le Calmar (rendement annuel divisé par la pire baisse) est meilleur "
        "avec elle.",
        "",
        head, sep,
        f"| **Bot complet** | {cell(ref[0])} | {cell(ref[1])} |",
        *[f"| {name} | {cell(v[0])} | {cell(v[1])} |" for name, _rule, v, _w, _t in it["rows"]],
        f"| Achats au hasard, mêmes stops (médiane de {SEEDS} tirages) | {cell(rand[0])} | "
        f"{cell(rand[1])} |",
        *[f"| Achat conservé : {label} | {cell(a)} | {cell(b)} |" for label, (a, b) in it["bh"].items()],
        "",
        "| Règle | 2018-22 | 2023 → | Verdict |",
        "|---|---|---|---|",
        *[f"| {rule[0].upper() + rule[1:]} | {'✓' if h[0] else '✗'} | {'✓' if h[1] else '✗'} | {text} |"
          for _n, rule, _v, h, text in it["rows"]],
        "",
        f"- **{len(useful)} règles sur {len(it['rows'])}** améliorent le rapport rendement / "
        "pire baisse sur les deux périodes"
        + (f" ; {len(insurance)} sont utiles surtout en 2018-2022, période des chutes de 2018 "
           "et 2022, et coûtent un peu ou rien depuis 2023, marché surtout haussier"
           if insurance else "")
        + ".",
        f"- **Achats au hasard** (même fréquence que les cassures, {fr(it['rate'] * 100, '.1f')} % "
        f"des jours, mêmes stops) : le bot fait mieux sur les deux périodes que "
        f"**{it['beaten']} tirages sur {SEEDS}**. "
        + ("Le signal d'achat apporte donc un vrai avantage, pas seulement les stops."
           if it["beaten"] >= SEEDS - 1 else
           "L'avantage du signal d'achat n'est pas net : les stops font une grande partie du "
           "résultat."),
        "- Limites du test : il suppose le même glissement (0,1 %) pour toutes les cryptos, "
        "et les cryptos testées sont celles qui existent encore aujourd'hui sur Binance ; "
        "une crypto récente qui s'est effondrée ou a été retirée n'y figure pas. Le filtre "
        "de liquidité et d'ancienneté, qui protège justement de ces deux risques, ne peut "
        "donc pas montrer son intérêt ici.",
        "- Une règle qui n'aide pas ici n'est pas retirée pour autant : la choisir d'après "
        "ces seuls chiffres serait du sur-ajustement.",
        "",
        "## 2. Ruse : pièges joués contre le vrai code du bot",
        "",
    ]
    if traps is None:
        lines += ["Non exécuté : pytest n'est pas installé (`pip install pytest`).", ""]
    else:
        ok = sum(1 for _n, _r, good, total in traps if good == total)
        lines += [
            "Chaque piège est joué par des tests automatiques sur un Binance simulé, avec "
            "le code exact du bot.",
            "",
            "| Piège | Réaction du bot | Résultat |",
            "|---|---|---|",
            *[f"| {name} | {react} | {'✓ déjoué' if good == total else '✗ ÉCHEC'} "
              f"({good}/{total} tests) |" for name, react, good, total in traps],
            "",
            f"- **{ok} pièges déjoués sur {len(traps)}.**",
            "",
        ]
    lines += ["## 3. Carnets d'ordres réels : la ruse laisserait-elle acheter ?", ""]
    if books is None:
        lines += ["Non exécuté (Binance injoignable).", ""]
    else:
        when, rows = books
        good = [r for r in rows if "error" not in r]
        blocked = [r for r in good if r["why"]]
        lines += [
            f"Relevé du {when}, pour un achat de {_money(notional)} (le plus gros possible "
            "avec 10 000 $ : 25 % du capital). Le bot achète seulement si l'écart "
            "achat/vente reste sous 0,5 % et si le carnet propose au moins 3 fois le "
            "montant à moins de 1 % du meilleur prix.",
            "",
            "| Crypto | Écart achat/vente | Proposé à moins de 1 % | Achat le plus gros accepté | "
            "Achat maintenant |",
            "|---|---|---|---|---|",
            *[(f"| {r['asset'].upper()} | – | – | – | illisible ({r['error']}) |" if "error" in r else
               f"| {r['asset'].upper()} | {fr(r['spread'], '.3f')} % | {_money(r['depth'])} | "
               f"{_money(r['max_buy'])} | {'différé : ' + r['why'] if r['why'] else 'oui'} |")
              for r in rows],
            "",
            f"- {len(good) - len(blocked)} carnets sur {len(good)} laisseraient acheter maintenant"
            + (f" ; achat différé sur {', '.join(r['asset'].upper() for r in blocked)}."
               if blocked else "."),
            f"- Le plus petit « achat le plus gros accepté » est de "
            f"{_money(min(r['max_buy'] for r in good))} "
            f"({min(good, key=lambda r: r['max_buy'])['asset'].upper()}) : au-delà, sur cette "
            "crypto, le bot différerait l'achat pour ne pas payer trop cher." if good else "",
            "",
        ]
    lines += [
        "## Conclusion",
        "",
        f"- **Intelligent** : le bot bat {it['beaten']} achats au hasard sur {SEEDS}. "
        + (f"Indispensables sur les deux périodes : {', '.join(r[1] for r in useful)}. "
           if useful else "")
        + (f"Utiles surtout en 2018-2022 (chutes de 2018 et 2022) : "
           f"{', '.join(r[1] for r in insurance)}." if insurance else ""),
    ]
    if traps is not None:
        ok = sum(1 for _n, _r, good, total in traps if good == total)
        lines.append(f"- **Rusé** : {ok} pièges déjoués sur {len(traps)}.")
    lines += [
        "- **Ce qu'il ne sait pas faire** : prévoir un krach ou la prochaine tendance ; "
        "éviter les séries de trades perdants, normales avec 35 à 50 % de trades "
        "gagnants (`docs/ROBUSTESSE.md`) ; surveiller le marché quand le PC est éteint.",
    ]
    return ts.format_markdown("\n".join(lines))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Examen de TrendGuard : intelligence et ruse")
    ap.add_argument("--cache", default="data_binance")
    ap.add_argument("--out", default="docs/EXAMEN.md")
    ap.add_argument("--notional", type=float, default=2_500.0,
                    help="montant d'achat testé sur les carnets réels (USDT)")
    ap.add_argument("--no-traps", action="store_true", help="sans les pièges (tests)")
    ap.add_argument("--offline", action="store_true", help="sans les carnets réels")
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    close, volume = ra.load_binance(args.cache)
    traps = None if args.no_traps else run_traps()
    books = None
    if not args.offline:
        try:
            books = live_books(list(close.columns), args.notional)
        except Exception as e:
            print(f"Carnets réels indisponibles : {type(e).__name__}")
    text = report(close, volume, traps, books, args.notional)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)
    print(f"\nRapport écrit : {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
