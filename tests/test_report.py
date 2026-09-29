"""Rapport quotidien (trendguard/report.py) : protections appliquées seules
et sans risque (sauvegarde, droits du fichier des secrets, secrets masqués
dans les journaux), constats du fond et de la forme, rapport gardé puis
envoyé (e-mail complet, WhatsApp résumé), lancé par le bot à 00:30 UTC et
affiché dans le panneau."""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from test_alerts import FakeTelegram, Recorder
from test_panel import _cfg

import trendguard_bot as tg
import v29
from panel import assistant as pa
from panel import server as ps
from trendguard import alerts
from trendguard import bot as bot_module
from trendguard import report as rp
from trendguard.diagnostics import Finding

SECRET = "UnSecretTresLong123456"


def proc(stdout="", code=0, stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class FakeRun:
    """Commandes système simulées : git, PowerShell, icacls, powercfg, ruff."""

    def __init__(self, files=(), acl=(("S-1-5-18", "Allow"),), tracked_env=""):
        self.files, self.acl, self.tracked_env, self.calls = list(files), list(acl), tracked_env, []

    def __call__(self, cmd, cwd=None, env=None, **kw):
        self.calls.append(cmd)
        if cmd[0] == "git":
            args = cmd[1:]
            if args[:1] == ["ls-files"]:
                return proc(self.tracked_env if "--" in args else "\n".join(self.files))
            if args[:1] == ["log"]:
                return proc("")
            if args[:1] == ["status"]:
                return proc("")
            if args[:2] == ["remote", "get-url"]:
                return proc("https://github.com/Ncloclo/Ncloclo.git\n")
            if args[:1] == ["rev-parse"]:
                return proc("abc123\n")
        if cmd[0] == "powershell":
            script = cmd[-1]
            if "Get-Acl" in script:
                return proc("\n".join(f"{s}|{k}" for s, k in self.acl))
            if "NetFirewallProfile" in script:
                return proc("Domain|True\nPrivate|True\nPublic|True")
            if "MpComputerStatus" in script:
                return proc("True|1")
        if cmd[0] == "icacls":
            if "/remove:g" in cmd:
                self.acl = [(s, k) for s, k in self.acl if s not in rp.BROAD_SIDS]
            return proc("ok")
        if cmd[0] == "powercfg":
            idx = "0x00000708" if "STANDBYIDLE" in cmd else "0x00000001"
            return proc(f"Minimum 0x00000000\nMaximum 0xffffffff\nAC {idx}\nDC {idx}")
        if "ruff" in cmd:
            return proc("")
        return proc("", 1)


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".env").write_text(f"SMTP_PASSWORD={SECRET}\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".env\n*.log\n", encoding="utf-8")
    return tmp_path


def test_secrets_file_is_never_published(root):
    ok = rp.check_env_published(str(root), rp.Deps(run=FakeRun()))
    assert ok["ok"] is True and "jamais publié" in ok["detail"]
    bad = rp.check_env_published(str(root), rp.Deps(run=FakeRun(tracked_env=".env\n")))
    assert bad["ok"] is False and "PUBLIÉ" in bad["detail"] and "nouvelles" in bad["reco"]


def test_windows_acl_is_tightened_only_for_broad_groups(root):
    run = FakeRun(acl=[("S-1-5-18", "Allow"), ("S-1-5-32-544", "Allow"), ("S-1-5-32-545", "Allow")])
    c = rp.check_env_permissions(str(root), rp.Deps(run=run, platform="win32"))
    assert c["ok"] is True and "Utilisateurs" in c["action"]
    assert ["icacls", str(root / ".env"), "/inheritance:d"] in run.calls
    assert ["icacls", str(root / ".env"), "/remove:g", "*S-1-5-32-545"] in run.calls
    calm = FakeRun()
    c = rp.check_env_permissions(str(root), rp.Deps(run=calm, platform="win32"))
    assert c["ok"] is True and not c["action"] and not any(x[0] == "icacls" for x in calm.calls)


@pytest.mark.skipif(os.name == "nt", reason="droits POSIX")
def test_posix_permissions_are_tightened(root):
    os.chmod(root / ".env", 0o644)
    c = rp.check_env_permissions(str(root), rp.Deps(run=FakeRun(), platform="linux"))
    assert c["ok"] is True and "644 → 600" in c["action"]
    assert oct(os.stat(root / ".env").st_mode & 0o777) == "0o600"


def test_secret_leaks_are_found_and_masked_in_logs(root):
    (root / "code.py").write_text(f"CLE = '{SECRET}'\n", encoding="utf-8")
    (root / "bot.log").write_text(f"2026-09-30 00:02:00,000 [INFO] mot de passe {SECRET} !\n",
                                  encoding="utf-8")
    env = {"SMTP_PASSWORD": SECRET, "TG_RISK_PCT": "0.01", "PANEL_PASSWORD": "court"}
    repo, logs = rp.check_secret_leaks(str(root), env, rp.Deps(run=FakeRun(files=["code.py"])))
    assert repo["ok"] is False and "SMTP_PASSWORD dans code.py" in repo["detail"]
    assert SECRET not in repo["detail"] + repo["reco"]
    text = (root / "bot.log").read_text(encoding="utf-8")
    assert SECRET not in text and "*" * len(SECRET) in text and logs["action"].endswith("bot.log")
    clean = rp.check_secret_leaks(str(root), env, rp.Deps(run=FakeRun(files=[])))
    assert clean[0]["ok"] is True and not clean[1]["action"]


def _db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO kv VALUES (?, ?)", ("trendguard", json.dumps({"halted": False})))
    con.commit()
    con.close()


def test_database_backup_is_verified_and_rotated(tmp_path):
    db = tmp_path / "trendguard_paper.db"
    _db(db)
    (tmp_path / "trendguard_paper.selection.json").write_text("{}", encoding="utf-8")
    dest = tmp_path / "sauvegardes"
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        c = rp.backup_database(str(db), str(dest), day, keep=2)
    assert c["ok"] is True and "intégrité vérifiée" in c["detail"] and c["action"]
    assert sorted(os.listdir(dest)) == ["trendguard_paper-2026-09-29.db",
                                        "trendguard_paper-2026-09-29.selection.json",
                                        "trendguard_paper-2026-09-30.db",
                                        "trendguard_paper-2026-09-30.selection.json"]
    assert rp.check_database(str(db))["ok"] is True
    assert rp.read_state(str(db)) == {"halted": False}


def test_windows_checks_read_only():
    run = FakeRun()
    checks = {c["label"]: c for c in rp.check_windows(rp.Deps(run=run, platform="win32"))}
    assert checks["Pare-feu Windows"]["ok"] is True and checks["Antivirus"]["ok"] is True
    power = checks["Veille du PC (sur secteur)"]
    assert power["ok"] is False and "après 30 min" in power["detail"] and "veille" in power["detail"]
    assert not any(c[0] == "icacls" for c in run.calls)            # constat seulement
    assert rp.check_windows(rp.Deps(run=run, platform="linux")) == []


def test_decision_time_and_bot_health(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text("2026-09-30 00:02:41,000 [INFO] [DAILY]\n"
                   "2026-09-30 00:05:00,000 [WARNING] [CYCLE] réseau : délai\n", encoding="utf-8")
    now = datetime(2026, 9, 30, 0, 31, tzinfo=timezone.utc)
    assert rp.decision_time(str(log), now) == "00:02:41"
    g = tg.GuardConfig(log_file=str(log), lock_file=str(tmp_path / "tg.lock"), db_file=":memory:")
    status = {"state": "running", "last_cycle_age_s": 20, "uptime": {"week_pct": 61.2},
              "autonomy": {"supervisor": {"running": True}, "autostart": True}}
    checks = {c["label"]: c for c in rp.bot_checks(g, {}, status, now)}
    assert checks["Bot en marche"]["ok"] and checks["Décision du jour"]["ok"]
    assert checks["Disponibilité (7 jours)"]["ok"] is False and checks["Risque configuré"]["ok"]
    assert rp.check_log(str(log), now)["ok"] is True and "[CYCLE] × 1" in rp.check_log(str(log), now)["detail"]


def fake_gh(url):
    if "actions/runs" in url:
        return {"workflow_runs": [{"name": "Contrôles", "status": "completed", "conclusion": "success"}]}
    return [{"title": "Amélioration quotidienne 2026-09-30 : contrastes", "html_url": "https://x/pr/7"}]


def _deps(tmp_path, **kw):
    base = dict(run=FakeRun(files=[]), http_json=fake_gh, platform="linux",
                panel=lambda port, pw: {"ms": 12, "status": {"state": "running", "last_cycle_age_s": 5,
                                                             "uptime": {"week_pct": 99.0},
                                                             "autonomy": {"supervisor": {"running": True},
                                                                          "autostart": True}},
                                        "security": {"checks": [{"label": "Accès au panneau", "ok": True,
                                                                 "detail": "ce PC uniquement"},
                                                                {"label": "Mode", "ok": True, "detail": "paper"}]}},
                binance=lambda env, testnet: rp.chk("Clé API Binance", False, "refusée", "Vérifiez l'IP."),
                diagnose=lambda g: ([Finding("Marché", "OK", "haussier"),
                                     Finding("Données", "ATTENTION", "ETC peu liquide", "Rien à faire.")],
                                    "2026-09-29"),
                now=datetime(2026, 9, 30, 0, 31, tzinfo=timezone.utc), extra={"root": str(tmp_path)})
    base.update(kw)
    return rp.Deps(**base)


def test_report_is_complete_ordered_and_secret_free(root):
    db = root / "trendguard_paper.db"
    _db(db)
    g = tg.GuardConfig(db_file=str(db), log_file=str(root / "bot.log"), lock_file=str(root / "tg.lock"))
    r = rp.build(g, {"SMTP_PASSWORD": SECRET}, _deps(root))
    titles = [s["title"] for s in r["sections"]]
    assert titles[0] == "Sécurité" and "Stratégie (le fond)" in titles and titles[-1].endswith("(la forme)")
    labels = [c["label"] for s in r["sections"] for c in s["checks"]]
    assert labels.count("Mode") == 1                                  # pas de doublon du panneau
    assert r["recommendations"][0] == "Vérifiez l'IP." and "Rien à faire." in r["recommendations"]
    assert any("base sauvegardée" in a for a in r["actions"])
    assert r["proposals"] == ["Amélioration quotidienne 2026-09-30 : contrastes — https://x/pr/7"]
    assert r["score"]["total"] == len(labels) and r["verdict"].endswith("à corriger")
    assert "CE QUE LE BOT A FAIT SEUL" in r["text"] and "À FAIRE" in r["text"]
    assert len(r["short"]) <= rp.SHORT_MAX and r["short"].startswith("🛡️ TrendGuard")
    assert SECRET not in json.dumps(r, ensure_ascii=False)


def test_generate_saves_sends_and_refuses_a_second_run(root):
    db = root / "trendguard_paper.db"
    _db(db)
    g = tg.GuardConfig(db_file=str(db), log_file=str(root / "bot.log"), lock_file=str(root / "tg.lock"))
    email, wa = Recorder(), Recorder()
    email.name, wa.name = "email", "whatsapp"
    hub = alerts.AlertHub(FakeTelegram(enabled=False), [email, wa], async_mode=False)
    r = rp.generate(g, {}, deps=_deps(root), hub=hub)
    assert email.got[0][1] == r["text"] and wa.got[0][1] == r["short"]
    assert r["delivery"]["email"]["ok"] and rp.load_latest(g)["delivery"]["whatsapp"]["ok"]
    assert os.path.exists(root / "rapports" / "rapport-2026-09-30.txt")
    assert not rp.is_running(g)
    open(rp.paths(g)["lock"], "w").close()
    with pytest.raises(RuntimeError):
        rp.generate(g, {}, deps=_deps(root), hub=hub)


def test_bot_launches_the_report_once_after_0030(tmp_path, monkeypatch):
    lg = logging.getLogger("test.report")
    g = tg.GuardConfig(universe=("BTC",), db_file=":memory:", log_file=os.devnull,
                       lock_file=str(tmp_path / "tg.lock"), daily_report=True)
    bot = tg.TrendGuardBot(g, lg, None, v29.Store(":memory:", lg), lambda *a, **k: True)
    launched = []
    monkeypatch.setattr(bot_module.report, "launch", lambda gc, action: launched.append(action) or True)
    bot._launch_report(datetime(2026, 9, 30, 0, 40, tzinfo=timezone.utc))
    assert launched == []                                           # cycle isolé : jamais
    bot.track_uptime = True
    bot._launch_report(datetime(2026, 9, 30, 0, 29, tzinfo=timezone.utc))
    bot._launch_report(datetime(2026, 9, 30, 0, 31, tzinfo=timezone.utc))
    bot._launch_report(datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc))
    bot._launch_report(datetime(2026, 10, 1, 0, 35, tzinfo=timezone.utc))
    assert launched == ["quotidien", "quotidien"]


def test_panel_shows_the_report_and_its_delivery(tmp_path):
    app = ps.build_app(_cfg(tmp_path), demo=True)
    r = app.api("GET", "/api/report", {}, {})[1]
    assert r["ready"] and r["sections"] and "CE QUE LE BOT A FAIT SEUL" in r["text"]
    assert app.api("POST", "/api/report/run", {}, {})[1]["ok"] is False    # démonstration
    row = next(c for c in app.security_view()["checks"] if c["label"] == "Rapport quotidien")
    assert "conformes" in row["detail"]
    ans = pa.local_answer("Que dit le rapport du jour ?", {"report": r})["answer"]
    assert "Rapport quotidien du" in ans and "Réglages ▸ Rapport quotidien" in ans
    live = ps.build_app(_cfg(tmp_path, evolution=False), demo=False)
    live.hub = SimpleNamespace(last={}, status=lambda: [{"name": "email", "label": "E-mail", "enabled": True}])
    rp._save_json(rp.paths(live.g)["json"], {"ready": True, "delivery": {
        "email": {"at": 1e12, "ok": False, "error": "mot de passe refusé"}}})
    assert live._alerts_check({})["ok"] is False                    # échec d'envoi du rapport vu
