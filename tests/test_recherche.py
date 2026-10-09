"""Prompt maître, étape 22 (docs/RECHERCHE.md) : recherche et
connaissances. Chaque source a son rang, sa confiance et sa validité ; un
agrégateur n'est pas une confirmation de plus ; chaque affirmation sur
laquelle le bot agit a son verdict ; contradictions relevées ; seule une
source officielle bloque un achat ; examen AC-001 à AC-080 et son contrat."""

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from panel import assistant as asst
from trendguard import contrats, recherche, report_health, savoir
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, ResearchReport

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)


def _db(tmp_path, bilan=None, views=(), last=None):
    path = str(tmp_path / "savoir.db")
    m = savoir.Memory(path)
    for family, age_h in (last or {"Presse spécialisée": 2, "Reddit": 1, "StockTwits": 30}).items():
        m.conn.execute("INSERT INTO docs (id, ts, day, family, kind, source, title, url, assets, tone) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?)", (family, NOW.timestamp() - age_h * 3600, "2026-10-09", family,
                                                         "presse", family, "titre", "", "", 0.0))
    for row in views:
        m.conn.execute("INSERT INTO views VALUES (?,?,?,?,?)", (*row, 1))
    m.conn.commit()
    if bilan is not None:
        m.put("bilan", bilan)
    m.close()
    return path


def test_every_source_has_its_rank_trust_and_validity(tmp_path):
    bilan = {"scores": [{"source": "Presse spécialisée", "verdict": "fiable", "weeks": 25, "edge": 0.05},
                        {"source": "Reddit", "verdict": "hasard", "weeks": 22, "edge": 0.0}]}
    sv = recherche._savoir_rows(_db(tmp_path, bilan))
    srcs = {s["name"]: s for s in recherche.sources({"last_cycle_ts": NOW.timestamp() - 60,
                                                     "last_watch_day": "2026-10-08"}, sv, NOW)}
    assert set(srcs) == set(recherche.SOURCES)                           # aucune source inventée
    assert set(recherche.SAVOIR_FAMILIES) <= set(srcs)
    assert srcs["Annonces de Binance"]["level"] == 1 and srcs["Reddit"]["level"] == 4
    assert srcs["Presse spécialisée"]["trust"] > srcs["Reddit"]["trust"] > 0
    assert srcs["StockTwits"]["fresh"] is False and srcs["Reddit"]["fresh"] is True    # 30 h pour 6 h permises
    assert srcs["Hacker News"]["age_h"] is None and srcs["Bougies de Binance"]["fresh"]
    assert recherche.trust(1, "fiable", 0.5, 24) == 100.0 and recherche.trust(4, "trompeuse", None, 24) == 14.0


def test_aggregators_are_not_extra_confirmations():
    assert recherche.independent(["Presse spécialisée", "Google Actualités", "Reddit", "Bing Actualités"]) == [
        "Presse spécialisée", "Reddit"]
    assert recherche.independent(["Google Actualités"]) == ["Google Actualités"]     # seul, il compte


def test_contradictions_and_verdicts(tmp_path):
    bilan = {"proven": ["Presse spécialisée"],
             "holds": {"eth": {"value": -0.7, "sources": ["Presse spécialisée"]},
                       "sol": {"value": -0.6, "sources": ["Reddit"]}}}
    views = [("2026-10-09", "Presse spécialisée", "btc", 0.5), ("2026-10-09", "Google Actualités", "btc", 0.4),
             ("2026-10-09", "Reddit", "btc", -0.6), ("2026-10-09", "StockTwits", "eth", 0.1)]
    sv = recherche._savoir_rows(_db(tmp_path, bilan, views))
    state = {"vetoes": {"luna": {"until": "2026-12-31", "reason": "retrait"}, "old": {"until": "2026-01-01"}}}
    cl = {c["claim"]: c for c in recherche.claims(state, sv, NOW)}
    assert cl["Binance retire LUNA de la cote"]["verdict"] == "CONFIRMED"
    assert cl["Binance retire OLD de la cote"]["verdict"] == "OUTDATED"
    assert cl["ETH nettement en baisse cette semaine"]["verdict"] == "PROBABLY_TRUE"
    assert cl["SOL nettement en baisse cette semaine"]["verdict"] == "UNCERTAIN"
    assert all(c["evidence"] for c in cl.values())                        # aucune affirmation sans preuve
    co = recherche.contradictions(sv)
    assert [c["asset"] for c in co] == ["btc"] and co[0]["up"] == ["Presse spécialisée"] and co[0]["down"] == ["Reddit"]


def test_only_an_official_source_can_block_a_buy():
    ok = recherche._measures([], [{"claim": "Binance retire X", "action": "achats bloqués", "level": 1,
                                    "verdict": "CONFIRMED"}])
    assert ok["AC-077"][0] == "PASS"
    bad = recherche._measures([], [{"claim": "un blog dit Y", "action": "achats bloqués", "level": 3,
                                     "verdict": "UNCERTAIN"}])
    assert bad["AC-077"][0] == "FAIL"
    weak = recherche._measures([], [{"claim": "Z baisse", "action": "achat reporté", "level": 4, "verdict": "UNCERTAIN"}])
    assert weak["AC-053"][0] == "FAIL"


def test_the_examination_and_its_contract(tmp_path):
    path = _db(tmp_path, {"scores": [], "proven": [], "holds": {}})
    r = recherche.evaluate(GuardConfig(), {"last_cycle_ts": NOW.timestamp() - 60}, NOW, savoir_db=path)
    assert len(r["rows"]) == 80 and len({x["id"] for x in r["rows"]}) == 80
    assert r["status"] == "READY" and not r["p0_failures"], r["p0_failures"]
    rep = recherche.report_of(r)
    assert contrats.validate("ResearchReport", rep.as_dict()).valid and not rep.fabricated
    text = recherche.render(r)
    assert "## Sources" in text and "## Contradictions du jour" in text and "AC-080" in text
    good = dataclasses.asdict(rep)
    for change in ({"fabricated": True}, {"passed": rep.passed - 1}, {"claims": (("x", "VRAI", 1),)},
                   {"claims": (("x", "CONFIRMED", 7),)}, {"band": "NOT_READY"}):
        with pytest.raises(ContractError):
            ResearchReport(**{**good, **change})
    empty = recherche.evaluate(GuardConfig(), {}, NOW, savoir_db=str(tmp_path / "absente.db"))
    assert all(s["age_h"] is None or s["name"] == "Bougies de Binance" for s in empty["sources"])


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    r = asst.local_answer("quelles sources le bot croit-il", {})["answer"]
    assert "Recherche et connaissances" in r and "officielle" in r and "je n'agis pas" in r
    g = GuardConfig(savoir_db=_db(tmp_path, {"scores": []}))
    line = next(x for x in report_health.analysis_checks(g, {}) if x["label"] == "Recherche et connaissances")
    assert "sources" in line["detail"]
    from trendguard import config, systeme
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: g)
    monkeypatch.setattr(systeme, "read_state", lambda path: {"last_cycle_ts": (NOW - timedelta(minutes=1)).timestamp()})
    code = recherche.main([])
    assert code in (0, 1) and "## Affirmations sur lesquelles le bot agit" in capsys.readouterr().out
