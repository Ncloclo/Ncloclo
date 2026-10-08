"""
Moteur de risque (prompt maître, étape 11 ; docs/MOTEUR_RISQUE.md) : mesurer,
comprendre, agréger, limiter, simuler, contrôler, alerter, et bloquer si
nécessaire, le risque du portefeuille avant et après chaque décision.

    données validées → volatilité (historique, EWMA, régime) → VaR et perte
    moyenne au-delà (historique, normale, Student, Monte-Carlo à graine) sur
    1, 5 et 10 jours → queue (asymétrie, aplatissement, indice de Hill,
    valeurs extrêmes) → corrélations (Pearson, Spearman, Kendall, en crise,
    dépendance de queue) → concentration (HHI, paris indépendants,
    contrepartie) → liquidité (part du volume, jours pour vendre, coût) →
    baisse depuis le plus haut → budget de risque → contributions de chaque
    crypto → stress et stress inversé → limites → note de risque (et ses
    composantes) → état et décision, valables jusqu'à la décision suivante

Le moteur mesure et prévient ; les limites de la règle restent celles que la
porte d'exécution applique (1 % par achat, 6 % cumulés, 8 positions, 25 %
par position, arrêt d'urgence à −40 %). Une seule chose est appliquée : sans
évaluation du risque valide pour la décision du jour (moteur en panne,
données critiques invalides, évaluation périmée), aucun achat (mode sûr ;
les ventes restent toujours permises). Les autres états (vigilance, élevé,
critique) informent, au rapport et dans le raisonnement : une règle validée
ne change pas sans preuve. Une mesure n'est pas une prévision, une confiance
n'est pas une probabilité de gain.

    python trendguard_bot.py risque                # dernière évaluation du bot
    python trendguard_bot.py risque calibrage      # la VaR éprouvée sur le backtest et sur le journal
"""

from __future__ import annotations

import argparse
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import v29

from . import stress as stress_tests
from . import trend_strategy as ts
from .contrats import RiskAssessment
from .registre import fingerprint
from .texte import fr

MODEL_VERSION = "risque-1.0.0"
LIMITS_VERSION = "limites-1.0.0"
SCORE_VERSION = "note-risque-1.0.0"
LEVELS = (0.95, 0.99)
HORIZONS = (1, 5, 10)
DAYS = 365                      # historique des mesures
TAIL_DAYS = 1095                # historique de la queue (trois ans)
MIN_DAYS = 60
EWMA_LAMBDA = 0.94
MC_SIMS = 10_000
SEED = 7
PARTICIPATIONS = (0.25, 0.10, 0.05, 0.02, 0.01)
IMPACT_Y = 1.0
VALID_HOURS = 24                # une évaluation vaut jusqu'à la décision suivante
STATES = ("RISK_UNKNOWN", "RISK_CALCULATING", "RISK_VALIDATING", "RISK_NORMAL", "RISK_WARNING", "RISK_HIGH",
          "RISK_CRITICAL", "RISK_BLOCKED")
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "RISK_UNKNOWN": ("RISK_CALCULATING",),
    "RISK_CALCULATING": ("RISK_VALIDATING", "RISK_BLOCKED"),
    "RISK_VALIDATING": ("RISK_NORMAL", "RISK_WARNING", "RISK_HIGH", "RISK_CRITICAL", "RISK_BLOCKED"),
    "RISK_NORMAL": ("RISK_CALCULATING",), "RISK_WARNING": ("RISK_CALCULATING",),
    "RISK_HIGH": ("RISK_CALCULATING",), "RISK_CRITICAL": ("RISK_CALCULATING",),
    "RISK_BLOCKED": ("RISK_CALCULATING",),
}
APPROVALS = ("RISK_APPROVED", "RISK_APPROVED_WITH_LIMIT", "RISK_RESTRICTED", "RISK_BLOCKED")
LEVEL_SCORE = {"LOW": 0.0, "MEDIUM": 1 / 3, "HIGH": 2 / 3, "CRITICAL": 1.0}
SCORE_WEIGHTS = {"market": 0.15, "volatility": 0.10, "liquidity": 0.10, "concentration": 0.10, "leverage": 0.05,
                 "drawdown": 0.15, "tail": 0.10, "stress": 0.10, "model": 0.05, "operational": 0.10}
# Seuils de lecture des composantes (versionnés) : bas, moyen, élevé, au-delà critique.
# Stress : baisse depuis le plus haut après un krach de 35 % (stops sautés), jugée contre l'arrêt
# d'urgence (−40 %) : sous 20 %, 30 %, 40 % ; au-delà, l'arrêt d'urgence serait déclenché.
THRESHOLDS = {"market": (5.0, 8.0, 12.0), "volatility": (40.0, 70.0, 100.0), "stress": (20.0, 30.0, 40.0),
              "correlation": (0.5, 0.7, 0.85), "liquidity": (1.0, 3.0, 10.0)}
STRESS_REFERENCE = 0.35         # krach de référence (mars 2020 : −40 % à −50 % sur BTC en deux jours)
BUDGET_BANDS = ((0.70, "NORMAL"), (0.85, "WARNING"), (1.00, "RESTRICTED"))
ALERT_LEVELS = ("INFO", "NOTICE", "WARNING", "HIGH", "CRITICAL", "EMERGENCY")
_N = NormalDist()


class RiskDataError(ValueError):
    """Données critiques invalides : le risque n'est pas mesurable."""


# ---------- Briques de mesure ----------

def var_es(r: np.ndarray, level: float) -> Tuple[float, float]:
    """VaR et perte moyenne au-delà (ES) d'une série de rendements, en
    fraction positive (une perte de 3 % vaut 0,03)."""
    x = np.asarray(r, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return float("nan"), float("nan")
    q = float(np.quantile(x, 1 - level))
    tail = x[x <= q]
    return max(0.0, -q), max(0.0, -float(tail.mean()) if len(tail) else -q)


def horizon_returns(daily: np.ndarray, h: int) -> np.ndarray:
    """Rendements composés sur `h` jours glissants (se chevauchent)."""
    x = np.asarray(daily, dtype=float)
    if h <= 1:
        return x
    logs = np.log1p(np.clip(x, -0.999999, None))
    c = np.concatenate([[0.0], np.cumsum(logs)])
    return np.expm1(c[h:] - c[:-h])


def parametric(mu: float, sigma: float, level: float, h: int = 1) -> Tuple[float, float]:
    """VaR et ES d'une loi normale (variance-covariance), sur `h` jours."""
    z = _N.inv_cdf(1 - level)
    m, s = mu * h, sigma * math.sqrt(h)
    return max(0.0, -(m + z * s)), max(0.0, -(m - s * _N.pdf(z) / (1 - level)))


_T_CACHE: Dict[Tuple[float, float], Tuple[float, float]] = {}


def _t_standard(nu: float, level: float) -> Tuple[float, float]:
    """Quantile et perte moyenne au-delà d'une loi de Student réduite (variance
    1), par simulation à graine fixée, gardés en mémoire."""
    key = (round(nu, 2), level)
    if key not in _T_CACHE:
        draws = np.random.default_rng(SEED).standard_t(key[0], size=200_000) * math.sqrt((key[0] - 2) / key[0])
        q = float(np.quantile(draws, 1 - level))
        _T_CACHE[key] = (q, float(draws[draws <= q].mean()))
    return _T_CACHE[key]


def student(mu: float, sigma: float, excess_kurtosis: float, level: float, h: int = 1) -> Tuple[float, float, float]:
    """VaR et ES d'une loi de Student de même variance (degrés de liberté par
    les moments : ν = 4 + 6 / aplatissement en excès) ; renvoie aussi ν."""
    nu = float(np.clip(4 + 6 / max(excess_kurtosis, 0.01), 2.5, 30.0))
    q, tail = _t_standard(nu, level)
    m, s = mu * h, sigma * math.sqrt(h)
    return max(0.0, -(m + s * q)), max(0.0, -(m + s * tail)), nu


HEADLINE = ("historical", "normal", "student")


def prudent(port: np.ndarray, level: float, h: int = 1) -> Tuple[float, float]:
    """La VaR et l'ES du moteur : la plus prudente des méthodes historique,
    normale et Student. Sur le backtest de la règle, la VaR historique seule
    est dépassée trop souvent (queue épaisse, cryptos achetées en pleine
    hausse) ; la plus prudente des trois est bien calibrée (docs/MOTEUR_RISQUE.md)."""
    mu, sd, _skew, exk = moments(port)
    rows = [var_es(horizon_returns(port, h), level), parametric(mu, sd, level, h), student(mu, sd, exk, level, h)[:2]]
    return max(r[0] for r in rows), max(r[1] for r in rows)


def montecarlo(rets: np.ndarray, w: np.ndarray, level: float, h: int = 1, sims: int = MC_SIMS,
               seed: int = SEED) -> Tuple[float, float]:
    """Monte-Carlo (§10.3) : rendements joints tirés d'une loi normale aux
    moyennes et covariances mesurées, `h` jours composés, graine fixée."""
    mean, cov = rets.mean(axis=0), np.atleast_2d(np.cov(rets, rowvar=False))
    rng = np.random.default_rng(seed)
    draws = rng.multivariate_normal(mean, cov, size=(sims, h), method="cholesky" if _psd(cov) else "eigh")
    port = np.prod(1 + draws @ w, axis=1) - 1
    return var_es(port, level)


def _psd(cov: np.ndarray) -> bool:
    try:
        np.linalg.cholesky(cov + 1e-15 * np.eye(len(cov)))
        return True
    except np.linalg.LinAlgError:
        return False


def moments(x: np.ndarray) -> Tuple[float, float, float, float]:
    """Moyenne, écart-type, asymétrie, aplatissement en excès."""
    x = np.asarray(x, dtype=float)
    m, s = float(x.mean()), float(x.std(ddof=1)) if len(x) > 1 else 0.0
    if s <= 0:
        return m, 0.0, 0.0, 0.0
    z = (x - m) / s
    return m, s, float((z ** 3).mean()), float((z ** 4).mean() - 3)


def hill(r: np.ndarray, share: float = 0.10) -> Optional[float]:
    """Indice de queue de Hill sur les pertes (plus il est petit, plus les
    pertes extrêmes sont fréquentes ; sous 2, variance infinie)."""
    losses = np.sort(-np.asarray(r, dtype=float)[np.asarray(r) < 0])[::-1]
    k = max(10, int(len(losses) * share))
    if len(losses) <= k + 1 or losses[k] <= 0:
        return None
    return float(1 / np.mean(np.log(losses[:k] / losses[k])))


def pot(r: np.ndarray, level: float = 0.99, threshold: float = 0.95) -> Optional[Dict[str, float]]:
    """Valeurs extrêmes, pics au-delà d'un seuil (§12) : loi de Pareto
    généralisée ajustée aux pertes au-delà du 95e centile (moments pondérés),
    VaR et ES à 99 %. Indicatif : peu de points."""
    losses = -np.asarray(r, dtype=float)
    u = float(np.quantile(losses, threshold))
    y = np.sort(losses[losses > u] - u)
    n, nu = len(losses), len(y)
    if nu < 15:
        return None
    a0 = float(y.mean())
    a1 = float(np.mean(y * (nu - 1 - np.arange(nu)) / (nu - 1)))
    if a0 - 2 * a1 <= 0:
        return None
    xi, beta = 2 - a0 / (a0 - 2 * a1), 2 * a0 * a1 / (a0 - 2 * a1)
    if xi >= 1 or beta <= 0:
        return None
    tail = (n / nu) * (1 - level)
    var = u + (beta / xi) * (tail ** (-xi) - 1) if abs(xi) > 1e-9 else u - beta * math.log(tail)
    return {"xi": xi, "beta": beta, "threshold": u, "var": var, "es": (var + beta - xi * u) / (1 - xi),
            "excesses": nu}


def kendall(a: np.ndarray, b: np.ndarray) -> float:
    """Tau de Kendall (version b, ex æquo comptés)."""
    da = np.sign(a[:, None] - a[None, :])
    db = np.sign(b[:, None] - b[None, :])
    iu = np.triu_indices(len(a), 1)
    s, ta, tb = (da * db)[iu].sum(), np.abs(da[iu]).sum(), np.abs(db[iu]).sum()
    return float(s / math.sqrt(ta * tb)) if ta > 0 and tb > 0 else 0.0


def _rank(x: np.ndarray) -> np.ndarray:
    return pd.Series(x).rank().to_numpy()


def _mean_offdiag(m: np.ndarray) -> Optional[float]:
    n = len(m)
    return float((m.sum() - np.trace(m)) / (n * (n - 1))) if n > 1 else None


def correlations(rets: np.ndarray, btc: Optional[np.ndarray]) -> Dict[str, Any]:
    """Corrélations moyennes entre les cryptos détenues (§18) : Pearson,
    Spearman, Kendall ; récente (30 jours) ; en crise (jours où BTC chute le
    plus) ; dépendance de queue (chutes simultanées)."""
    n = rets.shape[1]
    if n < 2 or len(rets) < 30:
        return {"pairs": 0}
    pear = np.corrcoef(rets, rowvar=False)
    spear = np.corrcoef(np.column_stack([_rank(rets[:, k]) for k in range(n)]), rowvar=False)
    kend = np.ones((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            kend[i, j] = kend[j, i] = kendall(rets[:, i], rets[:, j])
    out = {"pairs": n * (n - 1) // 2, "pearson": _mean_offdiag(pear), "spearman": _mean_offdiag(spear),
           "kendall": _mean_offdiag(kend), "recent": _mean_offdiag(np.corrcoef(rets[-30:], rowvar=False))}
    if btc is not None and len(btc) == len(rets):
        crisis = btc <= np.quantile(btc, 0.10)
        if crisis.sum() >= 10:
            out["crisis"] = _mean_offdiag(np.corrcoef(rets[crisis], rowvar=False))
    q = np.quantile(rets, 0.10, axis=0)
    low = rets <= q
    co = [float((low[:, i] & low[:, j]).sum() / max(1, low[:, i].sum())) for i in range(n) for j in range(n) if i != j]
    out["tail_dependence"] = float(np.mean(co))
    return out


def _level(value: Optional[float], bounds: Tuple[float, float, float]) -> str:
    if value is None or not math.isfinite(value):
        return "MEDIUM"
    return "LOW" if value < bounds[0] else "MEDIUM" if value < bounds[1] else "HIGH" if value < bounds[2] \
        else "CRITICAL"


def kupiec(exceptions: int, n: int, level: float) -> Dict[str, float]:
    """Test de couverture de Kupiec (§93) : la part des jours où la perte
    dépasse la VaR est-elle celle annoncée ? p-valeur du khi-deux à 1 degré."""
    p = 1 - level
    x = exceptions
    if n <= 0:
        return {"n": 0, "exceptions": 0, "p_value": float("nan")}
    rate = x / n
    ll0 = (n - x) * math.log(1 - p) + x * math.log(p)
    ll1 = (n - x) * math.log(1 - rate) if rate < 1 else 0.0
    ll1 += x * math.log(rate) if rate > 0 else 0.0
    lr = max(0.0, -2 * (ll0 - ll1))
    return {"n": n, "exceptions": x, "expected": n * p, "lr": lr, "p_value": math.erfc(math.sqrt(lr / 2))}


def christoffersen(hits: Sequence[bool]) -> Dict[str, float]:
    """Test d'indépendance de Christoffersen (§93) : les dépassements
    arrivent-ils en grappes ?"""
    h = [int(x) for x in hits]
    n = {(a, b): 0 for a in (0, 1) for b in (0, 1)}
    for a, b in zip(h, h[1:]):
        n[(a, b)] += 1
    n0, n1 = n[(0, 0)] + n[(0, 1)], n[(1, 0)] + n[(1, 1)]
    if n0 == 0 or n1 == 0:
        return {"lr": 0.0, "p_value": 1.0}
    p0, p1 = n[(0, 1)] / n0, n[(1, 1)] / n1
    p = (n[(0, 1)] + n[(1, 1)]) / (n0 + n1)

    def ll(q: float, k0: int, k1: int) -> float:
        return (k0 * math.log(1 - q) if q < 1 else 0.0) + (k1 * math.log(q) if q > 0 else 0.0)
    lr = max(0.0, -2 * (ll(p, n[(0, 0)] + n[(1, 0)], n[(0, 1)] + n[(1, 1)])
                        - ll(p0, n[(0, 0)], n[(0, 1)]) - ll(p1, n[(1, 0)], n[(1, 1)])))
    return {"lr": lr, "p_value": math.erfc(math.sqrt(lr / 2))}


def var_backtest(predicted: Sequence[float], realized: Sequence[float], level: float = 0.95) -> Dict[str, Any]:
    """VaR annoncée (fraction de perte) contre rendement réalisé le lendemain
    (§86, §93) : dépassements, couverture (Kupiec), grappes (Christoffersen),
    sous- ou surestimation."""
    pairs = [(v, r) for v, r in zip(predicted, realized) if v is not None and r is not None
             and math.isfinite(v) and math.isfinite(r)]
    hits = [r < -v for v, r in pairs]
    k = kupiec(sum(hits), len(hits), level)
    c = christoffersen(hits)
    verdict = ("pas assez d'observations" if len(hits) < MIN_DAYS else
               "VaR sous-estimée" if k["p_value"] < 0.05 and k["exceptions"] > k["expected"] else
               "VaR surestimée" if k["p_value"] < 0.05 else "VaR bien calibrée")
    return {**k, "independence_p": c["p_value"], "verdict": verdict}


def rolling_var_backtest(daily: np.ndarray, level: float = 0.95, window: int = DAYS) -> Dict[str, Any]:
    """VaR historique sur la fenêtre passée, comparée au jour suivant, sur
    toute une courbe (le backtest de la règle)."""
    x = np.asarray(daily, dtype=float)
    pred = [var_es(x[t - window:t], level)[0] for t in range(window, len(x))]
    return var_backtest(pred, x[window:].tolist(), level)


def position_backtest(close: pd.DataFrame, volume: Optional[pd.DataFrame], p: ts.TrendParams, start: str,
                      end: str) -> Dict[float, Dict[str, Any]]:
    """La VaR du moteur, celle des positions du jour (§86, §93) : chaque jour
    du backtest de la règle, VaR historique d'un jour des positions tenues
    après les achats, comparée au résultat du lendemain. Les jours sans
    position ne comptent pas."""
    rets = close.pct_change()
    days: List[Tuple[int, Dict[str, float]]] = []

    def watch(i: int, plans: List[Dict[str, Any]], holdings: Dict[str, Any], equity: float) -> List[Dict[str, Any]]:
        qty = {a: h.qty for a, h in holdings.items()}
        for x in plans:
            qty[x["asset"]] = qty.get(x["asset"], 0.0) + x["qty"]
        if qty:
            days.append((i, qty))
        return plans
    res = ts.backtest(close, volume, p, start, end, hooks=ts.BacktestHooks(filter_plans=watch))
    eq = res.equity
    pos = {close.index.get_loc(d): k for k, d in enumerate(eq.index)}
    pred: Dict[float, List[float]] = {lv: [] for lv in LEVELS}
    realized: List[float] = []
    for i, qty in days:
        k = pos.get(i)
        if k is None or k + 1 >= len(eq) or i < DAYS:
            continue
        assets = sorted(qty)
        w = np.array([qty[a] * float(close[a].iloc[i]) for a in assets]) / float(eq.iloc[k])
        port = rets[assets].iloc[i - DAYS + 1:i + 1].fillna(0.0).to_numpy() @ w
        for lv in LEVELS:
            pred[lv].append(prudent(port, lv)[0])
        realized.append(float(eq.iloc[k + 1] / eq.iloc[k] - 1))
    return {lv: var_backtest(pred[lv], realized, lv) for lv in LEVELS}


# ---------- Le portefeuille ----------

def _positions_frame(positions: Dict[str, Dict[str, float]], close: pd.DataFrame) -> List[str]:
    held = [a for a in sorted(positions) if a in close.columns]
    for a in held:
        px = positions[a].get("price")
        if px is None or not math.isfinite(float(px)) or float(px) <= 0:
            raise RiskDataError(f"{a.upper()} : prix du jour inconnu, le portefeuille ne peut être valorisé")
    return held


def liquidity(values: Dict[str, float], volume: Optional[pd.DataFrame], close: pd.DataFrame,
              p: ts.TrendParams, equity: float) -> Dict[str, Any]:
    """Liquidité (§19-20) : part du volume moyen de 30 jours, jours pour vendre
    à 25, 10, 5, 2 et 1 % du volume, coût de sortie (frais, glissement,
    impact en racine carrée), note de 0 à 1, VaR ajustée de ce coût."""
    rows, cost, score_w = [], 0.0, 0.0
    sig = close.pct_change().iloc[-30:].std()
    for a, v in values.items():
        adv = float(volume[a].iloc[-30:].mean()) if volume is not None and a in volume else float("nan")
        if not math.isfinite(adv) or adv <= 0:
            rows.append({"asset": a, "value": v, "adv": None, "participation": None, "days": {}, "cost_pct": None})
            continue
        part = v / adv
        impact = IMPACT_Y * float(sig.get(a, 0.0) or 0.0) * math.sqrt(part)
        c = p.fee + p.slippage + impact
        days = {x: part / x for x in PARTICIPATIONS}
        rows.append({"asset": a, "value": v, "adv": adv, "participation": part, "days": days, "cost_pct": c * 100})
        cost += v * c
        score_w += v * max(0.0, min(1.0, 1 - (days[0.10] - 1) / 9))
    known = sum(r["value"] for r in rows if r["adv"])
    unknown = [r["asset"] for r in rows if not r["adv"]]
    worst = max((r["days"][0.10] for r in rows if r["adv"]), default=0.0)
    return {"positions": rows, "exit_cost_pct": cost / equity * 100 if equity > 0 else 0.0,
            "score": score_w / known if known > 0 else (1.0 if not rows else 0.0), "unknown": unknown,
            "days_at_10pct": worst}


def reverse_stress(positions: Dict[str, Dict[str, float]], equity: float, peak: float, kill: float,
                   targets: Sequence[float] = (0.10, 0.20, 0.30, 0.50)) -> List[Dict[str, Any]]:
    """Stress inversé (§14) : la baisse uniforme des cryptos qui causerait une
    perte donnée du capital, stops sautés (cours qui passent par-dessus) ou
    tenus (vente au stop, 2 % de glissement) ; et celle qui déclencherait
    l'arrêt d'urgence (−40 % depuis le plus haut)."""
    values = {a: x["qty"] * x["price"] for a, x in positions.items()}
    invested = sum(values.values())
    gaps = {a: max(0.0, 1 - (x.get("stop") or 0.0) / x["price"]) + 0.02 for a, x in positions.items()}

    def with_stops(s: float) -> float:
        return sum(v * min(s, gaps[a]) for a, v in values.items())
    rows = []
    goals = [(t, f"perte de {fr(t * 100, '.0f')} % du capital") for t in targets]
    to_kill = 1 - (1 - kill) * peak / equity if equity > 0 else 0.0
    goals.append((max(0.0, to_kill), f"arrêt d'urgence (−{fr(kill * 100, '.0f')} % depuis le plus haut)"))
    for loss, name in goals:
        need = loss * equity
        bare = need / invested if invested > 0 else float("inf")
        held = None
        if with_stops(1.0) >= need > 0:
            lo, hi = 0.0, 1.0
            for _ in range(60):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if with_stops(mid) < need else (lo, mid)
            held = hi
        rows.append({"target": name, "loss": loss, "shock_no_stops": bare if bare <= 1 else None,
                     "shock_with_stops": held, "already": loss <= 0})
    return rows


def contributions(rets: np.ndarray, w: np.ndarray, assets: Sequence[str], level: float = 0.99) -> List[Dict[str, Any]]:
    """Contributions au risque (§25, §50) : marginale et par composante
    (covariances, la somme fait le risque du portefeuille), seule, et à la
    perte des pires jours (ES historique)."""
    cov = np.atleast_2d(np.cov(rets, rowvar=False))
    sp = math.sqrt(max(float(w @ cov @ w), 0.0))
    port = rets @ w
    q = np.quantile(port, 1 - level)
    tail = port <= q
    es = -float(port[tail].mean()) if tail.any() else 0.0
    out = []
    for k, a in enumerate(assets):
        marginal = float((cov @ w)[k] / sp) if sp > 0 else 0.0
        comp = w[k] * marginal
        tail_c = -float((rets[tail, k] * w[k]).mean()) if tail.any() else 0.0
        out.append({"asset": a, "weight": float(w[k]), "marginal": marginal, "component": comp,
                    "share": comp / sp if sp > 0 else 0.0, "standalone": float(w[k] * math.sqrt(cov[k, k])),
                    "tail_share": tail_c / es if es > 0 else 0.0})
    return sorted(out, key=lambda x: -x["share"])


def advance(state: str, new: str) -> str:
    """Machine à états du risque (§55) ; un passage non prévu est refusé."""
    if new not in TRANSITIONS.get(state, ()):
        raise ValueError(f"passage refusé : {state} → {new}")
    return new


def assess(day: str, close: pd.DataFrame, volume: Optional[pd.DataFrame], positions: Dict[str, Dict[str, float]],
           cash: float, equity: float, peak: float, p: ts.TrendParams, risk_mult: float = 1.0,
           context: Optional[Dict[str, Any]] = None, phase: str = "PRE", mode: str = "paper",
           history: Sequence[Tuple[float, float]] = (), now: Optional[datetime] = None,
           previous: str = "RISK_UNKNOWN") -> RiskAssessment:
    """L'évaluation complète du risque (RiskAssessment.v1, §47, §77) :
    mesures, composantes et leur niveau, limites, alertes, note, état et
    décision, valable jusqu'à la décision suivante. Données critiques
    invalides : état bloqué, raison dite. `history` : (VaR 95 % annoncée,
    rendement réalisé le lendemain) des évaluations passées."""
    ctx = dict(context or {})
    now = now or datetime.now(timezone.utc)
    kill = float(ctx.get("kill", 0.40))
    kill = kill if 0 < kill < 1 else 0.40
    path = [advance(previous if previous in ("RISK_UNKNOWN",) + STATES[3:] else "RISK_UNKNOWN",
                    "RISK_CALCULATING")]
    expires = now + timedelta(hours=VALID_HOURS)
    base = {"assessment_id": str(uuid.uuid4()), "day": day, "phase": phase, "mode": mode,
            "model_version": MODEL_VERSION, "limits_version": LIMITS_VERSION,
            "created_at": now.isoformat(timespec="seconds"), "expires_at": expires.isoformat(timespec="seconds")}
    try:
        if not (math.isfinite(equity) and equity > 0):
            raise RiskDataError("capital inconnu ou nul")
        held = _positions_frame(positions, close)
    except RiskDataError as e:
        path.append(advance(path[-1], "RISK_BLOCKED"))
        return RiskAssessment(**base, state="RISK_BLOCKED", approval="RISK_BLOCKED", path=tuple(path), score=1.0,
                              confidence=0.0, equity=float(equity) if math.isfinite(equity) else 0.0,
                              metrics={}, components={}, limits=(), warnings=(),
                              violations=(f"données critiques invalides : {e}",), alerts=(("EMERGENCY", "DATA_INVALID",
                                                                                         str(e)),),
                              required_actions=("aucun achat tant que le risque n'est pas mesurable",),
                              data_hash="")
    path.append(advance(path[-1], "RISK_VALIDATING"))
    values = {a: positions[a]["qty"] * positions[a]["price"] for a in held}
    invested = sum(values.values())
    w = np.array([values[a] / equity for a in held])
    window = close[held].pct_change().iloc[-DAYS:] if held else pd.DataFrame()
    rets = window.dropna(how="all").fillna(0.0).to_numpy() if held else np.zeros((0, 0))
    btc = close["btc"].pct_change().iloc[-DAYS:].reindex(window.index).fillna(0.0).to_numpy() \
        if "btc" in close and held else None
    metrics: Dict[str, Any] = {"invested_pct": invested / equity * 100, "cash_pct": cash / equity * 100,
                               "positions": len(held), "gross_leverage": invested / equity, "net_leverage": invested / equity}
    warnings: List[str] = []
    alerts: List[Tuple[str, str, str]] = []
    comps: Dict[str, str] = {}
    measured = held and len(rets) >= MIN_DAYS
    if held and not measured:
        warnings.append(f"historique trop court ({len(rets)} jours) : VaR non mesurée")
    if measured:
        port = rets @ w
        mu, sd, skew, exk = moments(port)
        var_rows = {}
        for lv in LEVELS:
            for h in HORIZONS:
                hv, he = var_es(horizon_returns(port, h), lv)
                nv, ne = parametric(mu, sd, lv, h)
                tv, te, nu = student(mu, sd, exk, lv, h)
                mv, me = montecarlo(rets, w, lv, h, sims=MC_SIMS if h == 1 else 2000)
                var_rows[f"{int(lv * 100)}_{h}"] = {"historical": (hv, he), "normal": (nv, ne), "student": (tv, te),
                                                    "montecarlo": (mv, me)}
        metrics["var"] = var_rows
        metrics["student_nu"] = student(mu, sd, exk, 0.99)[2]
        v95, e95 = (max(var_rows["95_1"][k][i] for k in HEADLINE) for i in (0, 1))
        v99, e99 = (max(var_rows["99_1"][k][i] for k in HEADLINE) for i in (0, 1))
        methods = [x[0] for x in var_rows["99_1"].values() if x[0] > 0]
        disagreement = max(methods) / min(methods) if methods else 1.0
        metrics.update({"var95_pct": v95 * 100, "es95_pct": e95 * 100, "var99_pct": v99 * 100, "es99_pct": e99 * 100,
                        "method_spread": disagreement})
        ew = float(np.sqrt(pd.Series(port).ewm(alpha=1 - EWMA_LAMBDA, adjust=False).var().iloc[-1]))
        vol = {"daily": sd, "weekly": sd * math.sqrt(7), "monthly": sd * math.sqrt(30), "annual": sd * math.sqrt(365),
               "ewma_daily": ew, "forecast_annual": ew * math.sqrt(365), "recent_ratio": float(np.std(port[-30:]) / sd)
               if sd > 0 else 1.0}
        flags = []
        if ew > 1.5 * sd:
            flags.append("VOLATILITY_EXPANSION")
        if ew < sd / 1.5:
            flags.append("VOLATILITY_COMPRESSION")
        if sd > 0 and abs(port[-1]) > 4 * sd:
            flags.append("VOLATILITY_BREAKOUT")
        if vol["recent_ratio"] > 2:
            flags.append("VOLATILITY_REGIME_CHANGE")
        vol["flags"] = flags
        metrics["volatility"] = vol
        long_w = close[held].pct_change().iloc[-TAIL_DAYS:].dropna(how="all").fillna(0.0).to_numpy() @ w
        _m, _s, lskew, lexk = moments(long_w)
        metrics["tail"] = {"skew": lskew, "excess_kurtosis": lexk, "hill": hill(long_w), "pot": pot(long_w),
                           "jumps": int((np.abs(long_w) > 4 * max(_s, 1e-12)).sum()), "days": int(len(long_w)),
                           "worst_day_pct": float(long_w.min() * 100) if len(long_w) else 0.0}
        metrics["correlation"] = correlations(rets, btc)
        cov = np.atleast_2d(np.cov(rets, rowvar=False))
        sp = math.sqrt(max(float(w @ cov @ w), 0.0))
        stand = float(np.sum(w * np.sqrt(np.diag(cov))))
        metrics["contributions"] = contributions(rets, w, held)
        reg = ts.btc_regime(close["btc"], p).reindex(window.index).fillna(False).to_numpy() if "btc" in close else None
        if reg is not None and reg.size == len(port):
            same = reg == reg[-1]
            if same.sum() >= MIN_DAYS:
                metrics["regime_var95_pct"] = var_es(port[same], 0.95)[0] * 100
                metrics["regime"] = "BULL" if reg[-1] else "BEAR"
        comps["market"] = _level(v99 * 100, THRESHOLDS["market"])
        comps["volatility"] = _level(vol["forecast_annual"] * 100, THRESHOLDS["volatility"])
        hl = metrics["tail"]["hill"]
        comps["tail"] = "MEDIUM" if hl is None else "LOW" if hl >= 4 else "MEDIUM" if hl >= 3 else "HIGH" if hl >= 2 \
            else "CRITICAL"
        corr = metrics["correlation"].get("pearson")
        metrics["concentration_corr_level"] = _level(corr, THRESHOLDS["correlation"]) if corr is not None else "LOW"
        dr = stand / sp if sp > 0 else 1.0
        metrics["diversification_ratio"] = dr
        metrics["effective_bets"] = dr ** 2
        if "VOLATILITY_EXPANSION" in flags or "VOLATILITY_BREAKOUT" in flags:
            alerts.append(("WARNING", "VOLATILITY_SPIKE", "volatilité récente nettement au-dessus de l'année"))
        c = metrics["correlation"]
        if c.get("crisis") is not None and corr is not None and c["crisis"] > corr + 0.2:
            alerts.append(("NOTICE", "CORRELATION_SPIKE", "les cryptos détenues chutent ensemble les jours de crise"))
        if disagreement > 2:
            warnings.append(f"méthodes de VaR en désaccord (de 1 à {fr(disagreement, '.1f')})")
        ex = {}
        bt = var_backtest([h[0] for h in history], [h[1] for h in history]) if history else var_backtest([], [])
        ex.update(bt)
        metrics["model"] = ex
        comps["model"] = ("MEDIUM" if bt["verdict"] == "pas assez d'observations" or disagreement > 2 else
                          "HIGH" if bt["verdict"] != "VaR bien calibrée" else "LOW")
        if bt["verdict"] == "VaR sous-estimée":
            alerts.append(("HIGH", "MODEL_DEGRADED", "la VaR annoncée est dépassée trop souvent : recalibrage requis"))
    else:
        metrics.update({"var95_pct": 0.0, "es95_pct": 0.0, "var99_pct": 0.0, "es99_pct": 0.0})
        for k in ("market", "volatility", "tail"):
            comps[k] = "LOW" if not held else "MEDIUM"
        comps["model"] = "LOW" if not held else "MEDIUM"
        metrics["concentration_corr_level"] = "LOW"
    weights_inv = np.array([values[a] / invested for a in held]) if invested > 0 else np.array([])
    hhi = float((weights_inv ** 2).sum()) if len(weights_inv) else 0.0
    largest = max(values.values()) / equity if values else 0.0
    metrics["concentration"] = {"hhi": hhi, "effective_positions": 1 / hhi if hhi > 0 else 0.0,
                                "largest_pct": largest * 100, "counterparty": "Binance",
                                "counterparty_pct": 100.0 if values else 0.0}
    conc = ("LOW" if invested / equity < 0.25 or (1 / hhi if hhi else 0) >= 4 else "MEDIUM" if 1 / hhi >= 2 else "HIGH")
    order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    comps["concentration"] = max(conc, metrics["concentration_corr_level"], key=order.index)
    liq = liquidity(values, volume, close, p, equity)
    metrics["liquidity"] = liq
    comps["liquidity"] = _level(liq["days_at_10pct"], THRESHOLDS["liquidity"]) if not liq["unknown"] else "HIGH"
    if liq["unknown"]:
        alerts.append(("WARNING", "LIQUIDITY_WARNING", "volume inconnu pour " + ", ".join(a.upper() for a in liq["unknown"])))
    metrics["lvar99_pct"] = metrics["var99_pct"] + liq["exit_cost_pct"]
    comps["leverage"] = "LOW" if invested <= equity * (1 + 1e-9) else "CRITICAL"
    dd = max(0.0, 1 - equity / peak) if peak > 0 else 0.0
    dd_state = ("NORMAL_DRAWDOWN" if dd < 0.5 * kill else "WARNING_DRAWDOWN" if dd < 0.75 * kill else
                "CRITICAL_DRAWDOWN" if dd < kill else "MAX_DRAWDOWN_BREACH")
    metrics["drawdown"] = {"peak": peak, "equity": equity, "drawdown_pct": dd * 100, "kill_pct": kill * 100,
                           "points_to_kill": (kill - dd) * 100, "state": dd_state}
    comps["drawdown"] = {"NORMAL_DRAWDOWN": "LOW", "WARNING_DRAWDOWN": "MEDIUM", "CRITICAL_DRAWDOWN": "HIGH",
                         "MAX_DRAWDOWN_BREACH": "CRITICAL"}[dd_state]
    if dd_state == "WARNING_DRAWDOWN":
        alerts.append(("WARNING", "DRAWDOWN_WARNING", f"baisse de {fr(dd * 100, '.1f')} % depuis le plus haut"))
    elif dd_state in ("CRITICAL_DRAWDOWN", "MAX_DRAWDOWN_BREACH"):
        alerts.append(("CRITICAL", "DRAWDOWN_CRITICAL", f"baisse de {fr(dd * 100, '.1f')} % depuis le plus haut"))
    initial = sum(float(positions[a].get("risk_quote") or 0.0) for a in held)
    to_stops = sum(positions[a]["qty"] * max(0.0, positions[a]["price"] - float(positions[a].get("stop") or 0.0))
                   for a in held)
    cap = p.max_total_risk * max(risk_mult, 0.0) * equity
    used = initial / cap if cap > 0 else (0.0 if initial == 0 else 99.0)
    band = next((name for top, name in BUDGET_BANDS if used < top), "BLOCK")
    metrics["budget"] = {"initial_pct": initial / equity * 100, "cap_pct": cap / equity * 100, "used": used,
                         "band": band, "to_stops_pct": to_stops / equity * 100,
                         "per_position": {a: float(positions[a].get("risk_quote") or 0.0) / equity * 100 for a in held}}
    rows = stress_tests.scenarios({a: positions[a] for a in held}, cash, equity) if held else []
    extra = []
    if measured:
        extra = [("Volatilité × 2 (VaR 99 % d'un jour)", 2 * metrics["var99_pct"]),
                 ("Volatilité × 3 (VaR 99 % d'un jour)", 3 * metrics["var99_pct"]),
                 ("Corrélations → 1 (VaR 99 % sans diversification)",
                  sum(var_es(rets[:, k] * w[k], 0.99)[0] for k in range(len(held))) * 100),
                 ("Liquidité ÷ 2 (coût de sortie doublé)", metrics["var99_pct"] + 2 * liq["exit_cost_pct"]),
                 ("Écart et glissement × 5 (sortie de tout)", invested * 5 * (p.fee + p.slippage) / equity * 100),
                 ("Pire jour des trois dernières années, rejoué", -metrics["tail"]["worst_day_pct"])]
    rows = rows + [{"name": n, "loss_pct": round(v, 2), "loss_usdt": round(v / 100 * equity, 2)} for n, v in extra]
    rows.sort(key=lambda r: -r["loss_pct"])
    metrics["stress"] = rows
    dd_now = max(0.0, 1 - equity / peak) if peak > 0 else 0.0
    after = 1 - (1 - dd_now) * (1 - STRESS_REFERENCE * invested / equity)
    metrics["stress_reference"] = {"crash_pct": STRESS_REFERENCE * 100, "drawdown_after_pct": after * 100,
                                   "kill_pct": kill * 100, "kill_triggered": after >= kill}
    comps["stress"] = _level(after * 100, THRESHOLDS["stress"])
    metrics["reverse_stress"] = reverse_stress({a: positions[a] for a in held}, equity, peak, kill) if held else []
    blocked = list(ctx.get("garde_blocked") or [])
    q = ctx.get("data_quality")
    op = "LOW"
    if ctx.get("safe_mode") or blocked:
        op = "HIGH"
    if q is not None and q < 90:
        op = max(op, "MEDIUM" if q >= 50 else "CRITICAL", key=order.index)
    comps["operational"] = op
    metrics["operational"] = {"garde_blocked": blocked, "safe_mode": bool(ctx.get("safe_mode")), "data_quality": q}
    if ctx.get("events"):
        warnings.append("annonce économique majeure dans les 48 h : " + ", ".join(ctx["events"][:2])
                        + " (prudence ; effet non prouvé)")
    if values:
        alerts.append(("NOTICE", "COUNTERPARTY_CONCENTRATION", "contrepartie unique : Binance (toutes les cryptos)"))
    limits = (("Budget de risque (6 % au plus)", used, band),
              ("Baisse depuis le plus haut (arrêt à −40 %)", dd / kill if kill > 0 else 0.0,
               "BLOCK" if dd >= kill else "RESTRICTED" if dd >= 0.75 * kill else "WARNING" if dd >= 0.5 * kill
               else "NORMAL"),
              ("Positions (8 au plus)", len(held) / max(1, p.max_positions),
               "NORMAL" if len(held) < p.max_positions else "RESTRICTED"),
              ("Taille d'une position (25 % au plus)", largest / max(p.max_position_pct, 1e-9),
               "NORMAL" if largest <= p.max_position_pct * 1.10 else "BLOCK"))
    violations = []
    if ctx.get("halted"):
        violations.append("arrêt d'urgence déclenché")
        alerts.append(("EMERGENCY", "KILL_SWITCH", "arrêt d'urgence : plus aucun achat"))
    if dd >= kill:
        violations.append(f"baisse de {fr(dd * 100, '.1f')} % : limite de {fr(kill * 100, '.0f')} % atteinte")
    if used > 1.0 + 1e-9:
        violations.append(f"budget de risque dépassé ({fr(used * 100, '.0f')} %)")
    if comps["leverage"] == "CRITICAL":
        violations.append("exposition supérieure au capital (levier) : impossible au comptant")
    score = sum(SCORE_WEIGHTS[k] * LEVEL_SCORE[comps.get(k, "MEDIUM")] for k in SCORE_WEIGHTS)
    highs = sum(1 for v in comps.values() if v == "HIGH")
    mediums = sum(1 for v in comps.values() if v == "MEDIUM")
    state = ("RISK_BLOCKED" if violations else "RISK_CRITICAL" if "CRITICAL" in comps.values() else
             "RISK_HIGH" if highs >= 2 or score >= 0.5 else
             "RISK_WARNING" if highs or mediums >= 3 or band != "NORMAL" or score >= 0.25 else "RISK_NORMAL")
    approval = ("RISK_BLOCKED" if state == "RISK_BLOCKED" else
                "RISK_RESTRICTED" if used >= 0.85 or state == "RISK_CRITICAL" else
                "RISK_APPROVED_WITH_LIMIT" if used >= 0.70 or state in ("RISK_HIGH", "RISK_WARNING") else
                "RISK_APPROVED")
    path.append(advance(path[-1], state))
    days_known = len(rets) if held else DAYS
    data_conf = min(1.0, days_known / DAYS) * ((q / 100) if q is not None else 1.0)
    model_conf = {"LOW": 1.0, "MEDIUM": 0.75, "HIGH": 0.5, "CRITICAL": 0.25}[comps.get("model", "MEDIUM")]
    metrics["uncertainty"] = {"data_confidence": data_conf, "model_confidence": model_conf,
                              "note": "une confiance n'est pas une probabilité de gain"}
    actions = []
    if state == "RISK_CRITICAL":
        actions.append("proposition : réduire l'exposition (décision humaine ; aucune vente forcée par le moteur)")
    if band in ("RESTRICTED", "BLOCK"):
        actions.append("achats limités au budget de risque restant (déjà appliqué par la règle)")
    try:
        data_hash = fingerprint(close[held + (["btc"] if "btc" in close and "btc" not in held else [])],
                                None, day)["hash"] if held else "sans-position"
    except (KeyError, IndexError, ValueError):
        data_hash = "inconnue"
    return RiskAssessment(**base, state=state, approval=approval, path=tuple(path), score=round(score, 4),
                          confidence=round(data_conf * model_conf, 4), equity=float(equity), metrics=metrics,
                          components=comps, limits=limits, warnings=tuple(warnings), violations=tuple(violations),
                          alerts=tuple(alerts), required_actions=tuple(actions), data_hash=data_hash)


# ---------- Résumé, décision du jour, explication ----------

def compact(a: RiskAssessment) -> Dict[str, Any]:
    """Ce que le bot garde de l'évaluation dans son état (panneau, rapport,
    Rachelle) : l'essentiel, sans les tableaux."""
    m = a.metrics
    contrib = m.get("contributions") or []
    stress = m.get("stress") or []
    return {"day": a.day, "phase": a.phase, "state": a.state, "approval": a.approval, "score": a.score,
            "confidence": a.confidence, "var95_pct": m.get("var95_pct"), "es95_pct": m.get("es95_pct"),
            "var99_pct": m.get("var99_pct"), "es99_pct": m.get("es99_pct"), "invested_pct": m.get("invested_pct"),
            "stress": [stress[0]["name"], stress[0]["loss_pct"]] if stress else None,
            "budget": (m.get("budget") or {}).get("used"), "drawdown_pct": (m.get("drawdown") or {}).get("drawdown_pct"),
            "top": [[c["asset"], round(c["share"], 3)] for c in contrib[:3]],
            "components": dict(a.components), "warnings": list(a.warnings[:4]), "violations": list(a.violations),
            "alerts": [list(x) for x in a.alerts[:5]], "expires_at": a.expires_at,
            "reverse": [[r["target"], r["shock_no_stops"], r["shock_with_stops"]] for r in m.get("reverse_stress") or []],
            "model": (m.get("model") or {}).get("verdict")}


def gate(view: Optional[Dict[str, Any]], day: str, now: datetime) -> Tuple[bool, str]:
    """Pour la porte d'exécution : une évaluation du jour, valide et non
    bloquée ; sinon, aucun achat (mode sûr, §46, §99)."""
    if not view or view.get("day") != day:
        return False, "évaluation du risque absente pour la décision du jour (moteur indisponible)"
    if view.get("error"):
        return False, f"moteur de risque indisponible ({view['error']})"
    try:
        expired = now >= datetime.fromisoformat(view["expires_at"])
    except (KeyError, TypeError, ValueError):
        expired = True
    if expired:
        return False, "évaluation du risque périmée"
    if view.get("approval") == "RISK_BLOCKED":
        return False, "risque bloqué : " + " ; ".join(view.get("violations") or ["raison inconnue"])
    return True, f"{STATE_FR.get(view.get('state'), view.get('state'))}, {APPROVAL_FR.get(view.get('approval'), '')}"


def incremental(pre: Optional[Dict[str, Any]], post: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Risque ajouté par les achats du jour (§26) : écarts entre l'évaluation
    d'avant et celle d'après (points de capital)."""
    if not pre or not post:
        return {}
    out = {}
    for k in ("var95_pct", "es95_pct", "var99_pct", "invested_pct", "budget"):
        a, b = pre.get(k), post.get(k)
        if a is not None and b is not None:
            out[k] = b - a
    if pre.get("stress") and post.get("stress"):
        out["stress_pct"] = post["stress"][1] - pre["stress"][1]
    return out


STATE_FR = {"RISK_NORMAL": "risque normal", "RISK_WARNING": "vigilance", "RISK_HIGH": "risque élevé",
            "RISK_CRITICAL": "risque critique", "RISK_BLOCKED": "bloqué", "RISK_UNKNOWN": "inconnu"}
APPROVAL_FR = {"RISK_APPROVED": "achats permis", "RISK_APPROVED_WITH_LIMIT": "achats permis dans les limites",
               "RISK_RESTRICTED": "achats limités au budget restant", "RISK_BLOCKED": "aucun achat"}
COMPONENT_FR = {"market": "marché (VaR)", "volatility": "volatilité", "liquidity": "liquidité",
                "concentration": "concentration", "leverage": "levier", "drawdown": "baisse", "tail": "queue",
                "stress": "stress", "model": "modèle", "operational": "opérations"}


def describe(view: Optional[Dict[str, Any]]) -> str:
    """Une phrase pour le raisonnement et le rapport (§76) : état, décision,
    VaR, pire stress, budget, premier contributeur, pourquoi."""
    if not view:
        return ""
    if view.get("error"):
        return f"Moteur de risque indisponible ({view['error']}) : aucun achat aujourd'hui (mode sûr)."
    text = (f"Moteur de risque : {STATE_FR.get(view['state'], view['state'])}, "
            f"{APPROVAL_FR.get(view['approval'], view['approval'])}")
    if view.get("var95_pct"):
        text += (f" ; VaR d'un jour {fr(view['var95_pct'], '.1f')} % (95 %), {fr(view['var99_pct'], '.1f')} % (99 %), "
                 f"perte moyenne au-delà {fr(view['es99_pct'], '.1f')} %")
    if view.get("stress"):
        text += f" ; pire stress {fr(view['stress'][1], '.0f')} % ({view['stress'][0][:1].lower()}{view['stress'][0][1:]})"
    if view.get("budget") is not None:
        text += f" ; budget de risque utilisé à {fr(view['budget'] * 100, '.0f')} %"
    if view.get("top"):
        text += f" ; premier contributeur {view['top'][0][0].upper()} ({fr(view['top'][0][1] * 100, '.0f')} %)"
    high = [COMPONENT_FR.get(k, k) for k, v in (view.get("components") or {}).items() if v in ("HIGH", "CRITICAL")]
    if high:
        text += " ; à surveiller : " + ", ".join(high)
    if view.get("violations"):
        text += " ; " + " ; ".join(view["violations"])
    return text + "."


def red_team(view: Dict[str, Any]) -> List[str]:
    """Équipe rouge (§53) : ce que l'évaluation pourrait oublier."""
    out = []
    comps = view.get("components") or {}
    if comps.get("concentration") in ("HIGH", "CRITICAL"):
        out.append("corrélation cachée : les cryptos détenues bougent ensemble, la diversification est apparente")
    if comps.get("model") != "LOW":
        out.append("modèle pas encore éprouvé sur le portefeuille réel : la VaR peut se tromper")
    if comps.get("tail") in ("HIGH", "CRITICAL"):
        out.append("queue épaisse : les pertes extrêmes sont plus fréquentes que la loi normale ne le dit")
    out.append("contrepartie unique : une panne ou une faillite de Binance n'est couverte par aucun stop")
    out.append("événement jamais vu : les stress rejouent le passé et des chocs imaginés, pas tout le futur")
    return out


# ---------- Commande ----------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `risque` : dernière évaluation (défaut) ou calibrage de la
    VaR (backtest de la règle et journal du bot)."""
    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Moteur de risque de TrendGuard")
    ap.add_argument("action", nargs="?", default="etat", choices=["etat", "calibrage"])
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    if args.action == "etat":
        st = (read_state(g.db_file) or {}).get("moteur_risque") or {}
        for phase in ("pre", "post"):
            if st.get(phase):
                print(f"{'Avant' if phase == 'pre' else 'Après'} les achats du {st[phase]['day']} : "
                      + describe(st[phase]))
        for line in red_team(st.get("post") or st.get("pre") or {}):
            print(f"- {line}")
        if not st:
            print("Aucune évaluation encore : le moteur évalue le risque à chaque décision du bot.")
        return 0
    close, volume = load_history(args.cache, list(g.universe), max_age_days=None)
    p = params_for(g)
    out = position_backtest(close, volume, p, "2018-01-01", str(close.index[-1].date()))
    for lv, bt in out.items():
        print(f"VaR {fr(lv * 100, '.0f')} % d'un jour du moteur, sur les positions de chaque jour du backtest de la "
              f"règle : {bt['exceptions']} dépassements pour {fr(bt['expected'], '.0f')} attendus sur {bt['n']} "
              f"jours, p-valeur {fr(bt['p_value'], '.3f')} (Kupiec), grappes p {fr(bt['independence_p'], '.3f')} : "
              f"{bt['verdict']}.")
    from .donnees import Journal, path_for
    path = path_for(g)
    if path and os.path.exists(path):
        j = Journal(path, readonly=True)
        pairs = j.risk_history(g.run_mode)
        j.close()
        bt = var_backtest([x for x, _r in pairs], [r for _x, r in pairs])
        print(f"Sur le journal du bot ({bt['n']} jour(s) évalué(s)) : {bt['verdict']}.")
    return 0
