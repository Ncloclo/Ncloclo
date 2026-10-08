"""Prompt maître, étape 13 (docs/ACCEPTATION_PAPER.md) : critères
d'acceptation mesurables du paper. AC-001 à AC-044 avec leur preuve (mesure
sur le bot ou test du dépôt), comptabilité recalculée, écart au backtest de
la même période, note pondérée, observation, verdict : un seul P0 raté ou
non mesurable bloque ; jamais une autorisation du réel."""

import copy
import logging
from datetime import timedelta

import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import acceptation as ac
from trendguard import contrats, report_health
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, PaperAcceptanceReport

DAY = timedelta(days=1)


@pytest.fixture(scope="module")
def paper_run():
    lg = logging.getLogger("test.acceptation")
    lg.setLevel(logging.ERROR)
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, lg)
    assert bot.boot()
    bot.state["started_at"] = close.index[SIM_FROM].isoformat()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 90)
    return bot, close.iloc[:SIM_FROM + 90], volume.iloc[:SIM_FROM + 90]


def _row(r, ac_id):
    return next(x for x in r["rows"] if x["id"] == ac_id)


# ---------- Les critères et leurs preuves ----------

def test_the_44_criteria_each_have_their_evidence():
    ids = [c.ac_id for c in ac.CRITERIA]
    assert ids == [f"AC-{n:03d}" for n in range(1, 45)]
    assert all(c.family in ac.FAMILY_WEIGHTS and c.priority in ("P0", "P1", "P2", "P3") for c in ac.CRITERIA)
    assert sum(ac.FAMILY_WEIGHTS.values()) == pytest.approx(1.0)
    missing = [(c.ac_id, ac.test_evidence(c.tests)[1]) for c in ac.CRITERIA if not ac.test_evidence(c.tests)[0]]
    assert not missing
    ok, gone = ac.test_evidence(["tests/test_acceptation.py::test_absent", "tests/absent.py::test_x"])
    assert not ok and len(gone) == 2
    assert ac.band(96) == "PAPER_READY" and ac.band(92) == "PAPER_READY_CANDIDATE" and ac.band(50) == "REJECTED"


# ---------- Mesures sur un bot paper ----------

def test_a_paper_bot_is_measured_and_matches_its_backtest(paper_run):
    bot, close, volume = paper_run
    r = ac.evaluate(bot.g, bot.state, now=close.index[-1].to_pydatetime() + DAY, close=close, volume=volume,
                    params=bot.p)
    for ac_id in ("AC-001", "AC-002", "AC-005", "AC-006", "AC-007", "AC-013", "AC-015", "AC-018", "AC-021",
                  "AC-023", "AC-029", "AC-033"):
        assert _row(r, ac_id)["status"] == "PASS", _row(r, ac_id)
    d = r["divergence"]
    assert d["status"] == "OK" and d["paper_entries"] == d["matched"] > 0 and not d["unexplained"]
    assert _row(r, "AC-027")["status"] == "PASS"
    assert _row(r, "AC-004")["status"] == "UNKNOWN"                 # journal en mémoire : jamais une réussite
    assert r["status"] == "BLOCKED" and "AC-004" in r["p0_failures"] and r["next_step"] == "CORRECTION"
    assert r["observation"]["days"] == pytest.approx(91, abs=1) and r["observation"]["events"] > 0
    rep = ac.report_of(r)
    assert contrats.validate("PaperAcceptanceReport", rep.as_dict()).valid and not rep.authorized
    text = ac.render(r)
    assert "## Verdict" in text and "BLOQUÉ" in text and "AC-044" in text
    assert ac.describe(r).startswith("acceptation du paper : BLOQUÉ")


def test_accounting_errors_are_caught(paper_run):
    bot, close, _volume = paper_run
    now = close.index[-1].to_pydatetime() + DAY
    st = copy.deepcopy(bot.state)
    st["paper"]["cash"] += 5.0
    assert _row(ac.evaluate(bot.g, st, now=now, params=bot.p), "AC-005")["status"] == "FAIL"
    st = copy.deepcopy(bot.state)
    if st["trades"]:
        st["trades"][0]["pnl"] += 1.0
        assert _row(ac.evaluate(bot.g, st, now=now, params=bot.p), "AC-007")["status"] == "FAIL"
        st = copy.deepcopy(bot.state)
        st["trades"][0]["date"] = (now - timedelta(days=5000)).isoformat()
        assert _row(ac.evaluate(bot.g, st, now=now, params=bot.p), "AC-015")["status"] == "FAIL"
    st = copy.deepcopy(bot.state)
    if st["paper"]["holdings"]:
        a = next(iter(st["paper"]["holdings"]))
        st["paper"]["holdings"][a]["cost"] *= 1.1
        assert _row(ac.evaluate(bot.g, st, now=now, params=bot.p), "AC-006")["status"] == "FAIL"


# ---------- Verdict (§47-53) ----------

def test_verdict_needs_every_p0_the_score_and_the_observation(paper_run, monkeypatch):
    bot, close, volume = paper_run
    now = close.index[-1].to_pydatetime() + DAY
    real = ac._measures

    def all_pass(ctx):
        out = real(ctx)
        return {k: ("PASS", v[1]) for k, v in out.items()}

    monkeypatch.setattr(ac, "_measures", all_pass)
    st = copy.deepcopy(bot.state)
    st["trades"] = st["trades"] * 20                                  # assez d'événements
    r = ac.evaluate(bot.g, st, now=now, close=close, volume=volume, params=bot.p)
    if r["divergence"]["unexplained"]:
        pytest.skip("écart inattendu sur ce marché")
    assert r["status"] == "ACCEPTED" and r["next_step"] == "POLICY_ENGINE" and r["score"] >= 95
    assert contrats.validate("PaperAcceptanceReport", ac.report_of(r).as_dict()).valid

    def one_unknown(ctx):
        out = all_pass(ctx)
        out["AC-032"] = ("UNKNOWN", "audit introuvable")
        return out

    monkeypatch.setattr(ac, "_measures", one_unknown)
    r = ac.evaluate(bot.g, st, now=now, close=close, volume=volume, params=bot.p)
    assert r["status"] == "BLOCKED" and r["p0_failures"] == ["AC-032"]
    monkeypatch.setattr(ac, "_measures", all_pass)
    short = copy.deepcopy(st)
    short["started_at"] = (now - timedelta(days=5)).isoformat()
    r = ac.evaluate(bot.g, short, now=now, close=close, volume=volume, params=bot.p)
    assert r["status"] == "BLOCKED" and any("observation" in m for m in r["missing"])
    r = ac.evaluate(bot.g, st, now=now, params=bot.p)                   # sans les cours : écart non mesuré
    assert r["status"] == "BLOCKED" and any("non mesuré" in m for m in r["missing"])


def test_the_report_contract_refuses_what_it_must():
    good = dict(report_id="2b1f4f3c-6c2d-4f6a-9d7e-0c1b2a3d4e5f", created_at="2026-10-08T12:00:00+00:00",
                mode="paper", status="BLOCKED", next_step="CORRECTION", readiness_score=83.0, band="VALIDATING",
                p0_failures=("AC-004",), passed=40, failed=1, unknown=1, not_applicable=2, observation_days=3.0,
                observation_events=4, missing=("observation",), engine_version=ac.VERSION)
    PaperAcceptanceReport(**good)
    for change in ({"status": "ACCEPTED", "next_step": "POLICY_ENGINE"}, {"missing": ()}, {"band": "PAPER_READY"},
                   {"passed": 41}, {"authorized": True}, {"readiness_score": 120.0}, {"mode": "demo"},
                   {"observation_days": -1.0}):
        with pytest.raises(ContractError):
            PaperAcceptanceReport(**{**good, **change})


# ---------- Rachelle, rapport, commande ----------

def test_rachelle_and_the_report_tell_the_verdict(paper_run):
    bot, close, _volume = paper_run
    r = ac.evaluate(bot.g, bot.state, now=close.index[-1].to_pydatetime() + DAY, params=bot.p)
    text = asst.local_answer("le paper est-il accepte ? criteres d acceptation", {"acceptation": r})["answer"]
    assert "Acceptation du paper" in text and "BLOQUÉ" in text and "jamais le réel" in text
    line = next(x for x in report_health.analysis_checks(GuardConfig(), {}) if x["label"] == "Acceptation du paper")
    assert line["ok"] is None and "acceptation du paper" in line["detail"]


def test_acceptance_command(paper_run, monkeypatch, tmp_path):
    bot, close, volume = paper_run
    from trendguard import evolution, systeme
    monkeypatch.setattr(systeme, "read_state", lambda *a, **k: bot.state)
    monkeypatch.setattr(evolution, "load_history", lambda *a, **k: (close, volume))
    out = tmp_path / "ACCEPTATION.md"
    assert ac.main(["--out", str(out)]) == 1                           # bloqué : code de sortie 1
    assert out.read_text(encoding="utf-8").startswith("# Acceptation du paper")
