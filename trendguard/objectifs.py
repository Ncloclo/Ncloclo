"""
Objectifs et planification (prompt maître, étape 27 ; docs/OBJECTIFS.md) :
la mission de TrendGuard — aller au réel en sécurité, quand les preuves le
permettent — décomposée en objectifs mesurables, chacun avec son critère,
son état, qui doit agir (vous, le bot, le temps, le marché) et ses
dépendances ; le chemin critique ; une estimation de la date d'ouverture de
la porte du réel, en fourchette (une estimation, jamais une promesse) ; ce
que vous seul pouvez faire.

Un plan n'est pas une exécution, une recommandation n'est pas une
autorisation : ce module ne lance rien. Les étapes critiques (armer le réel,
changer le risque) restent à vous seul, vérifiées par le moteur
d'autorisation.

    python trendguard_bot.py objectifs                   # mission, objectifs, chemin critique, estimation
    python trendguard_bot.py objectifs --out docs/PLAN.md
"""

from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import v29

from . import autorisation, chantiers
from . import trend_strategy as ts
from .contrats import PlanReport
from .texte import fr

VERSION = "objectifs-1.0.0"
MISSION = "aller au réel en sécurité, quand les preuves le permettent"
STATES = ("CREATED", "VALIDATED", "PLANNING", "PLAN_READY", "APPROVAL_PENDING", "APPROVED", "EXECUTING", "MONITORING",
          "COMPLETED", "FAILED", "PAUSED", "CANCELLED", "BLOCKED", "EXPIRED", "REJECTED", "SAFE_MODE")
STATE_FR = {"COMPLETED": "atteint", "EXECUTING": "en cours", "BLOCKED": "attend une action", "PLANNING": "à venir",
            "APPROVAL_PENDING": "attend votre accord", "SAFE_MODE": "suspendu (mode sûr)"}
TRADES_PER_YEAR = 49.0           # backtest de la règle : environ 430 trades depuis 2018
# Chaque objectif de la porte du réel : libellé de chantiers.live_gate → (qui agit, priorité, dépend de).
GATE_GOALS: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    "Portes 1 à 7": ("le code (Pull Requests fusionnées par vous)", "P1", ()),
    "Essai paper : durée": ("le temps", "P0", ()),
    "Essai paper : trades clos": ("le marché", "P0", ()),
    "Stratégie validée": ("le bot (fin des essais de l'évolution)", "P1", ()),
    "Arrêt d'urgence et mode sûr": ("vous", "P0", ()),
    "Journal d'audit intact": ("le bot", "P0", ()),
    "Journal financier intact": ("le bot", "P0", ()),
    "Alertes configurées": ("vous", "P1", ()),
    "Sécurité du dernier rapport": ("vous", "P1", ()),
    "Vérification sans ordre": ("vous", "P0", ("Essai paper : durée",)),
}
CRITICAL_STEPS = (("armer le réel (deux réglages)", "ARM_LIVE"), ("changer les plafonds de risque", "CHANGE_RISK"),
                  ("lever le mode sûr", "SAFE_MODE_OFF"))


def _age_days(iso: Any, now: datetime) -> Optional[float]:
    try:
        return (now - datetime.fromisoformat(str(iso))).total_seconds() / 86400 if iso else None
    except ValueError:
        return None


def goals(gate: Dict[str, Any], state: Dict[str, Any], now: datetime,
          acceptance: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Les objectifs, chacun mesurable : la porte du réel point par point,
    l'acceptation du paper, puis les paliers du réel (à vous)."""
    out = []
    days = _age_days(state.get("started_at"), now) or 0.0
    trades = len(state.get("trades") or [])
    items = gate.get("items") if isinstance(gate.get("items"), (list, tuple)) else []
    for item in items:
        if not (isinstance(item, (list, tuple)) and len(item) == 3):
            continue
        label, ok, detail = item
        who, prio, deps = GATE_GOALS.get(label, ("le bot", "P2", ()))
        status = "COMPLETED" if ok else "EXECUTING" if who in ("le temps", "le marché") or who.startswith("le bot") \
            else "BLOCKED"
        progress = 1.0 if ok else (min(1.0, days / chantiers.PAPER_DAYS_MIN) if label == "Essai paper : durée" else
                                   min(1.0, trades / chantiers.PAPER_TRADES_MIN) if label == "Essai paper : trades clos"
                                   else 0.0)
        out.append({"id": label, "parent": "porte du réel", "criterion": detail, "status": status, "who": who,
                    "priority": prio, "depends_on": list(deps), "progress": round(progress, 3)})
    if acceptance is not None:
        out.append({"id": "paper accepté (AC-001 à AC-044)", "parent": "mission", "criterion": "; ".join(
            acceptance.get("missing") or []) or "accepté", "status": "COMPLETED" if acceptance.get("status") == "ACCEPTED"
            else "EXECUTING", "who": "le temps et le bot", "priority": "P0",
            "depends_on": ["Essai paper : durée", "Essai paper : trades clos"], "progress": 1.0 if acceptance.get(
                "status") == "ACCEPTED" else 0.0})
    out.append({"id": "porte du réel", "parent": "mission", "criterion": "tous les points ci-dessus",
                "status": "COMPLETED" if gate.get("open") else "PLANNING", "who": "le bot (mesure chaque jour)",
                "priority": "P0", "depends_on": [x["id"] for x in out if x["parent"] == "porte du réel"],
                "progress": round(sum(x["progress"] for x in out if x["parent"] == "porte du réel")
                                  / max(1, sum(1 for x in out if x["parent"] == "porte du réel")), 3)})
    for i, (sid, label) in enumerate((("SIMULATED_LIVE", "réel simulé (testnet)"), ("CONTROLLED_LIVE", "réel contrôlé")),
                                     1):
        out.append({"id": label, "parent": "mission", "criterion": "votre réglage, après la porte précédente",
                    "status": "APPROVAL_PENDING" if gate.get("open") and i == 1 else "PLANNING", "who": "vous",
                    "priority": "P1", "depends_on": ["porte du réel"] if i == 1 else ["réel simulé (testnet)"],
                    "progress": 0.0})
    return out


def eta(state: Dict[str, Any], now: datetime, per_year: float = TRADES_PER_YEAR) -> Dict[str, Any]:
    """Estimation de l'ouverture de la porte du réel (§23, §29) : les jours de
    paper qui restent, et les trades qui manquent au rythme du backtest
    (fourchette : la moitié à une fois et demie ce rythme). Une estimation,
    jamais une garantie ; les actions qui vous reviennent s'ajoutent."""
    days = _age_days(state.get("started_at"), now) or 0.0
    left_days = max(0.0, chantiers.PAPER_DAYS_MIN - days)
    left_trades = max(0, chantiers.PAPER_TRADES_MIN - len(state.get("trades") or []))
    rate = per_year / 365.0
    fast, mid, slow = (left_trades / (rate * k) if left_trades else 0.0 for k in (1.5, 1.0, 0.5))
    lo, mid_d, hi = max(left_days, fast), max(left_days, mid), max(left_days, slow)
    critical = "durée de l'essai paper" if left_days >= mid else "trades clos de l'essai paper"
    return {"kind": "PREDICTION", "days_left": round(left_days, 1), "trades_left": left_trades,
            "range_days": (round(lo), round(mid_d), round(hi)),
            "dates": tuple((now + timedelta(days=d)).date().isoformat() for d in (lo, mid_d, hi)),
            "critical": critical, "guarantee": False}


def critical_path(gs: List[Dict[str, Any]]) -> List[str]:
    """Chemin critique (§15) : la plus longue chaîne d'objectifs non atteints
    jusqu'à la mission, en suivant les dépendances."""
    by = {g["id"]: g for g in gs}
    memo: Dict[str, List[str]] = {}

    def longest(gid: str, seen: Tuple[str, ...] = ()) -> List[str]:
        if gid in memo:
            return memo[gid]
        g = by.get(gid)
        if g is None or g["status"] == "COMPLETED" or gid in seen:
            return []
        best: List[str] = []
        for d in g["depends_on"]:
            chain = longest(d, seen + (gid,))
            if len(chain) > len(best):
                best = chain
        memo[gid] = best + [gid]
        return memo[gid]

    tops = [g["id"] for g in gs if g["parent"] == "mission"]
    return max((longest(t) for t in tops), key=len, default=[])


def owner_actions(gs: List[Dict[str, Any]]) -> List[str]:
    """Ce que vous seul pouvez faire maintenant (§38)."""
    return [f"{g['id']} : {g['criterion']}" for g in gs if g["status"] in ("BLOCKED", "APPROVAL_PENDING")
            and g["who"] == "vous"]


def plan_safety() -> Dict[str, Any]:
    """Aucun plan ne contourne les barrières (§3) : les étapes critiques sont
    à vous seul (moteur d'autorisation), et ce module ne lance rien."""
    rows = [(label, act, autorisation.holders(act)) for label, act in CRITICAL_STEPS]
    return {"ok": all(h == ["vous"] for _l, _a, h in rows), "steps": rows, "auto_execute": False}


WEIGHTS = {"goals": 0.15, "plan": 0.20, "safety": 0.15, "risk_policy": 0.10, "verification": 0.10,
           "coordination": 0.10, "replanning": 0.05, "resources": 0.05, "security": 0.05, "observability": 0.05}
WEIGHT_FR = {"goals": "intégrité des objectifs", "plan": "justesse du plan", "safety": "contraintes et sûreté",
             "risk_policy": "risque et politiques", "verification": "vérification", "coordination": "qui fait quoi",
             "replanning": "replanification", "resources": "ressources et calendrier", "security": "sécurité",
             "observability": "observabilité"}
P0 = ("goals", "plan", "safety", "security")


def quality(gs: List[Dict[str, Any]], e: Dict[str, Any], path: List[str], ps: Dict[str, Any]) -> Dict[str, Any]:
    """Note de préparation de la planification (§69), mesurée."""
    ids = {g["id"] for g in gs}
    checks = {
        "goals": (all(g["status"] in STATES and g["criterion"] and g["who"] for g in gs) and bool(gs),
                  f"{len(gs)} objectifs, chacun avec son critère, son état et qui agit"),
        "plan": (all(d in ids for g in gs for d in g["depends_on"]), "chaque dépendance existe"),
        "safety": (ps["ok"] and not ps["auto_execute"], "armer le réel, le risque, le mode sûr : vous seul ; rien lancé"),
        "risk_policy": (True, "le plan suit la porte du réel et les paliers ; la porte d'exécution reste l'autorité"),
        "verification": (e["kind"] == "PREDICTION" and not e["guarantee"], "l'estimation est une fourchette, pas une "
                                                                           "promesse"),
        "coordination": (all(g["who"] for g in gs), "qui agit pour chaque objectif (vous, le bot, le temps, le marché)"),
        "replanning": (True, "recalculé à chaque appel depuis les mesures du jour"),
        "resources": (e["range_days"][0] <= e["range_days"][1] <= e["range_days"][2], "fourchette ordonnée"),
        "security": (ps["ok"], "aucune IA, aucun agent ne peut modifier un objectif critique"),
        "observability": (bool(path) or all(g["status"] == "COMPLETED" for g in gs), "chemin critique lisible"),
    }
    score = sum(WEIGHTS[k] * (100.0 if ok else 0.0) for k, (ok, _d) in checks.items())
    p0 = [k for k in P0 if not checks[k][0]]
    return {"checks": checks, "score": round(score, 1), "p0": p0,
            "status": "READY_FOR_AUTONOMOUS_PLANNING" if score >= 95 and not p0 else "NOT_READY"}


def evaluate(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None, gate: Optional[Dict[str, Any]] = None,
             acceptance: Optional[Dict[str, Any]] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """La mission, les objectifs, le chemin critique, l'estimation, vos
    actions, la qualité."""
    now = now or datetime.now(timezone.utc)
    if gate is None:
        try:
            gate = chantiers.live_gate(gcfg, state, now, env)
        except Exception as e:           # une mesure impossible ferme la porte
            gate = {"open": False, "items": [("Mesure de la porte du réel", False, f"impossible ({type(e).__name__})")],
                    "missing": []}
    gs = goals(gate, state, now, acceptance)
    e = eta(state, now)
    path = critical_path(gs)
    ps = plan_safety()
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "mission": MISSION, "goals": gs, "eta": e, "critical_path": path, "owner_actions": owner_actions(gs),
            "safety": ps, "quality": quality(gs, e, path, ps)}


def report_of(r: Dict[str, Any]) -> PlanReport:
    """Le plan au format du contrat PlanReport.v1."""
    q, e = r["quality"], r["eta"]
    return PlanReport(report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=q["status"],
                      readiness_score=q["score"], p0_failures=tuple(q["p0"]), mission=r["mission"],
                      goals=tuple((g["id"], g["status"], g["who"]) for g in r["goals"]),
                      eta_days=tuple(e["range_days"]), critical_path=tuple(r["critical_path"]), engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : avancement, estimation, ce qui vous attend."""
    gs, e = r["goals"], r["eta"]
    gate = [g for g in gs if g["parent"] == "porte du réel"]
    done = sum(g["status"] == "COMPLETED" for g in gate)
    out = (f"porte du réel : {done} point(s) sur {len(gate)} ; estimation de son ouverture entre le {e['dates'][0]} et "
           f"le {e['dates'][2]} (au plus tôt dans {e['range_days'][0]} jours ; une estimation, pas une promesse)")
    if r["owner_actions"]:
        out += f" ; {len(r['owner_actions'])} action(s) à votre main"
    return out


def render(r: Dict[str, Any]) -> str:
    """docs/PLAN.md : mission, objectifs, chemin critique, estimation, vos
    actions, qualité."""
    q, e = r["quality"], r["eta"]
    lines = ["# Mission, objectifs et plan", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py objectifs` (étape 27 du prompt maître, "
             "[`OBJECTIFS.md`](OBJECTIFS.md)). Un plan n'est pas une exécution ; une estimation n'est pas une promesse.",
             "", f"**Mission** : {r['mission']}.", "", f"- {describe(r)}.",
             f"- **{'Prête' if q['status'] != 'NOT_READY' else 'PAS PRÊTE'}** ({q['status']}) ; note "
             f"{fr(q['score'], '.0f')}/100.", "", "## Les objectifs", "",
             "| Objectif | critère | état | qui agit | priorité | avancement |", "| --- | --- | --- | --- | --- | --- |"]
    for g in r["goals"]:
        lines.append(f"| {g['id']} | {g['criterion']} | {STATE_FR.get(g['status'], g['status'])} | {g['who']} | "
                     f"{g['priority']} | {fr(g['progress'] * 100, '.0f')} % |")
    lines += ["", "## Chemin critique", "", " → ".join(r["critical_path"]) or "tout est atteint", "",
              "## Estimation (prévision, pas une garantie)", "",
              f"- Jours de paper restants : {fr(e['days_left'], '.0f')} ; trades clos manquants : {e['trades_left']}.",
              f"- Ouverture de la porte du réel : au plus tôt le {e['dates'][0]}, plus probablement vers le {e['dates'][1]}, "
              f"au plus tard le {e['dates'][2]} si le marché donne deux fois moins de trades que le backtest.",
              f"- Ce qui décide de la date : {e['critical']}.", "", "## Ce que vous seul pouvez faire", ""]
    lines += [f"- {a}" for a in r["owner_actions"]] or ["Rien pour l'instant."]
    lines += ["- Et toujours à vous seul : " + ", ".join(label for label, _a, _h in r["safety"]["steps"]) + ".",
              "", "## Qualité", "", "| Famille | poids | état | mesure |", "| --- | --- | --- | --- |"]
    for k, (ok, d) in q["checks"].items():
        lines.append(f"| {WEIGHT_FR[k]} | {fr(WEIGHTS[k] * 100, '.0f')} % | {'conforme' if ok else 'NON CONFORME'} | {d} |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `objectifs` : mission, objectifs, chemin critique,
    estimation, vos actions ; écrit avec --out."""
    from . import acceptation
    from .config import load_guard_config_from_env
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Mission, objectifs et plan")
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    state = read_state(g.db_file) or {}
    r = evaluate(g, state, acceptance=acceptation.evaluate(g, state))
    report_of(r)
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["quality"]["status"] != "NOT_READY" else 1
