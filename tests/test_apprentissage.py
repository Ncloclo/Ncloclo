"""Prompt maître, étape 19 (docs/APPRENTISSAGE_CONTINU.md) : apprentissage
continu et gouvernance des modèles. Chaque modèle a sa fiche, son niveau de
risque et son état ; machine d'états ; dérive mesurée, jamais inventée ;
frontières de l'apprentissage tenues (rien ne touche aux plafonds, à la porte
ni au code ; évolution gelée en réel contrôlé) ; examen AC-001 à AC-070 et
son contrat."""

import dataclasses
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from panel import assistant as asst
from test_trendguard import make_bot, synthetic_market
from trendguard import apprentissage as ap
from trendguard import autonomy, autorisation, contrats, evolution, report_health
from trendguard import trend_strategy as ts
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, LearningGovernanceReport, ModelCard

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)
LIVE = dict(run_mode="live", enable_live_trading=True, live_confirmation="I_UNDERSTAND_RISK")


def _g(tmp_path, **kw):
    return GuardConfig(lock_file=str(tmp_path / "trendguard_paper.lock"), evolution=True, **kw)


def _market(shift_last=False, seed=3):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2018-01-01", "2025-12-31", freq="D", tz="UTC")
    data = {}
    for k, a in enumerate(("btc", "eth", "sol")):
        r = rng.normal(0.0005, 0.03, len(dates))
        if shift_last and a == "sol":
            r[-90:] = rng.normal(0.0, 0.12, 90)                        # volatilité quadruplée
        data[a] = 100 * (k + 1) * np.exp(np.cumsum(r))
    return pd.DataFrame(data, index=dates)


# ---------- Registre et fiches (§13-15) ----------

def test_every_model_has_its_card(tmp_path):
    cards = ap.registry(_g(tmp_path))
    ids = [c["model_id"] for c in cards]
    assert len(ids) == len(set(ids)) >= 10 and "regle" in ids and "evolution" in ids
    for c in cards:
        mc = ap.card_of(c, NOW)
        assert contrats.validate("ModelCard", mc.as_dict()).valid, c["model_id"]
        assert c["state"] in ap.STATES and c["risk"] in ap.RISK_LEVELS
    by = {c["model_id"]: c for c in cards}
    assert by["regle"]["risk"] == "CRITICAL" and by["regle"]["can_trade"]
    assert [c["model_id"] for c in cards if c["can_trade"]] == ["regle"]
    assert by["comite"]["risk"] == "LOW" and by["ia"]["risk"] == "MEDIUM" and by["palier_risque"]["risk"] == "CRITICAL"
    assert len({c["fingerprint"] for c in cards}) == len(cards)
    good = dataclasses.asdict(ap.card_of(by["regle"], NOW))
    for change in ({"rollback": ""}, {"owner": "bot"}, {"purpose": " "}, {"state": "TURBO"}, {"risk": "ÉNORME"}):
        with pytest.raises(ContractError):
            ModelCard(**{**good, **change})
    with pytest.raises(ContractError):
        ModelCard(**{**dataclasses.asdict(ap.card_of(by["ia"], NOW)), "can_trade": True})


def test_the_governance_state_machine():
    path = ["DRAFT", "TRAINING", "VALIDATING", "PENDING_APPROVAL", "APPROVED", "SHADOW", "CANARY", "ACTIVE"]
    for a, b in zip(path, path[1:]):
        assert ap.transition(a, b) == b
    for a, b in (("DRAFT", "ACTIVE"), ("SHADOW", "ACTIVE"), ("RETIRED", "ACTIVE"), ("REJECTED", "APPROVED")):
        with pytest.raises(ContractError):
            ap.transition(a, b)
    for s in ap.STATES:
        assert ap.transition(s, "EMERGENCY_DISABLED") == "EMERGENCY_DISABLED"       # couper : toujours permis
    with pytest.raises(ContractError):
        ap.transition("ACTIVE", "MAGIQUE")
    assert ap.risk_level(3, 3, 1, 2) == "CRITICAL" and ap.risk_level(0, 0, 1, 1) == "LOW"


# ---------- Dérive (§23) ----------

def test_drift_is_measured_never_invented(monkeypatch):
    rng = np.random.default_rng(1)
    same = ap.psi(rng.normal(0, 1, 5000), rng.normal(0, 1, 500))
    moved = ap.psi(rng.normal(0, 1, 5000), rng.normal(0, 3, 500))
    assert same < ap.PSI_MODERATE and moved > ap.PSI_SIGNIFICANT
    assert ap.psi([0.1] * 20, [0.2] * 40) is None                       # trop peu : rien d'inventé
    calm = ap.data_drift(_market())
    assert calm["status"] == "OK" and not calm["drifting"]
    storm = ap.data_drift(_market(shift_last=True))
    assert storm["drifting"] == ["sol"] and storm["worst"]["vol_ratio"] > 2
    short, _v = synthetic_market()                                      # cours depuis 2024 : pas d'époque d'apprentissage
    assert ap.data_drift(short)["status"] == "INSUFFICIENT_DATA"
    close = _market()
    trades = ([{"r": 1.0, "entry_date": str(d.date())} for d in close.index[:-400:20]]
              + [{"r": -0.8, "entry_date": str(d.date())} for d in close.index[-300::15]])
    monkeypatch.setattr(ts, "backtest", lambda *a, **k: SimpleNamespace(trades=trades))
    cd = ap.concept_drift(close, None, ts.TrendParams())
    assert cd["status"] == "OK" and cd["drift"] and cd["recent_r"] < 0 < cd["before_r"]
    monkeypatch.setattr(ts, "backtest", lambda *a, **k: SimpleNamespace(trades=trades[:5]))
    assert ap.concept_drift(close, None, ts.TrendParams())["status"] == "INSUFFICIENT_DATA"
    pdft = ap.performance_drift({"trades": [{"slippage_pct": 0.05}]}, ts.TrendParams())
    assert pdft["slippage"]["status"] == "OK" and pdft["shortfall_bps"] is None
    assert ap.performance_drift({}, ts.TrendParams())["slippage"]["status"] == "UNKNOWN"


# ---------- Frontières de l'apprentissage (§32, §49, §57) ----------

def test_learning_boundaries_keep_the_safety_barriers(tmp_path, monkeypatch):
    g = _g(tmp_path)
    b = ap.boundaries(g)
    assert b["ok"] and not b["touched"] and not b["leaks"] and b["risk_cap_pct"] <= 2.0
    assert b["frozen_stages"] == ["CONTROLLED_LIVE", "LIMITED_PRODUCTION"]
    monkeypatch.setitem(evolution.SPACE, "max_total_risk", [0.06, 0.10])
    assert not ap.boundaries(g)["ok"]                                   # l'évolution toucherait un plafond
    monkeypatch.delitem(evolution.SPACE, "max_total_risk")
    monkeypatch.setitem(autorisation.ROLES, "EVOLUTION", {**autorisation.ROLES["EVOLUTION"], "CHANGE_RISK": ()})
    assert ap.boundaries(g)["leaks"] == [("evolution", "CHANGE_RISK")]
    monkeypatch.setitem(autorisation.ROLES, "EVOLUTION", {k: v for k, v in autorisation.ROLES["EVOLUTION"].items()
                                                         if k != "CHANGE_RISK"})
    assert ap.evolution_allowed(GuardConfig())
    assert not ap.evolution_allowed(GuardConfig(**LIVE, live_stage="controle"))
    assert ap.evolution_allowed(GuardConfig(**LIVE, live_stage="production"))
    # Dans le bot réel : au réel contrôlé, les épreuves de l'évolution ne sont pas lancées.
    lg = logging.getLogger("test.apprentissage")
    lg.setLevel(logging.CRITICAL)
    close, _volume = synthetic_market()
    launched = []
    monkeypatch.setattr(autonomy, "launch_tool", lambda g_, args, ext: launched.append(args) or True)
    for stage, expected in (("controle", 0), ("production", 1)):
        bot, _fb = make_bot("live", close, lg, live_stage=stage, evolution=True)
        bot.track_uptime = True
        bot._launch_evolution("2026-10-07")
        assert len(launched) == expected, stage


# ---------- Examen AC-001 à AC-070, contrat (§66-67, §70) ----------

def test_the_examination_and_its_contract(tmp_path):
    g = _g(tmp_path)
    r = ap.evaluate(g, {"trades": [{"slippage_pct": 0.05}]}, NOW, close=_market(), volume=None)
    assert len(r["rows"]) == 70 and len({x["id"] for x in r["rows"]}) == 70
    assert not r["p0_failures"], r["p0_failures"]
    assert (r["status"] == "READY_FOR_CONTROLLED_CONTINUOUS_LEARNING") == (r["score"] >= 95)
    rep = ap.report_of(r)
    assert contrats.validate("LearningGovernanceReport", rep.as_dict()).valid and not rep.self_authorized
    text = ap.render(r)
    assert "## Registre des modèles" in text and "## Frontières de l'apprentissage" in text and "AC-070" in text
    good = dataclasses.asdict(rep)
    for change in ({"self_authorized": True}, {"passed": rep.passed - 1}, {"critical_models": rep.models + 1}):
        with pytest.raises(ContractError):
            LearningGovernanceReport(**{**good, **change})
    if rep.status != "NOT_READY":
        with pytest.raises(ContractError):
            LearningGovernanceReport(**{**good, "boundaries_ok": False})
    blind = ap.evaluate(g, {}, NOW)                                     # sans cours : la dérive n'est pas inventée
    assert {x["status"] for x in blind["rows"] if x["id"] in ("AC-017", "AC-018")} == {"UNKNOWN"}


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    r = asst.local_answer("gouvernance des modeles", {})["answer"]
    assert "Gouvernance des modèles" in r and "gelée" in r and "je n'agis pas" in r
    line = next(x for x in report_health.analysis_checks(_g(tmp_path), {}) if x["label"] == "Gouvernance des modèles")
    assert "modèles inscrits" in line["detail"] and line["ok"] is None
    from trendguard import config, systeme
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: _g(tmp_path))
    monkeypatch.setattr(systeme, "read_state", lambda path: {})
    monkeypatch.setattr(evolution, "load_history", lambda *a, **k: (_market(), None))
    monkeypatch.setattr(ts, "backtest", lambda *a, **k: SimpleNamespace(trades=[]))
    code = ap.main([])
    out = capsys.readouterr().out
    assert code in (0, 1) and "## Dérive" in out and "Relation signal" in out
