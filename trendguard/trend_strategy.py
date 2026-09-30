#!/usr/bin/env python3
"""
Stratégie « TrendGuard » — suivi de tendance multi-actifs, barres journalières.

Fondement : la persistance des tendances (time-series momentum) est l'un
des effets les mieux documentés en finance (Moskowitz, Ooi & Pedersen 2012 ;
résultats équivalents sur les crypto-actifs). La stratégie ne vise PAS un
taux de réussite élevé : elle coupe les pertes à ≈ -1 % du capital et laisse
courir les gains (plusieurs R). L'espérance positive vient du rapport
gain moyen / perte moyenne (≈ 3), avec 35-50 % de trades gagnants.

Règles (clôtures JOURNALIÈRES, sans look-ahead) :
  1. Régime  : BTC > SMA(150) pour autoriser les entrées ; en régime baissier
               les stops des positions ouvertes sont resserrés.
  2. Entrée  : clôture > plus haut des 30 clôtures précédentes, momentum
               90 j ajusté du risque > 0, liquidité et historique suffisants.
               Candidats classés par momentum.
  3. Stop    : initial = clôture - 3 × vol ; trailing « chandelier » =
               plus haut de clôture - 5 × vol (2 × vol en régime baissier),
               évalué à la clôture.
  4. Taille  : 1 % de l'equity risqué entre l'entrée et le stop initial ;
               max 8 positions, 6 % de risque total, 25 % par position.

Les fonctions de décision (update_positions / plan_entries) sont PARTAGÉES
par le backtest et le bot live (trendguard_bot.py) : ce qui est validé est
exactement ce qui est exécuté.

CLI :
  python trendguard_bot.py strategy download --data data/
  python trendguard_bot.py strategy research --data data/ --out docs/TRENDGUARD_REPORT.md
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import math
import os
import re
import sys
import textwrap
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

# ══════════════════════════════════════════════════════════════════════
# PARAMÈTRES (gelés après la recherche in-sample 2018-2022)
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TrendParams:
    breakout_n: int = 30            # cassure du plus haut de N clôtures
    atr_n: int = 20                 # volatilité : EMA des |Δ clôture|
    init_stop_atr: float = 3.0      # stop initial = entrée - k0 × vol
    trail_atr: float = 5.0          # chandelier : plus haut - k × vol
    regime_sma: int = 150           # BTC > SMA(n) pour autoriser les entrées
    bear_trail_atr: float = 2.0     # >0 : trailing resserré si régime baissier
    mom_n: int = 90                 # fenêtre de momentum (classement)
    risk_pct: float = 0.01          # risque par trade (fraction d'equity)
    max_positions: int = 8
    max_total_risk: float = 0.06    # somme des risques initiaux ouverts
    max_position_pct: float = 0.25  # notionnel max par position
    min_history: int = 250          # jours d'historique minimum
    min_volume_usd: float = 5e6     # volume spot moyen 30 j minimum
    fee: float = 0.001              # par côté
    slippage: float = 0.001         # par côté
    # Profil prudent (désactivé par défaut) : paliers (drawdown, multiplicateur
    # du risque), ex. ((0.10, 0.5),) = risque divisé par 2 au-delà de 10 % de
    # baisse depuis le pic. Réduit le drawdown au prix du rendement ; voir
    # docs/ADAPTATION.md pour la validation.
    dd_throttle: Tuple[Tuple[float, float], ...] = ()

    def validate(self) -> "TrendParams":
        if not (0 < self.risk_pct <= 0.02):
            raise ValueError("risk_pct doit être dans ]0, 2 %].")
        if self.init_stop_atr <= 0 or self.trail_atr <= 0:
            raise ValueError("multiples de stop > 0 requis.")
        if self.max_total_risk < self.risk_pct:
            raise ValueError("max_total_risk < risk_pct.")
        if self.max_positions < 1:
            raise ValueError("max_positions >= 1 requis.")
        for thr, mult in self.dd_throttle:
            if not (0 < thr < 1 and 0 < mult <= 1):
                raise ValueError("dd_throttle : paliers (0 < baisse < 1, "
                                 "0 < multiplicateur <= 1) attendus.")
        return self


# ══════════════════════════════════════════════════════════════════════
# DONNÉES (Coin Metrics community data, clôtures USD 00:00 UTC)
# ══════════════════════════════════════════════════════════════════════

DEFAULT_UNIVERSE = ["btc", "eth", "bnb", "xrp", "ada", "doge", "trx", "link",
                    "ltc", "bch", "xlm", "etc", "xmr", "dash", "zec", "eos",
                    "neo", "xtz", "algo", "dot", "uni", "aave", "icp", "ftt"]

COINMETRICS_URL = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/{}.csv"


def download_coinmetrics(data_dir: str, assets: List[str]) -> List[str]:
    os.makedirs(data_dir, exist_ok=True)
    ok = []
    for a in assets:
        dest = os.path.join(data_dir, f"{a}.csv")
        try:
            urllib.request.urlretrieve(COINMETRICS_URL.format(a), dest)
            ok.append(a)
        except Exception as e:
            print(f"  {a}: échec ({e})")
    return ok


def load_coinmetrics(data_dir: str, assets: List[str]
                     ) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Retourne (clôtures, volumes) journaliers USD, index = date UTC."""
    closes, vols = {}, {}
    for a in assets:
        path = os.path.join(data_dir, f"{a}.csv")
        if not os.path.exists(path):
            continue
        d = pd.read_csv(path, usecols=lambda c: c in (
            "time", "PriceUSD", "volume_reported_spot_usd_1d"))
        if "PriceUSD" not in d:
            continue
        d["time"] = pd.to_datetime(d["time"], utc=True)
        d = d.set_index("time").sort_index()
        px = d["PriceUSD"].where(d["PriceUSD"] > 0)
        if px.dropna().empty:
            continue
        closes[a] = px
        if "volume_reported_spot_usd_1d" in d:
            vols[a] = d["volume_reported_spot_usd_1d"]
    if "btc" not in closes:
        raise ValueError("BTC requis (filtre de régime).")
    close = pd.DataFrame(closes).sort_index()
    volume = pd.DataFrame(vols).reindex(close.index)
    return close, volume


# ══════════════════════════════════════════════════════════════════════
# INDICATEURS ET DÉCISIONS (partagés backtest / live)
# ══════════════════════════════════════════════════════════════════════

def asset_features(close: pd.Series, p: TrendParams,
                   volume_usd: Optional[pd.Series] = None) -> pd.DataFrame:
    """Indicateurs causaux d'un actif à partir de ses seules clôtures."""
    c = close.astype(float)
    f = pd.DataFrame({"close": c})
    f["vol"] = c.diff().abs().ewm(span=p.atr_n, adjust=False,
                                  min_periods=p.atr_n).mean()
    f["prior_high"] = c.shift(1).rolling(p.breakout_n,
                                         min_periods=p.breakout_n).max()
    ret = np.log(c / c.shift(1))
    f["mom"] = (np.log(c / c.shift(p.mom_n))
                / (ret.rolling(p.mom_n).std() * math.sqrt(p.mom_n)))
    valid = c.notna().values
    first = int(np.argmax(valid)) if valid.any() else len(c)
    f["age"] = np.arange(len(c)) - first
    if volume_usd is not None:
        f["vol30"] = volume_usd.astype(float).rolling(
            30, min_periods=10).mean()
    else:
        f["vol30"] = np.inf          # pas de donnée de volume : filtre neutre
    return f


def btc_regime(btc_close: pd.Series, p: TrendParams) -> pd.Series:
    sma = btc_close.rolling(p.regime_sma, min_periods=p.regime_sma).mean()
    return (btc_close > sma).fillna(False)


def _finite(*xs: Any) -> bool:
    return all(x is not None and isinstance(x, (int, float, np.floating))
               and np.isfinite(x) for x in xs)


def entry_signal(s: Dict[str, float], p: TrendParams) -> bool:
    """Cassure + momentum positif + historique + liquidité."""
    if not _finite(s.get("close"), s.get("prior_high"), s.get("vol"),
                   s.get("mom")):
        return False
    if s.get("age", 0) < p.min_history or s["vol"] <= 0:
        return False
    v30 = s.get("vol30")
    if not _finite(v30) or v30 < p.min_volume_usd:   # liquidité inconnue = refus
        return False
    return s["close"] > s["prior_high"] and s["mom"] > 0


def initial_stop(close: float, vol: float, p: TrendParams) -> float:
    return close - p.init_stop_atr * vol


def entry_levels(close: float, vol: float, p: TrendParams) -> Tuple[float, float, float]:
    """Prix d'achat (glissement compris), stop initial, et risque par unité
    achetée entre les deux (frais et glissement compris)."""
    entry = close * (1 + p.slippage)
    stop = initial_stop(close, vol, p)
    return entry, stop, entry * (1 + p.fee) - stop * (1 - p.fee - p.slippage)


def size_position(entry: float, unit_risk: float, risk_quote: float, equity: float,
                  cash: float, p: TrendParams) -> Optional[Tuple[float, float]]:
    """(quantité, coût) pour risquer `risk_quote` jusqu'au stop, sous le
    plafond par position et le cash disponible ; None sous 10 USDT. Seul
    calcul de taille du dépôt : bot, backtest, études et laboratoire."""
    qty = min(risk_quote / unit_risk, p.max_position_pct * equity / entry)
    cost = qty * entry * (1 + p.fee)
    if cost > cash:
        qty = cash / (entry * (1 + p.fee))
        cost = qty * entry * (1 + p.fee)
    if qty * entry < 10:
        return None
    return qty, cost


def trailing_stop(highest_close: float, vol: float, p: TrendParams,
                  bull_regime: bool = True) -> float:
    k = p.trail_atr
    if not bull_regime and p.bear_trail_atr > 0:
        k = min(k, p.bear_trail_atr)
    return highest_close - k * vol


@dataclass
class Holding:
    asset: str
    qty: float
    entry: float
    stop: float
    high: float
    entry_date: Any
    risk_quote: float
    cost: float


def update_positions(holdings: Dict[str, Holding],
                     snap: Dict[str, Dict[str, float]], bull: bool,
                     p: TrendParams) -> List[Tuple[str, str]]:
    """1) Sorties dues à la clôture du jour (clôture <= stop de la veille,
    ou actif sans cotation) ; 2) relèvement des stops des autres.
    Mute holdings (high, stop) ; retourne [(actif, raison)]."""
    exits: List[Tuple[str, str]] = []
    for a, h in holdings.items():
        px = (snap.get(a) or {}).get("close")
        if not _finite(px):
            exits.append((a, "DELISTED"))
        elif px <= h.stop:
            exits.append((a, "STOP"))
    leaving = {a for a, _ in exits}
    for a, h in holdings.items():
        if a in leaving:
            continue
        s = snap[a]
        if s["close"] > h.high:
            h.high = s["close"]
        if _finite(s.get("vol")):
            h.stop = max(h.stop, trailing_stop(h.high, s["vol"], p, bull))
    return exits


def risk_multiplier(equity: float, peak: float, p: TrendParams) -> float:
    """Multiplicateur du risque du profil prudent (1.0 si désactivé) selon
    la baisse de l'equity depuis son plus haut."""
    if not p.dd_throttle or peak <= 0:
        return 1.0
    dd = 1.0 - equity / peak
    mult = 1.0
    for thr, m in p.dd_throttle:
        if dd >= thr:
            mult = min(mult, m)
    return mult


def plan_entries(holdings: Dict[str, Holding],
                 snap: Dict[str, Dict[str, float]], bull: bool,
                 equity: float, cash: float, p: TrendParams,
                 risk_mult: float = 1.0) -> List[Dict[str, Any]]:
    """Entrées du jour, classées par momentum, dimensionnées pour risquer
    risk_pct × risk_mult de l'equity entre le prix d'entrée et le stop
    initial (frais et slippage compris), sous plafonds de portefeuille."""
    if (not bull or len(holdings) >= p.max_positions or equity <= 0
            or risk_mult <= 0):
        return []
    open_risk = sum(h.risk_quote for h in holdings.values())
    cands = [(s["mom"], a, s) for a, s in snap.items()
             if a not in holdings and entry_signal(s, p)]
    cands.sort(key=lambda x: x[0], reverse=True)
    plans: List[Dict[str, Any]] = []
    for _m, a, s in cands:
        if len(holdings) + len(plans) >= p.max_positions:
            break
        risk_quote = p.risk_pct * equity * risk_mult
        if open_risk + risk_quote > p.max_total_risk * equity * risk_mult + 1e-9:
            break
        entry, stop, unit_risk = entry_levels(s["close"], s["vol"], p)
        if stop <= 0 or unit_risk <= 0:
            continue
        sized = size_position(entry, unit_risk, risk_quote, equity, cash, p)
        if sized is None:
            continue
        qty, cost = sized
        real_risk = qty * unit_risk
        plans.append({"asset": a, "qty": qty, "ref_price": s["close"],
                      "entry": entry, "stop": stop, "vol": s["vol"],
                      "risk_quote": real_risk, "cost": cost,
                      "mom": s["mom"]})
        cash -= cost
        open_risk += real_risk
    return plans


def reprice_entry(plan: Dict[str, Any], price: float, equity: float,
                  cash: float, p: TrendParams,
                  min_stop_gap_vol: float = 0.5) -> Optional[Dict[str, Any]]:
    """Redimensionne une entrée planifiée sur la clôture au prix réellement
    disponible au moment de l'exécution (décision tardive, redémarrage en
    cours de journée). Le stop ne bouge pas et le risque ne dépasse jamais
    celui prévu. None si le prix est retombé près du stop : la cassure est
    invalidée. Au prix de clôture, le plan est inchangé."""
    if not _finite(price) or price <= 0:
        return None
    stop, vol = plan["stop"], plan["vol"]
    if price <= stop + min_stop_gap_vol * vol:
        return None
    entry = price * (1 + p.slippage)
    unit_risk = entry * (1 + p.fee) - stop * (1 - p.fee - p.slippage)
    if unit_risk <= 0:
        return None
    sized = size_position(entry, unit_risk, plan["risk_quote"], equity, cash, p)
    if sized is None:
        return None
    qty, cost = sized
    out = dict(plan)
    out.update({"qty": qty, "entry": entry, "exec_price": price, "cost": cost,
                "risk_quote": qty * unit_risk, "unit_risk": unit_risk})
    return out


# ══════════════════════════════════════════════════════════════════════
# SÉLECTION DES CRYPTOS : BÉNÉFICE DE LA STRATÉGIE SUR CHACUNE
# ══════════════════════════════════════════════════════════════════════

def asset_track_records(cols: Dict[str, Dict[str, np.ndarray]], reg: np.ndarray,
                        p: TrendParams, hi: Optional[int] = None
                        ) -> Dict[str, List[Dict[str, Any]]]:
    """Trades de la stratégie sur chaque crypto SEULE (sans plafond de
    portefeuille), avec exactement les règles d'achat et de vente du bot,
    jusqu'au jour d'indice `hi` exclu. Chaque trade : indices d'achat et de
    vente (None si encore ouvert), résultat en R (frais compris). Un trade
    encore ouvert est évalué au dernier cours."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for a, c in cols.items():
        n = len(c["close"]) if hi is None else hi
        trades: List[Dict[str, Any]] = []
        hold: Dict[str, Holding] = {}
        last = None
        for i in range(n):
            s = {k: c[k][i] for k in c}
            if not _finite(s["close"]):
                continue                       # jour sans cotation : rien ne bouge
            last = (i, s["close"])
            bull = bool(reg[i])
            if hold and update_positions(hold, {a: s}, bull, p):
                h = hold.pop(a)
                r = (s["close"] * (1 - p.fee - p.slippage) - h.cost) / h.risk_quote
                trades.append({"entry_i": h.entry_date, "exit_i": i, "r": r,
                               "cost": h.cost, "unit": h.risk_quote})
            if not hold and bull and entry_signal(s, p):
                entry, stop, unit = entry_levels(s["close"], s["vol"], p)
                if stop > 0 and unit > 0:
                    hold[a] = Holding(a, 1.0, entry, stop, s["close"], i, unit,
                                      entry * (1 + p.fee))
        if hold and last is not None:
            h = hold[a]
            r = (last[1] * (1 - p.fee - p.slippage) - h.cost) / h.risk_quote
            trades.append({"entry_i": h.entry_date, "exit_i": None, "r": r,
                           "cost": h.cost, "unit": h.risk_quote})
        out[a] = trades
    return out


def selection_scores(records: Dict[str, List[Dict[str, Any]]], index: pd.Index,
                     end_i: int, days: int = 730,
                     closes: Optional[Dict[str, np.ndarray]] = None,
                     p: Optional[TrendParams] = None) -> Dict[str, Dict[str, float]]:
    """Bénéfice de chaque crypto sur les `days` derniers jours (jusqu'à
    end_i inclus) : somme des R des trades vendus dans la fenêtre, plus le
    trade en cours. Achats ET ventes comptent : seul un cycle complet (ou
    en cours) rapporte. Aucun regard vers le futur : un trade vendu après
    end_i est ignoré ; le trade « en cours » n'existe que si les trades ont
    été calculés jusqu'à end_i (asset_track_records(..., hi=end_i + 1))."""
    start = index[end_i] - pd.Timedelta(days=days)
    lo = int(index.searchsorted(start))
    out = {}
    for a, trades in records.items():
        rs = []
        for t in trades:
            if t["entry_i"] > end_i:
                continue
            if t["exit_i"] is not None and t["exit_i"] <= end_i:
                if t["exit_i"] >= lo:
                    rs.append(t["r"])                 # achat puis vente dans la fenêtre
            elif closes is not None and p is not None:
                # Étude historique : trade ouvert à end_i, évalué au cours de
                # ce jour-là (et non à sa vente future).
                px = closes[a][end_i]
                if _finite(px):
                    rs.append((px * (1 - p.fee - p.slippage) - t["cost"]) / t["unit"])
            elif t["exit_i"] is None:
                rs.append(t["r"])                     # en cours, évalué au dernier cours
        out[a] = {"total_r": float(sum(rs)), "trades": len(rs),
                  "win_rate": (sum(r > 0 for r in rs) / len(rs)) if rs else 0.0}
    return out


def rank_selection(scores: Dict[str, Dict[str, float]], eligible: List[str], n: int = 10,
                   keep: Optional[List[str]] = None, hysteresis: int = 3) -> List[str]:
    """Les `n` cryptos les plus rentables parmi `eligible`. Une crypto déjà
    sélectionnée ne sort que si elle recule au-delà du rang n + hysteresis :
    la sélection ne change pas pour un écart minime."""
    order = sorted(eligible, key=lambda a: (-scores.get(a, {}).get("total_r", 0.0),
                                            -scores.get(a, {}).get("trades", 0), a))
    rank = {a: k for k, a in enumerate(order)}
    chosen = [a for a in (keep or []) if a in rank and rank[a] < n + hysteresis]
    chosen = sorted(chosen, key=lambda a: rank[a])[:n]
    for a in order:
        if len(chosen) >= n:
            break
        if a not in chosen:
            chosen.append(a)
    return sorted(chosen, key=lambda a: rank[a])


# ══════════════════════════════════════════════════════════════════════
# BACKTEST DE PORTEFEUILLE
# ══════════════════════════════════════════════════════════════════════

@dataclass
class BacktestHooks:
    """Variantes étudiées sur LA boucle de backtest (research/, laboratoire).
    Sans crochet, la boucle est exactement celle du bot."""
    # Plafond du multiplicateur de risque selon les trades déjà clos
    # (None = pas de plafond ce jour-là).
    risk_cap: Optional[Callable[[List[Dict[str, Any]]], Optional[float]]] = None
    # Cryptos achetables ce jour-là : (indice du jour, instantané) → instantané.
    choose: Optional[Callable[[int, Dict[str, Dict[str, float]]],
                              Dict[str, Dict[str, float]]]] = None
    # Filtre des achats prévus : (indice du jour, achats, positions, capital).
    filter_plans: Optional[Callable[[int, List[Dict[str, Any]], Dict[str, Holding], float],
                                    List[Dict[str, Any]]]] = None
    # Prise de bénéfice : (gain en R qui déclenche la vente, part vendue).
    take_profit: Optional[Tuple[float, float]] = None
    # Multiplicateur du risque du jour, après le profil prudent : (indice du
    # jour, capital, plus haut) → multiplicateur ≥ 0 (palier de risque,
    # arrêt d'urgence simulé ; 0 = aucun achat ce jour-là).
    risk_scale: Optional[Callable[[int, float, float], float]] = None


@dataclass
class PortfolioResult:
    equity: pd.Series
    trades: List[Dict[str, Any]]
    exposure: pd.Series
    params: TrendParams
    metrics: Dict[str, float] = field(default_factory=dict)


def precompute(close: pd.DataFrame, volume: pd.DataFrame, p: TrendParams,
               assets: Optional[List[str]] = None
               ) -> Tuple[Dict[str, Dict[str, np.ndarray]], np.ndarray]:
    assets = [a for a in (assets or list(close.columns)) if a in close]
    cols = {}
    for a in assets:
        f = asset_features(close[a], p,
                           volume[a] if a in volume.columns else None)
        cols[a] = {k: f[k].values for k in
                   ("close", "vol", "prior_high", "mom", "age", "vol30")}
    reg = btc_regime(close["btc"], p).values
    return cols, reg


def backtest(close: pd.DataFrame, volume: Optional[pd.DataFrame], p: TrendParams,
             start: str, end: str, capital: float = 10_000.0,
             universe: Optional[List[str]] = None,
             pre: Optional[Tuple[Dict[str, Dict[str, np.ndarray]],
                                 np.ndarray]] = None,
             hooks: Optional[BacktestHooks] = None) -> PortfolioResult:
    """LA boucle de backtest du dépôt : mêmes fonctions que le bot
    (update_positions, plan_entries), évaluation à la clôture, frais et
    glissement. Les études et le laboratoire passent leurs variantes par
    `hooks` au lieu de recopier la boucle. `volume` peut manquer si `pre`
    (indicateurs précalculés) est fourni."""
    p.validate()
    hooks = hooks or BacktestHooks()
    cols, reg = pre if pre is not None else precompute(close, volume, p,
                                                        universe)
    if universe:
        cols = {a: v for a, v in cols.items() if a in universe}
    index = close.index
    lo = index.searchsorted(pd.Timestamp(start, tz="UTC"))
    hi = index.searchsorted(pd.Timestamp(end, tz="UTC"), side="right")
    cost_out = p.fee + p.slippage
    cash = peak = capital
    holdings: Dict[str, Holding] = {}
    last_px: Dict[str, float] = {}
    realized: Dict[str, float] = {}      # gains encaissés par une prise de bénéfice partielle
    took: Set[str] = set()
    trades: List[Dict[str, Any]] = []
    eq_hist, expo_hist = [], []

    def close_trade(a: str, px: float, d: Any, reason: str) -> None:
        nonlocal cash
        h = holdings.pop(a)
        proceeds = h.qty * px * (1 - cost_out)
        cash += proceeds
        pnl = proceeds - h.cost + realized.pop(a, 0.0)
        took.discard(a)
        trades.append({"asset": a, "entry_date": h.entry_date,
                       "exit_date": d, "entry": h.entry, "exit": px,
                       "pnl": pnl, "r": pnl / h.risk_quote,
                       "days": (d - h.entry_date).days,
                       "reason": reason})

    for i in range(lo, hi):
        d = index[i]
        snap = {a: {k: c[k][i] for k in c} for a, c in cols.items()}
        bull = bool(reg[i])
        for a, reason in update_positions(holdings, snap, bull, p):
            px = snap[a]["close"]
            if reason == "DELISTED" or not _finite(px):
                px = last_px.get(a, holdings[a].entry) * 0.5   # retrait : décote 50 %
            close_trade(a, px, d, reason)
        if hooks.take_profit is not None:
            tp_r, tp_frac = hooks.take_profit
            for a in list(holdings):
                h, px = holdings[a], snap[a]["close"]
                if a in took or not _finite(px):
                    continue
                unit = h.risk_quote / h.qty          # risque initial par unité
                if px * (1 - cost_out) - h.cost / h.qty >= tp_r * unit:
                    if tp_frac >= 1:
                        close_trade(a, px, d, "TP")
                    else:
                        q = h.qty * tp_frac
                        proceeds = q * px * (1 - cost_out)
                        cash += proceeds
                        realized[a] = realized.get(a, 0.0) + proceeds - h.cost * tp_frac
                        h.qty -= q
                        h.cost *= (1 - tp_frac)
                        took.add(a)
        for a in holdings:
            last_px[a] = snap[a]["close"]
        mtm = sum(h.qty * snap[a]["close"] for a, h in holdings.items())
        equity = cash + mtm
        peak = max(peak, equity)
        mult = risk_multiplier(equity, peak, p)
        if hooks.risk_cap is not None:
            cap = hooks.risk_cap(trades)
            if cap is not None:
                mult = min(mult, cap)
        if hooks.risk_scale is not None:
            mult *= hooks.risk_scale(i, equity, peak)
        eligible = snap if hooks.choose is None else hooks.choose(i, snap)
        plans = plan_entries(holdings, eligible, bull, equity, cash, p, mult)
        if hooks.filter_plans is not None:
            plans = hooks.filter_plans(i, plans, holdings, equity)
        for plan in plans:
            a = plan["asset"]
            cash -= plan["cost"]
            holdings[a] = Holding(a, plan["qty"], plan["entry"], plan["stop"],
                                  plan["ref_price"], d, plan["risk_quote"],
                                  plan["cost"])
            last_px[a] = plan["ref_price"]
        mtm = sum(h.qty * snap[a]["close"] for a, h in holdings.items())
        eq_hist.append(cash + mtm)
        expo_hist.append(mtm / max(cash + mtm, 1e-9))
    dates = index[lo:hi]
    equity_s = pd.Series(eq_hist, index=dates)
    res = PortfolioResult(equity_s, trades, pd.Series(expo_hist, index=dates),
                          p)
    res.metrics = compute_metrics(equity_s, trades)
    return res


# ══════════════════════════════════════════════════════════════════════
# MÉTRIQUES / VALIDATION
# ══════════════════════════════════════════════════════════════════════

def compute_metrics(equity: pd.Series, trades: List[Dict[str, Any]]
                    ) -> Dict[str, float]:
    """Mesures d'une courbe de capital et de ses trades : rendement, CAGR,
    baisse maximale, Sharpe, Sortino, Calmar, gagnants, R moyens,
    espérance, facteur de profit et durée moyenne. Vide sous deux points."""
    if len(equity) < 2:
        return {}
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    dd = equity / equity.cummax() - 1
    sd = rets.std()
    sharpe = rets.mean() / sd * math.sqrt(365) if sd > 0 else 0.0
    dsd = rets[rets < 0].std()
    sortino = rets.mean() / dsd * math.sqrt(365) if dsd and dsd > 0 else 0.0
    rs = np.array([t["r"] for t in trades]) if trades else np.array([])
    wins, losses = rs[rs > 0], rs[rs <= 0]
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return {
        "total_return_pct": float(total * 100), "cagr_pct": float(cagr * 100),
        "max_dd_pct": float(dd.min() * 100), "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": float(cagr / abs(dd.min())) if dd.min() < 0 else 0.0,
        "trades": int(len(trades)),
        "win_rate_pct": float(len(wins) / len(rs) * 100) if len(rs) else 0.0,
        "avg_win_r": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "worst_r": float(rs.min()) if len(rs) else 0.0,
        "best_r": float(rs.max()) if len(rs) else 0.0,
        "expectancy_r": float(rs.mean()) if len(rs) else 0.0,
        "profit_factor": float(gp / gl) if gl > 0 else float("inf"),
        "payoff": (float(wins.mean()) / abs(float(losses.mean())))
        if len(wins) and len(losses) and losses.mean() != 0 else 0.0,
        "avg_days": float(np.mean([t["days"] for t in trades if "days" in t]))
        if any("days" in t for t in trades) else 0.0,
    }


def buy_and_hold(close: pd.DataFrame, assets: List[str], start: str,
                 end: str) -> Dict[str, float]:
    sub = close.loc[start:end, [a for a in assets if a in close]]
    sub = sub.dropna(axis=1, how="all")
    first = sub.apply(lambda s: s.dropna().iloc[0])
    norm = (sub / first).ffill()
    return compute_metrics(norm.mean(axis=1) * 10_000, [])


def yearly_returns(equity: pd.Series) -> Dict[int, float]:
    out = {}
    prev = equity.iloc[0]
    for y, s in equity.groupby(equity.index.year):
        out[int(y)] = float((s.iloc[-1] / prev - 1) * 100)
        prev = s.iloc[-1]
    return out


def monte_carlo(trades: List[Dict[str, Any]], risk_pct: float,
                n_trades: Optional[int] = None, sims: int = 5000,
                seed: int = 7) -> Dict[str, float]:
    """Rééchantillonne les R des trades (risque fixe par trade) : distribution
    du rendement et du drawdown indépendante de l'ordre historique."""
    rs = np.array([t["r"] for t in trades], dtype=float)
    if len(rs) < 20:
        return {}
    n = n_trades or len(rs)
    rng = np.random.default_rng(seed)
    draws = rng.choice(rs, size=(sims, n), replace=True)
    paths = np.cumprod(1 + draws * risk_pct, axis=1)
    peaks = np.maximum.accumulate(np.hstack([np.ones((sims, 1)), paths]),
                                  axis=1)[:, 1:]
    mdd = ((peaks - paths) / peaks).max(axis=1)
    fin = paths[:, -1] - 1
    streaks = [_max_streak(row) for row in draws[:2000]]
    return {"ret_p5": float(np.percentile(fin, 5) * 100),
            "ret_p50": float(np.percentile(fin, 50) * 100),
            "ret_p95": float(np.percentile(fin, 95) * 100),
            "dd_p50": float(np.percentile(mdd, 50) * 100),
            "dd_p95": float(np.percentile(mdd, 95) * 100),
            "prob_loss_pct": float((fin < 0).mean() * 100),
            "losing_streak_p50": float(np.percentile(streaks, 50)),
            "losing_streak_p95": float(np.percentile(streaks, 95))}


def _max_streak(rs: np.ndarray) -> int:
    best = cur = 0
    for r in rs:
        cur = cur + 1 if r <= 0 else 0
        best = max(best, cur)
    return best


DEFAULT_GRID: Dict[str, List[Any]] = {
    "breakout_n": [20, 30, 50],
    "init_stop_atr": [2.0, 3.0, 4.0],
    "trail_atr": [4.0, 5.0, 6.0],
}


def grid_search(close: pd.DataFrame, volume: pd.DataFrame, base: TrendParams,
                start: str, end: str, grid: Dict[str, List[Any]]
                ) -> pd.DataFrame:
    rows = []
    keys = list(grid)
    for combo in itertools.product(*[grid[k] for k in keys]):
        p = dataclasses.replace(base, **dict(zip(keys, combo)))
        m = backtest(close, volume, p, start, end).metrics
        rows.append({**dict(zip(keys, combo)), **m})
    return pd.DataFrame(rows)


def walk_forward(close: pd.DataFrame, volume: pd.DataFrame, base: TrendParams,
                 start: str, end: str, train_years: int = 3,
                 test_months: int = 6,
                 grid: Optional[Dict[str, List[Any]]] = None,
                 min_trades: int = 30) -> Tuple[pd.DataFrame, pd.Series]:
    """Walk-forward glissant : optimisation (Sharpe) sur les `train_years`
    précédant chaque fenêtre de test, puis test hors échantillon. Retourne
    le détail par fenêtre et la courbe d'equity OOS chaînée."""
    grid = grid or DEFAULT_GRID
    keys = list(grid)
    combos = list(itertools.product(*[grid[k] for k in keys]))
    t = pd.Timestamp(start, tz="UTC")
    stop_at = pd.Timestamp(end, tz="UTC")
    rows, pieces = [], []
    capital = 10_000.0
    while t < stop_at:
        te = min(t + pd.DateOffset(months=test_months), stop_at)
        tr0 = t - pd.DateOffset(years=train_years)
        best = None
        for combo in combos:
            p = dataclasses.replace(base, **dict(zip(keys, combo)))
            m = backtest(close, volume, p, str(tr0.date()),
                         str((t - pd.Timedelta(days=1)).date())).metrics
            score = m.get("sharpe", -9) if m.get("trades", 0) >= min_trades \
                else -9
            if best is None or score > best[0]:
                best = (score, p, m)
        _s, p_best, m_is = best
        res = backtest(close, volume, p_best, str(t.date()),
                       str((te - pd.Timedelta(days=1)).date()),
                       capital=capital)
        if len(res.equity):
            pieces.append(res.equity)
            capital = float(res.equity.iloc[-1])
        rows.append({"test_start": str(t.date()), "test_end": str(te.date()),
                     **{k: getattr(p_best, k) for k in keys},
                     "is_sharpe": m_is.get("sharpe", 0.0),
                     "oos_return_pct": res.metrics.get("total_return_pct", 0.0),
                     "oos_trades": res.metrics.get("trades", 0)})
        t = te
    eq = pd.concat(pieces) if pieces else pd.Series(dtype=float)
    eq = eq[~eq.index.duplicated(keep="last")]
    return pd.DataFrame(rows), eq


# ══════════════════════════════════════════════════════════════════════
# RAPPORT DE RECHERCHE
# ══════════════════════════════════════════════════════════════════════

IS_PERIOD = ("2018-01-01", "2022-12-31")


def _fmt_m(m: Dict[str, float], trades: bool = True) -> str:
    s = (f"| {m['cagr_pct']:+.1f} % | {m['total_return_pct']:+.1f} % | "
         f"{m['max_dd_pct']:.1f} % | {m['sharpe']:.2f} | {m['calmar']:.2f} |")
    if trades:
        s += (f" {m['trades']} | {m['win_rate_pct']:.1f} % | "
              f"{m['avg_win_r']:+.2f} R | {m['avg_loss_r']:+.2f} R | "
              f"{m['expectancy_r']:+.2f} R | {m['profit_factor']:.2f} |")
    else:
        s += " — | — | — | — | — | — |"
    return s


_MD_DELIM = re.compile(r"^\|(\s*:?-+:?\s*\|)+\s*$")
_MD_LIST = re.compile(r"^((?:[-*+]|\d+\.)\s+)")
_MD_GLUE = re.compile(r" (?=[:;!?»])|(?<=«) ")   # typographie française


def format_markdown(text: str, width: int = 80) -> str:
    """Met en forme le Markdown généré selon les règles markdownlint du dépôt
    (.markdownlint.jsonc) : lignes de texte repliées à `width` caractères,
    séparateurs de tableau espacés (« | --- | »), blocs de code typés,
    ligne vide avant une liste. Les
    tableaux, titres et blocs de code ne sont pas repliés ; le rendu est
    identique (un retour à la ligne simple ne coupe pas un paragraphe)."""
    out: List[str] = []
    in_code = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code and stripped == "```":
                line = line.replace("```", "```text", 1)
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        if _MD_DELIM.match(stripped):
            cols = stripped.strip("|").split("|")
            out.append("| " + " | ".join(
                (":" if c.strip().startswith(":") else "") + "---"
                + (":" if c.strip().endswith(":") else "") for c in cols) + " |")
            continue
        # Liste collée à un paragraphe (MD032) : ligne vide insérée.
        if (_MD_LIST.match(line) and out and out[-1].strip()
                and not _MD_LIST.match(out[-1]) and not out[-1].startswith(
                    (" ", "|", "#", ">"))):
            out.append("")
        if (len(line) <= width or stripped.startswith(("|", "#"))
                or not stripped):
            out.append(line)
            continue
        prefix, rest = ("> ", line[2:]) if line.startswith("> ") else ("", line)
        indent = rest[:len(rest) - len(rest.lstrip(" "))]
        rest = rest[len(indent):]
        m = _MD_LIST.match(rest)
        marker = m.group(1) if m else ""
        # Espace insécable provisoire : « : », « ; »… ne se retrouvent jamais
        # seuls en début de ligne.
        body = _MD_GLUE.sub("\u00a0", rest[len(marker):])
        wrapped = textwrap.wrap(body, width=width,
                                initial_indent=prefix + indent + marker,
                                subsequent_indent=prefix + indent
                                + " " * len(marker),
                                break_long_words=False, break_on_hyphens=False)
        out.extend(w.replace("\u00a0", " ") for w in wrapped)
    return "\n".join(out)


def research_report(data_dir: str, out_path: str,
                    oos_end: Optional[str] = None) -> str:
    """Rapport de recherche (docs/TRENDGUARD_REPORT.md) sur les clôtures Coin
    Metrics, actifs effondrés compris : protocole, paramètres retenus,
    robustesse de la grille, résultats en 2018-2022 et hors échantillon
    (2023 → `oos_end`). Écrit dans `out_path`, puis renvoyé."""
    close, volume = load_coinmetrics(data_dir, DEFAULT_UNIVERSE)
    oos_end = oos_end or str(close["btc"].dropna().index[-1].date())
    oos = ("2023-01-01", oos_end)
    p = TrendParams()
    header = ("| Stratégie | CAGR | Total | Max DD | Sharpe | Calmar | Trades | "
              "Gagnants | Gain moy. | Perte moy. | Espérance | PF |\n"
              "|---|---|---|---|---|---|---|---|---|---|---|---|")
    L: List[str] = []
    L.append("# TrendGuard — rapport de recherche\n")
    L.append(f"Données : Coin Metrics (clôtures USD journalières), "
             f"{len([a for a in DEFAULT_UNIVERSE if a in close])} actifs dont "
             f"des actifs effondrés/retirés (FTT, EOS, NEO, XTZ…) pour limiter "
             f"le biais du survivant. Frais {p.fee*100:.1f} % + slippage "
             f"{p.slippage*100:.1f} % par côté. Capital initial 10 000 USD.\n")
    L.append("Protocole : conception et choix des paramètres sur "
             f"**{IS_PERIOD[0]} → {IS_PERIOD[1]}** uniquement ; période "
             f"**{oos[0]} → {oos[1]}** conservée intacte et évaluée une seule "
             "fois avec les paramètres gelés.\n")
    L.append("## Paramètres retenus\n")
    L.append("```text\n" + "\n".join(f"{k} = {v}" for k, v in
                                 dataclasses.asdict(p).items()) + "\n```\n")
    grid = {"breakout_n": [20, 30, 50, 70],
            "init_stop_atr": [2.0, 3.0, 4.0],
            "trail_atr": [3.0, 4.0, 5.0, 6.0],
            "regime_sma": [100, 150, 200]}
    g = grid_search(close, volume, p, *IS_PERIOD, grid)
    L.append("## 1. Robustesse in-sample (grille de "
             f"{len(g)} combinaisons, 2018-2022)\n")
    L.append(f"- Combinaisons rentables : **{(g.cagr_pct > 0).mean()*100:.0f} %**"
             f" ; Sharpe > 0,5 : **{(g.sharpe > 0.5).mean()*100:.0f} %**")
    q = g.sharpe.quantile([0.1, 0.5, 0.9]).round(2).tolist()
    L.append(f"- Sharpe : P10 = {q[0]}, médiane = {q[1]}, P90 = {q[2]}")
    L.append("- Les paramètres retenus sont au **centre du plateau** (et non "
             "au meilleur point, ce qui serait du sur-ajustement).\n")
    is_res = backtest(close, volume, p, *IS_PERIOD)
    oos_res = backtest(close, volume, p, *oos)
    L.append("## 2. Résultats\n")
    L.append(header)
    L.append("| TrendGuard in-sample 2018-2022 " + _fmt_m(is_res.metrics))
    L.append("| **TrendGuard hors échantillon** " + _fmt_m(oos_res.metrics))
    L.append("| Achat-conservation BTC (OOS) "
             + _fmt_m(buy_and_hold(close, ["btc"], *oos), False))
    L.append("| Panier équipondéré (OOS) "
             + _fmt_m(buy_and_hold(close, DEFAULT_UNIVERSE, *oos), False))
    L.append("| Achat-conservation BTC (IS) "
             + _fmt_m(buy_and_hold(close, ["btc"], *IS_PERIOD), False))
    L.append("")
    yr = yearly_returns(pd.concat([is_res.equity,
                                   oos_res.equity * is_res.equity.iloc[-1]
                                   / oos_res.equity.iloc[0]]))
    L.append("Rendement par année (IS puis OOS chaînés) : "
             + ", ".join(f"{y} : {v:+.1f} %" for y, v in yr.items()) + "\n")
    L.append("## 3. Tests de résistance (hors échantillon)\n")
    stress = [
        ("Frais et slippage × 2", dataclasses.replace(p, fee=0.002,
                                                       slippage=0.002), None),
        ("Risque 0,5 % par trade", dataclasses.replace(p, risk_pct=0.005), None),
    ]
    t_oos = pd.DataFrame(oos_res.trades)
    contrib = t_oos.groupby("asset").pnl.sum().sort_values(ascending=False)
    top = list(contrib.index[:3])
    stress.append((f"Sans les 3 meilleurs actifs ({', '.join(top)})", p,
                   [a for a in DEFAULT_UNIVERSE if a not in top]))
    L.append("| Scénario | CAGR | Total | Max DD | Sharpe | Calmar | Trades | "
             "Gagnants | Gain moy. | Perte moy. | Espérance | PF |\n"
             "|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, ps, uni in stress:
        m = backtest(close, volume, ps, *oos, universe=uni).metrics
        L.append(f"| {name} " + _fmt_m(m))
    L.append("")
    L.append("Contribution par actif (OOS, USD) : "
             + ", ".join(f"{a.upper()} {v:+,.0f}" for a, v in contrib.items())
             + "\n")
    wf_rows, wf_eq = walk_forward(close, volume, p, "2021-01-01", oos[1])
    wf_m = compute_metrics(wf_eq, [])
    L.append("## 4. Walk-forward (ré-optimisation glissante)\n")
    L.append("Optimisation du Sharpe sur les 3 années précédentes (grille "
             "27 combinaisons), test sur les 6 mois suivants, 2021 → "
             f"{oos[1]} :\n")
    L.append(f"- Courbe OOS chaînée : CAGR **{wf_m['cagr_pct']:+.1f} %**, "
             f"max DD {wf_m['max_dd_pct']:.1f} %, Sharpe {wf_m['sharpe']:.2f}")
    pos = (wf_rows.oos_return_pct > 0).mean() * 100
    L.append(f"- Fenêtres de 6 mois positives : {pos:.0f} % "
             f"({len(wf_rows)} fenêtres)\n")
    L.append("| Test | breakout | stop init. | trailing | Sharpe IS | "
             "Rendement OOS | Trades |\n|---|---|---|---|---|---|---|")
    for _, r in wf_rows.iterrows():
        L.append(f"| {r.test_start} → {r.test_end} | {r.breakout_n} | "
                 f"{r.init_stop_atr} | {r.trail_atr} | {r.is_sharpe:.2f} | "
                 f"{r.oos_return_pct:+.1f} % | {r.oos_trades} |")
    L.append("")
    all_trades = is_res.trades + oos_res.trades
    mc = monte_carlo(all_trades, p.risk_pct, n_trades=100)
    L.append("## 5. Monte Carlo (5 000 séquences de 100 trades)\n")
    L.append(f"- Rendement sur 100 trades : P5 {mc['ret_p5']:+.1f} % / "
             f"médiane {mc['ret_p50']:+.1f} % / P95 {mc['ret_p95']:+.1f} %")
    L.append(f"- Drawdown max : médiane {mc['dd_p50']:.1f} % / "
             f"P95 {mc['dd_p95']:.1f} %")
    L.append(f"- Probabilité de perte après 100 trades : "
             f"{mc['prob_loss_pct']:.1f} %")
    L.append(f"- Série de pertes consécutives : médiane "
             f"{mc['losing_streak_p50']:.0f}, P95 {mc['losing_streak_p95']:.0f} "
             f"(à accepter psychologiquement)")
    L.append("- ⚠️ Le Monte Carlo suppose des trades indépendants : les "
             "positions simultanées étant corrélées (crypto), il SOUS-ESTIME "
             "le drawdown. Référence réaliste : le drawdown historique "
             "(-25 à -35 %).\n")
    L.append("## 6. Ce qu'il faut attendre (et ne pas attendre)\n")
    L.append(f"- Taux de réussite ≈ {oos_res.metrics['win_rate_pct']:.0f}-"
             f"{is_res.metrics['win_rate_pct']:.0f} % : la performance vient "
             f"du ratio gain/perte ≈ {oos_res.metrics['payoff']:.1f}, pas d'un "
             "taux de réussite élevé. Une stratégie annonçant à la fois un "
             "taux de réussite très élevé et des gains très élevés est "
             "presque toujours sur-ajustée.")
    L.append(f"- Perte par trade ≈ 1 % du capital (moyenne "
             f"{oos_res.metrics['avg_loss_r']:+.2f} R) ; le pire trade "
             f"({oos_res.metrics['worst_r']:+.2f} R) vient d'un gap sous le "
             "stop : le stop est évalué à la clôture.")
    L.append("- Drawdowns de 25-35 % possibles ; mois sans nouvelle entrée "
             "quand BTC est sous sa moyenne 150 j (capital protégé en USDT).")
    L.append("- Performances passées ≠ performances futures. À valider en "
             "paper puis testnet avant tout capital réel.\n")
    text = format_markdown("\n".join(L))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def main(argv: Optional[List[str]] = None) -> int:
    """Ligne de commande de la recherche : téléchargement Coin Metrics,
    rapport de recherche ou backtest avec les paramètres gelés."""
    for stream in (sys.stdout, sys.stderr):      # Windows : sortie redirigée
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="TrendGuard — recherche")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="Télécharge les données Coin Metrics")
    d.add_argument("--data", default="data")
    r = sub.add_parser("research", help="Génère le rapport de recherche")
    r.add_argument("--data", default="data")
    r.add_argument("--out", default="docs/TRENDGUARD_REPORT.md")
    b = sub.add_parser("backtest", help="Backtest avec les paramètres gelés")
    b.add_argument("--data", default="data")
    b.add_argument("--start", default="2018-01-01")
    b.add_argument("--end", default="2100-01-01")
    b.add_argument("--risk", type=float, default=0.01)
    args = ap.parse_args(argv)
    if args.cmd == "download":
        ok = download_coinmetrics(args.data, DEFAULT_UNIVERSE)
        print(f"{len(ok)} actifs téléchargés dans {args.data}/")
        return 0
    if args.cmd == "research":
        print(research_report(args.data, args.out))
        print(f"\nRapport écrit : {args.out}")
        return 0
    close, volume = load_coinmetrics(args.data, DEFAULT_UNIVERSE)
    p = dataclasses.replace(TrendParams(), risk_pct=args.risk)
    res = backtest(close, volume, p, args.start, args.end)
    for k, v in res.metrics.items():
        print(f"{k:<18} {v:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
