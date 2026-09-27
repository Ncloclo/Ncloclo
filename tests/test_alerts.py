"""Alertes : e-mail et WhatsApp à côté de Telegram, selon le niveau,
sans jamais bloquer ni faire échouer le bot."""

import io

import alerts


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
    secrets = iter(["mdp-application", "cle-callmebot"])
    out = io.StringIO()
    for k in ("ALERT_EMAIL_TO", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "WHATSAPP_PHONE",
              "WHATSAPP_PROVIDER", "CALLMEBOT_APIKEY", "ALERT_LEVEL"):
        monkeypatch.delenv(k, raising=False)
    assert alerts.cmd_configure(str(env), read=lambda _p: next(answers), secret=lambda _p: next(secrets), out=out) == 0
    text = env.read_text(encoding="utf-8")
    for line in ("ALERT_EMAIL_TO=moi@example.com", "SMTP_HOST=smtp.gmail.com", "SMTP_PORT=587",
                 "SMTP_PASSWORD=mdp-application", "WHATSAPP_PROVIDER=callmebot",
                 "CALLMEBOT_APIKEY=cle-callmebot", "ALERT_LEVEL=all", "RUN_MODE=paper"):
        assert line in text, line
    assert "mdp-application" not in out.getvalue() and "cle-callmebot" not in out.getvalue()
