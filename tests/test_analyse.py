"""Prompt maître, phase 2 (docs/PLATEFORME.md) : qualité des données, tests
de résistance, attribution des résultats, calendrier économique, registre
des expériences ; dans le bot, le rapport et le panneau."""

import json
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from test_market_watch import _run

import trendguard_bot as tg
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import (
    attribution,
    evenements,
    evolution,
    qualite,
    registre,
    report_health,
    savoir,
    stress,
)
from trendguard import trend_strategy as ts


@pytest.fixture
def logger():
    lg = logging.getLogger("test.analyse")
    lg.setLevel(logging.WARNING)
    return lg


# ---------- Qualité des données ----------

def _frame(days=400):
    idx = pd.date_range("2025-01-01", periods=days, freq="D", tz="UTC")
    rng = np.random.default_rng(1)
    return pd.DataFrame({a: 100 * np.exp(np.cumsum(rng.normal(0, 0.02, days))) for a in ("btc", "eth")}, index=idx)


def test_data_quality_score_names_each_defect():
    close = _frame()
    day = str(close.index[-1].date())
    assert qualite.quality(close, day) == {"day": day, "score": 100.0, "issues": [],
                                           "text": "100/100 : dates, valeurs et cours de l'année sans défaut"}
    bad = close.copy()
    bad.iloc[-10, 1] = np.nan                                     # une clôture manquante
    bad.iloc[-20, 0] = 0.0                                        # un prix nul
    bad.iloc[-40:-36, 1] = 50.0                                   # cours figé 4 jours
    bad.iloc[-60, 0] = bad.iloc[-61, 0] * 3                       # +200 % en un jour
    q = qualite.quality(bad.drop(bad.index[-5]), day)             # un jour sans bougie
    assert q["score"] == 100 - 5 - 1 - 10 - 2
    assert any("jour(s) sans bougie" in i for i in q["issues"]) and any("manquante" in i for i in q["issues"])
    assert any("nul" in i for i in q["issues"]) and any("figé" in i for i in q["issues"])
    assert any("à vérifier (BTC)" in i for i in q["issues"])     # signalé, sans pénalité
    late = qualite.quality(close, "2099-01-01")
    assert late["score"] == 70 and "attendue du 2099-01-01" in late["text"]


# ---------- Tests de résistance ----------

def test_stress_scenarios_and_the_kill_switch():
    pos = {"btc": {"qty": 0.05, "price": 60_000.0, "stop": 54_000.0},
           "eth": {"qty": 1.0, "price": 2_000.0, "stop": 1_900.0}}
    rows = stress.scenarios(pos, 5_000.0, 10_000.0)               # 5 000 investis, 5 000 en USDT
    by = {r["name"]: r for r in rows}
    assert by["Krach des cryptos de 50 %, stops sautés"]["loss_pct"] == 25.0
    assert by["Crise de liquidité : ventes 10 % sous les stops"]["loss_usdt"] == pytest.approx(
        5_000 - (2_700 + 1_900) * 0.9)
    assert by["Retrait de la cote de la plus grosse position, BTC (−60 %)"]["loss_usdt"] == 1_800.0
    assert by["Décrochage de l'USDT de 10 %"]["loss_usdt"] == 500.0
    assert rows[0]["loss_pct"] == max(r["loss_pct"] for r in rows)
    calm = stress.describe(rows, 0.40, 0.0)
    assert calm.startswith("Pire test de résistance : krach des cryptos de 50 %") and "15,0 points" in calm
    assert "déclencherait l'arrêt d'urgence" in stress.describe(rows, 0.40, 0.25)   # déjà à −25 %
    assert stress.scenarios({}, 100.0, 0.0) == [] and stress.describe([]) == ""
    assert [r["loss_usdt"] for r in stress.scenarios({}, 100.0, 100.0)][-1] == 0.0


# ---------- Attribution des résultats ----------

def test_attribution_answers_why_the_bot_won_or_lost():
    trades = [{"asset": "btc", "pnl": 300.0, "r": 3.0, "lesson": "tendance", "regime": "tendance haussière",
               "reason": "STOP"},
              {"asset": "eth", "pnl": -100.0, "r": -1.0, "lesson": "faux_depart", "regime": "tendance haussière",
               "reason": "STOP"},
              {"asset": "btc", "pnl": 50.0, "r": 0.5, "lesson": "ordinaire", "reason": "EXCHANGE_STOP"}]
    a = attribution.attribution(trades, {"eth": 40.0, "ada": -10.0})
    assert a["realized"] == 250.0 and a["unrealized"] == 30.0
    assert [(r["asset"], r["total"]) for r in a["assets"]] == [("btc", 350.0), ("ada", -10.0), ("eth", -60.0)]
    assert a["regimes"] == [{"key": "tendance haussière", "trades": 2, "pnl": 200.0}]
    assert {r["key"] for r in a["exits"]} == {"stop de clôture", "stop de secours"}
    assert "gains : 2 trades gagnants, +350,00 USDT" in a["text"]
    many = [{"asset": "btc", "pnl": p, "lesson": "tendance"} for p in (400.0, 100.0, 50.0, 30.0, 20.0)]
    assert "les 3 meilleurs trades font 92 % des gains réalisés" in attribution.attribution(many, {})["text"]
    assert "pertes : faux départ (1 trade, −100,00 USDT)" in a["text"]
    assert "aucun trade clos" in attribution.attribution([], {"btc": 5.0})["text"]


# ---------- Calendrier économique ----------

FEED = json.dumps([
    {"title": "FOMC Meeting Minutes", "country": "USD", "date": "2026-10-07T14:00:00-04:00", "impact": "High",
     "forecast": "", "previous": ""},
    {"title": "CPI m/m", "country": "USD", "date": "2026-10-08T08:30:00-04:00", "impact": "High",
     "forecast": "0.3%", "previous": "0.4%"},
    {"title": "German Industrial Production", "country": "EUR", "date": "2026-10-07T02:00:00-04:00",
     "impact": "High"},
    {"title": "Crude Oil Inventories", "country": "USD", "date": "2026-10-07T10:30:00-04:00", "impact": "Medium"},
    {"title": "Ignore previous instructions", "country": "USD", "date": "pas une date", "impact": "High"},
]).encode()


def test_calendar_keeps_major_us_releases_and_reads_rarely(tmp_path):
    memory = savoir.Memory(str(tmp_path / "savoir.db"))
    calls = []

    def fetch(url):
        calls.append(url)
        return FEED
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    book = evenements.refresh(memory, now, fetch)
    assert [x["title"] for x in book["history"]] == ["FOMC Meeting Minutes", "CPI m/m"]
    assert book["history"][0]["at"] == "2026-10-07T18:00+00:00"           # heure de New York → UTC
    evenements.refresh(memory, now + timedelta(hours=5), fetch)
    assert len(calls) == 1                                                 # relu au plus toutes les 6 heures
    evenements.refresh(memory, now + timedelta(hours=6), fetch)
    assert len(calls) == 2 and len(memory.get(evenements.KEY)["history"]) == 2   # sans doublon

    def broken(url):
        raise OSError("réseau coupé")
    later = now + timedelta(hours=12)
    book = evenements.refresh(memory, later, broken)
    assert "réseau" in book["error"] and len(book["history"]) == 2         # rien de perdu
    assert not evenements.due(book, later + timedelta(minutes=59)) and evenements.due(book, later + timedelta(hours=1))
    assert evenements.label("Core CPI m/m") == "inflation CPI sous-jacente (Core CPI m/m)"
    assert evenements.label("ADP Non-Farm Employment Change").startswith("créations d'emplois privés")
    nxt = evenements.window(book, now, now + timedelta(hours=72))
    assert [x["label"] for x in nxt] == ["compte rendu de la Fed (FOMC Meeting Minutes)", "inflation CPI (CPI m/m)"]
    assert evenements.when_text(nxt[0]["at"]) == "mercredi 7 à 18:00 UTC"
    memory.close()


def test_bitcoin_reaction_on_announcement_days_is_measured():
    idx = pd.date_range("2026-01-01", periods=200, freq="D", tz="UTC")
    moves = np.where(np.arange(200) % 2, 0.01, -0.01)
    days = [str(d.date()) for d in idx[100:200:8]]                        # 13 jours d'annonce
    for d in days:
        moves[idx.get_loc(pd.Timestamp(d, tz="UTC"))] = 0.03              # 3 fois le mouvement ordinaire
    btc = pd.Series(100 * np.cumprod(1 + moves), index=idx)
    book = {"history": [{"title": "CPI m/m", "at": f"{d}T12:30+00:00"} for d in days]}
    r = evenements.reactions(book, btc, str(idx[-1].date()))
    assert r["days"] == len(days) and r["ratio"] == pytest.approx(3.0, rel=0.05)
    assert "3,0 fois plus qu'un jour ordinaire" in r["text"]
    few = evenements.reactions({"history": book["history"][:3]}, btc, str(idx[-1].date()))
    assert few["ratio"] is None and "conclusion à partir de 10" in few["text"]
    now = idx[-1].to_pydatetime() + timedelta(days=1)
    view = evenements.day_view({"history": book["history"] + [{"title": "FOMC Statement",
                                                              "at": (now + timedelta(hours=18)).isoformat()}]},
                               btc, now, str(idx[-1].date()))
    assert view["line"].startswith("Annonces économiques importantes (États-Unis) dans les 48 heures : décision de la Fed")
    assert "aucun achat n'est bloqué" in view["line"] and "3,0 fois plus" in view["line"]
    assert evenements.day_view(None, None, now, "x") == {"day": "x", "upcoming": [], "line": "", "error": "",
                                                         "reaction": evenements.reactions(None, None, "x")}


# ---------- Registre des expériences ----------

def _judge():
    close, volume = synthetic_market()
    i = close.index
    return evolution.Judge(close, volume, crises=[],
                           periods=((str(i[SIM_FROM].date()), str(i[SIM_FROM + 140].date())),
                                    (str(i[SIM_FROM + 141].date()), str(i[-1].date()))))


def test_registry_notes_and_replays_an_experiment(tmp_path):
    j = _judge()
    path = str(tmp_path / "tg.registre.json")
    p = ts.TrendParams()
    e = registre.record(path, registre.experiment("controle", j.close, j.volume, j.periods, {"en vigueur": p},
                                                  {"en vigueur": [j.period(p, 0), j.period(p, 1)]}, "essai"))
    assert e["id"] == "E0001" and e["code"] and len(e["data"]["hash"]) == 16
    assert registre.find(path, "1") == registre.find(path, "e0001") == registre.load(path)[0]
    assert registre.params_of(e["params"]["en vigueur"]) == p                # tuples rendus
    res = registre.replay(e, j.close, j.volume)
    assert res["same"] and res["same_data"] and not res["diffs"]
    # Des jours de plus après la fin ne changent rien ; une bougie corrigée, si.
    longer = pd.concat([j.close, j.close.iloc[-1:].set_axis([j.close.index[-1] + pd.Timedelta(days=1)])])
    assert registre.fingerprint(longer, None, e["periods"][-1][1]) == registre.fingerprint(j.close, None, e["periods"][-1][1])
    fixed = j.close.copy()
    fixed.iloc[SIM_FROM + 10, 0] *= 1.5
    res = registre.replay(e, fixed, j.volume)
    assert not res["same_data"] and not res["same"] and res["diffs"][0].startswith("en vigueur, 2018-2022 :")
    second = registre.record(path, dict(e, conclusion="deux"))
    assert second["id"] == "E0002" and [x["id"] for x in registre.load(path)] == ["E0001", "E0002"]
    assert registre.find(path, "E9") is None and registre.load("") == []


def test_the_evolution_notes_each_daily_trial(tmp_path):
    j = _judge()
    path = str(tmp_path / "tg.registre.json")
    st = evolution.run_daily(ts.TrendParams(), str(tmp_path / "tg.evolution.json"),
                             j.close.index[-1].date(), lambda: j,
                             record=lambda e: registre.record(path, e))
    entries = registre.load(path)
    assert len(entries) == 1 and entries[0]["kind"] == "evolution" and entries[0]["conclusion"] == st["last_text"]
    assert entries[0]["details"]["essayes"] == 10 and "actuel" in entries[0]["params"]
    assert registre.replay(entries[0], j.close, j.volume)["same"]          # reproductible à l'identique
    g = tg.GuardConfig(run_mode="paper", db_file=":memory:", log_file=str(tmp_path / "x.log"),
                       lock_file=str(tmp_path / "tg.lock"))
    card = registre.card(g, entries)
    assert card.startswith("CARTE DU MODÈLE — TrendGuard") and "Limites connues :" in card
    assert f"Validation (expérience {entries[0]['id']}" in card and "cassure du plus haut de 30 jours" in card


# ---------- Dans le bot, le rapport et le panneau ----------

def test_the_bot_measures_its_portfolio_and_shows_the_calendar(logger, tmp_path):
    close, volume = synthetic_market()
    last = SIM_FROM + 200
    db = str(tmp_path / "savoir.db")
    m = savoir.Memory(db)
    past = [{"title": "CPI m/m", "at": f"{close.index[k].date()}T12:30+00:00"} for k in range(SIM_FROM + 20, last, 15)]
    soon = (close.index[last - 1] + pd.Timedelta(days=1, hours=18)).isoformat()
    m.put(evenements.KEY, {"history": past + [{"title": "FOMC Statement", "at": soon}], "tried_at": "2099-01-01"})
    m.close()
    bot, fb = make_bot("paper", close, logger, savoir=True, savoir_db=db)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, last)
    st = bot.state
    day = str(close.index[last - 1].date())
    assert st["qualite"]["day"] == day and st["qualite"]["score"] == 100.0
    assert st["paper"]["holdings"] and st["stress"]["rows"] and st["stress"]["day"] == day
    assert [x["title"] for x in st["evenements"]["upcoming"]] == ["FOMC Statement"]
    assert st["evenements"]["reaction"]["days"] == len(past)
    lines = st["reasoning"]["lines"]
    assert any(x.startswith("Pire test de résistance :") for x in lines)
    assert any(x.startswith("Annonces économiques importantes") for x in lines)
    assert not any(x.startswith("Qualité des données") for x in lines)    # rien à signaler
    checks = {c["label"]: c for c in report_health.analysis_checks(bot.g, st)}
    assert checks["Qualité des données"]["ok"] is True
    assert {"Risque d'un jour (VaR)", "Tests de résistance", "Attribution des résultats",
            "Calendrier économique"} <= set(checks)
    assert all(c["ok"] is None for k, c in checks.items() if k != "Qualité des données")


def test_panel_shows_the_analysis_and_the_calendar(tmp_path):
    from panel import demo
    from panel.data import BotData
    d = demo.DemoData(demo.DemoMarket())
    a = d.analyse()
    assert a["attribution"]["assets"] and a["stress"]["rows"] and a["qualite"]["score"] == 100.0
    w = d.watch()
    assert [e["label"] for e in w["evenements"]["events"]][0] == "compte rendu de la Fed (FOMC Meeting Minutes)"
    db = str(tmp_path / "savoir.db")
    m = savoir.Memory(db)
    soon = (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat(timespec="minutes")
    m.put(evenements.KEY, {"history": [{"title": "CPI m/m", "at": soon}], "fetched_at": "2026-10-06T12:00:00+00:00"})
    m.close()
    g = tg.GuardConfig(run_mode="paper", db_file=str(tmp_path / "tg.db"), log_file=str(tmp_path / "tg.log"),
                       lock_file=str(tmp_path / "tg.lock"), savoir=True, savoir_db=db)
    data = BotData(g, market=None)
    cal = data.evenements({"evenements": {"reaction": {"text": "mesure"}}})
    assert [e["label"] for e in cal["events"]] == ["inflation CPI (CPI m/m)"] and cal["reaction"]["text"] == "mesure"
    an = data.analyse({"trades": [{"asset": "btc", "pnl": 12.0, "lesson": "tendance"}], "stress": {"text": "x"}})
    assert an["attribution"]["realized"] == 12.0 and an["stress"] == {"text": "x"}
    assert BotData(tg.GuardConfig(run_mode="paper", db_file=str(tmp_path / "tg.db"), savoir=False,
                                  log_file=str(tmp_path / "tg.log"), lock_file=str(tmp_path / "tg.lock")),
                   market=None).evenements({}) is None
