"""Prompt maître, étape 24 (docs/MEMOIRE.md) : mémoire et graphe de
connaissances, reconstruits depuis les sources de vérité. Provenance et
dates de chaque fait ; une déduction n'est jamais une observation ; un texte
extérieur n'est jamais un fait ; questions (tout sur une entité, ce qui était
vrai à une date, chemin) ; note de santé et son contrat."""

import dataclasses
import logging
from datetime import datetime, timezone

import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import contrats, memoire, report_health
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, MemoryHealthReport

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def ran():
    lg = logging.getLogger("test.memoire")
    lg.setLevel(logging.CRITICAL)
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, lg)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 200)
    return bot


def test_the_memory_is_rebuilt_from_the_sources_of_truth(ran):
    g = memoire.build(ran.g, ran.state, journal=ran.journal, now=NOW)
    types = {n["type"] for n in g.nodes.values()}
    assert {"asset", "trade", "order", "decision", "model", "service", "contract", "runbook"} <= types
    assert all(n["source"] for n in g.nodes.values()) and all(e["source"] for e in g.edges)
    assert all(e["from"] in g.nodes and e["to"] in g.nodes for e in g.edges)
    journal_trades = [n for n in g.nodes.values() if n["type"] == "trade" and n["source"] == "journal financier"]
    assert journal_trades and all(n["valid_from"] <= n["valid_to"] for n in journal_trades)
    t = journal_trades[0]["id"]
    chain = [r for d, r, _n in g.neighbours(t)]
    assert "VENDU_PAR" in chain and "SUR" in chain                       # le trade remonte à ses ordres
    same = [e for e in g.edges if e["rel"] == "MEME_QUE"]
    assert same and all(e["kind"] == "INFERRED" for e in same)           # même trade vu par deux sources
    empty = memoire.build(GuardConfig(universe=("BTC",)), {}, journal_path="", now=NOW)
    assert {n["type"] for n in empty.nodes.values()} <= {"asset", "model", "service", "contract", "runbook"}


def test_inferences_and_reports_are_never_facts(ran):
    g = memoire.build(ran.g, ran.state, journal=ran.journal, now=NOW)
    inferred = [e for e in g.edges if e["kind"] == "INFERRED"]
    assert inferred and all(e["source"].startswith("déduit") for e in inferred)
    h = memoire.health(g, 0.5)
    assert h["checks"]["reasoning"][0] and h["checks"]["security"][0]
    g.edge("asset:eth", "VAUT", "asset:btc", "un forum", kind="REPORTED")   # un texte extérieur pris pour un fait
    assert not memoire.health(g, 0.5)["checks"]["security"][0] and "security" in memoire.health(g, 0.5)["p0"]
    g.edge("asset:eth", "MONTE", "asset:btc", "une IA", kind="INFERRED")    # déduction sans règle
    assert not memoire.health(g, 0.5)["checks"]["reasoning"][0]


def test_questions_about_an_entity_a_date_and_a_path(ran):
    g = memoire.build(ran.g, ran.state, journal=ran.journal, now=NOW)
    eth = memoire.about(g, "ETH")
    assert eth["found"] and eth["facts"] and all(f["source"] for f in eth["facts"])
    assert not memoire.about(g, "inconnu")["found"]
    trade = next(n for n in g.nodes.values() if n["type"] == "trade" and n["valid_from"])
    day = str(trade["valid_from"])[:10]
    assert trade["label"] in memoire.at(g, day)
    p = g.path("service:bot", "service:internet")
    assert p and p[0] == "service:bot" and p[-1] == "service:internet"
    assert g.path("service:bot", "pas-la") is None


def test_health_flags_broken_integrity_and_dates():
    g = memoire.Graph()
    g.node("a", "asset", "A", "réglages")
    g.edge("a", "SUR", "absent", "état du bot")
    h = memoire.health(g, 0.1)
    assert "integrity" in h["p0"] and h["status"] == "NOT_READY"
    g2 = memoire.Graph()
    g2.node("t", "trade", "T", "journal financier", valid_from="2026-10-09", valid_to="2026-10-01")
    assert "temporal" in memoire.health(g2, 0.1)["p0"]
    g3 = memoire.Graph()
    g3.node("x", "asset", "X", "")
    assert "provenance" in memoire.health(g3, 0.1)["p0"]


def test_the_examination_and_its_contract(ran):
    r = memoire.evaluate(ran.g, ran.state, NOW, journal=ran.journal)
    assert r["health"]["status"] == "READY_FOR_GOVERNED_LONG_TERM_MEMORY" and r["health"]["score"] >= 95
    rep = memoire.report_of(r)
    assert contrats.validate("MemoryHealthReport", rep.as_dict()).valid and not rep.silent_rewrite
    assert "Mémoire et graphe de connaissances" in memoire.render(r) and "intégrité" in memoire.render(r)
    good = dataclasses.asdict(rep)
    for change in ({"silent_rewrite": True}, {"inferred": rep.relations + 1}, {"health_score": 101.0},
                   {"p0_failures": ("integrity",)}):
        with pytest.raises(ContractError):
            MemoryHealthReport(**{**good, **change})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    r = asst.local_answer("que sait la memoire du bot", {"memoire": {"text": "120 entités"}})["answer"]
    assert "Mémoire" in r and "sources de vérité" in r and "je n'agis pas" in r
    g = GuardConfig(lock_file=str(tmp_path / "x.lock"), db_file=str(tmp_path / "x.db"))
    line = next(x for x in report_health.analysis_checks(g, {}) if x["label"] == "Mémoire")
    assert "entités" in line["detail"]
    from trendguard import config, systeme
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: g)
    monkeypatch.setattr(systeme, "read_state", lambda path: {})
    assert memoire.main(["BTC"]) == 0 and "BTC" in capsys.readouterr().out
    assert memoire.main([]) == 0 and "Mémoire et graphe" in capsys.readouterr().out
