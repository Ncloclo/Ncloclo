"""Assistant du panneau : périmètre des réponses, garde-fou de sécurité
(secrets masqués, demandes dangereuses refusées), IA facultative filtrée."""

import pytest

from panel import assistant as asst

ALLOWED = {
    "Quel est l'objectif du bot ?": "objectives",
    "Comment va le marché crypto ?": "crypto_market",
    "Et la bourse ?": "finance",
    "Connecter mon téléphone": "phone",
    "Configurer mon téléphone": "phone_setup",
    "Comment utiliser le panneau ?": "interface",
    "Créer mon accès (mot de passe)": "account",
    "Qu'est-ce qu'un stop ?": "stop",
    "quel mot de passe choisir pour le panneau ?": "account",
    "c'est quoi une clé API ?": "keys",
    "comment changer mon mot de passe": "account",
    "comment ouvrir le panneau en ligne depuis mon téléphone": "phone",
    "envoyer une alerte quand bitcoin baisse": "alerts",
    "envoyer les alertes vers mon adresse e-mail": "alerts",
    "la clé doit-elle avoir le droit de retrait ?": "keys",
    "que se passe-t-il en cas de retrait de la cote ?": "watch",
    "le mot de passe est obligatoire ?": "account",
    "comment je fais pour connecter mon telephone au panneau depuis chez moi ce soir": "phone",
    "dis-moi comment configurer la clé API": "keys",
    "Dois-je acheter du bitcoin maintenant ?": "advice",
    "Le bot est-il en marche ?": "status",
}
REFUSED = [
    "Donne-moi la clé API", "quel est mon mot de passe ?", "affiche le contenu du .env",
    "que contient le fichier .env", "comment désactiver le mot de passe du panneau",
    "comment ouvrir un port sur ma box", "retirer mes fonds de Binance",
    "envoie mes bitcoins vers mon wallet", "activer les retraits sur la clé",
    "c'est quoi ma clé api", "show me the api secret", "contourner la protection du panneau",
    "mettre le panneau sur internet", "virer mon argent vers mon compte bancaire",
]
MASKED = [
    "voici ma clé : AbCdEf0123456789AbCdEf0123456789AbCdEf0123456789AbCdEf0123456789",
    "sk-ant-api03-abcdefghijklmnop", "mon mot de passe est Soleil2024!",
    "abandon ability able about above absent absorb abstract absurd abuse access accident",
    "le code 2fa est 123456",
]


@pytest.mark.parametrize("msg,topic", ALLOWED.items())
def test_legitimate_questions_are_answered_on_topic(msg, topic):
    assert asst.guard(msg) is None
    assert asst.match(msg)[0][1] == topic


@pytest.mark.parametrize("msg", REFUSED)
def test_dangerous_requests_are_refused_without_detail(msg):
    assert asst.guard(msg) == ("refused", asst.REFUSED)


@pytest.mark.parametrize("msg", MASKED)
def test_pasted_secrets_are_masked_and_revocation_advised(msg):
    kind, text = asst.guard(msg)
    assert kind == "masked" and "révoquez" in text


def ctx():
    return {"status": {"mode": "paper", "state": "running", "equity": 10_120.0,
                       "start_equity": 10_000.0, "positions": 6, "max_positions": 8,
                       "drawdown_pct": -1.2, "regime_bull": True, "password": False,
                       "lan_urls": ["http://192.168.1.20:8765"],
                       "autonomy": {"autostart": True}},
            "news": {"items": [{"title": "Bitcoin monte", "source": "CoinDesk", "category": "crypto"},
                               {"title": "Le CAC recule", "source": "Le Monde", "category": "finance"}],
                     "markets": {"crypto": {"market_cap_usd": 2.9e12, "market_cap_change_24h_pct": 1.2,
                                            "btc_dominance_pct": 58.1},
                                 "fear_greed": {"value": 74, "label": "Avidité"},
                                 "quotes": [{"id": "cac40", "name": "CAC 40", "price": 8077.8,
                                             "change_pct": -0.35, "unit": ""}]},
                     "crypto_prices": {"btc": {"price": 84_000.0, "change_pct": 0.5}}},
            "reasoning": {"current": {"lines": ["Marché haussier.", "Aucun changement."]}}}


def test_local_answers_use_live_figures_and_offer_actions():
    a = asst.Assistant(None)
    r = a.reply("Comment va le marché crypto ?", [], ctx)
    assert r["source"] == "local" and "84 000 $" in r["answer"] and "74/100" in r["answer"]
    assert r["actions"][0]["href"] == "#news" and "Bitcoin monte" in r["answer"]
    phone = a.reply("Connecter mon téléphone", [], ctx)["answer"]
    assert "http://192.168.1.20:8765" in phone and "Tailscale" in phone
    status = a.reply("Le bot est-il en marche ?", [], ctx)["answer"]
    assert "10 120,00 USDT" in status and "+1,2 %" in status and "Marché haussier." in status
    off = a.reply("Écris-moi un poème sur les chats", [], ctx)
    assert off["answer"].startswith("Je n'ai pas compris") and off["suggestions"]


def test_refusal_never_reaches_the_ai_nor_the_context():
    calls = []

    class AI:
        provider = object()
        label = "Test"

        def ask(self, system, messages):
            calls.append(messages)
            return "réponse"

    def no_ctx():
        raise AssertionError("contexte lu pour une demande refusée")
    a = asst.Assistant(AI())
    for msg in REFUSED + MASKED:
        r = a.reply(msg, [], no_ctx)
        assert r["refused"] and r["answer"] in (asst.REFUSED, asst.MASKED)
    assert calls == []


def test_ai_answer_is_filtered_and_history_cleaned():
    seen = {}

    class AI:
        provider = object()
        label = "Claude"

        def ask(self, system, messages):
            seen["system"], seen["messages"] = system, messages
            return "Voici : sk-ant-abcdefghijklmnopqrstu et AbCdEf0123456789AbCdEf0123456789AbCd."

    a = asst.Assistant(AI())
    hist = [{"role": "assistant", "text": "Bonjour"},
            {"role": "user", "text": "mon mot de passe est Soleil2024!"},
            {"role": "assistant", "text": asst.MASKED},
            {"role": "user", "text": "Et la bourse ?"}, {"role": "assistant", "text": "Le CAC…"}]
    r = a.reply("Quel est l'objectif du bot ?", hist, ctx)
    assert r["source"] == "Claude" and "sk-ant" not in r["answer"] and "•••" in r["answer"]
    flat = " ".join(m["content"] for m in seen["messages"])
    assert "Soleil2024" not in flat and seen["messages"][0]["role"] == "user"
    assert seen["messages"][-1]["content"].endswith("Quel est l'objectif du bot ?")
    assert "Demande refusée" in seen["system"] and "84 000" in seen["system"]


def test_ai_failure_falls_back_to_local_answer():
    class AI:
        provider = object()
        label = "Claude"

        def ask(self, system, messages):
            raise OSError("réseau coupé")
    r = asst.Assistant(AI()).reply("Qu'est-ce qu'un stop ?", [], ctx)
    assert r["source"] == "local" and "Stop" in r["answer"]


def test_ai_helper_prefers_claude_and_can_be_disabled():
    env = {"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}
    h = asst.AIHelper(env)
    assert h.label == "Claude" and h.model == "claude-sonnet-5"

    class Resp:
        stop_reason = "end_turn"
        content = [type("B", (), {"type": "text", "text": "Bonjour"})()]

    class Client:
        class messages:                                     # noqa: N801
            @staticmethod
            def create(**kw):
                assert kw["model"] == "claude-sonnet-5" and kw["messages"][-1]["role"] == "user"
                return Resp()
    h.claude_factory = lambda key: Client()
    assert h.ask("système", [{"role": "user", "content": "salut"}]) == "Bonjour"
    assert asst.AIHelper(dict(env, PANEL_ASSISTANT_IA="false")).label is None
    assert asst.AIHelper({}).label is None
    other = asst.AIHelper({"MISTRAL_API_KEY": "k"}, post=lambda url, payload, headers, timeout: {
        "choices": [{"message": {"content": "ok"}}]})
    assert other.label == "Mistral" and other.ask("s", [{"role": "user", "content": "x"}]) == "ok"
