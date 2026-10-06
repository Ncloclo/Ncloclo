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
from datetime import datetime, timezone
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
                                        ModelConsensus)})


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
    Contract("CommitteeView.v1", "avis consultatif du comité d'agents sur une crypto", "comité (comite.py)",
             "journal financier, Rachelle, panneau", "marché, régime, portefeuille, politique",
             "recommandation, consensus, confiance (Confidence.v1), incertitude, raisons, votes",
             "agent critique absent : BLOCAGE", "jamais une autorisation", "à la décision", "aucun",
             "un avis par crypto et par décision", "journal financier (avis du comité), en ajout seulement", 2,
             ("trendguard/comite.py",)),
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
