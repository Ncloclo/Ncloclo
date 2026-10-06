"""
Cœur d'intelligence financière (prompt maître, étape 7 ; docs/FINANCE.md) :
le « docteur en finance » du bot, qui transforme des bougies en analyse,
sans jamais décider à la place de la règle ni passer d'ordre.

    instruments → données → qualité → indicateurs versionnés (sans fuite
    vers le futur) → technique, quantitatif, sentiment, régime, liens entre
    cryptos, calendrier → scénarios et prévisions de fréquence → signal de
    la règle → classement indicatif → « pas de trade », à surveiller ou
    signal → explication (preuves, risques, contradictions, confiance par
    niveau, conditions qui invalideraient l'analyse)

Règles :
- chaque indicateur porte sa version et la fin des données qui l'ont
  produit ; un test vérifie qu'aucun ne regarde le futur (recalculé sur les
  seules données connues ce jour-là, il est identique) ;
- une prévision est une fréquence observée dans un passé comparable (mêmes
  conditions de marché, fenêtres sans chevauchement), avec son intervalle et
  son nombre de cas : jamais un prix annoncé ; chacune est gardée puis
  comparée au résultat (score de Brier, calibration) ;
- une analyse ne vaut jamais autorisation : la règle du bot, puis la porte
  d'exécution, décident ; « pas de trade » est une réponse normale ;
- ce qui ne s'applique pas aux cryptos (bilans, valorisation d'entreprise,
  séries macroéconomiques) est dit « sans objet », jamais simulé.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import v29

from . import regimes
from . import trend_strategy as ts
from .contrats import (
    Confidence,
    FinancialAnalysis,
    FinancialSignal,
    Forecast,
    Instrument,
    Scenario,
    ScenarioSet,
)
from .texte import fr

FEATURE_VERSION = "1.0.0"         # changer un calcul change la version (un test tient les valeurs)
FORECAST_VERSION = "frequence-1.0.0"
ANALYSIS_VERSION = "analyse-1.0.0"
OPPORTUNITY_VERSION = "opportunite-1.0.0"
HORIZON = 30                      # jours
MIN_CASES = 12                    # cas comparables en dessous desquels on ne prévoit pas
SANS_OBJET = ("analyse fondamentale et valorisation (bilans, DCF, P/E…) : sans objet pour des cryptos, qui "
              "n'ont ni bilan ni bénéfice ; séries macroéconomiques (PIB, inflation) : non collectées, le "
              "calendrier des grandes annonces en tient lieu")
# Indicateurs (nom, unité, profondeur en jours, méthode) : la carte du magasin.
FEATURES: Tuple[Tuple[str, str, int, str], ...] = (
    ("ret_1d", "fraction (log)", 1, "rendement logarithmique d'un jour"),
    ("momentum_90d", "fraction (log)", 90, "rendement logarithmique sur 90 jours"),
    ("breakout_gap_30d", "fraction", 30, "écart de la clôture au plus haut des 30 clôtures précédentes"),
    ("volatility_30d", "fraction par an", 30, "écart-type des rendements journaliers × √365"),
    ("ema_ratio_50_200", "fraction", 200, "moyenne exponentielle de 50 jours / celle de 200 jours − 1"),
    ("rsi_14", "0 à 100", 14, "RSI de Wilder"),
    ("drawdown_365d", "fraction", 365, "baisse depuis le plus haut d'un an"),
    ("volume_zscore_30d", "écarts-types", 30, "volume du jour face à ses 30 derniers jours"),
    ("beta_btc_90d", "sans unité", 90, "bêta des rendements face à BTC"),
    ("corr_btc_90d", "−1 à +1", 90, "corrélation des rendements avec BTC"),
    ("sharpe_365d", "sans unité", 365, "rendement moyen / écart-type, annualisé"),
    ("sortino_365d", "sans unité", 365, "rendement moyen / écart-type des baisses, annualisé"),
    ("skew_365d", "sans unité", 365, "asymétrie des rendements journaliers"),
    ("kurtosis_365d", "sans unité", 365, "aplatissement (excès) des rendements journaliers"),
    ("var95_1d", "fraction (log)", 365, "rendement d'un jour dépassé à la baisse 1 jour sur 20"),
    ("cvar95_1d", "fraction (log)", 365, "rendement moyen de ces jours-là"),
)
FEATURE_NAMES = tuple(name for name, _u, _l, _m in FEATURES)
CLASSES: Tuple[Tuple[str, float, float], ...] = (     # rendement simple à l'horizon
    ("EXTREME", -math.inf, -0.40), ("STRESS", -0.40, -0.20), ("BEAR", -0.20, -0.05),
    ("BASE", -0.05, 0.10), ("BULL", 0.10, math.inf))
SCENARIO_LABELS = {"EXTREME": "fort recul (−40 % ou pire)", "STRESS": "crise (−40 à −20 %)",
                   "BEAR": "baisse (−20 à −5 %)", "BASE": "central (−5 à +10 %)", "BULL": "hausse (+10 % ou plus)"}
CONFIDENCE_LABELS = {"DATA_CONFIDENCE": "données", "MODEL_CONFIDENCE": "modèle", "SIGNAL_CONFIDENCE": "signal",
                     "FORECAST_CONFIDENCE": "prévision", "DECISION_CONFIDENCE": "décision"}
INVALIDATION = {"BULL": "BTC repasse sous sa moyenne de 150 jours, ou la crypto sous son stop",
                "BASE": "cassure nette d'un côté ou de l'autre",
                "BEAR": "nouveau plus haut de 30 jours avec momentum positif",
                "STRESS": "volatilité qui retombe sous sa médiane d'un an",
                "EXTREME": "aucun signe de crise de liquidité ni de contagion"}
OPPORTUNITY_WEIGHTS = {"technical": 0.25, "quant": 0.20, "regime": 0.15, "risk": 0.15, "sentiment": 0.10,
                       "liquidity": 0.10, "data_quality": 0.05}
HIGH_VOL = 1.5                    # volatilité annualisée jugée extrême (150 %)
TINY = 1e-12                      # dispersion sous laquelle un ratio n'a pas de sens (non mesurable)


# ---------- Instruments (phase 7.1) ----------

def instrument_id(base: str, quote: str = "USDT") -> str:
    return f"binance:spot:{base.upper()}-{quote.upper()}"


def instruments(close: pd.DataFrame, quote: str = "USDT", vetoed: Sequence[str] = (),
                markets: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Instrument]:
    """Référentiel des instruments (§8) : identifiant stable, place, classe,
    devise, calendrier (24 h sur 24), première bougie, état ; pas de cotation
    et minimums seulement s'ils sont connus (jamais inventés)."""
    out = []
    for a in close.columns:
        s = close[a]
        first = s.first_valid_index()
        m = (markets or {}).get(a) or {}
        status = "VETO" if a in vetoed else ("NO_DATA" if first is None else "TRADING")
        out.append(Instrument(instrument_id(a, quote), f"{a.upper()}/{quote.upper()}", a.lower(), quote.upper(),
                              "Binance", "CRYPTO", quote.upper(), "UTC", "24/7",
                              None if first is None else str(first.date()), status,
                              m.get("tick_size"), m.get("lot_step"), m.get("min_notional")))
    return out


# ---------- Qualité des données d'un instrument (phase 7.3) ----------

def asset_quality(close: pd.Series, day: str, ohlcv_bad: int = 0) -> Dict[str, Any]:
    """Qualité d'une série (§9) de 0 à 1 : complétude (jours manquants sur
    l'année), fraîcheur (dernière bougie), cohérence (prix nuls, mouvements
    absurdes, bougies incohérentes), fiabilité (cours figés) ; la note est
    leur moyenne."""
    s = close.loc[:pd.Timestamp(day, tz="UTC")].astype(float)
    listed = s.first_valid_index()
    if listed is None:
        return {"quality": 0.0, "completeness": 0.0, "freshness": 0.0, "consistency": 0.0, "reliability": 0.0}
    year = s.loc[listed:].iloc[-365:]
    completeness = 1 - float(year.isna().mean())
    last = year.last_valid_index()
    lag = (pd.Timestamp(day, tz="UTC") - last).days if last is not None else 99
    freshness = max(0.0, 1 - lag / 3)
    v = year.dropna()
    bad = int((v <= 0).sum()) + int((v.pct_change().abs() > 0.9).sum()) + int(ohlcv_bad)
    consistency = max(0.0, 1 - bad / 5)
    stale = int((v.diff() == 0).rolling(3).sum().eq(3).sum())
    reliability = max(0.0, 1 - stale / 10)
    parts = {"completeness": round(completeness, 3), "freshness": round(freshness, 3),
             "consistency": round(consistency, 3), "reliability": round(reliability, 3)}
    return {"quality": round(sum(parts.values()) / 4, 3), **parts}


# ---------- Indicateurs versionnés (phases 7.6, 7.7, 7.9) ----------

def _cvar(x: np.ndarray) -> float:
    x = x[~np.isnan(x)]
    if not len(x):
        return float("nan")
    q = np.quantile(x, 0.05)
    return float(x[x <= q].mean())


def features(close: pd.Series, btc: Optional[pd.Series] = None,
             volume: Optional[pd.Series] = None) -> pd.DataFrame:
    """Indicateurs techniques et quantitatifs d'une crypto, jour par jour,
    tous causaux (fenêtres glissantes vers le passé seulement)."""
    c = close.astype(float)
    r = np.log(c / c.shift(1))
    f = pd.DataFrame(index=c.index)
    f["ret_1d"] = r
    f["momentum_90d"] = np.log(c / c.shift(90))
    f["breakout_gap_30d"] = c / c.shift(1).rolling(30, min_periods=30).max() - 1
    f["volatility_30d"] = r.rolling(30, min_periods=30).std() * math.sqrt(365)
    f["ema_ratio_50_200"] = (c.ewm(span=50, adjust=False, min_periods=50).mean()
                             / c.ewm(span=200, adjust=False, min_periods=200).mean() - 1)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    f["rsi_14"] = 100 - 100 / (1 + gain / loss)
    f["drawdown_365d"] = c / c.rolling(365, min_periods=1).max() - 1
    if volume is not None:
        v = volume.astype(float).reindex(c.index)
        f["volume_zscore_30d"] = (v - v.rolling(30, min_periods=30).mean()) / v.rolling(30, min_periods=30).std()
    else:
        f["volume_zscore_30d"] = np.nan
    if btc is not None:
        b = btc.astype(float).reindex(c.index)
        rb = np.log(b / b.shift(1))
        var_b = rb.rolling(90, min_periods=60).var()
        var_a = r.rolling(90, min_periods=60).var()
        f["beta_btc_90d"] = r.rolling(90, min_periods=60).cov(rb) / var_b.where(var_b > TINY)
        f["corr_btc_90d"] = r.rolling(90, min_periods=60).corr(rb).where((var_a > TINY) & (var_b > TINY))
    else:
        f["beta_btc_90d"] = np.nan
        f["corr_btc_90d"] = np.nan
    roll = r.rolling(365, min_periods=180)
    sd = roll.std()
    f["sharpe_365d"] = roll.mean() / sd.where(sd > TINY) * math.sqrt(365)
    down = np.sqrt((r.clip(upper=0) ** 2).rolling(365, min_periods=180).mean())
    f["sortino_365d"] = roll.mean() / down.where(down > TINY) * math.sqrt(365)
    f["skew_365d"] = roll.skew()
    f["kurtosis_365d"] = roll.kurt()
    f["var95_1d"] = roll.quantile(0.05)
    f["cvar95_1d"] = r.rolling(365, min_periods=180).apply(_cvar, raw=True)
    return f.replace([np.inf, -np.inf], np.nan)


def snapshot(f: pd.DataFrame, day: str) -> Dict[str, Optional[float]]:
    """Les indicateurs d'un jour (None : non mesurable, jamais 0)."""
    try:
        row = f.loc[pd.Timestamp(day, tz="UTC")]
    except KeyError:
        return {k: None for k in FEATURE_NAMES}
    return {k: (None if pd.isna(row[k]) else round(float(row[k]), 6)) for k in FEATURE_NAMES}


def data_cutoff(day: str) -> str:
    """Fin des données d'une bougie journalière (minuit UTC du lendemain)."""
    return (pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1)).isoformat()


# ---------- Anti-fuite (phase 7.8) ----------

def leakage_violations(fn: Callable[[pd.DataFrame], pd.DataFrame], data: pd.DataFrame,
                       days: Sequence[pd.Timestamp], tol: float = 1e-9) -> List[str]:
    """Moteur anti-fuite (§22) : un calcul est sain si, pour chaque jour
    testé, son résultat sur les seules données connues ce jour-là est
    identique à celui calculé avec tout l'historique. Sinon il regarde le
    futur : SIGNAL_INVALID."""
    full = fn(data)
    out = []
    for d in days:
        part = fn(data.loc[:d])
        a, b = full.loc[d], part.loc[d]
        for col in getattr(a, "index", [None]):
            x, y = (a, b) if col is None else (a[col], b[col])
            if isinstance(x, str) or isinstance(y, str):
                same = x == y
            else:
                same = (pd.isna(x) and pd.isna(y)) or (not pd.isna(x) and not pd.isna(y)
                                                       and abs(float(x) - float(y)) <= tol * max(1.0, abs(float(x))))
            if not same:
                out.append(f"{d.date()} {col} : {x} avec l'avenir, {y} sans")
    return out


def outcome_windows(close: pd.Series, regime: pd.Series, day: str,
                    horizon: int = HORIZON) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rendements, pires baisses et volatilités à `horizon` jours observés
    dans le passé, aux dates de même régime de BTC qu'aujourd'hui, en fenêtres
    sans chevauchement, et seulement celles entièrement connues le jour de
    l'analyse (aucune fuite)."""
    end = pd.Timestamp(day, tz="UTC")
    c = close.loc[:end].astype(float).dropna()
    reg = regime.reindex(c.index)
    if c.empty or end not in reg.index or pd.isna(reg.loc[end]):
        return np.array([]), np.array([]), np.array([])
    today = reg.loc[end]
    values = c.values
    rets, dds, vols = [], [], []
    k = len(values) - 1 - horizon            # dernière fenêtre close au plus tard aujourd'hui
    while k >= 0:
        if reg.iloc[k] == today:
            w = values[k:k + horizon + 1]
            rets.append(w[-1] / w[0] - 1)
            dds.append(float(np.min(w / np.maximum.accumulate(w)) - 1))
            lr = np.diff(np.log(w))
            vols.append(float(np.std(lr, ddof=1) * math.sqrt(365)) if len(lr) > 1 else np.nan)
        k -= horizon
    return np.array(rets), np.array(dds), np.array(vols)


# ---------- Prévisions et scénarios (phases 7.14, 7.15) ----------

def _wilson(p: float, n: int, z: float = 1.645) -> Tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def forecast(asset: str, close: pd.Series, regime: pd.Series, day: str, horizon: int = HORIZON,
             target: float = 0.10) -> Optional[Forecast]:
    """Prévision de fréquence (§29) : dans un passé comparable, part des
    fenêtres de `horizon` jours finies en hausse, au-delà de +10 %, avec une
    baisse de plus de 20 % en route ; rendement moyen et intervalle de 80 % ;
    intervalle de confiance de la probabilité (Wilson, 90 %). Moins de 12 cas :
    aucune prévision (il faut plus de données)."""
    rets, dds, _v = outcome_windows(close, regime, day, horizon)
    n = len(rets)
    if n < MIN_CASES:
        return None
    p_up = float((rets > 0).mean())
    lo, hi = _wilson(p_up, n)
    return Forecast(instrument_id(asset), day, horizon, round(p_up, 4), round(float((rets > target).mean()), 4),
                    target, round(float((dds < -0.20).mean()), 4), round(float(rets.mean()), 4),
                    round(float(np.quantile(rets, 0.10)), 4), round(float(np.quantile(rets, 0.90)), 4),
                    round(lo, 4), round(hi, 4), n, FORECAST_VERSION)


def scenarios(asset: str, close: pd.Series, regime: pd.Series, day: str,
              horizon: int = HORIZON) -> Optional[ScenarioSet]:
    """Scénarios (§28) : fort recul, crise, baisse, central, hausse, avec la
    fréquence de chacun dans un passé comparable (leur somme fait 1), le
    rendement, la volatilité et la pire baisse moyens, et ce qui invaliderait
    chacun. Trop peu de cas : aucun scénario."""
    rets, dds, vols = outcome_windows(close, regime, day, horizon)
    n = len(rets)
    if n < MIN_CASES:
        return None
    items = []
    for name, lo, hi in CLASSES:
        m = (rets > lo) & (rets <= hi)
        k = int(m.sum())
        items.append(Scenario(name, k / n, k,
                              None if not k else round(float(rets[m].mean()), 4),
                              None if not k or np.isnan(vols[m]).all() else round(float(np.nanmean(vols[m])), 4),
                              None if not k else round(float(dds[m].mean()), 4), INVALIDATION[name]))
    return ScenarioSet(instrument_id(asset), day, horizon, tuple(items), n, FORECAST_VERSION)


def evaluate_forecast(p_up: float, actual_return: float) -> Dict[str, float]:
    """Comparaison d'une prévision au résultat (§34) : réussite de la
    direction et score de Brier (0 parfait, 0,25 hasard)."""
    up = 1.0 if actual_return > 0 else 0.0
    return {"hit": float((p_up >= 0.5) == bool(up)), "brier": round((p_up - up) ** 2, 6)}


def calibration(rows: Sequence[Tuple[float, float]]) -> Dict[str, Any]:
    """Calibration des prévisions évaluées (§35) : (probabilité, issue 0/1).
    Score de Brier, réussite, biais, tables par tranche ; jugée dégradée si
    elle ne bat pas le hasard (Brier ≥ 0,25) sur 30 cas au moins."""
    if not rows:
        return {"n": 0}
    p = np.array([r[0] for r in rows], dtype=float)
    o = np.array([r[1] for r in rows], dtype=float)
    brier = float(np.mean((p - o) ** 2))
    bins = []
    for lo_ in (0.0, 0.2, 0.4, 0.6, 0.8):
        m = (p >= lo_) & (p < lo_ + 0.2 + (1e-9 if lo_ == 0.8 else 0))
        if m.any():
            bins.append({"bin": f"{int(lo_ * 100)}-{int(lo_ * 100 + 20)} %", "n": int(m.sum()),
                         "predicted": round(float(p[m].mean()), 3), "observed": round(float(o[m].mean()), 3)})
    return {"n": len(rows), "brier": round(brier, 4), "hit_ratio": round(float(np.mean((p >= 0.5) == (o == 1))), 3),
            "bias": round(float(p.mean() - o.mean()), 3), "bins": bins,
            "degraded": len(rows) >= 30 and brier >= 0.25}


# ---------- Liens entre cryptos (phase 7.13) ----------

def cross_asset(close: pd.DataFrame, day: str) -> Dict[str, Any]:
    """Liens entre cryptos (§20) : corrélation moyenne sur 90 jours et son
    rang dans l'année (contagion quand toutes bougent ensemble), corrélation
    de chacune avec BTC, dispersion des rendements sur 30 jours."""
    end = pd.Timestamp(day, tz="UTC")
    r = np.log(close.loc[:end] / close.loc[:end].shift(1))

    def avg_corr(window: pd.DataFrame) -> Optional[float]:
        cm = window.corr(min_periods=60).values
        iu = np.triu_indices_from(cm, k=1)
        vals = cm[iu]
        vals = vals[~np.isnan(vals)]
        return float(vals.mean()) if len(vals) else None
    now = avg_corr(r.iloc[-90:])
    past = [avg_corr(r.iloc[max(0, k - 90):k]) for k in range(len(r) - 365, len(r), 7) if k > 90]
    past = [x for x in past if x is not None]
    rank = None if now is None or len(past) < 10 else float(np.mean([x <= now for x in past]))
    corr = r.iloc[-90:].corr(min_periods=60)
    to_btc = {a: round(float(corr.loc[a, "btc"]), 3) for a in corr.columns
              if "btc" in corr.columns and a != "btc" and not pd.isna(corr.loc[a, "btc"])}
    disp = np.log(close.loc[:end].iloc[-1] / close.loc[:end].iloc[-31]).std() if len(close.loc[:end]) > 31 else None
    return {"avg_corr_90d": None if now is None else round(now, 3), "rank_1y": None if rank is None else round(rank, 3),
            "contagion": bool(rank is not None and rank >= 0.9), "corr_btc": to_btc,
            "dispersion_30d": None if disp is None or pd.isna(disp) else round(float(disp), 4)}


def regime_labels(r: Dict[str, Any]) -> List[str]:
    """Le régime du jour (regimes.py, quatre lectures) dans le vocabulaire
    commun : BULL, BEAR, SIDEWAYS, HIGH_VOLATILITY, LOW_VOLATILITY, RISK_ON,
    RISK_OFF, CRISIS, TRANSITION."""
    m = {"haussière": "BULL", "baissière": "BEAR", "sans tendance": "SIDEWAYS", "forte": "HIGH_VOLATILITY",
         "faible": "LOW_VOLATILITY", "risk-on": "RISK_ON", "risk-off": "RISK_OFF", "crise": "CRISIS",
         "reprise": "TRANSITION"}
    return [m[v] for k in ("tendance", "volatilite", "appetit", "phase") for v in [r.get(k)] if v in m]


# ---------- Signal, classement, « pas de trade » (phases 7.16, 7.17) ----------

def btc_regime(close: pd.DataFrame, p: ts.TrendParams) -> pd.Series:
    """Marché haussier (BTC au-dessus de sa moyenne de 150 jours) jour par
    jour ; inconnu (vide) tant que la moyenne n'existe pas."""
    if "btc" not in close:
        return pd.Series(np.nan, index=close.index, dtype=object)
    b = close["btc"].astype(float)
    sma = b.rolling(p.regime_sma, min_periods=p.regime_sma).mean()
    return (b > sma).astype(object).where(sma.notna())


def strategy_version(p: ts.TrendParams) -> str:
    """Version de la règle : empreinte de ses réglages."""
    body = json.dumps({k: getattr(p, k) for k in sorted(vars(p))}, sort_keys=True, default=str)
    return "trendguard-" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]


def rule_signal(asset: str, snap: Dict[str, float], p: ts.TrendParams, bull: bool, day: str,
                confidence: Optional[Confidence] = None) -> FinancialSignal:
    """Le signal de la règle du bot au format commun (§23) : cassure du plus
    haut de 30 jours avec momentum positif, marché haussier ; force mesurée
    en volatilités ; risque attendu = distance au stop initial ; rendement
    attendu inconnu (jamais estimé) ; candidat seulement : la porte décide."""
    fires = bool(bull and ts.entry_signal(snap, p))
    vol, close, prior = snap.get("vol"), snap.get("close"), snap.get("prior_high")
    strength = 0.0
    if fires and vol and vol > 0:
        strength = max(0.0, min(1.0, (close - prior) / vol / 3))
    risk = None
    if close and vol and vol > 0:
        risk = round(min(1.0, p.init_stop_atr * vol / close), 4)
    return FinancialSignal(f"S-{day}-{asset}", instrument_id(asset), "LONG" if fires else "NEUTRAL", "BREAKOUT",
                           round(strength, 3), confidence, "MEDIUM_TERM",
                           f"clôture au-dessus du plus haut des {p.breakout_n} clôtures précédentes, momentum "
                           f"{p.mom_n} jours positif, BTC au-dessus de sa moyenne de {p.regime_sma} jours",
                           f"clôture sous le stop suiveur ({fr(p.trail_atr, 'g')} volatilités sous le plus haut)",
                           None, risk, ("breakout_gap_30d", "momentum_90d", "volatility_30d"),
                           strategy_version(p), "CANDIDATE", day)


def _clip01(x: Optional[float]) -> Optional[float]:
    return None if x is None or pd.isna(x) else max(0.0, min(1.0, float(x)))


def opportunity(snap: Dict[str, Optional[float]], quality: float, bull: bool, appetite: Optional[str],
                sentiment: Optional[float], volume_usd: Optional[float], min_volume: float,
                signal: FinancialSignal) -> Dict[str, Any]:
    """Classement indicatif (§25), pondérations versionnées : technique,
    quantitatif, régime, risque, sentiment, liquidité, qualité ; une
    composante non mesurée est dite et retirée (poids redistribués). Un
    ordre d'idées, jamais une décision ni une promesse de rendement."""
    comp = {"technical": 1.0 if signal.direction == "LONG" else _clip01(0.5 + (snap.get("momentum_90d") or 0) / 2)
            if snap.get("momentum_90d") is not None else None,
            "quant": _clip01(((snap.get("sharpe_365d") or 0) + 1) / 3) if snap.get("sharpe_365d") is not None else None,
            "regime": (1.0 if appetite == "risk-on" else 0.5 if appetite == "mitigé" else 0.2) if bull else 0.0,
            "risk": _clip01(1 - (snap.get("volatility_30d") or 0) / HIGH_VOL)
            if snap.get("volatility_30d") is not None else None,
            "sentiment": None if sentiment is None else _clip01((sentiment + 1) / 2),
            "liquidity": None if not volume_usd else _clip01(math.log10(max(volume_usd, 1) / max(min_volume, 1)) / 2 + 0.5),
            "data_quality": _clip01(quality)}
    used = {k: v for k, v in comp.items() if v is not None}
    total = sum(OPPORTUNITY_WEIGHTS[k] for k in used)
    score = None if not total else round(sum(OPPORTUNITY_WEIGHTS[k] * v for k, v in used.items()) / total, 3)
    return {"score": score, "components": {k: None if v is None else round(v, 3) for k, v in comp.items()},
            "not_measured": sorted(k for k, v in comp.items() if v is None), "version": OPPORTUNITY_VERSION,
            "fundamental": "sans objet", "macro": "non mesurée (calendrier des annonces à part)"}


def no_trade(snap: Dict[str, Optional[float]], quality: Dict[str, Any], bull: bool, regime: Dict[str, Any],
             signal: FinancialSignal, context: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Raisons de ne pas acheter (§24), dans le vocabulaire commun (codes du
    contrat NO_TRADE) avec leur explication."""
    out: List[Tuple[str, str]] = []
    if quality["quality"] < 0.5 or snap.get("volatility_30d") is None:
        out.append(("INSUFFICIENT_DATA", f"qualité des données {fr(quality['quality'] * 100, '.0f')} %"))
    if quality["freshness"] < 1:
        out.append(("STALE_DATA", "dernière bougie en retard"))
    if not bull:
        out.append(("POLICY_BLOCK", "BTC sous sa moyenne de 150 jours : la règle n'achète pas"))
    for why in context.get("policy_blocks") or ():
        out.append(("POLICY_BLOCK", why))
    if context.get("asset") in (context.get("vetoed") or ()):
        out.append(("POLICY_BLOCK", "retrait annoncé par Binance : achats bloqués"))
    vol = snap.get("volatility_30d")
    if vol is not None and vol > HIGH_VOL:
        out.append(("HIGH_RISK", f"volatilité extrême ({fr(vol * 100, '.0f')} % par an)"))
    if context.get("illiquid"):
        out.append(("HIGH_RISK", "liquidité insuffisante"))
    if context.get("disagreement") == "HIGH":
        out.append(("MODEL_DISAGREEMENT", "désaccord fort entre agents ou entre IA"))
    if regime.get("phase") == "crise":
        out.append(("MARKET_UNCERTAINTY", "marché en crise (BTC à plus de 40 % sous son plus haut)"))
    if signal.direction != "LONG":
        out.append(("LOW_CONFIDENCE", "aucun signal de la règle aujourd'hui"))
    return out


# ---------- Analyse complète d'un actif (§60) ----------

def _confidences(quality: float, fc: Optional[Forecast], committee: Optional[Dict[str, Any]],
                 calib: Optional[Dict[str, Any]]) -> Dict[str, Optional[Dict[str, Any]]]:
    """Confiances séparées (§30) : données, modèle (calibration mesurée),
    signal (comité), prévision (largeur de l'intervalle), décision (la plus
    faible). Une confiance n'est jamais une certitude."""
    def c(score: Optional[float], method: str, calibrated: bool = False) -> Optional[Dict[str, Any]]:
        if score is None:
            return None
        return dict(Confidence(round(max(0.0, min(1.0, score)), 3), method, calibrated).__dict__)
    out = {"DATA_CONFIDENCE": c(quality, "qualité des données (complétude, fraîcheur, cohérence, fiabilité)"),
           "MODEL_CONFIDENCE": c(None if not calib or not calib.get("n") else 1 - calib["brier"] / 0.25,
                                 "1 − Brier / 0,25 des prévisions déjà évaluées", True),
           "SIGNAL_CONFIDENCE": c((committee or {}).get("confidence", {}).get("score"),
                                  "consensus pondéré du comité d'agents"),
           "FORECAST_CONFIDENCE": c(None if fc is None else 1 - (fc.ci_high - fc.ci_low),
                                    "1 − largeur de l'intervalle de la probabilité de hausse")}
    known = [v["score"] for v in out.values() if v]
    out["DECISION_CONFIDENCE"] = c(min(known) if known else None, "la plus faible des confiances mesurées")
    return out


def analyze(asset: str, close: pd.DataFrame, volume: Optional[pd.DataFrame], day: str, p: ts.TrendParams,
            context: Optional[Dict[str, Any]] = None) -> FinancialAnalysis:
    """Analyse complète d'une crypto (§60) : données et qualité, technique,
    quantitatif, régime, liens avec les autres cryptos, calendrier,
    sentiment, scénarios, prévision, avis du comité, signal de la règle,
    classement, raisons de ne pas acheter, confiances, preuves et conditions
    d'invalidation. Une aide à la décision, jamais une autorisation."""
    context = context or {}
    asset = asset.lower()
    end = pd.Timestamp(day, tz="UTC")
    c = close.loc[:end]
    vol_usd = volume.loc[:end] if volume is not None else None
    f = context.get("feature_frame")
    if f is None:
        f = features(c[asset], c["btc"] if "btc" in c else None,
                     vol_usd[asset] if vol_usd is not None and asset in vol_usd else None)
    snap = snapshot(f, day)
    q = asset_quality(c[asset], day, int((context.get("ohlcv_bad") or {}).get(asset, 0)))
    reg = context.get("regime") or regimes.at(regimes.regime_frame(c), day)
    bull_series = btc_regime(c, p)
    val = bull_series.get(end)
    bull = bool(val) if isinstance(val, (bool, np.bool_)) else False
    strat = context.get("strat")
    if strat is None:
        strat = ts.asset_features(c[asset], p, vol_usd[asset] if vol_usd is not None and asset in vol_usd else None)
    srow = strat.loc[end] if end in strat.index else None
    s_snap = {} if srow is None else {k: float(srow[k]) for k in ("close", "vol", "prior_high", "mom", "age", "vol30")}
    committee = (context.get("committee") or {}).get(asset)
    conf = None
    if committee and committee.get("confidence"):
        cc = committee["confidence"]
        conf = Confidence(cc["score"], cc["method"], bool(cc.get("calibrated")), tuple(cc.get("basis") or ()))
    sig = rule_signal(asset, s_snap, p, bull, day, conf)
    fc = forecast(asset, c[asset], bull_series, day)
    sc = scenarios(asset, c[asset], bull_series, day)
    cross = context.get("cross") or cross_asset(c, day)
    sentiment = (context.get("sentiment") or {}).get(asset)
    v30 = s_snap.get("vol30")
    opp = opportunity(snap, q["quality"], bull, reg.get("appetit"), sentiment,
                      None if v30 is None or not math.isfinite(v30) else v30, p.min_volume_usd, sig)
    ctx = dict(context, asset=asset, illiquid=v30 is not None and math.isfinite(v30) and v30 < p.min_volume_usd,
               disagreement=(committee or {}).get("disagreement"))
    reasons = no_trade(snap, q, bull, reg, sig, ctx)
    hard = [code for code, _w in reasons if code != "LOW_CONFIDENCE"]
    reco = "NO_TRADE" if hard else ("BUY_SIGNAL" if sig.direction == "LONG" else "WATCH")
    evidence = [f"clôture {fr(s_snap.get('close') or 0, '.6g')}, plus haut des 30 jours "
                f"{fr(s_snap.get('prior_high') or 0, '.6g')}" if s_snap else "pas de bougie ce jour-là"]
    if snap.get("momentum_90d") is not None:
        evidence.append(f"momentum 90 jours {fr(snap['momentum_90d'] * 100, '+.1f')} %")
    if fc is not None:
        evidence.append(f"dans {fc.cases} périodes comparables, hausse à {HORIZON} jours "
                        f"{fr(fc.p_up * 100, '.0f')} % des fois (entre {fr(fc.ci_low * 100, '.0f')} et "
                        f"{fr(fc.ci_high * 100, '.0f')} %)")
    risks = []
    if snap.get("volatility_30d") is not None:
        risks.append(f"volatilité {fr(snap['volatility_30d'] * 100, '.0f')} % par an")
    if snap.get("cvar95_1d") is not None:
        risks.append(f"les pires jours (1 sur 20), {fr((math.exp(snap['cvar95_1d']) - 1) * 100, '.1f')} % en moyenne")
    if snap.get("drawdown_365d") is not None:
        risks.append(f"{fr(snap['drawdown_365d'] * 100, '.0f')} % depuis le plus haut d'un an")
    if cross.get("contagion"):
        risks.append("contagion : les cryptos bougent toutes ensemble")
    if context.get("events"):
        risks.append("annonce importante dans les 48 heures : " + ", ".join(context["events"][:3])
                     + " (prudence ; effet sur le bitcoin non prouvé, aucun achat bloqué pour cela)")
    contradictions = []
    if sig.direction == "LONG" and fc is not None and fc.p_up < 0.5:
        contradictions.append("signal de la règle, mais hausse moins d'une fois sur deux dans le passé comparable")
    if sig.direction == "LONG" and committee and committee.get("recommendation") in ("ATTENDRE", "PAS_DE_TRADE"):
        contradictions.append(f"le comité dit « {committee['recommendation'].lower().replace('_', ' ')} »")
    if sentiment is not None and sig.direction == "LONG" and sentiment < -0.3:
        contradictions.append("sentiment des sources prouvées négatif")
    invalid = [INVALIDATION["BULL"]] if sig.direction == "LONG" else [
        f"une clôture au-dessus de {fr(s_snap.get('prior_high') or 0, '.6g')} avec momentum positif ferait un signal"]
    return FinancialAnalysis(
        instrument_id(asset), day, reco, tuple(code for code, _w in reasons),
        tuple(w for _c, w in reasons), opp["score"], _confidences(q["quality"], fc, committee, context.get("calibration")),
        {"quality": q, "features": snap, "feature_version": FEATURE_VERSION, "data_cutoff_at": data_cutoff(day),
         "regime": reg, "regime_labels": regime_labels(reg), "btc_bull": bull, "cross_asset": cross,
         "sentiment": sentiment, "events": context.get("events") or [], "fundamental": SANS_OBJET,
         "forecast": None if fc is None else fc.__dict__, "scenarios": None if sc is None else sc.as_dict(),
         "signal": sig.as_dict(), "opportunity": opp, "committee": committee},
        tuple(evidence), tuple(risks), tuple(contradictions), tuple(invalid))


def render(a: FinancialAnalysis) -> str:
    """L'analyse en clair (§37) : décision, preuves, risques, contradictions,
    confiance, qualité des données, conditions d'invalidation."""
    d = a.details
    names = {"BUY_SIGNAL": "signal d'achat de la règle (la porte d'exécution décide)", "WATCH": "à surveiller",
             "NO_TRADE": "pas de trade"}
    lines = [f"ANALYSE — {a.instrument_id}, bougie du {a.day} (aide à la décision, jamais une autorisation)",
             f"Recommandation : {names[a.recommendation]}"
             + (f" — {' ; '.join(a.reason_texts)}" if a.reason_texts else ""),
             f"Qualité des données : {fr(d['quality']['quality'] * 100, '.0f')} % ; régime : "
             f"{d['regime'].get('texte') or 'inconnu'} ({', '.join(d['regime_labels']) or '—'})"]
    if a.opportunity is not None:
        lines.append(f"Classement indicatif : {fr(a.opportunity * 100, '.0f')}/100 (non mesuré : "
                     f"{', '.join(d['opportunity']['not_measured']) or 'rien'})")
    lines += [f"  + {e}" for e in a.evidence] + [f"  ! {r}" for r in a.risks]
    lines += [f"  ≠ {x}" for x in a.contradictions]
    sc = d.get("scenarios")
    if sc:
        lines.append("Scénarios à 30 jours (fréquences passées) : " + " ; ".join(
            f"{SCENARIO_LABELS[s['name']]} {fr(s['probability'] * 100, '.0f')} %" for s in sc["scenarios"]))
    conf = {k: v["score"] for k, v in a.confidence.items() if v}
    if conf:
        lines.append("Confiance : " + ", ".join(f"{CONFIDENCE_LABELS[k]} {fr(v * 100, '.0f')} %"
                                                for k, v in conf.items()))
    lines.append("L'analyse serait invalidée si : " + " ; ".join(a.invalidating))
    lines.append(f"Sans objet : {d['fundamental']}.")
    return "\n".join(lines)


def intelligence_score(qualities: Sequence[float], calib: Optional[Dict[str, Any]],
                       complete: float) -> Dict[str, Any]:
    """Note d'intelligence financière (§49) : moyenne des seules composantes
    mesurées (qualité des données, calibration des prévisions, analyses
    complètes) ; une composante non mesurée est dite."""
    parts = {"data_quality": float(np.mean(qualities)) if len(qualities) else None,
             "forecast_calibration": None if not calib or not calib.get("n") or calib["n"] < 30
             else max(0.0, 1 - calib["brier"] / 0.25),
             "explainability": complete}
    known = [v for v in parts.values() if v is not None]
    return {"score": round(float(np.mean(known)), 3) if known else None,
            "parts": {k: None if v is None else round(v, 3) for k, v in parts.items()},
            "not_measured": [k for k, v in parts.items() if v is None]}


def drift(f_by_asset: Dict[str, pd.DataFrame], day: str, z_max: float = 3.0) -> List[str]:
    """Dérive (§50) : volatilité du jour très loin de sa distribution d'un an
    (données qui changent), à plus de 3 écarts-types."""
    end = pd.Timestamp(day, tz="UTC")
    out = []
    for a, f in sorted(f_by_asset.items()):
        v = f["volatility_30d"].loc[:end].dropna()
        if len(v) < 200:
            continue
        hist, now = v.iloc[-366:-1], v.iloc[-1]
        sd = hist.std()
        if sd and sd > 0 and abs(now - hist.mean()) / sd > z_max:
            out.append(f"{a.upper()} : volatilité {fr(now * 100, '.0f')} % par an, loin de son année "
                       f"({fr(hist.mean() * 100, '.0f')} % en moyenne)")
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `finance <crypto>` : analyse complète (ou --json)."""
    import os

    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Cœur d'intelligence financière de TrendGuard (consultatif)")
    ap.add_argument("crypto", help="ex. aave")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    close, volume = load_history(args.cache, list(g.universe))
    asset = args.crypto.lower()
    if asset not in close.columns:
        print(f"Crypto inconnue : {asset.upper()}")
        return 1
    st = read_state(g.db_file)
    day = str(close.index[-1].date())
    opinion = (st.get("savoir") or {}).get("opinion") or {}
    ctx = {"sentiment": {a: o.get("value") for a, o in opinion.items()},
           "committee": (st.get("comite") or {}).get("views") or {}}
    a = analyze(asset, close, volume, day, params_for(g), ctx)
    print(json.dumps(a.as_dict(), ensure_ascii=False, indent=1, default=str) if args.json else render(a))
    return 0
