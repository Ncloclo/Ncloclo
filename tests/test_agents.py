"""Prompt maître, étape 5 (docs/AGENTS.md) : socle multi-agents (manifestes,
registre, quarantaine, disjoncteur, bus de messages, tableau noir cloisonné,
débat, désaccord, consensus) et comité d'agents financiers (banc d'essai,
consultatif dans le bot, jamais une autorisation)."""

import dataclasses
import logging
import pathlib

import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import agents, comite
from trendguard.agents import (
    Agent,
    AgentRegistry,
    AgentResult,
    AgentViolation,
    Challenge,
    Manifest,
    MessageBus,
    Supervisor,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def logger():
    lg = logging.getLogger("test.agents")
    lg.setLevel(logging.WARNING)
    return lg


def _m(aid, family="analyse", reads=("x",), **kw):
    return Manifest(aid, "1.0.0", aid, family, (aid,), reads, **kw)


# ---------- Manifestes, résultats, registre ----------

def test_manifests_and_results_are_contracts():
    with pytest.raises(AgentViolation):
        _m("trader", may_order=True)                             # aucun agent ne passe d'ordre
    with pytest.raises(ValueError):
        _m("x", family="inconnue")
    with pytest.raises(ValueError):
        _m("x", weight=5)
    with pytest.raises(ValueError):
        AgentResult("a", "POUR", 80)                              # avis tranché sans preuve
    with pytest.raises(ValueError):
        AgentResult("a", "ACHETER", 0)
    assert AgentResult("a", "POUR", 80, ("cassure",)).score == 80


def test_registry_breaker_quarantine_and_restore():
    reg = AgentRegistry([Agent(_m("a"), lambda v: AgentResult("a", "NEUTRE", 0))])
    with pytest.raises(ValueError):
        reg.register(Agent(_m("a"), lambda v: None))
    for _ in range(agents.BREAKER_FAILURES):
        reg.note("a", False, 5)
    assert reg.records["a"].status == "QUARANTINED" and "disjoncteur" in reg.records["a"].reason
    assert reg.ready() == [] and reg.reliability("a") == 0.2
    reg.restore("a", "examiné")
    assert reg.records["a"].status == "READY" and reg.metrics()["a"]["runs"] == 3


# ---------- Bus, tableau noir, sécurité ----------

def test_bus_only_known_members_and_no_replay():
    bus = MessageBus(["a"])
    assert bus.send("superviseur", "a", "TASK_ASSIGNMENT", {"x": 1}, "m1")
    assert not bus.send("superviseur", "a", "TASK_ASSIGNMENT", {"x": 1}, "m1")      # rejoué : ignoré
    with pytest.raises(AgentViolation):
        bus.send("intrus", "a", "TASK_RESULT", {})
    with pytest.raises(ValueError):
        bus.send("a", "superviseur", "ORDRE", {})
    assert len(bus.log) == 1 and len(bus.log[0]["hash"]) == 16


def test_isolation_impersonation_and_herding_are_blocked():
    def nosy(v):
        v["portefeuille"]                                        # hors de son manifeste
        return AgentResult("curieux", "NEUTRE", 0)

    def spoof(v):
        return AgentResult("risque", "POUR", 0)                  # se fait passer pour un autre

    def copycat(v):
        v["avis"]                                                # lire les autres avant d'avoir répondu
        return AgentResult("mouton", "NEUTRE", 0)
    reg = AgentRegistry([Agent(_m("curieux"), nosy), Agent(_m("usurpateur"), spoof),
                         Agent(_m("mouton"), copycat),
                         Agent(_m("honnete"), lambda v: AgentResult("honnete", "POUR", 60, (f"x={v['x']}",)))])
    res = Supervisor(reg).run("essai", ["curieux", "usurpateur", "mouton", "honnete"], {"x": 1, "portefeuille": {}})
    assert set(res.results) == {"honnete"} and len(res.violations) == 3
    assert {k for k, r in reg.records.items() if r.status == "QUARANTINED"} == {"curieux", "usurpateur", "mouton"}
    assert res.trace["messages"][0]["kind"] == "TASK_ASSIGNMENT"


# ---------- Débat, désaccord, consensus ----------

def test_debate_revision_disagreement_and_consensus():
    results = {"a": AgentResult("a", "POUR", 80, ("e",)), "b": AgentResult("b", "POUR", 60, ("e",)),
               "c": AgentResult("c", "CONTRE", -70, ("e",)), "d": AgentResult("d", "CONTRE", -60, ("e",))}
    factors, vetoes = agents.revise(results, [Challenge("x", "a", "douteux", "discount", 0.5),
                                              Challenge("x", "d", "faux", "nullify"),
                                              Challenge("x", "", "danger", "veto")])
    assert factors == {"a": 0.5, "b": 1.0, "c": 1.0, "d": 0.0} and vetoes == ["x : danger"]
    assert agents.disagreement(results, {k: 1.0 for k in results})[0] == "HIGH"   # deux pour, deux contre
    assert agents.disagreement(results, factors)[0] == "MEDIUM"                   # un contre annulé
    ms = {k: _m(k) for k in results}
    value, strength = agents.consensus(results, factors, ms, lambda k: 1.0)
    assert value == pytest.approx((40 + 60 - 70) / 2.5, abs=0.1) and strength == "faible"


# ---------- Comité d'agents financiers ----------

def test_committee_benchmark_scenarios_all_pass():
    rows = comite.benchmark()
    assert len(rows) == 8 and all(r["pass"] for r in rows), rows


def test_a_failed_non_critical_agent_degrades_safely():
    reg = comite.registry()
    quant = reg.records["quant"].agent
    reg.records["quant"].agent = dataclasses.replace(quant, fn=lambda v: (_ for _ in ()).throw(RuntimeError("panne")))
    data, _day = comite._scenario("nette")
    view, res = comite.evaluate("aave", data, reg)
    assert "quant" in res.missing and view.recommendation in ("ACHAT", "ATTENDRE")       # continue, dégradé
    with pytest.raises(ValueError):
        dataclasses.replace(view, authorized=True)                                   # jamais une autorisation


def test_no_agent_can_reach_an_order():
    for name in ("agents.py", "comite.py"):
        text = (ROOT / "trendguard" / name).read_text(encoding="utf-8")
        for forbidden in ("enter_planned", "market_buy", "porte.authorize", "porte.check", "_execute_entry",
                          "import porte", "record_order"):
            assert forbidden not in text, (name, forbidden)
    assert all(not a.manifest.may_order for a in comite.AGENTS)


def test_the_committee_is_consultative_in_the_bot(logger, monkeypatch):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 150)
    rec = bot.journal.committee_record()
    assert rec["views"] > 0 and bot.journal.verify()["ok"]
    seen = [r for r in bot.state["reasoning"]["lines"] if r.startswith("Comité d'agents")]
    assert seen or not bot.state["comite"]["views"]
    monkeypatch.setattr(comite, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("comité en panne")))
    ref, fr_ = make_bot("paper", close, logger)
    assert ref.boot()
    _run(ref, fr_, close, volume, SIM_FROM, SIM_FROM + 150)
    key = lambda b: [(t["asset"], t["date"]) for t in b.state["trades"]]  # noqa: E731
    assert key(bot) == key(ref) and ref.state["comite"]["views"] == {}             # même décisions, panne sans effet


def test_rachelle_reports_the_committee_view():
    ctx = {"comite": {"day": "2026-10-05", "views": {"aave": {
        "text": "AAVE : achat (consensus +35, 4 pour, 0 contre, désaccord faible) — consensus modéré (+35)",
        "reasons": ["consensus modéré (+35)", "objection critique → quant : volatilité forte"]}}}}
    r = asst.local_answer("Que penses-tu de AAVE ?", ctx)
    assert "AAVE : achat" in r["answer"] and "objection critique" in r["answer"] and "consultatif" in r["answer"]
    assert "python trendguard_bot.py comite" in asst.local_answer("avis des agents", {})["answer"]
