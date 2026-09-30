"""
Auto-diagnostic TrendGuard — LECTURE SEULE, aucun ordre.

Huit analyses, chacune produisant des constats notés OK / INFO / ATTENTION /
ALERTE avec une recommandation :

  1. Système       horloge vs Binance, latence, bot actif, base, disque
  2. Données       bougies en retard, trous, prix aberrants, liquidité
  3. Marché        régime BTC, hésitation du régime, volatilité
  4. Signaux       achats du jour et raisons des refus
  5. Portefeuille  risque engagé, corrélation, scénarios de krach
  6. Stratégie     l'avantage statistique tient-il encore ? (rendement
                   12 mois vs historique, espérance récente avec intervalle
                   de confiance) — c'est la partie « apprentissage »
  7. Réel vs attendu  les trades du bot sont-ils compatibles avec la
                   distribution historique ? (test statistique)
  8. Alternatives  tournoi des sept stratégies du laboratoire sur les
                   24 derniers mois (strategy_lab.py) : TrendGuard reste-t-elle
                   compétitive ?

Le diagnostic ALERTE mais ne modifie jamais la stratégie de lui-même :
adapter automatiquement des règles à des résultats récents est la source
n° 1 de sur-ajustement (voir docs/ADAPTATION.md).
"""

from __future__ import annotations

import math
import os
import platform
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import v29

from . import strategy_lab as sl
from . import trend_strategy as ts
from .texte import fr

DAY_MS = 86_400_000
LEVELS = ("OK", "INFO", "ATTENTION", "ALERTE")
ICONS = {"OK": "✅", "INFO": "ℹ️ ", "ATTENTION": "⚠️ ", "ALERTE": "❌"}


@dataclass
class Finding:
    section: str
    level: str
    message: str
    reco: str = ""


# ══════════════════════════════════════════════════════════════════════
# DONNÉES
# ══════════════════════════════════════════════════════════════════════

def _fetch_retry(exchange: Any, sym: str, since: int, tries: int = 4,
                 sleep=time.sleep) -> List[List[float]]:
    """Requête OHLCV avec nouvelles tentatives (connexion lente/instable)."""
    for i in range(tries):
        try:
            return exchange.fetch_ohlcv(sym, "1d", since=since, limit=1000)
        except Exception as e:
            transient = type(e).__name__ in ("RequestTimeout", "NetworkError",
                                             "ExchangeNotAvailable", "DDoSProtection")
            if not transient or i == tries - 1:
                raise
            sleep(2 * (i + 1))
    return []


def fetch_daily_history(exchange: Any, bases: List[str], quote: str = "USDT",
                        since: str = "2018-06-01",
                        now: Optional[datetime] = None
                        ) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, str]]:
    """Historique journalier Binance (bougies CLÔTURÉES uniquement).
    Retourne (clôtures, volumes en quote, erreurs par actif)."""
    now = now or datetime.now().astimezone()
    now_ms = int(now.timestamp() * 1000)
    since_ms = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)
    closes, vols, errors = {}, {}, {}
    for b in bases:
        sym = f"{b.upper()}/{quote}"
        rows: List[List[float]] = []
        t = since_ms
        try:
            while t < now_ms:
                batch = _fetch_retry(exchange, sym, t)
                if not batch:
                    break
                rows.extend(batch)
                nxt = int(batch[-1][0]) + DAY_MS
                if nxt <= t or len(batch) < 1000:
                    break
                t = nxt
        except Exception as e:
            errors[b.lower()] = f"{type(e).__name__}: {str(e)[:120]}"
            continue
        if not rows:
            errors[b.lower()] = "aucune bougie"
            continue
        df = pd.DataFrame([r[:6] for r in rows],
                          columns=["ts", "o", "h", "l", "c", "v"])
        df = df.drop_duplicates("ts").sort_values("ts")
        df = df[df["ts"].astype("int64") + DAY_MS <= now_ms]
        idx = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
        c = df["c"].astype(float).values
        closes[b.lower()] = pd.Series(c, index=idx)
        vols[b.lower()] = pd.Series(df["v"].astype(float).values * c, index=idx)
    close = pd.DataFrame(closes).sort_index()
    volume = pd.DataFrame(vols).reindex(close.index)
    return close, volume, errors


def bootstrap_mean_ci(x: np.ndarray, level: float = 0.90, n: int = 4000,
                      seed: int = 11) -> Tuple[float, float, float]:
    x = np.asarray(x, dtype=float)
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    a = (1 - level) / 2
    return float(x.mean()), float(np.quantile(means, a)), float(np.quantile(means, 1 - a))


# ══════════════════════════════════════════════════════════════════════
# 1. SYSTÈME
# ══════════════════════════════════════════════════════════════════════

def check_system(exchange: Any, state: Dict[str, Any], expected_day: str,
                 db_file: str, running: Optional[bool],
                 now: datetime) -> List[Finding]:
    """Contrôles du système : Python, horloge et latence vers Binance, bot en
    marche, dernier cycle, décision du jour, arrêt d'urgence, base et
    espace disque."""
    S = "Système"
    out: List[Finding] = []
    out.append(Finding(S, "INFO", f"Python {platform.python_version()} "
                       f"sur {platform.system()} {platform.release()}"))
    fetch_time = getattr(exchange, "fetch_time", None)
    if callable(fetch_time):
        samples = []
        for _ in range(5):
            try:
                t0 = time.time()
                server_ms = int(fetch_time())
                t1 = time.time()
                samples.append(((t1 - t0) * 1000,
                                server_ms - (t0 + t1) / 2 * 1000))
            except Exception:
                continue
        if not samples:
            out.append(Finding(S, "ALERTE", "Binance injoignable",
                               "Vérifier la connexion Internet / le pare-feu."))
        else:
            latency, offset = min(samples)          # mesure la plus rapide
            uncertainty = latency / 2
            excess = abs(offset) - uncertainty
            # Le bot vit à l'heure de Binance (v29.sync_exchange_clock, au
            # démarrage puis toutes les heures) : l'écart du PC n'affecte ni
            # les décisions, ni les ordres, ni les journaux. Seul un écart
            # énorme pose problème (certificats HTTPS refusés).
            lvl = ("OK" if excess < 1000 else "INFO" if excess < 3_600_000
                   else "ATTENTION")
            note = ("" if lvl == "OK" else " — le bot utilise l'heure de Binance "
                    "(décisions, ordres, journaux)")
            out.append(Finding(
                S, lvl, f"Horloge : {v29.describe_clock(offset, uncertainty)}{note}",
                "" if lvl in ("OK", "INFO") else "Écart de plus d'une heure : "
                "régler l'horloge du PC (README, section Windows) ; au-delà, "
                "les connexions sécurisées (HTTPS) peuvent échouer."))
            lvl = "OK" if latency < 1000 else "ATTENTION" if latency < 3000 else "ALERTE"
            out.append(Finding(
                S, lvl, f"Latence réseau vers Binance : {latency:.0f} ms "
                f"(meilleure de {len(samples)} mesures)",
                "" if lvl == "OK" else "Connexion lente : les ordres et la "
                "surveillance des stops seront retardés. Pour le mode réel, "
                "préférer une connexion filaire stable ou un petit serveur (VPS)."))
    if running is True:
        out.append(Finding(S, "OK", "Bot en cours d'exécution (verrou détenu)"))
    elif running is False:
        out.append(Finding(S, "INFO", "Bot arrêté (aucune instance ne détient le verrou)"))
    last_cycle = state.get("last_cycle_ts")
    if last_cycle:
        age = time.time() - float(last_cycle)
        lvl = "OK" if age < 600 or running is False else "ALERTE"
        out.append(Finding(S, lvl, f"Dernier cycle réussi il y a {age / 60:.0f} min",
                           "" if lvl == "OK" else "Le bot tourne mais ne réussit "
                           "plus ses cycles : consulter le journal et le fichier "
                           "<journal>.blocage.txt (pile écrite après 20 min de "
                           "blocage). Lancé avec F5 dans VS Code, il est peut-être "
                           "en pause dans le débogueur : le relancer avec Ctrl+F5."))
    last_day = state.get("last_decision_day")
    if last_day:
        lvl = "OK" if last_day >= expected_day or running is False else "ALERTE"
        out.append(Finding(S, lvl, f"Dernière décision journalière : {last_day} "
                           f"(attendue : {expected_day})",
                           "" if lvl == "OK" else "Décision manquée : vérifier "
                           "le journal (données ou réseau)."))
    if state.get("halted"):
        out.append(Finding(S, "ALERTE", f"Arrêt d'urgence actif : {state.get('halt_reason')}",
                           "Analyser la baisse, puis `python trendguard_bot.py resume` "
                           "(bot arrêté)."))
    if db_file and db_file != ":memory:" and os.path.exists(db_file):
        free = shutil.disk_usage(os.path.dirname(os.path.abspath(db_file))).free
        size = os.path.getsize(db_file)
        lvl = "OK" if free > 1e9 else "ATTENTION"
        out.append(Finding(S, lvl, f"Base {fr(size / 1e6, '.1f')} Mo, disque libre "
                           f"{fr(free / 1e9, '.1f')} Go",
                           "" if lvl == "OK" else "Libérer de l'espace disque."))
    return out


# ══════════════════════════════════════════════════════════════════════
# 2. DONNÉES
# ══════════════════════════════════════════════════════════════════════

def check_data(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams,
               expected_day: str, errors: Dict[str, str],
               held: List[str]) -> List[Finding]:
    """Qualité des données : historiques indisponibles, bougies en retard,
    jours manquants, prix aberrants, variations suspectes et paires trop
    peu liquides."""
    S = "Données"
    out: List[Finding] = []
    for a, err in errors.items():
        lvl = "ALERTE" if a in held or a == "btc" else "ATTENTION"
        out.append(Finding(S, lvl, f"{a.upper()} : historique indisponible ({err})",
                           "Paire retirée ou erreur réseau : vérifier sur Binance."))
    exp = pd.Timestamp(expected_day, tz="UTC")
    stale, gaps, bad, jumps, illiquid = [], [], [], [], []
    for a in close.columns:
        s = close[a].dropna()
        if s.empty:
            continue
        if s.index[-1] < exp:
            stale.append(f"{a.upper()} ({s.index[-1].date()})")
        recent = s[s.index >= exp - pd.Timedelta(days=400)]
        if len(recent) > 1:
            full = pd.date_range(recent.index[0], recent.index[-1], freq="D")
            missing = len(full) - len(recent)
            if missing > 0:
                gaps.append(f"{a.upper()} ({missing} j)")
        if (s <= 0).any():
            bad.append(a.upper())
        r = s.pct_change().abs()
        big = r[r.index >= exp - pd.Timedelta(days=90)]
        if (big > 0.6).any():
            jumps.append(f"{a.upper()} ({big.max() * 100:.0f} %)")
        v30 = volume[a].rolling(30, min_periods=10).mean().iloc[-1] \
            if a in volume else np.nan
        if not (np.isfinite(v30) and v30 >= p.min_volume_usd):
            illiquid.append(f"{a.upper()} ({fr((v30 if np.isfinite(v30) else 0) / 1e6, '.1f')} M$)")
    n = len(close.columns)
    out.append(Finding(S, "ALERTE" if stale else "OK",
                       f"Bougie du {expected_day} disponible pour "
                       f"{n - len(stale)}/{n} paires"
                       + (f" ; en retard : {', '.join(stale)}" if stale else ""),
                       "Données en retard : la décision du jour serait faussée." if stale else ""))
    if gaps:
        out.append(Finding(S, "ATTENTION", f"Jours manquants (400 j) : {', '.join(gaps)}",
                           "Suspension de cotation ? Les indicateurs sont moins fiables."))
    if bad:
        out.append(Finding(S, "ALERTE", f"Prix nuls ou négatifs : {', '.join(bad)}",
                           "Données corrompues : ne pas trader ces paires."))
    if jumps:
        out.append(Finding(S, "ATTENTION", f"Variation journalière > 60 % (90 j) : "
                           f"{', '.join(jumps)}",
                           "Vérifier qu'il s'agit d'un vrai mouvement et non d'une "
                           "erreur de cotation."))
    if illiquid:
        out.append(Finding(S, "INFO", f"Liquidité Binance < {p.min_volume_usd / 1e6:.0f} M$/j, "
                           f"exclues des achats : {', '.join(illiquid)}"))
    return out


# ══════════════════════════════════════════════════════════════════════
# 3. MARCHÉ   4. SIGNAUX
# ══════════════════════════════════════════════════════════════════════

def check_market(close: pd.DataFrame, p: ts.TrendParams) -> List[Finding]:
    """Régime du marché : BTC au-dessus ou au-dessous de sa moyenne, depuis
    combien de jours, et changements de régime fréquents."""
    S = "Marché"
    btc = close["btc"].dropna()
    sma = btc.rolling(p.regime_sma, min_periods=p.regime_sma).mean()
    bull = (btc > sma)
    run = bull.groupby(bull.ne(bull.shift()).cumsum()).cumcount() + 1
    last120 = bull.iloc[-120:]
    flips = int((last120 != last120.shift()).sum()) - 1
    gap = (btc.iloc[-1] / sma.iloc[-1] - 1) * 100
    out = [Finding(S, "INFO",
                   f"Régime BTC {'HAUSSIER (achats autorisés)' if bull.iloc[-1] else 'BAISSIER (aucun achat, stops resserrés)'}"
                   f" depuis {int(run.iloc[-1])} j — BTC {fr(btc.iloc[-1], ',.0f')} vs "
                   f"moyenne {p.regime_sma} j {fr(sma.iloc[-1], ',.0f')} ({fr(gap, '+.1f')} %)")]
    if flips >= 4:
        out.append(Finding(S, "ATTENTION", f"Régime hésitant : {flips} changements en 120 j",
                           "Marché sans tendance : faux départs plus fréquents, "
                           "séries de petites pertes probables (normal pour la stratégie)."))
    if abs(gap) < 3:
        out.append(Finding(S, "INFO", "BTC proche de sa moyenne : changement de régime "
                           "possible à court terme"))
    rv = np.log(btc / btc.shift(1)).rolling(30).std() * math.sqrt(365)
    pct = float((rv.dropna() <= rv.iloc[-1]).mean() * 100)
    out.append(Finding(S, "INFO", f"Volatilité BTC 30 j : {rv.iloc[-1] * 100:.0f} % annualisée "
                       f"(percentile {pct:.0f} de l'historique)"))
    return out


def today_signals(close: pd.DataFrame, volume: pd.DataFrame,
                  p: ts.TrendParams) -> Tuple[bool, List[Dict[str, Any]]]:
    rows = []
    for a in close.columns:
        s = close[a].dropna()
        if len(s) < 2:
            continue
        f = ts.asset_features(s, p, volume[a].reindex(s.index) if a in volume else None)
        snap = {k: float(f[k].iloc[-1]) for k in
                ("close", "vol", "prior_high", "mom", "age", "vol30")}
        why = []
        if not snap["close"] > snap["prior_high"]:
            gap = (snap["close"] / snap["prior_high"] - 1) * 100
            why.append(f"pas de cassure ({fr(gap, '+.1f')} %)")
        if not snap["mom"] > 0:
            why.append("momentum négatif")
        if not (np.isfinite(snap["vol30"]) and snap["vol30"] >= p.min_volume_usd):
            why.append("liquidité")
        if snap["age"] < p.min_history:
            why.append("historique court")
        rows.append({"asset": a, "signal": ts.entry_signal(snap, p), "why": why,
                     **snap})
    bull = bool(ts.btc_regime(close["btc"].dropna(), p).iloc[-1])
    return bull, sorted(rows, key=lambda r: -r["mom"] if np.isfinite(r["mom"]) else 0)


def check_signals(close: pd.DataFrame, volume: pd.DataFrame,
                  p: ts.TrendParams, held: List[str]) -> List[Finding]:
    S = "Signaux"
    bull, rows = today_signals(close, volume, p)
    buys = [r["asset"].upper() for r in rows if r["signal"] and r["asset"] not in held]
    near = [f"{r['asset'].upper()} ({fr((r['close'] / r['prior_high'] - 1) * 100, '+.1f')} %)"
            for r in rows if not r["signal"] and np.isfinite(r["prior_high"])
            and r["close"] > 0.97 * r["prior_high"] and r["mom"] > 0]
    if not bull:
        return [Finding(S, "INFO", "Régime baissier : aucun achat possible"
                        + (f" (cassures ignorées : {', '.join(buys)})" if buys else ""))]
    out = [Finding(S, "INFO", f"Achats signalés : {', '.join(buys) if buys else 'aucun'}")]
    if near:
        out.append(Finding(S, "INFO", f"Proches d'une cassure (< 3 %) : {', '.join(near[:8])}"))
    return out


# ══════════════════════════════════════════════════════════════════════
# 5. PORTEFEUILLE
# ══════════════════════════════════════════════════════════════════════

def check_portfolio(holdings: List[Dict[str, Any]], close: pd.DataFrame,
                    prices: Dict[str, float], equity: float,
                    max_total_risk: float = 0.06,
                    kill_drawdown: float = 0.40) -> List[Finding]:
    """Un portefeuille rempli jusqu'aux plafonds de la stratégie est
    l'état NORMAL : seuls les dépassements nets sont signalés, sinon
    l'auto-diagnostic alerterait chaque semaine sans raison."""
    S = "Portefeuille"
    if not holdings:
        return [Finding(S, "INFO", "Aucune position ouverte (100 % USDT)")]
    out: List[Finding] = []
    lines, below = [], []
    risks, names = [], []
    for h in holdings:
        a = h["asset"]
        px = prices.get(a) or h["entry"]
        dist = (px / h["stop"] - 1) * 100 if h["stop"] > 0 else float("nan")
        lines.append(f"{a.upper()} {fr((px / h['entry'] - 1) * 100, '+.1f')} % "
                     f"(stop à {fr(dist, '.1f')} %)")
        if px <= h["stop"]:
            below.append(a.upper())
        risk_now = max(h["qty"] * (px - h["stop"]), 0.0)
        risks.append(risk_now)
        names.append(a)
    out.append(Finding(S, "INFO", f"{len(holdings)} position(s) : {' ; '.join(lines)}"))
    if below:
        out.append(Finding(S, "INFO", f"Sous leur stop de clôture : {', '.join(below)} "
                           f"→ vente à la prochaine décision si la clôture confirme"))
    # Position sans historique (erreur de téléchargement, déjà signalée dans
    # « Données ») : colonne vide plutôt qu'un KeyError qui ferait échouer
    # tout le diagnostic.
    held_close = close.reindex(columns=names)
    rets = np.log(held_close / held_close.shift(1)).iloc[-90:]
    r = np.array(risks)
    if len(names) > 1:
        c = rets.corr().fillna(0).to_numpy(copy=True)   # pandas 3 : .values en lecture seule
        np.fill_diagonal(c, 1.0)
        corr_risk = math.sqrt(max(float(r @ c @ r), 0.0))
        avg = float(c[np.triu_indices(len(names), 1)].mean())
    else:
        corr_risk, avg = float(r.sum()), 1.0
    tot = float(r.sum())
    # Equity nulle ou négative (jamais encore de décision en live) : évite
    # une ZeroDivisionError qui ferait échouer tout le diagnostic.
    eq = max(equity, 1e-9)
    # Le plafond borne le risque À L'ENTRÉE ; quand les positions gagnent,
    # l'écart au stop grandit (gains latents exposés) : marge de 50 %.
    lvl = "OK" if tot / eq <= max_total_risk * 1.5 else "ATTENTION"
    out.append(Finding(S, lvl, f"Perte si tous les stops sont touchés : {fr(tot, ',.0f')} USDT "
                       f"({fr(tot / eq * 100, '.1f')} % du capital, plafond à l'entrée "
                       f"{max_total_risk * 100:.0f} %) ; risque ajusté des "
                       f"corrélations {fr(corr_risk / eq * 100, '.1f')} %",
                       "" if lvl == "OK" else "Risque engagé nettement au-dessus du "
                       "plafond : vérifier les stops posés sur Binance."))
    if len(names) > 1:
        lvl = "ATTENTION" if avg > 0.7 else "INFO"
        out.append(Finding(S, lvl, f"Corrélation moyenne des positions (90 j) : {fr(avg, '.2f')}",
                           "Positions très liées : elles risquent d'être stoppées le "
                           "même jour (déjà vu : -6,7 R le 10/10/2025)." if lvl != "INFO" else ""))
    for shock in (0.20, 0.35):
        loss = sum(h["qty"] * (prices.get(h["asset"]) or h["entry"]) * shock
                   for h in holdings)
        # Scénario de stress : n'alerte que s'il approcherait l'arrêt
        # d'urgence (TG_KILL_DRAWDOWN).
        lvl = "INFO" if loss / eq < kill_drawdown * 0.75 else "ATTENTION"
        out.append(Finding(S, lvl, f"Krach instantané de −{shock * 100:.0f} % sans "
                           f"exécution des stops (gap) : −{fr(loss / eq * 100, '.1f')} % "
                           f"du capital",
                           "" if lvl == "INFO" else "Exposition très forte : un krach "
                           "avec gap approcherait l'arrêt d'urgence ; envisager "
                           "TG_RISK_PCT=0.005 ou moins de positions."))
    return out


# ══════════════════════════════════════════════════════════════════════
# 6. SANTÉ DE LA STRATÉGIE   7. RÉEL VS ATTENDU
# ══════════════════════════════════════════════════════════════════════

def strategy_health(close: pd.DataFrame, volume: pd.DataFrame,
                    p: ts.TrendParams, start: str = "2019-01-01"
                    ) -> Tuple[List[Finding], ts.PortfolioResult]:
    """Santé de la stratégie : backtest Binance depuis `start` avec les
    paramètres actuels, rendements sur 12 mois glissants et espérance des
    trades des 24 derniers mois. Renvoie les constats et le backtest."""
    S = "Stratégie"
    last = str(close.index[-1].date())
    res = ts.backtest(close, volume, p, start, last)
    m = res.metrics
    out = [Finding(S, "INFO", f"Backtest Binance {start} → {last} (paramètres actuels) : "
                   f"{fr(m['cagr_pct'], '+.1f')} %/an, baisse max {fr(m['max_dd_pct'], '.1f')} %, "
                   f"Sharpe {fr(m['sharpe'], '.2f')}, {m['trades']} trades, "
                   f"{m['win_rate_pct']:.0f} % gagnants, espérance "
                   f"{fr(m['expectancy_r'], '+.2f')} R")]
    eq = res.equity
    r12 = (eq / eq.shift(365) - 1).dropna() * 100
    if len(r12) > 100:
        cur = float(r12.iloc[-1])
        pct = float((r12 <= cur).mean() * 100)
        p10 = float(r12.quantile(0.10))
        lvl = "OK" if cur >= p10 else "ATTENTION"
        out.append(Finding(S, lvl, f"Rendement sur 12 mois glissants : {fr(cur, '+.1f')} % "
                           f"(percentile {pct:.0f} ; historique : P10 {fr(p10, '+.1f')} %, "
                           f"médiane {fr(r12.median(), '+.1f')} %, "
                           f"{(r12 < 0).mean() * 100:.0f} % des fenêtres négatives)",
                           "" if lvl == "OK" else "Année parmi les 10 % les plus faibles : "
                           "surveiller, sans conclure seul (ces années existent)."))
    hs = sl.horizon_success(eq, months=(12, 24, 36), n_boot=4000)
    hs = hs[hs["windows"] > 0]
    if len(hs):
        parts = [f"{int(r.months)} mois : {r.hist_gain_pct:.0f} % des fenêtres en gain"
                 + (f" (bootstrap {r.boot_gain_pct:.0f} %)" if "boot_gain_pct" in hs
                    and not pd.isna(r.boot_gain_pct) else "")
                 for r in hs.itertuples()]
        out.append(Finding(S, "INFO", "Probabilité historique de finir en gain selon "
                           "la durée — " + " ; ".join(parts) + " (indication, pas une "
                           "garantie ; trade par trade : 35-50 % de gagnants)"))
    dd_now = float((eq.iloc[-1] / eq.cummax().iloc[-1] - 1) * 100)
    lvl = "OK" if dd_now > -15 else "ATTENTION" if dd_now > -30 else "ALERTE"
    out.append(Finding(S, lvl, "Baisse actuelle de la stratégie depuis son pic : "
                       f"{fr(dd_now, '.1f')} %"))
    cut = eq.index[-1] - pd.Timedelta(days=730)
    recent = np.array([t["r"] for t in res.trades if t["exit_date"] >= cut])
    if len(recent) >= 15:
        mean, lo, hi = bootstrap_mean_ci(recent)
        if hi < 0:
            lvl, reco = "ALERTE", ("L'avantage statistique semble avoir disparu : "
                                   "réduire fortement le risque ou arrêter, et relancer "
                                   "la recherche.")
        elif mean < 0:
            lvl, reco = "ATTENTION", ("Espérance récente négative mais non significative : "
                                      "envisager le profil prudent (TG_DD_THROTTLE).")
        else:
            lvl, reco = "OK", ""
        out.append(Finding(S, lvl, f"Espérance des {len(recent)} trades des 24 derniers mois : "
                           f"{fr(mean, '+.2f')} R (intervalle 90 % : {fr(lo, '+.2f')} à "
                           f"{fr(hi, '+.2f')} R)", reco))
    else:
        out.append(Finding(S, "INFO", f"Seulement {len(recent)} trades sur 24 mois : "
                           f"espérance récente non mesurable"))
    return out, res


def check_alternatives(close: pd.DataFrame, volume: pd.DataFrame,
                       p: ts.TrendParams, days: int = 730) -> List[Finding]:
    """Tournoi des stratégies du laboratoire sur les `days` derniers jours.
    Information seulement : suivre le meilleur récent a fait moins bien que
    la stratégie fixe (docs/STRATEGIES.md)."""
    S = "Alternatives"
    board = sl.tournament_recent(sl.Lab(close, volume, p), days=days)
    ref = board[board["name"] == sl.REF].iloc[0]
    rank = int(board.index[board["name"] == sl.REF][0]) + 1
    lines = [f"{k + 1}. {r.name} : Sharpe {fr(r.sharpe, '.2f')}, {fr(r.total_pct, '+.0f')} %, "
             f"{r.trades} trades, {r.win_rate_pct:.0f} % gagnants"
             for k, r in enumerate(board.itertuples())]
    out = [Finding(S, "INFO", f"Tournoi des {len(board)} stratégies sur "
                   f"{days // 365} ans (1 % de risque par trade) — TrendGuard "
                   f"rang {rank}/{len(board)}\n      " + "\n      ".join(lines))]
    strong = int((board["sharpe"] > 0.5).sum())
    if ref.sharpe < 0 and strong >= len(board) // 2 + 1:
        out.append(Finding(S, "ATTENTION", f"TrendGuard négative sur {days // 365} ans "
                           f"alors que {strong} alternatives sont nettement positives",
                           "Relancer l'étude complète (python trendguard_bot.py lab) avant "
                           "toute décision : changer de stratégie sur ce seul classement "
                           "a fait moins bien historiquement (docs/STRATEGIES.md)."))
    else:
        out.append(Finding(S, "OK", "Aucune alternative ne justifie de revoir la "
                           "stratégie (classement indicatif, jamais appliqué "
                           "automatiquement)"))
    return out


def check_watch(state: Dict[str, Any], held: List[str],
                today: Optional[str] = None) -> List[Finding]:
    """Veille (market_watch.py) : vetos officiels actifs et dernier avis des IA."""
    S = "Veille"
    out: List[Finding] = []
    for a, v in sorted((state.get("vetoes") or {}).items()):
        if today and v.get("until", "") < today:
            continue
        lvl = "ALERTE" if a in held else "INFO"
        out.append(Finding(S, lvl, f"Achats de {a.upper()} bloqués : {v.get('reason')} "
                           f"(jusqu'au {v.get('until')})",
                           f"Position détenue que Binance va retirer : la vendre avant la date "
                           f"annoncée ({v.get('url')})." if lvl == "ALERTE" else ""))
    last = state.get("last_watch")
    if not last:
        out.append(Finding(S, "INFO", "Veille par IA pas encore exécutée (quotidienne quand le "
                           "bot tourne ; clés d'IA : python trendguard_bot.py watch set-key <ia>)"))
        return out
    n, tot = last.get("providers", 0), last.get("providers_total", 0)
    who = f"{n}/{tot} IA" if tot else "mots-clés seulement (aucune IA configurée)"
    out.append(Finding(S, "INFO", f"Dernière veille {last.get('day')} : climat "
                       f"{fr(last.get('sentiment', 0), '+.2f')} ({who})"
                       + ("".join(f"\n      • {t}" for t in last.get("alerts") or []))))
    if tot and n == 0:
        out.append(Finding(S, "ATTENTION", "Aucune IA n'a répondu à la dernière veille",
                           "Vérifier les clés et les modèles : python trendguard_bot.py watch check."))
    return out


def live_vs_expected(bot_trades: List[Dict[str, Any]],
                     reference: List[Dict[str, Any]]) -> List[Finding]:
    """Trades réels du bot face à l'historique : R moyen, série de pertes en
    cours et probabilité qu'un résultat aussi faible sorte de l'historique."""
    S = "Réel vs attendu"
    rs = np.array([float(t["r"]) for t in bot_trades if "r" in t])
    ref = np.array([float(t["r"]) for t in reference])
    if len(rs) == 0:
        return [Finding(S, "INFO", "Aucun trade clos par le bot pour l'instant")]
    streak = 0
    for r in rs[::-1]:
        if r > 0:
            break
        streak += 1
    msg = (f"{len(rs)} trade(s) clos : {int((rs > 0).sum())} gagnant(s), R moyen "
           f"{fr(rs.mean(), '+.2f')} (historique {fr(ref.mean(), '+.2f')} R) ; série de pertes "
           f"en cours : {streak}")
    if len(rs) < 8 or len(ref) < 30:
        return [Finding(S, "INFO", msg + " — trop peu de trades pour conclure")]
    rng = np.random.default_rng(5)
    sims = rng.choice(ref, size=(20000, len(rs)), replace=True).mean(axis=1)
    pval = float((sims <= rs.mean()).mean())
    draws = rng.choice(ref, size=(4000, 60), replace=True)
    streaks = []
    for row in draws:
        best = cur = 0
        for r in row:
            cur = cur + 1 if r <= 0 else 0
            best = max(best, cur)
        streaks.append(best)
    s95 = float(np.percentile(streaks, 95))
    if pval < 0.02:
        lvl, reco = "ALERTE", ("Résultats réels incompatibles avec l'historique "
                               "(moins de 2 % de chances) : vérifier l'exécution "
                               "(prix, frais, retards) et réduire le risque.")
    elif pval < 0.10 or streak > s95:
        lvl, reco = "ATTENTION", "Résultats en bas de la fourchette attendue : surveiller."
    else:
        lvl, reco = "OK", ""
    return [Finding(S, lvl, msg + f" — probabilité historique d'un résultat aussi "
                    f"faible : {pval * 100:.0f} % ; série de pertes P95 attendue : "
                    f"{s95:.0f}", reco)]


# ══════════════════════════════════════════════════════════════════════
# ORCHESTRATION ET RENDU
# ══════════════════════════════════════════════════════════════════════

def run_diagnosis(exchange: Any, p: ts.TrendParams, bases: List[str],
                  state: Dict[str, Any], holdings: List[Dict[str, Any]],
                  equity: float, expected_day: str, now: datetime,
                  db_file: str = "", running: Optional[bool] = None,
                  quote: str = "USDT", kill_drawdown: float = 0.40,
                  sections: Tuple[str, ...] = (
                      "system", "data", "market", "signals", "portfolio",
                      "strategy", "live", "alternatives", "watch")) -> List[Finding]:
    """Diagnostic complet en lecture seule, par sections (`sections`) :
    système, veille, données, marché, signaux, portefeuille, stratégie,
    réel face à l'attendu et stratégies alternatives."""
    findings: List[Finding] = []
    if "system" in sections:
        findings += check_system(exchange, state, expected_day, db_file, running, now)
    if "watch" in sections:
        findings += check_watch(state, [h["asset"] for h in holdings], now.date().isoformat())
    close, volume, errors = fetch_daily_history(exchange, bases, quote, now=now)
    if "btc" not in close:
        findings.append(Finding("Données", "ALERTE", "Historique BTC indisponible : "
                                "diagnostic de marché impossible",
                                "Vérifier la connexion à Binance."))
        return findings
    held = [h["asset"] for h in holdings]
    prices = {a: float(close[a].dropna().iloc[-1]) for a in close.columns
              if close[a].notna().any()}
    if "data" in sections:
        findings += check_data(close, volume, p, expected_day, errors, held)
    if "market" in sections:
        findings += check_market(close, p)
    if "signals" in sections:
        findings += check_signals(close, volume, p, held)
    if "portfolio" in sections:
        findings += check_portfolio(holdings, close, prices, max(equity, 1e-9),
                                    p.max_total_risk, kill_drawdown)
    res = None
    if "strategy" in sections or "live" in sections:
        health, res = strategy_health(close, volume, p)
        if "strategy" in sections:
            findings += health
    if "live" in sections and res is not None:
        findings += live_vs_expected(state.get("trades", []), res.trades)
    if "alternatives" in sections:
        try:
            findings += check_alternatives(close, volume, p)
        except Exception as e:
            findings.append(Finding("Alternatives", "INFO",
                                    f"Tournoi indisponible : {type(e).__name__}: {e}"))
    return findings


def verdict(findings: List[Finding]) -> str:
    worst = max((LEVELS.index(f.level) for f in findings), default=0)
    return LEVELS[worst]


def render(findings: List[Finding], title: str = "") -> str:
    lines = ["═" * 78, f"DIAGNOSTIC TRENDGUARD {title}".rstrip(), "═" * 78]
    section = None
    for f in findings:
        if f.section != section:
            section = f.section
            lines.append(f"\n── {section}")
        lines.append(f"  {ICONS[f.level]} {f.message}")
    v = verdict(findings)
    lines.append("\n" + "═" * 78)
    lines.append({"OK": "✅ VERDICT : tout est conforme.",
                  "INFO": "✅ VERDICT : tout est conforme.",
                  "ATTENTION": "⚠️  VERDICT : points à surveiller.",
                  "ALERTE": "❌ VERDICT : action requise."}[v])
    recos = [f for f in findings if f.reco and f.level in ("ATTENTION", "ALERTE")]
    for i, f in enumerate(recos, 1):
        lines.append(f"  {i}. [{f.section}] {f.reco}")
    lines.append("")
    lines.append("Rappel : aucune stratégie ne garantit 99 % de réussite. Celle-ci "
                 "vise ~1 % de perte par trade et des gains de plusieurs R, avec "
                 "35-50 % de trades gagnants ; le succès se mesure sur la durée "
                 "(docs/STRATEGIES.md).")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit("Utiliser : python trendguard_bot.py diagnose")
