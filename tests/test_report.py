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


def test_secret_leaks_are_found_and_masked_in_logs(root):
    (root / "code.py").write_text(f"CLE = '{SECRET}'\n", encoding="utf-8")
    (root / "bot.log").write_text(f"2026-09-30 00:02:00,000 [INFO] mot de passe {SECRET} !\n",
                                  encoding="utf-8")
    env = {"SMTP_PASSWORD": SECRET, "TG_RISK_PCT": "0.01", "PANEL_PASSWORD": "court"}
    repo, logs = rps.check_secret_leaks(str(root), env, rp.Deps(run=FakeRun(files=["code.py"])))
    assert repo["ok"] is False and "SMTP_PASSWORD dans code.py" in repo["detail"]
    assert SECRET not in repo["detail"] + repo["reco"]
    text = (root / "bot.log").read_text(encoding="utf-8")
    assert SECRET not in text and "*" * len(SECRET) in text and logs["action"].endswith("bot.log")
    clean = rps.check_secret_leaks(str(root), env, rp.Deps(run=FakeRun(files=[])))
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
