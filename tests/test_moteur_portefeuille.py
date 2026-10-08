"""Prompt maître, étape 12 (docs/MOTEUR_PORTEFEUILLE.md) : moteur de
portefeuille. Contraintes jamais relâchées, optimiseurs (parts égales,
inverse de la volatilité, variance minimale, parité de risque, Sharpe
maximal), état du portefeuille, allocations comparées, décision
consultative et son contrat, étude de la taille des achats ; dans le bot :
aucun trade changé, une panne ne bloque rien."""

import logging

import numpy as np
import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import contrats, report_health
from trendguard import moteur_portefeuille as mp
from trendguard import trend_strategy as ts
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, PortfolioDecision

P = ts.TrendParams()
COV = np.array([[0.04, 0.018, 0.006], [0.018, 0.09, 0.012], [0.006, 0.012, 0.01]])


@pytest.fixture
def logger():
    lg = logging.getLogger("test.portefeuille")
    lg.setLevel(logging.ERROR)
    return lg


@pytest.fixture(scope="module")
def market():
    return synthetic_market()


def _book(close, k=650, usd=(30.0, 25.0, 20.0), assets=("eth", "xrp", "doge")):
    c = close.iloc[:k + 1]
    pos = {}
    for a, u in zip(assets, usd):
        px = float(c[a].iloc[-1])
        pos[a] = {"qty": u / px, "price": px, "stop": px * 0.85, "risk_quote": 1.0, "entry": px}
    return str(c.index[-1].date()), c, pos


# ---------- Contraintes et optimiseurs (§13-26) ----------

def test_constraints_are_never_relaxed():
    w = mp.project(np.array([0.9, 0.1, 0.05]), 0.6, 0.25)
    assert w.sum() == pytest.approx(0.6) and w.max() <= 0.25 + 1e-12 and w.min() >= 0
    assert mp.feasibility(2, 0.7, 0.25) and "impossible" in mp.feasibility(2, 0.7, 0.25)[0]
    assert mp.feasibility(3, 1.2, 0.5)                                   # ni levier
    with pytest.raises(ValueError, match="impossible"):
        mp.project(np.ones(2), 0.7, 0.25)
    assert mp.validate_weights(np.array([0.3, 0.2]), 0.5, 0.3) == []
    assert mp.validate_weights(np.array([0.4, 0.2]), 0.5, 0.3)


def test_each_optimizer_does_what_it_says():
    eq = mp.optimize("equal", COV, 0.9, 0.5)
    assert eq == pytest.approx([0.3, 0.3, 0.3])
    iv = mp.optimize("inverse_vol", COV, 0.9, 0.5)
    vols = np.sqrt(np.diag(COV))
    assert (iv * vols) == pytest.approx(np.full(3, (iv * vols)[0]), rel=1e-6)
    mv = mp.optimize("min_variance", COV, 0.9, 0.5)
    assert mv @ COV @ mv < eq @ COV @ eq and mv @ COV @ mv <= iv @ COV @ iv + 1e-12
    erc = mp.optimize("erc", COV, 0.9, 0.5)
    _s, rc = mp.risk_contributions(erc, COV)
    assert rc.sum() == pytest.approx(1.0) and rc.max() - rc.min() < 1e-3
    ms = mp.optimize("max_sharpe", COV, 0.9, 0.5, mu=np.array([0.001, 0.0, 0.002]))
    assert ms[2] >= ms[1] and mp.validate_weights(ms, 0.9, 0.5) == []
    for m in mp.METHODS:
        assert mp.validate_weights(mp.optimize(m, COV, 0.9, 0.5, mu=np.zeros(3)), 0.9, 0.5) == []
    with pytest.raises(ValueError):
        mp.optimize("max_sharpe", COV, 0.9, 0.5)
    with pytest.raises(ValueError):
        mp.optimize("magique", COV, 0.9, 0.5)


# ---------- État, comparaison, décision (§7-9, §28-35, §61) ----------

def test_portfolio_state_and_its_constraints(market):
    close, _volume = market
    day, c, pos = _book(close)
    st = mp.state(day, pos, c, 100.0, 25.0, P)
    assert st["count"] == 3 and st["exposure"] == pytest.approx(0.75) and st["cash_share"] == pytest.approx(0.25)
    assert sum(x["weight"] for x in st["positions"]) == pytest.approx(0.75)
    assert sum(x["risk_share"] for x in st["positions"]) == pytest.approx(1.0)
    assert st["effective_n"] == pytest.approx(1 / sum((u / 75) ** 2 for u in (30, 25, 20)))
    assert st["risk_used"] == pytest.approx(0.03) and st["vol_annual"] > 0 and st["diversification"] >= 1
    assert mp.constraints(st) == ["ETH pèse 30 % (achetée sous 25 %, elle a monté)"]
    small = mp.state(day, {"eth": pos["eth"]}, c, 1000.0, 970.0, ts.TrendParams(max_positions=0 + 1))
    assert mp.constraints(small) == []
    over = mp.state(day, pos, c, 100.0, 25.0, ts.TrendParams(max_positions=2, max_total_risk=0.02))
    assert len(mp.constraints(over)) == 3


def test_alternatives_compare_and_never_rebalance(market):
    close, _volume = market
    day, c, pos = _book(close, usd=(20.0, 20.0, 20.0))
    st = mp.state(day, pos, c, 100.0, 40.0, P)
    alt = mp.alternatives(st, c, P)
    assert alt["status"] == "OK" and alt["rebalance"] == "NO_REBALANCE" and "gagnants" in alt["rebalance_reason"]
    assert set(alt["methods"]) == {"rule", *mp.METHODS} and alt["methods"]["rule"]["turnover"] == 0
    assert all(m["errors"] == [] for m in alt["methods"].values())
    assert alt["methods"]["min_variance"]["vol_annual"] <= alt["methods"]["rule"]["vol_annual"] + 1e-9
    one = mp.state(day, {"eth": pos["eth"]}, c, 100.0, 80.0, P)
    assert mp.alternatives(one, c, P)["status"] == "NOT_APPLICABLE"
    _d, c2, big = _book(close, usd=(40.0, 40.0, 10.0))
    heavy = mp.state(day, {a: big[a] for a in ("eth", "xrp")}, c2, 100.0, 20.0, P)
    assert mp.alternatives(heavy, c2, P)["status"] == "INFEASIBLE"


def test_decision_and_its_contract(market):
    close, _volume = market
    day, c, pos = _book(close)
    st = mp.state(day, pos, c, 100.0, 25.0, P)
    dec = mp.decide(st, mp.alternatives(st, c, P))
    assert dec.decision == "REVIEW" and dec.violations and not dec.authorized
    assert contrats.validate("PortfolioDecision", dec.as_dict()).valid
    empty = mp.state(day, {}, c, 100.0, 100.0, P)
    assert mp.decide(empty, {}).decision == "NO_ALLOCATE"
    view = mp.compact(empty, mp.decide(empty, {}), {})
    assert mp.describe(view) == "aucune position : tout en liquidités"
    ok_view = mp.compact(st, dec, mp.alternatives(st, c, P))
    assert "3 position(s)" in mp.describe(ok_view) and "à regarder" in mp.describe(ok_view)
    assert "impossible" in mp.describe({"day": day, "error": "x"}) and "pas encore" in mp.describe(None)
    good = dec.as_dict()
    for change in ({"decision": "HOLD"}, {"decision": "NO_ALLOCATE"}, {"authorized": True}, {"exposure": 1.5},
                   {"rebalance": "VENDRE"}, {"positions": -1}):
        with pytest.raises(ContractError):
            PortfolioDecision(**{**good, **change, "violations": tuple(good["violations"])})


# ---------- Étude de la taille des achats (§13, §54, §89-90) ----------

def test_sizing_variants_change_only_the_size(market):
    close, volume = market
    a, b = str(close.index[SIM_FROM].date()), str(close.index[-1].date())
    plain = ts.backtest(close, volume, P, a, b)
    same = ts.backtest(close, volume, P, a, b, hooks=mp.sizing_hooks(close, P, "regle"))
    assert plain.metrics == same.metrics
    eq = ts.backtest(close, volume, P, a, b, hooks=mp.sizing_hooks(close, P, "egal"))
    assert [(t["asset"], t["entry_date"]) for t in eq.trades][:3] == [(t["asset"], t["entry_date"])
                                                                      for t in plain.trades][:3]
    assert eq.metrics != plain.metrics
    with pytest.raises(ValueError):
        ts.backtest(close, volume, P, a, b, hooks=mp.sizing_hooks(close, P, "magique"))


def test_study_reports_and_keeps_the_rule_unless_clearly_beaten(market, monkeypatch):
    close, volume = market
    monkeypatch.setattr(mp.mb, "IS_PERIOD", (str(close.index[0].date()), str(close.index[400].date())))
    monkeypatch.setattr(mp.mb, "OOS_START", str(close.index[401].date()))
    s = mp.study(close, volume, P)
    assert set(s["schemes"]) == set(mp.SCHEMES) and all(len(r) == 2 for r in s["schemes"].values())
    assert set(s["better"]) <= set(mp.SCHEMES[1:]) and s["verdict"]
    text = mp.render_study(s, None)
    assert text.startswith("# Portefeuille") and "## Verdict" in text and "parité de risque" in text


# ---------- Dans le bot ----------

def test_the_portfolio_engine_never_changes_a_trade(logger, monkeypatch):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 50)
    view = bot.state["portefeuille"]
    assert view["decision"] in contrats.PORTFOLIO_DECISIONS and "error" not in view
    ref, fr_ = make_bot("paper", close, logger)
    assert ref.boot()
    monkeypatch.setattr(mp, "state", lambda *a, **k: 1 / 0)                  # moteur en panne
    _run(ref, fr_, close, volume, SIM_FROM, SIM_FROM + 50)
    assert "error" in ref.state["portefeuille"]
    key = lambda b: [(t["asset"], t["date"], round(t["pnl"], 8)) for t in b.state["trades"]]  # noqa: E731
    assert key(bot) == key(ref) and sorted(bot.state["paper"]["holdings"]) == sorted(ref.state["paper"]["holdings"])


def test_rachelle_and_the_report_read_the_portfolio(market):
    close, _volume = market
    day, c, pos = _book(close)
    st = mp.state(day, pos, c, 100.0, 25.0, P)
    view = mp.compact(st, mp.decide(st, mp.alternatives(st, c, P)), mp.alternatives(st, c, P))
    r = asst.local_answer("que dit le moteur de portefeuille ?", {"portefeuille": view})["answer"]
    assert "Moteur de portefeuille" in r and "3 position(s)" in r and "rééquilibre jamais" in r
    line = next(x for x in report_health.analysis_checks(GuardConfig(), {"portefeuille": view})
                if x["label"] == "Moteur de portefeuille")
    assert line["ok"] is None and "à regarder" in line["detail"]


def test_portfolio_command(tmp_path, monkeypatch, market, capsys):
    close, volume = market
    from trendguard import evolution, systeme
    monkeypatch.setattr(evolution, "load_history", lambda *a, **k: (close, volume))
    monkeypatch.setattr(systeme, "read_state", lambda *a, **k: {})
    monkeypatch.setattr(mp.mb, "IS_PERIOD", (str(close.index[0].date()), str(close.index[400].date())))
    monkeypatch.setattr(mp.mb, "OOS_START", str(close.index[401].date()))
    assert mp.main([]) == 0 and "jamais évalué" in capsys.readouterr().out
    out = tmp_path / "PORTEFEUILLE.md"
    assert mp.main(["etude", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Portefeuille")
