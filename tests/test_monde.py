"""Prompt maître, étape 25 (docs/MONDE.md) : raisonnement et modèle du
monde. Chaque élément du monde daté, sourcé et classé ; histoire et
changements ; raisonnement du jour étape par étape ; scénarios sans
probabilité inventée ; note de qualité et son contrat."""

import dataclasses
import logging
from datetime import datetime, timezone

import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import contrats, monde, report_health
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, WorldModelReport

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
STATE = {"regime_detail": {"day": "2026-10-08", "tendance": "haussière", "volatilite": "normale",
                           "texte": "tendance haussière, volatilité normale"},
         "qualite": {"day": "2026-10-08", "score": 100.0}, "last_equity": 1000.0, "peak_equity": 1100.0,
         "last_decision_day": "2026-10-08", "last_cycle_ts": NOW.timestamp() - 120,
         "paper": {"cash": 400.0, "holdings": {"eth": {"qty": 2.0, "entry": 100.0, "stop": 92.0},
                                                "sol": {"qty": 4.0, "entry": 100.0, "stop": 75.0}}},
         "reasoning": {"assets": {"eth": {"status": "held", "breakout_gap_pct": None},
                                  "btc": {"status": "wait", "breakout_gap_pct": 6.0},
                                  "sol": {"status": "bought", "breakout_gap_pct": 0.0},
                                  "ada": {"status": "bear", "breakout_gap_pct": -1.0},
                                  "aave": {"status": "sold", "breakout_gap_pct": 9.0}}}}


def _g(tmp_path):
    return GuardConfig(lock_file=str(tmp_path / "trendguard_paper.lock"), db_file=str(tmp_path / "x.db"))


def test_the_world_is_dated_sourced_and_classified(tmp_path):
    snap = monde.snapshot(_g(tmp_path), STATE, NOW)
    facts = {f["name"]: f for f in snap["facts"]}
    assert all(f["kind"] in monde.KINDS and f["source"] for f in snap["facts"])
    assert facts["tendance"]["kind"] == "INTERPRETATION" and facts["capital"]["kind"] == "OBSERVATION"
    assert facts["baisse depuis le plus haut"]["kind"] == "INFERENCE" and facts["baisse depuis le plus haut"]["value"] == 9.09
    assert facts["positions"]["value"] == 2 and facts["crypto la plus proche d'une cassure"]["value"].startswith("ADA")
    bare = {f["name"] for f in monde.snapshot(_g(tmp_path), {}, NOW)["facts"]}
    assert bare == {"positions", "arrêt d'urgence", "mode sûr"}                # rien d'inventé
    assert monde.snapshot(_g(tmp_path), STATE, NOW)["fingerprint"] == snap["fingerprint"]


def test_history_and_what_changed(tmp_path):
    rows = [{"day": "2026-10-01", "bull": True, "halted": False, "safe_mode": False, "garde": False, "data_quality": 90},
            {"day": "2026-10-02", "bull": False, "halted": False, "safe_mode": True, "garde": True, "data_quality": 40},
            {"day": "2026-10-03", "bull": False, "halted": True, "safe_mode": True, "garde": True, "data_quality": 95}]
    ch = monde.changes(rows)
    assert "2026-10-02 : marché baissier (la veille haussier)" in ch and "2026-10-02 : mode sûr déclenché" in ch
    assert "2026-10-02 : données abîmées" in ch and "2026-10-03 : arrêt d'urgence déclenché" in ch
    assert "2026-10-03 : données redevenues saines" in ch
    lg = logging.getLogger("test.monde")
    lg.setLevel(logging.CRITICAL)
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, lg)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 60)
    hist = monde.history(bot.journal)
    assert len(hist) == 60 and [r["day"] for r in hist] == sorted(r["day"] for r in hist)
    assert {"day", "bull", "equity", "halted", "safe_mode", "garde", "data_quality"} <= set(hist[0])
    assert monde.history(None) == []


def test_the_reasoning_of_the_day():
    steps = monde.explain(STATE)
    assert [s["kind"] for s in steps] == ["INTERPRETATION", "OBSERVATION", "INFERENCE", "OBSERVATION", "DECISION"]
    assert steps[-1]["text"] == "achat de SOL ; vente de AAVE" and "ADA" in steps[3]["text"]
    assert all(s["source"] for s in steps)
    calm = monde.explain({})
    assert calm[-1]["text"] == "aucun achat aujourd'hui"


def test_scenarios_never_invent_probabilities():
    tree = monde.scenarios(STATE, kill=0.40)
    assert [n["shock_pct"] for n in tree] == [-30.0, -20.0, -10.0, 10.0]
    assert all(n["probability"] is None and n["kind"] == "SCENARIO" for n in tree)
    by = {n["shock_pct"]: n for n in tree}
    assert by[-10.0]["stops"] == ["ETH"] and by[-30.0]["stops"] == ["ETH", "SOL"]
    assert by[-30.0]["equity"] <= by[-20.0]["equity"] <= by[-10.0]["equity"] <= by[10.0]["equity"]
    assert by[-30.0]["children"] and not by[-10.0]["children"]
    assert "prix d'achat" in tree[0]["assumption"]                       # sans prix du jour, l'hypothèse le dit
    hard = monde.scenarios({**STATE, "peak_equity": 3000.0}, kill=0.40)
    assert hard[0]["kill_switch"]


def test_remember_keeps_history_and_diffs(tmp_path):
    g = _g(tmp_path)
    assert monde.remember(g, monde.snapshot(g, STATE, NOW)) == []
    later = {**STATE, "last_equity": 950.0}
    diff = monde.remember(g, monde.snapshot(g, later, NOW))
    assert "capital : 1000.0 → 950.0" in diff
    assert len(monde.remember(g, monde.snapshot(g, later, NOW))) == 0


def test_the_quality_and_its_contract(tmp_path):
    r = monde.evaluate(_g(tmp_path), STATE, NOW)
    q = r["quality"]
    assert q["status"] == "READY_FOR_ADVANCED_REASONING_AND_WORLD_MODEL" and q["score"] >= 95, q
    rep = monde.report_of(r)
    assert contrats.validate("WorldModelReport", rep.as_dict()).valid and not rep.hypothesis_as_fact
    text = monde.render(r, ["capital : 1000 → 950"])
    assert "## L'état du monde" in text and "## Et si" in text and "capital : 1000 → 950" in text
    good = dataclasses.asdict(rep)
    for change in ({"hypothesis_as_fact": True}, {"facts": (("x", "CERTITUDE"),)}, {"quality_score": 120.0},
                   {"p0_failures": ("world",)}, {"scenarios": -1}):
        with pytest.raises(ContractError):
            WorldModelReport(**{**good, **change})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    r = asst.local_answer("et si le marche baissait de 20 %", {"monde": {"text": "tendance haussière"}})["answer"]
    assert "Modèle du monde" in r and "sans probabilité" in r and "je n'agis pas" in r
    g = _g(tmp_path)
    line = next(x for x in report_health.analysis_checks(g, STATE) if x["label"] == "Modèle du monde")
    assert "position(s)" in line["detail"]
    from trendguard import config, systeme
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: g)
    monkeypatch.setattr(systeme, "read_state", lambda path: STATE)
    assert monde.main([]) == 0 and "Le raisonnement du jour" in capsys.readouterr().out
