"""Rejeu paper sur historique réel.

Partie du bot TrendGuard (paquet trendguard, point d'entrée : trendguard_bot.py).
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import ccxt
import pandas as pd

import v29

from . import trend_strategy as ts
from .bot import TrendGuardBot
from .config import DAY_MS, GuardConfig
from .journal import silent_logger
from .texte import fr


class HistoricalExchange:
    """Exchange de rejeu pour le mode paper : sert des bougies journalières
    reconstruites à partir des clôtures historiques, en ne révélant JAMAIS
    une bougie non clôturée à l'instant simulé."""

    ORDER_TYPES = ["LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS",
                   "STOP_LOSS_LIMIT"]

    def __init__(self, close: pd.DataFrame, volume: pd.DataFrame,
                 quote: str = "USDT"):
        self.close = close
        self.volume = volume
        self.quote = quote
        self.symbols = {f"{a.upper()}/{quote}": a for a in close.columns}
        self.markets: Dict[str, Any] = {}
        self.now_ms = 0
        # Conversion indépendante de la résolution de l'index (ns/us/s) :
        # une erreur d'unité révélerait des prix futurs.
        self._ms = close.index.as_unit("ms").asi8.astype("int64")

    def set_now(self, now: datetime) -> None:
        self.now_ms = int(now.timestamp() * 1000)

    def load_markets(self):
        self.markets = {s: self.market(s) for s in self.symbols}
        return self.markets

    def market(self, symbol: str) -> Dict[str, Any]:
        return {"id": symbol.replace("/", ""), "symbol": symbol, "spot": True,
                "active": True,
                "limits": {"amount": {"min": 0.0}, "cost": {"min": 5.0}},
                "info": {"filters": [], "orderTypes": self.ORDER_TYPES,
                         "ocoAllowed": True}}

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        return f"{math.floor(float(amount) * 1e8) / 1e8:.8f}"

    def price_to_precision(self, symbol: str, price: float) -> str:
        return f"{float(price):.10g}"

    def _visible(self, symbol: str) -> pd.Series:
        a = self.symbols[symbol]
        mask = self._ms + DAY_MS <= self.now_ms
        return self.close[a][mask].dropna()

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d",
                    since: Optional[int] = None, limit: int = 1000):
        c = self._visible(symbol)
        v = self.volume[self.symbols[symbol]].reindex(c.index) \
            if self.symbols[symbol] in self.volume else None
        rows = []
        for d, px in c.iloc[-limit:].items():
            vol_usd = float(v.loc[d]) if v is not None and pd.notna(v.loc[d]) \
                else float("nan")
            rows.append([int(d.timestamp() * 1000), px, px, px, px,
                         vol_usd / px if px > 0 else 0.0])
        return rows

    def fetch_ticker(self, symbol: str) -> Dict[str, float]:
        c = self._visible(symbol)
        if c.empty:
            raise ccxt.BadSymbol(f"pas de cotation pour {symbol}")
        px = float(c.iloc[-1])
        return {"last": px, "bid": px, "ask": px}


def simulated_bot(close: pd.DataFrame, volume: pd.DataFrame, first_day: pd.Timestamp,
                  capital: float, name: str, **overrides: Any
                  ) -> Tuple[TrendGuardBot, HistoricalExchange]:
    """Le VRAI bot en mode paper sur un marché historique (aucune bougie
    future visible), sans journal, fichier ni notification, démarré à la
    décision du premier jour. `overrides` : réglages propres à la
    simulation (paramètres de stratégie, par exemple)."""
    hx = HistoricalExchange(close, volume)
    g = GuardConfig(run_mode="paper", universe=tuple(a.upper() for a in close.columns),
                    paper_capital=capital, db_file=":memory:", log_file=os.devnull,
                    lock_file=os.devnull, auto_diagnose_days=0, heartbeat_min=0,
                    **overrides)
    lg = silent_logger(name)
    bot = TrendGuardBot(g, lg, hx, v29.Store(":memory:", lg), v29.Notifier("", "", logger=lg))
    bot.sleep = lambda s: None
    hx.set_now(first_day.to_pydatetime() + timedelta(days=1, minutes=5))
    if not bot.boot():
        raise RuntimeError("Démarrage du bot impossible")
    return bot, hx


def replay(data_dir: str, start: str, end: Optional[str] = None,
           capital: float = 10_000.0, verbose: bool = True,
           out=None) -> Dict[str, Any]:
    """Fait tourner le VRAI bot en mode paper, jour après jour, sur les
    clôtures historiques réelles (Coin Metrics)."""
    out = out or sys.stdout
    close, volume = ts.load_coinmetrics(data_dir, ts.DEFAULT_UNIVERSE)
    days = close.loc[start:end].index if end else close.loc[start:].index
    days = days[close["btc"].reindex(days).notna().values]
    if len(days) == 0:
        raise ValueError("Aucune donnée sur la période demandée.")
    bot, hx = simulated_bot(close, volume, days[0], capital, "trendguard.replay")
    curve = []
    last_month = None
    for d in days:
        now = d.to_pydatetime() + timedelta(days=1, minutes=5)
        hx.set_now(now)
        book = bot.state["paper"]
        before_h = set(book["holdings"])
        n_tr = len(bot.state["trades"])
        bot.run_cycle(now=now)
        px = close.loc[d]
        for t in bot.state["trades"][n_tr:]:
            if verbose:
                print(f"{d.date()}  ↘ VENTE  {t['asset'].upper():<5} "
                      f"{t['reason']:<13} PnL {fr(t['pnl'], '+9.2f')} USDT "
                      f"({fr(t['r'], '+.2f')} R)", file=out)
        for a in set(book["holdings"]) - before_h:
            h = book["holdings"][a]
            if verbose:
                print(f"{d.date()}  ↗ ACHAT  {a.upper():<5} @ {fr(h['entry'], '.6g')} "
                      f"stop {fr(h['stop'], '.6g')}  risque {fr(h['risk_quote'], '.2f')} USDT",
                      file=out)
        eq = book["cash"] + sum(h["qty"] * float(px[a])
                                for a, h in book["holdings"].items())
        curve.append(eq)
        month = d.strftime("%Y-%m")
        if verbose and month != last_month and last_month is not None:
            print(f"── {last_month} clôturé : equity {fr(curve[-2], ',.2f')} USDT", file=out)
        last_month = month
    equity = pd.Series(curve, index=days)
    metrics = ts.compute_metrics(equity, bot.state["trades"])
    return {"equity": equity, "trades": bot.state["trades"],
            "holdings": bot.state["paper"]["holdings"], "metrics": metrics,
            "regime_bull": bot.state.get("last_regime_bull"),
            "last_day": str(days[-1].date()), "prices": close.loc[days[-1]]}
