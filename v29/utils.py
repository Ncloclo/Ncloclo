"""Utilitaires : environnement, secrets masqués, arrondis, heure de Binance, client Binance.

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import logging
import math
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, fields
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import ccxt

from .constants import _ENV_BOOL, _LOG_ROOT


#
# Conventions d'unités :
#   quote : USDT  | base : TRX | ratio : R | price : quote/base
#   eq/pnl : quote | amount_held : base

def ensure_utf8_stdio() -> None:
    """Sortie console en UTF-8 : sous Windows, une sortie redirigée (fichier,
    tâche planifiée) est en cp1252 et les symboles des journaux (↗ ↘ 🛑 …)
    feraient échouer l'affichage."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if enc != "utf8" and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_env_logger: Optional[logging.Logger] = None


def _get_env_logger() -> logging.Logger:
    global _env_logger
    if _env_logger is None:
        lg = logging.getLogger(f"{_LOG_ROOT}.env")
        if not lg.handlers:
            sh = logging.StreamHandler()
            sh.setFormatter(logging.Formatter(
                "%(asctime)s [ENV] %(levelname)s %(message)s"))
            lg.addHandler(sh)
            lg.setLevel(logging.WARNING)
            lg.propagate = False
        _env_logger = lg
    return _env_logger


def _env_f(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = float(raw)
        if not math.isfinite(v):
            raise ValueError
        return v
    except (ValueError, TypeError):
        _get_env_logger().warning(
            f"{name}={raw!r} invalide (float) → défaut {default}")
        return default


def _env_i(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        _get_env_logger().warning(
            f"{name}={raw!r} invalide (int) → défaut {default}")
        return default


def _env_b(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _ENV_BOOL


def _env_s(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _env_tuple_csv(name: str, default: str = "") -> Tuple[str, ...]:
    raw = os.environ.get(name, default).strip()
    if not raw:
        return ()
    return tuple(a.strip().lower() for a in raw.split(",") if a.strip())


def redact_url(url: str) -> str:
    """Garde schéma + hôte ; retire identifiants, chemin, query, fragment.
    Les labels d'hôte ressemblant à une clé (≥ 20 car. alphanumériques)
    sont masqués."""
    if not url:
        return ""
    try:
        p = urlparse(url)
        host = p.hostname or ""
        labels = []
        for lab in host.split("."):
            if len(lab) >= 20 and re.fullmatch(r"[A-Za-z0-9_-]+", lab):
                labels.append("…")
            else:
                labels.append(lab)
        host = ".".join(labels)
        if p.port:
            host = f"{host}:{p.port}"
        path = "/…" if (p.path and p.path != "/") else ""
        return urlunparse((p.scheme, host, path, "", "", ""))
    except Exception:
        return "<redacted>"


def redact_address(addr: str) -> str:
    if not addr:
        return ""
    s = str(addr)
    if len(s) <= 12:
        return s
    return f"{s[:6]}…{s[-4:]}"


def scrub_secrets(text: str, secrets: List[str]) -> str:
    """Retire toute occurrence d'un secret (et de ses composants d'URL)
    d'un message avant log/notification."""
    out = str(text)
    for s in secrets:
        if not s:
            continue
        candidates = {s}
        try:
            p = urlparse(s)
            if p.path and len(p.path) > 1:
                candidates.add(p.path)
                candidates.add(p.path.strip("/"))
            if p.query:
                candidates.add(p.query)
            if p.username:
                candidates.add(p.username)
            if p.password:
                candidates.add(p.password)
        except Exception:
            pass
        for c in sorted(candidates, key=len, reverse=True):
            if c and len(c) >= 6:
                out = out.replace(c, "<redacted>")
    return out


def _dec_round(value: float, step: float, rounding) -> float:
    if step <= 0:
        return float(value)
    try:
        v = Decimal(str(value))
        s = Decimal(str(step))
        return float((v / s).to_integral_value(rounding=rounding) * s)
    except (InvalidOperation, ValueError):
        if rounding == ROUND_DOWN:
            return math.floor(value / step) * step
        if rounding == ROUND_UP:
            return math.ceil(value / step) * step
        return value


def _dataclass_from_dict(klass, data: Dict[str, Any]):
    valid = {f.name for f in fields(klass)}
    clean = {k: v for k, v in data.items() if k in valid}
    return klass(**clean)


# Horloge du bot : l'heure de l'exchange (Binance), pas celle du PC.
# _CLOCK_OFFSET_MS = heure Binance − heure du PC, mesuré par
# sync_exchange_clock() ; 0 tant qu'aucune mesure n'a été faite.
_CLOCK_OFFSET_MS = 0.0


def clock_offset_ms() -> float:
    return _CLOCK_OFFSET_MS


def set_clock_offset_ms(offset_ms: float) -> None:
    global _CLOCK_OFFSET_MS
    _CLOCK_OFFSET_MS = float(offset_ms)


def _utcnow() -> datetime:
    """Heure du bot = heure de Binance (heure du PC corrigée de l'écart
    mesuré). Sert aux décisions, à la clôture des bougies et aux dates
    enregistrées."""
    return datetime.now(timezone.utc) + timedelta(milliseconds=_CLOCK_OFFSET_MS)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _day_key(now: Optional[datetime] = None) -> str:
    return (now or _utcnow()).date().isoformat()


def _week_key(now: Optional[datetime] = None) -> str:
    n = now or _utcnow()
    return (n - timedelta(days=n.weekday())).date().isoformat()


def _today_utc() -> str:
    return _day_key()


def _week_start_utc() -> str:
    return _week_key()


_TF_UNITS_MS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000,
                "w": 604_800_000, "M": 2_592_000_000}


def _timeframe_ms(tf: str) -> int:
    """Convertit un timeframe ccxt en ms. 'm' = minute, 'M' = mois (30 j).
    Lève ValueError si le format est invalide (fail-closed)."""
    s = str(tf).strip()
    if len(s) < 2:
        raise ValueError(f"timeframe invalide: {tf!r}")
    unit = s[-1]
    if unit not in _TF_UNITS_MS:
        unit = unit.lower()
    if unit not in _TF_UNITS_MS:
        raise ValueError(f"timeframe invalide: {tf!r}")
    try:
        n = int(s[:-1])
    except ValueError:
        raise ValueError(f"timeframe invalide: {tf!r}")
    if n <= 0:
        raise ValueError(f"timeframe invalide: {tf!r}")
    return n * _TF_UNITS_MS[unit]


def _annualization_factor(timeframe: str) -> float:
    return (365.25 * 86_400_000) / _timeframe_ms(timeframe)


def _bars_per_day(timeframe: str) -> int:
    return max(1, int(round(86_400_000 / _timeframe_ms(timeframe))))


def _client_id(prefix: str = "V29") -> str:
    return (f"{prefix}{int(time.time()*1000)%10**10:010d}"
            f"{uuid.uuid4().hex[:8]}")[:36]


# Alias rétro-compatible (V29.5).
_cancel_client_id = _client_id


BINANCE_TIMEOUT_MS = 30_000


def make_binance(api_key: str = "", secret: str = "",
                 testnet: bool = False) -> Any:
    """Client ccxt Binance Spot partagé par tous les outils :
    - marchés Spot uniquement : une requête au démarrage au lieu de trois
      (ccxt charge par défaut aussi les deux marchés à terme, inutiles ici) ;
    - délai de 30 s au lieu de 10 s : une connexion lente ne fait plus
      échouer le chargement des marchés ;
    - horodatage des requêtes signées corrigé de l'écart d'horloge avec
      Binance (voir sync_exchange_clock)."""
    opts: Dict[str, Any] = {
        "enableRateLimit": True, "timeout": BINANCE_TIMEOUT_MS,
        "options": {"defaultType": "spot", "adjustForTimeDifference": True,
                    "fetchMarkets": {"types": ["spot"]}}}
    if api_key and secret:
        opts.update({"apiKey": api_key, "secret": secret})
    ex = ccxt.binance(opts)
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


# Données publiques de marché : data-api.binance.vision sert les mêmes
# bougies qu'api.binance.com, sans restriction géographique (serveurs de
# GitHub Actions ou d'un agent dans le cloud, aux États-Unis : 451 sur
# api.binance.com).
PUBLIC_DATA_API = "https://data-api.binance.vision/api/v3"


def make_public_binance() -> Any:
    """Client Binance Spot en lecture seule sur les données publiques de
    marché (aucune clé, aucun ordre possible)."""
    ex = make_binance()
    ex.urls["api"]["public"] = PUBLIC_DATA_API
    return ex


class PublicKlines:
    """Bougies par requête directe /api/v3/klines, sans charger la liste
    complète des marchés (4,7 Mo avec ccxt) : rapide sur une connexion
    faible. Pour l'historique et l'heure de Binance uniquement."""

    def __init__(self, exchange: Any = None):
        self.exchange = exchange or make_public_binance()

    def fetch_time(self) -> int:
        return int(self.exchange.fetch_time())

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d",
                    since: Optional[int] = None, limit: int = 1000) -> List[List[float]]:
        params: Dict[str, Any] = {"symbol": symbol.replace("/", ""),
                                  "interval": timeframe, "limit": limit}
        if since is not None:
            params["startTime"] = int(since)
        rows = self.exchange.publicGetKlines(params)
        return [[int(r[0])] + [float(x) for x in r[1:6]] for r in rows]


@dataclass
class ClockSync:
    offset_ms: float        # heure Binance − heure du PC
    uncertainty_ms: float   # ± demi aller-retour de la meilleure mesure
    latency_ms: float       # aller-retour de la meilleure mesure
    samples: int            # mesures réussies


def measure_exchange_clock(exchange: Any, samples: int = 5,
                           time_fn: Callable[[], float] = time.time
                           ) -> Optional[ClockSync]:
    """Écart entre l'heure du serveur Binance et celle du PC. L'heure du
    serveur est comparée au MILIEU de l'aller-retour, et seule la mesure la
    plus rapide est retenue : l'erreur est au plus d'un demi aller-retour
    (ccxt, lui, la compare à l'heure de réception : erreur jusqu'à un
    aller-retour complet, plusieurs secondes sur une connexion lente)."""
    fetch_time = getattr(exchange, "fetch_time", None)
    if not callable(fetch_time):
        return None
    best: Optional[Tuple[float, float]] = None
    ok = 0
    for _ in range(max(1, samples)):
        try:
            t0 = time_fn()
            server_ms = float(fetch_time())
            t1 = time_fn()
        except Exception:
            continue
        ok += 1
        rtt = (t1 - t0) * 1000
        offset = server_ms - (t0 + t1) / 2 * 1000
        if best is None or rtt < best[0]:
            best = (rtt, offset)
    if best is None:
        return None
    return ClockSync(offset_ms=best[1], uncertainty_ms=best[0] / 2,
                     latency_ms=best[0], samples=ok)


def sync_exchange_clock(exchange: Any, samples: int = 5) -> Optional[ClockSync]:
    """Met le bot à l'heure de Binance : _utcnow() (décisions, bougies
    clôturées, dates, journaux) ET l'horodatage des requêtes signées
    (ccxt : timeDifference = PC − Binance). Au-delà de 10 s d'écart,
    Binance refuserait tous les ordres, y compris les stops (-1021).
    None si Binance est injoignable (l'écart précédent est conservé)."""
    s = measure_exchange_clock(exchange, samples)
    if s is None:
        return None
    set_clock_offset_ms(s.offset_ms)
    opts = getattr(exchange, "options", None)
    if isinstance(opts, dict):
        opts["timeDifference"] = int(round(-s.offset_ms))
    return s


def resync_clock(exchange: Any) -> Optional[int]:
    """Recalage rapide (3 mesures) ; écart en ms (Binance − PC) ou None."""
    s = sync_exchange_clock(exchange, samples=3)
    return None if s is None else int(round(s.offset_ms))


def describe_clock(offset_ms: float, uncertainty_ms: Optional[float] = None) -> str:
    """« PC en retard de 1,3 s sur Binance (± 0,2 s) »."""
    if abs(offset_ms) < 50:
        txt = "PC à l'heure de Binance"
    else:
        txt = (f"PC en {'retard' if offset_ms > 0 else 'avance'} de "
               f"{abs(offset_ms) / 1000:.1f} s sur Binance").replace(".", ",")
    if uncertainty_ms is not None:
        txt += f" (± {uncertainty_ms / 1000:.1f} s)".replace(".", ",")
    return txt
