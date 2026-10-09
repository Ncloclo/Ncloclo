"""Prompt maître, étape 29 et ses corrections (docs/PERCEPTION.md) :
perception et observations multi-sources. Chaque observation a sa source et
sa provenance (contrat) ; alignement dans le temps (bougie non close
refusée, sans date jamais comparée) ; fusion sans moyenne (la source de plus
haut rang l'emporte) ; inconnu dit ; manifeste des dépendances STATUT × TYPE
et dépendances interdites ; qualité et son contrat."""

import dataclasses
from datetime import datetime, timezone

import pandas as pd
import pytest

from panel import assistant as asst
from trendguard import contrats, perception, report_health
from trendguard.config import GuardConfig
from trendguard.contrats import ContractError, Observation, PerceptionReport

NOW = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)


def _cache(last="2026-10-08", n=5, btc=80_000.0, eth=2_500.0):
    idx = pd.date_range(end=last, periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"btc": [btc] * n, "eth": [eth] * n}, index=idx)


def _state(day="2026-10-08", btc=80_000.0):
    return {"anticipation": {"day": day, "assets": {"btc": {"close": btc}, "eth": {"close": 2_500.0}}},
            "qualite": {"day": day, "score": 100.0}, "learning": {"books": {"btc": {"spread": 0.0001}}},
            "clock_offset_ms": 700, "clock_uncertainty_ms": 200, "clock_synced_at": "2026-10-09T08:00:00+00:00",
            "evenements": {"day": day, "upcoming": []}}


def _g(tmp_path):
    return GuardConfig(lock_file=str(tmp_path / "x.lock"), db_file=str(tmp_path / "x.db"), universe=("BTC", "ETH"))


def test_every_observation_has_its_source_and_provenance():
    obs, refused = perception.observe(_state(), {"cache de l'évolution": _cache()})
    assert obs and not refused
    assert all(o["source"] and o["provenance"] and o["observation_id"] for o in obs)
    assert {o["modality"] for o in obs} == {"SERIE", "JUGEMENT", "CARNET", "HORLOGE", "EVENEMENT"}
    good = Observation(**{k: v for k, v in obs[0].items() if k in {f.name for f in dataclasses.fields(Observation)}})
    for change in ({"source": " "}, {"provenance": ""}, {"modality": "IMAGE"}, {"day": "08/10/2026"},
                   {"observed_at": "2026-10-09T08:00:00"}, {"rank": 7}, {"value": float("nan")}):
        with pytest.raises(ContractError):
            dataclasses.replace(good, **change)
    bad = _state()
    bad["anticipation"]["assets"]["btc"]["close"] = "illisible"
    obs2, refused2 = perception.observe(bad, {})
    assert refused2 and not any(o["entity"] == "btc" and o["source"] == "bougies du bot" for o in obs2)


def test_temporal_alignment_refuses_unclosed_candles():
    obs, _r = perception.observe(_state(day="2026-10-09"), {"cache des études": _cache(last="2026-09-20")})
    perception.align(obs, NOW)
    bot = [o for o in obs if o["source"] == "bougies du bot"]
    assert bot and all(o["alignment"] == "FUTURE" for o in bot)               # bougie du jour : pas encore close
    assert all(o["alignment"] == "STALE" for o in obs if o["source"] == "cache des études")
    assert all(o["alignment"] == "UNDATED" for o in obs if o["source"] == "carnets d'ordres")
    facts, _c = perception.fuse(obs)
    used = {i for f in facts for i in f["sources"]}
    assert not used & {o["observation_id"] for o in bot}                     # jamais utilisée
    assert not used & {o["observation_id"] for o in obs if o["alignment"] == "UNDATED"}


def test_fusion_confirms_or_keeps_the_higher_rank_never_an_average():
    st = _state(btc=80_000.0)
    obs, _r = perception.observe(st, {"cache de l'évolution": _cache(btc=80_100.0),
                                      "cache des études": _cache(btc=84_000.0)})
    perception.align(obs, NOW)
    facts, conflicts = perception.fuse(obs)
    btc = next(f for f in facts if f["entity"] == "btc" and f["day"] == "2026-10-08")
    assert btc["state"] == "CONFLICT" and btc["value"] == 80_000.0 and btc["source"] == "bougies du bot"
    assert btc["confidence"] == "basse" and len(btc["sources"]) == 3
    c = next(c for c in conflicts if c["entity"] == "btc" and c["day"] == "2026-10-08")
    assert c["against"] == [("cache des études", 84_000.0)] and c["gap_pct"] == 5.0
    eth = next(f for f in facts if f["entity"] == "eth" and f["day"] == "2026-10-08")
    assert eth["state"] == "CONFIRMED" and eth["confidence"] == "haute"
    cur = perception.current(facts, ["btc", "eth", "sol"])
    assert cur[2]["state"] == "UNKNOWN" and cur[2]["value"] is None          # rien vu : inconnu, jamais deviné
    assert cur[0]["day"] == "2026-10-08"


def test_the_dependency_manifest(tmp_path, monkeypatch):
    real = perception.dependencies()
    assert real["ok"], real["problems"]
    statuses = {r["file"]: r["status"] for r in real["rows"]}
    assert [f for f, s in statuses.items() if s == "OBLIGATOIRE"] == [
        "trendguard/donnees.py", "trendguard/contrats.py", "trendguard/apprentissage.py", "trendguard/cyber.py"]
    assert statuses["trendguard/modeles.py"] == "CONDITIONNELLE" and statuses["trendguard/controle.py"] == "ACTIVATION"
    assert {f for f, s in statuses.items() if s == "INTERDITE"} >= {"trendguard/politique.py", "trendguard/autorisation.py",
                                                                    "trendguard/porte.py", "trendguard/bot_execution.py"}
    assert all(r["status"] in perception.DEP_STATUSES and r["type"] in perception.DEP_TYPES for r in real["rows"])
    (tmp_path / "trendguard").mkdir()
    for f in ("donnees", "contrats", "apprentissage", "cyber", "porte"):
        (tmp_path / "trendguard" / f"{f}.py").write_text('"""x"""\n', encoding="utf-8")
    (tmp_path / "trendguard" / "cyber.py").write_text("from . import perception\n", encoding="utf-8")
    me = tmp_path / "trendguard" / "perception.py"
    me.write_text("from . import porte, modeles\nimport socket\n", encoding="utf-8")
    bad = perception.dependencies(tmp_path, me)
    assert not bad["ok"] and bad["forbidden"] == ("trendguard/porte.py",)
    text = " ; ".join(bad["problems"])
    assert "interdite" in text and "cycle" in text and "réseau" in text and "conditionnelle" in text
    monkeypatch.setattr(perception, "MANIFEST", perception.MANIFEST + (("03", "trendguard/donnees.py", "OPTIONNELLE",
                                                                         "DATA", "", ""),))
    assert "deux statuts" in " ; ".join(perception.dependencies()["problems"])   # un seul statut par dépendance


def test_the_quality_and_its_contract(tmp_path):
    g = _g(tmp_path)
    caches = {"cache de l'évolution": _cache(), "cache des études": _cache()}
    r = perception.evaluate(g, _state(), caches, NOW)
    q = r["quality"]
    assert q["status"] == "READY" and not q["p0"] and q["unsourced"] == 0, q
    rep = perception.report_of(r)
    assert contrats.validate("PerceptionReport", rep.as_dict()).valid and rep.confirmed > 0
    assert "## Manifeste des dépendances (statut et type)" in perception.render(r)
    blind = perception.evaluate(g, {}, {}, NOW)
    assert blind["quality"]["status"] in ("NOT_READY", "REJECTED", "DEVELOPMENT", "VALIDATING")
    assert all(f["state"] == "UNKNOWN" for f in blind["current"])
    good = dataclasses.asdict(rep)
    for change in ({"unsourced": 1}, {"forbidden_dependencies": ("trendguard/porte.py",)},
                   {"p0_failures": ("provenance",)}, {"status": "READY", "readiness_score": 90.0},
                   {"confirmed": 10_000}, {"facts": -1}, {"readiness_score": 101.0}):
        with pytest.raises(ContractError):
            PerceptionReport(**{**good, **change})
    assert PerceptionReport(**{**good, "p0_failures": ("provenance",), "status": "NOT_READY"})


def test_rachelle_the_report_and_the_command(capsys, monkeypatch, tmp_path):
    r = asst.local_answer("comment le bot percoit le marche", {})["answer"]
    assert "Perception" in r and "INCONNU" in r and "je n'agis pas" in r
    g = _g(tmp_path)
    line = next(x for x in report_health.analysis_checks(g, {}) if x["label"] == "Perception")
    assert "observation" in line["detail"]
    from trendguard import config, systeme
    monkeypatch.setattr(config, "load_guard_config_from_env", lambda: g)
    monkeypatch.setattr(systeme, "read_state", lambda path: _state())
    monkeypatch.setattr(perception, "load_caches", lambda universe: {"cache de l'évolution": _cache()})
    code = perception.main([])
    assert code in (0, 1) and "## Les sources" in capsys.readouterr().out
