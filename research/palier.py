#!/usr/bin/env python3
"""
Palier de risque et arrêt d'urgence : étude reproductible (docs/ADAPTATION.md,
section 6).

Le bot peut-il choisir lui-même son risque par achat entre 1 % et 2 %, et
sortir seul d'un arrêt d'urgence ? Mêmes données Binance et même boucle de
backtest que le bot, avec son profil prudent (risque ÷ 2 au-delà de 10 % de
baisse) et son arrêt d'urgence à −40 % :

  1. paliers fixes de 1 % à 2 % (risque cumulé multiplié d'autant) sur les
     deux époques, et pire baisse du hasard (1 fois sur 20 en 3 ans) ;
  2. règles « selon le marché » : les meilleures de 2018-2022, vérifiées
     depuis 2023 (le piège du sur-ajustement) ;
  3. le palier décidé par l'analyse du bot, rejoué pas à pas : chaque fin de
     mois, avec le seul historique connu ce jour-là, sans puis avec les
     garde-fous du bot (evolution.py) ;
  4. arrêt d'urgence : bloqué jusqu'à la commande resume, ou reprise
     automatique après 60 jours de marché redevenu haussier (bot.py) ;
  5. budget de risque : risque de départ de chaque position (le bot), ou
     risque restant jusqu'au stop actuel (plus d'achats quand les stops
     montent).

  python -m research.palier --cache data_binance
"""

from __future__ import annotations

import dataclasses
import sys
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.commun import load_binance, study_args
from trendguard import evolution
from trendguard import trend_strategy as ts
from trendguard.texte import fr

KILL = 0.40
PROFILE = ((0.10, 0.5),)          # profil prudent du bot (TG_DD_THROTTLE=0.10:0.5)
Era = Tuple[str, str]


@dataclasses.dataclass(frozen=True)
class WhatIf(ts.TrendParams):
    """Réglages hors des limites du bot (essai 5 % / 20 % / 20), pour l'étude
    de l'arrêt d'urgence seulement : le bot les refuse."""

    def validate(self) -> "WhatIf":
        return self


def _row(m: Dict[str, float]) -> str:
    return (f"{fr(m['cagr_pct'], '+.1f')} % | {fr(m['max_dd_pct'], '.1f')} % | "
            f"{fr(m['calmar'], '.2f')}")


def _streak(flags: np.ndarray) -> np.ndarray:
    out = np.zeros(len(flags), dtype=int)
    for i, f in enumerate(flags):
        out[i] = (out[i - 1] + 1 if i else 1) if f else 0
    return out


def fixed_steps(close: pd.DataFrame, volume: pd.DataFrame, pre, base: ts.TrendParams,
                eras: List[Era]) -> None:
    """1. Paliers fixes : rendement, pire baisse et Calmar par époque, pire
    baisse du hasard sur tout l'historique."""
    print("## 1. Paliers fixes (risque cumulé multiplié d'autant)\n")
    print("| Risque par achat | 2018-22 rendement | baisse | Calmar | depuis 2023 rendement | "
          "baisse | Calmar | hasard 1 fois sur 20 |")
    print("|---|---|---|---|---|---|---|---|")
    for step in evolution.RISK_STEPS:
        p = evolution.at_step(base, step)
        cells = [_row(ts.backtest(close, volume, p, a, b, pre=pre).metrics) for a, b in eras]
        full = ts.backtest(close, volume, p, eras[0][0], eras[-1][1], pre=pre).equity
        print(f"| {fr(step, 'g')} % | {' | '.join(cells)} | −{fr(evolution.block_luck(full)[1], '.0f')} % |")


def market_rule(bull: np.ndarray, bull_days: int, step_days: int,
                down_dd: float) -> Callable[[int, float, float], float]:
    """Règle « selon le marché » : +0,25 × (jours d'affilée de BTC haussier
    au-delà de `bull_days`, par tranche de `step_days`), 2 au plus ; 1 dès
    `down_dd` de baisse."""
    run = _streak(bull)

    def scale(i: int, equity: float, peak: float) -> float:
        if peak > 0 and 1 - equity / peak >= down_dd:
            return 1.0
        return min(2.0, 1.0 + 0.25 * (max(0, run[i] - bull_days) // step_days))
    return scale


def market_rules(close: pd.DataFrame, volume: pd.DataFrame, pre, base: ts.TrendParams,
                 eras: List[Era]) -> None:
    """2. Les règles les mieux classées sur 2018-2022 (grille de 405 règles,
    30 septembre 2026), vérifiées depuis 2023."""
    print("\n## 2. Règles selon le marché : réglées sur 2018-2022, vérifiées depuis 2023\n")
    print("| Règle | 2018-22 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar |")
    print("|---|---|---|---|---|---|---|")
    bull = pre[1]
    rules = [("aucune (bot actuel, 1 %)", None),
             ("BTC haussier ≥ 30 j, +0,25 tous les 15 j, 1 % dès 10 % de baisse",
              market_rule(bull, 30, 15, 0.10)),
             ("BTC haussier ≥ 60 j, +0,25 tous les 30 j, 1 % dès 10 % de baisse",
              market_rule(bull, 60, 30, 0.10)),
             ("BTC haussier, +0,25 tous les 7 j, sans condition de baisse",
              market_rule(bull, 0, 7, 1.0))]
    for name, scale in rules:
        hooks = ts.BacktestHooks(risk_scale=scale)
        cells = [_row(ts.backtest(close, volume, base, a, b, pre=pre, hooks=hooks).metrics)
                 for a, b in eras]
        print(f"| {name} | {' | '.join(cells)} |")


class Walk:
    """Palier décidé par l'analyse du bot, rejoué pas à pas : à chaque fin
    de mois, épreuves avec le seul historique connu (deux moitiés de
    l'historique, Calmar et rendement, pire baisse et hasard) ; chaque jour,
    retour immédiat à 1 à `down_dd` de baisse ; 30 jours d'essai."""

    def __init__(self, close: pd.DataFrame, volume: pd.DataFrame, pre, base: ts.TrendParams,
                 up_dd: float, down_dd: float, dd_limit: float, luck_limit: float):
        self.close, self.volume, self.pre, self.base = close, volume, pre, base
        self.up_dd, self.down_dd, self.dd_limit, self.luck_limit = up_dd, down_dd, dd_limit, luck_limit
        self.step, self.rest, self.probation = 1.0, None, None
        self.changes: List[Tuple[str, float, float, str]] = []
        self.steps: List[float] = []
        self._memo: Dict[Tuple[float, str, str], ts.PortfolioResult] = {}

    def _bt(self, step: float, a: str, b: str) -> ts.PortfolioResult:
        key = (step, a, b)
        if key not in self._memo:
            self._memo[key] = ts.backtest(self.close, self.volume, evolution.at_step(self.base, step),
                                          a, b, pre=self.pre)
        return self._memo[key]

    def _better(self, hi: float, lo: float, day: str) -> bool:
        t0, t1 = pd.Timestamp("2018-01-01", tz="UTC"), pd.Timestamp(day, tz="UTC")
        mid = str((t0 + (t1 - t0) / 2).date())
        for a, b in (("2018-01-01", mid), (mid, day)):
            mh, ml = self._bt(hi, a, b).metrics, self._bt(lo, a, b).metrics
            if not (mh["calmar"] >= ml["calmar"] and mh["cagr_pct"] > ml["cagr_pct"]):
                return False
        eq = self._bt(hi, "2018-01-01", day).equity
        worst = float((1 - eq / eq.cummax()).max() * 100)
        return worst <= self.dd_limit and evolution.block_luck(eq)[1] <= self.luck_limit

    def _move(self, day: str, new: float, why: str) -> None:
        self.changes.append((day, self.step, new, why))
        self.step = new

    def scale(self, i: int, equity: float, peak: float) -> float:
        """Crochet risk_scale de la boucle de backtest : palier du jour."""
        idx = self.close.index
        d = idx[i]
        dd = 1 - equity / peak if peak > 0 else 0.0
        if self.step > 1 and dd >= self.down_dd:
            self._move(str(d.date()), 1.0, "alerte")
            self.probation, self.rest = None, d + pd.Timedelta(days=evolution.RISK_REST_DOWN)
        if i + 1 < len(idx) and idx[i + 1].month != d.month:
            self._month_end(d, dd, bool(self.pre[1][i]))
        self.steps.append(self.step)
        return self.step

    def _month_end(self, d: pd.Timestamp, dd: float, bull: bool) -> None:
        day, steps = str(d.date()), list(evolution.RISK_STEPS)
        k = steps.index(self.step)
        if self.probation is not None:
            since, old = self.probation
            if (d - since).days < evolution.PROBATION_DAYS:
                return
            self.probation = None
            eq_new, eq_old = self._bt(self.step, "2018-01-01", day).equity, self._bt(old, "2018-01-01", day).equity
            s = str(since.date())
            if evolution._ret(eq_new, s, day) < evolution._ret(eq_old, s, day) - evolution.PROBATION_TOL:
                self._move(day, old, "essai raté")
                self.rest = d + pd.Timedelta(days=evolution.RISK_REST_DOWN)
            return
        if k > 0 and not self._better(self.step, steps[k - 1], day):
            self._move(day, steps[k - 1], "analyse")
            return
        if self.rest is not None and d < self.rest:
            return
        if k + 1 < len(steps) and bull and dd <= self.up_dd and self._better(steps[k + 1], self.step, day):
            self.probation = (d, self.step)
            self._move(day, steps[k + 1], "analyse favorable")


def walk_forward(close: pd.DataFrame, volume: pd.DataFrame, pre, base: ts.TrendParams,
                 start: str, end: str) -> None:
    """3. Palier décidé par l'analyse du bot, rejoué pas à pas, sans puis
    avec ses garde-fous, contre le bot actuel (1 % fixe)."""
    print(f"\n## 3. Palier décidé par l'analyse du bot, rejoué pas à pas ({start[:4]} → {end[:4]})\n")
    print("| Variante | 2020-22 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar | "
          "au-dessus de 1 % | changements |")
    print("|---|---|---|---|---|---|---|---|---|")
    ref = ts.backtest(close, volume, base, start, end, pre=pre).equity
    room = min(KILL, 0.40)
    variants: List[Tuple[str, Optional[Walk]]] = [
        ("bot actuel, 1 % fixe", None),
        ("analyse seule, sans garde-fou", Walk(close, volume, pre, base, 1.0, 1.0, 1e9, 1e9)),
        ("analyse + retour à 1 % dès 10 % de baisse",
         Walk(close, volume, pre, base, 1.0, evolution.RISK_DOWN_DD, 1e9, 1e9)),
        ("règles du bot (evolution.py)",
         Walk(close, volume, pre, base, evolution.RISK_UP_DD, evolution.RISK_DOWN_DD,
              (room - evolution.RISK_DD_ROOM) * 100, (room - evolution.RISK_LUCK_ROOM) * 100))]
    for name, w in variants:
        eq = ref if w is None else ts.backtest(close, volume, base, start, end, pre=pre,
                                               hooks=ts.BacktestHooks(risk_scale=w.scale)).equity
        cells = [_row(ts.compute_metrics(eq.loc[a:b], [])) for a, b in
                 ((start, "2022-12-31"), ("2023-01-01", end))]
        above = f"{fr(np.mean(np.array(w.steps) > 1) * 100, '.0f')} % du temps" if w else "–"
        print(f"| {name} | {' | '.join(cells)} | {above} | {len(w.changes) if w else 0} |")


class KillSwitch:
    """Arrêt d'urgence du bot rejoué sur la boucle de backtest : plus aucun
    achat sous −40 % depuis le plus haut ; reprise automatique (si
    `resume_days`) après ce délai en marché haussier, une fois par an, plus
    haut remis au capital et risque ÷ 2 pendant 90 jours."""

    def __init__(self, bull: np.ndarray, index: pd.DatetimeIndex, p: ts.TrendParams,
                 resume_days: int):
        self.bull, self.index, self.p, self.resume_days = bull, index, p, resume_days
        self.peak, self.halted_at, self.resumed = 0.0, None, None
        self.halts: List[str] = []
        self.resumes: List[str] = []
        self.halted_days = 0

    def scale(self, i: int, equity: float, peak: float) -> float:
        """Crochet risk_scale : 0 pendant l'arrêt ; sinon le profil prudent
        mesuré depuis le plus haut du bot (remis à zéro à la reprise)."""
        d = self.index[i]
        self.peak = max(self.peak, equity)
        if self.halted_at is None and equity < self.peak * (1 - KILL):
            self.halted_at = d
            self.halts.append(str(d.date()))
        if self.halted_at is not None:
            recent = self.resumed is not None and (d - self.resumed).days < 365
            if (self.resume_days and (d - self.halted_at).days >= self.resume_days
                    and self.bull[i] and not recent):
                self.halted_at, self.peak, self.resumed = None, equity, d
                self.resumes.append(str(d.date()))
            else:
                self.halted_days += 1
                return 0.0
        gentle = 0.5 if self.resumed is not None and (d - self.resumed).days < 90 else 1.0
        return (ts.risk_multiplier(equity, self.peak, self.p)
                / ts.risk_multiplier(equity, peak, self.p) * gentle)


def kill_switch(close: pd.DataFrame, volume: pd.DataFrame, base: ts.TrendParams, end: str) -> None:
    """4. Arrêt d'urgence bloqué ou levé seul, aux réglages du bot et à
    ceux de l'essai 5 % / 20 % / 20 positions (le seul où il se déclenche)."""
    print("\n## 4. Arrêt d'urgence : bloqué jusqu'à « resume », ou reprise prudente après 60 jours\n")
    print("| Réglages | Période | Arrêt d'urgence | Capital final | Pire baisse | Arrêts | Reprises | "
          "Jours sans achat |")
    print("|---|---|---|---|---|---|---|---|")
    fields = {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}
    sets = [("bot : 1 % / 6 % / 8", base),
            ("essai : 5 % / 20 % / 20", WhatIf(**dict(fields, risk_pct=0.05, max_total_risk=0.20,
                                                       max_positions=20)))]
    for label, p in sets:
        pre = ts.precompute(close, volume, p)
        for era, a in (("2018 →", "2018-01-01"), ("2023 →", "2023-01-01")):
            for mode, days in (("bloqué", 0), ("reprise après 60 j", 60)):
                k = KillSwitch(pre[1], close.index, p, days)
                eq = ts.backtest(close, volume, p, a, end, pre=pre,
                                 hooks=ts.BacktestHooks(risk_scale=k.scale)).equity
                dd = float((eq / eq.cummax() - 1).min() * 100)
                print(f"| {label} | {era} | {mode} | {fr(eq.iloc[-1], ',.0f')} | {fr(dd, '.1f')} % | "
                      f"{', '.join(k.halts) or '–'} | {', '.join(k.resumes) or '–'} | {k.halted_days} |")


def open_risk_at_stop(close: pd.DataFrame, volume: pd.DataFrame, pre, base: ts.TrendParams,
                      eras: List[Era]) -> None:
    """5. Budget de risque compté avec le risque de départ de chaque position
    (le bot), ou avec le risque restant jusqu'à son stop actuel : le budget
    se libère quand les stops montent, le bot achète plus."""
    print("\n## 5. Budget de risque : risque de départ, ou risque restant jusqu'au stop\n")
    print("| Budget compté avec | 2018-22 rendement | baisse | Calmar | depuis 2023 rendement | "
          "baisse | Calmar | trades |")
    print("|---|---|---|---|---|---|---|---|")
    real = ts.plan_entries

    def at_stop(holdings, snap, bull, equity, cash, p, risk_mult=1.0):
        left = {a: dataclasses.replace(h, risk_quote=min(h.risk_quote, max(0.0, h.qty * (h.entry - h.stop))))
                for a, h in holdings.items()}
        return real(left, snap, bull, equity, cash, p, risk_mult)
    for name, fn in (("le risque de départ (bot)", real), ("le risque restant jusqu'au stop", at_stop)):
        ts.plan_entries = fn            # la boucle du bot appelle plan_entries du module
        try:
            res = [ts.backtest(close, volume, base, a, b, pre=pre) for a, b in eras]
        finally:
            ts.plan_entries = real
        print(f"| {name} | {' | '.join(_row(r.metrics) for r in res)} | "
              f"{' + '.join(str(r.metrics['trades']) for r in res)} |")


def main(argv: Optional[List[str]] = None) -> int:
    """Les cinq parties de l'étude, en tableaux Markdown."""
    args = study_args("Palier de risque et arrêt d'urgence de TrendGuard", None, argv)
    close, volume = load_binance(args.cache)
    end = str(close.index[-1].date())
    base = dataclasses.replace(ts.TrendParams(), dd_throttle=PROFILE)
    pre = ts.precompute(close, volume, base)
    eras = [("2018-01-01", "2022-12-31"), ("2023-01-01", end)]
    print(f"Données Binance jusqu'au {end} ({len(close.columns)} paires), profil prudent du bot, "
          f"arrêt d'urgence à −{fr(KILL * 100, '.0f')} %.\n")
    fixed_steps(close, volume, pre, base, eras)
    market_rules(close, volume, pre, base, eras)
    walk_forward(close, volume, pre, base, "2020-01-01", end)
    kill_switch(close, volume, base, end)
    open_risk_at_stop(close, volume, pre, base, eras)
    return 0


if __name__ == "__main__":
    sys.exit(main())
