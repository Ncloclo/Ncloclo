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
from trendguard import evolution as ev
from trendguard import report as rp
from trendguard import report_health as rph
from trendguard import report_render as rpr
from trendguard import report_security as rps
from trendguard import systeme as sy
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
                self.acl = [(s, k) for s, k in self.acl if s not in rps.BROAD_SIDS]
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
    ok = rps.check_env_published(str(root), rp.Deps(run=FakeRun()))
    assert ok["ok"] is True and "jamais publié" in ok["detail"]
    bad = rps.check_env_published(str(root), rp.Deps(run=FakeRun(tracked_env=".env\n")))
    assert bad["ok"] is False and "PUBLIÉ" in bad["detail"] and "nouvelles" in bad["reco"]


def test_windows_acl_is_tightened_only_for_broad_groups(root):
    run = FakeRun(acl=[("S-1-5-18", "Allow"), ("S-1-5-32-544", "Allow"), ("S-1-5-32-545", "Allow")])
    c = rps.check_env_permissions(str(root), rp.Deps(run=run, platform="win32"))
    assert c["ok"] is True and "Utilisateurs" in c["action"]
    assert ["icacls", str(root / ".env"), "/inheritance:d"] in run.calls
    assert ["icacls", str(root / ".env"), "/remove:g", "*S-1-5-32-545"] in run.calls
    calm = FakeRun()
    c = rps.check_env_permissions(str(root), rp.Deps(run=calm, platform="win32"))
    assert c["ok"] is True and not c["action"] and not any(x[0] == "icacls" for x in calm.calls)


@pytest.mark.skipif(os.name == "nt", reason="droits POSIX")
def test_posix_permissions_are_tightened(root):
    os.chmod(root / ".env", 0o644)
    c = rps.check_env_permissions(str(root), rp.Deps(run=FakeRun(), platform="linux"))
    assert c["ok"] is True and "644 → 600" in c["action"]
    assert oct(os.stat(root / ".env").st_mode & 0o777) == "0o600"


def test_secret_leaks_are_found_and_masked_in_logs_and_command_histories(root, tmp_path):
    (root / "code.py").write_text(f"CLE = '{SECRET}'\n", encoding="utf-8")
    (root / "bot.log").write_text(f"2026-09-30 00:02:00,000 [INFO] mot de passe {SECRET} !\n",
                                  encoding="utf-8")
    typed = tmp_path / "ConsoleHost_history.txt"
    typed.write_text(f"cd bot\n$env:SMTP_PASSWORD='{SECRET}'\npython trendguard_bot.py run\n",
                     encoding="utf-8")
    env = {"SMTP_PASSWORD": SECRET, "TG_RISK_PCT": "0.01", "PANEL_PASSWORD": "court"}
    deps = rp.Deps(run=FakeRun(files=["code.py"]), extra={"histories": [str(typed)]})
    repo, logs, history = rps.check_secret_leaks(str(root), env, deps)
    assert repo["ok"] is False and "SMTP_PASSWORD dans code.py" in repo["detail"]
    assert SECRET not in repo["detail"] + repo["reco"]
    text = (root / "bot.log").read_text(encoding="utf-8")
    assert SECRET not in text and "*" * len(SECRET) in text and logs["action"].endswith("bot.log")
    lines = typed.read_text(encoding="utf-8").splitlines()     # même forme, secret masqué
    assert SECRET not in typed.read_text(encoding="utf-8") and len(lines) == 3
    assert history["ok"] is True and history["action"].endswith("ConsoleHost_history.txt")
    assert "set-keys" in history["reco"]
    clean = rps.check_secret_leaks(str(root), env, rp.Deps(run=FakeRun(files=[]),
                                                           extra={"histories": [str(typed)]}))
    assert clean[0]["ok"] is True and not clean[1]["action"] and not clean[2]["action"]
    assert rps.history_files(rp.Deps(run=FakeRun())) == []      # commandes simulées : rien de réel


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
        c = rps.backup_database(str(db), str(dest), day, keep=2)
    assert c["ok"] is True and "intégrité vérifiée" in c["detail"] and c["action"]
    assert sorted(os.listdir(dest)) == ["trendguard_paper-2026-09-29.db",
                                        "trendguard_paper-2026-09-29.selection.json",
                                        "trendguard_paper-2026-09-30.db",
                                        "trendguard_paper-2026-09-30.selection.json"]
    assert rps.check_database(str(db))["ok"] is True
    assert rp.read_state(str(db)) == {"halted": False}


def test_windows_checks_read_only():
    run = FakeRun()
    checks = {c["label"]: c for c in rps.check_windows(rp.Deps(run=run, platform="win32"))}
    assert checks["Pare-feu Windows"]["ok"] is True and checks["Antivirus"]["ok"] is True
    power = checks["Veille du PC (sur secteur)"]
    assert power["ok"] is False and "après 30 min" in power["detail"] and "veille" in power["detail"]
    assert "« Jamais »" in power["reco"] and "capot" in power["reco"]
    assert not any(c[0] == "icacls" for c in run.calls)            # constat seulement
    assert rps.check_windows(rp.Deps(run=run, platform="linux")) == []


def test_power_check_on_a_desktop_without_lid():
    def run(cmd, **kw):
        if "LIDACTION" in cmd:                                      # PC fixe : réglage absent
            return proc("GUID du mode : 381b4222 (Utilisation normale)\n")
        return proc("Minimum 0x00000000\nMaximum 0xffffffff\nAC 0x00000000\nDC 0x00000384")
    c = rps._power_check(rp.Deps(run=run, platform="win32"))
    assert c["ok"] is True and c["detail"] == "mise en veille : jamais" and not c["reco"]


def test_acl_check_retries_a_slow_powershell(root):
    calls = []

    def run(cmd, timeout=60, **kw):
        calls.append(timeout)
        if len(calls) == 1:
            raise rps.subprocess.TimeoutExpired(cmd, timeout)
        return proc("S-1-5-18|Allow")
    c = rps.check_env_permissions(str(root), rp.Deps(run=run, platform="win32"))
    assert c["ok"] is True and calls == [60, 180]
    never = rps.check_env_permissions(str(root), rp.Deps(
        run=lambda cmd, **kw: proc("", 1, "Accès refusé"), platform="win32"))
    assert never["ok"] is None and "Accès refusé" in never["detail"]


def test_windows_powershell_does_not_inherit_the_modules_of_powershell_7(monkeypatch):
    """Bot lancé depuis un terminal PowerShell 7 : sa variable PSModulePath
    empêchait Windows PowerShell de lire les droits du fichier des secrets."""
    seen = []
    monkeypatch.setattr(sy.subprocess, "run", lambda cmd, **kw: seen.append(kw["env"]) or proc("ok"))
    monkeypatch.setenv("PSModulePath", r"c:\program files\powershell\7\Modules")
    sy.run(["powershell", "-NoProfile", "-Command", "Get-Acl x"])
    sy.run(["powershell", "-Command", "x"], env={"PSMODULEPATH": "ps7", "TG_ACL_PATH": "fichier"})
    sy.run(["git", "status"])
    clean, given, untouched = seen
    assert clean and "PATH" in {k.upper() for k in clean}
    assert not any(k.upper() == "PSMODULEPATH" for k in clean)
    assert given == {"TG_ACL_PATH": "fichier"}
    assert untouched is None                                        # les autres commandes : rien ne change


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell")
def test_acl_is_read_even_when_started_from_powershell_7(root, monkeypatch):
    monkeypatch.setenv("PSModulePath", r"c:\program files\powershell\7\Modules;"
                       + os.environ.get("PSModulePath", ""))
    acl, err = rps._win_acl(str(root / ".env"), rp.Deps())
    assert acl and not err


def test_decision_time_and_bot_health(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text("2026-09-30 00:02:41,000 [INFO] [DAILY]\n"
                   "2026-09-30 00:05:00,000 [WARNING] [CYCLE] réseau : délai\n", encoding="utf-8")
    now = datetime(2026, 9, 30, 0, 31, tzinfo=timezone.utc)
    assert rph.decision_time(str(log), now) == "00:02:41"
    g = tg.GuardConfig(log_file=str(log), lock_file=str(tmp_path / "tg.lock"), db_file=":memory:")
    status = {"state": "running", "last_cycle_age_s": 20, "uptime": {"week_pct": 61.2},
              "autonomy": {"supervisor": {"running": True}, "autostart": True}}
    checks = {c["label"]: c for c in rph.bot_checks(g, {}, status, now)}
    assert checks["Bot en marche"]["ok"] and checks["Décision du jour"]["ok"]
    assert checks["Disponibilité (7 jours)"]["ok"] is False and checks["Risque configuré"]["ok"]
    assert "1 % par achat (2 % au plus si l'analyse du bot le justifie)" in checks["Risque configuré"]["detail"]
    assert "reprise automatique après 60 jours" in checks["Arrêt d'urgence"]["detail"]
    halted = {"halted": True, "halt_reason": "baisse de 41 %", "resume_note": "reprise automatique "
              "possible à partir du 2026-11-29 si le marché est haussier"}
    row = {c["label"]: c for c in rph.bot_checks(g, halted, status, now)}["Arrêt d'urgence"]
    assert row["ok"] is False and "2026-11-29" in row["detail"] and "resume" in row["reco"]
    assert rph.check_log(str(log), now)["ok"] is True and "[CYCLE] × 1" in rph.check_log(str(log), now)["detail"]


def test_unsent_telegram_alerts_are_not_bot_errors_and_precision_is_information(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text("2026-09-30 00:01:00,000 [ERROR] [BOOT] marchés indisponibles (NetworkError): x\n"
                   "2026-09-30 00:02:41,000 [INFO] [DAILY]\n"
                   "2026-09-30 00:03:00,000 [ERROR] [NOTIFY] ⚠️ TrendGuard a été arrêté\n"
                   "2026-09-30 00:03:00,000 [ERROR] [NOTIFIER-OFF] ⚠️ TrendGuard a été arrêté\n",
                   encoding="utf-8")
    c = rph.check_log(str(log), datetime(2026, 9, 30, 0, 31, tzinfo=timezone.utc))
    assert c["ok"] is True and "0 erreur(s)" in c["detail"]
    assert "1 coupure(s) d'Internet ou de Binance" in c["detail"]
    assert "1 alerte(s) critique(s) émise(s) (Telegram non configuré)" in c["detail"]
    log.write_text("2026-09-30 00:05:00,000 [ERROR] [CYCLE] KO: division par zéro\n", encoding="utf-8")
    bad = rph.check_log(str(log), datetime(2026, 9, 30, 0, 31, tzinfo=timezone.utc))
    assert bad["ok"] is False and "division par zéro" in bad["reco"]
    g = tg.GuardConfig(log_file=str(log), lock_file=str(tmp_path / "tg.lock"), db_file=":memory:")
    few = {"learning": {"brier": {"sell": {"n": 51.0, "raw": 5.0, "cal": 6.0}}}}
    row = next(c for c in rph.skills_checks(g, few) if c["label"] == "Précision des prévisions")
    assert row["ok"] is None and "jugée à partir de 100" in row["detail"] and not row["reco"]
    evo = tg.GuardConfig(log_file=str(log), lock_file=str(tmp_path / "tg.lock"), db_file=":memory:",
                         evolution=True)
    with open(ev.state_path(evo), "w", encoding="utf-8") as fh:
        json.dump({"risk": {"step": 1.25, "last_text": "Essai du palier 1,25 % par achat, jour 3 sur 30."}}, fh)
    row = next(c for c in rph.skills_checks(evo, few) if c["label"] == "Palier de risque")
    assert row["ok"] is None and row["detail"].startswith("1,25 % par achat (2 % au plus")
    assert "jour 3 sur 30" in row["detail"]


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
                                                                {"label": "Alertes", "ok": False,
                                                                 "detail": "E-mail : dernier envoi RATÉ"},
                                                                {"label": "Mode", "ok": True, "detail": "paper"},
                                                                {"label": "Alimentation du PC", "ok": False,
                                                                 "detail": "SUR BATTERIE (40 %)"}]}},
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
    assert "Alimentation du PC" not in labels                         # contrôlée par le rapport lui-même
    assert r["recommendations"][0] == "Vérifiez l'IP." and "Rien à faire." in r["recommendations"]
    assert "Alertes : E-mail : dernier envoi RATÉ" in r["recommendations"]
    assert any("base sauvegardée" in a for a in r["actions"])
    assert r["proposals"] == ["Amélioration quotidienne 2026-09-30 : contrastes — https://x/pr/7"]
    assert r["score"]["total"] == len(labels) and r["verdict"].endswith("à corriger")
    assert "CE QUE LE BOT A FAIT SEUL" in r["text"] and "À FAIRE" in r["text"]
    assert len(r["short"]) <= rpr.SHORT_MAX and r["short"].startswith("🛡️ TrendGuard")
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


def test_html_email_is_escaped_and_keeps_a_text_fallback(root):
    r = rp.build(tg.GuardConfig(db_file=":memory:", log_file=os.devnull, lock_file=str(root / "tg.lock")),
                 {}, _deps(root, binance=lambda env, t: rp.chk("Clé API Binance", False, "<script>x</script>",
                                                               "Vérifiez l'IP.")), backups=False)
    page = rpr.render_html(r)
    assert "<script>" not in page and "&lt;script&gt;x&lt;/script&gt;" in page
    assert "À faire, par ordre d'importance" in page and "Vérifiez l&#x27;IP." in page
    sent = {}

    class Smtp:
        def __init__(self, host, port):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            sent["html"] = msg.get_body(preferencelist=("html",)).get_content()
            sent["text"] = msg.get_body(preferencelist=("plain",)).get_content()

    email = alerts.EmailChannel("smtp.example.com", 587, "moi@example.com", "mdp", "moi@example.com",
                                smtp_factory=Smtp)
    hub = alerts.AlertHub(FakeTelegram(enabled=False), [email], async_mode=False)
    assert hub.send_report("Sujet", r["text"], r["short"], page) == {"email": None}
    assert sent["html"].startswith("<div") and "CE QUE LE BOT A FAIT SEUL" in sent["text"]


def test_score_history_gives_the_trend(root):
    g = tg.GuardConfig(db_file=":memory:", log_file=os.devnull, lock_file=str(root / "tg.lock"))
    for day, ok in (("2026-09-29", 26), ("2026-09-30", 27), ("2026-09-30", 28)):
        rp.save(g, {"day": day, "score": {"ok": ok, "warn": 3, "info": 5, "total": 36}, "text": "x"})
    hist = rp.load_latest(g)["history"]
    assert [(h["day"], h["ok"]) for h in hist] == [("2026-09-29", 26), ("2026-09-30", 28)]


def test_bot_launches_the_report_once_after_0030(tmp_path, monkeypatch):
    lg = logging.getLogger("test.report")
    g = tg.GuardConfig(universe=("BTC",), db_file=":memory:", log_file=os.devnull,
                       lock_file=str(tmp_path / "tg.lock"), daily_report=True)
    bot = tg.TrendGuardBot(g, lg, None, v29.Store(":memory:", lg), lambda *a, **k: True)
    launched = []
    monkeypatch.setattr(rp, "launch", lambda gc, action: launched.append(action) or True)
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
    rp.autonomy.write_json(rp.paths(live.g)["json"], {"ready": True, "delivery": {
        "email": {"at": 1e12, "ok": False, "error": "mot de passe refusé"}}})
    assert live._alerts_check({})["ok"] is False                    # échec d'envoi du rapport vu
    t = datetime.now(timezone.utc).timestamp()
    st = {"last_cycle_ts": t, "uptime": {"since": t - 60 * 3600, "events": [
        {"start": t - 30 * 3600, "end": t - 5 * 3600, "cause": "off"}]}}
    assert "capot fermé sur « Ne rien faire » quand il est branché" in live._uptime_check(st)["detail"]
    rp.autonomy.write_json(rp.paths(live.g)["json"], {"ready": True, "sections": [{"title": "Sécurité", "checks": [
        rp.chk("Veille du PC (sur secteur)", True, "mise en veille : jamais")]}]})
    assert "la mesure remonte jour après jour" in live._uptime_check(st)["detail"]


def test_laptop_on_battery_is_flagged():
    """Sur batterie, un portable s'endort capot fermé puis s'éteint : à dire."""
    def source(power):
        return rps._source_check(rp.Deps(platform="win32", extra={"power": power}))
    on_battery = source({"ac": False, "battery_pct": 85})
    assert on_battery["ok"] is False and "SUR BATTERIE (batterie à 85 %)" in on_battery["detail"]
    assert "chargeur" in on_battery["reco"]
    assert source({"ac": True, "battery_pct": 100})["ok"] is True
    assert source({"ac": True, "battery_pct": None}) is None       # PC fixe : rien à dire
    assert source(None) is None and rps._source_check(rp.Deps(run=FakeRun(), platform="win32")) is None


def test_power_check_names_the_power_button_on_a_laptop():
    def run(cmd, **kw):
        idx = {"STANDBYIDLE": 0, "LIDACTION": 0, "PBUTTONACTION": 1}[cmd[-1]]
        return proc(f"Index possible : 000\nAC 0x{idx:08x}\nDC 0x00000001")
    c = rps._power_check(rp.Deps(run=run, platform="win32"))
    assert c["ok"] is True and "capot fermé : ne rien faire" in c["detail"]
    assert "bouton d'alimentation : veille" in c["detail"] and "fermez le capot" in c["detail"]


def test_report_warns_before_the_disk_or_the_memory_is_full(root):
    def checks(**res):
        base = {"disk_free": 120.0, "disk_total": 240.0, "memory_used": 12.0, "memory_limit": 22.9}
        deps = rp.Deps(run=FakeRun(), extra={"resources": dict(base, **res)})
        return {c["label"]: c for c in rph.resource_checks(deps)}
    fine = checks()
    assert fine["Espace disque"]["ok"] and fine["Espace disque"]["detail"] == "120,0 Go libres sur 240 (50 %)"
    assert fine["Mémoire du PC"]["ok"] and "52 % réservés aux programmes (12,0 Go sur 22,9" in fine["Mémoire du PC"]["detail"]
    low = checks(disk_free=17.1, disk_total=240.3, memory_used=20.9)
    assert low["Espace disque"]["ok"] is False and "moins de 10 %" in low["Espace disque"]["reco"]
    assert low["Mémoire du PC"]["ok"] is False and "Windows peut arrêter le bot" in low["Mémoire du PC"]["reco"]
    assert checks(disk_free=1.5, disk_total=8.0)["Espace disque"]["ok"] is False      # moins de 2 Go
    assert list(checks(memory_used=None, memory_limit=None)) == ["Espace disque"]    # hors Windows
    assert rph.resource_checks(rp.Deps(run=FakeRun())) == []         # commandes simulées : rien de réel
    real = sy.pc_resources()                                         # la vraie mesure, sur cette machine
    assert real["disk_total"] > real["disk_free"] > 0
    if os.name == "nt":
        assert real["memory_limit"] > real["memory_used"] > 0


def test_disk_and_memory_are_part_of_the_bot_s_health(root):
    g = tg.GuardConfig(db_file=":memory:", log_file=os.devnull, lock_file=str(root / "tg.lock"))
    full = {"disk_free": 17.1, "disk_total": 240.3, "memory_used": 21.5, "memory_limit": 22.9}
    r = rp.build(g, {}, _deps(root, extra={"root": str(root), "resources": full}), backups=False)
    health = next(s for s in r["sections"] if s["title"] == "Santé du bot (le fond)")
    labels = [c["label"] for c in health["checks"]]
    assert labels[-2:] == ["Espace disque", "Mémoire du PC"]
    assert any("Libérez de la place" in x for x in r["recommendations"])
    assert any("Fermez des programmes" in x for x in r["recommendations"])


class WindowsRun(FakeRun):
    """Commandes de Windows en plus : chiffrement du disque, mises à jour."""

    def __init__(self, protection="1", last="2026-09-10", reboot="False", **kw):
        super().__init__(**kw)
        self.protection, self.last, self.reboot = protection, last, reboot

    def __call__(self, cmd, cwd=None, env=None, **kw):
        if cmd[0] == "powershell" and "BitLockerProtection" in cmd[-1]:
            return proc(self.protection)
        if cmd[0] == "powershell" and "Microsoft.Update.Session" in cmd[-1]:
            return proc((f"LAST|{self.last}\n" if self.last else "") + f"REBOOT|{self.reboot}")
        return super().__call__(cmd, cwd=cwd, env=env, **kw)


def test_disk_encryption_and_windows_updates_are_read_without_admin_rights():
    now = datetime(2026, 10, 1, 1, 0)
    deps = rp.Deps(run=WindowsRun(), platform="win32")
    enc = rps.check_encryption(deps)
    assert enc["ok"] is True and "actif" in enc["detail"]
    off = rps.check_encryption(rp.Deps(run=WindowsRun(protection="2"), platform="win32"))
    assert off["ok"] is None and "Chiffrement de l'appareil" in off["reco"]
    up = rps.check_windows_updates(deps, now)
    assert up["ok"] is True and "10/09/2026 (il y a 21 jour(s))" in up["detail"] and not up["reco"]
    late = rps.check_windows_updates(rp.Deps(run=WindowsRun(last="2026-07-01", reboot="True")), now)
    assert late["ok"] is False and "REDÉMARRAGE EN ATTENTE" in late["detail"] and "Windows Update" in late["reco"]
    pending = rps.check_windows_updates(rp.Deps(run=WindowsRun(reboot="True")), now)
    assert pending["ok"] is True and "Redémarrez le PC" in pending["reco"]
    unknown = rps.check_windows_updates(rp.Deps(run=FakeRun()), now)
    assert unknown["ok"] is None and unknown["detail"] == "état inconnu"
    labels = [c["label"] for c in rps.check_windows(deps)]
    assert labels[-2:] == ["Chiffrement du disque", "Mises à jour de Windows"]


def test_bot_folder_outside_cloud_sync():
    assert rps.check_sync_folder(r"C:\TrendGuard", {})["ok"] is True
    synced = rps.check_sync_folder(r"C:\Users\moi\OneDrive\TrendGuard", {"OneDrive": r"C:\Users\moi\OneDrive"})
    assert synced["ok"] is False and "OneDrive" in synced["reco"]
    assert rps.check_sync_folder("/home/moi/Dropbox/bot", {})["ok"] is False


class FakeBinance:
    def __init__(self, rights=None, refuse=False):
        self.rights, self.refuse = rights or {}, refuse

    def sapi_get_account_apirestrictions(self):
        if self.refuse:
            import ccxt
            raise ccxt.AuthenticationError('binance {"code":-2015,"msg":"Invalid API-key, IP"}')
        return self.rights


def test_binance_key_rights_least_privilege_age_and_current_address(monkeypatch):
    env = {"BINANCE_API_KEY": "k" * 20, "BINANCE_API_SECRET": "s" * 20}
    created = (datetime(2026, 9, 29, tzinfo=timezone.utc).timestamp()) * 1000
    good = {"enableWithdrawals": False, "enableSpotAndMarginTrading": True, "ipRestrict": True,
            "enableReading": True, "createTime": created}
    monkeypatch.setattr(rps.v29, "make_binance", lambda *a: FakeBinance(good))
    c = rps.binance_key_check(env)
    assert c["ok"] is True and "droits superflus : aucun" in c["detail"] and "créée il y a" in c["detail"]
    extra, age = rps.key_rights(dict(good, enableFutures=True, permitsUniversalTransfer=True),
                                now=datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
    assert extra == ["contrats à terme", "transferts universels"] and age == 2
    monkeypatch.setattr(rps.v29, "make_binance", lambda *a: FakeBinance(dict(good, enableMargin=True)))
    c = rps.binance_key_check(env)
    assert c["ok"] is False and "décochez marge (emprunts)" in c["reco"]
    old = dict(good, createTime=created - 200 * 86400 * 1000)
    monkeypatch.setattr(rps.v29, "make_binance", lambda *a: FakeBinance(old))
    assert "Renouvelez la clé Binance" in rps.binance_key_check(env)["reco"]
    monkeypatch.setattr(rps.v29, "make_binance", lambda *a: FakeBinance(refuse=True))
    c = rps.binance_key_check(env, ip_lookup=lambda: "160.155.219.186")
    assert c["ok"] is False and "160.155.219.186" in c["detail"] and "160.155.219.186" in c["reco"]
    assert "connexion à la maison" in c["reco"]
    assert rps.binance_key_check({})["detail"] == "absente (normal en paper)"


def test_report_says_why_scores_security_and_lists_changes(tmp_path):
    prev = {"sections": [
        {"title": "Sécurité", "checks": [rp.chk("Clé API Binance", False, "refusée"),
                                         rp.chk("Pare-feu Windows", True, "actif")]},
        {"title": "Compétences acquises", "checks": [rp.chk("Palier de risque", None, "1 %")]}]}
    now = [{"title": "Sécurité", "checks": [rp.chk("Clé API Binance", True, "acceptée"),
                                            rp.chk("Pare-feu Windows", False, "désactivé"),
                                            rp.chk("Chiffrement du disque", True, "actif")]},
           {"title": "Compétences acquises", "checks": [rp.chk("Palier de risque", None, "1,25 %")]}]
    assert rp.changes_since(prev, now) == ["corrigé : Clé API Binance",
                                           "nouveau point à corriger : Pare-feu Windows",
                                           "compétence : Palier de risque, 1,25 %"]
    assert rp.changes_since(None, now) == [] and rp.score_of(now[0]["checks"]) == {
        "ok": 2, "warn": 1, "info": 0, "total": 3}
    r = {"day": "2026-10-01", "generated_at": "2026-10-01T09:00:00+00:00", "mode": "paper",
         "score": {"ok": 30, "warn": 2, "info": 3, "total": 35}, "verdict": "2 point(s) à corriger",
         "motif": "après une compétence acquise (palier de risque : 1,25 %)",
         "security": {"ok": 12, "warn": 1, "info": 1, "total": 14},
         "changes": rp.changes_since(prev, now), "actions": [], "recommendations": ["Faire ceci."],
         "proposals": [], "sections": now}
    short = rpr.render_short(r)
    assert "sécurité 12/14 ✓" in short and "Pourquoi : après une compétence acquise" in short
    assert "Depuis le dernier rapport : corrigé : Clé API Binance" in short
    text = rpr.render_text(r)
    assert "Pourquoi ce rapport : après une compétence acquise" in text and "DEPUIS LE DERNIER RAPPORT" in text
    assert "Sécurité : 12 contrôle(s) conforme(s) sur 14, 1 à corriger." in text
    html = rpr.render_html(r)
    assert "Depuis le dernier rapport" in html and "Sécurité : 12 contrôle(s)" in html


def test_last_report_is_sent_again_by_email_and_whatsapp(tmp_path):
    g = tg.GuardConfig(lock_file=str(tmp_path / "tg.lock"), db_file=":memory:", log_file=os.devnull)
    report = {"day": "2026-10-01", "verdict": "Tout est en ordre", "text": "texte", "short": "court",
              "generated_at": "2026-10-01T00:31:00+00:00", "mode": "paper",
              "score": {"ok": 1, "warn": 0, "info": 0, "total": 1}, "actions": [],
              "recommendations": [], "proposals": [], "sections": []}
    rec = Recorder()
    hub = alerts.AlertHub(FakeTelegram(enabled=False), [rec], async_mode=False)
    sent = rp.send_again(g, report, {}, hub)
    assert sent["rec"]["ok"] and rec.got and rp.load_latest(g)["delivery"]["rec"]["ok"]
