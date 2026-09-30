"""Cours Binance publics pour le panneau (data-api.binance.vision : sans
clé, sans ordre, accessible partout).

Le panneau ne fait pas attendre : une valeur déjà lue est rendue tout de
suite, puis relue en arrière-plan ; les valeurs consultées à l'instant sont
tenues à jour d'avance ; les connexions vers Binance restent ouvertes (la
poignée de main chiffrée, le plus long, n'est payée qu'une fois)."""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.parse
import urllib.request
import weakref
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

import v29

try:                        # installé avec ccxt ; sinon urllib, sans connexions gardées
    import requests
except ImportError:         # pragma: no cover
    requests = None

INTERVALS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
TICKER_TTL = 10             # secondes : Binance est relu au plus une fois par période
KLINE_TTL = 30
DAILY_TTL = 300
MAX_STALE = 600             # une valeur plus vieille n'est pas montrée sans être relue
KEEP_FRESH_SEC = 45         # valeur consultée depuis moins de 45 s : tenue à jour d'avance
WORKERS = 4                 # lectures simultanées en arrière-plan
SIZES = (100, 200, 500, 1000)   # bougies lues par paliers : une lecture sert tous les graphiques
UA = "TrendGuard-panneau"

Load = Callable[[], Any]

_session: Any = None
_session_lock = threading.Lock()


def http_json(url: str, timeout: float = 20.0) -> Any:
    """JSON d'une adresse publique, par une connexion gardée ouverte."""
    global _session
    if requests is None:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    with _session_lock:
        if _session is None:
            _session = requests.Session()
            _session.headers["User-Agent"] = UA
            _session.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=WORKERS + 4))
    r = _session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _worker(ref: "weakref.ref[Market]", tasks: "queue.Queue[Tuple[str, Load]]",
            stop: threading.Event) -> None:
    """Relit en arrière-plan les valeurs demandées ; s'arrête avec son Market."""
    while not stop.is_set():
        try:
            key, load = tasks.get(timeout=1.0)
        except queue.Empty:
            if ref() is None:
                return
            continue
        market = ref()
        if market is None:
            return
        market._run(key, load, must=False)
        del market


def _keeper(ref: "weakref.ref[Market]", stop: threading.Event) -> None:
    """Tient à jour d'avance les valeurs consultées à l'instant."""
    while not stop.wait(1.0):
        market = ref()
        if market is None:
            return
        market._refresh_due()
        del market


class Market:
    def __init__(self, fetch: Callable[[str], Any] = http_json, quote: str = "USDT",
                 background: bool = True, clock: Callable[[], float] = time.time):
        self.fetch = fetch
        self.quote = quote
        self.background = background
        self.clock = clock
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._failed: Set[str] = set()                   # dernière lecture ratée (réseau)
        self._loading: Dict[str, threading.Event] = {}   # une seule lecture par valeur à la fois
        self._used: Dict[str, Tuple[float, float, Load]] = {}
        self._bases: Set[str] = set()
        self._sizes: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._tasks: "queue.Queue[Tuple[str, Load]]" = queue.Queue()
        self._stop = threading.Event()
        self._started = False
        self._warming = threading.local()

    # ---------- Cache : jamais d'attente quand une valeur récente existe ----------

    def _cached(self, key: str, ttl: float, load: Load,
                valid: Callable[[Any], bool] = lambda value: True) -> Tuple[Any, bool]:
        """(valeur, périmée). Fraîche : rendue. Un peu vieille : rendue tout de
        suite et relue en arrière-plan. Absente ou trop vieille : lue
        maintenant (les demandes simultanées attendent la même lecture). En
        cas de panne réseau, la dernière valeur connue est rendue et signalée
        périmée."""
        hit: Optional[Tuple[float, Any]] = None
        for _ in range(2):
            now = self.clock()
            with self._lock:
                hit = self._cache.get(key)
                if hit is not None and not valid(hit[1]):
                    hit = None
                if not getattr(self._warming, "on", False):
                    self._used[key] = (now, ttl, load)
                failed = key in self._failed
                running = self._loading.get(key)
                age = now - hit[0] if hit is not None else MAX_STALE
                if hit is not None and age < ttl:
                    return hit[1], failed
                later = hit is not None and self.background and age < MAX_STALE
                if not later and running is None:
                    self._loading[key] = threading.Event()
            if later:
                self._refresh(key, load)
                return hit[1], failed
            if running is None:
                return self._run(key, load, hit)
            running.wait(timeout=30)
        if hit is not None:
            return hit[1], True
        raise OSError("cours Binance indisponibles")

    def _run(self, key: str, load: Load, hit: Optional[Tuple[float, Any]] = None,
             must: bool = True) -> Tuple[Any, bool]:
        """Lit la valeur et la range dans le cache (la lecture est annoncée
        dans `_loading` par celui qui l'a décidée)."""
        try:
            value = load()
        except Exception:
            with self._lock:
                self._failed.add(key)
            if hit is not None:
                return hit[1], True
            if must:
                raise
            return None, True
        else:
            with self._lock:
                self._cache[key] = (self.clock(), value)
                self._failed.discard(key)
            return value, False
        finally:
            with self._lock:
                done = self._loading.pop(key, None)
            if done is not None:
                done.set()

    def _refresh(self, key: str, load: Load) -> None:
        """Demande une relecture en arrière-plan (rien si elle est en cours)."""
        with self._lock:
            if key in self._loading:
                return
            self._loading[key] = threading.Event()
            start = not self._started
            self._started = True
        if start:
            ref = weakref.ref(self)
            for _ in range(WORKERS):
                threading.Thread(target=_worker, args=(ref, self._tasks, self._stop),
                                 daemon=True, name="cours-binance").start()
            threading.Thread(target=_keeper, args=(ref, self._stop), daemon=True,
                             name="cours-binance-avance").start()
        self._tasks.put((key, load))

    def _refresh_due(self) -> None:
        """Relit d'avance les valeurs consultées à l'instant dont la période
        est écoulée : la page suivante les trouve à jour."""
        now = self.clock()
        with self._lock:
            for key in [k for k, (used, _ttl, _load) in self._used.items()
                        if now - used >= KEEP_FRESH_SEC]:
                del self._used[key]
            due = [(key, load) for key, (_used, ttl, load) in self._used.items()
                   if key in self._cache and now - self._cache[key][0] >= ttl
                   and key not in self._loading]
        for key, load in due:
            self._refresh(key, load)

    def close(self) -> None:
        """Arrête les lectures en arrière-plan."""
        self._stop.set()

    # ---------- Cours et bougies ----------

    def symbol(self, base: str) -> str:
        return f"{base.upper()}{self.quote}"

    def tickers(self, bases: Iterable[str]) -> Tuple[Dict[str, Dict[str, float]], bool]:
        """Cours et variation sur 24 h. Une seule lecture sert toutes les
        pages : les cryptos demandées s'ajoutent à celles déjà suivies."""
        wanted = [b.lower() for b in bases]
        with self._lock:
            self._bases.update(wanted)
            symbols = sorted(self.symbol(b) for b in self._bases)

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
        data, stale = self._cached("tickers:" + ",".join(symbols), TICKER_TTL, load)
        return {b: data[b] for b in wanted if b in data}, stale

    def klines(self, base: str, interval: str = "1h", limit: int = 300
               ) -> Tuple[List[List[float]], bool]:
        """Les `limit` dernières bougies [t, ouverture, haut, bas, clôture,
        volume]. Lues par paliers et gardées par crypto et intervalle : un
        graphique plus court est servi sans nouvelle lecture."""
        if interval not in INTERVALS:
            raise ValueError(f"intervalle inconnu : {interval}")
        limit = max(10, min(1000, int(limit)))
        key = f"klines:{base.lower()}:{interval}"
        with self._lock:
            size = max(self._sizes.get(key, 0), next(s for s in SIZES if s >= limit))
            self._sizes[key] = size

        def load() -> Dict[str, Any]:
            q = urllib.parse.urlencode({"symbol": self.symbol(base), "interval": interval,
                                        "limit": size})
            rows = [[int(r[0]) // 1000] + [float(x) for x in r[1:6]]
                    for r in self.fetch(f"{v29.PUBLIC_DATA_API}/klines?{q}")]
            return {"rows": rows, "size": size}
        got, stale = self._cached(key, DAILY_TTL if interval == "1d" else KLINE_TTL, load,
                                  valid=lambda value: value["size"] >= size)
        return got["rows"][-limit:], stale

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

    def warm(self, bases: Iterable[str], charts: Iterable[Tuple[str, str, int]] = (),
             sparks: bool = True) -> None:
        """Lit d'avance ce que les pages afficheront : cours de `bases`, leurs
        bougies horaires (`sparks` : courbes des cartes) et les bougies de
        `charts` [(crypto, intervalle, nombre)]. Une panne est ignorée : la
        page relira elle-même."""
        bases = [b.lower() for b in bases]
        jobs: List[Load] = [lambda: self.tickers(bases)]
        if sparks:
            jobs += [lambda b=b: self.klines(b, "1h", SIZES[0]) for b in bases]
        jobs += [lambda c=c: self.klines(*c) for c in charts]
        todo: "queue.Queue[Load]" = queue.Queue()
        for job in jobs:
            todo.put(job)

        def run() -> None:
            self._warming.on = True          # lecture d'avance : pas une consultation
            while True:
                try:
                    job = todo.get_nowait()
                except queue.Empty:
                    return
                try:
                    job()
                except Exception:
                    pass
        threads = [threading.Thread(target=run, daemon=True, name="cours-binance-avance")
                   for _ in range(min(WORKERS, len(jobs)))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
