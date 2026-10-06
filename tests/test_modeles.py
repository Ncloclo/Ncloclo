"""Prompt maître, étape 6 (docs/MODELES.md) : socle multi-modèles d'IA
(registre sans modèle inventé, confidentialité, disjoncteur, budget de
jetons, repli tracé, versions des invites, désaccord entre IA, banc
d'évaluation) ; une IA n'a aucun chemin vers un ordre."""

import logging
import pathlib
import sqlite3
import time

import pytest
from test_assistant import ctx
from test_market_watch import NOW, UNIVERSE, _answer, _fetch_bytes, _fetch_json_quiet, _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import market_watch as mw
from trendguard import modeles

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEY = "k" * 20


def _ok(text="ok", tokens=(10, 5)):
    return lambda m: (text, tokens)


# ---------- Registre, confidentialité ----------

def test_the_registry_invents_nothing_and_never_holds_a_key():
    models = modeles.registry({})
    assert len(models) == len(mw.PROVIDERS) and not any(m.configured for m in models)
    env = {"OPENAI_API_KEY": "sk-secret-" + KEY, "VEILLE_MODEL_OPENAI": "gpt-x",
           "TG_LLM_LOCAL_URL": "http://127.0.0.1:11434/v1/chat/completions"}
    by = {m.provider: m for m in modeles.registry(env)}
    assert by["openai"].configured and by["openai"].model == "gpt-x" and not by["claude"].configured
    assert by["local"].kind == "LOCAL" and by["local"].model == modeles.LOCAL_MODEL
    assert "sk-secret" not in repr(list(by.values())) and "sk-secret" not in repr(modeles.status(env, "absent.db"))
    assert modeles.describe(modeles.status({}, "absent.db")).startswith("aucune IA configurée (8 fournisseurs")
    for url in ("http://localhost:11434/v1/chat/completions", "http://192.168.1.20:8000/v1/chat/completions",
                "http://[::1]:11434/v1/chat/completions"):
        assert modeles.local_provider({"TG_LLM_LOCAL_URL": url}) is not None, url
    for url in ("https://api.example.com/v1/chat/completions", f"http://{'.'.join(['8'] * 4)}/v1", "file:///etc/passwd"):
        assert modeles.local_provider({"TG_LLM_LOCAL_URL": url}) is None, url    # « local » vers Internet : refusé


def test_a_secret_never_leaves_the_pc():
    assert modeles.privacy_class("Que fait le bot ?") == "PUBLIC"
    assert modeles.privacy_class("Que fait le bot ?", "INTERNAL") == "INTERNAL"
    for text in ("ma clé sk-" + "A1" * 12, "mot de passe : hunter2024", "x" * 20 + "1234567890abcdef"):
        assert modeles.privacy_class(text, "INTERNAL") == "LOCAL_ONLY", text
    led = modeles.Ledger("")
    env = {"MISTRAL_API_KEY": KEY, "TG_LLM_LOCAL_URL": "http://127.0.0.1:11434/v1/chat/completions"}
    r = modeles.route(modeles.registry(env), "LOCAL_ONLY", led, 1000)
    assert r.selected == ["local"] and "fournisseur extérieur interdit" in r.refused["mistral"]
    assert modeles.execute("essai", ("p", "x"), "sk-" + "A1" * 12, _ok(), env={"MISTRAL_API_KEY": KEY},
                           ledger=led) is None                     # sans modèle local : aucun appel


# ---------- Trace, santé, disjoncteur, budget ----------

def test_breaker_opens_after_three_failures_and_closes_on_success():
    led = modeles.Ledger("")
    assert led.health("openai") == {"calls": 0, "failures": 0, "breaker": "CLOSED", "p50_ms": None, "p95_ms": None}
    t = time.time()
    led.record("x", "openai", "m", ("p", "v"), "PUBLIC", True, 800, at=t - 60)
    for _ in range(modeles.BREAKER_FAILS):
        led.record("x", "openai", "m", ("p", "v"), "PUBLIC", False, 100, "délai dépassé", at=t)
    h = led.health("openai", now=t + 1)
    assert h["breaker"] == "OPEN" and h["failures"] == 3 and h["p50_ms"] == 800    # durée des réussites seulement
    assert led.health("openai", now=t + modeles.BREAKER_COOLDOWN + 1)["breaker"] == "HALF_OPEN"
    r = modeles.route(modeles.registry({"OPENAI_API_KEY": KEY}), "PUBLIC", led, 1000)
    assert r.selected == [] and "disjoncteur" in r.refused["openai"]
    led.record("x", "openai", "m", ("p", "v"), "PUBLIC", True, 900)
    assert led.health("openai")["breaker"] == "CLOSED"
    with pytest.raises(sqlite3.IntegrityError):
        led.conn.execute("DELETE FROM llm_executions")                # trace en ajout seulement


def test_daily_token_budget_stops_cloud_calls():
    led = modeles.Ledger("")
    led.record("x", "mistral", "m", ("p", "v"), "PUBLIC", True, 100, tokens=(80, 30))
    env = {"MISTRAL_API_KEY": KEY, "TG_LLM_JETONS_JOUR": "100"}
    assert modeles.execute("x", ("p", "v"), "bonjour", _ok(), env=env, ledger=led) is None
    assert modeles.route(modeles.registry(env), "PUBLIC", led, 0).refused["mistral"].startswith("budget")


def test_fallback_is_traced_and_versions_recorded():
    led = modeles.Ledger("")
    env = {"OPENAI_API_KEY": KEY, "MISTRAL_API_KEY": KEY}

    def call(m):
        if m.provider == "openai":
            raise mw.HttpError(429, "slow down")
        return "réponse de secours", (12, 7)
    ex = modeles.execute("rachelle", ("rachelle", "invite"), "bonjour", call, env=env, ledger=led)
    assert ex.provider == "mistral" and ex.text == "réponse de secours" and ex.tokens == (12, 7)
    assert ex.attempts[0] == {"provider": "openai", "error": "quota ou limite de débit atteints", "retryable": True}
    rows = list(led.conn.execute("SELECT provider, ok, error, fallback_from, prompt_id, prompt_version, "
                                 "input_tokens FROM llm_executions ORDER BY id"))
    v = modeles.prompt_version("invite")
    assert rows == [("openai", 0, "quota ou limite de débit atteints", None, "rachelle", v, None),
                    ("mistral", 1, None, "openai", "rachelle", v, 12)]
    assert led.tokens_since(0) == 19
    last = led.last(2)
    assert last[0]["reason"] == "rang 2 sur 2 : jamais appelé (note neutre, rien d'inventé) ; repli après l'échec de openai"
    assert last[1]["reason"].startswith("rang 1 sur 2") and last[0]["input_hash"] == modeles.prompt_version("bonjour")
    assert "bonjour" not in repr(last)                                    # l'empreinte, jamais le contenu
    assert modeles.execute("x", ("p", "v"), "bonjour", lambda m: ("  ", (None, None)), env=env, ledger=led) is None


def test_prompt_versions_are_pinned():
    """Une invite changée change de version : mettre à jour ce test le dit."""
    assert modeles.prompt_version(mw.SYSTEM) == "e131eba32e6a"
    assert modeles.prompt_version(asst.SYSTEM) == "9f29d5a69fae"
    assert modeles.prompt_version(modeles.BENCH_SYSTEM) == "cdc1d43d2894"


# ---------- Adaptateurs ----------

def test_chat_adapters_report_tokens_and_refusals():
    p = mw.PROVIDER_BY_NAME["mistral"]
    seen = {}

    def post(url, payload, headers, timeout):
        seen.update(url=url, headers=headers, roles=[m["role"] for m in payload["messages"]])
        return {"choices": [{"message": {"content": "salut"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}
    assert modeles.chat_call(p, KEY, "m", "sys", [{"role": "user", "content": "x"}], post=post) == ("salut", (9, 2))
    assert seen["roles"] == ["system", "user"] and seen["headers"] == {"Authorization": f"Bearer {KEY}"}
    local = modeles.local_provider({"TG_LLM_LOCAL_URL": "http://127.0.0.1:11434/v1/chat/completions"})
    modeles.chat_call(local, "", "llama", "sys", [{"role": "user", "content": "x"}], post=post)
    assert seen["headers"] == {}                                          # modèle local : aucune clé

    class Resp:
        stop_reason = "refusal"
        content = []
        usage = type("U", (), {"input_tokens": 4, "output_tokens": 0})()

    class Client:
        class messages:                                                   # noqa: N801
            @staticmethod
            def create(**kw):
                return Resp()
    claude = mw.PROVIDER_BY_NAME["claude"]
    assert modeles.chat_call(claude, KEY, "c", "s", [], claude_factory=lambda k: Client(), refusal="NON") == ("NON", (4, 0))


# ---------- Rachelle et la veille passent par le socle ----------

def test_rachelle_falls_back_then_answers_alone():
    class Down:
        class messages:                                                   # noqa: N801
            @staticmethod
            def create(**kw):
                raise mw.HttpError(503, "surchargé")
    env = {"ANTHROPIC_API_KEY": KEY, "MISTRAL_API_KEY": KEY}
    ai = asst.AIHelper(env, post=lambda url, payload, headers, timeout: {"choices": [{"message": {"content": "Mistral répond"}}]},
                       claude_factory=lambda k: Down())
    r = asst.Assistant(ai).reply("Qu'est-ce qu'un stop ?", [], ctx)
    assert r["answer"] == "Mistral répond" and r["source"] == "Mistral"   # repli sur la suivante

    def broken(url, payload, headers, timeout):
        raise mw.HttpError(500, "panne")
    alone = asst.Assistant(asst.AIHelper(env, post=broken, claude_factory=lambda k: Down()))
    r = alone.reply("Qu'est-ce qu'un stop ?", [], ctx)
    assert r["source"] == "local" and "Stop" in r["answer"]               # aucune IA : réponse intégrée
    rows = modeles.status({}, "absent.db")
    assert "aucune IA configurée" in asst.local_answer("Quelles IA utilise le bot ?", {"modeles": rows})["answer"]


def test_the_watch_skips_a_tripped_ai_and_traces_the_others(tmp_path):
    path = str(tmp_path / "trendguard_modeles.db")
    led = modeles.Ledger(path)
    for _ in range(modeles.BREAKER_FAILS):
        led.record("veille", "openai", "m", ("veille", "v"), "PUBLIC", False, 100, "délai dépassé")
    led.close()
    assert modeles.ledger_path(str(tmp_path / "veille.db")) == path

    def call(p, key, model, system, prompt):
        assert p.name != "openai"                                         # disjoncteur ouvert : pas appelée
        return _answer([], views={"aave": 0.6 if p.name == "deepseek" else -0.6}), set()
    memory = mw.WatchMemory(str(tmp_path / "veille.db"))
    trace = modeles.WatchTrace(path)
    try:
        env = {"OPENAI_API_KEY": KEY, "DEEPSEEK_API_KEY": KEY, "MISTRAL_API_KEY": KEY}
        rep = mw.daily_report(UNIVERSE, [], NOW, memory, env=env, fetch_json=_fetch_json_quiet,
                              fetch_bytes=_fetch_bytes, call=call, trace=trace)
    finally:
        trace.close()
        memory.close()
    assert "disjoncteur" in rep["providers"]["openai"]["error"] and rep["consensus"]["providers"] == 2
    assert rep["consensus"]["disagreements"] == {"aave": [-0.6, 0.6]}
    assert "aave" not in rep["consensus"]["views"]                       # pas de moyenne trompeuse (0)
    assert "IA en désaccord" in mw.render(rep) and "AAVE de −0,6 à +0,6" in mw.render(rep)
    by = {r["provider"]: r for r in modeles.status(env, path)}
    assert by["deepseek"]["calls"] == 1 and by["mistral"]["calls"] == 1 and by["openai"]["breaker"] == "OPEN"
    led = modeles.Ledger(path, readonly=True)
    try:
        ids = {r[0] for r in led.conn.execute("SELECT prompt_version FROM llm_executions WHERE provider='deepseek'")}
    finally:
        led.close()
    assert ids == {modeles.prompt_version(mw.SYSTEM)}


def test_close_views_are_not_a_disagreement():
    ok = {n: {"sentiment": s, "views": {"aave": s}} for n, s in (("a", 0.1), ("b", 0.4))}
    assert mw.disagreements(ok) == {}
    ok["c"] = {"sentiment": -0.7, "views": {}}
    assert mw.disagreements(ok) == {"market": [-0.7, 0.4]}


# ---------- Banc d'évaluation ----------

def test_benchmark_measures_configured_models_only(tmp_path, monkeypatch, capsys):
    assert modeles.benchmark(lambda m, s, q: ("1", None), env={}) == []      # rien à mesurer : rien d'inventé
    answers = {"pourcentage": "1 %", "combien de jours": "30 jours", "découvert": "Non.", "17": "391"}

    def good(m, system, q):
        return next(a for k, a in answers.items() if k in q), (None, None)
    rows = modeles.benchmark(good, env={"MISTRAL_API_KEY": KEY})
    assert [(r["provider"], r["accuracy"]) for r in rows] == [("mistral", 1.0)]
    worse = [dict(rows[0], accuracy=0.5)]
    assert modeles.regression(rows, worse) == ["mistral (mistral-medium-latest) : 100 % → 50 %"]
    assert modeles.regression(rows, rows) == []
    for p in mw.PROVIDERS:
        monkeypatch.delenv(p.key_env, raising=False)
    monkeypatch.delenv("TG_LLM_LOCAL_URL", raising=False)
    monkeypatch.setenv("TG_VEILLE_DB", str(tmp_path / "veille.db"))
    assert modeles.main(["banc"]) == 0 and "rien à évaluer" in capsys.readouterr().out
    assert modeles.main([]) == 0 and "non configuré" in capsys.readouterr().out
    assert not (tmp_path / "trendguard_modeles.db").exists()


def test_no_model_can_reach_an_order():
    text = (ROOT / "trendguard" / "modeles.py").read_text(encoding="utf-8")
    for forbidden in ("enter_planned", "market_buy", "porte.", "_execute_entry", "import porte",
                      "record_order", "create_order"):
        assert forbidden not in text, forbidden


def test_unanimous_ais_change_no_trade(tmp_path, monkeypatch):
    """Toutes les IA d'accord, très positives ou très négatives : mêmes
    achats et mêmes ventes. Un avis d'IA n'est ni un signal ni une
    autorisation ; la règle du bot et la porte d'exécution décident."""
    close, volume = synthetic_market()
    logger = logging.getLogger("test.modeles")
    monkeypatch.setattr(mw, "refresh_official", lambda u, now, memory, **k: ({}, []))
    runs = []
    for mood in (1.0, -1.0):
        monkeypatch.setattr(mw, "daily_report", lambda *a, mood=mood, **k: {
            "day": "x", "providers": {"claude": {"ok": True}, "openai": {"ok": True}}, "alerts": [],
            "consensus": {"sentiment": mood, "providers": 2, "events": [], "summary": "s", "disagreements": {},
                          "views": {a.lower(): mood for a in ("BTC", "ETH", "SOL", "AAVE", "ADA")}},
            "vetoes": {}, "monitoring": [], "indicators": {}, "items": 0, "errors": [], "weights": {}})
        monkeypatch.setattr(mw, "render", lambda r: "rapport")
        bot, fb = make_bot("paper", close, logger, watch=True, watch_ai=True,
                           watch_db=str(tmp_path / f"veille{mood}.db"))
        assert bot.boot()
        _run(bot, fb, close, volume, SIM_FROM, SIM_FROM + 120)
        assert bot.state["last_watch"]["sentiment"] == mood
        runs.append([(t["asset"], t["date"], round(t["pnl"], 8)) for t in bot.state["trades"]])
    assert runs[0] == runs[1] and runs[0]
