"""Prompt maître, étape 3 (docs/DONNEES.md) : journal financier en tables
reliées dans la base du bot ; migrations versionnées et réversibles,
contraintes dans la base, ajout seul, unicité des ordres, lignée complète de
chaque trade (paper et réel), sauvegarde relue, domaine écrit par un seul
module."""

import dataclasses
import json
import logging
import pathlib
import re
import sqlite3

import pytest
from test_market_watch import _run

import trendguard_bot as tg
import v29
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import donnees, porte, report_health, report_security
from trendguard import trend_strategy as ts
from trendguard.contrats import OrderIntent

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAY = "2026-10-05"


@pytest.fixture
def logger():
    lg = logging.getLogger("test.donnees")
    lg.setLevel(logging.WARNING)
    return lg


def _decision(j, day=DAY):
    return j.record_decision(day, "paper", donnees.params_of(ts.TrendParams()), True, "tendance haussière",
                             100.0, False, False, [], 100.0)


def _checked(j, asset="aave", status_ok=True):
    from datetime import datetime, timezone
    now = datetime(2026, 10, 6, 0, 3, tzinfo=timezone.utc)
    pf = porte.Portfolio(equity=100.0, cash=100.0, invested=0.0, held_risk=(), risk_mult=1.0, expected_day=DAY,
                         universe=frozenset({asset}), allowed=frozenset({asset}), halted=not status_ok)
    intent = OrderIntent(asset=asset, qty=0.05, entry=200.0, stop=180.0, cost=10.01, risk_quote=1.0, decision_day=DAY)
    d = porte.check(intent, pf, ts.TrendParams(), now)
    a = porte.authorize(d, pf, now)
    j.record_risk_check(f"D-{DAY}", asset, d, a)
    return d, a, intent


# ---------- Schéma : migrations versionnées, réversibles, protégées ----------

def test_migrations_are_versioned_reversible_and_protected(tmp_path):
    path = str(tmp_path / "bot.db")
    j = donnees.Journal(path)
    assert j.applied() == {m.version: m.checksum for m in donnees.MIGRATIONS} and j.verify()["ok"]
    j.close()
    j = donnees.Journal(path)                                    # redémarrage : rien à refaire
    assert j.migrate() == []
    changed = dataclasses.replace(donnees.MIGRATIONS[0], up=donnees.MIGRATIONS[0].up + ("SELECT 1",))
    with pytest.raises(donnees.MigrationError):
        j.migrate([changed])                                     # migration modifiée après coup : refusée
    assert j.rollback(1) == [5, 4, 3, 2] and j.verify()["ok"] and j.verify()["schema"] == 1   # ancien schéma : lisible
    assert j.rollback(0) == [1] and j.applied() == {}
    assert not j.conn.execute("SELECT 1 FROM sqlite_master WHERE name='fin_orders'").fetchone()
    assert j.migrate() == [1, 2, 3, 4, 5] and j.verify()["ok"]
    j.close()
    ro = donnees.Journal(path, readonly=True)
    assert ro.verify()["schema"] == 5
    with pytest.raises(sqlite3.OperationalError):
        _decision(ro)                                            # lecture seule : aucune écriture
    ro.close()
    assert (ROOT / "docs" / "DONNEES.md").read_text(encoding="utf-8") == donnees.catalog()


def test_constraints_live_in_the_database():
    j = donnees.Journal(":memory:")
    _decision(j)
    d, a, intent = _checked(j)
    key = intent.idempotency_key
    oid = j.record_order(key, "aave", "BUY", 0.05, 200.0, "paper", "FILLED", decision_id=f"D-{DAY}",
                         risk_check_id=d.risk_check_id, authorization_id=a.authorization_id, fees=0.01)
    assert j.record_order(key, "aave", "BUY", 0.05, 200.0, "paper", "FILLED", decision_id=f"D-{DAY}",
                          risk_check_id=d.risk_check_id, authorization_id=a.authorization_id) == oid
    assert j.counts()["fin_orders"] == 1 and j.counts()["fin_executions"] == 1       # aucun doublon
    with pytest.raises(sqlite3.IntegrityError):                  # un achat sans contrôle du risque
        j.record_order("k2", "aave", "BUY", 0.05, 200.0, "paper", "FILLED")
    with pytest.raises(sqlite3.IntegrityError):
        j.record_order("k3", "aave", "SELL", 0.0, 200.0, "paper", "FILLED")        # quantité nulle
    with pytest.raises(sqlite3.IntegrityError):
        j.record_order("k4", "aave", "HOLD", 1.0, 200.0, "paper", "FILLED")
    with pytest.raises(sqlite3.IntegrityError):                  # contrôle d'une décision inconnue
        j.record_risk_check("D-1999-01-01", "aave", d, a)
    for sql in ("UPDATE fin_risk_checks SET status='REJECTED'", "DELETE FROM fin_risk_checks",
                "DELETE FROM fin_executions"):
        with pytest.raises(sqlite3.DatabaseError, match="ajout seulement"):
            j.conn.execute(sql)                                  # ajout seulement
    with pytest.raises(RuntimeError):
        with j._tx():                                            # transaction annulée en entier
            j.conn.execute("INSERT INTO fin_strategy_versions VALUES ('S-x', '{}', 'c', 'now')")
            raise RuntimeError("panne au milieu")
    assert not j.conn.execute("SELECT 1 FROM fin_strategy_versions WHERE id='S-x'").fetchone()
    refused, ra, _i = _checked(j, "aave", status_ok=False)
    assert refused.status == "EMERGENCY_BLOCK" and j.counts()["fin_risk_checks"] == 2   # le refus est gardé
    assert j.verify()["ok"]
    j.close()


def test_only_the_data_module_writes_the_financial_tables():
    tables = [t.name for t in donnees.TABLES]
    pattern = re.compile("|".join(tables))
    found = [p.relative_to(ROOT).as_posix() for pkg in ("trendguard", "panel", "research")
             for p in (ROOT / pkg).rglob("*.py")
             if p.name != "donnees.py" and pattern.search(p.read_text(encoding="utf-8"))]
    assert not found, found


# ---------- Lignée financière dans le bot ----------

def _lineage_ok(bot):
    j = bot.journal
    assert j.verify()["ok"], j.verify()
    trades = j.trades(1000)
    assert len(trades) == len(bot.state["trades"]) > 0
    buys = j.conn.execute("SELECT COUNT(*) FROM fin_orders WHERE side='BUY' AND status='FILLED'").fetchone()[0]
    assert buys == len(bot.state["buys"])
    for t in trades:
        lin = j.lineage(t["id"])
        assert lin["risk_check"]["status"] == "APPROVED"
        assert lin["entry_order"]["authorization_id"] == lin["risk_check"]["authorization_id"]
        assert lin["decision"]["id"] == lin["entry_order"]["decision_id"]
        assert lin["signal"]["direction"] == "LONG" and lin["signal"]["asset"] == t["asset"]
        assert json.loads(lin["strategy"]["params"])["breakout_n"] == bot.p.breakout_n
        assert lin["data"]["source"].startswith("Binance") and lin["data"]["quality"] == 100.0
        assert len(lin["executions"]) == 2                       # achat et vente
    return trades


def test_every_paper_trade_traces_back_to_its_data(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 200)
    _lineage_ok(bot)
    days = bot.journal.conn.execute("SELECT COUNT(*) FROM fin_decisions").fetchone()[0]
    assert days == 200                                           # une décision par jour, sans doublon


def test_every_live_trade_traces_back_to_its_data(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("live", close, logger)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 200)
    _lineage_ok(bot)


def test_restart_never_duplicates_an_order(tmp_path):
    path = str(tmp_path / "bot.db")
    j = donnees.Journal(path)
    _decision(j)
    d, a, intent = _checked(j)
    args = (intent.idempotency_key, "aave", "BUY", 0.05, 200.0, "paper", "FILLED")
    kw = dict(decision_id=f"D-{DAY}", risk_check_id=d.risk_check_id, authorization_id=a.authorization_id)
    first = j.record_order(*args, **kw)
    j.close()
    again = donnees.Journal(path)                                # le bot a redémarré au milieu
    assert again.record_order(*args, **kw) == first and again.counts()["fin_orders"] == 1
    again.close()


# ---------- Sauvegarde relue et rapport ----------

def test_backup_is_restored_for_real_and_reported(tmp_path, logger):
    db = str(tmp_path / "trendguard_paper.db")
    store = v29.Store(db, logger)
    store.set_kv("trendguard", {"paper": {"cash": 90.0, "holdings": {"aave": {"qty": 1.0}}}, "trades": []})
    store.close()
    j = donnees.Journal(db)
    _decision(j)
    j.close()
    c = report_security.backup_database(db, str(tmp_path / "sauvegardes"), DAY)
    assert c["ok"] is True and "restauration essayée : état du bot relu (1 position(s) paper" in c["detail"]
    assert "journal financier intact" in c["detail"]
    g = tg.GuardConfig(run_mode="paper", db_file=db, log_file=str(tmp_path / "x.log"),
                       lock_file=str(tmp_path / "tg.lock"))
    checks = {x["label"]: x for x in report_health.analysis_checks(g, {})}
    assert checks["Journal financier"]["ok"] is True and "1 décision(s)" in checks["Journal financier"]["detail"]
    broken = tmp_path / "abime.db"
    broken.write_bytes(b"pas une base")
    assert report_security.restore_test(str(broken)).startswith("ÉCHEC")
