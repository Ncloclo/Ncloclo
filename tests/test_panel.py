"""Panneau de contrôle : API, sécurité (origine, hôte, mot de passe,
fichiers), démarrage et arrêt propre du bot, lecture seule de sa base."""

import dataclasses
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pytest

import trendguard_bot as tg
import v29
from panel import assistant as pa
from panel import demo
from panel import server as ps
from panel.control import BotControl
from panel.data import BotData
from panel.market import Market
from test_trendguard import SIM_FROM, make_bot, run_days, synthetic_market
from trendguard import autonomy, report_health


def _cfg(tmp_path, **kw):
    base = dict(run_mode="paper", db_file=str(tmp_path / "tg.db"), log_file=str(tmp_path / "tg.log"),
                lock_file=str(tmp_path / "tg.lock"), auto_diagnose_days=0)
    base.update(kw)
    return tg.GuardConfig(**base)


@pytest.fixture
def demo_server(tmp_path):
    app = ps.build_app(_cfg(tmp_path), demo=True)
    httpd = ps.serve(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", app
    httpd.shutdown()
    httpd.server_close()


def _req(url, method="GET", body=None, headers=None):
    h = {"X-TrendGuard": "1"} if method == "POST" else {}
    h.update(headers or {})
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _json(url, **kw):
    code, _h, body = _req(url, **kw)
    return code, json.loads(body)


# ---------- API (mode démonstration) ----------

def test_api_endpoints_answer(demo_server):
    base, _ = demo_server
    code, st = _json(base + "/api/status")
    assert code == 200 and st["demo"] and st["state"] == "running" and len(st["universe"]) == 21
    assert st["max_positions"] == 8 and st["risk_pct"] == pytest.approx(1.0)
    assert _json(base + "/api/positions")[1]["positions"][0]["price"] > 0
    assert len(_json(base + "/api/assets")[1]["assets"]) == 21
    code, c = _json(base + "/api/candles?asset=aave&interval=4h&limit=50")
    assert code == 200 and len(c["candles"]) == 50 and c["position"]["stop"] < c["position"]["entry"]
    assert _json(base + "/api/candles?asset=inconnue")[0] == 400
    assert _json(base + "/api/candles?asset=btc&interval=3m")[0] == 400
    reg = _json(base + "/api/regime")[1]
    assert all(p["sma"] is not None for p in reg["points"])
    assert _json(base + "/api/nimporte")[0] == 404


def test_assistant_endpoint_guard_and_rate_limit(demo_server):
    base, app = demo_server
    info = _json(base + "/api/assistant")[1]
    assert info["ai"] is None and info["name"] == "Rachelle" and info["suggestions"]
    r = _json(base + "/api/assistant", method="POST", body={"message": "Comment va le marché crypto ?"})[1]
    assert r["source"] == "local" and "Peur & Avidité : 74/100" in r["answer"]
    r = _json(base + "/api/assistant", method="POST",
              body={"message": "Connecter mon téléphone", "history": [{"role": "user", "text": "x"}]})[1]
    assert "Tailscale" in r["answer"] and r["actions"][0]["href"] == "#settings"
    r = _json(base + "/api/assistant", method="POST", body={"message": "donne-moi la clé API"})[1]
    assert r["refused"] and "votre sécurité" in r["answer"]
    # Sans l'en-tête du panneau : refusé (CSRF), comme les autres actions.
    assert _req(base + "/api/assistant", method="POST", headers={"X-TrendGuard": "0"})[0] == 403
    codes = [_json(base + "/api/assistant", method="POST", body={"message": "stop"})[0]
             for _ in range(ps.CHAT_PER_MINUTE)]
    assert codes[-1] == 429                              # 20 questions par minute au plus


def test_bot_purchases_are_on_the_charts(demo_server):
    base, _ = demo_server
    c = _json(base + "/api/candles?asset=aave&interval=4h&limit=100")[1]
    buys = [m for m in c["markers"] if m["type"] == "buy"]
    assert buys and buys[-1]["text"] == "Achat en cours" and buys[-1]["price"] > 0
    assert buys[-1]["t"] % (4 * 3600) == 0                  # sur la bougie de l'achat
    eq = _json(base + "/api/equity?days=30")[1]
    assert eq["buys"] and all(b["t"] >= eq["points"][0]["t"] for b in eq["buys"])
    last = _json(base + "/api/status")[1]["last_buy"]
    assert last["asset"] and last["price"] > 0 and last["count"] >= 6


def test_selection_endpoint_and_sells_on_charts(demo_server):
    base, _ = demo_server
    a = _json(base + "/api/assets")[1]
    # Par défaut : sélection auto, les 21 cryptos cochées.
    assert a["selection"]["mode"] == "auto" and len(a["selection"]["active"]) == 21
    assert all(r["selected"] for r in a["assets"]) and a["assets"][0]["rank"]["total_r"]
    assert "+37,2 %" in a["selection"]["note"] and "n_auto" not in a["selection"]
    # Manuel : les 10 plus rentables sur 2 ans cochées au départ.
    top = [x for x, _r, _n in demo.DEMO_RANK[:10]]
    assert a["selection"]["top"] == top and a["selection"]["n_top"] == 10
    r = _json(base + "/api/selection", method="POST", body={"mode": "manual", "preset": "top"})[1]
    assert r["mode"] == "manual" and set(r["active"]) == set(top) and len(r["active"]) == 10
    rows = {x["asset"]: x for x in _json(base + "/api/assets")[1]["assets"]}
    assert rows["aave"]["selected"] and not rows["algo"]["selected"]
    # Tout décocher : plus aucune crypto achetable.
    r = _json(base + "/api/selection", method="POST", body={"mode": "manual", "manual": []})[1]
    assert r["ok"] and r["mode"] == "manual" and r["active"] == []
    rows = {x["asset"]: x for x in _json(base + "/api/assets")[1]["assets"]}
    assert not any(x["selected"] for x in rows.values())
    r = _json(base + "/api/selection", method="POST", body={"mode": "auto"})[1]
    assert r["mode"] == "auto" and len(r["active"]) == 21
    r = _json(base + "/api/selection", method="POST", body={"mode": "manual", "manual": ["btc", "eth"]})[1]
    assert r["active"] == ["btc", "eth"]
    assert _json(base + "/api/status")[1]["selection"]["active"] == ["btc", "eth"]
    assert _json(base + "/api/selection", method="POST", body={"mode": "manual", "manual": ["zzz"]})[0] == 400
    eq = _json(base + "/api/equity?days=60")[1]
    assert eq["sells"] and eq["sells"][0]["r"] is not None       # ventes sur la courbe du capital
    reg = _json(base + "/api/regime")[1]
    assert {m["type"] for m in reg["markers"]} == {"buy", "sell"}  # achat et vente de BTC


def test_news_endpoint(demo_server):
    base, _ = demo_server
    code, n = _json(base + "/api/news")
    assert code == 200 and len(n["items"]) == 16 and not n["loading"]
    assert n["movers"]["up"][0]["change_pct"] >= n["movers"]["down"][0]["change_pct"]
    assert len(n["markets"]["quotes"]) == 10 and n["markets"]["fear_greed"]["value"] == 74
    assert set(n["held"]) == {"aave", "ada", "icp", "link", "ltc", "xlm"}


def test_reasoning_and_autonomy_endpoints(demo_server):
    base, _ = demo_server
    r = _json(base + "/api/reasoning")[1]
    assert r["current"]["lines"] and len(r["current"]["assets"]) == 21
    assert r["pending"][0]["asset"] == "bch" and r["history"]
    rows = {x["asset"]: x for x in _json(base + "/api/assets")[1]["assets"]}
    assert rows["eth"]["status"] == "watch" and "plus haut" in rows["eth"]["why"]
    au = _json(base + "/api/status")[1]["autonomy"]
    assert au["supervisor"]["running"] is True and au["autostart"] is True
    res = _json(base + "/api/autostart", method="POST", body={"enabled": False})[1]
    assert res["ok"] and _json(base + "/api/status")[1]["autonomy"]["autostart"] is False
    assert _json(base + "/api/autostart", method="POST", body={"enabled": True})[1]["ok"]


def test_start_stop_and_page(demo_server):
    base, _ = demo_server
    assert _json(base + "/api/bot/stop", method="POST")[1]["ok"] is True
    assert _json(base + "/api/status")[1]["state"] == "stopped"
    assert _json(base + "/api/bot/stop", method="POST")[1]["ok"] is False      # déjà arrêté
    assert _json(base + "/api/bot/start", method="POST")[1]["ok"] is True
    code, headers, body = _req(base + "/")
    assert code == 200 and b"TrendGuard" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"
    code, headers, _ = _req(base + "/vendor/lightweight-charts.standalone.production.js")
    assert code == 200 and headers["Content-Type"].startswith("text/javascript")


def test_foreign_pages_and_hosts_are_refused(demo_server):
    base, _ = demo_server
    # Sans l'en-tête du panneau (formulaire d'un autre site) : refusé.
    code, _h, _b = _req(base + "/api/bot/stop", method="POST", headers={"X-TrendGuard": "0"})
    assert code == 403
    code, _h, _b = _req(base + "/api/bot/stop", method="POST", headers={"Origin": "http://evil.example"})
    assert code == 403
    # « DNS rebinding » : un nom de domaine qui pointe vers ce PC.
    code, _h, _b = _req(base + "/api/status", headers={"Host": "evil.example"})
    assert code == 421
    # Requête démesurée : refusée sans être gardée (le refus parvient au navigateur).
    assert _req(base + "/api/assistant", method="POST", body={"message": "x" * 40_000})[0] == 413
    # Aucun fichier hors de panel/static.
    for path in ("/../trendguard_bot.py", "/..%2f..%2fv29.py", "/static/../../.env"):
        assert _req(base + path)[0] == 404


def test_password_required_from_the_network(tmp_path):
    app = ps.build_app(_cfg(tmp_path), demo=True, password="secret-du-panneau", loopback=False)
    httpd = ps.serve(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert _json(base + "/api/status")[0] == 401
        assert _json(base + "/api/bot/stop", method="POST")[0] == 401
        assert _json(base + "/api/login", method="POST", body={"password": "faux"})[0] == 401
        code, headers, _ = _req(base + "/api/login", method="POST", body={"password": "secret-du-panneau"})
        cookie = headers["Set-Cookie"].split(";")[0]
        assert code == 200 and "HttpOnly" in headers["Set-Cookie"]
        assert _json(base + "/api/status", headers={"Cookie": cookie})[0] == 200
        assert _req(base + "/api/health")[0] == 200            # contrôle de santé libre
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_login_is_locked_after_repeated_failures(tmp_path):
    app = ps.build_app(_cfg(tmp_path), demo=True, password="secret-du-panneau", loopback=False)
    httpd = ps.serve(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        # Quatre échecs déjà notés : le suivant prévient, puis bloque.
        app._login_fails["127.0.0.1"] = [time.time()] * (ps.LOGIN_MAX_FAILS - 1)
        code, r = _json(base + "/api/login", method="POST", body={"password": "faux"})
        assert code == 401 and "bloqué 5 min" in r["error"]
        # Bloqué, même avec le bon mot de passe.
        code, r = _json(base + "/api/login", method="POST", body={"password": "secret-du-panneau"})
        assert code == 429 and "réessayez dans 5 min" in r["error"]
        sec = app.security_view()
        row = next(c for c in sec["checks"] if c["label"].startswith("Essais"))
        assert row["ok"] is False and "1 adresse(s) bloquée(s)" in row["detail"]
        app._login_fails["127.0.0.1"] = [time.time() - ps.LOGIN_WINDOW_SEC - 1] * 5  # délai passé
        assert _req(base + "/api/login", method="POST", body={"password": "secret-du-panneau"})[0] == 200
        assert "127.0.0.1" not in app._login_fails                   # compteur remis à zéro
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_anticipation_and_security_endpoints(demo_server, monkeypatch):
    base, _ = demo_server
    monkeypatch.setenv("BINANCE_API_KEY", "cle-factice-a-ne-jamais-afficher")
    monkeypatch.setenv("BINANCE_API_SECRET", "secret-factice-a-ne-jamais-afficher")
    code, f = _json(base + "/api/anticipation")
    assert code == 200 and f["ready"] and 0 < f["hours_left"] <= 48
    # ICP, la plus proche de son stop, est la vente la plus probable (la
    # probabilité elle-même dépend de l'heure : moins de temps, moins de chances).
    assert f["sells"][0]["asset"] == "icp" and f["sells"][0]["prob"] > 0
    assert f["sells"][0]["prob"] == max(s["prob"] for s in f["sells"])
    bnb = next(b for b in f["buys"] if b["asset"] == "bnb")
    assert bnb["prob"] > 0.5 and "plafond de risque atteint" in bnb["blocked"]  # 6 % engagés
    assert f["risk"]["slots"] == 0 and f["risk"]["budget_pct"] == 6.0
    assert f["regime"]["bull_now"] == _json(base + "/api/status")[1]["regime_bull"] and f["advice"]
    code, headers, body = _req(base + "/api/security")
    sec = json.loads(body)
    assert code == 200 and sec["total"] == len(sec["checks"]) >= 9 and sec["ok"] >= 6
    assert b"factice" not in body and b"enregistr" in body                       # présence seulement
    ans = _json(base + "/api/assistant", method="POST", body={"message": "Que va faire le bot ce soir ?"})[1]
    assert "ICP" in ans["answer"] and "probabilité" in ans["answer"]
    ans = _json(base + "/api/assistant", method="POST", body={"message": "Suis-je en sécurité ?"})[1]
    assert "protections sur" in ans["answer"] and ans["actions"][0]["href"] == "#settings"


def test_manual_default_skips_cryptos_the_bot_cannot_buy(tmp_path):
    """Les 10 plus rentables cochées par défaut sont achetables : une crypto
    trop peu échangée ou bloquée par la veille est sautée."""
    app = ps.build_app(_cfg(tmp_path), demo=True)
    ranking = [{"asset": a, "rank": k + 1, "total_r": 20.0 - k, "trades": 5, "win_rate": 0.4,
                "eligible": a != "btc"} for k, a in enumerate(app.g.universe[i].lower()
                                                               for i in range(12))]
    top = app.selection_view({"selection": {"ranking": ranking[::-1]}})["top"]
    assert len(top) == 10 and "btc" not in top
    assert top == [r["asset"] for r in ranking if r["asset"] != "btc"][:10]
    assert app.selection_view({"selection": {}})["top"] == []      # classement pas encore calculé


def test_second_panel_on_the_same_port_is_refused(tmp_path):
    # Démarrage avec l'ordinateur + lancement manuel : un seul panneau par port
    # (sous Windows, SO_REUSEADDR laissait les deux écouter en même temps).
    app = ps.build_app(_cfg(tmp_path), demo=True)
    httpd = ps.serve(app, "127.0.0.1", 0)
    try:
        with pytest.raises(OSError):
            ps.serve(app, "127.0.0.1", httpd.server_address[1])
    finally:
        httpd.server_close()


def test_expired_sessions_are_forgotten(tmp_path):
    app = ps.build_app(_cfg(tmp_path), demo=True, password="secret-du-panneau")
    old = app.new_session()
    app._sessions[old] = time.time() - 1                  # expirée
    fresh = app.new_session()
    assert old not in app._sessions and app.session_ok(fresh) and not app.session_ok(old)


def test_network_access_refused_without_password(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PANEL_PASSWORD", raising=False)
    assert ps.main(_cfg(tmp_path), host="0.0.0.0", port=0, demo=True, open_browser=False) == 2
    assert "PANEL_PASSWORD" in capsys.readouterr().err


# ---------- Démarrer / arrêter le vrai bot ----------

def test_control_start_stop_with_the_bot_lock(tmp_path):
    g = _cfg(tmp_path)
    calls = []

    class Proc:
        def poll(self):
            return None
    ctl = BotControl(g, popen=lambda cmd, **kw: calls.append((cmd, kw)) or Proc())
    assert ctl.state() == "stopped"
    open(g.stop_file, "w").close()                  # demande d'arrêt périmée
    off = autonomy.sidecar(g.lock_file, ".off")
    open(off, "w").close()                          # automatisation arrêtée auparavant
    ok, _msg = ctl.start()
    cmd = calls[0][0]
    # AUTO lance le superviseur, qui lance le bot et le relance s'il plante.
    assert ok and cmd[-1] == "supervise" and cmd[-2].endswith("trendguard_bot.py")
    assert calls[0][1]["env"]["RUN_MODE"] == "paper"
    assert not os.path.exists(g.stop_file) and not os.path.exists(off)
    assert ctl.state() == "starting" and ctl.start()[0] is False
    ctl._starting_until = 0
    lock = v29.ProcessLock(g.lock_file)             # le bot tient son verrou
    lock.acquire()
    try:
        assert ctl.state() == "running"
        ok, msg = ctl.stop()
        assert ok and os.path.exists(g.stop_file) and "stops" in msg
        assert os.path.exists(off)                  # pas de relance, ni au démarrage du PC
        assert ctl.state() == "stopping"
    finally:
        lock.release()
    assert ctl.state() == "stopped" and ctl.stop()[0] is False
    # Superviseur vivant, bot arrêté sur une erreur : relance en attente.
    os.remove(off)
    with open(autonomy.sidecar(g.lock_file, ".superviseur.json"), "w") as fh:
        json.dump({"state": "waiting", "restarts": 2}, fh)
    sup = v29.ProcessLock(autonomy.sidecar(g.lock_file, ".superviseur.lock"))
    sup.acquire()
    try:
        assert ctl.state() == "restarting" and ctl.start()[0] is False
        assert ctl.autonomy()["supervisor"]["restarts"] == 2
        assert ctl.stop()[0] is True and ctl.state() == "stopping"
    finally:
        sup.release()


def test_bot_stops_cleanly_on_request(tmp_path, logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    bot.g = dataclasses.replace(bot.g, lock_file=str(tmp_path / "tg.lock"))
    assert bot.boot()
    assert bot.stop_requested() is False
    with open(bot.g.stop_file, "w") as fh:
        fh.write("maintenant")
    t0 = time.time()
    tg._sleep(30, bot.stop_requested)               # interrompu tout de suite
    assert time.time() - t0 < 5
    assert bot.stop_requested() is True and not os.path.exists(bot.g.stop_file)
    bot.run_forever()                               # sort aussitôt : arrêt demandé
    assert "last_cycle_ts" not in bot.state
    # Demande déposée plus d'une minute avant le démarrage : ignorée.
    other, _ = make_bot("paper", close, logger)
    other.g = dataclasses.replace(other.g, lock_file=str(tmp_path / "tg2.lock"))
    with open(other.g.stop_file, "w") as fh:
        fh.write("ancienne")
    old = time.time() - 3600
    os.utime(other.g.stop_file, (old, old))
    assert other.stop_requested() is False and not os.path.exists(other.g.stop_file)


@pytest.fixture
def logger():
    lg = logging.getLogger("test.panel")
    lg.setLevel(logging.WARNING)
    return lg


# ---------- Lecture seule de la base du bot ----------

def test_bot_data_reads_state_equity_and_log(tmp_path, logger):
    close, volume = synthetic_market()
    g = _cfg(tmp_path, universe=tuple(a.upper() for a in close.columns))
    bot, fb = make_bot("paper", close, logger)
    bot.store = v29.Store(g.db_file, logger)        # base sur disque, lue par le panneau
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 60)
    bot.store.close()
    with open(g.log_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"ligne {i}" for i in range(50)))

    class M:
        def tickers(self, bases):
            return {b: {"price": 1.0} for b in bases}, False
    data = BotData(g, M())
    st = data.state()
    assert st["last_decision_day"] == str(close.index[SIM_FROM + 59].date())
    eq = data.equity(days=100000)
    assert eq and all(p["v"] > 0 for p in eq)       # historique du capital enregistré
    held = data.positions()["positions"]
    assert {p["asset"] for p in held} == set(st["paper"]["holdings"])
    assert data.log_tail(10) == [f"ligne {i}" for i in range(40, 50)]
    buys = data.buys()
    assert {b["asset"] for b in buys} >= set(st["paper"]["holdings"])
    assert [b["t"] for b in buys] == sorted(b["t"] for b in buys)
    why = data.reasoning()
    assert why["current"]["day"] == st["last_decision_day"] and why["history"][0]["day"] == st["last_decision_day"]
    assert BotData(_cfg(tmp_path / "vide"), M()).state() == {}


def test_market_cache_and_stale_fallback():
    calls = {"n": 0, "down": False}

    def fetch(url):
        calls["n"] += 1
        if calls["down"]:
            raise OSError("réseau coupé")
        if "ticker/24hr" in url:
            return [{"symbol": "BTCUSDT", "lastPrice": "84000", "priceChangePercent": "1.5",
                     "highPrice": "85000", "lowPrice": "83000", "quoteVolume": "1e9"}]
        return [[1790467200000 + i * 86_400_000, "1", "2", "0.5", str(100 + i), "10"] for i in range(200)]
    m = Market(fetch)
    t, stale = m.tickers(["btc"])
    assert t["btc"]["price"] == 84000 and not stale
    m.tickers(["btc"])
    assert calls["n"] == 1                           # servi par le cache
    reg, _ = m.regime(sma=150, show=30)
    assert len(reg["points"]) == 30 and reg["bull"] is True
    calls["down"] = True
    m._cache = {k: (0, v) for k, (_t, v) in m._cache.items()}   # cache expiré
    t, stale = m.tickers(["btc"])
    assert stale and t["btc"]["price"] == 84000      # dernière valeur, signalée


# ---------- Cours Binance : jamais d'attente, une lecture pour toutes les pages ----------

def _binance(calls, down=None):
    """Binance simulé : chaque adresse demandée est notée dans `calls`."""
    def fetch(url):
        calls.append(url)
        if down and down[0]:
            raise OSError("réseau coupé")
        if "ticker/24hr" in url:
            symbols = json.loads(urllib.parse.unquote(url.split("symbols=")[1]))
            return [{"symbol": s, "lastPrice": "10", "priceChangePercent": "1", "highPrice": "11",
                     "lowPrice": "9", "quoteVolume": "1e6"} for s in symbols]
        n = int(urllib.parse.parse_qs(url.split("?")[1])["limit"][0])
        return [[1790467200000 + i * 3_600_000, "1", "2", "0.5", str(100 + i), "10"] for i in range(n)]
    return fetch


def _until(done, seconds=5.0):
    end = time.time() + seconds
    while not done() and time.time() < end:
        time.sleep(0.02)
    return done()


def test_market_serves_a_recent_value_at_once_and_refreshes_behind():
    calls, down, now = [], [False], [1000.0]
    m = Market(_binance(calls, down), clock=lambda: now[0])
    try:
        assert set(m.tickers(["btc", "eth"])[0]) == {"btc", "eth"} and len(calls) == 1
        now[0] += 15                                 # période écoulée : valeur rendue tout de suite…
        t, stale = m.tickers(["btc"])                # … une page qui n'en veut qu'une partie aussi
        assert set(t) == {"btc"} and not stale
        assert _until(lambda: len(calls) == 2)       # … et relue en arrière-plan, pour tous
        assert "BTCUSDT" in urllib.parse.unquote(calls[1]) and "ETHUSDT" in urllib.parse.unquote(calls[1])
        down[0] = True                               # panne : dernière valeur gardée, puis signalée
        now[0] += 15
        assert m.tickers(["btc"])[0]["btc"]["price"] == 10
        assert _until(lambda: m.tickers(["btc"])[1] is True)
        now[0] += 3600                               # trop vieille : relue tout de suite, panne dite
        t, stale = m.tickers(["eth"])
        assert stale and t["eth"]["price"] == 10
    finally:
        m.close()


def test_market_candles_are_read_once_per_pair_and_interval():
    calls = []
    m = Market(_binance(calls), background=False)
    rows, stale = m.klines("aave", "1h", 48)         # courbe d'une carte
    assert len(rows) == 48 and not stale and "limit=100" in calls[0]
    assert len(m.klines("AAVE", "1h", 72)[0]) == 72 and len(calls) == 1      # graphique : même lecture
    assert rows[-1] == m.klines("aave", "1h", 72)[0][-1]
    assert len(m.klines("aave", "1h", 300)[0]) == 300 and "limit=500" in calls[1]
    assert len(m.klines("aave", "1h", 48)[0]) == 48 and len(calls) == 2      # le palier large sert tout
    assert len(m.klines("aave", "4h", 48)[0]) == 48 and len(calls) == 3      # autre intervalle
    with pytest.raises(ValueError):
        m.klines("aave", "3m")


def test_market_warm_reads_the_pages_ahead():
    calls, down = [], [False]
    m = Market(_binance(calls, down), background=False)
    m.warm(["btc", "eth"], [("btc", "1d", 515)])
    assert len(calls) == 4                           # cours, 2 courbes horaires, régime
    m.tickers(["eth"])
    m.klines("btc", "1h", 48)
    reg, _stale = m.regime(150, 365)
    assert len(calls) == 4 and len(reg["points"]) == 365                     # rien à relire
    m.warm(["btc"], sparks=False)
    assert len(calls) == 4                           # cours encore frais
    down[0] = True
    Market(_binance([], down), background=False).warm(["btc"])               # panne : ignorée


# ---------- Courbe du capital, graphiques, alimentation, veille ----------

class _Data:
    """Base du bot simulée : démarrage et achats il y a 3 jours, relevés du
    capital depuis 2 jours seulement."""

    def __init__(self):
        self.t0 = int(time.time()) - 3 * 86400

    def state(self):
        return {"started_at": datetime.fromtimestamp(self.t0, timezone.utc).isoformat(),
                "start_equity": 10_000.0, "trades": []}

    def equity(self, days=90):
        return [{"t": self.t0 + 86400, "v": 9912.0}, {"t": self.t0 + 2 * 86400, "v": 9990.0}]

    def buys(self, st=None):
        return [{"t": self.t0 + 1, "asset": "aave", "price": 154.0, "date": ""}]

    def trades(self, st=None):
        return []

    def holdings(self, st=None):
        return []


def test_capital_curve_starts_with_the_bot_and_shows_its_first_buys(tmp_path):
    data = _Data()
    app = ps.PanelApp(_cfg(tmp_path), data, None, None)
    eq = app._equity_view(30)
    assert [p["v"] for p in eq["points"]] == [10_000.0] * 4 + [9912.0, 9990.0]
    times = [p["t"] for p in eq["points"]]
    assert times == sorted(set(times)) and times[3] == data.t0               # palier, puis départ
    assert eq["buys"] == [{"t": data.t0 + 1, "asset": "aave", "price": 154.0}]
    short = app._equity_view(1)                      # fenêtre qui commence après le départ
    assert len(short["points"]) == 2 and short["buys"] == []


def test_drawdown_is_never_positive():
    assert ps.drawdown_pct(9_000.0, 10_000.0) == -10.0
    assert ps.drawdown_pct(10_086.86, 10_074.83) == 0.0          # au-dessus du plus haut relevé
    assert ps.drawdown_pct(None, 10_000.0) is None and ps.drawdown_pct(10_000.0, None) is None
    # Le plus haut affiché n'est jamais sous le capital en direct.
    assert ps.shown_peak(10_232.04, 10_074.83) == 10_232.04 and ps.shown_peak(9_000.0, 10_000.0) == 10_000.0
    assert ps.shown_peak(None, None) is None and ps.shown_peak(10_000.0, None) == 10_000.0
    status = {"state": "running", "mode": "paper", "equity": 10_086.86, "start_equity": 10_000.0,
              "positions": 6, "max_positions": 8, "drawdown_pct": 0.0}
    assert "aucune, le capital est à son plus haut" in pa.a_status({"status": status})
    assert "−1,2 %" in pa.a_status({"status": dict(status, drawdown_pct=-1.2)})


def test_chart_window_is_the_rule_of_the_page():
    now = 1_800_000_000.0

    def entry(hours):
        return datetime.fromtimestamp(now - hours * 3600, timezone.utc).isoformat()
    assert ps.chart_window(entry(10), now) == ("1h", 72)
    assert ps.chart_window(entry(100), now) == ("4h", 55)
    assert ps.chart_window(entry(480), now) == ("1d", 40)
    assert ps.chart_window(None, now) == ("1h", 72)


def test_panel_warms_prices_candles_and_held_charts(tmp_path):
    asked = []

    class M:
        def warm(self, bases, charts, sparks=True):
            asked.append((len(list(bases)), list(charts), sparks))

    class D(_Data):
        def holdings(self, st=None):
            return [{"asset": "aave", "entry_date": datetime.now(timezone.utc).isoformat()}]
    app = ps.PanelApp(_cfg(tmp_path), D(), M(), None)
    app.warm()
    app.warm(full=False)
    assert asked[0] == (21, [("btc", "1d", 515), ("aave", "1h", 72)], True)
    assert asked[1] == (21, [], False)               # chaque minute : les cours seulement


def test_security_center_says_when_the_laptop_runs_on_battery(tmp_path):
    def rows(power):
        class Ctl:
            def autonomy(self):
                return {"power": power}
        return ps.PanelApp(_cfg(tmp_path), _Data(), None, Ctl())._power_check()
    bad = rows({"ac": False, "battery_pct": 85})
    assert bad[0]["ok"] is False and "SUR BATTERIE (batterie à 85 %) ; branchez le chargeur" in bad[0]["detail"]
    assert rows({"ac": True, "battery_pct": 100})[0]["ok"] is True
    assert rows({"ac": True, "battery_pct": None}) == [] and rows(None) == []    # PC fixe
    ctl = BotControl(_cfg(tmp_path), power=lambda: {"ac": False, "battery_pct": 40})
    assert ctl.autonomy()["power"] == {"ac": False, "battery_pct": 40}


def test_security_center_watches_disk_and_memory_like_the_report(tmp_path, monkeypatch):
    full = {"disk_free": 17.1, "disk_total": 240.3, "memory_used": 21.5, "memory_limit": 22.9}
    monkeypatch.setattr(report_health, "pc_resources", lambda deps=None, root="": full)
    app = ps.PanelApp(_cfg(tmp_path), _Data(), None, None)
    disk, memory = app._resource_checks()
    assert disk["label"] == "Espace disque" and disk["ok"] is False and "17,1 Go libres sur 240 (7 %)" in disk["detail"]
    assert "; libérez de la place" in disk["detail"]                # la marche à suivre, dans la ligne
    assert memory["ok"] is False and "94 % réservés" in memory["detail"] and "onglets" in memory["detail"]
    assert {"Espace disque", "Mémoire du PC"} <= ps.report.PANEL_DUPLICATES    # pas en double dans le rapport
    assert ps.build_app(_cfg(tmp_path), demo=True)._resource_checks() == []   # démonstration : rien


def test_watch_page_data(monkeypatch):
    from panel.data import ai_summary, watch_summary
    s = watch_summary({"day": "2026-09-30", "generated": "2026-09-30 00:03 UTC", "items": 60,
                       "indicators": {"fear_greed": 71, "fear_greed_label": "Greed", "usdc_usdt": 1.0004},
                       "providers": {"grok": {"ok": False, "error": "clé refusée"},
                                     "claude": {"ok": True, "seconds": 3.2}}, "errors": []})
    assert (s["items"], s["fear_greed"], s["usdc_usdt"]) == (60, 71, 1.0004)
    assert s["providers"] == [{"label": "Claude", "ok": True, "error": None},
                              {"label": "Grok", "ok": False, "error": "clé refusée"}]
    assert watch_summary({})["providers"] == [] and watch_summary({})["fear_greed"] is None
    for p in ps.mw.PROVIDERS:
        monkeypatch.delenv(p.key_env, raising=False)
    monkeypatch.setenv("MISTRAL_API_KEY", "cle-secrete-de-test")
    ai = ai_summary()
    assert ai["configured"] == ["Mistral"] and len(ai["possible"]) == len(ps.mw.PROVIDERS)
    assert "cle-secrete-de-test" not in json.dumps(ai)                       # le nom, jamais la clé
