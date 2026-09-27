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

import market_watch as mw

from .control import BotControl
from .data import BotData, _ts
from .demo import DemoControl, DemoData, DemoMarket
from .market import INTERVALS, Market

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SESSION_DAYS = 30
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
                 lan_urls: Optional[List[str]] = None):
        self.g = gcfg
        self.data, self.market, self.control, self.hub = data, market, control, hub
        self.demo = demo
        self.password = password
        self.loopback = loopback
        self.lan_urls = lan_urls or []
        self._sessions: Dict[str, float] = {}
        self._lock = threading.Lock()

    # ---------- Sessions ----------

    def new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = time.time() + SESSION_DAYS * 86400
        return token

    def session_ok(self, token: Optional[str]) -> bool:
        if not self.password:
            return True
        with self._lock:
            exp = self._sessions.get(token or "")
        return bool(exp and exp > time.time())

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
            "next_decision_s": int((nxt - now).total_seconds()),
            "last_cycle_age_s": int(time.time() - float(last_cycle)) if last_cycle else None,
            "positions": len(self.data.holdings(st)), "max_positions": p.max_positions,
            "risk_pct": p.risk_pct * 100, "max_total_risk_pct": p.max_total_risk * 100,
            "dd_throttle": [list(x) for x in p.dd_throttle],
            "kill_drawdown_pct": self.g.kill_drawdown * 100,
            "universe": [b.lower() for b in self.g.universe],
            "vetoes": [dict(v, asset=a) for a, v in sorted((st.get("vetoes") or {}).items())],
            "watch": st.get("last_watch"),
            "alerts": self.hub.status() if self.hub is not None else [],
            "lan_urls": self.lan_urls, "password": bool(self.password),
            "server_time": now.isoformat(),
        }

    def assets(self) -> Dict[str, Any]:
        st = self.data.state()
        universe = [b.lower() for b in self.g.universe]
        held = {h["asset"] for h in self.data.holdings(st)}
        vetoes = st.get("vetoes") or {}
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
                         "vetoed": a in vetoes, "veto_reason": (vetoes.get(a) or {}).get("reason")})
        return {"assets": rows, "stale": stale}

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
        markers = []
        for tr in self.data.trades(st):
            if tr.get("asset") != asset:
                continue
            for when, kind, price in ((tr.get("entry_date"), "buy", tr.get("entry")),
                                      (tr.get("date"), "sell", tr.get("exit"))):
                t = snap(when)
                if t is not None:
                    markers.append({"t": t, "type": kind, "price": price,
                                    "text": "Achat" if kind == "buy" else f"Vente {tr.get('r', 0):+.2f} R"})
        position = None
        for h in self.data.holdings(st):
            if h["asset"] == asset:
                position = {k: h.get(k) for k in ("entry", "stop", "disaster", "qty", "risk", "entry_date")}
                t = snap(h.get("entry_date"))
                if t is not None:
                    markers.append({"t": t, "type": "buy", "price": h["entry"], "text": "Achat (en cours)"})
        markers.sort(key=lambda m: m["t"])
        return {"asset": asset, "interval": interval, "candles": rows, "stale": stale,
                "position": position, "markers": markers}

    # ---------- Routage ----------

    def api(self, method: str, path: str, query: Dict[str, List[str]],
            body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        q = lambda k, d="": (query.get(k) or [d])[0]          # noqa: E731
        try:
            if method == "GET":
                if path == "/api/health":
                    return 200, {"ok": True}
                if path == "/api/status":
                    return 200, self.status()
                if path == "/api/equity":
                    return 200, {"points": self.data.equity(days=int(q("days", "90")))}
                if path == "/api/positions":
                    return 200, self.data.positions()
                if path == "/api/trades":
                    return 200, {"trades": self.data.trades()}
                if path == "/api/assets":
                    return 200, self.assets()
                if path == "/api/candles":
                    return 200, self.candles(q("asset", "btc"), q("interval", "1h"),
                                             int(q("limit", "300")))
                if path == "/api/regime":
                    reg, stale = self.market.regime(self.g.params.regime_sma)
                    return 200, dict(reg, stale=stale)
                if path == "/api/watch":
                    return 200, self.data.watch()
                if path == "/api/log":
                    return 200, {"lines": self.data.log_tail(int(q("lines", "300")))}
            if method == "POST":
                if path == "/api/bot/start":
                    ok, msg = self.control.start()
                    return 200, {"ok": ok, "message": msg}
                if path == "/api/bot/stop":
                    ok, msg = self.control.stop()
                    return 200, {"ok": ok, "message": msg}
                if path == "/api/alerts/test":
                    if self.hub is None:
                        return 200, {"ok": False, "message": "Alertes indisponibles en démonstration."}
                    ok, err = self.hub.test(str(body.get("channel", "")))
                    return 200, {"ok": ok, "message": "Message de test envoyé." if ok else err}
            return 404, {"error": "adresse inconnue"}
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
            if not self._host_ok():
                return self._json(421, {"error": "hôte refusé"})
            if not self._same_origin():
                return self._json(403, {"error": "requête refusée (origine)"})
            url = urlparse(self.path)
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 10_000)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                if not isinstance(body, dict):
                    body = {}
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "JSON invalide"})
            if url.path == "/api/login":
                if not app.password:
                    return self._json(200, {"ok": True})
                if hmac.compare_digest(str(body.get("password", "")).encode(), app.password.encode()):
                    token = app.new_session()
                    cookie = (f"tg_session={token}; HttpOnly; SameSite=Strict; Path=/; "
                              f"Max-Age={SESSION_DAYS * 86400}")
                    return self._json(200, {"ok": True}, {"Set-Cookie": cookie})
                time.sleep(1.0)                          # freine les essais en rafale
                return self._json(401, {"ok": False, "error": "mot de passe incorrect"})
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
                        password, loopback, lan_urls)
    import alerts
    market = Market(quote=gcfg.quote)
    return PanelApp(gcfg, BotData(gcfg, market), market, BotControl(gcfg),
                    alerts.build_notifier(), False, password, loopback, lan_urls)


def serve(app: PanelApp, host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd


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
