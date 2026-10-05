#!/usr/bin/env python3
"""
Noyau de savoir de TrendGuard : le bot lit Internet, garde tout ce qu'il
apprend et mesure qui a raison (docs/SAVOIR.md).

Toutes les 15 minutes (TG_SAVOIR_MINUTES), dans un processus séparé :
  - presse spécialisée : CoinDesk, Cointelegraph, Decrypt ;
  - moteurs de recherche : Google Actualités, Bing Actualités ;
  - forums : Reddit (r/CryptoCurrency, r/Bitcoin, r/ethereum), Hacker News ;
  - réseau social : StockTwits (messages marqués « haussier » ou « baissier »
    par leurs auteurs) ;
  - tendances : recherches les plus fréquentes sur CoinGecko, indice Fear &
    Greed (et tout son historique depuis 2018, appris dès le premier jour) ;
  - IA : les avis du conseil des IA de la veille quotidienne
    (market_watch.py), quand des clés d'IA sont enregistrées.
Chaque texte devient une connaissance datée (source, titre, lien, cryptos
citées, ton) ; chaque source donne chaque jour un avis par crypto, de −1
(baisse) à +1 (hausse). Le noyau grandit à chaque lecture ; les textes de
plus de 180 jours sont effacés, les avis et les bilans gardés.

À chaque décision, le bot vérifie : chaque avis net est confronté au cours
réel 7 jours plus tard, un jour sur sept (des semaines indépendantes). Une
source n'est « fiable » qu'avec au moins 20 semaines vérifiées, si elle fait
mieux que le hasard avec 99 % de certitude ET sur chaque moitié de son
historique ; « trompeuse » si elle fait nettement moins bien (son contraire
est alors instructif) ; sinon, elle ne fait pas mieux que le hasard.

L'avis du bot sur une crypto : les avis du jour des seules sources prouvées,
pondérés par leur avance sur le hasard. Il ne peut que rendre le bot plus
prudent : une crypto qu'il voit nettement en baisse n'est pas achetée ce
jour-là (achat reporté, expliqué dans le raisonnement). Chaque report est
vérifié 7 jours plus tard ; si ces reports coûtent plus qu'ils n'évitent,
le bot les suspend de lui-même. Jamais de vente, ni d'achat, ni de risque en
plus, ni de règle changée : les règles ne changent que par l'évolution
encadrée (evolution.py), éprouvée sur 8 ans de données.

Sécurité : les textes sont des données, jamais des instructions (un message
manipulateur sur un forum n'a aucun effet) ; une source n'a aucun poids tant
qu'elle n'a pas prouvé sa fiabilité, et le perd dès qu'elle ne la prouve plus.

  python trendguard_bot.py savoir              # bilan
  python trendguard_bot.py savoir collecter    # une lecture d'Internet
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import pathlib
import re
import sqlite3
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import v29

from . import market_watch as mw
from .texte import fr

DEFAULT_DB = os.path.join(v29.APP_DIR, "trendguard_savoir.db")
HORIZON = 7               # jours : un avis est jugé sur le cours 7 jours plus tard
MIN_VIEW = 0.2            # avis net (|avis| ≥ 0,2) : seuls ceux-là sont jugés
MIN_WEEKS = 20            # semaines vérifiées avant tout verdict
Z = 2.576                 # 99 % de certitude
MARGIN = 0.02             # avance minimale sur le hasard (2 points)
HOLD_VIEW = -0.5          # avis du bot sous lequel un achat est reporté
HOLD_MIN = 10             # reports vérifiés avant de juger leur utilité
KEEP_DAYS = 180           # textes gardés ; avis et bilans gardés toujours
MAX_SOCIAL = 6            # cryptos lues sur StockTwits à chaque lecture (à tour de rôle)

GOOGLE_QUERY = "crypto OR bitcoin OR ethereum"
BING_URL = "https://www.bing.com/news/search?q={q}&format=rss"
SUBREDDITS = ("CryptoCurrency", "Bitcoin", "ethereum")
REDDIT_URL = "https://www.reddit.com/r/{sub}/hot/.rss?limit=25"
HN_URL = ("https://hn.algolia.com/api/v1/search_by_date?query={q}&tags=story&hitsPerPage=30"
          "&attributesToRetrieve=title,created_at_i&attributesToHighlight=")
STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/{sym}.X.json"
TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
FNG_URL = "https://api.alternative.me/fng/?limit={n}&format=json"
ATOM = "{http://www.w3.org/2005/Atom}"

# Ton d'un titre : mots de la hausse contre mots de la baisse (anglais, la
# langue de presque toutes les sources).
POSITIVE = re.compile(
    r"\b(surg(e|es|ed|ing)|soar(s|ed|ing)?|rall(y|ies|ied|ying)|jump(s|ed)?|gain(s|ed)?|"
    r"record high|all-time high|bull(s|ish)?|breakout|approv(e|es|ed|al)|adopt(s|ed|ion)|"
    r"partner(s|ship)?|upgrade[sd]?|inflows?|rebound(s|ed)?|recover(s|ed|y)|climb(s|ed)?|"
    r"rise[sn]?|rising|boost(s|ed)?|optimis(m|tic)|accumulat\w*)\b", re.I)
NEGATIVE = re.compile(
    r"\b(crash(es|ed)?|plung(e|es|ed)|plummet(s|ed)?|tumbl(e|es|ed)|slump(s|ed)?|drop(s|ped)?|"
    r"fall(s|en|ing)?|fell|sell-?off|bear(s|ish)?|hack(s|ed)?|exploit(s|ed)?|lawsuit|sue[sd]?|"
    r"ban(s|ned)?|delist\w*|liquidat\w*|outflows?|fraud|scam|probe|investigat\w*|warn(s|ing)?|"
    r"fear(s)?|panic|dump(s|ed)?|losses|collapse[sd]?|declin(e|es|ed)|sink(s)?|sank)\b", re.I)


def tone(text: str) -> float:
    """Ton d'un texte, de −1 (baisse) à +1 (hausse) ; 0 sans mot marqué."""
    pos, neg = len(POSITIVE.findall(text or "")), len(NEGATIVE.findall(text or ""))
    return (pos - neg) / (pos + neg) if pos + neg else 0.0


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def _read_only(path: str) -> sqlite3.Connection:
    """Base ouverte en lecture seule (panneau, avis des IA)."""
    uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


@dataclass
class Doc:
    """Une connaissance : un texte lu (ou un relevé), sa famille de sources,
    les cryptos qu'il cite et son ton."""
    family: str                 # source jugée (« Reddit », « StockTwits »…)
    kind: str                   # presse | recherche | forum | social | tendance
    source: str                 # origine précise (« Reddit r/Bitcoin »)
    title: str
    url: str
    ts: float
    assets: Tuple[str, ...] = ()
    tone: float = 0.0
    key: str = ""               # identifiant stable (sinon : famille + lien)

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.family}|{self.key or self.url or self.title}".encode()).hexdigest()[:20]


# ══════════════════════════════════════════════════════════════════════
# MÉMOIRE (SQLite) : textes, avis du jour par source, reports, bilan
# ══════════════════════════════════════════════════════════════════════

class Memory:
    """Le noyau de savoir sur le disque : il grandit à chaque lecture."""

    def __init__(self, path: str = DEFAULT_DB, readonly: bool = False):
        self.path = path
        if readonly:
            self.conn = _read_only(path)
            return
        self.conn = sqlite3.connect(path, timeout=30)
        c = self.conn
        c.execute("PRAGMA auto_vacuum=INCREMENTAL")
        c.execute("CREATE TABLE IF NOT EXISTS docs (id TEXT PRIMARY KEY, ts REAL, day TEXT, family TEXT, "
                  "kind TEXT, source TEXT, title TEXT, url TEXT, assets TEXT, tone REAL)")
        c.execute("CREATE INDEX IF NOT EXISTS docs_day ON docs(day)")
        c.execute("CREATE TABLE IF NOT EXISTS views (day TEXT, source TEXT, asset TEXT, value REAL, "
                  "n INTEGER, PRIMARY KEY (day, source, asset))")
        c.execute("CREATE TABLE IF NOT EXISTS holds (day TEXT, asset TEXT, value REAL, sources TEXT, "
                  "applied INTEGER, PRIMARY KEY (day, asset))")
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        c.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- méta-données (dernière lecture, bilan, sources en panne) ----

    def get(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
        self.conn.commit()

    # ---- textes et avis ----

    def add_docs(self, docs: Iterable[Doc]) -> Set[str]:
        """Enregistre les textes nouveaux ; renvoie les jours touchés."""
        days: Set[str] = set()
        cur = self.conn.cursor()
        for d in docs:
            day = _day(d.ts)
            cur.execute("INSERT OR IGNORE INTO docs VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (d.id, d.ts, day, d.family, d.kind, d.source, d.title[:300], d.url[:500],
                         json.dumps(sorted(set(d.assets))), round(d.tone, 3)))
            if cur.rowcount:
                days.add(day)
        self.conn.commit()
        return days

    def refresh_views(self, days: Iterable[str]) -> None:
        """Avis du jour de chaque famille de textes sur chaque crypto citée :
        le ton moyen de ses textes de la journée."""
        for day in sorted(set(days)):
            acc: Dict[Tuple[str, str], List[float]] = {}
            for family, assets, t in self.conn.execute(
                    "SELECT family, assets, tone FROM docs WHERE day=?", (day,)):
                for a in json.loads(assets):
                    acc.setdefault((family, a), []).append(float(t))
            families = {f for f, _a in acc}
            self.conn.executemany("DELETE FROM views WHERE day=? AND source=?", [(day, f) for f in families])
            self.conn.executemany("INSERT OR REPLACE INTO views VALUES (?,?,?,?,?)",
                                  [(day, f, a, round(sum(v) / len(v), 3), len(v)) for (f, a), v in acc.items()])
        self.conn.commit()

    def put_views(self, rows: Iterable[Tuple[str, str, str, float]]) -> int:
        """Avis donnés directement (historique, avis des IA)."""
        rows = [(d, s, a, round(float(v), 3), 1) for d, s, a, v in rows]
        self.conn.executemany("INSERT OR REPLACE INTO views VALUES (?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def views(self, since: str = "") -> List[Tuple[str, str, str, float]]:
        return self.conn.execute("SELECT day, source, asset, value FROM views WHERE day >= ? "
                                 "ORDER BY day", (since,)).fetchall()

    def views_of(self, day: str) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for source, asset, value in self.conn.execute(
                "SELECT source, asset, value FROM views WHERE day=?", (day,)):
            out.setdefault(source, {})[asset] = float(value)
        return out

    def counts(self, day: str) -> Dict[str, Any]:
        """Taille du noyau : textes (en tout, du jour, par type), avis,
        sources suivies, poids du fichier."""
        total = self.conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        kinds = dict(self.conn.execute("SELECT kind, COUNT(*) FROM docs WHERE day=? GROUP BY kind", (day,)))
        views = self.conn.execute("SELECT COUNT(*) FROM views").fetchone()[0]
        sources = self.conn.execute("SELECT COUNT(DISTINCT source) FROM views").fetchone()[0]
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = 0
        return {"docs": total, "today": sum(kinds.values()), "kinds": kinds, "views": views,
                "sources": sources, "mb": round(size / 1e6, 1)}

    def recent_docs(self, limit: int = 8) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT ts, source, kind, title, url, tone FROM docs WHERE kind != 'tendance' "
                                 "AND kind != 'social' ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "source": r[1], "kind": r[2], "title": r[3], "url": r[4], "tone": r[5]}
                for r in rows]

    def prune(self, now: datetime, keep_days: int = KEEP_DAYS) -> int:
        """Efface les textes trop anciens (les avis et bilans restent)."""
        cur = self.conn.execute("DELETE FROM docs WHERE ts < ?", ((now - timedelta(days=keep_days)).timestamp(),))
        self.conn.commit()
        if cur.rowcount:
            self.conn.execute("PRAGMA incremental_vacuum")
        return cur.rowcount

    # ---- reports d'achat décidés par le savoir ----

    def save_holds(self, day: str, holds: Dict[str, Dict[str, Any]], applied: bool) -> None:
        self.conn.executemany("INSERT OR REPLACE INTO holds VALUES (?,?,?,?,?)",
                              [(day, a, h["value"], json.dumps(h["sources"], ensure_ascii=False), int(applied))
                               for a, h in holds.items()])
        self.conn.commit()

    def holds(self) -> List[Tuple[str, str, float, int]]:
        return self.conn.execute("SELECT day, asset, value, applied FROM holds ORDER BY day").fetchall()


# ══════════════════════════════════════════════════════════════════════
# LECTURE D'INTERNET : une fonction par famille de sources
# ══════════════════════════════════════════════════════════════════════

Fetch = Callable[[str], bytes]


def http_get(url: str, timeout: float = 20.0) -> bytes:
    """Lecture compressée (gzip) quand le site le permet : plusieurs fois
    moins de données, utile la nuit sur le partage de connexion d'un
    téléphone."""
    req = urllib.request.Request(url, headers={"User-Agent": mw.UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return gzip.decompress(data) if r.headers.get("Content-Encoding", "").lower() == "gzip" else data
    except urllib.error.HTTPError as e:
        raise mw.HttpError(e.code, e.read()[:300].decode("utf-8", "replace")) from None


@dataclass
class Context:
    fetch: Fetch
    universe: List[str]
    held: List[str]
    now: datetime
    rotation: int = 0


def _cited(text: str, universe: Iterable[str]) -> Tuple[str, ...]:
    return tuple(a for a in universe if mw.mentions(text, a))


def _from_items(items: Sequence[mw.Item], ctx: Context, family: str, kind: str) -> List[Doc]:
    out = []
    for it in items:
        try:
            ts = datetime.fromisoformat(it.published).timestamp() if it.published else ctx.now.timestamp()
        except ValueError:
            ts = ctx.now.timestamp()
        out.append(Doc(family, kind, it.source, it.title, it.url, min(ts, ctx.now.timestamp()),
                       _cited(it.title, ctx.universe), tone(it.title)))
    return out


def read_press(ctx: Context) -> List[Doc]:
    items: List[mw.Item] = []
    for name, url in mw.FEEDS:
        items += mw.parse_rss(ctx.fetch(url), name)
    return _from_items(items, ctx, "Presse spécialisée", "presse")


def read_google(ctx: Context) -> List[Doc]:
    return _from_items(mw.parse_rss(ctx.fetch(mw.google_news_url(GOOGLE_QUERY)), "Google Actualités"),
                       ctx, "Google Actualités", "recherche")


def read_bing(ctx: Context) -> List[Doc]:
    url = BING_URL.format(q=urllib.parse.quote("crypto bitcoin"))
    return _from_items(mw.parse_rss(ctx.fetch(url), "Bing Actualités"), ctx, "Bing Actualités", "recherche")


def parse_atom(xml_bytes: bytes, source: str) -> List[mw.Item]:
    """Flux Atom (Reddit) : titre, lien, date."""
    root = ET.fromstring(xml_bytes)
    out = []
    for e in root.iter(ATOM + "entry"):
        title = (e.findtext(ATOM + "title") or "").strip()
        link = e.find(ATOM + "link")
        url = (link.get("href") if link is not None else "") or ""
        when = (e.findtext(ATOM + "updated") or e.findtext(ATOM + "published") or "").strip()
        try:
            pub = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except ValueError:
            pub = ""
        if title and url:
            out.append(mw.Item(title, url, source, pub))
    return out


def read_reddit(ctx: Context) -> List[Doc]:
    """Un forum Reddit par lecture, à tour de rôle : Reddit limite fortement
    les lectures sans compte."""
    sub = SUBREDDITS[ctx.rotation % len(SUBREDDITS)]
    return _from_items(parse_atom(ctx.fetch(REDDIT_URL.format(sub=sub)), f"Reddit r/{sub}"),
                       ctx, "Reddit", "forum")


def read_hacker_news(ctx: Context) -> List[Doc]:
    items = []
    for q in ("crypto", "bitcoin"):
        for h in json.loads(ctx.fetch(HN_URL.format(q=q))).get("hits", []):
            title = (h.get("title") or "").strip()
            if not title or not h.get("objectID"):
                continue
            ts = float(h.get("created_at_i") or ctx.now.timestamp())
            items.append(mw.Item(title, f"https://news.ycombinator.com/item?id={h['objectID']}",
                                 "Hacker News", datetime.fromtimestamp(ts, timezone.utc).isoformat()))
    return _from_items(items, ctx, "Hacker News", "forum")


def social_targets(universe: Sequence[str], held: Iterable[str], rotation: int,
                   limit: int = MAX_SOCIAL) -> List[str]:
    """Cryptos lues sur StockTwits à cette lecture : toutes à tour de rôle,
    BTC et les détenues en tête de liste (chaque crypto lue plusieurs fois
    par jour, sans trop de données)."""
    order = list(dict.fromkeys([a for a in ["btc", *held] if a in universe] + list(universe)))
    if len(order) <= limit:
        return order
    start = (rotation * limit) % len(order)
    return [order[(start + k) % len(order)] for k in range(limit)]


def read_stocktwits(ctx: Context) -> List[Doc]:
    """Messages marqués haussier/baissier par leurs auteurs : un relevé par
    crypto et par heure, ton = (haussiers − baissiers) / marqués."""
    out = []
    hour = ctx.now.strftime("%Y-%m-%dT%H")
    for a in social_targets(ctx.universe, ctx.held, ctx.rotation):
        data = json.loads(ctx.fetch(STOCKTWITS_URL.format(sym=a.upper())))
        msgs = data.get("messages") or []
        tags = [((m.get("entities") or {}).get("sentiment") or {}).get("basic") for m in msgs]
        bull, bear = tags.count("Bullish"), tags.count("Bearish")
        if bull + bear < 5:
            continue
        out.append(Doc("StockTwits", "social", "StockTwits",
                       f"StockTwits {a.upper()} : {bull} message(s) haussier(s), {bear} baissier(s) "
                       f"sur {len(msgs)}", f"https://stocktwits.com/symbol/{a.upper()}.X",
                       ctx.now.timestamp(), (a,), (bull - bear) / (bull + bear), key=f"{a}-{hour}"))
    return out


def read_trending(ctx: Context) -> List[Doc]:
    """Recherches les plus fréquentes sur CoinGecko : l'attention du public
    (hypothèse jugée par le bot : attention = hausse)."""
    coins = json.loads(ctx.fetch(TRENDING_URL)).get("coins") or []
    symbols = [str((c.get("item") or {}).get("symbol") or "").lower() for c in coins]
    hour = ctx.now.strftime("%Y-%m-%dT%H")
    return [Doc("Tendances CoinGecko", "tendance", "CoinGecko",
                f"{a.upper()} parmi les cryptos les plus recherchées sur CoinGecko",
                "https://www.coingecko.com/", ctx.now.timestamp(), (a,), 0.5, key=f"{a}-{hour}")
            for a in ctx.universe if a in symbols]


def _fng_view(value: int) -> float:
    return (int(value) - 50) / 50


def read_fear_greed(ctx: Context) -> List[Doc]:
    data = json.loads(ctx.fetch(FNG_URL.format(n=1))).get("data") or []
    out = []
    for d in data[:1]:
        ts = float(d["timestamp"])
        out.append(Doc("Fear & Greed", "tendance", "alternative.me",
                       f"Fear & Greed {d['value']} ({d.get('value_classification', '')})",
                       "https://alternative.me/crypto/fear-and-greed-index/", ts, ("btc",),
                       _fng_view(d["value"]), key=_day(ts)))
    return out


def fear_greed_history(fetch: Fetch) -> List[Tuple[str, str, str, float]]:
    """Tout l'historique de l'indice (depuis 2018) : appris dès le premier
    jour, jugé aussitôt sur les cours de BTC."""
    data = json.loads(fetch(FNG_URL.format(n=0))).get("data") or []
    return [(_day(float(d["timestamp"])), "Fear & Greed", "btc", _fng_view(d["value"])) for d in data]


READERS: Tuple[Tuple[str, Callable[[Context], List[Doc]]], ...] = (
    ("Presse spécialisée", read_press), ("Google Actualités", read_google),
    ("Bing Actualités", read_bing), ("Reddit", read_reddit), ("Hacker News", read_hacker_news),
    ("StockTwits", read_stocktwits), ("Tendances CoinGecko", read_trending),
    ("Fear & Greed", read_fear_greed))


def ai_views(watch_db: str) -> List[Tuple[str, str, str, float]]:
    """Avis du conseil des IA (veille quotidienne), une source par IA."""
    if not watch_db or not os.path.exists(watch_db):
        return []
    con = _read_only(watch_db)
    try:
        rows = con.execute("SELECT day, provider, asset, sentiment FROM opinions").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        con.close()
    return [(d, f"IA {p}", a, float(s)) for d, p, a, s in rows]


def collect(memory: Memory, universe: Sequence[str], held: Iterable[str], now: datetime,
            fetch: Fetch = http_get, watch_db: str = "") -> Dict[str, Any]:
    """Une lecture d'Internet : chaque famille de sources, les avis des IA,
    l'historique de Fear & Greed la première fois ; avis du jour recalculés,
    vieux textes effacés. Une source en panne n'arrête pas les autres."""
    rotation = int(memory.get("rotation", 0))
    ctx = Context(fetch, [a.lower() for a in universe], [a.lower() for a in held], now, rotation)
    docs: List[Doc] = []
    errors: Dict[str, str] = {}
    read: Dict[str, int] = {}
    for name, reader in READERS:
        try:
            got = reader(ctx)
        except Exception as e:
            errors[name] = mw.friendly_error(e)
            continue
        read[name] = len(got)
        docs += got
    days = memory.add_docs(docs)
    days.add(now.date().isoformat())
    memory.refresh_views(days)
    memory.put_views(ai_views(watch_db))
    if not memory.get("fng_history"):
        try:
            memory.put_views(fear_greed_history(fetch))
            memory.put("fng_history", now.isoformat(timespec="seconds"))
        except Exception as e:
            errors["Fear & Greed (historique)"] = mw.friendly_error(e)
    pruned = memory.prune(now)
    run = {"at": now.isoformat(timespec="seconds"), "read": read, "errors": errors,
           "new_days": sorted(days), "pruned": pruned}
    memory.put("rotation", rotation + 1)
    memory.put("last_run", run)
    return run


# ══════════════════════════════════════════════════════════════════════
# QUI A RAISON ? Avis confrontés aux cours réels
# ══════════════════════════════════════════════════════════════════════

def score_sources(views: Iterable[Tuple[str, str, str, float]], close: Any,
                  horizon: int = HORIZON, min_weeks: int = MIN_WEEKS, z: float = Z) -> Dict[str, Dict[str, Any]]:
    """Fiabilité de chaque source : ses avis nets confrontés au cours de la
    crypto `horizon` jours après la clôture du jour de l'avis. Une
    observation par jour (taux de réussite du jour), un jour gardé sur
    `horizon` (semaines indépendantes), comparée au hasard : une source
    sans avance qui dirait « hausse » aussi souvent réussirait
    p(hausse dite) × p(hausse) + p(baisse dite) × p(baisse)."""
    if close is None or not len(close):
        return {}
    index = {str(d.date()): i for i, d in enumerate(close.index)}
    per: Dict[str, Dict[str, List[Tuple[bool, bool]]]] = {}
    for day, source, asset, value in views:
        if abs(value) < MIN_VIEW or asset not in close.columns or day not in index:
            continue
        i = index[day]
        if i + horizon >= len(close):
            continue
        p0, p1 = close[asset].iloc[i], close[asset].iloc[i + horizon]
        if not (math.isfinite(p0) and math.isfinite(p1)) or p0 <= 0:
            continue
        per.setdefault(source, {}).setdefault(day, []).append((value > 0, bool(p1 > p0)))
    out = {}
    for source, days in per.items():
        kept, last = [], None
        for day in sorted(days):
            d = date.fromisoformat(day)
            if last is None or (d - last).days >= horizon:
                kept.append(days[day])
                last = d
        calls = [c for rows in kept for c in rows]
        pb = sum(b for b, _u in calls) / len(calls)
        pu = sum(u for _b, u in calls) / len(calls)
        chance = pb * pu + (1 - pb) * (1 - pu)
        rates = [sum(b == u for b, u in rows) / len(rows) for rows in kept]
        n = len(rates)
        rate = sum(rates) / n
        sd = statistics.pstdev(rates) if n > 1 else 0.0
        t = (rate - chance) / (sd / math.sqrt(n)) if sd > 0 else (0.0 if rate == chance else math.copysign(99.0, rate - chance))
        half = n // 2
        first = sum(rates[:half]) / half - chance if half else 0.0
        second = sum(rates[half:]) / (n - half) - chance if n - half else 0.0
        if n < min_weeks:
            verdict = "observation"
        elif t >= z and rate - chance >= MARGIN and first > 0 and second > 0:
            verdict = "fiable"
        elif t <= -z and rate - chance <= -MARGIN and first < 0 and second < 0:
            verdict = "trompeuse"
        else:
            verdict = "hasard"
        out[source] = {"source": source, "weeks": n, "rate": round(rate, 3), "chance": round(chance, 3),
                       "edge": round(rate - chance, 3), "t": round(t, 2), "verdict": verdict}
    return out


def opinion(today: Dict[str, Dict[str, float]], scores: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Avis du bot sur chaque crypto : les avis du jour des seules sources
    prouvées, pondérés par leur avance sur le hasard (l'avis d'une source
    trompeuse compte à l'envers)."""
    acc: Dict[str, List[Tuple[float, float, str]]] = {}
    for source, views in today.items():
        s = scores.get(source)
        if not s or s["verdict"] not in ("fiable", "trompeuse"):
            continue
        for a, v in views.items():
            if abs(v) >= MIN_VIEW:
                acc.setdefault(a, []).append((s["edge"], v, source))
    out = {}
    for a, rows in acc.items():
        value = sum(w * v for w, v, _s in rows) / sum(abs(w) for w, _v, _s in rows)
        out[a] = {"value": round(value, 2), "sources": sorted(s for _w, _v, s in rows)}
    return out


def hold_record(holds: Sequence[Tuple[str, str, float, int]], close: Any,
                horizon: int = HORIZON) -> Dict[str, Any]:
    """Bilan des reports d'achat : la crypto a-t-elle baissé dans les
    `horizon` jours (report utile) ou monté (achat manqué) ?"""
    if close is None or not len(close):
        return {"checked": 0, "helped": 0, "avg_pct": None}
    index = {str(d.date()): i for i, d in enumerate(close.index)}
    rets = []
    for day, asset, _v, _applied in holds:
        if asset not in close.columns or day not in index or index[day] + horizon >= len(close):
            continue
        i = index[day]
        p0, p1 = close[asset].iloc[i], close[asset].iloc[i + horizon]
        if math.isfinite(p0) and math.isfinite(p1) and p0 > 0:
            rets.append(float(p1 / p0 - 1))
    if not rets:
        return {"checked": 0, "helped": 0, "avg_pct": None}
    return {"checked": len(rets), "helped": int(sum(r < 0 for r in rets)),
            "avg_pct": round(sum(rets) / len(rets) * 100, 2)}


def judge(memory: Memory, close: Any, day: str) -> Dict[str, Any]:
    """Bilan du jour (à la décision) : fiabilité de chaque source, avis du
    bot, achats à reporter, bilan des reports passés. Enregistré pour le
    panneau et le rapport."""
    scores = score_sources(memory.views(), close)
    op = opinion(memory.views_of(day), scores)
    record = hold_record(memory.holds(), close)
    # Les reports se jugent eux-mêmes : s'ils ont coûté plus qu'ils n'ont
    # évité (la crypto a monté en moyenne), le bot les suspend.
    influence = not (record["checked"] >= HOLD_MIN and (record["avg_pct"] or 0.0) > 0)
    holds = {a: o for a, o in op.items() if o["value"] <= HOLD_VIEW}
    memory.save_holds(day, holds, influence)
    proven = sorted(s for s, x in scores.items() if x["verdict"] in ("fiable", "trompeuse"))
    res = {"day": day, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "scores": sorted(scores.values(), key=lambda x: (x["verdict"] != "fiable",
                                                            x["verdict"] != "trompeuse", -x["weeks"])),
           "opinion": op, "holds": holds if influence else {}, "shadow": {} if influence else holds,
           "influence": influence, "record": record, "proven": proven}
    memory.put("bilan", res)
    return res


# ══════════════════════════════════════════════════════════════════════
# RÉSUMÉS : raisonnement du bot, panneau, rapport
# ══════════════════════════════════════════════════════════════════════

KIND_LABEL = {"presse": "articles de presse", "recherche": "résultats de moteurs de recherche",
              "forum": "messages de forums", "social": "relevés de réseau social",
              "tendance": "tendances"}


def hold_text(a: str, h: Dict[str, Any]) -> str:
    return (f"Achat reporté : le savoir du bot voit {a.upper()} nettement en baisse (avis "
            f"{fr(h['value'], '+.2f')}), d'après {', '.join(h['sources'])}, source(s) prouvée(s) sur "
            f"au moins {MIN_WEEKS} semaines")


def summary(memory: Memory, day: str) -> Dict[str, Any]:
    """Ce que le noyau sait : sa taille, la dernière lecture, la fiabilité
    de chaque source, l'avis du bot et ses reports d'achat."""
    c = memory.counts(day)
    bilan = memory.get("bilan") or {}
    last = memory.get("last_run") or {}
    scores = bilan.get("scores") or []
    proven = [s for s in scores if s["verdict"] in ("fiable", "trompeuse")]
    watching = [s for s in scores if s["verdict"] == "observation"]
    parts = [f"{fr(c['docs'], ',.0f')} connaissances gardées ({fr(c['today'], ',.0f')} aujourd'hui)",
             f"{c['sources']} sources suivies"]
    if scores:
        parts.append(f"{len(proven)} prouvée(s)" if proven else "aucune source prouvée pour l'instant")
    text = ", ".join(parts)
    if watching:
        best = max(watching, key=lambda s: s["weeks"])
        text += f" ; en observation : {best['source']} ({best['weeks']} semaine(s) sur {MIN_WEEKS})"
    if bilan.get("holds"):
        text += " ; achats reportés aujourd'hui : " + ", ".join(a.upper() for a in sorted(bilan["holds"]))
    if bilan and not bilan.get("influence", True):
        text += " ; reports suspendus (ils ont coûté plus qu'ils n'ont évité)"
    return {"counts": c, "text": text, "last_run": last, "bilan": bilan,
            "recent": memory.recent_docs(), "kinds": {KIND_LABEL.get(k, k): n for k, n in c["kinds"].items()}}


def reasoning_line(bilan: Dict[str, Any], counts: Dict[str, Any]) -> str:
    """Une ligne pour « Ce que pense le bot »."""
    proven = bilan.get("proven") or []
    read = f"Savoir : {fr(counts.get('today', 0), ',.0f')} textes lus aujourd'hui sur Internet"
    if not proven:
        return (read + " ; aucune source n'a encore prouvé qu'elle voit juste mieux que le hasard : "
                "le bot s'en tient à ses règles.")
    op = bilan.get("opinion") or {}
    views = ", ".join(f"{a.upper()} {fr(o['value'], '+.2f')}" for a, o in sorted(op.items())) or "aucun avis net"
    return read + f" ; sources prouvées : {', '.join(proven)} ; avis du bot : {views}."


# ══════════════════════════════════════════════════════════════════════
# COMMANDE
# ══════════════════════════════════════════════════════════════════════

def render(s: Dict[str, Any]) -> str:
    lines = ["NOYAU DE SAVOIR — " + s["text"]]
    last = s.get("last_run") or {}
    if last:
        read = ", ".join(f"{k} {v}" for k, v in (last.get("read") or {}).items())
        lines.append(f"Dernière lecture : {last.get('at', '?')} — {read or 'rien'}")
        for k, e in (last.get("errors") or {}).items():
            lines.append(f"  ✗ {k} : {e}")
    for sc in (s.get("bilan") or {}).get("scores", []):
        rate = (f"réussite {fr(sc['rate'] * 100, '.0f')} % (hasard {fr(sc['chance'] * 100, '.0f')} %)"
                if sc["weeks"] >= 5 else "trop tôt pour une réussite")
        lines.append(f"  {sc['source']:<24} {sc['verdict']:<11} {sc['weeks']:>4} sem. {rate}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Noyau de savoir de TrendGuard")
    ap.add_argument("action", nargs="?", default="bilan", choices=["bilan", "collecter"])
    ap.add_argument("--detenues", default="", help="(collecter) cryptos détenues, séparées par des virgules")
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    gcfg = load_guard_config_from_env()
    now = datetime.now(timezone.utc)
    memory = Memory(gcfg.savoir_db or DEFAULT_DB)
    try:
        if args.action == "collecter":
            held = [a for a in args.detenues.split(",") if a]
            run = collect(memory, [a.lower() for a in gcfg.universe], held, now, watch_db=gcfg.watch_db)
            got = sum(run["read"].values())
            print(f"{now:%Y-%m-%d %H:%M:%S} UTC lecture : {got} texte(s) ; "
                  + (f"en panne : {', '.join(run['errors'])}" if run["errors"] else "toutes les sources lues"))
            return 0
        print(render(summary(memory, now.date().isoformat())))
        return 0
    finally:
        memory.close()


if __name__ == "__main__":
    sys.exit(main())
