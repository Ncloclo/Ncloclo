"""
Moteur de portefeuille (prompt maître, étape 12 ; docs/MOTEUR_PORTEFEUILLE.md)
: l'état du portefeuille à chaque décision, ce que d'autres allocations
auraient donné, et une étude de la taille des achats sur l'historique.

    positions et cours → poids, exposition, liquidités, concentration (HHI,
    nombre effectif), risque jusqu'aux stops contre le plafond, volatilité
    du portefeuille, contributions au risque, diversification → contraintes
    (25 % par position, plafond de risque, nombre de positions ; une
    contrainte impossible est expliquée, jamais relâchée) → optimiseurs
    (parts égales, inverse de la volatilité, variance minimale, parité de
    risque, Sharpe maximal) sur les mêmes cryptos → dérive et rééquilibrage
    (coût contre bénéfice) → décision consultative

La règle dimensionne chaque achat pour risquer la même part du capital
jusqu'à son stop : c'est déjà une parité de risque, position par position. Le
moteur le mesure et le compare ; il ne passe aucun ordre, ne rééquilibre
rien et ne change pas la règle (une autre allocation ne serait proposée,
par l'évolution encadrée, que si elle faisait mieux sur les deux époques).

    python trendguard_bot.py portefeuille                 # l'état du jour
    python trendguard_bot.py portefeuille etude --out docs/PORTEFEUILLE.md
"""

from __future__ import annotations

import argparse
import math
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import v29

from . import moteur_backtest as mb
from . import trend_strategy as ts
from .contrats import PortfolioDecision
from .texte import fr, fr_plain

ENGINE_VERSION = "portefeuille-1.0.0"
COV_DAYS = 90                   # covariance des positions détenues
YEAR = 365
METHODS = ("equal", "inverse_vol", "min_variance", "erc", "max_sharpe")
METHOD_FR = {"rule": "la règle (risque égal jusqu'au stop)", "equal": "parts égales",
             "inverse_vol": "inverse de la volatilité", "min_variance": "variance minimale",
             "erc": "parité de risque (corrélations comprises)", "max_sharpe": "Sharpe maximal"}
SCHEMES = ("regle", "egal", "correlation")
SCHEME_FR = {"regle": "la règle : même risque jusqu'au stop", "egal": "montant égal par achat",
             "correlation": "même risque, divisé par deux si l'achat suit les positions (corrélation > 0,7)"}
CORR_LIMIT = 0.7
MARGIN = 0.10                   # avance de Calmar exigée sur les deux époques pour proposer une autre taille


# ══════════════════════════════════════════════════════════════════════
# Contraintes et optimiseurs (§13-26)
# ══════════════════════════════════════════════════════════════════════

def feasibility(n: int, total: float, cap: float) -> List[str]:
    """Conflits de contraintes (§24) : une allocation de `total` sur n
    cryptos, chacune au plus `cap`, est-elle possible ? Jamais relâchées."""
    out = []
    if n <= 0 and total > 0:
        out.append("aucune crypto pour investir")
    elif n * cap < total - 1e-12:
        out.append(f"{n} position(s) × {fr(cap * 100, '.0f')} % au plus = {fr(n * cap * 100, '.0f')} % < "
                   f"{fr(total * 100, '.0f')} % à investir : impossible sans relâcher le plafond par position")
    if not 0 <= total <= 1:
        out.append("part investie hors de 0 à 100 % (ni levier ni vente à découvert en Spot)")
    return out


def project(v: np.ndarray, total: float, cap: float) -> np.ndarray:
    """Projection exacte sur {0 ≤ w ≤ cap, Σw = total} : la somme de
    clip(v − τ, 0, cap) décroît par morceaux en τ ; on cherche le morceau où
    elle vaut `total`, puis on interpole."""
    problems = feasibility(len(v), total, cap)
    if problems:
        raise ValueError(problems[0])
    v = np.asarray(v, dtype=float)
    bps = np.unique(np.concatenate([v, v - cap]))

    def mass(t: float) -> float:
        return float(np.clip(v - t, 0, cap).sum())

    lo, hi = 0, len(bps) - 1                      # mass(bps[lo]) ≥ total ≥ mass(bps[hi])
    if mass(bps[lo]) < total:
        return np.clip(v - (bps[0] - (total - mass(bps[0])) / len(v)), 0, cap)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if mass(bps[mid]) >= total:
            lo = mid
        else:
            hi = mid
    m_lo, m_hi = mass(bps[lo]), mass(bps[hi])
    tau = bps[lo] if m_lo == m_hi else bps[lo] + (m_lo - total) / (m_lo - m_hi) * (bps[hi] - bps[lo])
    return np.clip(v - tau, 0, cap)


def risk_contributions(w: np.ndarray, cov: np.ndarray) -> Tuple[float, np.ndarray]:
    """Volatilité du portefeuille et part de chaque position dans son risque
    (la somme fait 1) : RC_i = w_i (Σw)_i / σ²."""
    var = float(w @ cov @ w)
    if var <= 0:
        return 0.0, np.zeros(len(w))
    return math.sqrt(var), w * (cov @ w) / var


def optimize(method: str, cov: np.ndarray, total: float, cap: float, mu: Optional[np.ndarray] = None,
             iters: int = 3000) -> np.ndarray:
    """Allocation long seulement de `total` (part investie) sous un plafond
    par crypto (§13-20) : equal, inverse_vol, min_variance (gradient projeté),
    erc (parité de risque, corrélations comprises), max_sharpe (rendements
    attendus rétrécis de moitié vers leur moyenne : très incertains)."""
    n = len(cov)
    vols = np.sqrt(np.diag(cov))
    if method == "equal":
        return project(np.full(n, total / n), total, cap)
    if method == "inverse_vol":
        v = 1 / np.maximum(vols, 1e-12)
        return project(v / v.sum() * total, total, cap)
    step = 1.0 / (2 * float(np.linalg.eigvalsh(cov).max()) + 1e-12)
    w = project(np.full(n, total / n), total, cap)
    if method == "min_variance":
        for _ in range(iters):
            w_new = project(w - step * 2 * cov @ w, total, cap)
            if np.abs(w_new - w).max() < 1e-12:
                return w_new
            w = w_new
        return w
    if method == "erc":
        for _ in range(iters):
            _s, rc = risk_contributions(w, cov)
            w_new = project(w * np.sqrt((1 / n) / np.maximum(rc, 1e-12)), total, cap)
            if np.abs(w_new - w).max() < 1e-10:
                return w_new
            w = 0.5 * w + 0.5 * w_new
        return w
    if method == "max_sharpe":
        if mu is None:
            raise ValueError("max_sharpe : rendements attendus requis")
        m = 0.5 * mu + 0.5 * float(mu.mean())
        for _ in range(iters):
            sd = math.sqrt(max(float(w @ cov @ w), 1e-18))
            grad = m / sd - float(w @ m) * (cov @ w) / sd ** 3
            w_new = project(w + step * grad * 1e-2, total, cap)
            if np.abs(w_new - w).max() < 1e-12:
                return w_new
            w = w_new
        return w
    raise ValueError(f"méthode {method!r} inconnue")


def validate_weights(w: np.ndarray, total: float, cap: float) -> List[str]:
    """Validation numérique (§26) : valeurs finies, bornes, somme."""
    errors = []
    if not np.all(np.isfinite(w)):
        errors.append("poids non fini")
    if (w < -1e-9).any() or (w > cap + 1e-9).any():
        errors.append("poids hors des bornes")
    if abs(float(w.sum()) - total) > 1e-6:
        errors.append(f"somme {fr(float(w.sum()), '.6f')} au lieu de {fr(total, '.6f')}")
    return errors


# ══════════════════════════════════════════════════════════════════════
# État du portefeuille (§7-9, §28-35)
# ══════════════════════════════════════════════════════════════════════

def _cov(close: pd.DataFrame, assets: Sequence[str], days: int = COV_DAYS) -> Optional[np.ndarray]:
    rets = np.log(close[list(assets)] / close[list(assets)].shift(1)).iloc[-days:].dropna()
    if len(rets) < 30:
        return None
    return rets.cov().values


def state(day: str, positions: Dict[str, Dict[str, float]], close: pd.DataFrame, equity: float, cash: float,
          p: ts.TrendParams) -> Dict[str, Any]:
    """L'état du portefeuille (§7) : chaque position (valeur, poids, risque
    jusqu'au stop, volatilité, bêta à BTC, part du risque), l'exposition, les
    liquidités, la concentration, le risque engagé contre le plafond, la
    volatilité du portefeuille et sa diversification."""
    rows, assets = [], [a for a in positions if a in close.columns]
    values = {a: positions[a]["qty"] * positions[a]["price"] for a in assets}
    invested = sum(values.values())
    cov = _cov(close, assets) if assets else None
    btc = np.log(close["btc"] / close["btc"].shift(1)).iloc[-COV_DAYS:] if "btc" in close else None
    w = np.array([values[a] / equity for a in assets]) if equity > 0 else np.zeros(len(assets))
    sigma, rc = risk_contributions(w, cov) if cov is not None and len(assets) else (0.0, np.zeros(len(assets)))
    for k, a in enumerate(assets):
        pos = positions[a]
        r = np.log(close[a] / close[a].shift(1)).iloc[-COV_DAYS:]
        beta = None
        if btc is not None and a != "btc":
            ok = r.notna() & btc.notna()
            if ok.sum() >= 30 and float(btc[ok].var()) > 0:
                beta = float(np.cov(r[ok], btc[ok])[0, 1] / btc[ok].var())
        rows.append({"asset": a, "value": values[a], "weight": float(w[k]),
                     "risk_to_stop": max(0.0, pos["qty"] * (pos["price"] - pos["stop"])),
                     "risk_quote": pos.get("risk_quote", 0.0), "vol": float(r.std() * math.sqrt(YEAR)),
                     "beta": beta, "risk_share": float(rc[k]) if len(rc) else 0.0})
    hhi = float((w ** 2).sum() / (w.sum() ** 2)) if w.sum() > 0 else 0.0
    vols = np.array([x["vol"] for x in rows])
    div = float((w * vols).sum() / (sigma * math.sqrt(YEAR))) if sigma > 0 else None
    corr = None
    if cov is not None and len(assets) > 1:
        d = np.sqrt(np.diag(cov))
        c = cov / np.outer(d, d)
        corr = float(c[np.triu_indices(len(assets), 1)].mean())
    risk_used = sum(x["risk_quote"] for x in rows) / equity if equity > 0 else 0.0
    return {"day": day, "equity": equity, "cash": cash, "cash_share": cash / equity if equity > 0 else 0.0,
            "exposure": invested / equity if equity > 0 else 0.0, "positions": rows, "count": len(rows),
            "max_positions": p.max_positions, "hhi": hhi, "effective_n": 1 / hhi if hhi > 0 else 0.0,
            "risk_used": risk_used, "risk_cap": p.max_total_risk,
            "risk_to_stops": sum(x["risk_to_stop"] for x in rows) / equity if equity > 0 else 0.0,
            "vol_annual": sigma * math.sqrt(YEAR), "diversification": div, "mean_corr": corr,
            "largest": max((x["weight"] for x in rows), default=0.0), "cap": p.max_position_pct}


def constraints(st: Dict[str, Any]) -> List[str]:
    """Contraintes de la règle sur l'état (§22-23) : au plus N positions, 25 %
    par position au moment de l'achat (une position qui a monté peut
    dépasser : c'est une dérive, signalée), risque cumulé sous le plafond,
    jamais de levier ni de liquidités négatives."""
    out = []
    if st["count"] > st["max_positions"]:
        out.append(f"{st['count']} positions pour {st['max_positions']} au plus")
    for x in st["positions"]:
        if x["weight"] > st["cap"] + 1e-9:
            out.append(f"{x['asset'].upper()} pèse {fr(x['weight'] * 100, '.0f')} % (achetée sous "
                       f"{fr(st['cap'] * 100, '.0f')} %, elle a monté)")
    if st["risk_used"] > st["risk_cap"] + 1e-9:
        out.append(f"risque engagé {fr(st['risk_used'] * 100, '.1f')} % pour {fr(st['risk_cap'] * 100, '.0f')} % "
                   "au plus")
    if st["exposure"] > 1 + 1e-9 or st["cash"] < -1e-6:
        out.append("levier ou liquidités négatives (impossible en Spot)")
    return out


def alternatives(st: Dict[str, Any], close: pd.DataFrame, p: ts.TrendParams) -> Dict[str, Any]:
    """Les mêmes cryptos, la même part investie, réparties autrement (§13-20)
    : poids, volatilité, part de risque la plus haute ; rotation et coût pour
    y passer. Comparaison seulement : la règle ne rééquilibre jamais."""
    assets = [x["asset"] for x in st["positions"]]
    if len(assets) < 2:
        return {"status": "NOT_APPLICABLE", "reason": "moins de deux positions : rien à répartir"}
    cov = _cov(close, assets)
    if cov is None:
        return {"status": "INSUFFICIENT_DATA", "reason": f"moins de 30 jours communs sur {COV_DAYS}"}
    total, cap = st["exposure"], p.max_position_pct
    conflicts = feasibility(len(assets), total, cap)
    if conflicts:
        return {"status": "INFEASIBLE", "reason": conflicts[0]}
    rule = np.array([x["weight"] for x in st["positions"]])
    mu = np.log(close[assets].iloc[-1] / close[assets].iloc[-COV_DAYS]).values / COV_DAYS
    out: Dict[str, Any] = {"status": "OK", "assets": assets, "total": total, "cap": cap, "methods": {}}
    for name, w in [("rule", rule)] + [(m, optimize(m, cov, total, cap, mu)) for m in METHODS]:
        sigma, rc = risk_contributions(w, cov)
        turnover = float(np.abs(w - rule).sum())
        out["methods"][name] = {"weights": dict(zip(assets, (float(x) for x in w))),
                                "vol_annual": sigma * math.sqrt(YEAR), "max_risk_share": float(rc.max()),
                                "turnover": turnover,
                                "cost": turnover * st["equity"] * (p.fee + p.slippage),
                                "errors": validate_weights(w, total, cap) if name != "rule" else []}
    out["rebalance"] = "NO_REBALANCE"
    out["rebalance_reason"] = ("la règle laisse courir les gagnants et ne vend qu'au stop ; rééquilibrer vendrait "
                               "les meilleurs trades, d'où vient tout le résultat (laboratoire quantitatif)")
    return out


def decide(st: Dict[str, Any], alt: Dict[str, Any], now: Optional[datetime] = None) -> PortfolioDecision:
    """Décision consultative (§61) : HOLD (dans les contraintes), REVIEW (une
    contrainte dépassée, à regarder), NO_ALLOCATE (aucune position) ;
    jamais un ordre ni une autorisation."""
    now = now or datetime.now(timezone.utc)
    violations = tuple(constraints(st))
    decision = "NO_ALLOCATE" if not st["count"] else ("REVIEW" if violations else "HOLD")
    return PortfolioDecision(decision_id=str(uuid.uuid4()), day=st["day"], decision=decision,
                             engine_version=ENGINE_VERSION, positions=st["count"],
                             exposure=round(st["exposure"], 6), cash_share=round(st["cash_share"], 6),
                             hhi=round(st["hhi"], 6), risk_used=round(st["risk_used"], 6),
                             violations=violations, rebalance=alt.get("rebalance", "NO_REBALANCE"),
                             created_at=now.isoformat(timespec="seconds"))


def compact(st: Dict[str, Any], dec: PortfolioDecision, alt: Dict[str, Any]) -> Dict[str, Any]:
    """Résumé gardé dans l'état du bot (raisonnement, rapport, Rachelle)."""
    best = None
    if alt.get("status") == "OK":
        best = min(alt["methods"], key=lambda k: alt["methods"][k]["vol_annual"])
    return {"day": st["day"], "decision": dec.decision, "violations": list(dec.violations),
            "count": st["count"], "exposure": st["exposure"], "cash_share": st["cash_share"],
            "effective_n": st["effective_n"], "risk_used": st["risk_used"], "risk_cap": st["risk_cap"],
            "vol_annual": st["vol_annual"], "mean_corr": st["mean_corr"], "largest": st["largest"],
            "rule_vol": alt["methods"]["rule"]["vol_annual"] if best else None,
            "lowest_vol_method": best, "lowest_vol": alt["methods"][best]["vol_annual"] if best else None}


DECISION_FR = {"HOLD": "dans les contraintes", "REVIEW": "à regarder", "NO_ALLOCATE": "aucune position"}


def describe(view: Optional[Dict[str, Any]]) -> str:
    """Une phrase pour le raisonnement du jour et Rachelle."""
    if not view:
        return "pas encore d'état du portefeuille"
    if view.get("error"):
        return f"état du portefeuille impossible ({view['error']})"
    if not view["count"]:
        return "aucune position : tout en liquidités"
    text = (f"{view['count']} position(s), {fr(view['exposure'] * 100, '.0f')} % investis, "
            f"{fr(view['effective_n'], '.1f')} position(s) effective(s), risque engagé "
            f"{fr(view['risk_used'] * 100, '.1f')} % pour {fr(view['risk_cap'] * 100, '.0f')} % au plus, volatilité "
            f"{fr(view['vol_annual'] * 100, '.0f')} % par an ; {DECISION_FR[view['decision']]}")
    if view.get("violations"):
        text += " (" + " ; ".join(view["violations"]) + ")"
    return text


# ══════════════════════════════════════════════════════════════════════
# Étude de la taille des achats sur l'historique (§13, §54, §89-90)
# ══════════════════════════════════════════════════════════════════════

def sizing_hooks(close: pd.DataFrame, p: ts.TrendParams, scheme: str) -> ts.BacktestHooks:
    """Variante de taille des achats sur LA boucle de backtest : « egal »
    (montant égal : capital / nombre de positions), « correlation » (même
    risque, divisé par deux si la crypto suit les positions détenues). Les
    plafonds (25 % par position, liquidités) restent ceux de la règle."""
    if scheme == "regle":
        return ts.BacktestHooks()
    px = close.values
    col = {a: k for k, a in enumerate(close.columns)}
    logret = np.log(close / close.shift(1)).values

    def corr_to_book(i: int, a: str, holdings: Dict[str, Any]) -> float:
        if not holdings or i < 60:
            return 0.0
        win = logret[i - 59:i + 1]
        x = win[:, col[a]]
        out = []
        for b in holdings:
            y = win[:, col[b]]
            ok = np.isfinite(x) & np.isfinite(y)
            if ok.sum() >= 30:
                out.append(float(np.corrcoef(x[ok], y[ok])[0, 1]))
        return float(np.mean(out)) if out else 0.0

    def resize(i: int, plans: List[Dict[str, Any]], holdings: Dict[str, Any], equity: float
               ) -> List[Dict[str, Any]]:
        cash = equity - sum(h.qty * px[i, col[a]] for a, h in holdings.items() if np.isfinite(px[i, col[a]]))
        out = []
        for plan in plans:
            entry = plan["entry"]
            if scheme == "egal":
                notional = equity / p.max_positions
            elif scheme == "correlation":
                factor = 0.5 if corr_to_book(i, plan["asset"], holdings) > CORR_LIMIT else 1.0
                notional = plan["qty"] * entry * factor
            else:
                raise ValueError(f"taille {scheme!r} inconnue")
            qty = min(notional / entry, p.max_position_pct * equity / entry)
            cost = qty * entry * (1 + p.fee)
            if cost > cash:
                qty = cash / (entry * (1 + p.fee))
                cost = qty * entry * (1 + p.fee)
            if qty * entry < 10:
                continue
            unit_risk = plan["risk_quote"] / plan["qty"]
            out.append({**plan, "qty": qty, "cost": cost, "risk_quote": qty * unit_risk})
            cash -= cost
        return out

    return ts.BacktestHooks(filter_plans=resize)


def study(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams) -> Dict[str, Any]:
    """La taille des achats éprouvée sur les deux époques (§89-90) : la règle
    contre le montant égal et contre la réduction des achats corrélés ;
    rendement annualisé, baisse maximale, Calmar, Sharpe, exposition. Une
    autre taille n'est « à proposer » que si elle bat la règle sur les deux
    époques d'au moins 10 % de Calmar ; même alors, seule l'évolution encadrée
    pourrait l'essayer."""
    pre = ts.precompute(close, volume, p)
    periods = [mb.IS_PERIOD, (mb.OOS_START, str(close.index[-1].date()))]
    out: Dict[str, Any] = {"periods": periods, "schemes": {}}
    for scheme in SCHEMES:
        rows = []
        for a, b in periods:
            res = ts.backtest(close, volume, p, a, b, pre=pre, hooks=sizing_hooks(close, p, scheme))
            m = res.metrics
            rows.append({"cagr": m.get("cagr_pct", 0.0), "max_dd": m.get("max_dd_pct", 0.0),
                         "calmar": m.get("calmar", 0.0), "sharpe": m.get("sharpe", 0.0),
                         "trades": m.get("trades", 0), "exposure": float(res.exposure.mean())})
        out["schemes"][scheme] = rows
    base = out["schemes"]["regle"]
    better = [s for s in SCHEMES[1:] if all(out["schemes"][s][k]["calmar"] > base[k]["calmar"] * (1 + MARGIN)
                                            for k in range(len(periods)))]
    out["better"] = better
    out["verdict"] = ("à proposer à l'évolution encadrée : " + ", ".join(SCHEME_FR[s] for s in better)) if better \
        else "la règle reste : aucune autre taille ne la bat nettement sur les deux époques"
    return out


def render_study(s: Dict[str, Any], st: Optional[Dict[str, Any]] = None) -> str:
    """docs/PORTEFEUILLE.md : l'étude de la taille des achats (et l'état du
    jour s'il est connu)."""
    (a1, b1), (a2, b2) = s["periods"]
    lines = ["# Portefeuille : la taille des achats éprouvée", "",
             "Tiré de `python trendguard_bot.py portefeuille etude` (étape 12 du prompt maître, "
             "[`MOTEUR_PORTEFEUILLE.md`](MOTEUR_PORTEFEUILLE.md)) : la même règle, les mêmes achats et les mêmes "
             "ventes, seule la taille des achats change. Frais et glissement compris.", "",
             f"| Taille des achats | {a1[:4]}-{b1[:4]} rendement | baisse | Calmar | Sharpe | exposition | depuis "
             f"{a2[:4]} rendement | baisse | Calmar | Sharpe | exposition |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for scheme, rows in s["schemes"].items():
        cells = []
        for r in rows:
            cells += [f"{fr(r['cagr'], '+.1f')} %", f"{fr(r['max_dd'], '.1f')} %", fr(r["calmar"], ".2f"),
                      fr(r["sharpe"], ".2f"), f"{fr(r['exposure'] * 100, '.0f')} %"]
        lines.append(f"| {SCHEME_FR[scheme]} | " + " | ".join(cells) + " |")
    lines += ["", "## Verdict", "", f"- {s['verdict'][:1].upper()}{s['verdict'][1:]}.",
              "- La règle dimensionne déjà chaque achat en fonction de sa volatilité (même risque jusqu'au stop) : "
              "c'est une parité de risque position par position. Une autre taille ne change pas les trades, "
              "seulement ce que chacun pèse."]
    if st:
        lines += ["", f"## État du portefeuille ({st['day']})", "", describe(st)]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `portefeuille` : l'état du dernier jour (défaut) ou l'étude
    de la taille des achats (--out pour l'écrire)."""
    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Moteur de portefeuille de TrendGuard (consultatif)")
    ap.add_argument("action", nargs="?", default="etat", choices=["etat", "etude"])
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    view = (read_state(g.db_file) or {}).get("portefeuille")
    if args.action == "etat":
        print(f"Portefeuille ({(view or {}).get('day', 'jamais évalué')}) : {describe(view)}.")
        return 0
    close, volume = load_history(args.cache, list(g.universe), max_age_days=None)
    s = study(close, volume, params_for(g))
    text = render_study(s, view)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    print(f"({fr_plain(len(s['schemes']))} tailles éprouvées)")
    return 0
