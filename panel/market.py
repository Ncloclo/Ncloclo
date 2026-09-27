"""Cours Binance publics pour le panneau (data-api.binance.vision : sans
clé, sans ordre, accessible partout), avec cache : l'interface interroge
souvent, Binance est appelé au plus une fois par période."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

import v29

INTERVALS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def http_json(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "TrendGuard-panneau"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class Market:
    def __init__(self, fetch: Callable[[str], Any] = http_json, quote: str = "USDT"):
        self.fetch = fetch
        self.quote = quote
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _cached(self, key: str, ttl: float, load: Callable[[], Any]) -> Tuple[Any, bool]:
        """(valeur, périmée). En cas de panne réseau, la dernière valeur
        connue est rendue et signalée périmée."""
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1], False
        try:
            value = load()
        except Exception:
            if hit:
                return hit[1], True
            raise
        with self._lock:
            self._cache[key] = (now, value)
        return value, False

    def symbol(self, base: str) -> str:
        return f"{base.upper()}{self.quote}"

    def tickers(self, bases: List[str]) -> Tuple[Dict[str, Dict[str, float]], bool]:
        symbols = [self.symbol(b) for b in bases]

        def load() -> Dict[str, Dict[str, float]]:
            url = (f"{v29.PUBLIC_DATA_API}/ticker/24hr?symbols="
                   + urllib.parse.quote(json.dumps(symbols, separators=(",", ":"))))
            out = {}
            for t in self.fetch(url):
                base = t["symbol"][:-len(self.quote)].lower()
                out[base] = {"price": float(t["lastPrice"]),
                             "change_pct": float(t["priceChangePercent"]),
                             "high": float(t["highPrice"]), "low": float(t["lowPrice"]),
                             "volume_quote": float(t["quoteVolume"])}
            return out
        return self._cached("tickers:" + ",".join(symbols), 10, load)

    def klines(self, base: str, interval: str = "1h", limit: int = 300
               ) -> Tuple[List[List[float]], bool]:
        if interval not in INTERVALS:
            raise ValueError(f"intervalle inconnu : {interval}")
        limit = max(10, min(1000, int(limit)))

        def load() -> List[List[float]]:
            q = urllib.parse.urlencode({"symbol": self.symbol(base), "interval": interval,
                                        "limit": limit})
            return [[int(r[0]) // 1000] + [float(x) for x in r[1:6]]
                    for r in self.fetch(f"{v29.PUBLIC_DATA_API}/klines?{q}")]
        ttl = 300 if interval == "1d" else 30
        return self._cached(f"klines:{base}:{interval}:{limit}", ttl, load)

    def regime(self, sma: int = 150, show: int = 365) -> Tuple[Dict[str, Any], bool]:
        """BTC journalier et sa moyenne `sma` jours (régime du bot)."""
        rows, stale = self.klines("btc", "1d", sma + show)
        closes = [r[4] for r in rows]
        points = []
        for i, r in enumerate(rows):
            avg: Optional[float] = (sum(closes[i - sma + 1:i + 1]) / sma) if i >= sma - 1 else None
            points.append({"t": r[0], "close": r[4], "sma": avg})
        points = [p for p in points if p["sma"] is not None][-show:]
        bull = bool(points and points[-1]["close"] > points[-1]["sma"])
        return {"points": points, "bull": bull, "sma": sma}, stale
