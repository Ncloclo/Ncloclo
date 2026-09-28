"""Actualités et indicateurs des marchés (crypto et finance) pour le panneau.

Sources publiques, sans clé ni compte : flux RSS de médias crypto et
financiers (français et anglais), capitalisation du marché crypto
(CoinGecko), indice Peur & Avidité (alternative.me), indices boursiers,
or, pétrole, dollar et taux (Yahoo Finance, repli sur Binance pour l'or et
l'euro). Tout est mis en cache et rafraîchi en arrière-plan : le panneau
répond tout de suite, même sur une connexion lente. Lecture seule.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import market_watch as mw

UA = "Mozilla/5.0 (compatible; TrendGuard-panneau/1.0; +https://github.com/Ncloclo/Ncloclo)"
MAX_BYTES = 3_000_000
NEWS_TTL_SEC = 600
MARKETS_TTL_SEC = 300
KEEP_HOURS = 48
MAX_ITEMS = 200
PER_FEED = 20            # un agrégateur (Google) ne masque pas les autres médias


def _gnews(query: str) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{query} when:1d", "hl": "fr", "gl": "FR", "ceid": "FR:fr"})


# (source, catégorie, langue, adresse)
FEEDS: Tuple[Tuple[str, str, str, str], ...] = (
    ("CoinDesk", "crypto", "en", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "crypto", "en", "https://cointelegraph.com/rss"),
    ("Decrypt", "crypto", "en", "https://decrypt.co/feed"),
    ("Journal du Coin", "crypto", "fr", "https://journalducoin.com/feed/"),
    ("Cryptoast", "crypto", "fr", "https://cryptoast.fr/feed/"),
    ("Google Actualités", "crypto", "fr", _gnews("crypto OR bitcoin OR ethereum")),
    ("CNBC", "finance", "en", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("CNBC", "finance", "en", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("Le Monde", "finance", "fr", "https://www.lemonde.fr/economie/rss_full.xml"),
    ("Google Actualités", "finance", "fr", _gnews(
        '"CAC 40" OR "Wall Street" OR "marchés financiers" OR "Bourse de Paris" '
        'OR "banque centrale"')),
)

# (identifiant, nom, symbole Yahoo, unité)
QUOTES: Tuple[Tuple[str, str, str, str], ...] = (
    ("sp500", "S&P 500", "^GSPC", ""), ("nasdaq", "Nasdaq", "^IXIC", ""),
    ("dow", "Dow Jones", "^DJI", ""), ("cac40", "CAC 40", "^FCHI", ""),
    ("gold", "Or (once, $)", "GC=F", "$"), ("oil", "Pétrole WTI ($)", "CL=F", "$"),
    ("eurusd", "Euro / dollar", "EURUSD=X", ""), ("dxy", "Indice dollar", "DX-Y.NYB", ""),
    ("us10y", "Taux US 10 ans", "^TNX", "%"), ("vix", "VIX (volatilité)", "^VIX", ""),
)
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=1mo&interval=1d"
BINANCE_24H = "https://data-api.binance.vision/api/v3/ticker/24hr?symbols="
FALLBACK = {"gold": "PAXGUSDT", "eurusd": "EURUSDT"}     # Binance si Yahoo échoue

FNG_FR = {"extreme fear": "Peur extrême", "fear": "Peur", "neutral": "Neutre",
          "greed": "Avidité", "extreme greed": "Avidité extrême"}

TOPICS = (("hack", re.compile(r"\b(hack\w*|exploit\w*|piratage|pirat\w*|stolen|vol[ée]s?|drain\w*)\b", re.I)),
          ("régulation", re.compile(r"\b(SEC|AMF|MiCA|lawsuit|ban(s|ned)?|regulat\w*|r[ée]gulat\w*|"
                                    r"sanction\w*|plainte|interdi\w*)\b", re.I)),
          ("retrait", re.compile(r"\b(delist\w*|retrait de la cote)\b", re.I)),
          ("stablecoin", re.compile(r"\b(de-?peg\w*|stablecoin\w*|USDT|USDC|tether)\b", re.I)),
          ("macro", re.compile(r"\b(Fed|BCE|ECB|inflation|CPI|taux|rates?|recession|r[ée]cession|"
                               r"emploi|jobs|PIB|GDP|Powell|Lagarde)\b", re.I)),
          ("ETF", re.compile(r"\bETFs?\b")))
ALERT_TOPICS = {"hack", "retrait", "stablecoin", "régulation"}

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def http_bytes(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(MAX_BYTES)


def http_json(url: str, timeout: float = 20.0) -> Any:
    return json.loads(http_bytes(url, timeout))


def plain(text: str) -> str:
    """HTML d'un flux → texte brut (jamais interprété par le navigateur)."""
    return SPACE_RE.sub(" ", html.unescape(TAG_RE.sub(" ", text or ""))).strip()


def _when(raw: str) -> Optional[datetime]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_feed(xml_bytes: bytes, source: str, category: str, lang: str,
               universe: Tuple[str, ...] = ()) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    out: List[Dict[str, Any]] = []
    for it in root.iter("item"):
        title = plain(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        if not title or not link.startswith(("https://", "http://")):
            continue
        name = source
        src = it.find("source")
        if src is not None and (src.text or "").strip():
            name = src.text.strip()                  # Google Actualités : vrai média
            suffix = f" - {name}"
            if title.endswith(suffix):
                title = title[:-len(suffix)].strip()
        summary = plain(it.findtext("description") or "")
        if source == "Google Actualités" or summary.startswith(title[:40]):
            summary = ""                             # liens bruts, sans résumé
        if len(summary) > 420:
            summary = summary[:420].rsplit(" ", 1)[0] + "…"
        when = _when(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date") or "")
        text = f"{title} {summary}"
        topics = [t for t, rx in TOPICS if rx.search(text)]
        assets = [a for a in universe if mw.mentions(text, a)]
        # Alerte : le TITRE cite une crypto du bot ET un sujet sensible.
        alert = (any(mw.mentions(title, a) for a in assets)
                 and any(rx.search(title) for t, rx in TOPICS if t in ALERT_TOPICS))
        out.append({"title": title, "url": link, "source": name, "via": source,
                    "category": category, "lang": lang, "summary": summary,
                    "published": when.isoformat() if when else "",
                    "assets": assets, "topics": topics, "alert": alert})
    out.sort(key=lambda i: i["published"], reverse=True)
    return out[:PER_FEED]


def merge(items: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    """Plus récentes d'abord, sans doublons ni articles de plus de 48 h."""
    horizon = (now - timedelta(hours=KEEP_HOURS)).isoformat()
    future = (now + timedelta(hours=1)).isoformat()
    seen = set()
    out = []
    for it in sorted(items, key=lambda i: i["published"], reverse=True):
        key = re.sub(r"\W+", " ", it["title"].lower()).strip()[:90]
        if key in seen or (it["published"] and not horizon <= it["published"] <= future):
            continue
        seen.add(key)
        out.append(it)
    return out[:MAX_ITEMS]


def parse_yahoo(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Cours, variation du jour (clôture précédente = dernière clôture d'un
    jour antérieur) et clôtures du mois pour la mini-courbe."""
    res = ((data.get("chart") or {}).get("result") or [None])[0]
    if not res:
        return None
    meta = res.get("meta") or {}
    closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    pairs = [(int(t), float(c)) for t, c in zip(res.get("timestamp") or [], closes) if c is not None]
    if not pairs:
        return None
    price = float(meta.get("regularMarketPrice") or pairs[-1][1])
    at = int(meta.get("regularMarketTime") or pairs[-1][0])
    off = int(meta.get("gmtoffset") or 0)
    today = (at + off) // 86400
    prev = [c for t, c in pairs if (t + off) // 86400 < today]
    change = (price / prev[-1] - 1) * 100 if prev and prev[-1] else None
    return {"price": price, "change_pct": round(change, 2) if change is not None else None,
            "closes": [round(c, 6) for _t, c in pairs][-30:],
            "at": datetime.fromtimestamp(at, timezone.utc).isoformat()}


class NewsHub:
    """Cache des actualités et des marchés, rafraîchi en arrière-plan."""

    def __init__(self, universe: Tuple[str, ...] = (), fetch: Callable[[str], bytes] = http_bytes,
                 fetch_json: Callable[[str], Any] = http_json, background: bool = True,
                 clock: Callable[[], float] = time.time):
        self.universe = tuple(a.lower() for a in universe)
        self.fetch, self.fetch_json = fetch, fetch_json
        self.background = background
        self.clock = clock
        self._lock = threading.Lock()
        self._news: List[Dict[str, Any]] = []
        self._sources: List[Dict[str, Any]] = []
        self._markets: Dict[str, Any] = {}
        self._at = {"news": 0.0, "markets": 0.0}
        self._busy = {"news": False, "markets": False}

    # ---------- Rafraîchissement ----------

    def _refresh_news(self) -> None:
        now = datetime.now(timezone.utc)

        def one(feed: Tuple[str, str, str, str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
            name, cat, lang, url = feed
            try:
                items = parse_feed(self.fetch(url), name, cat, lang, self.universe)
                return items, {"name": name, "category": cat, "lang": lang, "ok": True,
                               "count": len(items)}
            except Exception as e:
                return [], {"name": name, "category": cat, "lang": lang, "ok": False,
                            "error": mw.friendly_error(e)}
        with ThreadPoolExecutor(max_workers=5) as ex:
            results = list(ex.map(one, FEEDS))
        items = [i for got, _s in results for i in got]
        with self._lock:
            if items or not self._news:                  # panne totale : on garde l'ancien
                self._news = merge(items, now)
            self._sources = [s for _i, s in results]

    def _refresh_markets(self) -> None:
        out: Dict[str, Any] = {"quotes": [], "errors": []}
        try:
            g = self.fetch_json("https://api.coingecko.com/api/v3/global")["data"]
            out["crypto"] = {
                "market_cap_usd": float(g["total_market_cap"]["usd"]),
                "market_cap_change_24h_pct": round(float(g.get("market_cap_change_percentage_24h_usd") or 0), 2),
                "volume_24h_usd": float(g["total_volume"]["usd"]),
                "btc_dominance_pct": round(float(g["market_cap_percentage"]["btc"]), 2),
                "eth_dominance_pct": round(float(g["market_cap_percentage"]["eth"]), 2),
                "active_cryptos": int(g.get("active_cryptocurrencies") or 0)}
        except Exception as e:
            out["errors"].append(f"CoinGecko : {mw.friendly_error(e)}")
        try:
            d = self.fetch_json("https://api.alternative.me/fng/?limit=30")["data"]
            out["fear_greed"] = {"value": int(d[0]["value"]),
                                 "label": FNG_FR.get(d[0]["value_classification"].lower(),
                                                     d[0]["value_classification"]),
                                 "history": [int(x["value"]) for x in reversed(d)]}
        except Exception as e:
            out["errors"].append(f"Peur & Avidité : {mw.friendly_error(e)}")

        def quote(q: Tuple[str, str, str, str]) -> Optional[Dict[str, Any]]:
            qid, name, sym, unit = q
            try:
                got = parse_yahoo(self.fetch_json(YAHOO_CHART.format(urllib.parse.quote(sym))))
            except Exception:
                got = None
            return dict(got, id=qid, name=name, unit=unit, source="Yahoo Finance") if got else None
        with ThreadPoolExecutor(max_workers=5) as ex:
            quotes = list(ex.map(quote, QUOTES))
        missing = [QUOTES[i][0] for i, q in enumerate(quotes) if q is None]
        fb = [FALLBACK[m] for m in missing if m in FALLBACK]
        if fb:
            try:
                rows = self.fetch_json(BINANCE_24H + urllib.parse.quote(json.dumps(fb, separators=(",", ":"))))
                by = {r["symbol"]: r for r in rows}
                for i, (qid, name, _sym, unit) in enumerate(QUOTES):
                    r = by.get(FALLBACK.get(qid, ""))
                    if quotes[i] is None and r:
                        quotes[i] = {"id": qid, "name": name, "unit": unit, "source": "Binance",
                                     "price": float(r["lastPrice"]),
                                     "change_pct": round(float(r["priceChangePercent"]), 2),
                                     "closes": [], "at": ""}
            except Exception as e:
                out["errors"].append(f"Binance : {mw.friendly_error(e)}")
        out["quotes"] = [q for q in quotes if q]
        if len(out["quotes"]) < len(QUOTES):
            out["errors"].append(f"Marchés financiers : {len(QUOTES) - len(out['quotes'])} "
                                 f"cours indisponibles")
        with self._lock:
            if out["quotes"] or out.get("crypto") or not self._markets:
                self._markets = out

    def _run(self, kind: str) -> None:
        try:
            (self._refresh_news if kind == "news" else self._refresh_markets)()
        finally:
            with self._lock:
                self._at[kind] = self.clock()
                self._busy[kind] = False

    def _ensure(self) -> None:
        todo = []
        with self._lock:
            now = self.clock()
            for kind, ttl, have in (("news", NEWS_TTL_SEC, self._news),
                                    ("markets", MARKETS_TTL_SEC, self._markets.get("quotes"))):
                # Rien obtenu (réseau coupé) : nouvel essai au bout d'une minute.
                if not self._busy[kind] and now - self._at[kind] >= (ttl if have else 60):
                    self._busy[kind] = True
                    todo.append(kind)
        for kind in todo:
            if self.background:
                threading.Thread(target=self._run, args=(kind,), daemon=True).start()
            else:
                self._run(kind)

    def snapshot(self) -> Dict[str, Any]:
        self._ensure()
        with self._lock:
            loading = self._busy["news"] and not self._news
            updated = self._at["news"]
            return {"items": list(self._news), "sources": list(self._sources),
                    "markets": dict(self._markets), "loading": loading,
                    "updated": (datetime.fromtimestamp(updated, timezone.utc).isoformat()
                                if updated else None)}
