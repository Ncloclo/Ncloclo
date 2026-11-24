"""
Ce que disent les contrats à terme perpétuels de Binance (API publique,
sans clé) : le positionnement des spéculateurs, que le marché au comptant ne
montre pas.

  - taux de financement : payé toutes les 8 heures par les acheteurs aux
    vendeurs quand il est positif (marché trop acheteur, effet de levier
    élevé) et l'inverse quand il est négatif ;
  - intérêt ouvert de BTC : montant total des contrats en cours ; une forte
    hausse veut dire beaucoup d'effet de levier, donc un risque de liquidations
    en chaîne ;
  - part des comptes acheteurs sur BTC (ratio long/short des comptes).

Information seulement : montrée dans la veille et transmise aux IA de la
veille. Elle n'agit sur aucun achat. L'étude research/sentiment.py mesure si
elle aurait aidé la stratégie.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .texte import fr

FAPI = "https://fapi.binance.com"
PAYMENTS_PER_DAY = 3            # financement payé toutes les 8 heures
Fetch = Callable[[str], Any]


def annual(rate_8h: float) -> float:
    """Taux de financement d'une période de 8 heures ramené à un an."""
    return rate_8h * PAYMENTS_PER_DAY * 365


def funding_rates(fetch: Fetch, bases: Iterable[str]) -> Dict[str, float]:
    """Dernier taux de financement (par 8 heures) de chaque crypto qui a un
    contrat perpétuel en USDT, en un seul appel."""
    want = {f"{b.upper()}USDT": b.lower() for b in bases}
    out: Dict[str, float] = {}
    for row in fetch(f"{FAPI}/fapi/v1/premiumIndex") or []:
        a = want.get(str(row.get("symbol")))
        if a is not None and row.get("lastFundingRate") not in (None, ""):
            out[a] = float(row["lastFundingRate"])
    return out


def open_interest_change(fetch: Fetch, symbol: str = "BTCUSDT", days: int = 30) -> Optional[float]:
    """Variation de l'intérêt ouvert (en dollars) sur `days` jours, ou None.
    Binance ne garde que le dernier mois."""
    rows = fetch(f"{FAPI}/futures/data/openInterestHist?symbol={symbol}&period=1d&limit={days + 1}") or []
    vals = [float(r["sumOpenInterestValue"]) for r in rows if r.get("sumOpenInterestValue")]
    if len(vals) < 2 or vals[0] <= 0:
        return None
    return vals[-1] / vals[0] - 1


def long_share(fetch: Fetch, symbol: str = "BTCUSDT") -> Optional[float]:
    """Part des comptes acheteurs (0 à 1) sur le dernier jour, ou None."""
    rows = fetch(f"{FAPI}/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1d&limit=1") or []
    if not rows:
        return None
    v = rows[-1].get("longAccount")
    return float(v) if v not in (None, "") else None


def indicators(fetch: Fetch, bases: Iterable[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Indicateurs du jour et sources indisponibles ; une source ratée
    n'empêche jamais les autres."""
    out: Dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(timespec="minutes")}
    errors: List[str] = []
    try:
        f = funding_rates(fetch, bases)
        if f:
            out["funding"] = f
            out["funding_btc"] = f.get("btc")
            out["funding_mean"] = sum(f.values()) / len(f)
    except Exception as e:
        errors.append(f"Financement des contrats à terme : {type(e).__name__}")
    for key, read in (("oi_btc_30d", open_interest_change), ("long_share_btc", long_share)):
        try:
            v = read(fetch)
            if v is not None:
                out[key] = v
        except Exception as e:
            errors.append(f"Contrats à terme ({key}) : {type(e).__name__}")
    return out, errors


def latest(watch_db: str) -> Dict[str, Any]:
    """Indicateurs des contrats à terme du dernier rapport de veille, avec
    son jour (lecture seule) ; {} sans rapport."""
    import json
    import pathlib
    import sqlite3
    if not watch_db or not pathlib.Path(watch_db).exists():
        return {}
    try:
        con = sqlite3.connect(pathlib.Path(watch_db).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return {}
    try:
        row = con.execute("SELECT day, report FROM reports ORDER BY day DESC LIMIT 1").fetchone()
        d = (json.loads(row[1]).get("indicators") or {}).get("derives") if row else None
    except (sqlite3.Error, ValueError, AttributeError):
        return {}
    finally:
        con.close()
    return dict(d, day=row[0]) if isinstance(d, dict) and d else {}


def describe(d: Optional[Dict[str, Any]]) -> Optional[str]:
    """Une ligne lisible, ou None s'il n'y a rien."""
    if not d or not any(k in d for k in ("funding_btc", "oi_btc_30d", "long_share_btc")):
        return None
    parts = []
    if d.get("funding_btc") is not None:
        parts.append(f"financement BTC {fr(d['funding_btc'] * 100, '+.4f')} % par 8 h "
                     f"({fr(annual(d['funding_btc']) * 100, '+.1f')} % par an)")
    if d.get("funding_mean") is not None:
        parts.append(f"moyenne des cryptos {fr(annual(d['funding_mean']) * 100, '+.1f')} % par an")
    if d.get("oi_btc_30d") is not None:
        parts.append(f"intérêt ouvert BTC {fr(d['oi_btc_30d'] * 100, '+.1f')} % sur 30 jours")
    if d.get("long_share_btc") is not None:
        parts.append(f"{fr(d['long_share_btc'] * 100, '.0f')} % des comptes acheteurs sur BTC")
    return "Contrats à terme : " + ", ".join(parts)


def funding_history(fetch: Fetch, symbol: str, start_ms: int, end_ms: int) -> List[Tuple[int, float]]:
    """Historique des taux de financement d'un contrat (heure ms, taux par
    8 heures), page par page (1000 paiements par appel)."""
    out: List[Tuple[int, float]] = []
    since = start_ms
    for _ in range(40):
        rows = fetch(f"{FAPI}/fapi/v1/fundingRate?symbol={symbol}&startTime={since}"
                     f"&endTime={end_ms}&limit=1000") or []
        rows = [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]
        if not rows:
            break
        out.extend(rows)
        nxt = rows[-1][0] + 1
        if len(rows) < 1000 or nxt >= end_ms:
            break
        since = nxt
    return out
