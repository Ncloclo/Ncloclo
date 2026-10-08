"""Prompt maître, étape 16 (docs/PORTE_EXECUTION.md) : porte d'exécution.
Une seule route vers Binance, après la porte et la validation finale ;
validation finale juste avant l'ordre (autorisation expirée, révoquée,
consommée, risque ou politique changés : blocage) ; règles de Binance en
réel seulement ; chaos de 10 000 demandes avec arrêt d'urgence, doublons et
changements d'état, jugé par un oracle ; une panne de la porte n'achète
jamais ; examen AC-001 à AC-050 et son contrat."""

import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import contrats, porte, report_health
from trendguard import porte_examen as pe
from trendguard import trend_strategy as ts
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, GateReadinessReport, OrderIntent, SafeModeState

NOW = datetime(2026, 10, 8, 0, 3, tzinfo=timezone.utc)
DAY = "2026-10-07"
P = ts.TrendParams()


def _checked():
    intent = OrderIntent(asset="eth", qty=1.0, entry=100.0, stop=90.0, cost=100.1, risk_quote=10.0, decision_day=DAY)
    pf = porte.Portfolio(equity=1000.0, cash=1000.0, invested=0.0, held_risk=(), risk_mult=1.0, expected_day=DAY,
                         universe=frozenset({"eth", "sol"}), allowed=frozenset({"eth", "sol"}))
    d = porte.check(intent, pf, P, NOW)
    return intent, pf, d, porte.authorize(d, pf, NOW), porte.fingerprint(pf)


def _lg():
    lg = logging.getLogger("test.porte_examen")
    lg.setLevel(logging.CRITICAL)
    return lg


# ---------- Aucune route ne contourne la porte (§2, P0-003) ----------

def test_one_single_route_to_binance_and_it_passes_the_gate(tmp_path):
    rt = pe.routes()
    assert rt["ok"] and len(rt["calls"]) == 1 and rt["calls"][0].startswith("trendguard/bot_execution.py:")
    for d in ("trendguard", "panel", "research"):
        (tmp_path / d).mkdir()
    (tmp_path / "trendguard_bot.py").write_text("", encoding="utf-8")
    src = (pe.ROOT / "trendguard" / "bot_execution.py").read_text(encoding="utf-8")
    (tmp_path / "trendguard" / "bot_execution.py").write_text(src, encoding="utf-8")
    assert pe.routes(tmp_path)["ok"]
    (tmp_path / "panel" / "raccourci.py").write_text("def f(eng):\n    eng.market_buy(1.0, 'x')\n", encoding="utf-8")
    assert not pe.routes(tmp_path)["ok"]                                   # une route de plus : vue
    (tmp_path / "panel" / "raccourci.py").unlink()
    (tmp_path / "trendguard" / "bot_execution.py").write_text(
        src.replace("self._final_validation(plan, now)", "True"), encoding="utf-8")
    assert not pe.routes(tmp_path)["ok"]                                   # validation finale sautée : vue


# ---------- Validation finale juste avant l'ordre (§21, §54-55) ----------

def test_final_validation_blocks_what_changed_since_the_check():
    intent, pf, d, auth, before = _checked()
    ok = porte.final_validation(intent, d, auth, before, pf, P, NOW)
    assert d.approved and ok.passed and not ok.revalidated
    assert contrats.validate("FinalValidationResult", ok.as_dict()).valid and "conformes" in porte.describe_final(ok)
    revoked = SafeModeState(active=True, reason="révoqué", activated_by="vous")
    for why, pf2, at in (("expirée", pf, NOW + timedelta(seconds=porte.AUTH_SECONDS)),
                         ("arrêt d'urgence", dataclasses.replace(pf, halted=True), NOW),
                         ("mode sûr", dataclasses.replace(pf, safe_mode=revoked), NOW),
                         ("consommée", dataclasses.replace(pf, bought_today=frozenset({intent.idempotency_key})), NOW),
                         ("risque", dataclasses.replace(pf, held_risk=(("sol", 60.0),)), NOW),
                         ("politique", dataclasses.replace(pf, vetoed=frozenset({"eth"})), NOW),
                         ("moteur de risque", dataclasses.replace(pf, risk_engine=(False, "bloqué")), NOW)):
        r = porte.final_validation(intent, d, auth, before, pf2, P, at)
        assert r.status == "BLOCK" and r.reasons and "arrêté" in porte.describe_final(r), why
    calm = porte.final_validation(intent, d, auth, before, dataclasses.replace(pf, held_risk=(("sol", 10.0),)), P, NOW)
    assert calm.passed and calm.revalidated and "nouveau contrôle" in porte.describe_final(calm)
    other = porte.authorize(porte.check(intent, pf, P, NOW), pf, NOW)       # autorisation d'un autre contrôle
    assert not porte.final_validation(intent, d, other, before, pf, P, NOW).passed
    for change in ({"status": "BLOCK"}, {"authorization_id": ""}, {"snapshot_after": "autre"}, {"checks": ()}):
        with pytest.raises(ContractError):
            dataclasses.replace(ok, **change)


# ---------- Règles de Binance, en réel seulement (§11, §14-15) ----------

def test_exchange_rules_are_checked_in_live_only():
    rules = SimpleNamespace(min_cost=5.0, min_amount=0.001, max_amount=1000.0)
    assert porte.instrument_check(rules, 0.5, 100.0, True)[0]
    for qty, px, listed, word in ((0.0, 100.0, True, "nulle"), (0.0005, 1e5, True, "sous le minimum de Binance"),
                                  (2000.0, 1.0, True, "maximum"), (0.05, 100.0, True, "montant"),
                                  (0.5, 100.0, False, "inactive")):
        ok, why = porte.instrument_check(rules, qty, px, listed)
        assert not ok and word in why, why
    intent, pf, *_ = _checked()
    live = dataclasses.replace(pf, live=True, live_armed=True, instrument=(False, "paire absente ou inactive"))
    d = porte.check(intent, live, P, NOW)
    assert d.status == "REJECTED" and any("instrument négociable" in r for r in d.blocking_reasons)
    assert ("Instrument négociable", True, "paper") in porte.check(intent, pf, P, NOW).limit_checks
    close, _volume = synthetic_market()
    bot, _fb = make_bot("live", close, _lg())
    assert bot.boot()
    px = float(close["eth"].iloc[0])
    assert bot._instrument({"asset": "eth", "qty": 1.0, "entry": px, "exec_price": px})[0]
    ok, why = bot._instrument({"asset": "eth", "qty": 1e-7, "entry": px, "exec_price": px})
    assert not ok and "nulle" in why                                        # arrondie au pas du lot : zéro
    assert bot._portfolio({"asset": "eth", "qty": 1.0, "entry": px}, 1000.0, 1000.0, DAY, NOW).instrument[0]


# ---------- Chaos (§52-57) ----------

def test_chaos_ten_thousand_requests_and_a_kill_switch(monkeypatch):
    ch = pe.chaos(10_000)
    assert ch["ok"] and ch["unauthorized"] == 0 and ch["after_kill"] == 0 and ch["duplicates"] == 0
    assert ch["sent"] >= 50 and ch["repeated"] >= 200 and ch["changes"] >= 1000 and ch["late"] >= 1000
    assert ch["final_blocked"] >= 10 and ch["revalidated"] >= 1
    assert ch["p95_ms"] <= pe.LATENCY_FINAL_MS
    a, b = pe.chaos(2_000), pe.chaos(2_000)                                # graine fixe : rejouable
    assert {**a, "p95_ms": 0} == {**b, "p95_ms": 0}
    # Une porte qui laisserait tout passer est prise par l'oracle.
    monkeypatch.setattr(porte, "final_validation", lambda *a, **k: SimpleNamespace(passed=True, revalidated=False))
    bad = pe.chaos(2_000)
    assert not bad["ok"] and bad["unauthorized"] > 0 and bad["after_kill"] > 0


# ---------- Dans le bot ----------

def test_in_the_bot_a_change_just_before_the_order_stops_the_buy(monkeypatch):
    close, volume = synthetic_market()
    ref, rfb = make_bot("paper", close, _lg())
    assert ref.boot()
    _run(ref, rfb, close, volume, SIM_FROM, SIM_FROM + 60)
    assert ref.state["trades"] or ref.state["paper"]["holdings"]
    bot, fb = make_bot("paper", close, _lg())
    assert bot.boot()
    revoked = {"on": False}
    seen = []
    real_gate, real_final, real_sm, real_fv = bot._gate, bot._final_validation, porte.safe_mode, porte.final_validation

    def gate(*a, **k):
        trace = real_gate(*a, **k)
        revoked["on"] = trace is not None              # vous posez le mode sûr juste après le contrôle
        return trace

    def final(*a, **k):
        try:
            return real_final(*a, **k)
        finally:
            revoked["on"] = False

    def fv(*a, **k):
        r = real_fv(*a, **k)
        seen.append(r.status)
        return r

    monkeypatch.setattr(bot, "_gate", gate)
    monkeypatch.setattr(bot, "_final_validation", final)
    monkeypatch.setattr(porte, "final_validation", fv)
    monkeypatch.setattr(porte, "safe_mode", lambda g: SafeModeState(active=True, reason="révoqué", activated_by="vous")
                        if revoked["on"] else real_sm(g))
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 60)
    assert seen and set(seen) == {"BLOCK"}
    assert not bot.state["trades"] and not bot.state["paper"]["holdings"]


def test_a_failing_component_never_buys(monkeypatch):
    close, volume = synthetic_market()

    def boom(*a, **k):
        raise RuntimeError("panne simulée")

    for target, name in ((porte, "check"), (porte, "authorize"), (porte, "fingerprint"), (porte, "final_validation")):
        bot, fb = make_bot("paper", close, _lg())
        assert bot.boot()
        with monkeypatch.context() as m:
            m.setattr(target, name, boom)
            _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 40)
        assert not bot.state["paper"]["holdings"] and not bot.state["trades"], name
    bot, fb = make_bot("paper", close, _lg())
    assert bot.boot()
    with monkeypatch.context() as m:                                       # audit impossible : aucun achat
        m.setattr(bot, "_audit", lambda *a, **k: False)
        _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 40)
    assert not bot.state["paper"]["holdings"] and not bot.state["trades"]


# ---------- Examen AC-001 à AC-050 (§68-69, §73) ----------

def test_the_examination_and_its_contract(tmp_path):
    g = GuardConfig(lock_file=str(tmp_path / "x.lock"))
    r = pe.evaluate(g, {"porte": {"day": DAY, "approved": 2, "refused": 1, "final_checked": 2}}, NOW, chaos_n=2_000)
    assert len(r["rows"]) == 50 and len({x["id"] for x in r["rows"]}) == 50
    assert {x["status"] for x in r["rows"]} <= {"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"}
    assert r["status"] == "READY_FOR_CONTROLLED_LIVE_EXECUTION" and not r["p0_failures"] and r["score"] >= 95, r
    rep = pe.report_of(r)
    assert contrats.validate("GateReadinessReport", rep.as_dict()).valid and not rep.authorized
    text = pe.render(r)
    assert "## Verdict" in text and "AC-050" in text and "n'autorise rien" in text
    bad = pe.evaluate(g, {"politique": {"day": DAY, "checked": 1, "mismatch": ["eth"]}}, NOW, chaos_n=500)
    assert bad["status"] == "NOT_READY" and "AC-005" in bad["p0_failures"]
    good = dataclasses.asdict(rep)
    for change in ({"authorized": True}, {"passed": rep.passed - 1}, {"chaos_unauthorized": 1},
                   {"band": "NOT_READY"}, {"p0_failures": ("AC-001",)}):
        with pytest.raises(ContractError):
            GateReadinessReport(**{**good, **change})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    view = {"day": DAY, "approved": 2, "refused": 0, "final_checked": 2, "final_blocked": 1, "revalidated": 1,
            "reasons": ["SOL : achat arrêté à la validation finale : mode sûr : activé depuis le contrôle"]}
    r = asst.local_answer("la porte d'execution", {"porte": view})["answer"]
    assert "Porte d'exécution" in r and "juste avant l'ordre" in r and "je n'agis pas" in r
    line = next(x for x in report_health.analysis_checks(GuardConfig(), {"porte": view})
                if x["label"] == "Porte d'exécution")
    assert "arrêté(s) juste avant l'ordre" in line["detail"]
    assert "aucun achat" in pe.describe(None) and "recontrôlé" in pe.describe(view)
    from trendguard import config, systeme
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: GuardConfig(lock_file=str(tmp_path / "x.lock")))
    monkeypatch.setattr(systeme, "read_state", lambda path: {})
    assert pe.main(["--chaos", "500"]) == 0 and "PRÊTE" in capsys.readouterr().out
