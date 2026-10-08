"""Prompt maître, étape 14 (docs/MOTEUR_POLITIQUE.md) : moteur de
politiques. Registre déclaratif et versionné, langage fermé, politique
illisible = blocage, conflits résolus par la gravité, bornes (seuil ± ε),
décision expliquée qui expire ; la même décision que la porte d'exécution
sur des milliers de cas ; dans le bot : 0 écart, aucun trade changé."""

import dataclasses
import logging
from datetime import datetime, timezone

import numpy as np
import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import contrats, porte, report_health
from trendguard import politique as po
from trendguard import trend_strategy as ts
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, OrderIntent, PolicyDecision, SafeModeState

NOW = datetime(2026, 10, 8, 0, 3, tzinfo=timezone.utc)
DAY = "2026-10-07"
P = ts.TrendParams()


def _case(**kw):
    intent = dict(asset="eth", qty=1.0, entry=100.0, stop=90.0, cost=100.1, risk_quote=10.0, decision_day=DAY)
    pf = dict(equity=1000.0, cash=1000.0, invested=0.0, held_risk=(), risk_mult=1.0, expected_day=DAY,
              universe=frozenset({"eth", "btc"}), allowed=frozenset({"eth", "btc"}))
    for k, v in kw.items():
        (intent if k in intent else pf)[k] = v
    return OrderIntent(**intent), porte.Portfolio(**pf)


# ---------- Registre, langage (§8-10, §47-48) ----------

def test_the_registry_is_coherent_and_covers_every_check_of_the_gate():
    assert po.conflicts() == []
    ids = [p["id"] for p in po.REGISTRY]
    assert len(ids) == len(set(ids)) and all(p["version"] == "1.0.0" for p in po.REGISTRY)
    bad = list(po.REGISTRY) + [dict(po.REGISTRY[0], id="POL-X", require={"field": "humeur", "op": "==", "value": 1})]
    assert any("humeur" in x for x in po.conflicts(bad))
    assert any("en double" in x for x in po.conflicts(list(po.REGISTRY) + [po.REGISTRY[0]]))
    text = po.render_registry()
    assert "POL-KILL-SWITCH" in text and "Registre cohérent" in text


def test_an_illegible_policy_blocks_never_allows():
    intent, pf = _case()
    broken = [dict(po.REGISTRY[0], require={"field": "humeur", "op": "==", "value": 1})]
    d = po.evaluate(intent, pf, P, NOW, registry=broken)
    assert d.decision == "BLOCK" and "illisible" in d.violations[0]
    weird = [dict(po.REGISTRY[0], require={"field": "live", "op": "≈", "value": False})]
    assert po.evaluate(intent, pf, P, NOW, registry=weird).decision == "BLOCK"


# ---------- Décisions, conflits, bornes, explication (§11, §34-41, §50) ----------

def test_decisions_conflicts_and_explanations():
    intent, pf = _case()
    ok = po.evaluate(intent, pf, P, NOW)
    assert ok.decision == "ALLOW" and not ok.violations and len(ok.evaluated) == len(po.REGISTRY)
    i2, pf2 = _case(halted=True, garde_blocked=("BTC bouge de 15 %",))
    d = po.evaluate(i2, pf2, P, NOW)
    assert d.decision == "FREEZE_ACCOUNT" and len(d.violations) == 2       # la plus grave l'emporte
    i3, pf3 = _case(live=True, live_armed=False, safe_mode=SafeModeState(active=True, reason="test"))
    assert po.evaluate(i3, pf3, P, NOW).decision == "SAFE_MODE"
    i4, pf4 = _case(live=True, live_armed=False)
    assert po.evaluate(i4, pf4, P, NOW).decision == "REQUIRE_HUMAN_APPROVAL"
    i5, pf5 = _case(risk_quote=20.0)
    d5 = po.evaluate(i5, pf5, P, NOW)
    assert d5.decision == "BLOCK" and "POL-TRADE-RISK v1.0.0" in d5.violations[0] and "seuil" in d5.violations[0]
    i6, pf6 = _case(risk_mult=0.5, risk_quote=5.0)
    d6 = po.evaluate(i6, pf6, P, NOW, events_soon=1)
    assert d6.decision == "REDUCE_SIZE" and len(d6.warnings) == 2 and po.porte_status(d6) == "APPROVED"
    assert po.porte_status(d) == "EMERGENCY_BLOCK" and po.porte_status(d5) == "REJECTED"


@pytest.mark.parametrize("eps,allowed", [(-1e-6, True), (0.0, True), (1e-6, False)])
def test_boundaries_threshold_plus_or_minus_epsilon(eps, allowed):
    cap = P.risk_pct * 1000.0 * (1 + porte.MARGIN)
    intent, pf = _case(risk_quote=cap + 1e-9 + eps)
    assert (po.evaluate(intent, pf, P, NOW).decision == "ALLOW") is allowed
    assert (porte.check(intent, pf, P, NOW).status == "APPROVED") is allowed
    q_intent, q_pf = _case(data_quality=porte.QUALITY_MIN + eps)
    assert (po.evaluate(q_intent, q_pf, P, NOW).decision == "ALLOW") is (eps >= 0)


def test_the_policies_give_the_decision_of_the_gate_on_thousands_of_cases():
    rng = np.random.default_rng(14)
    mismatch = []
    for k in range(3000):
        held = tuple((a, float(rng.uniform(5, 15))) for a in ("btc", "xrp", "ada")[:rng.integers(0, 4)])
        entry = float(rng.uniform(10, 200))
        intent = OrderIntent(asset=str(rng.choice(["eth", "btc", "doge"])), qty=float(rng.uniform(0.01, 2)),
                             entry=entry, stop=entry * float(rng.uniform(0.7, 1.05)),
                             cost=float(rng.uniform(5, 400)), risk_quote=float(rng.uniform(1, 25)),
                             decision_day=str(rng.choice([DAY, "2026-10-06"])))
        live = bool(rng.random() < 0.3)
        pf = porte.Portfolio(
            equity=float(rng.choice([0.0, 500.0, 1000.0])), cash=float(rng.uniform(0, 1200)), invested=0.0,
            held_risk=held, risk_mult=float(rng.choice([0.5, 1.0])), expected_day=DAY,
            universe=frozenset({"eth", "btc", "xrp", "ada"}),
            allowed=frozenset([["eth", "btc"], ["eth"], ["btc", "xrp", "ada"]][int(rng.integers(0, 3))]), vetoed=frozenset(["btc"] if rng.random() < 0.2 else []),
            bought_today=frozenset([f"{DAY}:eth:BUY"] if rng.random() < 0.1 else []), halted=bool(rng.random() < 0.1),
            garde_blocked=("garde",) if rng.random() < 0.1 else (),
            safe_mode=SafeModeState(active=bool(rng.random() < 0.1)), live=live, live_armed=bool(rng.random() < 0.5),
            production=None if rng.random() < 0.3 else (bool(rng.random() < 0.5), "porte du réel"),
            data_quality=None if rng.random() < 0.3 else float(rng.uniform(30, 100)),
            risk_engine=None if rng.random() < 0.3 else (bool(rng.random() < 0.8), "évaluation"))
        p = dataclasses.replace(P, max_positions=int(rng.integers(2, 6)))
        status = porte.check(intent, pf, p, NOW).status
        decision = po.evaluate(intent, pf, p, NOW)
        if po.porte_status(decision) != status:
            mismatch.append((k, status, decision.decision))
    assert not mismatch, mismatch[:5]


# ---------- Contrat (§35-37) ----------

def test_the_decision_contract_refuses_what_it_must():
    intent, pf = _case(halted=True)
    d = po.evaluate(intent, pf, P, NOW)
    assert contrats.validate("PolicyDecision", d.as_dict()).valid and not d.authorized
    good = d.as_dict()
    for change in ({"violations": ()}, {"decision": "ALLOW"}, {"authorized": True},
                   {"expires_at": good["created_at"]}, {"evaluated": ()}, {"decision": "PEUT-ÊTRE"},
                   {"decision": "REDUCE_SIZE", "violations": (), "warnings": ()}):
        with pytest.raises(ContractError):
            PolicyDecision(**{**good, **{k: tuple(v) if isinstance(v, list) else v for k, v in good.items()
                                         if isinstance(v, list)}, **change})


# ---------- Simulation des politiques de risque (§38-40) ----------

def test_risk_policies_are_simulated_on_history(monkeypatch):
    close, volume = synthetic_market()
    monkeypatch.setattr(po.mb, "IS_PERIOD", (str(close.index[0].date()), str(close.index[400].date())))
    monkeypatch.setattr(po.mb, "OOS_START", str(close.index[401].date()))
    s = po.simulate(close, volume, P)
    assert [v["label"] for v in s["variants"]][0] == "en vigueur" and len(s["variants"]) == 4
    assert all(len(v["rows"]) == 2 for v in s["variants"])
    hook = po._kill_hook(0.10)
    assert hook.risk_scale(0, 100.0, 100.0) == 1.0 and hook.risk_scale(1, 85.0, 100.0) == 0.0
    assert hook.risk_scale(2, 100.0, 100.0) == 0.0                         # reste déclenché


# ---------- Dans le bot ----------

def test_in_the_bot_the_policies_agree_with_the_gate_and_change_nothing(monkeypatch):
    lg = logging.getLogger("test.politique")
    lg.setLevel(logging.ERROR)
    close, volume = synthetic_market()
    agree = []
    real = po.compact

    def spy(decision, status):
        c = real(decision, status)
        agree.append(c["agree"])
        return c

    monkeypatch.setattr(po, "compact", spy)
    bot, fb = make_bot("paper", close, lg)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 60)
    assert agree and all(agree) and not (bot.state.get("politique") or {}).get("mismatch")
    ref, fr_ = make_bot("paper", close, lg)
    assert ref.boot()
    monkeypatch.setattr(po, "evaluate", lambda *a, **k: 1 / 0)             # moteur en panne
    _run(ref, fr_, close, volume, SIM_FROM, SIM_FROM + 60)
    key = lambda b: [(t["asset"], t["date"], round(t["pnl"], 8)) for t in b.state["trades"]]  # noqa: E731
    assert key(bot) == key(ref) and sorted(bot.state["paper"]["holdings"]) == sorted(ref.state["paper"]["holdings"])
    assert bot.state["trades"] or bot.state["paper"]["holdings"]


def test_rachelle_the_report_and_the_command(capsys):
    view = {"day": DAY, "checked": 2, "mismatch": [], "last": None}
    r = asst.local_answer("que disent les politiques ?", {"politique": view})["answer"]
    assert "Moteur de politiques" in r and "identiques à celles de la porte" in r and "dérogation" in r
    line = next(x for x in report_health.analysis_checks(GuardConfig(), {"politique": view})
                if x["label"] == "Moteur de politiques")
    assert line["ok"] is None
    bad = next(x for x in report_health.analysis_checks(GuardConfig(), {"politique": {**view, "mismatch": ["eth"]}})
               if x["label"] == "Moteur de politiques")
    assert bad["ok"] is False and "ÉCART" in bad["detail"]
    assert po.main([]) == 0 and "POL-RISK-ENGINE" in capsys.readouterr().out
    assert "aucun achat évalué" in po.describe(None)
