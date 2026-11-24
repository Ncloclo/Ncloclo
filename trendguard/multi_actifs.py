"""
Au-delà des cryptos : les règles de TrendGuard appliquées aux autres classes
d'actifs (docs/MULTI_ACTIFS.md) : matières premières, devises, indices
boursiers, obligations, et à part les actions.

Le suivi de tendance est l'un des effets les mieux documentés sur toutes les
classes d'actifs (Moskowitz, Ooi et Pedersen 2012, 58 contrats à terme), et
ces marchés évoluent souvent indépendamment des cryptos. Ce module fournit
l'univers, l'historique journalier (Yahoo Finance, sans clé, gardé dans
data_multi/) et la règle de TrendGuard transposée, déclarée à l'avance :

  - mêmes paramètres gelés (cassure de 30 séances, momentum de 90, stop
    initial à 3 volatilités, stop suiveur à 5, 1 % de risque par position,
    8 positions, 6 % de risque cumulé, 25 % du capital par position) ;
  - régime : la propre tendance de chaque actif (clôture au-dessus de sa
    moyenne de 150 séances) au lieu de BTC, qui ne dit rien de l'or ;
  - pas de filtre de liquidité (marchés parmi les plus liquides du monde),
    frais et glissement de 0,1 % par côté comme pour les cryptos ;
  - achats seulement, sans levier ; rendements en monnaie locale (change
    supposé couvert).

Information et études : le bot ne passe aucun ordre sur ces marchés. Sur
Binance, seuls l'or tokenisé (PAXG/USDT) et l'euro (EUR/USDT) se traitent ;
le reste demande un autre courtier. Le franc CFA (XOF) a une parité fixe avec
l'euro (655,957) : il n'a pas de tendance à suivre.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import trend_strategy as ts

# (code, nom, catégorie, symbole Yahoo). Les actions sont à part : choisies
# parmi les plus grandes d'aujourd'hui, leur passé est flatté (biais du
# survivant) ; elles ne comptent dans aucun verdict.
UNIVERSE: Tuple[Tuple[str, str, str, str], ...] = (
    ("or", "Or (once, $)", "Matières premières", "GC=F"),
    ("argent", "Argent (once, $)", "Matières premières", "SI=F"),
    ("wti", "Pétrole WTI", "Matières premières", "CL=F"),
    ("brent", "Pétrole Brent", "Matières premières", "BZ=F"),
    ("gaz", "Gaz naturel", "Matières premières", "NG=F"),
    ("cuivre", "Cuivre", "Matières premières", "HG=F"),
    ("eurusd", "EUR/USD", "Devises", "EURUSD=X"),
    ("gbpusd", "GBP/USD", "Devises", "GBPUSD=X"),
    ("usdjpy", "USD/JPY", "Devises", "JPY=X"),
    ("usdchf", "USD/CHF", "Devises", "CHF=X"),
    ("audusd", "AUD/USD", "Devises", "AUDUSD=X"),
    ("usdcad", "USD/CAD", "Devises", "CAD=X"),
    ("sp500", "S&P 500", "Indices", "^GSPC"),
    ("nasdaq100", "Nasdaq 100", "Indices", "^NDX"),
    ("dow", "Dow Jones", "Indices", "^DJI"),
    ("cac40", "CAC 40", "Indices", "^FCHI"),
    ("dax", "DAX", "Indices", "^GDAXI"),
    ("nikkei", "Nikkei 225", "Indices", "^N225"),
    ("ftse", "FTSE 100", "Indices", "^FTSE"),
    ("tlt", "Obligations d'État US à plus de 20 ans (ETF TLT)", "Obligations", "TLT"),
    ("ief", "Obligations d'État US à 7-10 ans (ETF IEF)", "Obligations", "IEF"),
    ("nvda", "Nvidia", "Actions", "NVDA"),
    ("aapl", "Apple", "Actions", "AAPL"),
    ("msft", "Microsoft", "Actions", "MSFT"),
    ("amzn", "Amazon", "Actions", "AMZN"),
    ("googl", "Alphabet", "Actions", "GOOGL"),
    ("meta", "Meta", "Actions", "META"),
    ("tsla", "Tesla", "Actions", "TSLA"),
    ("tte", "TotalEnergies", "Actions", "TTE.PA"),
    ("mc", "LVMH", "Actions", "MC.PA"),
)
# Hors cryptos, mais tradables sur Binance (jetons) : la manche que l'étude
# docs/BINANCE_HORS_CRYPTOS.md propose de suivre en paper. (code, nom et
# jeton, catégorie, symbole Yahoo du sous-jacent). Écartés : fonds à levier,
# sociétés non cotées, cotations trop récentes.
BINANCE: Tuple[Tuple[str, str, str, str], ...] = (
    ("or", "Or (PAXG, XAUT)", "Matières premières", "GC=F"),
    ("eurusd", "Euro (EUR/USDT)", "Devises", "EURUSD=X"),
    ("nvda", "Nvidia (NVDAB)", "Actions", "NVDA"), ("mu", "Micron (MUB)", "Actions", "MU"),
    ("googl", "Alphabet (GOOGLB)", "Actions", "GOOGL"), ("intc", "Intel (INTCB)", "Actions", "INTC"),
    ("mstr", "Strategy (MSTRB)", "Actions", "MSTR"), ("be", "Bloom Energy (BEB)", "Actions", "BE"),
    ("qqq", "Fonds Nasdaq 100 (QQQB)", "Indices", "QQQ"),
)
BINANCE_NAME = {c: n for c, n, _k, _s in BINANCE}
NAME = {**{c: n for c, n, _k, _s in BINANCE}, **{c: n for c, n, _k, _s in UNIVERSE}}
CATEGORY = {**{c: k for c, _n, k, _s in BINANCE}, **{c: k for c, _n, k, _s in UNIVERSE}}
SYMBOL = {**{c: s for c, _n, _k, s in BINANCE}, **{c: s for c, _n, _k, s in UNIVERSE}}
CATEGORIES = ("Matières premières", "Devises", "Indices", "Obligations", "Actions")
BIASED = {"Actions"}                 # mesurées à part, jamais dans un verdict
CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?period1={p1}&period2={p2}&interval=1d")
START = "2015-01-01"                 # assez d'historique avant 2018 (moyenne 150, momentum 90)
REGIME_N = 150                       # régime : la propre tendance de chaque actif
FILL_LIMIT = 5                       # jours fériés : dernier cours gardé 5 séances au plus
# Volume quotidien supposé (dollars) : ces marchés sont parmi les plus liquides
# du monde ; une liquidité inconnue ferait refuser tout achat (entry_signal).
LIQUID = 1e12
# Coût réel par côté (frais et écart compris), typique d'un compte de
# particulier, déclaré avant les mesures. Les frais de Binance (0,2 % par côté
# avec le glissement) mangeraient 23 à 45 % du risque de chaque trade sur les
# devises, les indices et les obligations, dont le stop est proche (règle du
# poids des frais : frais aller-retour / distance du stop).
COSTS = {"Matières premières": 0.0005, "Devises": 0.0001, "Indices": 0.0002, "Obligations": 0.0002,
         "Actions": 0.0005}

Fetch = Callable[[str], bytes]


def parse_chart(raw: bytes) -> pd.Series:
    """Clôtures journalières d'une réponse Yahoo (jour de la place de
    cotation) ; vide si la réponse est illisible."""
    res = ((json.loads(raw).get("chart") or {}).get("result") or [None])[0]
    if not res:
        return pd.Series(dtype=float)
    off = int((res.get("meta") or {}).get("gmtoffset") or 0)
    closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    rows = {datetime.fromtimestamp(int(t) + off, timezone.utc).date(): float(c)
            for t, c in zip(res.get("timestamp") or [], closes) if c is not None and c > 0}
    s = pd.Series(rows, dtype=float).sort_index()
    s.index = pd.DatetimeIndex(s.index).tz_localize("UTC")
    return s


def download(cache: str, fetch: Fetch, codes: Optional[List[str]] = None,
             start: str = START, pause: float = 1.0,
             symbols: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Télécharge l'historique journalier de chaque actif dans `cache`
    (un fichier par actif : date, clôture). `symbols` : {code : symbole
    Yahoo} pour des actifs hors de l'univers. Rend {code: erreur} pour ceux
    qui ont échoué ; un échec n'arrête pas les autres."""
    os.makedirs(cache, exist_ok=True)
    p1 = int(pd.Timestamp(start, tz="UTC").timestamp())
    p2 = int(time.time())
    known = dict(SYMBOL, **(symbols or {}))
    errors = {}
    for code in codes or list(symbols or {}) or [c for c, *_ in UNIVERSE]:
        url = CHART.format(sym=urllib.parse.quote(known[code]), p1=p1, p2=p2)
        try:
            s = parse_chart(fetch(url))
            if s.empty:
                raise ValueError("historique vide")
            s.rename("close").to_frame().to_csv(os.path.join(cache, f"{code}.csv"), index_label="date")
        except Exception as e:
            errors[code] = f"{type(e).__name__} : {str(e)[:120]}"
        if pause:
            time.sleep(pause)
    return errors


def load(cache: str, codes: Optional[List[str]] = None) -> pd.DataFrame:
    """Clôtures des actifs gardés dans `cache`, sur le calendrier commun des
    séances (un jour férié garde le dernier cours, 5 séances au plus)."""
    series = {}
    for code in codes or [c for c, *_ in UNIVERSE]:
        path = os.path.join(cache, f"{code}.csv")
        if os.path.exists(path):
            df = pd.read_csv(path, parse_dates=["date"])
            s = df.set_index("date")["close"].astype(float)
            s.index = pd.DatetimeIndex(s.index).tz_localize("UTC") if s.index.tz is None else s.index
            series[code] = s
    if not series:
        return pd.DataFrame()
    close = pd.DataFrame(series).sort_index()
    return close.ffill(limit=FILL_LIMIT)


def own_trend(close: pd.DataFrame, n: int = REGIME_N) -> Dict[str, np.ndarray]:
    """Régime de chaque actif : clôture au-dessus de sa moyenne de `n`
    séances (connue le jour même, à la clôture, comme le régime BTC)."""
    sma = close.rolling(n, min_periods=n).mean()
    return {a: (close[a] > sma[a]).fillna(False).values for a in close.columns}


def features(close: pd.DataFrame, p: ts.TrendParams) -> Dict[str, Dict[str, np.ndarray]]:
    """Indicateurs de la règle pour chaque actif ; le filtre de liquidité est
    toujours franchi (volume supposé LIQUID)."""
    out = {}
    liquid = pd.Series(LIQUID, index=close.index)
    for a in close.columns:
        f = ts.asset_features(close[a], p, liquid)
        out[a] = {k: f[k].values for k in ("close", "vol", "prior_high", "mom", "age", "vol30")}
    return out


def backtest(close: pd.DataFrame, start: str, end: str, p: Optional[ts.TrendParams] = None,
             capital: float = 10_000.0, real_costs: bool = False) -> ts.PortfolioResult:
    """La boucle de backtest du bot sur ces actifs, chacun avec son propre
    régime (au lieu de BTC). `real_costs` : coût réel de chaque marché
    (COSTS) au lieu des frais de Binance."""
    p = p or ts.TrendParams()
    cols, trend = features(close, p), own_trend(close)
    always = np.ones(len(close.index), dtype=bool)

    def choose(i: int, snap: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        return {a: s for a, s in snap.items() if trend[a][i]}
    hooks = ts.BacktestHooks(choose=choose)
    if real_costs:
        p = dataclasses.replace(p, fee=0.0, slippage=0.0)
        hooks = ts.BacktestHooks(choose=choose, exec_cost=lambda a, _n, _side: COSTS.get(CATEGORY.get(a, ""), 0.001))
    return ts.backtest(close, None, p, start, end, capital=capital, pre=(cols, always), hooks=hooks)


def of_category(close: pd.DataFrame, category: str) -> pd.DataFrame:
    return close[[c for c in close.columns if CATEGORY.get(c) == category]]


def unbiased(close: pd.DataFrame) -> pd.DataFrame:
    """Les actifs qui comptent dans un verdict : tout sauf les actions."""
    return close[[c for c in close.columns if CATEGORY.get(c) not in BIASED]]


def summary(close: pd.DataFrame) -> List[Dict[str, Any]]:
    """État du jour de chaque actif pour la règle : tendance propre,
    cassure, momentum (information seulement)."""
    if close.empty:
        return []
    p = ts.TrendParams()
    cols, trend = features(close, p), own_trend(close)
    rows = []
    for a in close.columns:
        c = cols[a]
        snap = {k: float(v[-1]) for k, v in c.items()}
        rows.append({"code": a, "name": NAME.get(a, a), "category": CATEGORY.get(a, ""),
                     "close": snap["close"], "trend": bool(trend[a][-1]),
                     "signal": bool(trend[a][-1] and ts.entry_signal(snap, p)),
                     "binance": BINANCE_NAME.get(a, "")})
    return rows


# ══════════════════════════════════════════════════════════════════════
# SUIVI QUOTIDIEN : tous les marchés, et un portefeuille en argent fictif
# ══════════════════════════════════════════════════════════════════════

KEY = "multi_actifs"                # méta du noyau de savoir
REFRESH_HOURS = 24                  # une lecture par jour (cours de clôture)
RETRY_HOURS = 6                     # après une panne
CURVE_POINTS = 120                  # points de la courbe gardés pour le panneau


def due(book: Any, now: datetime) -> bool:
    """À relire : une fois par jour, six heures après une panne."""
    last = (book or {}).get("tried_at") if isinstance(book, dict) else None
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (now - when).total_seconds() >= 3600 * (RETRY_HOURS if book.get("error") else REFRESH_HOURS)


def portfolio(close: pd.DataFrame, start: str, capital: float, binance: bool = False) -> Dict[str, Any]:
    """Portefeuille fictif depuis `start` : la règle rejouée chaque jour sur
    les clôtures réelles. Par défaut, hors cryptos sans les actions, au coût
    réel de chaque marché ; `binance` : la manche tradable sur Binance (or,
    euro, actions tokenisées), aux frais de Binance. Rejouer plutôt que tenir
    des comptes : le résultat est exactement celui du backtest."""
    sub = close[[c for c in BINANCE_NAME if c in close.columns]] if binance else unbiased(close)
    real = not binance
    end = str(sub.index[-1].date())
    if end < start:
        return {"start": start, "capital": capital, "equity": capital, "return_pct": 0.0,
                "positions": [], "trades": 0, "curve": []}
    r = backtest(sub, start, end, capital=capital, real_costs=real)
    eq = float(r.equity.iloc[-1]) if len(r.equity) else capital
    last = {a: float(sub[a].dropna().iloc[-1]) for a in r.holdings}
    positions = [{"code": a, "name": NAME.get(a, a), "category": CATEGORY.get(a, ""), "entry": h.entry,
                  "stop": h.stop, "price": last[a], "pnl_pct": (last[a] / h.entry - 1) * 100}
                 for a, h in sorted(r.holdings.items())]
    curve = [[str(d.date()), round(float(v), 2)] for d, v in r.equity.iloc[-CURVE_POINTS:].items()]
    return {"start": start, "capital": capital, "equity": eq, "return_pct": (eq / capital - 1) * 100,
            "positions": positions, "trades": len(r.trades), "curve": curve}


def refresh(memory: Any, now: datetime, fetch: Fetch, cache: str, capital: float,
            force: bool = False, pause: float = 1.0) -> Dict[str, Any]:
    """Relit tous les marchés hors cryptos une fois par jour (dans `cache`),
    note l'état de chacun pour la règle et rejoue le portefeuille fictif
    depuis son premier jour. Une panne est notée, jamais bloquante ; un
    marché illisible n'empêche pas les autres."""
    from .market_watch import friendly_error
    book = memory.get(KEY)
    book = dict(book) if isinstance(book, dict) else {}
    if not (force or due(book, now)):
        return book
    book["tried_at"] = now.isoformat(timespec="seconds")
    try:
        codes = list(dict.fromkeys([c for c, *_ in UNIVERSE] + list(BINANCE_NAME)))
        errors = download(cache, fetch, codes=codes, pause=pause, symbols=SYMBOL)
        close = load(cache, codes)
        if close.empty:
            raise ValueError("aucun historique lisible")
        start = book.get("start") or now.date().isoformat()
        book.update(fetched_at=book["tried_at"], error="", start=start, unread=sorted(errors),
                    rows=summary(close), portfolio=portfolio(close, start, float(capital)),
                    binance=portfolio(close, start, float(capital), binance=True))
    except Exception as e:
        book["error"] = friendly_error(e)
    memory.put(KEY, book)
    return book


# ══════════════════════════════════════════════════════════════════════
# PANNEAU : une page par classe d'actifs (lecture seule)
# ══════════════════════════════════════════════════════════════════════

# Pages du panneau : (onglet, catégorie), dans l'ordre du menu.
CLASSES: Tuple[Tuple[str, str], ...] = (
    ("commodities", "Matières premières"), ("forex", "Devises"), ("indices", "Indices"),
    ("stocks", "Actions"), ("bonds", "Obligations"))
ALL_CODES = list(dict.fromkeys([c for c, *_ in UNIVERSE] + [c for c, *_ in BINANCE]))
SESSIONS = {"1d": 1, "1m": 21, "3m": 63, "1y": 252}     # séances de chaque variation
SPARK_SESSIONS = {"1m": 21, "3m": 63, "1y": 252, "5y": 1260}
SPARK_POINTS = 120                                       # points d'une courbe de carte, au plus


def codes_of(category: str) -> List[str]:
    """Marchés d'une catégorie : ceux de l'univers, puis ceux de la manche
    Binance qui n'y sont pas."""
    return list(dict.fromkeys([c for c, _n, k, _s in UNIVERSE if k == category]
                              + [c for c, _n, k, _s in BINANCE if k == category]))


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def market_detail(code: str, close: pd.Series, p: Optional[ts.TrendParams] = None) -> Dict[str, Any]:
    """Ce que la page d'un marché dit de lui à sa dernière séance :
    variations, plus haut et plus bas sur un an, moyenne de 150 séances,
    niveau de cassure (plus haut des 30 séances précédentes), momentum,
    volatilité, stop initial d'un achat du jour, tendance et signal de la
    règle (calculés comme pour le suivi quotidien). Information seulement."""
    p = p or ts.TrendParams()
    s = close.dropna().astype(float)
    row: Dict[str, Any] = {"code": code, "name": NAME.get(code, code), "category": CATEGORY.get(code, ""),
                           "symbol": SYMBOL.get(code, ""), "binance": BINANCE_NAME.get(code, "")}
    if s.empty:
        return dict(row, close=None, date=None, trend=False, signal=False)
    f = ts.asset_features(s, p, pd.Series(LIQUID, index=s.index))
    snap = {k: float(f[k].iloc[-1]) for k in ("close", "vol", "prior_high", "mom", "age", "vol30")}
    px = float(s.iloc[-1])
    avg = _num(s.rolling(REGIME_N, min_periods=REGIME_N).mean().iloc[-1])
    trend = bool(avg is not None and px > avg)
    year = s[s.index > s.index[-1] - pd.Timedelta(days=365)]
    vol, high = _num(snap["vol"]), _num(snap["prior_high"])
    return dict(row, close=px, date=str(s.index[-1].date()),
                changes={k: (px / float(s.iloc[-1 - n]) - 1) * 100 if len(s) > n else None
                         for k, n in SESSIONS.items()},
                high_1y=float(year.max()), low_1y=float(year.min()),
                sma=avg, gap_pct=(px / avg - 1) * 100 if avg else None,
                breakout=high, breakout_gap_pct=(px / high - 1) * 100 if high else None,
                mom=_num(snap["mom"]),
                vol_pct=vol / px * 100 if vol and px else None,
                stop=px - p.init_stop_atr * vol if vol else None,
                trend=trend, signal=bool(trend and ts.entry_signal(snap, p)))


def market_points(close: pd.Series, days: int = 0) -> List[Dict[str, Any]]:
    """Points du graphique d'un marché : clôture, moyenne de 150 séances et
    niveau de cassure de chaque séance où la moyenne existe ; les `days`
    derniers jours (0 : tout). Temps en secondes (minuit UTC)."""
    s = close.dropna().astype(float)
    if s.empty:
        return []
    n = ts.TrendParams().breakout_n
    sma = s.rolling(REGIME_N, min_periods=REGIME_N).mean()
    high = s.shift(1).rolling(n, min_periods=n).max()
    frame = pd.DataFrame({"close": s, "sma": sma, "high": high}).dropna(subset=["sma"])
    if days:
        frame = frame[frame.index > s.index[-1] - pd.Timedelta(days=days)]
    return [{"t": int(d.timestamp()), "close": round(float(c), 6), "sma": round(float(m), 6),
             "high": None if pd.isna(h) else round(float(h), 6)}
            for d, c, m, h in zip(frame.index, frame["close"], frame["sma"], frame["high"])]


def spark(close: pd.Series, window: str = "1y", points: int = SPARK_POINTS) -> List[float]:
    """Clôtures de la courbe d'une carte sur `window` (1m, 3m, 1y, 5y),
    éclaircies à `points` au plus ; la dernière clôture est toujours gardée."""
    s = close.dropna().astype(float).iloc[-(SPARK_SESSIONS.get(window, 252) + 1):]
    if len(s) > points:
        step = -(-len(s) // points)                        # arrondi au-dessus
        s = s.iloc[::-1].iloc[::step].iloc[::-1]
    return [round(float(v), 6) for v in s.values]


def held_positions(book: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Positions des deux portefeuilles fictifs, par marché."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    book = book if isinstance(book, dict) else {}
    for key, label in (("portfolio", "Portefeuille fictif hors cryptos"), ("binance", "Manche Binance fictive")):
        for pos in ((book.get(key) or {}).get("positions") or []):
            if isinstance(pos, dict) and pos.get("code"):
                out.setdefault(str(pos["code"]), []).append(dict(pos, book=label))
    return out
