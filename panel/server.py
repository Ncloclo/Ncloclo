"""Serveur du panneau de contrôle (bibliothèque standard Python : Windows,
Linux, macOS, sans dépendance).

Sécurité :
- par défaut, écoute sur 127.0.0.1 : seul ce PC y accède ;
- accès depuis un téléphone (--host 0.0.0.0) : mot de passe obligatoire
  (PANEL_PASSWORD), session par cookie HttpOnly ;
- une page web tierce ne peut rien déclencher : les actions (POST) exigent
  un en-tête propre au panneau et une origine identique ; l'en-tête Host est
  contrôlé (attaque par « DNS rebinding ») ;
- fichiers servis uniquement depuis panel/static ; en-têtes de sécurité
  stricts (CSP, nosniff, pas d'intégration dans un cadre).
"""

from __future__ import annotations

import collections
import hmac
import json
import math
import mimetypes
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from trendguard import anticipation, evolution, learning, uptime
from trendguard import market_watch as mw

from .assistant import AIHelper, Assistant
from .control import BotControl
from .data import BotData, _ts
from .demo import DemoControl, DemoData, DemoMarket, DemoNews
from .market import INTERVALS, Market
from .news import NewsHub

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SESSION_DAYS = 30
LOGIN_MAX_FAILS = 5         # essais ratés tolérés par adresse…
LOGIN_WINDOW_SEC = 600      # … sur 10 minutes,
LOGIN_LOCK_SEC = 300        # puis 5 minutes de blocage
CHAT_PER_MINUTE = 20        # questions à l'assistant (coût d'une IA éventuelle)
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
       "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml",
         ".webmanifest": "application/manifest+json", ".json": "application/json"}


def _clean(obj: Any) -> Any:
    """JSON strict : NaN et infini deviennent null."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def lan_ips() -> List[str]:
    """Adresse du PC sur le réseau local (pour ouvrir le panneau depuis un
    téléphone connecté au même Wi-Fi). Aucun paquet n'est envoyé."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


class PanelApp:
    def __init__(self, gcfg: Any, data: Any, market: Any, control: Any, hub: Any = None,
                 demo: bool = False, password: str = "", loopback: bool = True,
                 lan_urls: Optional[List[str]] = None, news: Any = None,
                 assistant: Any = None):
        self.g = gcfg
        self.data, self.market, self.control, self.hub = data, market, control, hub
        self.news = news
        self.assistant = assistant or Assistant(None)
        self._chat_times: collections.deque = collections.deque()
        self._login_fails: Dict[str, List[float]] = {}
        self._login_failed_total: List[float] = []
        self.demo = demo
        self.password = password
        self.loopback = loopback
        self.lan_urls = lan_urls or []
        self._sessions: Dict[str, float] = {}
        self._lock = threading.Lock()

    # ---------- Sessions ----------

    def new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            # Sessions expirées oubliées : la mémoire ne grossit pas avec le temps.
            for old in [k for k, exp in self._sessions.items() if exp <= now]:
                del self._sessions[old]
            self._sessions[token] = now + SESSION_DAYS * 86400
        return token

    def session_ok(self, token: Optional[str]) -> bool:
        if not self.password:
            return True
        with self._lock:
            exp = self._sessions.get(token or "")
        return bool(exp and exp > time.time())

    def login_blocked(self, ip: str) -> int:
        """Secondes de blocage restantes pour cette adresse (0 = libre)."""
        now = time.time()
        with self._lock:
            fails = [t for t in self._login_fails.get(ip, []) if now - t < LOGIN_WINDOW_SEC]
            self._login_fails[ip] = fails
            if len(fails) >= LOGIN_MAX_FAILS:
                return max(0, int(fails[-1] + LOGIN_LOCK_SEC - now) + 1)
        return 0

    def login_failed(self, ip: str) -> int:
        """Note un essai raté ; renvoie le nombre d'essais restants."""
        now = time.time()
        with self._lock:
            fails = self._login_fails.setdefault(ip, [])
            fails.append(now)
            self._login_failed_total = [t for t in self._login_failed_total if now - t < 86400] + [now]
            return max(0, LOGIN_MAX_FAILS - len(fails))

    def login_ok(self, ip: str) -> None:
        with self._lock:
            self._login_fails.pop(ip, None)

    def drop_session(self, token: Optional[str]) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)

    # ---------- Données ----------

    def status(self) -> Dict[str, Any]:
        st = self.data.state()
        now = datetime.now(timezone.utc)
        nxt = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc) \
            + timedelta(seconds=self.g.decision_delay_sec)
        if nxt <= now:
            nxt += timedelta(days=1)
        eq, start, peak = st.get("last_equity"), st.get("start_equity"), st.get("peak_equity")
        pts = self.data.equity(days=2)
        if pts:
            eq = pts[-1]["v"]
        last_cycle = st.get("last_cycle_ts")
        p = self.g.params
        return {
            "mode": self.g.run_mode, "demo": self.demo,
            "testnet": bool(self.g.binance_testnet and self.g.run_mode == "live"),
            "state": self.control.state(),
            "equity": eq, "start_equity": start, "peak": peak,
            "drawdown_pct": round((eq / peak - 1) * 100, 2) if eq and peak else None,
            "halted": bool(st.get("halted")), "halt_reason": st.get("halt_reason"),
            "regime_bull": st.get("last_regime_bull"), "risk_mult": st.get("risk_mult", 1.0),
            "last_decision_day": st.get("last_decision_day"),
            "last_buy": self._last_buy(st),
            "selection": {k: v for k, v in self.selection_view(st).items()
                          if k in ("mode", "active", "universe")},
            "next_decision_s": int((nxt - now).total_seconds()),
            "last_cycle_age_s": int(time.time() - float(last_cycle)) if last_cycle else None,
            "positions": len(self.data.holdings(st)), "max_positions": p.max_positions,
            "risk_pct": p.risk_pct * 100, "max_total_risk_pct": p.max_total_risk * 100,
            "dd_throttle": [list(x) for x in p.dd_throttle],
            "kill_drawdown_pct": self.g.kill_drawdown * 100,
            "universe": [b.lower() for b in self.g.universe],
            "vetoes": [dict(v, asset=a) for a, v in sorted((st.get("vetoes") or {}).items())],
            "watch": st.get("last_watch"),
            "alerts": self._alert_channels(st),
            "uptime": uptime.summary(st.get("uptime"), last_cycle, st.get("stopped_at")),
            "evolution": evolution.summary(self.g),
            "learning": learning.summary(st.get("learning")),
            # Réglages de la stratégie en vigueur (l'évolution a pu les changer).
            "rules": {k: getattr(evolution.params_for(self.g), k) for k in evolution.SPACE},
            "autonomy": self.control.autonomy(),
            "lan_urls": self.lan_urls, "password": bool(self.password),
            "server_time": now.isoformat(),
        }

    SELECTION_NOTE = ("Historique (docs/SELECTION.md) : de 2023 à 2026, +37,2 % par an avec les "
                      "21 cryptos, contre +15,5 % avec seulement les 10 plus rentables.")

    MANUAL_TOP_N = 10       # sélection manuelle : les 10 plus rentables cochées au départ

    def selection_view(self, st: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Cryptos que le bot peut acheter : sélection auto (les 21, réglage
        recommandé) ou sélection manuelle (cases cochées ; au départ, les 10
        plus rentables sur 2 ans, bénéfice des achats ET des ventes)."""
        st = self.data.state() if st is None else st
        req = self.data.selection_request()
        sel = st.get("selection") or {}
        universe = [b.lower() for b in self.g.universe]
        active = universe if req["mode"] == "auto" else req["manual"]
        ranking = sorted(sel.get("ranking") or [], key=lambda r: r["rank"])
        # Achetables seulement : assez échangées, assez anciennes, sans veto.
        top = [r["asset"] for r in ranking
               if r.get("eligible", True) and r["asset"] in universe][:self.MANUAL_TOP_N]
        return {"mode": req["mode"], "manual": req["manual"], "active": active,
                "ranking": ranking, "day": sel.get("day"),
                "universe": len(universe), "note": self.SELECTION_NOTE,
                "ranked": bool(ranking), "top": top, "n_top": self.MANUAL_TOP_N}

    def _last_buy(self, st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Dernier achat du bot : le panneau l'annonce dès qu'il change."""
        buys = self.data.buys(st)
        if not buys:
            return None
        b = buys[-1]
        return {"asset": b["asset"], "date": b["date"], "price": b["price"],
                "cost": b.get("cost"), "note": b.get("note"), "count": len(buys)}

    def assets(self) -> Dict[str, Any]:
        st = self.data.state()
        universe = [b.lower() for b in self.g.universe]
        held = {h["asset"] for h in self.data.holdings(st)}
        vetoes = st.get("vetoes") or {}
        why = (st.get("reasoning") or {}).get("assets") or {}
        sel = self.selection_view(st)
        active = set(sel["active"])
        ranks = {r["asset"]: r for r in sel["ranking"]}
        try:
            tick, stale = self.market.tickers(universe)
        except Exception:
            tick, stale = {}, True
        rows = []
        for a in universe:
            t = tick.get(a) or {}
            names = [n for n in mw.ASSET_NAMES.get(a, ()) if n != a]
            rows.append({"asset": a, "name": names[0].title() if names else a.upper(),
                         "price": t.get("price"), "change_pct": t.get("change_pct"),
                         "high": t.get("high"), "low": t.get("low"),
                         "volume_quote": t.get("volume_quote"), "held": a in held,
                         "vetoed": a in vetoes, "veto_reason": (vetoes.get(a) or {}).get("reason"),
                         "status": (why.get(a) or {}).get("status"),
                         "why": (why.get(a) or {}).get("text"),
                         "breakout_gap_pct": (why.get(a) or {}).get("breakout_gap_pct"),
                         "selected": a in active, "rank": ranks.get(a)})
        return {"assets": rows, "stale": stale, "selection": sel}

    def news_view(self) -> Dict[str, Any]:
        """Actualités et marchés, avec les cryptos du bot (hausses et baisses
        du jour, détenues) pour relier chaque article au portefeuille."""
        snap = self.news.snapshot() if self.news is not None else {
            "items": [], "sources": [], "markets": {}, "loading": False, "updated": None}
        st = self.data.state()
        held = sorted(h["asset"] for h in self.data.holdings(st))
        universe = [b.lower() for b in self.g.universe]
        try:
            tick, _stale = self.market.tickers(universe)
        except Exception:
            tick = {}
        rows = [{"asset": a, "price": t.get("price"), "change_pct": t.get("change_pct")}
                for a, t in tick.items() if t.get("change_pct") is not None]
        rows.sort(key=lambda r: r["change_pct"], reverse=True)
        snap["movers"] = {"up": rows[:5], "down": rows[::-1][:5]}
        snap["crypto_prices"] = {a: tick[a] for a in ("btc", "eth", "bnb") if a in tick}
        snap["held"] = held
        return snap

    def assistant_context(self) -> Dict[str, Any]:
        """Données PUBLIQUES transmises à l'assistant : aucune clé, aucun
        mot de passe, rien du fichier .env."""
        return {"status": self.status(), "news": self.news_view(),
                "reasoning": self.data.reasoning(), "positions": self.data.positions(),
                "anticipation": self.anticipation_view(), "security": self.security_view()}

    def anticipation_view(self) -> Dict[str, Any]:
        """Ce que le bot fera probablement à la prochaine clôture, avec les
        cours du moment (anticipation.py : mêmes règles que le bot)."""
        st = self.data.state()
        basis = st.get("anticipation")
        if not basis:
            return {"ready": False}
        universe = [b.lower() for b in self.g.universe]
        try:
            tick, stale = self.market.tickers(universe)
        except Exception:
            tick, stale = {}, True
        prices = {a: t["price"] for a, t in tick.items() if t.get("price")}
        equity = st.get("last_equity")
        pts = self.data.equity(days=2)
        if pts:
            equity = pts[-1]["v"]
        sel = self.selection_view(st)
        f = anticipation.forecast(
            basis, prices, self.data.holdings(st), datetime.now(timezone.utc), evolution.params_for(self.g),
            float(equity or getattr(self.g, "paper_capital", 10_000.0)),
            float(st.get("risk_mult", 1.0) or 1.0), sel["active"],
            (st.get("vetoes") or {}).keys(), bool(st.get("halted")),
            calibrate=learning.calibrator(st.get("learning")))
        return dict(f, ready=True, stale=stale)

    @staticmethod
    def _check(label: str, ok: Optional[bool], detail: str) -> Dict[str, Any]:
        """Une ligne du centre de sécurité : ok = True (vert), False (à
        corriger) ou None (information)."""
        return {"label": label, "ok": ok, "detail": detail}

    def _access_checks(self) -> List[Dict[str, Any]]:
        """Accès au panneau et essais de mot de passe ratés."""
        now = time.time()
        with self._lock:
            fails = len([t for t in self._login_failed_total if now - t < 86400])
        blocked = sum(1 for ip in list(self._login_fails) if self.login_blocked(ip))
        if self.loopback:
            access = self._check("Accès au panneau", True, "ce PC uniquement")
        else:
            access = self._check("Accès au panneau", bool(self.password),
                                 "Wi-Fi, protégé par mot de passe" if self.password
                                 else "Wi-Fi SANS mot de passe")
        return [access, self._check(
            "Essais de mot de passe ratés (24 h)", fails == 0,
            f"{fails} essai(s) raté(s)" + (f", {blocked} adresse(s) bloquée(s) 5 min" if blocked else "")
            + " ; blocage automatique après 5 échecs")]

    def _key_checks(self) -> List[Dict[str, Any]]:
        """Mode, clés Binance (présence seulement) et fichier des secrets."""
        live = self.g.run_mode == "live"
        has_keys = bool(os.environ.get("BINANCE_API_KEY")) and bool(os.environ.get("BINANCE_API_SECRET"))
        if has_keys:
            keys = "enregistrées dans le fichier privé .env"
        else:
            keys = "absentes : le mode réel ne peut pas démarrer" if live else "absentes (normal en paper)"
        try:
            gi = os.path.join(os.path.dirname(os.path.dirname(STATIC_DIR)), ".gitignore")
            env_ok = ".env" in open(gi, encoding="utf-8").read().split()
        except OSError:
            env_ok = False
        return [
            self._check("Mode", None if live else True,
                        "RÉEL : de vrais ordres sont passés" if live else "paper : aucun argent réel en jeu"),
            self._check("Clés API Binance", has_keys or not live, keys),
            self._check("Droit de retrait de la clé", None,
                        "doit rester désactivé : python trendguard_bot.py verify le contrôle auprès de Binance"),
            self._check("Clé partagée par erreur", None,
                        "une clé montrée dans une conversation ou une capture doit être supprimée sur Binance"),
            self._check("Fichier des secrets (.env)", True if env_ok else None, "privé, exclu de GitHub"),
        ]

    @staticmethod
    def _when(ts: Any) -> str:
        try:
            return time.strftime("%d/%m à %H:%M", time.localtime(float(ts)))
        except (TypeError, ValueError, OverflowError, OSError):
            return "?"

    def _alert_channels(self, st: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Canaux d'alerte et résultat de leur dernier envoi : envoi du bot
        (son état) ou test depuis ce panneau, le plus récent des deux."""
        if self.hub is None:
            return []
        seen: Dict[str, Dict[str, Any]] = {}
        for src in (st.get("alerts_last"), getattr(self.hub, "last", None)):
            for name, r in (dict(src) if isinstance(src, dict) else {}).items():
                if isinstance(r, dict) and float(r.get("at") or 0) >= float((seen.get(name) or {}).get("at") or 0):
                    seen[name] = r
        return [dict(c, last=seen.get(c["name"])) for c in self.hub.status()]

    def _alerts_check(self, st: Dict[str, Any]) -> Dict[str, Any]:
        """« Alertes » : à corriger si le dernier envoi d'un canal a échoué,
        vert si un envoi a réussi, information tant qu'aucun n'est connu."""
        chans = [c for c in self._alert_channels(st) if c.get("enabled")]
        if not chans:
            return self._check("Alertes", True if self.demo else False,
                               "démonstration" if self.demo
                               else "aucune : python trendguard_bot.py alerts configurer")
        parts, failed, sent = [], False, False
        for c in chans:
            r = c.get("last")
            if not r:
                parts.append(f"{c['label']} : aucun envoi connu, cliquez sur Tester (Réglages ▸ Alertes)")
            elif r.get("ok"):
                sent = True
                parts.append(f"{c['label']} : dernier envoi réussi le {self._when(r.get('at'))}")
            else:
                failed = True
                parts.append(f"{c['label']} : dernier envoi RATÉ le {self._when(r.get('at'))} "
                             f"({r.get('error') or 'cause inconnue'})")
        return self._check("Alertes", False if failed else True if sent else None, " ; ".join(parts))

    def _uptime_check(self, st: Dict[str, Any]) -> Dict[str, Any]:
        """Temps de marche du bot sur 7 jours, hors arrêts demandés."""
        u = uptime.summary(st.get("uptime"), st.get("last_cycle_ts"), st.get("stopped_at"))
        pct = u["week_pct"]
        if pct is None:
            return self._check("Disponibilité du bot (7 j)", None, "mesurée à partir du prochain cycle du bot")
        detail = f"{pct:.0f} % du temps (hors arrêts demandés)"
        missed = [e for e in u["events"] if not e["requested"]]
        if missed:
            e = missed[0]
            detail += (f" ; dernier arrêt : {uptime.fdur(e['end'] - e['start'])} le "
                       f"{self._when(e['start'])}, {e['text']}")
        if pct < uptime.GOOD_PCT:
            detail += " ; PC branché et mise en veille sur « Jamais » quand il est branché"
        return self._check("Disponibilité du bot (7 j)", pct >= uptime.GOOD_PCT, detail)

    def _evolution_check(self) -> Dict[str, Any]:
        """Évolution encadrée : niveau atteint, et rappel de ce qui reste
        hors de sa portée (information)."""
        ev = evolution.summary(self.g)
        if not ev["enabled"]:
            return self._check("Évolution encadrée", None, "désactivée : réglages fixes (TG_EVOLUTION=false)")
        return self._check("Évolution encadrée", None,
                           f"niveau {ev['level']} sur {ev['levels']} ({ev['name']}) ; ne touche jamais au "
                           "risque, aux plafonds, à l'arrêt d'urgence ni au mode réel")

    def _runtime_checks(self, st: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Arrêt d'urgence, relance automatique, disponibilité, alertes,
        garde-fou."""
        if st.get("halted"):
            halt = f"déclenché : {st.get('halt_reason')}"
        else:
            halt = (f"prêt, à −{self.g.kill_drawdown * 100:.0f} % depuis le plus haut"
                    + (" ; profil prudent actif" if self.g.params.dd_throttle else ""))
        try:
            sup = self.control.autonomy().get("supervisor") or {}
        except Exception:
            sup = {}
        return [
            self._check("Arrêt d'urgence", not bool(st.get("halted")), halt),
            self._check("Relance automatique", bool(sup.get("running")),
                        "active" if sup.get("running") else "inactive : cliquez sur AUTO"),
            self._uptime_check(st),
            self._evolution_check(),
            self._alerts_check(st),
            self._check("Garde-fou de Rachelle", True, "secrets masqués, demandes sensibles refusées"),
        ]

    def security_view(self) -> Dict[str, Any]:
        """Centre de sécurité : état des protections, sans jamais afficher
        une clé ni un mot de passe (seulement leur présence)."""
        checks = (self._access_checks() + self._key_checks()
                  + self._runtime_checks(self.data.state()))
        ok = sum(1 for c in checks if c["ok"] is True)
        warn = sum(1 for c in checks if c["ok"] is False)
        return {"checks": checks, "ok": ok, "warn": warn, "total": len(checks)}

    def chat(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        now = time.time()
        with self._lock:
            while self._chat_times and now - self._chat_times[0] > 60:
                self._chat_times.popleft()
            if len(self._chat_times) >= CHAT_PER_MINUTE:
                return 429, {"error": "Trop de questions d'un coup : réessayez dans une minute."}
            self._chat_times.append(now)
        return 200, self.assistant.reply(body.get("message"), body.get("history"),
                                         self.assistant_context)

    def candles(self, asset: str, interval: str, limit: int) -> Dict[str, Any]:
        asset = asset.lower()
        if asset not in {b.lower() for b in self.g.universe}:
            raise ValueError("crypto inconnue")
        if interval not in INTERVALS:
            raise ValueError("intervalle inconnu")
        rows, stale = self.market.klines(asset, interval, limit)
        step = INTERVALS[interval]
        t0, t1 = (rows[0][0], rows[-1][0]) if rows else (0, 0)

        def snap(iso: Any) -> Optional[int]:
            t = _ts(iso)
            if t is None:
                return None
            t = t // step * step
            return t if t0 <= t <= t1 else None

        st = self.data.state()
        position = None
        for h in self.data.holdings(st):
            if h["asset"] == asset:
                position = {k: h.get(k) for k in ("entry", "stop", "disaster", "qty", "risk", "entry_date")}
        current = _ts((position or {}).get("entry_date"))
        # Tous les achats du bot sur cette crypto (journal en temps réel), puis
        # ses ventes (trades clos).
        markers = []
        for b in self.data.buys(st):
            t = snap(b["date"]) if b["asset"] == asset else None
            if t is not None:
                live = current is not None and abs(b["t"] - current) < 1800
                markers.append({"t": t, "type": "buy", "price": b["price"], "qty": b.get("qty"),
                                "cost": b.get("cost"), "date": b["date"],
                                "text": "Achat en cours" if live else "Achat"})
        for tr in self.data.trades(st):
            t = snap(tr.get("date")) if tr.get("asset") == asset else None
            if t is not None:
                r = f"{float(tr.get('r') or 0):+.2f}".replace(".", ",")
                markers.append({"t": t, "type": "sell", "price": tr.get("exit"),
                                "date": tr.get("date"), "text": f"Vente {r} R"})
        markers.sort(key=lambda m: (m["t"], m["type"] != "buy"))
        return {"asset": asset, "interval": interval, "candles": rows, "stale": stale,
                "position": position, "markers": markers}

    # ---------- Routage ----------

    def _equity_view(self, days: int) -> Dict[str, Any]:
        """Courbe du capital avec les achats et les ventes du bot."""
        pts = self.data.equity(days=days)
        since = pts[0]["t"] if pts else 0
        st = self.data.state()
        buys = [{"t": b["t"], "asset": b["asset"], "price": b["price"]}
                for b in self.data.buys(st) if b["t"] >= since]
        sells = [{"t": t, "asset": tr["asset"], "price": tr.get("exit"), "r": tr.get("r")}
                 for tr in self.data.trades(st)
                 if (t := _ts(tr.get("date"))) is not None and t >= since]
        sells.sort(key=lambda x: x["t"])
        return {"points": pts, "buys": buys, "sells": sells}

    def _regime_view(self) -> Dict[str, Any]:
        reg, stale = self.market.regime(evolution.params_for(self.g).regime_sma)
        marks = []
        if reg.get("points"):
            # Achats et ventes de BTC par le bot, sur la bougie du jour.
            c = self.candles("btc", "1d", len(reg["points"]))
            t0 = reg["points"][0]["t"]
            marks = [m for m in c["markers"] if m["t"] >= t0]
        return dict(reg, stale=stale, markers=marks)

    def _save_selection(self, body: Dict[str, Any]) -> Dict[str, Any]:
        manual = body.get("manual")
        if body.get("preset") == "top":          # les 10 plus rentables
            manual = self.selection_view()["top"]
        elif not isinstance(manual, list):
            manual = self.data.selection_request()["manual"]
        self.data.save_selection(str(body.get("mode", "")), [str(a) for a in manual][:100])
        return dict(self.selection_view(), ok=True)

    def _test_alerts(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if self.hub is None:
            return {"ok": False, "message": "Alertes indisponibles en démonstration."}
        ok, err = self.hub.test(str(body.get("channel", "")))
        return {"ok": ok, "message": "Message de test envoyé." if ok else err}

    def api(self, method: str, path: str, query: Dict[str, List[str]],
            body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Routes de l'API : GET pour lire, POST pour agir (protégé par
        l'en-tête du panneau). Chaque route renvoie (code HTTP, données)."""
        q = lambda k, d="": (query.get(k) or [d])[0]          # noqa: E731
        reply = lambda res: {"ok": res[0], "message": res[1]}  # noqa: E731
        get = {
            "/api/health": lambda: {"ok": True},
            "/api/status": self.status,
            "/api/equity": lambda: self._equity_view(int(q("days", "90"))),
            "/api/positions": self.data.positions,
            "/api/trades": lambda: {"trades": self.data.trades()},
            "/api/assets": self.assets,
            "/api/candles": lambda: self.candles(q("asset", "btc"), q("interval", "1h"),
                                                 int(q("limit", "300"))),
            "/api/regime": self._regime_view,
            "/api/watch": self.data.watch,
            "/api/reasoning": self.data.reasoning,
            "/api/news": self.news_view,
            "/api/assistant": self.assistant.info,
            "/api/anticipation": self.anticipation_view,
            "/api/security": self.security_view,
            "/api/log": lambda: {"lines": self.data.log_tail(int(q("lines", "300")))},
        }
        post = {
            "/api/bot/start": lambda: reply(self.control.start()),
            "/api/bot/stop": lambda: reply(self.control.stop()),
            "/api/selection": lambda: self._save_selection(body),
            "/api/autostart": lambda: reply(self.control.set_autostart(body.get("enabled") is True)),
            "/api/alerts/test": lambda: self._test_alerts(body),
        }
        try:
            if method == "POST" and path == "/api/assistant":
                return self.chat(body)                   # (code, réponse) : limite de débit
            route = (get if method == "GET" else post if method == "POST" else {}).get(path)
            if route is None:
                return 404, {"error": "adresse inconnue"}
            return 200, route()
        except ValueError as e:
            return 400, {"error": str(e)}
        except Exception as e:
            return 502, {"error": f"{type(e).__name__} : {str(e)[:200]}"}


def make_handler(app: PanelApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TrendGuard-panneau"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:     # journal silencieux
            pass

        # ---------- Réponses ----------

        def _headers(self, code: int, ctype: str, length: int, extra: Optional[Dict[str, str]] = None,
                     cache: str = "no-store") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def _json(self, code: int, payload: Dict[str, Any], extra: Optional[Dict[str, str]] = None) -> None:
            data = json.dumps(_clean(payload), ensure_ascii=False, allow_nan=False).encode("utf-8")
            self._headers(code, "application/json; charset=utf-8", len(data), extra)
            if self.command != "HEAD":
                self.wfile.write(data)

        # ---------- Contrôles d'accès ----------

        def _host_ok(self) -> bool:
            if not app.loopback:
                return True
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in LOOPBACK

        def _token(self) -> Optional[str]:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "tg_session":
                    return v
            return None

        def _same_origin(self) -> bool:
            if self.headers.get("X-TrendGuard") != "1":
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin == f"http://{self.headers.get('Host')}"

        # ---------- Méthodes ----------

        def do_GET(self) -> None:
            if not self._host_ok():
                return self._json(421, {"error": "hôte refusé"})
            url = urlparse(self.path)
            if url.path.startswith("/api/"):
                if url.path != "/api/health" and not app.session_ok(self._token()):
                    return self._json(401, {"error": "connexion requise"})
                code, payload = app.api("GET", url.path, parse_qs(url.query), {})
                return self._json(code, payload)
            self._static(url.path)

        do_HEAD = do_GET

        def do_POST(self) -> None:
            # Corps lu AVANT toute réponse, même un refus : un corps non lu fait
            # couper la connexion par Windows (le navigateur ne verrait pas
            # la réponse). Taille bornée ; au-delà, connexion fermée.
            try:
                length = max(0, int(self.headers.get("Content-Length") or 0))
            except ValueError:
                length = 0
            if length > 32_000:
                self.close_connection = True
                return self._json(413, {"error": "requête trop volumineuse"})
            raw = self.rfile.read(length) if length else b""
            if not self._host_ok():
                return self._json(421, {"error": "hôte refusé"})
            if not self._same_origin():
                return self._json(403, {"error": "requête refusée (origine)"})
            url = urlparse(self.path)
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    body = {}
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "JSON invalide"})
            if url.path == "/api/login":
                if not app.password:
                    return self._json(200, {"ok": True})
                ip = self.client_address[0]
                wait = app.login_blocked(ip)
                if wait:
                    return self._json(429, {"ok": False, "error": (
                        f"trop d'essais ratés : réessayez dans {max(1, round(wait / 60))} min")})
                if hmac.compare_digest(str(body.get("password", "")).encode(), app.password.encode()):
                    app.login_ok(ip)
                    token = app.new_session()
                    cookie = (f"tg_session={token}; HttpOnly; SameSite=Strict; Path=/; "
                              f"Max-Age={SESSION_DAYS * 86400}")
                    return self._json(200, {"ok": True}, {"Set-Cookie": cookie})
                left = app.login_failed(ip)
                time.sleep(1.0)                          # freine les essais en rafale
                warn = ("" if left > 2 else " : accès bloqué 5 min" if not left else
                        f" ({left} essai{'s' if left > 1 else ''} avant blocage de 5 min)")
                return self._json(401, {"ok": False, "error": "mot de passe incorrect" + warn})
            if url.path == "/api/logout":
                app.drop_session(self._token())
                return self._json(200, {"ok": True},
                                  {"Set-Cookie": "tg_session=; Max-Age=0; Path=/; SameSite=Strict"})
            if not app.session_ok(self._token()):
                return self._json(401, {"error": "connexion requise"})
            code, payload = app.api("POST", url.path, {}, body)
            self._json(code, payload)

        def _static(self, path: str) -> None:
            rel = {"/": "index.html", "": "index.html"}.get(path, path.lstrip("/"))
            full = os.path.realpath(os.path.join(STATIC_DIR, rel))
            if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(full):
                return self._json(404, {"error": "introuvable"})
            ext = os.path.splitext(full)[1].lower()
            ctype = TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
            with open(full, "rb") as fh:
                data = fh.read()
            cache = "no-cache" if ext in (".html", ".js", ".css", ".webmanifest") else "max-age=86400"
            extra = {"Service-Worker-Allowed": "/"} if rel == "sw.js" else None
            self._headers(200, ctype, len(data), extra, cache)
            if self.command != "HEAD":
                self.wfile.write(data)

    return Handler


def build_app(gcfg: Any, demo: bool = False, password: str = "", loopback: bool = True,
              lan_urls: Optional[List[str]] = None) -> PanelApp:
    if demo:
        market = DemoMarket()
        return PanelApp(gcfg, DemoData(market), market, DemoControl(), None, True,
                        password, loopback, lan_urls, news=DemoNews())
    from trendguard import alerts
    market = Market(quote=gcfg.quote)
    return PanelApp(gcfg, BotData(gcfg, market), market, BotControl(gcfg),
                    alerts.build_notifier(), False, password, loopback, lan_urls,
                    news=NewsHub(universe=tuple(gcfg.universe)),
                    assistant=Assistant(AIHelper()))


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows : avec SO_REUSEADDR, un second panneau écouterait sur le même
    # port sans erreur (requêtes réparties au hasard entre les deux). Port
    # réservé en exclusivité : le second panneau s'arrête en le signalant.
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(app: PanelApp, host: str, port: int) -> ThreadingHTTPServer:
    return PanelServer((host, port), make_handler(app))


def main(gcfg: Any, host: str = "127.0.0.1", port: int = 8765, demo: bool = False,
         open_browser: bool = True) -> int:
    password = os.environ.get("PANEL_PASSWORD", "")
    loopback = host in LOOPBACK
    if not loopback and not password:
        print("❌ Accès depuis le réseau (--host 0.0.0.0) : définissez d'abord un mot de passe "
              "dans .env (PANEL_PASSWORD=...).", file=sys.stderr)
        return 2
    lan = [f"http://{ip}:{port}" for ip in lan_ips()] if not loopback else []
    app = build_app(gcfg, demo, password, loopback, lan)
    try:
        httpd = serve(app, host, port)
    except OSError as e:
        print(f"❌ Port {port} indisponible ({e}). Essayez --port 8766.", file=sys.stderr)
        return 1
    local = f"http://127.0.0.1:{port}"
    print(f"Panneau TrendGuard{' (démonstration)' if demo else ''} : {local}")
    for u in lan:
        print(f"Depuis un téléphone sur le même Wi-Fi : {u}")
    print("Ctrl+C pour fermer le panneau (le bot continue de tourner).")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(local)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if app.hub is not None:
            app.hub.close()
    return 0
