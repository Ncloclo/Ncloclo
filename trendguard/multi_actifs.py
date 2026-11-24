"""
Au-delà des cryptos : les règles de TrendGuard appliquées aux autres classes
d'actifs (docs/MULTI_ACTIFS.md) : matières premières, devises, indices
boursiers, obligations, et à part les actions.

Le suivi de tendance est l'un des effets les mieux documentés sur toutes les
classes d'actifs (Moskowitz, Ooi et Pedersen 2012, 58 contrats à terme), et
ces marchés évoluent souvent indépendamment des cryptos. Ce module fournit
l'univers, l'historique journalier (Yahoo Finance, sans clé, gardé dans
data_multi/) et la règle de TrendGuard transposée, déclarée à l'avance :

  - mêmes paramètres gelés (cassure de 30 séances, momentum de 90, stop
    initial à 3 volatilités, stop suiveur à 5, 1 % de risque par position,
    8 positions, 6 % de risque cumulé, 25 % du capital par position) ;
  - régime : la propre tendance de chaque actif (clôture au-dessus de sa
    moyenne de 150 séances) au lieu de BTC, qui ne dit rien de l'or ;
  - pas de filtre de liquidité (marchés parmi les plus liquides du monde),
    frais et glissement de 0,1 % par côté comme pour les cryptos ;
  - achats seulement, sans levier ; rendements en monnaie locale (change
    supposé couvert).

Information et études : le bot ne passe aucun ordre sur ces marchés. Sur
Binance, seuls l'or tokenisé (PAXG/USDT) et l'euro (EUR/USDT) se traitent ;
le reste demande un autre courtier. Le franc CFA (XOF) a une parité fixe avec
l'euro (655,957) : il n'a pas de tendance à suivre.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import trend_strategy as ts
from .texte import fr

# (code, nom, catégorie, symbole Yahoo). Les actions sont à part : choisies
# parmi les plus grandes d'aujourd'hui, leur passé est flatté (biais du
# survivant) ; elles ne comptent dans aucun verdict.
UNIVERSE: Tuple[Tuple[str, str, str, str], ...] = (
    ("or", "Or (once, $)", "Matières premières", "GC=F"),
    ("argent", "Argent (once, $)", "Matières premières", "SI=F"),
    ("wti", "Pétrole WTI", "Matières premières", "CL=F"),
    ("brent", "Pétrole Brent", "Matières premières", "BZ=F"),
    ("gaz", "Gaz naturel", "Matières premières", "NG=F"),
    ("cuivre", "Cuivre", "Matières premières", "HG=F"),
    ("eurusd", "EUR/USD", "Devises", "EURUSD=X"),
    ("gbpusd", "GBP/USD", "Devises", "GBPUSD=X"),
    ("usdjpy", "USD/JPY", "Devises", "JPY=X"),
    ("usdchf", "USD/CHF", "Devises", "CHF=X"),
    ("audusd", "AUD/USD", "Devises", "AUDUSD=X"),
    ("usdcad", "USD/CAD", "Devises", "CAD=X"),
    ("sp500", "S&P 500", "Indices", "^GSPC"),
    ("nasdaq100", "Nasdaq 100", "Indices", "^NDX"),
    ("dow", "Dow Jones", "Indices", "^DJI"),
    ("cac40", "CAC 40", "Indices", "^FCHI"),
    ("dax", "DAX", "Indices", "^GDAXI"),
    ("nikkei", "Nikkei 225", "Indices", "^N225"),
    ("ftse", "FTSE 100", "Indices", "^FTSE"),
    ("tlt", "Obligations d'État US à plus de 20 ans (ETF TLT)", "Obligations", "TLT"),
    ("ief", "Obligations d'État US à 7-10 ans (ETF IEF)", "Obligations", "IEF"),
    ("nvda", "Nvidia", "Actions", "NVDA"),
    ("aapl", "Apple", "Actions", "AAPL"),
    ("msft", "Microsoft", "Actions", "MSFT"),
    ("amzn", "Amazon", "Actions", "AMZN"),
    ("googl", "Alphabet", "Actions", "GOOGL"),
    ("meta", "Meta", "Actions", "META"),
    ("tsla", "Tesla", "Actions", "TSLA"),
    ("tte", "TotalEnergies", "Actions", "TTE.PA"),
    ("mc", "LVMH", "Actions", "MC.PA"),
)
# Hors cryptos, mais tradables sur Binance (jetons) : la manche que l'étude
# docs/BINANCE_HORS_CRYPTOS.md propose de suivre en paper. (code, nom et
# jeton, catégorie, symbole Yahoo du sous-jacent). Écartés : fonds à levier,
# sociétés non cotées, cotations trop récentes.
BINANCE: Tuple[Tuple[str, str, str, str], ...] = (
    ("or", "Or (PAXG, XAUT)", "Matières premières", "GC=F"),
    ("eurusd", "Euro (EUR/USDT)", "Devises", "EURUSD=X"),
    ("nvda", "Nvidia (NVDAB)", "Actions", "NVDA"), ("mu", "Micron (MUB)", "Actions", "MU"),
    ("googl", "Alphabet (GOOGLB)", "Actions", "GOOGL"), ("intc", "Intel (INTCB)", "Actions", "INTC"),
    ("mstr", "Strategy (MSTRB)", "Actions", "MSTR"), ("be", "Bloom Energy (BEB)", "Actions", "BE"),
    ("qqq", "Fonds Nasdaq 100 (QQQB)", "Indices", "QQQ"),
)
# Ajoutés le 24/11/2026 d'après la synthèse des marchés de TradingView : les
# grands marchés du monde que le bot ne suivait pas encore. Cours lus chez
# Yahoo Finance comme les autres (jamais sur TradingView, qui interdit la
# collecte automatique). Suivis chaque jour dans les pages du panneau ; ils
# n'entrent dans le portefeuille fictif que si l'étude
# docs/MARCHES_AJOUTES.md les retient (ADDED_IN_PORTFOLIO).
ADDED: Tuple[Tuple[str, str, str, str], ...] = (
    ("platine", "Platine (once, $)", "Matières premières", "PL=F"),
    ("palladium", "Palladium (once, $)", "Matières premières", "PA=F"),
    ("aluminium", "Aluminium (tonne, $)", "Matières premières", "ALI=F"),
    ("essence", "Essence RBOB (gallon, $)", "Matières premières", "RB=F"),
    ("fioul", "Fioul (gallon, $)", "Matières premières", "HO=F"),
    ("ble", "Blé", "Matières premières", "ZW=F"),
    ("mais", "Maïs", "Matières premières", "ZC=F"),
    ("soja", "Soja", "Matières premières", "ZS=F"),
    ("cafe", "Café", "Matières premières", "KC=F"),
    ("sucre", "Sucre", "Matières premières", "SB=F"),
    ("coton", "Coton", "Matières premières", "CT=F"),
    ("nzdusd", "NZD/USD", "Devises", "NZDUSD=X"),
    ("dxy", "Indice du dollar (DXY)", "Devises", "DX-Y.NYB"),
    ("russell2000", "Russell 2000", "Indices", "^RUT"),
    ("nasdaqcomp", "Nasdaq Composite", "Indices", "^IXIC"),
    ("ftsemib", "FTSE MIB", "Indices", "FTSEMIB.MI"),
    ("ibex", "IBEX 35", "Indices", "^IBEX"),
    ("shanghai", "Shanghai Composite", "Indices", "000001.SS"),
    ("hangseng", "Hang Seng", "Indices", "^HSI"),
    ("nifty", "Nifty 50", "Indices", "^NSEI"),
    ("bovespa", "Bovespa", "Indices", "^BVSP"),
    ("shy", "Obligations d'État US à 1-3 ans (ETF SHY)", "Obligations", "SHY"),
    ("tsm", "TSMC (certificat américain)", "Actions", "TSM"),
    ("amd", "AMD", "Actions", "AMD"),
    ("ant", "Anthropic", "Actions", "ANT"),
    ("spcx", "SpaceX", "Actions", "SPCX"),
)
ADDED_IN_PORTFOLIO = False           # décision de l'étude docs/MARCHES_AJOUTES.md
# Le principal indice boursier de chaque pays classé par MSCI (trendguard/pays.py,
# docs/PAYS.md), quand Yahoo Finance en a l'historique ; sinon le fonds coté à
# New York qui suit l'indice MSCI du pays (en dollars). Suivis pour information.
WORLD: Tuple[Tuple[str, str, str, str], ...] = (
    ("ca_tsx", "S&P/TSX (Canada)", "Indices", "^GSPTSE"),
    ("at_atx", "ATX (Autriche)", "Indices", "^ATX"),
    ("be_bel20", "BEL 20 (Belgique)", "Indices", "^BFX"),
    ("dk_omxc25", "OMX Copenhague 25 (Danemark)", "Indices", "^OMXC25"),
    ("fi_omxh25", "OMX Helsinki 25 (Finlande)", "Indices", "^OMXH25"),
    ("ie_iseq", "ISEQ (Irlande)", "Indices", "^ISEQ"),
    ("il_ta35", "TA-35 (Israël)", "Indices", "TA35.TA"),
    ("nl_aex", "AEX (Pays-Bas)", "Indices", "^AEX"),
    ("no_obx", "OBX (Norvège)", "Indices", "OBX.OL"),
    ("pt_psi", "PSI (Portugal)", "Indices", "PSI20.LS"),
    ("se_omxs30", "OMX Stockholm 30 (Suède)", "Indices", "^OMX"),
    ("ch_smi", "SMI (Suisse)", "Indices", "^SSMI"),
    ("au_asx200", "S&P/ASX 200 (Australie)", "Indices", "^AXJO"),
    ("nz_nzx50", "S&P/NZX 50 (Nouvelle-Zélande)", "Indices", "^NZ50"),
    ("sg_sti", "STI (Singapour)", "Indices", "^STI"),
    ("cl_ech", "Fonds MSCI Chili (ECH, en $)", "Indices", "ECH"),
    ("co_colcap", "Fonds COLCAP (Colombie)", "Indices", "ICOLCAP.CL"),
    ("mx_ipc", "IPC (Mexique)", "Indices", "^MXX"),
    ("pe_epu", "Fonds MSCI Pérou (EPU, en $)", "Indices", "EPU"),
    ("gr_athex", "Indice général d'Athènes (Grèce)", "Indices", "GD.AT"),
    ("kw_kwt", "Fonds MSCI Koweït (KWT, en $)", "Indices", "KWT"),
    ("pl_epol", "Fonds MSCI Pologne (EPOL, en $)", "Indices", "EPOL"),
    ("qa_qat", "Fonds MSCI Qatar (QAT, en $)", "Indices", "QAT"),
    ("sa_ksa", "Fonds MSCI Arabie saoudite (KSA, en $)", "Indices", "KSA"),
    ("za_jse", "FTSE/JSE All Share (Afrique du Sud)", "Indices", "^J203.JO"),
    ("tr_bist100", "BIST 100 (Turquie)", "Indices", "XU100.IS"),
    ("ae_uae", "Fonds MSCI Émirats arabes unis (UAE, en $)", "Indices", "UAE"),
    ("id_jci", "Jakarta Composite (Indonésie)", "Indices", "^JKSE"),
    ("kr_kospi", "KOSPI (Corée du Sud)", "Indices", "^KS11"),
    ("my_klci", "FTSE Bursa Malaysia KLCI (Malaisie)", "Indices", "^KLSE"),
    ("ph_ephe", "Fonds MSCI Philippines (EPHE, en $)", "Indices", "EPHE"),
    ("tw_taiex", "TAIEX (Taïwan)", "Indices", "^TWII"),
    ("th_thd", "Fonds MSCI Thaïlande (THD, en $)", "Indices", "THD"),
    ("ee_omxt", "OMX Tallinn (Estonie)", "Indices", "^OMXTGI"),
    ("lv_omxr", "OMX Riga (Lettonie)", "Indices", "^OMXRGI"),
    ("lt_omxv", "OMX Vilnius (Lituanie)", "Indices", "^OMXVGI"),
    ("vn_vn30", "Fonds VN30 (Viêt Nam)", "Indices", "E1VFVN30.VN"),
    ("ar_argt", "Fonds MSCI Argentine (ARGT, en $)", "Indices", "ARGT"),
)
# Devises des pays (cours du dollar dans chaque devise, « XXX=X » chez Yahoo),
# pour la page des pays ; l'euro, la livre, le yen… ont déjà leur paire.
CURRENCIES: Tuple[str, ...] = tuple(
    "DKK ILS NOK SEK HKD SGD BRL CLP COP MXN PEN CZK EGP HUF KWD PLN QAR SAR ZAR TRY AED CNY INR IDR KRW "
    "MYR PHP TWD THB RON BHD ISK JOD KZT KES MUR MAD OMR RSD TND XOF BDT PKR LKR VND ARS JMD TTD LBP NGN "
    "UAH".split())
CURRENCY_CODES = [f"fx_{c.lower()}" for c in CURRENCIES]
# Jetons Binance d'actions cotées depuis peu (affichage ; hors de la manche Binance).
TOKENS = {"ant": "Anthropic (ANTB)", "spcx": "SpaceX (SPCXB)"}
# Indicateurs de marché, pas des marchés à trader : volatilité attendue du
# S&P 500 et taux de l'État américain, lus chaque jour pour la veille et
# l'agent macro du comité (information ; étude docs/MACRO.md).
INDICATORS: Tuple[Tuple[str, str, str], ...] = (
    ("vix", "Volatilité du S&P 500 (VIX)", "^VIX"), ("us3m", "Taux US à 3 mois", "^IRX"),
    ("us5y", "Taux US à 5 ans", "^FVX"), ("us10y", "Taux US à 10 ans", "^TNX"),
    ("us30y", "Taux US à 30 ans", "^TYX"))
BINANCE_NAME = {c: n for c, n, _k, _s in BINANCE}
_MARKETS = BINANCE + UNIVERSE + ADDED + WORLD  # un nom de l'univers l'emporte sur celui du jeton
NAME = {**{c: n for c, n, _k, _s in _MARKETS}, **{c: n for c, n, _s in INDICATORS},
        **{f"fx_{c.lower()}": f"USD/{c}" for c in CURRENCIES}}
CATEGORY = {c: k for c, _n, k, _s in _MARKETS}
SYMBOL = {**{c: s for c, _n, _k, s in _MARKETS}, **{c: s for c, _n, s in INDICATORS},
          **{f"fx_{c.lower()}": f"{c}=X" for c in CURRENCIES}}
ALL_CODES = list(dict.fromkeys(c for c, *_ in UNIVERSE + BINANCE + ADDED + WORLD))
INDICATOR_CODES = [c for c, *_ in INDICATORS]
# Marchés du portefeuille fictif hors cryptos (sans les actions, voir unbiased).
PORTFOLIO_CODES = [c for c, *_ in UNIVERSE] + ([c for c, *_ in ADDED] if ADDED_IN_PORTFOLIO else [])
CATEGORIES = ("Matières premières", "Devises", "Indices", "Obligations", "Actions")
BIASED = {"Actions"}                 # mesurées à part, jamais dans un verdict
CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?period1={p1}&period2={p2}&interval=1d")
START = "2015-01-01"                 # assez d'historique avant 2018 (moyenne 150, momentum 90)
REGIME_N = 150                       # régime : la propre tendance de chaque actif
# Place fermée (jour férié, Nouvel An chinois, semaine d'or) : dernier cours
# gardé 10 séances au plus ; au-delà, la boucle y voit un retrait de la cote.
FILL_LIMIT = 10
# Volume quotidien supposé (dollars) : ces marchés sont parmi les plus liquides
# du monde ; une liquidité inconnue ferait refuser tout achat (entry_signal).
LIQUID = 1e12
# Coût réel par côté (frais et écart compris), typique d'un compte de
# particulier, déclaré avant les mesures. Les frais de Binance (0,2 % par côté
# avec le glissement) mangeraient 23 à 45 % du risque de chaque trade sur les
# devises, les indices et les obligations, dont le stop est proche (règle du
# poids des frais : frais aller-retour / distance du stop).
COSTS = {"Matières premières": 0.0005, "Devises": 0.0001, "Indices": 0.0002, "Obligations": 0.0002,
         "Actions": 0.0005}

Fetch = Callable[[str], bytes]


def parse_chart(raw: bytes) -> pd.Series:
    """Clôtures journalières d'une réponse Yahoo (jour de la place de
    cotation) ; vide si la réponse est illisible."""
    res = ((json.loads(raw).get("chart") or {}).get("result") or [None])[0]
    if not res:
        return pd.Series(dtype=float)
    off = int((res.get("meta") or {}).get("gmtoffset") or 0)
    closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    rows = {datetime.fromtimestamp(int(t) + off, timezone.utc).date(): float(c)
            for t, c in zip(res.get("timestamp") or [], closes) if c is not None and c > 0}
    s = pd.Series(rows, dtype=float).sort_index()
    s.index = pd.DatetimeIndex(s.index).tz_localize("UTC")
    return s


def download(cache: str, fetch: Fetch, codes: Optional[List[str]] = None,
             start: str = START, pause: float = 1.0,
             symbols: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Télécharge l'historique journalier de chaque actif dans `cache`
    (un fichier par actif : date, clôture). `symbols` : {code : symbole
    Yahoo} pour des actifs hors de l'univers. Rend {code: erreur} pour ceux
    qui ont échoué ; un échec n'arrête pas les autres."""
    os.makedirs(cache, exist_ok=True)
    p1 = int(pd.Timestamp(start, tz="UTC").timestamp())
    p2 = int(time.time())
    known = dict(SYMBOL, **(symbols or {}))
    errors = {}
    for code in codes or list(symbols or {}) or [c for c, *_ in UNIVERSE]:
        url = CHART.format(sym=urllib.parse.quote(known[code]), p1=p1, p2=p2)
        try:
            s = parse_chart(fetch(url))
            if s.empty:
                raise ValueError("historique vide")
            s.rename("close").to_frame().to_csv(os.path.join(cache, f"{code}.csv"), index_label="date")
        except Exception as e:
            errors[code] = f"{type(e).__name__} : {str(e)[:120]}"
        if pause:
            time.sleep(pause)
    return errors


def load(cache: str, codes: Optional[List[str]] = None) -> pd.DataFrame:
    """Clôtures des actifs gardés dans `cache`, sur le calendrier commun des
    séances (un jour férié garde le dernier cours, 5 séances au plus)."""
    series = {}
    for code in codes or [c for c, *_ in UNIVERSE]:
        path = os.path.join(cache, f"{code}.csv")
        if os.path.exists(path):
            df = pd.read_csv(path, parse_dates=["date"])
            s = df.set_index("date")["close"].astype(float)
            s.index = pd.DatetimeIndex(s.index).tz_localize("UTC") if s.index.tz is None else s.index
            series[code] = s
    if not series:
        return pd.DataFrame()
    close = pd.DataFrame(series).sort_index()
    return close.ffill(limit=FILL_LIMIT)


def sessions(close: pd.Series) -> pd.Series:
    """Les séances réelles d'un marché dans le calendrier commun : sans les
    jours où sa place était fermée (cours recopié, donc inchangé). Compter ces
    jours diluerait sa volatilité et rapprocherait ses stops."""
    s = close.dropna().astype(float)
    return s[s.ne(s.shift())]


def own_trend(close: pd.DataFrame, n: int = REGIME_N) -> Dict[str, np.ndarray]:
    """Régime de chaque actif : clôture au-dessus de sa moyenne de `n`
    séances réelles (connue le jour même, à la clôture, comme le régime BTC),
    reporté sur les jours où sa place est fermée."""
    out = {}
    for a in close.columns:
        s = sessions(close[a])
        up = (s > s.rolling(n, min_periods=n).mean()).astype(float)
        out[a] = up.reindex(close.index).ffill(limit=FILL_LIMIT).fillna(0.0).to_numpy() > 0.5
    return out


def features(close: pd.DataFrame, p: ts.TrendParams) -> Dict[str, Dict[str, np.ndarray]]:
    """Indicateurs de la règle pour chaque actif, calculés sur ses seules
    séances puis reportés sur le calendrier commun ; un jour où sa place est
    fermée, aucune cassure n'est possible. Le filtre de liquidité est
    toujours franchi (volume supposé LIQUID)."""
    out = {}
    for a in close.columns:
        s = sessions(close[a])
        f = ts.asset_features(s, p, pd.Series(LIQUID, index=s.index))
        g = f.reindex(close.index).ffill(limit=FILL_LIMIT)
        g.loc[~close.index.isin(s.index), "prior_high"] = np.inf
        out[a] = {k: g[k].to_numpy(dtype=float) for k in ("close", "vol", "prior_high", "mom", "age", "vol30")}
    return out


def backtest(close: pd.DataFrame, start: str, end: str, p: Optional[ts.TrendParams] = None,
             capital: float = 10_000.0, real_costs: bool = False) -> ts.PortfolioResult:
    """La boucle de backtest du bot sur ces actifs, chacun avec son propre
    régime (au lieu de BTC). `real_costs` : coût réel de chaque marché
    (COSTS) au lieu des frais de Binance."""
    p = p or ts.TrendParams()
    cols, trend = features(close, p), own_trend(close)
    always = np.ones(len(close.index), dtype=bool)

    def choose(i: int, snap: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        return {a: s for a, s in snap.items() if trend[a][i]}
    hooks = ts.BacktestHooks(choose=choose)
    if real_costs:
        p = dataclasses.replace(p, fee=0.0, slippage=0.0)
        hooks = ts.BacktestHooks(choose=choose, exec_cost=lambda a, _n, _side: COSTS.get(CATEGORY.get(a, ""), 0.001))
    return ts.backtest(close, None, p, start, end, capital=capital, pre=(cols, always), hooks=hooks)


def of_category(close: pd.DataFrame, category: str) -> pd.DataFrame:
    return close[[c for c in close.columns if CATEGORY.get(c) == category]]


def unbiased(close: pd.DataFrame) -> pd.DataFrame:
    """Les actifs qui comptent dans un verdict : tout sauf les actions."""
    return close[[c for c in close.columns if CATEGORY.get(c) not in BIASED]]


def summary(close: pd.DataFrame) -> List[Dict[str, Any]]:
    """État du jour de chaque actif pour la règle : tendance propre,
    cassure, momentum (information seulement)."""
    if close.empty:
        return []
    p = ts.TrendParams()
    cols, trend = features(close, p), own_trend(close)
    rows = []
    for a in close.columns:
        c = cols[a]
        snap = {k: float(v[-1]) for k, v in c.items()}
        rows.append({"code": a, "name": NAME.get(a, a), "category": CATEGORY.get(a, ""),
                     "close": snap["close"], "trend": bool(trend[a][-1]),
                     "signal": bool(trend[a][-1] and ts.entry_signal(snap, p)),
                     "binance": BINANCE_NAME.get(a, "")})
    return rows


# ══════════════════════════════════════════════════════════════════════
# SUIVI QUOTIDIEN : tous les marchés, et un portefeuille en argent fictif
# ══════════════════════════════════════════════════════════════════════

KEY = "multi_actifs"                # méta du noyau de savoir
RETRY_HOURS = 6                     # après une panne
CURVE_POINTS = 120                  # points de la courbe gardés pour le panneau


READ_AFTER = (22, 30)                # heure UTC : New York a clos (21:00 ou 22:00 UTC), et toutes les places avant elle


def read_mark(now: datetime) -> datetime:
    """Dernier passage de READ_AFTER : une lecture faite après lui ne voit que
    des séances closes."""
    mark = now.replace(hour=READ_AFTER[0], minute=READ_AFTER[1], second=0, microsecond=0)
    return mark if now >= mark else mark - timedelta(days=1)


def due(book: Any, now: datetime) -> bool:
    """À relire : une fois par jour, après la clôture de New York (un cours lu
    en pleine séance n'est pas une clôture) ; six heures après une panne."""
    last = (book or {}).get("tried_at") if isinstance(book, dict) else None
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    if book.get("error"):
        return (now - when).total_seconds() >= 3600 * RETRY_HOURS
    return when < read_mark(now)


def portfolio(close: pd.DataFrame, start: str, capital: float, binance: bool = False) -> Dict[str, Any]:
    """Portefeuille fictif depuis `start` : la règle rejouée chaque jour sur
    les clôtures réelles. Par défaut, hors cryptos sans les actions, au coût
    réel de chaque marché ; `binance` : la manche tradable sur Binance (or,
    euro, actions tokenisées), aux frais de Binance. Rejouer plutôt que tenir
    des comptes : le résultat est exactement celui du backtest."""
    sub = (close[[c for c in BINANCE_NAME if c in close.columns]] if binance
           else unbiased(close[[c for c in close.columns if c in PORTFOLIO_CODES]]))
    real = not binance
    end = str(sub.index[-1].date())
    if end < start:
        return {"start": start, "capital": capital, "equity": capital, "return_pct": 0.0,
                "positions": [], "trades": 0, "curve": []}
    r = backtest(sub, start, end, capital=capital, real_costs=real)
    eq = float(r.equity.iloc[-1]) if len(r.equity) else capital
    last = {a: float(sub[a].dropna().iloc[-1]) for a in r.holdings}
    positions = [{"code": a, "name": NAME.get(a, a), "category": CATEGORY.get(a, ""), "entry": h.entry,
                  "stop": h.stop, "price": last[a], "pnl_pct": (last[a] / h.entry - 1) * 100}
                 for a, h in sorted(r.holdings.items())]
    curve = [[str(d.date()), round(float(v), 2)] for d, v in r.equity.iloc[-CURVE_POINTS:].items()]
    return {"start": start, "capital": capital, "equity": eq, "return_pct": (eq / capital - 1) * 100,
            "positions": positions, "trades": len(r.trades), "curve": curve}


def refresh(memory: Any, now: datetime, fetch: Fetch, cache: str, capital: float,
            force: bool = False, pause: float = 1.0) -> Dict[str, Any]:
    """Relit tous les marchés hors cryptos une fois par jour (dans `cache`),
    note l'état de chacun pour la règle et rejoue le portefeuille fictif
    depuis son premier jour. Une panne est notée, jamais bloquante ; un
    marché illisible n'empêche pas les autres."""
    from .market_watch import friendly_error
    book = memory.get(KEY)
    book = dict(book) if isinstance(book, dict) else {}
    if not (force or due(book, now)):
        return book
    book["tried_at"] = now.isoformat(timespec="seconds")
    try:
        errors = download(cache, fetch, codes=ALL_CODES + INDICATOR_CODES + CURRENCY_CODES, pause=pause,
                          symbols=SYMBOL)
        close = load(cache, ALL_CODES)
        if close.empty:
            raise ValueError("aucun historique lisible")
        start = book.get("start") or now.date().isoformat()
        book.update(fetched_at=book["tried_at"], error="", start=start, unread=sorted(errors),
                    rows=summary(close), portfolio=portfolio(close, start, float(capital)),
                    binance=portfolio(close, start, float(capital), binance=True),
                    macro=macro_view(load(cache, INDICATOR_CODES + ["dxy"])))
    except Exception as e:
        book["error"] = friendly_error(e)
    memory.put(KEY, book)
    return book


# ══════════════════════════════════════════════════════════════════════
# PANNEAU : une page par classe d'actifs (lecture seule)
# ══════════════════════════════════════════════════════════════════════

# Pages du panneau : (onglet, catégorie), dans l'ordre du menu.
CLASSES: Tuple[Tuple[str, str], ...] = (
    ("commodities", "Matières premières"), ("forex", "Devises"), ("indices", "Indices"),
    ("stocks", "Actions"), ("bonds", "Obligations"))
SESSIONS = {"1d": 1, "1m": 21, "3m": 63, "1y": 252}     # séances de chaque variation
SPARK_SESSIONS = {"1m": 21, "3m": 63, "1y": 252, "5y": 1260}
SPARK_POINTS = 120                                       # points d'une courbe de carte, au plus


def codes_of(category: str) -> List[str]:
    """Marchés d'une catégorie : ceux de l'univers, ceux de la manche Binance
    qui n'y sont pas, les marchés ajoutés, puis les indices des pays."""
    return list(dict.fromkeys([c for c, _n, k, _s in UNIVERSE + BINANCE + ADDED + WORLD if k == category]))


def books_of(code: str) -> List[str]:
    """Portefeuilles fictifs qui suivent ce marché."""
    main = code in PORTFOLIO_CODES and CATEGORY.get(code) not in BIASED
    return (["Portefeuille fictif hors cryptos"] if main else []) + (
        ["Manche Binance fictive"] if code in BINANCE_NAME else [])


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def market_detail(code: str, close: pd.Series, p: Optional[ts.TrendParams] = None) -> Dict[str, Any]:
    """Ce que la page d'un marché dit de lui à sa dernière séance :
    variations, plus haut et plus bas sur un an, moyenne de 150 séances,
    niveau de cassure (plus haut des 30 séances précédentes), momentum,
    volatilité, stop initial d'un achat du jour, tendance et signal de la
    règle (calculés comme pour le suivi quotidien). Information seulement."""
    p = p or ts.TrendParams()
    s = sessions(close)
    row: Dict[str, Any] = {"code": code, "name": NAME.get(code, code), "category": CATEGORY.get(code, ""),
                           "symbol": SYMBOL.get(code, ""), "binance": BINANCE_NAME.get(code) or TOKENS.get(code, ""),
                           "books": books_of(code)}
    if s.empty:
        return dict(row, close=None, date=None, trend=False, signal=False)
    f = ts.asset_features(s, p, pd.Series(LIQUID, index=s.index))
    snap = {k: float(f[k].iloc[-1]) for k in ("close", "vol", "prior_high", "mom", "age", "vol30")}
    px = float(s.iloc[-1])
    avg = _num(s.rolling(REGIME_N, min_periods=REGIME_N).mean().iloc[-1])
    trend = bool(avg is not None and px > avg)
    year = s[s.index > s.index[-1] - pd.Timedelta(days=365)]
    vol, high = _num(snap["vol"]), _num(snap["prior_high"])
    return dict(row, close=px, date=str(s.index[-1].date()),
                changes={k: (px / float(s.iloc[-1 - n]) - 1) * 100 if len(s) > n else None
                         for k, n in SESSIONS.items()},
                high_1y=float(year.max()), low_1y=float(year.min()),
                sma=avg, gap_pct=(px / avg - 1) * 100 if avg else None,
                breakout=high, breakout_gap_pct=(px / high - 1) * 100 if high else None,
                mom=_num(snap["mom"]),
                vol_pct=vol / px * 100 if vol and px else None,
                stop=px - p.init_stop_atr * vol if vol else None,
                trend=trend, signal=bool(trend and ts.entry_signal(snap, p)))


def market_points(close: pd.Series, days: int = 0) -> List[Dict[str, Any]]:
    """Points du graphique d'un marché : clôture, moyenne de 150 séances et
    niveau de cassure de chaque séance (None tant qu'ils n'existent pas : un
    marché coté depuis peu) ; les `days` derniers jours (0 : tout). Temps en
    secondes (minuit UTC)."""
    s = sessions(close)
    if s.empty:
        return []
    n = ts.TrendParams().breakout_n
    sma = s.rolling(REGIME_N, min_periods=REGIME_N).mean()
    high = s.shift(1).rolling(n, min_periods=n).max()
    frame = pd.DataFrame({"close": s, "sma": sma, "high": high})
    if days:
        frame = frame[frame.index > s.index[-1] - pd.Timedelta(days=days)]
    return [{"t": int(d.timestamp()), "close": round(float(c), 6), "sma": None if pd.isna(m) else round(float(m), 6),
             "high": None if pd.isna(h) else round(float(h), 6)}
            for d, c, m, h in zip(frame.index, frame["close"], frame["sma"], frame["high"])]


def spark(close: pd.Series, window: str = "1y", points: int = SPARK_POINTS) -> List[float]:
    """Clôtures de la courbe d'une carte sur `window` (1m, 3m, 1y, 5y),
    éclaircies à `points` au plus ; la dernière clôture est toujours gardée."""
    s = sessions(close).iloc[-(SPARK_SESSIONS.get(window, 252) + 1):]
    if len(s) > points:
        step = -(-len(s) // points)                        # arrondi au-dessus
        s = s.iloc[::-1].iloc[::step].iloc[::-1]
    return [round(float(v), 6) for v in s.values]


def held_positions(book: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Positions des deux portefeuilles fictifs, par marché."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    book = book if isinstance(book, dict) else {}
    for key, label in (("portfolio", "Portefeuille fictif hors cryptos"), ("binance", "Manche Binance fictive")):
        for pos in ((book.get(key) or {}).get("positions") or []):
            if isinstance(pos, dict) and pos.get("code"):
                out.setdefault(str(pos["code"]), []).append(dict(pos, book=label))
    return out


# ══════════════════════════════════════════════════════════════════════
# INDICATEURS DE MARCHÉ : lecture du jour (information, docs/MACRO.md)
# ══════════════════════════════════════════════════════════════════════

VIX_LEVELS = ((15.0, "calme"), (20.0, "normal"), (30.0, "tendu"))       # au-delà : stress


def vix_level(value: float) -> str:
    for limit, word in VIX_LEVELS:
        if value < limit:
            return word
    return "stress"


def macro_view(ind: pd.DataFrame) -> Dict[str, Any]:
    """Les indicateurs de marché à leur dernière séance : VIX et sa médiane
    d'un an, dollar (DXY) face à sa moyenne de 150 séances, taux américains
    et leur variation sur 60 séances, courbe 10 ans − 3 mois (inversée sous
    zéro). Information seulement : aucun achat n'en dépend."""
    def col(code: str) -> pd.Series:
        return ind[code].dropna().astype(float) if code in ind.columns else pd.Series(dtype=float)
    out: Dict[str, Any] = {}
    vix = col("vix")
    if len(vix):
        out["vix"] = {"value": float(vix.iloc[-1]), "date": str(vix.index[-1].date()),
                      "level": vix_level(float(vix.iloc[-1])), "median_1y": float(vix.iloc[-252:].median())}
    dxy = col("dxy")
    if len(dxy) >= REGIME_N:
        sma = float(dxy.iloc[-REGIME_N:].mean())
        out["dxy"] = {"value": float(dxy.iloc[-1]), "date": str(dxy.index[-1].date()), "sma": sma,
                      "strong": bool(dxy.iloc[-1] > sma)}
    rates = {}
    for code in ("us3m", "us5y", "us10y", "us30y"):
        s = col(code)
        if len(s):
            rates[code] = {"value": float(s.iloc[-1]), "date": str(s.index[-1].date()),
                           "change_60": float(s.iloc[-1] - s.iloc[-61]) if len(s) > 60 else None}
    if rates:
        out["rates"] = rates
    if "us10y" in rates and "us3m" in rates:
        out["curve"] = rates["us10y"]["value"] - rates["us3m"]["value"]
    return out


def describe_macro(view: Any) -> str:
    """La lecture du jour en une ligne (veille, agent macro du comité)."""
    if not isinstance(view, dict) or not view:
        return ""
    parts = []
    if view.get("vix"):
        v = view["vix"]
        parts.append(f"VIX {fr(v['value'], '.1f')} ({v['level']})")
    if view.get("dxy"):
        d = view["dxy"]
        parts.append(f"dollar {'au-dessus de' if d['strong'] else 'sous'} sa moyenne de 150 séances "
                     f"(DXY {fr(d['value'], '.1f')})")
    r = (view.get("rates") or {}).get("us10y")
    if r:
        parts.append(f"taux US à 10 ans {fr(r['value'], '.2f')} %" + (
            f" ({fr(r['change_60'], '+.2f')} point en 60 séances)" if r.get("change_60") is not None else ""))
    if view.get("curve") is not None:
        parts.append(f"courbe 10 ans − 3 mois {fr(view['curve'], '+.2f')} point"
                     + (" : inversée" if view["curve"] < 0 else ""))
    return " · ".join(parts)
