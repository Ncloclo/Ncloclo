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
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ContractError(ValueError):
    """Donnée refusée par un contrat (enveloppe d'erreur standard)."""

    def __init__(self, code: str, message: str, category: str = "VALIDATION_ERROR",
                 retryable: bool = False):
        super().__init__(message)
        self.code, self.category, self.retryable = code, category, retryable

    def envelope(self, source: str, correlation_id: str = "") -> Dict[str, Any]:
        """L'erreur au format commun (ErrorEnvelope.v1), pour le journal d'audit."""
        return {"error_code": self.code, "message": str(self), "category": self.category,
                "severity": "error", "retryable": self.retryable, "source": source,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "correlation_id": correlation_id}


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
                                             "est comprise", category="VALIDATION_ERROR")


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
    Contract("AuditEvent.v1", "trace infalsifiable de chaque opération critique", "bot (un seul écrivain)",
             "rapport quotidien, commande audit", "acteur, action, objet, avant, après, raison, autorisation, résultat",
             "ligne chaînée à la précédente par son empreinte", "journal illisible : achats refusés",
             "écriture par le bot seul ; aucun module ne modifie ni n'efface", "immédiat", "aucun",
             "numéro unique et croissant", "c'est l'audit", 1, ("trendguard/audit.py",)),
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
             "clôtures et volumes, bougies en cours exclues", "réseau : décision reportée, BTC manquant : reportée",
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
    Contract("HealthReport.v1", "rapport quotidien et centre de sécurité", "rapport (report.py)",
             "vous (e-mail, panneau)", "état du bot, PC, journal, GitHub", "constats conformes, à corriger, informations",
             "source illisible : information", "lecture seule", "00:30 UTC", "rattrapé au retour du PC",
             "un rapport par jour", "rapports gardés 14 jours", 4, ("trendguard/report.py",)),
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
             "| Contrat | Producteur | Consommateur | Niveau |", "| --- | --- | --- | --- |"]
    for c in sorted(REGISTRY, key=lambda c: (c.level, c.contract_id)):
        lines.append(f"| `{c.contract_id}` | {c.producer} | {c.consumer} | {LEVELS[c.level]} |")
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
