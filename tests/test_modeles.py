"""Prompt maître, étape 6 et sa seconde version (docs/MODELES.md) : socle
multi-modèles d'IA (registre sans modèle inventé, cycle de vie et banc
obligatoire, invites versionnées, routage par politique sur mesures réelles,
confidentialité, disjoncteur, budgets de jetons et de coût, repli tracé,
réponses vérifiées, désaccord entre IA, fiches et mesures) ; une IA n'a aucun
chemin vers un ordre."""

import logging
import pathlib
import sqlite3
import time
from decimal import Decimal

import pytest
from test_assistant import ctx
from test_market_watch import NOW, UNIVERSE, _answer, _fetch_bytes, _fetch_json_quiet, _run

from panel import assistant as asst
from test_trendguard import SIM_FROM, make_bot, synthetic_market
from trendguard import market_watch as mw
from trendguard import modeles
from trendguard.contrats import ContractError

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEY = "k" * 20
LOCAL = "http://127.0.0.1:11434/v1/chat/completions"
ANSWERS = {"pourcentage": "1 %", "combien de jours": "30 jours", "vendre à découvert": "Non.",
           "moyenne de 150 jours": "non", "combien d'USDT": "1 000", "hausse en pour cent": "20 %",
           "17 × 23": "391", "cours exact": "Inconnu"}


def _ok(text="ok", tokens=(10, 5)):
    return lambda m: (text, tokens)


def _approve(led, *models, accuracy=1.0):
    """Banc concluant pour ces modèles, comme après la saisie d'une clé."""
    for provider, model in models:
        passed = accuracy >= modeles.BENCH_MIN
        led.bench_record("essai", modeles.bench_version(), {
            "provider": provider, "model": model, "accuracy": accuracy, "categories": {"faits": accuracy},
            "p50_ms": 500, "cases": len(modeles.BENCH), "errors": 0, "passed": passed,
            "note": "" if passed else "justesse trop basse"})


def _good(m, system, question):
    return next(a for k, a in ANSWERS.items() if k in question), (None, None)


# ---------- Registre, cycle de vie, confidentialité ----------

def test_the_registry_invents_nothing_and_never_holds_a_key():
    models = modeles.registry({})
    assert len(models) == len(mw.PROVIDERS) and not any(m.configured for m in models)
    env = {"OPENAI_API_KEY": "sk-secret-" + KEY, "VEILLE_MODEL_OPENAI": "gpt-x", "TG_LLM_LOCAL_URL": LOCAL}
    by = {m.provider: m for m in modeles.registry(env)}
    assert by["openai"].configured and by["openai"].model_id == "openai:gpt-x" and not by["claude"].configured
    assert by["local"].kind == "LOCAL" and by["local"].model == modeles.LOCAL_MODEL
    assert "sk-secret" not in repr(list(by.values())) and "sk-secret" not in repr(modeles.status(env, "absent.db"))
    rows = {r["provider"]: r for r in modeles.status({}, "absent.db")}
    assert rows["claude"]["lifecycle"] == "REGISTERED" and rows["claude"]["status"] == "DISABLED"
    assert rows["claude"]["health_score"] is None and rows["claude"]["bench"] is None        # rien d'inventé
    assert modeles.describe(list(rows.values())).startswith("aucune IA configurée (8 fournisseurs")
    assert modeles.mode(list(rows.values())) == "DEGRADED"
    for url in ("http://localhost:11434/v1/chat/completions", "http://192.168.1.20:8000/v1/chat/completions",
                "http://[::1]:11434/v1/chat/completions"):
        assert modeles.local_provider({"TG_LLM_LOCAL_URL": url}) is not None, url
    for url in ("https://api.example.com/v1/chat/completions", f"http://{'.'.join(['8'] * 4)}/v1", "file:///etc/passwd"):
        assert modeles.local_provider({"TG_LLM_LOCAL_URL": url}) is None, url    # « local » vers Internet : refusé


def test_no_model_serves_before_passing_its_benchmark():
    led = modeles.Ledger("")
    env = {"OPENAI_API_KEY": KEY}
    openai = next(m for m in modeles.registry(env) if m.provider == "openai")
    assert modeles.lifecycle(openai, led)[0] == "TESTING"
    r = modeles.route(modeles.registry(env), "PUBLIC", led, 1000)
    assert r.selected == [] and "jamais évalué" in r.refused["openai"]
    ex = modeles.execute("essai", ("essai", "x"), "bonjour", _ok(), env=env, ledger=led)
    assert not ex.ok and ex.code == modeles.UNAVAILABLE and led.last(1) == []          # aucun appel
    _approve(led, ("openai", "gpt-5-mini"), accuracy=0.5)
    state, why, bench = modeles.lifecycle(openai, led)
    assert state == "TESTING" and "banc raté" in why and modeles.provider_status(state, bench, True) == "BLOCKED"
    _approve(led, ("openai", "gpt-5-mini"))
    assert modeles.lifecycle(openai, led)[0] == "APPROVED"
    assert modeles.execute("essai", ("essai", "x"), "bonjour", _ok(), env=env, ledger=led).ok
    assert modeles.lifecycle(openai, led)[0] == "ACTIVE"
    other = next(m for m in modeles.with_model(modeles.registry(env), {"openai": "gpt-6"}) if m.provider == "openai")
    assert modeles.lifecycle(other, led)[0] == "TESTING"                              # nouveau modèle : nouveau banc


def test_a_secret_never_leaves_the_pc():
    assert modeles.privacy_class("Que fait le bot ?") == "PUBLIC"
    assert modeles.privacy_class("Que fait le bot ?", "INTERNAL") == "INTERNAL"
    for text in ("ma clé sk-" + "A1" * 12, "mot de passe : hunter2024", "x" * 20 + "1234567890abcdef"):
        assert modeles.privacy_class(text, "INTERNAL") == "LOCAL_ONLY", text
    led = modeles.Ledger("")
    _approve(led, ("mistral", "mistral-medium-latest"), ("local", modeles.LOCAL_MODEL))
    env = {"MISTRAL_API_KEY": KEY, "TG_LLM_LOCAL_URL": LOCAL}
    r = modeles.route(modeles.registry(env), "LOCAL_ONLY", led, 1000)
    assert r.selected == ["local"] and "fournisseur extérieur interdit" in r.refused["mistral"]
    ex = modeles.execute("essai", ("p", "x"), "sk-" + "A1" * 12, _ok(), env={"MISTRAL_API_KEY": KEY}, ledger=led)
    assert not ex.ok and ex.code == "MODEL_UNAVAILABLE"                  # sans modèle local : aucun appel


# ---------- Trace, santé, disjoncteur, budgets ----------

def test_breaker_opens_after_three_failures_and_closes_on_success():
    led = modeles.Ledger("")
    _approve(led, ("openai", "gpt-5-mini"))
    assert led.health("openai") == modeles.EMPTY_HEALTH
    t = time.time()
    led.record("x", "openai", "m", ("p", "v"), "PUBLIC", True, 800, at=t - 60)
    for _ in range(modeles.BREAKER_FAILS):
        led.record("x", "openai", "m", ("p", "v"), "PUBLIC", False, 100, "délai dépassé", at=t)
    h = led.health("openai", now=t + 1)
    assert h["breaker"] == "OPEN" and h["failures"] == 3 and h["p50_ms"] == 800 and h["health_score"] == 0.25
    assert led.health("openai", now=t + modeles.BREAKER_COOLDOWN + 1)["breaker"] == "HALF_OPEN"
    r = modeles.route(modeles.registry({"OPENAI_API_KEY": KEY}), "PUBLIC", led, 1000)
    assert r.selected == [] and "disjoncteur" in r.refused["openai"]
    led.record("x", "openai", "m", ("p", "v"), "PUBLIC", True, 900)
    assert led.health("openai")["breaker"] == "CLOSED"
    for table in ("llm_executions", "llm_benchmarks"):
        with pytest.raises(sqlite3.IntegrityError):
            led.conn.execute(f"DELETE FROM {table}")                   # trace en ajout seulement


def test_daily_token_and_cost_budgets():
    led = modeles.Ledger("")
    _approve(led, ("mistral", "mistral-medium-latest"))
    led.record("x", "mistral", "m", ("p", "v"), "PUBLIC", True, 100, tokens=(80, 30))
    env = {"MISTRAL_API_KEY": KEY, "TG_LLM_JETONS_JOUR": "100"}
    assert not modeles.execute("x", ("p", "v"), "bonjour", _ok(), env=env, ledger=led).ok
    assert modeles.route(modeles.registry(env), "PUBLIC", led, 0).refused["mistral"].startswith("budget de jetons")
    assert modeles.prices({"TG_LLM_PRIX_MISTRAL": "2/6", "TG_LLM_PRIX_GROK": "beaucoup"}) == {
        "mistral": (Decimal(2), Decimal(6))}                                          # prix illisible : ignoré
    assert modeles.cost_of((1000, 500), (Decimal(2), Decimal(6))) == Decimal("0.005")
    assert modeles.cost_of((1000, None), (Decimal(2), Decimal(6))) is None           # jetons inconnus : coût inconnu
    priced = {"MISTRAL_API_KEY": KEY, "TG_LLM_PRIX_MISTRAL": "2/6", "TG_LLM_BUDGET_USD_JOUR": "0,004"}
    ex = modeles.execute("x", ("p", "v"), "bonjour", _ok(tokens=(1000, 500)), env=priced, ledger=led)
    assert ex.ok and ex.cost_usd == Decimal("0.005") and led.cost_since(0) == Decimal("0.005")
    again = modeles.execute("x", ("p", "v"), "bonjour", _ok(), env=priced, ledger=led)
    assert not again.ok and "budget de coût" in again.attempts[0]["refused"]
    typo = dict(priced, TG_LLM_BUDGET_USD_JOUR="dix dollars")                       # plafond illisible : 0
    assert not modeles.execute("x", ("p", "v"), "bonjour", _ok(), env=typo, ledger=led).ok


def test_fallback_is_traced_and_versions_recorded():
    led = modeles.Ledger("")
    _approve(led, ("openai", "gpt-5-mini"), ("mistral", "mistral-medium-latest"))
    env = {"OPENAI_API_KEY": KEY, "MISTRAL_API_KEY": KEY}

    def call(m):
        if m.provider == "openai":
            raise mw.HttpError(429, "slow down")
        return "réponse de secours", (12, 7)
    ex = modeles.execute("essai", ("essai", "invite"), "bonjour", call, env=env, ledger=led)
    assert ex.ok and ex.provider == "mistral" and ex.text == "réponse de secours" and ex.tokens == (12, 7)
    assert ex.attempts[0] == {"provider": "openai", "error": "quota ou limite de débit atteints", "retryable": True,
                              "rejected": False}
    rows = list(led.conn.execute("SELECT provider, ok, error, fallback_from, prompt_id, prompt_version, "
                                 "input_tokens FROM llm_executions ORDER BY id"))
    v = modeles.prompt_version("invite")
    assert rows == [("openai", 0, "quota ou limite de débit atteints", None, "essai", v, None),
                    ("mistral", 1, None, "openai", "essai", v, 12)]
    assert led.tokens_since(0) == 19
    last = led.last(2)
    assert last[0]["reason"].startswith("rang 2 sur 2 : note ") and last[0]["reason"].endswith(
        "repli après l'échec de openai")
    assert "justesse au banc 100 %" in last[1]["reason"] and "réussite récente non mesurée" in last[1]["reason"]
    assert last[0]["input_hash"] == modeles.prompt_version("bonjour") and "bonjour" not in repr(last)
    assert last[0]["request_id"] == last[1]["request_id"] == ex.request_id                 # une demande, deux essais
    assert not modeles.execute("x", ("p", "v"), "bonjour", lambda m: ("  ", (None, None)), env=env, ledger=led).ok


def test_routing_policy_ranks_on_measured_values_only():
    led = modeles.Ledger("")
    _approve(led, ("openai", "gpt-5-mini"), accuracy=0.8)
    _approve(led, ("mistral", "mistral-medium-latest"), accuracy=1.0)
    env = {"OPENAI_API_KEY": KEY, "MISTRAL_API_KEY": KEY, "ANTHROPIC_API_KEY": KEY}
    _approve(led, ("claude", "claude-opus-5"), accuracy=0.8)
    r = modeles.route(modeles.registry(env), "PUBLIC", led, 1000, policy="veille")
    assert r.selected == ["mistral", "claude", "openai"]           # justesse d'abord ; à égalité, la préférence
    for _ in range(9):                                            # 1 réussite sur 10 : peu fiable
        led.record("x", "mistral", "m", ("p", "v"), "PUBLIC", False, 100, "réseau injoignable")
    led.record("x", "mistral", "m", ("p", "v"), "PUBLIC", True, 100)
    assert modeles.route(modeles.registry(env), "PUBLIC", led, 1000, policy="veille").selected[0] == "claude"
    score, text = modeles.routing_score(modeles.EMPTY_HEALTH, None, "rachelle")
    assert score == 0.5 and text.count("non mesurée") == 3                          # rien d'inventé : neutre


# ---------- Réponses vérifiées, invites versionnées ----------

def test_invented_amounts_are_rejected_and_the_next_model_answers():
    sources = "Capital : 99,16 USDT ; risque 1 % ; position AAVE achetée à 245,30 USDT."
    assert modeles.invented_amounts("Votre capital est de 99,16 USDT, soit environ 99 USDT.", sources) == []
    assert modeles.invented_amounts("Vous avez gagné 1 250 $ hier.", sources) == ["1 250 $"]
    led = modeles.Ledger("")
    _approve(led, ("openai", "gpt-5-mini"), ("mistral", "mistral-medium-latest"))
    env = {"OPENAI_API_KEY": KEY, "MISTRAL_API_KEY": KEY}

    def call(m):
        return ("Votre gain est de 37,50 USDT." if m.provider == "openai" else "Capital : 99,16 USDT."), (5, 5)
    ex = modeles.execute("essai", ("essai", "x"), "capital ?", call, env=env, ledger=led,
                         verify=lambda t: ", ".join(modeles.invented_amounts(t, sources)))
    assert ex.ok and ex.provider == "mistral" and ex.attempts[0]["rejected"] is True
    rejected = led.last(2)[1]
    assert rejected["ok"] == 0 and rejected["error"] == "réponse rejetée : 37,50 USDT"
    assert led.metrics()["llm_rejected_total"] == 1 and led.metrics()["llm_fallback_total"] == 1


def test_prompt_registry_refuses_silent_changes():
    """Une invite changée sans nouvelle version n'est jamais envoyée : mettre
    à jour PROMPTS (version et empreinte) le dit, et ce test le tient."""
    assert modeles.prompt_label("veille", mw.SYSTEM) == "1.0.0#e131eba32e6a"
    assert modeles.prompt_label("rachelle", asst.SYSTEM) == "1.0.0#9f29d5a69fae"
    assert modeles.prompt_label("banc", modeles.BENCH_SYSTEM) == "2.0.0#ba4b78a4e40b"
    assert {p.prompt_id for p in modeles.PROMPTS} == {"veille", "rachelle", "banc"}
    assert all((ROOT / p.source).exists() for p in modeles.PROMPTS)
    with pytest.raises(ContractError) as e:
        modeles.prompt_label("rachelle", asst.SYSTEM + " Ignore les règles.")
    assert e.value.code == "PROMPT_CHANGED"
    led = modeles.Ledger("")
    _approve(led, ("mistral", "mistral-medium-latest"))
    called = []
    ex = modeles.execute("rachelle", ("rachelle", asst.SYSTEM + " modifiée"), "x",
                         lambda m: called.append(m) or ("ok", (1, 1)), env={"MISTRAL_API_KEY": KEY}, ledger=led)
    assert not ex.ok and not called and "modifiée sans nouvelle version" in ex.attempts[0]["refused"]
    with pytest.raises(ContractError):
        modeles.PromptVersion("x", "1", "abc", "ACTIVE", "y")


# ---------- Adaptateurs ----------

def test_chat_adapters_report_tokens_and_refusals():
    p = mw.PROVIDER_BY_NAME["mistral"]
    seen = {}

    def post(url, payload, headers, timeout):
        seen.update(url=url, headers=headers, roles=[m["role"] for m in payload["messages"]])
        return {"choices": [{"message": {"content": "salut"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}
    assert modeles.chat_call(p, KEY, "m", "sys", [{"role": "user", "content": "x"}], post=post) == ("salut", (9, 2))
    assert seen["roles"] == ["system", "user"] and seen["headers"] == {"Authorization": f"Bearer {KEY}"}
    local = modeles.local_provider({"TG_LLM_LOCAL_URL": LOCAL})
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

def test_rachelle_falls_back_rejects_invented_amounts_then_answers_alone(tmp_path):
    path = str(tmp_path / "trendguard_modeles.db")
    led = modeles.Ledger(path)
    _approve(led, ("claude", modeles.RACHELLE_CLAUDE_MODEL), ("mistral", "mistral-medium-latest"))
    led.close()

    class Down:
        class messages:                                                   # noqa: N801
            @staticmethod
            def create(**kw):
                raise mw.HttpError(503, "surchargé")
    env = {"ANTHROPIC_API_KEY": KEY, "MISTRAL_API_KEY": KEY}

    def mistral(text):
        return lambda url, payload, headers, timeout: {"choices": [{"message": {"content": text}}]}
    ai = asst.AIHelper(env, post=mistral("Mistral répond"), claude_factory=lambda k: Down(), ledger_path=path)
    r = asst.Assistant(ai).reply("Qu'est-ce qu'un stop ?", [], ctx)
    assert r["answer"] == "Mistral répond" and r["source"] == "Mistral"   # repli sur la suivante
    liar = asst.AIHelper(env, post=mistral("Votre gain atteint 4 321 USDT."), claude_factory=lambda k: Down(),
                         ledger_path=path)
    r = asst.Assistant(liar).reply("Combien ai-je gagné ?", [], ctx)
    assert r["source"] == "local" and "4 321" not in r["answer"]          # chiffre inventé : réponse intégrée

    def broken(url, payload, headers, timeout):
        raise mw.HttpError(500, "panne")
    alone = asst.Assistant(asst.AIHelper(env, post=broken, claude_factory=lambda k: Down(), ledger_path=path))
    r = alone.reply("Qu'est-ce qu'un stop ?", [], ctx)
    assert r["source"] == "local" and "Stop" in r["answer"]               # aucune IA : réponse intégrée
    untested = asst.AIHelper({"GEMINI_API_KEY": KEY}, post=mistral("Gemini répond"), ledger_path=path)
    assert asst.Assistant(untested).reply("Qu'est-ce qu'un stop ?", [], ctx)["source"] == "local"   # jamais évalué
    rows = modeles.status({}, "absent.db")
    assert "aucune IA configurée" in asst.local_answer("Quelles IA utilise le bot ?", {"modeles": rows})["answer"]


def test_the_watch_skips_untested_or_tripped_ais_and_traces_the_others(tmp_path):
    path = str(tmp_path / "trendguard_modeles.db")
    led = modeles.Ledger(path)
    _approve(led, ("openai", "gpt-5-mini"), ("deepseek", "deepseek-chat"), ("mistral", "mistral-medium-latest"))
    for _ in range(modeles.BREAKER_FAILS):
        led.record("veille", "openai", "m", ("veille", "v"), "PUBLIC", False, 100, "délai dépassé")
    led.close()
    assert modeles.ledger_path(str(tmp_path / "veille.db")) == path

    def call(p, key, model, system, prompt):
        assert p.name not in ("openai", "grok")                           # disjoncteur ouvert, jamais évaluée
        return _answer([], views={"aave": 0.6 if p.name == "deepseek" else -0.6}), set()
    env = {"OPENAI_API_KEY": KEY, "DEEPSEEK_API_KEY": KEY, "MISTRAL_API_KEY": KEY, "XAI_API_KEY": KEY}
    memory = mw.WatchMemory(str(tmp_path / "veille.db"))
    trace = modeles.WatchTrace(path, env)
    try:
        rep = mw.daily_report(UNIVERSE, ["aave"], NOW, memory, env=env, fetch_json=_fetch_json_quiet,
                              fetch_bytes=_fetch_bytes, call=call, trace=trace)
    finally:
        trace.close()
        memory.close()
    prov = rep["providers"]
    assert "disjoncteur" in prov["openai"]["error"] and "jamais évalué" in prov["grok"]["error"]
    c = rep["consensus"]
    assert c["providers"] == 2 and c["disagreements"] == {"aave": [-0.6, 0.6]} and c["status"] == "CONFLICT"
    assert "aave" not in c["views"]                                       # pas de moyenne trompeuse (0)
    detail = c["disagreement_details"][0]
    assert detail["subject"] == "aave" and detail["type"] == "FINANCIAL" and detail["resolution_status"] == "NO_DECISION"
    assert dict(detail["positions"]) == {"deepseek": 0.6, "mistral": -0.6}
    assert any(a["level"] == 1 and "IA en désaccord sur AAVE" in a["text"] for a in rep["alerts"])   # détenue
    assert "AAVE de −0,6 à +0,6" in mw.render(rep)
    by = {r["provider"]: r for r in modeles.status(env, path)}
    assert by["deepseek"]["calls"] == 1 and by["openai"]["breaker"] == "OPEN" and by["grok"]["status"] == "UNKNOWN"
    led = modeles.Ledger(path, readonly=True)
    try:
        ids = {r[0] for r in led.conn.execute("SELECT prompt_version FROM llm_executions WHERE provider='deepseek'")}
    finally:
        led.close()
    assert ids == {modeles.prompt_label("veille", mw.SYSTEM)}


def test_the_watch_repairs_an_invalid_answer_once():
    calls = {}

    def call(p, key, model, system, prompt):
        calls[p.name] = calls.get(p.name, 0) + 1
        if p.name == "mistral" and mw.REPAIR not in prompt:
            return "Voici mon analyse : le marché est calme.", set()      # hors schéma : redemandée
        if p.name == "gemini":
            return "Désolé.", set()                                       # deux fois hors schéma : échec
        return _answer([]), set()
    env = {"MISTRAL_API_KEY": KEY, "GEMINI_API_KEY": KEY}
    res = mw.ask_all(mw.configured(env), mw.SYSTEM, {False: "<<<DONNEES", True: "<<<DONNEES"}, [], UNIVERSE, call)
    assert res["mistral"]["ok"] and res["mistral"]["repaired"] and calls["mistral"] == 2
    assert not res["gemini"]["ok"] and "aucun objet JSON" in res["gemini"]["error"] and calls["gemini"] == 2


def test_close_views_are_not_a_disagreement():
    ok = {n: {"sentiment": s, "views": {"aave": s}} for n, s in (("a", 0.1), ("b", 0.4))}
    assert mw.disagreements(ok) == {}
    ok["c"] = {"sentiment": -0.7, "views": {}}
    assert mw.disagreements(ok) == {"market": [-0.7, 0.4]}


# ---------- Banc d'évaluation, fiches, mesures ----------

def test_benchmark_is_versioned_and_governs_the_router(tmp_path, monkeypatch, capsys):
    led = modeles.Ledger("")
    assert modeles.benchmark(_good, modeles.bench_targets({}), led) == []            # rien à mesurer
    env = {"MISTRAL_API_KEY": KEY, "ANTHROPIC_API_KEY": KEY}
    targets = modeles.bench_targets(env)
    assert {m.model_id for m in targets} == {"claude:claude-opus-5", "mistral:mistral-medium-latest",
                                            "claude:" + modeles.RACHELLE_CLAUDE_MODEL}
    rows = modeles.benchmark(_good, targets, led)
    assert all(r["passed"] and r["accuracy"] == 1.0 for r in rows) and rows[0]["version"] == modeles.bench_version()
    assert set(rows[0]["categories"]) == {"faits", "finance", "calcul", "invention"}
    assert "approuvé" in modeles.bench_line(rows[0])

    def sloppy(m, system, question):
        return ("oui" if "oui ou non" in question else "inconnu"), (None, None)
    newer = modeles.with_model(modeles.registry(env), {"mistral": "mistral-large-latest"})
    worse = modeles.benchmark(sloppy, [m for m in newer if m.provider == "mistral"], led)[0]
    assert not worse["passed"] and worse["note"].startswith("régression")          # nouveau modèle moins bon : écarté
    assert modeles.lifecycle(next(m for m in newer if m.provider == "mistral"), led)[0] == "TESTING"

    def offline(m, system, question):
        raise mw.HttpError(503, "indisponible")
    out = modeles.benchmark(offline, [m for m in targets if m.provider == "mistral"], led)[0]
    assert not out["passed"] and "non concluant" in out["note"]
    mistral = next(m for m in targets if m.provider == "mistral")
    state, _why, bench = modeles.lifecycle(mistral, led)
    assert state != "TESTING" and bench["accuracy"] == 1.0                         # un banc sans réseau ne compte pas
    for p in mw.PROVIDERS:
        monkeypatch.delenv(p.key_env, raising=False)
    monkeypatch.delenv("TG_LLM_LOCAL_URL", raising=False)
    monkeypatch.setenv("TG_VEILLE_DB", str(tmp_path / "veille.db"))
    assert modeles.main(["banc"]) == 0 and "rien à évaluer" in capsys.readouterr().out
    assert modeles.main([]) == 0 and "non configuré" in capsys.readouterr().out
    assert not (tmp_path / "trendguard_modeles.db").exists()


def test_scorecards_mode_and_metrics_show_only_measures(tmp_path):
    path = str(tmp_path / "trendguard_modeles.db")
    led = modeles.Ledger(path)
    _approve(led, ("mistral", "mistral-medium-latest"), ("local", modeles.LOCAL_MODEL))
    env = {"MISTRAL_API_KEY": KEY, "TG_LLM_LOCAL_URL": LOCAL, "TG_LLM_PRIX_MISTRAL": "2/6"}
    ex = modeles.execute("essai", ("essai", "x"), "bonjour", _ok(tokens=(1000, 500)), env=env, ledger=led)
    led.close()
    assert ex.ok and ex.cost_usd == Decimal("0.005")
    rows = modeles.status(env, path)
    assert modeles.mode(rows) == "HYBRID"
    card = "\n".join(modeles.scorecard(next(r for r in rows if r["provider"] == ex.provider)))
    assert "justesse au banc : 100 %" in card and "prix déclaré 2/6" in card and "fiabilité : 100 %" in card
    local = "\n".join(modeles.scorecard(next(r for r in rows if r["provider"] == "local")))
    assert "latence : non mesurée" in local and "coût inconnu" in local
    led = modeles.Ledger(path, readonly=True)
    try:
        m = led.metrics()
    finally:
        led.close()
    assert m["llm_calls_total"] == m["llm_success_total"] == 1 and m["llm_cost_total_usd"] == Decimal("0.005")
    assert m["llm_tokens_total"] == 1500 and m["llm_failure_total"] == 0
    assert modeles.describe(rows).startswith("2 IA configurée(s), mode hybride")


def test_a_new_key_is_benchmarked_before_use(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("RUN_MODE=paper\n", encoding="utf-8")
    for p in mw.PROVIDERS:
        monkeypatch.delenv(p.key_env, raising=False)
    monkeypatch.delenv("TG_LLM_LOCAL_URL", raising=False)
    monkeypatch.setenv("TG_VEILLE_DB", str(tmp_path / "veille.db"))
    monkeypatch.setattr(modeles, "resolver", lambda env=None: _good)
    key = "sk-" + "A1b2" * 10
    assert mw.cmd_set_key("mistral", str(env_file), ask=lambda _p: key, evaluate=True) == 0
    out = capsys.readouterr().out
    assert "Mistral approuvé" in out and "justesse 100 %" in out and key not in out
    led = modeles.Ledger(str(tmp_path / "trendguard_modeles.db"), readonly=True)
    try:
        assert led.bench_last("mistral", "mistral-medium-latest")["passed"] == 1
    finally:
        led.close()


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
