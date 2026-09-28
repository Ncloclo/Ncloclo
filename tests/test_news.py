"""Actualités et marchés du panneau : lecture des flux RSS (texte brut,
liens sûrs, cryptos du bot, alertes), fusion, cours Yahoo et repli Binance,
cache tolérant aux pannes."""

import json
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import pytest

from panel import news

NOW = datetime.now(timezone.utc)
UNIVERSE = ("btc", "eth", "ada", "aave")


def rss(items, channel="Flux"):
    body = "".join(
        f"<item><title>{escape(t)}</title><link>{escape(link)}</link><pubDate>{d}</pubDate>"
        f"<description>{desc}</description>{extra}</item>"
        for t, link, d, desc, extra in items)
    return f'<?xml version="1.0"?><rss><channel><title>{channel}</title>{body}</channel></rss>'.encode()


def rfc(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def test_parse_feed_plain_text_safe_links_assets_and_alerts():
    xml = rss([
        ("Cardano hacked: exploit drains a bridge", "https://ex.com/1", rfc(NOW),
         "&lt;p&gt;An &lt;b&gt;exploit&lt;/b&gt; hit &lt;script&gt;x&lt;/script&gt; the bridge.&lt;/p&gt;", ""),
        ("Bitcoin ETF inflows jump", "https://ex.com/2", rfc(NOW - timedelta(hours=1)),
         "Ethereum also rises.", ""),
        ("Piège", "javascript:alert(1)", rfc(NOW), "", ""),
        ("La Fed maintient ses taux - Les Echos", "https://news.google.com/a", rfc(NOW),
         "&lt;a href='x'&gt;La Fed maintient ses taux&lt;/a&gt;", "<source url='https://x'>Les Echos</source>"),
    ])
    items = news.parse_feed(xml, "Test", "crypto", "en", UNIVERSE)
    assert len(items) == 3
    by = {i["url"]: i for i in items}
    assert "javascript:alert(1)" not in by                       # lien dangereux écarté
    hack = by["https://ex.com/1"]
    assert "<" not in hack["summary"] and "exploit" in hack["summary"]
    assert hack["assets"] == ["ada"] and "hack" in hack["topics"] and hack["alert"] is True
    etf = by["https://ex.com/2"]
    assert etf["assets"] == ["btc", "eth"] and "ETF" in etf["topics"] and etf["alert"] is False
    fed = by["https://news.google.com/a"]
    assert fed["source"] == "Les Echos" and fed["title"] == "La Fed maintient ses taux"
    assert "macro" in fed["topics"]


def test_parse_feed_keeps_the_most_recent_per_source():
    xml = rss([(f"Titre {k}", f"https://ex.com/{k}", rfc(NOW - timedelta(minutes=k)), "", "")
               for k in range(40)])
    items = news.parse_feed(xml, "Google Actualités", "crypto", "fr")
    assert len(items) == news.PER_FEED and items[0]["title"] == "Titre 0"
    assert all(i["summary"] == "" for i in items)                # agrégateur : pas de résumé


def test_merge_deduplicates_and_drops_old_items():
    mk = lambda t, h: {"title": t, "published": (NOW - timedelta(hours=h)).isoformat()}  # noqa: E731
    out = news.merge([mk("Bitcoin monte", 1), mk("Bitcoin  monte !", 2), mk("Vieux", 72),
                      mk("Récent", 0.5), {"title": "Sans date", "published": ""}], NOW)
    assert [i["title"] for i in out] == ["Récent", "Bitcoin monte", "Sans date"]


def yahoo(price, rows, at, off=-14400):
    return {"chart": {"result": [{
        "meta": {"regularMarketPrice": price, "regularMarketTime": at, "gmtoffset": off},
        "timestamp": [t for t, _c in rows], "indicators": {"quote": [{"close": [c for _t, c in rows]}]}}]}}


def test_parse_yahoo_daily_change_uses_previous_session_close():
    day = 86400
    t0 = 1_790_000_000 - 1_790_000_000 % day + 14 * 3600      # 14 h UTC
    rows = [(t0 - 2 * day, 100.0), (t0 - day, 110.0), (t0, None), (t0 + 60, 121.0)]
    q = news.parse_yahoo(yahoo(121.0, rows, t0 + 60))
    assert q["change_pct"] == pytest.approx(10.0)              # vs clôture de la veille (110)
    assert q["closes"] == [100.0, 110.0, 121.0]
    assert news.parse_yahoo({"chart": {"result": None}}) is None


class Net:
    def __init__(self):
        self.down = set()
        self.calls = []

    def _check(self, url):
        self.calls.append(url)
        if any(d in url for d in self.down):
            raise OSError("réseau coupé")

    def bytes(self, url):
        self._check(url)
        return rss([(f"Bitcoin news from {url}", "https://ex.com/" + str(len(self.calls)), rfc(NOW),
                     "Résumé.", "")])

    def json(self, url):
        self._check(url)
        if "coingecko" in url:
            return {"data": {"total_market_cap": {"usd": 3e12}, "total_volume": {"usd": 1e11},
                             "market_cap_percentage": {"btc": 58.0, "eth": 11.0},
                             "market_cap_change_percentage_24h_usd": -1.5,
                             "active_cryptocurrencies": 20000}}
        if "alternative.me" in url:
            return {"data": [{"value": "74", "value_classification": "Greed"},
                             {"value": "60", "value_classification": "Greed"}]}
        if "binance" in url:
            return [{"symbol": "PAXGUSDT", "lastPrice": "4300", "priceChangePercent": "0.5"},
                    {"symbol": "EURUSDT", "lastPrice": "1.14", "priceChangePercent": "-0.1"}]
        return yahoo(50.0, [(1_790_000_000 - 86400, 49.0), (1_790_000_000, 50.0)], 1_790_000_000)


def test_hub_collects_news_and_markets_with_fallbacks():
    net = Net()
    hub = news.NewsHub(UNIVERSE, fetch=net.bytes, fetch_json=net.json, background=False)
    net.down = {"decrypt.co", "cnbc.com/id/10000664"}
    snap = hub.snapshot()
    assert len(snap["items"]) == len(news.FEEDS) - 2 and not snap["loading"]
    bad = [s for s in snap["sources"] if not s["ok"]]
    assert len(bad) == 2 and all(s["error"] for s in bad)
    m = snap["markets"]
    assert m["crypto"]["btc_dominance_pct"] == 58.0 and m["fear_greed"]["label"] == "Avidité"
    assert m["fear_greed"]["history"] == [60, 74] and len(m["quotes"]) == len(news.QUOTES)
    assert all(q["source"] == "Yahoo Finance" and q["change_pct"] is not None for q in m["quotes"])


def test_hub_falls_back_to_binance_and_keeps_old_news_on_outage():
    net = Net()
    clock = [1000.0]
    hub = news.NewsHub(UNIVERSE, fetch=net.bytes, fetch_json=net.json, background=False,
                       clock=lambda: clock[0])
    net.down = {"yahoo.com"}
    first = hub.snapshot()
    q = {x["id"]: x for x in first["markets"]["quotes"]}
    assert set(q) == {"gold", "eurusd"} and q["gold"]["source"] == "Binance"
    assert any("indisponibles" in e for e in first["markets"]["errors"])
    net.down = {"://"}                                         # plus aucun réseau
    clock[0] += news.NEWS_TTL_SEC + 1
    again = hub.snapshot()
    assert again["items"] == first["items"]                    # anciennes actualités gardées
    assert all(not s["ok"] for s in again["sources"])
    n = len(net.calls)
    assert hub.snapshot() and len(net.calls) == n              # cache : aucun nouvel appel


def test_demo_news_is_marked_as_example():
    from panel.demo import DemoNews
    snap = DemoNews().snapshot()
    assert snap["items"] and all("exemple" in i["title"] for i in snap["items"])
    json.dumps(snap)
