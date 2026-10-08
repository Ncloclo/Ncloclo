"""Évolution encadrée : le bot a le droit de modifier lui-même ses réglages,
et gagne en indépendance à mesure qu'il réussit des épreuves de plus en
plus difficiles (docs/EVOLUTION.md).

Chaque jour, après la décision de 00:02 UTC, le bot lance cette routine dans
un processus séparé : la surveillance des stops n'est jamais ralentie.

1. Un réglage en période d'essai (30 jours) est jugé sur ce qui s'est
   vraiment passé depuis son adoption. Confirmé : le bot gagne de
   l'expérience et monte de niveau. Raté : retour aux anciens réglages et un
   niveau de moins.
2. Sinon, le bot cherche un meilleur réglage parmi ceux que son niveau
   autorise et le soumet aux épreuves : deux époques, frais doublés, énigmes
   des crises passées, plateau, hasard. Le plus simple qui les réussit
   toutes est adopté à la décision suivante.

Plus le bot monte de niveau, plus il a de liberté (paramètres changés à la
fois, taille des pas) et plus les épreuves sont exigeantes.

3. Palier de risque : le bot peut aussi porter son risque par achat de 1 %
   à 2 % (le double de TG_RISK_PCT au plus, TG_RISK_MAX_PCT), un cran de
   0,25 % à la fois, seulement si son analyse le justifie (meilleur sur les
   deux époques, pire baisse et hasard loin de l'arrêt d'urgence), le
   capital près de son plus haut et le marché haussier ; 30 jours d'essai.
   Il redescend aussitôt à 1 % à la première alerte (baisse de 10 %, marché
   baissier, arrêt d'urgence) et d'un cran quand l'analyse ne le justifie
   plus (docs/ADAPTATION.md, section 6).

Hors de sa portée à tout niveau : nombre de positions, arrêt d'urgence,
filtres de liquidité, frais, passage en réel, et tout risque par achat
au-delà du palier permis. Il ne règle que la cassure, les stops, la lecture
du marché et son palier de risque.

  python trendguard_bot.py evolution            # statut, règles et historique
  python trendguard_bot.py evolution examen     # épreuves du jour, sans rien changer
  python trendguard_bot.py evolution quotidien  # la routine du jour (lancée par le bot)
  python trendguard_bot.py evolution revenir    # retour aux réglages d'origine (et à 1 %)
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import v29

from . import autonomy, registre
from . import diagnostics as dg
from . import trend_strategy as ts
from .texte import fr

# Ce que le bot peut régler lui-même, et les seules valeurs permises.
SPACE: Dict[str, List[float]] = {
    "breakout_n": [20, 25, 30, 35, 40, 50],
    "init_stop_atr": [2.0, 2.5, 3.0, 3.5, 4.0],
    "trail_atr": [3.0, 4.0, 5.0, 6.0, 7.0],
    "bear_trail_atr": [0.0, 1.5, 2.0, 2.5, 3.0],
    "regime_sma": [100, 125, 150, 175, 200],
}
LABELS = {
    "breakout_n": "cassure (jours)",
    "init_stop_atr": "stop initial (× volatilité)",
    "trail_atr": "stop suiveur (× volatilité)",
    "bear_trail_atr": "stop en marché baissier (× volatilité, 0 = non resserré)",
    "regime_sma": "lecture du marché (moyenne de BTC, jours)",
}
INT_PARAMS = {"breakout_n", "regime_sma"}


@dataclass(frozen=True)
class Level:
    name: str
    n_params: int                   # paramètres changés à la fois
    steps: int                      # pas maximal par paramètre
    margin: float                   # amélioration exigée du Calmar, sur les deux époques
    crisis_tol: float               # perte supplémentaire tolérée dans une crise (points)
    luck_tol: float                 # pire baisse (1 fois sur 20) supplémentaire tolérée (points)
    promote_after: Optional[int]    # réglages confirmés pour monter de niveau


LEVELS = [
    Level("Apprenti", 1, 1, 0.05, 1.0, 2.0, 1),
    Level("Compagnon", 1, 2, 0.07, 0.5, 1.5, 2),
    Level("Expert", 2, 1, 0.10, 0.25, 1.0, 3),
    Level("Maître", 2, 2, 0.12, 0.0, 0.5, None),
]

PROBATION_DAYS = 30
PROBATION_TOL = 2.0             # retard toléré sur l'essai (points de rendement)
REST_AFTER_CONFIRM = 7          # jours de repos après un essai réussi…
REST_AFTER_REVERT = 60          # … après un essai raté
REST_AFTER_RESET = 30           # … après un retour demandé aux réglages d'origine
STORM_DD = 0.15                 # baisse depuis le plus haut : tempête
MIN_CAGR_KEEP = 0.8             # un nouveau réglage garde au moins 80 % du rendement
PLATEAU_KEEP = 0.9              # ses voisins gardent au moins 90 % du Calmar actuel
HISTORY_MAX = 100

# Énigmes : crises et hausses passées. Crise : ne pas perdre plus que les
# réglages actuels ; hausse : en garder au moins 90 %.
CRISES = [
    ("Marché baissier de 2018", "2018-01-01", "2018-12-31", "crise"),
    ("Krach du Covid (mars 2020)", "2020-02-15", "2020-04-15", "crise"),
    ("Chute de mai 2021", "2021-05-01", "2021-07-31", "crise"),
    ("Effondrement de LUNA (mai 2022)", "2022-05-01", "2022-06-30", "crise"),
    ("Faillite de FTX (novembre 2022)", "2022-11-01", "2022-12-31", "crise"),
    ("Hausse de 2020-2021", "2020-10-01", "2021-04-30", "hausse"),
    ("Hausse de 2023-2024", "2023-10-01", "2024-03-31", "hausse"),
]

WISDOM = [
    "Dans le doute, ne rien changer : un nouveau réglage doit faire nettement mieux, pas de justesse.",
    "Un seul changement à la fois, puis 30 jours d'essai sur le vrai marché.",
    "Le risque ne monte que par petits paliers, de 1 % à 2 % par achat au plus, quand l'analyse "
    "le justifie, et redescend aussitôt à la première alerte ; nombre de positions, arrêt "
    "d'urgence et passage en réel restent hors de portée.",
    "Un plateau, pas un pic : les réglages voisins doivent aussi tenir.",
    "Les crises passées sont des énigmes : ne pas y perdre plus que les réglages actuels.",
    "Revenir en arrière sans honte : un essai raté est annulé et coûte un niveau.",
    "On ne change pas de cap dans la tempête : aucun changement pendant une forte baisse "
    "ou un arrêt d'urgence.",
    "Le plus simple d'abord : le plus petit changement qui réussit toutes les épreuves.",
    "Expliquer chaque choix en mots simples.",
]

Trial = Tuple[str, bool, str]


def fmt_value(k: str, v: float) -> str:
    return str(int(v)) if k in INT_PARAMS else fr(float(v), "g")


# ══════════════════════════════════════════════════════════════════════
# État (fichier à côté du verrou du bot)
# ══════════════════════════════════════════════════════════════════════

def state_path(gcfg: Any) -> str:
    return autonomy.sidecar(gcfg.lock_file, ".evolution.json")


def load_state(path: str) -> Dict[str, Any]:
    st: Dict[str, Any] = autonomy.read_json(path) if path else {}
    try:
        st["level"] = min(max(int(st.get("level", 1)), 1), len(LEVELS))
        st["xp"] = max(int(st.get("xp", 0)), 0)
    except (TypeError, ValueError):
        st["level"], st["xp"] = 1, 0
    st["params"] = approved(st.get("params"))
    st.setdefault("history", [])
    return st


def save_state(path: str, st: Dict[str, Any]) -> None:
    autonomy.write_json(path, st)


def approved(raw: Any) -> Dict[str, float]:
    """Réglages réellement autorisés : clés et valeurs de SPACE uniquement.
    Un fichier modifié à la main ne peut rien forcer d'autre."""
    out: Dict[str, float] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if (k in SPACE and isinstance(v, (int, float)) and not isinstance(v, bool)
                    and v in SPACE[k]):
                out[k] = int(v) if k in INT_PARAMS else float(v)
    return out


def apply(base: ts.TrendParams, over: Dict[str, float]) -> ts.TrendParams:
    over = approved(over)
    if not over:
        return base
    try:
        return dataclasses.replace(base, **over).validate()
    except ValueError:
        return base


def params_for(gcfg: Any) -> ts.TrendParams:
    """Réglages en vigueur : ceux du .env, modifiés par l'évolution si elle
    est activée (TG_EVOLUTION)."""
    if not getattr(gcfg, "evolution", False) or not state_path(gcfg):
        return gcfg.params
    return apply(gcfg.params, load_state(state_path(gcfg))["params"])


def describe(cur: ts.TrendParams, change: Dict[str, float]) -> str:
    return ", ".join(f"{LABELS[k]} {fmt_value(k, getattr(cur, k))} → {fmt_value(k, v)}"
                     for k, v in change.items())


# ══════════════════════════════════════════════════════════════════════
# Candidats autorisés par le niveau
# ══════════════════════════════════════════════════════════════════════

def _index(grid: List[float], x: float) -> int:
    return min(range(len(grid)), key=lambda i: abs(grid[i] - x))


def candidates(p: ts.TrendParams, lv: Level) -> List[Tuple[Dict[str, float], int]]:
    """(changement, pas au total), du plus simple au plus grand."""
    moves: Dict[str, List[Tuple[float, int]]] = {}
    for k, grid in SPACE.items():
        i, now = _index(grid, getattr(p, k)), getattr(p, k)
        moves[k] = [(grid[i + d], abs(d)) for d in range(-lv.steps, lv.steps + 1)
                    if d and 0 <= i + d < len(grid) and grid[i + d] != now]
    out = []
    for n in range(1, lv.n_params + 1):
        for keys in itertools.combinations(SPACE, n):
            for combo in itertools.product(*(moves[k] for k in keys)):
                out.append(({k: v for k, (v, _s) in zip(keys, combo)},
                            sum(s for _v, s in combo)))
    out.sort(key=lambda c: (len(c[0]), c[1]))
    return out


# ══════════════════════════════════════════════════════════════════════
# Les épreuves
# ══════════════════════════════════════════════════════════════════════

class Judge:
    """Rejoue l'historique Binance avec un réglage : même boucle que le bot,
    mêmes frais. Résultats gardés en mémoire pendant la routine."""

    def __init__(self, close: pd.DataFrame, volume: pd.DataFrame,
                 periods: Optional[Tuple[Tuple[str, str], Tuple[str, str]]] = None,
                 crises: Optional[List[Tuple[str, str, str, str]]] = None):
        self.close, self.volume = close, volume
        last = str(close.index[-1].date())
        self.periods = periods or (("2018-01-01", "2022-12-31"), ("2023-01-01", last))
        self.start, self.end = self.periods[0][0], self.periods[1][1]
        first, lastd = close.index[0], close.index[-1]
        self.crises = [c for c in (CRISES if crises is None else crises)
                       if pd.Timestamp(c[1], tz="UTC") >= first
                       and pd.Timestamp(c[2], tz="UTC") <= lastd]
        self._pre: Dict[Any, Any] = {}
        self._memo: Dict[Any, Any] = {}

    def _run(self, p: ts.TrendParams, start: str, end: str) -> ts.PortfolioResult:
        key = (dataclasses.astuple(p), start, end)
        if key not in self._memo:
            pk = (p.breakout_n, p.atr_n, p.mom_n, p.regime_sma)
            if pk not in self._pre:
                self._pre[pk] = ts.precompute(self.close, self.volume, p)
            self._memo[key] = ts.backtest(self.close, self.volume, p, start, end,
                                          pre=self._pre[pk])
        return self._memo[key]

    def period(self, p: ts.TrendParams, i: int) -> Dict[str, float]:
        return self._run(p, *self.periods[i]).metrics

    def equity(self, p: ts.TrendParams) -> pd.Series:
        return self._run(p, self.start, self.end).equity

    def window_return(self, p: ts.TrendParams, since: str, until: str) -> float:
        return _ret(self.equity(p), since, until)


def _ret(eq: pd.Series, a: str, b: str) -> float:
    s = eq.asof(pd.Timestamp(a, tz="UTC"))
    e = eq.asof(pd.Timestamp(b, tz="UTC"))
    s = eq.iloc[0] if pd.isna(s) else s
    e = eq.iloc[-1] if pd.isna(e) else e
    return float((e / s - 1) * 100)


def trial_epochs(j: Judge, cur: ts.TrendParams, new: ts.TrendParams, lv: Level) -> Trial:
    """Deux époques : nettement mieux (Calmar) sur 2018-2022 ET depuis 2023,
    sans sacrifier plus de 20 % du rendement."""
    ok, parts = True, []
    for i, label in enumerate(("2018-22", "2023 →")):
        c, n = j.period(cur, i), j.period(new, i)
        need = c["calmar"] + lv.margin * abs(c["calmar"])
        good = n["calmar"] >= need and (c["cagr_pct"] <= 0
                                        or n["cagr_pct"] >= MIN_CAGR_KEEP * c["cagr_pct"])
        ok = ok and good
        parts.append(f"{label} : Calmar {fr(n['calmar'])} contre {fr(c['calmar'])} "
                     f"(exigé {fr(need)}), rendement {fr(n['cagr_pct'], '+.1f')} % contre "
                     f"{fr(c['cagr_pct'], '+.1f')} %")
    return "Deux époques", ok, " ; ".join(parts)


def trial_costs(j: Judge, cur: ts.TrendParams, new: ts.TrendParams, lv: Level) -> Trial:
    """Frais doublés : l'avantage doit survivre à une exécution plus chère."""
    c2 = dataclasses.replace(cur, fee=cur.fee * 2, slippage=cur.slippage * 2)
    n2 = dataclasses.replace(new, fee=new.fee * 2, slippage=new.slippage * 2)
    ok, parts = True, []
    for i, label in enumerate(("2018-22", "2023 →")):
        c, n = j.period(c2, i)["calmar"], j.period(n2, i)["calmar"]
        ok = ok and n >= c
        parts.append(f"{label} : Calmar {fr(n)} contre {fr(c)}")
    return "Frais doublés", ok, " ; ".join(parts)


def trial_riddles(j: Judge, cur: ts.TrendParams, new: ts.TrendParams, lv: Level) -> Trial:
    """Énigmes des crises passées : ne pas y perdre plus que les réglages
    actuels, et garder au moins 90 % des grandes hausses."""
    ec, en = j.equity(cur), j.equity(new)
    solved, missed = 0, []
    for name, a, b, kind in j.crises:
        rc, rn = _ret(ec, a, b), _ret(en, a, b)
        if kind == "crise":
            good = rn >= rc - lv.crisis_tol
        else:
            good = rn >= 0.9 * rc if rc > 0 else rn >= rc
        solved += good
        if not good:
            missed.append(f"{name} : {fr(rn, '+.1f')} % contre {fr(rc, '+.1f')} %")
    return ("Énigmes des crises", not missed,
            f"{solved}/{len(j.crises)} résolues" + (" ; ratées : " + " ; ".join(missed) if missed else ""))


def neighbours(cur: ts.TrendParams, change: Dict[str, float]) -> List[Dict[str, float]]:
    """Réglages voisins du candidat : chaque valeur changée, un pas de part
    et d'autre (sauf le réglage actuel lui-même)."""
    out = []
    for k, v in change.items():
        grid = SPACE[k]
        i = _index(grid, v)
        for d in (-1, 1):
            if 0 <= i + d < len(grid):
                nb = dict(change, **{k: grid[i + d]})
                if any(getattr(cur, key) != val for key, val in nb.items()):
                    out.append(nb)
    return out


def trial_plateau(j: Judge, cur: ts.TrendParams, new: ts.TrendParams, lv: Level,
                  change: Dict[str, float]) -> Trial:
    """Plateau : les voisins du candidat gardent au moins 90 % du Calmar
    actuel sur les deux époques ; sinon c'est un pic trouvé par hasard."""
    weak = []
    nbs = neighbours(cur, change)
    for nb in nbs:
        q = dataclasses.replace(cur, **nb)
        for i in (0, 1):
            if j.period(q, i)["calmar"] < PLATEAU_KEEP * j.period(cur, i)["calmar"]:
                weak.append(describe(cur, nb))
                break
    return ("Plateau", not weak, f"{len(nbs) - len(weak)}/{len(nbs)} voisins solides"
            + (" ; fragiles : " + " | ".join(weak) if weak else ""))


def block_luck(eq: pd.Series, days: int = 1095, block: int = 30, sims: int = 1000,
               seed: int = 7) -> Tuple[float, float]:
    """Trois ans rejoués au hasard par blocs de 30 jours de la courbe :
    (rendement médian, pire baisse atteinte 1 fois sur 20), en %."""
    r = eq.pct_change().dropna().values
    if len(r) <= block:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(days / block)
    starts = rng.integers(0, len(r) - block, size=(sims, n_blocks))
    paths = r[starts[..., None] + np.arange(block)].reshape(sims, -1)[:, :days]
    curve = np.cumprod(1 + paths, axis=1)
    dd = (1 - curve / np.maximum.accumulate(curve, axis=1)).max(axis=1) * 100
    return float(np.median((curve[:, -1] - 1) * 100)), float(np.percentile(dd, 95))


def trial_luck(j: Judge, cur: ts.TrendParams, new: ts.TrendParams, lv: Level) -> Trial:
    """Hasard : rejoué 1 000 fois, pas de pire baisse nettement plus forte,
    et au moins 95 % du résultat médian."""
    mc, dc = block_luck(j.equity(cur))
    mn, dn = block_luck(j.equity(new))
    ok = dn <= dc + lv.luck_tol and (mc <= 0 or mn >= 0.95 * mc)
    return ("Hasard", ok, f"résultat médian sur 3 ans {fr(mn, '+.0f')} % contre {fr(mc, '+.0f')} % ; "
                          f"pire baisse 1 fois sur 20 −{fr(dn, '.0f')} % contre −{fr(dc, '.0f')} %")


def search(j: Judge, cur: ts.TrendParams, lv: Level) -> Dict[str, Any]:
    """Le plus simple des réglages autorisés qui réussit toutes les épreuves,
    ou aucun."""
    first: List[Tuple[Dict[str, float], ts.TrendParams, Trial]] = []
    near: Optional[Tuple[float, str]] = None
    cands = candidates(cur, lv)
    for change, _steps in cands:
        try:
            new = dataclasses.replace(cur, **change).validate()
        except ValueError:
            continue
        t = trial_epochs(j, cur, new, lv)
        if t[1]:
            first.append((change, new, t))
            continue
        score = min(j.period(new, i)["calmar"]
                    / max(j.period(cur, i)["calmar"] + lv.margin * abs(j.period(cur, i)["calmar"]), 1e-9)
                    for i in (0, 1))
        if near is None or score > near[0]:
            near = (score, f"{describe(cur, change)} ({t[2]})")
    failures = []
    for change, new, t in first:
        trials = [t]
        for fn in (trial_costs, trial_riddles, trial_plateau, trial_luck):
            r = fn(j, cur, new, lv, change) if fn is trial_plateau else fn(j, cur, new, lv)
            trials.append(r)
            if not r[1]:
                break
        if all(r[1] for r in trials):
            return {"chosen": change, "trials": trials, "tried": len(cands), "passed_first": len(first)}
        failures.append(f"{describe(cur, change)} : épreuve « {trials[-1][0]} » ratée ({trials[-1][2]})")
    return {"chosen": None, "tried": len(cands), "passed_first": len(first),
            "failures": failures, "near": near[1] if near else ""}


# ══════════════════════════════════════════════════════════════════════
# Palier de risque : de 1 % à 2 % par achat, selon l'analyse du bot
# ══════════════════════════════════════════════════════════════════════

RISK_STEPS = (1.0, 1.25, 1.5, 1.75, 2.0)   # × le risque par achat du .env
RISK_UP_DD = 0.05          # monter : capital à moins de 5 % de son plus haut
RISK_DOWN_DD = 0.10        # retour immédiat au premier palier : baisse de 10 %
RISK_DD_ROOM = 0.10        # pire baisse rejouée : 10 points sous l'arrêt d'urgence
RISK_LUCK_ROOM = 0.05      # pire baisse 1 fois sur 20 : 5 points sous l'arrêt d'urgence
RISK_REST_DOWN = 60        # jours de repos après une alerte ou un essai raté
RISK_REST_ANALYSIS = 30    # … après une descente décidée par l'analyse


def risk_steps(p: ts.TrendParams, max_pct: float = 0.02) -> List[float]:
    """Paliers permis : le risque par achat du .env multiplié par 1 à 2,
    jamais au-delà de `max_pct` (TG_RISK_MAX_PCT) ni de 2 %."""
    top = min(max_pct, 0.02) + 1e-12
    return [s for s in RISK_STEPS if p.risk_pct * s <= top] or [1.0]


def at_step(p: ts.TrendParams, step: float) -> ts.TrendParams:
    """Réglages au palier `step` : risque par achat et risque cumulé
    multipliés ensemble, le nombre de positions ne change pas."""
    if step == 1.0:
        return p
    return dataclasses.replace(p, risk_pct=p.risk_pct * step,
                               max_total_risk=p.max_total_risk * step)


def risk_state(st: Dict[str, Any], steps: List[float]) -> Dict[str, Any]:
    """Palier enregistré, borné aux paliers permis : un fichier modifié à la
    main ne peut rien forcer de plus que TG_RISK_MAX_PCT."""
    r = st.get("risk") if isinstance(st.get("risk"), dict) else {}
    step = r.get("step")
    ok = isinstance(step, (int, float)) and not isinstance(step, bool) and float(step) in steps
    r["step"] = float(step) if ok else 1.0
    pr = r.get("probation")
    if not (isinstance(pr, dict) and pr.get("new") == r["step"] and pr.get("old") in steps
            and isinstance(pr.get("since"), str)):
        r["probation"] = None
    if not isinstance(r.get("history"), list):
        r["history"] = []
    st["risk"] = r
    return r


def risk_step_for(gcfg: Any) -> float:
    """Palier de risque en vigueur (1.0 si l'évolution est désactivée) :
    lu par le bot juste avant la décision quotidienne."""
    if not getattr(gcfg, "evolution", False) or not state_path(gcfg):
        return 1.0
    steps = risk_steps(gcfg.params, getattr(gcfg, "risk_max_pct", 0.02))
    return risk_state(load_state(state_path(gcfg)), steps)["step"]


def _pct(p: ts.TrendParams, step: float) -> str:
    return fr(p.risk_pct * step * 100, "g") + " %"


def risk_trials(j: Judge, cur: ts.TrendParams, lo: float, hi: float,
                kill: float) -> List[Trial]:
    """Épreuves du palier `hi` face au palier `lo`, mêmes réglages : Calmar
    au moins égal ET rendement meilleur sur les deux époques, pire baisse
    rejouée et pire baisse du hasard loin de l'arrêt d'urgence (jamais plus
    loin que −40 % : relever TG_KILL_DRAWDOWN n'assouplit rien). S'arrête à
    la première épreuve ratée."""
    a, b = at_step(cur, lo), at_step(cur, hi)
    room = min(kill, 0.40)
    ok, parts = True, []
    for i, label in enumerate(("2018-22", "2023 →")):
        ml, mh = j.period(a, i), j.period(b, i)
        good = mh["calmar"] >= ml["calmar"] and mh["cagr_pct"] > ml["cagr_pct"]
        ok = ok and good
        parts.append(f"{label} : Calmar {fr(mh['calmar'])} contre {fr(ml['calmar'])}, rendement "
                     f"{fr(mh['cagr_pct'], '+.1f')} % contre {fr(ml['cagr_pct'], '+.1f')} %")
    out: List[Trial] = [("Deux époques", ok, " ; ".join(parts))]
    if not ok:
        return out
    eq = j.equity(b)
    worst = float((1 - eq / eq.cummax()).max() * 100)
    limit = (room - RISK_DD_ROOM) * 100
    out.append(("Pire baisse", worst <= limit, f"−{fr(worst, '.0f')} % rejoué depuis 2018 "
                                               f"(limite −{fr(limit, '.0f')} %)"))
    if not out[-1][1]:
        return out
    _med, luck = block_luck(eq)
    limit = (room - RISK_LUCK_ROOM) * 100
    out.append(("Hasard", luck <= limit, f"pire baisse 1 fois sur 20 en 3 ans −{fr(luck, '.0f')} % "
                                         f"(limite −{fr(limit, '.0f')} %, arrêt d'urgence à "
                                         f"−{fr(kill * 100, '.0f')} %)"))
    return out


Note = Tuple[str, str, bool]        # (texte, action, alerte)


def _risk_alarm(r: Dict[str, Any], cur: ts.TrendParams, today: date, dd: Optional[float],
                bull: Optional[bool], storm: bool) -> Optional[Note]:
    """Première alerte au-dessus du premier palier : retour immédiat à 1
    (60 jours de repos après une tempête ou une baisse de 10 %)."""
    alarm = storm or (dd is not None and dd >= RISK_DOWN_DD)
    if r["step"] <= 1.0 or not (alarm or bull is False):
        return None
    r.update(step=1.0, probation=None)
    if alarm:
        r["rest_until"] = (today + timedelta(days=RISK_REST_DOWN)).isoformat()
    why = ("tempête (arrêt d'urgence ou forte baisse)" if storm else
           f"baisse de {fr(dd * 100, '.1f')} % depuis le plus haut" if alarm else "marché baissier")
    return (f"Retour immédiat à {_pct(cur, 1.0)} par achat : {why}."
            + (f" Repos jusqu'au {r['rest_until']}." if alarm else ""), "descend", True)


def _risk_probation(r: Dict[str, Any], cur: ts.TrendParams, today: date,
                    judge: Callable[[], Judge]) -> Optional[Note]:
    """Essai de 30 jours du palier : jugé à son terme sur ce qui s'est vraiment
    passé, comparé au palier d'avant (retour en arrière s'il a fait moins bien)."""
    pr = r.get("probation")
    if not pr:
        return None
    day = today.isoformat()
    elapsed = (today - date.fromisoformat(pr["since"])).days
    if elapsed < PROBATION_DAYS:
        return (f"Essai du palier {_pct(cur, r['step'])} par achat, jour {elapsed} sur "
                f"{PROBATION_DAYS}.", "essai", False)
    j = judge()
    rn = j.window_return(at_step(cur, pr["new"]), pr["since"], day)
    ro = j.window_return(at_step(cur, pr["old"]), pr["since"], day)
    r["probation"] = None
    versus = (f"{fr(rn, '+.1f')} % en {elapsed} jours, contre {fr(ro, '+.1f')} % à "
              f"{_pct(cur, pr['old'])}")
    if rn >= ro - PROBATION_TOL:
        r["rest_until"] = (today + timedelta(days=REST_AFTER_CONFIRM)).isoformat()
        return (f"Essai réussi : palier {_pct(cur, r['step'])} par achat confirmé ({versus}).",
                "confirme", True)
    r.update(step=float(pr["old"]), rest_until=(today + timedelta(days=RISK_REST_DOWN)).isoformat())
    return (f"Essai raté : retour à {_pct(cur, pr['old'])} par achat ({versus}). Repos jusqu'au "
            f"{r['rest_until']}.", "annule", True)


def _risk_review(r: Dict[str, Any], cur: ts.TrendParams, today: date, judge: Callable[[], Judge],
                 steps: List[float], kill: float) -> Optional[Note]:
    """Au-dessus du premier palier : l'analyse de la nuit le justifie-t-elle
    encore face au palier du dessous ? Sinon, un cran plus bas."""
    k = steps.index(r["step"])
    if k == 0:
        return None
    step = r["step"]
    t = risk_trials(judge(), cur, steps[k - 1], step, kill)
    r["last_trials"] = [list(x) for x in t]
    if all(x[1] for x in t):
        return None
    r.update(step=steps[k - 1], rest_until=(today + timedelta(days=RISK_REST_ANALYSIS)).isoformat())
    return (f"L'analyse ne justifie plus {_pct(cur, step)} (épreuve « {t[-1][0]} » : {t[-1][2]}) : "
            f"retour à {_pct(cur, steps[k - 1])} par achat.", "descend", True)


def _risk_climb(r: Dict[str, Any], cur: ts.TrendParams, today: date, judge: Callable[[], Judge],
                steps: List[float], kill: float, dd: Optional[float], bull: Optional[bool],
                busy: bool) -> Note:
    """Un cran de plus, seulement si tout est réuni (marché haussier, capital
    près de son plus haut, aucun autre essai) et les épreuves réussies."""
    step = r["step"]
    k = steps.index(step)
    if k + 1 >= len(steps):
        return f"Palier le plus haut permis : {_pct(cur, step)} par achat.", "garde", False
    if busy:
        return (f"Un réglage est à l'essai : un seul changement à la fois, le palier reste à "
                f"{_pct(cur, step)} par achat.", "attend", False)
    if bull is not True or dd is None or dd > RISK_UP_DD:
        why = ("marché baissier" if bull is False else "situation du bot inconnue" if dd is None
               or bull is None else f"capital à {fr(dd * 100, '.1f')} % sous son plus haut "
                                    f"(au plus {fr(RISK_UP_DD * 100, '.0f')} % pour monter)")
        return f"Le bot garde {_pct(cur, step)} par achat : {why}.", "garde", False
    nxt = steps[k + 1]
    t = risk_trials(judge(), cur, step, nxt, kill)
    r["last_trials"] = [list(x) for x in t]
    if not all(x[1] for x in t):
        return (f"Le bot garde {_pct(cur, step)} par achat : {_pct(cur, nxt)} refusé, épreuve "
                f"« {t[-1][0]} » ratée ({t[-1][2]}).", "garde", False)
    r.update(step=nxt, probation={"since": today.isoformat(), "old": step, "new": nxt})
    return (f"Palier relevé à {_pct(cur, nxt)} par achat à la prochaine décision : épreuves "
            f"réussies ({', '.join(x[0] for x in t)}). Essai de {PROBATION_DAYS} jours ; retour "
            f"immédiat à {_pct(cur, 1.0)} à la première alerte.", "monte", True)


def run_risk(st: Dict[str, Any], cur: ts.TrendParams, today: date,
             judge: Callable[[], Judge], steps: List[float], kill: float,
             dd: Optional[float], bull: Optional[bool], storm: bool = False,
             busy: bool = False, notify: Callable[[str], None] = lambda _t: None) -> None:
    """Routine quotidienne du palier de risque (après celle des réglages), en
    quatre temps : la première alerte fait redescendre aussitôt au premier
    palier ; un essai de 30 jours est jugé à son terme ; un palier que
    l'analyse ne justifie plus perd un cran ; sinon, après le repos, un cran
    de plus si tout est réuni et les épreuves réussies. `dd` et `bull` :
    situation du bot (None si inconnue). Met à jour st["risk"] ; un
    changement est daté dans st["last_change"] (rapport aussitôt)."""
    r = risk_state(st, steps)
    day = today.isoformat()
    found = (_risk_alarm(r, cur, today, dd, bull, storm)
             or _risk_probation(r, cur, today, judge))
    if found is None and r.get("rest_until") and day < r["rest_until"]:
        found = (f"Repos jusqu'au {r['rest_until']} : le palier reste à {_pct(cur, r['step'])} "
                 "par achat.", "repos", False)
    found = (found or _risk_review(r, cur, today, judge, steps, kill)
             or _risk_climb(r, cur, today, judge, steps, kill, dd, bull, busy))
    text, action, alert = found
    r["history"] = r["history"][-(HISTORY_MAX - 1):] + [
        {"day": day, "action": action, "step": r["step"], "text": text}]
    r["last_text"] = text
    if alert:
        mark_change(st, f"palier de risque : {text}")
        notify(text)


def mark_change(st: Dict[str, Any], text: str) -> None:
    """Compétence ou expérience acquise (réglage adopté, confirmé ou annulé,
    palier de risque changé) : datée pour que le bot en fasse aussitôt un
    rapport."""
    st["last_change"] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "text": text}


def last_change(gcfg: Any) -> Optional[Dict[str, Any]]:
    """Dernière compétence acquise ({"at", "text"}), ou None (évolution
    désactivée ou rien encore)."""
    if not getattr(gcfg, "evolution", False) or not state_path(gcfg):
        return None
    ch = load_state(state_path(gcfg)).get("last_change")
    return ch if isinstance(ch, dict) and isinstance(ch.get("at"), str) else None


# ══════════════════════════════════════════════════════════════════════
# La routine quotidienne
# ══════════════════════════════════════════════════════════════════════

def _register(record: Optional[Callable[[Dict[str, Any]], Any]], j: Judge, kind: str,
              params: Dict[str, ts.TrendParams], conclusion: str, details: Dict[str, Any]) -> None:
    """Expérience notée au registre (registre.py) : réglages, résultats sur
    les deux époques, empreinte des données. Un registre illisible ne bloque
    jamais l'évolution."""
    if record is None:
        return
    try:
        record(registre.experiment(kind, j.close, j.volume, j.periods, params,
                                   {k: [j.period(p, i) for i in (0, 1)] for k, p in params.items()},
                                   conclusion, details))
    except Exception as e:
        print(f"Registre des expériences : expérience non notée ({type(e).__name__} : {e})")


def run_daily(base: ts.TrendParams, path: str, today: date,
              judge_factory: Callable[[], Judge], storm: bool = False,
              notify: Callable[[str], None] = lambda _t: None,
              force: bool = False, risk: Optional[Dict[str, Any]] = None,
              record: Optional[Callable[[Dict[str, Any]], Any]] = None) -> Dict[str, Any]:
    """Routine quotidienne de l'évolution encadrée (une fois par jour, sauf
    `force`) : rien par tempête ; l'essai en cours est jugé à son terme
    (confirmé ou annulé) ; sinon, après le repos, des réglages voisins sont
    éprouvés et un seul n'est adopté que s'il réussit toutes les épreuves.
    Puis, si `risk` est donné (paliers permis, arrêt d'urgence, baisse et
    marché du bot, alerte), la routine du palier de risque (run_risk) : un
    seul changement à l'essai à la fois, réglage ou palier. Chaque épreuve
    et chaque fin d'essai est notée par `record` (registre des expériences).
    État enregistré dans `path`, puis renvoyé."""
    st = load_state(path)
    day = today.isoformat()
    if st.get("last_run") == day and not force:
        return st
    lv = LEVELS[st["level"] - 1]
    cur = apply(base, st["params"])
    report: Dict[str, Any] = {}
    judges: List[Judge] = []

    def judge() -> Judge:
        """Un seul chargement de l'historique pour toute la routine."""
        if not judges:
            judges.append(judge_factory())
        return judges[0]

    def note(text: str, action: str, alert: bool = False) -> None:
        st["history"] = (st.get("history") or [])[-(HISTORY_MAX - 1):] + [
            {"day": day, "action": action, "level": st["level"], "text": text}]
        st["last_text"] = text
        if alert:
            mark_change(st, text)
            notify(text)

    pr = st.get("probation")
    risk_trial = isinstance((st.get("risk") or {}).get("probation"), dict)
    if storm:
        note("Tempête (arrêt d'urgence ou baisse de plus de 15 %) : aucun changement "
             "aujourd'hui, par sagesse.", "tempete")
    elif isinstance(pr, dict):
        elapsed = (today - date.fromisoformat(pr["since"])).days
        if elapsed < PROBATION_DAYS:
            note(f"Période d'essai, jour {elapsed} sur {PROBATION_DAYS} : {pr['text']}.", "essai")
        else:
            j = judge()
            rn = j.window_return(apply(base, pr["new"]), pr["since"], day)
            ro = j.window_return(apply(base, pr["old"]), pr["since"], day)
            st["probation"] = None
            versus = (f"{fr(rn, '+.1f')} % en {elapsed} jours, contre {fr(ro, '+.1f')} % "
                      "avec les anciens réglages")
            if rn >= ro - PROBATION_TOL:
                st["xp"] += 1
                text = f"Essai réussi : {pr['text']} ({versus}). Réglage confirmé."
                if lv.promote_after and st["xp"] >= lv.promote_after and st["level"] < len(LEVELS):
                    st["level"] += 1
                    st["xp"] = 0
                    nxt = LEVELS[st["level"] - 1]
                    text += (f" Promotion : niveau {st['level']}, {nxt.name} ; plus de liberté, "
                             "épreuves plus exigeantes.")
                st["rest_until"] = (today + timedelta(days=REST_AFTER_CONFIRM)).isoformat()
                note(text, "confirme", alert=True)
            else:
                st["params"] = approved(pr["old"])
                st["level"] = max(1, st["level"] - 1)
                st["xp"] = 0
                st["rest_until"] = (today + timedelta(days=REST_AFTER_REVERT)).isoformat()
                note(f"Essai raté : {pr['text']} ({versus}). Retour aux anciens réglages à la "
                     f"prochaine décision ; niveau {st['level']}, {LEVELS[st['level'] - 1].name}.",
                     "annule", alert=True)
            _register(record, j, "essai", {"ancien": apply(base, pr["old"]), "nouveau": apply(base, pr["new"])},
                      st["last_text"], {"depuis": pr["since"], "rendement_nouveau": rn, "rendement_ancien": ro})
    elif st.get("rest_until") and day < st["rest_until"]:
        note(f"Repos jusqu'au {st['rest_until']} : on laisse le marché juger le dernier "
             "changement.", "repos")
    elif risk_trial:
        note("Le palier de risque est à l'essai : un seul changement à la fois, les réglages "
             "attendent la fin de l'essai.", "attend")
    else:
        res = search(judge(), cur, lv)
        report = {"tried": res["tried"], "passed_first": res["passed_first"],
                  "trials": [list(t) for t in res.get("trials", [])]}
        if res["chosen"]:
            change = res["chosen"]
            new = {k: v for k, v in {**st["params"], **change}.items() if v != getattr(base, k)}
            text = describe(cur, change)
            st["probation"] = {"since": day, "old": st["params"], "new": approved(new), "text": text}
            st["params"] = approved(new)
            note(f"Nouveau réglage adopté à la prochaine décision : {text}. Épreuves réussies : "
                 + ", ".join(t[0] for t in res["trials"]) + f". Essai de {PROBATION_DAYS} jours.",
                 "adopte", alert=True)
        else:
            why = (f" Le plus proche : {res['near']}." if res.get("near") and not res["failures"]
                   else f" {res['failures'][0]}." if res["failures"] else "")
            note(f"{res['tried']} réglages essayés au niveau {lv.name} ; aucun ne réussit toutes "
                 f"les épreuves : le bot garde les siens.{why}", "garde")
        tried = {"actuel": cur}
        if res["chosen"]:
            tried["candidat"] = dataclasses.replace(cur, **res["chosen"])
        _register(record, judge(), "evolution", tried, st["last_text"],
                  {"niveau": lv.name, "essayes": res["tried"], "premiere_epreuve": res["passed_first"],
                   "epreuves": [[t[0], bool(t[1]), t[2]] for t in res.get("trials", [])]})
    if risk is not None:
        run_risk(st, apply(base, st["params"]), today, judge, risk["steps"], risk["kill"],
                 risk.get("dd"), risk.get("bull"), storm=storm,
                 busy=isinstance(st.get("probation"), dict),
                 notify=risk.get("notify") or (lambda _t: None))
    st["last_run"] = day
    st["last_report"] = report
    save_state(path, st)
    return st


def reset(path: str, today: date) -> Dict[str, Any]:
    """Retour aux réglages d'origine (demandé par l'utilisateur)."""
    st = load_state(path)
    st.update(params={}, probation=None, level=1, xp=0,
              rest_until=(today + timedelta(days=REST_AFTER_RESET)).isoformat())
    r = risk_state(st, [1.0])
    r.update(step=1.0, probation=None, rest_until=st["rest_until"],
             last_text="Retour aux réglages d'origine demandé : palier de risque ramené au premier.")
    text = ("Retour aux réglages d'origine demandé : niveau 1, Apprenti, palier de risque au "
            "premier, 30 jours de repos.")
    st["history"] = st["history"][-(HISTORY_MAX - 1):] + [
        {"day": today.isoformat(), "action": "revenir", "level": 1, "text": text}]
    st["last_text"] = text
    save_state(path, st)
    return st


def summary(gcfg: Any) -> Dict[str, Any]:
    """Pour le panneau et le rapport : niveau, réglages changés, essai en
    cours, palier de risque (en %, avec le plus haut permis)."""
    if not getattr(gcfg, "evolution", False) or not state_path(gcfg):
        return {"enabled": False}
    st = load_state(state_path(gcfg))
    lv = LEVELS[st["level"] - 1]
    steps = risk_steps(gcfg.params, getattr(gcfg, "risk_max_pct", 0.02))
    r = risk_state(st, steps)
    base = gcfg.params.risk_pct * 100
    return {"enabled": True, "level": st["level"], "levels": len(LEVELS), "name": lv.name,
            "xp": st["xp"], "promote_after": lv.promote_after,
            "changes": [{"param": LABELS[k], "from": fmt_value(k, getattr(gcfg.params, k)),
                         "to": fmt_value(k, v)} for k, v in st["params"].items()],
            "probation": st.get("probation"), "rest_until": st.get("rest_until"),
            "last_run": st.get("last_run"), "last_text": st.get("last_text"),
            "risk": {"step": r["step"], "pct": base * r["step"], "base_pct": base,
                     "max_pct": base * steps[-1], "probation": r.get("probation"),
                     "rest_until": r.get("rest_until"), "last_text": r.get("last_text")}}


# ══════════════════════════════════════════════════════════════════════
# Données : historique Binance en cache, rafraîchi s'il date
# ══════════════════════════════════════════════════════════════════════

def load_history(cache: str, bases: List[str], max_age_days: Optional[int] = 2,
                 fetch: Optional[Callable[..., Any]] = None,
                 now: Optional[datetime] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Clôtures et volumes journaliers Binance depuis 2017, au format Coin
    Metrics. Téléchargés s'ils manquent ou datent de plus de `max_age_days`
    jours (None : jamais rafraîchis, pour les études reproductibles)."""
    bases = [b.lower() for b in bases]
    if all(os.path.exists(os.path.join(cache, f"{a}.csv")) for a in bases):
        close, volume = ts.load_coinmetrics(cache, bases)
        today = (now or datetime.now(timezone.utc)).date()
        if max_age_days is None or (today - close.index[-1].date()).days <= max_age_days:
            return close, volume
    exchange = None if fetch is not None else v29.PublicKlines()
    close, volume, errors = (fetch or dg.fetch_daily_history)(
        exchange, [a.upper() for a in bases], since="2017-07-01")
    if errors:
        print(f"Erreurs de téléchargement : {errors}")
    os.makedirs(cache, exist_ok=True)
    for a in close.columns:
        pd.DataFrame({"time": close.index.strftime("%Y-%m-%d"),
                      "PriceUSD": close[a].values,
                      "volume_reported_spot_usd_1d": volume[a].values}
                     ).dropna(subset=["PriceUSD"]).to_csv(
            os.path.join(cache, f"{a}.csv"), index=False)
    return ts.load_coinmetrics(cache, bases)


# ══════════════════════════════════════════════════════════════════════
# Ligne de commande
# ══════════════════════════════════════════════════════════════════════

def _print_rules(say: Callable[[str], None]) -> None:
    say("Règles de sagesse :")
    for k, rule in enumerate(WISDOM, 1):
        say(f"  {k}. {rule}")
    say("\nNiveaux (liberté ↑, épreuves plus dures ↑) :")
    for k, lv in enumerate(LEVELS, 1):
        say(f"  {k}. {lv.name:<10} {lv.n_params} paramètre(s) à la fois, pas de {lv.steps} ; "
            f"Calmar +{lv.margin * 100:.0f} % exigé ; "
            + (f"monte après {lv.promote_after} réglage(s) confirmé(s)" if lv.promote_after
               else "niveau le plus haut"))
    say("\nPalier de risque (1 % → 2 % par achat, un cran de 0,25 % à la fois) :")
    say(f"  monter : marché haussier, capital à moins de {RISK_UP_DD * 100:.0f} % de son plus "
        "haut, aucun autre essai en cours, et trois épreuves : meilleur sur les deux époques, "
        f"pire baisse rejouée à {RISK_DD_ROOM * 100:.0f} points de l'arrêt d'urgence, pire baisse "
        f"du hasard à {RISK_LUCK_ROOM * 100:.0f} points ; puis {PROBATION_DAYS} jours d'essai.")
    say(f"  redescendre : aussitôt au premier palier à {RISK_DOWN_DD * 100:.0f} % de baisse, en "
        "marché baissier ou à l'arrêt d'urgence ; d'un cran si l'analyse ne le justifie plus.")


def _risk_examen(j: Judge, cur: ts.TrendParams, st: Dict[str, Any], gcfg: Any) -> None:
    """Examen du palier de risque (sans rien changer), pour la commande
    evolution examen."""
    steps = risk_steps(gcfg.params, gcfg.risk_max_pct)
    step = risk_state(st, steps)["step"]
    k = steps.index(step)
    print(f"\nPalier de risque : {_pct(cur, step)} par achat (au plus {_pct(cur, steps[-1])}).")
    pairs = ([(steps[k - 1], step)] if k > 0 else []) + (
        [(step, steps[k + 1])] if k + 1 < len(steps) else [])
    for lo, hi in pairs:
        print(f"  {_pct(cur, hi)} face à {_pct(cur, lo)} :")
        for t in risk_trials(j, cur, lo, hi, gcfg.kill_drawdown):
            print(f"    {'✓' if t[1] else '✗'} {t[0]} : {t[2]}")


def main(argv: Optional[List[str]] = None) -> int:
    """Ligne de commande de l'évolution encadrée : statut (défaut), routine
    quotidienne, examen (épreuves sans rien changer), retour aux réglages
    d'origine ou rappel des règles."""
    from . import alerts
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Évolution encadrée du bot TrendGuard")
    ap.add_argument("action", nargs="?", default="statut",
                    choices=["statut", "quotidien", "examen", "revenir", "regles"])
    ap.add_argument("--tempete", action="store_true", help="(quotidien) forte baisse en cours")
    ap.add_argument("--force", action="store_true", help="(quotidien) refaire la routine du jour")
    ap.add_argument("--baisse", type=float, default=None,
                    help="(quotidien) baisse du capital depuis son plus haut (0.05 = 5 %%)")
    ap.add_argument("--marche", choices=["haussier", "baissier"], default=None,
                    help="(quotidien) marché lu par le bot à la décision")
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    gcfg = load_guard_config_from_env()
    path = state_path(gcfg)
    today = datetime.now(timezone.utc).date()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if args.action == "regles":
        _print_rules(print)
        return 0
    if args.action == "revenir":
        st = reset(path, today)
        print(st["last_text"])
        return 0
    if args.action in ("quotidien", "examen"):
        if args.action == "quotidien" and not gcfg.evolution:
            print(f"{stamp} Évolution désactivée (TG_EVOLUTION=false).")
            return 0

        def judge() -> Judge:
            return Judge(*load_history(args.cache, list(gcfg.universe)))
        if args.action == "examen":
            st = load_state(path)
            lv = LEVELS[st["level"] - 1]
            j = judge()
            res = search(j, apply(gcfg.params, st["params"]), lv)
            print(f"Niveau {st['level']} ({lv.name}) : {res['tried']} réglages essayés, "
                  f"{res['passed_first']} passent la première épreuve.")
            for t in res.get("trials", []):
                print(f"  {'✓' if t[1] else '✗'} {t[0]} : {t[2]}")
            print("Réglage qui réussit tout : " + (describe(apply(gcfg.params, st["params"]), res["chosen"])
                                                   if res["chosen"] else "aucun"))
            for f in res.get("failures", [])[:5]:
                print(f"  - {f}")
            if res.get("near"):
                print(f"  Le plus proche : {res['near']}")
            _risk_examen(j, apply(gcfg.params, st["params"]), st, gcfg)
            return 0
        hub = alerts.build_notifier()
        risk = {"steps": risk_steps(gcfg.params, gcfg.risk_max_pct), "kill": gcfg.kill_drawdown,
                "dd": args.baisse, "bull": {"haussier": True, "baissier": False}.get(args.marche),
                "notify": lambda t: hub(f"🎚️ TrendGuard — palier de risque : {t}",
                                        dedup_key=f"palier-{today}", critical=True, sync=True)}
        try:
            st = run_daily(gcfg.params, path, today, judge, storm=args.tempete, force=args.force,
                           notify=lambda t: hub(f"🧬 TrendGuard — évolution : {t}",
                                                dedup_key=f"evolution-{today}", critical=True,
                                                sync=True), risk=risk,
                           record=lambda e: registre.record(registre.registry_path(gcfg), e))
        finally:
            hub.close()
        print(f"{stamp} {st.get('last_text', '')}")
        print(f"{stamp} Palier de risque : {(st.get('risk') or {}).get('last_text', '')}")
        return 0
    st = load_state(path)
    lv = LEVELS[st["level"] - 1]
    print(f"Évolution encadrée : {'activée' if gcfg.evolution else 'DÉSACTIVÉE (TG_EVOLUTION=false)'}")
    print(f"Niveau {st['level']} sur {len(LEVELS)} : {lv.name}"
          + (f" ({st['xp']}/{lv.promote_after} réglage(s) confirmé(s) pour monter)"
             if lv.promote_after else ""))
    print("Réglages changés : " + (describe(gcfg.params, st["params"]) if st["params"] else "aucun"))
    if st.get("probation"):
        print(f"En essai depuis le {st['probation']['since']} : {st['probation']['text']}")
    for h in st["history"][-10:]:
        print(f"  {h['day']} · {h['text']}")
    steps = risk_steps(gcfg.params, gcfg.risk_max_pct)
    r = risk_state(st, steps)
    print(f"\nPalier de risque : {_pct(gcfg.params, r['step'])} par achat (au plus "
          f"{_pct(gcfg.params, steps[-1])}, TG_RISK_MAX_PCT)")
    for h in r["history"][-5:]:
        print(f"  {h['day']} · {h['text']}")
    print()
    _print_rules(print)
    return 0


if __name__ == "__main__":
    sys.exit(main())
