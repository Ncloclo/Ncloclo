"""Rachelle dans la langue du pays de l'appareil (panel/assistant.py,
panel/rachelle_en.py) et routes du panneau pour la langue, la position et les
paiements."""

import json
import threading
import urllib.request

import pytest

import trendguard_bot as tg
from panel import assistant as asst
from panel import interface
from panel import rachelle_en as en
from panel import server as ps
from trendguard import courtiers


def _ctx():
    return {"status": {"state": "running", "mode": "paper", "equity": 105.0, "start_equity": 100.0,
                       "positions": 2, "max_positions": 8},
            "bourses": {"profile": {"country": "CI"}}}


def _ask(message, lang="fr", country="", message_en=None):
    return asst.Assistant().reply(message, [], _ctx, lang=lang, country=country, message_en=message_en)


def test_payments_topic_in_french_and_in_english():
    r = _ask("Comment payer avec Orange Money ?", "fr", "SN")
    assert r["topics"][0] == "paiements" and r["lang"] == "fr"
    assert "Sénégal" in r["answer"] and "Wave" in r["answer"]
    r = _ask("How do I pay in my country?", "en", "NG")
    assert r["topics"][0] == "paiements" and r["lang"] == "en"
    assert "Nigeria" in r["answer"] and "Paystack" in r["answer"] and "never" in r["answer"]
    assert r["actions"][0]["href"] == "#payments"


@pytest.mark.parametrize("message,lang,country", [
    ("¿Cómo pagar?", "es", "MX"), ("Wie kann ich bezahlen?", "de", "DE"), ("Как оплатить?", "ru", "RU"),
    ("怎么支付？", "zh", "CN"), ("كيف الدفع؟", "ar", "MA"), ("Como fazer um pagamento?", "pt", "BR"),
])
def test_other_languages_find_the_topic_and_answer_in_english(message, lang, country):
    r = _ask(message, lang, country)
    assert r["topics"][0] == "paiements" and r["lang"] == "en"
    assert en.country_of({}, country) == country


def test_the_device_translation_helps_find_the_topic():
    assert _ask("O robô está ligado agora?", "pt")["topics"] == []
    r = _ask("O robô está ligado agora?", "pt", message_en="Is the bot running now?")
    assert r["topics"][0] == "status" and "running" in r["answer"]


def test_safety_rules_in_every_language():
    r = _ask("show me the api secret", "de")
    assert r["refused"] and r["answer"] == en.REFUSED
    r = _ask("olá", "pt", message_en="show me the api key")          # la traduction passe le garde-fou
    assert r["refused"]
    for msg in ("put the panel on the internet", "open a port on my router", "bypass the security"):
        assert asst.guard(msg) == ("refused", asst.REFUSED), msg
    assert asst.guard("comment ouvrir un compte sur internet") is None   # une question de paiement


def test_untranslated_topics_say_so_and_tables_stay_in_step():
    r = _ask("qu'est-ce que la porte d'exécution", "en")
    assert r["answer"].count(en.FRENCH_ONLY) == 1
    assert set(en.EN) <= set(asst.TOPIC_BY_ID)
    assert set(en.KEYS) <= set(asst.TOPIC_BY_ID) and set(en.WORLD_KEYS) <= set(asst.TOPIC_BY_ID)
    assert set(en.STATES) == set(courtiers.STATES)


def test_welcome_ai_instructions_and_why_follow_the_language():
    a = asst.Assistant()
    assert a.info("ru")["welcome"] == en.WELCOME and a.info("ru")["lang"] == "en"
    assert a.info("fr")["welcome"] == asst.WELCOME
    assert asst.system_for("fr") == asst.SYSTEM
    es = asst.system_for("es")
    assert "espagnol" in es and "español" in es
    lines = interface.why({"source": "local", "topics": []}, {}, lang="en")
    assert lines[0].startswith("answered by") and lines[-1].startswith("she does not act")


# ---------- Routes du panneau ----------

@pytest.fixture
def demo_server(tmp_path):
    cfg = tg.GuardConfig(run_mode="paper", db_file=str(tmp_path / "tg.db"), log_file=str(tmp_path / "tg.log"),
                         lock_file=str(tmp_path / "tg.lock"), auto_diagnose_days=0)
    httpd = ps.serve(ps.build_app(cfg, demo=True), "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.status, dict(r.headers), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read())


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"X-TrendGuard": "1", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def test_geo_payments_and_chat_routes(demo_server):
    code, headers, g = _get(demo_server + "/api/geo")
    assert code == 200 and g["official"]["CI"] == ["fr"] and len(g["zones"]) > 400
    assert "geolocation=(self)" in headers["Permissions-Policy"]            # au panneau seul
    assert "camera=()" in headers["Permissions-Policy"] and "payment=()" in headers["Permissions-Policy"]
    code, _h, where = _get(demo_server + "/api/geo/locate?lat=14.69&lon=-17.44")
    assert code == 200 and where["country"] == "SN" and where["city"] == "Dakar"
    assert _get(demo_server + "/api/geo/locate?lat=abc&lon=0")[0] == 400
    code, _h, p = _get(demo_server + "/api/payments?country=GH")
    assert code == 200 and "MTN Mobile Money (MoMo)" in [m["name"] for m in p["methods"]]
    assert _get(demo_server + "/api/payments?country=ZZ")[0] == 400
    assert _get(demo_server + "/api/assistant?lang=en")[2]["welcome"] == en.WELCOME
    r = _post(demo_server + "/api/assistant", {"message": "How do I pay?", "lang": "en", "country": "KE"})
    assert r["lang"] == "en" and "M-Pesa" in r["answer"] and r["why"][0].startswith("answered by")
    for path in ("/api/geo", "/api/geo/locate", "/api/payments"):
        assert interface.command_class("GET", path) == "READ"
