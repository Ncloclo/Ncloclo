"""Prompt maître, étape 17 (docs/DEPLOIEMENT_REEL.md) : exécution réelle par
paliers. Le palier se lit dans vos réglages, jamais décidé par le bot ; les
plafonds du réel contrôlé ne s'appliquent qu'en réel et ne font que
réduire ; la qualité des exécutions est mesurée ; examen AC-001 à AC-060
et son contrat."""

import dataclasses
import logging
import pathlib
import re
from datetime import datetime, timezone

import pytest
from test_market_watch import _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import contrats, porte, report_health
from trendguard import deploiement as dp
from trendguard import trend_strategy as ts
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, LiveDeploymentReport, OrderIntent

NOW = datetime(2026, 10, 8, 0, 3, tzinfo=timezone.utc)
DAY = "2026-10-07"
ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = dict(run_mode="live", enable_live_trading=True, live_confirmation="I_UNDERSTAND_RISK")


def _lg():
    lg = logging.getLogger("test.deploiement")
    lg.setLevel(logging.CRITICAL)
    return lg


# ---------- Aucune promotion automatique (§33, §35, §57) ----------

def test_the_bot_never_promotes_itself():
    assert dp.current(GuardConfig()) == "PAPER"
    assert dp.current(GuardConfig(**LIVE, binance_testnet=True)) == "SIMULATED_LIVE"
    assert dp.current(GuardConfig(**LIVE, live_stage="controle")) == "CONTROLLED_LIVE"
    assert dp.current(GuardConfig(**LIVE, live_stage="limite")) == "LIMITED_PRODUCTION"
    assert dp.current(GuardConfig(**LIVE, live_stage="production")) == "PRODUCTION"
    with pytest.raises(ValueError):
        GuardConfig(**LIVE, live_stage="turbo")
    # Toutes les portes ouvertes : le palier ne bouge pas, la porte dit seulement « à vous de décider ».
    state = {"started_at": "2026-01-01T00:00:00+00:00", "trades": [{}] * 50}
    lad = dp.ladder(GuardConfig(), state, NOW, acceptance={"status": "ACCEPTED"},
                    gate_exam={"status": "READY_FOR_CONTROLLED_LIVE_EXECUTION"}, live_gate={"open": True})
    assert lad["stage"] == "PAPER" and lad["next"] == "SIMULATED_LIVE" and lad["next_gate_open"]
    # Aucun code ne change le palier : seuls la configuration et vos réglages le portent.
    for f in (ROOT / "trendguard").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        if f.name not in ("config.py",):
            assert not re.search(r"live_stage\s*=", text), f.name
        if f.name not in ("config.py", "deploiement.py"):
            assert "TG_PALIER_REEL" not in text, f.name


# ---------- Plafonds du palier, en réel seulement (§34) ----------

def test_the_stage_limits_only_apply_in_live_and_only_reduce(monkeypatch):
    ok, why = dp.stage_limits("CONTROLLED_LIVE", 0, 0.0, 30.0, 100.0, 0.0)
    assert not ok and "TG_MAX_CAPITAL" in why
    assert dp.stage_limits("CONTROLLED_LIVE", 1, 0.0, 30.0, 100.0, 100.0)[0]
    assert "pour 2 au plus" in dp.stage_limits("CONTROLLED_LIVE", 2, 0.0, 10.0, 100.0, 100.0)[1]
    assert not dp.stage_limits("CONTROLLED_LIVE", 1, 20.0, 25.0, 100.0, 100.0)[0]          # 45 % > 40 %
    assert dp.stage_limits("LIMITED_PRODUCTION", 3, 50.0, 20.0, 100.0, 100.0)[0]
    assert dp.stage_limits("PRODUCTION", 50, 1e9, 1e9, 1.0, 0.0)[0]
    intent = OrderIntent(asset="eth", qty=1.0, entry=100.0, stop=90.0, cost=100.1, risk_quote=10.0, decision_day=DAY)
    pf = porte.Portfolio(equity=1000.0, cash=1000.0, invested=0.0, held_risk=(), risk_mult=1.0, expected_day=DAY,
                         universe=frozenset({"eth"}), allowed=frozenset({"eth"}))
    assert ("Plafonds du palier", True, "paper") in porte.check(intent, pf, ts.TrendParams(), NOW).limit_checks
    live = dataclasses.replace(pf, live=True, live_armed=True, stage=(False, "réel contrôlé : 3 achats"))
    d = porte.check(intent, live, ts.TrendParams(), NOW)
    assert d.status == "REJECTED" and any("plafonds du palier" in r for r in d.blocking_reasons)
    # Dans le bot réel : jamais plus de 2 achats par jour au réel contrôlé ; sans capital fixé, aucun achat.
    close, volume = synthetic_market()
    per_day = {}
    bot, fb = make_bot("live", close, _lg(), live_stage="controle", max_capital=2_000.0)
    assert bot.boot()
    real = bot._bought

    def spy(a, plan, trace, now, *args, **kw):
        per_day[now.date()] = per_day.get(now.date(), 0) + 1
        return real(a, plan, trace, now, *args, **kw)

    monkeypatch.setattr(bot, "_bought", spy)
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 90)
    assert per_day and max(per_day.values()) <= 2
    blocked, bfb = make_bot("live", close, _lg(), live_stage="controle")
    assert blocked.boot()
    _run(blocked, bfb, close, volume, SIM_FROM, SIM_FROM + 40)
    assert not any(s.ctx.position.in_position for s in blocked.slots.values())


# ---------- Qualité des exécutions (§40-41) ----------

def test_execution_quality_is_measured():
    v = dp.note_quality(None, "eth", 100.0, 100.5, None)
    assert v["shortfall_bps"] == [50.0] and v["n"] == 1 and "latency_ms" not in v
    v = dp.note_quality(v, "sol", 10.0, 9.9, 230.0)
    assert v["shortfall_bps"][-1] == -100.0 and v["latency_ms"] == [230.0]
    for _ in range(200):
        v = dp.note_quality(v, "eth", 100.0, 100.0, 10.0)
    assert len(v["shortfall_bps"]) == dp.QUALITY_KEEP and len(v["latency_ms"]) == dp.QUALITY_KEEP
    assert "points de base" in dp.describe_quality(v) and "délai médian" in dp.describe_quality(v)
    assert dp.describe_quality(None) == "aucune exécution mesurée"
    close, volume = synthetic_market()
    for mode in ("paper", "live"):
        bot, fb = make_bot(mode, close, _lg())
        assert bot.boot()
        _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 60)
        q = bot.state.get("qualite_execution") or {}
        assert q.get("n", 0) >= 1 and all(abs(x) < 5_000 for x in q["shortfall_bps"]), mode
        assert bool(q.get("latency_ms")) == (mode == "live"), mode


# ---------- Examen AC-001 à AC-060, portes, contrat (§53-57, §62) ----------

def test_the_examination_the_ladder_and_the_contract():
    r = dp.evaluate(GuardConfig(), {}, NOW, acceptance={"status": "BLOCKED"},
                    gate_exam={"status": "NOT_READY"}, live_gate={"open": False})
    assert len(r["rows"]) == 60 and len({x["id"] for x in r["rows"]}) == 60
    assert r["status"] == "READY_FOR_STAGED_LIVE_EXECUTION" and not r["p0_failures"] and r["score"] >= 95, r
    lad = r["ladder"]
    assert lad["stage"] == "PAPER" and not lad["next_gate_open"] and lad["missing"] == ["paper accepté (AC-001 à AC-044)"]
    assert [x["state"] for x in lad["rows"]] == ["franchi", "en vigueur"] + ["à venir"] * 4
    rep = dp.report_of(r)
    assert contrats.validate("LiveDeploymentReport", rep.as_dict()).valid and not rep.auto_promoted
    text = dp.render(r)
    assert "## Palier" in text and "## Verdict" in text and "AC-060" in text
    good = dataclasses.asdict(rep)
    for change in ({"auto_promoted": True}, {"next_stage": "CONTROLLED_LIVE"}, {"stage": "CONTROLLED_LIVE"},
                   {"next_gate_open": True}, {"passed": rep.passed - 1}, {"band": "NOT_READY"}):
        with pytest.raises(ContractError):
            LiveDeploymentReport(**{**good, **change})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    g = GuardConfig(**LIVE, live_stage="controle")
    view = {"text": dp.describe(g), "stage": dp.current(g), "quality": "1 exécution(s)"}
    r = asst.local_answer("les paliers du reel", {"deploiement": view})["answer"]
    assert "Paliers du réel" in r and "réel contrôlé" in r and "je n'agis pas" in r
    line = next(x for x in report_health.analysis_checks(GuardConfig(), {}) if x["label"] == "Paliers du réel")
    assert "palier : paper" in line["detail"] and "seulement par votre réglage" in line["detail"]
    assert "2 achats par jour" in dp.describe(g)
    from trendguard import chantiers, config, evolution, systeme
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: GuardConfig(lock_file=str(tmp_path / "x.lock")))
    monkeypatch.setattr(systeme, "read_state", lambda path: {})
    monkeypatch.setattr(evolution, "load_history", lambda *a, **k: (_ for _ in ()).throw(OSError("pas de cache")))
    monkeypatch.setattr(chantiers, "live_gate", lambda *a, **k: {"open": False, "missing": ["essai"]})
    assert dp.main(["--chaos", "300"]) == 0 and "Palier" in capsys.readouterr().out
