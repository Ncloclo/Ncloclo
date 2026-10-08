"""
Moteur quantitatif (prompt maître, étape 8 ; docs/MOTEUR_QUANT.md) : le
laboratoire mathématique et statistique de TrendGuard. Il mesure avant de
prédire et vérifie avant de croire, sur les cours réels de Binance.

    rendements (simples, logarithmiques, cumulés, annualisés sur 365 jours)
    → statistiques descriptives → lois (normale, Laplace, Student) et
    normalité → tests d'hypothèse (et corrections des tests multiples) →
    autocorrélation, stationnarité (ADF, KPSS), persistance (ratio de
    variance) → volatilité (historique, EWMA, GARCH) → corrélations,
    covariance rétrécie, composantes principales → régression, modèle
    factoriel (bêta à BTC), cointégration → momentum et cassures : ont-ils un
    pouvoir prédictif ? → anomalies, ruptures, régimes cachés → manifeste

Consultatif : aucune de ces mesures ne change la règle en service, et le
moteur ne passe aucun ordre. Seulement numpy, pandas et la bibliothèque
standard : les lois (gamma, bêta incomplètes, Kolmogorov) sont écrites ici
et éprouvées par les tests contre des valeurs connues.

    python trendguard_bot.py quant                      # rapport complet (≈ 1 minute)
    python trendguard_bot.py quant --cache data_binance --out docs/QUANT.md
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import math
import os
import uuid
from datetime import datetime, timezone
from statistics import NormalDist
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import v29

from . import moteur_backtest as mb
from . import moteur_risque as mr
from . import trend_strategy as ts
from .contrats import PAST_NOT_PROMISE, QuantResult
from .registre import fingerprint, git_version
from .texte import fr, fr_plain

ENGINE_VERSION = "quant-1.0.0"
YEAR = 365                      # cryptos : cotées tous les jours
ALPHA = 0.05
SEED = 7
RECENT_DAYS = 1095              # trois ans pour les liens entre cryptos
HORIZONS = (10, 30, 60)         # jours après une cassure
MOM_STEP = 30                   # échantillons sans chevauchement pour le momentum
VR_LAGS = (5, 10, 30, 90)
BOOT_SIMS = 2000
ANOMALY_Z = 10.0                # écart robuste (MAD) au-delà duquel un rendement est examiné
LIMITATIONS = (
    PAST_NOT_PROMISE,
    "Cours journaliers de Binance depuis 2017 : vingt et une cryptos encore cotées (biais du survivant), "
    "ni carnet d'ordres ni données intrajournalières.",
    "Les rendements des cryptos ne suivent pas une loi normale : les tests qui la supposent sont donnés à côté "
    "de tests qui ne la supposent pas (rangs, rééchantillonnage par blocs).",
    "Une relation statistique n'est pas une cause, ni une promesse : elle a été mesurée sur le passé.",
)
_N = NormalDist()
_TINY = 1e-300


# ══════════════════════════════════════════════════════════════════════
# Lois de probabilité (sans scipy)
# ══════════════════════════════════════════════════════════════════════

def gamma_q(a: float, x: float) -> float:
    """Fonction gamma incomplète régularisée supérieure Q(a, x) (série ou
    fraction continue de Lentz)."""
    if x <= 0:
        return 1.0
    log_front = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1.0:
        ap, total, term = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * math.exp(log_front))
    b = x + 1.0 - a
    c, d = 1.0 / _TINY, 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = _TINY if abs(d) < _TINY else d
        c = b + an / c
        c = _TINY if abs(c) < _TINY else c
        d = 1.0 / d
        h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            break
    return min(1.0, math.exp(log_front) * h)


def chi2_sf(x: float, k: float) -> float:
    """P(χ²(k) > x)."""
    return gamma_q(k / 2.0, x / 2.0)


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (_TINY if abs(d) < _TINY else d)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (_TINY if abs(d) < _TINY else d)
            c = 1.0 + aa / c
            c = _TINY if abs(c) < _TINY else c
            h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            break
    return h


def beta_inc(a: float, b: float, x: float) -> float:
    """Fonction bêta incomplète régularisée I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """Fonction de répartition de la loi de Student à df degrés de liberté."""
    tail = 0.5 * beta_inc(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t >= 0 else tail


def t_pvalue(t: float, df: float) -> float:
    """p bilatérale d'une statistique de Student."""
    if not math.isfinite(t):
        return 0.0
    return beta_inc(df / 2.0, 0.5, df / (df + t * t))


def t_quantile(p: float, df: float) -> float:
    """Quantile de Student (bissection sur la répartition)."""
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def ks_pvalue(d: float, n: int) -> float:
    """p de la statistique de Kolmogorov-Smirnov (loi asymptotique avec la
    correction de Stephens)."""
    lam = (math.sqrt(n) + 0.12 + 0.11 / math.sqrt(n)) * d
    if lam < 0.2:
        return 1.0
    total = sum((-1) ** (j - 1) * math.exp(-2.0 * j * j * lam * lam) for j in range(1, 101))
    return min(1.0, max(0.0, 2.0 * total))


def _clean(x: Any) -> np.ndarray:
    a = np.asarray(x, dtype=float).ravel()
    return a[np.isfinite(a)]


# ══════════════════════════════════════════════════════════════════════
# Rendements et statistiques descriptives (§7-8)
# ══════════════════════════════════════════════════════════════════════

def returns(prices: pd.Series, kind: str = "log") -> pd.Series:
    """Rendements d'une série de cours : « simple » (P_t / P_t-1 − 1) ou
    « log » (ln(P_t / P_t-1)). Cours nul ou négatif : refusé."""
    p = prices.astype(float).dropna()
    if (p <= 0).any():
        raise ValueError("cours nul ou négatif : rendement impossible")
    if kind == "simple":
        return (p / p.shift(1) - 1.0).dropna()
    if kind == "log":
        return np.log(p / p.shift(1)).dropna()
    raise ValueError(f"rendement {kind!r} inconnu (simple ou log)")


def cumulative(simple: Sequence[float]) -> float:
    """Rendement cumulé Π(1 + R_t) − 1."""
    return float(np.prod(1.0 + _clean(simple)) - 1.0)


def annualized(total: float, days: int, frequency: int = YEAR) -> float:
    """Rendement annualisé (1 + R)^(fréquence / jours) − 1 ; la fréquence est
    toujours explicite (365 pour des cryptos)."""
    if days <= 0:
        raise ValueError("durée nulle")
    return (1.0 + total) ** (frequency / days) - 1.0


def describe(x: Sequence[float]) -> Dict[str, Any]:
    """Statistiques descriptives (§8) : moyenne, médiane, variance, écart-type,
    extrêmes, quantiles, écart interquartile, MAD, asymétrie, aplatissement
    (excès), coefficient de variation."""
    a = _clean(x)
    n = len(a)
    if n < 3:
        return {"n": n, "status": "INSUFFICIENT_DATA"}
    mean, std = float(a.mean()), float(a.std(ddof=1))
    q = np.quantile(a, [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    _m, _s, skew, kurt = mr.moments(a)
    return {"n": n, "status": "OK", "mean": mean, "median": float(q[3]), "var": std ** 2, "std": std,
            "min": float(a.min()), "max": float(a.max()), "q01": float(q[0]), "q05": float(q[1]),
            "q25": float(q[2]), "q75": float(q[4]), "q95": float(q[5]), "q99": float(q[6]),
            "iqr": float(q[4] - q[2]), "mad": float(np.median(np.abs(a - q[3]))), "skew": skew,
            "excess_kurtosis": kurt, "cv": std / abs(mean) if mean else None}


# ══════════════════════════════════════════════════════════════════════
# Lois et normalité (§9-10)
# ══════════════════════════════════════════════════════════════════════

def _t_logpdf(z: np.ndarray, nu: float) -> np.ndarray:
    return (math.lgamma((nu + 1) / 2) - math.lgamma(nu / 2) - 0.5 * math.log(nu * math.pi)
            - (nu + 1) / 2 * np.log1p(z * z / nu))


def _student_fit(a: np.ndarray) -> Tuple[float, float, float, float]:
    """(ν, position, échelle, log-vraisemblance) : grille sur ν, algorithme EM
    pour la position et l'échelle."""
    best = (0.0, 0.0, 0.0, -math.inf)
    for nu in (2.2, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0, 20.0, 30.0, 50.0):
        loc, scale = float(np.median(a)), float(a.std(ddof=1)) or 1e-12
        for _ in range(60):
            z2 = ((a - loc) / scale) ** 2
            w = (nu + 1) / (nu + z2)
            loc = float((w * a).sum() / w.sum())
            scale = math.sqrt(float((w * (a - loc) ** 2).sum() / len(a))) or 1e-12
        ll = float((_t_logpdf((a - loc) / scale, nu) - math.log(scale)).sum())
        if ll > best[3]:
            best = (nu, loc, scale, ll)
    return best


def fit_distributions(x: Sequence[float]) -> Dict[str, Any]:
    """Lois candidates (§9) ajustées par maximum de vraisemblance : normale,
    Laplace, Student (position et échelle) ; AIC, BIC, statistique de
    Kolmogorov-Smirnov ; la meilleure par l'AIC. Jamais la normale supposée."""
    a = np.sort(_clean(x))
    n = len(a)
    if n < 30:
        return {"n": n, "status": "INSUFFICIENT_DATA"}
    emp_hi, emp_lo = np.arange(1, n + 1) / n, np.arange(0, n) / n

    def ks(cdf: np.ndarray) -> float:
        return float(max(np.max(emp_hi - cdf), np.max(cdf - emp_lo)))

    mu, sigma = float(a.mean()), float(a.std(ddof=0))
    out: Dict[str, Dict[str, Any]] = {}
    ll = float(-0.5 * n * (math.log(2 * math.pi * sigma ** 2) + 1))
    out["normal"] = {"params": {"mu": mu, "sigma": sigma}, "loglik": ll, "k": 2,
                     "ks": ks(np.array([_N.cdf((v - mu) / sigma) for v in a]))}
    med = float(np.median(a))
    b = float(np.mean(np.abs(a - med))) or 1e-12
    out["laplace"] = {"params": {"mu": med, "b": b}, "loglik": float(-n * math.log(2 * b) - np.abs(a - med).sum() / b),
                      "k": 2, "ks": ks(np.where(a < med, 0.5 * np.exp((a - med) / b),
                                               1 - 0.5 * np.exp(-(a - med) / b)))}
    nu, loc, scale, ll_t = _student_fit(a)
    out["student"] = {"params": {"nu": nu, "loc": loc, "scale": scale}, "loglik": ll_t, "k": 3,
                      "ks": ks(np.array([t_cdf((v - loc) / scale, nu) for v in a]))}
    for d in out.values():
        d["aic"] = 2 * d["k"] - 2 * d["loglik"]
        d["bic"] = d["k"] * math.log(n) - 2 * d["loglik"]
        d["ks_p"] = ks_pvalue(d["ks"], n)
    best = min(out, key=lambda k: out[k]["aic"])
    return {"n": n, "status": "OK", "fits": out, "best": best}


def jarque_bera(x: Sequence[float]) -> Dict[str, Any]:
    """Jarque-Bera : JB = n/6 (S² + K²/4), K en excès ; p = P(χ²(2) > JB)."""
    a = _clean(x)
    n = len(a)
    _m, _s, skew, kurt = mr.moments(a)
    jb = n / 6.0 * (skew ** 2 + kurt ** 2 / 4.0)
    return {"statistic": jb, "p_value": math.exp(-jb / 2.0), "n": n}


def anderson_darling(x: Sequence[float]) -> Dict[str, Any]:
    """Anderson-Darling contre une normale aux paramètres estimés, statistique
    corrigée A*² et p de D'Agostino et Stephens."""
    a = np.sort(_clean(x))
    n = len(a)
    z = (a - a.mean()) / a.std(ddof=1)
    f = np.clip(np.array([_N.cdf(v) for v in z]), 1e-15, 1 - 1e-15)
    i = np.arange(1, n + 1)
    a2 = float(-n - np.mean((2 * i - 1) * (np.log(f) + np.log(1 - f[::-1]))))
    s = a2 * (1 + 0.75 / n + 2.25 / n ** 2)
    if s >= 0.6:
        p = math.exp(1.2937 - 5.709 * s + 0.0186 * s * s)
    elif s >= 0.34:
        p = math.exp(0.9177 - 4.279 * s - 1.38 * s * s)
    elif s >= 0.2:
        p = 1 - math.exp(-8.318 + 42.796 * s - 59.938 * s * s)
    else:
        p = 1 - math.exp(-13.436 + 101.14 * s - 223.73 * s * s)
    return {"statistic": s, "p_value": min(1.0, max(0.0, p)), "n": n}


def normality(x: Sequence[float], alpha: float = ALPHA) -> Dict[str, Any]:
    """NORMAL, NON_NORMAL ou INSUFFICIENT_DATA (§10) : rejetée si
    Jarque-Bera ou Anderson-Darling rejette au seuil alpha."""
    a = _clean(x)
    if len(a) < 30:
        return {"status": "INSUFFICIENT_DATA", "n": len(a)}
    jb, ad = jarque_bera(a), anderson_darling(a)
    reject = jb["p_value"] < alpha or ad["p_value"] < alpha
    return {"status": "NON_NORMAL" if reject else "NORMAL", "n": len(a), "jarque_bera": jb,
            "anderson_darling": ad}


# ══════════════════════════════════════════════════════════════════════
# Tests d'hypothèse (§11-12)
# ══════════════════════════════════════════════════════════════════════

def _test(name: str, h0: str, h1: str, stat: float, p: float, n: int, effect: Optional[float],
          alpha: float) -> Dict[str, Any]:
    return {"test_name": name, "null_hypothesis": h0, "alternative_hypothesis": h1, "statistic": stat,
            "p_value": p, "alpha": alpha, "decision": "REJECT_H0" if p < alpha else "KEEP_H0",
            "effect_size": effect, "sample_size": n}


def t_test(x: Sequence[float], mu0: float = 0.0, alpha: float = ALPHA) -> Dict[str, Any]:
    """Test de Student sur la moyenne (bilatéral) ; effet = d de Cohen. Un p
    sous alpha n'est jamais une preuve absolue (§11)."""
    a = _clean(x)
    n = len(a)
    sd = float(a.std(ddof=1)) if n > 1 else 0.0
    if n < 3 or sd == 0:
        return _test("t de Student", f"moyenne = {mu0}", f"moyenne ≠ {mu0}", float("nan"), 1.0, n, None, alpha)
    t = (float(a.mean()) - mu0) / (sd / math.sqrt(n))
    return _test("t de Student", f"moyenne = {mu0}", f"moyenne ≠ {mu0}", t, t_pvalue(t, n - 1), n,
                 (float(a.mean()) - mu0) / sd, alpha)


def welch(x: Sequence[float], y: Sequence[float], alpha: float = ALPHA) -> Dict[str, Any]:
    """Test de Welch (variances inégales), degrés de liberté de Satterthwaite."""
    a, b = _clean(x), _clean(y)
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    t = (a.mean() - b.mean()) / math.sqrt(va + vb)
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    pooled = math.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2.0)
    return _test("Welch", "moyennes égales", "moyennes différentes", float(t), t_pvalue(float(t), df),
                 len(a) + len(b), float((a.mean() - b.mean()) / pooled) if pooled else None, alpha)


def _ranks(x: np.ndarray) -> np.ndarray:
    """Rangs moyens (ex aequo partagés)."""
    return pd.Series(x).rank().to_numpy()


def mann_whitney(x: Sequence[float], y: Sequence[float], alpha: float = ALPHA) -> Dict[str, Any]:
    """Mann-Whitney (rangs, sans supposer de loi), approximation normale avec
    correction des ex aequo ; effet = P(X > Y)."""
    a, b = _clean(x), _clean(y)
    n1, n2 = len(a), len(b)
    allv = np.concatenate([a, b])
    r = _ranks(allv)
    u1 = float(r[:n1].sum() - n1 * (n1 + 1) / 2.0)
    n = n1 + n2
    _vals, counts = np.unique(allv, return_counts=True)
    tie = float(((counts ** 3) - counts).sum())
    sd = math.sqrt(n1 * n2 / 12.0 * ((n + 1) - tie / (n * (n - 1))))
    mean = n1 * n2 / 2.0
    z = (u1 - mean - math.copysign(0.5, u1 - mean)) / sd if sd else 0.0
    return _test("Mann-Whitney", "mêmes lois", "lois décalées", z, 2 * (1 - _N.cdf(abs(z))), n,
                 u1 / (n1 * n2), alpha)


def adjust(pvalues: Sequence[float], method: str) -> List[float]:
    """Tests multiples (§12) : bonferroni, holm ou bh (Benjamini-Hochberg),
    ceux du moteur de backtest."""
    return mb.adjust_pvalues(list(pvalues), method)


# ══════════════════════════════════════════════════════════════════════
# Séries temporelles, stationnarité, persistance (§13-15)
# ══════════════════════════════════════════════════════════════════════

def acf(x: Sequence[float], nlags: int = 10) -> List[float]:
    """Autocorrélations des retards 1 à nlags."""
    a = _clean(x)
    a = a - a.mean()
    denom = float((a * a).sum())
    return [float((a[k:] * a[:-k]).sum() / denom) for k in range(1, nlags + 1)]


def pacf(x: Sequence[float], nlags: int = 10) -> List[float]:
    """Autocorrélations partielles (récurrence de Durbin-Levinson)."""
    rho = [1.0] + acf(x, nlags)
    phi = np.zeros((nlags + 1, nlags + 1))
    out = []
    for k in range(1, nlags + 1):
        num = rho[k] - sum(phi[k - 1, j] * rho[k - j] for j in range(1, k))
        den = 1.0 - sum(phi[k - 1, j] * rho[j] for j in range(1, k))
        phi[k, k] = num / den if den else 0.0
        for j in range(1, k):
            phi[k, j] = phi[k - 1, j] - phi[k, k] * phi[k - 1, k - j]
        out.append(float(phi[k, k]))
    return out


def ljung_box(x: Sequence[float], lags: int = 10) -> Dict[str, Any]:
    """Ljung-Box : Q = n(n+2) Σ ρ_k² / (n−k) ; p = P(χ²(lags) > Q)."""
    n = len(_clean(x))
    rho = acf(x, lags)
    q = n * (n + 2) * sum(r * r / (n - k) for k, r in enumerate(rho, 1))
    return {"statistic": q, "p_value": chi2_sf(q, lags), "lags": lags, "n": n}


ADF_CV = {"1%": (-3.43035, -6.5393, -16.786, -79.433), "5%": (-2.86154, -2.8903, -4.234, -40.040),
          "10%": (-2.56677, -1.5384, -2.809, 0.0)}          # MacKinnon (2010), avec constante
EG_CV = {"1%": (-3.89644, -10.9519, -22.527, 0.0), "5%": (-3.33613, -6.1101, -6.823, 0.0),
         "10%": (-3.04445, -4.2412, -2.720, 0.0)}           # Engle-Granger, deux séries, avec constante
KPSS_CV = {"10%": 0.347, "5%": 0.463, "2.5%": 0.574, "1%": 0.739}


def _lstsq(y: np.ndarray, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    cov = np.linalg.pinv(X.T @ X) * float(resid @ resid) / max(len(y) - X.shape[1], 1)
    return beta, resid, cov


def adf(x: Sequence[float], maxlag: Optional[int] = None, cv: Optional[Dict[str, Tuple[float, ...]]] = None,
        constant: bool = True) -> Dict[str, Any]:
    """Dickey-Fuller augmenté (§14) : Δy_t = c + γ y_t-1 + Σ β_i Δy_t-i + ε,
    retards choisis par l'AIC ; statistique t de γ comparée aux valeurs
    critiques de MacKinnon (2010). H0 : racine unitaire (non stationnaire)."""
    y = _clean(x)
    n = len(y)
    if n < 30:
        return {"status": "INSUFFICIENT_DATA", "n": n}
    maxlag = int(12 * (n / 100) ** 0.25) if maxlag is None else maxlag
    dy = np.diff(y)

    def design(k: int, start: int) -> Tuple[np.ndarray, np.ndarray]:
        rows = range(start, len(dy))
        cols = [np.array([y[t] for t in rows])] + [np.array([dy[t - i] for t in rows]) for i in range(1, k + 1)]
        X = np.column_stack(([np.ones(len(rows))] if constant else []) + cols)
        return dy[start:], X

    best_k, best_aic = 0, math.inf
    for k in range(0, maxlag + 1):
        yy, X = design(k, maxlag)
        _b, resid, _c = _lstsq(yy, X)
        m = len(yy)
        aic = m * math.log(float(resid @ resid) / m) + 2 * X.shape[1]
        if aic < best_aic:
            best_k, best_aic = k, aic
    yy, X = design(best_k, best_k)
    beta, _resid, cov = _lstsq(yy, X)
    j = 1 if constant else 0
    stat = float(beta[j] / math.sqrt(cov[j, j]))
    t_obs = len(yy)
    crit = {lvl: c[0] + c[1] / t_obs + c[2] / t_obs ** 2 + c[3] / t_obs ** 3 for lvl, c in (cv or ADF_CV).items()}
    band = next((f"p < {lvl[:-1]} %" for lvl in ("1%", "5%", "10%") if stat < crit[lvl]), "p > 10 %")
    return {"status": "OK", "statistic": stat, "lags": best_k, "n": t_obs, "critical": crit, "p_band": band,
            "reject_unit_root": stat < crit["5%"]}


def kpss(x: Sequence[float]) -> Dict[str, Any]:
    """KPSS autour du niveau (§14) : H0 stationnaire ; variance de long terme
    de Newey-West, retards 12 (n/100)^¼."""
    y = _clean(x)
    n = len(y)
    if n < 30:
        return {"status": "INSUFFICIENT_DATA", "n": n}
    e = y - y.mean()
    s = np.cumsum(e)
    lags = int(12 * (n / 100) ** 0.25)
    lrv = float(e @ e) / n
    for k in range(1, lags + 1):
        lrv += 2 * (1 - k / (lags + 1)) * float(e[k:] @ e[:-k]) / n
    stat = float((s @ s) / (n * n * lrv))
    return {"status": "OK", "statistic": stat, "lags": lags, "n": n, "critical": KPSS_CV,
            "reject_stationarity": stat > KPSS_CV["5%"]}


def stationarity(x: Sequence[float]) -> Dict[str, Any]:
    """STATIONARY, NON_STATIONARY, UNCERTAIN ou INSUFFICIENT_DATA : ADF et
    KPSS doivent être d'accord pour conclure (§14)."""
    a, k = adf(x), kpss(x)
    if a["status"] != "OK" or k["status"] != "OK":
        return {"status": "INSUFFICIENT_DATA", "adf": a, "kpss": k}
    if a["reject_unit_root"] and not k["reject_stationarity"]:
        status = "STATIONARY"
    elif not a["reject_unit_root"] and k["reject_stationarity"]:
        status = "NON_STATIONARY"
    else:
        status = "UNCERTAIN"
    return {"status": status, "adf": a, "kpss": k}


def variance_ratio(r: Sequence[float], q: int) -> Dict[str, Any]:
    """Ratio de variance de Lo et MacKinlay (§13, §28) : variance des
    rendements sur q jours divisée par q fois celle d'un jour, avec la
    statistique z robuste à l'hétéroscédasticité. VR > 1 : les mouvements se
    prolongent (tendance) ; VR < 1 : ils se corrigent (retour à la moyenne)."""
    a = _clean(r)
    n = len(a)
    if n < 5 * q:
        return {"status": "INSUFFICIENT_DATA", "q": q, "n": n}
    mu = a.mean()
    c = a - mu
    var1 = float(c @ c) / (n - 1)
    sums = np.convolve(a, np.ones(q), mode="valid") - q * mu
    m = q * (n - q + 1) * (1 - q / n)
    vr = float(sums @ sums) / m / var1
    denom = float(c @ c) ** 2
    theta = 0.0
    for j in range(1, q):
        delta = n * float((c[j:] ** 2) @ (c[:-j] ** 2)) / denom
        theta += (2.0 * (q - j) / q) ** 2 * delta
    z = math.sqrt(n) * (vr - 1.0) / math.sqrt(theta) if theta > 0 else 0.0
    return {"status": "OK", "q": q, "n": n, "vr": vr, "z": z, "p_value": 2 * (1 - _N.cdf(abs(z)))}


# ══════════════════════════════════════════════════════════════════════
# Volatilité (§16)
# ══════════════════════════════════════════════════════════════════════

def ewma_variance(r: Sequence[float], lam: float = mr.EWMA_LAMBDA) -> np.ndarray:
    """Variance EWMA : σ²_t = λ σ²_t-1 + (1 − λ) r²_t-1 (prévision du jour t
    faite la veille), départ sur la variance des 30 premiers jours."""
    a = _clean(r)
    out = np.empty(len(a))
    out[0] = float(np.var(a[:30])) if len(a) >= 2 else float(a[0] ** 2)
    for t in range(1, len(a)):
        out[t] = lam * out[t - 1] + (1 - lam) * a[t - 1] ** 2
    return out


def nelder_mead(f: Callable[[np.ndarray], float], x0: Sequence[float], step: float = 0.5, iters: int = 600,
                tol: float = 1e-9) -> Tuple[np.ndarray, float]:
    """Minimisation sans dérivées (simplexe de Nelder et Mead)."""
    dim = len(x0)
    pts = [np.asarray(x0, dtype=float)]
    for i in range(dim):
        p = np.asarray(x0, dtype=float).copy()
        p[i] += step
        pts.append(p)
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = np.argsort(vals)
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol:
            break
        centroid = np.mean(pts[:-1], axis=0)
        xr = centroid + (centroid - pts[-1])
        fr_ = f(xr)
        if fr_ < vals[0]:
            xe = centroid + 2 * (centroid - pts[-1])
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr_ else (xr, fr_)
        elif fr_ < vals[-2]:
            pts[-1], vals[-1] = xr, fr_
        else:
            xc = centroid + 0.5 * (pts[-1] - centroid)
            fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0] + 0.5 * (p - pts[0]) for p in pts]
                vals = [f(p) for p in pts]
    k = int(np.argmin(vals))
    return pts[k], float(vals[k])


def _garch_params(u: np.ndarray, scale: float) -> Tuple[float, float, float]:
    ev, ew = math.exp(min(u[1], 50)), math.exp(min(u[2], 50))
    return math.exp(min(u[0], 50)) * scale, ev / (1 + ev + ew), ew / (1 + ev + ew)


def _garch_path(a: np.ndarray, omega: float, alpha: float, beta: float, start: float) -> np.ndarray:
    s2 = np.empty(len(a))
    s2[0] = start
    for t in range(1, len(a)):
        s2[t] = omega + alpha * a[t - 1] ** 2 + beta * s2[t - 1]
    return s2


def garch11(r: Sequence[float]) -> Dict[str, Any]:
    """GARCH(1,1) par maximum de vraisemblance (loi normale, Nelder-Mead) :
    σ²_t = ω + α r²_t-1 + β σ²_t-1, α + β < 1 imposé. Persistance α + β et
    demi-vie d'un choc de volatilité."""
    a = _clean(r)
    a = a - a.mean()
    if len(a) < 250:
        return {"status": "INSUFFICIENT_DATA", "n": len(a)}
    var0 = float(a.var())

    def nll(u: np.ndarray) -> float:
        omega, alpha, beta = _garch_params(u, var0)
        s2 = np.maximum(_garch_path(a, omega, alpha, beta, var0), 1e-12)
        return float(0.5 * np.sum(np.log(s2) + a * a / s2))

    u, val = nelder_mead(nll, [math.log(0.05), math.log(0.1 / 0.05), math.log(0.85 / 0.05)])
    omega, alpha, beta = _garch_params(u, var0)
    pers = alpha + beta
    s2 = _garch_path(a, omega, alpha, beta, var0)
    return {"status": "OK", "omega": omega, "alpha": alpha, "beta": beta, "persistence": pers,
            "half_life_days": math.log(0.5) / math.log(pers) if 0 < pers < 1 else None,
            "long_run_vol": math.sqrt(omega / (1 - pers) * YEAR) if pers < 1 else None,
            "forecast_vol": math.sqrt((omega + alpha * a[-1] ** 2 + beta * s2[-1]) * YEAR),
            "loglik": -val - 0.5 * len(a) * math.log(2 * math.pi), "n": len(a)}


def vol_forecasts(r: Sequence[float], split: float = 0.7) -> Dict[str, Any]:
    """Prévisions de la volatilité du lendemain comparées hors échantillon
    (§16) : GARCH (ajusté sur les 70 premiers pour cent), EWMA (λ = 0,94),
    historique de 30 jours ; pertes QLIKE et quadratique (plus bas = mieux)."""
    a = _clean(r)
    a = a - a.mean()
    cut = int(len(a) * split)
    if cut < 250 or len(a) - cut < 100:
        return {"status": "INSUFFICIENT_DATA", "n": len(a)}
    g = garch11(a[:cut])
    s_g = _garch_path(a, g["omega"], g["alpha"], g["beta"], float(a[:cut].var()))
    s_e = ewma_variance(a)
    s_h = pd.Series(a * a).rolling(30).mean().shift(1).bfill().values
    out = {"status": "OK", "n_test": len(a) - cut, "models": {}}
    real = a[cut:] ** 2
    for name, s in (("GARCH(1,1)", s_g), ("EWMA", s_e), ("historique 30 jours", s_h)):
        s = np.maximum(s[cut:], 1e-12)
        out["models"][name] = {"qlike": float(np.mean(np.log(s) + real / s)), "mse": float(np.mean((real - s) ** 2))}
    out["best"] = min(out["models"], key=lambda k: out["models"][k]["qlike"])
    return out


# ══════════════════════════════════════════════════════════════════════
# Corrélation, covariance, composantes principales (§17-19, §24)
# ══════════════════════════════════════════════════════════════════════

def ledoit_wolf(X: np.ndarray) -> Dict[str, Any]:
    """Covariance rétrécie de Ledoit et Wolf (2004) vers une identité mise à
    l'échelle : Σ* = δ m I + (1 − δ) S ; δ estimé. Avec la covariance
    échantillon, symétrie, positivité et conditionnement des deux."""
    Xc = X - X.mean(axis=0)
    n, p = Xc.shape
    S = Xc.T @ Xc / n
    m = float(np.trace(S)) / p
    d2 = float(((S - m * np.eye(p)) ** 2).sum()) / p
    b_bar = sum(float(((np.outer(x, x) - S) ** 2).sum()) for x in Xc) / (n * n) / p
    delta = min(b_bar, d2) / d2 if d2 > 0 else 1.0
    shrunk = delta * m * np.eye(p) + (1 - delta) * S
    return {"shrinkage": delta, "sample": S, "shrunk": shrunk, "sample_health": matrix_health(S),
            "shrunk_health": matrix_health(shrunk)}


def matrix_health(m: np.ndarray) -> Dict[str, Any]:
    """Symétrie, positivité (valeur propre minimale) et conditionnement."""
    eig = np.linalg.eigvalsh((m + m.T) / 2)
    return {"symmetric": bool(np.allclose(m, m.T)), "psd": bool(eig.min() > -1e-12),
            "min_eigenvalue": float(eig.min()),
            "condition": float(eig.max() / eig.min()) if eig.min() > 0 else math.inf}


def pca(R: np.ndarray, names: Sequence[str]) -> Dict[str, Any]:
    """Composantes principales de la matrice de corrélation (§24) : part de
    variance de chaque composante, poids de la première, nombre effectif de
    paris indépendants (Σλ)² / Σλ²."""
    C = np.corrcoef(R, rowvar=False)
    val, vec = np.linalg.eigh(C)
    order = np.argsort(val)[::-1]
    val, vec = val[order], vec[:, order]
    if vec[:, 0].sum() < 0:
        vec[:, 0] = -vec[:, 0]
    share = val / val.sum()
    return {"explained": [float(s) for s in share], "pc1_loadings": dict(zip(names, (float(v) for v in vec[:, 0]))),
            "effective_bets": float(val.sum() ** 2 / (val ** 2).sum()), "n_assets": len(names),
            "mean_correlation": float(C[np.triu_indices(len(names), 1)].mean())}


def dependence(a: np.ndarray, b: np.ndarray, crash: float = -0.05) -> Dict[str, Any]:
    """Liens entre deux séries de rendements (§17-19) : Pearson, Spearman,
    Kendall, et corrélation les jours de chute de b (sous `crash`) contre
    les autres jours. La corrélation n'est pas la seule dépendance."""
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    out = {"n": len(a), "pearson": float(np.corrcoef(a, b)[0, 1]),
           "spearman": float(np.corrcoef(_ranks(a), _ranks(b))[0, 1]),
           "kendall": mr.kendall(a[-500:], b[-500:])}
    down = b < crash
    out["crash_days"] = int(down.sum())
    out["crash_corr"] = float(np.corrcoef(a[down], b[down])[0, 1]) if down.sum() >= 15 else None
    out["calm_corr"] = float(np.corrcoef(a[~down], b[~down])[0, 1])
    return out


# ══════════════════════════════════════════════════════════════════════
# Régression, facteurs, cointégration (§20-27)
# ══════════════════════════════════════════════════════════════════════

def ols(y: Sequence[float], X: np.ndarray, names: Sequence[str], hac_lags: int = 0) -> Dict[str, Any]:
    """Moindres carrés ordinaires (§20-21) : coefficients, erreurs types
    (Newey-West si hac_lags > 0), t, p, intervalles à 95 %, R² et R² ajusté,
    AIC, BIC, Durbin-Watson (autocorrélation des résidus), Breusch-Pagan
    (hétéroscédasticité), VIF (multicolinéarité). X contient la constante."""
    y = np.asarray(y, dtype=float)
    n, k = X.shape
    beta, resid, cov = _lstsq(y, X)
    if hac_lags > 0:
        xtx_inv = np.linalg.pinv(X.T @ X)
        xu = X * resid[:, None]
        meat = xu.T @ xu
        for lag in range(1, hac_lags + 1):
            w = 1 - lag / (hac_lags + 1)
            g = xu[lag:].T @ xu[:-lag]
            meat += w * (g + g.T)
        cov = xtx_inv @ meat @ xtx_inv
    se = np.sqrt(np.maximum(np.diag(cov), 0))
    df = n - k
    tq = t_quantile(0.975, df)
    sse = float(resid @ resid)
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - sse / sst if sst else 0.0
    ll = -n / 2 * (math.log(2 * math.pi) + math.log(sse / n) + 1)
    e2 = resid ** 2
    bp_beta, bp_resid, _c = _lstsq(e2, X)
    bp_r2 = 1 - float(bp_resid @ bp_resid) / float(((e2 - e2.mean()) ** 2).sum())
    vif = {}
    for j in range(1, k):
        others = np.delete(X, j, axis=1)
        _b, rj, _c = _lstsq(X[:, j], others)
        r2j = 1 - float(rj @ rj) / float(((X[:, j] - X[:, j].mean()) ** 2).sum())
        vif[names[j]] = 1 / (1 - r2j) if r2j < 1 else math.inf
    coefs = {}
    for j, name in enumerate(names):
        t = float(beta[j] / se[j]) if se[j] > 0 else math.nan
        coefs[name] = {"coef": float(beta[j]), "se": float(se[j]), "t": t,
                       "p": t_pvalue(t, df) if math.isfinite(t) else 1.0,
                       "ci95": (float(beta[j] - tq * se[j]), float(beta[j] + tq * se[j]))}
    return {"n": n, "k": k, "coefficients": coefs, "r2": r2, "adj_r2": 1 - (1 - r2) * (n - 1) / df,
            "aic": 2 * k - 2 * ll, "bic": k * math.log(n) - 2 * ll,
            "durbin_watson": float((np.diff(resid) ** 2).sum() / sse),
            "breusch_pagan": {"lm": n * bp_r2, "p_value": chi2_sf(n * bp_r2, k - 1)}, "vif": vif,
            "residual_std": math.sqrt(sse / df), "hac_lags": hac_lags}


def factor_model(asset: np.ndarray, market: np.ndarray) -> Dict[str, Any]:
    """Modèle factoriel à un facteur (§22-23) : r = α + β r_BTC + ε ; part du
    risque expliquée par BTC (R²), volatilité propre annualisée, α annualisé
    et sa statistique t."""
    ok = np.isfinite(asset) & np.isfinite(market)
    y, x = asset[ok], market[ok]
    res = ols(y, np.column_stack([np.ones(len(x)), x]), ("alpha", "beta"))
    c = res["coefficients"]
    return {"n": res["n"], "beta": c["beta"]["coef"], "beta_ci": c["beta"]["ci95"], "r2": res["r2"],
            "alpha_annual": c["alpha"]["coef"] * YEAR, "alpha_t": c["alpha"]["t"], "alpha_p": c["alpha"]["p"],
            "idio_vol": res["residual_std"] * math.sqrt(YEAR)}


def engle_granger(y: Sequence[float], x: Sequence[float]) -> Dict[str, Any]:
    """Cointégration d'Engle et Granger (§26-27) : régression y = a + b x, ADF
    sur les résidus avec les valeurs critiques de deux séries ; si l'écart est
    stationnaire, sa demi-vie de retour à la moyenne et son z-score actuel."""
    y, x = np.asarray(y, dtype=float), np.asarray(x, dtype=float)
    ok = np.isfinite(y) & np.isfinite(x)
    y, x = y[ok], x[ok]
    reg = ols(y, np.column_stack([np.ones(len(x)), x]), ("a", "b"))
    spread = y - reg["coefficients"]["a"]["coef"] - reg["coefficients"]["b"]["coef"] * x
    test = adf(spread, cv=EG_CV, constant=False)
    lagged, delta = spread[:-1], np.diff(spread)
    k = float(np.polyfit(lagged, delta, 1)[0])
    half = -math.log(2) / math.log(1 + k) if -1 < k < 0 else None
    z = float((spread[-1] - spread[-250:].mean()) / spread[-250:].std(ddof=1))
    return {"n": len(y), "hedge_ratio": reg["coefficients"]["b"]["coef"], "adf": test,
            "cointegrated": bool(test.get("reject_unit_root")), "half_life_days": half, "zscore": z}


# ══════════════════════════════════════════════════════════════════════
# Momentum et cassures : pouvoir prédictif (§28, §43, §52)
# ══════════════════════════════════════════════════════════════════════

def cluster_bootstrap(values: np.ndarray, clusters: np.ndarray, sims: int = BOOT_SIMS,
                      seed: int = SEED) -> Dict[str, Any]:
    """Moyenne et intervalle à 95 % par rééchantillonnage de grappes (mois) :
    des observations d'un même mois ne sont pas indépendantes. p bilatérale
    de « moyenne = 0 »."""
    rng = np.random.default_rng(seed)
    keys = np.unique(clusters)
    groups = [values[clusters == k] for k in keys]
    sums = np.array([g.sum() for g in groups])
    sizes = np.array([len(g) for g in groups])
    draws = rng.integers(0, len(keys), size=(sims, len(keys)))
    means = sums[draws].sum(axis=1) / sizes[draws].sum(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    p = 2 * min(float((means <= 0).mean()), float((means >= 0).mean()))
    return {"mean": float(values.mean()), "ci95": (float(lo), float(hi)), "p_value": min(1.0, max(p, 1 / sims)),
            "n": len(values), "clusters": len(keys)}


def _eligible(f: Dict[str, np.ndarray], p: ts.TrendParams) -> np.ndarray:
    c, v30, age = f["close"], f["vol30"], f["age"]
    return np.isfinite(c) & (age >= p.min_history) & np.isfinite(v30) & (v30 >= p.min_volume_usd)


def breakout_study(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, regime: bool = True,
                   horizons: Sequence[int] = HORIZONS, seed: int = SEED) -> Dict[str, Any]:
    """Étude d'événement (§28, §52) : rendement logarithmique h jours après
    chaque signal d'achat de la règle (achat à la clôture du signal), contre
    celui de tous les jours où la même crypto était achetable. Différence,
    intervalle par grappes mensuelles, tests multiples (Holm) sur les
    horizons."""
    cols, reg = ts.precompute(close, volume, p)
    months = close.index.year.values * 12 + close.index.month.values
    out: Dict[str, Any] = {"regime_filter": regime, "horizons": {}}
    for h in horizons:
        ev, base, ev_m, base_m = [], [], [], []
        for a, f in cols.items():
            c = f["close"]
            fwd = np.full(len(c), np.nan)
            fwd[:-h] = np.log(c[h:] / c[:-h])
            elig = _eligible(f, p) & np.isfinite(fwd)
            sig = elig & np.isfinite(f["prior_high"]) & np.isfinite(f["mom"]) & (c > f["prior_high"]) & (f["mom"] > 0)
            if regime:
                sig &= reg.astype(bool)
            ev.append(fwd[sig])
            ev_m.append(months[sig])
            base.append(fwd[elig])
            base_m.append(months[elig])
        e, b = np.concatenate(ev), np.concatenate(base)
        em = np.concatenate(ev_m)
        if len(e) < 30:
            out["horizons"][h] = {"status": "INSUFFICIENT_DATA", "n": len(e)}
            continue
        excess = e - b.mean()
        boot = cluster_bootstrap(excess, em, seed=seed)
        out["horizons"][h] = {"status": "OK", "events": len(e), "event_mean": float(e.mean()),
                              "event_median": float(np.median(e)), "event_hit": float((e > 0).mean()),
                              "base_mean": float(b.mean()), "base_hit": float((b > 0).mean()),
                              "excess": boot["mean"], "ci95": boot["ci95"], "p_value": boot["p_value"],
                              "months": boot["clusters"], "mann_whitney": mann_whitney(e, b)}
    ok = [h for h in horizons if out["horizons"][h]["status"] == "OK"]
    for h, padj in zip(ok, adjust([out["horizons"][h]["p_value"] for h in ok], "holm")):
        out["horizons"][h]["p_holm"] = padj
    return out


def momentum_study(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, horizon: int = MOM_STEP
                   ) -> Dict[str, Any]:
    """Pouvoir prédictif du momentum de la règle (§28, §43) : tous les
    `horizon` jours (sans chevauchement), parmi les cryptos achetables,
    corrélation de rangs entre le momentum et le rendement des `horizon`
    jours suivants (coefficient d'information), et écart de rendement entre
    momentum positif et négatif ; tests de Student sur ces séries."""
    cols, _reg = ts.precompute(close, volume, p)
    assets = list(cols)
    n = len(close)
    ics, spreads, dates = [], [], []
    for t in range(p.mom_n + p.min_history, n - horizon, horizon):
        moms, fwds = [], []
        for a in assets:
            f = cols[a]
            if not _eligible(f, p)[t] or not np.isfinite(f["mom"][t]) or not np.isfinite(f["close"][t + horizon]):
                continue
            moms.append(f["mom"][t])
            fwds.append(math.log(f["close"][t + horizon] / f["close"][t]))
        if len(moms) < 5:
            continue
        m, r = np.array(moms), np.array(fwds)
        ics.append(float(np.corrcoef(_ranks(m), _ranks(r))[0, 1]))
        pos, neg = r[m > 0], r[m <= 0]
        if len(pos) >= 2 and len(neg) >= 2:
            spreads.append(float(pos.mean() - neg.mean()))
        dates.append(str(close.index[t].date()))
    if len(ics) < 10:
        return {"status": "INSUFFICIENT_DATA", "n": len(ics)}
    return {"status": "OK", "dates": len(ics), "first": dates[0], "last": dates[-1], "horizon": horizon,
            "ic_mean": float(np.mean(ics)), "ic_positive": float(np.mean(np.array(ics) > 0)), "ic_test": t_test(ics),
            "spread_mean": float(np.mean(spreads)) if spreads else None,
            "spread_test": t_test(spreads) if len(spreads) >= 3 else None, "spread_n": len(spreads)}


def trade_study(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, start: str = ts.IS_PERIOD[0],
                seed: int = SEED) -> Dict[str, Any]:
    """D'où vient l'avantage de la règle (§51-52) : les trades de son
    backtest (frais et glissement compris), en multiples du risque pris (R) :
    moyenne, médiane, part de gagnants, gain moyen contre perte moyenne,
    asymétrie, part du résultat venue des 10 % meilleurs trades ; test de la
    moyenne et intervalle par grappes mensuelles des achats."""
    res = ts.backtest(close, volume, p, start, str(close.index[-1].date()))
    rs = np.array([t["r"] for t in res.trades], dtype=float)
    if len(rs) < 30:
        return {"status": "INSUFFICIENT_DATA", "n": len(rs)}
    months = np.array([t["entry_date"].year * 12 + t["entry_date"].month for t in res.trades])
    wins, losses = rs[rs > 0], rs[rs <= 0]
    top = np.sort(rs)[::-1][:max(1, len(rs) // 10)]
    _m, _s, skew, _k = mr.moments(rs)
    return {"status": "OK", "n": len(rs), "mean_r": float(rs.mean()), "median_r": float(np.median(rs)),
            "win_rate": float((rs > 0).mean()), "avg_win": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss": float(losses.mean()) if len(losses) else 0.0, "skew": skew,
            "top10_share": float(top.sum() / rs.sum()) if rs.sum() > 0 else None,
            "t_test": t_test(rs), "boot": cluster_bootstrap(rs, months, seed=seed), "start": start}


# ══════════════════════════════════════════════════════════════════════
# Anomalies, ruptures, régimes cachés (§39-41)
# ══════════════════════════════════════════════════════════════════════

def anomalies(close: pd.DataFrame, z_max: float = ANOMALY_Z) -> Dict[str, Any]:
    """Rendements extrêmes (§39) : écart robuste |r − médiane| / (1,4826 MAD)
    au-delà de z_max. Une anomalie n'est pas une erreur : MARKET_EVENT si BTC
    ou le marché ont bougé le même jour, DATA_ERROR si le cours revient le
    lendemain sans mouvement de marché, UNKNOWN sinon."""
    rets = np.log(close / close.shift(1))
    btc = rets["btc"] if "btc" in rets else pd.Series(0.0, index=rets.index)
    found = []
    for a in rets.columns:
        r = rets[a]
        x = r.dropna()
        if len(x) < 100:
            continue
        mad = float(np.median(np.abs(x - x.median()))) * 1.4826
        z = (r - x.median()) / mad if mad else r * 0
        for day in z.index[z.abs() > z_max]:
            k = rets.index.get_loc(day)
            move = float(r.iloc[k])
            nxt = float(r.iloc[k + 1]) if k + 1 < len(r) and np.isfinite(r.iloc[k + 1]) else 0.0
            same = rets.iloc[k].drop(labels=[a]).dropna()
            market = abs(float(btc.iloc[k])) >= 0.07 or (len(same) and float((np.sign(same) == np.sign(move))[
                same.abs() > 0.05].sum()) >= max(3, 0.3 * len(same)))
            if market:
                kind = "MARKET_EVENT"
            elif nxt * move < 0 and abs(nxt) >= 0.8 * abs(move):
                kind = "DATA_ERROR"
            else:
                kind = "UNKNOWN"
            found.append({"asset": a, "day": str(day.date()), "return": move, "z": float(z.loc[day]), "kind": kind})
    found.sort(key=lambda d: -abs(d["z"]))
    counts = {k: sum(1 for d in found if d["kind"] == k) for k in ("MARKET_EVENT", "DATA_ERROR", "UNKNOWN")}
    return {"threshold": z_max, "count": len(found), "by_kind": counts, "top": found[:10]}


def variance_breaks(r: pd.Series, min_size: int = 90, crit: float = 1.358) -> List[Dict[str, Any]]:
    """Ruptures de variance (§40) : somme cumulée des carrés d'Inclán et Tiao,
    découpage binaire ; une rupture si max |D_k| √(T/2) dépasse la valeur
    critique à 5 % (1,358). Volatilité annualisée avant et après."""
    x = r.dropna()
    breaks: List[int] = []

    def split(lo: int, hi: int) -> None:
        seg = x.values[lo:hi]
        if len(seg) < 2 * min_size:
            return
        c = np.cumsum(seg ** 2)
        k = np.arange(1, len(seg) + 1)
        d = c / c[-1] - k / len(seg)
        j = int(np.argmax(np.abs(d[min_size:-min_size]))) + min_size
        if abs(d[j]) * math.sqrt(len(seg) / 2) > crit:
            breaks.append(lo + j + 1)
            split(lo, lo + j + 1)
            split(lo + j + 1, hi)

    split(0, len(x))
    out = []
    edges = [0] + sorted(breaks) + [len(x)]
    for i, b in enumerate(sorted(breaks), 1):
        before, after = x.values[edges[i - 1]:b], x.values[b:edges[i + 1]]
        out.append({"day": str(x.index[b].date()), "vol_before": float(before.std(ddof=1) * math.sqrt(YEAR)),
                    "vol_after": float(after.std(ddof=1) * math.sqrt(YEAR))})
    return out


def hmm2(r: Sequence[float], iters: int = 100, tol: float = 1e-7) -> Dict[str, Any]:
    """Régimes cachés (§40) : modèle de Markov caché gaussien à deux états,
    estimé par EM (Baum-Welch, avec mise à l'échelle). États triés par
    volatilité : 0 calme, 1 agité. Probabilité de chaque état chaque jour,
    matrice de transition, durée moyenne d'un régime."""
    x = _clean(r)
    n = len(x)
    if n < 200:
        return {"status": "INSUFFICIENT_DATA", "n": n}
    mu = np.array([x.mean(), x.mean()])
    sd = np.array([x.std() * 0.6, x.std() * 1.6])
    A = np.array([[0.97, 0.03], [0.05, 0.95]])
    pi = np.array([0.5, 0.5])
    prev = -math.inf
    for _ in range(iters):
        dens = np.exp(-0.5 * ((x[:, None] - mu) / sd) ** 2) / (sd * math.sqrt(2 * math.pi)) + 1e-300
        alpha, scale = np.empty((n, 2)), np.empty(n)
        alpha[0] = pi * dens[0]
        scale[0] = alpha[0].sum()
        alpha[0] /= scale[0]
        for t in range(1, n):
            alpha[t] = (alpha[t - 1] @ A) * dens[t]
            scale[t] = alpha[t].sum()
            alpha[t] /= scale[t]
        beta = np.empty((n, 2))
        beta[-1] = 1.0
        for t in range(n - 2, -1, -1):
            beta[t] = (A @ (dens[t + 1] * beta[t + 1])) / scale[t + 1]
        gamma = alpha * beta
        gamma /= gamma.sum(axis=1, keepdims=True)
        xi = (alpha[:-1, :, None] * A[None] * (dens[1:] * beta[1:])[:, None, :]) / scale[1:, None, None]
        A = xi.sum(axis=0) / gamma[:-1].sum(axis=0)[:, None]
        A /= A.sum(axis=1, keepdims=True)
        pi = gamma[0]
        w = gamma.sum(axis=0)
        mu = (gamma * x[:, None]).sum(axis=0) / w
        sd = np.sqrt((gamma * (x[:, None] - mu) ** 2).sum(axis=0) / w) + 1e-12
        ll = float(np.log(scale).sum())
        if ll - prev < tol:
            break
        prev = ll
    order = np.argsort(sd)
    mu, sd, gamma = mu[order], sd[order], gamma[:, order]
    A = A[np.ix_(order, order)]
    return {"status": "OK", "n": n, "mean_annual": [float(m * YEAR) for m in mu],
            "vol_annual": [float(s * math.sqrt(YEAR)) for s in sd], "transition": A.tolist(),
            "duration_days": [float(1 / (1 - A[i, i])) for i in range(2)], "loglik": prev,
            "prob_calm": gamma[:, 0], "share_calm": float((gamma[:, 0] > 0.5).mean())}


# ══════════════════════════════════════════════════════════════════════
# Laboratoire complet, manifeste, rapport (§63-67, §89)
# ══════════════════════════════════════════════════════════════════════

def code_hash() -> str:
    """Empreinte de ce moteur (reproductibilité)."""
    return hashlib.sha256(inspect.getsource(inspect.getmodule(code_hash)).replace("\r\n", "\n")
                          .encode("utf-8")).hexdigest()[:16]


def _log_returns(close: pd.DataFrame) -> pd.DataFrame:
    return np.log(close / close.shift(1))


def run(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, seed: int = SEED,
        now: Optional[datetime] = None) -> Dict[str, Any]:
    """Le laboratoire complet sur les cours (BTC requis) : chaque section
    garde son état (OK ou INSUFFICIENT_DATA), aucune n'invente de chiffre."""
    now = now or datetime.now(timezone.utc)
    rets = _log_returns(close)
    btc = rets["btc"].dropna()
    assets = [a for a in close.columns if close[a].notna().sum() > 400]
    r: Dict[str, Any] = {"assets": assets, "params": {"breakout_n": p.breakout_n, "mom_n": p.mom_n,
                                                      "regime_sma": p.regime_sma}}
    # Rendements, descriptives, lois, normalité, stationnarité, persistance, par crypto.
    per: Dict[str, Dict[str, Any]] = {}
    for a in assets:
        x = rets[a].dropna()
        c = close[a].dropna()
        d = describe(x)
        days = (c.index[-1] - c.index[0]).days
        d["annual_return"] = annualized(float(c.iloc[-1] / c.iloc[0] - 1), days)
        d["annual_vol"] = d["std"] * math.sqrt(YEAR)
        d["first"] = str(c.index[0].date())
        per[a] = {"describe": d, "normality": normality(x), "fit_best": fit_distributions(x).get("best"),
                  "stationarity_returns": stationarity(x)["status"],
                  "stationarity_price": stationarity(np.log(c.values))["status"],
                  "ljung_box": ljung_box(x, 10), "ljung_box_sq": ljung_box(x.values ** 2, 10),
                  "vr": {q: variance_ratio(x, q) for q in VR_LAGS}}
    r["per_asset"] = per
    r["btc_fits"] = fit_distributions(btc)
    r["btc_stationarity"] = {"price": stationarity(np.log(close["btc"].dropna().values)), "returns": stationarity(btc)}
    r["btc_acf"] = {"returns": acf(btc, 10), "abs_returns": acf(np.abs(btc.values), 10), "pacf": pacf(btc, 10)}
    vr30 = [(a, per[a]["vr"][30]) for a in assets if per[a]["vr"][30]["status"] == "OK"]
    r["vr_holm"] = dict(zip([a for a, _v in vr30], adjust([v["p_value"] for _a, v in vr30], "holm")))
    # Volatilité de BTC.
    r["garch"] = garch11(btc)
    r["vol_forecasts"] = vol_forecasts(btc)
    r["ewma_vol"] = float(math.sqrt(ewma_variance(btc)[-1] * YEAR))
    # Liens entre cryptos (trois ans).
    recent = rets.iloc[-RECENT_DAYS:]
    common = [a for a in assets if recent[a].notna().mean() > 0.98]
    R = recent[common].dropna()
    r["dependence"] = {a: dependence(recent[a].values, recent["btc"].values) for a in common if a != "btc"}
    lw = ledoit_wolf(R.values)
    r["covariance"] = {"assets": len(common), "days": len(R), "shrinkage": lw["shrinkage"],
                       "sample": lw["sample_health"], "shrunk": lw["shrunk_health"]}
    r["pca"] = pca(R.values, common)
    r["factor"] = {a: factor_model(recent[a].values, recent["btc"].values) for a in common if a != "btc"}
    alt = [a for a in r["factor"]]
    r["alpha_bh"] = dict(zip(alt, adjust([r["factor"][a]["alpha_p"] for a in alt], "bh")))
    if "eth" in close:
        pair = close[["eth", "btc"]].dropna()
        r["cointegration"] = engle_granger(np.log(pair["eth"].values), np.log(pair["btc"].values))
    # Pouvoir prédictif de la règle.
    r["breakout"] = breakout_study(close, volume, p, regime=True, seed=seed)
    r["breakout_all"] = breakout_study(close, volume, p, regime=False, seed=seed)
    r["momentum"] = momentum_study(close, volume, p)
    r["trades"] = trade_study(close, volume, p, seed=seed)
    # Anomalies, ruptures, régimes.
    r["anomalies"] = anomalies(close)
    r["breaks"] = variance_breaks(btc)
    h = hmm2(btc)
    if h["status"] == "OK":
        rule = ts.btc_regime(close["btc"], p).reindex(btc.index).values.astype(bool)
        calm = h.pop("prob_calm") > 0.5
        known = np.arange(len(btc)) >= p.regime_sma
        h["agreement"] = float((calm[known] == rule[known]).mean())
        h["calm_when_bull"] = float(calm[known & rule].mean()) if (known & rule).any() else None
        h["calm_when_bear"] = float(calm[known & ~rule].mean()) if (known & ~rule).any() else None
    r["hmm"] = h
    r["manifest"] = {"run_id": str(uuid.uuid4()), "engine_version": ENGINE_VERSION, "git": git_version(),
                     "code_hash": code_hash(), "dataset": fingerprint(close, volume, str(close.index[-1].date())),
                     "seed": seed, "generator": "numpy PCG64", "numpy": np.__version__, "pandas": pd.__version__,
                     "executed_at": now.isoformat(timespec="seconds")}
    r["findings"] = findings(r)
    r["result_hash"] = mb.digest(_hashable(r))
    return r


def _hashable(r: Dict[str, Any]) -> Dict[str, Any]:
    """Les chiffres du rapport, sans l'identifiant ni l'heure de l'exécution
    (deux exécutions identiques ont la même empreinte)."""
    def strip(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in ("manifest", "result_hash")}
        if isinstance(o, (list, tuple)):
            return [strip(v) for v in o]
        if isinstance(o, (float, np.floating)):
            return round(float(o), 9) if math.isfinite(o) else str(o)
        if isinstance(o, np.ndarray):
            return [round(float(v), 9) for v in o]
        return o
    return strip(r)


def findings(r: Dict[str, Any]) -> List[str]:
    """Conclusions tirées des chiffres, en clair (aucune n'est écrite à
    l'avance : chacune dépend d'une mesure)."""
    out = []
    per = r["per_asset"]
    n = len(per)
    non_normal = sum(1 for a in per.values() if a["normality"]["status"] == "NON_NORMAL")
    student = sum(1 for a in per.values() if a["fit_best"] == "student")
    out.append(f"Rendements journaliers non normaux pour {non_normal} cryptos sur {n} ; la loi de Student "
               f"(queues épaisses) les décrit le mieux pour {student} sur {n}.")
    sp = sum(1 for a in per.values() if a["stationarity_price"] == "NON_STATIONARY")
    sr = sum(1 for a in per.values() if a["stationarity_returns"] == "STATIONARY")
    out.append(f"Cours non stationnaires pour {sp} sur {n}, rendements stationnaires pour {sr} sur {n} : les "
               "modèles travaillent sur les rendements, jamais sur les cours.")
    sq = sum(1 for a in per.values() if a["ljung_box_sq"]["p_value"] < ALPHA)
    out.append(f"La volatilité se regroupe (Ljung-Box sur les carrés) pour {sq} cryptos sur {n} : un jour agité "
               "en annonce d'autres, d'où les stops proportionnels à la volatilité.")
    vr = [a["vr"][30] for a in per.values() if a["vr"][30]["status"] == "OK"]
    above = sum(1 for v in vr if v["vr"] > 1)
    sig = sum(1 for p in r["vr_holm"].values() if p < ALPHA)
    out.append(f"Ratio de variance à 30 jours au-dessus de 1 (les mouvements se prolongent) pour {above} cryptos "
               f"sur {len(vr)}, significatif après correction de Holm pour {sig}.")
    b = r["breakout"]["horizons"].get(30, {})
    if b.get("status") == "OK":
        proved = "prouvé" if b["p_holm"] < ALPHA else "pas prouvé statistiquement"
        out.append(f"Après un signal d'achat de la règle, le rendement moyen à 30 jours dépasse celui d'un jour "
                   f"ordinaire de {fr(b['excess'] * 100, '+.1f')} points, {proved} (intervalle "
                   f"{fr(b['ci95'][0] * 100, '+.1f')} à {fr(b['ci95'][1] * 100, '+.1f')}, p de Holm "
                   f"{_p(b['p_holm'])}, {fr_plain(b['events'])} signaux) : le signal seul prédit peu.")
    m = r["momentum"]
    if m.get("status") == "OK":
        verdict = ("un pouvoir prédictif mesurable" if m["ic_test"]["p_value"] < ALPHA
                   else "aucun pouvoir prédictif mesurable à lui seul")
        out.append(f"Coefficient d'information du momentum à 30 jours : {fr(m['ic_mean'], '+.3f')} en moyenne "
                   f"(p {_p(m['ic_test']['p_value'])}) : {verdict}.")
    t = r["trades"]
    if t.get("status") == "OK":
        out.append(f"L'avantage vient de la gestion des trades : {fr(t['win_rate'] * 100, '.0f')} % de gagnants "
                   f"seulement, mais un gain moyen de {fr(t['avg_win'], '+.2f')} R contre une perte moyenne de "
                   f"{fr(t['avg_loss'], '+.2f')} R (asymétrie {fr(t['skew'], '+.1f')}) ; les 10 % meilleurs trades "
                   f"font {fr((t['top10_share'] or 0) * 100, '.0f')} % du résultat ; moyenne "
                   f"{fr(t['mean_r'], '+.2f')} R par trade (intervalle {fr(t['boot']['ci95'][0], '+.2f')} à "
                   f"{fr(t['boot']['ci95'][1], '+.2f')}, {fr_plain(t['n'])} trades) : couper vite les pertes, "
                   "laisser courir les gains.")
    out.append(f"Sur trois ans, {fr(r['pca']['explained'][0] * 100, '.0f')} % des mouvements des "
               f"{r['pca']['n_assets']} cryptos viennent d'un facteur commun : elles forment environ "
               f"{fr(r['pca']['effective_bets'], '.1f')} paris indépendants, pas {r['pca']['n_assets']}.")
    vf = r["vol_forecasts"]
    if vf.get("status") == "OK":
        out.append(f"Prévision de la volatilité du lendemain (hors échantillon) : la meilleure est « {vf['best']} » "
                   "(perte QLIKE la plus basse).")
    h = r["hmm"]
    if h.get("status") == "OK":
        out.append(f"Régimes cachés de BTC : calme {fr(h['vol_annual'][0] * 100, '.0f')} % de volatilité annuelle, "
                   f"agité {fr(h['vol_annual'][1] * 100, '.0f')} % ; d'accord avec le régime de la règle "
                   f"{fr(h['agreement'] * 100, '.0f')} % des jours (deux mesures différentes : volatilité contre "
                   "tendance).")
    return out


def result_of(r: Dict[str, Any]) -> QuantResult:
    """Le résultat au format du contrat QuantResult.v1."""
    sections = ("btc_fits", "garch", "vol_forecasts", "momentum", "trades", "hmm")
    missing = tuple(s for s in sections if r[s].get("status") != "OK")
    missing += tuple(f"breakout {h}" for h, v in r["breakout"]["horizons"].items() if v.get("status") != "OK")
    m = r["manifest"]
    return QuantResult(run_id=m["run_id"], status="VALIDATED_WITH_WARNINGS" if missing else "VALIDATED",
                       engine_version=ENGINE_VERSION, dataset_hash=m["dataset"]["hash"], code_hash=m["code_hash"],
                       result_hash=r["result_hash"], seed=m["seed"], assets=tuple(r["assets"]),
                       findings=tuple(r["findings"]), warnings=tuple(f"section incomplète : {s}" for s in missing),
                       limitations=LIMITATIONS, created_at=m["executed_at"])


def _p(x: Optional[float]) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return "< 0,001" if x < 0.001 else fr(x, ".3f")


def render(r: Dict[str, Any]) -> str:
    """docs/QUANT.md : le rapport du laboratoire, chiffres et conclusions."""
    m = r["manifest"]
    ds = m["dataset"]
    lines = ["# Laboratoire quantitatif de TrendGuard", "",
             f"Tiré des cours journaliers de Binance ({ds['first']} → {ds['last']}, {len(r['assets'])} cryptos) par "
             "`python trendguard_bot.py quant` (étape 8 du prompt maître, "
             "[`MOTEUR_QUANT.md`](MOTEUR_QUANT.md)). Consultatif : rien ici ne change la règle.", "",
             "## Conclusions", ""]
    lines += [f"- {x}" for x in r["findings"]]
    lines += ["", "## 1. Rendements et lois", "",
              "| Crypto | depuis | rendement annualisé | volatilité annualisée | asymétrie | aplatissement (excès) "
              "| pire jour | meilleur jour | loi | normalité |", "| --- | --- | --- | --- | --- | --- | --- | --- "
              "| --- | --- |"]
    law = {"normal": "normale", "laplace": "Laplace", "student": "Student", None: "—"}
    for a, d in r["per_asset"].items():
        s = d["describe"]
        lines.append(f"| {a.upper()} | {s['first']} | {fr(s['annual_return'] * 100, '+.0f')} % | "
                     f"{fr(s['annual_vol'] * 100, '.0f')} % | {fr(s['skew'], '+.2f')} | "
                     f"{fr(s['excess_kurtosis'], '.1f')} | {fr(s['min'] * 100, '+.0f')} % | "
                     f"{fr(s['max'] * 100, '+.0f')} % | {law[d['fit_best']]} | "
                     f"{'non normale' if d['normality']['status'] == 'NON_NORMAL' else 'normale'} |")
    f = r["btc_fits"]
    if f.get("status") == "OK":
        lines += ["", "BTC, lois ajustées (maximum de vraisemblance) :", "",
                  "| Loi | log-vraisemblance | AIC | BIC | Kolmogorov-Smirnov | p |", "| --- | --- | --- | --- | --- | --- |"]
        for name, d in f["fits"].items():
            lines.append(f"| {law[name]} | {fr(d['loglik'], ',.0f')} | {fr(d['aic'], ',.0f')} | {fr(d['bic'], ',.0f')} "
                         f"| {fr(d['ks'], '.3f')} | {_p(d['ks_p'])} |")
        nu = f["fits"]["student"]["params"]["nu"]
        lines += ["", f"Degrés de liberté de la loi de Student : {fr_plain(nu)} (plus c'est bas, plus les queues sont "
                  "épaisses ; une normale aurait l'infini)."]
    st = r["btc_stationarity"]
    lines += ["", "## 2. Stationnarité, autocorrélation, persistance", "",
              "| Série (BTC) | ADF | seuil 5 % | KPSS | seuil 5 % | conclusion |", "| --- | --- | --- | --- | --- | --- |"]
    for label, s in (("log du cours", st["price"]), ("rendements", st["returns"])):
        if s["status"] != "INSUFFICIENT_DATA":
            lines.append(f"| {label} | {fr(s['adf']['statistic'], '.2f')} ({s['adf']['p_band']}) | "
                         f"{fr(s['adf']['critical']['5%'], '.2f')} | {fr(s['kpss']['statistic'], '.3f')} | 0,463 | "
                         f"{STATIONARITY_FR[s['status']]} |")
    ac = r["btc_acf"]
    lines += ["", "Autocorrélations de BTC aux retards 1 à 5 : rendements "
              + ", ".join(fr(x, '+.3f') for x in ac["returns"][:5]) + " ; valeur absolue des rendements "
              + ", ".join(fr(x, '+.3f') for x in ac["abs_returns"][:5]) + ".", "",
              "| Crypto | VR 5 j | VR 10 j | VR 30 j | z (30 j) | p de Holm (30 j) | Ljung-Box carrés p |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for a, d in r["per_asset"].items():
        v = d["vr"]
        if v[30]["status"] != "OK":
            continue
        lines.append(f"| {a.upper()} | {fr(v[5]['vr'], '.2f')} | {fr(v[10]['vr'], '.2f')} | {fr(v[30]['vr'], '.2f')} "
                     f"| {fr(v[30]['z'], '+.2f')} | {_p(r['vr_holm'].get(a))} | {_p(d['ljung_box_sq']['p_value'])} |")
    g, vf = r["garch"], r["vol_forecasts"]
    lines += ["", "## 3. Volatilité de BTC", ""]
    if g.get("status") == "OK":
        lines.append(f"GARCH(1,1) : α {fr(g['alpha'], '.3f')}, β {fr(g['beta'], '.3f')}, persistance "
                     f"{fr(g['persistence'], '.3f')}, demi-vie d'un choc {fr(g['half_life_days'] or 0, '.0f')} jours ; "
                     f"volatilité de long terme {fr((g['long_run_vol'] or 0) * 100, '.0f')} %, prévue demain "
                     f"{fr(g['forecast_vol'] * 100, '.0f')} % (annualisée) ; EWMA actuelle "
                     f"{fr(r['ewma_vol'] * 100, '.0f')} %.")
    if vf.get("status") == "OK":
        lines += ["", f"Prévision du lendemain, hors échantillon ({fr_plain(vf['n_test'])} jours) :", "",
                  "| Modèle | perte QLIKE | erreur quadratique (× 10⁶) |", "| --- | --- | --- |"]
        for name, d in vf["models"].items():
            lines.append(f"| {name} | {fr(d['qlike'], '.4f')} | {fr(d['mse'] * 1e6, '.2f')} |")
    cv = r["covariance"]
    pc = r["pca"]
    lines += ["", "## 4. Liens entre cryptos (trois ans)", "",
              f"{cv['assets']} cryptos, {fr_plain(cv['days'])} jours communs. Covariance échantillon : conditionnement "
              f"{fr(cv['sample']['condition'], ',.0f')} ; rétrécie (Ledoit-Wolf, δ = {fr(cv['shrinkage'], '.2f')}) : "
              f"{fr(cv['shrunk']['condition'], ',.0f')} ; les deux positives. Corrélation moyenne "
              f"{fr(pc['mean_correlation'], '.2f')} ; première composante {fr(pc['explained'][0] * 100, '.0f')} % de la "
              f"variance, deuxième {fr(pc['explained'][1] * 100, '.0f')} % ; paris indépendants "
              f"{fr(pc['effective_bets'], '.1f')}.", "",
              "| Crypto | corrélation à BTC (Pearson) | Spearman | Kendall | les jours où BTC chute de 5 % | "
              "les autres jours | bêta | part expliquée par BTC | α annualisé | p (BH) |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for a, d in r["dependence"].items():
        fm = r["factor"][a]
        lines.append(f"| {a.upper()} | {fr(d['pearson'], '.2f')} | {fr(d['spearman'], '.2f')} | "
                     f"{fr(d['kendall'], '.2f')} | {fr(d['crash_corr'], '.2f') if d['crash_corr'] is not None else '—'} "
                     f"| {fr(d['calm_corr'], '.2f')} | {fr(fm['beta'], '.2f')} | {fr(fm['r2'] * 100, '.0f')} % | "
                     f"{fr(fm['alpha_annual'] * 100, '+.0f')} % | {_p(r['alpha_bh'][a])} |")
    co = r.get("cointegration")
    if co:
        lines += ["", f"ETH et BTC (log des cours, depuis {ds['first']}) : statistique d'Engle-Granger "
                  f"{fr(co['adf'].get('statistic', 0), '.2f')} ({co['adf'].get('p_band', '—')}) : "
                  + ("cointégrés" if co["cointegrated"] else "pas de cointégration prouvée")
                  + f" ; écart actuel {fr(co['zscore'], '+.1f')} écart-type de sa moyenne d'un an. Aucune stratégie "
                  "d'arbitrage n'en est tirée."]
    lines += ["", "## 5. La règle a-t-elle un pouvoir prédictif ?", "",
              "Signal d'achat de la règle (cassure du plus haut de "
              f"{r['params']['breakout_n']} jours, momentum positif, liquidité), rendement des jours suivants "
              "contre celui d'un jour ordinaire de la même crypto achetable :", "",
              "| Signal | horizon | signaux | rendement moyen | médian | positifs | jour ordinaire | écart | "
              "intervalle 95 % | p de Holm |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for label, study in (("règle (régime haussier)", r["breakout"]), ("cassure seule", r["breakout_all"])):
        for h, d in study["horizons"].items():
            if d.get("status") != "OK":
                continue
            lines.append(f"| {label} | {h} j | {fr_plain(d['events'])} | {fr(d['event_mean'] * 100, '+.1f')} % | "
                         f"{fr(d['event_median'] * 100, '+.1f')} % | {fr(d['event_hit'] * 100, '.0f')} % | "
                         f"{fr(d['base_mean'] * 100, '+.1f')} % | {fr(d['excess'] * 100, '+.1f')} | "
                         f"{fr(d['ci95'][0] * 100, '+.1f')} à {fr(d['ci95'][1] * 100, '+.1f')} | {_p(d['p_holm'])} |")
    mo = r["momentum"]
    if mo.get("status") == "OK":
        lines += ["", f"Momentum de la règle ({r['params']['mom_n']} jours) et rendement des {mo['horizon']} jours "
                  f"suivants, {mo['dates']} dates sans chevauchement ({mo['first']} → {mo['last']}) : coefficient "
                  f"d'information moyen {fr(mo['ic_mean'], '+.3f')} (t {fr(mo['ic_test']['statistic'], '+.2f')}, p "
                  f"{_p(mo['ic_test']['p_value'])})" + (
                      f" ; momentum positif moins négatif {fr(mo['spread_mean'] * 100, '+.1f')} points "
                      f"(p {_p(mo['spread_test']['p_value'])}, {mo['spread_n']} dates)." if mo.get("spread_test")
                      else ".")]
    t = r["trades"]
    if t.get("status") == "OK":
        lines += ["", f"Trades du backtest de la règle depuis {t['start']} ({fr_plain(t['n'])} trades, frais et "
                  "glissement compris), en multiples du risque pris :", "",
                  "| Moyenne | médiane | gagnants | gain moyen | perte moyenne | asymétrie | part des 10 % meilleurs | "
                  "t (moyenne = 0) | intervalle 95 % (mois) |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                  f"| {fr(t['mean_r'], '+.2f')} R | {fr(t['median_r'], '+.2f')} R | {fr(t['win_rate'] * 100, '.0f')} % "
                  f"| {fr(t['avg_win'], '+.2f')} R | {fr(t['avg_loss'], '+.2f')} R | {fr(t['skew'], '+.1f')} | "
                  f"{fr((t['top10_share'] or 0) * 100, '.0f')} % | {fr(t['t_test']['statistic'], '+.2f')} "
                  f"(p {_p(t['t_test']['p_value'])}) | {fr(t['boot']['ci95'][0], '+.2f')} à "
                  f"{fr(t['boot']['ci95'][1], '+.2f')} R |"]
    an = r["anomalies"]
    lines += ["", "## 6. Anomalies, ruptures, régimes cachés", "",
              f"Rendements à plus de {fr_plain(an['threshold'])} écarts robustes : {an['count']} "
              f"(événements de marché {an['by_kind']['MARKET_EVENT']}, erreurs de données probables "
              f"{an['by_kind']['DATA_ERROR']}, inexpliqués {an['by_kind']['UNKNOWN']})."]
    if an["top"]:
        lines += ["", "| Crypto | jour | rendement | écart robuste | lecture |", "| --- | --- | --- | --- | --- |"]
        kinds = {"MARKET_EVENT": "marché", "DATA_ERROR": "erreur de données probable", "UNKNOWN": "inexpliqué"}
        for d in an["top"]:
            lines.append(f"| {d['asset'].upper()} | {d['day']} | {fr(d['return'] * 100, '+.0f')} % | "
                         f"{fr(d['z'], '+.0f')} | {kinds[d['kind']]} |")
    if r["breaks"]:
        lines += ["", "Ruptures de la volatilité de BTC (Inclán-Tiao) : " + " ; ".join(
            f"{b['day']} ({fr(b['vol_before'] * 100, '.0f')} % → {fr(b['vol_after'] * 100, '.0f')} %)"
            for b in r["breaks"]) + "."]
    h = r["hmm"]
    if h.get("status") == "OK":
        lines += ["", f"Modèle de Markov caché à deux états (BTC) : calme {fr(h['vol_annual'][0] * 100, '.0f')} % de "
                  f"volatilité, durée moyenne {fr(h['duration_days'][0], '.0f')} jours ; agité "
                  f"{fr(h['vol_annual'][1] * 100, '.0f')} %, {fr(h['duration_days'][1], '.0f')} jours ; BTC calme "
                  f"{fr(h['share_calm'] * 100, '.0f')} % du temps. Accord avec le régime de la règle (BTC au-dessus "
                  f"de sa moyenne de {r['params']['regime_sma']} jours) : {fr(h['agreement'] * 100, '.0f')} %."]
    lines += ["", "## 7. Manifeste", "",
              f"Exécution `{m['run_id']}`, moteur {m['engine_version']}, code `{m['git']}` (empreinte "
              f"`{m['code_hash']}`), données `{ds['hash']}`, graine {m['seed']} ({m['generator']}), numpy "
              f"{m['numpy']}, pandas {m['pandas']} ; empreinte du résultat `{r['result_hash']}` (refait, identique).",
              "", "## 8. Limites", ""]
    lines += [f"- {x}" for x in LIMITATIONS]
    return "\n".join(lines)


STATIONARITY_FR = {"STATIONARY": "stationnaire", "NON_STATIONARY": "non stationnaire", "UNCERTAIN": "incertaine",
                   "INSUFFICIENT_DATA": "données insuffisantes"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `quant` : le laboratoire sur les cours en cache (jamais
    retéléchargés pendant l'étude), rapport écrit avec --out."""
    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    ap = argparse.ArgumentParser(description="Laboratoire quantitatif de TrendGuard (consultatif)")
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    ap.add_argument("--out", default="")
    ap.add_argument("--graine", type=int, default=SEED)
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    close, volume = load_history(args.cache, list(g.universe), max_age_days=None)
    r = run(close, volume, params_for(g), seed=args.graine)
    result_of(r)
    text = render(r)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0
