"""
Courtiers et plateformes (docs/COURTIERS.md) : où le bot pourrait jouer sa
règle, et comment il s'y branche, en démonstration seulement.

- Annuaire : courtiers forex et CFD sous MetaTrader 5, Alpaca (actions
  américaines), les plateformes crypto de la bibliothèque CCXT, avec ou sans
  testnet, et les SGI de la BRVM. Information seulement.
- Cours comparés : les cryptos du bot lues sur dix grandes plateformes et
  comparées à Binance toutes les six heures, par leurs cours publics (sans
  compte ni clé). Information seulement : aucune décision n'en dépend.
- Comptes de démonstration : MetaTrader 5 (compte démo d'un courtier),
  Alpaca (compte papier), testnets des plateformes crypto. Un compte réel est
  refusé dès qu'il est reconnu, et aucun ordre n'est envoyé. Le plan miroir
  dit, à blanc, ce que la règle y tiendrait aujourd'hui.

Chaque requête de CCXT est vérifiée avant de partir : une adresse hors de la
liste blanche de cyber.py (et, sur un testnet, hors du testnet) est refusée.

    python trendguard_bot.py courtiers                  # comptes démo et cours comparés
    python trendguard_bot.py courtiers annuaire
    python trendguard_bot.py courtiers cours            # cours relus tout de suite
    python trendguard_bot.py courtiers verifier         # comptes démo relus tout de suite
    python trendguard_bot.py courtiers set-key mt5      # saisie masquée, vérifiée avant d'être gardée
    python trendguard_bot.py courtiers set-key alpaca
    python trendguard_bot.py courtiers set-key bybit    # testnet d'une plateforme crypto
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import functools
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import v29

from . import autonomy
from . import market_watch as mw
from .texte import fr

KEY = "courtiers"                       # méta du noyau de savoir
PRICES_EVERY_HOURS = 6                  # cours comparés : quatre lectures par jour
ACCOUNTS_EVERY_HOURS = 1                # comptes de démonstration : une lecture par heure
RETRY_HOURS = 1                         # après une lecture ratée
MARKETS_DAYS = 7                        # liste des marchés d'une plateforme, gardée une semaine
NOTABLE_PCT = 1.0                       # écart de Binance à la médiane des autres, jugé notable
SAME_TOKEN_PCT = 25.0                   # au-delà : un autre jeton sous le même symbole, écarté
MIN_VOLUME = 10_000.0                   # USDT échangés en 24 h : en dessous, marché trop mince
WIDE_SPREAD = 0.02                      # fourchette plus large : dernier cours plutôt que le milieu
QUOTE = "USDT"
CACHE = os.path.join(v29.APP_DIR, "data_courtiers")


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mask(value: Any) -> str:
    """Un numéro de compte montré par ses trois derniers chiffres seulement."""
    s = str(value or "")
    return "•••" + s[-3:] if len(s) > 3 else "•••"


# ══════════════════════════════════════════════════════════════════════
# ANNUAIRE (information seulement)
# ══════════════════════════════════════════════════════════════════════

MT5_KIND, STOCKS_KIND, CRYPTO_KIND, BRVM_KIND = (
    "Forex et CFD (MetaTrader 5)", "Actions américaines", "Cryptos", "BRVM (Abidjan)")
KINDS = (MT5_KIND, STOCKS_KIND, CRYPTO_KIND, BRVM_KIND)
SECONDARY = ("accepte en général les résidents, d'après des sources secondaires : à vérifier sur son "
             "site avant d'ouvrir un compte")
VERIFY = "à vérifier sur son site avant d'ouvrir un compte"
MT5_MARKETS = "devises, or et argent, pétrole et gaz, indices boursiers, actions (CFD)"
MT5_FEES = "écart achat-vente et commission selon le type de compte (site du courtier)"
MT5_LINK = "MetaTrader 5 : le terminal Windows de ce PC, compte démo seulement"
# Courtiers forex et CFD qui proposent MetaTrader 5 et un compte démo gratuit :
# (nom, site, Côte d'Ivoire, remarque). Leur accueil et leurs frais changent :
# rien n'est affirmé sans source.
MT5_BROKERS: Tuple[Tuple[str, str, str, str], ...] = (
    ("MetaQuotes (serveur MetaQuotes-Demo)", "https://www.metatrader5.com",
     "compte démo ouvert depuis le terminal lui-même",
     "l'éditeur de MetaTrader 5 : son serveur de démonstration suffit pour un premier essai, sans courtier"),
    ("Exness", "https://www.exness.com", SECONDARY, ""),
    ("XM", "https://www.xm.com", SECONDARY, ""),
    ("HFM", "https://www.hfm.com", SECONDARY, "anciennement HotForex"),
    ("FBS", "https://fbs.com", SECONDARY, ""),
    ("Deriv", "https://deriv.com", VERIFY, ""),
    ("IC Markets", "https://www.icmarkets.com", VERIFY, ""),
    ("Pepperstone", "https://pepperstone.com", VERIFY, ""),
    ("Admirals", "https://admirals.com", VERIFY, "anciennement Admiral Markets"),
    ("RoboForex", "https://roboforex.com", VERIFY, ""),
    ("FxPro", "https://www.fxpro.com", VERIFY, ""),
    ("Tickmill", "https://www.tickmill.com", VERIFY, ""),
    ("AvaTrade", "https://www.avatrade.com", VERIFY, ""),
    ("FP Markets", "https://www.fpmarkets.com", VERIFY, ""),
    ("Vantage", "https://www.vantagemarkets.com", VERIFY, ""),
)
ALPACA_ENTRY: Dict[str, Any] = {
    "name": "Alpaca", "kind": STOCKS_KIND, "markets": "actions et fonds cotés américains (et cryptos)",
    "demo": "compte papier gratuit (argent fictif), ouvert avec une adresse e-mail",
    "connection": "API du compte papier (paper-api.alpaca.markets), cours IEX gratuits",
    "availability": "compte papier : ouvert à tous, d'après Alpaca ; compte réel : à vérifier",
    "fees": "sans commission sur les actions (compte réel, hors frais réglementaires)",
    "note": "le compte papier ne reçoit que les cours IEX", "site": "https://alpaca.markets"}
BRVM_ENTRY: Dict[str, Any] = {
    "name": "SGI agréées de l'UEMOA", "kind": BRVM_KIND, "markets": "actions et obligations de la BRVM",
    "demo": "non", "connection": "aucune API publique pour un programme : ordres passés auprès de la SGI",
    "availability": "oui : les SGI sont établies dans les huit pays de l'UEMOA",
    "fees": "commission de la SGI et frais de la bourse (barème de chaque SGI)",
    "note": "liste et agréments : annuaire des membres de la BRVM et AMF-UMOA (ancien CREPMF)",
    "site": "https://www.brvm.org"}


def _pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{fr(x * 100, '.2f')} %"


@functools.lru_cache(maxsize=1)
def crypto_platforms() -> Tuple[Dict[str, Any], ...]:
    """Les plateformes de la bibliothèque CCXT installée : nom, site, testnet
    (adresse de test déclarée par CCXT) et frais de base indiqués par CCXT."""
    import ccxt
    rows = []
    for pid in ccxt.exchanges:
        try:
            ex = getattr(ccxt, pid)()
        except Exception:                       # plateforme mal décrite : ignorée
            continue
        www = ex.urls.get("www")
        www = www[0] if isinstance(www, (list, tuple)) and www else www
        fees = (ex.fees or {}).get("trading") or {}
        rows.append({"id": pid, "name": str(ex.name), "site": www if isinstance(www, str) else "",
                     "testnet": bool(ex.urls.get("test")), "spot": bool(ex.has.get("spot")),
                     "taker": _num(fees.get("taker")), "maker": _num(fees.get("maker"))})
    return tuple(rows)


def directory(prices: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """L'annuaire complet : courtiers MetaTrader 5, Alpaca, plateformes crypto
    (CCXT), SGI de la BRVM. `prices` : la dernière comparaison des cours, qui
    dit quelles plateformes ont répondu depuis ce PC."""
    reached = {p.get("id") for p in ((prices or {}).get("platforms") or []) if p.get("ok")}
    rows: List[Dict[str, Any]] = [
        {"name": n, "kind": MT5_KIND, "markets": MT5_MARKETS, "demo": "oui, gratuit", "connection": MT5_LINK,
         "availability": ci, "fees": MT5_FEES, "note": note, "site": site, "branchable": True}
        for n, site, ci, note in MT5_BROKERS]
    rows.append(dict(ALPACA_ENTRY, branchable=True))
    for p in crypto_platforms():
        pid = p["id"]
        if pid == "binance":
            link = "plateforme du bot : paper, puis testnet et réel derrière leurs verrous"
        elif pid in TESTNETS:
            link = "CCXT : cours publics ; testnet branchable"
        elif p["testnet"]:
            link = "CCXT : cours publics ; testnet pas encore branchable"
        else:
            link = "CCXT : cours publics seulement"
        notes = [s for s, yes in (("cours comparés à Binance", pid in COMPARE_IDS),
                                  ("a répondu depuis ce PC", pid in reached)) if yes]
        fees = (f"frais de base selon CCXT : {_pct(p['taker'])} au marché, {_pct(p['maker'])} en ordre limite"
                if p["taker"] is not None else "selon la plateforme")
        rows.append({"name": p["name"], "kind": CRYPTO_KIND, "id": pid,
                     "markets": "cryptos au comptant" if p["spot"] else "cryptos (dérivés seulement)",
                     "demo": "testnet" if p["testnet"] else "non", "connection": link, "availability": VERIFY,
                     "fees": fees, "note": " ; ".join(notes), "site": p["site"], "testnet": p["testnet"],
                     "branchable": pid in TESTNETS or pid == "binance", "compared": pid in COMPARE_IDS,
                     "reached": pid in reached})
    rows.append(dict(BRVM_ENTRY, branchable=False))
    return rows


# ══════════════════════════════════════════════════════════════════════
# COURS COMPARÉS (cours publics, sans compte ni clé)
# ══════════════════════════════════════════════════════════════════════

COMPARE: Tuple[Tuple[str, str], ...] = (
    ("okx", "OKX"), ("bybit", "Bybit"), ("kucoin", "KuCoin"), ("bitget", "Bitget"), ("gate", "Gate"),
    ("mexc", "MEXC"), ("htx", "HTX"), ("kraken", "Kraken"), ("coinbaseexchange", "Coinbase"),
    ("cryptocom", "Crypto.com"))
COMPARE_IDS = frozenset(p for p, _n in COMPARE)
# Seuls les marchés au comptant sont chargés : bien moins de données à lire.
SPOT_ONLY: Dict[str, Any] = {
    "binance": {"types": ["spot"]}, "okx": {"types": ["spot"]}, "bybit": {"types": ["spot"]},
    "kucoin": {"types": ["spot"]}, "bitget": {"types": ["spot"]}, "gate": {"types": ["spot"]},
    "mexc": {"types": {"spot": True, "swap": {"linear": False, "inverse": False}}},
    "htx": {"types": {"spot": True, "linear": False, "inverse": False}}}

Factory = Callable[..., Any]


def exchange(pid: str, config: Optional[Dict[str, Any]] = None) -> Any:
    """Une plateforme de la bibliothèque CCXT, marchés au comptant seulement."""
    import ccxt
    cfg: Dict[str, Any] = {"enableRateLimit": True, "timeout": 20_000, **(config or {})}
    if pid in SPOT_ONLY:
        cfg["options"] = {**(cfg.get("options") or {}), "fetchMarkets": SPOT_ONLY[pid]}
    return getattr(ccxt, pid)(cfg)


def guard(ex: Any, allowed: Iterable[str], header: Optional[Tuple[str, str]] = None) -> Any:
    """Chaque requête de cette plateforme est vérifiée avant de partir : une
    adresse hors de `allowed` est refusée. `header` : en-tête exigé à chaque
    requête (compte de démonstration d'OKX)."""
    hosts = frozenset(h.lower() for h in allowed)
    inner = ex.fetch

    def fetch(url: str, method: str = "GET", headers: Any = None, body: Any = None) -> Any:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if host not in hosts:
            raise PermissionError(f"adresse hors de la liste permise : {host or url[:60]}")
        if header and (getattr(ex, "headers", None) or {}).get(header[0]) != header[1]:
            raise PermissionError("en-tête de démonstration absent : requête refusée")
        return inner(url, method, headers, body)
    ex.fetch = fetch
    return ex


def load_markets(ex: Any, now: datetime, cache: str = CACHE) -> None:
    """Liste des marchés d'une plateforme, gardée une semaine sur le disque :
    elle pèse de 0,3 à 6 Mo selon la plateforme, alors que les cours du bot,
    relus à chaque fois, n'en font qu'une fraction. Gardée sans la réponse
    brute de chaque marché ni les monnaies : les cours lus sont les mêmes
    (vérifié sur les onze plateformes), pour quatre fois moins de place."""
    import ccxt
    path = os.path.join(cache, f"marches_{ex.id}.json") if cache else ""
    if path:
        saved = autonomy.read_json(path)
        try:
            if (saved.get("ccxt") == ccxt.__version__
                    and now.timestamp() - float(saved.get("saved", 0)) < MARKETS_DAYS * 86_400):
                ex.set_markets(saved["markets"])
                return
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    ex.load_markets()
    if path:
        lean = {s: {k: v for k, v in m.items() if k != "info"} for s, m in (ex.markets or {}).items()}
        try:
            os.makedirs(cache, exist_ok=True)
            autonomy.write_json(path, {"ccxt": ccxt.__version__, "saved": now.timestamp(), "markets": lean})
        except (OSError, TypeError, ValueError):
            pass


def _mid(t: Dict[str, Any]) -> Optional[float]:
    """Milieu de la fourchette achat-vente, ou dernier cours si la fourchette
    est large ou absente."""
    bid, ask, last = _num(t.get("bid")), _num(t.get("ask")), _num(t.get("last") or t.get("close"))
    if bid and ask and 0 < bid <= ask and (ask - bid) / ask <= WIDE_SPREAD:
        return (bid + ask) / 2
    return last if last and last > 0 else None


def quotes(ex: Any, assets: Sequence[str], now: datetime, cache: str = CACHE) -> Dict[str, Dict[str, Any]]:
    """Cours de chaque crypto en USDT sur cette plateforme (marché au comptant
    ouvert) : prix et volume de 24 h en USDT."""
    import ccxt
    load_markets(ex, now, cache)
    wanted = []
    for a in assets:
        m = (ex.markets or {}).get(f"{a.upper()}/{QUOTE}")
        if m and m.get("spot", True) and m.get("active") is not False:
            wanted.append(f"{a.upper()}/{QUOTE}")
    if not wanted:
        return {}
    if ex.has.get("fetchTickers"):
        try:
            tickers = ex.fetch_tickers(wanted)
        except (ccxt.BadRequest, ccxt.ArgumentsRequired, ccxt.NotSupported):
            tickers = ex.fetch_tickers()            # liste refusée : toute la plateforme, triée ici
    else:
        tickers = {s: ex.fetch_ticker(s) for s in wanted}
    out: Dict[str, Dict[str, Any]] = {}
    for s in wanted:
        t = tickers.get(s) or {}
        price = _mid(t)
        if not price:
            continue
        vol = _num(t.get("quoteVolume"))
        base = _num(t.get("baseVolume"))
        out[s.split("/")[0].lower()] = {"price": price, "volume": vol if vol is not None else
                                        (base * price if base is not None else None)}
    return out


def _short(e: BaseException) -> str:
    """Message court d'une plateforme illisible (sans clé ni secret)."""
    name, text = type(e).__name__, str(e).replace("\n", " ")
    if isinstance(e, PermissionError):
        return text[:160]
    if name in ("RequestTimeout", "TimeoutError") or "timed out" in text:
        return "délai dépassé"
    if name in ("NetworkError", "ExchangeNotAvailable", "DDoSProtection", "URLError", "ConnectionError",
                "OnMaintenance"):
        return "plateforme injoignable"
    if name in ("AuthenticationError", "PermissionDenied", "AccountSuspended"):
        return "accès refusé"
    return f"{name} : {text[:140]}"


def compare(universe: Sequence[str], factory: Factory = exchange, now: Optional[datetime] = None,
            cache: str = CACHE, platforms: Sequence[Tuple[str, str]] = COMPARE) -> Dict[str, Any]:
    """Les cryptos du bot sur chaque plateforme, lues en même temps, face à
    Binance : écart de Binance à la médiane des autres, écart extrême, part
    de Binance dans le volume déclaré. Un cours à plus de 25 % de Binance est
    celui d'un autre jeton sous le même symbole ; un marché trop mince (moins
    de 10 000 USDT en 24 h) ne compte pas dans la médiane."""
    from . import cyber
    now = now or datetime.now(timezone.utc)
    assets = [a.lower() for a in universe]
    ids = ["binance"] + [p for p, _n in platforms]
    names = {"binance": "Binance", **dict(platforms)}
    allowed = set(cyber.ALLOWLIST)

    def read(pid: str) -> Tuple[str, Dict[str, Dict[str, Any]], str, float]:
        t0 = time.monotonic()
        try:
            return pid, quotes(guard(factory(pid), allowed), assets, now, cache), "", time.monotonic() - t0
        except Exception as e:
            return pid, {}, _short(e), time.monotonic() - t0
    with cf.ThreadPoolExecutor(max_workers=len(ids)) as pool:
        got = {pid: (q, err, secs) for pid, q, err, secs in pool.map(read, ids)}
    ref = got["binance"][0]
    rows = []
    for a in assets:
        b = ref.get(a)
        seen: Dict[str, Dict[str, Any]] = {}
        other_token = []
        for pid, _n in platforms:
            o = got[pid][0].get(a)
            if not o:
                continue
            gap = (o["price"] / b["price"] - 1) * 100 if b else None
            if gap is not None and abs(gap) > SAME_TOKEN_PCT:
                other_token.append(pid)
                continue
            seen[pid] = {"price": o["price"], "gap_pct": gap, "volume": o["volume"],
                         "thin": o["volume"] is not None and o["volume"] < MIN_VOLUME}
        usable = {pid: q for pid, q in seen.items() if not q["thin"]}
        med = statistics.median(q["price"] for q in usable.values()) if usable else None
        row: Dict[str, Any] = {
            "asset": a, "binance": b["price"] if b else None, "binance_volume": b["volume"] if b else None,
            "median": med, "count": len(usable), "gap_pct": (b["price"] / med - 1) * 100 if b and med else None,
            "quotes": seen, "other_token": other_token}
        if b and usable:
            far = max(usable, key=lambda k: abs(usable[k]["gap_pct"] or 0.0))
            row["widest"] = {"id": far, "name": names[far], "gap_pct": usable[far]["gap_pct"]}
        vols = [q["volume"] for q in seen.values() if q["volume"]]
        if b and b["volume"] and vols:
            row["binance_share"] = b["volume"] / (b["volume"] + sum(vols)) * 100
        rows.append(row)
    gaps = [abs(r["gap_pct"]) for r in rows if r["gap_pct"] is not None]
    ok_others = [pid for pid, _n in platforms if not got[pid][1]]
    error = ("Binance illisible : " + got["binance"][1] if got["binance"][1]
             else "aucune autre plateforme lisible" if not ok_others else "")
    return {"fetched_at": now.isoformat(timespec="seconds"), "error": error,
            "platforms": [{"id": pid, "name": names[pid], "ok": not got[pid][1], "error": got[pid][1],
                           "listed": len(got[pid][0]), "seconds": round(got[pid][2], 1)} for pid in ids],
            "rows": rows, "median_gap_pct": statistics.median(gaps) if gaps else None,
            "notable": [r["asset"] for r in rows if r["gap_pct"] is not None and abs(r["gap_pct"]) >= NOTABLE_PCT]}


def due(part: Any, now: datetime, hours: float) -> bool:
    """Lecture due : jamais faite, `hours` après la dernière, une heure après
    une lecture ratée."""
    if not isinstance(part, dict) or not part.get("tried_at"):
        return True
    try:
        when = datetime.fromisoformat(str(part["tried_at"]))
    except ValueError:
        return True
    return (now - when).total_seconds() >= 3600 * (RETRY_HOURS if part.get("error") else hours)


# ══════════════════════════════════════════════════════════════════════
# COMPTES DE DÉMONSTRATION : MetaTrader 5, Alpaca, testnets (jamais le réel)
# ══════════════════════════════════════════════════════════════════════

MT5_ENV = ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")
ALPACA_ENV = ("ALPACA_API_KEY", "ALPACA_API_SECRET")
MT5_DEMO = 0                    # ACCOUNT_TRADE_MODE_DEMO ; 1 (concours) et 2 (réel) sont refusés
# Symbole MetaTrader 5 de chaque marché du portefeuille fictif hors cryptos :
# noms courants chez les courtiers, par ordre de préférence. Les suffixes d'un
# courtier (« XAUUSDm », « US500.cash »…) sont reconnus.
MT5_SYMBOLS: Dict[str, Tuple[str, ...]] = {
    "or": ("XAUUSD", "GOLD"), "argent": ("XAGUSD", "SILVER"),
    "wti": ("USOIL", "XTIUSD", "WTI", "USOUSD"), "brent": ("UKOIL", "XBRUSD", "BRENT", "UKOUSD"),
    "gaz": ("XNGUSD", "NATGAS", "NGAS"), "cuivre": ("XCUUSD", "COPPER"),
    "eurusd": ("EURUSD",), "gbpusd": ("GBPUSD",), "usdjpy": ("USDJPY",), "usdchf": ("USDCHF",),
    "audusd": ("AUDUSD",), "usdcad": ("USDCAD",),
    "sp500": ("US500", "SPX500", "SP500", "USA500"), "nasdaq100": ("USTEC", "NAS100", "US100", "NDX100"),
    "dow": ("US30", "DJ30", "WS30", "USA30"), "cac40": ("FRA40", "FR40", "F40"),
    "dax": ("DE40", "GER40", "DAX40", "GER30", "DE30"), "nikkei": ("JP225", "JPN225", "J225"),
    "ftse": ("UK100", "FTSE100")}
# Alpaca : les obligations par leur propre fonds coté, les autres marchés par
# le fonds coté américain qui les suit (pas le marché lui-même : son rendement
# peut s'en écarter, surtout pour le pétrole et le gaz).
ALPACA_SYMBOLS: Dict[str, str] = {
    "tlt": "TLT", "ief": "IEF", "sp500": "SPY", "nasdaq100": "QQQ", "dow": "DIA", "or": "GLD",
    "argent": "SLV", "wti": "USO", "brent": "BNO", "gaz": "UNG", "cuivre": "CPER", "eurusd": "FXE",
    "gbpusd": "FXB", "audusd": "FXA"}
ALPACA_DIRECT = ("tlt", "ief")
ALPACA_PAPER = "https://paper-api.alpaca.markets"     # le seul compte permis : le papier (argent fictif)
ALPACA_DATA = "https://data.alpaca.markets"           # cours IEX, gratuits avec le compte papier
# Testnets branchables : plateforme CCXT → (nom, adresses de son testnet, les
# seules permises une fois branché). OKX : même adresse que son réel, son
# en-tête de démonstration est donc exigé à chaque requête. Binance : son
# testnet est celui du bot lui-même (python trendguard_bot.py set-keys).
TESTNETS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "bybit": ("Bybit", ("api-testnet.bybit.com",)),
    "okx": ("OKX (démonstration)", ("www.okx.com",)),
    "gate": ("Gate", ("api-testnet.gateapi.io",)),
    "coinbaseexchange": ("Coinbase Exchange", ("api-public.sandbox.exchange.coinbase.com",)),
    "gemini": ("Gemini", ("api.sandbox.gemini.com",)),
    "cryptocom": ("Crypto.com", ("uat-api.3ona.co",))}
DEMO_HEADER: Dict[str, Tuple[str, str]] = {"okx": ("x-simulated-trading", "1")}


def mt5_running() -> bool:
    """Le terminal MetaTrader 5 est-il ouvert ? La lecture de chaque heure ne
    l'ouvre jamais elle-même."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/NH"], capture_output=True,
                             text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "terminal64.exe" in (out or "").lower()


def _core(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name).upper())


def find_symbol(candidates: Sequence[str], names: Iterable[str]) -> Optional[str]:
    """Le symbole du courtier pour un marché : le nom exact d'abord, sinon le
    même nom suivi d'un suffixe court du courtier (« XAUUSDm »)."""
    cores = [(n, _core(n)) for n in names]
    for c in candidates:
        cc = _core(c)
        exact = [n for n, k in cores if k == cc]
        if exact:
            return min(exact, key=len)
        near = [n for n, k in cores if len(cc) >= 4 and k.startswith(cc) and 0 < len(k) - len(cc) <= 4
                and not k[len(cc)].isdigit()]
        if near:
            return min(near, key=len)
    return None


def _mt5_error(err: Any) -> str:
    code, msg = (list(err) + [None, None])[:2] if isinstance(err, (tuple, list)) else (None, err)
    if code == -10003:
        return ("terminal MetaTrader 5 introuvable : installez-le (site de MetaTrader 5 ou de votre courtier), "
                "puis ouvrez-y votre compte démo")
    if code == -6:
        return "connexion refusée : numéro de compte, mot de passe ou serveur incorrects"
    if code in (-10004, -10005):
        return "le terminal MetaTrader 5 ne répond pas (est-il ouvert et connecté ?)"
    return f"MetaTrader 5 : {msg} ({code})"


def _spec(module: Any, symbol: str) -> Optional[Dict[str, Any]]:
    """Ce qu'il faut pour dimensionner une position : cours, valeur d'un lot
    dans la monnaie du compte (le gain d'une hausse de 1 %, calculé par le
    terminal lui-même), lot minimum et pas."""
    try:
        module.symbol_select(symbol, True)
        info, tick = module.symbol_info(symbol), module.symbol_info_tick(symbol)
        price = _num(getattr(tick, "ask", None)) if tick is not None else None
        if info is None or not price:
            return None
        gain = _num(module.order_calc_profit(getattr(module, "ORDER_TYPE_BUY", 0), symbol, 1.0, price,
                                             price * 1.01))
        return {"price": price, "lot_value": gain / 0.01 if gain else None, "min": _num(info.volume_min),
                "step": _num(info.volume_step), "max": _num(info.volume_max),
                "tradable": info.trade_mode == getattr(module, "SYMBOL_TRADE_MODE_FULL", 4)}
    except Exception:
        return None


def mt5_status(env: Dict[str, str], module: Any = None, running: Optional[Callable[[], bool]] = None,
               launch: bool = False) -> Dict[str, Any]:
    """Le compte démo MetaTrader 5, en lecture seule : compte, positions,
    symboles des marchés du portefeuille fictif. Un compte réel ou de
    concours est refusé dès qu'il est reconnu. `launch` : ouvrir le terminal
    s'il est fermé (commande lancée par vous seulement)."""
    now = _now_iso()
    login, password, server = (str(env.get(k) or "").strip() for k in MT5_ENV)
    if not (login and password and server):
        return {"state": "absent", "checked_at": now,
                "message": "aucun compte démo : python trendguard_bot.py courtiers set-key mt5"}
    if not login.isdigit():
        return {"state": "erreur", "checked_at": now, "message": "MT5_LOGIN doit être le numéro du compte démo"}
    if module is None:
        if os.name != "nt":
            return {"state": "indisponible", "checked_at": now,
                    "message": "MetaTrader 5 ne tourne que sous Windows"}
        try:
            import MetaTrader5 as module
        except ImportError:
            return {"state": "indisponible", "checked_at": now,
                    "message": "paquet MetaTrader5 absent : .venv\\Scripts\\python.exe -m pip install MetaTrader5"}
    if not launch and not (running or mt5_running)():
        return {"state": "ferme", "checked_at": now,
                "message": "terminal MetaTrader 5 fermé : ouvrez-le pour que le bot lise le compte démo"}
    kw: Dict[str, Any] = {"login": int(login), "password": password, "server": server, "timeout": 60_000}
    if env.get("MT5_PATH"):
        kw["path"] = env["MT5_PATH"]
    try:
        if not module.initialize(**kw):
            return {"state": "erreur", "checked_at": now, "message": _mt5_error(module.last_error())}
        info = module.account_info()
        if info is None:
            return {"state": "erreur", "checked_at": now, "message": _mt5_error(module.last_error())}
        if info.trade_mode != getattr(module, "ACCOUNT_TRADE_MODE_DEMO", MT5_DEMO):
            kind = "de concours" if info.trade_mode == getattr(module, "ACCOUNT_TRADE_MODE_CONTEST", 1) else "réel"
            return {"state": "refuse", "checked_at": now,
                    "message": f"compte {kind} : refusé. Le bot ne se branche qu'à un compte démo ; "
                               "rien d'autre n'a été lu"}
        account = {"login": _mask(login), "server": str(info.server), "company": str(info.company),
                   "currency": str(info.currency), "balance": _num(info.balance), "equity": _num(info.equity),
                   "leverage": int(info.leverage or 0), "trade_allowed": bool(info.trade_allowed)}
        buy = getattr(module, "POSITION_TYPE_BUY", 0)
        positions = [{"symbol": str(p.symbol), "side": "achat" if p.type == buy else "vente",
                      "volume": _num(p.volume), "price_open": _num(p.price_open), "profit": _num(p.profit)}
                     for p in (module.positions_get() or ())]
        names = [str(s.name) for s in (module.symbols_get() or ())]
        symbols = {code: find_symbol(cands, names) for code, cands in MT5_SYMBOLS.items()}
        specs = {code: _spec(module, s) for code, s in symbols.items() if s}
        return {"state": "demo", "checked_at": now, "message": "compte démo branché (lecture seule)",
                "account": account, "positions": positions, "symbols": symbols, "specs": specs}
    except Exception as e:
        return {"state": "erreur", "checked_at": now, "message": f"MetaTrader 5 : {type(e).__name__} : {str(e)[:140]}"}
    finally:
        try:
            module.shutdown()
        except Exception:           # terminal déjà déconnecté : rien à fermer
            pass


Get = Callable[[str, float, Dict[str, str]], bytes]


def alpaca_get(url: str, key: str, secret: str, get: Optional[Get] = None) -> Any:
    """Une lecture du compte papier d'Alpaca. Toute autre adresse, dont celle
    du compte réel (api.alpaca.markets), est refusée."""
    if not (url.startswith(ALPACA_PAPER + "/") or url.startswith(ALPACA_DATA + "/")):
        raise PermissionError("Alpaca : seul le compte papier est permis")
    return json.loads((get or mw.http_get)(url, 30.0, {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}))


def alpaca_status(env: Dict[str, str], get: Optional[Get] = None) -> Dict[str, Any]:
    """Le compte papier d'Alpaca, en lecture seule : compte, positions, séance
    de Wall Street et cours des fonds du plan miroir."""
    now = _now_iso()
    key, secret = (str(env.get(k) or "").strip() for k in ALPACA_ENV)
    if not (key and secret):
        return {"state": "absent", "checked_at": now,
                "message": "aucun compte papier : python trendguard_bot.py courtiers set-key alpaca"}
    try:
        acct = alpaca_get(ALPACA_PAPER + "/v2/account", key, secret, get)
        held = alpaca_get(ALPACA_PAPER + "/v2/positions", key, secret, get)
        clock = alpaca_get(ALPACA_PAPER + "/v2/clock", key, secret, get)
    except mw.HttpError as e:
        return {"state": "erreur", "checked_at": now,
                "message": "clés refusées par Alpaca : prenez celles du compte papier (Paper Trading)"
                if e.status in (401, 403) else f"Alpaca : erreur {e.status}"}
    except Exception as e:
        return {"state": "erreur", "checked_at": now, "message": f"Alpaca : {mw.friendly_error(e)}"}
    prices: Dict[str, float] = {}
    try:                            # cours des fonds du plan miroir (IEX) : facultatifs
        tickers = ",".join(sorted(set(ALPACA_SYMBOLS.values())))
        snap = alpaca_get(f"{ALPACA_DATA}/v2/stocks/snapshots?symbols={tickers}&feed=iex", key, secret, get)
        for sym, s in (snap or {}).items():
            s = s or {}
            price = _num((s.get("latestTrade") or {}).get("p")) or _num((s.get("dailyBar") or {}).get("c"))
            if price:
                prices[str(sym)] = price
    except Exception:               # cours facultatifs : le plan miroir dira « cours indisponible »
        pass
    acct = acct if isinstance(acct, dict) else {}
    account = {"number": _mask(acct.get("account_number")), "status": str(acct.get("status") or ""),
               "currency": str(acct.get("currency") or "USD"), "cash": _num(acct.get("cash")),
               "equity": _num(acct.get("equity")), "buying_power": _num(acct.get("buying_power")),
               "blocked": bool(acct.get("trading_blocked") or acct.get("account_blocked"))}
    positions = [{"symbol": str(p.get("symbol")), "qty": _num(p.get("qty")),
                  "price_open": _num(p.get("avg_entry_price")), "value": _num(p.get("market_value")),
                  "pnl_pct": (_num(p.get("unrealized_plpc")) or 0.0) * 100}
                 for p in (held if isinstance(held, list) else []) if isinstance(p, dict)]
    return {"state": "demo", "checked_at": now, "message": "compte papier branché (lecture seule)",
            "account": account, "positions": positions,
            "market_open": bool((clock or {}).get("is_open")) if isinstance(clock, dict) else False,
            "prices": prices}


def testnet_keys(env: Dict[str, str], pid: str) -> Tuple[str, str, str]:
    """Clé, secret et phrase secrète du testnet `pid` (CCXT_<PLATEFORME>_KEY…)."""
    p = f"CCXT_{pid.upper()}_"
    key, secret, password = (str(env.get(p + k) or "").strip() for k in ("KEY", "SECRET", "PASSWORD"))
    return key, secret, password


@functools.lru_cache(maxsize=None)
def needs_password(pid: str) -> bool:
    """La plateforme demande-t-elle une phrase secrète en plus de la clé ?"""
    import ccxt
    return bool(getattr(ccxt, pid)().requiredCredentials.get("password"))


def testnet_exchange(pid: str, key: str, secret: str, password: str = "", factory: Factory = exchange) -> Any:
    """Une plateforme CCXT branchée à son testnet, et à lui seul : mode test
    obligatoire, adresses du testnet seules permises."""
    if pid not in TESTNETS:
        raise PermissionError(f"{pid} : pas de testnet branchable")
    ex = factory(pid, {"apiKey": key, "secret": secret, **({"password": password} if password else {})})
    if not (getattr(ex, "urls", None) or {}).get("test"):
        raise PermissionError(f"{pid} : pas de testnet")
    ex.set_sandbox_mode(True)
    return guard(ex, TESTNETS[pid][1], DEMO_HEADER.get(pid))


def testnet_status(env: Dict[str, str], pid: str, factory: Factory = exchange) -> Optional[Dict[str, Any]]:
    """Le testnet `pid`, en lecture seule (soldes) ; None sans clé."""
    key, secret, password = testnet_keys(env, pid)
    if not (key and secret):
        return None
    base = {"id": pid, "name": TESTNETS[pid][0], "checked_at": _now_iso()}
    try:
        bal = testnet_exchange(pid, key, secret, password, factory).fetch_balance()
    except PermissionError as e:
        return dict(base, state="refuse", message=str(e))
    except Exception as e:
        auth = type(e).__name__ in ("AuthenticationError", "PermissionDenied")
        return dict(base, state="erreur",
                    message="clés refusées : prenez celles du testnet, pas du compte réel" if auth else _short(e))
    totals = {str(c): float(v) for c, v in ((bal or {}).get("total") or {}).items() if _num(v)}
    return dict(base, state="demo", message="testnet branché (lecture seule)",
                balances=dict(sorted(totals.items(), key=lambda kv: -kv[1])[:8]))


def check_accounts(env: Dict[str, str], now: datetime, factory: Factory = exchange, mt5_module: Any = None,
                   get: Optional[Get] = None, running: Optional[Callable[[], bool]] = None,
                   launch: bool = False) -> Dict[str, Any]:
    """Tous les comptes de démonstration dont les identifiants sont dans .env."""
    return {"tried_at": now.isoformat(timespec="seconds"), "error": "",
            "mt5": mt5_status(env, mt5_module, running, launch), "alpaca": alpaca_status(env, get),
            "testnets": [s for s in (testnet_status(env, pid, factory) for pid in TESTNETS) if s]}


def refresh(memory: Any, now: datetime, universe: Sequence[str], force_prices: bool = False,
            force_accounts: bool = False, env: Optional[Dict[str, str]] = None, factory: Factory = exchange,
            mt5_module: Any = None, get: Optional[Get] = None, running: Optional[Callable[[], bool]] = None,
            launch: bool = False, cache: str = CACHE, parts: Sequence[str] = ("prices", "accounts")
            ) -> Dict[str, Any]:
    """Cours comparés toutes les six heures, comptes de démonstration chaque
    heure, gardés dans le noyau de savoir (clé « courtiers »). Une panne est
    notée, jamais bloquante : les derniers cours lus restent."""
    book = memory.get(KEY)
    book = dict(book) if isinstance(book, dict) else {}
    prices = dict(book["prices"]) if isinstance(book.get("prices"), dict) else {}
    if "prices" in parts and (force_prices or due(prices, now, PRICES_EVERY_HOURS)):
        prices["tried_at"] = now.isoformat(timespec="seconds")
        try:
            got = compare(universe, factory, now, cache)
            if got["error"]:
                prices.update(error=got["error"], platforms=got["platforms"])
            else:
                prices.update(got)
        except Exception as e:
            prices["error"] = _short(e)
        book["prices"] = prices
    old = book["accounts"] if isinstance(book.get("accounts"), dict) else {}
    if "accounts" in parts and (force_accounts or due(old, now, ACCOUNTS_EVERY_HOURS)):
        new = check_accounts(dict(os.environ) if env is None else env, now, factory, mt5_module, get, running,
                             launch)
        prev = old.get("mt5") if isinstance(old.get("mt5"), dict) else {}
        if new["mt5"]["state"] == "ferme" and prev.get("account"):        # terminal fermé : le dernier état reste
            new["mt5"].update({k: prev[k] for k in ("account", "positions", "symbols", "specs") if k in prev},
                              seen_at=prev.get("seen_at") or prev.get("checked_at"))
        elif new["mt5"]["state"] == "demo":
            new["mt5"]["seen_at"] = new["mt5"]["checked_at"]
        book["accounts"] = new
    memory.put(KEY, book)
    return book


# ══════════════════════════════════════════════════════════════════════
# PLAN MIROIR (à blanc : rien n'est envoyé)
# ══════════════════════════════════════════════════════════════════════

def lots(value: float, spec: Optional[Dict[str, Any]]) -> Tuple[Optional[float], str]:
    """Lots MetaTrader 5 d'une position de `value` (monnaie du compte),
    arrondis au pas du courtier, jamais au-dessus."""
    if not spec or not spec.get("lot_value") or spec["lot_value"] <= 0:
        return None, "valeur d'un lot inconnue"
    step = spec.get("step") or 0.01
    vol = math.floor(value / spec["lot_value"] / step + 1e-9) * step
    low = spec.get("min") or step
    if vol < low - 1e-12:
        return 0.0, f"trop petit pour ce compte (minimum {fr(low, '.2f')} lot)"
    if spec.get("max"):
        vol = min(vol, spec["max"])
    return round(vol, 8), ""


def mirror(multi_book: Any, accounts: Any) -> Dict[str, Any]:
    """Plan miroir, à blanc : les positions du portefeuille fictif hors
    cryptos (la règle du bot sur l'or, les devises, les indices et les
    obligations), recopiées au même poids sur chaque compte de démonstration
    branché. Rien n'est envoyé."""
    book = multi_book if isinstance(multi_book, dict) else {}
    pf = book.get("portfolio") if isinstance(book.get("portfolio"), dict) else {}
    acc = accounts if isinstance(accounts, dict) else {}
    mt5 = acc.get("mt5") if isinstance(acc.get("mt5"), dict) else {}
    alp = acc.get("alpaca") if isinstance(acc.get("alpaca"), dict) else {}
    mt5_eq = _num((mt5.get("account") or {}).get("equity")) if mt5.get("state") in ("demo", "ferme") else None
    alp_eq = _num((alp.get("account") or {}).get("equity")) if alp.get("state") == "demo" else None
    rows = []
    for p in pf.get("positions") or []:
        if not isinstance(p, dict):
            continue
        code, w = str(p.get("code")), _num(p.get("weight_pct"))
        sym = (mt5.get("symbols") or {}).get(code) if mt5_eq is not None else None
        m: Dict[str, Any] = {"symbol": sym, "lots": None, "note": ""}
        if code not in MT5_SYMBOLS:
            m["note"] = "pas de symbole MetaTrader 5 prévu"
        elif mt5_eq is None:
            m["note"] = "compte démo non branché (symboles courants : " + ", ".join(MT5_SYMBOLS[code][:2]) + ")"
        elif not sym:
            m["note"] = "introuvable chez ce courtier"
        elif w is None:
            m["note"] = "poids inconnu jusqu'à la prochaine lecture des marchés"
        else:
            m["lots"], m["note"] = lots(mt5_eq * w / 100, (mt5.get("specs") or {}).get(code))
        tk = ALPACA_SYMBOLS.get(code)
        a: Dict[str, Any] = {"symbol": tk, "qty": None,
                             "note": "fonds coté qui suit ce marché" if tk and code not in ALPACA_DIRECT else ""}
        if not tk:
            a["note"] = "pas de fonds coté prévu"
        elif alp_eq is None:
            a["note"] = "compte papier non branché"
        elif w is not None:
            price = _num((alp.get("prices") or {}).get(tk))
            if price:
                a["qty"] = math.floor(alp_eq * w / 100 / price * 1e4) / 1e4
            else:
                a["note"] = "cours indisponible"
        rows.append({"code": code, "name": p.get("name") or code, "category": p.get("category") or "",
                     "weight_pct": w, "stop": _num(p.get("stop")), "price": _num(p.get("price")),
                     "mt5": m, "alpaca": a})
    return {"rows": rows, "since": pf.get("start"), "equity": _num(pf.get("equity")),
            "fetched_at": book.get("fetched_at"), "sent": False}


# ══════════════════════════════════════════════════════════════════════
# PANNEAU ET LIGNE DE COMMANDE
# ══════════════════════════════════════════════════════════════════════

STATES = {"absent": "non branché", "attente": "pas encore lu", "demo": "démo branchée", "refuse": "REFUSÉ",
          "erreur": "erreur", "ferme": "terminal fermé", "indisponible": "indisponible"}


def _accounts_view(accounts: Any, env: Dict[str, str]) -> Dict[str, Any]:
    """Les comptes connus du noyau de savoir ; un compte jamais lu dit
    comment le brancher (sans jamais montrer une valeur de .env)."""
    acc = dict(accounts) if isinstance(accounts, dict) else {}
    for name, keys in (("mt5", MT5_ENV), ("alpaca", ALPACA_ENV)):
        if not isinstance(acc.get(name), dict):
            ready = all(env.get(k) for k in keys)
            acc[name] = {"state": "attente" if ready else "absent",
                         "message": "pas encore lu : prochaine lecture dans l'heure "
                                    "(ou python trendguard_bot.py courtiers verifier)" if ready
                         else f"non branché : python trendguard_bot.py courtiers set-key {name}"}
    acc["testnets"] = [t for t in (acc.get("testnets") or []) if isinstance(t, dict)]
    return acc


def view(book: Any, multi_book: Any = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Page « Courtiers » du panneau (lecture seule) : comptes de
    démonstration, plan miroir à blanc, cours comparés, annuaire."""
    book = book if isinstance(book, dict) else {}
    prices = book["prices"] if isinstance(book.get("prices"), dict) else {}
    accounts = _accounts_view(book.get("accounts"), dict(os.environ) if env is None else env)
    return {"directory": directory(prices), "kinds": list(KINDS), "prices": prices or None, "accounts": accounts,
            "mirror": mirror(multi_book, accounts), "compare": [{"id": p, "name": n} for p, n in COMPARE],
            "testnets": [{"id": pid, "name": n, "password": needs_password(pid)} for pid, (n, _h) in TESTNETS.items()],
            "notable_pct": NOTABLE_PCT, "every_hours": PRICES_EVERY_HOURS}


def _when(iso: Any) -> str:
    s = str(iso or "")
    return f"{s[8:10]}/{s[5:7]}/{s[:4]} à {s[11:16]} UTC" if len(s) >= 16 else "–"


def _gap(x: Optional[float]) -> str:
    return "–" if x is None else f"{'+' if x > 0 else ''}{fr(x, '.2f')} %"


def render(book: Any, multi_book: Any = None) -> str:
    """État en texte : comptes de démonstration, cours comparés, annuaire."""
    v = view(book, multi_book)
    acc = v["accounts"]
    lines = ["Courtiers et plateformes : démonstration seulement, aucun ordre envoyé", "", "Comptes de démonstration"]
    for label, key in (("MetaTrader 5", "mt5"), ("Alpaca (papier)", "alpaca")):
        s = acc[key]
        lines.append(f"  {label:<17} {STATES.get(s.get('state'), s.get('state'))} : {s.get('message', '')}")
        a = s.get("account") or {}
        if a.get("equity") is not None:
            where = " · ".join(x for x in (a.get("company"), a.get("server"), a.get("login") or a.get("number")) if x)
            lines.append(f"  {'':<17} {where} · capital {fr(a['equity'], ',.2f')} {a.get('currency', '')}")
    tn = acc.get("testnets") or []
    lines.append(f"  {'Testnets crypto':<17} " + (", ".join(f"{t['name']} : {STATES.get(t['state'], t['state'])}"
                                                            for t in tn)
                                                  if tn else "aucun (par exemple : python trendguard_bot.py "
                                                             "courtiers set-key bybit)"))
    p = v["prices"] or {}
    lines.append("")
    if not p.get("rows"):
        lines.append("Cours comparés : pas encore lus" + (f" ({p['error']})" if p.get("error") else ""))
    else:
        ok = [x for x in p.get("platforms") or [] if x["ok"] and x["id"] != "binance"]
        lines.append(f"Cours des cryptos du bot sur {len(ok)} autres plateformes, face à Binance "
                     f"(lus le {_when(p.get('fetched_at'))})")
        lines.append(f"  écart médian de Binance : {_gap(p.get('median_gap_pct'))} ; écarts notables "
                     f"({fr(NOTABLE_PCT, '.0f')} % et plus) : {', '.join(a.upper() for a in p.get('notable') or []) or 'aucun'}")
        for r in sorted(p["rows"], key=lambda r: -abs(r["gap_pct"] or 0.0))[:12]:
            share = r.get("binance_share")
            lines.append(f"  {r['asset'].upper():<8} Binance {_gap(r['gap_pct']):>9} face à la médiane de "
                         f"{r['count']:>2} plateformes"
                         + (f" · {fr(share, '.0f')} % du volume sur Binance" if share is not None else ""))
        failed = [x for x in p.get("platforms") or [] if not x["ok"]]
        if failed:
            lines.append("  illisibles : " + ", ".join(f"{x['name']} ({x['error']})" for x in failed))
        if p.get("error"):
            lines.append(f"  dernière lecture ratée : {p['error']}")
    d = v["directory"]
    n = {k: sum(1 for r in d if r["kind"] == k) for k in KINDS}
    lines += ["", f"Annuaire : {n[MT5_KIND]} courtiers MetaTrader 5, Alpaca, {n[CRYPTO_KIND]} plateformes crypto "
                  f"({sum(1 for r in d if r.get('testnet'))} avec testnet), SGI de la BRVM "
                  "(python trendguard_bot.py courtiers annuaire)"]
    return "\n".join(lines)


def render_directory() -> str:
    """L'annuaire en texte."""
    rows = directory()
    lines: List[str] = []
    for kind in KINDS:
        part = [r for r in rows if r["kind"] == kind]
        lines += ["", f"{kind} ({len(part)})"]
        for r in part:
            if kind == CRYPTO_KIND:
                lines.append(f"  {r['name']:<26} {'testnet' if r['testnet'] else '':<8} {r['connection']}")
            else:
                lines.append(f"  {r['name']:<38} {r['site']}")
                lines.append(f"  {'':<38} Côte d'Ivoire : {r['availability']}")
    return "\n".join(lines).lstrip("\n")


def _check_one(target: str, env: Dict[str, str]) -> Dict[str, Any]:
    if target == "mt5":
        return mt5_status(env, launch=True)
    if target == "alpaca":
        return alpaca_status(env)
    return testnet_status(env, target) or {"state": "absent", "message": "clés absentes"}


def _describe(target: str, status: Dict[str, Any]) -> str:
    a = status.get("account") or {}
    if target == "mt5":
        return (f"Compte démo MetaTrader 5 {a.get('login', '')} chez {a.get('company', '?')} ({a.get('server', '?')}), "
                f"capital {fr(a.get('equity') or 0.0, ',.2f')} {a.get('currency', '')}")
    if target == "alpaca":
        return f"Compte papier Alpaca {a.get('number', '')}, capital {fr(a.get('equity') or 0.0, ',.2f')} USD"
    return f"Testnet {status.get('name', target)}"


def _remember(target: str, status: Dict[str, Any], memory: Any = None) -> None:
    """Le compte vérifié est aussitôt visible dans le panneau (noyau de
    savoir) ; sinon, il le sera à la prochaine lecture de l'heure."""
    own = memory is None
    try:
        if own:
            from . import savoir
            from .config import load_guard_config_from_env
            g = load_guard_config_from_env()
            if not g.savoir:
                return
            memory = savoir.Memory(g.savoir_db or savoir.DEFAULT_DB)
        try:
            book = memory.get(KEY)
            book = dict(book) if isinstance(book, dict) else {}
            acc = dict(book["accounts"]) if isinstance(book.get("accounts"), dict) else {}
            if target in ("mt5", "alpaca"):
                acc[target] = dict(status, seen_at=status.get("checked_at")) if target == "mt5" else status
            else:
                acc["testnets"] = [t for t in acc.get("testnets") or [] if t.get("id") != target] + [status]
            book["accounts"] = acc
            memory.put(KEY, book)
        finally:
            if own:
                memory.close()
    except Exception:               # le panneau verra le compte à la prochaine lecture de l'heure
        pass


def cmd_set_key(target: str, env_path: Optional[str] = None, ask: Optional[Callable[[str], str]] = None,
                read: Optional[Callable[[str], str]] = None, out: Any = None,
                check: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None,
                memory: Any = None) -> int:
    """Saisie d'un compte de démonstration : mot de passe et clés en saisie
    masquée, compte vérifié (démo, papier ou testnet) avant d'être écrit dans
    .env. Un compte réel est refusé, et rien n'est écrit."""
    import getpass

    from . import config as tgc
    out = out or sys.stdout

    def say(msg: str = "") -> None:
        print(msg, file=out)
    ask, read = ask or getpass.getpass, read or input
    target = (target or "").strip().lower()
    if target == "mt5":
        fields = [("MT5_LOGIN", "Numéro du compte démo", False),
                  ("MT5_SERVER", "Serveur du compte démo (ex. MetaQuotes-Demo)", False),
                  ("MT5_PASSWORD", "Mot de passe du compte démo (rien ne s'affiche)", True)]
    elif target == "alpaca":
        fields = [("ALPACA_API_KEY", "API Key ID du compte papier (rien ne s'affiche)", True),
                  ("ALPACA_API_SECRET", "Secret Key du compte papier (rien ne s'affiche)", True)]
    elif target in TESTNETS:
        p = f"CCXT_{target.upper()}_"
        fields = [(p + "KEY", f"API Key du testnet {TESTNETS[target][0]} (rien ne s'affiche)", True),
                  (p + "SECRET", "Secret de la clé (rien ne s'affiche)", True)]
        if needs_password(target):
            fields.append((p + "PASSWORD", "Phrase secrète de la clé (passphrase, rien ne s'affiche)", True))
    else:
        say(f"Compte inconnu : {target or '(aucun)'}. Choix : mt5, alpaca, {', '.join(TESTNETS)}.")
        say("Binance : son testnet est celui du bot lui-même (python trendguard_bot.py set-keys).")
        return 2
    say("Compte de démonstration seulement : un compte réel sera refusé.")
    values: Dict[str, str] = {}
    try:
        for name, label, secret in fields:
            v = (ask if secret else read)(f"{label} : ").strip().strip("\"'")
            if not v or re.search(r"\s", v):
                say(f"❌ {label.split(' (')[0]} : valeur vide ou avec des espaces. Rien n'a été modifié.")
                return 1
            values[name] = v
    except (EOFError, KeyboardInterrupt):
        say("\nAnnulé. Rien n'a été modifié.")
        return 1
    if any(tgc.is_exposed(v) for v in values.values()):
        say("❌ Cette valeur a été montrée dans une conversation : elle n'est plus sûre. Créez-en une neuve, puis "
            "recommencez. Rien n'a été modifié.")
        return 1
    say("Vérification du compte (lecture seule, aucun ordre)…")
    status = (check or _check_one)(target, dict(os.environ, **values))
    if status.get("state") != "demo":
        say(f"❌ {status.get('message') or 'compte refusé'}. Rien n'a été enregistré.")
        return 1
    for name, value in values.items():
        tgc.set_env_var(env_path or tgc.ENV_FILE, name, value)
    say(f"✅ {_describe(target, status)} : enregistré dans .env. Le bot le relira chaque heure "
        "(page Courtiers du panneau).")
    _remember(target, status, memory)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Ligne de commande : état (défaut), annuaire, cours (relus tout de
    suite), verifier (comptes relus tout de suite), set-key <compte>."""
    v29.ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Courtiers et plateformes de TrendGuard (démonstration seulement)")
    ap.add_argument("action", nargs="?", default="etat", choices=["etat", "annuaire", "cours", "verifier", "set-key"])
    ap.add_argument("compte", nargs="?", default="", help="(set-key) mt5, alpaca ou un testnet : " + ", ".join(TESTNETS))
    args = ap.parse_args(argv)
    if args.action == "annuaire":
        print(render_directory())
        return 0
    if args.action == "set-key":
        return cmd_set_key(args.compte)
    from . import multi_actifs, savoir
    from .config import load_guard_config_from_env
    gcfg = load_guard_config_from_env()
    memory = savoir.Memory(gcfg.savoir_db or savoir.DEFAULT_DB)
    try:
        if args.action in ("cours", "verifier"):
            book = refresh(memory, datetime.now(timezone.utc), [a.lower() for a in gcfg.universe],
                           force_prices=True, force_accounts=True, launch=args.action == "verifier",
                           parts=("prices",) if args.action == "cours" else ("accounts",))
        else:
            book = memory.get(KEY)
        print(render(book, memory.get(multi_actifs.KEY)))
    finally:
        memory.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
