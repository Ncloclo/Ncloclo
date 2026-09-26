#!/usr/bin/env python3
"""
Laboratoire de stratégies TrendGuard — « apprendre et adopter la meilleure
stratégie », mesuré honnêtement.

Trois questions, un protocole unique (choix sur 2018-2022, vérification sur
2023 → aujourd'hui, période jamais utilisée pour choisir) :

  1. Tournoi : sept stratégies définies À L'AVANCE (famille suivi de
     tendance, régimes alternatifs, rotation de momentum, retour à la
     moyenne à fort taux de réussite), toutes avec 1 % de risque par trade,
     les mêmes plafonds, frais et slippage que TrendGuard.
  2. Méta-apprentissage : un « chef d'orchestre » qui, tous les 6 mois,
     confie le capital à la stratégie la plus performante des 24 derniers
     mois (ou le répartit selon leurs Sharpe). Bat-il la stratégie fixe ?
  3. Probabilité de succès par horizon : sur 1, 3, 6, 12, 24, 36 mois,
     combien de fenêtres historiques finissent en gain ?

  python strategy_lab.py --cache data_binance --out docs/STRATEGIES.md

Les fonctions `tournament_recent` et `horizon_success` sont aussi utilisées
par l'auto-diagnostic (diagnostics.py) : le bot réévalue les alternatives
en continu, mais n'en change jamais seul (voir docs/STRATEGIES.md).
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import trend_strategy as ts

IS_PERIOD = ("2018-01-01", "2022-12-31")
OOS_START = "2023-01-01"
REF = "TrendGuard (référence)"


# ══════════════════════════════════════════════════════════════════════
# INDICATEURS SUPPLÉMENTAIRES
# ══════════════════════════════════════════════════════════════════════

def rsi(close: pd.Series, n: int = 2) -> pd.Series:
    """RSI de Wilder (causal)."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-12))


def breadth(close: pd.DataFrame, n: int = 150) -> pd.Series:
    """Part des actifs cotés au-dessus de leur moyenne n jours."""
    sma = close.rolling(n, min_periods=n).mean()
    return (close > sma).where(sma.notna()).mean(axis=1, skipna=True)


def lab_features(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams
                 ) -> Dict[str, Dict[str, np.ndarray]]:
    feats = {}
    for a in close.columns:
        f = ts.asset_features(close[a], p,
                              volume[a] if a in volume.columns else None)
        c = close[a].astype(float)
        f["sma150"] = c.rolling(150, min_periods=150).mean()
        f["sma5"] = c.rolling(5, min_periods=5).mean()
        f["rsi2"] = rsi(c, 2)
        feats[a] = {k: f[k].values for k in f.columns}
    return feats


def eligible(s: Dict[str, float], p: ts.TrendParams) -> bool:
    """Mêmes filtres d'historique et de liquidité que TrendGuard."""
    return (ts._finite(s.get("close"), s.get("vol"), s.get("mom"), s.get("vol30"))
            and s["age"] >= p.min_history and s["vol"] > 0
            and s["vol30"] >= p.min_volume_usd)


# ══════════════════════════════════════════════════════════════════════
# SIMULATEUR GÉNÉRIQUE (règles d'entrée / sortie personnalisées)
# ══════════════════════════════════════════════════════════════════════

def simulate(close: pd.DataFrame, feats: Dict[str, Dict[str, np.ndarray]],
             regime: np.ndarray, p: ts.TrendParams, start: str, end: str,
             entry_fn: Callable, exit_fn: Callable, rank_fn: Callable,
             entry_day: Optional[Callable] = None, capital: float = 10_000.0
             ) -> Tuple[pd.Series, List[Dict[str, Any]]]:
    """Portefeuille évalué à la clôture, comme ts.backtest : sorties, puis
    entrées classées par rank_fn, 1 % de risque jusqu'au stop initial
    (3 × vol), frais + slippage, plafonds de positions et de risque."""
    idx = close.index
    lo = idx.searchsorted(pd.Timestamp(start, tz="UTC"))
    hi = idx.searchsorted(pd.Timestamp(end, tz="UTC"), side="right")
    cost_out = p.fee + p.slippage
    cash = capital
    hold: Dict[str, Dict[str, Any]] = {}
    trades: List[Dict[str, Any]] = []
    curve = []
    for i in range(lo, hi):
        d = idx[i]
        snap = {a: {k: v[i] for k, v in f.items()} for a, f in feats.items()}
        for a, s in snap.items():
            s["_a"] = a
        bull = bool(regime[i])
        ctx = {"i": i, "date": d, "bull": bull, "snap": snap}
        for a in list(hold):
            h, s = hold[a], snap[a]
            if not ts._finite(s["close"]):
                px, reason = h["last"] * 0.5, "DELISTED"
            else:
                px, reason = s["close"], exit_fn(h, s, ctx)
            if not reason:
                h["last"] = px
                continue
            proceeds = h["qty"] * px * (1 - cost_out)
            cash += proceeds
            pnl = proceeds - h["cost"]
            trades.append({"asset": a, "entry_date": h["date"], "exit_date": d,
                           "entry": h["entry"], "exit": px, "pnl": pnl,
                           "r": pnl / h["risk"], "days": (d - h["date"]).days,
                           "reason": reason})
            del hold[a]
        equity = cash + sum(h["qty"] * snap[a]["close"] for a, h in hold.items())
        if bull and (entry_day is None or entry_day(d)):
            open_risk = sum(h["risk"] for h in hold.values())
            cands = [(rank_fn(s), a, s) for a, s in snap.items()
                     if a not in hold and entry_fn(s, ctx)]
            cands.sort(key=lambda x: x[0], reverse=True)
            for _k, a, s in cands:
                if len(hold) >= p.max_positions:
                    break
                risk_quote = p.risk_pct * equity
                if open_risk + risk_quote > p.max_total_risk * equity + 1e-9:
                    break
                entry = s["close"] * (1 + p.slippage)
                stop = ts.initial_stop(s["close"], s["vol"], p)
                if stop <= 0:
                    continue
                unit = entry * (1 + p.fee) - stop * (1 - p.fee - p.slippage)
                if unit <= 0:
                    continue
                qty = min(risk_quote / unit, p.max_position_pct * equity / entry)
                cost = qty * entry * (1 + p.fee)
                if cost > cash:
                    qty = cash / (entry * (1 + p.fee))
                    cost = qty * entry * (1 + p.fee)
                if qty * entry < 10:
                    continue
                cash -= cost
                hold[a] = {"qty": qty, "entry": entry, "stop": stop,
                           "high": s["close"], "date": d, "risk": qty * unit,
                           "cost": cost, "last": s["close"], "i0": i}
                open_risk += qty * unit
        curve.append(cash + sum(h["qty"] * snap[a]["close"] for a, h in hold.items()))
    return pd.Series(curve, index=idx[lo:hi]), trades


def _trail_exit(p: ts.TrendParams) -> Callable:
    def exit_fn(h, s, ctx):
        if s["close"] <= h["stop"]:
            return "STOP"
        h["high"] = max(h["high"], s["close"])
        if ts._finite(s["vol"]):
            h["stop"] = max(h["stop"], ts.trailing_stop(h["high"], s["vol"], p,
                                                        ctx["bull"]))
        return None
    return exit_fn


# ══════════════════════════════════════════════════════════════════════
# CANDIDATS (définis à l'avance, non optimisés)
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    rule: str


CANDIDATES: Tuple[Candidate, ...] = (
    Candidate(REF, "Suivi de tendance",
              "Cassure 30 j, momentum 90 j, régime BTC > moyenne 150 j"),
    Candidate("Tendance rapide", "Suivi de tendance",
              "Idem, cassure 20 j et momentum 60 j"),
    Candidate("Tendance lente", "Suivi de tendance",
              "Idem, cassure 55 j et momentum 180 j"),
    Candidate("Régime « largeur de marché »", "Suivi de tendance",
              "Régime : ≥ 50 % des actifs au-dessus de leur moyenne 150 j"),
    Candidate("Régime BTC et largeur", "Suivi de tendance",
              "Les deux régimes doivent être haussiers"),
    Candidate("Rotation momentum", "Momentum relatif",
              "Chaque lundi : les 5 meilleurs momentums 90 j ; sortie si rang > 8"),
    Candidate("Retour à la moyenne", "Contrarien (fort taux de réussite)",
              "Achat si RSI(2) < 10 au-dessus de la moyenne 150 j ; "
              "vente au retour sur la moyenne 5 j ou après 10 j"),
)
NAMES = [c.name for c in CANDIDATES]


class Lab:
    """Précalcule les indicateurs une fois, puis exécute n'importe quel
    candidat sur n'importe quelle période."""

    def __init__(self, close: pd.DataFrame, volume: pd.DataFrame,
                 p: Optional[ts.TrendParams] = None):
        self.close = close
        self.volume = volume.reindex(close.index)
        self.p = p or ts.TrendParams()
        self._pre: Dict[Tuple[int, int], Any] = {}
        self._feats: Optional[Dict[str, Dict[str, np.ndarray]]] = None
        self.btc = ts.btc_regime(close["btc"], self.p).values
        self.breadth_ok = (breadth(close) >= 0.5).values

    @property
    def feats(self) -> Dict[str, Dict[str, np.ndarray]]:
        if self._feats is None:
            self._feats = lab_features(self.close, self.volume, self.p)
        return self._feats

    def _tg(self, p: ts.TrendParams, start: str, end: str,
            regime: Optional[np.ndarray] = None):
        key = (p.breakout_n, p.mom_n)
        if key not in self._pre:
            self._pre[key] = ts.precompute(self.close, self.volume, p)
        cols, reg = self._pre[key]
        r = ts.backtest(self.close, self.volume, p, start, end,
                        pre=(cols, reg if regime is None else regime))
        return r.equity, r.trades

    def run(self, name: str, start: str, end: str
            ) -> Tuple[pd.Series, List[Dict[str, Any]]]:
        p = self.p
        if name == REF:
            return self._tg(p, start, end)
        if name == "Tendance rapide":
            return self._tg(dataclasses.replace(p, breakout_n=20, mom_n=60), start, end)
        if name == "Tendance lente":
            return self._tg(dataclasses.replace(p, breakout_n=55, mom_n=180), start, end)
        if name == "Régime « largeur de marché »":
            return self._tg(p, start, end, self.breadth_ok)
        if name == "Régime BTC et largeur":
            return self._tg(p, start, end, self.btc & self.breadth_ok)
        if name == "Rotation momentum":
            return self._rotation(start, end)
        if name == "Retour à la moyenne":
            return self._mean_reversion(start, end)
        raise KeyError(name)

    def _rotation(self, start: str, end: str, top: int = 5, keep: int = 8):
        p = self.p
        cache: Dict[int, Dict[str, int]] = {}

        def ranks(ctx):
            i = ctx["i"]
            if i not in cache:
                el = sorted(((s["mom"], a) for a, s in ctx["snap"].items()
                             if eligible(s, p) and s["mom"] > 0), reverse=True)
                cache[i] = {a: k for k, (_m, a) in enumerate(el)}
            return cache[i]

        trail = _trail_exit(p)

        def exit_fn(h, s, ctx):
            e = trail(h, s, ctx)
            if e:
                return e
            if ctx["date"].weekday() == 0 and ranks(ctx).get(s["_a"], 10 ** 6) >= keep:
                return "RANG"
            return None

        return simulate(self.close, self.feats, self.btc, p, start, end,
                        entry_fn=lambda s, ctx: ranks(ctx).get(s["_a"], 10 ** 6) < top,
                        exit_fn=exit_fn, rank_fn=lambda s: s["mom"],
                        entry_day=lambda d: d.weekday() == 0)

    def _mean_reversion(self, start: str, end: str, rsi_max: float = 10.0,
                        max_days: int = 10):
        p = self.p

        def entry_fn(s, ctx):
            return (eligible(s, p) and ts._finite(s["sma150"], s["rsi2"])
                    and s["close"] > s["sma150"] and s["rsi2"] < rsi_max)

        def exit_fn(h, s, ctx):
            if s["close"] <= h["stop"]:
                return "STOP"
            if ts._finite(s["sma5"]) and s["close"] > s["sma5"] and ctx["i"] > h["i0"]:
                return "OBJECTIF"
            if ctx["i"] - h["i0"] >= max_days:
                return "DURÉE"
            return None

        return simulate(self.close, self.feats, self.btc, p, start, end,
                        entry_fn=entry_fn, exit_fn=exit_fn,
                        rank_fn=lambda s: -s["rsi2"])


# ══════════════════════════════════════════════════════════════════════
# MESURES
# ══════════════════════════════════════════════════════════════════════

def curve_stats(ret: pd.Series) -> Dict[str, float]:
    """Statistiques d'une série de rendements journaliers."""
    ret = ret.dropna()
    eq = (1 + ret).cumprod()
    years = max((ret.index[-1] - ret.index[0]).days / 365.25, 1e-9)
    cagr = float(eq.iloc[-1] ** (1 / years) - 1) if eq.iloc[-1] > 0 else -1.0
    dd = float((eq / eq.cummax() - 1).min())
    sd = float(ret.std())
    return {"cagr_pct": cagr * 100, "max_dd_pct": dd * 100,
            "sharpe": float(ret.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0,
            "calmar": cagr / abs(dd) if dd < 0 else 0.0}


def trailing_sharpe(ret: pd.Series) -> float:
    sd = float(ret.std())
    return float(ret.mean() / sd * math.sqrt(365)) if sd > 0 and len(ret) > 30 else -9.0


def meta_weights(returns: pd.DataFrame, pool: List[str], start: str,
                 lookback_days: int = 730, mode: str = "best",
                 months: int = 6) -> Tuple[pd.DataFrame, List[Tuple[str, str]]]:
    """Allocation du « chef d'orchestre » : à chaque échéance (tous les
    `months` mois), poids fixés d'après les SEULS rendements antérieurs.
    mode « best » : tout sur le meilleur Sharpe des `lookback_days`
    derniers jours ; « soft » : poids proportionnels aux Sharpe positifs."""
    idx = returns.index
    dates = pd.date_range(pd.Timestamp(start, tz="UTC"), idx[-1],
                          freq=f"{months}MS")
    w = pd.DataFrame(0.0, index=idx, columns=pool)
    picks: List[Tuple[str, str]] = []
    for k, d in enumerate(dates):
        hist = returns.loc[(idx >= d - pd.Timedelta(days=lookback_days))
                           & (idx < d), pool]
        score = {n: trailing_sharpe(hist[n]) for n in pool}
        if mode == "best":
            best = max(pool, key=lambda n: score[n])
            weights = {n: float(n == best) for n in pool}
            picks.append((str(d.date()), best))
        else:
            pos = {n: max(v, 0.0) for n, v in score.items()}
            tot = sum(pos.values())
            weights = {n: pos[n] / tot if tot > 0 else 1 / len(pool) for n in pool}
        end = dates[k + 1] if k + 1 < len(dates) else idx[-1] + pd.Timedelta(days=1)
        mask = (idx >= d) & (idx < end)
        for n in pool:
            w.loc[mask, n] = weights[n]
    return w, picks


def blend(returns: pd.DataFrame, pool: List[str],
          weights: Optional[pd.DataFrame] = None) -> pd.Series:
    """Rendement d'un portefeuille de « manches » (une stratégie par manche)."""
    if weights is None:
        weights = pd.DataFrame(1 / len(pool), index=returns.index, columns=pool)
    return (returns[pool] * weights[pool]).sum(axis=1)


def horizon_success(equity: pd.Series, months: Tuple[int, ...] = (1, 3, 6, 12, 24, 36),
                    n_boot: int = 10_000, block: int = 30, seed: int = 1
                    ) -> pd.DataFrame:
    """Pour chaque horizon : part des fenêtres historiques en gain / en perte
    (glissantes, jour par jour) et estimation par bootstrap par blocs de
    30 jours (conserve les séries de hausse et de baisse)."""
    equity = equity.dropna()
    r = equity.pct_change().dropna().values
    rng = np.random.default_rng(seed)
    rows = []
    for m in months:
        d = int(round(m * 30.44))
        roll = (equity / equity.shift(d) - 1).dropna()
        row = {"months": m, "windows": len(roll),
               "hist_gain_pct": float((roll > 0).mean() * 100) if len(roll) else np.nan,
               "hist_loss_pct": float((roll < 0).mean() * 100) if len(roll) else np.nan,
               "hist_p5_pct": float(roll.quantile(0.05) * 100) if len(roll) else np.nan,
               "hist_median_pct": float(roll.median() * 100) if len(roll) else np.nan}
        if len(r) > block * 2:
            nb = math.ceil(d / block)
            starts = rng.integers(0, len(r) - block, size=(n_boot, nb))
            paths = r[starts[..., None] + np.arange(block)].reshape(n_boot, -1)[:, :d]
            tot = np.prod(1 + paths, axis=1) - 1
            row.update({"boot_gain_pct": float((tot > 0).mean() * 100),
                        "boot_loss_pct": float((tot < 0).mean() * 100),
                        "boot_p5_pct": float(np.percentile(tot, 5) * 100)})
        rows.append(row)
    return pd.DataFrame(rows)


def tournament_recent(lab: Lab, days: int = 730) -> pd.DataFrame:
    """Classement des candidats sur les `days` derniers jours (utilisé par
    l'auto-diagnostic). Positions à zéro en début de fenêtre."""
    end = lab.close.index[-1]
    start = str((end - pd.Timedelta(days=days)).date())
    rows = []
    for n in NAMES:
        eq, trades = lab.run(n, start, str(end.date()))
        st = curve_stats(eq.pct_change().fillna(0.0))
        rs = [t["r"] for t in trades]
        rows.append({"name": n, **st, "trades": len(trades),
                     "win_rate_pct": float(np.mean([x > 0 for x in rs]) * 100) if rs else 0.0,
                     "total_pct": float((eq.iloc[-1] / eq.iloc[0] - 1) * 100)})
    return pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════
# RAPPORT
# ══════════════════════════════════════════════════════════════════════

def _n(x: float, spec: str = ".2f") -> str:
    """Nombre à la française (virgule décimale), sans « -0,00 »."""
    out = format(x, spec)
    if not out.lstrip("+-").replace("0", "").replace(".", ""):
        out = format(0.0, spec)
    return out.replace(".", ",")


def _pct(x: float, sign: bool = True) -> str:
    return _n(x, "+.1f" if sign else ".1f") + " %"


def _st(st: Dict[str, float]) -> str:
    return (f" {_pct(st['cagr_pct'])} | {_pct(st['max_dd_pct'], False)} | "
            f"{_n(st['sharpe'])} | {_n(st['calmar'])} |")


def lab_report(close: pd.DataFrame, volume: pd.DataFrame, source: str,
               is_period: Tuple[str, str] = IS_PERIOD, oos_start: str = OOS_START,
               meta_start: str = "2020-01-01", horizon_start: str = "2019-01-01"
               ) -> str:
    """Rapport du laboratoire. Les périodes par défaut sont celles du
    protocole (choix 2018-2022, vérification 2023 → aujourd'hui)."""
    lab = Lab(close, volume)
    last = str(close.index[-1].date())
    oos = (oos_start, last)
    L: List[str] = []
    L.append(f"## Données : {source} ({len(close.columns)} actifs, jusqu'au {last})\n")
    # --- 1. Tournoi -----------------------------------------------------
    L.append("### 1. Tournoi (1 % de risque par trade pour toutes)\n")
    L.append(f"| Stratégie | Famille | Choix {is_period[0][:4]}-{is_period[1][2:4]} : "
             "CAGR | Baisse max | Sharpe | "
             f"Calmar | Gagnants | Espérance | Vérif. {oos_start[:4]} → : CAGR | "
             "Baisse max | "
             "Sharpe | Calmar | Gagnants | Espérance |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    curves: Dict[str, pd.Series] = {}
    table = {}
    for c in CANDIDATES:
        cells = []
        for per in (is_period, oos):
            eq, trades = lab.run(c.name, *per)
            m = ts.compute_metrics(eq, trades)
            table[(c.name, per[0])] = m
            cells.append(_st(m) + f" {m['win_rate_pct']:.0f} % | "
                         f"{_n(m['expectancy_r'], '+.2f')} R |")
        L.append(f"| {c.name} | {c.family} |" + "".join(cells))
        curves[c.name] = lab.run(c.name, is_period[0], last)[0]
    L.append("")
    best_is = max(NAMES, key=lambda n: table[(n, is_period[0])]["calmar"])
    ref_is = table[(REF, is_period[0])]
    ref_oos = table[(REF, oos[0])]
    beats_both = [n for n in NAMES if n != REF
                  and table[(n, is_period[0])]["calmar"] > ref_is["calmar"]
                  and table[(n, oos[0])]["calmar"] > ref_oos["calmar"]]
    L.append(f"- Choisie sur {is_period[0][:4]}-{is_period[1][:4]} (meilleur Calmar) : "
             f"**{best_is}**.")
    L.append("- Battent la référence sur les deux périodes : "
             + (", ".join(f"**{n}**" for n in beats_both) if beats_both else "**aucune**")
             + ".")
    mr_is, mr_oos = (table[("Retour à la moyenne", is_period[0])],
                     table[("Retour à la moyenne", oos[0])])
    L.append(f"- Le retour à la moyenne gagne **{mr_is['win_rate_pct']:.0f} % puis "
             f"{mr_oos['win_rate_pct']:.0f} %** de ses trades, contre "
             f"{ref_is['win_rate_pct']:.0f} % et {ref_oos['win_rate_pct']:.0f} % pour "
             f"TrendGuard, mais son espérance est de {_n(mr_is['expectancy_r'], '+.2f')} R "
             f"puis {_n(mr_oos['expectancy_r'], '+.2f')} R par trade : un taux de réussite élevé "
             "ne fait pas une stratégie rentable.\n")
    # --- 2. Méta-apprentissage -------------------------------------------
    rets = pd.DataFrame(curves).pct_change().fillna(0.0)
    trend3 = [REF, "Tendance rapide", "Tendance lente"]
    w_best, picks = meta_weights(rets, NAMES, meta_start)
    w_soft, _ = meta_weights(rets, NAMES, meta_start, mode="soft")
    w_h, _ = meta_weights(rets, trend3, meta_start)
    variants = {
        "TrendGuard seule (référence)": rets[REF],
        "Chef d'orchestre : meilleure des 7 sur 24 mois": blend(rets, NAMES, w_best),
        "Chef d'orchestre : 7 pondérées par Sharpe 24 mois": blend(rets, NAMES, w_soft),
        "Chef d'orchestre : meilleur horizon de tendance": blend(rets, trend3, w_h),
        "Répartition fixe : 3 horizons de tendance": blend(rets, trend3),
        "Répartition fixe : les 7 stratégies": blend(rets, NAMES),
    }
    L.append("### 2. Méta-apprentissage : suivre la stratégie qui marche le mieux ?\n")
    L.append(f"Tous les 6 mois depuis {meta_start[:4]}, le chef d'orchestre regarde "
             "les 24 mois "
             "précédents, et seulement eux.\n")
    L.append(f"| Portefeuille | {meta_start[:4]}-{is_period[1][2:4]} : CAGR | "
             "Baisse max | Sharpe | Calmar | "
             f"{oos_start[:4]} → : CAGR | Baisse max | Sharpe | Calmar |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    meta_tab = {}
    for label, r in variants.items():
        a = curve_stats(r.loc[meta_start:is_period[1]])
        b = curve_stats(r.loc[oos[0]:])
        meta_tab[label] = (a, b)
        L.append(f"| {label} |" + _st(a) + _st(b))
    L.append("")
    L.append("Stratégie confiée par le chef d'orchestre (7 candidates) : "
             + ", ".join(f"{d[:7]} → {n}" for d, n in picks) + "\n")
    ref_a, ref_b = meta_tab["TrendGuard seule (référence)"]
    wins = [lbl for lbl, (a, b) in meta_tab.items()
            if not lbl.startswith("TrendGuard") and a["calmar"] > ref_a["calmar"]
            and b["calmar"] > ref_b["calmar"]]
    L.append("- Portefeuilles qui battent TrendGuard seule sur les deux périodes "
             "(rendement divisé par la pire baisse) : "
             + (", ".join(f"**{w}**" for w in wins) if wins else "**aucun**") + ".")
    richer = [lbl for lbl, (a, b) in meta_tab.items()
              if not lbl.startswith("TrendGuard") and lbl not in wins
              and a["cagr_pct"] > ref_a["cagr_pct"] and b["cagr_pct"] > ref_b["cagr_pct"]]
    for w in richer:
        a, b = meta_tab[w]
        L.append(f"- **{w}** rapporte plus sur les deux périodes, mais avec des baisses "
                 f"maximales de {_pct(a['max_dd_pct'], False)} et "
                 f"{_pct(b['max_dd_pct'], False)} (référence : "
                 f"{_pct(ref_a['max_dd_pct'], False)} et {_pct(ref_b['max_dd_pct'], False)}) "
                 "et un rapport rendement / pire baisse moins bon sur au moins une "
                 "période : plus de risque, pas un meilleur apprentissage.")
    L.append("")
    # --- 3. Probabilité de succès par horizon -----------------------------
    hs = horizon_success(curves[REF].loc[horizon_start:])
    L.append(f"### 3. Probabilité de finir en gain selon la durée (TrendGuard, "
             f"{horizon_start[:4]} →)\n")
    L.append("| Durée | Fenêtres historiques en gain | en perte | 5 % pires | Médiane | "
             "Bootstrap : en gain | en perte | 5 % pires |")
    L.append("|---|---|---|---|---|---|---|---|")
    for _, r in hs.iterrows():
        L.append(f"| {int(r.months)} mois | {r.hist_gain_pct:.0f} % | {r.hist_loss_pct:.0f} % | "
                 f"{_pct(r.hist_p5_pct)} | {_pct(r.hist_median_pct)} | "
                 f"{r.boot_gain_pct:.0f} % | {r.boot_loss_pct:.0f} % | {_pct(r.boot_p5_pct)} |")
    years = (curves[REF].index[-1] - pd.Timestamp(horizon_start, tz="UTC")).days / 365.25
    L.append("\nLes fenêtres ni en gain ni en perte sont celles passées entièrement en "
             "USDT (régime baissier). Les fenêtres se chevauchent : sur "
             f"{_n(years, '.1f')} ans d'historique, il n'y a que "
             f"{_n(years / 2, '.1f')} périodes indépendantes de 24 mois et "
             f"{_n(years / 3, '.1f')} de 36 mois. Les pourcentages sur longue durée "
             "sont donc des indications, pas des probabilités précises ; le "
             "bootstrap (10 000 trajectoires recomposées par blocs de 30 jours) "
             "en donne une estimation plus prudente.\n")
    return "\n".join(L)


INTRO = """# Laboratoire de stratégies — le bot peut-il « apprendre la meilleure stratégie » ?

Étude reproductible : `python strategy_lab.py --cache data_binance`
(`--cm data` ajoute les données Coin Metrics, qui incluent des actifs
effondrés). Frais 0,1 % et slippage 0,1 % par côté, 1 % du capital risqué
par trade, mêmes plafonds de portefeuille pour toutes les stratégies.

Protocole : les sept stratégies sont écrites **avant** de regarder leurs
résultats. La sélection se fait sur 2018-2022, puis est vérifiée sur
2023 → aujourd'hui, période qui n'a servi à aucun choix.
"""

OUTRO = """## Ce que le bot en retient

- **Il réévalue les alternatives en continu, mais n'en change pas seul.**
  `python trendguard_bot.py diagnose` (et le diagnostic automatique hebdomadaire)
  classe les sept stratégies sur les 24 derniers mois et affiche la probabilité
  historique de gain par durée. Confier le capital à la meilleure des sept
  stratégies récentes a fait moins bien que TrendGuard seule sur les deux
  périodes (section 2) : ce classement est une information, pas un ordre.
- **« 99 % de réussite et 1 % de perte maximale » n'existe pas trade par
  trade.** La section 1 le montre : la stratégie au meilleur taux de réussite
  (retour à la moyenne) ne gagne presque rien. La limite de 1 % s'applique à
  chaque trade (TrendGuard : −1,0 R en moyenne), pas au portefeuille, qui
  traverse des baisses de 25 à 35 %.
- **Le « succès » se mesure sur la durée.** Sur un mois, la stratégie est plus
  souvent à plat (en USDT) ou en perte qu'en gain. Sur deux à trois ans, la
  quasi-totalité des fenêtres historiques finissent en gain (section 3), sans
  garantie pour l'avenir.
"""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Laboratoire de stratégies TrendGuard")
    ap.add_argument("--cache", default="data_binance",
                    help="dossier des données Binance (téléchargées si absentes)")
    ap.add_argument("--cm", default=None,
                    help="dossier Coin Metrics (optionnel, actifs effondrés inclus)")
    ap.add_argument("--out", default="docs/STRATEGIES.md")
    args = ap.parse_args(argv)
    import research_adaptation as ra
    parts = [INTRO]
    close, volume = ra.load_binance(args.cache)
    parts.append(lab_report(close, volume, "Binance, paires tradées par le bot"))
    if args.cm:
        c2, v2 = ts.load_coinmetrics(args.cm, ts.DEFAULT_UNIVERSE)
        c2 = c2.loc["2017-01-01":]
        parts.append(lab_report(c2, v2.reindex(c2.index),
                                "Coin Metrics, actifs effondrés inclus (FTT, EOS…)"))
    parts.append(OUTRO)
    text = "\n".join(parts)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    print(f"\nRapport écrit : {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
