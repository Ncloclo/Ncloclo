"""
Noyau cognitif (prompt maître, étape 4 ; docs/COGNITIF.md) : plan, graphe de
tâches, orchestration, vérification, incertitude, synthèse et décision, sans
IA sur le chemin des ordres.

    demande → plan (graphe de tâches validé) → orchestration (outils
    autorisés, délais, nouveaux essais selon la cause, annulation) →
    vérification croisée → incertitude → synthèse → décision → trace

Règles tenues par construction :
- un outil n'est utilisable que s'il est inscrit au registre ET permis par
  la politique d'autonomie ; aucun outil d'exécution (ordre, transfert)
  n'est permis en marche autonome : le noyau ne peut rien acheter ni vendre ;
- un graphe avec un cycle, une dépendance absente ou un outil inconnu est
  refusé avant toute exécution ;
- chaque tâche suit une machine d'états (transition impossible : erreur) ;
  une tâche dont un parent a échoué n'est pas lancée (BLOCKED) ; un résultat
  partiel est dit partiel ;
- nouvel essai seulement pour une panne passagère (TransientError), jamais
  pour une erreur de validation ou d'autorisation ;
- la décision n'est jamais une autorisation : une action proposée attend le
  niveau d'autorisation requis (vous) ;
- « inconnu » plutôt qu'une réponse inventée, « aucune action » plutôt qu'une
  action risquée.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

# ---------- Machine d'états des tâches ----------

TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "PENDING": frozenset({"READY", "BLOCKED", "CANCELLED"}),
    "READY": frozenset({"RUNNING", "CANCELLED"}),
    "RUNNING": frozenset({"COMPLETED", "FAILED", "TIMEOUT", "READY"}),       # READY : nouvel essai
    "COMPLETED": frozenset(), "FAILED": frozenset(), "TIMEOUT": frozenset(),
    "BLOCKED": frozenset(), "CANCELLED": frozenset(),
}
FINAL = frozenset({"COMPLETED", "FAILED", "TIMEOUT", "BLOCKED", "CANCELLED"})


class InvalidTransition(RuntimeError):
    """Transition interdite par la machine d'états (ex. COMPLETED → RUNNING)."""


class TransientError(RuntimeError):
    """Panne passagère (réseau, délai) : un nouvel essai est permis."""


@dataclass
class TaskState:
    task_id: str
    state: str = "PENDING"
    history: List[str] = field(default_factory=lambda: ["PENDING"])

    def move(self, new: str) -> None:
        if new not in TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.task_id} : {self.state} → {new} interdit")
        self.state = new
        self.history.append(new)


# ---------- Outils et politique d'autonomie ----------

TOOL_CLASSES = ("READ_ONLY", "COMPUTE", "RESEARCH", "DATA", "CODE", "SYSTEM", "FINANCIAL", "EXECUTION", "ADMIN")
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


@dataclass(frozen=True)
class Tool:
    tool_id: str
    description: str
    fn: Callable[[Dict[str, Any]], Any]
    tool_class: str = "READ_ONLY"
    risk: str = "LOW"
    timeout: float = 30.0
    network: bool = False

    def __post_init__(self) -> None:
        if self.tool_class not in TOOL_CLASSES or self.risk not in RISK_LEVELS:
            raise ValueError(f"outil {self.tool_id} : classe ou risque inconnu")


@dataclass(frozen=True)
class AutonomyPolicy:
    """Ce que le noyau peut faire seul (niveau 1 : analyser, sans agir)."""
    level: int = 1
    allowed_classes: FrozenSet[str] = frozenset({"READ_ONLY", "COMPUTE", "RESEARCH", "DATA"})
    max_risk: str = "MEDIUM"
    network: bool = True
    max_seconds: float = 900.0
    max_tasks: int = 50

    def permits(self, tool: Tool) -> Tuple[bool, str]:
        if tool.tool_class not in self.allowed_classes:
            return False, f"classe {tool.tool_class} interdite en marche autonome"
        if RISK_LEVELS.index(tool.risk) > RISK_LEVELS.index(self.max_risk):
            return False, f"risque {tool.risk} au-dessus de {self.max_risk}"
        if tool.network and not self.network:
            return False, "accès au réseau non permis"
        return True, "permis"


class ToolRegistry:
    """Registre des outils : un outil absent n'existe pas pour le noyau."""

    def __init__(self, tools: Sequence[Tool] = ()):
        self.tools: Dict[str, Tool] = {}
        for t in tools:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.tool_id in self.tools:
            raise ValueError(f"outil {tool.tool_id} déjà inscrit")
        self.tools[tool.tool_id] = tool

    def get(self, tool_id: str, policy: AutonomyPolicy) -> Tool:
        """L'outil, s'il est inscrit et permis (sinon PermissionError)."""
        tool = self.tools.get(tool_id)
        if tool is None:
            raise PermissionError(f"outil inconnu : {tool_id}")
        ok, why = policy.permits(tool)
        if not ok:
            raise PermissionError(f"outil {tool_id} refusé : {why}")
        return tool


# ---------- Plan : graphe de tâches ----------

@dataclass(frozen=True)
class Task:
    task_id: str
    tool_id: str
    deps: Tuple[str, ...] = ()
    retries: int = 0
    title: str = ""


class TaskGraph:
    """Graphe de tâches (DAG) : validé avant toute exécution."""

    def __init__(self, goal: str, tasks: Sequence[Task]):
        self.goal = goal
        self.tasks = {t.task_id: t for t in tasks}
        self._count = len(tasks)

    def validate(self, registry: ToolRegistry, policy: AutonomyPolicy) -> List[str]:
        """Erreurs du graphe : doublon, dépendance absente, outil inconnu ou
        interdit, cycle, trop de tâches. Liste vide : graphe valide."""
        errors = []
        if len(self.tasks) != self._count:
            errors.append("identifiant de tâche en double")
        if len(self.tasks) > policy.max_tasks:
            errors.append(f"{len(self.tasks)} tâches pour {policy.max_tasks} permises")
        for t in self.tasks.values():
            errors += [f"{t.task_id} : dépendance absente {d}" for d in t.deps if d not in self.tasks]
            try:
                registry.get(t.tool_id, policy)
            except PermissionError as e:
                errors.append(f"{t.task_id} : {e}")
        if not errors and self.order() is None:
            errors.append("cycle dans le graphe")
        return errors

    def order(self) -> Optional[List[List[str]]]:
        """Vagues de tâches indépendantes (ordre topologique), None s'il y a
        un cycle."""
        left = {k: set(t.deps) for k, t in self.tasks.items()}
        waves = []
        while left:
            ready = sorted(k for k, deps in left.items() if not deps)
            if not ready:
                return None
            waves.append(ready)
            for k in ready:
                left.pop(k)
            for deps in left.values():
                deps.difference_update(ready)
        return waves


# ---------- Orchestration ----------

@dataclass
class TaskRun:
    task_id: str
    title: str
    state: TaskState
    result: Any = None
    error: str = ""
    attempts: int = 0
    ms: int = 0


def _call(tool: Tool, ctx: Dict[str, Any], out: Dict[str, Any]) -> None:
    try:
        out["result"] = tool.fn(ctx)
    except BaseException as e:            # rapporté à l'orchestrateur, jamais avalé
        out["error"] = e


class Orchestrator:
    """Exécute un graphe validé, vague par vague (tâches d'une vague en
    parallèle), avec délai par tâche, budget total, nouveaux essais selon la
    cause et annulation."""

    def __init__(self, registry: ToolRegistry, policy: Optional[AutonomyPolicy] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.registry, self.policy, self.clock = registry, policy or AutonomyPolicy(), clock
        self.cancel = threading.Event()

    def run(self, graph: TaskGraph, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, TaskRun]:
        """Résultat de chaque tâche ; ValueError si le graphe est invalide."""
        errors = graph.validate(self.registry, self.policy)
        if errors:
            raise ValueError("plan refusé : " + " ; ".join(errors))
        ctx = dict(ctx or {})
        ctx.setdefault("results", {})
        runs = {k: TaskRun(k, t.title or k, TaskState(k)) for k, t in graph.tasks.items()}
        start = self.clock()
        for wave in graph.order() or []:
            live: List[Tuple[TaskRun, threading.Thread, Dict[str, Any], Tool, float]] = []
            for k in wave:
                run, task = runs[k], graph.tasks[k]
                if self.cancel.is_set():
                    run.state.move("CANCELLED")
                    run.error = "annulée"
                    continue
                if any(runs[d].state.state != "COMPLETED" for d in task.deps):
                    run.state.move("BLOCKED")
                    run.error = "tâche préalable non aboutie : " + ", ".join(
                        d for d in task.deps if runs[d].state.state != "COMPLETED")
                    continue
                if self.clock() - start > self.policy.max_seconds:
                    run.state.move("CANCELLED")
                    run.error = "budget de temps épuisé"
                    continue
                run.state.move("READY")
                live.append(self._start(run, self.registry.get(task.tool_id, self.policy), ctx))
            for run, th, out, tool, t0 in live:
                self._finish(run, graph.tasks[run.task_id], th, out, tool, t0, ctx)
        return runs

    def _start(self, run: TaskRun, tool: Tool, ctx: Dict[str, Any]
               ) -> Tuple[TaskRun, threading.Thread, Dict[str, Any], Tool, float]:
        run.state.move("RUNNING")
        run.attempts += 1
        out: Dict[str, Any] = {}
        th = threading.Thread(target=_call, args=(tool, ctx, out), daemon=True)
        t0 = self.clock()
        th.start()
        return run, th, out, tool, t0

    def _finish(self, run: TaskRun, task: Task, th: threading.Thread, out: Dict[str, Any],
                tool: Tool, t0: float, ctx: Dict[str, Any]) -> None:
        while True:
            th.join(max(0.0, tool.timeout - (self.clock() - t0)))
            run.ms = int((self.clock() - t0) * 1000)
            if th.is_alive():
                run.state.move("TIMEOUT")
                run.error = f"délai de {tool.timeout:.0f} s dépassé"
                return
            err = out.get("error")
            if err is None:
                run.result = out.get("result")
                ctx["results"][run.task_id] = run.result
                run.state.move("COMPLETED")
                return
            if isinstance(err, TransientError) and run.attempts <= task.retries and not self.cancel.is_set():
                run.state.move("READY")
                run, th, out, tool, t0 = self._start(run, tool, ctx)
                continue
            run.state.move("FAILED")
            run.error = f"{type(err).__name__} : {err}"
            return


# ---------- Vérification, incertitude, synthèse, décision ----------

VERDICTS = ("VERIFIED", "PARTIALLY_VERIFIED", "UNVERIFIED", "CONTRADICTED", "FAILED")
SEVERITIES = ("info", "moyenne", "élevée", "critique")
DECISIONS = ("ANSWER", "RESEARCH_MORE", "WAIT", "NO_ACTION", "ESCALATE", "BLOCK", "SIMULATE")
LEVELS = {0: "lecture autonome", 1: "analyse autonome", 2: "simulation autonome", 3: "action paper",
          4: "votre accord", 5: "action réelle contrôlée"}


@dataclass(frozen=True)
class Finding:
    """Un constat : sujet, gravité, détail, tâche source, geste proposé."""
    subject: str
    severity: str
    detail: str
    source: str
    action: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"gravité inconnue : {self.severity}")


@dataclass(frozen=True)
class Check:
    """Une vérification croisée : deux sources indépendantes, un verdict."""
    name: str
    kind: str
    verdict: str
    detail: str

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict inconnu : {self.verdict}")


def uncertainty(runs: Dict[str, TaskRun], checks: Sequence[Check]) -> Tuple[str, List[str]]:
    """Niveau d'incertitude (LOW à CRITICAL) et ses raisons : tâches non
    abouties, contradictions, vérifications impossibles."""
    missing = [r.task_id for r in runs.values() if r.state.state != "COMPLETED"]
    contra = [c.name for c in checks if c.verdict == "CONTRADICTED"]
    unver = [c.name for c in checks if c.verdict in ("UNVERIFIED", "FAILED")]
    why = ([f"tâche(s) non abouties : {', '.join(missing)}"] if missing else []) + \
          ([f"contradiction(s) : {', '.join(contra)}"] if contra else []) + \
          ([f"non vérifié : {', '.join(unver)}"] if unver else [])
    share = len(missing) / max(1, len(runs))
    level = ("CRITICAL" if share > 0.5 else "HIGH" if contra or share > 0.25
             else "MEDIUM" if missing or unver else "LOW")
    return level, why


@dataclass(frozen=True)
class Proposal:
    action: str
    reason: str
    level: int = 4                    # votre accord : jamais exécutée par le noyau


@dataclass(frozen=True)
class DecisionResult:
    decision: str
    rationale: str
    findings: Tuple[Finding, ...]
    proposals: Tuple[Proposal, ...]
    uncertainty: str
    authorized: bool = False          # une décision n'est jamais une autorisation

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(f"décision inconnue : {self.decision}")
        if self.authorized:
            raise ValueError("le noyau cognitif ne s'autorise jamais lui-même")


def decide(findings: Sequence[Finding], level: str, block: Sequence[Proposal] = ()) -> DecisionResult:
    """Décision d'après les constats, l'incertitude et les propositions
    critiques : BLOCK (une action de sûreté est proposée), RESEARCH_MORE
    (trop d'inconnu pour conclure), ESCALATE (des points pour vous) ou
    NO_ACTION (rien à faire)."""
    ordered = tuple(sorted(findings, key=lambda f: -SEVERITIES.index(f.severity)))
    serious = [f for f in ordered if f.severity in ("élevée", "critique")]
    if block:
        return DecisionResult("BLOCK", "un point critique demande une mesure de sûreté, à votre accord",
                              ordered, tuple(block), level)
    if level in ("HIGH", "CRITICAL"):
        return DecisionResult("RESEARCH_MORE", "trop d'éléments manquent ou se contredisent pour conclure",
                              ordered, (), level)
    if serious or any(f.severity == "moyenne" for f in ordered):
        props = tuple(Proposal(f.action, f.subject) for f in ordered if f.action and f.severity != "info")
        return DecisionResult("ESCALATE", f"{len([f for f in ordered if f.severity != 'info'])} point(s) "
                                          "demandent votre attention", ordered, props, level)
    return DecisionResult("NO_ACTION", "rien d'anormal : aucune action nécessaire", ordered, (), level)


def trace(goal: str, runs: Dict[str, TaskRun], checks: Sequence[Check], result: DecisionResult) -> Dict[str, Any]:
    """Trace lisible du raisonnement (sans pensée privée) : plan, tâches et
    leurs états, vérifications, incertitude, décision, propositions."""
    return {"goal": goal, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tasks": [{"id": r.task_id, "title": r.title, "state": r.state.state, "history": r.state.history,
                       "attempts": r.attempts, "ms": r.ms, "error": r.error} for r in runs.values()],
            "partial": any(r.state.state != "COMPLETED" for r in runs.values()),
            "checks": [c.__dict__ for c in checks], "uncertainty": result.uncertainty,
            "decision": result.decision, "rationale": result.rationale,
            "findings": [f.__dict__ for f in result.findings],
            "proposals": [{**p.__dict__, "level_text": LEVELS[p.level]} for p in result.proposals],
            "authorized": result.authorized}
