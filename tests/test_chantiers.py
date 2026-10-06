"""Cadre de priorités et de dépendances (docs/FEUILLE_DE_ROUTE.md) : registre
des composants (priorité, état, dépendances typées, preuves, santé), graphe
sans cycle ni dépendance vers l'aval, portes 1 à 7 mesurées dans le dépôt,
porte du réel (porte 8) mesurée sur l'état du bot et appliquée par la porte
d'exécution."""

import ast
import json
import logging
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import trendguard_bot as tg
from test_trendguard import SIM_FROM, make_bot, run_days, synthetic_market
from trendguard import audit, autonomy, chantiers, porte
from trendguard import trend_strategy as ts
from trendguard.contrats import ContractError, OrderIntent

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def logger():
    lg = logging.getLogger("test.chantiers")
    lg.setLevel(logging.ERROR)
    return lg


# ---------- Registre, graphe, preuves ----------

def test_the_dependency_graph_is_sound():
    assert chantiers.problems() == []
    order = chantiers.topological()
    pos = {t: k for k, t in enumerate(order)}
    comps = chantiers.by_id()
    assert all(pos[d] < pos[c.task_id] for c in chantiers.COMPONENTS for d, _k in c.deps)
    path = chantiers.critical_path()
    assert path[0] == "TASK-000001" and path[-1] == "TASK-000018" and len(path) >= 6
    assert "TASK-000018" in chantiers.dependents("TASK-000017")
    assert comps["TASK-000023"].priority == "P4" and not chantiers.dependents("TASK-000023")   # P4 jamais requis
    with pytest.raises(ContractError):
        chantiers.Component("TASK-1", "x", "P0", "DEPLOYED", "FONDATION")


def test_a_status_in_service_is_proven_not_declared():
    for c in chantiers.COMPONENTS:
        h = chantiers.health(c)
        if c.status in chantiers.SATISFIED:
            assert h["proven"] and h["ready"], (c.task_id, h)
        if c.status in chantiers.BUILT:
            assert not h["missing"], (c.task_id, h["missing"])
    blocked = chantiers.health(chantiers.by_id()["TASK-000018"])
    assert not blocked["ready"] and blocked["blocking"] == ["TASK-000017"]             # réel : connecteur en essai
    fake = chantiers.Component("TASK-000099", "Fantôme", "P0", "DEPLOYED", "FONDATION",
                               files=("trendguard/inexistant.py",), tests=("tests/test_rien.py",),
                               contracts=("Inconnu.v1",), security="x", observability="y")
    h = chantiers.health(fake)
    assert not h["proven"] and h["contract_validity"] == 0 and h["test_readiness"] == 0


def test_gates_one_to_seven_are_measured_in_the_repository():
    for g in chantiers.GATES:
        s = chantiers.gate_status(g)
        assert s["ok"], s
    four = chantiers.gate_status(next(g for g in chantiers.GATES if g[0] == "4"))
    assert any(ok is None and "fondamentale" in d for _n, ok, d in four["items"])     # sans objet, dit


def test_the_roadmap_document_is_in_sync():
    doc = (ROOT / "docs" / "FEUILLE_DE_ROUTE.md").read_text(encoding="utf-8")
    assert doc == chantiers.render(), "docs/FEUILLE_DE_ROUTE.md à régénérer : python -m trendguard.chantiers document"


def test_foundation_and_intelligence_never_load_order_code():
    """Dépendance inversée interdite (§22) : au chargement, aucun module de
    fondation ni d'intelligence n'importe un module qui passe des ordres."""
    pkg = ROOT / "trendguard"

    def top(mod):
        tree = ast.parse((pkg / f"{mod}.py").read_text(encoding="utf-8"))
        out = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                out |= {node.module.split(".")[0]} if node.module else {a.name for a in node.names}
        return {m for m in out if (pkg / f"{m}.py").exists()}

    def closure(mod):
        seen, todo = set(), [mod]
        while todo:
            for d in top(todo.pop()):
                if d not in seen:
                    seen.add(d)
                    todo.append(d)
        return seen
    orders = {"bot", "bot_execution", "bot_routines", "cli", "porte"}
    for mod in ("contrats", "texte", "audit", "autonomy", "donnees", "qualite", "agents", "comite", "modeles",
                "market_watch", "watch_claude", "savoir", "cognitif", "evenements", "regimes", "learning",
                "anticipation", "chantiers"):
        assert not closure(mod) & orders, (mod, closure(mod) & orders)


# ---------- Porte du réel (porte 8) ----------

def _ready_world(tmp_path, days=61, trades=10):
    """Un bot dont toutes les conditions du réel sont réunies."""
    g = tg.GuardConfig(run_mode="live", enable_live_trading=True, live_confirmation="I_UNDERSTAND_RISK",
                       db_file=str(tmp_path / "trendguard_live.db"), log_file=str(tmp_path / "x.log"),
                       lock_file=str(tmp_path / "trendguard_live.lock"), release_gate=True)
    paper = {"started_at": (NOW - timedelta(days=days)).isoformat(), "trades": [{"asset": "aave"}] * trades}
    con = sqlite3.connect(str(tmp_path / "trendguard_paper.db"))
    con.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO kv VALUES ('trendguard', ?)", (json.dumps(paper),))
    con.commit()
    con.close()
    audit.AuditLog(audit.path_for(g)).append("bot", "porte.controle", "aave", "APPROVED", now=NOW)
    report = {"day": "2026-10-05", "generated_at": (NOW - timedelta(hours=12)).isoformat(),
              "sections": [{"title": "Sécurité", "checks": [{"label": "Clés API Binance", "ok": True}]}]}
    autonomy.write_json(autonomy.sidecar(str(tmp_path / "trendguard_paper.lock"), ".rapport.json"), report)
    autonomy.write_json(chantiers.verification_path(g), {"at": (NOW - timedelta(days=1)).isoformat(), "ok": True,
                                                         "testnet": False})
    env = {"SMTP_HOST": "smtp.example.com", "ALERT_EMAIL_TO": "moi@example.com"}
    return g, env


def test_the_live_gate_opens_only_when_every_condition_is_met(tmp_path):
    g, env = _ready_world(tmp_path)
    g8 = chantiers.live_gate(g, {}, now=NOW, env=env)
    assert g8["open"], g8["missing"]
    assert chantiers.describe_live(g8).startswith("ouverte")
    assert "Alertes" in " ".join(chantiers.live_gate(g, {}, now=NOW, env=dict(env, SMTP_HOST=""))["missing"])
    assert not chantiers.live_gate(g, {"halted": True}, now=NOW, env=env)["open"]          # arrêt d'urgence
    assert not chantiers.live_gate(g, {}, now=NOW + timedelta(days=9), env=env)["open"]    # vérification trop vieille
    (tmp_path / "jeune").mkdir()
    young, env2 = _ready_world(tmp_path / "jeune", days=20, trades=3)
    missing = " ".join(chantiers.live_gate(young, {}, now=NOW, env=env2)["missing"])
    assert "durée" in missing and "trades clos" in missing
    chantiers.note_verification(g, True, True)                                             # testnet : ne compte pas
    assert not chantiers.live_gate(g, {}, now=NOW, env=env)["open"]


def test_a_closed_live_gate_refuses_every_real_buy():
    intent = OrderIntent.from_plan({"asset": "aave", "qty": 0.5, "entry": 100.0, "stop": 90.0, "cost": 50.05,
                                    "risk_quote": 5.0}, "2026-10-05")
    p = ts.TrendParams(risk_pct=0.01, max_total_risk=0.10, max_positions=20)
    base = dict(equity=500.0, cash=300.0, invested=0.0, held_risk=(), risk_mult=1.0, expected_day="2026-10-05",
                universe=frozenset({"aave"}), allowed=frozenset({"aave"}), live=True, live_armed=True)
    closed = porte.check(intent, porte.Portfolio(**base, production=(False, "fermée : essai paper trop court")), p, NOW)
    assert closed.status == "REJECTED" and any("porte du réel" in r for r in closed.blocking_reasons)
    assert porte.check(intent, porte.Portfolio(**base, production=(True, "ouverte")), p, NOW).approved
    paper = dict(base, live=False, live_armed=False)
    assert porte.check(intent, porte.Portfolio(**paper, production=(False, "x")), p, NOW).approved   # paper : sans objet


def test_the_live_bot_buys_nothing_while_the_gate_is_closed(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("live", close, logger, release_gate=True)
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 60)
    assert not bot.state.get("buys") and bot.state["mise_en_production"]["open"] is False
    ref, fr_ = make_bot("live", close, logger)                                   # porte non mesurée (essais)
    assert ref.boot()
    run_days(ref, fr_, close, volume, SIM_FROM, SIM_FROM + 60)
    assert ref.state.get("buys")
