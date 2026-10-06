"""
Socle multi-agents (prompt maître, étape 5 ; docs/AGENTS.md) : des agents
spécialisés, déclarés, limités et surveillés, qui analysent ensemble sans
jamais pouvoir agir sur l'argent.

    mission → superviseur → équipe → analyses indépendantes (tableau noir
    cloisonné) → critique, équipe rouge, vérification indépendante → débat
    (révision sur preuves seulement) → désaccord mesuré → consensus pondéré,
    quorum → recommandation (jamais une autorisation) → trace

Garanties par construction :
- un agent n'existe que par son manifeste inscrit au registre (identifiant,
  version, rôle, capacités, données lisibles, poids, veto, délai) ; un
  manifeste qui demanderait de passer des ordres est refusé ;
- un agent ne lit que les données de son manifeste (tableau noir cloisonné) :
  lire autre chose est une violation de sécurité, et l'agent est mis en
  quarantaine ; les analystes ne voient pas l'avis des autres avant d'avoir
  rendu le leur (pas d'effet moutonnier) ;
- tout passe par le bus de messages (horodatés, empreinte du contenu,
  identifiant unique : un message rejoué n'est pas traité deux fois) ;
- un agent qui échoue trois fois de suite est coupé (disjoncteur) ;
- un consensus n'est pas une vérité : un désaccord fort, une incertitude
  forte ou un agent critique absent empêchent toute recommandation d'achat ;
- une recommandation n'est jamais une autorisation : seule la porte
  d'exécution, après les règles du bot, peut laisser passer un ordre.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .cognitif import AutonomyPolicy, Orchestrator, Task, TaskGraph, Tool, ToolRegistry

STANCES = ("POUR", "NEUTRE", "CONTRE")
FAMILIES = ("analyse", "contrôle")
STATUSES = ("READY", "QUARANTINED", "DISABLED")
MESSAGE_KINDS = ("TASK_ASSIGNMENT", "TASK_RESULT", "REQUEST_REVIEW", "REQUEST_VERIFICATION", "CONFLICT",
                 "WARNING", "ESCALATION", "CONSENSUS", "REJECTION", "CANCELLATION")
BREAKER_FAILURES = 3


class AgentViolation(PermissionError):
    """Un agent a tenté de sortir de son manifeste."""


# ---------- Manifestes et résultats ----------

@dataclass(frozen=True)
class Manifest:
    """Ce qu'un agent est et a le droit de faire (déclaratif, versionné)."""
    agent_id: str
    version: str
    role: str
    family: str
    capabilities: Tuple[str, ...]
    reads: Tuple[str, ...]
    weight: float = 1.0
    veto: bool = False
    critical: bool = False
    timeout: float = 10.0
    may_order: bool = False

    def __post_init__(self) -> None:
        if self.may_order:
            raise AgentViolation(f"{self.agent_id} : aucun agent ne peut passer d'ordre")
        if self.family not in FAMILIES or not self.capabilities:
            raise ValueError(f"{self.agent_id} : famille ou capacités invalides")
        if not (0.0 <= self.weight <= 2.0):
            raise ValueError(f"{self.agent_id} : poids hors limites")


@dataclass(frozen=True)
class Challenge:
    """Une objection d'un agent de contrôle : contre quel agent, pourquoi, et
    son effet (réduire le poids, annuler l'avis, ou une raison de ne pas
    acheter)."""
    by: str
    target: str
    reason: str
    effect: str = "discount"          # discount, nullify, veto
    factor: float = 0.5


@dataclass(frozen=True)
class AgentResult:
    """Sortie structurée d'un agent (jamais du texte libre seul)."""
    agent_id: str
    stance: str
    score: float
    evidence: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    uncertainties: Tuple[str, ...] = ()
    veto: str = ""
    challenges: Tuple[Challenge, ...] = ()

    def __post_init__(self) -> None:
        if self.stance not in STANCES or not (-100.0 <= self.score <= 100.0):
            raise ValueError(f"{self.agent_id} : position ou score invalide")
        if abs(self.score) >= 50 and not self.evidence:
            raise ValueError(f"{self.agent_id} : un avis tranché exige des preuves")


@dataclass(frozen=True)
class Agent:
    manifest: Manifest
    fn: Callable[["View"], AgentResult]


# ---------- Registre (cycle de vie, quarantaine, disjoncteur, mesures) ----------

@dataclass
class AgentRecord:
    agent: Agent
    status: str = "READY"
    reason: str = ""
    runs: int = 0
    failures: int = 0
    streak: int = 0
    ms: int = 0


class AgentRegistry:
    """Registre central des agents."""

    def __init__(self, agents: Sequence[Agent] = ()):
        self.records: Dict[str, AgentRecord] = {}
        for a in agents:
            self.register(a)

    def register(self, agent: Agent) -> None:
        mid = agent.manifest.agent_id
        if mid in self.records:
            raise ValueError(f"agent {mid} déjà inscrit")
        self.records[mid] = AgentRecord(agent)

    def ready(self) -> List[Agent]:
        return [r.agent for r in self.records.values() if r.status == "READY"]

    def quarantine(self, agent_id: str, reason: str) -> None:
        r = self.records[agent_id]
        r.status, r.reason = "QUARANTINED", reason

    def restore(self, agent_id: str, reason: str) -> None:
        """Remise en service après examen (jamais automatique)."""
        r = self.records[agent_id]
        r.status, r.reason, r.streak = "READY", f"rétabli : {reason}", 0

    def note(self, agent_id: str, ok: bool, ms: int) -> None:
        r = self.records[agent_id]
        r.runs += 1
        r.ms += ms
        if ok:
            r.streak = 0
            return
        r.failures += 1
        r.streak += 1
        if r.streak >= BREAKER_FAILURES and r.status == "READY":
            self.quarantine(agent_id, f"disjoncteur : {r.streak} échecs de suite")

    def reliability(self, agent_id: str) -> float:
        r = self.records[agent_id]
        return 1.0 if not r.runs else max(0.2, 1.0 - r.failures / r.runs)

    def metrics(self) -> Dict[str, Dict[str, Any]]:
        return {k: {"status": r.status, "reason": r.reason, "runs": r.runs, "failures": r.failures,
                    "ms": r.ms // max(1, r.runs), "version": r.agent.manifest.version}
                for k, r in self.records.items()}


# ---------- Bus de messages et tableau noir cloisonné ----------

class MessageBus:
    """Seul canal entre agents et superviseur : messages horodatés, signés
    par l'empreinte de leur contenu, traités une seule fois."""

    def __init__(self, members: Sequence[str]):
        self.members = set(members) | {"superviseur"}
        self.log: List[Dict[str, Any]] = []
        self._seen: set = set()

    def send(self, sender: str, recipient: str, kind: str, payload: Any, message_id: str = "") -> bool:
        """Envoie un message ; False s'il a déjà été traité (rejoué)."""
        if sender not in self.members or recipient not in self.members:
            raise AgentViolation(f"message hors du bus : {sender} → {recipient}")
        if kind not in MESSAGE_KINDS:
            raise ValueError(f"type de message inconnu : {kind}")
        mid = message_id or uuid.uuid4().hex
        if mid in self._seen:
            return False
        self._seen.add(mid)
        body = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        self.log.append({"id": mid, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "from": sender, "to": recipient, "kind": kind,
                         "hash": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]})
        return True


class View:
    """Ce qu'un agent voit du tableau noir : seulement ses lectures permises."""

    def __init__(self, board: Dict[str, Any], manifest: Manifest):
        self._board, self._m = board, manifest

    def __getitem__(self, key: str) -> Any:
        if key not in self._m.reads:
            raise AgentViolation(f"{self._m.agent_id} n'a pas le droit de lire « {key} »")
        return self._board.get(key)

    def get(self, key: str, default: Any = None) -> Any:
        v = self[key]
        return default if v is None else v


# ---------- Débat, désaccord, consensus ----------

def revise(results: Dict[str, AgentResult], challenges: Sequence[Challenge]) -> Tuple[Dict[str, float], List[str]]:
    """Révision des avis sur preuves seulement : chaque objection retenue
    réduit ou annule le poids de l'avis visé ; les vetos s'ajoutent. Un avis
    n'est jamais aligné sur celui de la majorité."""
    factors = {k: 1.0 for k in results}
    vetoes = []
    for c in challenges:
        if c.effect == "veto":
            vetoes.append(f"{c.by} : {c.reason}")
        elif c.target in factors:
            factors[c.target] *= 0.0 if c.effect == "nullify" else c.factor
    return factors, vetoes


def disagreement(results: Dict[str, AgentResult], factors: Dict[str, float]) -> Tuple[str, float]:
    """Intensité du désaccord entre analystes (0 à 1) : écart entre l'avis
    le plus favorable et le plus défavorable encore en jeu."""
    scores = [r.score for k, r in results.items() if factors.get(k, 0) > 0 and abs(r.score) >= 10]
    if len(scores) < 2:
        return "LOW", 0.0
    spread = (max(scores) - min(scores)) / 200
    pros, cons = sum(s > 0 for s in scores), sum(s < 0 for s in scores)
    level = "HIGH" if spread >= 0.6 and min(pros, cons) >= 2 else "MEDIUM" if spread >= 0.4 and pros and cons else "LOW"
    return level, round(spread, 2)


def consensus(results: Dict[str, AgentResult], factors: Dict[str, float], manifests: Dict[str, Manifest],
              reliability: Callable[[str], float]) -> Tuple[float, str]:
    """Consensus pondéré (expertise du manifeste × fiabilité mesurée × effet
    des objections), et sa force : un consensus faible est dit faible."""
    w = {k: manifests[k].weight * reliability(k) * factors.get(k, 1.0) for k in results}
    total = sum(w.values())
    if total <= 0:
        return 0.0, "aucun"
    value = sum(results[k].score * w[k] for k in results) / total
    strength = "fort" if abs(value) >= 40 else "modéré" if abs(value) >= 20 else "faible"
    return round(value, 1), strength


# ---------- Superviseur ----------

@dataclass
class MissionResult:
    mission: str
    team: List[str]
    results: Dict[str, AgentResult]
    controls: Dict[str, AgentResult]
    missing: List[str]
    violations: List[str]
    challenges: List[Challenge]
    factors: Dict[str, float]
    vetoes: List[str]
    disagreement: Tuple[str, float]
    consensus: Tuple[float, str]
    messages: int
    trace: Dict[str, Any] = field(default_factory=dict)


class Supervisor:
    """Construit l'équipe, distribue les tâches par le bus, surveille,
    écarte les agents défaillants, organise critique, équipe rouge et
    vérification, puis mesure désaccord et consensus. Il n'exécute aucune
    opération lui-même."""

    def __init__(self, registry: AgentRegistry, policy: Optional[AutonomyPolicy] = None):
        self.registry = registry
        self.policy = policy or AutonomyPolicy(network=False, max_seconds=60)

    def team(self, needed: Sequence[str]) -> List[Agent]:
        """Équipe de la mission : les agents prêts dont une capacité est
        demandée (deux familles au moins : analyse et contrôle)."""
        return [a for a in self.registry.ready() if set(a.manifest.capabilities) & set(needed)]

    def _phase(self, agents: Sequence[Agent], board: Dict[str, Any], bus: MessageBus,
               violations: List[str]) -> Dict[str, AgentResult]:
        tools, tasks = [], []
        for a in agents:
            m = a.manifest

            def call(ctx: Dict[str, Any], a: Agent = a) -> AgentResult:
                res = a.fn(View(board, a.manifest))
                if res.agent_id != a.manifest.agent_id:
                    raise AgentViolation(f"{a.manifest.agent_id} se fait passer pour {res.agent_id}")
                return res
            tools.append(Tool(m.agent_id, m.role, call, "COMPUTE", timeout=m.timeout))
            tasks.append(Task(m.agent_id, m.agent_id, title=m.role))
            bus.send("superviseur", m.agent_id, "TASK_ASSIGNMENT", {"reads": list(m.reads)})
        runs = Orchestrator(ToolRegistry(tools), self.policy).run(TaskGraph("phase", tasks))
        out: Dict[str, AgentResult] = {}
        for k, run in runs.items():
            ok = run.state.state == "COMPLETED" and isinstance(run.result, AgentResult)
            self.registry.note(k, ok, run.ms)
            if ok:
                out[k] = run.result
                bus.send(k, "superviseur", "TASK_RESULT", {"stance": run.result.stance, "score": run.result.score})
            elif "AgentViolation" in run.error:
                violations.append(f"{k} : {run.error}")
                self.registry.quarantine(k, f"violation : {run.error}")
                bus.send("superviseur", k, "REJECTION", {"error": run.error})
        return out

    def run(self, mission: str, needed: Sequence[str], board: Dict[str, Any]) -> MissionResult:
        """Une mission complète, du choix de l'équipe au consensus."""
        team = self.team(needed)
        bus = MessageBus([a.manifest.agent_id for a in team])
        violations: List[str] = []
        analysts = [a for a in team if a.manifest.family == "analyse"]
        controls = [a for a in team if a.manifest.family == "contrôle"]
        results = self._phase(analysts, board, bus, violations)
        # Les contrôles lisent les avis rendus (jamais avant : pas d'effet moutonnier).
        board = dict(board, avis={k: r for k, r in results.items()})
        checked = self._phase(controls, board, bus, violations)
        challenges = [c for r in list(results.values()) + list(checked.values()) for c in r.challenges]
        for c in challenges:
            bus.send(c.by if c.by in bus.members else "superviseur", "superviseur", "CONFLICT", c.__dict__)
        factors, vetoes = revise(results, challenges)
        vetoes = [f"{k} : {r.veto}" for k, r in results.items() if r.veto] + vetoes
        manifests = {a.manifest.agent_id: a.manifest for a in team}
        dis = disagreement(results, factors)
        cons = consensus(results, factors, manifests, self.registry.reliability)
        bus.send("superviseur", "superviseur", "CONSENSUS", {"value": cons[0], "disagreement": dis[0]})
        missing = [a.manifest.agent_id for a in team if a.manifest.agent_id not in results
                   and a.manifest.agent_id not in checked]
        return MissionResult(mission, [a.manifest.agent_id for a in team], results, checked, missing, violations,
                             challenges, factors, vetoes, dis, cons, len(bus.log),
                             {"messages": [{k: m[k] for k in ("from", "to", "kind")} for m in bus.log]})
