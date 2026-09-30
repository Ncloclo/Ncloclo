"""Alertes : e-mail et WhatsApp à côté de Telegram, selon le niveau,
sans jamais bloquer ni faire échouer le bot."""

import io
import os
import smtplib

from trendguard import alerts


class FakeSMTP:
    sent = []

    def __init__(self, host, port):
        self.host, self.port, self.log = host, port, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.log.append("starttls")

    def login(self, user, password):
        self.log.append(("login", user))

    def send_message(self, msg):
        FakeSMTP.sent.append((msg["To"], msg["Subject"], msg.get_content()))


class FakeTelegram:
    def __init__(self, enabled=True):
        self.enabled, self.calls = enabled, []

    def __call__(self, msg, dedup_key=None, critical=False, sync=False):
        self.calls.append((msg, critical))
        return True

    def close(self):
        pass


class Recorder:
    name, label, enabled, secrets = "rec", "Enregistreur", True, []

    def __init__(self, fail=False):
        self.fail, self.got = fail, []

    def send(self, subject, text):
        if self.fail:
            raise RuntimeError("serveur injoignable")
        self.got.append((subject, text))


def test_email_channel_uses_starttls_and_login():
    FakeSMTP.sent = []
    ch = alerts.EmailChannel("smtp.example.com", 587, "moi@example.com", "mdp", "moi@example.com",
                             smtp_factory=FakeSMTP)
    ch.send("🛑 TrendGuard : alerte critique", "Arrêt d'urgence")
    assert FakeSMTP.sent == [("moi@example.com", "🛑 TrendGuard : alerte critique", "Arrêt d'urgence\n")]
    env = {"SMTP_HOST": "smtp.gmail.com", "ALERT_EMAIL_TO": "a@b.c", "SMTP_PORT": "pas-un-nombre"}
    assert alerts.EmailChannel.from_env(env).port == 587 and alerts.EmailChannel.from_env(env).enabled
    assert not alerts.EmailChannel.from_env({}).enabled


def test_whatsapp_callmebot_and_twilio():
    seen = []
    cmb = alerts.WhatsAppCallMeBot("+2250700000000", "123456",
                                   get=lambda url: seen.append(url) or (200, "Message queued"))
    cmb.send("TrendGuard", "Bonjour")
    assert "phone=%2B2250700000000" in seen[0] and "apikey=123456" in seen[0]
    bad = alerts.WhatsAppCallMeBot("+225", "k", get=lambda url: (200, "APIKey is invalid. ERROR"))
    try:
        bad.send("s", "t")
        raise AssertionError("erreur attendue")
    except RuntimeError as e:
        assert "CallMeBot" in str(e)
    posted = []
    tw = alerts.WhatsAppTwilio("+2250700000000", "AC123", "jeton", "+14155238886",
                               post=lambda url, form, auth: posted.append((url, form, auth)) or (201, "{}"))
    tw.send("TrendGuard", "x" * 5000)
    url, form, _auth = posted[0]
    assert "AC123/Messages.json" in url and form["To"] == "whatsapp:+2250700000000"
    assert len(form["Body"]) <= alerts.WHATSAPP_MAX
    env = {"WHATSAPP_PROVIDER": "twilio", "WHATSAPP_PHONE": "+1", "TWILIO_ACCOUNT_SID": "AC",
           "TWILIO_AUTH_TOKEN": "t", "TWILIO_WHATSAPP_FROM": "+2"}
    assert isinstance(alerts.whatsapp_from_env(env), alerts.WhatsAppTwilio)
    assert isinstance(alerts.whatsapp_from_env({}), alerts.WhatsAppCallMeBot)


def test_hub_levels_dedup_and_failure_isolation():
    tele, ok, ko = FakeTelegram(), Recorder(), Recorder(fail=True)
    ko.name = "ko"
    hub = alerts.AlertHub(tele, [ko, ok], level="critical", async_mode=False)
    hub("Résumé quotidien")                                  # non critique : Telegram seulement
    assert ok.got == [] and tele.calls == [("Résumé quotidien", False)]
    assert hub("🛑 Arrêt d'urgence", critical=True) is True  # un canal en panne ne bloque rien
    assert ok.got and ok.got[0][0].startswith("🛑")
    hub("🛑 Arrêt d'urgence", critical=True)                 # doublon : ignoré
    assert len(ok.got) == 1
    all_hub = alerts.AlertHub(FakeTelegram(), [ok], level="all", async_mode=False)
    all_hub("Résumé quotidien")
    assert len(ok.got) == 2
    assert {c["name"] for c in hub.status()} == {"telegram", "ko", "rec"}


def test_hub_test_messages_and_disabled_channels():
    hub = alerts.AlertHub(FakeTelegram(enabled=False), [Recorder(fail=True)], async_mode=False)
    assert hub.test("telegram")[0] is False
    ok, err = hub.test("rec")
    assert ok is False and "injoignable" in err
    assert hub.test("email")[0] is False
    hub.close()


def test_build_notifier_from_env_keeps_secrets_out_of_errors():
    env = {"SMTP_HOST": "smtp.example.com", "ALERT_EMAIL_TO": "a@b.c", "SMTP_PASSWORD": "motdepasse-secret",
           "WHATSAPP_PHONE": "+225", "CALLMEBOT_APIKEY": "cle-secrete-123"}
    hub = alerts.build_notifier(env=env)
    try:
        assert {c["name"] for c in hub.status()} == {"telegram", "email", "whatsapp"}
        ch = hub.channels[1]
        ch._get = lambda url: (_ for _ in ()).throw(RuntimeError(f"échec pour {url}"))
        ok, err = hub.test("whatsapp")
        assert not ok and "cle-secrete-123" not in err
    finally:
        hub.close()


def test_configure_writes_env_without_echoing_secrets(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("RUN_MODE=paper\n", encoding="utf-8")
    answers = iter(["moi@example.com", "", "", "", "+2250700000000", "1", "2"])
    secrets = iter(["mdpapplicationxy", "cle-callmebot"])
    out = io.StringIO()
    for k in ("ALERT_EMAIL_TO", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "WHATSAPP_PHONE",
              "WHATSAPP_PROVIDER", "CALLMEBOT_APIKEY", "ALERT_LEVEL"):
        monkeypatch.delenv(k, raising=False)
    assert alerts.cmd_configure(str(env), read=lambda _p: next(answers), secret=lambda _p: next(secrets), out=out) == 0
    text = env.read_text(encoding="utf-8")
    for line in ("ALERT_EMAIL_TO=moi@example.com", "SMTP_HOST=smtp.gmail.com", "SMTP_PORT=587",
                 "SMTP_PASSWORD=mdpapplicationxy", "WHATSAPP_PROVIDER=callmebot",
                 "CALLMEBOT_APIKEY=cle-callmebot", "ALERT_LEVEL=all", "RUN_MODE=paper"):
        assert line in text, line
    assert "mdpapplicationxy" not in out.getvalue() and "cle-callmebot" not in out.getvalue()


def test_configure_refuses_a_receiving_server_and_explains_gmail(tmp_path, monkeypatch):
    """« pop3 » (serveur de réception) est refusé ; pour Gmail, le mot de
    passe d'application est expliqué avant la saisie."""
    env = tmp_path / ".env"
    env.write_text("RUN_MODE=paper\n", encoding="utf-8")
    answers = iter(["moi@gmail.com", "pop3", "", "", "", "", "1"])
    secrets = iter(["MonMotDePasse2026", "abcd efgh ijkl mnop"])   # habituel refusé, puis le bon
    out = io.StringIO()
    for k in ("ALERT_EMAIL_TO", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "ALERT_LEVEL"):
        monkeypatch.delenv(k, raising=False)
    assert alerts.cmd_configure(str(env), read=lambda _p: next(answers), secret=lambda _p: next(secrets), out=out) == 0
    saved = env.read_text(encoding="utf-8")
    assert "SMTP_HOST=smtp.gmail.com" in saved and "SMTP_PASSWORD=abcdefghijklmnop" in saved
    text = out.getvalue()
    assert "« pop3 » n'est pas un serveur d'envoi" in text and "apppasswords" in text
    assert "Ce n'est pas un mot de passe d'application Gmail" in text


def test_send_errors_are_explained_in_plain_french():
    import smtplib
    import socket
    auth = alerts.explain_send_error(smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted"))
    assert "mot de passe d'application" in auth and "apppasswords" in auth
    dns = alerts.explain_send_error(socket.gaierror(11001, "getaddrinfo failed"), "pop3")
    assert "« pop3 » introuvable" in dns and "smtp.gmail.com" in dns
    assert "port" in alerts.explain_send_error(smtplib.SMTPServerDisconnected("closed"))
    assert "apppasswords" in alerts.explain_send_error(smtplib.SMTPServerDisconnected("closed"),
                                                       "smtp.gmail.com")


# ---------- Pause après trois refus du mot de passe ----------

class RefusingSMTP(FakeSMTP):
    """Serveur qui refuse le mot de passe, ou qui est injoignable."""
    tries, error = 0, None

    def login(self, user, password):
        RefusingSMTP.tries += 1
        if RefusingSMTP.error is not None:
            raise RefusingSMTP.error


def _paused_hub(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SMTP_PASSWORD=x\n", encoding="utf-8")
    os.utime(env, (1_000, 1_000))
    clock = {"now": 10_000.0}
    pause = alerts.Pause(str(tmp_path / "tg.alertes.json"), str(env), clock=lambda: clock["now"])
    ch = alerts.EmailChannel("smtp.gmail.com", 587, "moi@example.com", "mdp", "moi@example.com",
                             smtp_factory=RefusingSMTP)
    hub = alerts.AlertHub(FakeTelegram(enabled=False), [ch], level="all", async_mode=False,
                          dedup_sec=0, pause=pause)
    RefusingSMTP.tries, RefusingSMTP.error = 0, smtplib.SMTPAuthenticationError(535, b"refuse")
    return hub, clock, env


def test_email_pauses_after_three_refused_passwords(tmp_path):
    """Chaque essai raté est une connexion refusée sur le compte de
    messagerie : après trois, un seul essai par jour."""
    hub, clock, _env = _paused_hub(tmp_path)
    for k in range(5):
        clock["now"] += 60
        hub(f"alerte {k}", critical=True)
    assert RefusingSMTP.tries == 3                                  # les deux suivantes : aucun essai
    assert hub.last["email"]["ok"] is False and "en pause après 3 refus" in hub.last["email"]["error"]
    assert "alerts configurer" in hub.last["email"]["error"]
    res = hub.send_report("Rapport", "complet", "court")
    assert RefusingSMTP.tries == 3 and "en pause" in res["email"]   # le rapport non plus
    clock["now"] += alerts.PAUSE_RETRY_SEC                          # le lendemain : un essai, un seul
    hub("alerte du lendemain", critical=True)
    hub("encore une", critical=True)
    assert RefusingSMTP.tries == 4


def test_paused_email_resumes_with_a_new_password_or_a_success(tmp_path):
    hub, clock, env = _paused_hub(tmp_path)
    for k in range(3):
        hub(f"alerte {k}", critical=True)
    assert hub.pause.held("email")
    os.utime(env, (clock["now"] + 5, clock["now"] + 5))             # nouveau mot de passe enregistré
    clock["now"] += 10
    assert hub.pause.held("email") is None
    RefusingSMTP.error = None                                        # il est accepté
    hub("reprise", critical=True)
    assert hub.last["email"]["ok"] is True and not os.path.exists(hub.pause.path)
    assert hub.pause.held("email") is None


def test_pause_ignores_network_failures_and_a_requested_test_always_goes(tmp_path):
    hub, clock, _env = _paused_hub(tmp_path)
    RefusingSMTP.error = OSError("réseau coupé")                     # pas un refus du mot de passe
    for k in range(5):
        hub(f"alerte {k}", critical=True)
    assert RefusingSMTP.tries == 5 and hub.pause.held("email") is None
    RefusingSMTP.error = smtplib.SMTPServerDisconnected("fermé")     # Gmail, après plusieurs refus
    for k in range(3):
        hub(f"refus {k}", critical=True)
    assert hub.pause.held("email") and RefusingSMTP.tries == 8
    ok, err = hub.test("email")                                     # test demandé : envoyé malgré la pause
    assert RefusingSMTP.tries == 9 and not ok and "coupé la connexion" in err
    RefusingSMTP.error = None
    assert hub.test("email") == (True, "") and hub.pause.held("email") is None


def test_pause_file_is_the_bot_s_only_with_the_real_environment(tmp_path):
    assert alerts.build_notifier(env={}).pause is None              # environnement fourni : aucune pause
    hub = alerts.build_notifier(env={}, pause_file=str(tmp_path / "p.json"))
    assert hub.pause.path.endswith("p.json") and hub.pause.env_file.endswith(".env")
    assert alerts.PAUSE_FILE.endswith("trendguard.alertes.json")
