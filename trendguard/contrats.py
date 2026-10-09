"""
Contrats entre les modules du bot (prompt maître, étape 2 ; docs/CONTRATS.md).

Chaque échange important a un contrat écrit : qui le produit, qui le
consomme, ce qui entre et ce qui sort, les erreurs possibles, les droits, le
délai, les nouveaux essais, l'unicité (idempotence) et la trace laissée
(audit). Le registre ci-dessous est la référence : docs/CONTRATS.md en est
tiré, et un test vérifie que les deux restent identiques ; un contrat changé
oblige donc à relire sa documentation et ses tests.

Les contrats du chemin d'achat (intention d'ordre, contrôle du risque,
autorisation, mode sûr) ont en plus des données vérifiées à la création :
une valeur absente, du mauvais type, hors limites ou d'une autre version est
refusée (ContractError), jamais corrigée en silence.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import MISSING, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar, Dict, List, Optional, Tuple

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


ERROR_CATEGORIES = ("VALIDATION", "AUTHENTICATION", "AUTHORIZATION", "NOT_FOUND", "CONFLICT", "RATE_LIMIT",
                    "TIMEOUT", "DEPENDENCY", "TRANSIENT", "PERMANENT", "SECURITY", "POLICY", "FINANCIAL", "SYSTEM")
SEVERITIES = ("INFO", "WARNING", "ERROR", "CRITICAL")


class ContractError(ValueError):
    """Donnée refusée par un contrat (enveloppe d'erreur standard)."""

    def __init__(self, code: str, message: str, category: str = "VALIDATION",
                 retryable: bool = False, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        if category not in ERROR_CATEGORIES:
            raise ValueError(f"catégorie d'erreur inconnue : {category!r}")
        self.code, self.category, self.retryable = code, category, retryable
        self.details = dict(details or {})

    def envelope(self, source: str, correlation_id: str = "", severity: str = "ERROR") -> Dict[str, Any]:
        """L'erreur au format commun (ErrorEnvelope.v2 de la spécification
        des contrats de données, §13), pour le journal d'audit."""
        if severity not in SEVERITIES:
            raise ValueError(f"gravité inconnue : {severity!r}")
        return {"schema_name": "ErrorEnvelope", "schema_version": "2.0.0", "code": self.code,
                "category": self.category, "message": str(self), "retryable": self.retryable,
                "severity": severity, "source": source, "details": self.details, "occurred_at": utc_now(),
                "correlation_id": correlation_id or None}


def _number(name: str, v: Any, positive: bool = True) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ContractError("WRONG_TYPE", f"{name} : nombre attendu, reçu {type(v).__name__}")
    v = float(v)
    if not math.isfinite(v) or (positive and v <= 0):
        raise ContractError("OUT_OF_RANGE", f"{name} : {v} n'est pas un nombre {'positif' if positive else 'fini'}")
    return v


def _version(obj: Any, expected: int) -> None:
    if obj.version != expected:
        raise ContractError("WRONG_VERSION", f"{type(obj).__name__} v{obj.version} : seule la v{expected} "
                                             "est comprise")


@dataclass(frozen=True)
class OrderIntent:
    """Intention d'achat (OrderIntent.v1), tirée du plan du jour et
    contrôlée par la porte d'exécution avant tout ordre. Les ventes n'en
    ont pas : elles réduisent le risque et ne sont jamais bloquées."""
    VERSION: ClassVar[int] = 1
    asset: str
    qty: float
    entry: float
    stop: float
    cost: float
    risk_quote: float
    decision_day: str
    side: str = "BUY"
    strategy: str = "trendguard"
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        if not isinstance(self.asset, str) or not re.fullmatch(r"[a-z0-9]{2,12}", self.asset):
            raise ContractError("INVALID_FIELD", f"asset : nom de crypto invalide ({self.asset!r})")
        if self.side != "BUY":
            raise ContractError("INVALID_FIELD", f"side : seul l'achat passe par la porte ({self.side!r})")
        for name in ("qty", "entry", "stop", "cost", "risk_quote"):
            object.__setattr__(self, name, _number(name, getattr(self, name)))
        if not isinstance(self.decision_day, str) or not DAY_RE.match(self.decision_day):
            raise ContractError("INVALID_FIELD", f"decision_day : date AAAA-MM-JJ attendue ({self.decision_day!r})")

    @property
    def idempotency_key(self) -> str:
        """Un achat par crypto et par décision : la même intention ne peut
        jamais produire deux ordres."""
        return f"{self.decision_day}:{self.asset}:{self.side}"

    @classmethod
    def from_plan(cls, plan: Dict[str, Any], day: str) -> "OrderIntent":
        """L'intention tirée d'un plan d'achat du bot (champ manquant :
        ContractError)."""
        missing = [k for k in ("asset", "qty", "entry", "stop", "cost", "risk_quote") if k not in plan]
        if missing:
            raise ContractError("MISSING_FIELD", "plan incomplet : " + ", ".join(missing))
        return cls(asset=plan["asset"], qty=plan["qty"], entry=plan["entry"], stop=plan["stop"],
                   cost=plan["cost"], risk_quote=plan["risk_quote"], decision_day=plan.get("day") or day)


RISK_STATUSES = ("APPROVED", "APPROVED_WITH_LIMIT", "REVIEW", "REJECTED", "EMERGENCY_BLOCK")


@dataclass(frozen=True)
class RiskDecision:
    """Résultat du contrôle du risque (RiskDecision.v1). La porte n'emploie
    que APPROVED, REJECTED et EMERGENCY_BLOCK : elle ne redimensionne
    jamais (c'est le rôle du plan) et ne demande pas d'avis humain en marche
    automatique ; les deux autres statuts restent réservés."""
    VERSION: ClassVar[int] = 1
    risk_check_id: str
    status: str
    approved_size: float
    expected_loss: float
    exposure_pct: float
    open_risk_pct: float
    limit_checks: Tuple[Tuple[str, bool, str], ...]
    warnings: Tuple[str, ...] = ()
    blocking_reasons: Tuple[str, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        if self.status not in RISK_STATUSES:
            raise ContractError("INVALID_FIELD", f"status inconnu : {self.status!r}")
        if self.status == "APPROVED" and self.blocking_reasons:
            raise ContractError("INCONSISTENT", "un achat approuvé ne peut avoir de raison de blocage")
        if self.status in ("REJECTED", "EMERGENCY_BLOCK") and not self.blocking_reasons:
            raise ContractError("INCONSISTENT", "un refus doit dire pourquoi")

    @property
    def approved(self) -> bool:
        return self.status == "APPROVED"


@dataclass(frozen=True)
class ExecutionAuthorization:
    """Autorisation d'exécuter (ExecutionAuthorization.v1), valable quelques
    minutes, liée au contrôle du risque qui l'a permise."""
    VERSION: ClassVar[int] = 1
    authorized: bool
    authorization_id: str
    policy_version: str
    risk_check_id: str
    expiration: str
    restrictions: Tuple[str, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        if not self.risk_check_id:
            raise ContractError("MISSING_FIELD", "une autorisation vient toujours d'un contrôle du risque")

    def valid_at(self, now: datetime) -> bool:
        """Autorisée et pas encore expirée à `now`."""
        return self.authorized and now < datetime.fromisoformat(self.expiration)


@dataclass(frozen=True)
class SafeModeState:
    """Mode sûr (SafeModeState.v1) : plus aucun achat ; le bot continue de
    lire, analyser, protéger et vendre ses positions."""
    VERSION: ClassVar[int] = 1
    active: bool = False
    reason: str = ""
    activated_at: str = ""
    activated_by: str = ""
    restricted_operations: Tuple[str, ...] = ("achats",)
    recovery_conditions: str = "python trendguard_bot.py mode-sur off"
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)

    @classmethod
    def from_dict(cls, d: Any) -> "SafeModeState":
        """État relu du fichier du mode sûr ; illisible : mode sûr ACTIF
        (en cas de doute, on n'achète pas)."""
        if not isinstance(d, dict) or not d:
            return cls()
        try:
            names = {f.name for f in fields(cls)}
            kw = {k: (tuple(v) if isinstance(v, list) else v) for k, v in d.items() if k in names}
            return cls(**kw)
        except (TypeError, ContractError):
            return cls(active=True, reason="fichier du mode sûr illisible", activated_by="bot")


# ══════════════════════════════════════════════════════════════════════
# Types communs (spécification des contrats de données v1.0)
# ══════════════════════════════════════════════════════════════════════

CLASSIFICATIONS = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "SENSITIVE", "RESTRICTED", "SECRET", "LOCAL_ONLY")
LEVELS4 = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
VERIFICATION = ("VERIFIED", "PARTIALLY_VERIFIED", "FAILED", "UNKNOWN")
CONSENSUS_STATUSES = ("STRONG", "MODERATE", "WEAK", "NONE", "CONFLICT")
NO_TRADE_REASONS = ("MODEL_DISAGREEMENT", "INSUFFICIENT_DATA", "HIGH_RISK", "STALE_DATA", "LOW_CONFIDENCE",
                    "POLICY_BLOCK", "MARKET_UNCERTAINTY")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def utc_now() -> str:
    """Horodatage UTC ISO-8601 à la milliseconde (« …T16:21:00.123Z »)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    """Identifiant unique (UUID v4)."""
    return str(uuid.uuid4())


def check_uuid(name: str, v: Any) -> str:
    if not isinstance(v, str) or not UUID_RE.match(v):
        raise ContractError("INVALID_UUID", f"{name} : UUID attendu ({v!r})")
    return v


def check_timestamp(name: str, v: Any) -> str:
    """Horodatage ISO-8601 avec fuseau (une heure sans fuseau est refusée)."""
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise ContractError("INVALID_TIMESTAMP", f"{name} : date ISO-8601 attendue ({v!r})") from None
    if dt.tzinfo is None:
        raise ContractError("INVALID_TIMESTAMP", f"{name} : fuseau horaire absent ({v!r})")
    return str(v)


def _enum(name: str, v: Any, allowed: Tuple[str, ...]) -> None:
    if v not in allowed:
        raise ContractError("INVALID_ENUM", f"{name} : {v!r} n'est pas parmi {', '.join(allowed)}")


def _score01(name: str, v: Any) -> float:
    v = _number(name, v, positive=False)
    if not 0.0 <= v <= 1.0:
        raise ContractError("OUT_OF_RANGE", f"{name} : {v} hors de [0, 1]")
    return v


@dataclass(frozen=True)
class Confidence:
    """Confiance (§11) : un score de 0 à 1, la méthode qui le produit, s'il
    est calibré et sur quoi il repose. Une confiance n'est pas une
    probabilité d'avoir raison."""
    score: float
    method: str
    calibrated: bool = False
    basis: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", _score01("score", self.score))
        if not self.method:
            raise ContractError("MISSING_FIELD", "method : une confiance dit comment elle est obtenue")


@dataclass(frozen=True)
class Uncertainty:
    """Incertitude (§12) : niveau, sources, inconnues, hypothèses, impact."""
    level: str
    sources: Tuple[str, ...] = ()
    unknowns: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    impact: str = "LOW"

    def __post_init__(self) -> None:
        _enum("level", self.level, LEVELS4)
        _enum("impact", self.impact, LEVELS4)


@dataclass(frozen=True)
class Provenance:
    """D'où vient une donnée (§10) : source, quand, version, transformations,
    vérification."""
    source_type: str
    source_name: str
    retrieved_at: str
    verification_status: str = "UNKNOWN"
    source_version: str = ""
    observed_at: str = ""
    transformation: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check_timestamp("retrieved_at", self.retrieved_at)
        if self.observed_at:
            check_timestamp("observed_at", self.observed_at)
        _enum("verification_status", self.verification_status, VERIFICATION)


@dataclass(frozen=True)
class Money:
    """Montant (§59) : décimal exact et devise, jamais un flottant brut."""
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.amount, float) or not isinstance(self.amount, (Decimal, int, str)):
            raise ContractError("WRONG_TYPE", "amount : Decimal attendu (un flottant passe par Money.of)")
        try:
            object.__setattr__(self, "amount", Decimal(self.amount))
        except InvalidOperation:
            raise ContractError("WRONG_TYPE", f"amount : nombre décimal attendu ({self.amount!r})") from None
        if not self.amount.is_finite():
            raise ContractError("OUT_OF_RANGE", "amount : montant fini attendu")
        if not isinstance(self.currency, str) or not re.fullmatch(r"[A-Z]{3,5}", self.currency):
            raise ContractError("INVALID_CURRENCY", f"currency : code de devise attendu ({self.currency!r})")

    @classmethod
    def of(cls, value: float, currency: str, scale: int = 8) -> "Money":
        """Un flottant du moteur arrondi à `scale` décimales (8 : précision
        des montants de Binance)."""
        return cls(Decimal(str(round(_number("amount", value, positive=False), scale))), currency)

    def as_dict(self) -> Dict[str, str]:
        return {"amount": format(self.amount, "f"), "currency": self.currency}


def money_or_none(value: Any, currency: str) -> Optional[Dict[str, str]]:
    """Montant au format commun, ou None s'il est inconnu ou illisible
    (jamais 0 à la place : inconnu n'est pas zéro)."""
    try:
        return Money.of(value, currency).as_dict()
    except ContractError:
        return None


@dataclass(frozen=True)
class ValidationResult:
    """Résultat d'une validation (§14)."""
    valid: bool
    errors: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    schema_name: str = ""
    validator_version: str = "1.0.0"
    validated_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class Envelope:
    """Enveloppe canonique d'un message entre modules (§8) : identité,
    schéma et version, producteur, corrélation, cause, classification,
    contenu, provenance."""
    message_type: str
    schema_name: str
    schema_version: str
    producer: str
    payload: Dict[str, Any]
    classification: str = "INTERNAL"
    correlation_id: str = field(default_factory=new_id)
    causation_id: Optional[str] = None
    trace_id: str = field(default_factory=new_id)
    message_id: str = field(default_factory=new_id)
    occurred_at: str = field(default_factory=utc_now)
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not SEMVER.match(self.schema_version):
            raise ContractError("INVALID_VERSION", f"schema_version : MAJEUR.MINEUR.CORRECTIF attendu "
                                                   f"({self.schema_version!r})")
        _enum("classification", self.classification, CLASSIFICATIONS)
        for name in ("message_id", "trace_id"):
            check_uuid(name, getattr(self, name))
        check_timestamp("occurred_at", self.occurred_at)
        if not isinstance(self.payload, dict):
            raise ContractError("WRONG_TYPE", "payload : objet attendu")

    def as_dict(self) -> Dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["provenance"] = None if self.provenance is None else {
            f.name: getattr(self.provenance, f.name) for f in fields(self.provenance)}
        d["security"] = {"classification": d.pop("classification")}
        d["producer"] = {"module": self.producer}
        return d


LLM_STATUSES = ("COMPLETED", "FAILED", "TIMEOUT", "CANCELLED")


def _count(name: str, v: Any) -> Optional[int]:
    """Nombre de jetons : entier positif ou nul, ou None (inconnu, jamais 0
    par défaut)."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ContractError("OUT_OF_RANGE", f"{name} : entier positif ou nul attendu ({v!r})")
    return v


@dataclass(frozen=True)
class LLMExecution:
    """Un appel à un modèle d'IA (LLMExecution.v1, §24 et §49)."""
    execution_id: str
    request_id: str
    purpose: str
    provider: str
    model: str
    prompt_id: str
    prompt_version: str
    classification: str
    status: str
    latency_ms: int
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    error: str = ""
    fallback_from: str = ""
    reason: str = ""
    input_hash: str = ""

    def __post_init__(self) -> None:
        check_uuid("execution_id", self.execution_id)
        check_uuid("request_id", self.request_id)
        _enum("classification", self.classification, CLASSIFICATIONS)
        _enum("status", self.status, LLM_STATUSES)
        _count("latency_ms", self.latency_ms)
        _count("input_tokens", self.input_tokens)
        _count("output_tokens", self.output_tokens)
        if self.status != "COMPLETED" and not self.error:
            raise ContractError("INCONSISTENT", "un appel raté dit pourquoi")


@dataclass(frozen=True)
class ModelConsensus:
    """Consensus de plusieurs modèles (ModelConsensus.v1, §28) : état,
    accord et désaccord de 0 à 1, désaccords par sujet (plus bas, plus
    haut)."""
    status: str
    participants: int
    agreement_score: float
    disagreement_score: float
    disagreements: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _enum("status", self.status, CONSENSUS_STATUSES)
        _count("participants", self.participants)
        for name in ("agreement_score", "disagreement_score"):
            object.__setattr__(self, name, _score01(name, getattr(self, name)))
        if self.disagreements and self.status != "CONFLICT":
            raise ContractError("INCONSISTENT", "un désaccord net fait un consensus CONFLICT")
        if self.participants == 0 and self.status != "NONE":
            raise ContractError("INCONSISTENT", "sans participant, pas de consensus")


DISAGREEMENT_TYPES = ("FACTUAL", "NUMERICAL", "LOGICAL", "TEMPORAL", "FINANCIAL", "STRATEGIC", "INTERPRETATIVE",
                      "POLICY")
RESOLUTIONS = ("OPEN", "RESOLVED", "ESCALATED", "NO_DECISION")


@dataclass(frozen=True)
class ModelDisagreement:
    """Désaccord entre modèles (ModelDisagreement.v1 ; §29 de la
    spécification, §27 de l'étape 6) : sujet, type, gravité, position de
    chacun, issue. NO_DECISION : pas de moyenne trompeuse, le désaccord est
    montré tel quel."""
    subject: str
    type: str
    severity: str
    positions: Tuple[Tuple[str, float], ...]
    resolution_status: str = "NO_DECISION"
    disagreement_id: str = field(default_factory=new_id)

    def __post_init__(self) -> None:
        _enum("type", self.type, DISAGREEMENT_TYPES)
        _enum("severity", self.severity, LEVELS4)
        _enum("resolution_status", self.resolution_status, RESOLUTIONS)
        check_uuid("disagreement_id", self.disagreement_id)
        if len(self.positions) < 2:
            raise ContractError("INCONSISTENT", "un désaccord oppose au moins deux positions")


ASSET_CLASSES = ("EQUITY", "ETF", "BOND", "INDEX", "FX", "COMMODITY", "CRYPTO", "RATE", "DERIVATIVE")
INSTRUMENT_STATUSES = ("TRADING", "VETO", "DELISTED", "NO_DATA")
SIGNAL_DIRECTIONS = ("LONG", "SHORT", "NEUTRAL")
SIGNAL_TYPES = ("TREND", "MOMENTUM", "MEAN_REVERSION", "BREAKOUT", "VALUE", "QUALITY", "GROWTH", "MACRO", "EVENT",
                "SENTIMENT", "VOLATILITY", "STATISTICAL_ARBITRAGE")
HORIZONS = ("INTRADAY", "SHORT_TERM", "MEDIUM_TERM", "LONG_TERM")
SIGNAL_STATUSES = ("CANDIDATE", "VALIDATED", "REJECTED", "EXPIRED")
ANALYSIS_RECOMMENDATIONS = ("BUY_SIGNAL", "WATCH", "NO_TRADE")
SCENARIO_NAMES = ("EXTREME", "STRESS", "BEAR", "BASE", "BULL")
INSTRUMENT_RE = re.compile(r"^[a-z]+:[a-z]+:[A-Z0-9]+-[A-Z0-9]+$")
# Étape 9 (moteur de stratégie) : raisons précises de la règle, décisions et états.
STRATEGY_REASONS = ("NO_BREAKOUT", "NO_MOMENTUM", "SHORT_HISTORY", "LOW_LIQUIDITY", "NO_VOLATILITY",
                    "REGIME_BLOCK", "DATA_INVALID")
STRATEGY_DECISIONS = ("TRADE_CANDIDATE", "NO_TRADE", "HOLD", "EXIT")
STRATEGY_STATES = ("STRATEGY_READY", "NO_TRADE", "STRATEGY_BLOCKED", "IN_POSITION")
# Étape 10 (moteur de backtest) : verdicts, préparation au moteur de risque, limite obligatoire.
BACKTEST_STATUSES = ("VALID", "VALID_WITH_WARNINGS", "INVALID", "REJECTED")
BACKTEST_READINESS = ("READY_FOR_RISK", "RESEARCH_ONLY", "REJECTED")
NO_GUARANTEE = "Un backtest n'est pas une garantie de performance future."
QUANT_STATUSES = ("VALIDATED", "VALIDATED_WITH_WARNINGS", "INVALID")
PAST_NOT_PROMISE = "Une mesure du passé n'est ni une certitude ni une promesse."
PORTFOLIO_DECISIONS = ("HOLD", "REVIEW", "NO_ALLOCATE")
REBALANCE_DECISIONS = ("NO_REBALANCE", "REBALANCE_PROPOSED")
ACCEPTANCE_STATUSES = ("ACCEPTED", "BLOCKED")
POLICY_ACTIONS = ("ALLOW", "ALLOW_WITH_LIMITS", "REDUCE_SIZE", "NO_TRADE", "REQUIRE_HUMAN_APPROVAL", "BLOCK",
                  "SAFE_MODE", "FREEZE_ACCOUNT")
ACCEPTANCE_BANDS = ("PAPER_READY", "PAPER_READY_CANDIDATE", "VALIDATING", "DEVELOPMENT", "REJECTED")
# Étape 16 (porte d'exécution) : validation finale, examen de la porte.
FINAL_VALIDATION_STATUSES = ("PASS", "BLOCK")
GATE_READINESS_STATUSES = ("READY_FOR_CONTROLLED_LIVE_EXECUTION", "NOT_READY")
GATE_BANDS = ("READY", "CANDIDATE", "VALIDATION", "DEVELOPMENT", "NOT_READY")
# Étape 17 (exécution réelle par paliers) : paliers, verdict.
DEPLOYMENT_STAGES = ("SHADOW", "PAPER", "SIMULATED_LIVE", "CONTROLLED_LIVE", "LIMITED_PRODUCTION", "PRODUCTION")
LIVE_READINESS_STATUSES = ("READY_FOR_STAGED_LIVE_EXECUTION", "NOT_READY")
# Étape 18 (plan de contrôle) : verdict, gravités des incidents.
CONTROL_STATUSES = ("READY_FOR_PRODUCTION_CONTROLLED_OPERATIONS", "NOT_READY")
INCIDENT_SEVERITIES = ("P0", "P1", "P2", "P3", "P4")
# Étape 19 (apprentissage continu) : états des modèles, niveaux de risque, verdict.
MODEL_STATES = ("DRAFT", "TRAINING", "VALIDATING", "PENDING_APPROVAL", "APPROVED", "SHADOW", "CANARY", "ACTIVE",
                "DEGRADED", "SUSPENDED", "RETIRED", "REJECTED", "BLOCKED", "EMERGENCY_DISABLED")
MODEL_RISKS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
LEARNING_STATUSES = ("READY_FOR_CONTROLLED_CONTINUOUS_LEARNING", "NOT_READY")
# Étape 20 (cybersécurité) : verdict.
SECURITY_STATUSES = ("READY_FOR_PRODUCTION_SECURITY", "NOT_READY")
# Étape 11 (moteur de risque) : états, décisions pour les achats, phases, niveaux d'alerte.
RISK_STATES = ("RISK_NORMAL", "RISK_WARNING", "RISK_HIGH", "RISK_CRITICAL", "RISK_BLOCKED")
RISK_APPROVALS = ("RISK_APPROVED", "RISK_APPROVED_WITH_LIMIT", "RISK_RESTRICTED", "RISK_BLOCKED")
RISK_PHASES = ("PRE", "POST")
ALERT_LEVELS = ("INFO", "NOTICE", "WARNING", "HIGH", "CRITICAL", "EMERGENCY")


def _day(name: str, v: Any) -> None:
    if not isinstance(v, str) or not DAY_RE.match(v):
        raise ContractError("INVALID_FIELD", f"{name} : date AAAA-MM-JJ attendue ({v!r})")


def _opt01(name: str, v: Any) -> Optional[float]:
    return None if v is None else _score01(name, v)


def _plain(obj: Any) -> Any:
    """Un contrat en dictionnaire simple (pour JSON), contrats imbriqués compris."""
    if hasattr(obj, "__dataclass_fields__"):
        return {f.name: _plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    return obj


@dataclass(frozen=True)
class Instrument:
    """Un instrument du référentiel (Instrument.v1, §8 de l'étape 7)."""
    instrument_id: str
    symbol: str
    base: str
    quote: str
    exchange: str
    asset_class: str
    currency: str
    timezone: str
    calendar: str
    listing_date: Optional[str]
    status: str
    tick_size: Optional[float] = None
    lot_step: Optional[float] = None
    min_notional: Optional[float] = None

    def __post_init__(self) -> None:
        if not INSTRUMENT_RE.match(self.instrument_id or ""):
            raise ContractError("INVALID_FIELD", f"instrument_id : place:marché:BASE-DEVISE attendu ({self.instrument_id!r})")
        _enum("asset_class", self.asset_class, ASSET_CLASSES)
        _enum("status", self.status, INSTRUMENT_STATUSES)
        if self.listing_date is not None:
            _day("listing_date", self.listing_date)
        for name in ("tick_size", "lot_step", "min_notional"):
            if getattr(self, name) is not None:
                _number(name, getattr(self, name))


@dataclass(frozen=True)
class Feature:
    """Un indicateur du magasin (Feature.v1, §21 de l'étape 7, §42 des
    contrats) : valeur (None : non mesurable, jamais 0), version, fin des
    données qui l'ont produit."""
    instrument_id: str
    day: str
    name: str
    value: Optional[float]
    version: str
    data_cutoff_at: str

    def __post_init__(self) -> None:
        _day("day", self.day)
        if not re.fullmatch(r"[a-z0-9_]+", self.name or ""):
            raise ContractError("INVALID_FIELD", f"name : nom d'indicateur attendu ({self.name!r})")
        if self.value is not None:
            object.__setattr__(self, "value", _number("value", self.value, positive=False))
        if not SEMVER.match(self.version):
            raise ContractError("INVALID_VERSION", f"version MAJEUR.MINEUR.CORRECTIF attendue ({self.version!r})")
        check_timestamp("data_cutoff_at", self.data_cutoff_at)
        end = datetime.fromisoformat(self.day).replace(tzinfo=timezone.utc) + timedelta(days=1)
        if datetime.fromisoformat(self.data_cutoff_at.replace("Z", "+00:00")) > end:
            raise ContractError("LOOK_AHEAD", f"{self.name} du {self.day} : données jusqu'au {self.data_cutoff_at}, "
                                              "après la bougie (regard vers le futur)")


@dataclass(frozen=True)
class Forecast:
    """Prévision de fréquence (Forecast.v1, §29) : probabilités observées
    dans un passé comparable, intervalle, nombre de cas ; jamais un prix."""
    instrument_id: str
    day: str
    horizon_days: int
    p_up: float
    p_target: float
    target: float
    p_drawdown20: float
    expected_return: float
    q10: float
    q90: float
    ci_low: float
    ci_high: float
    cases: int
    version: str

    def __post_init__(self) -> None:
        _day("day", self.day)
        if not isinstance(self.horizon_days, int) or self.horizon_days <= 0:
            raise ContractError("OUT_OF_RANGE", "horizon_days : entier positif attendu")
        for name in ("p_up", "p_target", "p_drawdown20", "ci_low", "ci_high"):
            object.__setattr__(self, name, _score01(name, getattr(self, name)))
        if not self.ci_low <= self.p_up <= self.ci_high:
            raise ContractError("INCONSISTENT", "la probabilité doit être dans son intervalle")
        if self.q10 > self.q90:
            raise ContractError("INCONSISTENT", "intervalle de rendement inversé")
        _count("cases", self.cases)


@dataclass(frozen=True)
class Scenario:
    """Un scénario (Scenario.v1, §28)."""
    name: str
    probability: float
    cases: int
    expected_return: Optional[float]
    expected_volatility: Optional[float]
    expected_drawdown: Optional[float]
    invalidating: str

    def __post_init__(self) -> None:
        _enum("name", self.name, SCENARIO_NAMES)
        object.__setattr__(self, "probability", _score01("probability", self.probability))
        _count("cases", self.cases)


@dataclass(frozen=True)
class ScenarioSet:
    """Les scénarios d'un instrument : leurs probabilités font 1 (contrôlé)."""
    instrument_id: str
    day: str
    horizon_days: int
    scenarios: Tuple[Scenario, ...]
    cases: int
    version: str

    def __post_init__(self) -> None:
        _day("day", self.day)
        if sorted(s.name for s in self.scenarios) != sorted(SCENARIO_NAMES):
            raise ContractError("INCONSISTENT", "chaque scénario une fois : " + ", ".join(SCENARIO_NAMES))
        if abs(sum(s.probability for s in self.scenarios) - 1) > 1e-6:
            raise ContractError("PROBABILITIES", "la somme des probabilités des scénarios doit faire 1")
        if sum(s.cases for s in self.scenarios) != self.cases:
            raise ContractError("INCONSISTENT", "les cas des scénarios doivent faire le total")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class FinancialSignal:
    """Signal financier (FinancialSignal.v1, §32 des contrats, §23 de l'étape
    7) : candidat seulement ; rendement attendu None tant qu'il n'est pas
    mesuré ; un signal n'est jamais une autorisation."""
    signal_id: str
    instrument_id: str
    direction: str
    signal_type: str
    strength: float
    confidence: Optional[Confidence]
    horizon: str
    entry_condition: str
    exit_condition: str
    expected_return: Optional[float]
    expected_risk: Optional[float]
    source_features: Tuple[str, ...]
    model_version: str
    status: str
    day: str

    def __post_init__(self) -> None:
        _enum("direction", self.direction, SIGNAL_DIRECTIONS)
        _enum("signal_type", self.signal_type, SIGNAL_TYPES)
        _enum("horizon", self.horizon, HORIZONS)
        _enum("status", self.status, SIGNAL_STATUSES)
        object.__setattr__(self, "strength", _score01("strength", self.strength))
        object.__setattr__(self, "expected_risk", _opt01("expected_risk", self.expected_risk))
        _day("day", self.day)

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class FinancialAnalysis:
    """Analyse d'une crypto (FinancialAnalysis.v1, §60 et §37 de l'étape 7) :
    recommandation, raisons de ne pas acheter (codes du contrat NO_TRADE),
    classement indicatif, confiances séparées, détails, preuves, risques,
    contradictions, conditions d'invalidation. Jamais une autorisation."""
    instrument_id: str
    day: str
    recommendation: str
    reasons: Tuple[str, ...]
    reason_texts: Tuple[str, ...]
    opportunity: Optional[float]
    confidence: Dict[str, Any]
    details: Dict[str, Any]
    evidence: Tuple[str, ...]
    risks: Tuple[str, ...]
    contradictions: Tuple[str, ...]
    invalidating: Tuple[str, ...]
    authorized: bool = False

    def __post_init__(self) -> None:
        _enum("recommendation", self.recommendation, ANALYSIS_RECOMMENDATIONS)
        for r in self.reasons:
            _enum("reasons", r, NO_TRADE_REASONS)
        hard = [r for r in self.reasons if r != "LOW_CONFIDENCE"]
        if self.recommendation == "NO_TRADE" and not hard:
            raise ContractError("INCONSISTENT", "« pas de trade » dit toujours pourquoi")
        if self.recommendation == "BUY_SIGNAL" and self.reasons:
            raise ContractError("INCONSISTENT", "un signal d'achat ne peut avoir de raison de s'abstenir")
        if len(self.reasons) != len(self.reason_texts):
            raise ContractError("INCONSISTENT", "chaque raison a son explication")
        object.__setattr__(self, "opportunity", _opt01("opportunity", self.opportunity))
        if self.authorized:
            raise ContractError("POLICY", "une analyse n'est jamais une autorisation", category="POLICY")
        _day("day", self.day)

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class StrategyDecision:
    """Décision de la règle pour une crypto (StrategyDecision.v1, étape 9
    §39) : candidate, « pas de trade » avec ses raisons, position tenue ou
    sortie due ; chaque condition avec son résultat et ses valeurs.
    Probabilité et rendement attendu absents : la règle n'en estime pas.
    Jamais une autorisation : la porte d'exécution décide."""
    VERSION: ClassVar[int] = 1
    strategy_id: str
    strategy_version: str
    instrument_id: str
    day: str
    regime: str
    direction: str
    entry_condition: bool
    exit_condition: bool
    conditions: Tuple[Tuple[str, bool, str], ...]
    signal: Optional[float]
    expected_cost: float
    position_size: Optional[float]
    stop_loss: Optional[float]
    status: str
    decision: str
    reasons: Tuple[str, ...] = ()
    reason_texts: Tuple[str, ...] = ()
    authorized: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("regime", self.regime, ("BULL", "BEAR"))
        _enum("direction", self.direction, SIGNAL_DIRECTIONS)
        _enum("status", self.status, STRATEGY_STATES)
        _enum("decision", self.decision, STRATEGY_DECISIONS)
        for r in self.reasons:
            _enum("reasons", r, STRATEGY_REASONS)
        _day("day", self.day)
        if not INSTRUMENT_RE.match(self.instrument_id):
            raise ContractError("INVALID_FIELD", f"instrument_id : {self.instrument_id!r}")
        if self.authorized:
            raise ContractError("POLICY", "une décision de stratégie n'est jamais une autorisation",
                                category="POLICY")
        if len(self.reasons) != len(self.reason_texts):
            raise ContractError("INCONSISTENT", "chaque raison a son explication")
        if self.decision == "TRADE_CANDIDATE" and (self.reasons or not self.entry_condition):
            raise ContractError("INCONSISTENT", "une candidate remplit toutes les conditions")
        if self.decision == "NO_TRADE" and not self.reasons:
            raise ContractError("INCONSISTENT", "« pas de trade » dit toujours pourquoi")
        if (self.decision in ("HOLD", "EXIT")) != (self.status == "IN_POSITION"):
            raise ContractError("INCONSISTENT", "tenir ou vendre concerne une position détenue")
        _number("expected_cost", self.expected_cost, positive=False)
        if self.expected_cost < 0:
            raise ContractError("OUT_OF_RANGE", "expected_cost : un coût n'est jamais négatif")
        object.__setattr__(self, "position_size", _opt01("position_size", self.position_size))

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class BacktestResult:
    """Résultat validé d'un backtest (BacktestResult.v1, étape 10 §54 et
    §77) : verdict, préparation au moteur de risque, empreintes des données,
    de la configuration et du résultat, mesures, note de qualité, réserves,
    raisons de rejet et limites, dont toujours « pas une garantie »."""
    VERSION: ClassVar[int] = 1
    run_id: str
    status: str
    readiness: str
    strategy_id: str
    strategy_version: str
    dataset_hash: str
    config_hash: str
    result_hash: str
    periods: Tuple[Tuple[str, str], ...]
    metrics: Dict[str, Any]
    quality_score: float
    warnings: Tuple[str, ...]
    rejection_reasons: Tuple[str, ...]
    limitations: Tuple[str, ...]
    created_at: str
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("status", self.status, BACKTEST_STATUSES)
        _enum("readiness", self.readiness, BACKTEST_READINESS)
        check_uuid("run_id", self.run_id)
        check_timestamp("created_at", self.created_at)
        object.__setattr__(self, "quality_score", _score01("quality_score", self.quality_score))
        for name in ("dataset_hash", "config_hash", "result_hash"):
            if not getattr(self, name):
                raise ContractError("INVALID_FIELD", f"{name} : empreinte requise (reproductibilité)")
        for a, b in self.periods:
            _day("periods", a)
            _day("periods", b)
        if self.status == "REJECTED" and not self.rejection_reasons:
            raise ContractError("INCONSISTENT", "un rejet dit toujours pourquoi")
        if self.readiness == "READY_FOR_RISK" and (self.status not in ("VALID", "VALID_WITH_WARNINGS")
                                                   or self.rejection_reasons):
            raise ContractError("INCONSISTENT", "seul un backtest valide est prêt pour le moteur de risque")
        if NO_GUARANTEE not in self.limitations:
            raise ContractError("POLICY", "les limites disent toujours qu'un backtest n'est pas une garantie",
                                category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class RiskAssessment:
    """Évaluation du risque du portefeuille (RiskAssessment.v1, étape 11 §47
    et §77) : mesures, composantes et leur niveau, limites et leur usage,
    alertes, note (jamais seule : avec ses composantes), état, décision pour
    les achats, chemin dans la machine à états, fin de validité. Un blocage
    dit pourquoi ; jamais une autorisation."""
    VERSION: ClassVar[int] = 1
    assessment_id: str
    day: str
    phase: str
    mode: str
    state: str
    approval: str
    path: Tuple[str, ...]
    score: float
    confidence: float
    equity: float
    metrics: Dict[str, Any]
    components: Dict[str, str]
    limits: Tuple[Tuple[str, float, str], ...]
    warnings: Tuple[str, ...]
    violations: Tuple[str, ...]
    alerts: Tuple[Tuple[str, str, str], ...]
    required_actions: Tuple[str, ...]
    model_version: str
    limits_version: str
    data_hash: str
    created_at: str
    expires_at: str
    authorized: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("state", self.state, RISK_STATES)
        _enum("approval", self.approval, RISK_APPROVALS)
        _enum("phase", self.phase, RISK_PHASES)
        check_uuid("assessment_id", self.assessment_id)
        _day("day", self.day)
        check_timestamp("created_at", self.created_at)
        check_timestamp("expires_at", self.expires_at)
        if datetime.fromisoformat(self.expires_at) <= datetime.fromisoformat(self.created_at):
            raise ContractError("INCONSISTENT", "une évaluation a une durée de validité")
        object.__setattr__(self, "score", _score01("score", self.score))
        object.__setattr__(self, "confidence", _score01("confidence", self.confidence))
        for name, level in self.components.items():
            _enum(f"components.{name}", level, LEVELS4)
        for level, _code, _text in self.alerts:
            _enum("alerts", level, ALERT_LEVELS)
        if (self.state == "RISK_BLOCKED") != (self.approval == "RISK_BLOCKED"):
            raise ContractError("INCONSISTENT", "état bloqué et décision bloquée vont ensemble")
        if self.approval == "RISK_BLOCKED" and not self.violations:
            raise ContractError("INCONSISTENT", "un blocage dit toujours pourquoi")
        if not self.path or self.path[-1] != self.state:
            raise ContractError("INCONSISTENT", "le chemin de la machine à états finit par l'état")
        if self.authorized:
            raise ContractError("POLICY", "une évaluation du risque n'est jamais une autorisation", category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class QuantResult:
    """Résultat du laboratoire quantitatif (QuantResult.v1, étape 8 §89) :
    état, empreintes des données, du code et du résultat, graine, cryptos
    étudiées, conclusions tirées des chiffres, réserves et limites (dont
    toujours « une mesure du passé n'est pas une promesse »). Jamais un
    signal d'achat ni une autorisation."""
    VERSION: ClassVar[int] = 1
    run_id: str
    status: str
    engine_version: str
    dataset_hash: str
    code_hash: str
    result_hash: str
    seed: int
    assets: Tuple[str, ...]
    findings: Tuple[str, ...]
    warnings: Tuple[str, ...]
    limitations: Tuple[str, ...]
    created_at: str
    authorized: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("status", self.status, QUANT_STATUSES)
        check_uuid("run_id", self.run_id)
        check_timestamp("created_at", self.created_at)
        for name in ("dataset_hash", "code_hash", "result_hash"):
            if not getattr(self, name):
                raise ContractError("INVALID_FIELD", f"{name} : empreinte requise (reproductibilité)")
        if "btc" not in self.assets:
            raise ContractError("INVALID_FIELD", "BTC est requis (facteur de marché, régime)")
        if self.status == "VALIDATED" and self.warnings:
            raise ContractError("INCONSISTENT", "un résultat avec réserves n'est pas « validé » sans réserve")
        if PAST_NOT_PROMISE not in self.limitations:
            raise ContractError("POLICY", "les limites disent toujours qu'une mesure du passé n'est pas une promesse",
                                category="POLICY")
        if self.authorized:
            raise ContractError("POLICY", "une mesure quantitative n'est jamais une autorisation", category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class PortfolioDecision:
    """Décision du moteur de portefeuille (PortfolioDecision.v1, étape 12
    §61) : dans les contraintes (HOLD), à regarder (REVIEW, avec les
    contraintes dépassées) ou aucune position (NO_ALLOCATE) ; exposition,
    liquidités, concentration, risque engagé ; rééquilibrage proposé ou non.
    Consultative : jamais un ordre ni une autorisation."""
    VERSION: ClassVar[int] = 1
    decision_id: str
    day: str
    decision: str
    engine_version: str
    positions: int
    exposure: float
    cash_share: float
    hhi: float
    risk_used: float
    violations: Tuple[str, ...]
    rebalance: str
    created_at: str
    authorized: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("decision", self.decision, PORTFOLIO_DECISIONS)
        _enum("rebalance", self.rebalance, REBALANCE_DECISIONS)
        check_uuid("decision_id", self.decision_id)
        _day("day", self.day)
        check_timestamp("created_at", self.created_at)
        if not isinstance(self.positions, int) or self.positions < 0:
            raise ContractError("OUT_OF_RANGE", "positions : entier positif ou nul")
        for name in ("exposure", "cash_share", "hhi"):
            object.__setattr__(self, name, _score01(name, getattr(self, name)))
        if self.risk_used < 0:
            raise ContractError("OUT_OF_RANGE", "risk_used : jamais négatif")
        if (self.decision == "REVIEW") != bool(self.violations):
            raise ContractError("INCONSISTENT", "« à regarder » dit toujours pourquoi, et seulement alors")
        if (self.decision == "NO_ALLOCATE") != (self.positions == 0):
            raise ContractError("INCONSISTENT", "« aucune position » va avec zéro position")
        if self.authorized:
            raise ContractError("POLICY", "une décision de portefeuille n'est jamais une autorisation",
                                category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class PaperAcceptanceReport:
    """Verdict des critères d'acceptation du paper (PaperAcceptanceReport.v1,
    étape 13 §52-53) : ACCEPTED (prêt pour le moteur de politique) seulement
    sans aucun P0 raté, avec une note d'au moins 95, l'observation suffisante
    et l'écart au backtest compris ; sinon BLOCKED, avec ce qui manque.
    Jamais une autorisation de trader en réel."""
    VERSION: ClassVar[int] = 1
    report_id: str
    created_at: str
    mode: str
    status: str
    next_step: str
    readiness_score: float
    band: str
    p0_failures: Tuple[str, ...]
    passed: int
    failed: int
    unknown: int
    not_applicable: int
    observation_days: float
    observation_events: int
    missing: Tuple[str, ...]
    engine_version: str
    authorized: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("status", self.status, ACCEPTANCE_STATUSES)
        _enum("band", self.band, ACCEPTANCE_BANDS)
        _enum("mode", self.mode, ("paper", "testnet", "live"))
        check_uuid("report_id", self.report_id)
        check_timestamp("created_at", self.created_at)
        score = _number("readiness_score", self.readiness_score, positive=False)
        if not 0 <= score <= 100:
            raise ContractError("OUT_OF_RANGE", "readiness_score : de 0 à 100")
        if self.passed + self.failed + self.unknown + self.not_applicable != 44:
            raise ContractError("INCONSISTENT", "les 44 critères AC-001 à AC-044 sont tous comptés")
        if self.observation_days < 0 or self.observation_events < 0:
            raise ContractError("OUT_OF_RANGE", "observation : jamais négative")
        floor = {"PAPER_READY": 95, "PAPER_READY_CANDIDATE": 90, "VALIDATING": 80, "DEVELOPMENT": 70, "REJECTED": 0}
        upper = {"PAPER_READY": 100.01, "PAPER_READY_CANDIDATE": 95, "VALIDATING": 90, "DEVELOPMENT": 80,
                 "REJECTED": 70}
        if not floor[self.band] <= score < upper[self.band]:
            raise ContractError("INCONSISTENT", "la bande suit la note")
        if self.status == "ACCEPTED":
            if self.p0_failures or self.missing or score < 95 or self.next_step != "POLICY_ENGINE":
                raise ContractError("INCONSISTENT", "accepté : aucun P0 raté, note ≥ 95, rien ne manque, étape "
                                                    "suivante la politique")
        elif not self.missing or self.next_step != "CORRECTION":
            raise ContractError("INCONSISTENT", "bloqué : dit toujours ce qui manque ; étape suivante la correction")
        if self.authorized:
            raise ContractError("POLICY", "le paper valide la préparation, jamais le réel", category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class PolicyDecision:
    """Décision du moteur de politiques pour une intention d'achat
    (PolicyDecision.v1, étape 14 §35-37) : l'action la plus grave des
    politiques qui ne tiennent pas, les politiques évaluées (avec leur
    version), les violations et avertissements expliqués, une durée de
    validité. Jamais une autorisation : la porte d'exécution décide."""
    VERSION: ClassVar[int] = 1
    decision_id: str
    asset: str
    decision: str
    registry_version: str
    evaluated: Tuple[str, ...]
    violations: Tuple[str, ...]
    warnings: Tuple[str, ...]
    created_at: str
    expires_at: str
    authorized: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("decision", self.decision, POLICY_ACTIONS)
        check_uuid("decision_id", self.decision_id)
        check_timestamp("created_at", self.created_at)
        check_timestamp("expires_at", self.expires_at)
        if datetime.fromisoformat(self.expires_at) <= datetime.fromisoformat(self.created_at):
            raise ContractError("INCONSISTENT", "une décision de politique expire")
        if not self.evaluated:
            raise ContractError("INCONSISTENT", "une décision dit quelles politiques ont été évaluées")
        blocking = POLICY_ACTIONS.index(self.decision) >= POLICY_ACTIONS.index("NO_TRADE")
        if blocking and not self.violations:
            raise ContractError("INCONSISTENT", "un refus dit toujours quelle politique ne tient pas")
        if self.decision == "ALLOW" and (self.violations or self.warnings):
            raise ContractError("INCONSISTENT", "« permis » sans réserve : ni violation ni avertissement")
        if self.decision in ("ALLOW_WITH_LIMITS", "REDUCE_SIZE") and not self.warnings:
            raise ContractError("INCONSISTENT", "une limite dit laquelle")
        if self.authorized:
            raise ContractError("POLICY", "une décision de politique n'est jamais une autorisation", category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class AuthorizationDecision:
    """Décision du moteur d'autorisation (AuthorizationDecision.v1, étape 15
    §10, §37) : qui, quoi, sur quelle ressource, permis ou refusé, raisons et
    conditions vérifiées, version de la matrice, fin de validité. Une IA, un
    agent ou une identité extérieure n'obtient jamais un droit critique."""
    VERSION: ClassVar[int] = 1
    decision_id: str
    principal: str
    principal_type: str
    action: str
    resource: str
    allowed: bool
    reasons: Tuple[str, ...]
    conditions: Tuple[Tuple[str, bool], ...]
    policy_version: str
    created_at: str
    expires_at: str
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        check_uuid("decision_id", self.decision_id)
        check_timestamp("created_at", self.created_at)
        check_timestamp("expires_at", self.expires_at)
        if datetime.fromisoformat(self.expires_at) <= datetime.fromisoformat(self.created_at):
            raise ContractError("INCONSISTENT", "une décision d'autorisation expire")
        if not self.reasons:
            raise ContractError("INCONSISTENT", "une décision dit toujours pourquoi")
        if self.allowed and not all(bool(ok) for _c, ok in self.conditions):
            raise ContractError("INCONSISTENT", "permis seulement si chaque condition est remplie")
        critical = ("AUTHORIZE_BUY", "TRADE_LIVE", "CHANGE_RISK", "ARM_LIVE", "SAFE_MODE_OFF", "KILL_RESET",
                    "SET_SECRETS", "MERGE_CODE")
        if self.allowed and self.action in critical and self.principal_type in ("AGENT", "AI_MODEL", "EXTERNAL"):
            raise ContractError("POLICY", "une IA, un agent ou une identité extérieure n'a jamais de droit critique",
                                category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class FinalValidationResult:
    """Validation finale d'un achat, juste avant l'ordre (FinalValidationResult.v1,
    étape 16 §21, §42) : autorisation encore valable, liée à son contrôle et
    non consommée ; arrêt d'urgence et mode sûr toujours levés ; état
    critique inchangé depuis le contrôle, sinon nouveau contrôle. PASS
    seulement si chaque vérification tient."""
    VERSION: ClassVar[int] = 1
    validation_id: str
    risk_check_id: str
    authorization_id: str
    status: str
    checks: Tuple[Tuple[str, bool, str], ...]
    snapshot_before: str
    snapshot_after: str
    revalidated: bool
    reasons: Tuple[str, ...]
    created_at: str
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("status", self.status, FINAL_VALIDATION_STATUSES)
        check_uuid("validation_id", self.validation_id)
        check_timestamp("created_at", self.created_at)
        if not self.checks:
            raise ContractError("INCONSISTENT", "une validation finale vérifie toujours quelque chose")
        if (self.status == "PASS") != all(bool(ok) for _n, ok, _d in self.checks):
            raise ContractError("INCONSISTENT", "PASS seulement si chaque vérification tient")
        if self.status == "PASS" and not self.authorization_id:
            raise ContractError("POLICY", "aucun ordre sans autorisation", category="POLICY")
        if self.status == "BLOCK" and not self.reasons:
            raise ContractError("INCONSISTENT", "un blocage dit toujours pourquoi")
        if self.revalidated != (self.snapshot_before != self.snapshot_after):
            raise ContractError("INCONSISTENT", "un état changé depuis le contrôle est toujours contrôlé de nouveau")

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class GateReadinessReport:
    """Examen de la porte d'exécution (GateReadinessReport.v1, étape 16
    §68-69, §73) : critères AC-001 à AC-050, note pondérée, essai de chaos.
    READY_FOR_CONTROLLED_LIVE_EXECUTION seulement sans P0 raté, avec une
    note d'au moins 95 et aucun ordre non autorisé au chaos. Jamais une
    autorisation de trader en réel (la porte du réel en décide)."""
    VERSION: ClassVar[int] = 1
    report_id: str
    created_at: str
    mode: str
    status: str
    readiness_score: float
    band: str
    p0_failures: Tuple[str, ...]
    passed: int
    failed: int
    unknown: int
    not_applicable: int
    chaos_orders: int
    chaos_unauthorized: int
    engine_version: str
    authorized: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("status", self.status, GATE_READINESS_STATUSES)
        _enum("band", self.band, GATE_BANDS)
        _enum("mode", self.mode, ("paper", "testnet", "live"))
        check_uuid("report_id", self.report_id)
        check_timestamp("created_at", self.created_at)
        score = _number("readiness_score", self.readiness_score, positive=False)
        if not 0 <= score <= 100:
            raise ContractError("OUT_OF_RANGE", "readiness_score : de 0 à 100")
        if self.passed + self.failed + self.unknown + self.not_applicable != 50:
            raise ContractError("INCONSISTENT", "les 50 critères AC-001 à AC-050 sont tous comptés")
        if self.chaos_orders < 0 or not 0 <= self.chaos_unauthorized <= self.chaos_orders:
            raise ContractError("OUT_OF_RANGE", "chaos : des nombres d'ordres cohérents")
        floor = {"READY": 95, "CANDIDATE": 90, "VALIDATION": 80, "DEVELOPMENT": 70, "NOT_READY": 0}
        upper = {"READY": 100.01, "CANDIDATE": 95, "VALIDATION": 90, "DEVELOPMENT": 80, "NOT_READY": 70}
        if not floor[self.band] <= score < upper[self.band]:
            raise ContractError("INCONSISTENT", "la bande suit la note")
        if self.status == "READY_FOR_CONTROLLED_LIVE_EXECUTION" and (
                self.p0_failures or score < 95 or self.chaos_orders <= 0 or self.chaos_unauthorized):
            raise ContractError("INCONSISTENT", "prête : aucun P0 raté, note ≥ 95, chaos passé sans ordre non autorisé")
        if self.authorized:
            raise ContractError("POLICY", "l'examen de la porte n'autorise jamais le réel", category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class LiveDeploymentReport:
    """Exécution réelle par paliers (LiveDeploymentReport.v1, étape 17
    §33-35, §53-57, §62) : palier en vigueur, palier suivant et sa porte,
    examen AC-001 à AC-060. READY_FOR_STAGED_LIVE_EXECUTION seulement sans
    P0 raté et avec une note d'au moins 95 ; jamais une promotion
    automatique, jamais une production sans restriction."""
    VERSION: ClassVar[int] = 1
    report_id: str
    created_at: str
    mode: str
    stage: str
    next_stage: str
    next_gate_open: bool
    missing: Tuple[str, ...]
    status: str
    readiness_score: float
    band: str
    p0_failures: Tuple[str, ...]
    passed: int
    failed: int
    unknown: int
    not_applicable: int
    engine_version: str
    auto_promoted: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("stage", self.stage, DEPLOYMENT_STAGES)
        _enum("status", self.status, LIVE_READINESS_STATUSES)
        _enum("band", self.band, GATE_BANDS)
        _enum("mode", self.mode, ("paper", "live"))
        check_uuid("report_id", self.report_id)
        check_timestamp("created_at", self.created_at)
        i = DEPLOYMENT_STAGES.index(self.stage)
        expected = DEPLOYMENT_STAGES[i + 1] if i + 1 < len(DEPLOYMENT_STAGES) else ""
        if self.next_stage != expected:
            raise ContractError("INCONSISTENT", "le palier suivant est toujours le palier d'après, un à la fois")
        if (self.mode == "paper") != (self.stage in ("SHADOW", "PAPER")):
            raise ContractError("INCONSISTENT", "un palier du réel suppose le mode réel, et l'inverse")
        if self.next_gate_open == bool(self.missing) or (self.next_gate_open and not self.next_stage):
            raise ContractError("INCONSISTENT", "une porte fermée dit ce qui manque ; une porte ouverte, rien")
        score = _number("readiness_score", self.readiness_score, positive=False)
        if not 0 <= score <= 100:
            raise ContractError("OUT_OF_RANGE", "readiness_score : de 0 à 100")
        if self.passed + self.failed + self.unknown + self.not_applicable != 60:
            raise ContractError("INCONSISTENT", "les 60 critères AC-001 à AC-060 sont tous comptés")
        floor = {"READY": 95, "CANDIDATE": 90, "VALIDATION": 80, "DEVELOPMENT": 70, "NOT_READY": 0}
        upper = {"READY": 100.01, "CANDIDATE": 95, "VALIDATION": 90, "DEVELOPMENT": 80, "NOT_READY": 70}
        if not floor[self.band] <= score < upper[self.band]:
            raise ContractError("INCONSISTENT", "la bande suit la note")
        if self.status == "READY_FOR_STAGED_LIVE_EXECUTION" and (self.p0_failures or score < 95):
            raise ContractError("INCONSISTENT", "prête : aucun P0 raté et une note d'au moins 95")
        if self.auto_promoted:
            raise ContractError("POLICY", "aucune promotion automatique : seul votre réglage fait monter d'un palier",
                                category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class ControlPlaneReport:
    """Plan de contrôle de la production (ControlPlaneReport.v1, étape 18
    §38, §45-47, §52) : services, incidents ouverts, examen AC-001 à AC-060.
    READY_FOR_PRODUCTION_CONTROLLED_OPERATIONS seulement sans P0 raté, avec
    une note d'au moins 95 et aucun incident P0 ouvert ; jamais une
    exploitation autonome sans limite."""
    VERSION: ClassVar[int] = 1
    report_id: str
    created_at: str
    mode: str
    status: str
    readiness_score: float
    band: str
    p0_failures: Tuple[str, ...]
    passed: int
    failed: int
    unknown: int
    not_applicable: int
    services: int
    services_down: int
    incidents: Tuple[Tuple[str, str], ...]
    engine_version: str
    unrestricted_autonomy: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("status", self.status, CONTROL_STATUSES)
        _enum("band", self.band, GATE_BANDS)
        _enum("mode", self.mode, ("paper", "live"))
        check_uuid("report_id", self.report_id)
        check_timestamp("created_at", self.created_at)
        for _iid, sev in self.incidents:
            _enum("severity", sev, INCIDENT_SEVERITIES)
        if not 0 <= self.services_down <= self.services or self.services <= 0:
            raise ContractError("OUT_OF_RANGE", "services : des nombres cohérents")
        score = _number("readiness_score", self.readiness_score, positive=False)
        if not 0 <= score <= 100:
            raise ContractError("OUT_OF_RANGE", "readiness_score : de 0 à 100")
        if self.passed + self.failed + self.unknown + self.not_applicable != 60:
            raise ContractError("INCONSISTENT", "les 60 critères AC-001 à AC-060 sont tous comptés")
        floor = {"READY": 95, "CANDIDATE": 90, "VALIDATION": 80, "DEVELOPMENT": 70, "NOT_READY": 0}
        upper = {"READY": 100.01, "CANDIDATE": 95, "VALIDATION": 90, "DEVELOPMENT": 80, "NOT_READY": 70}
        if not floor[self.band] <= score < upper[self.band]:
            raise ContractError("INCONSISTENT", "la bande suit la note")
        if self.status != "NOT_READY" and (self.p0_failures or score < 95
                                           or any(sev == "P0" for _i, sev in self.incidents)):
            raise ContractError("INCONSISTENT", "prêt : aucun P0 raté, note ≥ 95, aucun incident P0 ouvert")
        if self.unrestricted_autonomy:
            raise ContractError("POLICY", "aucune exploitation autonome sans limite", category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class ModelCard:
    """Fiche d'un modèle (ModelCard.v1, étape 19 §13-15) : à quoi il sert, ce
    qu'il ne peut pas faire, ses données, sa validation, ses limites, son
    plan de retour, son niveau de risque et son état. Un modèle CRITICAL a
    un propriétaire humain et un plan de retour ; un modèle qui n'achète pas
    ne peut pas être déclaré capable d'acheter."""
    VERSION: ClassVar[int] = 1
    model_id: str
    name: str
    kind: str
    purpose: str
    forbidden_use: str
    data: str
    validation: str
    limitations: str
    rollback: str
    risk: str
    state: str
    can_trade: bool
    owner: str
    model_version: str
    fingerprint: str
    created_at: str
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("risk", self.risk, MODEL_RISKS)
        _enum("state", self.state, MODEL_STATES)
        check_timestamp("created_at", self.created_at)
        for name in ("model_id", "name", "purpose", "forbidden_use", "data", "validation", "limitations",
                     "model_version", "fingerprint"):
            if not str(getattr(self, name)).strip():
                raise ContractError("MISSING_FIELD", f"fiche incomplète : {name}")
        if self.risk == "CRITICAL" and (self.owner != "vous" or not self.rollback.strip()):
            raise ContractError("POLICY", "un modèle critique a un propriétaire humain et un plan de retour",
                                category="POLICY")
        if self.can_trade and self.model_id != "regle":
            raise ContractError("POLICY", "seule la règle décide d'un achat ; les autres modèles conseillent ou "
                                          "réduisent", category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class LearningGovernanceReport:
    """Apprentissage continu et gouvernance des modèles
    (LearningGovernanceReport.v1, étape 19 §66-67, §70) : registre, frontières
    de l'apprentissage, examen AC-001 à AC-070. Prêt seulement sans P0 raté,
    avec une note d'au moins 95 et des frontières tenues ; jamais une
    auto-amélioration sans contrôle."""
    VERSION: ClassVar[int] = 1
    report_id: str
    created_at: str
    mode: str
    status: str
    readiness_score: float
    band: str
    p0_failures: Tuple[str, ...]
    passed: int
    failed: int
    unknown: int
    not_applicable: int
    models: int
    critical_models: int
    boundaries_ok: bool
    engine_version: str
    self_authorized: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("status", self.status, LEARNING_STATUSES)
        _enum("band", self.band, GATE_BANDS)
        _enum("mode", self.mode, ("paper", "live"))
        check_uuid("report_id", self.report_id)
        check_timestamp("created_at", self.created_at)
        if not 0 <= self.critical_models <= self.models or self.models <= 0:
            raise ContractError("OUT_OF_RANGE", "modèles : des nombres cohérents")
        score = _number("readiness_score", self.readiness_score, positive=False)
        if not 0 <= score <= 100:
            raise ContractError("OUT_OF_RANGE", "readiness_score : de 0 à 100")
        if self.passed + self.failed + self.unknown + self.not_applicable != 70:
            raise ContractError("INCONSISTENT", "les 70 critères AC-001 à AC-070 sont tous comptés")
        floor = {"READY": 95, "CANDIDATE": 90, "VALIDATION": 80, "DEVELOPMENT": 70, "NOT_READY": 0}
        upper = {"READY": 100.01, "CANDIDATE": 95, "VALIDATION": 90, "DEVELOPMENT": 80, "NOT_READY": 70}
        if not floor[self.band] <= score < upper[self.band]:
            raise ContractError("INCONSISTENT", "la bande suit la note")
        if self.status != "NOT_READY" and (self.p0_failures or score < 95 or not self.boundaries_ok):
            raise ContractError("INCONSISTENT", "prêt : aucun P0 raté, note ≥ 95, frontières tenues")
        if self.self_authorized:
            raise ContractError("POLICY", "l'intelligence n'est jamais son propre mécanisme d'autorisation",
                                category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True)
class SecurityPostureReport:
    """Cybersécurité et autodéfense (SecurityPostureReport.v1, étape 20 §44-45,
    §49) : actifs, sorties hors liste blanche, événements de sécurité, examen
    AC-001 à AC-070. Prêt seulement sans P0 raté, avec une note d'au moins
    95, aucune adresse hors liste blanche et aucun événement P0 ; jamais
    offensif."""
    VERSION: ClassVar[int] = 1
    report_id: str
    created_at: str
    mode: str
    status: str
    readiness_score: float
    band: str
    p0_failures: Tuple[str, ...]
    passed: int
    failed: int
    unknown: int
    not_applicable: int
    assets: int
    unknown_hosts: Tuple[str, ...]
    events: Tuple[Tuple[str, str], ...]
    engine_version: str
    offensive: bool = False
    version: int = 1

    def __post_init__(self) -> None:
        _version(self, self.VERSION)
        _enum("status", self.status, SECURITY_STATUSES)
        _enum("band", self.band, GATE_BANDS)
        _enum("mode", self.mode, ("paper", "live"))
        check_uuid("report_id", self.report_id)
        check_timestamp("created_at", self.created_at)
        for _w, sev in self.events:
            _enum("severity", sev, INCIDENT_SEVERITIES)
        if self.assets <= 0:
            raise ContractError("OUT_OF_RANGE", "un inventaire compte au moins un actif")
        score = _number("readiness_score", self.readiness_score, positive=False)
        if not 0 <= score <= 100:
            raise ContractError("OUT_OF_RANGE", "readiness_score : de 0 à 100")
        if self.passed + self.failed + self.unknown + self.not_applicable != 70:
            raise ContractError("INCONSISTENT", "les 70 critères AC-001 à AC-070 sont tous comptés")
        floor = {"READY": 95, "CANDIDATE": 90, "VALIDATION": 80, "DEVELOPMENT": 70, "NOT_READY": 0}
        upper = {"READY": 100.01, "CANDIDATE": 95, "VALIDATION": 90, "DEVELOPMENT": 80, "NOT_READY": 70}
        if not floor[self.band] <= score < upper[self.band]:
            raise ContractError("INCONSISTENT", "la bande suit la note")
        if self.status != "NOT_READY" and (self.p0_failures or score < 95 or self.unknown_hosts
                                           or any(sev == "P0" for _w, sev in self.events)):
            raise ContractError("INCONSISTENT", "prête : aucun P0 raté, note ≥ 95, aucune sortie hors liste "
                                                "blanche, aucun événement P0")
        if self.offensive:
            raise ContractError("POLICY", "jamais offensif : observer, recommander, réduire", category="POLICY")

    def as_dict(self) -> Dict[str, Any]:
        return _plain(self)


def ohlcv_violations(rows: Any) -> int:
    """Bougies incohérentes (§41) : plus haut sous l'ouverture, la clôture
    ou le plus bas ; plus bas au-dessus ; volume négatif. Une bougie
    incomplète (valeur absente) n'est pas comptée ici : elle est « inconnue »,
    pas « fausse » (la qualité des données la compte à part)."""
    o, h, low, c, v = (rows[k].astype(float) for k in ("open", "high", "low", "close", "volume"))
    known = o.notna() & h.notna() & low.notna() & c.notna()
    bad = known & ((h < o) | (h < c) | (h < low) | (low > o) | (low > c))
    return int(bad.sum() + (v < 0).sum())


# Versions comprises par les consommateurs (§55) et migrations (§56) :
# une évolution incompatible fournit sa migration, jamais en silence.
SUPPORTED = {"ErrorEnvelope": ("1.0.0", "2.0.0"), "Envelope": ("1.0.0",), "Confidence": ("1.0.0",),
             "LLMExecution": ("1.0.0",), "ModelConsensus": ("1.0.0",), "AuditEvent": ("1.0.0", "2.0.0")}


def migrate_error_v1(d: Dict[str, Any]) -> Dict[str, Any]:
    """ErrorEnvelope v1 (étape 2) → v2 (spécification des contrats) :
    error_code → code, timestamp → occurred_at, catégorie et gravité au
    format commun."""
    cat = str(d.get("category", "")).replace("_ERROR", "")
    return {"schema_name": "ErrorEnvelope", "schema_version": "2.0.0", "code": d["error_code"],
            "category": cat if cat in ERROR_CATEGORIES else "SYSTEM", "message": d.get("message", ""),
            "retryable": bool(d.get("retryable")), "severity": str(d.get("severity", "error")).upper(),
            "source": d.get("source", ""), "details": {}, "occurred_at": d.get("timestamp"),
            "correlation_id": d.get("correlation_id") or None}


def upgrade(schema: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Une donnée d'une version comprise, ramenée à la version courante ;
    une version inconnue est refusée (ContractError)."""
    version = str(data.get("schema_version") or "1.0.0")
    if version not in SUPPORTED.get(schema, ()):
        raise ContractError("UNSUPPORTED_VERSION", f"{schema} {version} : versions comprises "
                                                   f"{', '.join(SUPPORTED.get(schema, ())) or 'aucune'}")
    if schema == "ErrorEnvelope" and version == "1.0.0":
        return migrate_error_v1(data)
    return data


SCHEMAS: Dict[str, Any] = {}          # nom → classe, rempli plus bas


def validate(schema: str, data: Dict[str, Any]) -> ValidationResult:
    """Validateur de contrats (§64) : schéma connu, champs requis présents,
    aucun champ inconnu, types, valeurs permises et bornes vérifiés par le
    contrat lui-même. Rien n'est corrigé : une erreur est dite."""
    cls = SCHEMAS.get(schema)
    if cls is None:
        return ValidationResult(False, (f"schéma inconnu : {schema}",), schema_name=schema)
    if not isinstance(data, dict):
        return ValidationResult(False, ("objet attendu",), schema_name=schema)
    known = {f.name: f for f in fields(cls)}
    errors = [f"champ inconnu : {k}" for k in data if k not in known]
    errors += [f"champ requis absent : {n}" for n, f in known.items()
               if n not in data and f.default is MISSING and f.default_factory is MISSING]
    if not errors:
        try:
            cls(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in data.items()})
        except ContractError as e:
            errors.append(f"{e.code} : {e}")
        except TypeError as e:
            errors.append(f"WRONG_TYPE : {e}")
    return ValidationResult(not errors, tuple(errors), schema_name=schema)


SCHEMAS.update({c.__name__: c for c in (OrderIntent, RiskDecision, ExecutionAuthorization, SafeModeState,
                                        Confidence, Uncertainty, Provenance, Money, Envelope, LLMExecution,
                                        ModelConsensus, ModelDisagreement, Instrument, Feature, Forecast,
                                        Scenario, FinancialSignal, StrategyDecision, BacktestResult,
                                        RiskAssessment, QuantResult, PortfolioDecision,
                                        PaperAcceptanceReport, PolicyDecision, AuthorizationDecision,
                                        FinalValidationResult, GateReadinessReport, LiveDeploymentReport,
                                        ControlPlaneReport, ModelCard, LearningGovernanceReport,
                                        SecurityPostureReport)})


# ══════════════════════════════════════════════════════════════════════
# Registre des contrats (la référence ; docs/CONTRATS.md en est tiré)
# ══════════════════════════════════════════════════════════════════════

LEVELS = {1: "critique", 2: "cœur", 3: "données", 4: "support"}


@dataclass(frozen=True)
class Contract:
    contract_id: str
    purpose: str
    producer: str
    consumer: str
    inputs: str
    outputs: str
    errors: str
    permissions: str
    timeout: str
    retry: str
    idempotency: str
    audit: str
    level: int
    files: Tuple[str, ...] = field(default_factory=tuple)
    version: str = "1.0.0"
    classification: str = "INTERNAL"
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        if not SEMVER.match(self.version):
            raise ContractError("INVALID_VERSION", f"{self.contract_id} : version MAJEUR.MINEUR.CORRECTIF attendue")
        _enum("classification", self.classification, CLASSIFICATIONS)
        _enum("status", self.status, ("ACTIVE", "DEPRECATED"))


REGISTRY: Tuple[Contract, ...] = (
    Contract("OrderIntent.v1", "intention d'achat tirée du plan du jour", "exécution (bot_execution.py)",
             "porte d'exécution (porte.py)", "crypto, quantité, prix d'entrée, stop, coût, risque, jour de la décision",
             "intention validée, clé d'unicité jour:crypto:BUY",
             "MISSING_FIELD, WRONG_TYPE, OUT_OF_RANGE, INVALID_FIELD, WRONG_VERSION", "bot seulement",
             "immédiat", "aucun (l'achat différé repasse par la porte à chaque essai)",
             "un achat par crypto et par décision", "contrôle de la porte", 1,
             ("trendguard/contrats.py", "trendguard/bot_execution.py")),
    Contract("RiskCheck.v1", "contrôle déterministe du risque avant tout achat", "exécution",
             "porte d'exécution", "OrderIntent, portefeuille du moment, réglages de risque, garde, mode sûr, "
             "évaluation du jour du moteur de risque",
             "RiskDecision : APPROVED, REJECTED ou EMERGENCY_BLOCK, chaque contrôle et sa raison",
             "contrat refusé = achat refusé", "règles fixes, aucune IA", "immédiat", "aucun",
             "même entrée, même décision", "chaque décision, approuvée ou refusée", 1, ("trendguard/porte.py",)),
    Contract("ExecutionAuthorization.v1", "autorisation d'envoyer l'ordre, liée au contrôle du risque",
             "porte d'exécution", "exécution (paper) ou moteur d'ordres v29 (réel)", "RiskDecision, mode, réel armé",
             "autorisée ou non, identifiant, version de la politique, expiration (5 min), restrictions",
             "réel non armé, contrôle refusé", "réel : ENABLE_LIVE_TRADING et LIVE_TRADING_CONFIRMATION",
             "5 minutes", "aucun", "liée à un seul contrôle", "identifiant gardé avec l'achat", 1,
             ("trendguard/porte.py",)),
    Contract("Order.v1", "ordre d'achat au marché et stop de secours posé chez Binance", "moteur v29 (réel)",
             "Binance Spot", "quantité, identifiant client unique, stop de secours",
             "exécution (prix, quantité, frais) ou ordre ambigu", "fonds insuffisants, ordre refusé, ambigu",
             "clé API : lecture et trading Spot, retrait interdit", "délai de ccxt",
             "jamais à l'aveugle : un ordre ambigu arrête la paire jusqu'au rapprochement",
             "intention écrite avant l'ordre, résolue par l'identifiant client au démarrage", "achat et vente", 1,
             ("v29/execution.py", "v29/exchange.py")),
    Contract("TradeRecord.v1", "trade clos relié à sa décision et à son autorisation", "exécution",
             "journal des trades, attribution, apprentissage", "achat, vente, raison de la sortie",
             "résultat, R, meilleur et pire moment, glissement, régime, leçon, identifiants",
             "trade sans clôtures : excursions inconnues", "bot seulement", "immédiat", "aucun",
             "une vente ferme un seul trade", "vente", 1, ("trendguard/postmortem.py", "trendguard/bot.py")),
    Contract("AuditEvent.v2", "trace infalsifiable de chaque opération critique", "bot (un seul écrivain)",
             "rapport quotidien, commande audit, diagnostic expert",
             "acteur, action et type d'événement (Domaine.Entité.Action), objet, avant, après (montants en "
             "décimal avec devise), raison, autorisation, résultat, corrélation (la décision) et cause",
             "ligne chaînée à la précédente par son empreinte ; les lignes v1 restent lisibles et vérifiées",
             "journal illisible : achats refusés",
             "écriture par le bot seul ; aucun module ne modifie ni n'efface", "immédiat", "aucun",
             "numéro unique et croissant", "c'est l'audit", 1, ("trendguard/audit.py",), version="2.0.0",
             classification="CONFIDENTIAL"),
    Contract("SafeModeState.v1", "mode sûr : plus aucun achat, protection et ventes maintenues",
             "vous (commande mode-sur)", "porte d'exécution, décision du jour",
             "actif ou non, raison, date, qui", "achats refusés (EMERGENCY_BLOCK) tant qu'il est actif",
             "fichier illisible : mode sûr actif", "vous seul", "immédiat", "aucun", "état unique",
             "activation et levée", 1, ("trendguard/porte.py",)),
    Contract("KillSwitch.v1", "arrêt d'urgence : baisse de 40 % depuis le plus haut", "décision du jour",
             "porte d'exécution, panneau, rapport", "capital, plus haut, réglage TG_KILL_DRAWDOWN",
             "plus aucun achat ; reprise prudente après 60 jours de marché haussier, ou commande resume", "aucune",
             "bot ; levée manuelle bot arrêté", "à la décision", "aucun", "état unique",
             "déclenchement et reprise", 1, ("trendguard/bot.py",)),
    Contract("NoTradeGate.v1", "garde « NO TRADE » du jour", "décision du jour", "porte d'exécution, raisonnement",
             "données du jour, mouvement de BTC, perte du jour, place sur le disque",
             "liste des raisons de ne pas acheter aujourd'hui", "mesure ratée : contrôle non bloquant",
             "règles fixes", "à la décision", "aucun", "une garde par jour", "dans l'état du bot", 1,
             ("trendguard/garde.py",)),
    Contract("MarketData.v1", "bougies journalières clôturées de Binance", "Binance (klines publiques)",
             "décision du jour, régimes, qualité", "21 paires, bougies jusqu'à la dernière clôture",
             "clôtures et volumes, bougies en cours exclues ; bougies incohérentes (OHLCV.v1) : crypto écartée "
             "du jour", "réseau : décision reportée, BTC manquant ou incohérent : reportée",
             "lecture publique", "3 essais par paire", "3 essais, attente croissante", "lecture seule",
             "note de qualité sur 100", 3, ("trendguard/bot.py", "trendguard/qualite.py")),
    Contract("Signal.v1", "cassure du plus haut de 30 jours, momentum 90 jours", "stratégie (trend_strategy.py)",
             "plan d'achat", "clôtures, volatilité, régime de BTC", "cryptos à acheter, classées",
             "données insuffisantes : pas de signal", "règles fixes", "immédiat", "aucun",
             "même bougie, même signal", "raisonnement du jour", 2, ("trendguard/trend_strategy.py",)),
    Contract("EntryPlan.v1", "taille de chaque achat (1 % de risque) sous les plafonds", "stratégie",
             "exécution", "signaux, portefeuille, capital, argent disponible, réglages",
             "quantité, prix, stop, risque, coût par crypto", "taille sous 10 USDT : pas d'achat",
             "règles fixes", "immédiat", "aucun", "une décision par jour", "raisonnement du jour", 2,
             ("trendguard/trend_strategy.py",)),
    Contract("AIOpinion.v1", "avis des IA sur le marché (veille)", "IA consultées (market_watch.py)",
             "veille, noyau de savoir", "actualités du jour", "avis de −1 à +1 par crypto",
             "IA en panne ou clé refusée : ignorée", "clés saisies masquées ; une IA ne passe jamais d'ordre",
             "60 s par IA", "aucun", "un avis par jour", "rapport de la veille", 2,
             ("trendguard/market_watch.py",)),
    Contract("KnowledgeHold.v1", "achat reporté par le noyau de savoir", "noyau de savoir (savoir.py)",
             "décision du jour", "avis des sources PROUVÉES sur les cours réels", "crypto à ne pas acheter aujourd'hui",
             "noyau illisible : aucun report", "peut seulement reporter un achat, jamais vendre ni acheter",
             "à la décision", "aucun", "un report par crypto et par jour", "reports vérifiés après 7 jours", 2,
             ("trendguard/savoir.py",)),
    Contract("EvolutionChange.v1", "réglage adopté par l'évolution encadrée", "évolution (evolution.py)",
             "bot, à la décision suivante", "épreuves sur 8 ans de cours, réglages permis par le niveau",
             "réglage à l'essai 30 jours, confirmé ou annulé", "épreuve ratée : rien ne change",
             "jamais le risque cumulé, le nombre de positions, l'arrêt d'urgence ni le mode réel",
             "une fois par jour", "aucun", "un seul changement à l'essai", "registre des expériences", 2,
             ("trendguard/evolution.py", "trendguard/registre.py")),
    Contract("PanelCommand.v1", "commandes du panneau : marche, arrêt, sélection, démarrage", "vous (panneau)",
             "contrôle du bot", "en-tête du panneau, origine, mot de passe", "fait ou refusé, message",
             "origine inconnue, mot de passe raté (blocage après 5 échecs)", "ce PC seulement",
             "immédiat", "aucun", "état cible (marche ou arrêt)", "journal du bot", 4,
             ("panel/server.py", "panel/control.py")),
    Contract("OHLCV.v1", "règles d'une bougie : plus haut ≥ ouverture, clôture et plus bas ; plus bas ≤ "
             "ouverture et clôture ; volume ≥ 0", "Binance (klines publiques)", "décision du jour, qualité des données",
             "bougies des 90 derniers jours de chaque crypto", "nombre de bougies incohérentes",
             "une bougie incohérente : la crypto est écartée du jour (BTC : décision reportée)", "lecture seule",
             "à la décision", "à la décision suivante", "mêmes bougies, même verdict", "journal du bot, qualité", 3,
             ("trendguard/contrats.py", "trendguard/bot.py", "trendguard/qualite.py"), classification="PUBLIC"),
    Contract("DecisionRecord.v1", "décision du jour dans le journal financier, avec la date limite de ses "
             "données (data_cutoff_at)", "décision du jour", "journal financier, lignée des trades",
             "jour, mode, réglages, régime, capital, gardes, qualité, fin des données utilisées",
             "identifiant D-jour ; contrainte : les données s'arrêtent avant la décision (pas de regard vers le futur)",
             "données postérieures à la décision : refusées par la base", "bot seulement", "immédiat", "aucun",
             "une décision par jour", "journal financier", 3, ("trendguard/donnees.py", "trendguard/bot.py"),
             classification="CONFIDENTIAL"),
    Contract("Envelope.v1", "enveloppe commune d'un message entre modules", "tout module (contrats.py)",
             "tout module", "type, schéma et version, producteur, contenu, classification, provenance",
             "identifiant, corrélation, cause, trace, horodatage UTC à la milliseconde",
             "UUID, version, date ou classification invalides : refusé", "selon la classification", "immédiat",
             "aucun", "identifiant unique du message", "selon le contenu", 4, ("trendguard/contrats.py",)),
    Contract("ErrorEnvelope.v2", "erreur au format commun (code, catégorie, gravité, nouvel essai permis)",
             "tout module", "journal d'audit, rapport", "erreur d'un contrat",
             "code, catégorie, message, nouvel essai permis, gravité, source, détails, date, corrélation ; "
             "une erreur v1 est migrée", "catégorie ou gravité inconnues : refusé", "aucun", "immédiat",
             "selon « retryable »", "aucune", "selon l'erreur", 4, ("trendguard/contrats.py",), version="2.0.0"),
    Contract("Confidence.v1", "confiance et incertitude d'un avis (score de 0 à 1, méthode, calibrée ou non, "
             "bases ; niveau, inconnues, impact)", "comité d'agents", "Rachelle, panneau, journal",
             "avis des agents", "Confidence et Uncertainty",
             "score hors de [0, 1], méthode absente, niveau inconnu : refusé",
             "une confiance n'est jamais une probabilité d'avoir raison ni une autorisation", "immédiat", "aucun",
             "un avis, une confiance", "avec l'avis", 2, ("trendguard/contrats.py", "trendguard/comite.py")),
    Contract("ModelSelection.v1", "choix du modèle d'IA pour une demande, avec ses raisons",
             "routeur (modeles.py)", "exécution des modèles", "modèles connus, confidentialité, santé, budget",
             "modèles retenus dans l'ordre, raison de chacun, modèles écartés et pourquoi",
             "aucun modèle permis : réponse intégrée sans IA", "règles fixes", "immédiat", "aucun",
             "même état, même choix", "raison notée avec chaque appel", 2, ("trendguard/modeles.py",)),
    Contract("LLMExecution.v1", "un appel à un modèle d'IA", "exécution des modèles (modeles.py)",
             "trace des IA, rapport, panneau", "demande (identifiant), modèle, invite et sa version, classe "
             "de confidentialité", "identifiant d'exécution, réussi ou non, durée, jetons (inconnus : vides), "
             "erreur, repli, raison, empreinte des données envoyées",
             "échec : repli sur le modèle suivant, tracé", "clés jamais notées", "60 s",
             "repli sur un autre modèle (pas le même)", "identifiant unique par appel",
             "table llm_executions, en ajout seulement", 2, ("trendguard/modeles.py",)),
    Contract("ModelConsensus.v1", "consensus des IA de la veille", "veille (market_watch.py)",
             "rapport de la veille, panneau, Rachelle", "avis validés de chaque IA, fiabilité mesurée",
             "état (STRONG, MODERATE, WEAK, NONE, CONFLICT), accord et désaccord de 0 à 1, désaccords par crypto",
             "désaccord net : pas d'avis moyen", "conseil seulement : aucun effet sur les ordres", "à la veille",
             "aucun", "un consensus par jour", "rapport de la veille", 2, ("trendguard/market_watch.py",),
             classification="PUBLIC"),
    Contract("ModelDisagreement.v1", "désaccord net entre IA sur un sujet (une crypto, le climat)",
             "veille (market_watch.py)", "rapport de la veille, panneau, Rachelle",
             "positions de chaque IA (−1 à +1)", "sujet, type, gravité, positions, issue (NO_DECISION : pas de moyenne)",
             "moins de deux positions, type ou gravité inconnus : refusé", "conseil seulement", "à la veille",
             "aucun", "un désaccord par sujet et par rapport", "rapport de la veille", 2,
             ("trendguard/contrats.py", "trendguard/market_watch.py"), classification="PUBLIC"),
    Contract("PromptVersion.v1", "invite enregistrée : version, empreinte exacte, statut", "socle des modèles (modeles.py)",
             "exécution des modèles, veille, Rachelle, banc", "texte de l'invite",
             "« version#empreinte » notée avec chaque appel",
             "texte changé sans nouvelle version, ou invite non active : IA non appelée",
             "une invite ne change que par une revue du code (et son test)", "immédiat", "aucun",
             "une empreinte par version", "trace des appels", 2, ("trendguard/modeles.py",)),
    Contract("ModelBenchmark.v1", "banc d'évaluation d'un modèle d'IA, versionné", "banc (modeles.py)",
             "routeur (approbation), fiche du modèle, rapport", "questions à réponse connue : faits, finance, "
             "calcul, piège à invention", "justesse par catégorie, durée, erreurs, approuvé ou non et pourquoi",
             "appels en erreur : banc non concluant ; justesse sous 75 % ou régression : modèle écarté",
             "aucun modèle en service sans banc réussi", "60 s par question", "banc à refaire",
             "un résultat par modèle et par banc", "table llm_benchmarks, en ajout seulement", 2,
             ("trendguard/modeles.py",)),
    Contract("CommitteeView.v1", "avis consultatif du comité d'agents sur une crypto", "comité (comite.py)",
             "journal financier, Rachelle, panneau", "marché, régime, portefeuille, politique",
             "recommandation, consensus, confiance (Confidence.v1), incertitude, raisons, votes",
             "agent critique absent : BLOCAGE", "jamais une autorisation", "à la décision", "aucun",
             "un avis par crypto et par décision", "journal financier (avis du comité), en ajout seulement", 2,
             ("trendguard/comite.py",)),
    Contract("ExpertDiagnosis.v1", "diagnostic expert du bot (noyau cognitif)", "diagnostic expert (expert.py)",
             "rapport quotidien, Rachelle, panneau", "état, décision, audit, journal, journal du bot, PC, réseau",
             "verdict, constats avec gravité et preuves, propositions (jamais des actions)",
             "outil en panne : constat « inconnu », jamais inventé", "lecture seule", "00:45 UTC",
             "à la nuit suivante", "un diagnostic par nuit", "fichier du diagnostic, rapport", 2,
             ("trendguard/expert.py", "trendguard/cognitif.py")),
    Contract("PortfolioAnalysis.v1", "analyse du portefeuille : risque d'un jour, tests de résistance, attribution",
             "décision du jour (risque.py, stress.py, attribution.py)", "panneau, rapport, raisonnement",
             "positions, clôtures, trades clos", "VaR et CVaR à 95 %, pertes par scénario, résultat par crypto",
             "données insuffisantes : non mesuré", "mesures seulement, aucune décision", "à la décision", "aucun",
             "une analyse par décision", "dans l'état du bot", 2,
             ("trendguard/risque.py", "trendguard/stress.py", "trendguard/attribution.py")),
    Contract("Experiment.v1", "expérience notée au registre (réglages, données, version du code, résultats)",
             "évolution, études (registre.py)", "rejeu, contrôle, rapport", "réglages, période, données, code",
             "identifiant E0001…, empreinte des données, mesures, rejouable à l'identique",
             "rejeu différent : signalé", "écriture par le bot", "à chaque épreuve", "rejeu sur demande",
             "un identifiant par expérience", "fichier du registre", 3, ("trendguard/registre.py",)),
    Contract("ReleaseGate.v1", "porte du réel (porte 8), mesurée sur l'état du bot", "feuille de route (chantiers.py)",
             "porte d'exécution (achats réels), rapport, Rachelle",
             "portes 1 à 7, essai paper, réglages, arrêt d'urgence, mode sûr, journaux, alertes, rapport, vérification",
             "ouverte ou fermée, et chaque condition manquante", "mesure impossible : porte fermée",
             "aucune option ne la contourne ; fermée, aucun achat réel", "une fois par jour", "à la décision suivante",
             "une mesure par jour", "raisonnement et rapport quotidien", 1, ("trendguard/chantiers.py",)),
    Contract("Instrument.v1", "référentiel des instruments (cryptos suivies)", "cœur financier (finance.py)",
             "analyses, panneau", "liste des cryptos, première bougie, veto de la veille, règles de cotation connues",
             "identifiant stable place:marché:BASE-DEVISE, classe, devise, calendrier 24/7, état",
             "règle de cotation inconnue : vide, jamais inventée", "lecture seule", "à la décision", "aucun",
             "un identifiant par instrument", "analyse du jour", 3, ("trendguard/finance.py",), classification="PUBLIC"),
    Contract("Feature.v1", "indicateur versionné du magasin (technique, quantitatif)", "cœur financier (finance.py)",
             "analyses, journal financier", "bougies clôturées", "valeur (vide si non mesurable), version, fin des "
             "données", "données postérieures à la bougie : refusé (regard vers le futur)", "lecture seule",
             "à la décision", "aucun", "un indicateur par crypto, jour et version", "journal financier", 3,
             ("trendguard/finance.py", "trendguard/donnees.py"), classification="PUBLIC"),
    Contract("Forecast.v1", "prévision de fréquence à 30 jours", "cœur financier (finance.py)",
             "analyses, journal financier, calibration", "clôtures passées dans un marché comparable",
             "probabilité de hausse et son intervalle, rendement moyen et intervalle de 80 %, nombre de cas",
             "moins de 12 cas : aucune prévision", "jamais un prix annoncé ni un ordre", "à la décision",
             "évaluée à l'échéance", "une prévision par crypto et par jour", "journal financier (évaluation comprise)",
             2, ("trendguard/finance.py",), classification="PUBLIC"),
    Contract("Scenario.v1", "scénarios à 30 jours : fort recul, crise, baisse, central, hausse",
             "cœur financier (finance.py)", "analyses, Rachelle", "clôtures passées dans un marché comparable",
             "probabilité (leur somme fait 1), rendement, volatilité et baisse moyens, conditions d'invalidation",
             "somme différente de 1 ou scénario manquant : refusé", "information", "à la demande", "aucun",
             "un jeu par crypto et par jour", "analyse", 2, ("trendguard/finance.py",), classification="PUBLIC"),
    Contract("FinancialSignal.v1", "signal de la règle au format commun", "cœur financier (finance.py)",
             "analyses, journal", "indicateurs de la règle, régime de BTC",
             "direction, type, force, confiance, horizon, conditions d'entrée et de sortie, risque attendu, "
             "version de la règle", "rendement attendu inconnu : vide", "candidat seulement : la porte décide",
             "à la décision", "aucun", "un signal par crypto et par jour", "analyse", 2,
             ("trendguard/finance.py",), classification="PUBLIC"),
    Contract("FinancialAnalysis.v1", "analyse d'une crypto par le cœur financier (aide à la décision)",
             "cœur financier (finance.py)", "journal financier, Rachelle, rapport, commande finance",
             "indicateurs, qualité, régime, liens entre cryptos, calendrier, sentiment, prévisions, comité",
             "signal, à surveiller ou pas de trade avec les raisons, classement indicatif, confiances, preuves, "
             "risques, contradictions, conditions d'invalidation", "« pas de trade » sans raison : refusé",
             "jamais une autorisation", "à la décision", "aucun", "une analyse par crypto et par jour",
             "journal financier", 2, ("trendguard/finance.py",)),
    Contract("StrategySpec.v1", "fiche déclarative de la règle : langage sûr, versionnée, verrouillée sur le code",
             "moteur de stratégie (moteur_strategie.py)", "validation, backtest, décisions, Rachelle, commande regle",
             "réglages en vigueur", "conditions d'achat et de vente, stops, taille, contraintes, coûts, liquidité, "
             "réglages et plages validées, verrous du code", "fiche hors du langage permis : refusée, raison dite",
             "lecture seule : une fiche ne passe aucun ordre", "immédiat", "aucun", "même fiche, même empreinte",
             "version et empreinte dans chaque décision", 2, ("trendguard/moteur_strategie.py",),
             classification="PUBLIC"),
    Contract("StrategyDecision.v1", "décision de la règle pour une crypto : candidate, pas de trade, tenue ou vendue",
             "moteur de stratégie (moteur_strategie.py)", "raisonnement, rapport, Rachelle",
             "fiche compilée, indicateurs du jour, régime de BTC, positions",
             "chaque condition avec ses valeurs, raisons au format commun, taille et stop d'un achat, coût "
             "aller-retour", "« pas de trade » sans raison, candidate incomplète : refusées",
             "jamais une autorisation : la porte décide", "à la décision", "aucun", "une décision par crypto et par jour",
             "écart avec la règle exécutée signalé au journal du bot et au rapport", 2,
             ("trendguard/moteur_strategie.py",)),
    Contract("BacktestManifest.v1", "manifeste d'un backtest : tout ce qu'il faut pour le refaire à l'identique",
             "moteur de backtest (moteur_backtest.py)", "rapport de validation, vous",
             "configuration, données, fiche de la règle, code", "version du code, empreintes des données, de la "
             "configuration, du code, de l'environnement et du résultat, graine", "résultat refait différent : INVALID",
             "lecture seule : aucun ordre", "étude hors ligne", "aucun", "mêmes entrées, même empreinte du résultat",
             "dans le rapport de validation", 3, ("trendguard/moteur_backtest.py",), classification="PUBLIC"),
    Contract("BacktestResult.v1", "verdict d'un backtest : valide, avec réserves, invalide ou rejeté ; prêt pour le "
             "moteur de risque ou recherche seulement", "moteur de backtest (moteur_backtest.py)",
             "moteur de risque, vous", "backtest de la règle, épreuves, statistique",
             "mesures par époque, Sharpe probabiliste et dégonflé, note de qualité, réserves, raisons de rejet, "
             "limites", "rejet sans raison, « prêt » sans validité, limites sans « pas une garantie » : refusés",
             "jamais une autorisation : la règle en service ne change pas", "étude hors ligne", "aucun",
             "un résultat par manifeste", "dans le rapport de validation", 2, ("trendguard/moteur_backtest.py",)),
    Contract("RiskAssessment.v1", "évaluation du risque du portefeuille avant et après les achats du jour",
             "moteur de risque (moteur_risque.py)", "porte d'exécution, journal financier, raisonnement, rapport, "
             "Rachelle", "positions, cours et volumes validés, capital, plus haut, réglages de risque, garde, "
             "évaluations passées", "VaR et ES (quatre méthodes, 1 à 10 jours), volatilité, queue, corrélations, "
             "concentration, liquidité, baisse, budget, contributions, stress et stress inversé, limites, alertes, "
             "note et composantes, état et décision, fin de validité", "moteur en panne, données critiques "
             "invalides ou évaluation périmée : aucun achat (mode sûr), ventes permises", "lecture seule : jamais "
             "une autorisation", "à chaque décision", "aucun : la décision suivante réévalue",
             "une évaluation par jour, avant et après les achats", "journal financier, en ajout seulement", 1,
             ("trendguard/moteur_risque.py",)),
    Contract("QuantResult.v1", "résultat du laboratoire quantitatif : lois, stationnarité, persistance, volatilité, "
             "liens entre cryptos, pouvoir prédictif de la règle, anomalies, ruptures, régimes cachés",
             "moteur quantitatif (moteur_quant.py)", "vous, rapport docs/QUANT.md, moteurs de stratégie et de risque "
             "(lecture)", "cours et volumes journaliers en cache, réglages de la règle", "statistiques, tests avec "
             "statistique, p, effet et taille d'échantillon, corrections des tests multiples, conclusions, manifeste",
             "section aux données insuffisantes : INSUFFICIENT_DATA, jamais un chiffre inventé ; empreinte absente : "
             "refusé", "lecture seule : jamais un signal d'achat ni une autorisation", "étude hors ligne", "aucun",
             "mêmes données, même graine : même empreinte du résultat", "dans le rapport du laboratoire", 3,
             ("trendguard/moteur_quant.py",), classification="PUBLIC"),
    Contract("PortfolioDecision.v1", "état et décision consultative du portefeuille à chaque décision : dans les "
             "contraintes, à regarder ou aucune position", "moteur de portefeuille (moteur_portefeuille.py)",
             "raisonnement du jour, rapport, Rachelle, vous", "positions, cours, capital, liquidités, réglages de "
             "la règle", "poids, exposition, liquidités, concentration, risque engagé, volatilité, contributions, "
             "allocations comparées, rééquilibrage (jamais : la règle laisse courir les gagnants)",
             "« à regarder » sans raison, poids hors bornes, contrainte relâchée : refusés ; panne : aucune "
             "conséquence sur le trading", "lecture seule : jamais un ordre ni une autorisation", "à chaque décision",
             "aucun : la décision suivante réévalue", "une décision par jour", "dans l'état du bot", 2,
             ("trendguard/moteur_portefeuille.py",)),
    Contract("PaperAcceptanceReport.v1", "verdict des critères d'acceptation du paper (AC-001 à AC-044) : accepté "
             "(prêt pour le moteur de politique) ou bloqué, avec ce qui manque", "acceptation du paper (acceptation.py)",
             "rapport quotidien, Rachelle, vous", "état du bot, journal financier, audit, tests du dépôt, cours en "
             "cache (écart au backtest)", "état de chaque critère et sa preuve, note par famille et pondérée, "
             "observation, écart paper/backtest, gouvernance, verdict", "P0 raté, mesure impossible d'un P0, "
             "observation trop courte, écart inexpliqué : BLOCKED ; un accepté incohérent est refusé",
             "lecture seule : jamais une autorisation du réel", "à la demande et chaque nuit", "aucun",
             "un verdict par évaluation", "dans le rapport et docs/ACCEPTATION.md", 2, ("trendguard/acceptation.py",)),
    Contract("PolicyDecision.v1", "décision des politiques pour une intention d'achat : permis, limité, taille "
             "réduite, pas d'achat aujourd'hui, votre accord d'abord, interdit, mode sûr, compte gelé",
             "moteur de politiques (politique.py)", "journal du bot, état du bot, rapport, Rachelle",
             "intention d'achat, portefeuille et politiques du moment (vue de la porte), réglages",
             "politiques évaluées et leur version, violations et avertissements (observé, seuil), action la plus "
             "grave, fin de validité", "politique illisible : blocage ; refus sans raison, « permis » avec réserve : "
             "refusés", "lecture seule : la porte d'exécution applique, jamais une autorisation",
             "à chaque contrôle de la porte", "aucun", "une décision par contrôle",
             "écart avec la porte signalé au journal du bot", 1, ("trendguard/politique.py",)),
    Contract("AuthorizationDecision.v1", "qui peut faire quoi : identité, action, ressource, permis ou refusé, "
             "avec raisons et conditions", "moteur d'autorisation (autorisation.py)", "panneau (chaque action), "
             "bot (chaque achat), vous", "identité, action, ressource, conditions du moment (réel armé, porte du "
             "réel, évaluation du risque, contrôle approuvé…)", "décision, raisons, conditions vérifiées, version "
             "de la matrice, fin de validité", "identité, action ou condition inconnue : refus ; droit critique "
             "donné à une IA ou à un agent : refusé par le contrat", "lecture seule : décide, n'exécute rien",
             "à chaque action du panneau et à chaque achat", "aucun", "une décision par demande",
             "refus du panneau renvoyés avec leur raison ; écart avec la porte au journal du bot", 1,
             ("trendguard/autorisation.py",)),
    Contract("FinalValidationResult.v1", "validation finale d'un achat juste avant l'ordre : autorisation encore "
             "valable et non consommée, arrêt d'urgence et mode sûr levés, état inchangé depuis le contrôle (sinon "
             "nouveau contrôle)", "porte d'exécution (porte.py)", "bot (chaque achat), journal d'audit en cas de "
             "blocage", "intention, contrôle et autorisation de la porte, état critique au contrôle et juste avant "
             "l'ordre", "vérifications, empreintes avant/après, nouveau contrôle, raisons", "vérification impossible "
             "ou état changé hors des limites : blocage ; « PASS » avec une vérification fausse : refusé",
             "bloque seulement : ne rend jamais possible un achat refusé par la porte", "à chaque achat",
             "aucun : la décision suivante recommence", "une validation par achat",
             "blocages comptés dans l'état du bot et au journal d'audit", 1, ("trendguard/porte.py",)),
    Contract("GateReadinessReport.v1", "examen de la porte d'exécution : critères AC-001 à AC-050, note pondérée, "
             "essai de chaos", "examen de la porte (porte_examen.py)", "rapport, Rachelle, vous",
             "état du bot, code (routes vers Binance), tests du dépôt, essai de chaos", "état de chaque critère et "
             "sa preuve, note par famille et pondérée, chaos (ordres, ordres non autorisés, latences), verdict",
             "P0 raté ou non mesurable, ordre non autorisé au chaos : NOT_READY ; un « prêt » incohérent est refusé",
             "lecture seule : jamais une autorisation du réel", "à la demande", "aucun", "un examen par appel",
             "dans docs/PORTE_EXAMEN.md", 2, ("trendguard/porte_examen.py",)),
    Contract("LiveDeploymentReport.v1", "exécution réelle par paliers : palier en vigueur (lu dans vos réglages), "
             "palier suivant et sa porte, examen AC-001 à AC-060", "paliers du réel (deploiement.py)",
             "rapport, Rachelle, vous", "réglages, état du bot, acceptation du paper, porte du réel, examen de la "
             "porte d'exécution, tests du dépôt", "palier, portes et ce qui manque, état de chaque critère, note "
             "pondérée, verdict", "P0 raté ou non mesurable : NOT_READY ; palier sauté, porte ouverte qui dit ce qui "
             "manque, promotion automatique : refusés", "lecture seule : jamais une promotion ni une autorisation du "
             "réel", "à la demande", "aucun", "un examen par appel", "dans docs/DEPLOIEMENT.md", 2,
             ("trendguard/deploiement.py",)),
    Contract("ControlPlaneReport.v1", "plan de contrôle de la production : santé des services, dépendances, "
             "objectifs de service, incidents, reprise, superviseur borné, examen AC-001 à AC-060",
             "plan de contrôle (controle.py)", "rapport, Rachelle, vous", "état du bot, journaux, sauvegardes, "
             "ordinateur, réglages, tests du dépôt", "état de chaque service, incidents P0 à P4 et leur procédure, "
             "objectifs et budgets d'erreur, RPO et RTO mesurés, verdict", "mesure impossible : « non mesuré », jamais "
             "une réussite ; prêt avec un incident P0 ouvert : refusé", "lecture seule : il voit, il n'agit pas",
             "à la demande ; chaque nuit dans le rapport", "aucun", "un état par appel ; journal des incidents",
             "incidents ouverts et clos dans <bot>.incidents.json", 2, ("trendguard/controle.py",)),
    Contract("ModelCard.v1", "fiche d'un modèle : usage, interdits, données, validation, limites, plan de retour, "
             "niveau de risque, état", "gouvernance des modèles (apprentissage.py)", "vous, Rachelle, l'examen",
             "réglages, état de l'évolution, versions des moteurs", "fiche complète, empreinte, état lu dans le bot",
             "fiche incomplète, modèle critique sans propriétaire ni retour, modèle déclaré acheteur hors la règle : "
             "refusés", "lecture seule", "à la demande", "aucun", "une fiche par modèle et par version", "dans "
             "docs/APPRENTISSAGE.md", 2, ("trendguard/apprentissage.py",)),
    Contract("LearningGovernanceReport.v1", "apprentissage continu et gouvernance des modèles : registre, "
             "champion et challenger, dérive, frontières de l'apprentissage, examen AC-001 à AC-070",
             "gouvernance des modèles (apprentissage.py)", "rapport, Rachelle, vous", "registre, état du bot, cours "
             "en cache, tests du dépôt", "état de chaque critère, note, frontières vérifiées, verdict",
             "P0 raté, frontière franchie : NOT_READY ; auto-autorisation : refusée", "lecture seule : il mesure, il "
             "ne change rien", "à la demande", "aucun", "un examen par appel", "dans docs/APPRENTISSAGE.md", 2,
             ("trendguard/apprentissage.py",)),
    Contract("SecurityPostureReport.v1", "cybersécurité et autodéfense : inventaire des actifs, sorties vers "
             "Internet comparées à la liste blanche, intégrité du code, bibliothèques, événements, réponses "
             "prévues, examen AC-001 à AC-070", "cybersécurité (cyber.py)", "rapport, Rachelle, vous",
             "code (adresses), git, versions installées, état du bot, rapport de la nuit (libellés seulement)",
             "état de chaque critère, note, sorties hors liste, événements, verdict", "adresse hors liste blanche, "
             "événement P0 : NOT_READY ; « offensif » : refusé", "lecture seule : jamais offensif, jamais un secret "
             "lu", "à la demande ; chaque nuit dans le rapport", "aucun", "un état par appel",
             "dans docs/CYBER.md (sans adresse ni nom de réseau)", 2, ("trendguard/cyber.py",)),
    Contract("HealthReport.v1", "rapport quotidien et centre de sécurité", "rapport (report.py)",
             "vous (e-mail, panneau)", "état du bot, PC, journal, GitHub", "constats conformes, à corriger, informations",
             "source illisible : information", "lecture seule", "00:30 UTC", "rattrapé au retour du PC",
             "un rapport par jour", "rapports gardés 14 jours", 4, ("trendguard/report.py",)),
)


CONVENTIONS = (
    "Identifiants : UUID v4 pour les nouveaux contrats (messages, appels aux IA) ; les identifiants lisibles "
    "existants restent (décision `D-2026-10-06`, clé d'achat `jour:crypto:BUY`).",
    "Corrélation : l'identifiant de la décision relie le contrôle du risque, l'autorisation, l'ordre et la "
    "vente ; la cause (`causation_id`) dit quel événement a provoqué le suivant.",
    "Dates : UTC, ISO-8601 avec fuseau ; une heure sans fuseau est refusée.",
    "Versions : MAJEUR.MINEUR.CORRECTIF ; une évolution incompatible change le MAJEUR et fournit sa "
    "migration (ErrorEnvelope v1 → v2), jamais en silence.",
    "Classification : PUBLIC, INTERNAL, CONFIDENTIAL, SENSITIVE, RESTRICTED, SECRET, LOCAL_ONLY ; LOCAL_ONLY "
    "ne sort jamais du PC.",
    "Montants : `Money` (décimal exact et devise) aux frontières (journal d'audit) ; le moteur de trading v29 "
    "calcule en flottants arrondis à la précision de Binance.",
    "Unités : dans le nom du champ (`_usdt`, `_ms`, `_pct` en pour cent) ; un taux interne est une fraction "
    "(`risk_pct = 0.01` pour 1 %).",
    "Inconnu n'est pas zéro : une valeur inconnue est vide (`None`), jamais 0 ni faux (jetons non donnés par "
    "un fournisseur, rendement attendu).",
    "Confiance : un score de 0 à 1 avec sa méthode ; jamais une probabilité d'avoir raison, jamais une "
    "autorisation.",
    "Immuable : décisions, contrôles du risque, exécutions, avis du comité, appels aux IA et audit sont en "
    "ajout seulement ; une correction est une nouvelle ligne.",
    "Validation : `contrats.validate(schéma, données)` refuse un champ inconnu, absent, du mauvais type, hors "
    "bornes ou d'une valeur non permise ; rien n'est corrigé en silence.",
)


def contract(contract_id: str) -> Contract:
    """Un contrat du registre par son identifiant (KeyError s'il n'existe pas)."""
    for c in REGISTRY:
        if c.contract_id == contract_id:
            return c
    raise KeyError(contract_id)


def render() -> str:
    """docs/CONTRATS.md : la matrice des contrats et la fiche de chacun."""
    lines = ["# Contrats entre les modules de TrendGuard", "",
             "Tiré du registre `trendguard/contrats.py` (`python -m trendguard.contrats` le réécrit) ; un test "
             "vérifie que ce document et le registre restent identiques.", "",
             "| Contrat | Producteur (propriétaire) | Consommateurs | Version | Classification | Niveau |",
             "| --- | --- | --- | --- | --- | --- |"]
    for c in sorted(REGISTRY, key=lambda c: (c.level, c.contract_id)):
        lines.append(f"| `{c.contract_id}` | {c.producer} | {c.consumer} | {c.version} | {c.classification} "
                     f"| {LEVELS[c.level]} |")
    lines += ["", "## Conventions communes", ""] + [f"- {x}" for x in CONVENTIONS]
    for c in sorted(REGISTRY, key=lambda c: (c.level, c.contract_id)):
        lines += ["", f"## {c.contract_id}", "", f"{c.purpose[:1].upper()}{c.purpose[1:]}.", "",
                  "| Rubrique | Contrat |", "| --- | --- |"]
        for label, value in (("Producteur", c.producer), ("Consommateur", c.consumer), ("Entrée", c.inputs),
                             ("Sortie", c.outputs), ("Erreurs", c.errors), ("Droits", c.permissions),
                             ("Délai", c.timeout), ("Nouveaux essais", c.retry), ("Unicité", c.idempotency),
                             ("Trace (audit)", c.audit), ("Fichiers", ", ".join(f"`{f}`" for f in c.files))):
            lines.append(f"| {label} | {value} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    """Réécrit docs/CONTRATS.md à partir du registre."""
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / "docs" / "CONTRATS.md"
    path.write_text(render(), encoding="utf-8")
    print(f"{path} réécrit ({len(REGISTRY)} contrats)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
