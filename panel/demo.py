"""Mode démonstration du panneau : données fictives mais vivantes (les cours
bougent), sans réseau ni bot. Sert d'aperçu et aux tests du navigateur.
Rien n'est lu ni écrit sur le disque, aucun ordre n'est possible."""

from __future__ import annotations

import math
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from .data import merge_buys, reasoning_view
from .market import INTERVALS

BASE_PRICES = {"btc": 84400.0, "eth": 2690.0, "bnb": 780.0, "xrp": 0.58, "ada": 0.254,
               "doge": 0.118, "trx": 0.27, "link": 14.1, "ltc": 72.5, "bch": 410.0,
               "xlm": 0.216, "etc": 19.2, "zec": 48.0, "dash": 31.0, "neo": 9.4, "xtz": 0.71,
               "algo": 0.19, "dot": 4.2, "uni": 8.9, "aave": 155.0, "icp": 3.2}
HELD = ("aave", "ada", "icp", "link", "ltc", "xlm")
# Raisonnement d'exemple : pourquoi le bot n'achète pas les autres cryptos.
WHY = {"btc": ("wait", "Pas de cassure : il lui faut +3,8 % pour dépasser son plus haut de 30 jours", 3.8),
       "eth": ("watch", "Sous surveillance : encore +1,9 % pour casser son plus haut de 30 jours", 1.9),
       "bnb": ("full", "Signal d'achat, mais plafond atteint (positions, risque total ou liquidités)", None),
       "xrp": ("weak", "Tendance de fond (90 jours) négative", 9.4),
       "doge": ("wait", "Pas de cassure : il lui faut +12,6 % pour dépasser son plus haut de 30 jours", 12.6),
       "trx": ("watch", "Sous surveillance : encore +4,2 % pour casser son plus haut de 30 jours", 4.2),
       "bch": ("deferred", "Achat différé : écart achat/vente anormal (0,74 %, limite 0,50 %). "
                           "Nouvel essai toutes les 5 min", None),
       "etc": ("illiquid", "Pas assez échangée sur Binance (3,1 M$ par jour, minimum 5 M$)", 6.5),
       "zec": ("wait", "Pas de cassure : il lui faut +8,1 % pour dépasser son plus haut de 30 jours", 8.1),
       "dash": ("weak", "Tendance de fond (90 jours) négative", 15.0),
       "neo": ("illiquid", "Pas assez échangée sur Binance (2,4 M$ par jour, minimum 5 M$)", 7.7),
       "xtz": ("veto", "Achats bloqués par la veille : Binance retire XTZ (exemple)", None),
       "algo": ("illiquid", "Pas assez échangée sur Binance (4,2 M$ par jour, minimum 5 M$)", 5.4),
       "dot": ("watch", "Sous surveillance : encore +2,7 % pour casser son plus haut de 30 jours", 2.7),
       "uni": ("wait", "Pas de cassure : il lui faut +6,3 % pour dépasser son plus haut de 30 jours", 6.3)}


def _walk(asset: str, interval_s: int, n: int, end: int) -> List[List[float]]:
    rnd = random.Random(f"{asset}:{interval_s}")
    vol = 0.004 * math.sqrt(interval_s / 3600)
    px = BASE_PRICES.get(asset, 10.0) * 0.9
    rows = []
    for i in range(n):
        t = end - (n - 1 - i) * interval_s
        o = px
        px = max(1e-6, px * (1 + rnd.gauss(0.00003 * interval_s / 3600, vol)))
        hi, lo = max(o, px) * (1 + abs(rnd.gauss(0, vol / 2))), min(o, px) * (1 - abs(rnd.gauss(0, vol / 2)))
        rows.append([t, o, hi, lo, px, rnd.uniform(500, 5000)])
    # Dernière bougie « en cours » : bouge avec l'horloge.
    wiggle = 1 + 0.003 * math.sin(time.time() / 20 + len(asset))
    last = rows[-1]
    last[4] = last[1] * wiggle
    last[2], last[3] = max(last[2], last[4]), min(last[3], last[4])
    # Raccord au prix de référence de l'actif.
    k = BASE_PRICES.get(asset, 10.0) / rows[-1][4] * wiggle
    return [[r[0]] + [v * k for v in r[1:5]] + [r[5]] for r in rows]


class DemoMarket:
    def tickers(self, bases: List[str]) -> Tuple[Dict[str, Dict[str, float]], bool]:
        out = {}
        for b in bases:
            rows = _walk(b, 3600, 30, int(time.time()) // 3600 * 3600)
            p0, p1 = rows[-25][4], rows[-1][4]
            out[b] = {"price": p1, "change_pct": round((p1 / p0 - 1) * 100, 2),
                      "high": max(r[2] for r in rows[-24:]), "low": min(r[3] for r in rows[-24:]),
                      "volume_quote": 5e6 + 1e8 * random.Random(b).random()}
        return out, False

    def klines(self, base: str, interval: str = "1h", limit: int = 300) -> Tuple[List[List[float]], bool]:
        step = INTERVALS[interval]
        return _walk(base, step, max(10, min(1000, int(limit))), int(time.time()) // step * step), False

    def regime(self, sma: int = 150, show: int = 365) -> Tuple[Dict[str, Any], bool]:
        rows, _ = self.klines("btc", "1d", sma + show)
        closes = [r[4] for r in rows]
        pts = [{"t": r[0], "close": r[4], "sma": sum(closes[i - sma + 1:i + 1]) / sma}
               for i, r in enumerate(rows) if i >= sma - 1][-show:]
        return {"points": pts, "bull": pts[-1]["close"] > pts[-1]["sma"], "sma": sma}, False


# Classement d'exemple (bénéfice de la stratégie sur 2 ans, en R).
DEMO_RANK = (("aave", 14.2, 9), ("link", 11.8, 8), ("ada", 10.4, 10), ("icp", 9.1, 7),
             ("eth", 8.3, 9), ("xlm", 7.6, 8), ("ltc", 6.9, 9), ("bnb", 6.1, 7), ("dot", 5.2, 8),
             ("btc", 4.8, 6), ("trx", 3.9, 7), ("doge", 2.7, 9), ("uni", 1.8, 8), ("bch", 0.9, 7),
             ("xrp", -0.4, 8), ("zec", -1.1, 6), ("dash", -1.9, 7), ("etc", -2.3, 6),
             ("xtz", -2.8, 5), ("neo", -3.4, 6), ("algo", -4.0, 7))


class DemoData:
    def __init__(self, market: DemoMarket):
        self.market = market
        self._sel = {"mode": "manual", "manual": list(BASE_PRICES)}
        now = datetime.now(timezone.utc)
        self._entry = {a: (now - timedelta(days=2 + i)).isoformat() for i, a in enumerate(HELD)}

    def state(self) -> Dict[str, Any]:
        eq = 10_000 + 180 * math.sin(time.time() / 600) + 420
        return {"last_decision_day": (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
                "last_equity": round(eq, 2), "start_equity": 10_000.0, "peak_equity": 10_650.0,
                "halted": False, "last_regime_bull": self.market.regime()[0]["bull"], "risk_mult": 1.0,
                "last_cycle_ts": time.time() - 20,
                "vetoes": {"xtz": {"until": "2099-01-01", "reason": "Binance retire XTZ (exemple)",
                                   "url": "https://www.binance.com/", "date": "2026-09-20"}},
                "last_watch": {"day": "2026-09-27", "sentiment": 0.18, "providers": 3,
                               "providers_total": 4,
                               "alerts": ["Parité USDC/USDT normale (exemple)"]},
                "trades": self.trades(),
                "reasoning": self._reasoning(),
                "reasoning_log": [{"day": (datetime.now(timezone.utc) - timedelta(days=k)).date().isoformat(),
                                   "text": "Marché haussier : BTC au-dessus de sa moyenne 150 jours (+4,1 %), "
                                           "achats autorisés. Aujourd'hui : aucun changement, 6 position(s) "
                                           "conservée(s)."} for k in range(6, 0, -1)],
                "pending_entries": {"bch": {"reason": "écart achat/vente anormal (0,74 %, limite 0,50 %)",
                                            "tries": 3, "until": time.time() + 4 * 3600}},
                "anticipation": self._basis(),
                "selection": {"mode": self._sel["mode"], "day": "2026-09-27",
                              "auto": [a for a, _r, _n in DEMO_RANK[:10]],
                              "ranking": [{"asset": a, "rank": k + 1, "total_r": r, "trades": n,
                                           "win_rate": 0.45, "eligible": True}
                                          for k, (a, r, n) in enumerate(DEMO_RANK)]}}

    def _basis(self) -> Dict[str, Any]:
        """Niveaux d'exemple de la prochaine décision."""
        day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        assets = {}
        for a, px in BASE_PRICES.items():
            st, _t, gap = WHY.get(a, ("held", "", None))
            gap = {"bnb": -0.8, "bch": 0.6}.get(a, gap if gap is not None else 12.0)
            assets[a] = {"close": px, "buy_trigger": px * (1 + gap / 100), "mom_ref": px * 0.8,
                         "vol": px * 0.03, "liquid": st != "illiquid"}
        last = self.market.regime()[0]["points"][-1]       # même régime que le reste de la démo
        return {"day": day.isoformat(), "btc_threshold": BASE_PRICES["btc"] * last["sma"] / last["close"],
                "next_close": (datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
                               + timedelta(days=2)).isoformat(), "assets": assets}

    def selection_request(self) -> Dict[str, Any]:
        return dict(self._sel, saved=True)

    def save_selection(self, mode: str, manual: List[str]) -> Dict[str, Any]:
        if mode not in ("auto", "manual") or any(a not in BASE_PRICES for a in manual):
            raise ValueError("sélection invalide")
        self._sel = {"mode": mode, "manual": [a for a in BASE_PRICES if a in manual]}
        return self.selection_request()

    def _reasoning(self) -> Dict[str, Any]:
        assets = {}
        for a in BASE_PRICES:
            if a in HELD:
                assets[a] = {"status": "held", "text": "En portefeuille : la tendance tient, stop à −9,8 % du cours",
                             "breakout_gap_pct": None}
            else:
                st, text, gap = WHY.get(a, ("wait", "Pas de cassure", 10.0))
                assets[a] = {"status": st, "text": text, "breakout_gap_pct": gap}
        reg = self.market.regime()[0]
        last = reg["points"][-1]
        gap = (last["close"] / last["sma"] - 1) * 100
        txt = f"{gap:+.1f} %".replace(".", ",")
        first = (f"Marché haussier : BTC au-dessus de sa moyenne 150 jours ({txt}), achats autorisés."
                 if reg["bull"] else
                 f"Marché baissier : BTC sous sa moyenne 150 jours ({txt}), aucun achat et stops "
                 f"resserrés pour protéger les gains.")
        return {"day": (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
                "bull": reg["bull"], "btc_gap_pct": round(gap, 2), "radar": ["eth", "dot", "trx"],
                "assets": assets,
                "lines": [first,
                          "Aujourd'hui : aucun changement, 6 position(s) conservée(s).",
                          "Ruse : achat de BCH différé (conditions d'achat anormales), nouvel essai toutes les 5 min.",
                          "Sous surveillance : ETH (+1,9 %), DOT (+2,7 %), TRX (+4,2 %) avant la cassure."]}

    def reasoning(self, state: Any = None) -> Dict[str, Any]:
        return reasoning_view(self.state())

    def equity(self, days: int = 90, max_points: int = 1500) -> List[Dict[str, float]]:
        rnd = random.Random("equity")
        end = int(time.time()) // 900 * 900
        n = min(days * 96, max_points)
        v, pts = 10_000.0, []
        for i in range(n):
            v *= 1 + rnd.gauss(0.00012, 0.002)
            pts.append({"t": end - (n - 1 - i) * 900, "v": round(v, 2)})
        return pts

    def holdings(self, state: Any = None) -> List[Dict[str, Any]]:
        out = []
        for a in HELD:
            px = BASE_PRICES[a]
            entry = px * 0.99
            stop = entry * 0.9
            if a == "icp":                    # exemple : position proche de sa vente
                stop = px * 0.985
            out.append({"asset": a, "qty": round(100 / (entry - stop), 4), "entry": entry,
                        "stop": stop, "disaster": stop * 0.97, "risk": 100.0,
                        "cost": 100 / (entry - stop) * entry, "entry_date": self._entry[a]})
        return out

    def positions(self, state: Any = None) -> Dict[str, Any]:
        rows = self.holdings()
        prices, _ = self.market.tickers([r["asset"] for r in rows])
        for r in rows:
            px = prices[r["asset"]]["price"]
            r.update(price=px, pnl_pct=round((px / r["entry"] - 1) * 100, 2),
                     r=round((px - r["entry"]) * r["qty"] / r["risk"], 2),
                     stop_dist_pct=round((px - r["stop"]) / px * 100, 2))
        return {"positions": rows, "stale": False}

    def buys(self, state: Any = None) -> List[Dict[str, Any]]:
        return merge_buys(self.state(), self.holdings())

    def trades(self, state: Any = None) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        rows = []
        for i, (a, r) in enumerate((("eth", 2.9), ("btc", -1.02), ("bnb", 4.4), ("doge", -0.98),
                                    ("uni", 1.7), ("xrp", -1.05))):
            px = BASE_PRICES[a]
            rows.append({"asset": a, "date": (now - timedelta(days=5 + 9 * i)).isoformat(),
                         "entry_date": (now - timedelta(days=25 + 9 * i)).isoformat(),
                         "entry": px * 0.95, "exit": px * (0.95 + 0.02 * r),
                         "pnl": round(100 * r, 2), "r": r,
                         "reason": "STOP" if r < 0 else "EXCHANGE_STOP", "days": 20})
        return rows

    def watch(self, state: Any = None) -> Dict[str, Any]:
        st = self.state()
        return {"last": st["last_watch"],
                "vetoes": [dict(v, asset=a) for a, v in st["vetoes"].items()],
                "report_text": "VEILLE DU MARCHÉ (démonstration)\nClimat selon 3 IA : neutre (+0,18).\n"
                               "Fear & Greed : 70 (Greed) · USDC/USDT 1.0002"}

    def log_tail(self, lines: int = 300) -> List[str]:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return [f"{now},000 [INFO] [HEARTBEAT] equity 10 420 USDT | régime BTC HAUSSIER (démonstration)",
                f"{now},000 [WARNING] [PAPER] ADA/USDT : prix indisponible (exemple)",
                f"{now},000 [INFO] [DAILY] TrendGuard — 6 positions (exemple)"]


class DemoControl:
    def __init__(self) -> None:
        self._state = "running"
        self._autostart = True

    def autonomy(self) -> Dict[str, Any]:
        return {"supervisor": {"running": self._state == "running", "state": "running", "restarts": 1,
                               "last_exit": {"code": 1, "at": (datetime.now(timezone.utc)
                                                               - timedelta(days=3)).isoformat(),
                                             "stalled": False},
                               "since": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
                               "next_start": None},
                "off": self._state != "running", "autostart": self._autostart, "os": "windows",
                "keep_awake": True}

    def set_autostart(self, enabled: bool) -> Tuple[bool, str]:
        self._autostart = bool(enabled)
        return True, ("Démarrage automatique activé (démonstration)." if enabled
                      else "Démarrage automatique désactivé (démonstration).")

    def state(self) -> str:
        return self._state

    def start(self) -> Tuple[bool, str]:
        if self._state == "running":
            return False, "Le bot est déjà en marche."
        self._state = "running"
        return True, "Automatisation démarrée (démonstration)."

    def stop(self) -> Tuple[bool, str]:
        if self._state == "stopped":
            return False, "Le bot est déjà arrêté."
        self._state = "stopped"
        return True, "Arrêt demandé (démonstration)."


# Actualités d'exemple (aucun réseau) : titres marqués « exemple ».
DEMO_NEWS = (
    ("crypto", "fr", "Journal du Coin", "Bitcoin : les ETF enregistrent une semaine d'entrées record", ["btc"], ["ETF"]),
    ("finance", "en", "CNBC", "Stocks edge higher as investors await the Fed's rate decision", [], ["macro"]),
    ("crypto", "en", "CoinDesk", "Cardano developers ship a major network upgrade", ["ada"], []),
    ("finance", "fr", "Le Monde", "La BCE maintient ses taux directeurs, l'inflation ralentit en zone euro", [], ["macro"]),
    ("crypto", "en", "Cointelegraph", "Chainlink expands its oracle network to three new blockchains", ["link"], []),
    ("crypto", "fr", "Cryptoast", "Un protocole DeFi victime d'un piratage, Aave suspend un marché par précaution",
     ["aave"], ["hack"]),
    ("finance", "en", "CNBC", "Oil slips as supply worries ease; gold holds near its highs", [], []),
    ("crypto", "en", "Decrypt", "Ethereum layer-2 activity hits a new all-time high", ["eth"], []),
    ("finance", "fr", "Google Actualités", "Le CAC 40 termine en hausse, porté par le luxe et les banques", [], []),
    ("crypto", "fr", "Journal du Coin", "Litecoin : le nombre d'adresses actives grimpe de 12 % sur un mois", ["ltc"], []),
    ("finance", "en", "CNBC", "Treasury yields rise after stronger-than-expected jobs data", [], ["macro"]),
    ("crypto", "en", "CoinDesk", "Stablecoin supply climbs above a new record as trading volumes recover", [],
     ["stablecoin"]),
    ("crypto", "fr", "Cryptoast", "Polkadot : la feuille de route 2027 dévoilée", ["dot"], []),
    ("finance", "fr", "Le Monde", "Le dollar recule face à l'euro après les propos de la Fed", [], ["macro"]),
    ("crypto", "en", "Decrypt", "Stellar partners with a payments firm for cross-border transfers", ["xlm"], []),
    ("finance", "en", "CNBC", "Tech stocks lead Nasdaq to a fresh record close", [], []),
)
DEMO_QUOTES = (("sp500", "S&P 500", "", 7743.0, 0.42), ("nasdaq", "Nasdaq", "", 27068.0, 0.81),
               ("dow", "Dow Jones", "", 51828.0, 0.12), ("cac40", "CAC 40", "", 8077.0, -0.35),
               ("gold", "Or (once, $)", "$", 4295.0, 0.55), ("oil", "Pétrole WTI ($)", "$", 93.3, -1.2),
               ("eurusd", "Euro / dollar", "", 1.1384, -0.03), ("dxy", "Indice dollar", "", 101.0, 0.08),
               ("us10y", "Taux US 10 ans", "%", 5.18, 1.1), ("vix", "VIX (volatilité)", "", 14.9, -3.4))


class DemoNews:
    def snapshot(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        items = []
        for k, (cat, lang, src, title, assets, topics) in enumerate(DEMO_NEWS):
            items.append({"title": f"{title} (exemple)", "url": "https://www.example.com/",
                          "source": src, "via": src, "category": cat, "lang": lang,
                          "summary": "Article fictif du mode démonstration : les vraies actualités "
                                     "s'affichent quand le panneau tourne avec le bot.",
                          "published": (now - timedelta(minutes=7 + 23 * k)).isoformat(),
                          "assets": assets, "topics": topics,
                          "alert": "hack" in topics and bool(assets)})
        quotes = []
        for qid, name, unit, px, chg in DEMO_QUOTES:
            rnd = random.Random(qid)
            closes, v = [], px / (1 + chg / 100) * 0.97
            for _ in range(21):
                v *= 1 + rnd.gauss(0.0015, 0.008)
                closes.append(round(v, 4))
            closes.append(px)
            quotes.append({"id": qid, "name": name, "unit": unit, "price": px, "change_pct": chg,
                           "closes": closes, "at": now.isoformat(), "source": "démonstration"})
        sources = [{"name": n, "category": c, "lang": lg, "ok": True, "count": 4}
                   for n, c, lg in (("CoinDesk", "crypto", "en"), ("Journal du Coin", "crypto", "fr"),
                                    ("CNBC", "finance", "en"), ("Le Monde", "finance", "fr"))]
        return {"items": items, "sources": sources, "loading": False, "updated": now.isoformat(),
                "markets": {"quotes": quotes, "errors": [],
                            "crypto": {"market_cap_usd": 3.42e12, "market_cap_change_24h_pct": 1.35,
                                       "volume_24h_usd": 1.18e11, "btc_dominance_pct": 57.8,
                                       "eth_dominance_pct": 11.9, "active_cryptos": 21642},
                            "fear_greed": {"value": 74, "label": "Avidité",
                                           "history": [48 + (i * 7) % 30 for i in range(29)] + [74]}}}
