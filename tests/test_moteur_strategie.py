"""Prompt maître, étape 9 (docs/MOTEUR_STRATEGIE.md) : moteur de stratégie.
La règle en fiche déclarative (langage sûr, sans eval), versionnée et
verrouillée sur le code ; validation jusqu'à « prête pour le backtest » ;
mêmes signaux que la règle exécutée ; décisions expliquées ; cycle de vie
sans raccourci ; Kelly plafonné ; consultatif : il ne change aucun trade."""

import copy
import dataclasses
import logging

import numpy as np
import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import contrats, report_health
from trendguard import moteur_strategie as ms
from trendguard import trend_strategy as ts
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, StrategyDecision

P = ts.TrendParams()


@pytest.fixture
def logger():
    lg = logging.getLogger("test.strategie")
    lg.setLevel(logging.ERROR)
    return lg


@pytest.fixture(scope="module")
def market():
    return synthetic_market()


def _spec(**entry):
    s = copy.deepcopy(ms.TREND)
    s["entry"] = entry or s["entry"]
    return s


# ---------- Langage déclaratif sûr (§6, §54) ----------

def test_the_spec_compiles_and_refuses_anything_outside_the_language():
    c = ms.compile_spec(ms.TREND, P)
    assert [x.code for x in c.conditions()] == ["NO_BREAKOUT", "NO_MOMENTUM", "SHORT_HISTORY", "LOW_LIQUIDITY",
                                                 "NO_VOLATILITY"]
    assert c.params["min_history"] == 250 and c.regimes == ("BULL",)
    ok = {"if": ["close", ">", "prior_high"], "code": "NO_BREAKOUT", "label": "x"}
    bad = [
        {"if": ["__import__('os')", ">", 0], "code": "NO_BREAKOUT", "label": "x"},   # code injecté : refusé
        {"if": ["close", "in", "prior_high"], "code": "NO_BREAKOUT", "label": "x"},
        {"if": ["close", ">", "$inconnu"], "code": "NO_BREAKOUT", "label": "x"},
        {"if": ["close", ">", float("inf")], "code": "NO_BREAKOUT", "label": "x"},
        {"if": ["close", ">", True], "code": "NO_BREAKOUT", "label": "x"},
        {"if": ["close", ">", 0], "code": "PEUT_ETRE", "label": "x"},
        {"if": ["close", ">", 0], "code": "NO_BREAKOUT"},
        {"not": ok},
        {"all": []},
        {"all": [{"all": [{"all": [{"all": [{"all": [ok]}]}]}]}]},                     # trop imbriqué
        {"all": [ok] * (ms.MAX_CONDITIONS + 1)},
    ]
    for node in bad:
        with pytest.raises(ms.SpecError):
            ms.compile_spec(_spec(**node) if "if" not in node else _spec(all=[node]), P)
    with pytest.raises(ms.SpecError):
        ms.compile_spec(dict(ms.TREND, regime={"allowed": ["LUNE"], "code": "REGIME_BLOCK"}), P)


def test_an_unknown_value_never_makes_a_condition_true():
    c = ms.compile_spec(ms.TREND, P)
    good = {"close": 110.0, "prior_high": 100.0, "mom": 0.5, "age": 400, "vol30": 1e8, "vol": 2.0}
    assert bool(c.evaluate(good, True)[0]) and not bool(c.evaluate(good, False)[0])
    for k, v in (("prior_high", np.nan), ("vol30", np.inf), ("mom", np.nan), ("vol", 0.0)):
        assert not bool(c.evaluate(dict(good, **{k: v}), True)[0]), k
        assert ts.entry_signal(dict(good, **{k: v}), P) is False


# ---------- Équivalence avec la règle exécutée ----------

def test_the_spec_gives_exactly_the_signals_of_the_executed_rule(market):
    close, volume = market
    eq = ms.equivalence(ms.compile_spec(ms.TREND, P), close, volume, P)
    assert eq["mismatches"] == 0 and eq["signals"] > 20 and eq["checked"] == close.size
    other = dataclasses.replace(P, breakout_n=20, min_history=150)      # mêmes règles, autres réglages
    assert ms.equivalence(ms.compile_spec(ms.TREND, other), close, volume, other)["mismatches"] == 0
    wrong = _spec(all=ms.TREND["entry"]["all"][:2])                     # une fiche qui oublie des conditions
    assert ms.equivalence(ms.compile_spec(wrong, P), close, volume, P)["mismatches"] > 0


# ---------- Validation (§41, §66) ----------

def test_validation_says_ready_for_backtest_only_when_everything_holds(market):
    close, volume = market
    v = ms.validate(ms.TREND, P, close, volume)
    assert v["status"] == "READY_FOR_BACKTEST" and all(ok for _n, ok, _d in v["steps"]), v["steps"]
    assert v["data"]["hash"] and v["spec_hash"] == ms.spec_hash(ms.TREND)
    blind = ms.validate(ms.TREND, P)
    assert blind["status"] == "BLOCKED" and any("sans données" in d for _n, ok, d in blind["steps"] if not ok)
    stale = dict(ms.TREND, locks={"features": "0" * 16, "rules": ms.TREND["locks"]["rules"]})
    v = ms.validate(stale, P, close, volume)
    assert v["status"] == "BLOCKED" and any("nouvelle version" in d for n, ok, d in v["steps"] if n == "Verrous du code")
    mine = dataclasses.replace(P, max_positions=20, max_total_risk=0.10, dd_throttle=((0.10, 0.5),))
    v = ms.validate(ms.spec_for(mine), mine, close, volume)                # risque choisi par le propriétaire
    assert v["status"] == "READY_FOR_BACKTEST" and v["spec_id"] == "trendguard.cassure.prudent"
    assert "choisis par vous : max_positions 20 (recherche : 8), max_total_risk 0,1 (recherche : 0,06)" in         dict((n, d) for n, _ok, d in v["steps"])["Paramètres"]
    assert ms.validate(ms.TREND, dataclasses.replace(P, max_positions=30), close, volume)["status"] == "BLOCKED"
    far = ms.validate(ms.TREND, dataclasses.replace(P, breakout_n=80), close, volume)
    assert far["status"] == "BLOCKED" and any("hors de la plage" in d for _n, _ok, d in far["steps"])
    free = ms.validate(ms.TREND, dataclasses.replace(P, fee=0.0), close, volume)
    assert any(n == "Coûts" and not ok for n, ok, _d in free["steps"])
    assert ms.spec_for(P)["id"] == "trendguard.cassure"
    assert ms.spec_for(dataclasses.replace(P, dd_throttle=((0.10, 0.5),)))["id"] == "trendguard.cassure.prudent"
    assert ms.spec_for(dataclasses.replace(P, dd_throttle=((0.2, 0.3),)))["status"] == "DEVELOPMENT"


def test_the_code_locks_match_the_executed_code():
    """Changer un indicateur ou une règle de trend_strategy.py change ces
    empreintes : la fiche doit alors changer de version (et ses verrous)."""
    assert ms.TREND["locks"] == ms._locks_now() == ms.PRUDENT["locks"]


def test_a_look_into_the_future_is_caught(market, monkeypatch):
    close, volume = market
    assert ms.leakage(close, volume, P) == []
    honest = ts.asset_features

    def leaky(c, p, v=None):
        f = honest(c, p, v)
        f["prior_high"] = c.shift(-1)                                             # regarde demain
        return f
    monkeypatch.setattr(ts, "asset_features", leaky)
    v = ms.validate(ms.TREND, P, close, volume)
    assert v["status"] == "BLOCKED" and any(n == "Fuite vers le futur" and not ok for n, ok, _d in v["steps"])


# ---------- Décisions expliquées (§37-39) ----------

def test_each_decision_says_which_condition_holds_or_fails():
    c = ms.compile_spec(ms.TREND, P)
    good = {"close": 110.0, "prior_high": 100.0, "mom": 0.5, "age": 400, "vol30": 1e8, "vol": 2.0}
    d = ms.decide(c, "2026-09-27", "eth", good, True, False, False, None, 1000.0, P)
    assert d.decision == "TRADE_CANDIDATE" and d.status == "STRATEGY_READY" and not d.authorized
    assert d.stop_loss == pytest.approx(110 - 3 * 2.0) and d.expected_cost == pytest.approx(0.004)
    bought = ms.decide(c, "2026-09-27", "eth", good, True, False, False, {"cost": 50.0, "stop": 104.0}, 1000.0, P)
    assert bought.position_size == pytest.approx(0.05) and bought.stop_loss == 104.0
    no = ms.decide(c, "2026-09-27", "eth", dict(good, close=95.0, vol30=1e6), False, False, False, None, 1000.0, P)
    assert no.decision == "NO_TRADE" and no.reasons == ("REGIME_BLOCK", "NO_BREAKOUT", "LOW_LIQUIDITY")
    assert "non : BTC au-dessus de sa moyenne de 150 jours" in no.reason_texts[0]
    assert "(95 > 100)" in no.reason_texts[1] and "5 000 000 dollars" in no.reason_texts[2]
    held = ms.decide(c, "2026-09-27", "eth", good, True, True, True, None, 1000.0, P)
    assert held.decision == "EXIT" and held.status == "IN_POSITION"
    assert contrats.validate("StrategyDecision", no.as_dict()).valid
    with pytest.raises(ContractError):
        dataclasses.replace(no, authorized=True)                                  # jamais une autorisation
    with pytest.raises(ContractError):
        dataclasses.replace(no, reasons=(), reason_texts=())                      # « pas de trade » dit pourquoi
    with pytest.raises(ContractError):
        dataclasses.replace(d, reasons=("NO_BREAKOUT",), reason_texts=("x",))    # candidate incomplète
    with pytest.raises(ContractError):
        StrategyDecision("s", "1", "binance:spot:ETH-USDT", "2026-09-27", "BULL", "LONG", True, False, (), None,
                         -0.1, None, None, "STRATEGY_READY", "TRADE_CANDIDATE")    # coût négatif


def test_the_day_view_and_its_explanations(market):
    close, volume = market
    cols, reg = ts.precompute(close, volume, P)
    i = next(k for k in range(SIM_FROM, len(close)) if reg[k] and any(
        ts.entry_signal({x: c[x][k] for x in c}, P) for c in cols.values()))
    snap = {a: {k: float(c[k][i]) for k in c} for a, c in cols.items()}
    day = str(close.index[i].date())
    view = ms.day_view(day, snap, True, set(), set(), {}, 10_000.0, P)
    assert view["candidates"] and not view["mismatch"] and view["held"] == 0 and view["regime"] == "BULL"
    a = view["candidates"][0]
    assert "toutes les conditions d'achat remplies" in ms.explain(view, a) and "pas évaluée" in ms.explain(view, "zzz")
    kept = ms.day_view(day, snap, True, {a}, set(), {}, 10_000.0, P)
    assert kept["held"] == 1 and a not in kept["candidates"] and "détenue" in ms.explain(kept, a)
    bear = ms.day_view(day, snap, False, set(), set(), {}, 10_000.0, P)
    assert not bear["candidates"] and bear["reasons"]["REGIME_BLOCK"] == len(snap)


# ---------- Cycle de vie, Kelly, sur-ajustement (§5, §15, §27) ----------

def test_a_strategy_never_jumps_to_live():
    path = ["DRAFT", "DEVELOPMENT", "VALIDATING", "BACKTEST_PENDING", "BACKTESTING", "BACKTEST_PASSED",
            "ROBUSTNESS_PENDING", "ROBUSTNESS_PASSED", "PAPER_PENDING", "PAPER", "PRODUCTION_CANDIDATE", "APPROVED"]
    for a, b in zip(path, path[1:]):
        assert ms.transition(a, b) == b
    for a, b in (("DRAFT", "APPROVED"), ("DRAFT", "PAPER"), ("BACKTEST_FAILED", "PAPER"), ("REJECTED", "DRAFT"),
                 ("PAPER", "LIVE")):
        with pytest.raises(ValueError):
            ms.transition(a, b)
    assert set(ms.TRANSITIONS) == set(ms.STATUSES) and all(s in ms.STATUSES for x in ms.TRANSITIONS.values()
                                                            for s in x)
    assert all(x["status"] == "PAPER" for x in ms.REGISTRY)            # rien n'est approuvé pour le réel


def test_kelly_is_fractional_capped_and_never_changes_the_size():
    k = ms.kelly(0.5, 2.0, 0.01)
    assert k["full"] == pytest.approx(0.25) and k["rows"][1]["risk"] == pytest.approx(0.0625)
    assert all(r["capped"] <= 0.02 for r in k["rows"]) and k["times_below_full"] == pytest.approx(25)
    assert ms.kelly(0.3, 1.0, 0.01)["full"] < 0 and all(r["risk"] == 0 for r in ms.kelly(0.3, 1.0, 0.01)["rows"])


def test_overfitting_risk_reads_the_evidence():
    assert ms.overfitting_risk(7, 8, 300, 0.9, 1.0)["level"] == "LOW"
    mid = ms.overfitting_risk(7, 8, 80, 0.7, 1.0)
    assert mid["level"] == "MEDIUM" and len(mid["reasons"]) == 2
    high = ms.overfitting_risk(12, 12, 20, 0.3, 0.4)
    assert high["level"] == "HIGH" and any("voisins" in r for r in high["reasons"])


def test_the_spec_reads_in_plain_french():
    text = ms.render_spec(ms.TREND, P)
    assert "cassure du plus haut des 30 clôtures précédentes" in text and "| `breakout_n` | 20 à 50 jours" in text
    assert "sans levier, sans vente à découvert" in text and ms.TREND["locks"]["rules"] in text
    v = ms.render_validation({"spec_id": "x", "version": "1.0.0", "status": "BLOCKED",
                              "steps": [("Schéma", False, "champ requis absent : id")]})
    assert "BLOQUÉE" in v and "✗ Schéma" in v


# ---------- Dans le bot : consultatif ----------

def test_the_strategy_engine_never_changes_a_trade(logger, monkeypatch):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 50)
    sg = bot.state["strategie"]
    assert sg["id"] == "trendguard.cassure" and not sg["mismatch"] and set(sg["assets"]) <= set(close.columns)
    assert bot.state["trades"] or bot.state["paper"]["holdings"]
    monkeypatch.setattr(ms, "day_view", lambda *a, **k: 1 / 0)            # moteur en panne : rien ne change
    ref, fr_ = make_bot("paper", close, logger)
    assert ref.boot()
    _run(ref, fr_, close, volume, SIM_FROM, SIM_FROM + 50)
    key = lambda b: [(t["asset"], t["date"], round(t["pnl"], 8)) for t in b.state["trades"]]  # noqa: E731
    assert key(bot) == key(ref) and sorted(bot.state["paper"]["holdings"]) == sorted(ref.state["paper"]["holdings"])
    assert "strategie" not in ref.state


def test_rachelle_and_the_report_tell_why_the_rule_does_not_buy():
    view = {"day": "2026-09-27", "id": "trendguard.cassure", "version": "1.0.0", "candidates": ["dot"],
            "reasons": {"NO_BREAKOUT": 17, "LOW_LIQUIDITY": 4}, "mismatch": [], "no_trade": 19,
            "assets": {"dot": {"d": "TRADE_CANDIDATE", "why": []},
                       "eth": {"d": "NO_TRADE", "why": ["non : cassure du plus haut des 30 clôtures précédentes "
                                                        "(2 689 > 2 776)"]}}}
    r = asst.local_answer("pourquoi le bot n'achete pas ETH ?", {"strategie": view})["answer"]
    assert "ETH : pas de trade" in r and "2 689 > 2 776" in r and "candidates : DOT" in r
    assert "python trendguard_bot.py regle" in asst.local_answer("fiche de la regle", {})["answer"]
    checks = report_health.analysis_checks(GuardConfig(), {"strategie": view})
    line = next(c for c in checks if c["label"] == "Moteur de stratégie")
    assert "1 candidate(s), 19 « pas de trade »" in line["detail"] and "fidèle" in line["detail"] and line["ok"] is None
