#!/usr/bin/env python3
"""
V29.6-QUANT — Bot hybride CEX (Binance Spot) + Wallet EVM.

════════════════════════════════════════════════════════════════════════
PRINCIPES D'HARMONISATION
════════════════════════════════════════════════════════════════════════
P1. Résultats nommés. P2. Pas de réentrance implicite. P3. Fail-closed/noisy.
P4. I/O cohérentes. P5. Sections ascendantes (0 → 17).
P6. Env unifiés. P7. Logs [CAT]. P8. Exception → log + action.
P9. Transitoires exclus. P10. Unités : quote/base/ratio.
P11. Le solde exchange fait foi : on ne vend/protège jamais plus que le
     solde disponible du bot (hors EXTERNAL_BASE_RESERVE).
P12. Toute exécution est idempotente : chaque fill est comptabilisé une
     seule fois (recorded_fills), chaque ordre sensible est précédé d'une
     intention persistée (pending_order) et résolu par client-id.

════════════════════════════════════════════════════════════════════════
CHANGELOG V29.6 (refonte corrective du diagnostic V29.5)
════════════════════════════════════════════════════════════════════════
Bloquants
  - Config par défaut valide (adaptive_freshness_max=45 ≤ max_signal_age_min).
  - Suite de tests déplacée dans tests/ (pytest) avec un simulateur
    Binance Spot ; plus aucun test tautologique.
Exécution live
  - Cache de soldes invalidé après chaque ordre (fin des panic-sells
    systématiques après chaque achat).
  - Frais d'achat prélevés en base pris en compte : quantité détenue =
    net reçu, plafonnée au solde réel.
  - Types d'ordres Spot valides (STOP_LOSS / STOP_LOSS_LIMIT selon
    exchangeInfo) + OCO natif uniquement (le pseudo-OCO à 2 ordres est
    impossible en Spot : le solde est bloqué par le premier ordre).
  - Self-test via POST /api/v3/order/test (aucun ordre réel, aucun solde requis).
  - Machine de protection unique : snapshot → fills comptés une fois →
    re-protection systématique ; une position live n'est jamais laissée nue
    (re-protection à chaque cycle + stop logiciel de dernier recours).
  - Annulation « sûre » : on relit chaque jambe APRÈS l'annulation (plus de
    fill perdu entre la vérification et l'annulation).
  - Sorties partielles live : annulation de la protection → vente → nouvelle
    protection sur le reliquat.
  - Time-exit : plus de TypeError, plus de position laissée sans stop.
  - Intentions d'ordres persistées + résolution par client-id (crash ou
    timeout réseau entre l'ordre et la sauvegarde).
  - Reconciliation fail-closed (une erreur d'API n'efface plus une position).
  - GET /api/v3/orderList sans paramètre `symbol`.
Blocages définitifs supprimés
  - Pause pertes consécutives : compteur dédié remis à zéro à la pause.
  - Plus de faux « double-sell » / orphelin à chaque sortie OCO.
  - Orphelin re-vérifié périodiquement ; commande `resume` après audit.
  - Poussière (< min_notional) sortie proprement des livres.
Risque / quant
  - Adaptatif « consec » dans le bon sens (Sharpe négatif → pause plus tôt).
  - Latence adaptative mesurée sur chaque bougie (plus de biais de survie).
  - Break-even au-dessus du coût de revient (frais + slippage).
  - R-multiple rapporté au risque initial (additif sur les jambes partielles).
  - Disjoncteurs fail-closed si l'equity est illisible ; DD hebdo réarmé
    chaque semaine.
  - Volatilité réalisée annualisée selon le timeframe.
Backtest / recherche
  - Même logique que le live : biais HTF/BTC (sans look-ahead), BE,
    trailing, time-exit, cooldowns, disjoncteurs, désactivation de module.
  - Plus de double comptage des partielles ; métriques sur TOUS les trades.
  - Walk-forward avec vraie optimisation in-sample et fenêtres sans perte
    de warm-up.
Blockchain / sécurité
  - Nonce : une seule sémantique (« prochain nonce libre ») → plus de trou.
  - Confirmations, effectiveGasPrice, suivi de toutes les tx RBF.
  - Encodage ERC-20 compatible web3 v6/v7/v8 ; montants en Decimal.
  - Secrets (URL RPC, clés) retirés des messages d'erreur.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import errno
import hashlib
import itertools
import json
import logging
import math
import os
import queue
import re
import signal
import socket
import sqlite3
import statistics
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import ccxt
import numpy as np
import pandas as pd

try:
    from web3 import Web3
    from eth_account import Account
    try:
        from web3.middleware import ExtraDataToPOAMiddleware
    except ImportError:
        try:
            from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware
        except ImportError:
            ExtraDataToPOAMiddleware = None
    WEB3_AVAILABLE = True
except ImportError:
    Web3 = None
    Account = None
    WEB3_AVAILABLE = False
    ExtraDataToPOAMiddleware = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ══════════════════════════════════════════════════════════════════════
# SECTION 0 — CONSTANTES
# ══════════════════════════════════════════════════════════════════════

VERSION_MODULE = "V29.6"
# Les fichiers d'état par défaut sont placés à côté du programme (et non
# dans le dossier courant) : un lancement depuis un autre dossier retrouve
# la même base et le même verrou.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_VERSION = 11

_ENV_BOOL = ("1", "true", "yes", "on")
_LOG_ROOT = "v29"

# Préfixes de client-id : permettent d'identifier nos ordres sur l'exchange.
CID_ENTRY_MARKET = "BM"
CID_ENTRY_LIMIT = "QE"
CID_EXIT_MARKET = "SM"
CID_OCO_LIST = "QO"
CID_OCO_TP = "QT"
CID_OCO_SL = "QS"
CID_STOP = "QB"
PROTECTION_CID_PREFIXES = (CID_OCO_LIST, CID_OCO_TP, CID_OCO_SL, CID_STOP)

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}],
     "type": "function"},
    {"constant": False, "inputs": [{"name": "_to", "type": "address"},
                                     {"name": "_value", "type": "uint256"}],
     "name": "transfer", "outputs": [{"name": "success", "type": "bool"}],
     "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
]

ENV_DOC: Dict[str, str] = {
    "SYMBOL": "Paire CEX (ex: TRX/USDT)",
    "BTC_SYMBOL": "Paire de référence BTC (biais et volatilité)",
    "TIMEFRAME": "Timeframe principal (15m, 1h, 4h)",
    "HTF_TIMEFRAME": "Timeframe supérieur pour le biais (> TIMEFRAME)",
    "RUN_MODE": "paper | live",
    "ENABLE_LIVE_TRADING": "true pour autoriser le live",
    "LIVE_TRADING_CONFIRMATION": "Doit valoir I_UNDERSTAND_RISK",
    "BINANCE_API_KEY": "Clé API Binance (trading autorisé, retraits INTERDITS)",
    "BINANCE_API_SECRET": "Secret API Binance",
    "BINANCE_TESTNET": "true : Binance Spot testnet (à utiliser avant tout live)",
    "RISK_BASE_PCT": "Risque de base par trade en fraction d'equity (0.010 = 1%)",
    "RISK_MIN_PCT": "Risque minimum par trade",
    "RISK_MAX_PCT": "Risque maximum par trade",
    "SIZING_MODE": "fixed | kelly | vol_target",
    "MAX_DAILY_DD": "Drawdown journalier déclenchant le halt (0.03 = 3%)",
    "MAX_WEEKLY_DD": "Drawdown hebdomadaire déclenchant le halt",
    "EXTERNAL_BASE_RESERVE": "Quantité de base détenue hors bot (jamais vendue)",
    "USE_OCO": "true : protection OCO native (TP + SL)",
    "USE_L2_FILTER": "true : filtre carnet L2",
    "USE_SMART_BUY": "true : chaser limit au lieu de market",
    "SELF_TEST_CONDITIONAL_ORDERS": "true : valide les ordres au boot via order/test",
    "RECOVERY_REQUIRE_VERIFIED_ENTRY": "true : refuse une recovery sans achat vérifié",
    "RECOVERY_ADOPT_ORDERS": "true : adopter les ordres du bot inconnus de la base (base perdue)",
    "INTRABAR_PARTIAL_MODE": "Backtest : ohlc | optimistic | conservative",
    "ADAPTIVE_ENABLED": "true : active l'AdaptiveEngine",
    "HEARTBEAT_EVERY_CYCLES": "Intervalle heartbeat (cycles)",
    "HEARTBEAT_SPINNER": "true : spinner Unicode",
    "HEARTBEAT_USE_COLORS": "true : couleurs ANSI",
    "HEARTBEAT_LOG_FILE": "Chemin log heartbeat",
    "BLOCKCHAIN_ENABLED": "true : active le wallet EVM",
    "BLOCKCHAIN_CHAIN": "ethereum | bsc | polygon | arbitrum",
    "BLOCKCHAIN_RPC_URL": "URL du RPC (secret : jamais loggée en clair)",
    "BLOCKCHAIN_CHAIN_ID": "1 | 56 | 137 | 42161",
    "BLOCKCHAIN_PRIVATE_KEY": "Clé privée hex (0x…) — JAMAIS commit, wallet dédié",
    "BLOCKCHAIN_DRY_RUN": "true : simule sans broadcast",
    "BLOCKCHAIN_MAX_TX_VALUE_ETH": "Plafond par tx en natif",
    "BLOCKCHAIN_MIN_GAS_RESERVE_ETH": "Réserve native à ne pas entamer",
    "BLOCKCHAIN_MAX_PRIORITY_FEE_GWEI": "Tip EIP-1559 max",
    "BLOCKCHAIN_MAX_FEE_GWEI": "Fee total EIP-1559 max",
    "BLOCKCHAIN_CONFIRMATIONS": "Confirmations requises avant de finaliser une tx",
    "BLOCKCHAIN_TX_TIMEOUT_SEC": "Délai avant remplacement (RBF) d'une tx en attente",
    "BLOCKCHAIN_WHITELIST_ENABLED": "true : restreint les destinations",
    "BLOCKCHAIN_WHITELIST": "Adresses autorisées, CSV",
    "BLOCKCHAIN_TRACK_TOKEN": "Adresse ERC-20 à suivre",
    "BLOCKCHAIN_TRACK_TOKEN_SYMBOL": "Symbole du token suivi",
    "BLOCKCHAIN_SWEEP_ENABLED": "true : sweep auto de l'excédent natif",
    "BLOCKCHAIN_SWEEP_TARGET": "Adresse cible du sweep (doit être whitelistée)",
    "BLOCKCHAIN_SWEEP_TRIGGER_ETH": "Solde au-dessus duquel on sweep",
    "BLOCKCHAIN_SWEEP_KEEP_ETH": "Réserve native conservée pour le gaz",
    "TELEGRAM_TOKEN": "Token bot Telegram",
    "TELEGRAM_CHAT_ID": "Chat ID destination",
    "DB_FILE": "Chemin DB SQLite",
    "LOG_FILE": "Chemin log",
    "LOCK_FILE": "Chemin lock",
}


# ══════════════════════════════════════════════════════════════════════
# SECTION 1 — UTILITAIRES MODULE
# ══════════════════════════════════════════════════════════════════════
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


# ══════════════════════════════════════════════════════════════════════
# SECTION 2 — CONFIG
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Config:
    # ── Marché ──
    symbol: str = "TRX/USDT"
    btc_symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    htf_timeframe: str = "4h"
    base: str = "TRX"
    quote: str = "USDT"
    # ── Mode ──
    run_mode: str = "paper"
    live_confirmation: str = ""
    live_confirmation_required: str = "I_UNDERSTAND_RISK"
    enable_live_trading: bool = False
    binance_testnet: bool = False
    # ── Risque ──
    risk_base_pct: float = 0.010
    risk_min_pct: float = 0.005
    risk_max_pct: float = 0.015
    risk_absolute_max_pct: float = 0.020
    max_sl_dist_pct: float = 0.05
    max_position_capital_pct: float = 0.95
    min_cash_reserve_pct: float = 0.05
    external_base_reserve: float = 0.0
    sizing_mode: str = "fixed"
    kelly_fraction: float = 0.25
    kelly_min_trades: int = 10
    kelly_window: int = 30
    vol_target_annual: float = 0.20
    tier_mults: Tuple[Tuple[str, float], ...] = (
        ("S", 1.20), ("A", 1.00), ("B", 0.65))
    tier_thresholds: Tuple[Tuple[str, int], ...] = (
        ("S", 70), ("A", 50), ("B", 30))
    regime_risk_mult: Tuple[Tuple[str, float], ...] = (
        ("TREND_UP", 1.00), ("TREND_DOWN", 0.00),
        ("RANGE", 0.65), ("UNCLEAR", 0.45))
    dd_derisk_curve: Tuple[Tuple[float, float], ...] = (
        (0.005, 1.00), (0.010, 0.85), (0.020, 0.60), (0.030, 0.35))
    max_signal_age_min: int = 45
    signal_age_decay_pct: float = 0.30
    module_min_sharpe: float = 0.15
    module_sharpe_window: int = 25
    module_sharpe_min_trades: int = 15
    module_reactivation_hours: int = 72
    # ── Sorties partielles (fractions de la quantité INITIALE) ──
    partial_exit_enabled: bool = True
    partial_exit_r1: float = 1.0
    partial_exit_pct1: float = 0.40
    partial_exit_r2: float = 2.0
    partial_exit_pct2: float = 0.30
    min_remaining_notional_mult: float = 1.5
    # ── Filtre volatilité BTC ──
    btc_vol_filter_enabled: bool = True
    btc_vol_window_hours: int = 24
    btc_vol_threshold_annual: float = 1.00
    btc_vol_size_reduction: float = 0.60
    # ── Disjoncteurs ──
    max_daily_dd: float = 0.03
    max_weekly_dd: float = 0.08
    consec_loss_pause: int = 5
    consec_loss_pause_hours: int = 24
    post_loss_cooldown_hours: int = 6
    post_loss_skip_adx: float = 30.0
    # ── Break-even / trailing / time-exit ──
    break_even_trigger: float = 0.01
    break_even_offset: float = 0.004
    trailing_enabled: bool = True
    trail_atr_mult: float = 2.0
    trailing_min_raise_pct: float = 0.005
    trailing_min_interval_sec: int = 300
    max_trade_age_hours: int = 48
    time_exit_min_r_mult: float = 0.5
    time_exit_max_attempts: int = 3
    time_exit_cooldown_hours: int = 4
    time_exit_min_retry_sec: int = 120
    # ── Indicateurs ──
    trend_ema: int = 200
    fast_ema: int = 9
    slow_ema: int = 21
    atr_period: int = 14
    atr_min_pct: float = 0.005
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    adx_period: int = 14
    vol_ma_period: int = 20
    vwap_period: int = 24
    obv_ema_span: int = 20
    swing_window: int = 5
    atr_rank_window: int = 200
    adx_trend_threshold: float = 22.0
    adx_range_threshold: float = 20.0
    # ── Biais ──
    btc_bias_enabled: bool = True
    btc_ema: int = 200
    btc_bull_buffer: float = 0.005
    btc_bear_buffer: float = 0.005
    htf_bias_enabled: bool = True
    htf_ema: int = 200
    htf_buffer: float = 0.005
    cache_ttl_sec: int = 900
    # ── Filtres de signal ──
    rsi_adaptive: bool = True
    vol_confirm_ratio: float = 1.3
    vol_breakout_ratio: float = 1.8
    vol_capitulation: float = 3.0
    pullback_rsi_lo: float = 40.0
    pullback_rsi_hi: float = 58.0
    pullback_max_dist_atr: float = 2.0
    pullback_prev_bullish_buffer: float = 1.005
    max_dist_ema_fast_pct: float = 0.06
    flash_move_pct: float = 0.05
    flash_move_lookback: int = 5
    flash_cooldown_min: int = 30
    # ── Exécution / protection ──
    use_oco: bool = True
    stop_only_protection: bool = False   # stratégies sans TP (TrendGuard)
    break_even_enabled: bool = True
    time_exit_enabled: bool = True
    stop_limit_offset_pct: float = 0.01
    software_stop_enabled: bool = True
    recovery_require_verified_entry: bool = True
    recovery_adopt_orders: bool = False
    protection_unknown_max: int = 6
    protection_unknown_max_sec: int = 120
    pending_order_timeout_sec: int = 300
    use_l2_filter: bool = False
    use_smart_buy: bool = False
    l2_depth: int = 20
    l2_cache_ttl_sec: int = 2
    obi_toxic_threshold: float = -0.55
    max_spread_tolerance: float = 0.005
    chaser_max_attempts: int = 3
    chaser_max_slippage_pct: float = 0.002
    chaser_wait_sec: int = 3
    self_test_conditional_orders: bool = True
    fee_rate: float = 0.001
    entry_slippage_buffer_pct: float = 0.0005
    exit_slippage_buffer_pct: float = 0.001
    paper_slippage_pct: float = 0.0005
    paper_fee_rate: float = 0.001
    # ── Boucle ──
    loop_interval_sec: int = 30
    network_backoff_init: int = 5
    network_backoff_max: int = 180
    network_backoff_mult: float = 2.0
    equity_cache_ttl_sec: int = 30
    balance_cache_ttl_sec: int = 5
    orphan_recheck_sec: int = 300
    report_hour_utc: int = 20
    report_retry_throttle_sec: int = 300
    intrabar_partial_mode: str = "ohlc"
    heartbeat_every_cycles: int = 10
    heartbeat_spinner: bool = True
    heartbeat_use_colors: bool = True
    heartbeat_log_file: str = ""
    heartbeat_equity_window: int = 30
    # ── Adaptatif ──
    adaptive_enabled: bool = True
    adaptive_latency_window: int = 50
    adaptive_freshness_min: int = 15
    adaptive_freshness_max: int = 45
    adaptive_atr_scaling: bool = True
    adaptive_consec_scaling: bool = True
    adaptive_min_samples: int = 10
    last_trades_per_module: int = 50
    reconcile_trades_window_hours: int = 72
    max_consecutive_api_errors: int = 3
    # ── Blockchain ──
    blockchain_enabled: bool = False
    blockchain_chain: str = "ethereum"
    blockchain_rpc_url: str = ""
    blockchain_chain_id: int = 1
    blockchain_dry_run: bool = True
    blockchain_max_tx_value_eth: float = 0.01
    blockchain_min_gas_reserve_eth: float = 0.005
    blockchain_max_priority_fee_gwei: float = 5.0
    blockchain_max_fee_gwei: float = 100.0
    blockchain_confirmations: int = 2
    blockchain_tx_timeout_sec: int = 600
    blockchain_whitelist_enabled: bool = True
    blockchain_whitelist: Tuple[str, ...] = ()
    blockchain_track_token: str = ""
    blockchain_track_token_symbol: str = ""
    blockchain_replace_fee_boost_pct: float = 0.15
    blockchain_max_rbf_attempts: int = 3
    blockchain_sweep_enabled: bool = False
    blockchain_sweep_target: str = ""
    blockchain_sweep_trigger_eth: float = 0.05
    blockchain_sweep_keep_eth: float = 0.01
    blockchain_sweep_fail_cooldown_sec: int = 1800
    # ── Fichiers ──
    db_file: str = ""
    log_file: str = ""
    lock_file: str = ""

    def __post_init__(self):
        if self.run_mode not in {"paper", "live"}:
            raise ValueError("run_mode doit être 'paper' ou 'live'.")
        if _timeframe_ms(self.htf_timeframe) <= _timeframe_ms(self.timeframe):
            raise ValueError("HTF_TIMEFRAME doit être supérieur à TIMEFRAME.")
        if self.sizing_mode not in {"fixed", "kelly", "vol_target"}:
            raise ValueError(
                f"sizing_mode={self.sizing_mode!r} : fixed | kelly | vol_target.")
        if not (0 < self.risk_min_pct <= self.risk_base_pct <= self.risk_max_pct):
            raise ValueError("Bornes de risque incohérentes.")
        if self.risk_max_pct > self.risk_absolute_max_pct:
            raise ValueError("risk_max_pct > risk_absolute_max_pct.")
        if not (0 < self.max_daily_dd <= self.max_weekly_dd < 1):
            raise ValueError("Il faut 0 < max_daily_dd <= max_weekly_dd < 1.")
        if not (0 < self.max_position_capital_pct <= 1):
            raise ValueError("max_position_capital_pct doit être dans ]0, 1].")
        if self.external_base_reserve < 0:
            raise ValueError("external_base_reserve doit être >= 0.")
        if self.partial_exit_enabled:
            if not (0 < self.partial_exit_pct1 and 0 < self.partial_exit_pct2
                    and self.partial_exit_pct1 + self.partial_exit_pct2 < 1):
                raise ValueError(
                    "partial_exit_pct1 + partial_exit_pct2 doit être < 1.")
            if not (0 < self.partial_exit_r1 < self.partial_exit_r2):
                raise ValueError("Il faut 0 < partial_exit_r1 < partial_exit_r2.")
        round_trip = 2 * self.fee_rate + self.exit_slippage_buffer_pct
        if self.break_even_offset < round_trip:
            raise ValueError(
                f"break_even_offset={self.break_even_offset} < coûts "
                f"aller-retour {round_trip:.4f} : un break-even serait une perte.")
        if self.break_even_trigger <= self.break_even_offset:
            raise ValueError("break_even_trigger doit être > break_even_offset.")
        if not (0 < self.stop_limit_offset_pct < 0.1):
            raise ValueError("stop_limit_offset_pct doit être dans ]0, 0.1[.")
        if self.intrabar_partial_mode not in {"ohlc", "optimistic",
                                                "conservative"}:
            raise ValueError("intrabar_partial_mode invalide.")
        if {k for k, _ in self.tier_mults} != {k for k, _ in
                                                self.tier_thresholds}:
            raise ValueError("tier_mults et tier_thresholds : clés différentes.")
        if self.adaptive_freshness_min >= self.adaptive_freshness_max:
            raise ValueError("adaptive_freshness_min >= max.")
        if self.adaptive_freshness_max > self.max_signal_age_min:
            raise ValueError(
                "adaptive_freshness_max > max_signal_age_min : "
                "l'adaptatif ne peut que resserrer, pas desserrer.")
        if self.run_mode == "live":
            if not self.enable_live_trading:
                raise ValueError("LIVE refusé: ENABLE_LIVE_TRADING=true requis.")
            if self.live_confirmation != self.live_confirmation_required:
                raise ValueError(
                    "LIVE refusé: LIVE_TRADING_CONFIRMATION requis.")
            if not self.use_oco and not self.stop_only_protection:
                raise ValueError("LIVE refusé: use_oco=true requis.")
        if self.blockchain_enabled:
            if not WEB3_AVAILABLE:
                raise ValueError("blockchain_enabled=true mais web3 absent.")
            if not self.blockchain_rpc_url:
                raise ValueError("blockchain_enabled=true mais RPC URL absente.")
            if self.blockchain_chain_id <= 0:
                raise ValueError("blockchain_chain_id invalide.")
            if self.blockchain_max_priority_fee_gwei > self.blockchain_max_fee_gwei:
                raise ValueError("max_priority_fee > max_fee.")
            if self.blockchain_confirmations < 1:
                raise ValueError("blockchain_confirmations doit être >= 1.")
            if self.blockchain_chain.lower() in ("bsc", "polygon") \
                    and ExtraDataToPOAMiddleware is None:
                raise ValueError("Chaîne POA requise mais web3.py trop ancien.")
            if self.blockchain_sweep_enabled:
                if not self.blockchain_sweep_target:
                    raise ValueError("sweep_enabled mais target vide.")
                if self.blockchain_sweep_trigger_eth <= self.blockchain_sweep_keep_eth:
                    raise ValueError(
                        "sweep_trigger_eth doit être > sweep_keep_eth.")
                if (self.blockchain_whitelist_enabled
                        and self.blockchain_sweep_target.lower()
                        not in {a.lower() for a in self.blockchain_whitelist}):
                    raise ValueError(
                        "BLOCKCHAIN_SWEEP_TARGET absent de BLOCKCHAIN_WHITELIST.")
        if not self.db_file:
            object.__setattr__(self, "db_file",
                               os.path.join(APP_DIR, f"v29_{self.base}_{self.quote}_{self.run_mode}.db"))
        if not self.log_file:
            object.__setattr__(self, "log_file",
                               os.path.join(APP_DIR, f"v29_{self.base}_{self.quote}_{self.run_mode}.log"))
        if not self.lock_file:
            object.__setattr__(self, "lock_file",
                               os.path.join(APP_DIR, f"v29_{self.base}_{self.quote}_{self.run_mode}.lock"))
        if not self.heartbeat_log_file:
            object.__setattr__(self, "heartbeat_log_file",
                               os.path.join(APP_DIR, f"v29_{self.base}_{self.quote}_heartbeat.log"))

    @property
    def tier_mult_map(self) -> Dict[str, float]:
        return dict(self.tier_mults)

    @property
    def tier_threshold_map(self) -> Dict[str, int]:
        return dict(self.tier_thresholds)

    @property
    def regime_mult_map(self) -> Dict[str, float]:
        return dict(self.regime_risk_mult)

    @property
    def round_trip_cost_pct(self) -> float:
        return 2 * self.fee_rate + self.exit_slippage_buffer_pct


def _parse_symbol(raw: str) -> Tuple[str, str, str]:
    s = (raw or "").strip().upper()
    parts = s.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1] or ":" in s:
        raise ValueError(f"SYMBOL={raw!r} invalide (format attendu BASE/QUOTE).")
    return s, parts[0], parts[1]


def load_config_from_env() -> Config:
    symbol, base, quote = _parse_symbol(_env_s("SYMBOL", "TRX/USDT"))
    return Config(
        symbol=symbol, base=base, quote=quote,
        btc_symbol=_env_s("BTC_SYMBOL", "BTC/USDT").upper(),
        timeframe=_env_s("TIMEFRAME", "1h"),
        htf_timeframe=_env_s("HTF_TIMEFRAME", "4h"),
        run_mode=_env_s("RUN_MODE", "paper").lower(),
        live_confirmation=_env_s("LIVE_TRADING_CONFIRMATION", ""),
        enable_live_trading=_env_b("ENABLE_LIVE_TRADING", False),
        binance_testnet=_env_b("BINANCE_TESTNET", False),
        risk_base_pct=_env_f("RISK_BASE_PCT", 0.010),
        risk_min_pct=_env_f("RISK_MIN_PCT", 0.005),
        risk_max_pct=_env_f("RISK_MAX_PCT", 0.015),
        sizing_mode=_env_s("SIZING_MODE", "fixed").lower(),
        max_daily_dd=_env_f("MAX_DAILY_DD", 0.03),
        max_weekly_dd=_env_f("MAX_WEEKLY_DD", 0.08),
        external_base_reserve=_env_f("EXTERNAL_BASE_RESERVE", 0.0),
        use_l2_filter=_env_b("USE_L2_FILTER", False),
        use_smart_buy=_env_b("USE_SMART_BUY", False),
        use_oco=_env_b("USE_OCO", True),
        self_test_conditional_orders=_env_b(
            "SELF_TEST_CONDITIONAL_ORDERS", True),
        recovery_require_verified_entry=_env_b(
            "RECOVERY_REQUIRE_VERIFIED_ENTRY", True),
        recovery_adopt_orders=_env_b("RECOVERY_ADOPT_ORDERS", False),
        intrabar_partial_mode=_env_s("INTRABAR_PARTIAL_MODE", "ohlc").lower(),
        adaptive_enabled=_env_b("ADAPTIVE_ENABLED", True),
        heartbeat_every_cycles=_env_i("HEARTBEAT_EVERY_CYCLES", 10),
        heartbeat_spinner=_env_b("HEARTBEAT_SPINNER", True),
        heartbeat_use_colors=_env_b("HEARTBEAT_USE_COLORS", True),
        heartbeat_log_file=_env_s("HEARTBEAT_LOG_FILE", ""),
        blockchain_enabled=_env_b("BLOCKCHAIN_ENABLED", False),
        blockchain_chain=_env_s("BLOCKCHAIN_CHAIN", "ethereum").lower(),
        blockchain_rpc_url=_env_s("BLOCKCHAIN_RPC_URL", ""),
        blockchain_chain_id=_env_i("BLOCKCHAIN_CHAIN_ID", 1),
        blockchain_dry_run=_env_b("BLOCKCHAIN_DRY_RUN", True),
        blockchain_max_tx_value_eth=_env_f("BLOCKCHAIN_MAX_TX_VALUE_ETH", 0.01),
        blockchain_min_gas_reserve_eth=_env_f(
            "BLOCKCHAIN_MIN_GAS_RESERVE_ETH", 0.005),
        blockchain_max_priority_fee_gwei=_env_f(
            "BLOCKCHAIN_MAX_PRIORITY_FEE_GWEI", 5.0),
        blockchain_max_fee_gwei=_env_f("BLOCKCHAIN_MAX_FEE_GWEI", 100.0),
        blockchain_confirmations=_env_i("BLOCKCHAIN_CONFIRMATIONS", 2),
        blockchain_tx_timeout_sec=_env_i("BLOCKCHAIN_TX_TIMEOUT_SEC", 600),
        blockchain_whitelist_enabled=_env_b(
            "BLOCKCHAIN_WHITELIST_ENABLED", True),
        blockchain_whitelist=_env_tuple_csv("BLOCKCHAIN_WHITELIST"),
        blockchain_track_token=_env_s("BLOCKCHAIN_TRACK_TOKEN", ""),
        blockchain_track_token_symbol=_env_s(
            "BLOCKCHAIN_TRACK_TOKEN_SYMBOL", ""),
        blockchain_sweep_enabled=_env_b("BLOCKCHAIN_SWEEP_ENABLED", False),
        blockchain_sweep_target=_env_s("BLOCKCHAIN_SWEEP_TARGET", "").lower(),
        blockchain_sweep_trigger_eth=_env_f(
            "BLOCKCHAIN_SWEEP_TRIGGER_ETH", 0.05),
        blockchain_sweep_keep_eth=_env_f("BLOCKCHAIN_SWEEP_KEEP_ETH", 0.01),
        db_file=_env_s("DB_FILE", ""),
        log_file=_env_s("LOG_FILE", ""),
        lock_file=_env_s("LOCK_FILE", ""),
    )


def dump_config_summary(cfg: Config) -> str:
    lines = ["═" * 72, f"CONFIGURATION {VERSION_MODULE}", "═" * 72]
    lines.append(f"  Marché         : {cfg.symbol} "
                 f"({cfg.timeframe} / HTF {cfg.htf_timeframe})")
    lines.append(f"  Mode           : {cfg.run_mode.upper()}"
                 + (" [LIVE]" if cfg.run_mode == "live" else "")
                 + (" [TESTNET]" if cfg.binance_testnet else ""))
    lines.append(f"  Sizing         : {cfg.sizing_mode} "
                 f"(base={cfg.risk_base_pct*100:.2f}% "
                 f"min={cfg.risk_min_pct*100:.2f}% "
                 f"max={cfg.risk_max_pct*100:.2f}%)")
    lines.append(f"  Disjoncteurs   : DD j={cfg.max_daily_dd*100:.1f}% "
                 f"hebdo={cfg.max_weekly_dd*100:.1f}% "
                 f"pause={cfg.consec_loss_pause} pertes")
    lines.append(f"  Protection     : OCO natif={cfg.use_oco} "
                 f"stop logiciel={cfg.software_stop_enabled} "
                 f"self-test={cfg.self_test_conditional_orders}")
    lines.append(f"  Filtres        : L2={cfg.use_l2_filter} "
                 f"smartbuy={cfg.use_smart_buy} "
                 f"partials={cfg.partial_exit_enabled}")
    lines.append(f"  Break-even     : trigger={cfg.break_even_trigger*100:.2f}% "
                 f"offset={cfg.break_even_offset*100:.2f}% "
                 f"(coûts A/R={cfg.round_trip_cost_pct*100:.2f}%)")
    lines.append(f"  Adaptive       : {cfg.adaptive_enabled} "
                 f"(freshness {cfg.adaptive_freshness_min}-"
                 f"{cfg.adaptive_freshness_max} min)")
    if cfg.external_base_reserve > 0:
        lines.append(f"  Réserve base   : {cfg.external_base_reserve} "
                     f"{cfg.base} (hors bot)")
    if cfg.blockchain_enabled:
        lines.append(f"  Blockchain     : {cfg.blockchain_chain} "
                     f"(id={cfg.blockchain_chain_id}) "
                     f"dry_run={cfg.blockchain_dry_run}")
        lines.append(f"    RPC          : {redact_url(cfg.blockchain_rpc_url)}")
        lines.append(f"    Whitelist    : {len(cfg.blockchain_whitelist)} "
                     f"adresse(s)")
        if cfg.blockchain_sweep_enabled:
            lines.append(f"    SWEEP        : actif → "
                         f"{redact_address(cfg.blockchain_sweep_target)}")
    else:
        lines.append("  Blockchain     : désactivée")
    lines.append("═" * 72)
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# SECTION 3 — TYPES PARTAGÉS
# ══════════════════════════════════════════════════════════════════════

class BotState(str, enum.Enum):
    FLAT = "FLAT"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    HALTED = "HALTED"


class ProtectionMode(str, enum.Enum):
    NONE = "NONE"
    OCO = "OCO"
    STOP_ONLY = "STOP_ONLY"


class ProtectionState(str, enum.Enum):
    NONE = "NONE"          # aucun ordre de protection connu
    ACTIVE = "ACTIVE"      # un ordre stop est ouvert, rien d'exécuté
    FILLED = "FILLED"      # au moins une exécution détectée
    CANCELED = "CANCELED"  # ordres terminés sans exécution (annulés/expirés)
    UNKNOWN = "UNKNOWN"    # état illisible (API)


class EntryResult(str, enum.Enum):
    SKIPPED = "skipped"        # aucun ordre envoyé
    OPENED = "opened"          # position ouverte (et protégée en live)
    ORDER_SENT = "order_sent"  # un ordre est parti mais pas de position nette


class HaltKind:
    DAILY_DD = "DAILY_DD"      # réarmé automatiquement le jour suivant
    WEEKLY_DD = "WEEKLY_DD"    # réarmé automatiquement la semaine suivante
    INTEGRITY = "INTEGRITY"    # état incertain : gel des actions discrétionnaires
    MANUAL = "MANUAL"          # nécessite `resume` après audit


# Raisons INTEGRITY levées automatiquement une fois l'ambiguïté résolue.
AUTO_CLEARABLE_HALTS = frozenset({"AMBIGUOUS_BUY", "AMBIGUOUS_SELL",
                                  "AMBIGUOUS_ORDER",
                                  "PENDING_ORDER_UNRESOLVED"})


@dataclass
class Fill:
    qty: float
    price: float
    order_id: str
    leg: str = ""                     # TP | SL | STOP | MARKET | EXTERNAL
    fee_quote: Optional[float] = None  # None = inconnu (estimé au fee_rate)


@dataclass
class ProtectionSnapshot:
    state: ProtectionState
    fills: List[Fill] = field(default_factory=list)
    open_ids: List[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class CancelResult:
    all_terminal: bool
    fills: List[Fill] = field(default_factory=list)


@dataclass
class Position:
    in_position: bool = False
    buy_price: float = 0.0            # prix moyen d'exécution (quote/base)
    cost_basis: float = 0.0           # coût de revient net de frais (quote/base)
    sl_price: float = 0.0
    tp_price: float = 0.0
    amount_held: float = 0.0          # base réellement détenue par le bot
    initial_amount: float = 0.0
    risk_per_unit: float = 0.0
    risk_quote_initial: float = 0.0   # dénominateur du R-multiple
    rr_used: float = 0.0
    module: str = ""
    regime: str = ""
    tier: str = ""
    entry_score: int = 0
    entry_adx: Optional[float] = None
    entry_obi: Optional[float] = None
    entry_order_id: Optional[str] = None
    entry_timestamp_ms: Optional[int] = None
    opened_at: Optional[str] = None
    entry_candle_ts: Optional[int] = None
    sl_update_candle_ts: Optional[int] = None
    low_since_sl_update: Optional[float] = None
    high_since_entry: Optional[float] = None
    break_even_done: bool = False
    last_trailing_ts: float = 0.0
    time_exit_attempts: int = 0
    time_exit_last_attempt_ts: float = 0.0
    time_exit_cooldown_until: Optional[str] = None
    realized_pnl: float = 0.0
    soft_stop: float = 0.0            # stop évalué à la clôture (TrendGuard)
    highest_close: float = 0.0
    partial_exit_count: int = 0
    eff_risk_pct: Optional[float] = None
    entry_equity: float = 0.0
    protection_mode: str = ProtectionMode.NONE.value
    oco_order_id: Optional[str] = None         # orderListId (OCO natif)
    oco_client_id: Optional[str] = None
    oco_tp_client_id: Optional[str] = None
    oco_sl_client_id: Optional[str] = None
    oco_tp_order_id: Optional[str] = None
    oco_sl_order_id: Optional[str] = None
    standalone_stop_order_id: Optional[str] = None
    protection_confirmed_ts: float = 0.0
    oco_misses: int = 0
    oco_uncertain_since: Optional[str] = None
    recorded_fills: Dict[str, float] = field(default_factory=dict)
    legs: List[Dict[str, Any]] = field(default_factory=list)

    def has_protection_ids(self) -> bool:
        return bool(self.oco_order_id or self.oco_tp_order_id
                    or self.oco_sl_order_id or self.standalone_stop_order_id)

    def clear_protection_ids(self) -> None:
        self.oco_order_id = None
        self.oco_client_id = None
        self.oco_tp_client_id = None
        self.oco_sl_client_id = None
        self.oco_tp_order_id = None
        self.oco_sl_order_id = None
        self.standalone_stop_order_id = None
        self.protection_mode = ProtectionMode.NONE.value
        self.oco_misses = 0
        self.oco_uncertain_since = None


@dataclass
class Portfolio:
    paper_cash: float = 1000.0
    paper_base: float = 0.0
    stats_wins: int = 0
    stats_losses: int = 0
    stats_total_pnl: float = 0.0
    consec_losses: int = 0
    losses_since_pause: int = 0
    dust_base: float = 0.0
    last_trades: List[Dict[str, Any]] = field(default_factory=list)
    per_module: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    per_tier: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class RiskState:
    daily_start_equity: Optional[float] = None
    daily_start_date: Optional[str] = None
    weekly_start_equity: Optional[float] = None
    weekly_start_date: Optional[str] = None
    paused_until: Optional[str] = None
    cooldown_until: Optional[str] = None
    cooldown_reason: Optional[str] = None
    halted: bool = False
    halt_reason: Optional[str] = None
    halt_kind: Optional[str] = None
    halted_date: Optional[str] = None
    halted_week: Optional[str] = None
    flash_cooldown_until: Optional[str] = None


@dataclass
class BlockchainState:
    last_balance_eth: float = 0.0
    last_balance_token: float = 0.0
    last_nonce: int = 0
    last_pending_tx_hash: Optional[str] = None
    pending_tx_hashes: List[str] = field(default_factory=list)
    last_pending_tx_since: Optional[str] = None
    last_pending_tx_fee_wei: Optional[str] = None
    last_pending_tx_priority_wei: Optional[str] = None
    last_pending_tx_nonce: Optional[int] = None
    last_pending_tx_to: Optional[str] = None
    last_pending_tx_value_wei: Optional[str] = None
    last_pending_tx_data: Optional[str] = None
    last_pending_tx_gas: Optional[int] = None
    last_pending_tx_kind: Optional[str] = None
    rbf_attempts: int = 0
    rbf_exhausted_notified: bool = False
    total_gas_spent_eth: float = 0.0
    tx_count: int = 0
    last_block_seen: int = 0
    eip1559_supported: Optional[bool] = None
    last_sweep_ts: float = 0.0
    last_sweep_fail_ts: float = 0.0
    sweep_count: int = 0


@dataclass
class AdaptiveState:
    signal_latencies_ms: List[int] = field(default_factory=list)
    current_freshness_min: int = 45
    current_atr_min_pct: float = 0.005
    current_consec_pause: int = 5
    last_update_ts: float = 0.0
    last_latency_candle_ts: Optional[int] = None
    bootstrap_done: bool = False


@dataclass
class BotContext:
    schema_version: int = SCHEMA_VERSION
    state: str = BotState.FLAT.value
    position: Position = field(default_factory=Position)
    portfolio: Portfolio = field(default_factory=Portfolio)
    risk: RiskState = field(default_factory=RiskState)
    blockchain: BlockchainState = field(default_factory=BlockchainState)
    adaptive: AdaptiveState = field(default_factory=AdaptiveState)
    last_signal_candle_ts: Optional[int] = None
    last_decision_candle_ts: Optional[int] = None
    last_report_date: Optional[str] = None
    last_report_attempt_ts: float = 0.0
    benchmark_start_price: Optional[float] = None
    benchmark_start_equity: Optional[float] = None
    last_equity_snapshot_ts: float = 0.0
    last_orphan_check_ts: float = 0.0
    orphan_balance: bool = False
    orphan_balance_since: Optional[str] = None
    pending_order: Optional[Dict[str, Any]] = None
    flatten_in_progress: bool = False
    cycle_count: int = 0
    started_at: Optional[str] = None
    bot_version: str = VERSION_MODULE

    _TRANSIENT_FIELDS = frozenset({"flatten_in_progress"})
    _SCALAR_FIELDS = ("last_signal_candle_ts", "last_decision_candle_ts",
                      "last_report_date", "benchmark_start_price",
                      "benchmark_start_equity", "orphan_balance",
                      "orphan_balance_since", "pending_order", "started_at")
    _CAST_FIELDS = (("last_report_attempt_ts", float),
                    ("last_equity_snapshot_ts", float),
                    ("last_orphan_check_ts", float),
                    ("cycle_count", int))
    _SECTIONS = (("position", Position), ("portfolio", Portfolio),
                 ("risk", RiskState), ("blockchain", BlockchainState),
                 ("adaptive", AdaptiveState))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for f in self._TRANSIENT_FIELDS:
            if f in d:
                d[f] = False if isinstance(d[f], bool) else None
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotContext":
        if not isinstance(data, dict):
            return cls()
        try:
            raw_version = int(data.get("schema_version", 1))
        except Exception:
            raw_version = 1
        if raw_version < SCHEMA_VERSION:
            return cls._migrate(data, raw_version)
        if raw_version > SCHEMA_VERSION:
            log = logging.getLogger(_LOG_ROOT)
            log.critical(
                f"[MIGRATE] schéma {raw_version} > {SCHEMA_VERSION} : "
                f"contexte d'une version plus récente → HALT")
            ctx = cls._rebuild(data)
            halt_ctx(ctx, "SCHEMA_DOWNGRADE", HaltKind.MANUAL)
            return ctx
        return cls._rebuild(data)

    @classmethod
    def _migrate(cls, raw: Dict[str, Any], from_version: int) -> "BotContext":
        log = logging.getLogger(_LOG_ROOT)
        log.info(f"[MIGRATE] schéma {from_version} → {SCHEMA_VERSION}")
        try:
            ctx = cls._rebuild(raw)
            ctx.schema_version = SCHEMA_VERSION
            ctx.bot_version = VERSION_MODULE
            return ctx
        except Exception as e:
            log.critical(
                f"[MIGRATE-FAIL] {from_version}→{SCHEMA_VERSION}: {e} — "
                f"contexte préservé en mode dégradé + HALT")
            ctx = cls()
            ctx.schema_version = SCHEMA_VERSION
            for fname, klass in cls._SECTIONS:
                sec = raw.get(fname)
                if isinstance(sec, dict):
                    try:
                        setattr(ctx, fname, _dataclass_from_dict(klass, sec))
                    except Exception:
                        pass
            halt_ctx(ctx, "SCHEMA_MIGRATION_FAILED", HaltKind.MANUAL)
            return ctx

    @classmethod
    def _rebuild(cls, data: Dict[str, Any]) -> "BotContext":
        log = logging.getLogger(_LOG_ROOT)
        ctx = cls()
        try:
            ctx.schema_version = int(data.get("schema_version", SCHEMA_VERSION))
        except Exception:
            ctx.schema_version = SCHEMA_VERSION
        ctx.state = str(data.get("state", BotState.FLAT.value))
        for fname, klass in cls._SECTIONS:
            raw = data.get(fname)
            if not isinstance(raw, dict):
                continue
            valid = {f.name for f in fields(klass)}
            unknown = set(raw.keys()) - valid
            if unknown:
                log.debug(f"[REBUILD] {fname}: champs ignorés {sorted(unknown)}")
            try:
                setattr(ctx, fname, _dataclass_from_dict(klass, raw))
            except Exception as e:
                log.error(f"[REBUILD] {fname} corrompu → reset ({e})")
                setattr(ctx, fname, klass())
        try:
            lat = ctx.adaptive.signal_latencies_ms
            if not isinstance(lat, list):
                ctx.adaptive.signal_latencies_ms = []
            elif len(lat) > 2000:
                ctx.adaptive.signal_latencies_ms = lat[-500:]
                log.warning("[REBUILD] signal_latencies_ms tronqué à 500")
        except Exception:
            ctx.adaptive.signal_latencies_ms = []
        for k in cls._SCALAR_FIELDS:
            if k in data:
                try:
                    setattr(ctx, k, data[k])
                except Exception:
                    pass
        for k, cast in cls._CAST_FIELDS:
            if k in data:
                try:
                    setattr(ctx, k, cast(data[k]))
                except Exception:
                    pass
        ctx.flatten_in_progress = False
        cls._post_load_fixups(ctx, data)
        return ctx

    @staticmethod
    def _post_load_fixups(ctx: "BotContext", raw: Dict[str, Any]) -> None:
        """Rend un contexte V29.5 (schéma 10) cohérent avec V29.6."""
        p = ctx.position
        if p.in_position:
            if p.initial_amount <= 0:
                p.initial_amount = p.amount_held
            if p.cost_basis <= 0 and p.buy_price > 0:
                p.cost_basis = p.buy_price * 1.001
            if p.risk_quote_initial <= 0 and p.initial_amount > 0:
                per_unit = max(p.cost_basis - p.sl_price * 0.999,
                               p.risk_per_unit, p.buy_price * 0.001)
                p.risk_quote_initial = p.initial_amount * per_unit
        if not isinstance(p.recorded_fills, dict):
            p.recorded_fills = {}
        r = ctx.risk
        if r.halted and not r.halt_kind:
            reason = r.halt_reason or ""
            if reason.startswith("Daily DD"):
                r.halt_kind = HaltKind.DAILY_DD
            elif reason.startswith("Weekly DD"):
                r.halt_kind = HaltKind.WEEKLY_DD
                d = _parse_iso(r.halted_date) if r.halted_date else None
                r.halted_week = _week_key(d) if d else _week_key()
            else:
                r.halt_kind = HaltKind.INTEGRITY
        pf = ctx.portfolio
        if pf.losses_since_pause <= 0 and pf.consec_losses > 0 \
                and not r.paused_until:
            pf.losses_since_pause = pf.consec_losses
        b = ctx.blockchain
        if b.last_pending_tx_hash and not b.pending_tx_hashes:
            b.pending_tx_hashes = [b.last_pending_tx_hash]

    @classmethod
    def for_backtest(cls, initial_capital: float = 1000.0) -> "BotContext":
        ctx = cls()
        ctx.portfolio.paper_cash = initial_capital
        return ctx


def halt_ctx(ctx: BotContext, reason: str, kind: str = HaltKind.INTEGRITY,
             now: Optional[datetime] = None, orphan: bool = False) -> None:
    n = now or _utcnow()
    r = ctx.risk
    r.halted = True
    r.halt_reason = reason
    r.halt_kind = kind
    r.halted_date = _day_key(n)
    r.halted_week = _week_key(n)
    if orphan:
        ctx.orphan_balance = True
        ctx.orphan_balance_since = ctx.orphan_balance_since or n.isoformat()


def clear_halt(ctx: BotContext) -> None:
    r = ctx.risk
    r.halted = False
    r.halt_reason = None
    r.halt_kind = None
    r.halted_date = None
    r.halted_week = None


def is_frozen(ctx: BotContext) -> bool:
    """Gel des actions discrétionnaires (partielles, BE, trailing, time-exit)
    quand l'état est incertain. La protection reste maintenue."""
    return bool(ctx.risk.halted and ctx.risk.halt_kind in
                (HaltKind.INTEGRITY, HaltKind.MANUAL))


@dataclass
class Signal:
    action: str
    regime: str
    module: str
    tier: str
    score: int
    reject: Optional[str] = None

    @property
    def is_buy(self) -> bool:
        return self.action == "BUY"


# ══════════════════════════════════════════════════════════════════════
# SECTION 4 — INFRASTRUCTURE
# ══════════════════════════════════════════════════════════════════════

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def build_logger(cfg: Config) -> logging.Logger:
    parent = logging.getLogger(_LOG_ROOT)
    parent.handlers.clear()
    parent.setLevel(logging.INFO)
    fh = RotatingFileHandler(cfg.log_file, maxBytes=10_000_000,
                              backupCount=10, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    fh.setLevel(logging.INFO)
    parent.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"))
    sh.setLevel(logging.INFO)
    parent.addHandler(sh)
    parent.propagate = False
    env_lg = logging.getLogger(f"{_LOG_ROOT}.env")
    env_lg.handlers.clear()
    env_lg.propagate = True
    lg = logging.getLogger(f"{_LOG_ROOT}.{cfg.base}_{cfg.quote}")
    lg.setLevel(logging.DEBUG)
    lg.handlers.clear()
    lg.propagate = True
    return lg


def build_heartbeat_logger(cfg: Config) -> logging.Logger:
    lg = logging.getLogger(f"{_LOG_ROOT}.{cfg.base}_{cfg.quote}.heartbeat")
    lg.setLevel(logging.INFO)
    lg.handlers.clear()
    fh = RotatingFileHandler(cfg.heartbeat_log_file, maxBytes=5_000_000,
                             backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    fh.setLevel(logging.INFO)
    lg.addHandler(fh)
    lg.propagate = False
    return lg


class Notifier:
    """Notifications Telegram. Par défaut ASYNCHRONES (thread dédié) :
    un envoi lent ne retarde jamais une action de trading (panic, stop)."""

    def __init__(self, token: str, chat: str,
                 logger: Optional[logging.Logger] = None,
                 dedup_sec: int = 60, fail_backoff_sec: int = 30,
                 async_mode: bool = True):
        self.token = token
        self.chat = chat
        self.logger = logger
        self.dedup_sec = dedup_sec
        self.fail_backoff_sec = fail_backoff_sec
        self._last_success: Dict[str, float] = {}
        self._last_fail: Dict[str, float] = {}
        self._last_purge_ts = time.time()
        self._lock = threading.Lock()
        self._q: "queue.Queue" = queue.Queue(maxsize=500)
        self._thread: Optional[threading.Thread] = None
        if async_mode and token and chat:
            self._thread = threading.Thread(target=self._worker,
                                            name="notifier", daemon=True)
            self._thread.start()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat)

    def __call__(self, msg: str, dedup_key: Optional[str] = None,
                 critical: bool = False, sync: bool = False) -> bool:
        if critical and self.logger:
            self.logger.error(f"[NOTIFY] {msg}")
        if not self.enabled:
            if critical and self.logger:
                self.logger.error(f"[NOTIFIER-OFF] {msg}")
            return True
        key = dedup_key or hashlib.sha1(msg.encode()).hexdigest()
        now = time.time()
        with self._lock:
            if now - self._last_purge_ts > 300 or len(self._last_success) > 1000:
                cutoff = now - 2 * max(self.dedup_sec, self.fail_backoff_sec)
                self._last_success = {k: v for k, v in self._last_success.items()
                                      if v > cutoff}
                self._last_fail = {k: v for k, v in self._last_fail.items()
                                   if v > cutoff}
                self._last_purge_ts = now
            if now - self._last_fail.get(key, 0) < self.fail_backoff_sec:
                return False
            if now - self._last_success.get(key, 0) < self.dedup_sec:
                return True
        if sync or self._thread is None:
            return self._send(msg, key, critical)
        try:
            self._q.put_nowait((msg, key, critical))
            return True
        except queue.Full:
            if self.logger:
                self.logger.warning(f"[NOTIFIER-FULL] {msg[:200]}")
            return False

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            try:
                self._send(*item)
            except Exception:
                pass

    def _send(self, msg: str, key: str, critical: bool) -> bool:
        now = time.time()
        try:
            import urllib.parse
            import urllib.request
            data = urllib.parse.urlencode({"chat_id": self.chat,
                                           "text": msg}).encode()
            with urllib.request.urlopen(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data=data, timeout=5,
            ) as resp:
                body = resp.read().decode(errors="replace")
            try:
                ok = bool(json.loads(body).get("ok", False))
            except Exception:
                ok = True
        except Exception as e:
            ok = False
            if self.logger:
                level = logging.ERROR if critical else logging.WARNING
                self.logger.log(level, "[NOTIFIER-KO] "
                                + scrub_secrets(str(e), [self.token])
                                + f" — msg: {msg[:200]}")
        with self._lock:
            if ok:
                self._last_success[key] = now
            else:
                self._last_fail[key] = now
        return ok

    def close(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass
            self._thread.join(timeout)
            self._thread = None


class ProcessLock:
    """Verrou d'instance unique posé par le système d'exploitation (flock
    sous Unix, msvcrt.locking sous Windows) : il est libéré automatiquement
    si le process meurt, même brutalement. Aucun verrou « périmé » possible."""

    _HELD_ERRNOS = {errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES,
                    getattr(errno, "EDEADLK", -1), getattr(errno, "EDEADLOCK", -1)}
    # Windows : les octets verrouillés sont illisibles par les autres
    # process. On verrouille un octet loin du texte d'identité (au-delà de
    # la fin du fichier, ce que Windows autorise) pour qu'il reste lisible.
    _MSVCRT_OFFSET = 1 << 20

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.handle = None
        self._mode: Optional[str] = None

    def holder(self) -> str:
        """Identité du détenteur écrite dans le fichier (pid, machine, date)."""
        try:
            with open(self.path, encoding="utf-8") as fh:
                return fh.read().strip() or "détenteur inconnu"
        except OSError:
            return "détenteur inconnu"

    def _lock(self, handle) -> None:
        try:
            import fcntl
        except ImportError:
            fcntl = None
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._mode = "fcntl"
            return
        try:
            import msvcrt
        except ImportError:
            raise SystemExit("Aucun mécanisme de verrouillage disponible sur ce "
                             "système : démarrage refusé.")
        handle.seek(self._MSVCRT_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        self._mode = "msvcrt"

    def acquire(self) -> None:
        try:
            handle = open(self.path, "a+", encoding="utf-8")
        except OSError as e:
            raise SystemExit(f"Verrou impossible à créer ({self.path}) : "
                             f"{e.strerror or e}")
        try:
            self._lock(handle)
        except OSError as e:
            handle.close()
            if e.errno in self._HELD_ERRNOS:
                raise SystemExit(f"Une autre instance du bot tourne déjà "
                                 f"({self.holder()}) — verrou {self.path}")
            raise SystemExit(f"Verrouillage impossible sur {self.path} "
                             f"({e.strerror or e}) : ce système de fichiers ne "
                             f"gère pas les verrous (partage réseau ?). Placez "
                             f"les fichiers d'état sur un disque local.")
        except BaseException:
            handle.close()
            raise
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} machine={socket.gethostname()} "
                     f"depuis={_utcnow_iso()}\n")
        handle.flush()
        self.handle = handle

    def release(self) -> None:
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            if self._mode == "fcntl":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif self._mode == "msvcrt":
                import msvcrt
                handle.seek(self._MSVCRT_OFFSET)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            handle.close()
            self._mode = None


def acquire_instance_locks(lock_file: str, db_file: str) -> List[ProcessLock]:
    """Verrou configuré + verrou attaché à la base de données (chemin
    absolu) : deux instances ne peuvent jamais partager la même base, même
    avec des LOCK_FILE différents ou lancées depuis des dossiers différents."""
    paths: List[str] = []
    candidates = [lock_file]
    if db_file and db_file != ":memory:":
        candidates.append(db_file + ".lock")
    for p in candidates:
        if p and p != os.devnull:
            ap = os.path.abspath(p)
            if ap not in paths:
                paths.append(ap)
    locks: List[ProcessLock] = []
    try:
        for p in paths:
            lk = ProcessLock(p)
            lk.acquire()
            locks.append(lk)
    except BaseException:
        release_locks(locks)
        raise
    return locks


def release_locks(locks: List[ProcessLock]) -> None:
    for lk in reversed(locks):
        lk.release()


class Console:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    GREY = "\033[90m"
    BG_RED = "\033[41m"

    def __init__(self, enabled: bool):
        self.enabled = bool(enabled) and sys.stdout.isatty()

    def c(self, text: str, color: str) -> str:
        if not self.enabled:
            return text
        return f"{color}{text}{self.RESET}"


class Heartbeat:
    SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    SPARK = "▁▂▃▄▅▆▇█"
    REGIME_ICON = {"TREND_UP": "🟢↑", "TREND_DOWN": "🔴↓",
                    "RANGE": "🟡↔", "UNCLEAR": "⚪?"}

    def __init__(self, cfg: Config, logger: logging.Logger,
                 hb_logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger
        self.hb_logger = hb_logger
        self.console = Console(cfg.heartbeat_use_colors)
        self._start_ts = time.time()
        self._frame = 0
        self._last_equity = 0.0
        self._equity_history: deque = deque(
            maxlen=cfg.heartbeat_equity_window)
        self._last_equity_snapshot = 0.0

    def _fmt_duration(self, seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _sparkline(self, values: List[float]) -> str:
        if len(values) < 2:
            return ""
        try:
            arr = np.array(values, dtype=float)
            arr = arr[np.isfinite(arr)]
            if len(arr) < 2:
                return ""
            lo, hi = float(arr.min()), float(arr.max())
            if hi - lo < 1e-9:
                return self.SPARK[0] * len(arr)
            norm = (arr - lo) / (hi - lo)
            return "".join(
                self.SPARK[min(len(self.SPARK) - 1,
                                int(v * (len(self.SPARK) - 1)))]
                for v in norm)
        except Exception:
            return ""

    def _colored_pnl(self, value: float, fmt: str = "+.3f") -> str:
        s = f"{value:{fmt}}"
        if value > 0:
            return self.console.c(s, Console.GREEN)
        if value < 0:
            return self.console.c(s, Console.RED)
        return self.console.c(s, Console.GREY)

    def _fmt_subsystems(self, subs: Dict[str, str]) -> str:
        if not subs:
            return ""
        parts = []
        for k, v in subs.items():
            v_up = str(v).upper()
            parts_set = set(v_up.replace("+", "|").split("|"))
            bad = parts_set & {"KO", "HALTED", "UNPROTECTED", "LOST", "NONE",
                                "HALT", "ORPHAN", "TX_PENDING", "PENDING",
                                "FROZEN"}
            good = parts_set & {"OK", "OCO", "STOP_ONLY"}
            off = parts_set & {"OFF", "INIT", "NOOP", "—", ""}
            if bad:
                parts.append(f"{k}={self.console.c(v, Console.RED)}")
            elif good:
                parts.append(f"{k}={self.console.c(v, Console.GREEN)}")
            elif off:
                parts.append(f"{k}={self.console.c(v, Console.GREY)}")
            else:
                parts.append(f"{k}={self.console.c(v, Console.YELLOW)}")
        return " ".join(parts)

    def tick(self, ctx: BotContext, regime: str, equity: Optional[float],
             live_price: float, adaptive: Optional[AdaptiveState] = None,
             benchmark_start_equity: Optional[float] = None,
             subsystems: Optional[Dict[str, str]] = None) -> str:
        if equity is not None:
            if abs(equity - self._last_equity_snapshot) > 1e-9:
                self._equity_history.append(equity)
                self._last_equity_snapshot = equity
        eq_delta = 0.0
        if equity is not None and self._last_equity > 0:
            eq_delta = equity - self._last_equity
        if equity is not None:
            self._last_equity = equity
        total_pnl_pct = 0.0
        if (equity is not None and benchmark_start_equity
                and benchmark_start_equity > 0):
            total_pnl_pct = (equity / benchmark_start_equity - 1) * 100
        self._frame = (self._frame + 1) % len(self.SPINNER)
        spin = self.SPINNER[self._frame] if self.cfg.heartbeat_spinner else "*"
        uptime = self._fmt_duration(time.time() - self._start_ts)
        eq_str = "n/a" if equity is None else f"{equity:.2f}"
        delta_str = ""
        if eq_delta != 0.0:
            sign = "+" if eq_delta > 0 else ""
            delta_str = " (" + self._colored_pnl(eq_delta, f"{sign}.3f") + ")"
        regime_icon = self.REGIME_ICON.get(regime, "⚪")
        pos_str = "FLAT"
        if ctx.position.in_position:
            pos_str = f"LONG {ctx.position.module}/{ctx.position.tier}"
        flags = []
        if ctx.risk.halted:
            flags.append(self.console.c(
                f"HALTED[{ctx.risk.halt_kind or '?'}]", Console.BG_RED))
        if ctx.risk.paused_until:
            flags.append(self.console.c("PAUSED", Console.YELLOW))
        if ctx.orphan_balance:
            flags.append(self.console.c("ORPHAN", Console.YELLOW))
        if ctx.pending_order:
            flags.append(self.console.c("ORDER_PENDING", Console.YELLOW))
        if ctx.blockchain.last_pending_tx_hash:
            flags.append(self.console.c("TX_PENDING", Console.CYAN))
        flag_str = (" " + " ".join(flags)) if flags else ""
        line1 = (f"{spin} #{ctx.cycle_count:>5} {uptime} "
                 f"eq={eq_str}{delta_str} px={live_price:.5f} "
                 f"{regime_icon} {regime} | {pos_str}{flag_str}")
        wins = int(ctx.portfolio.stats_wins)
        losses = int(ctx.portfolio.stats_losses)
        total_trades = wins + losses
        wr = (wins / total_trades * 100) if total_trades else 0.0
        total_pnl = float(ctx.portfolio.stats_total_pnl)
        dd_str = "-"
        if ctx.risk.daily_start_equity and equity:
            dd = (equity / ctx.risk.daily_start_equity - 1) * 100
            dd_str = f"{dd:+.2f}%"
        consec_str = ""
        if ctx.portfolio.consec_losses > 0:
            consec_str = self.console.c(
                f" consec={ctx.portfolio.consec_losses}",
                Console.RED if ctx.portfolio.consec_losses >= 3
                else Console.YELLOW)
        line2 = (f"  ├─ PnL: {self._colored_pnl(total_pnl, '+.3f')} "
                 f"({total_pnl_pct:+.2f}%) W/L: {wins}/{losses} "
                 f"({wr:.0f}%) DD(j): {dd_str}{consec_str}")
        adapt_str = ""
        if adaptive:
            adapt_str = (f"fresh={adaptive.current_freshness_min}min "
                         f"atr={adaptive.current_atr_min_pct*100:.2f}% "
                         f"consec={adaptive.current_consec_pause}")
        spark = self._sparkline(list(self._equity_history))
        spark_str = f" equity: {spark}" if spark else ""
        line3 = f"  ├─{spark_str}  {adapt_str}"
        line4 = "  └─ FLAT"
        if ctx.position.in_position:
            p = ctx.position
            pnl_live = (live_price * (1 - self.cfg.fee_rate) - p.cost_basis) \
                * p.amount_held + p.realized_pnl
            pnl_str = self._colored_pnl(pnl_live, "+.4f")
            opened = _parse_iso(p.opened_at)
            held_sec = (_utcnow() - opened).total_seconds() if opened else 0.0
            held_str = self._fmt_duration(held_sec)
            sl_dist_pct = ((p.buy_price - p.sl_price) / p.buy_price * 100
                           if p.buy_price else 0.0)
            be_flag = self.console.c(" BE", Console.GREEN) \
                if p.break_even_done else ""
            line4 = (f"  └─ {p.module}/{p.tier} entry={p.buy_price:.5f} "
                     f"sl={p.sl_price:.5f} ({-sl_dist_pct:+.2f}%) "
                     f"tp={p.tp_price:.5f} held={p.amount_held:.2f} "
                     f"pnl={pnl_str} age={held_str}{be_flag}")
        block_lines = [line1, line2, line3]
        if subsystems:
            block_lines.append(f"  ├─ {self._fmt_subsystems(subsystems)}")
        block_lines.append(line4)
        block = "\n".join(block_lines)
        plain = re.sub(r"\033\[[0-9;]*m", "", block)
        self.hb_logger.info(plain)
        if sys.stdout.isatty():
            print(block, flush=True)
        return plain


# ══════════════════════════════════════════════════════════════════════
# SECTION 5 — STORE
# ══════════════════════════════════════════════════════════════════════

class Store:
    def __init__(self, path: str, logger: logging.Logger):
        self.path = path
        self.logger = logger
        self.conn: Optional[sqlite3.Connection] = None
        self._digests: Dict[str, str] = {}
        self._closed = False
        self.healthy = True
        self._open()
        self._init_schema()

    def _open(self) -> None:
        self.conn = sqlite3.connect(self.path, isolation_level=None,
                                    timeout=30, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA wal_autocheckpoint=1000")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Store is closed")
        if self.conn is None:
            self._open()
            self._init_schema()

    @contextmanager
    def transaction(self, immediate: bool = True):
        self._ensure_open()
        mode = "BEGIN IMMEDIATE" if immediate else "BEGIN"
        for attempt in range(3):
            try:
                self.conn.execute(mode)
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 2:
                    time.sleep(0.1 * (2 ** attempt))
                    continue
                raise
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            try:
                self.conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def _exec(self, sql: str, params: tuple = (), retries: int = 3) -> None:
        self._ensure_open()
        for attempt in range(retries):
            try:
                self.conn.execute(sql, params)
                return
            except sqlite3.IntegrityError as e:
                self.logger.error(f"[SQL-INTEG] {e} | sql={sql[:80]}")
                raise
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < retries - 1:
                    time.sleep(0.1 * (2 ** attempt))
                    continue
                raise

    def checkpoint(self, mode: str = "PASSIVE") -> None:
        if mode not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            mode = "PASSIVE"
        try:
            self._ensure_open()
            self.conn.execute(f"PRAGMA wal_checkpoint({mode})")
        except Exception as e:
            self.logger.warning(f"[SQL-CKPT] {e}")

    def _migrate_columns(self, table: str, columns: Dict[str, str]) -> None:
        if not table.isidentifier():
            raise RuntimeError(f"Nom de table invalide: {table!r}")
        try:
            existing = {row[1] for row in self.conn.execute(
                f"PRAGMA table_info({table})").fetchall()}
        except Exception as e:
            raise RuntimeError(f"PRAGMA table_info({table}) KO: {e}") from e
        to_add = [(col, ddl) for col, ddl in columns.items()
                  if col not in existing]
        if not to_add:
            return
        for col, _ in to_add:
            if not col.isidentifier():
                raise RuntimeError(f"Nom de colonne invalide: {col!r}")
        try:
            with self.transaction(immediate=True):
                for col, ddl in to_add:
                    self.logger.info(f"[SQL-MIGRATE] {table}: +{col}")
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        except sqlite3.Error as e:
            raise RuntimeError(
                f"Migration {table} impossible ({to_add}): {e}") from e

    def _init_schema(self) -> None:
        c = self.conn
        c.execute("CREATE TABLE IF NOT EXISTS kv "
                  "(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, event TEXT NOT NULL, severity TEXT NOT NULL,
            payload TEXT, mode TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, entry REAL, exit REAL, amount REAL,
            pnl REAL, pnl_pct REAL, r_mult REAL,
            cumulative_pnl REAL, cumulative_r REAL,
            barrier TEXT, module TEXT, tier TEXT, score INTEGER, entry_obi REAL,
            entry_order_id TEXT, exit_order_id TEXT,
            regime TEXT, eff_risk_pct REAL, mode TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, signal TEXT, module TEXT, regime TEXT,
            score INTEGER, reject TEXT, payload TEXT, mode TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS equity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, equity REAL, cash REAL, base_qty REAL,
            base_price REAL, halted INTEGER, mode TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS blockchain_txs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, chain TEXT NOT NULL, chain_id INTEGER NOT NULL,
            tx_hash TEXT NOT NULL, from_addr TEXT, to_addr TEXT,
            value_eth REAL, token_addr TEXT, token_amount REAL,
            gas_used INTEGER, gas_price_wei TEXT, status INTEGER,
            dry_run INTEGER NOT NULL, mode TEXT NOT NULL,
            rbf INTEGER DEFAULT 0, pending INTEGER DEFAULT 0)""")
        self._migrate_columns("blockchain_txs", {
            "rbf": "INTEGER DEFAULT 0",
            "pending": "INTEGER DEFAULT 0",
        })
        for idx in (
            "CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts)",
            "CREATE INDEX IF NOT EXISTS idx_trades_module ON trades(module)",
            "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
            "CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts)",
            "CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity(ts)",
            "CREATE INDEX IF NOT EXISTS idx_bctx_hash ON blockchain_txs(tx_hash)",
            "CREATE INDEX IF NOT EXISTS idx_bctx_ts ON blockchain_txs(ts)",
        ):
            c.execute(idx)

    def load_context(self, key: str = "context") -> Optional[BotContext]:
        """Retourne None si aucun contexte. Lève si le contexte existe mais
        est illisible (fail-closed : on ne repart pas d'un état vierge
        alors qu'une position peut être ouverte)."""
        self._ensure_open()
        row = self.conn.execute(
            "SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        try:
            return BotContext.from_dict(json.loads(row[0]))
        except Exception as e:
            self.logger.critical(f"[DB] Context corrompu: {e}")
            raise RuntimeError(f"Contexte DB illisible: {e}") from e

    def save_context(self, ctx: BotContext, mode: str,
                     force: bool = False, key: str = "context") -> bool:
        try:
            payload = json.dumps(ctx.to_dict(), sort_keys=True,
                                 separators=(",", ":"), default=str)
        except Exception as e:
            self.logger.critical(f"[DB] sérialisation contexte KO: {e}")
            self.healthy = False
            return False
        digest = hashlib.sha256(payload.encode()).hexdigest()
        if not force and digest == self._digests.get(key):
            return True
        try:
            with self.transaction(immediate=True):
                self.conn.execute(
                    "INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)",
                    (key, payload))
            self._digests[key] = digest
            if not self.healthy:
                self.logger.warning("[DB] save_context rétabli")
            self.healthy = True
            return True
        except Exception as e:
            self.logger.critical(f"[DB] save_context KO: {e}")
            self.healthy = False
            return False

    def get_kv(self, key: str) -> Optional[Dict[str, Any]]:
        self._ensure_open()
        row = self.conn.execute("SELECT value FROM kv WHERE key=?",
                                (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_kv(self, key: str, value: Dict[str, Any]) -> bool:
        try:
            payload = json.dumps(value, sort_keys=True, default=str)
            with self.transaction(immediate=True):
                self.conn.execute(
                    "INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)",
                    (key, payload))
            return True
        except Exception as e:
            self.logger.critical(f"[DB] set_kv({key}) KO: {e}")
            self.healthy = False
            return False

    def log_event(self, event: str, severity: str,
                  payload: Dict[str, Any], mode: str,
                  durable: bool = False) -> None:
        try:
            self._exec(
                "INSERT INTO events(ts,event,severity,payload,mode)"
                " VALUES(?,?,?,?,?)",
                (_utcnow_iso(), event, severity,
                 json.dumps(payload, default=str), mode))
            if durable:
                self.checkpoint(mode="FULL")
        except Exception as e:
            self.logger.warning(f"[DB] event log KO: {e}")

    def log_trade(self, payload: Dict[str, Any], mode: str) -> None:
        try:
            self._exec(
                """INSERT INTO trades(ts,entry,exit,amount,pnl,pnl_pct,r_mult,
                   cumulative_pnl,cumulative_r,barrier,module,tier,score,
                   entry_obi,entry_order_id,exit_order_id,regime,eff_risk_pct,
                   mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (_utcnow_iso(),
                 payload.get("entry"), payload.get("exit"),
                 payload.get("amount"), payload.get("pnl"),
                 payload.get("pnl_pct"), payload.get("r_mult"),
                 payload.get("cumulative_pnl"), payload.get("cumulative_r"),
                 payload.get("barrier"), payload.get("module") or "",
                 payload.get("tier") or "", int(payload.get("score") or 0),
                 payload.get("entry_obi"), payload.get("entry_order_id"),
                 payload.get("exit_order_id"), payload.get("regime") or "",
                 payload.get("eff_risk_pct"), mode))
        except Exception as e:
            self.logger.warning(f"[DB] trade log KO: {e}")

    def log_decision(self, payload: Dict[str, Any], mode: str) -> None:
        try:
            self._exec(
                """INSERT INTO decisions(ts,signal,module,regime,score,
                   reject,payload,mode) VALUES(?,?,?,?,?,?,?,?)""",
                (_utcnow_iso(),
                 payload.get("signal") or "", payload.get("module") or "",
                 payload.get("regime") or "", int(payload.get("score") or 0),
                 payload.get("reject") or "",
                 json.dumps(payload, default=str), mode))
        except Exception as e:
            self.logger.warning(f"[DB] decision log KO: {e}")

    def log_equity(self, equity: float, cash: float, base_qty: float,
                   base_price: float, halted: bool, mode: str) -> None:
        try:
            self._exec(
                "INSERT INTO equity(ts,equity,cash,base_qty,base_price,"
                "halted,mode) VALUES(?,?,?,?,?,?,?)",
                (_utcnow_iso(), equity, cash,
                 base_qty, base_price, int(halted), mode))
        except Exception as e:
            self.logger.warning(f"[DB] equity log KO: {e}")

    def log_blockchain_tx(self, payload: Dict[str, Any], mode: str) -> None:
        try:
            self._exec(
                """INSERT INTO blockchain_txs(ts,chain,chain_id,tx_hash,
                   from_addr,to_addr,value_eth,token_addr,token_amount,
                   gas_used,gas_price_wei,status,dry_run,mode,rbf,pending)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (_utcnow_iso(),
                 payload.get("chain"), int(payload.get("chain_id")),
                 payload.get("tx_hash"), payload.get("from_addr"),
                 payload.get("to_addr"), payload.get("value_eth"),
                 payload.get("token_addr"), payload.get("token_amount"),
                 payload.get("gas_used"), payload.get("gas_price_wei"),
                 payload.get("status"), int(payload.get("dry_run", 1)), mode,
                 int(payload.get("rbf", 0)), int(payload.get("pending", 0))))
        except Exception as e:
            self.logger.warning(f"[DB] blockchain tx log KO: {e}")

    def log_panic_sequence(self, reason: str, extra: Dict[str, Any],
                            mode: str) -> None:
        try:
            with self.transaction(immediate=True):
                self.conn.execute(
                    "INSERT INTO events(ts,event,severity,payload,mode)"
                    " VALUES(?,?,?,?,?)",
                    (_utcnow_iso(),
                     "panic_flatten", "CRITICAL",
                     json.dumps({"reason": reason, **extra}, default=str),
                     mode))
            self.checkpoint(mode="FULL")
        except Exception as e:
            self.logger.warning(f"[DB] panic sequence KO: {e}")

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self.conn is not None:
                try:
                    self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
                self.conn.close()
        except Exception:
            pass
        self.conn = None
        self._closed = True


# ══════════════════════════════════════════════════════════════════════
# SECTION 6 — EXCHANGE ADAPTER (Binance Spot)
# ══════════════════════════════════════════════════════════════════════

class AmbiguousOrder(Exception):
    """L'ordre a peut-être été exécuté mais son état est inconnaissable
    pour l'instant. `client_id` permet de le résoudre plus tard."""

    def __init__(self, message: str, client_id: Optional[str] = None):
        super().__init__(message)
        self.client_id = client_id


@dataclass
class MarketRules:
    min_amount: float = 0.0
    max_amount: Optional[float] = None
    min_cost: float = 10.0
    step_size: float = 0.0
    tick_size: float = 0.0
    order_types: Tuple[str, ...] = ()
    oco_allowed: bool = True


@dataclass
class OrderResult:
    order_id: str
    client_id: str = ""
    status: str = ""          # open | closed | canceled
    filled: float = 0.0       # base (brut)
    average: float = 0.0      # quote/base
    cost: float = 0.0         # quote
    fee_base: float = 0.0
    fee_quote: float = 0.0
    fee_other: float = 0.0    # frais payés dans un autre actif (ex: BNB)
    fee_known: bool = False
    order_type: str = ""
    side: str = ""
    list_id: Optional[str] = None
    amount: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.status == "open"


_ORDER_STATUS_MAP = {
    "new": "open", "open": "open", "partially_filled": "open",
    "pending_new": "open", "pending_cancel": "open",
    "filled": "closed", "closed": "closed",
    "canceled": "canceled", "cancelled": "canceled", "expired": "canceled",
    "rejected": "canceled", "expired_in_match": "canceled",
}


_NUMERIC_STR = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def _fnum(x: Any) -> float:
    """Valeur d'API (nombre, chaîne, None…) → float fini, 0.0 si absente ou
    invalide. Les valeurs absentes sont filtrées AVANT float() : aucune
    exception n'est levée (le débogueur ne s'arrête plus ici)."""
    if x is None:
        return 0.0
    if isinstance(x, str):
        x = x.strip()
        if not _NUMERIC_STR.fullmatch(x):
            return 0.0
    elif not isinstance(x, (int, float, Decimal, np.number)):
        return 0.0
    try:
        v = float(x)
    except OverflowError:          # entier trop grand pour un float
        return 0.0
    return v if math.isfinite(v) else 0.0


class ExchangeAdapter:
    def __init__(self, cfg: Config, logger: logging.Logger,
                 exchange: Any = None):
        self.cfg = cfg
        self.logger = logger
        if exchange is not None:
            self.exchange = exchange
        else:
            self.exchange = make_binance(
                os.environ.get("BINANCE_API_KEY", "").strip(),
                os.environ.get("BINANCE_API_SECRET", "").strip(),
                cfg.binance_testnet)
        self.sleep: Callable[[float], None] = time.sleep
        self.rules = MarketRules()
        self._market_id = ""
        self._paper_ctx: Optional[BotContext] = None
        self._balance_cache: Dict[str, Any] = {"ts": 0.0, "free": {}, "total": {}}
        self._equity_cache: Dict[str, Any] = {"ts": 0.0, "value": None}
        self._l2_cache = {"ts": 0.0, "micro": None, "obi": 0.0, "spread": 1.0}
        self._htf_cache = {"ts": 0.0, "bias": None, "error_count": 0}
        self._btc_cache = {"ts": 0.0, "bias": None, "error_count": 0}
        self._btc_vol_cache = {"ts": 0.0, "vol": None, "error_count": 0}

    # ---------- Marché ----------

    def load_markets(self) -> None:
        self.exchange.load_markets()
        if self.cfg.symbol not in self.exchange.markets:
            raise RuntimeError(f"Marché {self.cfg.symbol} indisponible.")
        m = self.exchange.market(self.cfg.symbol)
        if not m.get("spot", True):
            raise RuntimeError(f"{self.cfg.symbol} n'est pas Spot.")
        if m.get("active") is False:
            raise RuntimeError(f"{self.cfg.symbol} inactif.")
        self._market_id = m["id"]
        limits = m.get("limits", {}) or {}
        info = m.get("info", {}) or {}
        self.rules.min_amount = float(
            (limits.get("amount") or {}).get("min") or 0)
        self.rules.max_amount = (limits.get("amount") or {}).get("max")
        self.rules.min_cost = float(
            (limits.get("cost") or {}).get("min") or 0)
        for f in (info.get("filters", []) or []):
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                self.rules.step_size = float(f.get("stepSize") or 0)
                self.rules.min_amount = max(
                    self.rules.min_amount, float(f.get("minQty") or 0))
            elif ft == "PRICE_FILTER":
                self.rules.tick_size = float(f.get("tickSize") or 0)
            elif ft in {"MIN_NOTIONAL", "NOTIONAL"}:
                if f.get("minNotional") is not None:
                    self.rules.min_cost = max(
                        self.rules.min_cost, float(f["minNotional"]))
        if self.rules.min_cost <= 0:
            self.rules.min_cost = 10.0
        self.rules.order_types = tuple(
            str(t).upper() for t in (info.get("orderTypes") or ()))
        self.rules.oco_allowed = bool(info.get("ocoAllowed", True))

    @property
    def stop_order_type(self) -> str:
        """Type stop Spot valide pour ce marché (jamais STOP_LOSS_MARKET,
        qui n'existe pas en Spot)."""
        types = set(self.rules.order_types)
        if not types or "STOP_LOSS" in types:
            return "STOP_LOSS"
        if "STOP_LOSS_LIMIT" in types:
            return "STOP_LOSS_LIMIT"
        raise RuntimeError(f"Aucun type stop Spot supporté: {sorted(types)}")

    def round_amount(self, amount: float) -> float:
        if amount <= 0:
            return 0.0
        if self.rules.step_size > 0:
            return float(_dec_round(amount, self.rules.step_size, ROUND_DOWN))
        try:
            return float(self.exchange.amount_to_precision(
                self.cfg.symbol, amount))
        except Exception:
            return amount

    def round_price(self, price: float, direction: str = "nearest") -> float:
        if price <= 0:
            return 0.0
        if self.rules.tick_size > 0:
            rounding = {"down": ROUND_DOWN, "up": ROUND_UP}.get(direction)
            if rounding is not None:
                return float(_dec_round(price, self.rules.tick_size, rounding))
        try:
            return float(self.exchange.price_to_precision(
                self.cfg.symbol, price))
        except Exception:
            return price

    def _qty_str(self, amount: float) -> str:
        try:
            return str(self.exchange.amount_to_precision(self.cfg.symbol, amount))
        except Exception:
            return format(Decimal(str(amount)).normalize(), "f")

    def _px_str(self, price: float) -> str:
        try:
            return str(self.exchange.price_to_precision(self.cfg.symbol, price))
        except Exception:
            return format(Decimal(str(price)).normalize(), "f")

    def min_notional(self) -> float:
        return float(self.rules.min_cost or 10.0)

    # ---------- Soldes (le solde exchange fait foi) ----------

    def bind_paper_context(self, ctx: BotContext) -> None:
        self._paper_ctx = ctx

    def refresh_balances(self, ctx: Optional[BotContext] = None,
                         force: bool = False) -> None:
        """Lève en cas d'échec API (fail-closed) : un solde inconnu n'est
        jamais remplacé par 0."""
        now = time.time()
        if (not force and now - self._balance_cache["ts"]
                <= self.cfg.balance_cache_ttl_sec):
            return
        if self.cfg.run_mode == "paper":
            pc = ctx or self._paper_ctx
            if pc is None:
                raise RuntimeError("Solde paper demandé sans contexte.")
            free = {self.cfg.quote: float(pc.portfolio.paper_cash),
                    self.cfg.base: float(pc.portfolio.paper_base)}
            self._balance_cache = {"ts": now, "free": free,
                                   "total": dict(free)}
            return
        bal = self.exchange.fetch_balance()
        self._balance_cache = {"ts": now,
                               "free": dict(bal.get("free") or {}),
                               "total": dict(bal.get("total") or {})}

    def invalidate_balances(self) -> None:
        self._balance_cache["ts"] = 0.0
        self._equity_cache["ts"] = 0.0

    def get_free_balance(self, currency: str,
                         ctx: Optional[BotContext] = None,
                         force: bool = False) -> float:
        self.refresh_balances(ctx, force=force)
        return float(self._balance_cache["free"].get(currency, 0) or 0)

    def get_total_balance(self, currency: str,
                          ctx: Optional[BotContext] = None,
                          force: bool = False) -> float:
        self.refresh_balances(ctx, force=force)
        return float(self._balance_cache["total"].get(currency, 0) or 0)

    def _reserve(self) -> float:
        return (self.cfg.external_base_reserve
                if self.cfg.run_mode == "live" else 0.0)

    def bot_free_base(self, ctx: Optional[BotContext] = None,
                      force: bool = True) -> float:
        """Base librement vendable par le bot (hors réserve externe)."""
        free = self.get_free_balance(self.cfg.base, ctx, force=force)
        return max(0.0, free - self._reserve())

    def bot_total_base(self, ctx: Optional[BotContext] = None,
                       force: bool = False) -> float:
        total = self.get_total_balance(self.cfg.base, ctx, force=force)
        return max(0.0, total - self._reserve())

    def get_ticker(self) -> Dict[str, float]:
        t = self.exchange.fetch_ticker(self.cfg.symbol)
        last = _fnum(t.get("last"))
        bid = _fnum(t.get("bid")) or last
        ask = _fnum(t.get("ask")) or last
        if last <= 0 and bid > 0 and ask > 0:
            last = (bid + ask) / 2
        if last <= 0:
            raise ccxt.ExchangeError(f"Ticker {self.cfg.symbol} illisible")
        return {"bid": bid, "ask": ask, "last": last}

    def get_equity(self, ctx: BotContext,
                   force: bool = False) -> Optional[float]:
        now = time.time()
        if (not force and self._equity_cache["value"] is not None
                and now - self._equity_cache["ts"]
                < self.cfg.equity_cache_ttl_sec):
            return float(self._equity_cache["value"])
        try:
            last = self.get_ticker()["last"]
            if self.cfg.run_mode == "paper":
                eq = (float(ctx.portfolio.paper_cash)
                      + float(ctx.portfolio.paper_base) * last)
            else:
                bal = self.exchange.fetch_balance()
                total = bal.get("total", {}) or {}
                cash = _fnum(total.get(self.cfg.quote))
                base = max(0.0, _fnum(total.get(self.cfg.base)) - self._reserve())
                eq = cash + base * last
            self._equity_cache = {"ts": now, "value": float(eq)}
            return float(eq)
        except Exception as e:
            self.logger.warning(f"[CEX] equity KO: {e}")
            return None

    def get_l2_state(self, force: bool = False
                     ) -> Tuple[Optional[float], float, float]:
        if not self.cfg.use_l2_filter and not self.cfg.use_smart_buy:
            return None, 0.0, 1.0
        now = time.time()
        if (not force and now - self._l2_cache["ts"]
                < self.cfg.l2_cache_ttl_sec):
            return (self._l2_cache["micro"], self._l2_cache["obi"],
                    self._l2_cache["spread"])
        try:
            ob = self.exchange.fetch_order_book(self.cfg.symbol,
                                                 limit=self.cfg.l2_depth)
            bids = ob.get("bids") or []
            asks = ob.get("asks") or []
            if not bids or not asks:
                return None, 0.0, 1.0
            best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
            if best_bid <= 0:
                return None, 0.0, 1.0
            spread = (best_ask - best_bid) / best_bid
            bid_notional = sum(float(p) * float(q) for p, q, *_ in bids)
            ask_notional = sum(float(p) * float(q) for p, q, *_ in asks)
            total = bid_notional + ask_notional
            obi = 0.0 if total <= 0 else (bid_notional - ask_notional) / total
            bid_qty = sum(float(q) for _, q, *_ in bids)
            ask_qty = sum(float(q) for _, q, *_ in asks)
            qt = bid_qty + ask_qty
            micro = (best_bid if qt <= 0
                     else best_bid * (ask_qty / qt) + best_ask * (bid_qty / qt))
            self._l2_cache.update({"ts": now, "micro": micro, "obi": obi,
                                    "spread": spread})
            return micro, obi, spread
        except Exception as e:
            self.logger.warning(f"[CEX] L2 KO: {e}")
            return None, 0.0, 1.0

    def fetch_ohlcv(self, timeframe: str, limit: int = 400) -> pd.DataFrame:
        return self.fetch_ohlcv_htf(self.cfg.symbol, timeframe, limit)

    def fetch_ohlcv_htf(self, symbol: str, timeframe: str,
                        limit: int) -> pd.DataFrame:
        bars = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        cols = ["ts", "open", "high", "low", "close", "volume"]
        if not bars:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame([b[:6] for b in bars], columns=cols)

    def _raw(self, *names: str):
        for name in names:
            fn = getattr(self.exchange, name, None)
            if callable(fn):
                return fn
        return None

    # ---------- Ordres : parsing ----------

    def _parse_order(self, order: Dict[str, Any]) -> OrderResult:
        if not isinstance(order, dict):
            raise ccxt.ExchangeError(f"Ordre illisible: {order!r}")
        info = order.get("info") or {}
        if "orderId" in order and "id" not in order:
            info = order
        oid = order.get("id")
        if oid is None:
            oid = info.get("orderId")
        cid = order.get("clientOrderId") or info.get("clientOrderId") or ""
        raw_status = str(order.get("status") or info.get("status") or "").lower()
        status = _ORDER_STATUS_MAP.get(raw_status, raw_status)
        filled = _fnum(order.get("filled")) or _fnum(info.get("executedQty"))
        cost = _fnum(order.get("cost")) or _fnum(info.get("cummulativeQuoteQty"))
        avg = _fnum(order.get("average")) or _fnum(info.get("avgPrice"))
        if avg <= 0 and filled > 0 and cost > 0:
            avg = cost / filled
        fee_base = fee_quote = fee_other = 0.0
        fee_known = False
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        for f in fees:
            if not f or f.get("cost") is None:
                continue
            fee_known = True
            c = _fnum(f.get("cost"))
            cur = f.get("currency")
            if cur == self.cfg.base:
                fee_base += c
            elif cur == self.cfg.quote:
                fee_quote += c
            else:
                fee_other += c
        if not fee_known:
            for fl in info.get("fills") or []:
                fee_known = True
                c = _fnum(fl.get("commission"))
                cur = fl.get("commissionAsset")
                if cur == self.cfg.base:
                    fee_base += c
                elif cur == self.cfg.quote:
                    fee_quote += c
                else:
                    fee_other += c
        if filled <= 0:
            fee_known = True
        lid = info.get("orderListId", order.get("orderListId"))
        list_id = None if lid in (None, -1, "-1") else str(lid)
        return OrderResult(
            order_id=str(oid) if oid is not None else "",
            client_id=str(cid), status=status, filled=filled,
            average=avg, cost=cost, fee_base=fee_base, fee_quote=fee_quote,
            fee_other=fee_other, fee_known=fee_known,
            order_type=str(order.get("type") or info.get("type") or "").upper(),
            side=str(order.get("side") or info.get("side") or "").lower(),
            list_id=list_id,
            amount=_fnum(order.get("amount")) or _fnum(info.get("origQty")),
            price=_fnum(order.get("price")) or _fnum(info.get("price")),
            stop_price=(_fnum(order.get("stopPrice"))
                        or _fnum(order.get("triggerPrice"))
                        or _fnum(info.get("stopPrice"))))

    def fetch_order_result(self, order_id: Optional[str] = None,
                           client_id: Optional[str] = None) -> OrderResult:
        params: Dict[str, Any] = {}
        if client_id and not order_id:
            params["origClientOrderId"] = client_id
        o = self.exchange.fetch_order(str(order_id) if order_id else "",
                                      self.cfg.symbol, params)
        return self._parse_order(o)

    def fetch_order(self, order_id: str) -> Dict[str, Any]:
        return self.exchange.fetch_order(order_id, self.cfg.symbol)

    def _lookup_by_cid(self, cid: str, attempts: int = 3
                       ) -> Optional[OrderResult]:
        for i in range(attempts):
            try:
                return self.fetch_order_result(client_id=cid)
            except ccxt.OrderNotFound:
                pass
            except Exception as e:
                self.logger.warning(f"[ORD] lookup {cid} KO: {e}")
            self.sleep(0.5 * (i + 1))
        return None

    # ---------- Ordres : envoi ----------

    def new_client_id(self, prefix: str) -> str:
        return _client_id(prefix)

    def _submit(self, otype: str, side: str, amount: float,
                price: Optional[float], extra: Optional[Dict[str, Any]],
                cid: str) -> OrderResult:
        params = {"newClientOrderId": cid, "newOrderRespType": "FULL"}
        params.update(extra or {})
        try:
            try:
                o = self.exchange.create_order(self.cfg.symbol, otype, side,
                                               amount, price, dict(params))
            except ccxt.InvalidNonce as e:
                # -1021 : horodatage hors de la fenêtre de réception. Binance
                # rejette la requête AVANT tout traitement : on resynchronise
                # l'horloge et on renvoie une fois le même ordre (même
                # client-id, donc jamais de doublon).
                offset = resync_clock(self.exchange)
                self.logger.warning(f"[ORD] horodatage refusé ({e}) → "
                                    f"horloge resynchronisée ({offset} ms), "
                                    f"nouvel envoi de {cid}")
                o = self.exchange.create_order(self.cfg.symbol, otype, side,
                                               amount, price, dict(params))
        except ccxt.InvalidNonce as e:
            self.invalidate_balances()
            raise ccxt.InvalidOrder(f"Ordre rejeté (horodatage): {e}") from e
        except ccxt.OperationFailed as e:
            # Réseau, 5xx, -1007 et -1001 : Binance documente ces réponses
            # comme « statut d'exécution INCONNU » (l'ordre a pu passer).
            # ccxt classe -1001 en OperationFailed, hors NetworkError.
            self.invalidate_balances()
            self.logger.warning(
                f"[ORD] {otype} {side} statut inconnu ({type(e).__name__}) "
                f"→ recherche {cid}")
            found = self._lookup_by_cid(cid)
            if found is None:
                raise AmbiguousOrder(f"{otype} {side} ambigu: {cid}", cid) from e
            return found
        self.invalidate_balances()
        res = self._parse_order(o)
        if not res.client_id:
            res.client_id = cid
        return res

    def _complete_market(self, res: OrderResult, cid: str) -> OrderResult:
        for i in range(4):
            if res.status in ("closed", "canceled") and (
                    res.filled <= 0 or res.average > 0):
                return res
            self.sleep(0.3 * (i + 1))
            try:
                res = self.fetch_order_result(order_id=res.order_id or None,
                                              client_id=cid)
            except Exception as e:
                self.logger.warning(f"[ORD] relecture {cid} KO: {e}")
        if res.filled > 0 and res.average > 0:
            return res
        raise AmbiguousOrder(f"MARKET non vérifiable: {cid}", cid)

    def market_buy(self, amount: float, cid: str) -> OrderResult:
        amount = self.round_amount(amount)
        if amount <= 0:
            raise ValueError("Amount <= 0")
        ref = self.get_ticker()["ask"]
        if amount * ref < self.min_notional():
            raise ValueError("Sous min_notional")
        res = self._submit("market", "buy", amount, None, None, cid)
        return self._complete_market(res, cid)

    def market_sell(self, amount: float, cid: str,
                    ref_price: Optional[float] = None) -> OrderResult:
        amount = self.round_amount(amount)
        if amount <= 0:
            raise ValueError("Amount <= 0")
        ref = ref_price or self.get_ticker()["bid"]
        if amount * ref < self.min_notional():
            raise ValueError("Sous min_notional")
        res = self._submit("market", "sell", amount, None, None, cid)
        return self._complete_market(res, cid)

    def limit_buy(self, amount: float, price: float, cid: str) -> OrderResult:
        amount = self.round_amount(amount)
        price = self.round_price(price, "down")
        if amount <= 0 or price <= 0:
            raise ValueError("limit_buy: amount/price invalides")
        return self._submit("limit", "buy", amount, price,
                            {"timeInForce": "GTC"}, cid)

    def place_stop_loss(self, amount: float, stop_price: float,
                        cid: str) -> OrderResult:
        amount = self.round_amount(amount)
        stop = self.round_price(stop_price, "down")
        if amount <= 0 or stop <= 0:
            raise ValueError("stop: amount/stop invalides")
        otype = self.stop_order_type
        if otype == "STOP_LOSS":
            return self._submit("STOP_LOSS", "sell", amount, None,
                                {"stopPrice": stop}, cid)
        limit = self.round_price(stop * (1 - self.cfg.stop_limit_offset_pct),
                                 "down")
        return self._submit("STOP_LOSS_LIMIT", "sell", amount, limit,
                            {"stopPrice": stop, "timeInForce": "GTC"}, cid)

    def cancel_order(self, order_id: Optional[str] = None,
                     client_id: Optional[str] = None) -> bool:
        """True si l'ordre est annulé OU déjà terminal (l'appelant DOIT
        relire le statut pour connaître d'éventuels fills)."""
        if not order_id and not client_id:
            return True
        params: Dict[str, Any] = {}
        if client_id and not order_id:
            params["origClientOrderId"] = client_id
        try:
            self.exchange.cancel_order(str(order_id) if order_id else "",
                                       self.cfg.symbol, params)
            self.invalidate_balances()
            return True
        except ccxt.OrderNotFound:
            self.invalidate_balances()
            return True
        except Exception as e:
            self.logger.warning(f"[ORD] cancel {order_id or client_id} KO: {e}")
            return False

    # ---------- OCO natif ----------

    def place_oco(self, amount: float, tp: float,
                  sl_stop: float) -> Dict[str, Any]:
        """Pose un OCO natif SELL (LIMIT_MAKER au-dessus, stop en dessous).
        Lève en cas d'échec ; AmbiguousOrder si l'état est inconnu."""
        amount = self.round_amount(amount)
        tp = self.round_price(tp, "up")
        sl = self.round_price(sl_stop, "down")
        if amount <= 0 or tp <= 0 or sl <= 0 or tp <= sl:
            raise ValueError(f"OCO invalide: qty={amount} tp={tp} sl={sl}")
        if not self.rules.oco_allowed:
            raise ccxt.InvalidOrder("OCO non autorisé sur ce marché")
        list_cid = _client_id(CID_OCO_LIST)
        tp_cid = _client_id(CID_OCO_TP)
        sl_cid = _client_id(CID_OCO_SL)
        below_type = self.stop_order_type
        stop_limit = self.round_price(
            sl * (1 - self.cfg.stop_limit_offset_pct), "down")
        fn = self._raw("privatePostOrderListOco", "private_post_orderlist_oco")
        if fn is not None:
            params = {
                "symbol": self._market_id, "side": "SELL",
                "quantity": self._qty_str(amount),
                "aboveType": "LIMIT_MAKER", "abovePrice": self._px_str(tp),
                "belowType": below_type, "belowStopPrice": self._px_str(sl),
                "listClientOrderId": list_cid,
                "aboveClientOrderId": tp_cid,
                "belowClientOrderId": sl_cid,
                "newOrderRespType": "FULL"}
            if below_type == "STOP_LOSS_LIMIT":
                params["belowPrice"] = self._px_str(stop_limit)
                params["belowTimeInForce"] = "GTC"
        else:
            fn = self._raw("privatePostOrderOco", "private_post_order_oco")
            if fn is None:
                raise ccxt.NotSupported("Endpoint OCO absent")
            params = {
                "symbol": self._market_id, "side": "SELL",
                "quantity": self._qty_str(amount),
                "price": self._px_str(tp), "stopPrice": self._px_str(sl),
                "listClientOrderId": list_cid,
                "limitClientOrderId": tp_cid,
                "stopClientOrderId": sl_cid,
                "newOrderRespType": "FULL"}
            if below_type == "STOP_LOSS_LIMIT":
                params["stopLimitPrice"] = self._px_str(stop_limit)
                params["stopLimitTimeInForce"] = "GTC"
        try:
            resp = fn(params)
        except ccxt.OperationFailed as e:        # statut inconnu (voir _submit)
            self.invalidate_balances()
            resp = self._lookup_list_by_cid(list_cid)
            if resp is None:
                raise AmbiguousOrder(f"OCO ambigu: {list_cid}", list_cid) from e
        self.invalidate_balances()
        out = self.parse_order_list(resp, tp_cid, sl_cid)
        out["list_client_order_id"] = out.get("list_client_order_id") or list_cid
        return out

    def parse_order_list(self, resp: Dict[str, Any],
                         tp_cid: Optional[str] = None,
                         sl_cid: Optional[str] = None) -> Dict[str, Any]:
        lid = resp.get("orderListId") if isinstance(resp, dict) else None
        if lid is None:
            raise ccxt.ExchangeError("Réponse OCO sans orderListId")
        tp_id = sl_id = None
        unclassified: List[str] = []
        entries = list(resp.get("orderReports") or []) \
            + list(resp.get("orders") or [])
        for o in entries:
            oid = o.get("orderId")
            if oid is None:
                continue
            oid = str(oid)
            c = str(o.get("clientOrderId") or "")
            t = str(o.get("type") or "").upper()
            if (tp_cid and c == tp_cid) or c.startswith(CID_OCO_TP) \
                    or t in ("LIMIT_MAKER", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"):
                tp_id = tp_id or oid
            elif (sl_cid and c == sl_cid) or c.startswith(CID_OCO_SL) \
                    or t.startswith("STOP_LOSS"):
                sl_id = sl_id or oid
            elif oid not in unclassified:
                unclassified.append(oid)
        unclassified = [u for u in unclassified if u not in (tp_id, sl_id)]
        return {"order_list_id": str(lid),
                "list_client_order_id": resp.get("listClientOrderId"),
                "list_status": str(resp.get("listOrderStatus") or ""),
                "tp_order_id": tp_id, "sl_order_id": sl_id,
                "tp_client_order_id": tp_cid, "sl_client_order_id": sl_cid,
                "unclassified_order_ids": unclassified}

    def query_order_list(self, list_id: Optional[str] = None,
                         list_cid: Optional[str] = None) -> Dict[str, Any]:
        """GET /api/v3/orderList — n'accepte PAS de paramètre `symbol`."""
        fn = self._raw("privateGetOrderList", "private_get_orderlist")
        if fn is None:
            raise ccxt.NotSupported("Endpoint orderList absent")
        if list_id is not None:
            return fn({"orderListId": str(list_id)})
        if list_cid:
            return fn({"origClientOrderId": list_cid})
        raise ValueError("query_order_list: identifiant requis")

    def _lookup_list_by_cid(self, list_cid: str,
                            attempts: int = 3) -> Optional[Dict[str, Any]]:
        for i in range(attempts):
            try:
                resp = self.query_order_list(list_cid=list_cid)
                if resp and resp.get("orderListId") is not None:
                    return resp
            except ccxt.OrderNotFound:
                pass
            except Exception as e:
                self.logger.warning(f"[OCO] lookup {list_cid} KO: {e}")
            self.sleep(0.5 * (i + 1))
        return None

    def cancel_order_list(self, list_id: str) -> bool:
        """True si annulée ou déjà terminée (relire les jambes ensuite)."""
        if not list_id:
            return True
        fn = self._raw("privateDeleteOrderList", "private_delete_orderlist")
        if fn is None:
            return False
        try:
            fn({"symbol": self._market_id, "orderListId": str(list_id)})
            self.invalidate_balances()
            return True
        except ccxt.OrderNotFound:
            self.invalidate_balances()
            return True
        except Exception as e:
            self.logger.warning(f"[OCO] cancel list {list_id} KO: {e}")
            return False

    # ---------- Lecture compte ----------

    def fetch_open_orders(self) -> List[Dict[str, Any]]:
        """Lève en cas d'erreur (fail-closed)."""
        return list(self.exchange.fetch_open_orders(self.cfg.symbol) or [])

    def fetch_my_trades(self, since_ms: int, limit: int = 500
                        ) -> List[Dict[str, Any]]:
        return list(self.exchange.fetch_my_trades(
            self.cfg.symbol, since=since_ms, limit=limit) or [])

    # ---------- Self-test (sans ordre réel) ----------

    def self_test_conditional_orders(self) -> bool:
        if (self.cfg.run_mode != "live"
                or not self.cfg.self_test_conditional_orders):
            return True
        problems: List[str] = []
        types = set(self.rules.order_types)
        if types and "LIMIT_MAKER" not in types:
            problems.append("LIMIT_MAKER non supporté")
        if types and not ({"STOP_LOSS", "STOP_LOSS_LIMIT"} & types):
            problems.append("aucun type stop supporté")
        if self.cfg.use_oco and not self.rules.oco_allowed:
            problems.append("OCO non autorisé sur ce marché")
        if (self._raw("privatePostOrderListOco", "private_post_orderlist_oco")
                is None and self._raw("privatePostOrderOco",
                                      "private_post_order_oco") is None):
            problems.append("endpoint OCO absent (ccxt trop ancien ?)")
        fn = self._raw("privatePostOrderTest", "private_post_order_test")
        if fn is None:
            problems.append("endpoint order/test absent")
        elif not problems:
            try:
                last = self.get_ticker()["last"]
                stop = self.round_price(last * 0.9, "down")
                qty = self.round_amount(max(self.rules.min_amount,
                                            self.min_notional() * 1.5 / stop))
                otype = self.stop_order_type
                params = {"symbol": self._market_id, "side": "SELL",
                          "type": otype, "quantity": self._qty_str(qty),
                          "stopPrice": self._px_str(stop)}
                if otype == "STOP_LOSS_LIMIT":
                    params["price"] = self._px_str(self.round_price(
                        stop * (1 - self.cfg.stop_limit_offset_pct), "down"))
                    params["timeInForce"] = "GTC"
                fn(params)
            except Exception as e:
                problems.append(f"order/test refusé: {e}")
        if problems:
            self.logger.critical(f"[SELFTEST] KO: {'; '.join(problems)}")
            return False
        self.logger.info(f"[SELFTEST] OK (stop={self.stop_order_type}, "
                         f"oco={self.rules.oco_allowed})")
        return True


# ══════════════════════════════════════════════════════════════════════
# SECTION 7 — BLOCKCHAIN
# ══════════════════════════════════════════════════════════════════════

class BlockchainError(Exception):
    pass


def _to_wei_exact(amount: float, decimals: int = 18) -> int:
    """Conversion exacte (Decimal) — jamais de float * 10**18."""
    q = Decimal(str(amount)) * (Decimal(10) ** int(decimals))
    return int(q.to_integral_value(rounding=ROUND_DOWN))


class BlockchainPolicy:
    def __init__(self, cfg: Config, self_address: str,
                 logger: logging.Logger):
        self.cfg = cfg
        self.self_address = self_address.lower()
        self.logger = logger

    def guard_tx(self, to: str, value_eth: float) -> str:
        if not Web3.is_address(to):
            raise BlockchainError(f"Adresse invalide: {to}")
        to_cs = Web3.to_checksum_address(to)
        if to_cs.lower() == self.self_address:
            raise BlockchainError("Destination = émetteur (refus).")
        if self.cfg.blockchain_whitelist_enabled:
            wl = {a.lower() for a in self.cfg.blockchain_whitelist}
            if not wl:
                raise BlockchainError("Whitelist activée mais vide.")
            if to_cs.lower() not in wl:
                raise BlockchainError(f"Destination {to_cs} non autorisée.")
        if not isinstance(value_eth, (int, float)) or not math.isfinite(value_eth):
            raise BlockchainError("value_eth non fini.")
        if value_eth < 0:
            raise BlockchainError("value_eth négatif.")
        if value_eth > self.cfg.blockchain_max_tx_value_eth:
            raise BlockchainError(
                f"value_eth={value_eth} > "
                f"max={self.cfg.blockchain_max_tx_value_eth}")
        return to_cs


class BlockchainAdapter:
    def __init__(self, cfg: Config, logger: logging.Logger,
                 store: Optional[Store] = None,
                 notifier: Optional[Notifier] = None):
        self.cfg = cfg
        self.logger = logger
        self.store = store
        self.notifier = notifier or Notifier("", "")
        self.enabled = bool(cfg.blockchain_enabled)
        self.w3: Optional[Any] = None
        self.account: Optional[Any] = None
        self.address: Optional[str] = None
        self.policy: Optional[BlockchainPolicy] = None
        self._nonce_lock = threading.Lock()
        # Sémantique UNIQUE : prochain nonce libre (jamais « dernier utilisé »).
        self._next_nonce: Optional[int] = None
        self._token_contract: Optional[Any] = None
        self._eip1559_supported: Optional[bool] = None
        self._secrets = [cfg.blockchain_rpc_url]
        if not self.enabled:
            return
        if not WEB3_AVAILABLE:
            raise BlockchainError("web3 non installé.")
        pk = os.environ.get("BLOCKCHAIN_PRIVATE_KEY", "").strip()
        if not pk:
            raise BlockchainError("BLOCKCHAIN_PRIVATE_KEY absent.")
        if not pk.startswith("0x"):
            pk = "0x" + pk
        self._secrets.append(pk)
        try:
            self.account = Account.from_key(pk)
            self.address = self.account.address
        except Exception:
            raise BlockchainError("Clé privée invalide.")
        try:
            self.w3 = Web3(Web3.HTTPProvider(
                cfg.blockchain_rpc_url, request_kwargs={"timeout": 20}))
            if cfg.blockchain_chain.lower() in ("bsc", "polygon"):
                self._inject_poa()
            if not self.w3.is_connected():
                raise BlockchainError(
                    f"RPC injoignable: {redact_url(cfg.blockchain_rpc_url)}")
            remote_chain_id = self.w3.eth.chain_id
            if remote_chain_id != cfg.blockchain_chain_id:
                raise BlockchainError(
                    f"chain_id mismatch: config={cfg.blockchain_chain_id} "
                    f"remote={remote_chain_id}")
            self._detect_eip1559()
            bal_eth = self.balance_native()
            self.logger.info(
                f"[CHAIN] OK chain={cfg.blockchain_chain} "
                f"id={cfg.blockchain_chain_id} "
                f"address={redact_address(self.address)} "
                f"balance={bal_eth:.6f} dry_run={cfg.blockchain_dry_run}")
            if cfg.blockchain_track_token:
                if not Web3.is_address(cfg.blockchain_track_token):
                    raise BlockchainError(
                        f"Adresse ERC-20 invalide: "
                        f"{cfg.blockchain_track_token}")
                addr_cs = Web3.to_checksum_address(cfg.blockchain_track_token)
                code = self.w3.eth.get_code(addr_cs)
                if not code or len(code) < 10:
                    raise BlockchainError(
                        f"Adresse {addr_cs} n'est pas un contrat.")
                self._token_contract = self.w3.eth.contract(
                    address=addr_cs, abi=ERC20_ABI)
            self.policy = BlockchainPolicy(cfg, self.address, logger)
        except BlockchainError:
            raise
        except Exception as e:
            raise BlockchainError(f"Init Web3 KO: {self._scrub(e)}")

    def _scrub(self, e: Any) -> str:
        return scrub_secrets(str(e), self._secrets)

    def _inject_poa(self) -> None:
        if ExtraDataToPOAMiddleware is None:
            raise BlockchainError("Chaîne POA mais middleware absent.")
        try:
            self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            return
        except AttributeError:
            pass
        except Exception as e:
            self.logger.warning(f"[POA] inject KO: {self._scrub(e)}")
        try:
            self.w3.middleware.add(ExtraDataToPOAMiddleware)
        except Exception as e:
            raise BlockchainError(f"Middleware POA non injectable: {self._scrub(e)}")

    def _detect_eip1559(self) -> None:
        try:
            latest = self.w3.eth.get_block("latest")
            self._eip1559_supported = bool(latest.get("baseFeePerGas"))
        except Exception as e:
            self.logger.warning(f"[CHAIN] EIP-1559 detect KO: {self._scrub(e)}")
            self._eip1559_supported = bool(self._eip1559_supported)

    def balance_native(self) -> float:
        if not self.enabled:
            return 0.0
        try:
            return float(self.w3.from_wei(
                self.w3.eth.get_balance(self.address), "ether"))
        except Exception as e:
            raise BlockchainError(f"balance_native KO: {self._scrub(e)}")

    def balance_token(self, token_addr: Optional[str] = None) -> float:
        if not self.enabled:
            return 0.0
        try:
            contract = self._token_contract
            if contract is None and token_addr:
                contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(token_addr),
                    abi=ERC20_ABI)
            if contract is None:
                return 0.0
            decimals = contract.functions.decimals().call()
            raw = contract.functions.balanceOf(self.address).call()
            return float(Decimal(raw) / (Decimal(10) ** int(decimals)))
        except Exception as e:
            raise BlockchainError(f"balance_token KO: {self._scrub(e)}")

    def _compute_fees(self) -> Tuple[int, int, bool]:
        cap = int(self.w3.to_wei(self.cfg.blockchain_max_fee_gwei, "gwei"))
        if not self._eip1559_supported:
            gp = int(self.w3.eth.gas_price)
            if gp > cap:
                raise BlockchainError(
                    f"gas_price={gp} > cap={cap} — tx reportée")
            return gp, gp, False
        latest = self.w3.eth.get_block("latest")
        base_fee = int(latest.get("baseFeePerGas") or 0)
        cap_priority = int(self.w3.to_wei(
            self.cfg.blockchain_max_priority_fee_gwei, "gwei"))
        try:
            s = self.w3.eth.max_priority_fee
            s = int(s() if callable(s) else s)
        except Exception:
            s = cap_priority // 4
        priority = max(1, min(s, cap_priority))
        if base_fee + priority > cap:
            raise BlockchainError(
                f"base_fee={base_fee} + priority={priority} > cap={cap} — "
                f"tx reportée")
        max_fee = min(base_fee * 2 + priority, cap)
        return max_fee, priority, True

    # ---------- Nonce ----------

    def _acquire_nonce(self) -> int:
        """Prochain nonce libre = max(nonce pending du nœud, compteur local).
        Aucune estimation « à l'aveugle » si le nœud est injoignable."""
        with self._nonce_lock:
            try:
                chain = int(self.w3.eth.get_transaction_count(
                    self.address, "pending"))
            except Exception as e:
                raise BlockchainError(f"Nonce indisponible: {self._scrub(e)}")
            n = chain if self._next_nonce is None else max(chain,
                                                           self._next_nonce)
            self._next_nonce = n + 1
            return n

    def _resync_nonce(self) -> None:
        with self._nonce_lock:
            try:
                self._next_nonce = int(self.w3.eth.get_transaction_count(
                    self.address, "pending"))
            except Exception:
                self._next_nonce = None

    # ---------- Envoi ----------

    def _sign_and_send(self, tx: Dict[str, Any]) -> str:
        signed = self.w3.eth.account.sign_transaction(
            tx, private_key=self.account.key)
        raw = (getattr(signed, "raw_transaction", None)
               or getattr(signed, "rawTransaction"))
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        h = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
        return h if h.startswith("0x") else "0x" + h

    def _build_tx(self, nonce: int, to_cs: str, value_wei: int, gas: int,
                  max_fee: int, priority: int, eip1559: bool,
                  data: Optional[str] = None) -> Dict[str, Any]:
        if eip1559:
            tx = {"type": 2, "chainId": self.cfg.blockchain_chain_id,
                  "nonce": nonce, "from": self.address, "to": to_cs,
                  "value": value_wei, "gas": gas,
                  "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority}
        else:
            tx = {"chainId": self.cfg.blockchain_chain_id, "nonce": nonce,
                  "from": self.address, "to": to_cs, "value": value_wei,
                  "gas": gas, "gasPrice": max_fee}
        if data:
            tx["data"] = data
        return tx

    @staticmethod
    def _encode_transfer(contract: Any, to_cs: str, raw_amount: int) -> str:
        """Encodage ERC-20 compatible web3 v6/v7/v8."""
        fn = contract.functions.transfer(to_cs, raw_amount)
        enc = getattr(fn, "_encode_transaction_data", None)
        if callable(enc):
            return enc()
        enc_abi = getattr(contract, "encode_abi", None)
        if callable(enc_abi):
            try:
                return enc_abi("transfer", args=[to_cs, raw_amount])
            except TypeError:
                return enc_abi(fn_name="transfer", args=[to_cs, raw_amount])
        return contract.encodeABI(fn_name="transfer", args=[to_cs, raw_amount])

    def _track_pending(self, ctx: Optional[BotContext], tx_hash: str,
                       nonce: int, max_fee: int, to: str, value_wei: int,
                       data: Optional[str], gas: int, kind: str,
                       priority_wei: int = 0) -> None:
        if ctx is None:
            return
        b = ctx.blockchain
        b.last_pending_tx_hash = tx_hash
        b.pending_tx_hashes = [tx_hash]
        b.last_pending_tx_since = _utcnow_iso()
        b.last_pending_tx_nonce = nonce
        b.last_pending_tx_fee_wei = str(max_fee)
        b.last_pending_tx_priority_wei = str(priority_wei)
        b.last_pending_tx_to = to
        b.last_pending_tx_value_wei = str(value_wei)
        b.last_pending_tx_data = data
        b.last_pending_tx_gas = gas
        b.last_pending_tx_kind = kind
        b.rbf_attempts = 0
        b.rbf_exhausted_notified = False

    def _clear_pending(self, ctx: Optional[BotContext],
                       resync_nonce: bool = False) -> None:
        if ctx is None:
            return
        b = ctx.blockchain
        b.last_pending_tx_hash = None
        b.pending_tx_hashes = []
        b.last_pending_tx_since = None
        b.last_pending_tx_nonce = None
        b.last_pending_tx_fee_wei = None
        b.last_pending_tx_priority_wei = None
        b.last_pending_tx_to = None
        b.last_pending_tx_value_wei = None
        b.last_pending_tx_data = None
        b.last_pending_tx_gas = None
        b.last_pending_tx_kind = None
        b.rbf_attempts = 0
        b.rbf_exhausted_notified = False
        if resync_nonce:
            self._resync_nonce()

    def _check_no_pending(self, ctx: Optional[BotContext]) -> None:
        if ctx is None:
            return
        if ctx.blockchain.last_pending_tx_hash:
            raise BlockchainError(
                f"Pending en vol: "
                f"{ctx.blockchain.last_pending_tx_hash[:16]}… "
                f"(attendre résolution/RBF)")

    def send_native(self, to: str, amount_eth: float,
                    ctx: Optional[BotContext] = None) -> Dict[str, Any]:
        if self.policy is None:
            raise BlockchainError("Policy absente")
        to_cs = self.policy.guard_tx(to, amount_eth)
        self._check_no_pending(ctx)
        if ctx is None and not self.cfg.blockchain_dry_run:
            raise BlockchainError("send_native live sans contexte : refus "
                                  "(la tx ne serait pas suivie)")
        value_wei = _to_wei_exact(amount_eth)
        native_bal = self.balance_native()
        max_fee, priority, eip1559 = self._compute_fees()
        gas_estimate = 21000
        gas_cost_eth = float(self.w3.from_wei(gas_estimate * max_fee, "ether"))
        required = (amount_eth + gas_cost_eth
                    + self.cfg.blockchain_min_gas_reserve_eth)
        if native_bal < required:
            if self.cfg.blockchain_dry_run:
                self.logger.warning(
                    f"[DRY-RUN] solde insuffisant ({native_bal:.6f} < "
                    f"{required:.6f})")
            else:
                raise BlockchainError(
                    f"Solde insuffisant: {native_bal:.6f} < {required:.6f}")
        if self.cfg.blockchain_dry_run:
            self.logger.info(
                f"[DRY-RUN] send_native → {redact_address(to_cs)} "
                f"{amount_eth} (non diffusé)")
            return {"tx_hash": "DRY_RUN", "dry_run": True, "pending": False,
                    "value_eth": amount_eth, "to": to_cs}
        nonce = self._acquire_nonce()
        tx = self._build_tx(nonce, to_cs, value_wei, gas_estimate,
                            max_fee, priority, eip1559)
        try:
            tx_hash = self._sign_and_send(tx)
        except Exception as e:
            self._resync_nonce()
            raise BlockchainError(f"send_native KO: {self._scrub(e)}")
        self._track_pending(
            ctx, tx_hash, nonce, max_fee, to_cs, value_wei, None,
            gas_estimate, "native",
            priority_wei=(priority if eip1559 else max_fee))
        self.logger.info(
            f"[CHAIN] send_native: {amount_eth} → {redact_address(to_cs)} "
            f"hash={tx_hash[:16]}… nonce={nonce}")
        return {"tx_hash": tx_hash, "dry_run": False, "pending": True,
                "value_eth": amount_eth, "to": to_cs}

    def send_token(self, token_addr: str, to: str, amount_token: float,
                   ctx: Optional[BotContext] = None) -> Dict[str, Any]:
        if not self.enabled:
            raise BlockchainError("blockchain disabled")
        if not Web3.is_address(token_addr):
            raise BlockchainError(f"Token invalide: {token_addr}")
        if self.policy is None:
            raise BlockchainError("Policy absente")
        to_cs = self.policy.guard_tx(to, 0.0)
        self._check_no_pending(ctx)
        if ctx is None and not self.cfg.blockchain_dry_run:
            raise BlockchainError("send_token live sans contexte : refus")
        token_cs = Web3.to_checksum_address(token_addr)
        contract = self.w3.eth.contract(address=token_cs, abi=ERC20_ABI)
        decimals = int(contract.functions.decimals().call())
        raw_amount = _to_wei_exact(amount_token, decimals)
        if raw_amount <= 0:
            raise BlockchainError("Montant token nul après conversion.")
        bal = contract.functions.balanceOf(self.address).call()
        if bal < raw_amount:
            raise BlockchainError(
                f"Balance token insuffisante: "
                f"{Decimal(bal) / (Decimal(10) ** decimals)} < {amount_token}")
        native_bal = self.balance_native()
        try:
            gas_estimate = contract.functions.transfer(
                to_cs, raw_amount).estimate_gas({"from": self.address})
        except Exception as e:
            raise BlockchainError(f"Estimation gaz ERC-20 KO: {self._scrub(e)}")
        gas_estimate = int(gas_estimate * 1.3)
        max_fee, priority, eip1559 = self._compute_fees()
        gas_cost_eth = float(self.w3.from_wei(gas_estimate * max_fee, "ether"))
        required = gas_cost_eth + self.cfg.blockchain_min_gas_reserve_eth
        if native_bal < required:
            if self.cfg.blockchain_dry_run:
                self.logger.warning(
                    f"[DRY-RUN] gaz insuffisant ({native_bal:.6f} < "
                    f"{required:.6f})")
            else:
                raise BlockchainError(
                    f"Gaz insuffisant: {native_bal:.6f} < {required:.6f}")
        if self.cfg.blockchain_dry_run:
            self.logger.info(
                f"[DRY-RUN] send_token {amount_token} → "
                f"{redact_address(to_cs)} (non diffusé)")
            return {"tx_hash": "DRY_RUN", "dry_run": True, "pending": False,
                    "token_amount": amount_token,
                    "token_addr": token_cs, "to": to_cs}
        data = self._encode_transfer(contract, to_cs, raw_amount)
        nonce = self._acquire_nonce()
        tx = self._build_tx(nonce, token_cs, 0, gas_estimate,
                            max_fee, priority, eip1559, data=data)
        try:
            tx_hash = self._sign_and_send(tx)
        except Exception as e:
            self._resync_nonce()
            raise BlockchainError(f"send_token KO: {self._scrub(e)}")
        self._track_pending(
            ctx, tx_hash, nonce, max_fee, token_cs, 0, data,
            gas_estimate, "token",
            priority_wei=(priority if eip1559 else max_fee))
        self.logger.info(
            f"[CHAIN] send_token: {amount_token} → {redact_address(to_cs)} "
            f"hash={tx_hash[:16]}… nonce={nonce}")
        return {"tx_hash": tx_hash, "dry_run": False, "pending": True,
                "token_amount": amount_token,
                "token_addr": token_cs, "to": to_cs}

    # ---------- Suivi ----------

    def poll_pending_tx(self, ctx: BotContext) -> Optional[Dict[str, Any]]:
        if not self.enabled or self.cfg.blockchain_dry_run:
            return None
        b = ctx.blockchain
        if not b.last_pending_tx_hash:
            return None
        hashes = list(dict.fromkeys(
            list(b.pending_tx_hashes or []) + [b.last_pending_tx_hash]))
        receipt = None
        mined_hash = None
        for h in hashes:
            try:
                r = self.w3.eth.get_transaction_receipt(h)
            except Exception:
                r = None
            if r:
                receipt, mined_hash = r, h
                break
        kind = b.last_pending_tx_kind or "native"
        if receipt is None:
            nonce = b.last_pending_tx_nonce
            if nonce is not None:
                try:
                    latest = int(self.w3.eth.get_transaction_count(
                        self.address, "latest"))
                    if latest > int(nonce):
                        self.logger.error(
                            f"[CHAIN] nonce {nonce} consommé par une tx "
                            f"inconnue (wallet partagé ?) → suivi arrêté")
                        self.notifier(
                            f"⚠️ Nonce {nonce} consommé hors bot", critical=True)
                        self._clear_pending(ctx, resync_nonce=True)
                        return {"tx_hash": None, "status": None,
                                "kind": kind, "replaced_externally": True}
                except Exception:
                    pass
            since = _parse_iso(b.last_pending_tx_since)
            if since and (_utcnow() - since).total_seconds() \
                    > self.cfg.blockchain_tx_timeout_sec:
                self.check_stuck_tx(ctx)
            return None
        try:
            head = int(self.w3.eth.block_number)
            confs = head - int(receipt.get("blockNumber") or head) + 1
        except Exception:
            confs = 0
        if confs < self.cfg.blockchain_confirmations:
            return None
        status = int(receipt.get("status", 0))
        gas_used = int(receipt.get("gasUsed", 0))
        eff_price = int(receipt.get("effectiveGasPrice")
                        or int(b.last_pending_tx_fee_wei or "0"))
        value_wei = int(b.last_pending_tx_value_wei or "0")
        gas_eth = float(self.w3.from_wei(gas_used * eff_price, "ether"))
        if status != 1:
            self.logger.error(
                f"[CHAIN] REVERT ({kind}): hash={mined_hash} gas={gas_used}")
            self.notifier(f"❌ Tx revert: {mined_hash[:10]}…", critical=True)
        else:
            self.logger.info(
                f"[CHAIN] OK ({kind}): hash={mined_hash} gas={gas_used} "
                f"confs={confs}")
        if self.store:
            self.store.log_blockchain_tx({
                "chain": self.cfg.blockchain_chain,
                "chain_id": self.cfg.blockchain_chain_id,
                "tx_hash": mined_hash, "from_addr": self.address,
                "to_addr": b.last_pending_tx_to,
                "value_eth": float(self.w3.from_wei(value_wei, "ether")),
                "token_addr": None, "token_amount": None,
                "gas_used": gas_used, "gas_price_wei": str(eff_price),
                "status": status, "dry_run": 0, "pending": 0,
                "rbf": 1 if b.rbf_attempts > 0 else 0},
                self.cfg.run_mode)
        b.tx_count = int(b.tx_count or 0) + 1
        b.total_gas_spent_eth = float(b.total_gas_spent_eth or 0.0) + gas_eth
        self._clear_pending(ctx)
        return {"tx_hash": mined_hash, "status": status,
                "gas_used": gas_used, "kind": kind, "confirmations": confs}

    def check_stuck_tx(self, ctx: BotContext) -> bool:
        """Remplace (RBF) une tx bloquée. Au-delà de max_rbf_attempts on
        cesse de surenchérir mais on CONTINUE le suivi : une tx diffusée
        n'est jamais « oubliée » (elle peut encore être minée)."""
        if not self.enabled or self.cfg.blockchain_dry_run:
            return False
        b = ctx.blockchain
        tx_hash = b.last_pending_tx_hash
        if not tx_hash:
            return False
        attempts = int(b.rbf_attempts or 0)
        if attempts >= self.cfg.blockchain_max_rbf_attempts:
            if not b.rbf_exhausted_notified:
                self.logger.error(
                    f"[RBF] {tx_hash[:16]}… — {attempts} remplacements, "
                    f"arrêt des surenchères (suivi maintenu)")
                self.notifier(f"❌ Tx {tx_hash[:10]}… bloquée : action "
                              f"manuelle requise", critical=True)
                b.rbf_exhausted_notified = True
            return False
        nonce = b.last_pending_tx_nonce
        if nonce is None:
            self.logger.error("[RBF] nonce manquant → suivi arrêté")
            self._clear_pending(ctx, resync_nonce=True)
            return False
        old_fee = int(b.last_pending_tx_fee_wei or "0")
        old_priority = int(b.last_pending_tx_priority_wei or "0")
        boost = 1.0 + self.cfg.blockchain_replace_fee_boost_pct
        try:
            _mf, net_priority, eip1559 = self._compute_fees()
            cap_fee = int(self.w3.to_wei(self.cfg.blockchain_max_fee_gwei, "gwei"))
            cap_priority = int(self.w3.to_wei(
                self.cfg.blockchain_max_priority_fee_gwei, "gwei"))
            if eip1559:
                new_priority = max(net_priority, int(old_priority * boost) + 1)
                new_fee = max(_mf, int(old_fee * boost) + 1,
                              new_priority + 1)
                if new_priority > cap_priority or new_fee > cap_fee:
                    self.logger.error(
                        f"[RBF] plafond atteint (prio={new_priority} "
                        f"fee={new_fee}) → attente")
                    b.rbf_attempts = attempts + 1
                    return False
            else:
                new_fee = max(int(self.w3.eth.gas_price),
                              int(old_fee * boost) + 1)
                if new_fee > cap_fee:
                    self.logger.error(f"[RBF] plafond legacy {new_fee} > {cap_fee}")
                    b.rbf_attempts = attempts + 1
                    return False
                new_priority = new_fee
            tx = self._build_tx(
                int(nonce), b.last_pending_tx_to,
                int(b.last_pending_tx_value_wei or "0"),
                int(b.last_pending_tx_gas or 21000), new_fee, new_priority,
                eip1559, data=b.last_pending_tx_data)
            new_hash = self._sign_and_send(tx)
        except BlockchainError as e:
            self.logger.error(f"[RBF] KO: {e}")
            b.rbf_attempts = attempts + 1
            return False
        except Exception as e:
            self.logger.error(f"[RBF] KO: {self._scrub(e)}")
            b.rbf_attempts = attempts + 1
            return False
        b.pending_tx_hashes = list(dict.fromkeys(
            list(b.pending_tx_hashes or [tx_hash]) + [new_hash]))
        b.last_pending_tx_hash = new_hash
        b.last_pending_tx_since = _utcnow_iso()
        b.last_pending_tx_fee_wei = str(new_fee)
        b.last_pending_tx_priority_wei = str(new_priority)
        b.rbf_attempts = attempts + 1
        self.logger.warning(
            f"[RBF] #{attempts + 1}: {tx_hash[:10]}… → {new_hash[:10]}… "
            f"(fee {old_fee}→{new_fee}, prio {old_priority}→{new_priority})")
        self.notifier(f"🔄 RBF #{attempts + 1} {tx_hash[:10]}…")
        return True

    def refresh_state(self, ctx: BotContext) -> None:
        if not self.enabled:
            return
        try:
            ctx.blockchain.last_balance_eth = self.balance_native()
            if self._token_contract is not None:
                ctx.blockchain.last_balance_token = self.balance_token()
            chain_nonce = int(self.w3.eth.get_transaction_count(
                self.address, "pending"))
            ctx.blockchain.last_nonce = chain_nonce
            with self._nonce_lock:
                if self._next_nonce is None:
                    self._next_nonce = chain_nonce
                elif (self._next_nonce > chain_nonce
                      and ctx.blockchain.last_pending_tx_hash is None):
                    # Une tx locale a disparu du mempool : on se recale.
                    self._next_nonce = chain_nonce
            ctx.blockchain.last_block_seen = int(self.w3.eth.block_number)
            self._detect_eip1559()
            ctx.blockchain.eip1559_supported = self._eip1559_supported
        except Exception as e:
            self.logger.warning(f"[CHAIN] refresh_state KO: {self._scrub(e)}")

    def maybe_sweep(self, ctx: BotContext) -> bool:
        if not self.enabled or not self.cfg.blockchain_sweep_enabled:
            return False
        if ctx.blockchain.last_pending_tx_hash:
            return False
        now = time.time()
        if now - float(ctx.blockchain.last_sweep_ts or 0) < 600:
            return False
        if (now - float(ctx.blockchain.last_sweep_fail_ts or 0)
                < self.cfg.blockchain_sweep_fail_cooldown_sec):
            return False
        try:
            bal = self.balance_native()
            trigger = self.cfg.blockchain_sweep_trigger_eth
            keep = self.cfg.blockchain_sweep_keep_eth
            if bal <= trigger:
                return False
            max_fee, _, _ = self._compute_fees()
            gas_cost = float(self.w3.from_wei(21000 * max_fee, "ether"))
            amount = bal - keep - gas_cost * 1.5
            if amount <= 0:
                return False
            amount = min(amount, self.cfg.blockchain_max_tx_value_eth)
            res = self.send_native(self.cfg.blockchain_sweep_target, amount, ctx)
            ctx.blockchain.last_sweep_ts = now
            if res.get("dry_run"):
                self.logger.info(f"[SWEEP][DRY-RUN] {amount:.6f} (non diffusé)")
                return False
            ctx.blockchain.sweep_count = int(ctx.blockchain.sweep_count or 0) + 1
            self.notifier(
                f"💸 Sweep {amount:.6f} → "
                f"{redact_address(self.cfg.blockchain_sweep_target)}")
            return True
        except BlockchainError as e:
            self.logger.info(f"[SWEEP] reporté: {e}")
            ctx.blockchain.last_sweep_fail_ts = now
            return False
        except Exception as e:
            self.logger.warning(f"[SWEEP] KO: {self._scrub(e)}")
            ctx.blockchain.last_sweep_fail_ts = now
            return False


# ══════════════════════════════════════════════════════════════════════
# SECTION 8 — ADAPTIVE ENGINE
# ══════════════════════════════════════════════════════════════════════

def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        v = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return v


def _isnan(x: Any) -> bool:
    try:
        return x is None or bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


class AdaptiveEngine:
    """Ajuste trois paramètres de filtrage. Il ne peut que RESSERRER la
    fraîcheur (bornée par max_signal_age_min) et la pause (bornée à ±2)."""

    def __init__(self, cfg: Config, logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger

    def record_latency(self, state: AdaptiveState, latency_ms: int) -> None:
        if not self.cfg.adaptive_enabled:
            return
        state.signal_latencies_ms.append(int(max(0, latency_ms)))
        max_len = self.cfg.adaptive_latency_window * 2
        if len(state.signal_latencies_ms) > max_len:
            state.signal_latencies_ms = state.signal_latencies_ms[-max_len:]

    def observe_closed_candle(self, state: AdaptiveState, closed_ts: int,
                              now_ms: Optional[int] = None) -> None:
        """Latence mesurée sur CHAQUE nouvelle bougie fermée (et non sur
        les seules entrées acceptées → pas de biais de survie)."""
        if state.last_latency_candle_ts == int(closed_ts):
            return
        state.last_latency_candle_ts = int(closed_ts)
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        close_ms = int(closed_ts) + _timeframe_ms(self.cfg.timeframe)
        self.record_latency(state, now_ms - close_ms)

    def _p95_latency_min(self, state: AdaptiveState) -> Optional[float]:
        if len(state.signal_latencies_ms) < self.cfg.adaptive_min_samples:
            return None
        arr = np.array(state.signal_latencies_ms[
            -self.cfg.adaptive_latency_window:], dtype=float)
        return float(np.percentile(arr, 95)) / 60_000

    def update(self, ctx: BotContext, closed: Any) -> None:
        if not self.cfg.adaptive_enabled:
            return
        state = ctx.adaptive
        p95_min = self._p95_latency_min(state)
        if p95_min is not None:
            target = max(self.cfg.adaptive_freshness_min,
                         min(self.cfg.adaptive_freshness_max,
                             int(p95_min * 2) + 10))
            if target != state.current_freshness_min:
                self.logger.info(
                    f"[ADAPT] freshness {state.current_freshness_min} → "
                    f"{target} (p95={p95_min:.1f}min)")
                state.current_freshness_min = target
            state.bootstrap_done = True
        else:
            state.current_freshness_min = self.cfg.adaptive_freshness_max
            state.bootstrap_done = False
        if self.cfg.adaptive_atr_scaling and closed is not None:
            atr_pct = _row_get(closed, "atr_pct")
            avg = _row_get(closed, "atr_pct_avg")
            if not _isnan(atr_pct) and not _isnan(avg) and float(avg) > 0:
                ratio = float(atr_pct) / float(avg)
                target_atr = self.cfg.atr_min_pct * max(
                    0.7, min(1.5, 1.0 / max(0.5, ratio)))
                if abs(target_atr - state.current_atr_min_pct) > 0.0005:
                    state.current_atr_min_pct = target_atr
        if self.cfg.adaptive_consec_scaling:
            recent = ctx.portfolio.last_trades[-30:]
            base = self.cfg.consec_loss_pause
            if len(recent) >= 15:
                arr = np.array([float(t.get("r", 0) or 0) for t in recent])
                sd = float(arr.std())
                sharpe = float(arr.mean() / sd) if sd > 1e-9 else 0.0
                # Sharpe négatif → pause PLUS TÔT (seuil plus bas).
                target = int(round(float(np.clip(
                    base * (1.0 + sharpe * 0.3), max(2, base - 2), base + 2))))
                if target != state.current_consec_pause:
                    self.logger.info(
                        f"[ADAPT] consec_pause {state.current_consec_pause} "
                        f"→ {target} (sharpe={sharpe:.2f})")
                    state.current_consec_pause = target
            else:
                state.current_consec_pause = base
        state.last_update_ts = time.time()

    def effective_freshness(self, state: AdaptiveState, base: int) -> int:
        if not self.cfg.adaptive_enabled:
            return base
        return min(state.current_freshness_min, base)

    def effective_atr_min(self, state: AdaptiveState, base: float) -> float:
        if not self.cfg.adaptive_enabled or not self.cfg.adaptive_atr_scaling:
            return base
        return state.current_atr_min_pct

    def effective_consec_pause(self, state: AdaptiveState, base: int) -> int:
        if not self.cfg.adaptive_enabled or not self.cfg.adaptive_consec_scaling:
            return base
        return state.current_consec_pause


# ══════════════════════════════════════════════════════════════════════
# SECTION 9 — INDICATORS
# ══════════════════════════════════════════════════════════════════════

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def macd_calc(series: pd.Series, fast: int = 12, slow: int = 26,
              signal: int = 9):
    ef = series.ewm(span=fast, adjust=False).mean()
    es = series.ewm(span=slow, adjust=False).mean()
    line = ef - es
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def atr_calc(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def bollinger(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width = (upper - lower) / mid.replace(0, np.nan)
    return mid, upper, lower, width


def adx_calc(df: pd.DataFrame, period: int = 14):
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr_w = tr.ewm(alpha=1/period, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1/period, adjust=False).mean() / atr_w.replace(0, 1e-12)
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1/period, adjust=False).mean() / atr_w.replace(0, 1e-12)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, 1e-12)
    return dx.ewm(alpha=1/period, adjust=False).mean(), pdi, mdi


def obv_calc(df: pd.DataFrame, ema_span: int = 20):
    obv_line = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
    obv_ema = obv_line.ewm(span=ema_span, adjust=False).mean()
    return obv_line, obv_ema, obv_ema.diff(5)


def vwap_calc(df: pd.DataFrame, period: int = 24) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol_sum = df["volume"].rolling(period).sum().replace(0, np.nan)
    return (tp * df["volume"]).rolling(period).sum() / vol_sum


def compute_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Indicateurs strictement causaux (aucun shift négatif)."""
    df = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    c = df["close"]
    df["ema_fast"] = ema(c, cfg.fast_ema)
    df["ema_slow"] = ema(c, cfg.slow_ema)
    df["ema_trend"] = ema(c, cfg.trend_ema)
    df["ema_trend_slope"] = df["ema_trend"].pct_change(5)
    df["rsi"] = rsi(c, cfg.rsi_period)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd_calc(
        c, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    df["atr"] = atr_calc(df, cfg.atr_period)
    df["atr_pct"] = df["atr"] / c.replace(0, np.nan)
    df["atr_pct_avg"] = df["atr_pct"].rolling(200, min_periods=50).mean()
    df["atr_rank"] = df["atr_pct"].rolling(
        cfg.atr_rank_window, min_periods=50).rank(pct=True)
    df["bb_mid"], df["bb_upper"], df["bb_lower"], df["bb_width"] = bollinger(
        c, cfg.bb_period, cfg.bb_std)
    df["bb_width_avg"] = df["bb_width"].rolling(50).mean()
    df["adx"], df["plus_di"], df["minus_di"] = adx_calc(df, cfg.adx_period)
    df["vol_ma"] = df["volume"].rolling(cfg.vol_ma_period).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma"].replace(0, np.nan)
    df["obv"], df["obv_ema"], df["obv_slope"] = obv_calc(df, cfg.obv_ema_span)
    df["vwap_24"] = vwap_calc(df, cfg.vwap_period)
    df["last_swing_low"] = df["low"].rolling(
        cfg.swing_window, min_periods=cfg.swing_window).min().shift(1)
    df["ret"] = np.log(c / c.shift(1))
    bpd = _bars_per_day(cfg.timeframe)
    df["realized_vol"] = (df["ret"].rolling(max(bpd, 2)).std()
                          * math.sqrt(_annualization_factor(cfg.timeframe)))
    return df


# ══════════════════════════════════════════════════════════════════════
# SECTION 10 — SIGNAL
# ══════════════════════════════════════════════════════════════════════

_SIGNAL_REQUIRED = ("ema_fast", "ema_slow", "ema_trend", "atr", "atr_pct",
                    "adx", "plus_di", "minus_di", "bb_width", "bb_width_avg",
                    "bb_upper", "bb_lower", "rsi", "macd", "macd_signal",
                    "macd_hist", "obv_slope", "ema_trend_slope")


def detect_regime(c: Any, cfg: Config) -> str:
    if any(_isnan(_row_get(c, k)) for k in ("adx", "bb_width", "bb_width_avg",
                                            "plus_di", "minus_di")):
        return "UNCLEAR"
    if float(c["adx"]) >= cfg.adx_trend_threshold:
        if c["plus_di"] > c["minus_di"]:
            return "TREND_UP"
        if c["minus_di"] > c["plus_di"]:
            return "TREND_DOWN"
    if (float(c["adx"]) <= cfg.adx_range_threshold
            and float(c["bb_width"]) < float(c["bb_width_avg"]) * 1.2):
        return "RANGE"
    return "UNCLEAR"


def adaptive_rsi_bounds(rank, cfg: Config) -> Tuple[float, float]:
    if not cfg.rsi_adaptive or _isnan(rank):
        return 45.0, 70.0
    if rank > 0.75:
        return 35.0, 65.0
    if rank > 0.50:
        return 40.0, 68.0
    if rank > 0.25:
        return 45.0, 70.0
    return 50.0, 75.0


def _assign_tier(score: int, cfg: Config) -> Optional[str]:
    for tier, threshold in sorted(cfg.tier_thresholds, key=lambda x: -x[1]):
        if score >= threshold:
            return tier
    return None


def _above_vwap(c: Any) -> int:
    v = _row_get(c, "vwap_24")
    return int(not _isnan(v) and c["close"] > v)


def _score_trend(c: Any, p: Any) -> int:
    cross = False
    try:
        cross = (not _isnan(p["macd"])) and (not _isnan(p["macd_signal"])) \
            and (p["macd"] < p["macd_signal"]) \
            and (c["macd"] > c["macd_signal"])
    except Exception:
        cross = False
    return min(int(sum([
        12 * int(c["macd"] > c["macd_signal"]),
        18 * int(bool(cross)),
        5 * int(c["macd_hist"] > p["macd_hist"]),
        12 * int(c["adx"] >= 30),
        8 * int(c["adx"] >= 35),
        10 * int(c["ema_trend_slope"] > 0),
        5 * int(c["vol_ratio"] >= 1.8),
        10 * int(c["obv_slope"] > 0),
        10 * _above_vwap(c),
        10 * int(c["close"] > c["ema_fast"]),
    ])), 100)


def _score_breakout(c: Any, p: Any) -> int:
    return min(int(sum([
        20 * int(c["close"] > c["bb_upper"]),
        20 * int(c["vol_ratio"] >= 2.5),
        15 * int(c["atr"] > p["atr"] * 1.05),
        15 * int(c["obv_slope"] > 0),
        15 * int(c["adx"] >= 25),
        15 * _above_vwap(c),
    ])), 100)


def _score_range(c: Any, p: Any) -> int:
    return min(int(sum([
        20 * int(c["rsi"] <= 30),
        15 * int(c["rsi"] <= 25),
        20 * int(c["close"] <= c["bb_lower"] * 1.005),
        15 * int(c["obv_slope"] > 0),
        10 * int(c["vol_ratio"] < 1.5),
        10 * int(p["close"] < p["open"]),
        10 * int(c["close"] > p["close"]),
    ])), 100)


def _score_pullback(c: Any, p: Any) -> int:
    dist = abs(c["close"] - c["ema_slow"]) / c["atr"] if c["atr"] > 0 else 999
    return min(int(sum([
        12 * int(c["rsi"] <= 45),
        12 * int(c["rsi"] <= 40),
        12 * int(c["close"] > c["open"]),
        10 * int(p["close"] < p["open"]),
        12 * int(dist <= 0.8),
        6 * int(0.8 < dist <= 1.5),
        10 * int(c["adx"] >= 25),
        6 * int(c["adx"] >= 30),
        10 * int(c["obv_slope"] > 0),
        10 * _above_vwap(c),
    ])), 100)


def _essential_trend(c, p, rank, cfg, atr_min_pct=None):
    atr_min = atr_min_pct if atr_min_pct is not None else cfg.atr_min_pct
    if c["ema_fast"] <= c["ema_slow"]:
        return False, "ema_align"
    if c["ema_slow"] <= c["ema_trend"]:
        return False, "below_trend_ema"
    if c["adx"] < cfg.adx_trend_threshold:
        return False, "adx_low"
    if c["atr_pct"] < atr_min:
        return False, "atr_low"
    lo, hi = adaptive_rsi_bounds(rank, cfg)
    if not (lo - 5 <= c["rsi"] <= hi + 5):
        return False, "rsi_extreme"
    if c["vol_ratio"] < cfg.vol_confirm_ratio:
        return False, "vol_low"
    if c["vol_ratio"] >= cfg.vol_capitulation and c["close"] < c["open"]:
        return False, "capitulation"
    return True, None


def _essential_breakout(c, p, cfg):
    if p["bb_width"] > c["bb_width_avg"] * 1.1:
        return False, "no_squeeze"
    if p["close"] > p["bb_upper"]:
        return False, "already_broken"
    if c["close"] <= c["bb_upper"]:
        return False, "no_breakout"
    if c["atr"] <= p["atr"]:
        return False, "atr_flat"
    if c["vol_ratio"] < cfg.vol_breakout_ratio:
        return False, "vol_weak"
    return True, None


def _essential_range(c, p, cfg):
    if c["close"] > c["bb_lower"] * 1.005:
        return False, "not_near_bottom"
    if c["rsi"] > 35:
        return False, "rsi_high"
    if c["close"] <= p["close"]:
        return False, "no_reversal"
    if c["vol_ratio"] >= cfg.vol_capitulation:
        return False, "vol_too_high"
    if c["atr_pct"] <= cfg.atr_min_pct * 0.5:
        return False, "atr_too_low"
    return True, None


def _essential_pullback(c, p, cfg, atr_min_pct=None):
    atr_min = atr_min_pct if atr_min_pct is not None else cfg.atr_min_pct
    if c["ema_fast"] <= c["ema_slow"]:
        return False, "ema_align"
    if c["ema_slow"] <= c["ema_trend"]:
        return False, "below_trend_ema"
    if not (cfg.pullback_rsi_lo <= c["rsi"] <= cfg.pullback_rsi_hi):
        return False, "rsi_not_pullback"
    if _isnan(c["atr"]) or c["atr"] <= 0:
        return False, "no_atr"
    if c["atr_pct"] < atr_min:
        return False, "atr_low"
    if abs(c["close"] - c["ema_slow"]) > c["atr"] * cfg.pullback_max_dist_atr:
        return False, "far_from_ema"
    if c["close"] <= p["close"]:
        return False, "no_bounce"
    if p["close"] >= p["open"] * cfg.pullback_prev_bullish_buffer:
        return False, "prev_too_bullish"
    return True, None


def module_enabled(ctx: BotContext, mod: str,
                   now: Optional[datetime] = None) -> bool:
    info = ctx.portfolio.per_module.get(mod) or {}
    du = _parse_iso(info.get("disabled_until"))
    if du is None:
        if info.get("disabled_until"):
            info["disabled_until"] = None
        return True
    if (now or _utcnow()) >= du:
        info["disabled_until"] = None
        return True
    return False


_module_enabled = module_enabled


def min_signal_bars(cfg: Config) -> int:
    return max(cfg.trend_ema, cfg.atr_period, cfg.bb_period,
               cfg.adx_period * 3, cfg.vol_ma_period, cfg.vwap_period,
               cfg.atr_rank_window // 2) + 20


def generate_signal_from_rows(c: Any, p: Any, n_bars: int, htf_bias, btc_bias,
                              ctx: BotContext, cfg: Config,
                              atr_min_pct: Optional[float] = None,
                              now: Optional[datetime] = None) -> Signal:
    """c = dernière bougie FERMÉE, p = la précédente (dict ou Series)."""
    if n_bars < min_signal_bars(cfg):
        return Signal("NONE", "UNCLEAR", "", "", 0, "min_candles")
    if any(_isnan(_row_get(c, col)) for col in _SIGNAL_REQUIRED):
        return Signal("NONE", "UNCLEAR", "", "", 0, "indicators_nan")
    if cfg.htf_bias_enabled and htf_bias != "UP":
        return Signal("NONE", "UNCLEAR", "", "", 0, "htf_not_up")
    if cfg.btc_bias_enabled and btc_bias == "DOWN":
        return Signal("NONE", "UNCLEAR", "", "", 0, "btc_down")
    if (c["close"] - c["ema_fast"]) / c["ema_fast"] > cfg.max_dist_ema_fast_pct:
        return Signal("NONE", "UNCLEAR", "", "", 0, "extended")
    regime = detect_regime(c, cfg)
    candidates: List[Tuple[str, str, int]] = []
    rejects: Dict[str, Tuple[int, str]] = {}

    def _try(mod: str):
        if not module_enabled(ctx, mod, now):
            rejects[mod] = (0, "module_disabled")
            return
        if mod == "trend":
            if regime != "TREND_UP":
                return
            ok, r = _essential_trend(c, p, _row_get(c, "atr_rank"), cfg,
                                     atr_min_pct)
            score_fn = _score_trend
        elif mod == "pullback":
            if regime != "TREND_UP":
                return
            ok, r = _essential_pullback(c, p, cfg, atr_min_pct)
            score_fn = _score_pullback
        elif mod == "breakout":
            if regime not in ("UNCLEAR", "RANGE"):
                return
            ok, r = _essential_breakout(c, p, cfg)
            score_fn = _score_breakout
        elif mod == "range":
            if regime != "RANGE":
                return
            ok, r = _essential_range(c, p, cfg)
            score_fn = _score_range
        else:
            return
        if not ok:
            rejects[mod] = (0, r or "rejected")
            return
        s = score_fn(c, p)
        tier = _assign_tier(s, cfg)
        if not tier:
            rejects[mod] = (s, "score_low")
            return
        candidates.append((mod, tier, s))

    for m in ("trend", "pullback", "breakout", "range"):
        _try(m)

    if not candidates:
        priority = {"TREND_UP": ["trend", "pullback"],
                    "RANGE": ["range", "breakout"],
                    "UNCLEAR": ["breakout"]}.get(regime, [])
        for mod in priority:
            if mod in rejects:
                s, r = rejects[mod]
                return Signal("NONE", regime, mod, "", s, r)
        if rejects:
            mod = next(iter(rejects))
            s, r = rejects[mod]
            return Signal("NONE", regime, mod, "", s, r)
        return Signal("NONE", regime, "", "", 0, "no_module")

    candidates.sort(key=lambda x: ({"S": 3, "A": 2, "B": 1}.get(x[1], 0),
                                   x[2]), reverse=True)
    mod, tier, s = candidates[0]
    return Signal("BUY", regime, mod, tier, s, None)


def generate_signal(df: pd.DataFrame, htf_bias, btc_bias,
                    ctx: BotContext, cfg: Config,
                    atr_min_pct: Optional[float] = None,
                    now: Optional[datetime] = None) -> Signal:
    if len(df) < 3:
        return Signal("NONE", "UNCLEAR", "", "", 0, "min_candles")
    return generate_signal_from_rows(df.iloc[-2], df.iloc[-3], len(df),
                                     htf_bias, btc_bias, ctx, cfg,
                                     atr_min_pct, now)


# ══════════════════════════════════════════════════════════════════════
# SECTION 11 — RISK
# ══════════════════════════════════════════════════════════════════════

RR_BY_MODULE = {"trend": 2.5, "pullback": 2.0, "breakout": 2.0, "range": 1.5}


class RiskEngine:
    def __init__(self, cfg: Config, logger: Optional[logging.Logger] = None):
        self.cfg = cfg
        self.logger = logger or logging.getLogger("risk.null")
        if logger is None and not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())

    @staticmethod
    def _extract_r_multiples(trades: List[Dict[str, Any]]) -> List[float]:
        rs = []
        for t in trades:
            try:
                if t.get("r") is not None:
                    rs.append(float(t["r"]))
                    continue
            except Exception:
                pass
            rq = float(t.get("risk_quote", 0) or 0)
            pnl = float(t.get("pnl", 0) or 0)
            if rq > 1e-12:
                rs.append(pnl / rq)
        return rs

    def kelly_base(self, ctx: BotContext, module: str) -> float:
        trades = [t for t in ctx.portfolio.last_trades
                  if t.get("module") == module][-self.cfg.kelly_window:]
        rs = self._extract_r_multiples(trades)
        if len(rs) < self.cfg.kelly_min_trades:
            return self.cfg.risk_base_pct
        wins = [x for x in rs if x > 0]
        losses = [abs(x) for x in rs if x <= 0]
        if not losses:
            return self.cfg.risk_max_pct
        if not wins:
            return self.cfg.risk_min_pct
        w = len(wins) / len(rs)
        aw = sum(wins) / len(wins)
        al = sum(losses) / len(losses)
        if al <= 1e-12:
            return self.cfg.risk_base_pct
        rr = aw / al
        f = w - (1 - w) / rr
        # Kelly en fraction du risque : borné ensuite par risk_min/max.
        return max(0.0, f * self.cfg.kelly_fraction)

    def vol_target_base(self, realized_vol: Optional[float]) -> float:
        if realized_vol is None or _isnan(realized_vol) or realized_vol <= 1e-6:
            return self.cfg.risk_base_pct
        return self.cfg.risk_base_pct * (self.cfg.vol_target_annual
                                         / float(realized_vol))

    def regime_multiplier(self, regime: str) -> float:
        return self.cfg.regime_mult_map.get(regime, 0.5)

    def dd_derisk_multiplier(self, ctx: BotContext,
                             equity: Optional[float]) -> float:
        ref = float(ctx.risk.daily_start_equity or 0)
        if equity is None or ref <= 0:
            return 1.0
        dd = max(0.0, (ref - equity) / ref)
        curve = sorted(self.cfg.dd_derisk_curve, key=lambda x: x[0])
        for threshold, mult in curve:
            if dd <= threshold:
                return mult
        return curve[-1][1] if curve else 1.0

    def signal_freshness_mult(self, closed_ts: int,
                              max_age_min: Optional[int] = None,
                              now_ms: Optional[int] = None
                              ) -> Tuple[float, Optional[str]]:
        max_age = (max_age_min if max_age_min is not None
                   else self.cfg.max_signal_age_min)
        candle_close_ts = int(closed_ts) + _timeframe_ms(self.cfg.timeframe)
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        age_min = max(0.0, (now_ms - candle_close_ts) / 60_000)
        if age_min > max_age:
            return 0.0, f"signal_stale_{age_min:.0f}min"
        ratio = age_min / max(max_age, 1)
        return max(0.5, 1.0 - self.cfg.signal_age_decay_pct * ratio), None

    def module_sharpe(self, ctx: BotContext,
                      module: str) -> Tuple[float, int]:
        trades = [t for t in ctx.portfolio.last_trades
                  if t.get("module") == module][-self.cfg.module_sharpe_window:]
        rs = self._extract_r_multiples(trades)
        if len(rs) < self.cfg.module_sharpe_min_trades:
            return 0.0, len(rs)
        arr = np.array(rs, dtype=float)
        if arr.std() <= 1e-9:
            return 0.0, len(rs)
        return float(arr.mean() / arr.std()), len(rs)

    def base_risk(self, ctx: BotContext, module: str,
                  realized_vol: Optional[float] = None) -> float:
        mode = self.cfg.sizing_mode
        if mode == "kelly":
            base = self.kelly_base(ctx, module)
        elif mode == "vol_target":
            base = self.vol_target_base(realized_vol)
        else:
            recent = [t for t in ctx.portfolio.last_trades
                      if t.get("module") == module][-20:]
            if len(recent) < 8:
                base = self.cfg.risk_base_pct
            else:
                wr = sum(1 for t in recent
                         if float(t.get("pnl", 0) or 0) > 0) / len(recent)
                base = (self.cfg.risk_max_pct if wr >= 0.55
                        else self.cfg.risk_min_pct if wr < 0.40
                        else self.cfg.risk_base_pct)
        trades = [t for t in ctx.portfolio.last_trades
                  if t.get("module") == module][-30:]
        if len(trades) >= 5:
            rs = self._extract_r_multiples(trades)
            if rs:
                exp_r = float(np.mean(rs))
                wins = [x for x in rs if x > 0]
                losses = [x for x in rs if x <= 0]
                total_losses_abs = abs(sum(losses))
                if total_losses_abs > 0:
                    pf = sum(wins) / total_losses_abs
                elif wins:
                    pf = 10.0
                else:
                    pf = 0.0
                base *= 1.0 + max(-0.35, min(0.20, exp_r * 0.35))
                if pf < 1.0:
                    base *= 0.70
                elif pf > 1.50:
                    base *= 1.05
        cl = int(ctx.portfolio.consec_losses or 0)
        if cl >= 2:
            base *= 0.75
        if cl >= 3:
            base *= 0.60
        if cl >= 4:
            base *= 0.50
        return max(self.cfg.risk_min_pct,
                   min(self.cfg.risk_max_pct,
                       self.cfg.risk_absolute_max_pct, base))

    def position_size(self, price: float, sl_dist: float, equity: float,
                      risk_pct: float,
                      max_notional: Optional[float] = None) -> float:
        """Quantité telle que la perte au stop (frais + slippage inclus)
        vaille equity × risk_pct, plafonnée par le notionnel disponible."""
        if price <= 0 or sl_dist <= 0 or equity <= 0:
            return 0.0
        eff = min(max(risk_pct, 0.0), self.cfg.risk_absolute_max_pct)
        risk_amount = equity * eff
        fee_buf = (price * self.cfg.fee_rate
                   + (price - sl_dist) * self.cfg.fee_rate)
        slip_buf = price * (self.cfg.entry_slippage_buffer_pct
                            + self.cfg.exit_slippage_buffer_pct)
        unit_risk = sl_dist + fee_buf + slip_buf
        if unit_risk <= 0:
            return 0.0
        raw = risk_amount / unit_risk
        cap_notional = equity * self.cfg.max_position_capital_pct
        if max_notional is not None:
            cap_notional = min(cap_notional, max_notional)
        return max(0.0, min(raw, cap_notional / price))

    def sl_distance(self, module: str, atr_val: float, price: float,
                    closed: Any, cfg: Config) -> Optional[float]:
        if _isnan(atr_val) or atr_val <= 0 or price <= 0:
            return None
        profiles = {
            "trend": {"sl_mult": 1.5, "use_swing": True},
            "pullback": {"sl_mult": 1.2, "use_swing": False},
            "breakout": {"sl_mult": 1.8, "use_swing": True},
            "range": {"sl_mult": 1.0, "use_swing": False},
            "recovered": {"sl_mult": 1.5, "use_swing": False},
        }
        prof = profiles.get(module, profiles["trend"])
        atr_sl = atr_val * prof["sl_mult"]
        if prof["use_swing"]:
            sw = _row_get(closed, "last_swing_low")
            if not _isnan(sw):
                d = price - float(sw)
                if 0 < d < price * cfg.max_sl_dist_pct:
                    atr_sl = max(atr_sl, d + atr_val * 0.3)
        return max(min(atr_sl, price * cfg.max_sl_dist_pct), price * 0.001)

    def check_circuit_breakers(self, ctx: BotContext, equity: Optional[float],
                               consec_pause: Optional[int] = None,
                               now: Optional[datetime] = None
                               ) -> Tuple[bool, Optional[str]]:
        """Retourne (bloque_les_entrées, raison). Fail-closed : une equity
        illisible bloque les entrées (sans halt)."""
        n = now or _utcnow()
        day, week = _day_key(n), _week_key(n)
        r = ctx.risk
        if r.halted and r.halt_kind == HaltKind.DAILY_DD and r.halted_date != day:
            clear_halt(ctx)
        if r.halted and r.halt_kind == HaltKind.WEEKLY_DD \
                and r.halted_week != week:
            clear_halt(ctx)
        if r.halted:
            return True, r.halt_reason or "HALTED"
        if r.paused_until:
            pu = _parse_iso(r.paused_until)
            if pu is not None and n < pu:
                return True, r.cooldown_reason or "PAUSED"
            r.paused_until = None
            r.cooldown_reason = None
        if equity is None or equity <= 0:
            return True, "EQUITY_UNAVAILABLE"
        if r.daily_start_date != day or not r.daily_start_equity:
            r.daily_start_date = day
            r.daily_start_equity = equity
        if r.weekly_start_date != week or not r.weekly_start_equity:
            r.weekly_start_date = week
            r.weekly_start_equity = equity
        d_eq = float(r.daily_start_equity or 0)
        w_eq = float(r.weekly_start_equity or 0)
        if d_eq > 0 and equity <= d_eq * (1 - self.cfg.max_daily_dd):
            reason = f"Daily DD dépassé ({(equity/d_eq-1)*100:.2f}%)"
            halt_ctx(ctx, reason, HaltKind.DAILY_DD, now=n)
            return True, reason
        if w_eq > 0 and equity <= w_eq * (1 - self.cfg.max_weekly_dd):
            reason = f"Weekly DD dépassé ({(equity/w_eq-1)*100:.2f}%)"
            halt_ctx(ctx, reason, HaltKind.WEEKLY_DD, now=n)
            return True, reason
        threshold = (consec_pause if consec_pause is not None
                     else self.cfg.consec_loss_pause)
        if ctx.portfolio.losses_since_pause >= threshold:
            r.paused_until = (n + timedelta(
                hours=self.cfg.consec_loss_pause_hours)).isoformat()
            r.cooldown_reason = (f"{ctx.portfolio.losses_since_pause} "
                                 f"pertes consécutives")
            # La pause « consomme » la série : pas de ré-armement infini.
            ctx.portfolio.losses_since_pause = 0
            return True, r.cooldown_reason
        return False, None


def in_cooldown(ctx: BotContext, now: Optional[datetime] = None) -> bool:
    until = _parse_iso(ctx.risk.cooldown_until)
    if until is None:
        ctx.risk.cooldown_until = None
        return False
    if (now or _utcnow()) < until:
        return True
    ctx.risk.cooldown_until = None
    ctx.risk.cooldown_reason = None
    return False


def in_flash_cooldown(ctx: BotContext, now: Optional[datetime] = None) -> bool:
    until = _parse_iso(ctx.risk.flash_cooldown_until)
    if until is None:
        ctx.risk.flash_cooldown_until = None
        return False
    if (now or _utcnow()) < until:
        return True
    ctx.risk.flash_cooldown_until = None
    return False


def detect_flash_move(ref_close: float, price: float, ctx: BotContext,
                      cfg: Config, now: Optional[datetime] = None) -> bool:
    if ref_close <= 0 or price <= 0:
        return False
    if abs(price / ref_close - 1) >= cfg.flash_move_pct:
        ctx.risk.flash_cooldown_until = (
            (now or _utcnow()) + timedelta(minutes=cfg.flash_cooldown_min)
        ).isoformat()
        return True
    return False


def record_closed_trade(ctx: BotContext, cfg: Config, risk: RiskEngine,
                        trade: Dict[str, Any], now: Optional[datetime] = None,
                        logger: Optional[logging.Logger] = None) -> None:
    """Statistiques communes live / paper / backtest pour un trade CLOS
    (toutes jambes confondues). trade: pnl, r, module, tier, reason,
    entry_adx, risk_quote, entry_price, exit_price."""
    n = now or _utcnow()
    pf = ctx.portfolio
    pnl = float(trade.get("pnl", 0.0))
    if pnl > 0:
        pf.stats_wins += 1
        pf.consec_losses = 0
        pf.losses_since_pause = 0
    else:
        pf.stats_losses += 1
        pf.consec_losses += 1
        pf.losses_since_pause += 1
    pf.stats_total_pnl += pnl
    entry = dict(trade)
    entry.setdefault("ts", n.isoformat())
    pf.last_trades.append(entry)
    by_module: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in pf.last_trades:
        by_module[t.get("module") or "_orphan"].append(t)
    capped: List[Dict[str, Any]] = []
    for trades in by_module.values():
        capped.extend(trades[-cfg.last_trades_per_module:])
    capped.sort(key=lambda x: x.get("ts") or "")
    pf.last_trades = capped
    module = trade.get("module") or ""
    if module:
        info = pf.per_module.setdefault(
            module, {"trades": 0, "wins": 0, "pnl": 0.0,
                     "disabled_until": None})
        info["trades"] += 1
        info["wins"] += int(pnl > 0)
        info["pnl"] += pnl
        sharpe, cnt = risk.module_sharpe(ctx, module)
        if cnt >= cfg.module_sharpe_min_trades:
            info["sharpe"] = sharpe
            if sharpe < cfg.module_min_sharpe and not info.get("disabled_until"):
                info["disabled_until"] = (
                    n + timedelta(hours=cfg.module_reactivation_hours)
                ).isoformat()
                if logger:
                    logger.warning(
                        f"[MODULE] {module} désactivé: Sharpe={sharpe:.2f}")
    tier = trade.get("tier") or ""
    if tier:
        ti = pf.per_tier.setdefault(tier, {"trades": 0, "wins": 0, "pnl": 0.0})
        ti["trades"] += 1
        ti["wins"] += int(pnl > 0)
        ti["pnl"] += pnl
    reason = str(trade.get("reason") or "")
    entry_adx = trade.get("entry_adx")
    if pnl < 0 and "BARRIER" in reason:
        if entry_adx is None or _isnan(entry_adx) \
                or float(entry_adx) < cfg.post_loss_skip_adx:
            ctx.risk.cooldown_until = (
                n + timedelta(hours=cfg.post_loss_cooldown_hours)).isoformat()
            ctx.risk.cooldown_reason = f"Post-loss ({reason})"


def break_even_stop(p: Position, cfg: Config) -> float:
    """Stop de break-even couvrant réellement le coût de revient :
    une sortie au stop (frais + slippage) ne doit pas être une perte."""
    net_be = p.cost_basis / max(1e-9, 1 - cfg.fee_rate
                                - cfg.exit_slippage_buffer_pct)
    return max(p.buy_price * (1 + cfg.break_even_offset), net_be)


def trailing_stop(p: Position, atr: float, cfg: Config) -> Optional[float]:
    if _isnan(atr) or atr <= 0 or p.high_since_entry is None:
        return None
    return float(p.high_since_entry) - cfg.trail_atr_mult * float(atr)


# ══════════════════════════════════════════════════════════════════════
# SECTION 12 — EXECUTION
# ══════════════════════════════════════════════════════════════════════
#
# Invariants :
#   I1. En live, une position ouverte est protégée par un ordre exchange
#       (OCO natif ou stop) ; sinon elle est liquidée (panic) — jamais nue.
#   I2. Aucun ordre de vente « marché » n'est envoyé tant que la protection
#       n'est pas confirmée terminale (le solde serait bloqué).
#   I3. Chaque fill est comptabilisé une seule fois (recorded_fills).
#   I4. Tout ordre d'entrée/sortie marché est précédé d'une intention
#       persistée (pending_order) résolue par client-id.
#   I5. Les quantités vendues/protégées sont plafonnées au solde libre du
#       bot (hors EXTERNAL_BASE_RESERVE).

def update_extremes(p: Position, df: pd.DataFrame, live_price: float) -> None:
    if p.high_since_entry is None:
        p.high_since_entry = p.buy_price
    if p.low_since_sl_update is None:
        p.low_since_sl_update = p.buy_price
    if len(df) >= 2:
        closed = df.iloc[-2]
        closed_ts = int(closed["ts"])
        entry_ts = p.entry_candle_ts
        sl_ts = p.sl_update_candle_ts or entry_ts
        if entry_ts is not None and closed_ts > entry_ts:
            p.high_since_entry = max(float(p.high_since_entry),
                                     float(closed["high"]))
        if sl_ts is not None and closed_ts > sl_ts:
            p.low_since_sl_update = min(float(p.low_since_sl_update),
                                        float(closed["low"]))
    p.high_since_entry = max(float(p.high_since_entry), live_price)
    p.low_since_sl_update = min(float(p.low_since_sl_update), live_price)


_update_extremes = update_extremes

_LEG_REASON = {"TP": "BARRIER_TP", "SL": "BARRIER_SL", "STOP": "BARRIER_SL"}


class ExecutionEngine:
    def __init__(self, cfg: Config, logger: logging.Logger,
                 exchange: ExchangeAdapter, store: Optional[Store],
                 notifier: Notifier, risk: RiskEngine):
        self.cfg = cfg
        self.logger = logger
        self.ex = exchange
        self.store = store
        self.notifier = notifier
        self.risk = risk
        self.context_key = "context"

    # ---------- Utilitaires ----------

    @property
    def live(self) -> bool:
        return self.cfg.run_mode == "live"

    def _persist(self, ctx: BotContext) -> None:
        if self.store is not None:
            self.store.save_context(ctx, self.cfg.run_mode, force=True,
                                    key=self.context_key)

    def _event(self, name: str, severity: str,
               payload: Dict[str, Any]) -> None:
        if self.store is not None:
            self.store.log_event(name, severity, payload, self.cfg.run_mode,
                                 durable=(severity == "CRITICAL"))

    def _halt(self, ctx: BotContext, reason: str,
              kind: str = HaltKind.INTEGRITY, orphan: bool = False,
              payload: Optional[Dict[str, Any]] = None) -> None:
        halt_ctx(ctx, reason, kind, orphan=orphan)
        self.logger.critical(f"[HALT] {reason} ({kind})")
        self._event("halt", "CRITICAL",
                    {"reason": reason, "kind": kind, **(payload or {})})
        self.notifier(f"🛑 HALT {reason}", critical=True)
        self._persist(ctx)

    # ---------- Paper ----------

    def _paper_buy(self, ctx: BotContext, amount: float,
                   ref_price: float) -> OrderResult:
        px = ref_price * (1 + self.cfg.paper_slippage_pct)
        fee_rate = self.cfg.paper_fee_rate
        cash = float(ctx.portfolio.paper_cash)
        if amount * px * (1 + fee_rate) > cash:
            amount = self.ex.round_amount(cash / (px * (1 + fee_rate)))
        cost = amount * px
        fee = cost * fee_rate
        ctx.portfolio.paper_cash -= cost + fee
        ctx.portfolio.paper_base += amount
        self.ex.invalidate_balances()
        return OrderResult("PAPER", "", "closed", amount, px, cost,
                           0.0, fee, 0.0, True, "MARKET", "buy")

    def _paper_sell(self, ctx: BotContext, amount: float, price: float,
                    maker: bool = False) -> OrderResult:
        amount = min(float(amount), float(ctx.portfolio.paper_base))
        px = price if maker else price * (1 - self.cfg.paper_slippage_pct)
        proceeds = amount * px
        fee = proceeds * self.cfg.paper_fee_rate
        ctx.portfolio.paper_cash += proceeds - fee
        ctx.portfolio.paper_base = max(0.0, ctx.portfolio.paper_base - amount)
        self.ex.invalidate_balances()
        return OrderResult("PAPER", "", "closed", amount, px, proceeds,
                           0.0, fee, 0.0, True, "MARKET", "sell")

    # ---------- Entrée ----------

    def enter(self, ctx: BotContext, closed: Any, sig: Signal,
              live_price: float, current_candle_ts: int,
              equity: Optional[float], btc_vol_mult: float = 1.0,
              freshness_max: Optional[int] = None) -> EntryResult:
        cfg = self.cfg
        if ctx.position.in_position or ctx.pending_order:
            return EntryResult.SKIPPED
        fresh_mult, stale = self.risk.signal_freshness_mult(
            int(closed["ts"]), freshness_max)
        if stale:
            self.logger.info(f"[ENTRY] rejet: {stale}")
            return EntryResult.SKIPPED
        if equity is None or equity <= 0:
            return EntryResult.SKIPPED
        quote_free = (self.ex.get_free_balance(cfg.quote, ctx, force=True)
                      if self.live else float(ctx.portfolio.paper_cash))
        max_notional = quote_free * (1 - cfg.min_cash_reserve_pct)
        if max_notional <= self.ex.min_notional() * 1.05:
            self.logger.info("[ENTRY] cash disponible insuffisant")
            return EntryResult.SKIPPED
        expected_slip = cfg.entry_slippage_buffer_pct
        if cfg.use_smart_buy:
            expected_slip = max(expected_slip, cfg.chaser_max_slippage_pct)
        sizing_price = live_price * (1 + expected_slip)
        sl_dist = self.risk.sl_distance(sig.module, float(closed["atr"]),
                                        sizing_price, closed, cfg)
        if sl_dist is None:
            return EntryResult.SKIPPED
        risk_pct = self.risk.base_risk(ctx, sig.module,
                                       _row_get(closed, "realized_vol"))
        tier_mult = cfg.tier_mult_map.get(sig.tier, 1.0)
        regime_mult = self.risk.regime_multiplier(sig.regime)
        dd_mult = self.risk.dd_derisk_multiplier(ctx, equity)
        effective_risk = min(
            risk_pct * tier_mult * regime_mult * dd_mult * fresh_mult
            * btc_vol_mult, cfg.risk_absolute_max_pct)
        if effective_risk <= 0:
            return EntryResult.SKIPPED
        amount = self.ex.round_amount(self.risk.position_size(
            sizing_price, sl_dist, equity, effective_risk, max_notional))
        if amount <= 0 or amount * sizing_price < self.ex.min_notional() * 1.05:
            self.logger.info("[ENTRY] taille sous le minimum négociable")
            return EntryResult.SKIPPED
        obi_used: Optional[float] = None
        if cfg.use_l2_filter:
            micro, obi, spread = self.ex.get_l2_state(force=True)
            obi_used = obi
            if (micro is None or spread > cfg.max_spread_tolerance
                    or obi < cfg.obi_toxic_threshold):
                self.logger.info(
                    f"[ENTRY] L2 block micro={micro} spread={spread:.5f} "
                    f"obi={obi:.3f}")
                return EntryResult.SKIPPED
        entry_adx = _row_get(closed, "adx")
        intent = {
            "kind": "entry", "cids": [], "ts": _utcnow_iso(),
            "ts_epoch": time.time(), "amount": amount, "sl_dist": sl_dist,
            "rr": RR_BY_MODULE.get(sig.module, 2.0), "module": sig.module,
            "regime": sig.regime, "tier": sig.tier, "score": int(sig.score),
            "entry_adx": None if _isnan(entry_adx) else float(entry_adx),
            "entry_obi": obi_used, "candle_ts": int(current_candle_ts),
            "eff_risk_pct": effective_risk * 100, "equity": equity,
            "mults": {"tier": tier_mult, "regime": regime_mult,
                      "dd": dd_mult, "fresh": fresh_mult,
                      "btc_vol": btc_vol_mult}}
        if not self.live:
            res = self._paper_buy(ctx, amount, live_price)
            return (EntryResult.OPENED if self._open_from_fill(ctx, intent, [res])
                    else EntryResult.ORDER_SENT)
        ctx.pending_order = intent
        ctx.state = BotState.OPENING.value
        self._persist(ctx)
        try:
            if cfg.use_smart_buy:
                results = self._smart_buy(ctx, intent, amount)
            else:
                cid = self.ex.new_client_id(CID_ENTRY_MARKET)
                intent["cids"].append(cid)
                self._persist(ctx)
                results = [self.ex.market_buy(amount, cid)]
        except AmbiguousOrder as e:
            self._halt(ctx, "AMBIGUOUS_BUY",
                       payload={"error": str(e), "cid": e.client_id})
            return EntryResult.ORDER_SENT
        except ValueError as e:
            self.logger.info(f"[ENTRY] ordre non envoyé: {e}")
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return EntryResult.SKIPPED
        except (ccxt.InsufficientFunds, ccxt.InvalidOrder,
                ccxt.BadRequest) as e:
            self.logger.warning(f"[ENTRY] ordre rejeté: {e}")
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return EntryResult.ORDER_SENT
        opened = self._open_from_fill(ctx, intent, results)
        return EntryResult.OPENED if opened else EntryResult.ORDER_SENT

    def enter_planned(self, ctx: BotContext, amount: float, ref_price: float,
                      sl_abs: float, tp_abs: float, meta: Dict[str, Any],
                      candle_ts: int) -> EntryResult:
        """Entrée dont la taille et les stops sont décidés par une stratégie
        externe (TrendGuard), exécutée avec les mêmes garanties que enter() :
        intention persistée, frais en base, protection immédiate."""
        if ctx.position.in_position or ctx.pending_order:
            return EntryResult.SKIPPED
        amount = self.ex.round_amount(amount)
        if amount <= 0 or amount * ref_price < self.ex.min_notional() * 1.05:
            self.logger.info(f"[ENTRY] {self.cfg.symbol}: taille sous le minimum")
            return EntryResult.SKIPPED
        intent = {"kind": "entry", "cids": [], "ts": _utcnow_iso(),
                  "ts_epoch": time.time(), "amount": amount,
                  "sl_abs": sl_abs, "tp_abs": tp_abs,
                  "candle_ts": int(candle_ts), "ref_price": ref_price,
                  "module": meta.get("module", "planned"),
                  "regime": meta.get("regime", ""), "tier": meta.get("tier", ""),
                  "score": int(meta.get("score", 0)),
                  "soft_stop": meta.get("soft_stop"),
                  "risk_per_unit": meta.get("risk_per_unit"),
                  "equity": meta.get("equity"),
                  "eff_risk_pct": meta.get("eff_risk_pct")}
        if not self.live:
            res = self._paper_buy(ctx, amount, ref_price)
            return (EntryResult.OPENED if self._open_from_fill(ctx, intent, [res])
                    else EntryResult.ORDER_SENT)
        ctx.pending_order = intent
        ctx.state = BotState.OPENING.value
        cid = self.ex.new_client_id(CID_ENTRY_MARKET)
        intent["cids"].append(cid)
        self._persist(ctx)
        try:
            results = [self.ex.market_buy(amount, cid)]
        except AmbiguousOrder as e:
            self._halt(ctx, "AMBIGUOUS_BUY",
                       payload={"error": str(e), "cid": e.client_id})
            return EntryResult.ORDER_SENT
        except ValueError as e:
            self.logger.info(f"[ENTRY] ordre non envoyé: {e}")
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return EntryResult.SKIPPED
        except (ccxt.InsufficientFunds, ccxt.InvalidOrder,
                ccxt.BadRequest) as e:
            self.logger.warning(f"[ENTRY] ordre rejeté: {e}")
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return EntryResult.ORDER_SENT
        opened = self._open_from_fill(ctx, intent, results)
        return EntryResult.OPENED if opened else EntryResult.ORDER_SENT

    def _smart_buy(self, ctx: BotContext, intent: Dict[str, Any],
                   amount: float) -> List[OrderResult]:
        """Chaser limit : chaque ordre est annulé puis relu une seule fois
        (état final) → aucun double comptage."""
        cfg = self.cfg
        results: List[OrderResult] = []
        start_bid = self.ex.get_ticker()["bid"]
        max_price = start_bid * (1 + cfg.chaser_max_slippage_pct)
        filled_total = 0.0
        for attempt in range(cfg.chaser_max_attempts):
            bid = start_bid if attempt == 0 else self.ex.get_ticker()["bid"]
            if bid > max_price:
                self.logger.warning(f"[CHASE] prix évadé ({bid} > {max_price})")
                break
            remaining = self.ex.round_amount(amount - filled_total)
            if remaining <= 0 or remaining * bid < self.ex.min_notional():
                break
            cid = self.ex.new_client_id(CID_ENTRY_LIMIT)
            intent["cids"].append(cid)
            self._persist(ctx)
            try:
                self.ex.limit_buy(remaining, bid, cid)
            except (ccxt.InsufficientFunds, ccxt.InvalidOrder,
                    ccxt.BadRequest, ValueError) as e:
                self.logger.warning(f"[CHASE] limit rejeté: {e}")
                break
            self.ex.sleep(cfg.chaser_wait_sec)
            final = self._finalize_order(cid)
            results.append(final)
            filled_total += final.filled
            if filled_total >= amount * 0.999:
                break
        return results

    def _finalize_order(self, cid: str) -> OrderResult:
        last_exc: Optional[Exception] = None
        for i in range(4):
            try:
                r = self.ex.fetch_order_result(client_id=cid)
                if not r.is_open:
                    return r
                self.ex.cancel_order(client_id=cid)
            except Exception as e:
                last_exc = e
            self.ex.sleep(0.5 * (i + 1))
        raise AmbiguousOrder(f"état final inconnu: {cid} ({last_exc})", cid)

    def _open_from_fill(self, ctx: BotContext, intent: Dict[str, Any],
                        results: List[OrderResult]) -> bool:
        cfg = self.cfg
        results = [r for r in results if r.filled > 0]
        filled = sum(r.filled for r in results)
        if filled <= 0:
            self.logger.info("[ENTRY] aucun fill")
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return False
        cost = sum(r.cost if r.cost > 0 else r.filled * r.average
                   for r in results)
        avg = cost / filled
        fee_base = sum(r.fee_base for r in results)
        fee_quote = sum(r.fee_quote for r in results)
        fee_unknown = any(not r.fee_known for r in results)
        fee_other = any(r.fee_other > 0 for r in results)
        net = filled - fee_base
        if fee_unknown and self.live:
            # Frais non communiqués : on suppose (prudemment) un prélèvement
            # en base → on ne protège jamais plus que ce qui est détenu.
            net = filled * (1 - cfg.fee_rate)
        if self.live:
            sellable = self.ex.bot_free_base(ctx, force=True)
            amount_held = self.ex.round_amount(min(net, sellable))
        else:
            amount_held = self.ex.round_amount(net)
        if amount_held * avg < self.ex.min_notional():
            self.logger.warning(
                f"[ENTRY] fill {filled:.8f} trop petit pour être protégé "
                f"→ laissé en poussière")
            ctx.portfolio.dust_base += max(0.0, net)
            ctx.pending_order = None
            ctx.state = BotState.FLAT.value
            self._persist(ctx)
            return False
        ctx.portfolio.dust_base += max(0.0, net - amount_held)
        extra_fee = cost * cfg.fee_rate if (fee_other or (
            fee_unknown and not self.live)) else 0.0
        cost_basis = (cost + fee_quote + extra_fee) / amount_held
        sl_dist = float(intent.get("sl_dist") or avg * 0.02)
        rr = float(intent.get("rr") or 2.0)
        if intent.get("sl_abs"):
            sl = self.ex.round_price(float(intent["sl_abs"]), "down")
            sl_dist = avg - sl
        else:
            sl = self.ex.round_price(avg - sl_dist, "down")
        if intent.get("tp_abs"):
            tp = self.ex.round_price(float(intent["tp_abs"]), "up")
        else:
            tp = self.ex.round_price(avg + sl_dist * rr, "up")
        if not (0 < sl < avg < tp):
            self.logger.critical(
                f"[ENTRY] géométrie SL/TP invalide ({sl}/{avg}/{tp}) → défaut")
            sl = self.ex.round_price(avg * (1 - cfg.max_sl_dist_pct), "down")
            tp = self.ex.round_price(avg * (1 + cfg.max_sl_dist_pct * rr), "up")
        risk_quote = amount_held * (cost_basis - sl * (1 - cfg.fee_rate))
        if intent.get("risk_per_unit"):
            # Risque planifié par la stratégie (stop de clôture) : c'est lui
            # qui définit le R, pas le stop de protection exchange.
            risk_quote = amount_held * float(intent["risk_per_unit"])
        if risk_quote <= 0:
            risk_quote = amount_held * max(avg - sl, avg * 0.001)
        candle_ts = int(intent.get("candle_ts") or int(time.time() * 1000))
        ctx.position = Position(
            in_position=True, buy_price=avg, cost_basis=cost_basis,
            sl_price=sl, tp_price=tp, amount_held=amount_held,
            initial_amount=amount_held, risk_per_unit=avg - sl,
            risk_quote_initial=risk_quote, rr_used=rr,
            module=str(intent.get("module") or ""),
            regime=str(intent.get("regime") or ""),
            tier=str(intent.get("tier") or ""),
            entry_score=int(intent.get("score") or 0),
            entry_adx=intent.get("entry_adx"),
            entry_obi=intent.get("entry_obi"),
            entry_order_id=results[-1].order_id or "PAPER",
            entry_timestamp_ms=candle_ts, opened_at=_utcnow_iso(),
            entry_candle_ts=candle_ts, sl_update_candle_ts=candle_ts,
            low_since_sl_update=avg, high_since_entry=avg,
            eff_risk_pct=intent.get("eff_risk_pct"),
            entry_equity=float(intent.get("equity") or 0.0),
            soft_stop=float(intent.get("soft_stop") or 0.0),
            highest_close=float(intent.get("ref_price") or avg))
        ctx.pending_order = None
        ctx.state = BotState.OPEN.value
        self._persist(ctx)
        self.logger.info(
            f"[ENTRY] {ctx.position.module} {ctx.position.regime} "
            f"{ctx.position.tier} qty={amount_held:.8f} @ {avg:.8f} "
            f"(coût {cost_basis:.8f}) SL={sl:.8f} TP={tp:.8f} "
            f"risque={intent.get('eff_risk_pct', 0):.3f}% "
            f"({risk_quote:.4f} {cfg.quote})")
        self._event("entry", "INFO", {
            "module": ctx.position.module, "tier": ctx.position.tier,
            "score": ctx.position.entry_score, "entry": avg,
            "cost_basis": cost_basis, "sl": sl, "tp": tp,
            "amount": amount_held, "gross_filled": filled,
            "fee_base": fee_base, "fee_quote": fee_quote,
            "eff_risk_pct": intent.get("eff_risk_pct"),
            "mults": intent.get("mults")})
        if self.live:
            status = self.sync_protection(ctx)
            if ctx.position.in_position and status == "FAILED":
                self.panic_flatten(ctx, "protection impossible après achat")
            self._persist(ctx)
        return True

    # ---------- Intentions en attente ----------

    def resolve_pending(self, ctx: BotContext) -> None:
        """Résout une intention d'ordre persistée (crash, timeout réseau)
        en interrogeant l'exchange par client-id."""
        intent = ctx.pending_order
        if not intent:
            return
        if not self.live:
            ctx.pending_order = None
            return
        cids = [c for c in (intent.get("cids") or []) if c]
        results: List[OrderResult] = []
        unknown = False
        for cid in cids:
            try:
                r = self.ex.fetch_order_result(client_id=cid)
                if r.is_open:
                    self.ex.cancel_order(client_id=cid)
                    r = self.ex.fetch_order_result(client_id=cid)
                    if r.is_open:
                        unknown = True
                        continue
                results.append(r)
            except ccxt.OrderNotFound:
                continue
            except Exception as e:
                self.logger.warning(f"[PENDING] lecture {cid} KO: {e}")
                unknown = True
        if unknown:
            return
        age = time.time() - float(intent.get("ts_epoch") or 0)
        if cids and not results and age < self.cfg.pending_order_timeout_sec:
            return
        kind = intent.get("kind")
        filled = sum(r.filled for r in results)
        self.logger.warning(
            f"[PENDING] résolution {kind}: {len(results)} ordre(s) trouvé(s), "
            f"filled={filled:.8f}")
        if kind == "entry":
            if filled > 0 and not ctx.position.in_position:
                self._open_from_fill(ctx, intent, results)
            else:
                ctx.pending_order = None
                if not ctx.position.in_position:
                    ctx.state = BotState.FLAT.value
        elif kind == "exit":
            ctx.pending_order = None
            self._record_order_fills(ctx, results,
                                     str(intent.get("reason") or "EXIT"))
        else:
            ctx.pending_order = None
        if ctx.risk.halted and ctx.risk.halt_reason in AUTO_CLEARABLE_HALTS:
            self.logger.warning(
                f"[PENDING] ambiguïté résolue → levée du halt "
                f"{ctx.risk.halt_reason}")
            self.notifier(f"✅ Ambiguïté résolue ({ctx.risk.halt_reason})")
            clear_halt(ctx)
        self._persist(ctx)

    # ---------- Protection : lecture ----------

    @staticmethod
    def _leg_list(p: Position) -> List[Tuple[str, str]]:
        legs: List[Tuple[str, str]] = []
        if p.oco_tp_order_id:
            legs.append(("TP", p.oco_tp_order_id))
        if p.oco_sl_order_id:
            legs.append(("SL", p.oco_sl_order_id))
        if p.standalone_stop_order_id:
            legs.append(("STOP", p.standalone_stop_order_id))
        return legs

    def _hydrate_oco_legs(self, p: Position) -> bool:
        """Complète les IDs de jambes d'un OCO (contexte hérité / réponse
        partielle) via GET orderList. False si illisible."""
        if not p.oco_order_id or (p.oco_tp_order_id and p.oco_sl_order_id):
            return True
        try:
            resp = self.ex.query_order_list(list_id=p.oco_order_id)
        except ccxt.OrderNotFound:
            self.logger.warning(f"[PROT] orderList {p.oco_order_id} inconnue")
            p.oco_order_id = None
            return True
        except Exception as e:
            self.logger.warning(f"[PROT] orderList illisible: {e}")
            return False
        parsed = self.ex.parse_order_list(resp, p.oco_tp_client_id,
                                          p.oco_sl_client_id)
        tp, sl = parsed["tp_order_id"], parsed["sl_order_id"]
        for oid in parsed["unclassified_order_ids"]:
            try:
                r = self.ex.fetch_order_result(order_id=oid)
            except Exception as e:
                self.logger.warning(f"[PROT] jambe {oid} illisible: {e}")
                return False
            if r.order_type.startswith("STOP"):
                sl = sl or oid
            else:
                tp = tp or oid
        p.oco_tp_order_id = p.oco_tp_order_id or tp
        p.oco_sl_order_id = p.oco_sl_order_id or sl
        return bool(p.oco_tp_order_id or p.oco_sl_order_id)

    def protection_snapshot(self, ctx: BotContext) -> ProtectionSnapshot:
        p = ctx.position
        if not self._hydrate_oco_legs(p):
            return ProtectionSnapshot(ProtectionState.UNKNOWN,
                                      detail="orderList illisible")
        legs = self._leg_list(p)
        if not legs:
            return ProtectionSnapshot(ProtectionState.NONE)
        fills: List[Fill] = []
        open_ids: List[str] = []
        open_kinds: List[str] = []
        unknown = False
        for kind, oid in legs:
            try:
                r = self.ex.fetch_order_result(order_id=oid)
            except ccxt.OrderNotFound:
                continue
            except Exception as e:
                self.logger.warning(f"[PROT] lecture {kind} {oid} KO: {e}")
                unknown = True
                continue
            if r.filled > 0:
                fee = r.fee_quote if (r.fee_known and r.fee_other <= 0) else None
                fills.append(Fill(r.filled, r.average, oid, kind, fee))
            if r.is_open:
                open_ids.append(oid)
                open_kinds.append(kind)
        if fills:
            state = ProtectionState.FILLED
        elif unknown:
            state = ProtectionState.UNKNOWN
        elif any(k in ("SL", "STOP") for k in open_kinds):
            state = ProtectionState.ACTIVE
        else:
            state = ProtectionState.CANCELED
        return ProtectionSnapshot(state, fills, open_ids)

    # ---------- Protection : annulation sûre ----------

    def cancel_protection(self, ctx: BotContext) -> CancelResult:
        """Annule toute la protection puis RELIT chaque jambe : les fills
        survenus entre-temps sont retournés (jamais perdus)."""
        p = ctx.position
        if not p.has_protection_ids():
            return CancelResult(True, [])
        # Jamais d'annulation à l'aveugle : si l'état de la protection est
        # illisible, on ne pourrait pas confirmer l'annulation ni reposer un
        # stop → une panne de lecture deviendrait une position sans stop.
        pre = self.protection_snapshot(ctx)
        if pre.state == ProtectionState.UNKNOWN:
            self.logger.error("[PROT] protection illisible → annulation "
                              "reportée, ordres conservés")
            return CancelResult(False, pre.fills)
        if p.oco_order_id:
            self.ex.cancel_order_list(p.oco_order_id)
        for _kind, oid in self._leg_list(p):
            self.ex.cancel_order(order_id=oid)
        snap = self.protection_snapshot(ctx)
        if pre.fills:
            snap.fills = pre.fills + snap.fills
        if snap.state == ProtectionState.UNKNOWN or snap.open_ids:
            for oid in snap.open_ids:
                self.ex.cancel_order(order_id=oid)
            self.ex.sleep(0.5)
            snap = self.protection_snapshot(ctx)
            if snap.state == ProtectionState.UNKNOWN or snap.open_ids:
                self.logger.error("[PROT] annulation non confirmée")
                return CancelResult(False, snap.fills)
        p.clear_protection_ids()
        self.ex.invalidate_balances()
        return CancelResult(True, snap.fills)

    def _apply_fills(self, ctx: BotContext, fills: List[Fill]) -> None:
        merged: Dict[str, Fill] = {}
        for f in fills:
            cur = merged.get(f.order_id)
            if cur is None or f.qty > cur.qty:
                merged[f.order_id] = f
        for f in merged.values():
            p = ctx.position
            if not p.in_position:
                return
            done = float(p.recorded_fills.get(f.order_id, 0.0))
            delta = f.qty - done
            if delta <= 1e-12:
                continue
            p.recorded_fills[f.order_id] = f.qty
            fee = (f.fee_quote * delta / f.qty
                   if f.fee_quote is not None and f.qty > 0 else None)
            self._record_exit(ctx, f.price, delta,
                              _LEG_REASON.get(f.leg, f.leg or "BARRIER"),
                              fee_quote=fee, order_id=f.order_id)

    # ---------- Protection : synchronisation ----------

    def sync_protection(self, ctx: BotContext) -> str:
        """Retourne ACTIVE | UNCERTAIN | FAILED | CLOSED."""
        if not self.live:
            return "ACTIVE" if ctx.position.in_position else "CLOSED"
        p = ctx.position
        if not p.in_position:
            return "CLOSED"
        snap = self.protection_snapshot(ctx)
        if snap.state == ProtectionState.FILLED:
            res = self.cancel_protection(ctx)
            self._apply_fills(ctx, snap.fills + res.fills)
            self._persist(ctx)
            if not ctx.position.in_position:
                return "CLOSED"
            if not res.all_terminal:
                return "UNCERTAIN"
            return self._place_or_status(ctx)
        if snap.state == ProtectionState.ACTIVE:
            p.oco_misses = 0
            p.oco_uncertain_since = None
            p.protection_confirmed_ts = time.time()
            return "ACTIVE"
        if snap.state == ProtectionState.UNKNOWN:
            p.oco_misses = int(p.oco_misses or 0) + 1
            if p.oco_uncertain_since is None:
                p.oco_uncertain_since = _utcnow_iso()
            since = _parse_iso(p.oco_uncertain_since) or _utcnow()
            elapsed = (_utcnow() - since).total_seconds()
            # Les ordres de protection sont CONSERVÉS : les annuler sans pouvoir
            # lire leur état laisserait la position sans stop pendant la panne.
            # Le stop logiciel reste armé (statut UNCERTAIN).
            if (p.oco_misses == self.cfg.protection_unknown_max
                    or (p.oco_misses > self.cfg.protection_unknown_max
                        and p.oco_misses % 60 == 0)):
                self.logger.critical(
                    f"[PROT] protection illisible depuis {elapsed:.0f} s "
                    f"({p.oco_misses} lectures KO) : ordres conservés, stop "
                    f"logiciel armé")
                self.notifier("⚠️ Protection illisible (API Binance) : ordres "
                              "conservés, surveillance renforcée",
                              critical=True, dedup_key="prot_unknown")
            return "UNCERTAIN"
        if snap.open_ids:
            # Jambe TP seule restante (SL annulé hors bot) → on repart à neuf.
            res = self.cancel_protection(ctx)
            self._apply_fills(ctx, res.fills)
            if not ctx.position.in_position:
                self._persist(ctx)
                return "CLOSED"
            if not res.all_terminal:
                return "UNCERTAIN"
        elif p.has_protection_ids():
            self.logger.warning(
                "[PROT] protection annulée/expirée hors bot → re-protection")
            self.notifier("⚠️ Protection annulée hors bot : re-pose",
                          dedup_key="prot_canceled")
            p.clear_protection_ids()
        return self._place_or_status(ctx)

    def _place_or_status(self, ctx: BotContext) -> str:
        ok = self._place_protection(ctx)
        self._persist(ctx)
        if not ctx.position.in_position:
            return "CLOSED"
        return "ACTIVE" if ok else "FAILED"

    def _place_or_panic(self, ctx: BotContext, reason: str) -> None:
        if ctx.position.in_position and not self._place_protection(ctx):
            if ctx.position.in_position:
                self.panic_flatten(ctx, reason)

    # ---------- Protection : pose ----------

    def _place_protection(self, ctx: BotContext) -> bool:
        p = ctx.position
        self.ex.invalidate_balances()
        sellable = self.ex.bot_free_base(ctx, force=True)
        if sellable < p.amount_held * 0.98:
            # Relecture après un court délai : un solde peut être publié
            # avec retard juste après une annulation.
            self.ex.sleep(1.0)
            sellable = self.ex.bot_free_base(ctx, force=True)
        if sellable < p.amount_held * 0.98:
            self._reconcile_missing_base(ctx, sellable)
            if not ctx.position.in_position:
                return True
            p = ctx.position
        t = self.ex.get_ticker()
        last = t["last"]
        qty = self.ex.round_amount(min(p.amount_held, sellable))
        if qty * last < self.ex.min_notional():
            self._finalize_close(ctx, last, "DUST_UNPROTECTABLE")
            self._persist(ctx)
            return True
        if last >= p.tp_price or last <= p.sl_price:
            reason = "BARRIER_TP" if last >= p.tp_price else "BARRIER_SL"
            self.logger.warning(
                f"[PROT] prix {last} hors barrières [{p.sl_price}, "
                f"{p.tp_price}] → sortie marché")
            self._market_exit(ctx, qty, reason, t["bid"])
            return not ctx.position.in_position
        if self.cfg.use_oco:
            try:
                placed = self.ex.place_oco(qty, p.tp_price, p.sl_price)
                p.oco_order_id = placed["order_list_id"]
                p.oco_client_id = placed.get("list_client_order_id")
                p.oco_tp_client_id = placed.get("tp_client_order_id")
                p.oco_sl_client_id = placed.get("sl_client_order_id")
                p.oco_tp_order_id = placed.get("tp_order_id")
                p.oco_sl_order_id = placed.get("sl_order_id")
                p.protection_mode = ProtectionMode.OCO.value
                p.oco_misses = 0
                p.oco_uncertain_since = None
                if not (p.oco_tp_order_id and p.oco_sl_order_id):
                    self._hydrate_oco_legs(p)
                self.logger.info(
                    f"[PROT] OCO qty={qty} tp={p.tp_price} sl={p.sl_price} "
                    f"list={p.oco_order_id}")
                return True
            except AmbiguousOrder as e:
                self.logger.error(f"[PROT] OCO ambigu: {e}")
                if self._adopt_open_protection(ctx):
                    return True
            except Exception as e:
                self.logger.error(f"[PROT] OCO KO: {e}")
                if isinstance(e, ccxt.InsufficientFunds) \
                        and self._adopt_open_protection(ctx):
                    return True
        try:
            res = self.ex.place_stop_loss(qty, p.sl_price,
                                          self.ex.new_client_id(CID_STOP))
            p.standalone_stop_order_id = res.order_id
            p.protection_mode = ProtectionMode.STOP_ONLY.value
            if self.cfg.use_oco:
                self.logger.warning("[PROT] fallback STOP seul (TP géré en logiciel)")
                self.notifier("⚠️ Protection en STOP seul", dedup_key="stop_only")
            else:
                self.logger.info(f"[PROT] STOP qty={qty} sl={p.sl_price}")
            return True
        except AmbiguousOrder as e:
            self.logger.error(f"[PROT] STOP ambigu: {e}")
            return self._adopt_open_protection(ctx)
        except Exception as e:
            self.logger.critical(f"[PROT] STOP KO: {e}")
            if isinstance(e, ccxt.InsufficientFunds):
                return self._adopt_open_protection(ctx)
        return False

    def _adopt_open_protection(self, ctx: BotContext) -> bool:
        """Retrouve sur l'exchange nos ordres de protection ouverts
        (préfixes de client-id) et les rattache à la position."""
        try:
            orders = self.ex.fetch_open_orders()
        except Exception as e:
            self.logger.warning(f"[PROT] open orders KO: {e}")
            return False
        p = ctx.position
        found = False
        for o in orders:
            try:
                r = self.ex._parse_order(o)
            except Exception:
                continue
            if r.side != "sell" or not r.client_id.startswith(
                    PROTECTION_CID_PREFIXES):
                continue
            found = True
            is_stop = r.order_type.startswith("STOP") \
                or r.client_id.startswith((CID_OCO_SL, CID_STOP))
            if r.list_id:
                p.oco_order_id = r.list_id
                p.protection_mode = ProtectionMode.OCO.value
                if is_stop:
                    p.oco_sl_order_id = r.order_id
                else:
                    p.oco_tp_order_id = r.order_id
            elif is_stop:
                p.standalone_stop_order_id = r.order_id
                p.protection_mode = ProtectionMode.STOP_ONLY.value
        if found:
            self.logger.warning("[PROT] protection existante adoptée")
        return found

    def _reconcile_missing_base(self, ctx: BotContext, sellable: float) -> None:
        """Le solde du bot est inférieur à la position : ventes hors bot
        (manuelles) ou fills non vus. On les comptabilise au prix réel si
        possible, sinon au prix courant."""
        p = ctx.position
        missing = p.amount_held - sellable
        if missing <= 0:
            return
        opened = _parse_iso(p.opened_at) or _utcnow()
        try:
            trades = self.ex.fetch_my_trades(
                int(opened.timestamp() * 1000) - 60_000)
        except Exception as e:
            self.logger.warning(f"[PROT] my_trades KO: {e}")
            trades = []
        by_order: Dict[str, List[float]] = {}
        order_seq: List[str] = []
        for t in trades:
            if t.get("side") != "sell":
                continue
            oid = str(t.get("order") or t.get("id") or "")
            if not oid or oid in p.recorded_fills:
                continue
            if oid not in by_order:
                by_order[oid] = [0.0, 0.0]
                order_seq.append(oid)
            by_order[oid][0] += _fnum(t.get("amount"))
            by_order[oid][1] += _fnum(t.get("cost")) or (
                _fnum(t.get("amount")) * _fnum(t.get("price")))
        left = missing
        for oid in order_seq:
            q, c = by_order[oid]
            if q <= 0 or left <= 0 or not ctx.position.in_position:
                continue
            take = min(q, left)
            ctx.position.recorded_fills[oid] = q
            self.logger.critical(
                f"[PROT] vente hors bot détectée: {take:.8f} @ {c / q:.8f}")
            self._record_exit(ctx, c / q, take, "EXTERNAL_SELL", order_id=oid)
            left -= take
        if left > max(self.ex.rules.step_size, 1e-12) \
                and ctx.position.in_position:
            last = self.ex.get_ticker()["last"]
            self.logger.critical(
                f"[PROT] {left:.8f} {self.cfg.base} manquants sans vente "
                f"identifiée → sortie comptable au prix courant")
            self.notifier(f"🚨 {left:.6f} {self.cfg.base} manquants (hors bot)",
                          critical=True)
            self._record_exit(ctx, last, left, "EXTERNAL_ADJUST")

    # ---------- Comptabilité ----------

    def _record_order_fills(self, ctx: BotContext, results: List[OrderResult],
                            reason: str) -> None:
        """Comptabilise des ordres de sortie en ignorant la part déjà
        enregistrée (même ordre vu par plusieurs chemins)."""
        for r in results:
            p = ctx.position
            if not p.in_position or r.filled <= 0 or not r.order_id:
                continue
            delta = r.filled - float(p.recorded_fills.get(r.order_id, 0.0))
            if delta <= 1e-12:
                continue
            p.recorded_fills[r.order_id] = r.filled
            fee = None
            if r.fee_known and r.fee_other <= 0 and r.filled > 0:
                fee = r.fee_quote * delta / r.filled
            self._record_exit(ctx, r.average, delta, reason, fee_quote=fee,
                              order_id=r.order_id)

    def _record_exit(self, ctx: BotContext, price: float, qty: float,
                     reason: str, fee_quote: Optional[float] = None,
                     order_id: Optional[str] = None) -> None:
        p = ctx.position
        if not p.in_position or qty <= 0 or price <= 0:
            return
        if order_id and order_id != "PAPER":
            p.recorded_fills.setdefault(order_id, qty)
        qty = min(qty, p.amount_held) if p.amount_held > 0 else qty
        fee = fee_quote if fee_quote is not None else qty * price * self.cfg.fee_rate
        pnl = qty * price - fee - qty * p.cost_basis
        p.realized_pnl += pnl
        p.amount_held = max(0.0, float(Decimal(str(p.amount_held))
                                       - Decimal(str(qty))))
        if reason in ("R1", "R2"):
            p.partial_exit_count += 1
        r_leg = pnl / p.risk_quote_initial if p.risk_quote_initial > 0 else 0.0
        p.legs.append({"ts": _utcnow_iso(), "reason": reason, "qty": qty,
                       "price": price, "pnl": pnl, "r": r_leg,
                       "order_id": order_id})
        if p.amount_held > 0 and p.amount_held * price >= self.ex.min_notional():
            ctx.state = BotState.OPEN.value
            total_r = (p.realized_pnl / p.risk_quote_initial
                       if p.risk_quote_initial > 0 else 0.0)
            if self.store:
                self.store.log_trade({
                    "entry": p.buy_price, "exit": price, "amount": qty,
                    "pnl": pnl, "pnl_pct": (price / p.cost_basis - 1) * 100,
                    "r_mult": r_leg, "cumulative_pnl": p.realized_pnl,
                    "cumulative_r": total_r, "barrier": f"{reason}_PARTIAL",
                    "module": p.module, "tier": p.tier,
                    "score": p.entry_score, "entry_obi": p.entry_obi,
                    "entry_order_id": p.entry_order_id,
                    "exit_order_id": order_id, "regime": p.regime,
                    "eff_risk_pct": p.eff_risk_pct}, self.cfg.run_mode)
            self.logger.info(
                f"[CLOSE] {reason}_PARTIAL qty={qty:.8f} @ {price:.8f} "
                f"pnl={pnl:.6f} R={r_leg:+.3f} reste={p.amount_held:.8f}")
            return
        self._finalize_close(ctx, price, reason,
                             last_leg=(qty, pnl, r_leg, order_id))

    def _finalize_close(self, ctx: BotContext, price: float, reason: str,
                        last_leg: Optional[Tuple[float, float, float,
                                                 Optional[str]]] = None) -> None:
        p = ctx.position
        if not p.in_position:
            return
        dust = p.amount_held
        if dust > 0:
            dust_pnl = dust * price * (1 - self.cfg.fee_rate) - dust * p.cost_basis
            p.realized_pnl += dust_pnl
            ctx.portfolio.dust_base += dust
            self.logger.info(
                f"[CLOSE] reliquat {dust:.8f} {self.cfg.base} < min_notional "
                f"→ poussière valorisée au marché")
        total_pnl = p.realized_pnl
        total_r = (total_pnl / p.risk_quote_initial
                   if p.risk_quote_initial > 0 else 0.0)
        qty, pnl, r_leg, oid = last_leg or (0.0, 0.0, 0.0, None)
        if self.store:
            self.store.log_trade({
                "entry": p.buy_price, "exit": price, "amount": qty,
                "pnl": pnl,
                "pnl_pct": (price / p.cost_basis - 1) * 100 if p.cost_basis else 0,
                "r_mult": r_leg, "cumulative_pnl": total_pnl,
                "cumulative_r": total_r, "barrier": reason,
                "module": p.module, "tier": p.tier, "score": p.entry_score,
                "entry_obi": p.entry_obi, "entry_order_id": p.entry_order_id,
                "exit_order_id": oid, "regime": p.regime,
                "eff_risk_pct": p.eff_risk_pct}, self.cfg.run_mode)
        record_closed_trade(ctx, self.cfg, self.risk, {
            "pnl": total_pnl, "r": total_r, "module": p.module,
            "tier": p.tier, "reason": reason, "entry_adx": p.entry_adx,
            "risk_quote": p.risk_quote_initial, "entry_price": p.buy_price,
            "exit_price": price, "legs": len(p.legs),
            "ret": total_pnl / p.entry_equity if p.entry_equity > 0 else 0.0},
            logger=self.logger)
        self.logger.info(
            f"[CLOSE] {reason} {p.module}/{p.tier} exit={price:.8f} "
            f"pnl={total_pnl:+.6f} R={total_r:+.3f} jambes={len(p.legs)}")
        self._event("close", "INFO", {
            "reason": reason, "module": p.module, "tier": p.tier,
            "pnl": total_pnl, "r": total_r, "legs": p.legs})
        ctx.position = Position()
        ctx.state = BotState.FLAT.value

    # ---------- Sorties ----------

    def _market_exit(self, ctx: BotContext, qty: float, reason: str,
                     ref_price: float) -> bool:
        """Vente marché. Précondition live : protection terminale (I2)."""
        p = ctx.position
        if not p.in_position:
            return False
        full = qty >= p.amount_held * 0.999
        if not self.live:
            res = self._paper_sell(ctx, min(qty, p.amount_held), ref_price)
            self._record_exit(ctx, res.average, res.filled, reason,
                              fee_quote=res.fee_quote, order_id="PAPER")
            return True
        sellable = self.ex.bot_free_base(ctx, force=True)
        qty = self.ex.round_amount(min(qty, sellable, p.amount_held))
        if qty * ref_price < self.ex.min_notional():
            if full:
                self._finalize_close(ctx, ref_price, f"{reason}_DUST")
                self._persist(ctx)
                return True
            self.logger.warning(f"[SELL] {reason}: quantité sous le minimum")
            return False
        cid = self.ex.new_client_id(CID_EXIT_MARKET)
        ctx.pending_order = {"kind": "exit", "cids": [cid], "ts": _utcnow_iso(),
                             "ts_epoch": time.time(), "reason": reason,
                             "amount": qty}
        self._persist(ctx)
        try:
            res = self.ex.market_sell(qty, cid, ref_price)
        except AmbiguousOrder as e:
            self._halt(ctx, "AMBIGUOUS_SELL",
                       payload={"error": str(e), "cid": cid, "reason": reason})
            return False
        except (ccxt.InsufficientFunds, ccxt.InvalidOrder, ccxt.BadRequest,
                ValueError) as e:
            ctx.pending_order = None
            self.logger.error(f"[SELL] {reason} rejeté: {e}")
            self._persist(ctx)
            return False
        ctx.pending_order = None
        self._record_order_fills(ctx, [res], reason)
        self._persist(ctx)
        return res.filled > 0

    def close_position(self, ctx: BotContext, reason: str,
                       ref_price: float) -> bool:
        """Sortie totale : annulation sûre → vente → re-protection si reste."""
        if not ctx.position.in_position:
            return True
        if not self.live:
            return self._market_exit(ctx, ctx.position.amount_held, reason,
                                     ref_price)
        cancel = self.cancel_protection(ctx)
        self._apply_fills(ctx, cancel.fills)
        if not ctx.position.in_position:
            self._persist(ctx)
            return True
        if not cancel.all_terminal:
            self.logger.error(f"[{reason}] annulation protection non confirmée "
                              f"→ sortie reportée")
            self._persist(ctx)
            return False
        self._market_exit(ctx, ctx.position.amount_held, reason, ref_price)
        if ctx.position.in_position and not ctx.pending_order:
            self._place_or_panic(ctx, f"{reason}: re-protection impossible")
        self._persist(ctx)
        return not ctx.position.in_position

    def _reduce_position(self, ctx: BotContext, qty: float, reason: str,
                         ref_price: float) -> bool:
        if not self.live:
            return self._market_exit(ctx, qty, reason, ref_price)
        cancel = self.cancel_protection(ctx)
        self._apply_fills(ctx, cancel.fills)
        if not ctx.position.in_position:
            self._persist(ctx)
            return False
        if not cancel.all_terminal:
            self.logger.error(f"[{reason}] annulation protection non confirmée")
            self._persist(ctx)
            return False
        if cancel.fills:
            # La protection a exécuté entre-temps : on re-protège le reste
            # et on réévalue au cycle suivant.
            self._place_or_panic(ctx, f"{reason}: re-protection impossible")
            self._persist(ctx)
            return False
        ok = self._market_exit(ctx, qty, reason, ref_price)
        if ctx.position.in_position and not ctx.pending_order:
            self._place_or_panic(ctx, f"{reason}: re-protection impossible")
        self._persist(ctx)
        return ok

    def panic_flatten(self, ctx: BotContext, reason: str) -> bool:
        if ctx.flatten_in_progress:
            self.logger.error("[PANIC] réentrant → ignoré")
            return False
        ctx.flatten_in_progress = True
        try:
            self.logger.critical(f"[PANIC] {reason}")
            if self.store:
                self.store.log_panic_sequence(
                    reason, {"symbol": self.cfg.symbol, "state": ctx.state,
                             "amount_held": ctx.position.amount_held},
                    self.cfg.run_mode)
            self.notifier(f"🚨 PANIC FLATTEN: {reason}", critical=True)
            if not ctx.position.in_position:
                return True
            if not self.live:
                return self._market_exit(ctx, ctx.position.amount_held,
                                         "PANIC", self.ex.get_ticker()["bid"])
            for _attempt in range(3):
                cancel = self.cancel_protection(ctx)
                self._apply_fills(ctx, cancel.fills)
                if not ctx.position.in_position:
                    return True
                if cancel.all_terminal:
                    bid = self.ex.get_ticker()["bid"]
                    self._market_exit(ctx, ctx.position.amount_held, "PANIC", bid)
                    if not ctx.position.in_position:
                        return True
                    if ctx.pending_order:
                        return False  # vente ambiguë : resolve_pending
                self.ex.sleep(1.0)
            self._halt(ctx, "PANIC_FAILED")
            return False
        except Exception as e:
            self.logger.exception(f"[PANIC] exception: {e}")
            self._halt(ctx, "PANIC_EXCEPTION", payload={"error": str(e)})
            return False
        finally:
            ctx.flatten_in_progress = False
            self._persist(ctx)

    # ---------- Gestion de position (chaque cycle) ----------

    def manage_position(self, ctx: BotContext, df: pd.DataFrame, closed: Any,
                        live_price: float, current_candle_ts: int) -> None:
        if not ctx.position.in_position:
            return
        update_extremes(ctx.position, df, live_price)
        if self.live:
            if not self.maintain_protection(ctx, live_price):
                return
        else:
            if self._paper_barriers(ctx, df, live_price):
                return
        if is_frozen(ctx):
            return
        self.check_partial_exits(ctx, live_price)
        if not ctx.position.in_position:
            return
        if self.should_time_exit(ctx, live_price):
            self.execute_time_exit(ctx, live_price)
            if not ctx.position.in_position:
                return
        self.apply_break_even(ctx, live_price, current_candle_ts)
        if not ctx.position.in_position:
            return
        self.apply_trailing(ctx, closed, live_price, current_candle_ts)

    def maintain_protection(self, ctx: BotContext, live_price: float) -> bool:
        """Live : synchronise la protection exchange, applique le stop
        logiciel de dernier recours. Retourne False si la position a été
        clôturée ou liquidée (rien d'autre à faire ce cycle)."""
        if not ctx.position.in_position:
            return False
        status = self.sync_protection(ctx)
        if not ctx.position.in_position:
            return False
        if status == "FAILED":
            self.logger.critical("[PROT] position sans protection → liquidation")
            self.panic_flatten(ctx, "protection impossible")
            return False
        p = ctx.position
        if self.cfg.software_stop_enabled:
            if status == "UNCERTAIN" and live_price <= p.sl_price:
                self.panic_flatten(ctx, "stop logiciel (protection incertaine)")
                return False
            if live_price <= p.sl_price * (1 - self.cfg.stop_limit_offset_pct):
                # Stop déclenché mais non exécuté (ex. STOP_LOSS_LIMIT
                # dépassé par un gap) : sortie marché de dernier recours.
                self.panic_flatten(ctx, "stop logiciel (stop exchange non exécuté)")
                return False
        if (self.cfg.use_oco
                and p.protection_mode == ProtectionMode.STOP_ONLY.value
                and live_price >= p.tp_price and not is_frozen(ctx)):
            self.close_position(ctx, "BARRIER_TP", live_price)
            return False
        return True

    def _paper_barriers(self, ctx: BotContext, df: pd.DataFrame,
                        live_price: float) -> bool:
        """Simulation des ordres de protection en paper : uniquement les
        barres POSTÉRIEURES à l'entrée / au dernier déplacement du stop,
        puis le prix courant. SL prioritaire (hypothèse prudente)."""
        p = ctx.position
        gate = int(p.sl_update_candle_ts or p.entry_candle_ts or 0)
        fill_px: Optional[float] = None
        reason = ""
        post = df[df["ts"] > gate] if len(df) else df
        for row in post.itertuples(index=False):
            if float(row.low) <= p.sl_price:
                fill_px = min(p.sl_price, float(row.open)) \
                    * (1 - self.cfg.paper_slippage_pct)
                reason = "BARRIER_SL"
                break
            if float(row.high) >= p.tp_price:
                fill_px, reason = p.tp_price, "BARRIER_TP"
                break
        if fill_px is None:
            if live_price <= p.sl_price:
                fill_px = p.sl_price * (1 - self.cfg.paper_slippage_pct)
                reason = "BARRIER_SL"
            elif live_price >= p.tp_price:
                fill_px, reason = p.tp_price, "BARRIER_TP"
        if fill_px is None:
            return False
        # fill_px inclut déjà le slippage (SL) ; le TP est un ordre maker.
        res = self._paper_sell(ctx, p.amount_held, fill_px, maker=True)
        self._record_exit(ctx, res.average, res.filled, reason,
                          fee_quote=res.fee_quote, order_id="PAPER")
        return not ctx.position.in_position

    def check_partial_exits(self, ctx: BotContext, live_price: float) -> None:
        cfg = self.cfg
        p = ctx.position
        if (not cfg.partial_exit_enabled or not p.in_position
                or p.risk_per_unit <= 0):
            return
        levels = [(cfg.partial_exit_r1, cfg.partial_exit_pct1),
                  (cfg.partial_exit_r2, cfg.partial_exit_pct2)]
        while p.in_position and p.partial_exit_count < len(levels):
            idx = p.partial_exit_count
            r_level, pct = levels[idx]
            if live_price < p.buy_price + p.risk_per_unit * r_level:
                return
            qty = self.ex.round_amount(min(p.initial_amount * pct, p.amount_held))
            remaining = p.amount_held - qty
            mn = self.ex.min_notional()
            if (qty * live_price < mn
                    or remaining * live_price < mn * cfg.min_remaining_notional_mult):
                self.logger.info(f"[R5] partiel R{idx + 1} ignoré (taille)")
                p.partial_exit_count += 1
                continue
            self.logger.info(f"[R5] partiel R{idx + 1}: {qty:.8f}")
            self._reduce_position(ctx, qty, f"R{idx + 1}", live_price)
            return

    def apply_break_even(self, ctx: BotContext, live_price: float,
                         current_candle_ts: int) -> bool:
        p = ctx.position
        if p.break_even_done or not self.cfg.break_even_enabled:
            return False
        if float(p.high_since_entry or 0) < p.buy_price * (
                1 + self.cfg.break_even_trigger):
            return False
        new_sl = self.ex.round_price(break_even_stop(p, self.cfg), "up")
        if new_sl <= p.sl_price:
            p.break_even_done = True
            return False
        if new_sl >= live_price * 0.999:
            return False
        ok = self._modify_stop(ctx, new_sl, current_candle_ts, "BE")
        if ok and ctx.position.in_position:
            ctx.position.break_even_done = True
            ctx.position.low_since_sl_update = live_price
        return ok

    def apply_trailing(self, ctx: BotContext, closed: Any, live_price: float,
                       current_candle_ts: int) -> bool:
        p = ctx.position
        if not self.cfg.trailing_enabled or not p.break_even_done:
            return False
        atr = _row_get(closed, "atr")
        if _isnan(atr):
            return False
        if float(p.high_since_entry or 0) <= p.buy_price + p.risk_per_unit:
            return False
        if time.time() - float(p.last_trailing_ts or 0) \
                < self.cfg.trailing_min_interval_sec:
            return False
        raw = trailing_stop(p, float(atr), self.cfg)
        if raw is None:
            return False
        new_sl = self.ex.round_price(raw, "down")
        if new_sl <= p.sl_price * (1 + self.cfg.trailing_min_raise_pct):
            return False
        if new_sl >= live_price * 0.999:
            return False
        return self._modify_stop(ctx, new_sl, current_candle_ts, "TRAIL")

    def _modify_stop(self, ctx: BotContext, new_sl: float,
                     current_candle_ts: int, reason: str) -> bool:
        p = ctx.position
        if new_sl <= p.sl_price:
            return False
        if not self.live:
            self.logger.info(f"[{reason}] SL {p.sl_price:.8f} → {new_sl:.8f}")
            p.sl_price = new_sl
            p.sl_update_candle_ts = current_candle_ts
            p.last_trailing_ts = time.time()
            return True
        cancel = self.cancel_protection(ctx)
        self._apply_fills(ctx, cancel.fills)
        if not ctx.position.in_position:
            self._persist(ctx)
            return False
        if not cancel.all_terminal:
            self.logger.error(f"[{reason}] annulation non confirmée → SL inchangé")
            self._persist(ctx)
            return False
        if cancel.fills:
            self._place_or_panic(ctx, f"{reason}: re-protection impossible")
            self._persist(ctx)
            return False
        old = p.sl_price
        p.sl_price = new_sl
        if self._place_protection(ctx):
            if ctx.position.in_position:
                p.sl_update_candle_ts = current_candle_ts
                p.last_trailing_ts = time.time()
                self.logger.info(f"[{reason}] SL {old:.8f} → {new_sl:.8f}")
            self._persist(ctx)
            return ctx.position.in_position
        if not ctx.position.in_position:
            self._persist(ctx)
            return False
        p.sl_price = old
        if self._place_protection(ctx):
            self.logger.warning(f"[{reason}] nouveau SL KO, ancien restauré")
            self._persist(ctx)
            return False
        self.panic_flatten(ctx, f"{reason}: aucune protection")
        return False

    def should_time_exit(self, ctx: BotContext, live_price: float) -> bool:
        p = ctx.position
        if not self.cfg.time_exit_enabled:
            return False
        cd = _parse_iso(p.time_exit_cooldown_until)
        if cd is not None and _utcnow() < cd:
            return False
        if not p.opened_at or p.break_even_done:
            return False
        if time.time() - float(p.time_exit_last_attempt_ts or 0) \
                < self.cfg.time_exit_min_retry_sec:
            return False
        opened = _parse_iso(p.opened_at)
        if opened is None:
            return False
        age_h = (_utcnow() - opened).total_seconds() / 3600
        if age_h < self.cfg.max_trade_age_hours:
            return False
        return live_price < p.buy_price + p.risk_per_unit * self.cfg.time_exit_min_r_mult

    def execute_time_exit(self, ctx: BotContext, live_price: float) -> bool:
        p = ctx.position
        p.time_exit_last_attempt_ts = time.time()
        if p.time_exit_attempts >= self.cfg.time_exit_max_attempts:
            p.time_exit_attempts = 0
            p.time_exit_cooldown_until = (_utcnow() + timedelta(
                hours=self.cfg.time_exit_cooldown_hours)).isoformat()
            return False
        ok = self.close_position(ctx, "BARRIER_TIME", live_price)
        if not ok and ctx.position.in_position:
            ctx.position.time_exit_attempts += 1
        return ok

    # ---------- À plat : orphelins et poussière ----------

    def check_orphan(self, ctx: BotContext, last_price: float,
                     force: bool = False) -> None:
        if not self.live or ctx.position.in_position or ctx.pending_order:
            return
        now = time.time()
        if not force and now - ctx.last_orphan_check_ts < self.cfg.orphan_recheck_sec:
            return
        ctx.last_orphan_check_ts = now
        total = self.ex.bot_total_base(ctx, force=True)
        ctx.portfolio.dust_base = min(float(ctx.portfolio.dust_base), total)
        unexplained = max(0.0, total - ctx.portfolio.dust_base)
        if 0 < unexplained * last_price < 2 * self.ex.min_notional():
            # Reliquats de frais/arrondis : traités comme poussière du bot
            # (revendue par sweep_dust dès qu'elle est négociable).
            ctx.portfolio.dust_base = total
            unexplained = 0.0
        if unexplained * last_price >= 2 * self.ex.min_notional():
            if not ctx.orphan_balance:
                self.logger.critical(
                    f"[ORPHAN] {unexplained:.8f} {self.cfg.base} non suivis "
                    f"→ entrées bloquées (vendre/déclarer EXTERNAL_BASE_RESERVE "
                    f"puis `resume`)")
                self.notifier(f"⚠️ Solde orphelin {unexplained:.6f} "
                              f"{self.cfg.base}", critical=True)
                ctx.orphan_balance = True
                ctx.orphan_balance_since = _utcnow_iso()
        elif ctx.orphan_balance:
            self.logger.warning("[ORPHAN] résolu (solde revenu sous le minimum)")
            ctx.orphan_balance = False
            ctx.orphan_balance_since = None

    def sweep_dust(self, ctx: BotContext, last_price: float) -> None:
        """Revend la poussière accumulée par le bot dès qu'elle devient
        négociable (live uniquement)."""
        if not self.live or ctx.position.in_position or ctx.pending_order:
            return
        dust = float(ctx.portfolio.dust_base)
        if dust * last_price < self.ex.min_notional() * 1.2:
            return
        free = self.ex.bot_free_base(ctx, force=True)
        qty = self.ex.round_amount(min(dust, free))
        if qty * last_price < self.ex.min_notional():
            return
        cid = self.ex.new_client_id(CID_EXIT_MARKET)
        try:
            res = self.ex.market_sell(qty, cid, last_price)
        except Exception as e:
            self.logger.warning(f"[DUST] revente KO: {e}")
            return
        ctx.portfolio.dust_base = max(0.0, dust - res.filled)
        self.logger.info(f"[DUST] {res.filled:.8f} revendus @ {res.average:.8f}")
        self._persist(ctx)


# ══════════════════════════════════════════════════════════════════════
# SECTION 13 — RECONCILIATION (boot)
# ══════════════════════════════════════════════════════════════════════

def reconcile(ctx: BotContext, cfg: Config, ex: ExchangeAdapter,
              logger: logging.Logger,
              exec_engine: ExecutionEngine) -> BotContext:
    """Aligne le contexte sur l'exchange au démarrage. Fail-closed : toute
    erreur d'API lève (le boot est interrompu, rien n'est effacé)."""
    if cfg.run_mode == "paper":
        ctx.pending_order = None
        return ctx
    ex.refresh_balances(ctx, force=True)
    open_orders = ex.fetch_open_orders()
    exec_engine.resolve_pending(ctx)
    if ctx.pending_order:
        # Résolu automatiquement aux cycles suivants (halt auto-levable).
        logger.critical("[RECON] intention d'ordre non résolue → HALT")
        halt_ctx(ctx, "PENDING_ORDER_UNRESOLVED", HaltKind.INTEGRITY)
    if ctx.position.in_position:
        # La protection est TOUJOURS vérifiée, même avec une intention en
        # attente (les fills sont dédupliqués par order_id).
        status = exec_engine.sync_protection(ctx)
        if ctx.position.in_position and status == "FAILED":
            exec_engine.panic_flatten(ctx, "protection impossible au boot")
        state = "maintenue" if ctx.position.in_position else "clôturée"
        logger.info(f"[RECON] position {state} (protection={status})")
        return ctx
    if ctx.pending_order:
        return ctx
    ours = []
    for o in open_orders:
        try:
            r = ex._parse_order(o)
        except Exception:
            continue
        if r.side == "sell" and r.client_id.startswith(PROTECTION_CID_PREFIXES):
            ours.append(r)
    if ours:
        if not cfg.recovery_adopt_orders:
            # Des ordres du bot existent mais cette base ne les connaît pas :
            # soit la base a été perdue, soit UNE AUTRE INSTANCE gère ce
            # compte. Les adopter ferait gérer la même position par deux bots.
            ids = ", ".join(o.client_id for o in ours[:4])
            logger.critical(
                f"[RECON] {len(ours)} ordre(s) du bot inconnu(s) de cette base "
                f"({ids}) : une autre instance gère peut-être ce compte → HALT. "
                f"Base perdue ? Relancer une fois avec RECOVERY_ADOPT_ORDERS=true.")
            halt_ctx(ctx, "UNKNOWN_BOT_ORDERS", HaltKind.MANUAL)
            return ctx
        _recover_position(ctx, cfg, ex, logger, exec_engine, ours)
        return ctx
    last = ex.get_ticker()["last"]
    exec_engine.check_orphan(ctx, last, force=True)
    return ctx


def _recover_position(ctx: BotContext, cfg: Config, ex: ExchangeAdapter,
                      logger: logging.Logger, exec_engine: ExecutionEngine,
                      orders: List[OrderResult]) -> None:
    """Ordres de protection du bot présents alors que le contexte est à plat
    (perte du contexte) : reconstruction à partir des ordres et des achats."""
    sl = next((o.stop_price for o in orders
               if o.order_type.startswith("STOP") and o.stop_price > 0), None)
    tp = next((o.price for o in orders
               if not o.order_type.startswith("STOP") and o.price > 0), None)
    qty_orders = max((o.amount - o.filled) for o in orders)
    amount = ex.round_amount(min(qty_orders, ex.bot_total_base(ctx, force=True)))
    since = int((time.time() - cfg.reconcile_trades_window_hours * 3600) * 1000)
    try:
        trades = ex.fetch_my_trades(since)
    except Exception as e:
        logger.critical(f"[RECON] historique d'achats illisible: {e}")
        halt_ctx(ctx, "RECOVERY_TRADES_UNAVAILABLE", HaltKind.MANUAL)
        return
    buys = [t for t in trades if t.get("side") == "buy"]
    if buys:
        acc_q = acc_c = 0.0
        for t in reversed(buys):
            q = _fnum(t.get("amount"))
            take = min(q, amount - acc_q)
            if take <= 0:
                break
            acc_q += take
            acc_c += take * _fnum(t.get("price"))
        buy_px = acc_c / acc_q if acc_q > 0 else ex.get_ticker()["last"]
    else:
        if cfg.recovery_require_verified_entry:
            logger.critical("[RECON] protection orpheline sans achat vérifié → HALT")
            halt_ctx(ctx, "RECOVERY_ENTRY_UNKNOWN", HaltKind.MANUAL, orphan=True)
            return
        buy_px = ex.get_ticker()["last"]
    if sl is None:
        sl = ex.round_price(buy_px * (1 - cfg.max_sl_dist_pct), "down")
    risk_unit = max(buy_px - sl, buy_px * 0.005)
    if tp is None or tp <= sl:
        tp = ex.round_price(buy_px + 2 * risk_unit, "up")
    if amount * buy_px < ex.min_notional():
        logger.warning("[RECON] protection orpheline sur quantité négligeable")
        return
    now_ms = int(time.time() * 1000)
    cost_basis = buy_px * (1 + cfg.fee_rate)
    p = Position(
        in_position=True, buy_price=buy_px, cost_basis=cost_basis,
        sl_price=sl, tp_price=tp, amount_held=amount, initial_amount=amount,
        risk_per_unit=risk_unit, risk_quote_initial=amount * risk_unit,
        rr_used=(tp - buy_px) / risk_unit, module="recovered",
        break_even_done=sl >= buy_px, opened_at=_utcnow_iso(),
        entry_timestamp_ms=now_ms, entry_candle_ts=now_ms,
        sl_update_candle_ts=now_ms, low_since_sl_update=buy_px,
        high_since_entry=buy_px)
    for o in orders:
        is_stop = o.order_type.startswith("STOP")
        if o.list_id:
            p.oco_order_id = o.list_id
            p.protection_mode = ProtectionMode.OCO.value
            if is_stop:
                p.oco_sl_order_id = o.order_id
            else:
                p.oco_tp_order_id = o.order_id
        elif is_stop:
            p.standalone_stop_order_id = o.order_id
            p.protection_mode = ProtectionMode.STOP_ONLY.value
    ctx.position = p
    ctx.state = BotState.OPEN.value
    logger.warning(f"[RECON] position récupérée {amount:.8f} @ {buy_px:.8f} "
                   f"SL={sl} TP={tp}")
    exec_engine.sync_protection(ctx)


# ══════════════════════════════════════════════════════════════════════
# SECTION 14 — METRICS / BACKTEST
# ══════════════════════════════════════════════════════════════════════

def compute_metrics(equity_curve: List[float],
                    trades: List[Dict[str, Any]],
                    timeframe: str = "1h") -> Dict[str, Any]:
    if len(equity_curve) < 2:
        return _empty_metrics()
    eq = np.array(equity_curve, dtype=float)
    initial = eq[0]
    final = eq[-1]
    returns = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1.0)
    returns = returns[np.isfinite(returns)]
    total_return = (final / initial - 1) * 100 if initial > 0 else 0.0
    bpy = _annualization_factor(timeframe)
    sharpe = ((returns.mean() / returns.std()) * math.sqrt(bpy)
              if len(returns) > 1 and returns.std() > 0 else 0.0)
    downside = returns[returns < 0]
    sortino = ((returns.mean() / downside.std()) * math.sqrt(bpy)
               if len(downside) > 1 and downside.std() > 0 else 0.0)
    cummax = np.maximum.accumulate(eq)
    dd = (eq - cummax) / np.where(cummax > 0, cummax, 1.0)
    max_dd = abs(float(dd.min())) * 100 if len(dd) else 0.0
    base = {"total_return_pct": float(total_return), "sharpe": float(sharpe),
            "sortino": float(sortino), "max_drawdown_pct": float(max_dd)}
    if not trades:
        base.update({"profit_factor": 0.0, "win_rate": 0.0,
                     "expectancy_r": 0.0, "avg_trade_r": 0.0, "num_trades": 0})
        return base
    pnls = [float(t.get("pnl", 0)) for t in trades]
    rs = [float(t.get("r", 0)) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_wins = sum(wins)
    total_losses = abs(sum(losses))
    pf = ((total_wins / total_losses) if total_losses > 0
          else (10.0 if total_wins > 0 else 0.0))
    base.update({
        "profit_factor": float(min(pf, 10.0)),
        "win_rate": len(wins) / len(pnls) * 100,
        "expectancy_r": float(np.mean(rs)),
        "avg_trade_r": float(np.mean(rs)),
        "num_trades": len(trades)})
    return base


def monte_carlo_trades(trades: List[Dict[str, Any]], n_sims: int = 2000,
                       seed: int = 42) -> Optional[Dict[str, float]]:
    """Bootstrap (tirage avec remise) des rendements par trade : distribution
    du rendement final et du drawdown max, indépendante de l'ordre historique
    des trades (risque de séquence)."""
    rets = np.array([float(t.get("ret", 0.0)) for t in trades], dtype=float)
    if len(rets) < 10:
        return None
    rng = np.random.default_rng(seed)
    samples = rng.choice(rets, size=(n_sims, len(rets)), replace=True)
    paths = np.cumprod(1.0 + samples, axis=1)
    peaks = np.maximum.accumulate(np.concatenate(
        [np.ones((n_sims, 1)), paths], axis=1), axis=1)[:, 1:]
    max_dd = ((peaks - paths) / peaks).max(axis=1)
    final = paths[:, -1] - 1.0
    return {"ret_p5": float(np.percentile(final, 5) * 100),
            "ret_p50": float(np.percentile(final, 50) * 100),
            "ret_p95": float(np.percentile(final, 95) * 100),
            "dd_p50": float(np.percentile(max_dd, 50) * 100),
            "dd_p95": float(np.percentile(max_dd, 95) * 100),
            "prob_loss": float((final < 0).mean() * 100)}


def _empty_metrics() -> Dict[str, Any]:
    out = {k: 0.0 for k in ["total_return_pct", "sharpe", "sortino",
                             "max_drawdown_pct", "profit_factor", "win_rate",
                             "expectancy_r", "avg_trade_r"]}
    out["num_trades"] = 0
    return out


def format_report(m: Dict[str, Any]) -> str:
    return f"""
📊 PERFORMANCE
─────────────────────────────────
Rendement total     : {m['total_return_pct']:+.2f}%
Sharpe (annualisé)  : {m['sharpe']:.2f}
Sortino (annualisé) : {m['sortino']:.2f}
Max Drawdown        : {m['max_drawdown_pct']:.2f}%
Profit Factor       : {m['profit_factor']:.2f}
Win Rate            : {m['win_rate']:.1f}%
Expectancy          : {m['expectancy_r']:+.3f} R
Trades              : {m['num_trades']}
─────────────────────────────────
"""


@dataclass
class BacktestResult:
    trades: List[Dict[str, Any]] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    timestamps: List[int] = field(default_factory=list)
    initial_capital: float = 1000.0
    final_equity: float = 0.0
    total_return_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    avg_trade_r: float = 0.0
    num_trades: int = 0
    module_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tier_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)


def _series_to_list(s: Optional[Any], n: int) -> Optional[List[Any]]:
    if s is None:
        return None
    vals = list(s.values) if hasattr(s, "values") else list(s)
    if len(vals) != n:
        raise ValueError(f"Série de biais de longueur {len(vals)} ≠ {n}")
    return vals


class BacktestEngine:
    """Simulation barre à barre partageant les règles du live : biais
    HTF/BTC, disjoncteurs, cooldowns, désactivation de module, partielles,
    break-even, trailing, time-exit. Décisions prises à l'ouverture de la
    barre i avec l'information disponible jusqu'à la clôture de i-1."""

    def __init__(self, cfg: Config, initial_capital: float = 1000.0):
        self.cfg = cfg
        self.initial_capital = float(initial_capital)
        self.risk = RiskEngine(cfg, logger=None)
        bt_logger = logging.getLogger(f"{_LOG_ROOT}.backtest")
        bt_logger.addHandler(logging.NullHandler())
        bt_logger.propagate = False
        self.adaptive = AdaptiveEngine(cfg, bt_logger)
        self.slip = cfg.paper_slippage_pct
        self.fee = cfg.paper_fee_rate
        self.min_notional = 10.0
        self.tf_ms = _timeframe_ms(cfg.timeframe)

    def run(self, df: pd.DataFrame,
            htf_bias_series: Optional[Any] = None,
            btc_bias_series: Optional[Any] = None,
            btc_vol_mult_series: Optional[Any] = None,
            start: Optional[int] = None, end: Optional[int] = None,
            rows: Optional[List[Dict[str, Any]]] = None) -> BacktestResult:
        cfg = self.cfg
        if rows is None:
            if "atr" not in df.columns:
                df = compute_indicators(df, cfg)
            rows = df.to_dict("records")
        n = len(rows)
        warm = min_signal_bars(cfg)
        i0 = max(warm, int(start) if start is not None else warm)
        i1 = min(n, int(end) if end is not None else n)
        if i1 - i0 < 2:
            raise ValueError("Pas assez de données après warm-up.")
        htf = _series_to_list(htf_bias_series, n)
        btc = _series_to_list(btc_bias_series, n)
        vmul = _series_to_list(btc_vol_mult_series, n)
        ctx = BotContext.for_backtest(self.initial_capital)
        pf = ctx.portfolio
        trades: List[Dict[str, Any]] = []
        equity_curve = [self.initial_capital]
        timestamps = [int(rows[i0 - 1]["ts"])]
        for i in range(i0, i1):
            bar, prev, prev2 = rows[i], rows[i - 1], rows[i - 2]
            now = datetime.fromtimestamp(int(bar["ts"]) / 1000, tz=timezone.utc)
            if ctx.position.in_position:
                self._manage(ctx, bar, prev, now, trades)
            if cfg.adaptive_enabled:
                self.adaptive.update(ctx, prev)
            eq_open = pf.paper_cash + pf.paper_base * float(bar["open"])
            halted, _ = self.risk.check_circuit_breakers(
                ctx, eq_open, self.adaptive.effective_consec_pause(
                    ctx.adaptive, cfg.consec_loss_pause), now=now)
            if (not ctx.position.in_position and not halted
                    and not in_cooldown(ctx, now)
                    and not in_flash_cooldown(ctx, now)):
                j = i - 1 - cfg.flash_move_lookback
                flash = j >= 0 and detect_flash_move(
                    float(rows[j]["close"]), float(bar["open"]), ctx, cfg, now)
                if not flash:
                    sig = generate_signal_from_rows(
                        prev, prev2, i + 1,
                        htf[i] if htf is not None else "UP",
                        btc[i] if btc is not None else "UP",
                        ctx, cfg,
                        atr_min_pct=self.adaptive.effective_atr_min(
                            ctx.adaptive, cfg.atr_min_pct), now=now)
                    if sig.is_buy:
                        mult = float(vmul[i]) if vmul is not None \
                            and not _isnan(vmul[i]) else 1.0
                        self._open(ctx, sig, bar, prev, now, eq_open, mult)
                        if ctx.position.in_position:
                            self._intrabar(ctx, bar, now, trades,
                                           check_gap=False)
                            if ctx.position.in_position:
                                ctx.position.high_since_entry = max(
                                    float(ctx.position.high_since_entry),
                                    float(bar["high"]))
            equity_curve.append(pf.paper_cash + pf.paper_base * float(bar["close"]))
            timestamps.append(int(bar["ts"]))
        if ctx.position.in_position:
            last = rows[i1 - 1]
            self._exit(ctx, float(last["close"]) * (1 - self.slip),
                       ctx.position.amount_held, "END_OF_DATA",
                       datetime.fromtimestamp(int(last["ts"]) / 1000,
                                              tz=timezone.utc), trades)
            equity_curve[-1] = pf.paper_cash + pf.paper_base * float(last["close"])
        return self._build_result(ctx, trades, equity_curve, timestamps)

    # ---------- Entrée ----------

    def _open(self, ctx: BotContext, sig: Signal, bar: Dict[str, Any],
              signal_row: Dict[str, Any], now: datetime, equity: float,
              btc_vol_mult: float) -> None:
        cfg = self.cfg
        pf = ctx.portfolio
        entry_px = float(bar["open"]) * (1 + self.slip)
        sl_dist = self.risk.sl_distance(sig.module, float(signal_row["atr"]),
                                        entry_px, signal_row, cfg)
        if sl_dist is None:
            return
        risk_pct = self.risk.base_risk(ctx, sig.module,
                                       signal_row.get("realized_vol"))
        eff = min(risk_pct * cfg.tier_mult_map.get(sig.tier, 1.0)
                  * self.risk.regime_multiplier(sig.regime)
                  * self.risk.dd_derisk_multiplier(ctx, equity)
                  * btc_vol_mult, cfg.risk_absolute_max_pct)
        if eff <= 0:
            return
        max_notional = pf.paper_cash * (1 - cfg.min_cash_reserve_pct)
        amount = self.risk.position_size(entry_px, sl_dist, equity, eff,
                                         max_notional)
        if amount * entry_px < self.min_notional:
            return
        cost = amount * entry_px
        fee = cost * self.fee
        if cost + fee > pf.paper_cash:
            amount = pf.paper_cash / (entry_px * (1 + self.fee)) * 0.999
            cost = amount * entry_px
            fee = cost * self.fee
            if amount * entry_px < self.min_notional:
                return
        pf.paper_cash -= cost + fee
        pf.paper_base += amount
        cost_basis = (cost + fee) / amount
        sl = entry_px - sl_dist
        rr = RR_BY_MODULE.get(sig.module, 2.0)
        risk_quote = amount * (cost_basis - sl * (1 - self.fee))
        adx = signal_row.get("adx")
        ctx.position = Position(
            in_position=True, buy_price=entry_px, cost_basis=cost_basis,
            sl_price=sl, tp_price=entry_px + sl_dist * rr, amount_held=amount,
            initial_amount=amount, risk_per_unit=sl_dist,
            risk_quote_initial=max(risk_quote, amount * sl_dist * 0.5),
            rr_used=rr, module=sig.module, regime=sig.regime, tier=sig.tier,
            entry_score=sig.score,
            entry_adx=None if _isnan(adx) else float(adx),
            opened_at=now.isoformat(), entry_timestamp_ms=int(bar["ts"]),
            entry_candle_ts=int(bar["ts"]), sl_update_candle_ts=int(bar["ts"]),
            high_since_entry=entry_px, low_since_sl_update=entry_px,
            eff_risk_pct=eff * 100, entry_equity=equity)
        ctx.state = BotState.OPEN.value

    # ---------- Gestion ----------

    def _manage(self, ctx: BotContext, bar: Dict[str, Any],
                prev: Dict[str, Any], now: datetime,
                trades: List[Dict[str, Any]]) -> None:
        self._update_stops(ctx, prev, float(bar["open"]))
        self._intrabar(ctx, bar, now, trades, check_gap=True)
        p = ctx.position
        if not p.in_position:
            return
        close_ms = int(bar["ts"]) + self.tf_ms
        age_h = (close_ms - int(p.entry_timestamp_ms or bar["ts"])) / 3_600_000
        if (age_h >= self.cfg.max_trade_age_hours and not p.break_even_done
                and float(bar["close"]) < p.buy_price
                + p.risk_per_unit * self.cfg.time_exit_min_r_mult):
            self._exit(ctx, float(bar["close"]) * (1 - self.slip),
                       p.amount_held, "BARRIER_TIME", now, trades)
            return
        p.high_since_entry = max(float(p.high_since_entry), float(bar["high"]))

    def _update_stops(self, ctx: BotContext, prev: Dict[str, Any],
                      bar_open: float) -> None:
        cfg = self.cfg
        p = ctx.position
        if (not p.break_even_done and float(p.high_since_entry or 0)
                >= p.buy_price * (1 + cfg.break_even_trigger)):
            be = break_even_stop(p, cfg)
            if be <= p.sl_price:
                p.break_even_done = True
            elif be < bar_open * 0.999:
                p.sl_price = be
                p.break_even_done = True
        if (cfg.trailing_enabled and p.break_even_done
                and float(p.high_since_entry or 0) > p.buy_price + p.risk_per_unit):
            ts = trailing_stop(p, prev.get("atr"), cfg)
            if (ts is not None and ts > p.sl_price * (1 + cfg.trailing_min_raise_pct)
                    and ts < bar_open * 0.999):
                p.sl_price = ts

    def _intrabar(self, ctx: BotContext, bar: Dict[str, Any], now: datetime,
                  trades: List[Dict[str, Any]], check_gap: bool) -> None:
        cfg = self.cfg
        p = ctx.position
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if check_gap and o <= p.sl_price:
            self._exit(ctx, o * (1 - self.slip), p.amount_held, "BARRIER_SL",
                       now, trades)
            return
        if check_gap and o >= p.tp_price:
            self._exit(ctx, p.tp_price, p.amount_held, "BARRIER_TP", now,
                       trades, maker=True)
            return
        mode = cfg.intrabar_partial_mode
        if mode == "conservative":
            sl_first, max_partials = True, 1
        elif mode == "optimistic":
            sl_first, max_partials = False, 2
        else:
            # Règle OHLC : l'extrême le plus proche de l'ouverture est
            # atteint en premier.
            sl_first, max_partials = (o - l) <= (h - o), 2

        def down_leg() -> None:
            if ctx.position.in_position and l <= ctx.position.sl_price:
                self._exit(ctx, ctx.position.sl_price * (1 - self.slip),
                           ctx.position.amount_held, "BARRIER_SL", now, trades)

        def up_leg() -> None:
            done = 0
            levels = [(cfg.partial_exit_r1, cfg.partial_exit_pct1),
                      (cfg.partial_exit_r2, cfg.partial_exit_pct2)]
            while (cfg.partial_exit_enabled and ctx.position.in_position
                   and ctx.position.partial_exit_count < 2
                   and done < max_partials):
                q = ctx.position
                r_level, pct = levels[q.partial_exit_count]
                target = q.buy_price + q.risk_per_unit * r_level
                if h < target:
                    break
                qty = min(q.initial_amount * pct, q.amount_held)
                if (qty * target < self.min_notional
                        or (q.amount_held - qty) * target
                        < self.min_notional * cfg.min_remaining_notional_mult):
                    q.partial_exit_count += 1
                    continue
                self._exit(ctx, target * (1 - self.slip), qty,
                           f"R{q.partial_exit_count + 1}", now, trades)
                done += 1
            if ctx.position.in_position and h >= ctx.position.tp_price:
                self._exit(ctx, ctx.position.tp_price, ctx.position.amount_held,
                           "BARRIER_TP", now, trades, maker=True)

        if sl_first:
            down_leg()
            up_leg()
        else:
            up_leg()
            down_leg()

    def _exit(self, ctx: BotContext, price: float, qty: float, reason: str,
              now: datetime, trades: List[Dict[str, Any]],
              maker: bool = False) -> None:
        p = ctx.position
        pf = ctx.portfolio
        if not p.in_position or qty <= 0 or price <= 0:
            return
        qty = min(qty, p.amount_held)
        if p.amount_held - qty > 0 and (p.amount_held - qty) * price \
                < self.min_notional:
            qty = p.amount_held
        proceeds = qty * price
        fee = proceeds * self.fee
        pf.paper_cash += proceeds - fee
        pf.paper_base = max(0.0, pf.paper_base - qty)
        pnl = proceeds - fee - qty * p.cost_basis
        p.realized_pnl += pnl
        p.amount_held = max(0.0, p.amount_held - qty)
        if reason in ("R1", "R2"):
            p.partial_exit_count += 1
        p.legs.append({"ts": now.isoformat(), "reason": reason, "qty": qty,
                       "price": price, "pnl": pnl})
        if p.amount_held > 1e-12:
            return
        pf.paper_base = 0.0 if pf.paper_base < 1e-9 else pf.paper_base
        total_r = p.realized_pnl / p.risk_quote_initial \
            if p.risk_quote_initial > 0 else 0.0
        trade = {"ts": now.isoformat(), "pnl": p.realized_pnl, "r": total_r,
                 "module": p.module, "tier": p.tier, "reason": reason,
                 "entry_adx": p.entry_adx, "risk_quote": p.risk_quote_initial,
                 "entry_price": p.buy_price, "exit_price": price,
                 "entry_ts": p.entry_timestamp_ms, "legs": len(p.legs),
                 "ret": (p.realized_pnl / p.entry_equity
                         if p.entry_equity > 0 else 0.0)}
        record_closed_trade(ctx, self.cfg, self.risk, trade, now)
        trades.append(trade)
        ctx.position = Position()
        ctx.state = BotState.FLAT.value

    def _build_result(self, ctx: BotContext, trades: List[Dict[str, Any]],
                      equity_curve: List[float],
                      timestamps: List[int]) -> BacktestResult:
        m = compute_metrics(equity_curve, trades, timeframe=self.cfg.timeframe)
        return BacktestResult(
            trades=trades, equity_curve=equity_curve, timestamps=timestamps,
            initial_capital=self.initial_capital,
            final_equity=equity_curve[-1] if equity_curve else self.initial_capital,
            module_stats=ctx.portfolio.per_module,
            tier_stats=ctx.portfolio.per_tier, **m)


# ---------- Séries de biais sans look-ahead ----------

def build_bias_series(ltf_ts: pd.Series, htf: pd.DataFrame, ema_span: int,
                      up_buf: float, down_buf: float,
                      htf_timeframe: str) -> pd.Series:
    """Biais HTF connu à l'OUVERTURE de chaque barre LTF : seules les barres
    HTF clôturées (ts + durée <= ts LTF) sont utilisées."""
    h = htf[["ts", "close"]].copy().sort_values("ts").reset_index(drop=True)
    e = h["close"].astype(float).ewm(span=ema_span, adjust=False).mean()
    close = h["close"].astype(float)
    bias = np.where(close > e * (1 + up_buf), "UP",
                    np.where(close < e * (1 - down_buf), "DOWN", ""))
    right = pd.DataFrame({
        "avail_ts": (h["ts"].astype("int64") + _timeframe_ms(htf_timeframe)),
        "bias": bias}).sort_values("avail_ts")
    left = pd.DataFrame({"ts": ltf_ts.astype("int64").values,
                         "_i": np.arange(len(ltf_ts))}).sort_values("ts")
    m = pd.merge_asof(left, right, left_on="ts", right_on="avail_ts",
                      direction="backward").sort_values("_i")
    vals = [v if v in ("UP", "DOWN") else None for v in m["bias"].tolist()]
    return pd.Series(vals, index=ltf_ts.index, dtype=object)


def build_btc_vol_mult_series(ltf_ts: pd.Series, btc_1h: pd.DataFrame,
                              cfg: Config) -> pd.Series:
    b = btc_1h[["ts", "close"]].copy().sort_values("ts").reset_index(drop=True)
    ret = np.log(b["close"].astype(float) / b["close"].astype(float).shift(1))
    vol = ret.rolling(cfg.btc_vol_window_hours).std() * math.sqrt(24 * 365)
    right = pd.DataFrame({"avail_ts": b["ts"].astype("int64") + 3_600_000,
                          "vol": vol}).sort_values("avail_ts")
    left = pd.DataFrame({"ts": ltf_ts.astype("int64").values,
                         "_i": np.arange(len(ltf_ts))}).sort_values("ts")
    m = pd.merge_asof(left, right, left_on="ts", right_on="avail_ts",
                      direction="backward").sort_values("_i")
    mult = [_btc_vol_mult(None if _isnan(v) else float(v), cfg)
            for v in m["vol"].tolist()]
    return pd.Series(mult, index=ltf_ts.index, dtype=float)


def _btc_vol_mult(vol: Optional[float], cfg: Config) -> float:
    if vol is None:
        return 1.0
    if vol >= cfg.btc_vol_threshold_annual:
        return cfg.btc_vol_size_reduction
    half = cfg.btc_vol_threshold_annual * 0.5
    if vol <= half:
        return 1.0
    ratio = (vol - half) / (cfg.btc_vol_threshold_annual - half)
    return 1.0 - ratio * (1.0 - cfg.btc_vol_size_reduction)


# ---------- Walk-forward / sensibilité ----------

INDICATOR_PARAMS = frozenset({
    "trend_ema", "fast_ema", "slow_ema", "atr_period", "rsi_period",
    "macd_fast", "macd_slow", "macd_signal", "bb_period", "bb_std",
    "adx_period", "vol_ma_period", "vwap_period", "obv_ema_span",
    "swing_window", "atr_rank_window", "timeframe"})

DEFAULT_WF_GRID: Dict[str, List[Any]] = {
    "adx_trend_threshold": [20.0, 22.0, 25.0],
    "trail_atr_mult": [1.5, 2.0, 2.5],
}


@dataclass
class WalkForwardWindow:
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    is_sharpe: float
    oos_sharpe: float
    is_pf: float
    oos_pf: float
    is_wr: float
    oos_wr: float
    is_trades: int
    oos_trades: int
    degradation: float
    params: Dict[str, Any] = field(default_factory=dict)


def walk_forward(df: pd.DataFrame, cfg: Config, is_months: int = 6,
                 oos_months: int = 3, step_months: int = 2,
                 initial_capital: float = 1000.0,
                 param_grid: Optional[Dict[str, List[Any]]] = None,
                 series: Optional[Dict[str, Any]] = None,
                 min_is_trades: int = 10) -> List[WalkForwardWindow]:
    """Walk-forward ANCRÉ sur l'historique complet (pas de perte de
    warm-up) : optimisation in-sample sur la grille, puis évaluation
    out-of-sample avec les paramètres retenus."""
    grid = param_grid or DEFAULT_WF_GRID
    bad = set(grid) & INDICATOR_PARAMS
    if bad:
        raise ValueError(f"Paramètres d'indicateurs non supportés en WF: {bad}")
    df = df.reset_index(drop=True)
    if "atr" not in df.columns:
        df = compute_indicators(df, cfg)
    dt = pd.to_datetime(df["ts"], unit="ms", utc=True)
    rows = df.to_dict("records")
    series = series or {}
    keys = list(grid)
    combos = list(itertools.product(*[grid[k] for k in keys]))
    warm = min_signal_bars(cfg)
    if len(df) <= warm + 10:
        return []
    t = dt.iloc[warm]
    end = dt.iloc[-1]
    results: List[WalkForwardWindow] = []
    while True:
        is_start = t
        is_end = is_start + pd.DateOffset(months=is_months)
        oos_end = is_end + pd.DateOffset(months=oos_months)
        if oos_end > end:
            break
        a = int(dt.searchsorted(is_start))
        b = int(dt.searchsorted(is_end))
        c = int(dt.searchsorted(oos_end))
        t = t + pd.DateOffset(months=step_months)
        if b - a < 200 or c - b < 50:
            continue
        best: Optional[Tuple[float, Tuple[Any, ...], BacktestResult]] = None
        for combo in combos:
            ccfg = dataclasses.replace(cfg, **dict(zip(keys, combo)))
            res = BacktestEngine(ccfg, initial_capital).run(
                df, start=a, end=b, rows=rows, **series)
            score = res.sharpe if res.num_trades >= min_is_trades else -math.inf
            if best is None or score > best[0]:
                best = (score, combo, res)
        assert best is not None
        params = dict(zip(keys, best[1]))
        res_is = best[2]
        res_oos = BacktestEngine(dataclasses.replace(cfg, **params),
                                 initial_capital).run(
            df, start=b, end=c, rows=rows, **series)
        deg = (res_oos.sharpe / res_is.sharpe) if res_is.sharpe > 0 else 0.0
        results.append(WalkForwardWindow(
            is_start=is_start.isoformat(), is_end=is_end.isoformat(),
            oos_start=is_end.isoformat(), oos_end=oos_end.isoformat(),
            is_sharpe=res_is.sharpe, oos_sharpe=res_oos.sharpe,
            is_pf=res_is.profit_factor, oos_pf=res_oos.profit_factor,
            is_wr=res_is.win_rate, oos_wr=res_oos.win_rate,
            is_trades=res_is.num_trades, oos_trades=res_oos.num_trades,
            degradation=deg, params=params))
    return results


def walkforward_verdict(windows: List[WalkForwardWindow]) -> str:
    if not windows:
        return "❌ PAS DE DONNÉES"
    degs = [w.degradation for w in windows if w.is_sharpe > 0.3]
    if not degs:
        return "❌ IS SHARPE TROP FAIBLE"
    median_deg = statistics.median(degs)
    pct_bad = sum(1 for d in degs if d < 0.5) / len(degs)
    oos_pos = sum(1 for w in windows if w.oos_sharpe > 0) / len(windows)
    if median_deg >= 0.7 and pct_bad <= 0.2 and oos_pos >= 0.6:
        return f"✅ ROBUSTE (dégradation médiane {median_deg:.2f})"
    if median_deg >= 0.5 and pct_bad <= 0.4:
        return f"⚠️ LIMITE (dégradation médiane {median_deg:.2f})"
    return f"❌ OVERFITTÉE (dégradation médiane {median_deg:.2f})"


def format_walkforward_report(windows: List[WalkForwardWindow]) -> str:
    if not windows:
        return "Aucune fenêtre valide."
    lines = ["Walk-forward (optimisation IS → validation OOS):", "─" * 100]
    lines.append(f"{'IS start':<12} {'IS Sharpe':>10} {'OOS Sharpe':>11} "
                 f"{'Dégrad':>8} {'IS PF':>7} {'OOS PF':>7} {'OOS WR':>7} "
                 f"{'OOS N':>6}  params")
    for w in windows:
        lines.append(
            f"{w.is_start[:10]:<12} {w.is_sharpe:>10.2f} "
            f"{w.oos_sharpe:>11.2f} {w.degradation:>8.2f} "
            f"{w.is_pf:>7.2f} {w.oos_pf:>7.2f} {w.oos_wr:>6.1f}% "
            f"{w.oos_trades:>6}  {w.params}")
    lines.append("─" * 100)
    lines.append(walkforward_verdict(windows))
    return "\n".join(lines)


def run_sensitivity(df: pd.DataFrame, base_cfg: Config,
                    param_grid: Dict[str, List[Any]],
                    series: Optional[Dict[str, Any]] = None,
                    start: Optional[int] = None) -> pd.DataFrame:
    series = series or {}
    raw_cols = ["ts", "open", "high", "low", "close", "volume"]
    base_df = df if "atr" in df.columns else compute_indicators(df, base_cfg)
    base_rows = base_df.to_dict("records")
    results = []
    for param_name, values in param_grid.items():
        for val in values:
            try:
                cfg = dataclasses.replace(base_cfg, **{param_name: val})
                if param_name in INDICATOR_PARAMS:
                    d = compute_indicators(df[raw_cols], cfg)
                    res = BacktestEngine(cfg).run(d, start=start, **series)
                else:
                    res = BacktestEngine(cfg).run(base_df, start=start,
                                                  rows=base_rows, **series)
                results.append({"param": param_name, "value": val,
                                "sharpe": res.sharpe, "pf": res.profit_factor,
                                "wr": res.win_rate, "trades": res.num_trades,
                                "max_dd": res.max_drawdown_pct})
            except Exception as e:
                results.append({"param": param_name, "value": val,
                                "sharpe": 0.0, "pf": 0.0, "wr": 0.0,
                                "trades": 0, "max_dd": 0.0, "error": str(e)})
    return pd.DataFrame(results)


def report_sensitivity(results: pd.DataFrame) -> str:
    lines = ["Analyse de sensibilité", "=" * 70]
    for param, group in results.groupby("param"):
        lines.append(f"\n{param}:")
        for _, row in group.iterrows():
            marker = "⚠️" if row["trades"] < 20 else "  "
            err = f" ERREUR: {row['error']}" if "error" in row and \
                isinstance(row.get("error"), str) else ""
            lines.append(f"  {marker} {str(row['value']):<12} → "
                         f"Sharpe={row['sharpe']:.2f} PF={row['pf']:.2f} "
                         f"WR={row['wr']:.1f}% trades={int(row['trades'])}{err}")
        sharpe_std = group["sharpe"].std()
        if (group["sharpe"] <= 0).all():
            lines.append("  → 🔴 AUCUN EDGE : Sharpe ≤ 0 pour toutes les valeurs "
                         "(stabilité ≠ rentabilité)")
        elif sharpe_std > 0.5:
            lines.append(f"  → 🔴 CRITIQUE : Sharpe varie de {sharpe_std:.2f}")
        elif sharpe_std > 0.2:
            lines.append(f"  → 🟡 MODÉRÉ : Sharpe varie de {sharpe_std:.2f}")
        else:
            lines.append("  → 🟢 STABLE")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# SECTION 15 — BOT LOOP
# ══════════════════════════════════════════════════════════════════════

OHLCV_LIMIT = 1000   # EMA200 : résidu d'initialisation < 0,01 % sur 1000 barres

_running = True


def _handle_stop(sig, frame):
    global _running
    print(f"\nSignal {sig} → arrêt propre")
    _running = False


def install_signal_handlers() -> None:
    """Installé par run_bot() uniquement (pas à l'import du module)."""
    for _s in ("SIGINT", "SIGTERM"):
        try:
            if hasattr(signal, _s):
                signal.signal(getattr(signal, _s), _handle_stop)
        except Exception:
            pass


def _cached_bias(cache: Dict[str, Any], cfg: Config, logger: logging.Logger,
                 label: str, fetch: Callable[[], pd.DataFrame], span: int,
                 up_buf: float, down_buf: float) -> Optional[str]:
    now = time.time()
    if now - cache["ts"] < cfg.cache_ttl_sec:
        return cache["bias"]
    try:
        df = fetch()
        if len(df) < span + 2:
            raise ValueError(f"historique insuffisant ({len(df)} barres)")
        close = df["close"].astype(float)
        ema_val = float(close.ewm(span=span, adjust=False).mean().iloc[-2])
        last_closed = float(close.iloc[-2])
        cache["bias"] = ("UP" if last_closed > ema_val * (1 + up_buf)
                         else "DOWN" if last_closed < ema_val * (1 - down_buf)
                         else None)
        cache["ts"] = now
        cache["error_count"] = 0
    except Exception as e:
        cache["error_count"] = int(cache.get("error_count", 0)) + 1
        # Fail-closed : biais inconnu = pas d'entrée ; nouvelle tentative
        # rapide tant que les erreurs restent rares.
        retry = 60 if cache["error_count"] < cfg.max_consecutive_api_errors else 300
        cache["ts"] = now - cfg.cache_ttl_sec + retry
        cache["bias"] = None
        logger.warning(f"[BIAS] {label} KO: {e}")
    return cache["bias"]


def _htf_bias(ex: ExchangeAdapter, cfg: Config,
              logger: logging.Logger) -> Optional[str]:
    if not cfg.htf_bias_enabled:
        return None
    return _cached_bias(
        ex._htf_cache, cfg, logger, "HTF",
        lambda: ex.fetch_ohlcv_htf(cfg.symbol, cfg.htf_timeframe,
                                   limit=min(1000, cfg.htf_ema * 3 + 20)),
        cfg.htf_ema, cfg.htf_buffer, cfg.htf_buffer)


def _btc_bias(ex: ExchangeAdapter, cfg: Config,
              logger: logging.Logger) -> Optional[str]:
    if not cfg.btc_bias_enabled:
        return None
    return _cached_bias(
        ex._btc_cache, cfg, logger, "BTC",
        lambda: ex.fetch_ohlcv_htf(cfg.btc_symbol, cfg.htf_timeframe,
                                   limit=min(1000, cfg.btc_ema * 3 + 20)),
        cfg.btc_ema, cfg.btc_bull_buffer, cfg.btc_bear_buffer)


def _btc_annual_vol(ex: ExchangeAdapter, cfg: Config,
                    logger: logging.Logger) -> Optional[float]:
    if not cfg.btc_vol_filter_enabled:
        return None
    now = time.time()
    c = ex._btc_vol_cache
    if now - c["ts"] < cfg.cache_ttl_sec:
        return c["vol"]
    try:
        df = ex.fetch_ohlcv_htf(cfg.btc_symbol, "1h",
                                limit=cfg.btc_vol_window_hours * 4)
        df = df.iloc[:-1]   # bougies fermées uniquement
        ret = np.log(df["close"].astype(float)
                     / df["close"].astype(float).shift(1)).dropna()
        recent = ret.tail(cfg.btc_vol_window_hours)
        if len(recent) < max(8, cfg.btc_vol_window_hours // 2):
            c.update({"ts": now, "vol": None})
            return None
        vol = float(recent.std() * math.sqrt(24 * 365))
        c.update({"ts": now, "vol": vol, "error_count": 0})
        return vol
    except Exception as e:
        c["error_count"] = int(c.get("error_count", 0)) + 1
        c["ts"] = now
        c["vol"] = None
        logger.warning(f"[VOL] BTC KO: {e}")
        return None


def _send_daily_report(ctx: BotContext, ex: ExchangeAdapter,
                       notifier: Notifier, cfg: Config,
                       bchain: Optional[BlockchainAdapter] = None) -> None:
    today = _today_utc()
    if ctx.last_report_date == today:
        return
    if _utcnow().hour < cfg.report_hour_utc:
        return
    if time.time() - ctx.last_report_attempt_ts < cfg.report_retry_throttle_sec:
        return
    ctx.last_report_attempt_ts = time.time()
    eq = ex.get_equity(ctx)
    total = ctx.portfolio.stats_wins + ctx.portfolio.stats_losses
    wr = ctx.portfolio.stats_wins / total * 100 if total else 0.0
    lines = [
        f"📊 Rapport {today} ({VERSION_MODULE})",
        f"Mode={cfg.run_mode} | Equity={'n/a' if eq is None else f'{eq:.2f}'}",
        f"Trades={total} | WR={wr:.1f}% | "
        f"PnL={ctx.portfolio.stats_total_pnl:.4f}",
        f"ConsecLoss={ctx.portfolio.consec_losses} | "
        f"Halted={ctx.risk.halted}"
        + (f" ({ctx.risk.halt_reason})" if ctx.risk.halted else ""),
    ]
    if bchain and bchain.enabled:
        lines.append(f"⛓️ {cfg.blockchain_chain} | "
                     f"bal={ctx.blockchain.last_balance_eth:.6f} | "
                     f"gas={ctx.blockchain.total_gas_spent_eth:.6f} | "
                     f"tx={ctx.blockchain.tx_count} | "
                     f"sweep={ctx.blockchain.sweep_count}")
    if notifier("\n".join(lines), dedup_key=f"report-{today}", sync=True):
        ctx.last_report_date = today


def _init_benchmark(ctx: BotContext, ex: ExchangeAdapter,
                    logger: logging.Logger) -> None:
    if ctx.benchmark_start_equity is None:
        init_eq = ex.get_equity(ctx, force=True)
        if init_eq is not None and init_eq > 0:
            ctx.benchmark_start_equity = float(init_eq)
            logger.info(f"[BENCH] equity initiale: {init_eq:.2f}")
    if ctx.benchmark_start_price is None:
        try:
            init_px = ex.get_ticker().get("last", 0)
            if init_px > 0:
                ctx.benchmark_start_price = float(init_px)
        except Exception as e:
            logger.warning(f"[BENCH] price init KO: {e}")


def _compute_subsystems(ctx: BotContext,
                        bchain: Optional[BlockchainAdapter]) -> Dict[str, str]:
    cex = "PENDING" if ctx.pending_order else "OK"
    chain = "OFF"
    if bchain and bchain.enabled:
        chain = "OK" if not ctx.blockchain.last_pending_tx_hash else "TX_PENDING"
    prot = ctx.position.protection_mode if ctx.position.in_position else "—"
    recon = "ORPHAN" if ctx.orphan_balance else "OK"
    bench = "OK" if ctx.benchmark_start_equity else "INIT"
    risk_flags = []
    if ctx.risk.halted:
        risk_flags.append("FROZEN" if is_frozen(ctx) else "HALT")
    if ctx.risk.paused_until:
        risk_flags.append("PAUSE")
    if ctx.risk.cooldown_until:
        risk_flags.append("COOL")
    return {"CEX": cex, "CHAIN": chain, "PROT": prot, "RECON": recon,
            "BENCH": bench, "RISK": "+".join(risk_flags) if risk_flags else "OK"}


class BotRunner:
    def __init__(self, cfg: Config, logger: logging.Logger,
                 ex: ExchangeAdapter, store: Store, notifier: Notifier,
                 risk: RiskEngine, exec_engine: ExecutionEngine,
                 adaptive: AdaptiveEngine, heartbeat: Optional[Heartbeat] = None,
                 bchain: Optional[BlockchainAdapter] = None):
        self.cfg = cfg
        self.logger = logger
        self.ex = ex
        self.store = store
        self.notifier = notifier
        self.risk = risk
        self.exec = exec_engine
        self.adaptive = adaptive
        self.heartbeat = heartbeat
        self.bchain = bchain
        self.ctx: Optional[BotContext] = None
        self.regime_now = "UNCLEAR"
        self._last_chain_refresh = 0.0
        self._last_sweep_check = 0.0

    def boot(self) -> bool:
        cfg = self.cfg
        try:
            self.ex.load_markets()
        except Exception as e:
            self.logger.error(f"[BOOT] load_markets KO: {e}")
            return False
        clock = sync_exchange_clock(self.ex.exchange)
        if clock is not None:
            self.logger.info(f"[CLOCK] {describe_clock(clock.offset_ms, clock.uncertainty_ms)}"
                             f" → le bot utilise l'heure de Binance")
        if cfg.run_mode == "live" and not self.ex.self_test_conditional_orders():
            self.logger.critical("[BOOT] self-test ordres KO → arrêt")
            return False
        try:
            ctx = self.store.load_context() or BotContext()
        except RuntimeError as e:
            self.logger.critical(f"[BOOT] {e} — restaurer la DB avant relance")
            return False
        self.ctx = ctx
        self.ex.bind_paper_context(ctx)
        if ctx.started_at is None:
            ctx.started_at = _utcnow_iso()
        ctx.bot_version = VERSION_MODULE
        if not (cfg.adaptive_freshness_min <= ctx.adaptive.current_freshness_min
                <= cfg.adaptive_freshness_max):
            ctx.adaptive.current_freshness_min = cfg.adaptive_freshness_max
        _init_benchmark(ctx, self.ex, self.logger)
        try:
            reconcile(ctx, cfg, self.ex, self.logger, self.exec)
        except Exception as e:
            self.logger.error(f"[BOOT] reconciliation KO (fail-closed): {e}")
            return False
        if self.bchain and self.bchain.enabled:
            self.bchain.refresh_state(ctx)
        if not self.store.save_context(ctx, cfg.run_mode, force=True):
            self.logger.critical("[BOOT] DB non inscriptible → arrêt")
            return False
        t = self.ex.get_ticker()
        self.logger.info(
            f"[BOOT] {cfg.symbol} last={t['last']} minQty={self.ex.rules.min_amount} "
            f"minNotional={self.ex.min_notional()} stop={self.ex.stop_order_type} "
            f"état={'LONG' if ctx.position.in_position else 'FLAT'}"
            + (f" HALT={ctx.risk.halt_reason}" if ctx.risk.halted else ""))
        return True

    def run_cycle(self) -> None:
        cfg = self.cfg
        ctx = self.ctx
        assert ctx is not None
        df = self.ex.fetch_ohlcv(cfg.timeframe, limit=OHLCV_LIMIT)
        if df.empty or len(df) < 60:
            self.logger.warning(f"[CYCLE] OHLCV insuffisant ({len(df)})")
            return
        df = compute_indicators(df, cfg)
        closed = df.iloc[-2]
        live_price = self.ex.get_ticker()["last"]
        current_candle_ts = int(df.iloc[-1]["ts"])
        ctx.cycle_count += 1
        try:
            self.adaptive.observe_closed_candle(ctx.adaptive, int(closed["ts"]))
            self.adaptive.update(ctx, closed)
            self.exec.resolve_pending(ctx)
            equity = self.ex.get_equity(ctx)
            halted, _reason = self.risk.check_circuit_breakers(
                ctx, equity, consec_pause=self.adaptive.effective_consec_pause(
                    ctx.adaptive, cfg.consec_loss_pause))
            if len(df) > cfg.flash_move_lookback + 1:
                detect_flash_move(
                    float(df.iloc[-1 - cfg.flash_move_lookback]["close"]),
                    live_price, ctx, cfg)
            self.regime_now = detect_regime(closed, cfg)
            if ctx.position.in_position:
                self.exec.manage_position(ctx, df, closed, live_price,
                                          current_candle_ts)
            else:
                self.exec.check_orphan(ctx, live_price)
                self.exec.sweep_dust(ctx, live_price)
                if (not halted and not ctx.orphan_balance
                        and not ctx.pending_order and self.store.healthy
                        and not in_cooldown(ctx) and not in_flash_cooldown(ctx)):
                    self._maybe_enter(ctx, df, closed, live_price,
                                      current_candle_ts, equity)
        finally:
            self.store.save_context(ctx, cfg.run_mode)
        self._post_cycle(ctx, live_price)

    def _maybe_enter(self, ctx: BotContext, df: pd.DataFrame, closed: Any,
                     live_price: float, current_candle_ts: int,
                     equity: Optional[float]) -> None:
        cfg = self.cfg
        htf = _htf_bias(self.ex, cfg, self.logger)
        btc = _btc_bias(self.ex, cfg, self.logger)
        btc_vol = _btc_annual_vol(self.ex, cfg, self.logger)
        btc_vol_mult = _btc_vol_mult(btc_vol, cfg)
        atr_eff = self.adaptive.effective_atr_min(ctx.adaptive, cfg.atr_min_pct)
        sig = generate_signal(df, htf, btc, ctx, cfg, atr_min_pct=atr_eff)
        closed_ts = int(closed["ts"])
        if ctx.last_decision_candle_ts != closed_ts or sig.is_buy:
            ctx.last_decision_candle_ts = closed_ts
            self.store.log_decision({
                "signal": sig.action, "module": sig.module,
                "regime": sig.regime, "score": sig.score,
                "reject": sig.reject, "htf_bias": htf, "btc_bias": btc,
                "btc_vol": btc_vol, "btc_vol_mult": btc_vol_mult,
                "atr_min_pct_eff": atr_eff,
                "freshness_eff": self.adaptive.effective_freshness(
                    ctx.adaptive, cfg.max_signal_age_min)}, cfg.run_mode)
        if sig.is_buy and current_candle_ts != ctx.last_signal_candle_ts:
            res = self.exec.enter(
                ctx, closed, sig, live_price, current_candle_ts, equity,
                btc_vol_mult=btc_vol_mult,
                freshness_max=self.adaptive.effective_freshness(
                    ctx.adaptive, cfg.max_signal_age_min))
            if res != EntryResult.SKIPPED:
                # Un ordre est parti : aucune nouvelle tentative sur cette bougie.
                ctx.last_signal_candle_ts = current_candle_ts

    def _post_cycle(self, ctx: BotContext, live_price: float) -> None:
        cfg = self.cfg
        if self.bchain and self.bchain.enabled:
            try:
                self.bchain.poll_pending_tx(ctx)
                if time.time() - self._last_chain_refresh > 300:
                    self.bchain.refresh_state(ctx)
                    self._last_chain_refresh = time.time()
                if cfg.blockchain_sweep_enabled \
                        and time.time() - self._last_sweep_check > 60:
                    self.bchain.maybe_sweep(ctx)
                    self._last_sweep_check = time.time()
            except Exception as e:
                # Le sous-système EVM ne doit jamais retarder le CEX.
                self.logger.error(f"[CHAIN] cycle KO: {scrub_secrets(str(e), [cfg.blockchain_rpc_url])}")
        if time.time() - ctx.last_equity_snapshot_ts >= 300:
            eq = self.ex.get_equity(ctx, force=True)
            if eq is not None:
                try:
                    if cfg.run_mode == "paper":
                        cash, bqty = (ctx.portfolio.paper_cash,
                                      ctx.portfolio.paper_base)
                    else:
                        cash = self.ex.get_total_balance(cfg.quote, ctx)
                        bqty = self.ex.bot_total_base(ctx)
                    self.store.log_equity(eq, cash, bqty, live_price,
                                          ctx.risk.halted, cfg.run_mode)
                except Exception as e:
                    self.logger.warning(f"[EQ] snapshot KO: {e}")
            ctx.last_equity_snapshot_ts = time.time()
        _send_daily_report(ctx, self.ex, self.notifier, cfg, self.bchain)
        if self.heartbeat and ctx.cycle_count % max(1, cfg.heartbeat_every_cycles) == 0:
            self.heartbeat.tick(ctx, self.regime_now, self.ex.get_equity(ctx),
                                live_price, ctx.adaptive,
                                ctx.benchmark_start_equity,
                                subsystems=_compute_subsystems(ctx, self.bchain))
        self.store.save_context(ctx, cfg.run_mode)

    def run_forever(self) -> None:
        cfg = self.cfg
        backoff = cfg.network_backoff_init
        while _running:
            try:
                self.run_cycle()
                backoff = cfg.network_backoff_init
                self._sleep(cfg.loop_interval_sec)
                continue
            except AmbiguousOrder as e:
                self.logger.critical(f"[CYCLE] ordre ambigu non géré: {e}")
                if self.ctx is not None:
                    halt_ctx(self.ctx, "AMBIGUOUS_ORDER", HaltKind.INTEGRITY)
                    self.store.save_context(self.ctx, cfg.run_mode, force=True)
                self.notifier(f"🚨 Ordre ambigu — halt: {e}", critical=True)
            except ccxt.NetworkError as e:
                self.logger.warning(f"[CYCLE] NetworkError: {e}")
            except ccxt.ExchangeError as e:
                self.logger.error(f"[CYCLE] ExchangeError: {e}")
            except Exception as e:
                self.logger.exception(f"[CYCLE] KO: {e}")
            self._sleep(backoff)
            backoff = min(backoff * cfg.network_backoff_mult,
                          cfg.network_backoff_max)

    @staticmethod
    def _sleep(seconds: float) -> None:
        end = time.time() + seconds
        while _running and time.time() < end:
            time.sleep(min(1.0, max(0.0, end - time.time())))


def run_bot():
    global _running
    _running = True
    try:
        cfg = load_config_from_env()
    except ValueError as e:
        print(f"Configuration invalide : {e}", file=sys.stderr)
        sys.exit(2)
    logger = build_logger(cfg)
    hb_logger = build_heartbeat_logger(cfg)
    notifier = Notifier(os.environ.get("TELEGRAM_TOKEN", ""),
                        os.environ.get("TELEGRAM_CHAT_ID", ""), logger=logger)
    logger.info(dump_config_summary(cfg))
    install_signal_handlers()
    locks = acquire_instance_locks(cfg.lock_file, cfg.db_file)
    store: Optional[Store] = None
    runner: Optional[BotRunner] = None
    try:
        store = Store(cfg.db_file, logger)
        ex = ExchangeAdapter(cfg, logger)
        risk = RiskEngine(cfg, logger)
        exec_engine = ExecutionEngine(cfg, logger, ex, store, notifier, risk)
        bchain: Optional[BlockchainAdapter] = None
        if cfg.blockchain_enabled:
            try:
                bchain = BlockchainAdapter(cfg, logger, store, notifier)
            except BlockchainError as e:
                logger.critical(f"[BOOT] blockchain init KO: {e}")
                return
        runner = BotRunner(cfg, logger, ex, store, notifier, risk,
                           exec_engine, AdaptiveEngine(cfg, logger),
                           Heartbeat(cfg, logger, hb_logger), bchain)
        if not runner.boot():
            return
        runner.run_forever()
    finally:
        if store is not None:
            if runner is not None and runner.ctx is not None:
                store.save_context(runner.ctx, cfg.run_mode, force=True)
            store.checkpoint(mode="TRUNCATE")
            store.close()
        notifier.close()
        release_locks(locks)
        logger.info("[BOOT] arrêt propre.")


# ══════════════════════════════════════════════════════════════════════
# SECTION 16 — CLI
# ══════════════════════════════════════════════════════════════════════

def _parse_utc_date(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fetch_historical(symbol: str, timeframe: str, start: str, end: str,
                     exchange: Any = None) -> pd.DataFrame:
    ex = exchange or make_binance()
    since = int(_parse_utc_date(start).timestamp() * 1000)
    end_ms = int(_parse_utc_date(end).timestamp() * 1000)
    all_bars: List[List[Any]] = []
    while since < end_ms:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        all_bars.extend(b[:6] for b in batch)
        nxt = int(batch[-1][0]) + 1
        if nxt <= since:
            break
        since = nxt
    df = pd.DataFrame(all_bars, columns=["ts", "open", "high", "low", "close",
                                         "volume"])
    df = df[df["ts"] < end_ms]
    df = df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def load_backtest_data(cfg: Config, start: str, end: str,
                       csv_path: Optional[str] = None,
                       with_bias: bool = True
                       ) -> Tuple[pd.DataFrame, Dict[str, Any], int]:
    """Charge l'historique avec une période de chauffe AVANT `start`
    (indicateurs et EMA HTF stabilisés) et construit les séries de biais
    sans look-ahead. Retourne (df, series, index_de_départ)."""
    tf_ms = _timeframe_ms(cfg.timeframe)
    warm_bars = min_signal_bars(cfg) + cfg.trend_ema * 3
    start_dt = _parse_utc_date(start)
    warm_start = (start_dt - timedelta(milliseconds=warm_bars * tf_ms)).isoformat()
    if csv_path:
        df = pd.read_csv(csv_path)
        df = df[["ts", "open", "high", "low", "close", "volume"]]
        df = df.sort_values("ts").reset_index(drop=True)
        with_bias = False
    else:
        df = fetch_historical(cfg.symbol, cfg.timeframe, warm_start, end)
    df = compute_indicators(df, cfg)
    start_idx = int(np.searchsorted(df["ts"].values,
                                    int(start_dt.timestamp() * 1000)))
    series: Dict[str, Any] = {}
    if with_bias:
        htf_ms = _timeframe_ms(cfg.htf_timeframe)
        htf_start = (start_dt - timedelta(
            milliseconds=cfg.htf_ema * 3 * htf_ms)).isoformat()
        if cfg.htf_bias_enabled:
            htf = fetch_historical(cfg.symbol, cfg.htf_timeframe, htf_start, end)
            series["htf_bias_series"] = build_bias_series(
                df["ts"], htf, cfg.htf_ema, cfg.htf_buffer, cfg.htf_buffer,
                cfg.htf_timeframe)
        if cfg.btc_bias_enabled:
            btc = fetch_historical(cfg.btc_symbol, cfg.htf_timeframe,
                                   htf_start, end)
            series["btc_bias_series"] = build_bias_series(
                df["ts"], btc, cfg.btc_ema, cfg.btc_bull_buffer,
                cfg.btc_bear_buffer, cfg.htf_timeframe)
        if cfg.btc_vol_filter_enabled:
            btc1h = fetch_historical(cfg.btc_symbol, "1h", warm_start, end)
            series["btc_vol_mult_series"] = build_btc_vol_mult_series(
                df["ts"], btc1h, cfg)
    return df, series, start_idx


def _backtest_cfg(args) -> Config:
    symbol, base, quote = _parse_symbol(args.symbol)
    return Config(run_mode="paper", symbol=symbol, base=base, quote=quote,
                  timeframe=args.timeframe,
                  htf_timeframe=getattr(args, "htf_timeframe", "4h"),
                  sizing_mode=getattr(args, "sizing_mode", "fixed"),
                  intrabar_partial_mode=getattr(args, "intrabar", "ohlc"))


def cmd_backtest(args):
    cfg = _backtest_cfg(args)
    print(f"Chargement {cfg.symbol} {cfg.timeframe} de {args.start} à {args.end}…")
    df, series, start_idx = load_backtest_data(
        cfg, args.start, args.end, args.csv, with_bias=not args.no_bias)
    print(f"{len(df)} barres (dont chauffe), biais={sorted(series) or 'aucun'}")
    res = BacktestEngine(cfg, initial_capital=args.capital).run(
        df, start=start_idx, **series)
    print(format_report({k: getattr(res, k) for k in (
        "total_return_pct", "sharpe", "sortino", "max_drawdown_pct",
        "profit_factor", "win_rate", "expectancy_r", "num_trades")}))
    print("Par module :")
    for mod, info in res.module_stats.items():
        t = info.get("trades", 0)
        w = info.get("wins", 0)
        wr = w / t * 100 if t else 0
        print(f"  {mod}: {w}W/{t - w}L ({wr:.0f}%) pnl={info.get('pnl', 0):.4f}")
    mc = monte_carlo_trades(res.trades)
    if mc:
        print(f"\nMonte Carlo (2000 tirages des trades) : rendement p5/p50/p95 = "
              f"{mc['ret_p5']:+.1f}% / {mc['ret_p50']:+.1f}% / "
              f"{mc['ret_p95']:+.1f}% | DD max p50/p95 = {mc['dd_p50']:.1f}% / "
              f"{mc['dd_p95']:.1f}% | P(perte) = {mc['prob_loss']:.0f}%")
    if res.num_trades < 30:
        print("\n⚠️ Moins de 30 trades : résultats statistiquement non significatifs.")
    return 0


def cmd_walkforward(args):
    cfg = _backtest_cfg(args)
    df, series, _ = load_backtest_data(cfg, args.start, args.end, args.csv,
                                       with_bias=not args.no_bias)
    windows = walk_forward(df, cfg, is_months=args.is_months,
                           oos_months=args.oos_months,
                           step_months=args.step_months, series=series)
    print(format_walkforward_report(windows))
    return 0


def cmd_sensitivity(args):
    cfg = _backtest_cfg(args)
    df, series, start_idx = load_backtest_data(cfg, args.start, args.end,
                                               args.csv,
                                               with_bias=not args.no_bias)
    grid = {
        "adx_trend_threshold": [20.0, 22.0, 25.0, 28.0],
        "atr_min_pct": [0.003, 0.005, 0.008],
        "break_even_trigger": [0.008, 0.01, 0.015],
        "trail_atr_mult": [1.5, 2.0, 2.5],
    }
    print(report_sensitivity(run_sensitivity(df, cfg, grid, series, start_idx)))
    return 0


def cmd_wallet(args):
    cfg = load_config_from_env()
    logger = build_logger(cfg)
    print("=" * 60)
    print("Configuration blockchain")
    print("=" * 60)
    print(f"enabled          : {cfg.blockchain_enabled}")
    if not cfg.blockchain_enabled:
        print("→ Définir BLOCKCHAIN_ENABLED=true pour activer.")
        return 0
    print(f"chain            : {cfg.blockchain_chain}")
    print(f"chain_id         : {cfg.blockchain_chain_id}")
    print(f"rpc_url          : {redact_url(cfg.blockchain_rpc_url)}")
    print(f"dry_run          : {cfg.blockchain_dry_run}")
    print(f"max_tx_value_eth : {cfg.blockchain_max_tx_value_eth}")
    print(f"confirmations    : {cfg.blockchain_confirmations}")
    print(f"whitelist        : {len(cfg.blockchain_whitelist)} adresse(s)")
    print(f"track_token      : {cfg.blockchain_track_token or '(none)'}")
    print(f"sweep_enabled    : {cfg.blockchain_sweep_enabled}")
    if cfg.blockchain_sweep_enabled:
        print(f"sweep_target     : {redact_address(cfg.blockchain_sweep_target)}")
        print(f"sweep_trigger    : {cfg.blockchain_sweep_trigger_eth}")
    print()
    try:
        adapter = BlockchainAdapter(cfg, logger, None, None)
        print(f"address          : {adapter.address}")
        print(f"balance_native   : {adapter.balance_native():.6f}")
        if adapter._token_contract is not None:
            print(f"balance_token    : {adapter.balance_token():.6f} "
                  f"({cfg.blockchain_track_token_symbol})")
        print(f"eip1559_support  : {adapter._eip1559_supported}")
    except BlockchainError as e:
        print(f"❌ {e}")
        return 1
    return 0


def cmd_docs(args):
    print("=" * 72)
    print(f"DOCUMENTATION VARIABLES D'ENVIRONNEMENT ({VERSION_MODULE})")
    print("=" * 72)
    for k, doc in sorted(ENV_DOC.items()):
        print(f"  {k:<35} {doc}")
    print("\nExemples :")
    print("  PAPER        : RUN_MODE=paper python v29.py bot")
    print("  TESTNET      : BINANCE_TESTNET=true RUN_MODE=live \\")
    print("                 ENABLE_LIVE_TRADING=true \\")
    print("                 LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_RISK \\")
    print("                 BINANCE_API_KEY=... BINANCE_API_SECRET=... \\")
    print("                 python v29.py bot")
    print("  BACKTEST     : python v29.py backtest --start 2023-01-01 --end 2025-01-01")
    print("  APRÈS HALT   : python v29.py status   puis   python v29.py resume")
    return 0


def _load_ctx_for_cli() -> Tuple[Config, Store, Optional[BotContext]]:
    cfg = load_config_from_env()
    lg = logging.getLogger(f"{_LOG_ROOT}.cli")
    if not lg.handlers:
        lg.addHandler(logging.StreamHandler())
    store = Store(cfg.db_file, lg)
    return cfg, store, store.load_context()


def cmd_status(args):
    cfg, store, ctx = _load_ctx_for_cli()
    try:
        if ctx is None:
            print("Aucun contexte en base.")
            return 0
        p = ctx.position
        print(f"Version   : {ctx.bot_version} (schéma {ctx.schema_version})")
        print(f"État      : {ctx.state} | cycles={ctx.cycle_count}")
        print(f"Position  : {'LONG' if p.in_position else 'FLAT'}"
              + (f" {p.amount_held} @ {p.buy_price} SL={p.sl_price} "
                 f"TP={p.tp_price} prot={p.protection_mode}"
                 if p.in_position else ""))
        print(f"Halt      : {ctx.risk.halted} {ctx.risk.halt_kind or ''} "
              f"{ctx.risk.halt_reason or ''}")
        print(f"Pause     : {ctx.risk.paused_until or '-'} | cooldown="
              f"{ctx.risk.cooldown_until or '-'}")
        print(f"Orphelin  : {ctx.orphan_balance} | dust={ctx.portfolio.dust_base}")
        print(f"Pending   : {json.dumps(ctx.pending_order) if ctx.pending_order else '-'}")
        print(f"Stats     : W={ctx.portfolio.stats_wins} L={ctx.portfolio.stats_losses} "
              f"PnL={ctx.portfolio.stats_total_pnl:.4f}")
        if ctx.blockchain.last_pending_tx_hash:
            print(f"Tx EVM    : {ctx.blockchain.last_pending_tx_hash}")
        return 0
    finally:
        store.close()


def cmd_resume(args):
    """Lève les blocages après audit manuel. Le bot doit être arrêté."""
    cfg = load_config_from_env()
    locks = acquire_instance_locks(cfg.lock_file, cfg.db_file)
    try:
        _cfg, store, ctx = _load_ctx_for_cli()
        try:
            if ctx is None:
                print("Aucun contexte en base.")
                return 0
            if ctx.pending_order and not args.force_clear_pending:
                print("❌ Intention d'ordre en attente : vérifier l'ordre sur "
                      "l'exchange puis relancer avec --force-clear-pending.")
                return 1
            clear_halt(ctx)
            ctx.risk.paused_until = None
            ctx.risk.cooldown_until = None
            ctx.risk.cooldown_reason = None
            ctx.orphan_balance = False
            ctx.orphan_balance_since = None
            ctx.portfolio.losses_since_pause = 0
            if args.force_clear_pending:
                ctx.pending_order = None
            if args.clear_chain_pending:
                ctx.blockchain.last_pending_tx_hash = None
                ctx.blockchain.pending_tx_hashes = []
                ctx.blockchain.last_pending_tx_nonce = None
                ctx.blockchain.rbf_attempts = 0
            if args.flat:
                ctx.position = Position()
                ctx.state = BotState.FLAT.value
            store.save_context(ctx, cfg.run_mode, force=True)
            store.log_event("manual_resume", "WARNING", vars(args), cfg.run_mode,
                            durable=True)
            print("✅ Blocages levés. Au prochain démarrage, la reconciliation "
                  "revérifiera l'exchange.")
            return 0
        finally:
            store.close()
    finally:
        release_locks(locks)


def cmd_test(args):
    try:
        import pytest
    except ImportError:
        print("pytest requis : pip install pytest")
        return 1
    here = os.path.dirname(os.path.abspath(__file__))
    return pytest.main([os.path.join(here, "tests"), "-q"])


# ══════════════════════════════════════════════════════════════════════
# SECTION 17 — ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

def _add_data_args(p: argparse.ArgumentParser, start: str, end: str) -> None:
    p.add_argument("--symbol", default="TRX/USDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--htf-timeframe", dest="htf_timeframe", default="4h")
    p.add_argument("--start", default=start)
    p.add_argument("--end", default=end)
    p.add_argument("--csv", default=None,
                   help="OHLCV local (ts,open,high,low,close,volume) ; "
                        "désactive les biais externes")
    p.add_argument("--no-bias", action="store_true",
                   help="Ne pas télécharger HTF/BTC (biais forcés à UP)")
    p.add_argument("--intrabar", default="ohlc",
                   choices=["ohlc", "optimistic", "conservative"])


def main():
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description=f"V29-QUANT {VERSION_MODULE}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bot", help="Lance le bot")
    sub.add_parser("docs", help="Documentation variables d'environnement")
    sub.add_parser("wallet", help="État configuration blockchain")
    sub.add_parser("test", help="Tests (pytest)")
    sub.add_parser("status", help="État persistant du bot")
    p_res = sub.add_parser("resume", help="Lève halt/pause/orphelin après audit")
    p_res.add_argument("--force-clear-pending", action="store_true")
    p_res.add_argument("--clear-chain-pending", action="store_true")
    p_res.add_argument("--flat", action="store_true",
                       help="Oublier la position locale (après vente manuelle)")

    p_bt = sub.add_parser("backtest", help="Backtest historique")
    _add_data_args(p_bt, "2023-01-01", "2025-01-01")
    p_bt.add_argument("--capital", type=float, default=1000.0)
    p_bt.add_argument("--sizing-mode", dest="sizing_mode", default="fixed")

    p_wf = sub.add_parser("walkforward", help="Walk-forward (optimisation IS/OOS)")
    _add_data_args(p_wf, "2022-01-01", "2025-01-01")
    p_wf.add_argument("--is-months", type=int, default=6)
    p_wf.add_argument("--oos-months", type=int, default=3)
    p_wf.add_argument("--step-months", type=int, default=3)

    p_sens = sub.add_parser("sensitivity", help="Sensibilité")
    _add_data_args(p_sens, "2023-01-01", "2025-01-01")

    args = parser.parse_args()
    handlers = {"docs": cmd_docs, "wallet": cmd_wallet, "test": cmd_test,
                "status": cmd_status, "resume": cmd_resume,
                "backtest": cmd_backtest, "walkforward": cmd_walkforward,
                "sensitivity": cmd_sensitivity}
    if args.command == "bot":
        run_bot()
        return
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompu.")
        sys.exit(0)
