"""
Moteur de stratégie (prompt maître, étape 9 ; docs/MOTEUR_STRATEGIE.md) : la
règle du bot écrite en données, versionnée, validée et expliquée, sans jamais
la remplacer.

    fiche déclarative → compilation sûre (liste blanche, aucun eval) →
    validation (schéma, dépendances, paramètres, coûts, liquidité,
    contraintes, verrous, déterminisme, fuite vers le futur, équivalence) →
    PRÊTE POUR LE BACKTEST ou BLOQUÉE → décision par crypto (chaque
    condition vraie ou fausse, taille, stop, coût) → « pas de trade » avec
    ses raisons

La fiche décrit la règle exécutée (trend_strategy.py) ; elle n'en est pas une
seconde. Un test, et chaque décision du bot, vérifient qu'elle donne
exactement les mêmes signaux ; un écart est signalé, jamais corrigé en
silence. Ses verrous (empreintes du code des indicateurs et des règles)
obligent à changer sa version quand ce code change. Une stratégie n'a aucune
autorité d'exécution : la porte d'exécution décide (porte.py).

    python trendguard_bot.py regle                 # la fiche de la règle en vigueur
    python trendguard_bot.py regle valider         # validation sur l'historique Binance
    python trendguard_bot.py regle etats           # cycle de vie d'une stratégie
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import inspect
import json
import math
import operator
import os
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

import v29

from . import finance
from . import trend_strategy as ts
from .contrats import STRATEGY_REASONS, StrategyDecision
from .registre import fingerprint
from .texte import fr, fr_plain

ENGINE_VERSION = "1.0.0"
FEATURES = {"close": "clôture du jour",
            "prior_high": "plus haut des clôtures précédentes (fenêtre de la cassure)",
            "mom": "momentum : rendement logarithmique de la fenêtre, divisé par sa volatilité",
            "vol": "volatilité : moyenne exponentielle des variations absolues de la clôture",
            "age": "jours d'historique de la crypto",
            "vol30": "volume moyen de 30 jours, en dollars"}
OPERATORS: Dict[str, Callable[[Any, Any], Any]] = {">": operator.gt, ">=": operator.ge, "<": operator.lt,
                                                   "<=": operator.le, "==": operator.eq, "!=": operator.ne}
FAMILIES = ("TREND_FOLLOWING", "MOMENTUM", "MEAN_REVERSION", "BREAKOUT", "STATISTICAL_ARBITRAGE", "FACTOR",
            "VOLATILITY", "MACRO", "EVENT_DRIVEN", "HYBRID")
REGIMES = ("BULL", "BEAR")
MAX_DEPTH = 4                   # imbrication des « toutes » / « une des »
MAX_CONDITIONS = 32
REQUIRED = ("id", "name", "version", "family", "status", "description", "universe", "timeframe", "regime",
            "entry", "rank", "exit", "stops", "sizing", "constraints", "costs", "liquidity", "parameters",
            "dependencies", "locks")
TUNED = ("breakout_n", "atr_n", "init_stop_atr", "trail_atr", "regime_sma", "bear_trail_atr", "mom_n")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# Cycle de vie (§5, §40) : jamais de raccourci vers le réel.
STATUSES = ("DRAFT", "DEVELOPMENT", "VALIDATING", "BACKTEST_PENDING", "BACKTESTING", "BACKTEST_FAILED",
            "BACKTEST_PASSED", "ROBUSTNESS_PENDING", "ROBUSTNESS_FAILED", "ROBUSTNESS_PASSED", "PAPER_PENDING",
            "PAPER", "PRODUCTION_CANDIDATE", "APPROVED", "SUSPENDED", "DEPRECATED", "BLOCKED", "REJECTED")
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "DRAFT": ("DEVELOPMENT", "REJECTED"),
    "DEVELOPMENT": ("VALIDATING", "DRAFT", "REJECTED"),
    "VALIDATING": ("BACKTEST_PENDING", "BLOCKED", "DEVELOPMENT"),
    "BACKTEST_PENDING": ("BACKTESTING",),
    "BACKTESTING": ("BACKTEST_PASSED", "BACKTEST_FAILED"),
    "BACKTEST_FAILED": ("DEVELOPMENT", "REJECTED"),
    "BACKTEST_PASSED": ("ROBUSTNESS_PENDING",),
    "ROBUSTNESS_PENDING": ("ROBUSTNESS_PASSED", "ROBUSTNESS_FAILED"),
    "ROBUSTNESS_FAILED": ("DEVELOPMENT", "REJECTED"),
    "ROBUSTNESS_PASSED": ("PAPER_PENDING",),
    "PAPER_PENDING": ("PAPER",),
    "PAPER": ("PRODUCTION_CANDIDATE", "SUSPENDED", "REJECTED"),
    "PRODUCTION_CANDIDATE": ("APPROVED", "PAPER", "REJECTED"),
    "APPROVED": ("SUSPENDED", "DEPRECATED"),
    "SUSPENDED": ("PAPER", "DEPRECATED", "REJECTED"),
    "BLOCKED": ("DEVELOPMENT", "REJECTED"),
    "DEPRECATED": (),
    "REJECTED": (),
}
STATUS_LABELS = {"PAPER": "en essai, argent fictif", "APPROVED": "approuvée pour le réel",
                 "PRODUCTION_CANDIDATE": "candidate au réel (porte du réel)", "SUSPENDED": "suspendue",
                 "ROBUSTNESS_PASSED": "épreuves de robustesse passées", "BLOCKED": "bloquée"}

# Le code verrouillé : changer l'un de ces calculs change l'empreinte, et la
# fiche doit changer de version (test : la validation la bloque sinon).
LOCKED_FEATURES: Tuple[Callable[..., Any], ...] = (ts.asset_features, ts.btc_regime)
LOCKED_RULES: Tuple[Callable[..., Any], ...] = (ts.entry_signal, ts.initial_stop, ts.entry_levels, ts.size_position,
                                                ts.trailing_stop, ts.update_positions, ts.risk_multiplier,
                                                ts.plan_entries)


class SpecError(ValueError):
    """Fiche refusée : la raison est dite, rien n'est corrigé."""


def code_lock(fns: Sequence[Callable[..., Any]]) -> str:
    """Empreinte du code source de fonctions (fins de ligne normalisées)."""
    h = hashlib.sha256()
    for fn in fns:
        h.update(inspect.getsource(fn).replace("\r\n", "\n").encode("utf-8"))
    return h.hexdigest()[:16]


RISK_SETTINGS = ("risk_pct", "max_positions", "max_total_risk", "max_position_pct")


def _param(name: str, lo: float, hi: float, step: float, unit: str, text: str) -> Dict[str, Any]:
    """Un réglage de la fiche : plage validée par la recherche (règle) ou
    plage permise (risque, choisi par le propriétaire ; la valeur de la
    recherche reste le défaut)."""
    risk = name in RISK_SETTINGS
    return {"default": getattr(ts.TrendParams(), name), "min": lo, "max": hi, "step": step, "unit": unit,
            "description": text, "kind": "RISK" if risk else "RULE",
            "source": "recherche 2018-2022, gelé ensuite" if name in TUNED else
            "réglage de risque : plage permise, défaut de la recherche" if risk else "réglage de la règle",
            "optimization": "FROZEN", "version": "1.0.0"}


PARAMETERS: Dict[str, Dict[str, Any]] = {
    "breakout_n": _param("breakout_n", 20, 50, 10, "jours", "fenêtre du plus haut à casser"),
    "atr_n": _param("atr_n", 10, 30, 5, "jours", "fenêtre de la volatilité"),
    "init_stop_atr": _param("init_stop_atr", 2.0, 4.0, 0.5, "volatilités", "distance du stop initial"),
    "trail_atr": _param("trail_atr", 4.0, 6.0, 1.0, "volatilités", "distance du stop suiveur"),
    "regime_sma": _param("regime_sma", 100, 200, 50, "jours", "moyenne de BTC qui définit le régime"),
    "bear_trail_atr": _param("bear_trail_atr", 0.0, 3.0, 1.0, "volatilités",
                             "stop suiveur resserré en régime baissier (0 : non)"),
    "mom_n": _param("mom_n", 60, 120, 30, "jours", "fenêtre du momentum"),
    "risk_pct": _param("risk_pct", 0.0025, 0.02, 0.0025, "part du capital", "risque d'un achat jusqu'au stop"),
    "max_positions": _param("max_positions", 1, 20, 1, "positions", "positions simultanées au plus"),
    "max_total_risk": _param("max_total_risk", 0.01, 0.10, 0.01, "part du capital", "risque cumulé au plus"),
    "max_position_pct": _param("max_position_pct", 0.05, 0.50, 0.05, "part du capital", "taille d'une position au plus"),
    "min_history": _param("min_history", 150, 365, 50, "jours", "historique minimum d'une crypto"),
    "min_volume_usd": _param("min_volume_usd", 1e6, 2e7, 1e6, "dollars", "volume moyen de 30 jours minimum"),
    "fee": _param("fee", 0.0001, 0.005, 0.0005, "par côté", "frais de Binance"),
    "slippage": _param("slippage", 0.0001, 0.01, 0.0005, "par côté", "glissement (prix obtenu moins bon)"),
}

TREND: Dict[str, Any] = {
    "id": "trendguard.cassure",
    "name": "Cassure du plus haut de 30 jours, momentum de 90 jours, régime de BTC",
    "version": "1.0.0",
    "family": "TREND_FOLLOWING",
    "families": ["TREND_FOLLOWING", "BREAKOUT", "MOMENTUM"],
    "status": "PAPER",
    "description": "suit les tendances : achète une crypto qui casse son plus haut récent avec un momentum "
                   "positif, quand BTC est au-dessus de sa moyenne ; coupe les pertes au stop et laisse courir "
                   "les gains sous un stop suiveur. Peu de trades gagnants, mais des gains bien plus grands que "
                   "les pertes.",
    "universe": {"asset_classes": ["CRYPTO"], "markets": ["BINANCE_SPOT"], "quote": "USDT", "direction": "LONG"},
    "timeframe": {"signal": "1d", "execution": "1d", "when": "juste après la clôture de 00:00 UTC"},
    "regime": {"allowed": ["BULL"], "code": "REGIME_BLOCK",
               "label": "BTC au-dessus de sa moyenne de $regime_sma jours"},
    "entry": {"all": [
        {"if": ["close", ">", "prior_high"], "code": "NO_BREAKOUT",
         "label": "cassure du plus haut des $breakout_n clôtures précédentes"},
        {"if": ["mom", ">", 0], "code": "NO_MOMENTUM", "label": "momentum de $mom_n jours positif"},
        {"if": ["age", ">=", "$min_history"], "code": "SHORT_HISTORY",
         "label": "au moins $min_history jours d'historique"},
        {"if": ["vol30", ">=", "$min_volume_usd"], "code": "LOW_LIQUIDITY",
         "label": "volume moyen de 30 jours d'au moins $min_volume_usd dollars"},
        {"if": ["vol", ">", 0], "code": "NO_VOLATILITY", "label": "volatilité mesurée"},
    ]},
    "rank": {"by": "mom", "order": "DESC", "label": "les plus forts momentums d'abord quand la place manque"},
    "exit": {"any": [{"rule": "STOP", "label": "clôture sous le stop de la veille"},
                     {"rule": "DELISTING", "label": "crypto sans cotation (vendue, décote de 50 % au backtest)"}]},
    "stops": {"initial": "clôture − $init_stop_atr volatilités",
              "trailing": "plus haute clôture − $trail_atr volatilités ($bear_trail_atr en régime baissier)",
              "moves": "ne descend jamais"},
    "sizing": {"method": "FIXED_FRACTIONAL", "risk_pct": "$risk_pct",
               "to": "stop initial, frais et glissement compris", "max_position_pct": "$max_position_pct",
               "min_notional_usdt": 10, "drawdown_throttle": []},
    "constraints": {"max_positions": "$max_positions", "max_total_risk": "$max_total_risk",
                    "max_position_pct": "$max_position_pct", "leverage": 1.0, "short": False},
    "costs": {"model": "FIXED_PER_SIDE", "fee": "$fee", "slippage": "$slippage"},
    "liquidity": {"min_volume_usd": "$min_volume_usd", "window_days": 30},
    "parameters": PARAMETERS,
    "dependencies": {"features": sorted(FEATURES), "data": ["MarketData.v1"],
                     "code": ["trendguard/trend_strategy.py"], "regime": "trend_strategy.btc_regime"},
    "evidence": ["docs/ROBUSTESSE.md", "docs/EXAMEN.md", "docs/STRATEGIES.md"],
    "locks": {"features": "967db492c7a14287", "rules": "f0c07811207c5e64"},
}
PRUDENT: Dict[str, Any] = dict(
    copy.deepcopy(TREND), id="trendguard.cassure.prudent", parent="trendguard.cassure",
    name="Cassure, momentum et régime, profil prudent (risque divisé par 2 au-delà de −10 %)",
    sizing=dict(copy.deepcopy(TREND["sizing"]), drawdown_throttle=[[0.10, 0.5]]))
REGISTRY: Tuple[Dict[str, Any], ...] = (TREND, PRUDENT)


def spec_hash(spec: Dict[str, Any]) -> str:
    """Empreinte de la fiche (JSON canonique)."""
    body = json.dumps(spec, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def spec_for(p: ts.TrendParams) -> Dict[str, Any]:
    """La fiche qui décrit les réglages en vigueur (profil prudent ou non)."""
    throttle = [list(x) for x in p.dd_throttle]
    for spec in REGISTRY:
        if spec["sizing"].get("drawdown_throttle", []) == throttle:
            return spec
    return dict(copy.deepcopy(TREND), id="trendguard.cassure.palier", parent="trendguard.cassure",
                status="DEVELOPMENT", sizing=dict(copy.deepcopy(TREND["sizing"]), drawdown_throttle=throttle))


def transition(current: str, new: str) -> str:
    """Passage d'un statut à un autre (§5) ; un passage non prévu est
    refusé (jamais de brouillon vers le réel)."""
    if current not in TRANSITIONS or new not in TRANSITIONS:
        raise ValueError(f"statut inconnu : {current if current not in TRANSITIONS else new}")
    if new not in TRANSITIONS[current]:
        raise ValueError(f"passage refusé : {current} → {new} (permis : {', '.join(TRANSITIONS[current]) or 'aucun'})")
    return new


# ---------- Langage déclaratif et compilation sûre (§6) ----------

@dataclasses.dataclass(frozen=True)
class Condition:
    left: Tuple[str, Any]
    op: str
    right: Tuple[str, Any]
    code: str
    label: str


def params_of(p: ts.TrendParams) -> Dict[str, float]:
    """Les réglages numériques, nommés comme dans la fiche ($nom)."""
    return {f.name: getattr(p, f.name) for f in dataclasses.fields(p)
            if isinstance(getattr(p, f.name), (int, float)) and not isinstance(getattr(p, f.name), bool)}


def _operand(x: Any, params: Dict[str, float]) -> Tuple[str, Any]:
    if isinstance(x, bool):
        raise SpecError(f"opérande refusé : {x!r} (vrai/faux n'est pas un nombre)")
    if isinstance(x, (int, float)):
        if not math.isfinite(x):
            raise SpecError("nombre non fini refusé")
        return ("number", float(x))
    if isinstance(x, str) and x.startswith("$"):
        if x[1:] not in params:
            raise SpecError(f"paramètre inconnu : {x}")
        return ("number", float(params[x[1:]]))
    if isinstance(x, str) and x in FEATURES:
        return ("feature", x)
    raise SpecError(f"opérande refusé : {x!r} (indicateurs permis : {', '.join(sorted(FEATURES))})")


def _compile(node: Any, params: Dict[str, float], depth: int, count: List[int]) -> Tuple[str, Any]:
    if depth > MAX_DEPTH:
        raise SpecError(f"règles trop imbriquées (au plus {MAX_DEPTH} niveaux)")
    if not isinstance(node, dict):
        raise SpecError(f"règle illisible : {node!r}")
    if "if" in node:
        cond = node["if"]
        if not (isinstance(cond, list) and len(cond) == 3):
            raise SpecError(f"condition illisible : {cond!r} ([gauche, opérateur, droite] attendu)")
        left, op, right = cond
        if op not in OPERATORS:
            raise SpecError(f"opérateur refusé : {op!r} (permis : {' '.join(OPERATORS)})")
        code, label = node.get("code"), node.get("label")
        if code not in STRATEGY_REASONS:
            raise SpecError(f"code de raison inconnu : {code!r}")
        if not isinstance(label, str) or not label:
            raise SpecError("chaque condition dit ce qu'elle vérifie (label)")
        count[0] += 1
        if count[0] > MAX_CONDITIONS:
            raise SpecError(f"trop de conditions (au plus {MAX_CONDITIONS})")
        return ("cond", Condition(_operand(left, params), op, _operand(right, params), code, label))
    kinds = [k for k in ("all", "any") if k in node]
    if len(kinds) != 1 or not isinstance(node[kinds[0]], list) or not node[kinds[0]]:
        raise SpecError("une règle est une condition, « all » ou « any » avec une liste non vide")
    return (kinds[0], tuple(_compile(n, params, depth + 1, count) for n in node[kinds[0]]))


def _used(rule: Tuple[str, Any]) -> List[Condition]:
    if rule[0] == "cond":
        return [rule[1]]
    return [c for child in rule[1] for c in _used(child)]


def _value(operand: Tuple[str, Any], values: Dict[str, Any]) -> Any:
    kind, v = operand
    return np.asarray(values[v], dtype=float) if kind == "feature" else v


def _eval(rule: Tuple[str, Any], values: Dict[str, Any], out: List[Tuple[Condition, Any]]) -> Any:
    """Toutes les conditions sont évaluées (pour expliquer chacune) ; une
    valeur inconnue (absente, infinie) rend la condition fausse."""
    if rule[0] == "cond":
        c = rule[1]
        left, right = _value(c.left, values), _value(c.right, values)
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(left) & np.isfinite(right) & OPERATORS[c.op](left, right)
        out.append((c, ok))
        return ok
    oks = [_eval(child, values, out) for child in rule[1]]
    return np.logical_and.reduce(oks) if rule[0] == "all" else np.logical_or.reduce(oks)


@dataclasses.dataclass(frozen=True)
class Compiled:
    """Fiche compilée : l'entrée évaluable sur un instantané (nombres) ou
    sur des séries entières (tableaux), sans eval ni code de la fiche."""
    spec: Dict[str, Any]
    params: Dict[str, float]
    entry: Tuple[str, Any]
    regimes: Tuple[str, ...]

    def evaluate(self, values: Dict[str, Any], bull: Any) -> Tuple[Any, List[Tuple[Condition, Any]], Any]:
        """(entrée permise, résultat de chaque condition, régime permis)."""
        out: List[Tuple[Condition, Any]] = []
        ok = _eval(self.entry, values, out)
        b = np.asarray(bull, dtype=bool)
        regime_ok = (b if self.regimes == ("BULL",) else ~b if self.regimes == ("BEAR",)
                     else np.ones_like(b, dtype=bool))
        return ok & regime_ok, out, regime_ok

    def conditions(self) -> List[Condition]:
        return _used(self.entry)


def compile_spec(spec: Dict[str, Any], p: ts.TrendParams) -> Compiled:
    """Compile la fiche avec les réglages `p` ; SpecError si elle sort du
    langage permis (indicateur, opérateur, paramètre ou code inconnus,
    imbrication ou taille excessives)."""
    params = params_of(p)
    regimes = tuple((spec.get("regime") or {}).get("allowed") or ())
    if not regimes or any(r not in REGIMES for r in regimes):
        raise SpecError(f"régimes permis attendus parmi {', '.join(REGIMES)}")
    if (spec.get("regime") or {}).get("code") not in STRATEGY_REASONS:
        raise SpecError("le régime dit son code de raison")
    entry = _compile(spec.get("entry"), params, 0, [0])
    return Compiled(spec, params, entry, tuple(sorted(set(regimes))))


def label(text: str, params: Dict[str, float]) -> str:
    """Le texte d'une condition, réglages remplacés par leur valeur."""
    return re.sub(r"\$([a-z_0-9]+)", lambda m: fr_plain(params[m.group(1)]) if m.group(1) in params
                  else m.group(0), text)


def _num(x: Any) -> str:
    x = float(x)
    if not math.isfinite(x):
        return "inconnu"
    return fr_plain(float(f"{x:.4g}"))


def _detail(c: Condition, values: Dict[str, Any]) -> str:
    left = _num(values[c.left[1]]) if c.left[0] == "feature" else _num(c.left[1])
    right = _num(values[c.right[1]]) if c.right[0] == "feature" else _num(c.right[1])
    return f"{left} {c.op} {right}"


# ---------- Validation (§41, §66) ----------

def _locks_now() -> Dict[str, str]:
    return {"features": code_lock(LOCKED_FEATURES), "rules": code_lock(LOCKED_RULES)}


def _schema(spec: Dict[str, Any]) -> List[str]:
    out = [f"champ requis absent : {k}" for k in REQUIRED if k not in spec]
    if not SEMVER.match(str(spec.get("version", ""))):
        out.append("version MAJEUR.MINEUR.CORRECTIF attendue")
    if spec.get("family") not in FAMILIES:
        out.append(f"famille inconnue : {spec.get('family')!r}")
    if spec.get("status") not in STATUSES:
        out.append(f"statut inconnu : {spec.get('status')!r}")
    return out


def _resolve(x: Any, params: Dict[str, float]) -> Optional[float]:
    if isinstance(x, str) and x.startswith("$"):
        return params.get(x[1:])
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _check_params(spec: Dict[str, Any], p: ts.TrendParams) -> List[str]:
    out = []
    known = params_of(p)
    for name, d in (spec.get("parameters") or {}).items():
        if name not in known:
            out.append(f"{name} : réglage inconnu de la règle")
            continue
        if not d["min"] <= d["default"] <= d["max"]:
            out.append(f"{name} : défaut {fr_plain(d['default'])} hors de sa plage")
        if not d["min"] <= known[name] <= d["max"]:
            out.append(f"{name} : {fr_plain(known[name])} en vigueur, hors de la plage "
                       f"{'permise' if d.get('kind') == 'RISK' else 'validée'} [{fr_plain(d['min'])} ; "
                       f"{fr_plain(d['max'])}]")
    try:
        p.validate()
    except ValueError as e:
        out.append(str(e))
    return out


def _check_money(spec: Dict[str, Any], params: Dict[str, float]) -> Tuple[List[str], List[str], List[str]]:
    costs, liquidity, limits = [], [], []
    for k in ("fee", "slippage"):
        v = _resolve((spec.get("costs") or {}).get(k), params)
        if v is None or not 0 < v < 0.05:
            costs.append(f"{k} : coût absent ou irréaliste")
    v = _resolve((spec.get("liquidity") or {}).get("min_volume_usd"), params)
    if v is None or v <= 0:
        liquidity.append("volume minimum absent")
    c = spec.get("constraints") or {}
    pos, tot = _resolve(c.get("max_positions"), params), _resolve(c.get("max_total_risk"), params)
    size, risk = _resolve(c.get("max_position_pct"), params), params.get("risk_pct")
    if pos is None or pos < 1:
        limits.append("nombre de positions absent")
    if tot is None or not 0 < tot <= 0.10 or (risk is not None and risk > tot):
        limits.append("risque cumulé absent ou incohérent")
    if size is None or not 0 < size <= 1:
        limits.append("taille d'une position absente")
    if c.get("leverage") != 1.0 or c.get("short") is not False:
        limits.append("levier ou vente à découvert : hors du cadre (Binance Spot, achat seul)")
    return costs, liquidity, limits


def leakage(close: pd.DataFrame, volume: Optional[pd.DataFrame], p: ts.TrendParams,
            days: int = 8, assets: int = 3) -> List[str]:
    """Fuite vers le futur (§29) : indicateurs et régime recalculés sur les
    seules données connues chaque jour testé (jours où la crypto cote) ; ils
    doivent être identiques (finance.leakage_violations)."""
    def picks(s: pd.Series) -> List[pd.Timestamp]:
        valid = s.index[s.notna()]
        if len(valid) < 60:
            return []
        return sorted({valid[int(k)] for k in np.linspace(30, len(valid) - 2, days)})

    out: List[str] = []
    for a in [a for a in close.columns if a != "btc"][:assets] + (["btc"] if "btc" in close else []):
        has_volume = volume is not None and a in volume
        data = pd.DataFrame({"close": close[a], "volume": volume[a] if has_volume else np.nan})
        out += [f"{a.upper()} {x}" for x in finance.leakage_violations(
            lambda d, known=has_volume: ts.asset_features(d["close"], p, d["volume"] if known else None),
            data, picks(close[a]))]
    if "btc" in close:
        out += [f"régime {x}" for x in finance.leakage_violations(
            lambda d: ts.btc_regime(d["close"], p).astype(float).to_frame("bull"), close[["btc"]].rename(
                columns={"btc": "close"}), picks(close["btc"]))]
    return out


def equivalence(compiled: Compiled, close: pd.DataFrame, volume: Optional[pd.DataFrame],
                p: ts.TrendParams) -> Dict[str, Any]:
    """La fiche contre la règle exécutée, sur chaque crypto et chaque jour :
    mêmes signaux d'achat (régime compris) ; les écarts sont comptés."""
    cols, reg = ts.precompute(close, volume if volume is not None else pd.DataFrame(index=close.index), p)
    checked = signals = 0
    diffs: List[str] = []
    for a, c in cols.items():
        ok, _out, _r = compiled.evaluate(c, reg)
        for i in range(len(reg)):
            ref = bool(reg[i]) and ts.entry_signal({k: c[k][i] for k in c}, p)
            checked += 1
            signals += int(ref)
            if bool(ok[i]) != ref and len(diffs) < 50:
                diffs.append(f"{a.upper()} {close.index[i].date()} : fiche {bool(ok[i])}, règle {ref}")
    return {"checked": checked, "signals": signals, "mismatches": len(diffs), "examples": diffs[:5]}


def validate(spec: Dict[str, Any], p: ts.TrendParams, close: Optional[pd.DataFrame] = None,
             volume: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """Validation de la fiche (§41) : chaque étape, conforme ou non, avec son
    détail. PRÊTE POUR LE BACKTEST (READY_FOR_BACKTEST, §66) seulement si
    toutes le sont ; sans données, la fuite et l'équivalence ne sont pas
    vérifiables : la fiche reste bloquée."""
    steps: List[Tuple[str, bool, str]] = []
    bad = _schema(spec)
    steps.append(("Schéma", not bad, " ; ".join(bad) or f"{len(REQUIRED)} rubriques, version {spec.get('version')}"))
    compiled: Optional[Compiled] = None
    try:
        compiled = compile_spec(spec, p)
        steps.append(("Langage sûr", True, f"{len(compiled.conditions())} conditions, opérateurs et indicateurs "
                                           "de la liste blanche, aucun code exécuté"))
    except SpecError as e:
        steps.append(("Langage sûr", False, str(e)))
    params = params_of(p)
    if compiled is not None:
        used = {x[1] for c in compiled.conditions() for x in (c.left, c.right) if x[0] == "feature"}
        missing = sorted(used - set((spec.get("dependencies") or {}).get("features") or ()))
        steps.append(("Dépendances", not missing, "indicateurs non déclarés : " + ", ".join(missing) if missing
                      else f"{len(used)} indicateurs déclarés, produits par trend_strategy.asset_features"))
    bad = _check_params(spec, p)
    chosen = [f"{k} {fr_plain(params[k])} (recherche : {fr_plain(d['default'])})"
              for k, d in (spec.get("parameters") or {}).items()
              if d.get("kind") == "RISK" and k in params and params[k] != d["default"]]
    steps.append(("Paramètres", not bad, " ; ".join(bad) or f"{len(spec.get('parameters') or {})} réglages dans "
                  "leurs plages" + (" ; réglages de risque choisis par vous : " + ", ".join(chosen) if chosen else "")))
    costs, liquidity, limits = _check_money(spec, params)
    steps.append(("Coûts", not costs, " ; ".join(costs) or f"frais {fr(params['fee'] * 100, '.2f')} % et glissement "
                                                          f"{fr(params['slippage'] * 100, '.2f')} % par côté"))
    steps.append(("Liquidité", not liquidity, " ; ".join(liquidity) or
                  f"volume moyen de 30 jours d'au moins {fr_plain(params['min_volume_usd'])} dollars"))
    steps.append(("Contraintes", not limits, " ; ".join(limits) or "positions, risque cumulé, taille ; sans levier "
                                                                   "ni vente à découvert"))
    now = _locks_now()
    locks = spec.get("locks") or {}
    changed = [k for k in ("features", "rules") if locks.get(k) != now[k]]
    steps.append(("Verrous du code", not changed,
                  "code modifié depuis la version " + str(spec.get("version")) + " : " + ", ".join(
                      {"features": "indicateurs", "rules": "règles"}[k] for k in changed) +
                  " ; nouvelle version de la fiche requise" if changed else "indicateurs et règles inchangés"))
    data = None
    if close is not None and compiled is not None:
        sample = {k: np.array([np.nan, 1.0, 2.0, np.inf, 3.0]) for k in FEATURES}
        a, _o, _r = compiled.evaluate(sample, np.array([True, True, False, True, True]))
        b, _o, _r = compiled.evaluate(sample, np.array([True, True, False, True, True]))
        steps.append(("Déterminisme", bool(np.array_equal(a, b)), "mêmes entrées, mêmes décisions"))
        leaks = leakage(close, volume, p)
        steps.append(("Fuite vers le futur", not leaks, " ; ".join(leaks[:3]) or
                      "indicateurs et régime identiques sur les seules données connues"))
        eq = equivalence(compiled, close, volume, p)
        steps.append(("Équivalence avec la règle exécutée", eq["mismatches"] == 0,
                      f"{fr_plain(eq['checked'])} cas, {fr_plain(eq['signals'])} signaux, "
                      f"{eq['mismatches']} écart(s)" + (" : " + " ; ".join(eq["examples"]) if eq["examples"] else "")))
        data = fingerprint(close, volume, str(close.index[-1].date()))
        steps.append(("Données verrouillées", True, f"{data['first']} → {data['last']}, empreinte {data['hash']}"))
    else:
        steps.append(("Fuite vers le futur", False, "non vérifiable sans données"))
        steps.append(("Équivalence avec la règle exécutée", False, "non vérifiable sans données"))
    ok = all(s[1] for s in steps)
    return {"status": "READY_FOR_BACKTEST" if ok else "BLOCKED", "steps": steps, "spec_id": spec.get("id"),
            "version": spec.get("version"), "spec_hash": spec_hash(spec), "data": data,
            "engine": ENGINE_VERSION}


# ---------- Décisions par crypto (§9, §37-39) ----------

def instrument(asset: str) -> str:
    return finance.instrument_id(asset)


def decide(compiled: Compiled, day: str, asset: str, s: Dict[str, float], bull: bool, held: bool,
           exiting: bool, bought: Optional[Dict[str, Any]], equity: float, p: ts.TrendParams) -> StrategyDecision:
    """La décision de la règle pour une crypto ce jour-là (StrategyDecision,
    §39) : candidate (toutes les conditions), pas de trade (les conditions
    fausses, dites), position tenue ou sortie due. Le coût attendu est
    l'aller-retour (frais et glissement, deux côtés)."""
    ok, out, regime_ok = compiled.evaluate(s, bull)
    rows = tuple((label(c.label, compiled.params), bool(r), _detail(c, s)) for c, r in out)
    cost = 2 * (p.fee + p.slippage)
    mom = s.get("mom")
    signal = float(mom) if mom is not None and math.isfinite(float(mom)) else None
    regime = "BULL" if bull else "BEAR"
    if held:
        return StrategyDecision(compiled.spec["id"], compiled.spec["version"], instrument(asset), day, regime,
                                "LONG", bool(ok), exiting, rows, signal, cost, None, None, "IN_POSITION",
                                "EXIT" if exiting else "HOLD")
    if bool(ok):
        size = stop = None
        if bought:
            size = round(min(1.0, float(bought.get("cost", 0.0)) / equity), 6) if equity > 0 else None
            stop = float(bought["stop"]) if bought.get("stop") is not None else None
        elif s.get("vol") is not None:
            stop = ts.initial_stop(float(s["close"]), float(s["vol"]), p)
        return StrategyDecision(compiled.spec["id"], compiled.spec["version"], instrument(asset), day, regime,
                                "LONG", True, False, rows, signal, cost, size, stop, "STRATEGY_READY",
                                "TRADE_CANDIDATE")
    codes, texts = [], []
    if not bool(regime_ok):
        codes.append(compiled.spec["regime"]["code"])
        texts.append("non : " + label(compiled.spec["regime"]["label"], compiled.params))
    for (c, r), (text, _ok, detail) in zip(out, rows):
        if not bool(r) and c.code not in codes:
            codes.append(c.code)
            texts.append(f"non : {text} ({detail})")
    return StrategyDecision(compiled.spec["id"], compiled.spec["version"], instrument(asset), day, regime,
                            "NEUTRAL", False, False, rows, signal, cost, None, None, "NO_TRADE", "NO_TRADE",
                            tuple(codes), tuple(texts))


def day_view(day: str, snap: Dict[str, Dict[str, float]], bull: bool, held: Set[str], exiting: Set[str],
             bought: Dict[str, Dict[str, Any]], equity: float, p: ts.TrendParams) -> Dict[str, Any]:
    """Toutes les cryptos du jour vues par la fiche, et comparées à la règle
    exécutée (écart = fiche à corriger, signalé)."""
    spec = spec_for(p)
    compiled = compile_spec(spec, p)
    assets: Dict[str, Dict[str, Any]] = {}
    counts: Dict[str, int] = {}
    mismatch = []
    for a in sorted(snap):
        d = decide(compiled, day, a, snap[a], bull, a in held, a in exiting, bought.get(a), equity, p)
        if a not in held and (d.decision == "TRADE_CANDIDATE") != (bool(bull) and ts.entry_signal(snap[a], p)):
            mismatch.append(a)
        for code in d.reasons:
            counts[code] = counts.get(code, 0) + 1
        assets[a] = {"d": d.decision, "why": list(d.reason_texts[:3]), "size": d.position_size}
    return {"day": day, "id": spec["id"], "version": spec["version"], "hash": spec_hash(spec),
            "regime": "BULL" if bull else "BEAR", "assets": assets,
            "candidates": [a for a, x in assets.items() if x["d"] == "TRADE_CANDIDATE"],
            "bought": sorted(a for a in bought if a in assets),
            "no_trade": sum(1 for x in assets.values() if x["d"] == "NO_TRADE"), "reasons": counts,
            "held": sum(1 for x in assets.values() if x["d"] in ("HOLD", "EXIT")), "mismatch": mismatch}


def explain(view: Dict[str, Any], asset: str) -> str:
    """Pourquoi la règle achète, n'achète pas, garde ou vend une crypto."""
    x = (view.get("assets") or {}).get(asset)
    if not x:
        return f"{asset.upper()} : pas évaluée à la décision du {view.get('day')}."
    if x["d"] == "TRADE_CANDIDATE":
        bought = asset in (view.get("bought") or [])
        return (f"{asset.upper()} : toutes les conditions d'achat remplies ; "
                + ("achetée (porte d'exécution d'accord)." if bought else
                   "candidate, non achetée (place, budget de risque ou porte d'exécution)."))
    if x["d"] in ("HOLD", "EXIT"):
        return f"{asset.upper()} : détenue ; " + ("vendue, clôture sous le stop." if x["d"] == "EXIT"
                                                  else "gardée tant que la clôture reste au-dessus du stop.")
    return f"{asset.upper()} : pas de trade — " + " ; ".join(x["why"]) + "."


# ---------- Kelly, sur-ajustement (§15, §27) ----------

def kelly(win_rate: float, payoff: float, risk_pct: float, fractions: Sequence[float] = (0.10, 0.25, 0.50, 1.00),
          cap: float = 0.02) -> Dict[str, Any]:
    """Kelly (§15) : f* = (b·p − q) / b, en part du capital risquée jusqu'au
    stop. Jamais le Kelly complet : chaque fraction est plafonnée (2 %, la
    limite de la règle), et elle suppose des trades indépendants, ce que des
    cryptos corrélées ne sont pas. Une mesure : la taille reste celle de la
    règle."""
    q = 1 - win_rate
    full = (payoff * win_rate - q) / payoff if payoff > 0 else 0.0
    rows = [{"fraction": f, "risk": max(0.0, full * f), "capped": min(max(0.0, full * f), cap)} for f in fractions]
    return {"full": full, "rows": rows, "cap": cap, "in_force": risk_pct,
            "times_below_full": full / risk_pct if risk_pct > 0 and full > 0 else None}


def overfitting_risk(n_params: int, n_rules: int, trades: Optional[int], oos_ratio: Optional[float],
                     plateau_share: Optional[float]) -> Dict[str, Any]:
    """Risque de sur-ajustement (§27) : trop de réglages ou de règles, trop
    peu de trades, chute hors échantillon (Sharpe hors échantillon divisé par
    Sharpe d'apprentissage), réglages voisins qui échouent. LOW, MEDIUM ou
    HIGH, avec les raisons."""
    points, reasons = 0, []
    if n_params > 8:
        points += 1
        reasons.append(f"{n_params} réglages ajustés (plus de 8)")
    if n_rules > 10:
        points += 1
        reasons.append(f"{n_rules} règles (plus de 10)")
    if trades is None or trades < 100:
        points += 2 if trades is None or trades < 30 else 1
        reasons.append(f"{trades if trades is not None else 'aucun'} trade(s) seulement")
    if oos_ratio is None:
        points += 1
        reasons.append("pas de mesure hors échantillon")
    elif oos_ratio < 0.5:
        points += 2
        reasons.append(f"hors échantillon, Sharpe à {fr(oos_ratio * 100, '.0f')} % de l'apprentissage")
    elif oos_ratio < 0.75:
        points += 1
        reasons.append(f"hors échantillon, Sharpe à {fr(oos_ratio * 100, '.0f')} % de l'apprentissage")
    if plateau_share is not None and plateau_share < 0.8:
        points += 2 if plateau_share < 0.5 else 1
        reasons.append(f"{fr(plateau_share * 100, '.0f')} % seulement des réglages voisins gagnants")
    level = "LOW" if points == 0 else "MEDIUM" if points <= 2 else "HIGH"
    return {"level": level, "points": points, "reasons": reasons}


# ---------- Rendu et commande ----------

def render_spec(spec: Dict[str, Any], p: ts.TrendParams) -> str:
    """La fiche en clair : règles, stops, taille, contraintes, coûts,
    réglages (plage et valeur en vigueur), verrous."""
    params = params_of(p)
    c = compile_spec(spec, p)
    lines = [f"# {spec['name']}", "",
             f"Fiche `{spec['id']}` version {spec['version']} (empreinte {spec_hash(spec)}), famille "
             f"{', '.join(spec.get('families') or [spec['family']])}, statut {spec['status']} "
             f"({STATUS_LABELS.get(spec['status'], spec['status'].lower())}).", "", spec["description"], "",
             "Achat (toutes les conditions) :",
             f"- régime : {label(spec['regime']['label'], params)}"]
    lines += [f"- {label(x.label, params)}" for x in c.conditions()]
    lines += [f"- classement : {spec['rank']['label']}", "", "Vente (une seule suffit) :"]
    lines += [f"- {x['label']}" for x in spec["exit"]["any"]]
    st, sz, ct = spec["stops"], spec["sizing"], spec["constraints"]
    lines += ["", f"Stops : initial {label(st['initial'], params)} ; suiveur {label(st['trailing'], params)} ; "
                  f"{st['moves']}.",
              f"Taille : {fr(params['risk_pct'] * 100, 'g')} % du capital risqué jusqu'au {sz['to']}, "
              f"{fr(params['max_position_pct'] * 100, 'g')} % du capital au plus par position, "
              f"{sz['min_notional_usdt']} USDT au moins"
              + (" ; profil prudent : " + ", ".join(f"risque × {fr_plain(m)} au-delà de −{fr_plain(t * 100)} %"
                                                    for t, m in sz["drawdown_throttle"]) if sz["drawdown_throttle"]
                 else "") + ".",
              f"Contraintes : {fr_plain(params['max_positions'])} positions au plus, risque cumulé "
              f"{fr(params['max_total_risk'] * 100, 'g')} % au plus, sans levier, sans vente à découvert "
              f"(levier {fr_plain(ct['leverage'])}).",
              f"Coûts : frais {fr(params['fee'] * 100, 'g')} % et glissement {fr(params['slippage'] * 100, 'g')} % "
              "par côté.", "", "| Réglage | Plage (validée, ou permise pour le risque) | Pas | Recherche | En vigueur "
              "| Rôle |", "| --- | --- | --- | --- | --- | --- |"]
    for name, d in spec["parameters"].items():
        lines.append(f"| `{name}` | {fr_plain(d['min'])} à {fr_plain(d['max'])} {d['unit']} | {fr_plain(d['step'])} | "
                     f"{fr_plain(d['default'])} | "
                     f"{fr_plain(params.get(name, d['default']))} | {d['description']} |")
    lines += ["", f"Verrous : indicateurs {spec['locks']['features']}, règles {spec['locks']['rules']}."]
    return "\n".join(lines)


def render_validation(v: Dict[str, Any]) -> str:
    lines = [f"Validation de la fiche {v['spec_id']} v{v['version']} : "
             + ("PRÊTE POUR LE BACKTEST" if v["status"] == "READY_FOR_BACKTEST" else "BLOQUÉE")]
    lines += [f"- {'✓' if ok else '✗'} {name} : {detail}" for name, ok, detail in v["steps"]]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `regle` : fiche (défaut), valider, etats."""
    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    ap = argparse.ArgumentParser(description="Moteur de stratégie de TrendGuard : la règle en fiche déclarative")
    ap.add_argument("action", nargs="?", default="fiche", choices=["fiche", "valider", "etats"])
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    p = params_for(g)
    spec = spec_for(p)
    if args.action == "etats":
        for s in STATUSES:
            print(f"{s} → {', '.join(TRANSITIONS[s]) or '(fin)'}")
        return 0
    if args.action == "fiche":
        print(render_spec(spec, p))
        print("\nRegistre : " + " ; ".join(f"{x['id']} v{x['version']} ({x['status']})" for x in REGISTRY))
        return 0
    close, volume = load_history(args.cache, list(g.universe))
    v = validate(spec, p, close, volume)
    print(render_validation(v))
    for start, end in (("2018-01-01", "2022-12-31"), ("2023-01-01", str(close.index[-1].date()))):
        m = ts.backtest(close, volume, p, start, end).metrics
        k = kelly(m["win_rate_pct"] / 100, m["payoff"], p.risk_pct)
        print(f"Kelly {start[:4]}-{end[:4]} : complet {fr(k['full'] * 100, '.1f')} % du capital par trade, "
              f"quart {fr(k['rows'][1]['risk'] * 100, '.1f')} % (plafonné à {fr(k['cap'] * 100, 'g')} %) ; "
              f"la règle risque {fr(p.risk_pct * 100, 'g')} % : la taille ne change pas.")
    return 0 if v["status"] == "READY_FOR_BACKTEST" else 1
