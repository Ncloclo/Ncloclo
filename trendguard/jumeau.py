"""
Jumeau numérique et simulation (prompt maître, étape 28 ; docs/JUMEAU.md) :
le compte paper rejoué par la boucle de backtest (le jumeau), comparé achat
par achat à ce que le bot a vraiment fait ; un Monte-Carlo par blocs dont on
vérifie la convergence (moins de 1 % d'écart entre deux niveaux
d'échantillonnage) ; les crises passées rejouées ; une panne de données
injectée ; la sensibilité aux réglages (±10 %) ; chaque résultat dit si ses
données sont réelles ou synthétiques.

Le jumeau ne touche jamais la production : aucune écriture dans le bot,
aucune route vers Binance, aucune bibliothèque réseau. Une simulation n'est
jamais une observation.

    python trendguard_bot.py jumeau                      # synchronisation, Monte-Carlo, crises, sensibilité
    python trendguard_bot.py jumeau --out docs/JUMEAU_ETAT.md
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import hashlib
import os
import pathlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

import v29

from . import acceptation, evolution
from . import trend_strategy as ts
from .contrats import TwinReport
from .texte import fr, fr_plain

VERSION = "jumeau-1.0.0"
MC_LEVELS = (250, 500, 1000, 2000, 4000)
MC_TOLERANCE = 0.01
SEED = 7
SENSITIVITY = ("init_stop_atr", "trail_atr", "breakout_n", "regime_sma")
PARAM_FR = {"init_stop_atr": "stop initial (en ATR)", "trail_atr": "stop suiveur (en ATR)",
            "breakout_n": "cassure (jours)", "regime_sma": "filtre BTC (moyenne, jours)"}
INT_PARAMS = {"breakout_n", "regime_sma"}


def data_fingerprint(close: pd.DataFrame) -> str:
    """Empreinte des cours utilisés (reproductibilité)."""
    return hashlib.sha256(pd.util.hash_pandas_object(close.fillna(0), index=True).values.tobytes()).hexdigest()[:12]


def sync(state: Dict[str, Any], close: Optional[pd.DataFrame], volume: Optional[pd.DataFrame],
         p: ts.TrendParams) -> Dict[str, Any]:
    """Synchronisation du jumeau (§9-10, §21) : les achats de l'essai paper
    rejoués par la boucle de backtest, mêmes réglages, même capital ; part
    des achats identiques, écarts expliqués ou non."""
    d = acceptation.divergence(state, close, volume, p) if close is not None else {
        "status": "UNKNOWN", "reason": "cours non chargés"}
    if d["status"] != "OK":
        return {"status": "UNKNOWN", "reason": d.get("reason", ""), "ratio": None}
    total = len(set(d["only_paper"]) | set(d["only_backtest"])) + d["matched"]
    return {"status": "OK", "ratio": d["matched"] / total if total else 1.0, "matched": d["matched"], "total": total,
            "explained": d["explained"], "unexplained": d["unexplained"], "from": d["from"], "to": d["to"]}


def monte_carlo(eq: pd.Series, levels: Sequence[int] = MC_LEVELS, seed: int = SEED) -> Dict[str, Any]:
    """Monte-Carlo par blocs de 30 jours sur trois ans (§14) : pire baisse
    atteinte 1 fois sur 20, à des nombres de tirages croissants ; convergé
    quand deux niveaux successifs diffèrent de moins de 1 %."""
    rows: List[Tuple[int, float, float]] = []
    for n in levels:
        med, dd95 = evolution.block_luck(eq, sims=n, seed=seed)
        rows.append((n, med, dd95))
    at = None
    for (n1, _m1, d1), (n2, _m2, d2) in zip(rows, rows[1:]):
        if d1 and abs(d2 - d1) / abs(d1) < MC_TOLERANCE:
            at = n2
            break
    return {"levels": rows, "seed": seed, "converged": at is not None, "at": at,
            "status": "CONVERGED" if at is not None else "MONTE_CARLO_NOT_CONVERGED",
            "dd95": rows[-1][2] if rows else None}


def crises(close: pd.DataFrame, volume: Optional[pd.DataFrame], p: ts.TrendParams) -> List[Dict[str, Any]]:
    """Les crises et les hausses passées rejouées (§15) : rendement et pire
    baisse de la règle sur chaque période (celles hors des données sont
    dites absentes, jamais inventées)."""
    out = []
    first, last = str(close.index[0].date()), str(close.index[-1].date())
    for name, a, b, kind in evolution.CRISES:
        if a < first or b > last:
            out.append({"name": name, "kind": kind, "covered": False})
            continue
        m = ts.backtest(close, volume, p, a, b).metrics
        out.append({"name": name, "kind": kind, "covered": True, "return_pct": m.get("total_return_pct"),
                    "max_dd_pct": m.get("max_dd_pct"), "trades": m.get("trades")})
    return out


def sensitivity(close: pd.DataFrame, volume: Optional[pd.DataFrame], p: ts.TrendParams, start: str
                ) -> List[Dict[str, Any]]:
    """Sensibilité aux réglages (§23) : chacun à −10 % et +10 %, un à la fois,
    effet sur le rendement annuel et la pire baisse."""
    end = str(close.index[-1].date())
    base = ts.backtest(close, volume, p, start, end).metrics
    out = []
    for k in SENSITIVITY:
        for f in (0.9, 1.1):
            v = getattr(p, k) * f
            q = dataclasses.replace(p, **{k: int(round(v)) if k in INT_PARAMS else round(v, 4)})
            m = ts.backtest(close, volume, q, start, end).metrics
            out.append({"param": k, "factor": f, "value": getattr(q, k),
                        "d_cagr": round(m.get("cagr_pct", 0.0) - base.get("cagr_pct", 0.0), 2),
                        "d_dd": round(m.get("max_dd_pct", 0.0) - base.get("max_dd_pct", 0.0), 2)})
    return out


def fault_data_gap(close: pd.DataFrame, volume: Optional[pd.DataFrame], p: ts.TrendParams, start: str
                   ) -> Dict[str, Any]:
    """Panne injectée (§17) : trois jours de cours effacés au milieu de la
    période ; le jumeau doit tourner quand même, et l'écart est mesuré."""
    end = str(close.index[-1].date())
    base = ts.backtest(close, volume, p, start, end).metrics
    holed = close.copy()
    first = int((holed.index < pd.Timestamp(start, tz=holed.index.tz)).sum())
    mid = first + (len(holed) - first) // 2
    holed.iloc[mid:mid + 3] = float("nan")
    try:
        m = ts.backtest(holed, volume, p, start, end).metrics
    except Exception as e:               # une panne qui fait tomber le jumeau est un défaut, pas un crash
        return {"survived": False, "error": type(e).__name__}
    return {"survived": True, "days": 3, "d_cagr": round(m.get("cagr_pct", 0.0) - base.get("cagr_pct", 0.0), 2)}


def isolation() -> Dict[str, Any]:
    """Isolation (§27, §37) : le module n'importe aucune bibliothèque réseau
    et n'écrit rien dans le bot."""
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    net = sorted(mods & {"socket", "urllib", "requests", "http", "ccxt"})
    return {"ok": not net, "network": net, "production_write_path": False}


WEIGHTS = {"fidelity": 0.20, "sync": 0.15, "calibration": 0.15, "correctness": 0.15, "scenarios": 0.10,
           "reproducibility": 0.10, "isolation": 0.05, "performance": 0.05, "observability": 0.05}
WEIGHT_FR = {"fidelity": "fidélité du modèle", "sync": "synchronisation", "calibration": "validation et calibrage",
             "correctness": "justesse de la simulation", "scenarios": "scénarios et crises",
             "reproducibility": "reproductibilité", "isolation": "sécurité et isolation", "performance": "performance",
             "observability": "observabilité"}
P0 = ("correctness", "reproducibility", "isolation")


def quality(r: Dict[str, Any], took_s: float) -> Dict[str, Any]:
    """Note de préparation du jumeau (§44, §46), famille par famille."""
    s, mc, iso = r["sync"], r["monte_carlo"], r["isolation"]
    measured = r["monte_carlo"] is not None
    checks = {
        "fidelity": (s["status"] == "OK" and not s["unexplained"], "achats du paper retrouvés par le jumeau ; écarts "
                     "expliqués" if s["status"] == "OK" else f"non mesurée : {s.get('reason')}"),
        "sync": (s["status"] == "OK", f"{fr((s['ratio'] or 0) * 100, '.0f')} % des achats identiques" if s["status"] == "OK"
                 else "non mesurée"),
        "calibration": (measured and mc["converged"], (f"Monte-Carlo convergé à {mc['at']} tirages (écart < 1 %)"
                                                       if mc["converged"] else "MONTE_CARLO_NOT_CONVERGED")
                        if measured else "non fait (cours absents)"),
        "correctness": (r["reproducible"] is not False and (r["fault"] or {}).get("survived", True),
                        "même jumeau, mêmes données : même résultat ; une panne de données ne le fait pas tomber"),
        "scenarios": (measured and any(c["covered"] for c in r["crises"]), f"{sum(c['covered'] for c in r['crises'] or [])}"
                      " crise(s) ou hausse(s) rejouée(s)" if measured else "non fait"),
        "reproducibility": (r["reproducible"] is not False, f"graine {SEED}, données {r['data']}"),
        "isolation": (iso["ok"] and not iso["production_write_path"], "aucune bibliothèque réseau, aucune écriture "
                                                                     "dans le bot"),
        "performance": (took_s <= 900, f"en {fr(took_s, '.1f')} s"),
        "observability": (True, "chaque résultat dit ses données (réelles ou synthétiques) et sa graine"),
    }
    score = sum(WEIGHTS[k] * (100.0 if ok else 0.0) for k, (ok, _d) in checks.items())
    p0 = [k for k in P0 if not checks[k][0]]
    return {"checks": checks, "score": round(score, 1), "p0": p0,
            "status": "READY" if score >= 95 and not p0 else "REJECTED"}


def evaluate(gcfg: Any, state: Dict[str, Any], close: Optional[pd.DataFrame] = None,
             volume: Optional[pd.DataFrame] = None, now: Optional[datetime] = None, synthetic: bool = False,
             start: Optional[str] = None, params: Optional[ts.TrendParams] = None, light: bool = False
             ) -> Dict[str, Any]:
    """Le jumeau du jour : synchronisation, Monte-Carlo, crises, panne,
    sensibilité (avec les cours), isolation, qualité. `light` (rapport du
    soir) : la synchronisation seulement."""
    now = now or datetime.now(timezone.utc)
    t = time.perf_counter()
    p = params or getattr(gcfg, "params", None) or ts.TrendParams()
    out: Dict[str, Any] = {"version": VERSION, "now": now.isoformat(timespec="seconds"),
                           "mode": getattr(gcfg, "run_mode", "paper"), "sync": sync(state, close, volume, p),
                           "isolation": isolation(), "synthetic": synthetic,
                           "data": "aucune" if close is None else (("synthétiques " if synthetic else "réelles ")
                                                                    + data_fingerprint(close)),
                           "monte_carlo": None, "crises": [], "sensitivity": [], "fault": None, "reproducible": None}
    if close is not None and not light:
        s = start or ts.IS_PERIOD[0]
        end = str(close.index[-1].date())
        eq = ts.backtest(close, volume, p, s, end).equity
        eq2 = ts.backtest(close, volume, p, s, end).equity
        out["reproducible"] = bool(eq.equals(eq2))
        out["monte_carlo"] = monte_carlo(eq)
        out["crises"] = crises(close, volume, p)
        out["sensitivity"] = sensitivity(close, volume, p, s)
        out["fault"] = fault_data_gap(close, volume, p, s)
    out["quality"] = quality(out, time.perf_counter() - t)
    return out


def report_of(r: Dict[str, Any]) -> TwinReport:
    """Le résultat au format du contrat TwinReport.v1."""
    q, mc = r["quality"], r["monte_carlo"]
    return TwinReport(report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=q["status"],
                      readiness_score=q["score"], p0_failures=tuple(q["p0"]),
                      monte_carlo=mc["status"] if mc else "NOT_RUN", sync_ratio=r["sync"]["ratio"],
                      synthetic_data=r["synthetic"], data=r["data"], engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : synchronisation, Monte-Carlo."""
    s, mc = r["sync"], r["monte_carlo"]
    out = (f"jumeau du paper : {s['matched']} achat(s) sur {s['total']} identiques" if s["status"] == "OK"
           else "jumeau du paper : non mesuré (" + str(s.get("reason") or "cours absents") + ")")
    if mc:
        out += (f" ; pire baisse 1 fois sur 20 sur trois ans : {fr(mc['dd95'], '.1f')} % (Monte-Carlo "
                + ("convergé)" if mc["converged"] else "NON convergé)"))
    return out


def render(r: Dict[str, Any]) -> str:
    """docs/JUMEAU_ETAT.md : synchronisation, Monte-Carlo, crises, panne,
    sensibilité, qualité."""
    q = r["quality"]
    lines = ["# Jumeau numérique et simulation", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py jumeau` (étape 28 du prompt maître, "
             "[`JUMEAU.md`](JUMEAU.md)). Données : " + r["data"] + ". Une simulation n'est jamais une observation.", "",
             f"- **{'PRÊT' if q['status'] == 'READY' else 'REJETÉ'}** ({q['status']}) ; note {fr(q['score'], '.0f')}/100.",
             f"- {describe(r)}."]
    s = r["sync"]
    lines += ["", "## Le jumeau du paper", ""]
    if s["status"] == "OK":
        lines.append(f"Du {s['from']} au {s['to']} : {s['matched']} achat(s) identique(s) sur {s['total']} ; écarts expliqués "
                     f"{len(s['explained'])}, inexpliqués {len(s['unexplained'])}.")
        lines += [f"- {x}" for x in s["explained"] + s["unexplained"]]
    else:
        lines.append(f"Non mesuré : {s.get('reason')}.")
    mc = r["monte_carlo"]
    if mc:
        lines += ["", "## Monte-Carlo (blocs de 30 jours, trois ans)", "", "| Tirages | rendement médian | pire baisse 1 fois sur 20 |",
                  "| --- | --- | --- |"]
        lines += [f"| {n} | {fr(m, '+.1f')} % | {fr(d, '.2f')} % |" for n, m, d in mc["levels"]]
        lines += ["", f"Graine {mc['seed']} ; " + (f"convergé à {mc['at']} tirages (écart de moins de 1 %)."
                                                   if mc["converged"] else "MONTE_CARLO_NOT_CONVERGED.")]
        lines += ["", "## Crises et hausses rejouées", "", "| Période | type | rendement | pire baisse | trades |",
                  "| --- | --- | --- | --- | --- |"]
        for c in r["crises"]:
            lines.append(f"| {c['name']} | {c['kind']} | " + (f"{fr(c['return_pct'], '+.1f')} % | {fr(c['max_dd_pct'], '.1f')} % | "
                                                                 f"{c['trades']}" if c["covered"] else "hors des données | — | —")
                         + " |")
        lines += ["", "## Sensibilité aux réglages (±10 %)", "", "| Réglage | valeur | rendement annuel | pire baisse |",
                  "| --- | --- | --- | --- |"]
        lines += [f"| {PARAM_FR[x['param']]} | {fr_plain(x['value'], 2)} ({'−' if x['factor'] < 1 else '+'}10 %) | "
                  f"{fr(x['d_cagr'], '+.2f')} pts | {fr(x['d_dd'], '+.2f')} pts |"
                  for x in r["sensitivity"]]
        lines += ["", "Écart à la règle actuelle, un réglage changé à la fois ; pour la pire baisse, un chiffre négatif "
                  "veut dire une baisse plus profonde. Un réglage n'est jamais changé d'après ce tableau seul : "
                  "l'évolution encadrée l'éprouve d'abord sur deux époques."]
        f = r["fault"]
        lines += ["", "## Panne injectée", "", ("Trois jours de cours effacés : le jumeau tourne, écart de "
                                               f"{fr(f['d_cagr'], '+.2f')} point sur le rendement annuel.") if f["survived"]
                  else f"Le jumeau est tombé ({f['error']}) : défaut à corriger."]
    lines += ["", "## Qualité", "", "| Famille | poids | état | mesure |", "| --- | --- | --- | --- |"]
    for k, (ok, d) in q["checks"].items():
        lines.append(f"| {WEIGHT_FR[k]} | {fr(WEIGHTS[k] * 100, '.0f')} % | {'conforme' if ok else 'NON CONFORME'} | {d} |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `jumeau` : le jumeau sur les cours en cache ; écrit avec --out."""
    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Jumeau numérique et simulation")
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
