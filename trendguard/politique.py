"""
Moteur de politiques (prompt maître, étape 14 ; docs/MOTEUR_POLITIQUE.md) :
les règles qui disent quand un achat est permis, limité, soumis à votre
accord, reporté ou interdit, écrites en registre déclaratif, versionné et
expliqué.

    contexte (intention d'achat, portefeuille, réglages) → registre des
    politiques (chacune : identifiant, version, type, gravité, catégorie,
    condition à tenir, action si elle ne tient pas) → évaluation (une
    condition illisible = blocage, jamais une permission) → résolution des
    conflits (l'action la plus grave l'emporte) → décision (ALLOW,
    ALLOW_WITH_LIMITS, REDUCE_SIZE, NO_TRADE, REQUIRE_HUMAN_APPROVAL,
    BLOCK, SAFE_MODE, FREEZE_ACCOUNT), valable 5 minutes, expliquée

La porte d'exécution (porte.py) reste le point d'application : elle seule
laisse passer ou refuse un achat. Ce registre en est la description
déclarative, vérifiée à chaque achat (0 écart attendu, un écart est signalé)
et par les tests sur des milliers de cas tirés au hasard. Une décision de
politique n'est jamais une autorisation ; il n'y a pas de dérogation
silencieuse : une règle change dans le code, versionnée et testée.

    python trendguard_bot.py politique                  # le registre
    python trendguard_bot.py politique simulation       # les politiques de risque éprouvées sur l'historique
"""

from __future__ import annotations

import argparse
import math
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

import v29

from . import moteur_backtest as mb
from . import porte
from . import trend_strategy as ts
from .contrats import POLICY_ACTIONS, ContractError, OrderIntent, PolicyDecision
from .texte import fr

REGISTRY_VERSION = "politiques-1.2.0"     # 1.1.0 (étape 16) : POL-EXCHANGE-RULES ; 1.2.0 (17) : POL-LIVE-STAGE
TTL_SECONDS = porte.AUTH_SECONDS
ACTIONS = POLICY_ACTIONS
SEVERITY = {a: k for k, a in enumerate(ACTIONS)}           # la plus grave l'emporte
CATEGORIES = ("SECURITY", "HARD_RISK", "COMPLIANCE", "ACCOUNT", "PORTFOLIO", "STRATEGY", "DATA", "SOFT")
TYPES = ("AUTONOMY", "SECURITY", "RISK", "ACCOUNT", "PORTFOLIO", "STRATEGY", "INSTRUMENT", "DATA", "MODEL",
         "EVENT", "OPERATIONAL", "LEVERAGE", "LIQUIDITY")
OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}
EMERGENCY_ACTIONS = ("SAFE_MODE", "FREEZE_ACCOUNT")
ACTION_FR = {"ALLOW": "permis", "ALLOW_WITH_LIMITS": "permis dans les limites", "REDUCE_SIZE": "taille réduite",
             "NO_TRADE": "pas d'achat aujourd'hui", "REQUIRE_HUMAN_APPROVAL": "votre accord d'abord",
             "BLOCK": "interdit", "SAFE_MODE": "mode sûr", "FREEZE_ACCOUNT": "compte gelé (arrêt d'urgence)"}


def _pol(pid: str, name: str, ptype: str, category: str, require: Dict[str, Any], action: str, porte_check: str,
         reason: str, severity: str = "HARD") -> Dict[str, Any]:
    return {"id": pid, "version": "1.0.0", "name": name, "type": ptype, "category": category, "severity": severity,
            "scope": "achat", "require": require, "action": action, "porte": porte_check, "reason": reason,
            "status": "ACTIVE"}


def _f(field: str, op: str, value: Any = None, ref: Optional[str] = None, factor: float = 1.0,
       plus: float = 0.0) -> Dict[str, Any]:
    out: Dict[str, Any] = {"field": field, "op": op}
    if ref is None:
        out["value"] = value
    else:
        out.update(ref=ref, factor=factor, plus=plus)
    return out


M = 1 + porte.MARGIN
REGISTRY: Tuple[Dict[str, Any], ...] = (
    _pol("POL-LIVE-ARMED", "Mode réel armé par vous", "AUTONOMY", "SECURITY",
         {"any": [_f("live", "==", False), _f("live_armed", "==", True)]}, "REQUIRE_HUMAN_APPROVAL",
         "Mode réel armé", "LIVE_NOT_ARMED"),
    _pol("POL-LIVE-GATE", "Porte du réel ouverte", "AUTONOMY", "SECURITY",
         {"any": [_f("live", "==", False), _f("production_ok", "==", True)]}, "BLOCK", "Porte du réel",
         "LIVE_GATE_CLOSED"),
    _pol("POL-KILL-SWITCH", "Arrêt d'urgence non déclenché", "RISK", "HARD_RISK", _f("halted", "==", False),
         "FREEZE_ACCOUNT", "Arrêt d'urgence", "KILL_SWITCH"),
    _pol("POL-SAFE-MODE", "Mode sûr inactif", "OPERATIONAL", "SECURITY", _f("safe_mode", "==", False), "SAFE_MODE",
         "Mode sûr", "SAFE_MODE"),
    _pol("POL-NO-TRADE-GUARD", "Garde « pas de trade » du jour", "RISK", "HARD_RISK",
         _f("garde_blocked", "==", 0), "NO_TRADE", "Garde du jour", "NO_TRADE_GUARD"),
    _pol("POL-DECISION-DAY", "Décision du jour (données fraîches)", "DATA", "DATA",
         _f("decision_day_ok", "==", True), "BLOCK", "Décision du jour", "STALE_DECISION"),
    _pol("POL-INSTRUMENT", "Crypto de la liste, choisie, sans veto", "INSTRUMENT", "COMPLIANCE",
         {"all": [_f("in_universe", "==", True), _f("allowed", "==", True), _f("vetoed", "==", False)]}, "BLOCK",
         "Crypto autorisée", "INSTRUMENT_NOT_ALLOWED"),
    _pol("POL-DUPLICATE", "Pas de doublon", "ACCOUNT", "ACCOUNT",
         {"all": [_f("held", "==", False), _f("bought_today", "==", False)]}, "BLOCK", "Pas de doublon", "DUPLICATE"),
    _pol("POL-MAX-POSITIONS", "Nombre de positions", "PORTFOLIO", "PORTFOLIO",
         _f("positions_after", "<=", ref="max_positions"), "BLOCK", "Nombre de positions", "MAX_POSITIONS"),
    _pol("POL-TRADE-RISK", "Risque de l'achat", "RISK", "HARD_RISK",
         {"all": [_f("equity", ">", 0), _f("risk_quote", "<=", ref="risk_cap", factor=M, plus=1e-9)]}, "BLOCK",
         "Risque de l'achat", "TRADE_RISK"),
    _pol("POL-TOTAL-RISK", "Risque cumulé", "RISK", "HARD_RISK",
         {"all": [_f("equity", ">", 0), _f("open_risk_after", "<=", ref="total_cap", factor=M, plus=1e-9)]}, "BLOCK",
         "Risque cumulé", "TOTAL_RISK"),
    _pol("POL-POSITION-SIZE", "Taille de la position", "PORTFOLIO", "PORTFOLIO",
         {"all": [_f("equity", ">", 0), _f("cost", "<=", ref="size_cap", factor=M, plus=1e-9)]}, "BLOCK",
         "Taille de la position", "POSITION_SIZE"),
    _pol("POL-CASH", "Argent disponible (ni levier ni emprunt)", "LEVERAGE", "ACCOUNT",
         _f("cost", "<=", ref="cash", factor=1 + 1e-9, plus=1e-6), "BLOCK", "Argent disponible", "NO_LEVERAGE"),
    _pol("POL-STOP", "Stop sous le prix d'achat", "STRATEGY", "STRATEGY", _f("stop", "<", ref="entry"), "BLOCK",
         "Stop sous le prix", "STOP_ABOVE_PRICE"),
    _pol("POL-MIN-NOTIONAL", "Montant minimum", "LIQUIDITY", "STRATEGY",
         _f("notional", ">=", ref="min_notional", factor=1 - 1e-9), "BLOCK", "Montant minimum", "MIN_NOTIONAL"),
    _pol("POL-DATA-QUALITY", "Qualité des données du jour", "DATA", "DATA", _f("data_quality", ">=", porte.QUALITY_MIN),
         "BLOCK", "Qualité des données", "DATA_QUALITY"),
    _pol("POL-RISK-ENGINE", "Évaluation du jour du moteur de risque", "MODEL", "HARD_RISK",
         _f("risk_engine_ok", "==", True), "SAFE_MODE", "Moteur de risque", "RISK_ENGINE"),
    _pol("POL-EXCHANGE-RULES", "Règles de Binance pour cet achat (réel)", "INSTRUMENT", "COMPLIANCE",
         _f("instrument_ok", "==", True), "BLOCK", "Instrument négociable", "EXCHANGE_RULES"),
    _pol("POL-LIVE-STAGE", "Plafonds du palier du réel (réel contrôlé, production limitée)", "AUTONOMY", "SECURITY",
         _f("stage_ok", "==", True), "BLOCK", "Plafonds du palier", "LIVE_STAGE_LIMITS"),
    _pol("POL-DRAWDOWN-THROTTLE", "Profil prudent : risque réduit après une baisse", "RISK", "SOFT",
         _f("risk_mult", ">=", 1.0), "REDUCE_SIZE", "", "DRAWDOWN_THROTTLE", severity="SOFT"),
    _pol("POL-EVENT", "Grande annonce dans les 48 heures (prudence, effet non prouvé)", "EVENT", "SOFT",
         _f("events_soon", "==", 0), "ALLOW_WITH_LIMITS", "", "EVENT_RISK", severity="SOFT"),
)


# ══════════════════════════════════════════════════════════════════════
# Contexte, évaluation, décision (§7, §34-41)
# ══════════════════════════════════════════════════════════════════════

def context(intent: OrderIntent, pf: porte.Portfolio, p: Any, events_soon: int = 0) -> Dict[str, Any]:
    """Le contexte d'une politique (§7) : tout ce que les conditions ont le
    droit de lire, rien d'autre (liste blanche)."""
    eq = pf.equity
    held = dict(pf.held_risk)
    return {"live": pf.live, "live_armed": pf.live_armed,
            "production_ok": pf.production is None or bool(pf.production[0]), "halted": pf.halted,
            "safe_mode": pf.safe_mode.active, "garde_blocked": len(pf.garde_blocked),
            "decision_day_ok": intent.decision_day == pf.expected_day, "in_universe": intent.asset in pf.universe,
            "allowed": intent.asset in pf.allowed, "vetoed": intent.asset in pf.vetoed, "held": intent.asset in held,
            "bought_today": intent.idempotency_key in pf.bought_today, "positions_after": len(held) + 1,
            "max_positions": p.max_positions, "equity": eq, "risk_quote": intent.risk_quote,
            "risk_cap": p.risk_pct * eq * pf.risk_mult, "open_risk_after": sum(held.values()) + intent.risk_quote,
            "total_cap": p.max_total_risk * eq * pf.risk_mult, "cost": intent.cost,
            "size_cap": p.max_position_pct * eq * (1 + p.fee), "cash": pf.cash, "stop": intent.stop,
            "entry": intent.entry, "notional": intent.qty * intent.entry, "min_notional": porte.MIN_NOTIONAL,
            "data_quality": porte.QUALITY_MIN if pf.data_quality is None else pf.data_quality,
            "risk_engine_ok": pf.risk_engine is None or bool(pf.risk_engine[0]), "risk_mult": pf.risk_mult,
            "instrument_ok": pf.instrument is None or bool(pf.instrument[0]),
            "stage_ok": pf.stage is None or bool(pf.stage[0]), "events_soon": int(events_soon)}


def holds(cond: Dict[str, Any], ctx: Dict[str, Any], seen: List[Tuple[str, Any, Any]]) -> bool:
    """La condition tient-elle ? Langage fermé : all, any, not, comparaison
    d'un champ du contexte à une valeur ou à un autre champ (× facteur +
    marge). Champ ou opérateur inconnu : ContractError (la politique ne peut
    pas être évaluée, l'achat est bloqué)."""
    if "all" in cond:
        return all([holds(c, ctx, seen) for c in cond["all"]])
    if "any" in cond:
        return any([holds(c, ctx, seen) for c in cond["any"]])
    if "not" in cond:
        return not holds(cond["not"], ctx, seen)
    field, op = cond.get("field"), cond.get("op")
    if field not in ctx or op not in OPS:
        raise ContractError("INVALID_FIELD", f"politique illisible : champ {field!r} ou opérateur {op!r} inconnu")
    value = ctx[field]
    if "ref" in cond:
        if cond["ref"] not in ctx:
            raise ContractError("INVALID_FIELD", f"politique illisible : champ {cond['ref']!r} inconnu")
        target = ctx[cond["ref"]] * cond.get("factor", 1.0) + cond.get("plus", 0.0)
    else:
        target = cond.get("value")
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("OUT_OF_RANGE", f"politique illisible : {field} n'est pas un nombre fini")
    seen.append((field, value, target))
    return bool(OPS[op](value, target))


def evaluate(intent: OrderIntent, pf: porte.Portfolio, p: Any, now: datetime, events_soon: int = 0,
             registry: Sequence[Dict[str, Any]] = REGISTRY) -> PolicyDecision:
    """La décision de politique d'une intention d'achat (§35-41) : chaque
    politique active évaluée ; une politique illisible bloque (fail-closed) ;
    l'action la plus grave l'emporte ; explication de chaque politique qui ne
    tient pas (observé, seuil, version)."""
    ctx = context(intent, pf, p, events_soon)
    violations, warnings, evaluated, actions = [], [], [], []
    for pol in registry:
        if pol["status"] != "ACTIVE":
            continue
        evaluated.append(f"{pol['id']} v{pol['version']}")
        seen: List[Tuple[str, Any, Any]] = []
        try:
            ok = holds(pol["require"], ctx, seen)
        except ContractError as e:
            ok, seen = False, [("politique", str(e), "lisible")]
            action = "BLOCK"
        else:
            action = pol["action"]
        if ok:
            continue
        observed = " ; ".join(f"{name} = {_show(v)} (seuil {_show(t)})" for name, v, t in seen[-2:])
        text = f"{pol['id']} v{pol['version']} : {pol['name']} — {observed} → {ACTION_FR[action]}"
        (violations if pol["severity"] == "HARD" or action == "BLOCK" else warnings).append(text)
        actions.append(action)
    decision = max(actions, key=lambda a: SEVERITY[a]) if actions else "ALLOW"
    return PolicyDecision(decision_id=str(uuid.uuid4()), asset=intent.asset, decision=decision,
                          registry_version=REGISTRY_VERSION, evaluated=tuple(evaluated), violations=tuple(violations),
                          warnings=tuple(warnings), created_at=now.isoformat(timespec="seconds"),
                          expires_at=(now + timedelta(seconds=TTL_SECONDS)).isoformat(timespec="seconds"))


def _show(v: Any) -> str:
    if isinstance(v, bool):
        return "oui" if v else "non"
    if isinstance(v, float):
        return fr(v, ".4g")
    return str(v)


def porte_status(decision: PolicyDecision) -> str:
    """Ce que la porte devrait dire pour cette décision (équivalence) :
    APPROVED, REJECTED ou EMERGENCY_BLOCK."""
    if decision.decision in EMERGENCY_ACTIONS:
        return "EMERGENCY_BLOCK"
    if SEVERITY[decision.decision] >= SEVERITY["NO_TRADE"]:
        return "REJECTED"
    return "APPROVED"


def conflicts(registry: Sequence[Dict[str, Any]] = REGISTRY) -> List[str]:
    """Défauts du registre (§34, §47-48) : identifiant en double, version,
    type, catégorie, action ou gravité inconnus, politique HARD sans lien à la
    porte, condition illisible."""
    out, seen = [], set()
    probe = context(OrderIntent(asset="eth", qty=1.0, entry=100.0, stop=90.0, cost=100.1, risk_quote=10.2,
                                decision_day="2026-01-01"),
                    porte.Portfolio(equity=1000.0, cash=1000.0, invested=0.0, held_risk=(), risk_mult=1.0,
                                    expected_day="2026-01-01", universe=frozenset({"eth"}),
                                    allowed=frozenset({"eth"})), ts.TrendParams())
    for pol in registry:
        if pol["id"] in seen:
            out.append(f"{pol['id']} : identifiant en double")
        seen.add(pol["id"])
        if pol["type"] not in TYPES or pol["category"] not in CATEGORIES or pol["action"] not in ACTIONS:
            out.append(f"{pol['id']} : type, catégorie ou action inconnus")
        if pol["severity"] == "HARD" and not pol["porte"]:
            out.append(f"{pol['id']} : politique bloquante sans contrôle de la porte")
        try:
            holds(pol["require"], probe, [])
        except ContractError as e:
            out.append(f"{pol['id']} : {e}")
    names = {name for name, _ok, _d in porte._limits(
        OrderIntent(asset="eth", qty=1.0, entry=100.0, stop=90.0, cost=100.1, risk_quote=10.2,
                    decision_day="2026-01-01"),
        porte.Portfolio(equity=1000.0, cash=1000.0, invested=0.0, held_risk=(), risk_mult=1.0,
                        expected_day="2026-01-01", universe=frozenset({"eth"}), allowed=frozenset({"eth"})),
        ts.TrendParams())}
    covered = {pol["porte"] for pol in registry if pol["porte"]}
    out += [f"contrôle de la porte sans politique : {n}" for n in sorted(names - covered)]
    return out


def compact(decision: PolicyDecision, status: str) -> Dict[str, Any]:
    """Résumé gardé dans l'état du bot (un achat évalué)."""
    return {"asset": decision.asset, "decision": decision.decision, "porte": status,
            "agree": porte_status(decision) == status, "violations": list(decision.violations[:3]),
            "warnings": list(decision.warnings[:3])}


def describe(view: Optional[Dict[str, Any]]) -> str:
    """Une phrase pour le raisonnement, le rapport et Rachelle."""
    if not view or not view.get("checked"):
        return "aucun achat évalué par les politiques aujourd'hui"
    text = (f"{view['checked']} achat(s) évalué(s) par {len(REGISTRY)} politiques ({REGISTRY_VERSION}) ; "
            + ("ÉCART avec la porte d'exécution : " + ", ".join(a.upper() for a in view["mismatch"])
               if view.get("mismatch") else "décisions identiques à celles de la porte d'exécution"))
    return text


# ══════════════════════════════════════════════════════════════════════
# Simulation des politiques de risque sur l'historique (§38-40)
# ══════════════════════════════════════════════════════════════════════

def _kill_hook(threshold: float) -> ts.BacktestHooks:
    hit = {"on": False}

    def scale(_i: int, equity: float, peak: float) -> float:
        if peak > 0 and equity / peak - 1 <= -threshold:
            hit["on"] = True
        return 0.0 if hit["on"] else 1.0

    return ts.BacktestHooks(risk_scale=scale)


def simulate(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams) -> Dict[str, Any]:
    """Que se serait-il passé avec d'autres politiques de risque (§38-40) ?
    Les réglages en vigueur, ceux de la recherche (8 positions, 6 %), sans
    profil prudent, et un arrêt d'urgence à −30 % (il reste ensuite
    déclenché, comme dans le bot) : rendement, baisse, Calmar, trades sur les
    deux époques. Une politique ne change que par votre décision."""
    import dataclasses
    pre = ts.precompute(close, volume, p)
    periods = [mb.IS_PERIOD, (mb.OOS_START, str(close.index[-1].date()))]
    variants = [("en vigueur", p, None),
                ("recherche : 8 positions, 6 % de risque cumulé",
                 dataclasses.replace(p, max_positions=8, max_total_risk=0.06), None),
                ("sans profil prudent", dataclasses.replace(p, dd_throttle=()), None),
                ("arrêt d'urgence à −30 % (au lieu de −40 %)", p, 0.30)]
    out = []
    for label, q, kill in variants:
        rows = []
        for a, b in periods:
            res = ts.backtest(close, volume, q, a, b, pre=pre if q.breakout_n == p.breakout_n else None,
                              hooks=_kill_hook(kill) if kill else None)
            m = res.metrics
            rows.append({"cagr": m.get("cagr_pct", 0.0), "max_dd": m.get("max_dd_pct", 0.0),
                         "calmar": m.get("calmar", 0.0), "trades": m.get("trades", 0)})
        out.append({"label": label, "rows": rows})
    return {"periods": periods, "variants": out}


def render_registry() -> str:
    """Le registre des politiques, lisible."""
    lines = [f"Registre {REGISTRY_VERSION} : {len(REGISTRY)} politiques (la porte d'exécution les applique ; "
             "aucune dérogation silencieuse).", ""]
    for pol in REGISTRY:
        lines.append(f"- {pol['id']} v{pol['version']} [{pol['severity']}, {pol['category']}] {pol['name']} : "
                     f"sinon {ACTION_FR[pol['action']]}" + (f" (porte : {pol['porte']})" if pol["porte"] else ""))
    problems = conflicts()
    lines += ["", "Registre cohérent : chaque contrôle de la porte a sa politique." if not problems
              else "Défauts : " + " ; ".join(problems)]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `politique` : le registre (défaut) ou la simulation des
    politiques de risque sur l'historique."""
    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    ap = argparse.ArgumentParser(description="Moteur de politiques de TrendGuard")
    ap.add_argument("action", nargs="?", default="registre", choices=["registre", "simulation"])
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    if args.action == "registre":
        print(render_registry())
        return 0 if not conflicts() else 1
    g = load_guard_config_from_env()
    close, volume = load_history(args.cache, list(g.universe), max_age_days=None)
    s = simulate(close, volume, params_for(g))
    (a1, b1), (a2, b2) = s["periods"]
    for v in s["variants"]:
        cells = [f"{a1[:4]}-{b1[:4]} : {fr(v['rows'][0]['cagr'], '+.1f')} %/an, baisse {fr(v['rows'][0]['max_dd'], '.1f')} "
                 f"%, Calmar {fr(v['rows'][0]['calmar'], '.2f')}",
                 f"depuis {a2[:4]} : {fr(v['rows'][1]['cagr'], '+.1f')} %/an, baisse {fr(v['rows'][1]['max_dd'], '.1f')} "
                 f"%, Calmar {fr(v['rows'][1]['calmar'], '.2f')}"]
        print(f"{v['label']} — " + " ; ".join(cells))
    return 0
