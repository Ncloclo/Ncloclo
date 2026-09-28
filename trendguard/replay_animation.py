#!/usr/bin/env python3
"""
Animation du rejeu TrendGuard — page HTML autonome, lecture seule.

Le VRAI bot (trendguard_bot.py, mode paper) est rejoué jour après jour sur
les clôtures journalières réelles de Binance : mêmes décisions, mêmes stops,
mêmes tailles qu'en fonctionnement. Seuls les ordres sont simulés (capital
fictif). La page montre le marché, le régime BTC, les achats, les ventes,
les stops, le capital et le portefeuille paper actuel du bot.

  python trendguard_bot.py animation                      # 2025-01-01 → dernière clôture
  python trendguard_bot.py animation --start 2024-06-01 --out rejeu.html --no-open

Aucune clé API, aucun ordre : uniquement des données publiques Binance.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import webbrowser
from datetime import timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from . import diagnostics as dg
from . import trend_strategy as ts
from .bot import TrendGuardBot
from .config import GuardConfig, load_guard_config_from_env
from .replay import HistoricalExchange
import v29

TEMPLATE = os.path.join(v29.APP_DIR, "templates", "rejeu_trendguard.html")
DEFAULT_OUT = os.path.join(v29.APP_DIR, "rejeu_trendguard.html")
DEFAULT_PAPER_DB = os.path.join(v29.APP_DIR, "trendguard_paper.db")
# Gabarit en trois fichiers (structure, style, script), intégrés dans une
# seule page à la génération : la page s'ouvre sans serveur ni fichier annexe.
PLACEHOLDER = "/*__DATA__*/"
CSS_TAG = '<link rel="stylesheet" href="rejeu_trendguard.css">'
JS_TAG = '<script src="rejeu_trendguard.js"></script>'
# Historique chargé avant le début du rejeu : indicateurs (moyenne 150 j,
# momentum 90 j) et ancienneté minimale (250 j) déjà valides au jour 1.
WARMUP_DAYS = 800
# Données publiques sans restriction géographique : client partagé de v29.
PUBLIC_DATA_API = v29.PUBLIC_DATA_API
PublicKlines = v29.PublicKlines
public_client = v29.make_public_binance


def _sig(x: Any, n: int = 6) -> Optional[float]:
    """Arrondi à n chiffres significatifs (page plus légère), None si absent."""
    return None if x is None or pd.isna(x) else float(f"{float(x):.{n}g}")


def fetch_binance(bases: List[str], start: str, exchange: Any = None,
                  now=None) -> tuple:
    """Clôtures et volumes journaliers Binance (bougies clôturées), avec
    l'historique de chauffe nécessaire aux indicateurs."""
    exchange = exchange or PublicKlines(public_client())
    if now is None:
        v29.sync_exchange_clock(exchange, samples=3)       # heure de Binance
        now = v29._utcnow()
    since = (pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS)).date().isoformat()
    close, volume, errors = dg.fetch_daily_history(exchange, bases, since=since, now=now)
    for a, err in errors.items():
        print(f"  ⚠️  {a.upper()} ignorée : {err}")
    close = close.dropna(how="all")
    return close, volume.reindex(close.index)


def build_replay(close: pd.DataFrame, volume: pd.DataFrame, start: str,
                 capital: float = 10_000.0,
                 params: Optional[ts.TrendParams] = None,
                 end: Optional[str] = None) -> Dict[str, Any]:
    """Rejoue le bot paper jour après jour (HistoricalExchange : aucune
    bougie future visible) et enregistre, pour chaque jour, le capital, le
    régime, les ordres, les signaux et le stop de chaque position."""
    p = params or ts.TrendParams()
    days = close.loc[start:end].index if end else close.loc[start:].index
    days = days[close["btc"].reindex(days).notna().values]
    if len(days) == 0:
        raise ValueError("Aucune clôture BTC sur la période demandée.")
    hx = HistoricalExchange(close, volume)
    g = GuardConfig(run_mode="paper", universe=tuple(a.upper() for a in close.columns),
                       params=p, paper_capital=capital, db_file=":memory:",
                       log_file=os.devnull, lock_file=os.devnull,
                       auto_diagnose_days=0, heartbeat_min=0)
    lg = logging.getLogger("trendguard.animation")
    lg.handlers[:] = [logging.NullHandler()]
    lg.propagate = False
    store = v29.Store(":memory:", lg)
    bot = TrendGuardBot(g, lg, hx, store, v29.Notifier("", "", logger=lg))
    bot.sleep = lambda s: None
    hx.set_now(days[0].to_pydatetime() + timedelta(days=1, minutes=5))
    if not bot.boot():
        raise RuntimeError("Démarrage du bot impossible")

    # Signaux affichés (achats possibles non exécutés) : mêmes fonctions que
    # le bot, calculées une fois sur tout l'historique (indicateurs causaux).
    feats = {}
    for a in close.columns:
        c = close[a].dropna()
        feats[a] = ts.asset_features(c, p, volume[a].reindex(c.index)).reindex(close.index)
    sma = close["btc"].rolling(p.regime_sma, min_periods=p.regime_sma).mean()
    cols = ("close", "vol", "prior_high", "mom", "age", "vol30")

    equity: List[float] = []
    bull: List[int] = []
    events: List[Dict[str, Any]] = []
    episodes: Dict[tuple, Dict[str, Any]] = {}
    closed: List[Dict[str, Any]] = []
    try:
        for k, d in enumerate(days):
            now = d.to_pydatetime() + timedelta(days=1, minutes=5)
            hx.set_now(now)
            book = bot.state["paper"]
            n_trades = len(bot.state["trades"])
            before = set(book["holdings"])
            bot.run_cycle(now=now)
            exits, entries = [], []
            for t in bot.state["trades"][n_trades:]:
                ep = episodes.pop((t["asset"], t["entry_date"]))
                ep.update(d1=k, exit=_sig(t["exit"]), reason=t["reason"],
                          pnl=round(t["pnl"], 2), r=round(t["r"], 2))
                closed.append(ep)
                exits.append({"a": t["asset"], "reason": t["reason"],
                              "pnl": round(t["pnl"], 2), "r": round(t["r"], 2)})
            for a in sorted(set(book["holdings"]) - before):
                h = book["holdings"][a]
                episodes[(a, h["entry_date"])] = {
                    "a": a, "d0": k, "d1": None, "entry": _sig(h["entry"]),
                    "qty": _sig(h["qty"]), "risk": round(h["risk_quote"], 2),
                    "cost": round(h["cost"], 2), "stops": [], "dis": []}
                entries.append({"a": a, "entry": _sig(h["entry"]), "stop": _sig(h["stop"]),
                                "risk": round(h["risk_quote"], 2),
                                "cost": round(h["cost"], 2)})
            for a, h in book["holdings"].items():
                ep = episodes[(a, h["entry_date"])]
                ep["stops"].append(_sig(h["stop"]))
                ep["dis"].append(_sig(h["disaster"]))
            taken = set(book["holdings"]) | {e["a"] for e in entries}
            signals = [a for a in close.columns if a not in taken and ts.entry_signal(
                {c: float(feats[a][c].loc[d]) for c in cols}, p)]
            px = close.loc[d]
            equity.append(round(book["cash"] + sum(h["qty"] * float(px[a])
                                                   for a, h in book["holdings"].items()), 2))
            bull.append(1 if bot.state.get("last_regime_bull") else 0)
            events.append({"x": exits, "e": entries, "s": signals,
                           "risk": round(sum(h["risk_quote"]
                                             for h in book["holdings"].values()), 2)})
    finally:
        store.close()

    metrics = ts.compute_metrics(pd.Series(equity, index=days), bot.state["trades"])
    last = close.index.get_loc(days[-1])
    return {
        "start": str(days[0].date()), "end": str(days[-1].date()),
        "capital": capital,
        "dates": [str(d.date()) for d in days],
        "assets": list(close.columns),
        "close": {a: [_sig(v) for v in close[a].reindex(days)] for a in close.columns},
        "high30": {a: [_sig(v) for v in feats[a]["prior_high"].reindex(days)]
                   for a in close.columns},
        "liquid": {a: bool(feats[a]["vol30"].iloc[last] >= p.min_volume_usd)
                   for a in close.columns},
        "sma150": [_sig(v) for v in sma.reindex(days)],
        "bull": bull, "equity": equity, "events": events,
        "episodes": closed + list(episodes.values()),
        "metrics": {k: (round(v, 3) if isinstance(v, float) else v)
                    for k, v in metrics.items()},
        "params": {"breakout_n": p.breakout_n, "init_stop_atr": p.init_stop_atr,
                   "trail_atr": p.trail_atr, "bear_trail_atr": p.bear_trail_atr,
                   "regime_sma": p.regime_sma, "risk_pct": p.risk_pct,
                   "max_positions": p.max_positions,
                   "max_total_risk": p.max_total_risk,
                   "min_volume_usd": p.min_volume_usd},
    }


def read_paper_portfolio(db_file: str, close: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Portefeuille paper actuel du bot, lu en LECTURE SEULE dans sa base
    (le bot peut tourner en même temps). None si la base est absente."""
    if not db_file or not os.path.exists(db_file):
        return None
    con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT value FROM kv WHERE key=?",
                          (TrendGuardBot.STATE_KEY,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if not row:
        return None
    st = json.loads(row[0])
    if "paper" not in st:
        return None
    holdings = []
    for a, h in sorted(st["paper"].get("holdings", {}).items()):
        last = close[a].dropna() if a in close else pd.Series(dtype=float)
        holdings.append({"a": a, "entry": _sig(h["entry"]), "stop": _sig(h["stop"]),
                         "dis": _sig(h.get("disaster")), "qty": _sig(h["qty"]),
                         "risk": round(h["risk_quote"], 2),
                         "date": str(h["entry_date"])[:10],
                         "last": _sig(last.iloc[-1]) if len(last) else None})
    return {"last_decision_day": st.get("last_decision_day"),
            "last_equity": st.get("last_equity"),
            "cash": st["paper"].get("cash"), "holdings": holdings}


def render_html(data: Dict[str, Any], template: str = TEMPLATE) -> str:
    """Page autonome : gabarit, style et script intégrés, données du rejeu
    dans un bloc JSON (aucun fichier externe hormis les polices Google,
    facultatives)."""
    def read(name: str) -> str:
        with open(os.path.join(os.path.dirname(template), name), encoding="utf-8") as fh:
            return fh.read()
    page = read(os.path.basename(template))
    css, js = read("rejeu_trendguard.css"), read("rejeu_trendguard.js")
    for marker in (PLACEHOLDER, CSS_TAG, JS_TAG):
        if page.count(marker) != 1:
            raise ValueError(f"{template} : repère {marker!r} introuvable ou en double")
    if "</style" in css.lower() or "</script" in js.lower():
        raise ValueError("balise fermante dans le style ou le script du gabarit")
    # « < » échappé : aucun texte des données ne peut fermer le bloc JSON ni
    # ouvrir une balise (le JSON reste valide, < = « < »).
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return (page.replace(CSS_TAG, f"<style>\n{css}</style>")
                .replace(JS_TAG, f"<script>\n{js}</script>")
                .replace(PLACEHOLDER, payload))


def main(argv: Optional[List[str]] = None) -> int:
    v29.ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Animation du rejeu TrendGuard (HTML)")
    ap.add_argument("--start", default="2025-01-01", help="premier jour rejoué")
    ap.add_argument("--end", default=None, help="dernier jour (défaut : dernière clôture)")
    ap.add_argument("--capital", type=float, default=10_000.0, help="capital fictif (USDT)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="page HTML produite")
    ap.add_argument("--db", default=DEFAULT_PAPER_DB,
                    help="base du bot paper (portefeuille actuel)")
    ap.add_argument("--no-open", action="store_true", help="ne pas ouvrir le navigateur")
    args = ap.parse_args(argv)
    try:
        g = load_guard_config_from_env()          # univers et paramètres du bot
    except ValueError as e:
        print(f"Configuration invalide : {e}", file=sys.stderr)
        return 2
    print(f"Historique Binance de {len(g.universe)} paires (5 à 15 min selon la connexion)…", flush=True)
    close, volume = fetch_binance(list(g.universe), args.start)
    if "btc" not in close:
        print("❌ Historique BTC indisponible : vérifier la connexion à Binance.",
              file=sys.stderr)
        return 1
    print(f"Rejeu du bot du {args.start} à la dernière clôture…", flush=True)
    data = build_replay(close, volume, args.start, args.capital, g.params, args.end)
    data["generated"] = v29._utcnow_iso()[:16].replace("T", " ") + " UTC"
    data["paper_live"] = read_paper_portfolio(args.db, close)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render_html(data))
    m = data["metrics"]
    btc = data["close"]["btc"]
    print(f"✅ {args.out}\n   {data['start']} → {data['end']} : bot "
          f"{m['total_return_pct']:+.1f} % (pire baisse {m['max_dd_pct']:.1f} %), "
          f"BTC conservé {(btc[-1] / btc[0] - 1) * 100:+.1f} %, {m['trades']} trades, "
          f"{m['win_rate_pct']:.0f} % gagnants")
    if not args.no_open:
        webbrowser.open("file:///" + os.path.abspath(args.out).replace(os.sep, "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
