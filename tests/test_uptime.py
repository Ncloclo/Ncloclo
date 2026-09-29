"""Disponibilité du bot : arrêts de plus d'une heure reconstitués d'après le
journal puis relevés à chaque reprise, alerte si l'arrêt n'était pas
demandé, affichage dans le panneau ; résultat du dernier envoi des alertes
dans le centre de sécurité."""

import logging
import os
import time

import pytest
from test_alerts import FakeTelegram, Recorder
from test_panel import _cfg

import trendguard_bot as tg
import v29
from panel import server as ps
from trendguard import alerts, uptime

H = 3600


@pytest.fixture
def logger():
    lg = logging.getLogger("test.uptime")
    lg.setLevel(logging.WARNING)
    return lg


def _write_log(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        for t, text in lines:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) + f",123 [INFO] {text}\n")


def _bot(tmp_path, logger, sent):
    g = tg.GuardConfig(run_mode="paper", universe=("BTC",), db_file=":memory:",
                       log_file=str(tmp_path / "tg.log"), lock_file=os.devnull, auto_diagnose_days=0)
    bot = tg.TrendGuardBot(g, logger, None, v29.Store(":memory:", logger),
                           lambda msg, **kw: sent.append((msg, kw)))
    return bot


def test_log_backfill_finds_stops_and_tells_requested_ones_apart(tmp_path):
    t0 = time.time() - 30 * H
    _write_log(tmp_path / "tg.log", [
        (t0, "TrendGuard — PAPER — 21 actifs"), (t0 + 900, "[HEARTBEAT] equity"),
        (t0 + 3 * H, "TrendGuard — PAPER — 21 actifs"),                 # 2 h 45 de silence : panne
        (t0 + 3 * H + 60, "[ARRÊT] demandé depuis le panneau de contrôle : arrêt propre"),
        (t0 + 8 * H, "TrendGuard — PAPER — 21 actifs"),                 # arrêt demandé
        (t0 + 8 * H + 900, "[HEARTBEAT] equity"),
        (t0 + 8 * H + 1800, "[HEARTBEAT] equity"),                      # 30 min : normal
        (t0 + 20 * H, "TrendGuard — PAPER — 21 actifs"),                # trou en cours : noté par le bot
    ])
    up = uptime.from_log(str(tmp_path / "tg.log"), until=t0 + 10 * H)
    assert up["since"] == pytest.approx(t0, abs=1)
    assert [(e["cause"], round((e["end"] - e["start"]) / 60)) for e in up["events"]] == \
        [("off", 165), ("user", 299)]
    assert uptime.from_log(str(tmp_path / "absent.log"), None) == {"since": None, "events": []}


def test_availability_leaves_requested_stops_out():
    now = 1_000_000_000.0
    events = [{"start": now - 50 * H, "end": now - 40 * H, "cause": "user"},
              {"start": now - 10 * H, "end": now - 4 * H, "cause": "off"}]
    assert uptime.availability(events, now - 100 * H, now, uptime.DAY_SEC) == 75.0
    # 7 jours, historique de 100 h dont 10 h d'arrêt demandé : 6 h perdues sur 90.
    assert uptime.availability(events, now - 100 * H, now, uptime.WEEK_SEC) == pytest.approx(93.3)
    assert uptime.availability([], now - 600, now, uptime.DAY_SEC) is None     # trop récent
    s = uptime.summary({"since": now - 100 * H, "events": events}, now - 2 * H, None, now)
    assert s["events"][0]["ongoing"] and s["events"][0]["cause"] == "off"    # arrêté en ce moment
    assert s["recent"]["minutes"] == 360 and not s["recent"]["requested"]
    assert s["day_pct"] == pytest.approx(66.7)
    s = uptime.summary({"since": now - 100 * H, "events": events}, now - 2 * H, now - H, now)
    assert s["events"][0]["requested"] and s["day_pct"] == pytest.approx(72.7)


def test_cause_of_a_gap():
    last, now = 1000.0, 1000.0 + 3 * H
    assert uptime.cause_of_gap(last, now, started_at=500, tries_since=now - 5, stopped_at=None) == "asleep"
    assert uptime.cause_of_gap(last, now, now - 60, now - 5, stopped_at=None) == "off"
    assert uptime.cause_of_gap(last, now, now - 60, now - 5, stopped_at=last + 10) == "user"
    assert uptime.cause_of_gap(last, now, 500, tries_since=last + 60, stopped_at=None) == "network"
    assert uptime.crossed_close({"start": 86400 * 5 - 60, "end": 86400 * 5 + 200}, 120)
    assert not uptime.crossed_close({"start": 86400 * 5 + 200, "end": 86400 * 5 + 2 * H}, 120)


def test_bot_notes_an_unrequested_stop_and_warns(tmp_path, logger):
    now = time.time()
    # Une panne de 2 h 45 dans le journal, puis une ligne par quart d'heure.
    _write_log(tmp_path / "tg.log", [(now - 30 * H, "TrendGuard — PAPER"),
                                     (now - 29.75 * H, "[HEARTBEAT] equity")]
               + [(now - 27 * H + k * 900, "[HEARTBEAT] equity") for k in range(98)])
    sent = []
    bot = _bot(tmp_path, logger, sent)
    bot.state = {"last_cycle_ts": now - 2.5 * H}
    bot._started_at, bot._tries_since = now - 60, now - 30          # redémarré au retour du PC
    bot._note_downtime()
    ev = bot.state["uptime"]["events"]
    assert [e["cause"] for e in ev] == ["off", "off"]                 # journal, puis ce trou
    assert bot.state["uptime"]["since"] == pytest.approx(now - 30 * H, abs=1)
    assert len(sent) == 1 and sent[0][1]["critical"]
    assert "arrêté 2 h 30" in sent[0][0] and "Alimentation" in sent[0][0]
    # Trou d'une demi-heure : rien.
    bot.state["last_cycle_ts"] = time.time() - 1800
    bot._note_downtime()
    assert len(bot.state["uptime"]["events"]) == 2 and len(sent) == 1


def test_requested_stop_is_noted_and_kept_silent(tmp_path, logger):
    sent = []
    bot = _bot(tmp_path, logger, sent)
    bot._stop_flag = True
    bot.run_forever()                                   # arrêt demandé : noté
    assert bot.state["stopped_at"] == pytest.approx(time.time(), abs=5)
    now = time.time()
    bot.state.update(last_cycle_ts=now - 2 * H - 5, stopped_at=now - 2 * H,
                     uptime={"since": now - 10 * H, "events": []})
    bot._started_at, bot._tries_since = now - 60, now - 10      # relancé avec AUTO
    bot._note_downtime()
    assert bot.state["uptime"]["events"][0]["cause"] == "user" and sent == []


def test_last_send_result_is_kept_per_channel():
    ch = Recorder(fail=True)
    hub = alerts.AlertHub(FakeTelegram(enabled=False), [ch], level="all", async_mode=False)
    hub("Achat de BTC")
    assert hub.last["rec"]["ok"] is False and "injoignable" in hub.last["rec"]["error"]
    ch.fail = False
    assert hub.test("rec") == (True, "")
    assert hub.last["rec"]["ok"] is True and hub.last["rec"]["error"] is None


class FakeHub:
    def __init__(self):
        self.last = {}

    def status(self):
        return [{"name": "telegram", "label": "Telegram", "enabled": False},
                {"name": "email", "label": "E-mail", "enabled": True}]


def test_security_center_flags_failed_alerts_and_low_availability(tmp_path):
    app = ps.build_app(_cfg(tmp_path), demo=True)
    app.hub, app.demo = FakeHub(), False
    check = app._alerts_check({})
    assert check["ok"] is None and "aucun envoi connu" in check["detail"]
    t = time.time()
    failed = {"alerts_last": {"email": {"at": t - 60, "ok": False, "error": "mot de passe refusé"}}}
    check = app._alerts_check(failed)
    assert check["ok"] is False and "RATÉ" in check["detail"] and "mot de passe refusé" in check["detail"]
    app.hub.last = {"email": {"at": t, "ok": True, "error": None}}      # test réussi depuis
    assert app._alerts_check(failed)["ok"] is True
    assert app._alert_channels(failed)[1]["last"]["ok"] is True
    st = {"last_cycle_ts": t, "uptime": {"since": t - 60 * H, "events": [
        {"start": t - 30 * H, "end": t - 5 * H, "cause": "off"}]}}
    check = app._uptime_check(st)
    assert check["ok"] is False and check["detail"].startswith("58 % du temps")
    assert "25 h 00" in check["detail"] and "Jamais" in check["detail"]
    assert app._uptime_check({})["ok"] is None


def test_demo_status_shows_availability(tmp_path):
    app = ps.build_app(_cfg(tmp_path), demo=True)
    u = app.status()["uptime"]
    assert u["tracked"] and u["recent"]["cause"] == "asleep" and u["week_pct"] > 95
    assert u["day_pct"] == pytest.approx(93.8)
    row = next(c for c in app.security_view()["checks"] if c["label"].startswith("Disponibilité"))
    assert row["ok"] is True
