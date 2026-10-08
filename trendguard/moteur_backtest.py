"""
Moteur de backtest (prompt maître, étape 10 ; docs/MOTEUR_BACKTEST.md) : la
boucle de backtest du bot (trend_strategy.backtest, la même qu'en marche,
mêmes fonctions de décision) entourée de ce qui fait une preuve.

    configuration versionnée → données validées avant de simuler (schéma,
    dates, trous, prix impossibles, sauts, cours figés, survivants, fuite) →
    simulation → performance, risque, coûts → hors échantillon,
    walk-forward, réglages voisins (probabilité de sur-ajustement) →
    Monte-Carlo → stress des coûts, du glissement et du retard d'exécution →
    capacité → régimes → références (BTC, panier, achats au hasard) →
    statistique (Sharpe probabiliste et dégonflé, intervalles, tests
    multiples) → contrôles de biais → note de qualité → VALID,
    VALID_WITH_WARNINGS, INVALID ou REJECTED → prête pour le moteur de
    risque, ou recherche seulement

Chaque exécution a son manifeste (version du code, empreintes des données,
de la configuration, du code, de l'environnement, graine, empreinte du
résultat) : refaite, elle doit donner exactement le même résultat. Un
backtest n'est jamais une garantie de performance future ; il ne change
jamais la règle en service et ne passe aucun ordre.

    python trendguard_bot.py validation                  # rapport complet (≈ 3 minutes)
    python trendguard_bot.py validation --rapide         # sans walk-forward ni réglages voisins
    python trendguard_bot.py validation --cache data_binance --out docs/VALIDATION.md
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import inspect
import itertools
import json
import math
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from statistics import NormalDist
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import v29

from . import moteur_strategie as ms
from . import trend_strategy as ts
from .contrats import NO_GUARANTEE, BacktestResult
from .registre import fingerprint, git_version
from .texte import fr, fr_plain

ENGINE_VERSION = "1.0.0"
QUALITY_VERSION = "qualite-backtest-1.0.0"
QUALITY_WEIGHTS = {"data": 0.15, "execution": 0.15, "sample": 0.10, "oos": 0.15, "robustness": 0.15,
                   "statistics": 0.15, "reproducibility": 0.15}
IS_PERIOD = ts.IS_PERIOD
OOS_START = "2023-01-01"
COST_MULTIPLIERS = (0.5, 1.0, 1.5, 2.0, 3.0)
SLIPPAGE_EXTRA = (0.25, 0.5, 1.0, 2.0)
DELAYS = (0, 1, 2)
CAPITALS = (1e4, 1e5, 1e6, 1e7, 1e8)
IMPACT_Y = 1.0                  # loi en racine carrée : impact ≈ Y × volatilité du jour × √(part du volume)
PARTICIPATION_MAX = 0.05        # part du volume moyen au-delà de laquelle l'exécution n'est plus réaliste
NEIGHBOURS = {"breakout_n": (20, 30, 40), "init_stop_atr": (2.5, 3.0, 3.5), "trail_atr": (4.0, 5.0, 6.0)}
DATA_MIN = 70.0                 # note des données sous laquelle on ne simule pas (sauf recherche)
PSR_MIN = 0.95                  # Sharpe probabiliste (> 0) exigé
DSR_MIN = 0.95                  # Sharpe dégonflé (essais multiples) exigé
RUIN_DD = 0.40                  # baisse qui déclenche l'arrêt d'urgence du bot
RUIN_MAX = 0.20                 # probabilité acceptée de l'atteindre en trois ans (Monte-Carlo)
MIN_TRADES = 30
EULER = 0.5772156649015329
LIMITATIONS = (
    NO_GUARANTEE,
    "Bougies journalières de Binance seulement : ni carnet d'ordres, ni ticks ; achats et ventes simulés à la "
    "clôture, glissement et frais fixes par côté.",
    "Cryptos encore cotées aujourd'hui : celles retirées de la cote (FTT, LUNA…) manquent, un biais du survivant "
    "est possible (le laboratoire le mesure avec les données Coin Metrics : --cm).",
    "Deux époques de marché (2018-2022, 2023 → aujourd'hui) : un régime jamais vu reste possible.",
    "Réglages choisis sur 2018-2022 parmi d'autres essais : le Sharpe dégonflé en tient compte, pas entièrement.",
    "Capacité et impact de marché estimés (loi en racine carrée), pas observés.",
)


# ---------- Configuration, manifeste, reproductibilité (§5-6) ----------

def config(p: ts.TrendParams, start: str, end: str, capital: float = 10_000.0,
           universe: Optional[Sequence[str]] = None, seed: int = 7) -> Dict[str, Any]:
    """Configuration complète d'un backtest (§5), écrite en données : toute
    modification change son empreinte."""
    spec = ms.spec_for(p)
    return {"strategy_id": spec["id"], "strategy_version": spec["version"], "spec_hash": ms.spec_hash(spec),
            "params": json.loads(json.dumps(dataclasses.asdict(p))), "start": start, "end": end,
            "capital": float(capital), "base_currency": "USDT", "timeframe": "1d",
            "universe": sorted(universe) if universe else "toutes les paires du bot",
            "fee_model": f"fixe, {fr(p.fee * 100, 'g')} % par côté",
            "slippage_model": f"fixe, {fr(p.slippage * 100, 'g')} % par côté",
            "spread_model": "compris dans le glissement (achat au-dessus, vente au-dessous de la clôture)",
            "impact_model": "aucun à la taille du bot ; estimé par la capacité (loi en racine carrée)",
            "latency_model": "décision à la clôture, achat dans les minutes qui suivent ; retard d'un ou deux jours "
                             "éprouvé à part", "execution_model": "ordre au marché à la clôture, Binance Spot",
            "liquidity_model": f"volume moyen de 30 jours d'au moins {fr_plain(p.min_volume_usd)} dollars",
            "margin_model": "sans objet (Spot, sans levier)", "funding_model": "sans objet (Spot)",
            "borrow_model": "sans objet (pas de vente à découvert)",
            "corporate_action_model": "sans objet (cryptos) ; retrait de la cote : vente avec décote de 50 %",
            "benchmark": "BTC acheté et gardé ; panier des cryptos à parts égales", "seed": seed,
            "engine_version": ENGINE_VERSION}


def digest(obj: Any) -> str:
    """Empreinte SHA-256 (16 caractères) d'un objet JSON canonique."""
    body = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def code_hash() -> str:
    """Empreinte du code qui décide et simule (stratégie, fiche, ce moteur)."""
    h = hashlib.sha256()
    for mod in (ts, ms, sys.modules[__name__]):
        h.update(inspect.getsource(mod).replace("\r\n", "\n").encode("utf-8"))
    return h.hexdigest()[:16]


def environment() -> Dict[str, str]:
    return {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
            "system": platform.system(), "machine": platform.machine()}


def result_hash(res: ts.PortfolioResult) -> str:
    """Empreinte du résultat : courbe du capital et trades, arrondis au
    millionième (deux exécutions identiques ont la même)."""
    eq = [round(float(x), 6) for x in res.equity.values]
    trades = [(t["asset"], str(t["entry_date"]), str(t["exit_date"]), round(float(t["pnl"]), 6), t["reason"])
              for t in res.trades]
    return digest({"equity": eq, "trades": trades})


def manifest(cfg: Dict[str, Any], close: pd.DataFrame, volume: Optional[pd.DataFrame],
             res: ts.PortfolioResult, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Manifeste d'exécution (BacktestManifest.v1, §6) : tout ce qu'il faut
    pour refaire le backtest et vérifier qu'il donne le même résultat."""
    env = environment()
    return {"run_id": str(uuid.uuid4()), "git": git_version(), "engine_version": ENGINE_VERSION,
            "strategy_id": cfg["strategy_id"], "strategy_version": cfg["strategy_version"],
            "spec_hash": cfg["spec_hash"], "dataset": fingerprint(close, volume, cfg["end"]),
            "feature_lock": ms.TREND["locks"]["features"], "config_hash": digest(cfg), "code_hash": code_hash(),
            "environment": env, "environment_hash": digest(env), "seed": cfg["seed"],
            "executed_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            "result_hash": result_hash(res)}


# ---------- Données validées avant de simuler (§7-9) ----------

def validate_data(close: pd.DataFrame, volume: Optional[pd.DataFrame], p: ts.TrendParams) -> Dict[str, Any]:
    """Contrôle des données (§7) : chaque contrôle (critique ou non), une
    note sur 100 et un état PASS, WARN ou FAIL. Rien n'est corrigé."""
    checks: List[Tuple[str, bool, bool, str]] = []          # (nom, conforme, critique, détail)
    idx = close.index
    ok_schema = (isinstance(idx, pd.DatetimeIndex) and idx.tz is not None and str(idx.tz) == "UTC"
                 and "btc" in close.columns and all(np.issubdtype(t, np.number) for t in close.dtypes))
    checks.append(("Schéma", ok_schema, True, "dates UTC, BTC présent, valeurs numériques" if ok_schema
                   else "dates sans fuseau UTC, BTC absent ou valeurs non numériques"))
    if not ok_schema:
        return {"checks": checks, "score": 0.0, "status": "FAIL", "critical": ["Schéma"]}
    dup = int(idx.duplicated().sum())
    unsorted = not idx.is_monotonic_increasing
    off = int(((idx.hour != 0) | (idx.minute != 0)).sum())
    checks.append(("Dates", not (dup or unsorted or off), True,
                   f"{dup} doublon(s), {'désordre, ' if unsorted else ''}{off} hors de 00:00 UTC"))
    full = pd.date_range(idx[0], idx[-1], freq="D")
    missing = len(full.difference(idx))
    checks.append(("Jours manquants", missing == 0, False, f"{missing} jour(s) sur {len(full)}"))
    values = close.to_numpy(dtype=float)
    started = ~np.isnan(values) | (np.cumsum(~np.isnan(values), axis=0) > 0)
    listed = np.cumsum(~np.isnan(values), axis=0) > 0
    bad_px = int(((values <= 0) | np.isinf(values)).sum())
    checks.append(("Prix impossibles", bad_px == 0, True, f"{bad_px} prix nul(s), négatif(s) ou infini(s)"))
    holes = int((listed & np.isnan(values) & started).sum())
    checks.append(("Trous après cotation", holes == 0, False, f"{holes} clôture(s) manquante(s)"))
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.abs(np.diff(np.log(values), axis=0))
    jumps = int(np.nansum(lr > 0.7))
    checks.append(("Sauts extrêmes", jumps <= 10, False, f"{jumps} variation(s) de plus de ×2 ou ÷2 en un jour"))
    stale = 0
    for a in close.columns:
        s = close[a].dropna()
        runs = (s != s.shift()).cumsum()
        stale += int((s.groupby(runs).size() >= 5).sum())
    checks.append(("Cours figés", stale == 0, False, f"{stale} série(s) de 5 clôtures identiques ou plus"))
    cover = 0.0
    if volume is not None and len(volume.columns):
        v = volume.reindex(index=idx, columns=close.columns).to_numpy(dtype=float)
        cover = float((~np.isnan(v) & listed).sum() / max(1, listed.sum()))
        neg = int((v < 0).sum())
        checks.append(("Volumes", neg == 0 and cover >= 0.9, False,
                       f"connus {fr(cover * 100, '.1f')} % des jours cotés, {neg} négatif(s)"))
    else:
        checks.append(("Volumes", False, False, "absents : le filtre de liquidité refuse tout achat"))
    checks.append(("Survivants", False, False, "seules les cryptos encore cotées : biais du survivant possible"))
    if any(crit and not ok for _n, ok, crit, _d in checks):
        checks.append(("Fuite vers le futur", False, False, "non vérifiée : données déjà invalides"))
    else:
        leaks = ms.leakage(close, volume, p)
        checks.append(("Fuite vers le futur", not leaks, True, " ; ".join(leaks[:2]) or
                       "indicateurs et régime identiques sans l'avenir"))
    critical = [n for n, ok, crit, _d in checks if crit and not ok]
    penalty = (min(20.0, missing / max(1, len(full)) * 400) + min(20.0, holes / max(1, listed.sum()) * 400)
               + min(10.0, jumps * 0.5) + min(10.0, stale * 1.0) + (0.0 if cover >= 0.9 else 10.0) + 5.0)
    score = 0.0 if critical else max(0.0, 100.0 - penalty)
    status = "FAIL" if critical or score < DATA_MIN else "WARN" if score < 90 else "PASS"
    return {"checks": checks, "score": round(score, 1), "status": status, "critical": critical}


# ---------- Statistique (§39-40) ----------

_N = NormalDist()


def moments(x: np.ndarray) -> Tuple[float, float, float, float]:
    """Moyenne, écart-type, asymétrie, aplatissement (3 pour une loi normale)."""
    x = np.asarray(x, dtype=float)
    m, s = float(x.mean()), float(x.std(ddof=1))
    if s <= 0:
        return m, 0.0, 0.0, 3.0
    z = (x - m) / s
    return m, s, float((z ** 3).mean()), float((z ** 4).mean())


def sharpe_stats(daily: np.ndarray, trials: int = 1, trials_var: Optional[float] = None) -> Dict[str, Any]:
    """Sharpe d'une série de rendements journaliers, annualisé (365 jours) :
    statistique t et p-valeur (moyenne > 0), erreur type tenant compte de
    l'asymétrie et de l'aplatissement, Sharpe probabiliste PSR (probabilité
    que le vrai Sharpe soit positif) et, avec plusieurs essais, Sharpe
    dégonflé DSR (le meilleur de N essais au hasard aurait ce Sharpe-là)."""
    x = np.asarray(daily, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 30:
        return {"n": n}
    m, s, skew, kurt = moments(x)
    sr = m / s if s > 0 else 0.0
    var = max(1e-12, (1 - skew * sr + (kurt - 1) / 4 * sr ** 2) / (n - 1))
    se = math.sqrt(var)
    t = sr * math.sqrt(n)
    out = {"n": n, "sharpe": sr * math.sqrt(365), "sharpe_daily": sr, "skew": skew, "kurtosis": kurt, "t": t,
           "p_value": 1 - _N.cdf(t), "se_annual": se * math.sqrt(365),
           "ci95": (sr * math.sqrt(365) - 1.96 * se * math.sqrt(365), sr * math.sqrt(365) + 1.96 * se * math.sqrt(365)),
           "psr": _N.cdf(sr / se)}
    if trials > 1 and trials_var is not None and trials_var > 0:
        sr0 = math.sqrt(trials_var) * ((1 - EULER) * _N.inv_cdf(1 - 1 / trials)
                                       + EULER * _N.inv_cdf(1 - 1 / (trials * math.e)))
        out.update({"trials": trials, "sr0_annual": sr0 * math.sqrt(365), "dsr": _N.cdf((sr - sr0) / se)})
    return out


def block_bootstrap(daily: np.ndarray, stat: Callable[[np.ndarray], float], block: int = 20, sims: int = 2000,
                    seed: int = 7, level: float = 0.95) -> Tuple[float, float]:
    """Intervalle de confiance d'une statistique par rééchantillonnage en
    blocs circulaires (garde l'enchaînement des jours)."""
    x = np.asarray(daily, dtype=float)
    n = len(x)
    rng = np.random.default_rng(seed)
    k = -(-n // block)
    starts = rng.integers(0, n, size=(sims, k))
    idx = (starts[:, :, None] + np.arange(block)).reshape(sims, -1)[:, :n] % n
    vals = np.array([stat(x[row]) for row in idx])
    a = (1 - level) / 2
    return float(np.quantile(vals, a)), float(np.quantile(vals, 1 - a))


def adjust_pvalues(pvals: Sequence[float], method: str) -> List[float]:
    """Correction des tests multiples (§40) : bonferroni, holm ou bh
    (Benjamini-Hochberg, taux de fausses découvertes)."""
    p = np.asarray(pvals, dtype=float)
    m = len(p)
    order = np.argsort(p)
    out = np.empty(m)
    if method == "bonferroni":
        return [float(min(1.0, x * m)) for x in p]
    if method == "holm":
        running = 0.0
        for rank, i in enumerate(order):
            running = max(running, min(1.0, (m - rank) * p[i]))
            out[i] = running
        return [float(x) for x in out]
    if method == "bh":
        running = 1.0
        for rank in range(m - 1, -1, -1):
            i = order[rank]
            running = min(running, p[i] * m / (rank + 1))
            out[i] = min(1.0, running)
        return [float(x) for x in out]
    raise ValueError(f"méthode inconnue : {method}")


def pbo(matrix: np.ndarray, blocks: int = 10, embargo: int = 0) -> Dict[str, Any]:
    """Probabilité de sur-ajustement du backtest (§38, validation croisée
    combinatoire symétrique de Bailey et López de Prado) : les jours sont
    coupés en `blocks` blocs ; pour chaque moitié de blocs prise comme
    apprentissage, le meilleur réglage y est choisi, puis classé sur l'autre
    moitié. PBO = part des cas où il finit dans la moitié basse. Les
    `embargo` premiers jours de chaque bloc sont écartés (trades à cheval)."""
    r = np.asarray(matrix, dtype=float)
    T, N = r.shape
    size = T // blocks
    if N < 2 or size <= embargo + 5:
        return {"pbo": None, "combos": 0}
    groups = [r[T - size * blocks + b * size + embargo: T - size * blocks + (b + 1) * size] for b in range(blocks)]
    s1 = np.array([g.sum(axis=0) for g in groups])
    s2 = np.array([(g ** 2).sum(axis=0) for g in groups])
    cnt = np.array([len(g) for g in groups], dtype=float)

    def sharpe(sel: Sequence[int]) -> np.ndarray:
        n = cnt[list(sel)].sum()
        m = s1[list(sel)].sum(axis=0) / n
        v = s2[list(sel)].sum(axis=0) / n - m ** 2
        return np.where(v > 0, m / np.sqrt(np.maximum(v, 1e-18)), 0.0)

    logits, oos_best, oos_med = [], [], []
    for combo in itertools.combinations(range(blocks), blocks // 2):
        rest = [b for b in range(blocks) if b not in combo]
        is_sr, oos_sr = sharpe(combo), sharpe(rest)
        best = int(np.argmax(is_sr))
        rank = 1 + int((oos_sr < oos_sr[best]).sum()) + 0.5 * int((oos_sr == oos_sr[best]).sum() - 1)
        w = rank / (N + 1)
        logits.append(math.log(w / (1 - w)))
        oos_best.append(float(oos_sr[best]))
        oos_med.append(float(np.median(oos_sr)))
    lg = np.array(logits)
    return {"pbo": float((lg <= 0).mean()), "combos": len(lg), "median_logit": float(np.median(lg)),
            "oos_loss": float((np.array(oos_best) < 0).mean()),
            "best_vs_median": float(np.mean(np.array(oos_best) - np.array(oos_med)) * math.sqrt(365))}


# ---------- Simulations éprouvées (§41-48) ----------

def _metrics(close: pd.DataFrame, volume: Optional[pd.DataFrame], p: ts.TrendParams, pre: Any,
             periods: Sequence[Tuple[str, str]], hooks: Optional[ts.BacktestHooks] = None,
             capital: float = 10_000.0) -> List[Dict[str, float]]:
    return [ts.backtest(close, volume, p, a, b, capital=capital, pre=pre, hooks=hooks).metrics for a, b in periods]


def delayed_entries(close: pd.DataFrame, p: ts.TrendParams, delay: int, pre: Any) -> ts.BacktestHooks:
    """Achats exécutés `delay` jours après leur décision (PC éteint, bot en
    retard), au cours de clôture de ce jour-là : même stop, risque jamais
    plus grand (ts.reprice_entry), achat annulé si le cours est retombé près
    du stop ; plafonds de positions et de risque revérifiés."""
    cols, _reg = pre
    queue: Dict[int, List[Dict[str, Any]]] = {}

    def filter_plans(i: int, plans: List[Dict[str, Any]], holdings: Dict[str, ts.Holding],
                     equity: float) -> List[Dict[str, Any]]:
        queue[i + delay] = list(plans)
        cash = equity - sum(h.qty * float(cols[a]["close"][i]) for a, h in holdings.items())
        risk = sum(h.risk_quote for h in holdings.values())
        out: List[Dict[str, Any]] = []
        for plan in queue.pop(i, []):
            a = plan["asset"]
            px = float(cols[a]["close"][i])
            if a in holdings or any(x["asset"] == a for x in out) or not math.isfinite(px) \
                    or len(holdings) + len(out) >= p.max_positions:
                continue
            q = ts.reprice_entry(plan, px, equity, cash, p)
            if q is None or risk + q["risk_quote"] > p.max_total_risk * equity + 1e-9:
                continue
            q["ref_price"] = px
            cash -= q["cost"]
            risk += q["risk_quote"]
            out.append(q)
        return out
    return ts.BacktestHooks(filter_plans=filter_plans)


def random_entries(index_len: int, assets: Sequence[str], rate: float, seed: int) -> ts.BacktestHooks:
    """Référence (§49) : achats tirés au hasard, au rythme des signaux de la
    règle ; stops, taille, plafonds, régime et coûts inchangés. Mesure ce
    qu'apporte la cassure elle-même."""
    rng = np.random.default_rng(seed)
    picks = rng.random((index_len, len(assets))) < rate
    order = rng.random((index_len, len(assets)))
    col = {a: k for k, a in enumerate(assets)}

    def choose(i: int, snap: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        out = {}
        for a, s in snap.items():
            k = col.get(a)
            hit = k is not None and bool(picks[i, k])
            out[a] = dict(s, prior_high=0.0 if hit else np.inf, mom=float(order[i, k]) + 1e-6 if hit else -1.0)
        return out
    return ts.BacktestHooks(choose=choose)


def signal_rate(pre: Any, p: ts.TrendParams) -> float:
    """Part des jours achetables (régime haussier, données et volume
    suffisants) où la règle voit une cassure."""
    cols, reg = pre
    seen = hits = 0
    for c in cols.values():
        with np.errstate(invalid="ignore"):
            ok = reg & np.isfinite(c["vol"]) & (c["age"] >= p.min_history) & np.isfinite(c["vol30"]) \
                & (c["vol30"] >= p.min_volume_usd)
            sig = ok & (c["close"] > c["prior_high"]) & (c["mom"] > 0)
        seen += int(ok.sum())
        hits += int(sig.sum())
    return hits / max(1, seen)


def capacity(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, start: str, end: str, pre: Any,
             capitals: Sequence[float] = CAPITALS) -> List[Dict[str, Any]]:
    """Capacité (§45) : chaque achat du backtest rapporté au volume moyen de
    30 jours de sa crypto ; à chaque capital, part du volume et impact de
    marché (loi en racine carrée) ajouté au glissement, puis backtest
    refait. Une estimation, pas une observation."""
    rows: List[Tuple[int, str, float]] = []

    def record(i: int, plans: List[Dict[str, Any]], _h: Any, _e: float) -> List[Dict[str, Any]]:
        rows.extend((i, x["asset"], float(x["cost"])) for x in plans)
        return plans
    base = capitals[0]
    ts.backtest(close, volume, p, start, end, capital=base, pre=pre, hooks=ts.BacktestHooks(filter_plans=record))
    adv = volume.rolling(30, min_periods=10).mean()
    sigma = close.pct_change().rolling(30, min_periods=10).std()
    part = np.array([cost / float(adv[a].iloc[i]) if a in adv and float(adv[a].iloc[i] or 0) > 0 else np.nan
                     for i, a, cost in rows])
    sig = np.array([float(sigma[a].iloc[i]) if a in sigma else np.nan for i, a, _c in rows])
    weights = np.array([c for _i, _a, c in rows])
    out = []
    ref_cagr = None
    for cap in capitals:
        scaled = part * cap / base
        ok = np.isfinite(scaled) & np.isfinite(sig)
        impact = float(np.average(IMPACT_Y * sig[ok] * np.sqrt(scaled[ok]), weights=weights[ok])) if ok.any() else 0.0
        q = dataclasses.replace(p, slippage=p.slippage + impact)
        m = ts.backtest(close, volume, q, start, end, capital=cap, pre=pre).metrics
        ref_cagr = m.get("cagr_pct", 0.0) if ref_cagr is None else ref_cagr
        p95 = float(np.nanpercentile(scaled, 95)) if ok.any() else float("nan")
        out.append({"capital": cap, "participation_median": float(np.nanmedian(scaled)) if ok.any() else float("nan"),
                    "participation_p95": p95, "impact_pct": impact * 100, "cagr_pct": m.get("cagr_pct", 0.0),
                    "sharpe": m.get("sharpe", 0.0), "max_dd_pct": m.get("max_dd_pct", 0.0),
                    "ok": bool(p95 <= PARTICIPATION_MAX and m.get("cagr_pct", 0.0) >= 0.5 * ref_cagr > 0)})
    return out


def bootstrap_paths(daily: np.ndarray, days: int = 1095, block: int = 30, sims: int = 2000,
                    seed: int = 7) -> Dict[str, float]:
    """Monte-Carlo (§41-42) : trois ans recomposés au hasard par blocs de 30
    jours de la vraie courbe du capital (positions simultanées et
    corrélations gardées dans chaque bloc)."""
    x = np.asarray(daily, dtype=float)
    rng = np.random.default_rng(seed)
    k = -(-days // block)
    starts = rng.integers(0, len(x) - block, size=(sims, k))
    paths = x[(starts[:, :, None] + np.arange(block)).reshape(sims, -1)[:, :days]]
    eq = np.cumprod(1 + paths, axis=1)
    peak = np.maximum.accumulate(np.hstack([np.ones((sims, 1)), eq]), axis=1)[:, 1:]
    mdd = ((peak - eq) / peak).max(axis=1)
    fin = eq[:, -1] - 1
    return {"ret_p5": float(np.percentile(fin, 5) * 100), "ret_p50": float(np.percentile(fin, 50) * 100),
            "ret_p95": float(np.percentile(fin, 95) * 100), "prob_loss": float((fin < 0).mean()),
            "dd_p50": float(np.percentile(mdd, 50) * 100), "dd_p95": float(np.percentile(mdd, 95) * 100),
            "p_dd20": float((mdd >= 0.20).mean()), "p_dd30": float((mdd >= 0.30).mean()),
            "p_ruin": float((mdd >= RUIN_DD).mean()), "sims": sims, "days": days}


def regimes(res: ts.PortfolioResult, close: pd.DataFrame, p: ts.TrendParams) -> List[Dict[str, Any]]:
    """Résultats par régime (§44) : BTC au-dessus ou au-dessous de sa
    moyenne, volatilité de BTC haute ou basse (au-dessus de sa médiane
    passée)."""
    rets = res.equity.pct_change().dropna()
    bull = ts.btc_regime(close["btc"], p).reindex(rets.index).fillna(False)
    vol = close["btc"].pct_change().rolling(30, min_periods=20).std()
    high = (vol > vol.expanding(min_periods=60).median()).reindex(rets.index).fillna(False)
    entries = pd.Series([t["entry_date"] for t in res.trades])
    out = []
    for name, mask in (("BTC haussier", bull), ("BTC baissier", ~bull), ("volatilité haute", high),
                       ("volatilité basse", ~high)):
        r = rets[mask]
        if len(r) < 20:
            continue
        sd = float(r.std())
        n_tr = int(entries.isin(r.index).sum()) if len(entries) else 0
        out.append({"regime": name, "days": int(len(r)), "return_pct": float((np.prod(1 + r.values) - 1) * 100),
                    "sharpe": float(r.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0,
                    "worst_day_pct": float(r.min() * 100), "trades": n_tr})
    return out


def benchmark(equity: pd.Series, bench: pd.Series) -> Dict[str, float]:
    """Comparaison à une référence (§28) : alpha annualisé, bêta,
    corrélation, écart de suivi, ratio d'information, captures haussière
    et baissière."""
    a = equity.pct_change()
    b = bench.reindex(equity.index).ffill().pct_change()
    df = pd.concat([a, b], axis=1).dropna()
    x, y = df.iloc[:, 1].values, df.iloc[:, 0].values
    vb = float(np.var(x, ddof=1))
    beta = float(np.cov(y, x, ddof=1)[0, 1] / vb) if vb > 0 else 0.0
    alpha = float((y.mean() - beta * x.mean()) * 365)
    te = float(np.std(y - x, ddof=1) * math.sqrt(365))
    up, down = x > 0, x < 0
    return {"alpha_pct": alpha * 100, "beta": beta, "correlation": float(np.corrcoef(y, x)[0, 1]),
            "tracking_error_pct": te * 100, "information_ratio": float((y - x).mean() * 365 / te) if te > 0 else 0.0,
            "up_capture": float(y[up].mean() / x[up].mean()) if up.any() and x[up].mean() else 0.0,
            "down_capture": float(y[down].mean() / x[down].mean()) if down.any() and x[down].mean() else 0.0}


def accounting(res: ts.PortfolioResult, capital: float) -> Dict[str, Any]:
    """Comptabilité (§23, §69) : au dernier jour tout en argent liquide, le
    capital = départ + somme des gains et pertes des trades clos à cette
    date (conservation de l'argent), et le capital n'est jamais négatif."""
    flat = res.exposure[res.exposure.abs() < 1e-12]
    finite = bool(np.isfinite(res.equity.values).all() and (res.equity.values > 0).all())
    if flat.empty:
        return {"ok": finite, "checked": False, "detail": "jamais entièrement en argent liquide"}
    d = flat.index[-1]
    pnl = sum(t["pnl"] for t in res.trades if t["exit_date"] <= d)
    gap = float(res.equity.loc[d] - capital - pnl)
    ok = finite and abs(gap) <= 1e-6 * max(1.0, capital)
    return {"ok": ok, "checked": True, "day": str(d.date()), "gap": gap,
            "detail": f"au {d.date()}, capital − départ − gains des trades = {fr(gap, '.2e')}"}


# ---------- Note, verdict, prêt pour le risque (§51-53, §77, §81) ----------

def quality(components: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """Note de qualité (§52), pondérations versionnées ; une composante non
    mesurée compte zéro (ce qui n'est pas prouvé n'est pas acquis)."""
    vals = {k: (max(0.0, min(1.0, float(components.get(k)))) if components.get(k) is not None else 0.0)
            for k in QUALITY_WEIGHTS}
    return {"score": round(sum(QUALITY_WEIGHTS[k] * v for k, v in vals.items()), 3), "components": vals,
            "missing": [k for k in QUALITY_WEIGHTS if components.get(k) is None], "version": QUALITY_VERSION}


def verdict(r: Dict[str, Any]) -> Dict[str, Any]:
    """Verdict du backtest et liste « prêt pour le moteur de risque » (§81),
    à partir des mesures du rapport ; chaque raison est dite."""
    data, stats, wf, rob, mc = r["data"], r["stats"], r.get("walk_forward"), r.get("neighbours"), r["monte_carlo"]
    m_is, m_oos = r["is"], r["oos"]
    costs = {row["multiplier"]: row for row in r["costs"]}
    delay1 = next((x for x in r["latency"] if x["delay"] == 1), None)
    reject, warn = [], []
    if "Fuite vers le futur" in data["critical"]:
        reject.append("regard vers le futur détecté")
    if m_oos.get("trades", 0) + m_is.get("trades", 0) < MIN_TRADES:
        reject.append("trop peu de trades")
    if m_oos.get("cagr_pct", 0) <= 0:
        reject.append("perdante hors échantillon (depuis 2023)")
    two = costs.get(2.0)
    if two and two["is"].get("cagr_pct", 0) <= 0 and two["oos"].get("cagr_pct", 0) <= 0:
        reject.append("perdante avec des frais doublés")
    if stats.get("psr", 0) < 0.5:
        reject.append("Sharpe probabiliste sous 50 %")
    if rob and rob.get("pbo") is not None and rob["pbo"] > 0.5:
        reject.append(f"probabilité de sur-ajustement {fr(rob['pbo'] * 100, '.0f')} %")
    invalid = bool([c for c in data["critical"] if c != "Fuite vers le futur"]) or not r["accounting"]["ok"] \
        or not r["reproducible"]
    if data["status"] == "WARN":
        warn.append(f"données : note {fr(data['score'], '.0f')}/100")
    if delay1 and (delay1["is"].get("cagr_pct", 0) <= 0 or delay1["oos"].get("cagr_pct", 0) <= 0):
        warn.append("un jour de retard à l'achat la rend perdante sur une époque")
    if mc["p_ruin"] > RUIN_MAX:
        warn.append(f"Monte-Carlo : {fr(mc['p_ruin'] * 100, '.0f')} % de chances d'atteindre −40 % en trois ans")
    if stats.get("dsr") is not None and stats["dsr"] < DSR_MIN:
        warn.append(f"Sharpe dégonflé {fr(stats['dsr'] * 100, '.0f')} % (essais multiples)")
    status = ("INVALID" if invalid else "REJECTED" if reject else
              "VALID_WITH_WARNINGS" if warn or r["quality"]["score"] < 0.8 else "VALID")

    def item(ok: Optional[bool], good: str = "ACCEPTABLE") -> str:
        return "NON MESURÉ" if ok is None else good if ok else "FAIL"
    oos_ratio = (m_oos.get("sharpe", 0) / m_is["sharpe"]) if m_is.get("sharpe", 0) > 0 else None
    checklist = [
        ("Validation des données", item(data["status"] != "FAIL", "PASS")),
        ("Anti-regard vers le futur", item("Fuite vers le futur" not in data["critical"], "PASS")),
        ("Validation de la stratégie", item(r["strategy"]["status"] == "READY_FOR_BACKTEST", "PASS")),
        ("Réalisme de l'exécution", item(bool(two and two["is"].get("cagr_pct", 0) > 0
                                              and two["oos"].get("cagr_pct", 0) > 0))),
        ("Backtest", item(status in ("VALID", "VALID_WITH_WARNINGS"), "PASS")),
        ("Hors échantillon", item(oos_ratio is not None and oos_ratio >= 0.5 and m_oos.get("cagr_pct", 0) > 0)),
        ("Walk-forward", item(None if wf is None else wf["cagr_pct"] > 0)),
        ("Robustesse (voisins, PBO)", item(None if rob is None else rob["plateau"] >= 0.8
                                           and (rob.get("pbo") is None or rob["pbo"] <= 0.5))),
        ("Validation statistique", item(stats.get("psr", 0) >= PSR_MIN and (stats.get("dsr") is None
                                                                            or stats["dsr"] >= DSR_MIN)
                                        if rob is not None else None)),
        ("Stress (Monte-Carlo, −40 %)", item(mc["p_ruin"] <= RUIN_MAX)),
        ("Coûts (frais doublés)", item(bool(two and two["is"].get("cagr_pct", 0) > 0
                                            and two["oos"].get("cagr_pct", 0) > 0))),
        ("Liquidité (capacité à 100 000 USDT)", item(any(x["capital"] == 1e5 and x["ok"] for x in r["capacity"]))),
        ("Contrôles de biais", item(not r["red_team"]["critical"], "PASS")),
        ("Reproductibilité", item(r["reproducible"], "PASS")),
    ]
    ready = all(v in ("PASS", "ACCEPTABLE") for _n, v in checklist)
    readiness = "REJECTED" if status in ("REJECTED", "INVALID") else "READY_FOR_RISK" if ready else "RESEARCH_ONLY"
    return {"status": status, "readiness": readiness, "rejections": reject, "warnings": warn,
            "checklist": checklist, "oos_ratio": oos_ratio}


def red_team(r: Dict[str, Any]) -> Dict[str, Any]:
    """Équipe rouge (§60) : les biais cherchés et ce qui a été trouvé, sans
    complaisance."""
    data = {n: (ok, d) for n, ok, _c, d in r["data"]["checks"]}
    found = [
        ("Regard vers le futur", data["Fuite vers le futur"][0], data["Fuite vers le futur"][1]),
        ("Survivants", False, "cryptos retirées de la cote absentes : biais possible, mesuré à part (--cm)"),
        ("Exécutions impossibles", True, "achats et ventes à la clôture, glissement contre le bot, jamais au prix "
                                         "du signal ; sorties sous le stop au cours de clôture, pas au stop"),
        ("Coûts oubliés", True, "frais et glissement à l'achat et à la vente ; stress jusqu'à × 3"),
        ("Fuseau horaire", data["Dates"][0], data["Dates"][1]),
        ("Comptabilité (gains et pertes)", r["accounting"]["ok"], r["accounting"]["detail"]),
        ("Taille et levier", True, "taille par le risque jusqu'au stop, plafonds, sans levier (Spot)"),
        ("Liquidité imaginaire", True, "volume minimum de 5 M$ ; capacité estimée à part"),
        ("Sur-optimisation", (r.get("neighbours") or {}).get("pbo") is not None
         and r["neighbours"]["pbo"] <= 0.5, "voisins et PBO" if r.get("neighbours") else "non mesurée (rapide)"),
    ]
    critical = [n for n, ok, _d in found if not ok and n in ("Regard vers le futur", "Comptabilité (gains et pertes)",
                                                             "Fuseau horaire")]
    return {"findings": found, "critical": critical}


# ---------- Le rapport complet ----------

def _period_metrics(close: pd.DataFrame) -> List[Tuple[str, str]]:
    return [IS_PERIOD, (OOS_START, str(close.index[-1].date()))]


def neighbours(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, start: str, end: str
               ) -> Dict[str, Any]:
    """Les 27 réglages voisins (§35-38) sur toute la période : part des
    voisins gagnants sur les deux époques (plateau), Sharpe de chacun,
    probabilité de sur-ajustement (PBO) et tests multiples."""
    pres: Dict[int, Any] = {}
    series, plateau, pvals, srs = [], 0, [], []
    for combo in itertools.product(*NEIGHBOURS.values()):
        q = dataclasses.replace(p, **dict(zip(NEIGHBOURS, combo)))
        if q.breakout_n not in pres:
            pres[q.breakout_n] = ts.precompute(close, volume, q)
        eq = ts.backtest(close, volume, q, start, end, pre=pres[q.breakout_n]).equity
        rets = eq.pct_change().fillna(0.0)
        series.append(rets.values)
        a, b = eq.loc[:IS_PERIOD[1]], eq.loc[OOS_START:]
        plateau += int(len(a) > 1 and len(b) > 1 and a.iloc[-1] > a.iloc[0] and b.iloc[-1] > b.iloc[0])
        st = sharpe_stats(rets.values[1:])
        pvals.append(st.get("p_value", 1.0))
        srs.append(st.get("sharpe_daily", 0.0))
    mat = np.column_stack(series)[1:]
    out = pbo(mat, blocks=10, embargo=20)
    holm, bh = adjust_pvalues(pvals, "holm"), adjust_pvalues(pvals, "bh")
    return {"n": len(series), "plateau": plateau / len(series), "pbo": out["pbo"], "pbo_detail": out,
            "sr_var": float(np.var(srs, ddof=1)), "significant_holm": sum(x < 0.05 for x in holm),
            "significant_bh": sum(x < 0.05 for x in bh), "best_p": min(pvals)}


def run(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams, quick: bool = False, seed: int = 7,
        now: Optional[datetime] = None) -> Dict[str, Any]:
    """Le backtest complet de la règle en vigueur, validé (§78) : données,
    simulation, mesures, épreuves, statistique, verdict. `quick` saute le
    walk-forward et les réglages voisins (le verdict ne peut alors pas dire
    « prêt pour le risque »)."""
    end = str(close.index[-1].date())
    start = IS_PERIOD[0]
    cfg = config(p, start, end, seed=seed)
    data = validate_data(close, volume, p)
    strategy = ms.validate(ms.spec_for(p), p, close, volume)
    pre = ts.precompute(close, volume, p)
    full = ts.backtest(close, volume, p, start, end, pre=pre)
    again = ts.backtest(close, volume, p, start, end, pre=ts.precompute(close, volume, p))
    reproducible = result_hash(full) == result_hash(again)
    periods = _period_metrics(close)
    m_is, m_oos = _metrics(close, volume, p, pre, periods)
    daily = full.equity.pct_change().dropna().values
    r: Dict[str, Any] = {"config": cfg, "manifest": manifest(cfg, close, volume, full, now), "data": data,
                         "strategy": strategy, "full": full.metrics, "is": m_is, "oos": m_oos,
                         "reproducible": reproducible, "accounting": accounting(full, cfg["capital"])}
    r["costs"] = [{"multiplier": k, **dict(zip(("is", "oos"), _metrics(
        close, volume, dataclasses.replace(p, fee=p.fee * k, slippage=p.slippage * k), pre, periods)))}
        for k in COST_MULTIPLIERS]
    r["slippage"] = [{"extra": x, **dict(zip(("is", "oos"), _metrics(
        close, volume, dataclasses.replace(p, slippage=p.slippage * (1 + x)), pre, periods)))} for x in SLIPPAGE_EXTRA]
    r["latency"] = [{"delay": d, **dict(zip(("is", "oos"), _metrics(
        close, volume, p, pre, periods, None if d == 0 else delayed_entries(close, p, d, pre))))} for d in DELAYS]
    r["capacity"] = capacity(close, volume, p, OOS_START, end, pre)
    r["regimes"] = regimes(full, close, p)
    btc = close["btc"].loc[start:end]
    r["benchmarks"] = {"btc": {**benchmark(full.equity, btc), **ts.compute_metrics(btc / btc.iloc[0] * 1e4, [])},
                       "basket": ts.buy_and_hold(close, list(close.columns), start, end)}
    rate = signal_rate(pre, p)
    rand = [ts.backtest(close, volume, p, start, end, pre=pre,
                        hooks=random_entries(len(close.index), list(pre[0]), rate, seed + k)).metrics for k in range(10)]
    r["random"] = {"rate": rate, "runs": len(rand), "cagr_median": float(np.median([x["cagr_pct"] for x in rand])),
                   "sharpe_median": float(np.median([x["sharpe"] for x in rand])),
                   "beaten": sum(full.metrics["sharpe"] > x["sharpe"] for x in rand)}
    r["monte_carlo"] = {**bootstrap_paths(daily, seed=seed), **{f"trades_{k}": v for k, v in
                                                               ts.monte_carlo(full.trades, p.risk_pct, seed=seed).items()}}
    r["neighbours"] = None if quick else neighbours(close, volume, p, start, end)
    nb = r["neighbours"]
    r["stats"] = sharpe_stats(daily, trials=nb["n"] if nb else 1, trials_var=nb["sr_var"] if nb else None)
    if nb:                          # compte prudent : bien plus d'essais que les 27 voisins
        r["stats"]["dsr_prudent"] = sharpe_stats(daily, trials=100, trials_var=nb["sr_var"]).get("dsr")
    r["stats"]["ci_sharpe_bootstrap"] = block_bootstrap(daily, lambda x: x.mean() / x.std() * math.sqrt(365)
                                                        if x.std() > 0 else 0.0, seed=seed)
    r["stats"]["ci_cagr_bootstrap"] = tuple(v * 100 for v in block_bootstrap(
        daily, lambda x: float(np.prod(1 + x) ** (365 / len(x)) - 1), seed=seed))
    if quick:
        r["walk_forward"] = None
    else:
        rows, eq = ts.walk_forward(close, volume, p, "2020-01-01", end)
        wm = ts.compute_metrics(eq, []) if len(eq) > 1 else {}
        r["walk_forward"] = {"windows": len(rows), "positive": int((rows["oos_return_pct"] > 0).sum()),
                             "cagr_pct": wm.get("cagr_pct", 0.0), "max_dd_pct": wm.get("max_dd_pct", 0.0),
                             "sharpe": wm.get("sharpe", 0.0),
                             "changes": int((rows[list(ts.DEFAULT_GRID)].diff().abs().sum(axis=1) > 0).sum())}
    oos_ratio = (m_oos.get("sharpe", 0) / m_is["sharpe"]) if m_is.get("sharpe", 0) > 0 else None
    two = next(x for x in r["costs"] if x["multiplier"] == 2.0)
    stress_ok = [x["is"].get("cagr_pct", 0) > 0 and x["oos"].get("cagr_pct", 0) > 0
                 for x in (two, next(x for x in r["slippage"] if x["extra"] == 1.0),
                           next(x for x in r["latency"] if x["delay"] == 1))]
    r["overfitting"] = ms.overfitting_risk(len(ms.TUNED), len(ms.compile_spec(ms.spec_for(p), p).conditions()) + 3,
                                           full.metrics.get("trades"), oos_ratio, nb["plateau"] if nb else None)
    r["quality"] = quality({"data": data["score"] / 100, "execution": sum(stress_ok) / len(stress_ok),
                            "sample": min(1.0, full.metrics.get("trades", 0) / 300),
                            "oos": None if oos_ratio is None else min(1.0, max(0.0, oos_ratio)),
                            "robustness": None if nb is None else (nb["plateau"] + (1 - (nb["pbo"] or 0))) / 2,
                            "statistics": r["stats"].get("dsr", r["stats"].get("psr")) if nb else None,
                            "reproducibility": 1.0 if reproducible else 0.0})
    r["red_team"] = red_team(r)
    r["verdict"] = verdict(r)
    r["result"] = result_of(r)
    return r


def result_of(r: Dict[str, Any]) -> BacktestResult:
    """Le contrat BacktestResult.v1 du rapport."""
    m, v = r["manifest"], r["verdict"]
    keys = ("cagr_pct", "max_dd_pct", "sharpe", "sortino", "calmar", "trades", "win_rate_pct", "profit_factor",
            "expectancy_r")
    return BacktestResult(m["run_id"], v["status"], v["readiness"], m["strategy_id"], m["strategy_version"],
                          m["dataset"]["hash"], m["config_hash"], m["result_hash"],
                          (tuple(IS_PERIOD), (OOS_START, r["config"]["end"])),
                          {"is": {k: r["is"].get(k) for k in keys}, "oos": {k: r["oos"].get(k) for k in keys},
                           "psr": r["stats"].get("psr"), "dsr": r["stats"].get("dsr")},
                          r["quality"]["score"], tuple(v["warnings"]), tuple(v["rejections"]), LIMITATIONS,
                          m["executed_at"])


# ---------- Rendu ----------

def settings_text(params: Dict[str, Any]) -> str:
    """Les réglages en vigueur qui diffèrent de ceux de la recherche."""
    ref = json.loads(json.dumps(dataclasses.asdict(ts.TrendParams())))
    diff = [f"{k} {json.dumps(v) if isinstance(v, list) else fr_plain(v)} (recherche : "
            f"{json.dumps(ref[k]) if isinstance(ref[k], list) else fr_plain(ref[k])})"
            for k, v in params.items() if k in ref and v != ref[k]]
    return ("Réglages en vigueur différents de ceux de la recherche : " + ", ".join(diff) + "."
            if diff else "Réglages de la recherche, inchangés.")


def _row(name: str, m: Dict[str, float]) -> str:
    return (f"| {name} | {fr(m.get('cagr_pct', 0), '+.1f')} % | {fr(m.get('max_dd_pct', 0), '.1f')} % | "
            f"{fr(m.get('sharpe', 0), '.2f')} | {fr(m.get('calmar', 0), '.2f')} | {m.get('trades', 0)} |")


MODELS_FR = {"fee_model": "frais", "slippage_model": "glissement", "spread_model": "écart achat-vente",
             "impact_model": "impact de marché", "latency_model": "délai d'exécution", "execution_model": "exécution",
             "liquidity_model": "liquidité", "margin_model": "marge", "funding_model": "financement",
             "borrow_model": "emprunt", "corporate_action_model": "opérations sur titres"}
QUALITY_FR = {"data": "données", "execution": "exécution", "sample": "échantillon", "oos": "hors échantillon",
              "robustness": "robustesse", "statistics": "statistique", "reproducibility": "reproductibilité"}
STATUS_FR = {"VALID": "VALIDE", "VALID_WITH_WARNINGS": "VALIDE AVEC RÉSERVES", "INVALID": "INVALIDE",
             "REJECTED": "REJETÉ"}
READY_FR = {"READY_FOR_RISK": "prête pour le moteur de risque", "RESEARCH_ONLY": "recherche seulement",
            "REJECTED": "rejetée"}


def render(r: Dict[str, Any]) -> str:
    """Le rapport du backtest (§55) en Markdown : résumé, stratégie, données,
    configuration, exécution et coûts, performance, hors échantillon,
    robustesse, Monte-Carlo, stress, capacité, régimes, références,
    statistique, biais, verdict, limites."""
    v, m, s = r["verdict"], r["manifest"], r["stats"]
    q = r["quality"]
    lines = ["# Validation du backtest de la règle (moteur de backtest, étape 10)", "",
             f"Rapport reproductible : `python trendguard_bot.py validation --cache data_binance --out "
             f"docs/VALIDATION.md`. Données Binance jusqu'au {r['config']['end']}.", "",
             "## 1. Résumé", "",
             f"Verdict : **{STATUS_FR[v['status']]}**, {READY_FR[v['readiness']]}. Note de qualité "
             f"{fr(q['score'] * 100, '.0f')}/100.", ""]
    lines += [f"- Réserve : {w}" for w in v["warnings"]] + [f"- Rejet : {x}" for x in v["rejections"]]
    lines += ["", f"> {NO_GUARANTEE}", "", "## 2. Stratégie et données", "",
              f"Fiche `{m['strategy_id']}` v{m['strategy_version']} (empreinte {m['spec_hash']}) : "
              + ("prête pour le backtest." if r["strategy"]["status"] == "READY_FOR_BACKTEST" else "BLOQUÉE."),
              f"Données : {m['dataset']['first']} → {m['dataset']['last']}, {len(m['dataset']['assets'])} cryptos, "
              f"empreinte {m['dataset']['hash']} ; note {fr(r['data']['score'], '.0f')}/100 ({r['data']['status']}).",
              settings_text(r["config"]["params"]),
              "", "| Contrôle des données | Résultat | Détail |", "| --- | --- | --- |"]
    lines += [f"| {n} | {'✓' if ok else '✗' if crit else '!'} | {d} |" for n, ok, crit, d in r["data"]["checks"]]
    lines += ["", "## 3. Configuration et manifeste", "",
              f"Moteur {m['engine_version']}, code `{m['git']}` (empreinte {m['code_hash']}), configuration "
              f"{m['config_hash']}, environnement {m['environment_hash']} (Python {m['environment']['python']}, "
              f"numpy {m['environment']['numpy']}, pandas {m['environment']['pandas']}), graine {m['seed']}. "
              f"Résultat {m['result_hash']} ; refait : " + ("identique." if r["reproducible"] else "DIFFÉRENT."),
              "", "## 4. Exécution et coûts", ""]
    lines += [f"- {MODELS_FR[k]} : {r['config'][k]}" for k in MODELS_FR]
    lines += ["", "## 5. Performance et risque", "", "| Période | CAGR | Pire baisse | Sharpe | Calmar | Trades |",
              "| --- | --- | --- | --- | --- | --- |", _row("2018-2022 (apprentissage)", r["is"]),
              _row("Depuis 2023 (hors échantillon)", r["oos"]), _row("2018 → aujourd'hui", r["full"]), ""]
    f = r["full"]
    lines += [f"Gagnants {fr(f.get('win_rate_pct', 0), '.0f')} %, gain moyen {fr(f.get('avg_win_r', 0), '.2f')} R, "
              f"perte moyenne {fr(f.get('avg_loss_r', 0), '.2f')} R, espérance {fr(f.get('expectancy_r', 0), '+.2f')} R "
              f"par trade (frais compris), facteur de profit {fr(f.get('profit_factor', 0), '.2f')}, "
              f"durée moyenne {fr(f.get('avg_days', 0), '.0f')} jours.", "", "## 6. Hors échantillon et walk-forward", ""]
    ratio = v["oos_ratio"]
    lines.append("Sharpe depuis 2023 / Sharpe 2018-2022 : " + (fr(ratio, ".2f") if ratio is not None else "non mesurable")
                 + f" ; risque de sur-ajustement {r['overfitting']['level']}"
                 + (f" ({' ; '.join(r['overfitting']['reasons'])})" if r["overfitting"]["reasons"] else "") + ".")
    wf = r["walk_forward"]
    lines.append("Walk-forward : non mesuré (rapport rapide)." if wf is None else
                 f"Walk-forward 2020 → aujourd'hui (réglages choisis sur les 3 années précédentes, testés 6 mois) : "
                 f"{wf['positive']}/{wf['windows']} fenêtres en gain, CAGR enchaîné {fr(wf['cagr_pct'], '+.1f')} %, "
                 f"pire baisse {fr(wf['max_dd_pct'], '.1f')} %, réglages changés {wf['changes']} fois.")
    nb = r["neighbours"]
    lines += ["", "## 7. Robustesse : réglages voisins", ""]
    lines.append("Non mesurée (rapport rapide)." if nb is None else
                 f"{nb['n']} réglages voisins : {fr(nb['plateau'] * 100, '.0f')} % gagnants sur les deux époques ; "
                 f"probabilité de sur-ajustement (PBO, {nb['pbo_detail']['combos']} découpages) "
                 f"{fr((nb['pbo'] or 0) * 100, '.0f')} % ; après correction des tests multiples, "
                 f"{nb['significant_holm']} voisins restent significatifs (Holm), {nb['significant_bh']} (BH).")
    mc = r["monte_carlo"]
    lines += ["", "## 8. Monte-Carlo", "",
              f"{mc['sims']} trajectoires de trois ans par blocs de 30 jours : rendement médian "
              f"{fr(mc['ret_p50'], '+.0f')} % (5 % pires : {fr(mc['ret_p5'], '+.0f')} %), en perte "
              f"{fr(mc['prob_loss'] * 100, '.0f')} % des fois ; pire baisse médiane {fr(mc['dd_p50'], '.0f')} %, "
              f"{fr(mc['dd_p95'], '.0f')} % une fois sur 20 ; −40 % (arrêt d'urgence) atteint "
              f"{fr(mc['p_ruin'] * 100, '.0f')} % des fois.", "", "## 9. Stress : coûts, glissement, retard", "",
              "| Épreuve | CAGR 2018-2022 | CAGR depuis 2023 | Sharpe depuis 2023 |", "| --- | --- | --- | --- |"]
    for x in r["costs"]:
        lines.append(f"| Frais et glissement × {fr_plain(x['multiplier'])} | {fr(x['is'].get('cagr_pct', 0), '+.1f')} % | "
                     f"{fr(x['oos'].get('cagr_pct', 0), '+.1f')} % | {fr(x['oos'].get('sharpe', 0), '.2f')} |")
    for x in r["slippage"]:
        lines.append(f"| Glissement + {fr_plain(x['extra'] * 100)} % | {fr(x['is'].get('cagr_pct', 0), '+.1f')} % | "
                     f"{fr(x['oos'].get('cagr_pct', 0), '+.1f')} % | {fr(x['oos'].get('sharpe', 0), '.2f')} |")
    for x in r["latency"][1:]:
        lines.append(f"| Achats {x['delay']} jour(s) en retard | {fr(x['is'].get('cagr_pct', 0), '+.1f')} % | "
                     f"{fr(x['oos'].get('cagr_pct', 0), '+.1f')} % | {fr(x['oos'].get('sharpe', 0), '.2f')} |")
    lines += ["", "## 10. Capacité (depuis 2023)", "",
              "| Capital | Part du volume (médiane, 95 %) | Impact estimé | CAGR | Sharpe | Réaliste |",
              "| --- | --- | --- | --- | --- | --- |"]
    for x in r["capacity"]:
        lines.append(f"| {fr(x['capital'], ',.0f')} USDT | {fr(x['participation_median'] * 100, '.3f')} % ; "
                     f"{fr(x['participation_p95'] * 100, '.3f')} % | {fr(x['impact_pct'], '.3f')} % | "
                     f"{fr(x['cagr_pct'], '+.1f')} % | {fr(x['sharpe'], '.2f')} | {'oui' if x['ok'] else 'non'} |")
    lines += ["", "## 11. Régimes", "", "| Régime | Jours | Rendement | Sharpe | Pire jour | Achats |",
              "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {x['regime']} | {x['days']} | {fr(x['return_pct'], '+.0f')} % | {fr(x['sharpe'], '.2f')} | "
              f"{fr(x['worst_day_pct'], '.1f')} % | {x['trades']} |" for x in r["regimes"]]
    b, rd = r["benchmarks"]["btc"], r["random"]
    lines += ["", "## 12. Références", "",
              f"BTC acheté et gardé : CAGR {fr(b.get('cagr_pct', 0), '+.1f')} %, pire baisse "
              f"{fr(b.get('max_dd_pct', 0), '.1f')} % ; face à lui, la règle : alpha {fr(b['alpha_pct'], '+.1f')} %/an, "
              f"bêta {fr(b['beta'], '.2f')}, corrélation {fr(b['correlation'], '.2f')}, capture haussière "
              f"{fr(b['up_capture'] * 100, '.0f')} %, baissière {fr(b['down_capture'] * 100, '.0f')} %.",
              f"Panier des cryptos à parts égales : CAGR {fr(r['benchmarks']['basket'].get('cagr_pct', 0), '+.1f')} %, "
              f"pire baisse {fr(r['benchmarks']['basket'].get('max_dd_pct', 0), '.1f')} %.",
              f"Achats au hasard au même rythme ({rd['runs']} tirages) : CAGR médian {fr(rd['cagr_median'], '+.1f')} %, "
              f"Sharpe médian {fr(rd['sharpe_median'], '.2f')} ; la règle fait mieux {rd['beaten']} fois sur "
              f"{rd['runs']}.", "", "## 13. Statistique", ""]
    ci = s.get("ci_sharpe_bootstrap", (0, 0))
    lines.append(f"Sharpe {fr(s.get('sharpe', 0), '.2f')} (intervalle à 95 % par blocs : {fr(ci[0], '.2f')} à "
                 f"{fr(ci[1], '.2f')}), t = {fr(s.get('t', 0), '.2f')}, p-valeur {fr(s.get('p_value', 1), '.4f')} ; "
                 f"Sharpe probabiliste {fr(s.get('psr', 0) * 100, '.1f')} %"
                 + (f" ; dégonflé pour {s['trials']} essais {fr(s['dsr'] * 100, '.1f')} % (seuil du hasard "
                    f"{fr(s['sr0_annual'], '.2f')}), pour 100 essais (compte prudent) "
                    f"{fr((s.get('dsr_prudent') or 0) * 100, '.1f')} %" if s.get("dsr") is not None else "") + ".")
    lines += ["", "## 14. Contrôles de biais (équipe rouge)", ""]
    lines += [f"- {'✓' if ok else '✗'} {n} : {d}" for n, ok, d in r["red_team"]["findings"]]
    lines += ["", "## 15. Prêt pour le moteur de risque ?", "", "| Critère | Résultat |", "| --- | --- |"]
    lines += [f"| {n} | {x} |" for n, x in v["checklist"]]
    lines += ["", f"Note de qualité {fr(q['score'] * 100, '.0f')}/100 (" + ", ".join(
        f"{QUALITY_FR[k]} {fr(val * 100, '.0f')}" for k, val in q["components"].items()) + f" ; {q['version']}).",
        "", "## 16. Limites", ""]
    lines += [f"- {x}" for x in LIMITATIONS]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `validation` : rapport complet (ou --rapide), écrit avec
    --out ; données jamais retéléchargées pendant l'étude."""
    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    ap = argparse.ArgumentParser(description="Moteur de backtest de TrendGuard : validation de la règle")
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    ap.add_argument("--out", default="")
    ap.add_argument("--rapide", action="store_true")
    ap.add_argument("--graine", type=int, default=7)
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    close, volume = load_history(args.cache, list(g.universe), max_age_days=None)
    r = run(close, volume, params_for(g), quick=args.rapide, seed=args.graine)
    text = render(r)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 0 if r["verdict"]["status"] in ("VALID", "VALID_WITH_WARNINGS") else 1
