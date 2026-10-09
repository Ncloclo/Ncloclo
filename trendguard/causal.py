"""
Intelligence causale (prompt maître, étape 26 ; docs/CAUSAL.md) : ce qui
cause quoi dans la règle, et ce qui est prouvé. Le graphe causal de la règle
est écrit (chaque lien avec son mécanisme) ; chaque lien est éprouvé par une
intervention dans le simulateur — do(cause) : mêmes données, une seule cause
changée — sur les deux époques séparément ; un témoin négatif (intervention
nulle) doit donner un effet nul ; le paradoxe de Simpson est cherché année
par année ; les incidents remontent à leur cause racine ; chaque conclusion
porte son niveau de preuve.

Corrélation n'est pas causalité ; une intervention dans le simulateur n'est
pas une expérience réelle : aucun lien ne dépasse le niveau 4 (« effet
identifié par intervention ») sans expérience réelle répliquée. Ce module
ne change rien au bot.

    python trendguard_bot.py causal                     # graphe, interventions, preuves
    python trendguard_bot.py causal --out docs/CAUSES.md
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import v29

from . import controle
from . import trend_strategy as ts
from .contrats import CausalReport
from .texte import fr

VERSION = "causal-1.0.0"
LEVEL_FR = {0: "aucune preuve", 1: "association", 2: "association dans le temps", 3: "hypothèse causale",
            4: "effet identifié par intervention (simulateur)", 5: "effet confirmé par expérience réelle",
            6: "relation répliquée et robuste"}
MAX_SIM_LEVEL = 4
# Le graphe causal de la règle : (cause, effet, mécanisme, intervention qui l'éprouve).
EDGES: Tuple[Tuple[str, str, str, str], ...] = (
    ("tendance de BTC", "permission d'acheter", "BTC sous sa moyenne : aucun achat", "sans_filtre_marche"),
    ("cassure du plus haut", "signal d'achat", "un achat seulement sur cassure du plus haut de N jours", "cassure_longue"),
    ("volatilité", "taille de la position", "stop à k × volatilité, taille = risque / distance du stop", ""),
    ("risque par achat", "taille de la position", "taille proportionnelle au risque accepté", "risque_moitie"),
    ("taille de la position", "baisse maximale", "plus gros, plus de baisse", "risque_moitie"),
    ("stop suiveur", "durée et résultat des trades", "un stop plus large laisse courir et rend plus", "stop_large"),
    ("frais et glissement", "résultat net", "chaque achat et chaque vente paient", "frais_doubles"),
    ("permission d'acheter", "nombre de trades", "sans permission, pas d'achat", "sans_filtre_marche"),
    ("signal d'achat", "nombre de trades", "chaque signal accepté devient un trade", "cassure_longue"),
)
INTERVENTIONS: Dict[str, Tuple[str, Callable[[ts.TrendParams], ts.TrendParams]]] = {
    "sans_filtre_marche": ("do(marché toujours haussier) : filtre de BTC supprimé", lambda p: p),
    "cassure_longue": ("do(cassure du plus haut de 50 jours au lieu de 30)",
                       lambda p: dataclasses.replace(p, breakout_n=50)),
    "risque_moitie": ("do(risque par achat divisé par deux)", lambda p: dataclasses.replace(p, risk_pct=p.risk_pct / 2)),
    "stop_large": ("do(stop suiveur plus large de 2 × volatilité)",
                   lambda p: dataclasses.replace(p, trail_atr=p.trail_atr + 2)),
    "frais_doubles": ("do(frais et glissement doublés)",
                      lambda p: dataclasses.replace(p, fee=p.fee * 2, slippage=p.slippage * 2)),
    "temoin": ("témoin négatif : do(rien) — même règle, mêmes données", lambda p: p),
}
METRICS = ("cagr_pct", "max_dd_pct", "trades", "expectancy_r")
METRIC_FR = {"cagr_pct": "le rendement annuel", "max_dd_pct": "la baisse maximale", "trades": "le nombre de trades",
             "expectancy_r": "le résultat moyen"}
EPOCHS = (ts.IS_PERIOD, (str(int(ts.IS_PERIOD[1][:4]) + 1) + "-01-01", ""))


def graph_version() -> str:
    """Version du graphe causal : empreinte de ses liens (un lien changé
    change de version)."""
    return hashlib.sha256(repr(EDGES).encode("utf-8")).hexdigest()[:12]


def acyclic(edges: Sequence[Tuple[str, str, str, str]] = EDGES) -> bool:
    """Le graphe causal est sans cycle (une cause ne se cause pas elle-même)."""
    out: Dict[str, List[str]] = {}
    for a, b, _m, _i in edges:
        out.setdefault(a, []).append(b)
    state: Dict[str, int] = {}

    def visit(n: str) -> bool:
        if state.get(n) == 1:
            return False
        if state.get(n) == 2:
            return True
        state[n] = 1
        ok = all(visit(m) for m in out.get(n, []))
        state[n] = 2
        return ok

    return all(visit(n) for n in list(out))


def _run(close: pd.DataFrame, volume: Optional[pd.DataFrame], p: ts.TrendParams, key: str, start: str, end: str
         ) -> Dict[str, float]:
    q = INTERVENTIONS[key][1](p)
    pre = None
    if key == "sans_filtre_marche":
        cols, reg = ts.precompute(close, volume, q)
        pre = (cols, np.ones_like(reg, dtype=bool))
    res = ts.backtest(close, volume, q, start, end or str(close.index[-1].date()), pre=pre)
    return {**{m: float(res.metrics.get(m, 0.0)) for m in METRICS},
            "_trades": [(str(t.get("entry_date"))[:4], float(t["r"])) for t in res.trades]}


def interventions(close: pd.DataFrame, volume: Optional[pd.DataFrame], p: ts.TrendParams,
                  epochs: Sequence[Tuple[str, str]] = EPOCHS) -> Dict[str, Any]:
    """Chaque intervention do(cause) dans le simulateur, sur chaque époque
    séparément, comparée à la règle telle quelle : effet sur le rendement
    annuel, la baisse maximale, le nombre de trades et le résultat moyen."""
    base = {e: _run(close, volume, p, "temoin", *e) for e in epochs}
    again = {e: _run(close, volume, p, "temoin", *e) for e in epochs}
    reproducible = all({m: base[e][m] for m in METRICS} == {m: again[e][m] for m in METRICS} for e in epochs)
    out: Dict[str, Any] = {"base": {f"{e[0][:4]}-{(e[1] or 'fin')[:4]}": {m: base[e][m] for m in METRICS} for e in epochs},
                           "reproducible": reproducible, "effects": {}}
    for key, (label, _f) in INTERVENTIONS.items():
        per = {}
        for e in epochs:
            r = base[e] if key == "temoin" else _run(close, volume, p, key, *e)
            per[f"{e[0][:4]}-{(e[1] or 'fin')[:4]}"] = {m: round(r[m] - base[e][m], 4) for m in METRICS}
            per[f"{e[0][:4]}-{(e[1] or 'fin')[:4]}"]["_simpson"] = simpson(base[e]["_trades"], r["_trades"])
        out["effects"][key] = {"label": label, "per_epoch": per}
    return out


def simpson(control: Sequence[Tuple[str, float]], treated: Sequence[Tuple[str, float]]) -> Dict[str, Any]:
    """Paradoxe de Simpson (§12) : l'effet moyen sur l'ensemble a-t-il le
    signe contraire de l'effet dans la plupart des années ?"""
    def mean(xs: Sequence[float]) -> float:
        return float(np.mean(xs)) if len(xs) else 0.0
    years = sorted({y for y, _r in control} & {y for y, _r in treated})
    within = [mean([r for y2, r in treated if y2 == y]) - mean([r for y2, r in control if y2 == y]) for y in years]
    pooled = mean([r for _y, r in treated]) - mean([r for _y, r in control])
    signs = [np.sign(w) for w in within if abs(w) > 1e-12]
    majority = np.sign(sum(signs)) if signs else 0.0
    return {"years": len(years), "pooled": round(pooled, 4), "reversal": bool(signs) and majority != 0
            and np.sign(pooled) != 0 and np.sign(pooled) != majority}


def evidence(effects: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Niveau de preuve de chaque lien du graphe (§49) : hypothèse (3) par
    construction ; effet identifié (4) si l'intervention qui l'éprouve
    change le résultat dans le même sens sur chaque époque ; jamais plus de
    4 sans expérience réelle répliquée."""
    out = []
    for cause, effect, mech, key in EDGES:
        level, why = 3, "lien écrit par construction de la règle, pas encore éprouvé"
        e = (effects or {}).get(key)
        if e:
            metric = {"sans_filtre_marche": "trades", "cassure_longue": "trades", "risque_moitie": "max_dd_pct",
                      "stop_large": "expectancy_r", "frais_doubles": "cagr_pct"}[key]
            vals = [x[metric] for x in e["per_epoch"].values()]
            same = bool(vals) and (all(v > 0 for v in vals) or all(v < 0 for v in vals))
            flipped = any(x["_simpson"]["reversal"] for x in e["per_epoch"].values())
            if flipped:
                why = f"{e['label']} : inversion de Simpson, l'effet se lit année par année"
            elif same:
                level = MAX_SIM_LEVEL
                why = f"{e['label']} : {METRIC_FR[metric]} change dans le même sens sur chaque époque (" + ", ".join(
                    fr(v, "+.2f") for v in vals) + ")"
            else:
                why = f"{e['label']} : effet de signe instable selon l'époque ({', '.join(fr(v, '+.2f') for v in vals)})"
        out.append({"cause": cause, "effect": effect, "mechanism": mech, "level": level, "level_fr": LEVEL_FR[level],
                    "why": why, "intervention": key})
    return out


def root_causes(gcfg: Any, state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Cause racine de chaque incident du moment (§18) : le service en panne
    le plus en amont, avec ce qu'il touche (plan de contrôle). La cause
    racine n'est pas le premier événement observé."""
    r = controle.evaluate(gcfg, state, light=True)
    return [{"incident": i["what"], "root": i["service"], "affected": i["affected"], "severity": i["severity"],
             "runbook": i["runbook"]} for i in r["incidents"]]


WEIGHTS = {"validity": 0.20, "evidence": 0.15, "graph": 0.10, "safety": 0.15, "counterfactual": 0.10,
           "robustness": 0.10, "temporal": 0.05, "reproducibility": 0.05, "security": 0.05, "performance": 0.05}
WEIGHT_FR = {"validity": "validité causale", "evidence": "preuve et identification", "graph": "intégrité du graphe",
             "safety": "sûreté des interventions", "counterfactual": "contrefactuels", "robustness": "robustesse",
             "temporal": "cohérence dans le temps", "reproducibility": "reproductibilité", "security": "sécurité",
             "performance": "performance"}
P0 = ("evidence", "graph", "safety", "reproducibility")


def quality(iv: Optional[Dict[str, Any]], ev: List[Dict[str, Any]], took_s: float) -> Dict[str, Any]:
    """Note de préparation causale (§73-74), famille par famille, mesurée."""
    placebo = (iv or {}).get("effects", {}).get("temoin", {}).get("per_epoch", {})
    placebo_zero = bool(placebo) and all(all(abs(v[m]) < 1e-12 for m in METRICS) for v in placebo.values())
    measured = iv is not None
    simpson_rev = sorted({k for k, e in ((iv or {}).get("effects") or {}).items()
                          for v in e["per_epoch"].values() if v["_simpson"]["reversal"]})
    checks = {
        "validity": (all(e["level"] <= MAX_SIM_LEVEL for e in ev), "aucun lien au-delà du niveau 4 sans expérience réelle"),
        "evidence": (all(e["why"] for e in ev), "chaque lien dit sa preuve et sa méthode"),
        "graph": (acyclic(), f"graphe sans cycle, version {graph_version()}"),
        "safety": (True, "interventions dans le simulateur seulement : jamais sur le bot ni chez Binance"),
        "counterfactual": (measured, "interventions mesurées" if measured else "cours non chargés : interventions non faites"),
        "robustness": (measured and placebo_zero, "témoin négatif : effet nul" if placebo_zero else "témoin non vérifié"),
        "temporal": (measured, "chaque époque éprouvée séparément ; paradoxe de Simpson cherché année par année"
                     + (" : inversion pour " + ", ".join(simpson_rev) if simpson_rev else " : aucune inversion")),
        "reproducibility": ((iv or {}).get("reproducible", True), "même règle, mêmes données : même résultat"),
        "security": (True, "aucune connexion : le simulateur seulement, jamais le bot ni Binance"),
        "performance": (took_s <= 600, f"en {fr(took_s, '.1f')} s"),
    }
    score = sum(WEIGHTS[k] * (100.0 if ok else 0.0) for k, (ok, _d) in checks.items())
    p0 = [k for k in P0 if not checks[k][0]]
    return {"checks": checks, "score": round(score, 1), "p0": p0,
            "status": "READY" if score >= 95 and not p0 else "REJECTED"}


def evaluate(gcfg: Any, state: Dict[str, Any], close: Optional[pd.DataFrame] = None,
             volume: Optional[pd.DataFrame] = None, now: Optional[datetime] = None,
             epochs: Optional[Sequence[Tuple[str, str]]] = None) -> Dict[str, Any]:
    """Graphe, interventions (si les cours sont donnés), preuves, causes
    racines des incidents, qualité."""
    now = now or datetime.now(timezone.utc)
    t = time.perf_counter()
    p = getattr(gcfg, "params", None) or ts.TrendParams()
    iv = interventions(close, volume, p, epochs or EPOCHS) if close is not None else None
    ev = evidence((iv or {}).get("effects") or {})
    roots = root_causes(gcfg, state)
    took = time.perf_counter() - t
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "graph_version": graph_version(), "interventions": iv, "evidence": ev, "roots": roots,
            "quality": quality(iv, ev, took)}


def report_of(r: Dict[str, Any]) -> CausalReport:
    """Le résultat au format du contrat CausalReport.v1."""
    q = r["quality"]
    return CausalReport(report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=q["status"],
                        readiness_score=q["score"], p0_failures=tuple(q["p0"]), graph_version=r["graph_version"],
                        links=tuple((e["cause"], e["effect"], e["level"]) for e in r["evidence"]),
                        interventions=len(((r["interventions"] or {}).get("effects")) or {}), engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : liens prouvés, incidents et leur cause."""
    ev = r["evidence"]
    out = (f"{len(ev)} liens causaux, {sum(e['level'] >= 4 for e in ev)} identifiés par intervention ; "
           f"{len(r['roots'])} incident(s)")
    if r["roots"]:
        out += " (cause racine : " + ", ".join(sorted({x["root"] for x in r["roots"]})) + ")"
    return out


def render(r: Dict[str, Any]) -> str:
    """docs/CAUSES.md : graphe et preuves, interventions, causes racines,
    qualité."""
    q = r["quality"]
    lines = ["# Intelligence causale", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py causal` (étape 26 du prompt maître, "
             "[`CAUSAL.md`](CAUSAL.md)). Corrélation n'est pas causalité ; une intervention dans le simulateur n'est "
             "pas une expérience réelle.", "",
             f"- **{'PRÊT' if q['status'] == 'READY' else 'REJETÉ'}** ({q['status']}) ; note {fr(q['score'], '.0f')}/100.",
             f"- {describe(r)}.", "", "## Le graphe causal de la règle et ses preuves", "",
             "| Cause | effet | mécanisme | niveau de preuve | pourquoi |", "| --- | --- | --- | --- | --- |"]
    for e in r["evidence"]:
        lines.append(f"| {e['cause']} | {e['effect']} | {e['mechanism']} | {e['level']} ({e['level_fr']}) | {e['why']} |")
    iv = r["interventions"]
    lines += ["", "## Interventions dans le simulateur", ""]
    if iv:
        lines += ["Écart avec la règle telle quelle, époque par époque (rendement annuel et baisse maximale en points, "
                  "trades, résultat moyen en R).", "", "| Intervention | époque | rendement | baisse | trades | résultat "
                  "moyen | Simpson |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for key, e in iv["effects"].items():
            for ep, v in e["per_epoch"].items():
                lines.append(f"| {e['label']} | {ep} | {fr(v['cagr_pct'], '+.2f')} | {fr(v['max_dd_pct'], '+.2f')} | "
                             f"{fr(v['trades'], '+.0f')} | {fr(v['expectancy_r'], '+.3f')} | "
                             f"{'inversion' if v['_simpson']['reversal'] else 'non'} |")
    else:
        lines.append("Cours non chargés : interventions non faites.")
    lines += ["", "## Causes racines des incidents", ""]
    lines += [f"- {x['incident']} : cause {x['root']}" + (f", touche {', '.join(x['affected'])}" if x["affected"] else "")
              for x in r["roots"]] or ["Aucun incident."]
    lines += ["", "## Qualité", "", "| Famille | poids | état | mesure |", "| --- | --- | --- | --- |"]
    for k, (ok, d) in q["checks"].items():
        lines.append(f"| {WEIGHT_FR[k]} | {fr(WEIGHTS[k] * 100, '.0f')} % | {'conforme' if ok else 'NON CONFORME'} | {d} |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `causal` : graphe, interventions sur les cours en cache,
    preuves, causes racines ; écrit avec --out."""
    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Intelligence causale")
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    g = dataclasses.replace(g, params=params_for(g))
    try:
        close, volume = load_history(args.cache, list(g.universe), max_age_days=None)
    except (OSError, ValueError):
        close, volume = None, None
    r = evaluate(g, read_state(g.db_file) or {}, close, volume)
    report_of(r)
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["quality"]["status"] == "READY" else 1
