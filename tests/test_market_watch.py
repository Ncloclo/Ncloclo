"""Veille de marché : veto officiel Binance (sans IA), IA en conseil
seulement, sources vérifiées, recoupement, apprentissage, pannes."""

import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from test_trendguard import DAY, N_DAYS, SIM_FROM, feed, make_bot, synthetic_market
from trendguard import diagnostics as dg
from trendguard import market_watch as mw
from trendguard import watch_claude

NOW = datetime(2026, 9, 27, 0, 5, tzinfo=timezone.utc)
UNIVERSE = ["btc", "eth", "aave", "algo", "xtz", "neo", "link"]


@pytest.fixture
def logger():
    lg = logging.getLogger("test.veille")
    lg.setLevel(logging.WARNING)
    return lg


@pytest.fixture
def memory(tmp_path):
    m = mw.WatchMemory(str(tmp_path / "veille.db"))
    yield m
    m.close()


# ---------- Annonces officielles (vrais titres Binance) ----------

def test_classify_real_binance_titles():
    c = mw.classify_announcement
    assert c("Binance Will Delist ICX, SCRT, STORJ on 2026-09-03") == ("delist", ["icx", "scrt", "storj"])
    assert c("Binance Will Delist ICX, SCRT, STORJ on 2026-09-03", "", ["icx", "btc"]) == ("delist", ["icx"])
    body = '{"text":"ENJ/USDC, NEO/USDT, ALGO/BTC, TNSR/USDC"}'
    assert c("Notice of Removal of Spot Trading Pairs - 2026-09-25", body) == ("spot_pairs", ["neo"])
    assert c("Binance Will Extend the Monitoring Tag to Include ACT, BLUR, PIVX & QKC on 2026-06-18") \
        == ("monitoring", ["act", "blur", "pivx", "qkc"])
    # Hors Spot ou sans retrait : aucun veto.
    for title in ("Binance Margin And Loan Will Delist BTTC & POWR on 2026-08-14",
                  "Binance Futures Will Delist USDⓈ-M AERGOUSDT Perpetual Contract (2026-07-24)",
                  "Binance Alpha Will Remove MTP, BDXN, TALE (2026-09-04)",
                  "Binance Will Close UAH Deposits and Withdrawals via Fiat Trade UAH and Delist "
                  "USDT/UAH Spot Trading Pair"):
        assert c(title)[0] == "other", title


def _cms(articles, bodies=None, calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        if "/list/query" in url:
            return {"data": {"catalogs": [{"articles": articles}]}}
        code = url.split("articleCode=")[1]
        return {"data": {"body": (bodies or {}).get(code, "")}}
    return fetch


def _art(code, title, days_ago):
    return {"code": code, "title": title,
            "releaseDate": int((NOW - timedelta(days=days_ago)).timestamp() * 1000)}


def test_official_vetoes_and_memory(memory):
    articles = [_art("a1", "Binance Will Delist ALGO, XTZ on 2026-10-05", 2),
                _art("a2", "Notice of Removal of Spot Trading Pairs - 2026-09-25", 3),
                _art("a3", "Binance Will Extend the Monitoring Tag to Include AAVE & QKC on 2026-09-20", 7),
                _art("a4", "Binance Will Delist ETH on 2026-01-10", 200)]       # trop ancien
    calls = []
    fetch = _cms(articles, {"a2": "NEO/USDT, LINK/BTC"}, calls)
    vetoes, monitoring = mw.refresh_official(UNIVERSE, NOW, memory, fetch)
    assert sorted(vetoes) == ["algo", "neo", "xtz"]           # LINK/BTC ne touche pas LINK/USDT
    assert vetoes["algo"]["until"] == (NOW - timedelta(days=2) + timedelta(days=90)).date().isoformat()
    assert "binance.com" in vetoes["neo"]["url"]
    assert [m["assets"] for m in monitoring] == [["aave"]]
    assert sum("detail" in u for u in calls) == 1
    calls.clear()
    assert mw.refresh_official(UNIVERSE, NOW, memory, fetch)[0] == vetoes
    assert not any("detail" in u for u in calls)                # annonce déjà en mémoire


# ---------- Le veto bloque les achats, ne vend jamais ----------

def _run(bot, fb, close, volume, first, last):
    feed(fb, close, volume)
    for d in close.index[first:last]:
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))


def test_veto_blocks_new_buys_but_never_sells(logger):
    close, volume = synthetic_market()
    ref, fb = make_bot("paper", close, logger)
    assert ref.boot()
    _run(ref, fb, close, volume, SIM_FROM, N_DAYS)
    bought = {t["asset"] for t in ref.state["trades"]} | set(ref.state["paper"]["holdings"])
    target = sorted(bought)[0]
    veto = {target: {"until": "2100-01-01", "reason": f"Binance retire {target.upper()}",
                     "url": "https://www.binance.com/x", "date": "2024-01-01"}}
    bot, fb2 = make_bot("paper", close, logger)
    assert bot.boot()
    bot.state["vetoes"] = veto
    _run(bot, fb2, close, volume, SIM_FROM, N_DAYS)
    assert target not in {t["asset"] for t in bot.state["trades"]}
    assert target not in bot.state["paper"]["holdings"]
    assert bot.state["trades"]                                  # les autres restent achetés
    # Crypto déjà détenue puis frappée d'un veto : le veto ne vend rien. Deux
    # bots identiques, l'un avec le veto, finissent avec les mêmes ventes.
    (va, fa), (vb, fbb) = make_bot("paper", close, logger), make_bot("paper", close, logger)
    assert va.boot() and vb.boot()
    for k in range(SIM_FROM, N_DAYS):
        _run(va, fa, close, volume, k, k + 1)
        _run(vb, fbb, close, volume, k, k + 1)
        if va.state["paper"]["holdings"]:
            break
    held = set(va.state["paper"]["holdings"])
    va.state["vetoes"] = {a: dict(veto[target]) for a in held}
    _run(va, fa, close, volume, k + 1, k + 40)
    _run(vb, fbb, close, volume, k + 1, k + 40)
    exits = lambda bt: [(t["asset"], t["date"]) for t in bt.state["trades"] if t["asset"] in held]  # noqa: E731
    assert exits(va) == exits(vb)


# ---------- Rapport des IA ----------

ITEMS_RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Hackers drain $50M from Aave lending pool</title><link>https://news.example/aave</link>
<pubDate>Sat, 26 Sep 2026 20:00:00 GMT</pubDate><source url="https://x">CoinDesk</source></item>
<item><title>Bitcoin climbs as ETF inflows return</title><link>https://news.example/btc</link>
<pubDate>Sat, 26 Sep 2026 18:00:00 GMT</pubDate></item>
<item><title>Bitcoin climbs as ETF inflows return</title><link>https://news.example/btc2</link>
<pubDate>Sat, 26 Sep 2026 17:00:00 GMT</pubDate></item>
<item><title>Old story about Chainlink</title><link>https://news.example/old</link>
<pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def _fetch_bytes(url):
    return ITEMS_RSS


def _fetch_json_quiet(url):
    if "alternative.me" in url:
        return {"data": [{"value": "70", "value_classification": "Greed"}]}
    if "ticker/price" in url:
        return {"price": "1.0002"}
    return {"data": {"catalogs": [{"articles": []}]}}


def _answer(events, sentiment=0.1, views=None):
    return json.dumps({"market_summary": "Marché calme, piratage d'Aave.",
                       "market_sentiment": sentiment, "events": events,
                       "asset_views": [{"asset": a, "sentiment": s, "reason": "r"}
                                       for a, s in (views or {}).items()]})


def test_news_are_fresh_and_deduplicated():
    items, errors = mw.collect_news(["aave"], NOW, _fetch_bytes)
    titles = [i.title for i in items]
    assert titles == ["Hackers drain $50M from Aave lending pool", "Bitcoin climbs as ETF inflows return"]
    assert items[0].source == "CoinDesk" and errors == []


def test_validate_keeps_only_verifiable_events():
    items, _ = mw.collect_news([], NOW, _fetch_bytes)
    raw = {"market_summary": "x", "market_sentiment": 7, "asset_views": [
        {"asset": "AAVE", "sentiment": -3, "reason": ""}, {"asset": "sol", "sentiment": 1, "reason": ""}],
        "events": [
            {"asset": "aave", "category": "hack", "severity": 7, "sentiment": -5, "summary": "ok", "sources": ["#1"]},
            {"asset": "eth", "category": "hack", "severity": 3, "sentiment": -1, "summary": "faux", "sources": ["#1"]},
            {"asset": "sol", "category": "hack", "severity": 3, "sentiment": -1, "summary": "hors liste", "sources": ["#1"]},
            {"asset": "aave", "category": "rugpull", "severity": 2, "sentiment": -1, "summary": "url inventée",
             "sources": ["https://evil.example/x"]},
            {"asset": "market", "category": "macro", "severity": 1, "sentiment": 0.3, "summary": "etf", "sources": ["#2"]}]}
    v = mw.validate(raw, items, UNIVERSE, set())
    assert [(e["asset"], e["category"]) for e in v["events"]] == [("aave", "hack"), ("market", "macro")]
    assert v["events"][0]["severity"] == 3 and v["events"][0]["sentiment"] == -1
    assert v["sentiment"] == 1 and v["views"] == {"aave": -1}
    # Une IA qui cherche sur le web peut citer les pages qu'elle a consultées.
    web = mw.validate(raw, items, UNIVERSE, {"https://evil.example/x"})
    assert ("aave", "other") in [(e["asset"], e["category"]) for e in web["events"]]
    # Une source citée par son URL (plutôt que « #n ») doit nommer la
    # crypto, comme une citation numérotée : citer l'URL d'un article sur
    # Bitcoin pour justifier un événement AAVE est écarté.
    off_topic = {"market_summary": "x", "market_sentiment": 0, "events": [
        {"asset": "aave", "category": "hack", "severity": 1, "sentiment": -1,
         "summary": "hors sujet", "sources": ["https://news.example/btc"]}]}
    assert mw.validate(off_topic, items, UNIVERSE, set())["events"] == []


def test_alert_needs_two_ais_and_ai_never_vetoes(memory):
    hack = {"asset": "aave", "category": "hack", "severity": 3, "sentiment": -0.9,
            "summary": "Piratage du protocole Aave", "sources": ["#1"]}
    # Une seule IA prétend que Binance retire ETH (manipulation) : sans effet.
    fake_delist = {"asset": "eth", "category": "delisting", "severity": 3, "sentiment": -1,
                   "summary": "Binance retire ETH", "sources": ["#2"]}
    answers = {"openai": _answer([hack], views={"aave": -0.8}),
               "deepseek": _answer([hack], views={"aave": -0.6}),
               "mistral": _answer([fake_delist])}

    def call(p, key, model, system, prompt):
        assert "<<<DONNEES" in prompt and "Hackers drain" in prompt
        return answers[p.name], set()
    env = {"OPENAI_API_KEY": "k" * 20, "DEEPSEEK_API_KEY": "k" * 20, "MISTRAL_API_KEY": "k" * 20}
    rep = mw.daily_report(UNIVERSE, ["aave"], NOW, memory, env=env, fetch_json=_fetch_json_quiet,
                          fetch_bytes=_fetch_bytes, call=call)
    assert rep["vetoes"] == {}                                  # aucune IA ne pose de veto
    assert rep["consensus"]["providers"] == 3
    texts = " | ".join(a["text"] for a in rep["alerts"])
    assert "AAVE · hack" in texts and "confirmé par 2 IA" in texts
    assert "ETH" not in texts                                   # une seule IA : pas d'alerte (et source hors sujet)
    assert rep["consensus"]["views"]["aave"] == pytest.approx(-0.7)
    assert memory.recent_reports("2026-09-28")[0]["day"] == "2026-09-27"
    text = mw.render(rep)
    assert "3 IA" in text and "aucune IA ne passe d'ordre" in text


def test_failing_ais_never_block_the_others(memory):
    def call(p, key, model, system, prompt):
        if p.name == "openai":
            raise mw.HttpError(401, "invalid key")
        if p.name == "gemini":
            return "Désolé, je ne peux pas.", set()
        if p.name == "grok":
            raise mw.HttpError(404, "model not found")
        return _answer([]), set()
    env = {k: "k" * 20 for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY", "DEEPSEEK_API_KEY")}
    rep = mw.daily_report(UNIVERSE, [], NOW, memory, env=env, fetch_json=_fetch_json_quiet,
                          fetch_bytes=_fetch_bytes, call=call)
    prov = rep["providers"]
    assert prov["openai"]["error"] == "clé API refusée"
    assert "aucun objet JSON" in prov["gemini"]["error"]
    assert "modèle" in prov["grok"]["error"]
    assert prov["deepseek"]["ok"] and rep["consensus"]["providers"] == 1


def test_without_ai_keyword_signals_only(memory):
    rep = mw.daily_report(UNIVERSE, [], NOW, memory, env={}, fetch_json=_fetch_json_quiet,
                          fetch_bytes=_fetch_bytes)
    assert rep["consensus"]["providers"] == 0
    assert [(e["asset"], e["category"]) for e in rep["consensus"]["events"]] == [("aave", "hack")]
    assert not [a for a in rep["alerts"] if "hack" in a["text"]]   # mots-clés : jamais d'alerte


def test_ai_weights_follow_measured_reliability():
    idx = pd.date_range("2026-01-01", periods=80, freq="D", tz="UTC")
    close = pd.DataFrame({"eth": np.linspace(100, 200, 80)}, index=idx)   # hausse régulière
    days = [str(d.date()) for d in idx[:60]]
    opinions = ([(d, "good", "eth", 0.6) for d in days] + [(d, "bad", "eth", -0.6) for d in days]
                + [(d, "new", "eth", 0.6) for d in days[:10]])
    w = mw.provider_weights(opinions, close)
    assert w["good"]["weight"] == 2.0 and w["good"]["hit_rate"] == 1.0
    assert w["bad"]["weight"] == 0.25
    assert w["new"]["weight"] == 1.0                            # trop peu d'avis vérifiés
    results = {n: {"ok": True, "data": {"sentiment": s, "summary": n, "events": [], "views": {}}}
               for n, s in (("good", 0.8), ("bad", -0.8))}
    assert mw.consensus(results, w)["sentiment"] > 0.5


# ---------- Claude : SDK officiel, sortie structurée, repli serveur ----------

def test_claude_call_uses_structured_output_and_fallbacks():
    seen = {}

    class Client:
        def __init__(self, stop="end_turn"):
            self.stop = stop
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

        def create(self, **kw):
            seen.update(kw)
            return SimpleNamespace(stop_reason=self.stop, stop_details=SimpleNamespace(category="cyber"),
                                   content=[SimpleNamespace(type="text", text='{"ok": 1}')])
    text = watch_claude.ask_claude("sys", "prompt", mw.SCHEMA, "key",
                                   client_factory=lambda k, t: Client())
    assert text == '{"ok": 1}'
    assert seen["model"] == "claude-opus-5" and seen["fallbacks"] == "default"
    assert seen["betas"] == ["server-side-fallback-2026-07-01"]
    assert seen["output_config"]["format"] == {"type": "json_schema", "schema": mw.SCHEMA}
    with pytest.raises(RuntimeError, match="refus"):
        watch_claude.ask_claude("s", "p", mw.SCHEMA, "k", client_factory=lambda k, t: Client("refusal"))


def test_extract_json_tolerates_fences():
    assert mw.extract_json('Voici :\n```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        mw.extract_json("pas de JSON")


# ---------- Intégration au bot ----------

def test_watch_failures_never_block_the_daily_decision(logger, tmp_path, monkeypatch):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, watch=True, watch_ai=True,
                       watch_db=str(tmp_path / "veille.db"))
    assert bot.boot()

    def boom(*a, **k):
        raise mw.HttpError(503, "indisponible")
    monkeypatch.setattr(mw, "refresh_official", boom)
    monkeypatch.setattr(mw, "daily_report", boom)
    bot.state["vetoes"] = {"xrp": {"until": "2100-01-01", "reason": "ancien veto", "url": "u", "date": "d"}}
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 1)
    assert bot.state["last_decision_day"] == str(close.index[SIM_FROM].date())
    assert "xrp" in bot.state["vetoes"]                         # vetos précédents conservés


def test_bot_stores_vetoes_and_daily_report(logger, tmp_path, monkeypatch):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, watch=True, watch_ai=True,
                       watch_db=str(tmp_path / "veille.db"))
    assert bot.boot()
    monkeypatch.setattr(mw, "refresh_official", lambda u, now, memory, **k: (
        {"doge": {"until": "2100-01-01", "reason": "Binance retire DOGE", "url": "u", "date": "d"}}, []))
    monkeypatch.setattr(mw, "daily_report", lambda *a, **k: {
        "day": "x", "consensus": {"sentiment": -0.3, "providers": 2, "events": [], "summary": "s", "views": {}},
        "providers": {"openai": {"ok": True}, "claude": {"ok": True}}, "alerts": [], "vetoes": {},
        "monitoring": [], "indicators": {}, "items": 0, "errors": [], "weights": {}})
    monkeypatch.setattr(mw, "render", lambda r: "rapport")
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 2)
    assert "doge" in bot.state["vetoes"] and not bot._can_enter("doge")
    assert bot.state["last_watch"]["providers"] == 2
    f = dg.check_watch(bot.state, ["doge"], "2026-09-27")
    assert f[0].level == "ALERTE" and "DOGE" in f[0].message


def test_set_key_is_masked_and_checked(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("RUN_MODE=paper\n", encoding="utf-8")
    key = "sk-" + "A1b2" * 10
    assert mw.cmd_set_key("openai", str(env), ask=lambda _p: key) == 0
    assert f"OPENAI_API_KEY={key}" in env.read_text(encoding="utf-8")
    assert key not in capsys.readouterr().out
    assert mw.cmd_set_key("openai", str(env), ask=lambda _p: "trop court") == 1
    assert mw.cmd_set_key("inconnue", str(env), ask=lambda _p: key) == 2
