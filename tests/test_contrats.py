"""Prompt maître, étape 2 (docs/CONTRATS.md) : contrats validés à la création,
porte d'exécution déterministe avant chaque achat, journal d'audit
infalsifiable, mode sûr, registre des contrats tenu à jour ; dans le bot, en
paper comme en réel."""

import dataclasses
import json
import logging
import pathlib
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from test_market_watch import _run

import trendguard_bot as tg
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import audit, contrats, porte, report_health
from trendguard import trend_strategy as ts
from trendguard.contrats import (
    ContractError,
    ExecutionAuthorization,
    OrderIntent,
    RiskDecision,
    SafeModeState,
)

NOW = datetime(2026, 10, 6, 0, 3, tzinfo=timezone.utc)
DAY = "2026-10-05"
P = ts.TrendParams(risk_pct=0.01, max_total_risk=0.10, max_positions=20)
PLAN = {"asset": "aave", "qty": 0.5, "entry": 100.0, "stop": 90.0, "cost": 50.05, "risk_quote": 5.0}


@pytest.fixture
def logger():
    lg = logging.getLogger("test.contrats")
    lg.setLevel(logging.WARNING)
    return lg


def _pf(**kw):
    base = dict(equity=500.0, cash=300.0, invested=200.0, held_risk=(("btc", 5.0), ("eth", 5.0)),
                risk_mult=1.0, expected_day=DAY, universe=frozenset({"aave", "btc", "eth"}),
                allowed=frozenset({"aave", "btc", "eth"}))
    base.update(kw)
    return porte.Portfolio(**base)


def _intent(**kw):
    return OrderIntent.from_plan(dict(PLAN, **kw), DAY)


# ---------- Contrats de données : valide, invalide, manquant, mauvais type, mauvaise version ----------

def test_order_intent_contract():
    i = _intent()
    assert i.idempotency_key == f"{DAY}:aave:BUY" and i.version == 1 and i.side == "BUY"
    cases = [({"qty": "0.5"}, "WRONG_TYPE"), ({"qty": 0}, "OUT_OF_RANGE"), ({"entry": float("nan")}, "OUT_OF_RANGE"),
             ({"qty": True}, "WRONG_TYPE"), ({"asset": "AAVE!"}, "INVALID_FIELD"), ({"day": "05/10/2026"}, "INVALID_FIELD")]
    for change, code in cases:
        with pytest.raises(ContractError) as e:
            _intent(**change)
        assert e.value.code == code, change
    with pytest.raises(ContractError) as e:
        OrderIntent.from_plan({k: v for k, v in PLAN.items() if k != "stop"}, DAY)
    assert e.value.code == "MISSING_FIELD" and "stop" in str(e.value)
    with pytest.raises(ContractError) as e:
        dataclasses.replace(i, version=2)
    assert e.value.code == "WRONG_VERSION"
    with pytest.raises(ContractError):
        dataclasses.replace(i, side="SELL")                       # une vente ne passe jamais par la porte
    env = ContractError("WRONG_TYPE", "qty").envelope("porte", "D-x")
    assert env["category"] == "VALIDATION" and env["retryable"] is False and env["correlation_id"] == "D-x"


def test_decision_authorization_and_safe_mode_contracts():
    with pytest.raises(ContractError):
        RiskDecision("R-1", "MAYBE", 0, 0, 0, 0, ())
    with pytest.raises(ContractError):
        RiskDecision("R-1", "APPROVED", 1, 0, 0, 0, (), blocking_reasons=("x",))
    with pytest.raises(ContractError):
        RiskDecision("R-1", "REJECTED", 0, 0, 0, 0, ())          # un refus dit toujours pourquoi
    with pytest.raises(ContractError):
        ExecutionAuthorization(True, "A-1", "porte.v1", "", NOW.isoformat())
    a = ExecutionAuthorization(True, "A-1", "porte.v1", "R-1", (NOW + timedelta(minutes=5)).isoformat())
    assert a.valid_at(NOW) and not a.valid_at(NOW + timedelta(minutes=6))          # délai dépassé
    assert SafeModeState.from_dict({}) == SafeModeState() and not SafeModeState().active
    broken = SafeModeState.from_dict({"active": True, "version": 9})
    assert broken.active and "illisible" in broken.reason      # en cas de doute : pas d'achat


# ---------- Porte d'exécution ----------

def test_gate_approves_the_normal_purchase():
    d = porte.check(_intent(), _pf(), P, NOW)
    assert d.status == "APPROVED" and d.approved_size == 0.5 and not d.blocking_reasons
    assert len(d.limit_checks) == 17 and all(ok for _n, ok, _d in d.limit_checks)
    assert d.open_risk_pct == 3.0 and d.exposure_pct == pytest.approx(50.01)
    a = porte.authorize(d, _pf(), NOW)
    assert a.authorized and a.risk_check_id == d.risk_check_id and a.authorization_id.startswith("A-")
    assert a.valid_at(NOW) and a.policy_version == porte.POLICY_VERSION
    assert "autorisé" in porte.describe(d, a)
    within = porte.check(_intent(risk_quote=5.4), _pf(), P, NOW)         # 8 % au-dessus du plan : marge
    assert within.approved and "dans la marge" in within.warnings[0]


@pytest.mark.parametrize("change,pf,status,word", [
    ({}, {"halted": True}, "EMERGENCY_BLOCK", "arrêt d'urgence"),
    ({}, {"safe_mode": SafeModeState(active=True, reason="vacances")}, "EMERGENCY_BLOCK", "vacances"),
    ({}, {"garde_blocked": ("BTC −18 % en un jour",)}, "REJECTED", "garde"),
    ({}, {"expected_day": "2026-10-06"}, "REJECTED", "décision du jour"),
    ({"asset": "doge"}, {}, "REJECTED", "hors de la liste"),
    ({}, {"allowed": frozenset({"btc"})}, "REJECTED", "non choisie"),
    ({}, {"vetoed": frozenset({"aave"})}, "REJECTED", "veille"),
    ({}, {"held_risk": (("aave", 5.0),)}, "REJECTED", "déjà détenue"),
    ({}, {"bought_today": frozenset({f"{DAY}:aave:BUY"})}, "REJECTED", "déjà achetée"),
    ({}, {"held_risk": tuple((f"c{k}", 0.1) for k in range(20))}, "REJECTED", "nombre de positions"),
    ({"risk_quote": 6.0}, {}, "REJECTED", "risque de l'achat"),
    ({}, {"held_risk": (("btc", 51.0),)}, "REJECTED", "risque cumulé"),
    ({"cost": 200.0}, {"cash": 1000.0}, "REJECTED", "taille de la position"),
    ({}, {"cash": 20.0}, "REJECTED", "argent disponible"),
    ({"stop": 100.0}, {}, "REJECTED", "stop sous le prix"),
    ({"qty": 0.05, "cost": 5.0}, {}, "REJECTED", "montant minimum"),
    ({}, {"live": True}, "REJECTED", "réel NON armé"),
    ({}, {"data_quality": 40.0}, "REJECTED", "qualité des données"),
])
def test_gate_refuses_with_reasons(change, pf, status, word):
    d = porte.check(_intent(**change), _pf(**pf), P, NOW)
    assert d.status == status and d.approved_size == 0
    assert any(word in r for r in d.blocking_reasons), d.blocking_reasons
    assert not porte.authorize(d, _pf(**pf), NOW).authorized


def test_safe_mode_switch(tmp_path):
    g = tg.GuardConfig(run_mode="paper", db_file=":memory:", log_file=str(tmp_path / "x.log"),
                       lock_file=str(tmp_path / "tg.lock"))
    assert not porte.safe_mode(g).active
    said = []
    porte.cmd_safe_mode(g, "on", NOW, said.append)
    st = porte.safe_mode(g)
    assert st.active and st.activated_at.startswith("2026-10-06") and "ACTIF" in said[0]
    porte.cmd_safe_mode(g, "status", NOW, said.append)
    assert "ACTIF depuis" in said[-1]
    porte.cmd_safe_mode(g, "off", NOW, said.append)
    assert not porte.safe_mode(g).active
    pathlib.Path(porte.safe_mode_path(g)).write_text("{pas du json", encoding="utf-8")
    assert porte.safe_mode(g).active                             # fichier abîmé : on n'achète pas
    with pytest.raises(ValueError):
        porte.set_safe_mode(tg.GuardConfig(run_mode="paper", db_file=":memory:", log_file="/dev/null",
                                           lock_file="/dev/null"), True, NOW)


# ---------- Journal d'audit ----------

def test_audit_chain_detects_any_change(tmp_path):
    path = str(tmp_path / "tg.audit.jsonl")
    log = audit.AuditLog(path)
    for k in range(3):
        log.append("bot", "ordre.achat", f"c{k}", "exécuté", correlation_id="D-1", now=NOW)
    assert audit.verify(path) == {"ok": True, "events": 3, "bad_line": None, "last": NOW.isoformat(timespec="seconds")}
    again = audit.AuditLog(path)                                 # redémarrage : la chaîne continue
    assert again.append("bot", "ordre.vente", "c0", "exécuté")["audit_id"] == 4
    assert audit.verify(path)["ok"] and [e["resource"] for e in audit.read(path, 2)] == ["c2", "c0"]
    lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[1])
    forged["result"] = "refusé"
    for bad in (lines[:1] + [json.dumps(forged)] + lines[2:],      # une ligne modifiée
                lines[:1] + lines[2:]):                              # une ligne retirée
        pathlib.Path(path).write_text("\n".join(bad) + "\n", encoding="utf-8")
        v = audit.verify(path)
        assert not v["ok"] and v["bad_line"] == 2 and "MODIFIÉ" in audit.describe(v)
    assert audit.AuditLog("").append("bot", "ordre.achat", "y", "z") is None and audit.verify("")["ok"]


# ---------- Registre des contrats ----------

def test_contract_registry_is_complete_and_documented():
    ids = [c.contract_id for c in contrats.REGISTRY]
    assert len(ids) == len(set(ids)) and all(c.contract_id.split(".")[-1].startswith("v") for c in contrats.REGISTRY)
    root = pathlib.Path(__file__).resolve().parent.parent
    for c in contrats.REGISTRY:
        assert all(getattr(c, f.name) not in ("", ()) for f in dataclasses.fields(c)), c.contract_id
        assert c.level in contrats.LEVELS and all((root / f).exists() for f in c.files), c.contract_id
    assert {"OrderIntent.v1", "RiskCheck.v1", "ExecutionAuthorization.v1", "AuditEvent.v2",
            "SafeModeState.v1"} <= set(ids)
    assert contrats.contract("RiskCheck.v1").level == 1
    with pytest.raises(KeyError):
        contrats.contract("Inconnu.v1")
    doc = (root / "docs" / "CONTRATS.md").read_text(encoding="utf-8")
    assert doc == contrats.render(), "docs/CONTRATS.md à régénérer : python -m trendguard.contrats"


# ---------- Dans le bot ----------

def _audited_bot(mode, close, logger, tmp_path):
    bot, fb = make_bot(mode, close, logger)
    bot.g = dataclasses.replace(bot.g, lock_file=str(tmp_path / f"{mode}.lock"))
    bot.audit = audit.AuditLog(audit.path_for(bot.g))
    return bot, fb


def test_every_buy_goes_through_the_gate_and_changes_nothing(logger, tmp_path, monkeypatch):
    close, volume = synthetic_market()
    last = SIM_FROM + 200
    bot, fb = _audited_bot("paper", close, logger, tmp_path)
    seen = []
    check = porte.check
    monkeypatch.setattr(porte, "check", lambda *a: seen.append(check(*a)) or seen[-1])
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, last)
    assert seen and all(d.approved for d in seen)                # décisions normales : toutes autorisées
    events = audit.read(audit.path_for(bot.g), 10_000)
    assert audit.verify(audit.path_for(bot.g))["ok"]
    buys = [e for e in events if e["action"] == "ordre.achat" and e["result"] == "exécuté"]
    assert len(buys) == len(bot.state["buys"]) and all(e["authorization"].startswith("A-") for e in buys)
    sales = [e for e in events if e["action"] == "ordre.vente"]
    assert len(sales) == len(bot.state["trades"]) > 0
    trades = bot.state["trades"]
    assert all(t["trace"]["decision_id"] == "D-" + str((pd.Timestamp(t["entry_date"]) - pd.Timedelta(days=1)).date())
               for t in trades)                                  # chaque trade remonte à sa décision
    # Le même bot, porte grande ouverte : exactement les mêmes trades.
    monkeypatch.setattr(porte, "check", lambda intent, pf, p, now: RiskDecision(
        f"R-{intent.asset}", "APPROVED", intent.qty, 0.0, 0.0, 0.0, ()))
    ref, fr_ = make_bot("paper", close, logger)
    assert ref.boot()
    _run(ref, fr_, close, volume, SIM_FROM, last)
    key = lambda b: [(t["asset"], t["date"], round(t["pnl"], 8)) for t in b.state["trades"]]  # noqa: E731
    assert key(bot) == key(ref) and sorted(bot.state["paper"]["holdings"]) == sorted(ref.state["paper"]["holdings"])
    checks = {c["label"]: c for c in report_health.analysis_checks(bot.g, bot.state)}
    assert checks["Journal d'audit"]["ok"] is True and "Porte d'exécution" in checks


def test_safe_mode_stops_buys_but_not_sales(logger, tmp_path):
    close, volume = synthetic_market()
    ref, fr_ = make_bot("paper", close, logger)
    assert ref.boot()
    _run(ref, fr_, close, volume, SIM_FROM, SIM_FROM + 200)
    bot, fb = _audited_bot("paper", close, logger, tmp_path)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 150)
    held = set(bot.state["paper"]["holdings"])
    since = close.index[SIM_FROM + 150].isoformat()
    porte.set_safe_mode(bot.g, True, NOW, reason="essai")
    n_buys = len(bot.state["buys"])
    _run(bot, fb, close, volume, SIM_FROM + 150, SIM_FROM + 200)
    assert len(bot.state["buys"]) == n_buys                       # plus aucun achat…
    assert [b for b in ref.state["buys"] if b["date"] > since]    # … là où le bot normal achetait
    def sold(b):                                                 # première vente de chaque position gardée
        out = {}
        for t in b.state["trades"]:
            if t["date"] > since and t["asset"] in held:
                out.setdefault(t["asset"], t["date"])
        return out
    assert sold(bot) and sold(bot) == sold(ref)                   # les ventes, elles, continuent
    assert any(x.startswith("Mode sûr actif") for x in bot.state["reasoning"]["lines"])
    actions = [e["action"] for e in audit.read(audit.path_for(bot.g), 10_000)]
    assert "mode_sur.active" in actions
    porte.set_safe_mode(bot.g, False, NOW)
    bot.run_cycle(now=close.index[SIM_FROM + 199].to_pydatetime() + timedelta(days=1, minutes=6))
    assert audit.read(audit.path_for(bot.g), 1)[0]["action"] == "mode_sur.leve"


def test_live_buys_are_authorized_and_traced(logger, tmp_path):
    close, volume = synthetic_market()
    bot, fb = _audited_bot("live", close, logger, tmp_path)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 200)
    events = audit.read(audit.path_for(bot.g), 10_000)
    buys = [e for e in events if e["action"] == "ordre.achat" and e["result"] == "exécuté"]
    assert buys and len(buys) == len(bot.state["buys"]) and audit.verify(audit.path_for(bot.g))["ok"]
    traced = [t for t in bot.state["trades"] if t.get("trace")]
    assert len(traced) == len(bot.state["trades"]) and all(t["trace"]["authorization_id"].startswith("A-")
                                                           for t in traced)
