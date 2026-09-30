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

Hors de sa portée à tout niveau : risque par trade, nombre de positions,
risque cumulé, arrêt d'urgence, filtres de liquidité, frais, passage en
réel. Il ne règle que la cassure, les stops et la lecture du marché.

  python trendguard_bot.py evolution            # statut, règles et historique
  python trendguard_bot.py evolution examen     # épreuves du jour, sans rien changer
  python trendguard_bot.py evolution quotidien  # la routine du jour (lancée par le bot)
  python trendguard_bot.py evolution revenir    # retour aux réglages d'origine
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import v29

from . import autonomy
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
    "Jamais plus de risque : risque par trade, nombre de positions, risque cumulé, arrêt "
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
    st: Dict[str, Any] = {}
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            st = raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            st = {}
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
# La routine quotidienne
# ══════════════════════════════════════════════════════════════════════

def run_daily(base: ts.TrendParams, path: str, today: date,
              judge_factory: Callable[[], Judge], storm: bool = False,
              notify: Callable[[str], None] = lambda _t: None,
              force: bool = False) -> Dict[str, Any]:
    st = load_state(path)
    day = today.isoformat()
    if st.get("last_run") == day and not force:
        return st
    lv = LEVELS[st["level"] - 1]
    cur = apply(base, st["params"])
    report: Dict[str, Any] = {}

    def note(text: str, action: str, alert: bool = False) -> None:
        st["history"] = (st.get("history") or [])[-(HISTORY_MAX - 1):] + [
            {"day": day, "action": action, "level": st["level"], "text": text}]
        st["last_text"] = text
        if alert:
            notify(text)

    pr = st.get("probation")
    if storm:
        note("Tempête (arrêt d'urgence ou baisse de plus de 15 %) : aucun changement "
             "aujourd'hui, par sagesse.", "tempete")
    elif isinstance(pr, dict):
        elapsed = (today - date.fromisoformat(pr["since"])).days
        if elapsed < PROBATION_DAYS:
            note(f"Période d'essai, jour {elapsed} sur {PROBATION_DAYS} : {pr['text']}.", "essai")
        else:
            j = judge_factory()
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
    elif st.get("rest_until") and day < st["rest_until"]:
        note(f"Repos jusqu'au {st['rest_until']} : on laisse le marché juger le dernier "
             "changement.", "repos")
    else:
        res = search(judge_factory(), cur, lv)
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
    st["last_run"] = day
    st["last_report"] = report
    save_state(path, st)
    return st


def reset(path: str, today: date) -> Dict[str, Any]:
    """Retour aux réglages d'origine (demandé par l'utilisateur)."""
    st = load_state(path)
    st.update(params={}, probation=None, level=1, xp=0,
              rest_until=(today + timedelta(days=REST_AFTER_RESET)).isoformat())
    text = "Retour aux réglages d'origine demandé : niveau 1, Apprenti, 30 jours de repos."
    st["history"] = st["history"][-(HISTORY_MAX - 1):] + [
        {"day": today.isoformat(), "action": "revenir", "level": 1, "text": text}]
    st["last_text"] = text
    save_state(path, st)
    return st


def summary(gcfg: Any) -> Dict[str, Any]:
    """Pour le panneau : niveau, réglages changés, essai en cours."""
    if not getattr(gcfg, "evolution", False) or not state_path(gcfg):
        return {"enabled": False}
    st = load_state(state_path(gcfg))
    lv = LEVELS[st["level"] - 1]
    return {"enabled": True, "level": st["level"], "levels": len(LEVELS), "name": lv.name,
            "xp": st["xp"], "promote_after": lv.promote_after,
            "changes": [{"param": LABELS[k], "from": fmt_value(k, getattr(gcfg.params, k)),
                         "to": fmt_value(k, v)} for k, v in st["params"].items()],
            "probation": st.get("probation"), "rest_until": st.get("rest_until"),
            "last_run": st.get("last_run"), "last_text": st.get("last_text")}


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


def main(argv: Optional[List[str]] = None) -> int:
    from . import alerts
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Évolution encadrée du bot TrendGuard")
    ap.add_argument("action", nargs="?", default="statut",
                    choices=["statut", "quotidien", "examen", "revenir", "regles"])
    ap.add_argument("--tempete", action="store_true", help="(quotidien) forte baisse en cours")
    ap.add_argument("--force", action="store_true", help="(quotidien) refaire la routine du jour")
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
            res = search(judge(), apply(gcfg.params, st["params"]), lv)
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
            return 0
        hub = alerts.build_notifier()
        try:
            st = run_daily(gcfg.params, path, today, judge, storm=args.tempete, force=args.force,
                           notify=lambda t: hub(f"🧬 TrendGuard — évolution : {t}",
                                                dedup_key=f"evolution-{today}", critical=True,
                                                sync=True))
        finally:
            hub.close()
        print(f"{stamp} {st.get('last_text', '')}")
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
    print()
    _print_rules(print)
    return 0


if __name__ == "__main__":
    sys.exit(main())
