"""Prompt maître, étape 11 (docs/MOTEUR_RISQUE.md) : moteur de risque. VaR
et ES (historique, normale, Student, Monte-Carlo, 1 à 10 jours), queue,
corrélations, liquidité, budget, contributions, stress inversé, calibrage
(Kupiec, Christoffersen), état et décision, machine à états, journal en ajout
seul ; dans le bot : ne change aucun trade, et sans évaluation valide, aucun
achat (les ventes restent permises)."""

import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import contrats, donnees, porte, report_health
from trendguard import moteur_risque as mr
from trendguard import trend_strategy as ts
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, OrderIntent, SafeModeState

P = ts.TrendParams()
NOW = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)


@pytest.fixture
def logger():
    lg = logging.getLogger("test.risque")
    lg.setLevel(logging.ERROR)
    return lg


@pytest.fixture(scope="module")
def market():
    return synthetic_market()


def _book(close, k=650, assets=("eth", "xrp", "doge"), usd=(30.0, 25.0, 20.0)):
    """Un portefeuille au jour k : (jour, cours jusqu'à k, positions)."""
    c = close.iloc[:k + 1]
    pos = {}
    for a, u in zip(assets, usd):
        px = float(c[a].iloc[-1])
        pos[a] = {"qty": u / px, "price": px, "stop": px * 0.85, "risk_quote": 1.0, "entry": px}
    return str(c.index[-1].date()), c, pos


# ---------- Mesures (§10-12) ----------

def test_var_and_es_follow_their_definitions():
    v, e = mr.parametric(0.0, 0.02, 0.99)
    assert v == pytest.approx(2.3263 * 0.02, rel=1e-4) and e == pytest.approx(0.02 * 0.026652 / 0.01, rel=1e-3)
    v10, _e = mr.parametric(0.0, 0.02, 0.99, 10)
    assert v10 == pytest.approx(v * math.sqrt(10))
    x = np.random.default_rng(1).normal(0.0, 0.02, 20_000)
    hv, he = mr.var_es(x, 0.99)
    assert hv == pytest.approx(v, rel=0.05) and he >= hv
    tv, te, nu = mr.student(0.0, 0.02, 0.0, 0.99)
    assert nu == 30.0 and tv == pytest.approx(v, rel=0.08) and te >= tv                # presque normale
    fat, _fe, fat_nu = mr.student(0.0, 0.02, 6.0, 0.99)
    assert fat_nu == 5.0 and fat > tv                                               # queue épaisse : VaR 99 % plus haute
    rets = np.random.default_rng(2).normal(0.001, 0.02, (500, 2))
    w = np.array([0.5, 0.3])
    a = mr.montecarlo(rets, w, 0.95)
    assert a == mr.montecarlo(rets, w, 0.95) and a[1] >= a[0] > 0                   # graine fixée, ES ≥ VaR
    assert mr.horizon_returns(np.array([0.1, 0.1, -0.5]), 2) == pytest.approx([0.21, -0.45])


def test_tail_index_and_extreme_values():
    rng = np.random.default_rng(3)
    losses = (rng.pareto(3.0, 5000) + 1) * 0.01
    r = np.concatenate([-losses, losses * 0.5])
    assert mr.hill(r) == pytest.approx(3.0, abs=0.6)
    p = mr.pot(r)
    assert p["xi"] > 0 and p["es"] > p["var"] > p["threshold"]
    assert mr.pot(r[:100]) is None and mr.hill(np.ones(30)) is None


def test_correlations_rank_and_tail_dependence():
    a = np.arange(50, dtype=float)
    assert mr.kendall(a, a) == 1.0 and mr.kendall(a, -a) == -1.0
    assert mr.kendall(np.array([1.0, 2.0, 3.0]), np.array([1.0, 3.0, 2.0])) == pytest.approx(1 / 3)
    rng = np.random.default_rng(4)
    common = rng.normal(0, 0.03, 365)
    rets = np.column_stack([common + rng.normal(0, 0.01, 365), np.exp(common * 10)])  # même ordre, autre forme
    c = mr.correlations(rets, common)
    assert c["spearman"] > c["pearson"] and c["kendall"] > 0
    assert c["pairs"] == 1 and 0 <= c["tail_dependence"] <= 1 and c["crisis"] is not None
    assert mr.correlations(rets[:, :1], common) == {"pairs": 0}


def test_var_calibration_tests():
    assert mr.kupiec(12, 250, 0.95)["p_value"] > 0.8
    assert mr.kupiec(30, 250, 0.95)["p_value"] < 0.01
    clustered = [False] * 100 + [True] * 10 + [False] * 100
    spread = ([False] * 20 + [True]) * 10
    assert mr.christoffersen(clustered)["p_value"] < 0.01 < mr.christoffersen(spread)["p_value"]
    x = np.random.default_rng(5).normal(0, 0.02, 1500)
    assert mr.rolling_var_backtest(x)["verdict"] == "VaR bien calibrée"
    pv, pe = mr.prudent(x, 0.99)
    assert pv == max(mr.var_es(x, 0.99)[0], mr.parametric(x.mean(), x.std(ddof=1), 0.99)[0],
                     mr.student(*mr.moments(x)[:2], mr.moments(x)[3], 0.99)[0]) and pe >= pv
    under = mr.var_backtest([0.01] * 200, list(np.random.default_rng(6).normal(0, 0.03, 200)))
    assert under["verdict"] == "VaR sous-estimée" and mr.var_backtest([0.02], [0.0])["verdict"] == \
        "pas assez d'observations"


def test_the_engine_var_is_checked_on_the_rule_s_own_positions(market):
    close, volume = market
    out = mr.position_backtest(close, volume, P, str(close.index[SIM_FROM].date()), str(close.index[-1].date()))
    assert set(out) == set(mr.LEVELS) and out[0.95]["n"] == out[0.99]["n"] > 50
    assert out[0.95]["expected"] == pytest.approx(out[0.95]["n"] * 0.05) and out[0.99]["verdict"]


# ---------- L'évaluation du portefeuille (§15-50) ----------

def test_a_full_assessment_of_a_portfolio(market):
    close, volume = market
    day, c, pos = _book(close)
    a = mr.assess(day, c, volume, pos, 25.0, 100.0, 110.0, P, 1.0, {"kill": 0.40, "data_quality": 100.0},
                  "PRE", "paper", [], NOW)
    assert contrats.validate("RiskAssessment", a.as_dict()).valid and not a.authorized
    assert a.path[-1] == a.state and a.state in contrats.RISK_STATES and set(a.components) == set(mr.SCORE_WEIGHTS)
    m = a.metrics
    for key in ("95_1", "99_1", "95_10", "99_5"):
        for method, (v, e) in m["var"][key].items():
            assert 0 <= v <= e + 1e-12, (key, method)                          # perte au-delà ≥ VaR
    assert m["var"]["99_10"]["normal"][0] > m["var"]["99_1"]["normal"][0]
    shares = [x["share"] for x in m["contributions"]]
    assert sum(shares) == pytest.approx(1.0) and shares == sorted(shares, reverse=True)
    assert [r["loss_pct"] for r in m["stress"]] == sorted((r["loss_pct"] for r in m["stress"]), reverse=True)
    rev = [r["shock_no_stops"] for r in m["reverse_stress"][:4]]
    assert all(x is None or y is None or x < y for x, y in zip(rev, rev[1:]))
    assert m["budget"]["used"] == pytest.approx(3.0 / (0.06 * 100.0)) and m["budget"]["band"] == "NORMAL"
    assert m["drawdown"]["drawdown_pct"] == pytest.approx((1 - 100 / 110) * 100)
    assert m["liquidity"]["score"] == 1.0 and m["concentration"]["counterparty"] == "Binance"
    assert a.expires_at > a.created_at and m["uncertainty"]["note"].startswith("une confiance n'est pas")
    view = mr.compact(a)
    assert view["var95_pct"] == pytest.approx(m["var95_pct"]) and mr.gate(view, day, NOW)[0]
    text = mr.describe(view)
    assert text.startswith("Moteur de risque : ") and "VaR d'un jour" in text and "budget de risque" in text
    empty = mr.assess(day, c, volume, {}, 100.0, 100.0, 100.0, P, 1.0, {}, "PRE", "paper", [], NOW)
    assert empty.state == "RISK_NORMAL" and empty.approval == "RISK_APPROVED" and empty.score == 0.0


def test_blocked_only_for_what_already_stops_buying(market):
    close, volume = market
    day, c, pos = _book(close)

    def run(**kw):
        args = dict(cash=25.0, equity=100.0, peak=110.0, ctx={"kill": 0.40})
        args.update(kw)
        return mr.assess(day, c, volume, args.get("pos", pos), args["cash"], args["equity"], args["peak"], P,
                         args.get("mult", 1.0), args["ctx"], "PRE", "paper", [], NOW)
    for case, why in ((run(equity=0.0), "données critiques invalides"),
                      (run(pos=dict(pos, eth=dict(pos["eth"], price=float("nan")))), "prix du jour inconnu"),
                      (run(ctx={"kill": 0.40, "halted": True}), "arrêt d'urgence déclenché"),
                      (run(peak=200.0), "limite de 40 % atteinte"),
                      (run(mult=0.1), "budget de risque dépassé")):
        assert case.state == "RISK_BLOCKED" and case.approval == "RISK_BLOCKED", why
        assert any(why in v for v in case.violations), (why, case.violations)
        ok, detail = mr.gate(mr.compact(case), day, NOW)
        assert not ok and detail.startswith("risque bloqué")
    with pytest.raises(ContractError):
        contrats.RiskAssessment(**dict(run().__dict__, approval="RISK_BLOCKED"))       # blocage sans raison
    with pytest.raises(ContractError):
        contrats.RiskAssessment(**dict(run().__dict__, authorized=True))


def test_the_gate_wants_a_fresh_valid_assessment(market):
    close, volume = market
    day, c, pos = _book(close)
    view = mr.compact(mr.assess(day, c, volume, pos, 25.0, 100.0, 100.0, P, 1.0, {}, "PRE", "paper", [], NOW))
    assert mr.gate(view, day, NOW + timedelta(hours=3))[0]
    for bad, why in ((None, "absente"), (dict(view, day="2020-01-01"), "absente"),
                     ({"day": day, "error": "ZeroDivisionError"}, "indisponible"),
                     (view, "périmée")):
        ok, detail = mr.gate(bad, day, NOW + timedelta(hours=30) if bad is view else NOW)
        assert not ok and why in detail, (why, detail)
    assert "mode sûr" in mr.describe({"day": day, "error": "x"})


def test_the_state_machine_of_risk():
    assert mr.advance("RISK_UNKNOWN", "RISK_CALCULATING") == "RISK_CALCULATING"
    assert mr.advance("RISK_CRITICAL", "RISK_CALCULATING") == "RISK_CALCULATING"     # retour contrôlé : recalcul
    for a, b in (("RISK_UNKNOWN", "RISK_NORMAL"), ("RISK_CRITICAL", "RISK_NORMAL"), ("RISK_BLOCKED", "RISK_NORMAL")):
        with pytest.raises(ValueError):
            mr.advance(a, b)


def test_the_gate_of_execution_refuses_without_a_risk_assessment():
    intent = OrderIntent.from_plan({"asset": "eth", "qty": 0.01, "entry": 2000.0, "stop": 1800.0,
                                    "risk_quote": 2.0, "cost": 20.0, "day": "2026-09-27"}, "2026-09-27")
    base = dict(equity=1000.0, cash=500.0, invested=0.0, held_risk=(), risk_mult=1.0, expected_day="2026-09-27",
                universe=frozenset({"eth"}), allowed=frozenset({"eth"}), safe_mode=SafeModeState())
    ok = porte.check(intent, porte.Portfolio(**base, risk_engine=(True, "vigilance")), P, NOW)
    assert ok.approved and ("Moteur de risque", True, "vigilance") in ok.limit_checks
    no = porte.check(intent, porte.Portfolio(**base, risk_engine=(False, "moteur de risque indisponible (x)")), P, NOW)
    assert no.status == "EMERGENCY_BLOCK" and any("moteur de risque" in r for r in no.blocking_reasons)
    assert porte.check(intent, porte.Portfolio(**base), P, NOW).approved              # essais : non mesuré


# ---------- Journal (§56-58) ----------

def test_the_journal_keeps_every_assessment(market):
    close, volume = market
    j = donnees.Journal(":memory:")
    assert max(j.applied()) == 5 and j.verify()["ok"]
    day, c, pos = _book(close)
    a = mr.assess(day, c, volume, pos, 25.0, 100.0, 100.0, P, 1.0, {}, "POST", "paper", [], NOW)
    j.record_risk_assessment(a)
    j.record_risk_assessment(a)                                               # la même : jamais en double
    nxt = str((pd.Timestamp(day) + pd.Timedelta(days=1)).date())
    b = mr.assess(nxt, close.iloc[:652], volume, pos, 25.0, 97.0, 100.0, P, 1.0, {}, "PRE", "paper", [], NOW)
    j.record_risk_assessment(b)
    c2 = mr.assess(nxt, close.iloc[:652], volume, pos, 25.0, 97.0, 100.0, P, 1.0, {}, "POST", "paper", [], NOW)
    j.record_risk_assessment(c2)
    assert j.counts()["fin_risk_assessments"] == 3
    hist = j.risk_history("paper")
    assert len(hist) == 1 and hist[0][0] == pytest.approx(a.metrics["var95_pct"] / 100)
    assert hist[0][1] == pytest.approx(97.0 / 100.0 - 1)                      # le lendemain, avant les achats
    with pytest.raises(sqlite3.IntegrityError):
        j.conn.execute("UPDATE fin_risk_assessments SET score = 0")              # en ajout seulement
    assert j.verify()["ok"] and j.rollback(4) == [5] and j.migrate() == [5]


# ---------- Dans le bot ----------

def test_the_risk_engine_never_changes_a_trade(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, risk_engine=True)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 50)
    view = bot.state["moteur_risque"]
    assert view["pre"]["state"] in contrats.RISK_STATES and view["post"]["approval"] in contrats.RISK_APPROVALS
    assert bot.journal.counts()["fin_risk_assessments"] >= 2 * 45 and bot.journal.verify()["ok"]
    ref, fr_ = make_bot("paper", close, logger)
    assert ref.boot()
    _run(ref, fr_, close, volume, SIM_FROM, SIM_FROM + 50)
    key = lambda b: [(t["asset"], t["date"], round(t["pnl"], 8)) for t in b.state["trades"]]  # noqa: E731
    assert key(bot) == key(ref) and sorted(bot.state["paper"]["holdings"]) == sorted(ref.state["paper"]["holdings"])
    assert bot.state["trades"] or bot.state["paper"]["holdings"]


def test_without_a_valid_assessment_no_buy_but_sales_go_on(logger, monkeypatch):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, risk_engine=True)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 50)
    held = set(bot.state["paper"]["holdings"])
    assert held                                                                # le moteur laisse acheter
    cut = close.index[SIM_FROM + 50] + pd.Timedelta(hours=12)
    monkeypatch.setattr(mr, "assess", lambda *a, **k: 1 / 0)                 # moteur en panne
    _run(bot, fb, close, volume, SIM_FROM + 50, len(close) - 1)
    bought = [t for t in bot.state["trades"] if pd.Timestamp(t["entry_date"]) >= cut]
    bought += [h for h in bot.state["paper"]["holdings"].values() if pd.Timestamp(h["entry_date"]) >= cut]
    assert not bought                                                          # plus aucun achat
    assert any(t["asset"] in held and pd.Timestamp(t["date"]) >= cut for t in bot.state["trades"])  # ventes
    assert "error" in bot.state["moteur_risque"]["pre"]


def test_rachelle_and_the_report_read_the_risk_engine(market):
    close, volume = market
    day, c, pos = _book(close)
    view = mr.compact(mr.assess(day, c, volume, pos, 25.0, 100.0, 100.0, P, 1.0, {}, "POST", "paper", [], NOW))
    st = {"moteur_risque": {"day": day, "post": view, "added": {"var95_pct": 0.4}}}
    r = asst.local_answer("que dit le moteur de risque ?", st)["answer"]
    assert "VaR d'un jour" in r and "aucun achat" in r and "pas une probabilité de gain" in r
    assert "python trendguard_bot.py risque" in asst.local_answer("moteur de risque", {})["answer"]
    line = next(x for x in report_health.analysis_checks(GuardConfig(), st) if x["label"] == "Moteur de risque")
    assert line["ok"] is None and "Achats du jour : VaR à 95 % +0,4 point(s)" in line["detail"]
    bad = {"moteur_risque": {"day": day, "pre": {"day": day, "error": "ValueError : x"}}}
    line = next(x for x in report_health.analysis_checks(GuardConfig(), bad) if x["label"] == "Moteur de risque")
    assert line["ok"] is False and "aucun achat" in line["detail"]


def test_history_replay_never_blocks_a_healthy_portfolio(market):
    """Le portefeuille du backtest, jour après jour : le moteur ne bloque
    jamais quand la porte laisserait acheter (pas d'arrêt d'urgence)."""
    close, volume = market
    seen = []

    def watch(i, plans, holdings, equity):
        if i % 25 == 0 and holdings:
            c = close.iloc[:i + 1]
            pos = {a: {"qty": h.qty, "price": float(c[a].iloc[-1]), "stop": h.stop, "risk_quote": h.risk_quote,
                       "entry": h.entry} for a, h in holdings.items()}
            cash = equity - sum(p["qty"] * p["price"] for p in pos.values())
            seen.append(mr.assess(str(c.index[-1].date()), c, volume, pos, cash, equity, max(equity, 10_000.0), P,
                                  1.0, {}, "PRE", "paper", [], NOW))
        return plans
    ts.backtest(close, volume, P, str(close.index[SIM_FROM].date()), str(close.index[-1].date()),
                hooks=ts.BacktestHooks(filter_plans=watch))
    assert len(seen) >= 4
    for a in seen:
        dd = a.metrics["drawdown"]["drawdown_pct"]
        assert (a.approval == "RISK_BLOCKED") == (dd >= 40.0 or a.metrics["budget"]["used"] > 1), (a.day, a.violations)
