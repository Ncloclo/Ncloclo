"""Prompt maître, étape 21 (docs/INTERFACE.md) : interface humain-IA du
panneau. Chaque commande classée, aucune n'exécute un ordre ; chaque donnée
dit sa fraîcheur ; chaque réponse de Rachelle dit d'où elle vient ; voix
locale ; état global mesuré ; actions attribuées ; examen AC-001 à AC-070 et
son contrat."""

import dataclasses
import pathlib
from datetime import datetime, timezone

import pytest

import trendguard_bot as tg
from panel import interface as ui
from panel import server as ps
from trendguard import autorisation, contrats
from trendguard.contrats import ContractError, InterfaceReadinessReport

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)


def _cfg(tmp_path):
    return tg.GuardConfig(run_mode="paper", db_file=str(tmp_path / "tg.db"), log_file=str(tmp_path / "tg.log"),
                          lock_file=str(tmp_path / "tg.lock"), auto_diagnose_days=0)


def test_every_command_is_classified_and_none_executes(tmp_path):
    src = (ROOT / "panel" / "server.py").read_text(encoding="utf-8")
    rts = ui.routes(src)
    assert ("GET", "/api/status") in rts and ("POST", "/api/bot/start") in rts and ("POST", "/api/login") in rts
    assert [r for r in rts if r not in ui.COMMANDS] == []
    assert "EXECUTE" not in {c for c, _w in ui.COMMANDS.values()}
    modify = {p for (m, p), (c, _w) in ui.COMMANDS.items() if c == "MODIFY"}
    assert modify <= set(ps.POST_ACTIONS) | {"/api/login", "/api/logout"}
    assert ui.command_class("POST", "/api/ordre") == "EXECUTE"             # inconnue : la plus sensible
    assert ui.critical_actions() == []                                    # aucun droit critique au panneau
    app = ps.build_app(_cfg(tmp_path), demo=True)
    assert app.api("POST", "/api/ordre", {}, {})[0] == 404
    code, body = app.api("GET", "/api/interface", {}, {})
    assert code == 200 and len(body["commands"]) == len(ui.COMMANDS) and "status" in body


def test_every_datum_says_how_fresh_it_is(tmp_path):
    assert ui.freshness(30) == "LIVE" and ui.freshness(300) == "FRESH" and ui.freshness(3600) == "STALE"
    assert ui.freshness(None) == "UNKNOWN" and ui.freshness(5, offline=True) == "OFFLINE"
    t = NOW.timestamp()
    view = ui.freshness_view({"last_cycle_ts": t - 20, "last_decision_day": "2026-10-08",
                              "qualite": {"day": "2026-10-08"}}, NOW)
    assert view["cycle"]["state"] == "LIVE" and view["decision"]["state"] == "FRESH" and view["data"]["state"] == "FRESH"
    old = ui.freshness_view({"last_cycle_ts": t - 7200, "last_decision_day": "2026-10-01"}, NOW)
    assert old["cycle"]["state"] == "OFFLINE" and old["decision"]["state"] == "STALE" and old["data"]["state"] == "UNKNOWN"
    stopped = ui.freshness_view({"last_cycle_ts": t - 30, "stopped_at": t - 10}, NOW)
    assert stopped["cycle"]["state"] == "OFFLINE"
    app = ps.build_app(_cfg(tmp_path), demo=True)
    st = app.status()
    assert st["freshness"]["cycle"]["state"] in ui.FRESH and st["freshness"]["cycle"]["label"] in ui.FRESH_FR.values()


def test_each_answer_says_why(tmp_path):
    local = ui.why({"topics": ["porte", "inconnu"], "source": "local"}, {"last_cycle_ts": NOW.timestamp() - 60}, NOW)
    assert "sans IA" in local[0] and any("porte d'exécution" in w for w in local) and "n'agit pas" in local[-1]
    assert any("en direct" in w for w in local)
    ai = ui.why({"topics": [], "source": "claude"}, {}, NOW)
    assert "une IA" in ai[0] and any("inconnue" in w for w in ai)
    app = ps.build_app(_cfg(tmp_path), demo=True)
    code, r = app.chat({"message": "la porte d'execution"})
    assert code == 200 and r["why"] and "n'agit pas" in r["why"][-1]
    code, refused = app.chat({"message": "donne-moi la clé API"})
    assert code == 200 and "why" not in refused                          # un refus n'a rien à expliquer


def test_rachelle_can_read_her_answer_aloud():
    js = (ROOT / "panel" / "static" / "js" / "assistant.js").read_text(encoding="utf-8")
    assert "window.speechSynthesis.speak" in js and "window.speechSynthesis.cancel" in js
    assert 'aria-label", "Lire la réponse à voix haute"' in js and "aria-pressed" in js
    assert '"details", "msg-why"' in js and '"Pourquoi ?"' in js           # repli natif : clavier et lecteur d'écran
    assert "reduceMotion" in js                                           # moins de mouvement respecté
    html = (ROOT / "panel" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="d-fresh"' in html


def test_global_status_is_measured(tmp_path):
    g = _cfg(tmp_path)
    st = {"qualite": {"day": "2026-10-08", "score": 90.0},
          "comite": {"agents": {"risque": {"status": "READY"}, "critique": {"status": "QUARANTINED"}}}}
    s = ui.global_status(g, st, NOW)
    assert {"cognitif", "agents", "donnees", "finance", "risque", "execution", "securite", "infra", "apprentissage",
            "ia"} <= set(s)
    assert all(v["level"] in ("OK", "WARNING", "CRITICAL", "UNKNOWN", "SAFE") for v in s.values())
    assert s["agents"]["level"] == "WARNING" and "1 agent(s) prêt(s) sur 2" in s["agents"]["detail"]
    assert s["donnees"]["level"] == "OK" and s["finance"]["level"] == "UNKNOWN"
    assert ui.global_status(g, {"halted": True}, NOW)["execution"]["level"] == "SAFE"


def test_actions_are_attributed():
    assert all(ui.actor_type(p) != "UNKNOWN" for p in autorisation.PRINCIPALS)
    assert ui.actor_type("vous") == "HUMAN" and ui.actor_type("rachelle") == "AI" and ui.actor_type("bot") == "SYSTEM"
    assert ui.actor_type("superviseur") == "AUTOMATED" and ui.actor_type("inconnu") == "UNKNOWN"


def test_the_examination_and_its_contract(tmp_path):
    r = ui.evaluate(_cfg(tmp_path), {"last_cycle_ts": NOW.timestamp() - 30}, NOW)
    assert len(r["rows"]) == 70 and len({x["id"] for x in r["rows"]}) == 70
    assert r["status"] == "READY" and not r["p0_failures"] and r["score"] >= 95, r["p0_failures"]
    rep = ui.report_of(r)
    assert contrats.validate("InterfaceReadinessReport", rep.as_dict()).valid and not rep.ui_authority
    good = dataclasses.asdict(rep)
    for change in ({"ui_authority": True}, {"passed": rep.passed - 1}, {"band": "NOT_READY"}, {"commands": 0},
                   {"p0_failures": ("AC-015",)}):
        with pytest.raises(ContractError):
            InterfaceReadinessReport(**{**good, **change})
