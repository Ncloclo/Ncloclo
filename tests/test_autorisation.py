"""Prompt maître, étape 15 (docs/MOTEUR_AUTORISATION.md) : moteur
d'autorisation. Refus par défaut ; matrice des droits ; conditions du
moment ; séparation des tâches ; aucune IA ni agent avec un droit critique ;
délégations sans re-délégation ; autorisation d'achat à usage unique ;
chaque action du panneau vérifiée ; dans le bot : mêmes réponses que la
porte, aucun trade changé."""

import logging
from datetime import datetime, timedelta, timezone

import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import autorisation as au
from trendguard import contrats, porte, report_health
from trendguard import trend_strategy as ts
from trendguard.config import GuardConfig
from trendguard.contrats import AuthorizationDecision, ContractError, OrderIntent

NOW = datetime(2026, 10, 8, 0, 3, tzinfo=timezone.utc)
DAY = "2026-10-07"
FULL_LIVE = {"live": True, "live_armed": True, "live_gate_open": True, "risk_assessment_valid": True,
             "porte_approved": True, "authorization_valid": True}


# ---------- Refus par défaut, matrice, conditions (§3-10) ----------

def test_deny_by_default():
    for who, act in (("inconnu", "READ"), ("vous", "VOLER"), ("rachelle", "ARM_LIVE"), ("ia", "TRADE_PAPER"),
                     ("comite", "AUTHORIZE_BUY"), ("nuage", "MERGE_CODE"), ("evolution", "CHANGE_RISK"),
                     ("savoir", "TRADE_LIVE"), ("libre", "TRADE_LIVE"), ("panneau", "SET_SECRETS")):
        d = au.decide(who, act, now=NOW)
        assert not d.allowed and d.reasons, (who, act)
    assert au.decide("rachelle", "READ", now=NOW).allowed
    assert au.decide("nuage", "PROPOSE_CODE", now=NOW).allowed


def test_conditions_must_all_hold_exactly():
    assert au.decide("bot", "TRADE_LIVE", context=FULL_LIVE, now=NOW).allowed
    for missing in FULL_LIVE:
        ctx = {**FULL_LIVE, missing: False}
        d = au.decide("bot", "TRADE_LIVE", context=ctx, now=NOW)
        assert not d.allowed and any("condition non remplie" in r for r in d.reasons), missing
    assert not au.decide("bot", "TRADE_LIVE", context={**FULL_LIVE, "live_armed": "oui"}, now=NOW).allowed
    assert not au.decide("vous", "ARM_LIVE", now=NOW).allowed                    # deux réglages exigés
    assert au.decide("vous", "ARM_LIVE", context={"two_settings": True}, now=NOW).allowed
    assert not au.decide("maintenance", "INSTALL_UPDATE", context={"merged_by_owner": True, "ci_green": True,
                                                                   "fast_forward": True}, now=NOW).allowed
    assert au.decide("savoir", "DEFER_BUY", context={"binance_announcement": True}, now=NOW).allowed


# ---------- Séparation des tâches, délégation (§15-16, §26-30) ----------

def test_separation_of_duties_and_delegations():
    assert au.separation_of_duties() == []
    assert au.holders("PROPOSE_BUY") == ["regle"] and au.holders("AUTHORIZE_BUY") == ["porte"]
    assert au.holders("TRADE_LIVE") == ["bot"]
    for act in ("ARM_LIVE", "CHANGE_RISK", "KILL_RESET", "SAFE_MODE_OFF", "SET_SECRETS", "MERGE_CODE"):
        assert au.holders(act) == ["vous"], act
    saved = dict(au.ROLES)
    try:
        au.ROLES["ASSISTANT"] = {**saved["ASSISTANT"], "AUTHORIZE_BUY": ()}
        au.ROLES["STRATEGY"] = {**saved["STRATEGY"], "AUTHORIZE_BUY": ()}
        problems = au.separation_of_duties()
        assert any("propose et autorise" in p for p in problems) and any("rachelle" in p for p in problems)
    finally:
        au.ROLES.clear()
        au.ROLES.update(saved)
    bad = au.DELEGATIONS + (("panneau", "rachelle", ("READ",)), ("vous", "panneau", ("TRADE_LIVE",)))
    old = au.DELEGATIONS
    try:
        au.DELEGATIONS = bad
        problems = au.separation_of_duties()
        assert any("re-délégation" in p for p in problems) and any("au-delà" in p for p in problems)
    finally:
        au.DELEGATIONS = old


# ---------- Contrat (§37) ----------

def test_the_decision_contract_refuses_what_it_must():
    d = au.decide("bot", "TRADE_LIVE", context=FULL_LIVE, now=NOW)
    assert contrats.validate("AuthorizationDecision", d.as_dict()).valid
    good = {k: (tuple(tuple(x) if isinstance(x, list) else x for x in v) if isinstance(v, list) else v)
            for k, v in d.as_dict().items()}
    for change in ({"reasons": ()}, {"conditions": (("live", False),)}, {"expires_at": good["created_at"]},
                   {"principal_type": "AI_MODEL"}, {"principal_type": "AGENT"}):
        with pytest.raises(ContractError):
            AuthorizationDecision(**{**good, **change})


# ---------- Autorisation d'achat : 5 minutes, usage unique (§19-24) ----------

def test_a_buy_authorization_expires_and_cannot_be_replayed():
    intent = OrderIntent(asset="eth", qty=1.0, entry=100.0, stop=90.0, cost=100.1, risk_quote=10.0, decision_day=DAY)
    pf = porte.Portfolio(equity=1000.0, cash=1000.0, invested=0.0, held_risk=(), risk_mult=1.0, expected_day=DAY,
                         universe=frozenset({"eth"}), allowed=frozenset({"eth"}))
    decision = porte.check(intent, pf, ts.TrendParams(), NOW)
    auth = porte.authorize(decision, pf, NOW)
    assert auth.valid_at(NOW) and not auth.valid_at(NOW + timedelta(seconds=porte.AUTH_SECONDS))
    ctx = au.trade_context(pf, decision, auth, NOW)
    assert au.decide("bot", "TRADE_PAPER", context=ctx, now=NOW).allowed
    late = au.trade_context(pf, decision, auth, NOW + timedelta(minutes=6))
    assert not au.decide("bot", "TRADE_PAPER", context=late, now=NOW).allowed
    again = porte.Portfolio(**{**pf.__dict__, "bought_today": frozenset({intent.idempotency_key})})
    assert porte.check(intent, again, ts.TrendParams(), NOW).status == "REJECTED"   # rejouer : refusé


# ---------- Panneau : chaque action vérifiée ----------

def test_every_panel_action_has_its_right():
    from panel import server
    for path, act in server.POST_ACTIONS.items():
        assert au.decide("panneau", act, path, {"session_ok": True}).allowed, path
        assert not au.decide("panneau", act, path, {}).allowed
    assert not au.decide("panneau", server.POST_ACTIONS.get("/api/secrets", "INCONNUE"), "", {"session_ok": True}).allowed


# ---------- Dans le bot ----------

def test_in_the_bot_the_rights_agree_with_the_gate_and_change_nothing(monkeypatch):
    lg = logging.getLogger("test.autorisation")
    lg.setLevel(logging.ERROR)
    close, volume = synthetic_market()
    seen = []
    real = au.decide

    def spy(*a, **k):
        d = real(*a, **k)
        seen.append(d.allowed)
        return d

    monkeypatch.setattr(au, "decide", spy)
    bot, fb = make_bot("paper", close, lg)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 60)
    assert seen and not (bot.state.get("autorisation") or {}).get("mismatch")
    ref, fr_ = make_bot("paper", close, lg)
    assert ref.boot()
    monkeypatch.setattr(au, "decide", lambda *a, **k: 1 / 0)               # moteur en panne
    _run(ref, fr_, close, volume, SIM_FROM, SIM_FROM + 60)
    key = lambda b: [(t["asset"], t["date"], round(t["pnl"], 8)) for t in b.state["trades"]]  # noqa: E731
    assert key(bot) == key(ref) and sorted(bot.state["paper"]["holdings"]) == sorted(ref.state["paper"]["holdings"])


def test_rachelle_the_report_and_the_command(capsys):
    view = {"day": DAY, "checked": 3, "mismatch": []}
    r = asst.local_answer("qui peut armer le reel ?", {"autorisation": view})["answer"]
    assert "Moteur d'autorisation" in r and "Vous seul" in r and "je n'agis pas" in r
    line = next(x for x in report_health.analysis_checks(GuardConfig(), {"autorisation": view})
                if x["label"] == "Moteur d'autorisation")
    assert line["ok"] is None and "mêmes réponses" in line["detail"]
    assert au.main([]) == 0 and "Séparation des tâches : tenue" in capsys.readouterr().out
    assert au.main(["verifier", "rachelle", "arm_live"]) == 1 and "REFUSÉ" in capsys.readouterr().out
    assert "aucun achat" in au.describe(None)
