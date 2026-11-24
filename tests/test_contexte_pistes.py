"""Agents qui informent le comité sans voter (macro, dérivés, flux de
stablecoins) et pistes de recherche testées par le bot lui-même
(trendguard/flux.py, derives.latest, comite.py, pistes.py)."""

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from trendguard import autonomy, bibliotheque, comite, derives, flux, pistes
from trendguard.bot_routines import RoutinesMixin

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


class _Memory:
    def __init__(self):
        self.meta = {}

    def get(self, key, default=None):
        return self.meta.get(key, default)

    def put(self, key, value):
        self.meta[key] = value


def _llama(values):
    """Réponse de DefiLlama : un point par jour, le plus ancien d'abord."""
    start = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
    return json.dumps([{"date": str(start + i * 86400), "totalCirculating": {"peggedUSD": v, "peggedEUR": 1.0}}
                       for i, v in enumerate(values)] + [{"date": "x", "totalCirculating": {}}]).encode()


def test_stablecoin_supply_is_read_once_a_day_and_described():
    memory, calls = _Memory(), []
    values = [300e9] * 31 + [285e9]          # −5 % sur 30 jours

    def fetch(url):
        calls.append(url)
        return _llama(values)
    book = flux.refresh(memory, NOW, fetch)
    assert calls == [flux.FEED] and len(book["series"]) == 32 and book["error"] == ""
    flux.refresh(memory, NOW.replace(hour=20), fetch)
    assert len(calls) == 1                                                   # une fois par jour
    v = flux.view(book)
    assert v["usd"] == 285e9 and round(v["change_30d"], 3) == -0.05
    assert "argent qui sort des cryptos" in flux.describe(v) and flux.describe({}) is None

    def down(url):
        raise TimeoutError("timed out")
    book = flux.refresh(memory, NOW.replace(day=26), down)
    assert book["error"] == "délai dépassé" and len(book["series"]) == 32    # rien de perdu


def test_latest_futures_indicators_come_from_the_last_watch_report(tmp_path):
    db = tmp_path / "veille avec espace.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE reports (day TEXT PRIMARY KEY, generated TEXT, report TEXT)")
    for day, rate in (("2026-11-23", 0.0001), ("2026-11-24", 0.0004)):
        con.execute("INSERT INTO reports VALUES (?,?,?)",
                    (day, day, json.dumps({"indicators": {"derives": {"funding": {"btc": rate}, "funding_btc": rate}}})))
    con.commit()
    con.close()
    d = derives.latest(str(db))
    assert d["day"] == "2026-11-24" and d["funding_btc"] == 0.0004
    assert derives.latest(str(tmp_path / "absent.db")) == {} and derives.latest("") == {}


def test_context_agents_inform_without_voting():
    by_id = {a.manifest.agent_id: a for a in comite.AGENTS}
    for name in ("macro", "derives", "flux", "chercheur"):
        assert by_id[name].manifest.weight == 0.0 and not by_id[name].manifest.veto
    board = {"marche": {"asset": "eth"}, "macro": {"upcoming": [{"label": "Fed"}], "line": "compte rendu de la Fed"},
             "derives": {"funding": {"eth": 0.0004}, "funding_btc": 0.0001, "oi_btc_30d": 0.12},
             "flux": {"usd": 285e9, "change_30d": -0.05}}
    view = lambda agent: comite.View(board, by_id[agent].manifest)          # noqa: E731
    macro = comite.a_macro(view("macro"))
    assert macro.score == 0 and macro.evidence == ("compte rendu de la Fed",) and macro.uncertainties
    der = comite.a_derives(view("derives"))
    assert der.score == 0 and "levier acheteur élevé" in der.evidence[0]       # 0,04 % par 8 h : +43,8 % par an
    assert "intérêt ouvert BTC +12,0 %" in der.evidence[1]
    assert "argent qui sort" in comite.a_flux(view("flux")).evidence[0]
    empty = {"marche": {"asset": "eth"}}
    assert comite.a_macro(comite.View(empty, by_id["macro"].manifest)).evidence == (
        "aucune grande annonce américaine dans les 48 heures",)
    assert comite.a_derives(comite.View(empty, by_id["derives"].manifest)).evidence == (
        "contrats à terme : pas de relevé récent",)


def test_a_variant_is_only_proposed_when_it_wins_on_both_periods():
    ref = [1.6, 0.9]
    rows = [{"label": "récente seulement", "calmar": [1.2, 1.3]},
            {"label": "partout", "calmar": [1.7, 1.0]}]
    v = pistes.verdict("tendance", ref, rows)
    assert v["status"] == "a_etudier" and "« partout »" in v["text"] and "Jamais appliqué seul" in v["text"]
    v = pistes.verdict("tendance", ref, rows[:1])
    assert v["status"] == "tient" and "la règle actuelle tient" in v["text"]
    assert pistes.verdict("couts", ref, [{"label": "coûts doublés", "calmar": [1.2, 0.5]}])["status"] == "tient"
    assert pistes.verdict("couts", ref, [{"label": "coûts doublés", "calmar": [0.4, -0.1]}])["status"] == "fragile"


def test_every_declared_variant_is_tested_and_stored(monkeypatch):
    seen = []

    def calmar(close, volume, p, start, end):
        seen.append((p.breakout_n, p.mom_n, p.init_stop_atr, p.regime_sma, p.fee, start))
        return 1.0
    monkeypatch.setattr(pistes, "_calmar", calmar)
    import pandas as pd
    close = pd.DataFrame({"btc": [1.0]}, index=pd.DatetimeIndex(["2026-11-23"], tz="UTC"))
    results = pistes.evaluate(close, close)
    variants = sum(len(v) for v in pistes.FAMILIES.values())
    assert len(seen) == 2 * (1 + variants) and set(results) == set(pistes.FAMILIES)
    memory = _Memory()
    book = pistes.store(memory, results, "2026-11-24")
    assert set(book["verdicts"]) == set(pistes.FAMILIES) | set(pistes.STANDING)
    assert not pistes.due(book, "2026-11-30") and pistes.due(book, "2026-12-01")
    # Les pistes de la bibliothèque portent le verdict de leur thème.
    lib = {"papers": [{"id": "2611.00001v1", "title": "Trend following in crypto", "published": "2026-11-20",
                       "link": "https://arxiv.org/abs/2611.00001v1", "score": 5, "themes": ["tendance", "crypto"]}]}
    monkeypatch.setattr(bibliotheque, "datetime", SimpleNamespace(now=lambda tz=None: NOW))
    row = bibliotheque.pistes(lib, verdicts=book)[0]
    assert row["status"] == "tient" and row["verdict"].startswith("la règle actuelle tient")


def test_the_bot_tests_its_research_leads_once_a_week(monkeypatch):
    launched = []
    monkeypatch.setattr(autonomy, "launch_tool", lambda g, args, ext: launched.append(args) or True)
    bot = SimpleNamespace(g=SimpleNamespace(savoir=True), track_uptime=True, state={},
                          logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None),
                          PISTES_MINUTE=RoutinesMixin.PISTES_MINUTE)
    run = lambda when: RoutinesMixin._launch_pistes(bot, when)              # noqa: E731
    run(NOW.replace(hour=0, minute=30))                                      # avant 01:15 UTC : rien
    run(NOW)
    run(NOW.replace(hour=23))                                                # même jour : rien de plus
    run(NOW.replace(day=29))                                                 # moins d'une semaine
    assert launched == [["bibliotheque", "tester"]]
    run(datetime(2026, 12, 1, 9, tzinfo=timezone.utc))                      # une semaine plus tard
    assert len(launched) == 2
