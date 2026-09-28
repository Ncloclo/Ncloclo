"""Panneau de contrôle : API, sécurité (origine, hôte, mot de passe,
fichiers), démarrage et arrêt propre du bot, lecture seule de sa base."""

import dataclasses
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

import pytest

import autonomy
import trendguard_bot as tg
import v29
from panel import server as ps
from panel.control import BotControl
from panel.data import BotData
from panel.market import Market
from test_trendguard import SIM_FROM, make_bot, run_days, synthetic_market


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
    # Requête démesurée : refusée sans être lue.
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
