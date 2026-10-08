"""Spécification des contrats de données v1.0 (docs/CONTRATS.md) : types
communs (confiance, incertitude, provenance, montant, enveloppe, erreur),
validateur, versions et migrations, tests négatifs du §77, et leur emploi
réel : audit corrélé et causé, bougies vérifiées, date limite des données
de chaque décision, appels aux IA, consensus des IA, avis du comité."""

import json
import logging
import pathlib
import re
import sqlite3
from datetime import date, timedelta

import pytest
from test_contrats import _audited_bot
from test_market_watch import _run

from test_trendguard import DAY, SIM_FROM, feed, make_bot, synthetic_market
from trendguard import audit, comite, contrats, donnees, modeles, qualite
from trendguard import market_watch as mw
from trendguard.contrats import (
    Confidence,
    ContractError,
    Envelope,
    LLMExecution,
    ModelConsensus,
    Money,
    Provenance,
    Uncertainty,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLAN = {"asset": "aave", "qty": 0.5, "entry": 100.0, "stop": 90.0, "cost": 50.05, "risk_quote": 5.0,
        "decision_day": "2026-10-05"}


@pytest.fixture
def logger():
    lg = logging.getLogger("test.contrats_donnees")
    lg.setLevel(logging.ERROR)
    return lg


def _code(fn):
    with pytest.raises(ContractError) as e:
        fn()
    return e.value.code


# ---------- Tests négatifs obligatoires (§77) ----------

def test_negative_cases_are_refused_never_corrected():
    ok = contrats.validate("OrderIntent", PLAN)
    assert ok.valid and ok.errors == () and ok.validated_at.endswith("Z")
    cases = {
        "champ requis absent : stop": {k: v for k, v in PLAN.items() if k != "stop"},
        "champ inconnu : levier": dict(PLAN, levier=3),
        "OUT_OF_RANGE": dict(PLAN, qty=-1.0),                                 # quantité négative
        "WRONG_TYPE": dict(PLAN, qty="0.5"),
        "INVALID_FIELD": dict(PLAN, side="SELL"),                              # ordre invalide
    }
    for expected, data in cases.items():
        r = contrats.validate("OrderIntent", data)
        assert not r.valid and any(expected in e for e in r.errors), (expected, r.errors)
    assert not contrats.validate("Inconnu", {}).valid
    assert _code(lambda: Confidence(1.2, "m")) == "OUT_OF_RANGE"               # confiance > 1
    assert _code(lambda: Confidence(-0.1, "m")) == "OUT_OF_RANGE"              # confiance < 0
    assert _code(lambda: Confidence(0.5, "")) == "MISSING_FIELD"
    assert _code(lambda: Uncertainty("PEUT-ÊTRE")) == "INVALID_ENUM"           # valeur non permise
    assert _code(lambda: Money("10", "usd")) == "INVALID_CURRENCY"
    assert _code(lambda: Money(0.1, "USD")) == "WRONG_TYPE"                    # jamais un flottant brut
    assert _code(lambda: Provenance("MARKET_DATA", "Binance", "2026-10-06T10:00:00")) == "INVALID_TIMESTAMP"
    assert _code(lambda: Provenance("MARKET_DATA", "Binance", "hier")) == "INVALID_TIMESTAMP"
    assert _code(lambda: Envelope("x", "s", "1", "bot", {})) == "INVALID_VERSION"
    assert _code(lambda: Envelope("x", "s", "1.0.0", "bot", {}, message_id="42")) == "INVALID_UUID"
    assert _code(lambda: Envelope("x", "s", "1.0.0", "bot", {}, classification="TOP")) == "INVALID_ENUM"
    good = dict(execution_id=contrats.new_id(), request_id=contrats.new_id(), purpose="x", provider="p", model="m",
                prompt_id="p", prompt_version="v", classification="PUBLIC", status="COMPLETED", latency_ms=5)
    assert LLMExecution(**good).input_tokens is None                          # inconnu, pas 0
    assert _code(lambda: LLMExecution(**dict(good, input_tokens=-3))) == "OUT_OF_RANGE"   # jetons négatifs
    assert _code(lambda: LLMExecution(**dict(good, status="FAILED"))) == "INCONSISTENT"   # échec sans raison
    assert _code(lambda: contrats.upgrade("Envelope", {"schema_version": "9.0.0"})) == "UNSUPPORTED_VERSION"


def test_money_and_unknown_values():
    m = Money.of(0.1 + 0.2, "USDT")
    assert m.amount.as_tuple().exponent >= -8 and m.as_dict() == {"amount": "0.3", "currency": "USDT"}
    assert contrats.money_or_none(None, "USDT") is None                        # inconnu n'est pas zéro
    assert contrats.money_or_none(float("nan"), "USDT") is None
    assert Money("1250.50", "USD").as_dict()["amount"] == "1250.50"


def test_envelope_and_error_envelope_with_migration():
    e = Envelope("Order.Proposed", "OrderIntent", "1.0.0", "bot_execution", {"asset": "aave"},
                 provenance=Provenance("MARKET_DATA", "Binance", contrats.utc_now(), "VERIFIED"))
    d = e.as_dict()
    assert d["security"] == {"classification": "INTERNAL"} and d["producer"] == {"module": "bot_execution"}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", d["occurred_at"])
    assert d["message_id"] != d["correlation_id"] and json.dumps(d)
    err = ContractError("WRONG_TYPE", "qty", details={"field": "qty"}).envelope("porte", "D-x")
    assert err["schema_version"] == "2.0.0" and err["category"] == "VALIDATION" and err["severity"] == "ERROR"
    with pytest.raises(ValueError):
        ContractError("X", "y", category="VALIDATION_ERROR")                  # catégorie hors spécification
    v1 = {"error_code": "WRONG_TYPE", "message": "qty", "category": "VALIDATION_ERROR", "severity": "error",
          "retryable": False, "source": "porte", "timestamp": "2026-10-06T00:03:00+00:00", "correlation_id": "D-x"}
    up = contrats.upgrade("ErrorEnvelope", v1)
    assert up["code"] == "WRONG_TYPE" and up["category"] == "VALIDATION" and up["occurred_at"] == v1["timestamp"]
    assert set(up) == set(err)                                                 # même forme que la v2


# ---------- Registre (§65-66, §79) ----------

def test_registry_owners_versions_and_classification():
    ids = {c.contract_id for c in contrats.REGISTRY}
    assert {"AuditEvent.v2", "OHLCV.v1", "DecisionRecord.v1", "Envelope.v1", "ErrorEnvelope.v2", "Confidence.v1",
            "ModelSelection.v1", "LLMExecution.v1", "ModelConsensus.v1", "CommitteeView.v1"} <= ids
    for c in contrats.REGISTRY:
        assert contrats.SEMVER.match(c.version) and c.classification in contrats.CLASSIFICATIONS
        assert c.producer and c.consumer and c.files
        assert all((ROOT / f).exists() for f in c.files), c.contract_id
        assert c.version.split(".")[0] == c.contract_id.rsplit(".v", 1)[1]    # v2 = version 2.x.x
    with pytest.raises(ContractError):
        contrats.Contract("X.v1", "", "", "", "", "", "", "", "", "", "", "", 1, version="1")
    assert set(modeles.PRIVACY) <= set(contrats.CLASSIFICATIONS)


# ---------- Audit corrélé et causé (§4, §47-48, §53) ----------

def test_every_audited_action_has_a_canonical_event_type(tmp_path):
    used = set()
    for path in (ROOT / "trendguard").glob("*.py"):
        used |= set(re.findall(r'_audit\(\s*"([a-z_.]+)"', path.read_text(encoding="utf-8")))
        used |= set(re.findall(r'_audit\(\s*"([a-z_.]+)" if', path.read_text(encoding="utf-8")))
    used |= {"mode_sur.leve"}
    assert used and used <= set(audit.EVENT_TYPES), used - set(audit.EVENT_TYPES)
    assert all(re.fullmatch(r"[A-Z][A-Za-z]+\.[A-Z][A-Za-z]+\.[A-Z][A-Za-z]+", t) for t in audit.EVENT_TYPES.values())
    log = audit.AuditLog(str(tmp_path / "x.audit.jsonl"))
    e = log.append("bot", "ordre.achat", "aave", "exécuté", correlation_id="D-2026-10-05", causation_id="R-1")
    assert e["event_type"] == "Order.Buy.Executed" and e["causation_id"] == "R-1" and e["version"] == 2
    with pytest.raises(KeyError):
        log.append("bot", "ordre.inconnu", "aave", "?")
    assert audit.verify(log.path)["ok"]


def test_the_buy_chain_is_correlated_and_caused(logger, tmp_path):
    close, volume = synthetic_market()
    bot, fb = _audited_bot("paper", close, logger, tmp_path)
    assert bot.boot()
    _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 120)
    events = audit.read(audit.path_for(bot.g), 10_000)
    buys = [e for e in events if e["action"] == "ordre.achat" and e["result"] == "exécuté"]
    checks = {e["after"]["risk_check_id"]: e for e in events if e["action"] == "porte.controle"}
    assert buys and audit.verify(audit.path_for(bot.g))["ok"]
    for b in buys:
        check = checks[b["causation_id"]]                       # l'achat a pour cause un contrôle du risque
        assert check["correlation_id"] == b["correlation_id"] and check["causation_id"] == b["correlation_id"]
        assert b["after"]["cost"]["currency"] == "USDT" and isinstance(b["after"]["cost"]["amount"], str)
    days = [r["data_cutoff_at"] for r in bot.journal.conn.execute(
        "SELECT day, data_cutoff_at FROM fin_decisions ORDER BY day")]
    assert days and all(days)                                   # chaque décision dit où s'arrêtent ses données
    first = bot.journal.conn.execute("SELECT day, data_cutoff_at FROM fin_decisions ORDER BY day").fetchone()
    assert first["data_cutoff_at"].startswith(str(date.fromisoformat(first["day"]) + timedelta(days=1)))


def test_a_decision_cannot_look_into_the_future():
    j = donnees.Journal(":memory:")
    assert max(j.applied()) >= 3
    j.record_decision("2026-10-05", "paper", {"a": 1}, True, None, 100.0, False, False, [], 90.0,
                      "2026-10-06T00:00:00+00:00")
    with pytest.raises(ValueError):
        j.record_decision("2026-10-04", "paper", {"a": 1}, True, None, 100.0, False, False, [], 90.0,
                          "2026-10-06T00:00:00+00:00")
    with pytest.raises(sqlite3.IntegrityError):                 # la base refuse aussi, en second rempart
        j.conn.execute("INSERT INTO fin_decisions (id, day, mode, strategy_version_id, bull, equity, halted, "
                       "safe_mode, garde_blocked, data_source, created_at, data_cutoff_at) SELECT 'D-x', "
                       "'2026-10-03', 'paper', strategy_version_id, 1, 1, 0, 0, '[]', 's', 't', "
                       "'2026-10-06T00:00:00+00:00' FROM fin_decisions")
    assert j.verify()["ok"] and j.rollback(2) == [5, 4, 3] and j.migrate() == [3, 4, 5]


# ---------- Bougies vérifiées (§41) ----------

def test_incoherent_candles_exclude_the_crypto_and_btc_defers(logger):
    import pandas as pd
    rows = pd.DataFrame({"open": [10, 10, 10], "high": [11, 9, 12], "low": [9, 9, 13], "close": [10, 10, 11],
                         "volume": [1, 1, -1]})
    assert contrats.ohlcv_violations(rows) == 3                # plus haut trop bas, plus bas trop haut, volume
    assert contrats.ohlcv_violations(rows.assign(high=float("nan"))) == 1        # inconnu n'est pas faux
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    feed(fb, close, volume)
    k = SIM_FROM - 3
    bar = fb.ohlcv[("ETH/USDT", "1d")][k]
    bar[2] = bar[4] * 0.5                                       # plus haut sous la clôture
    d = close.index[SIM_FROM]
    for a in close.columns:
        fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    q = bot.state["qualite"]
    assert bot._ohlcv_bad == {"eth": 1} and "ETH écartée(s) du jour" in " ".join(q["issues"])
    assert bot.state["last_decision_day"] == str(d.date())
    fb.ohlcv[("BTC/USDT", "1d")][k][3] = fb.ohlcv[("BTC/USDT", "1d")][k][4] * 2   # plus bas au-dessus
    d2 = close.index[SIM_FROM + 1]
    bot.run_cycle(now=d2.to_pydatetime() + DAY + timedelta(minutes=5))
    assert bot.state["last_decision_day"] == str(d.date())                        # BTC douteux : reportée
    assert qualite.quality(close.iloc[:SIM_FROM], str(close.index[SIM_FROM - 1].date()), {"eth": 2})["score"] < 100


# ---------- Consensus des IA et avis du comité (§11-12, §28-29) ----------

def test_consensus_contract_statuses():
    def ok(*moods):
        return {f"ia{k}": {"sentiment": m, "views": {"aave": m}} for k, m in enumerate(moods)}
    assert mw.consensus_contract({}, {}).status == "NONE"
    assert mw.consensus_contract(ok(0.4), {}).status == "WEAK"                 # une seule IA : jamais fort
    assert mw.consensus_contract(ok(0.4, 0.5), {}).status == "STRONG"
    two = ok(0.6, -0.6)
    c = mw.consensus_contract(two, mw.disagreements(two))
    assert c.status == "CONFLICT" and c.disagreement_score == 0.6 and c.agreement_score == 0.4
    with pytest.raises(ContractError):
        ModelConsensus("STRONG", 2, 0.4, 0.6, {"aave": (-0.6, 0.6)})           # désaccord net non dit
    with pytest.raises(ContractError):
        ModelConsensus("STRONG", 0, 1.0, 0.0)


def test_committee_views_carry_confidence_and_uncertainty():
    data, _day = comite._scenario("nette")
    view, _res = comite.evaluate("aave", data, comite.registry())
    d = comite.as_dict(view)
    assert contrats.validate("Confidence", d["confidence"]).valid and d["confidence"]["calibrated"] is False
    assert contrats.validate("Uncertainty", d["uncertainty"]).valid
    assert d["confidence"]["score"] == round(min(1.0, abs(view.consensus) / 100), 2) and json.dumps(d)


def test_llm_executions_follow_their_contract():
    led = modeles.Ledger("")
    env = {"OPENAI_API_KEY": "k" * 20, "MISTRAL_API_KEY": "k" * 20}
    for provider, model in (("openai", "gpt-5-mini"), ("mistral", "mistral-medium-latest")):
        led.bench_record("essai", modeles.bench_version(), {   # banc réussi : modèles approuvés
            "provider": provider, "model": model, "accuracy": 1.0, "categories": {"faits": 1.0}, "p50_ms": 500,
            "cases": 8, "errors": 0, "passed": True, "note": ""})

    def call(m):
        if m.provider == "openai":
            raise mw.HttpError(503, "panne")
        return "ok", (3, "beaucoup")                           # jetons illisibles : inconnus, pas inventés
    ex = modeles.execute("x", ("p", "v"), "bonjour", call, env=env, ledger=led)
    rows = led.last(2)
    assert ex.ok and ex.tokens == (3, None) and contrats.UUID_RE.match(ex.request_id)
    assert {r["request_id"] for r in rows} == {ex.request_id} and len({r["execution_id"] for r in rows}) == 2
    with pytest.raises(ContractError):
        led.record("x", "p", "m", ("p", "v"), "PUBLIC", True, 5, tokens=(-1, 0))
    with pytest.raises(ContractError):
        led.record("x", "p", "m", ("p", "v"), "SECRET-DEFENSE", True, 5)
    assert len(led.last(10)) == 2                                              # rien d'écrit en cas de refus
