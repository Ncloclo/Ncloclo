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
                                        Scenario, FinancialSignal, StrategyDecision)})


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
             "porte d'exécution", "OrderIntent, portefeuille du moment, réglages de risque, garde, mode sûr",
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
