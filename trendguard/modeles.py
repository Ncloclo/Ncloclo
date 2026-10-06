"""
Socle multi-modèles d'IA (prompt maître, étape 6 ; docs/MODELES.md) : registre
des fournisseurs et des modèles, routage, santé, disjoncteur, repli tracé,
budget de jetons, confidentialité, versions des invites, banc d'évaluation.

    demande → classe de confidentialité → routeur (modèles configurés,
    permis, sains, dans le budget) → appel → en cas d'échec : repli tracé
    vers le suivant → sinon réponse intégrée sans IA (mode dégradé sûr)

Règles :
- aucun modèle n'est inventé : le registre ne contient que les fournisseurs
  du code (veille) et, s'il est configuré, un modèle local (Ollama ou tout
  serveur compatible) ; un modèle sans clé est « non configuré » et n'est
  jamais présenté comme disponible ; aucune mesure n'est inventée (sans
  appel, pas de chiffre) ;
- une clé n'apparaît jamais dans le registre, les traces ni les journaux ;
- confidentialité : un texte qui ressemble à un secret est LOCAL_ONLY, jamais
  envoyé à un fournisseur extérieur ;
- chaque appel est tracé (fournisseur, modèle, invite et sa version, durée,
  jetons quand le fournisseur les donne, erreur, repli) dans
  trendguard_modeles.db, en ajout seulement ; la santé et le disjoncteur se
  déduisent de cette trace, partagée par le bot et le panneau ;
- un modèle ne décide ni n'agit : sa réponse n'est qu'un texte, sans aucun
  chemin vers un ordre (la règle du bot puis la porte d'exécution décident).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import ipaddress
import os
import pathlib
import re
import sqlite3
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import v29

from . import market_watch as mw
from .texte import fr

DEFAULT_DB = os.path.join(v29.APP_DIR, "trendguard_modeles.db")
PRIVACY = ("PUBLIC", "INTERNAL", "LOCAL_ONLY")
BREAKER_FAILS = 3                 # échecs de suite : disjoncteur ouvert
BREAKER_COOLDOWN = 15 * 60        # secondes avant un nouvel essai (demi-ouvert)
BREAKER_LABELS = {"CLOSED": "fermé", "OPEN": "ouvert", "HALF_OPEN": "demi-ouvert"}
DAILY_TOKENS = 300_000            # budget de jetons par jour (TG_LLM_JETONS_JOUR)
LOCAL_MODEL = "llama3.1"
SECRET_LIKE = (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
               re.compile(r"\b(?=[A-Za-z0-9_\-+=]*\d)(?=[A-Za-z0-9_\-+=]*[A-Za-z])[A-Za-z0-9_\-+=]{32,}"),
               re.compile(r"(?i)\b(mot de passe|password|api[ _-]?key|secret)\s*[:=]\s*\S{6,}"))
RETRYABLE = ("délai dépassé", "réseau injoignable", "quota ou limite de débit atteints")


def privacy_class(text: str, base: str = "PUBLIC") -> str:
    """LOCAL_ONLY si le texte ressemble à un secret (clé, mot de passe) ;
    sinon la classe de la demande : PUBLIC (actualités) ou INTERNAL
    (données du bot, envoyées seulement aux IA dont vous avez mis la clé)."""
    return "LOCAL_ONLY" if any(p.search(text or "") for p in SECRET_LIKE) else base


def prompt_version(text: str) -> str:
    """Version d'une invite : l'empreinte de son texte. Une invite changée
    change de version (et un test la tient)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def ledger_path(watch_db: str) -> str:
    """La trace des appels, à côté de la mémoire de la veille."""
    return os.path.join(os.path.dirname(watch_db), "trendguard_modeles.db") if watch_db else DEFAULT_DB


# ---------- Registre des fournisseurs et des modèles ----------

@dataclass(frozen=True)
class ModelManifest:
    """Un modèle tel que le registre le connaît (jamais sa clé)."""
    provider: str
    label: str
    kind: str                     # CLOUD ou LOCAL
    model: str
    host: str
    capabilities: Tuple[str, ...]
    max_privacy: str              # classe la plus sensible acceptée
    configured: bool

    def accepts(self, privacy: str) -> bool:
        return PRIVACY.index(privacy) <= PRIVACY.index(self.max_privacy)


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1) if m else "api.anthropic.com"


def is_local_host(url: str) -> bool:
    """Adresse de ce PC ou du réseau privé seulement : un « modèle local »
    qui pointerait vers Internet ferait sortir des données LOCAL_ONLY."""
    host = re.sub(r":\d+$", "", _host(url)).strip("[]").lower()
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False                     # nom de machine : invérifiable, refusé
    return ip.is_loopback or ip.is_private


def local_provider(env: Optional[Dict[str, str]] = None) -> Optional[mw.Provider]:
    """Le modèle local comme fournisseur compatible (API « chat/completions »
    d'Ollama, de vLLM ou de llama.cpp), ou None s'il n'est pas configuré ou
    si son adresse n'est pas locale."""
    env = os.environ if env is None else env
    url = (env.get("TG_LLM_LOCAL_URL") or "").strip()
    if not url.startswith(("http://", "https://")) or not is_local_host(url):
        return None
    return mw.Provider("local", "Modèle local", "TG_LLM_LOCAL_URL",
                       (env.get("TG_LLM_LOCAL_MODEL") or LOCAL_MODEL).strip(), url)


def registry(env: Optional[Dict[str, str]] = None) -> List[ModelManifest]:
    """Les modèles connus : fournisseurs du code (configurés si leur clé est
    dans .env), puis le modèle local s'il est configuré."""
    env = os.environ if env is None else env
    found = {p.name: m for p, _k, m in mw.configured(env)}
    out = []
    for p in mw.PROVIDERS:
        caps = ("texte", "json") + (("recherche web",) if p.web else ())
        out.append(ModelManifest(p.name, p.label, "CLOUD", found.get(p.name, p.model), _host(p.url), caps,
                                 "INTERNAL", p.name in found))
    lp = local_provider(env)
    if lp is not None:
        out.append(ModelManifest(lp.name, lp.label, "LOCAL", lp.model, _host(lp.url), ("texte", "json"),
                                 "LOCAL_ONLY", True))
    return out


# ---------- Trace des appels (santé, disjoncteur, jetons) ----------

class Ledger:
    """Trace de chaque appel à un modèle, en ajout seulement (WAL : le bot et
    le panneau y écrivent tous deux)."""

    def __init__(self, path: str = DEFAULT_DB, readonly: bool = False):
        self.path = path or ":memory:"
        if readonly:
            self.conn = sqlite3.connect(pathlib.Path(self.path).resolve().as_uri() + "?mode=ro", uri=True,
                                        timeout=30)
            return
        self.conn = sqlite3.connect(self.path, timeout=30)
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS llm_executions (
            id INTEGER PRIMARY KEY, at REAL NOT NULL, purpose TEXT NOT NULL, provider TEXT NOT NULL,
            model TEXT NOT NULL, prompt_id TEXT NOT NULL, prompt_version TEXT NOT NULL,
            privacy TEXT NOT NULL CHECK (privacy IN ('PUBLIC', 'INTERNAL', 'LOCAL_ONLY')),
            ok INTEGER NOT NULL CHECK (ok IN (0, 1)), error TEXT, latency_ms INTEGER NOT NULL,
            input_tokens INTEGER, output_tokens INTEGER, fallback_from TEXT,
            reason TEXT, input_hash TEXT)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS llm_exec_provider ON llm_executions(provider, id)")
        for op in ("UPDATE", "DELETE"):
            self.conn.execute(f"CREATE TRIGGER IF NOT EXISTS llm_executions_{op.lower()} BEFORE {op} ON "
                              "llm_executions BEGIN SELECT RAISE(ABORT, 'llm_executions : ajout seulement'); END")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def record(self, purpose: str, provider: str, model: str, prompt: Tuple[str, str], privacy: str,
               ok: bool, latency_ms: int, error: str = "",
               tokens: Tuple[Optional[int], Optional[int]] = (None, None), fallback_from: str = "",
               reason: str = "", input_hash: str = "", at: Optional[float] = None) -> None:
        """Un appel : `prompt` est (identifiant de l'invite, version) ;
        `reason` dit pourquoi ce modèle ; `input_hash` est l'empreinte des
        données envoyées (jamais leur contenu)."""
        self.conn.execute("INSERT INTO llm_executions (at, purpose, provider, model, prompt_id, prompt_version, "
                          "privacy, ok, error, latency_ms, input_tokens, output_tokens, fallback_from, reason, "
                          "input_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (at or time.time(), purpose, provider, model, prompt[0], prompt[1], privacy, int(ok),
                           error or None, int(latency_ms), tokens[0], tokens[1], fallback_from or None,
                           reason or None, input_hash or None))
        self.conn.commit()

    def last(self, n: int = 10) -> List[Dict[str, Any]]:
        """Les derniers appels, du plus récent au plus ancien (pour répondre
        à « quel modèle, pourquoi, quel repli ? »)."""
        cur = self.conn.execute("SELECT * FROM llm_executions ORDER BY id DESC LIMIT ?", (n,))
        names = [c[0] for c in cur.description]
        return [dict(zip(names, row)) for row in cur]

    def tokens_since(self, since: float) -> int:
        r = self.conn.execute("SELECT COALESCE(SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)), 0) "
                              "FROM llm_executions WHERE at >= ?", (since,)).fetchone()
        return int(r[0])

    def health(self, provider: str, now: Optional[float] = None) -> Dict[str, Any]:
        """Santé mesurée (jamais inventée) sur les 20 derniers appels :
        nombre, échecs, durée médiane et 95e centile, état du disjoncteur
        (ouvert après 3 échecs de suite, demi-ouvert 15 minutes plus tard :
        un essai permis, fermé au premier succès)."""
        now = now or time.time()
        rows = list(self.conn.execute("SELECT at, ok, latency_ms FROM llm_executions WHERE provider=? "
                                      "ORDER BY id DESC LIMIT 20", (provider,)))
        if not rows:
            return {"calls": 0, "failures": 0, "breaker": "CLOSED", "p50_ms": None, "p95_ms": None}
        streak = 0
        for _at, ok, _ms in rows:
            if ok:
                break
            streak += 1
        breaker = "CLOSED"
        if streak >= BREAKER_FAILS:
            breaker = "OPEN" if now - rows[0][0] < BREAKER_COOLDOWN else "HALF_OPEN"
        lat = sorted(ms for _a, ok, ms in rows if ok)
        return {"calls": len(rows), "failures": sum(1 for _a, ok, _m in rows if not ok), "breaker": breaker,
                "p50_ms": int(statistics.median(lat)) if lat else None,
                "p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None}


# ---------- Routage et exécution avec repli ----------

@dataclass(frozen=True)
class Route:
    selected: List[str]
    refused: Dict[str, str]
    why: Dict[str, str] = dataclasses.field(default_factory=dict)


def route(models: Sequence[ModelManifest], privacy: str, ledger: Ledger, budget_left: int,
          needs: Sequence[str] = ("texte",), prefer: Sequence[str] = ("claude",)) -> Route:
    """Modèles candidats, du meilleur au moins bon, et pourquoi les autres
    sont écartés (non configuré, confidentialité, capacité, disjoncteur,
    budget). Classement : taux de réussite mesuré, puis préférence
    déclarée, puis ordre du registre ; un modèle jamais appelé n'a pas de
    note inventée (neutre)."""
    ok, refused, why = [], {}, {}
    for order, m in enumerate(models):
        h = ledger.health(m.provider)
        if not m.configured:
            refused[m.provider] = "non configuré (aucune clé)"
        elif not m.accepts(privacy):
            refused[m.provider] = f"confidentialité {privacy} : fournisseur extérieur interdit"
        elif not set(needs) <= set(m.capabilities):
            refused[m.provider] = "capacité absente"
        elif h["breaker"] == "OPEN":
            refused[m.provider] = "disjoncteur ouvert (échecs répétés)"
        elif m.kind == "CLOUD" and budget_left <= 0:
            refused[m.provider] = "budget de jetons du jour épuisé"
        else:
            rate = 1 - h["failures"] / h["calls"] if h["calls"] else 1.0
            rank = list(prefer).index(m.provider) if m.provider in prefer else len(prefer)
            ok.append((-rate, rank, order, m.provider))
            why[m.provider] = ((f"réussite récente {fr(rate * 100, '.0f')} % sur {h['calls']} appel(s)"
                                if h["calls"] else "jamais appelé (note neutre, rien d'inventé)")
                               + (", préféré" if rank < len(prefer) else "")
                               + (f", disjoncteur {BREAKER_LABELS[h['breaker']]}" if h["breaker"] != "CLOSED" else ""))
    ok.sort()
    for k, x in enumerate(ok, 1):
        why[x[-1]] = f"rang {k} sur {len(ok)} : {why[x[-1]]}"
    return Route([x[-1] for x in ok], refused, why)


@dataclass
class Execution:
    text: str
    provider: str
    model: str
    attempts: List[Dict[str, Any]]
    tokens: Tuple[Optional[int], Optional[int]] = (None, None)


def execute(purpose: str, prompt: Tuple[str, str], user_text: str, call: Callable[[ModelManifest], Any],
            env: Optional[Dict[str, str]] = None, ledger: Optional[Ledger] = None,
            base_privacy: str = "PUBLIC", models: Optional[Sequence[ModelManifest]] = None
            ) -> Optional[Execution]:
    """Appelle le meilleur modèle permis ; en cas d'échec, le suivant (repli
    tracé : modèle d'origine, raison, modèle de repli). `prompt` est
    (identifiant, texte fixe de l'invite) ; `call(manifest)` renvoie
    (texte, (jetons d'entrée, de sortie)). None si aucun modèle ne répond :
    l'appelant donne alors sa réponse intégrée (mode dégradé sûr)."""
    env = os.environ if env is None else env
    own = ledger is None
    ledger = ledger or Ledger(env.get("TG_LLM_DB") or DEFAULT_DB)
    try:
        privacy = privacy_class(user_text, base_privacy)
        pid = (prompt[0], prompt_version(prompt[1]))
        day0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        budget = int(env.get("TG_LLM_JETONS_JOUR") or DAILY_TOKENS) - ledger.tokens_since(day0)
        known = {m.provider: m for m in (registry(env) if models is None else models)}
        r = route(list(known.values()), privacy, ledger, budget)
        attempts: List[Dict[str, Any]] = [{"provider": p, "refused": why} for p, why in r.refused.items()
                                          if "non configuré" not in why]
        digest = prompt_version(user_text)
        previous = ""
        for name in r.selected:
            m = known[name]
            reason = r.why[name] + (f" ; repli après l'échec de {previous}" if previous else "")
            t0 = time.time()
            try:
                text, tokens = call(m)
                if not (text or "").strip():
                    raise ValueError("réponse vide")
            except Exception as e:
                why = mw.friendly_error(e)
                ledger.record(purpose, name, m.model, pid, privacy, False, int((time.time() - t0) * 1000), why,
                              fallback_from=previous, reason=reason, input_hash=digest)
                attempts.append({"provider": name, "error": why, "retryable": why in RETRYABLE})
                previous = name
                continue
            ledger.record(purpose, name, m.model, pid, privacy, True, int((time.time() - t0) * 1000),
                          tokens=tokens, fallback_from=previous, reason=reason, input_hash=digest)
            attempts.append({"provider": name, "ok": True})
            return Execution(text, name, m.model, attempts, tokens)
        return None
    finally:
        if own:
            ledger.close()


def chat_call(provider: mw.Provider, key: str, model: str, system: str, messages: List[Dict[str, str]],
              post: Callable[..., Any] = mw.http_post_json, claude_factory: Optional[Callable[[str], Any]] = None,
              refusal: str = "") -> Tuple[str, Tuple[Optional[int], Optional[int]]]:
    """Un appel « conversation » par l'adaptateur du fournisseur (SDK
    Anthropic pour Claude, API compatible pour les autres et le modèle
    local) : (texte, (jetons d'entrée, de sortie) quand ils sont donnés).
    Un refus du modèle rend `refusal`."""
    if provider.name == "claude":
        if claude_factory is not None:
            client = claude_factory(key)
        else:
            import anthropic
            client = anthropic.Anthropic(api_key=key, timeout=60.0, max_retries=1)
        r = client.messages.create(model=model, max_tokens=1024, system=system, messages=messages)
        u = getattr(r, "usage", None)
        tokens = (getattr(u, "input_tokens", None), getattr(u, "output_tokens", None))
        if r.stop_reason == "refusal":
            return refusal, tokens
        return next((b.text for b in r.content if b.type == "text"), ""), tokens
    resp = post(provider.url, {"model": model, "messages": [{"role": "system", "content": system}] + messages},
                {"Authorization": f"Bearer {key}"} if key else {}, timeout=60.0)
    u = resp.get("usage") or {}
    return resp["choices"][0]["message"]["content"] or "", (u.get("prompt_tokens"), u.get("completion_tokens"))


def resolver(env: Optional[Dict[str, str]] = None) -> Callable[[ModelManifest, str, str], Any]:
    """L'adaptateur de chaque modèle du registre (clé lue dans .env au
    moment de l'appel, jamais gardée dans le registre)."""
    env = os.environ if env is None else env
    keys = {p.name: (p, k) for p, k, _m in mw.configured(env)}
    lp = local_provider(env)
    if lp is not None:
        keys[lp.name] = (lp, "")

    def call(m: ModelManifest, system: str, user: str) -> Any:
        p, k = keys[m.provider]
        return chat_call(p, k, m.model, system, [{"role": "user", "content": user}])
    return call


class WatchTrace:
    """La veille passe par le socle : une IA dont le disjoncteur est ouvert
    n'est pas appelée ; chaque appel est tracé avec la version de l'invite
    (la veille ne connaît pas les jetons : ils restent vides, pas estimés)."""

    def __init__(self, path: str):
        self.path = path
        self._ledger: Optional[Ledger] = None

    def _open(self) -> Ledger:
        if self._ledger is None:
            self._ledger = Ledger(self.path)
        return self._ledger

    def screen(self, providers: List[Tuple[mw.Provider, str, str]]
               ) -> Tuple[List[Tuple[mw.Provider, str, str]], Dict[str, Dict[str, Any]]]:
        kept, skipped = [], {}
        for p, k, m in providers:
            if self._open().health(p.name)["breaker"] == "OPEN":
                skipped[p.name] = {"ok": False, "model": m, "error": "disjoncteur ouvert (échecs répétés)",
                                   "seconds": 0.0}
            else:
                kept.append((p, k, m))
        return kept, skipped

    def record(self, results: Dict[str, Dict[str, Any]]) -> None:
        pid = ("veille", prompt_version(mw.SYSTEM))
        for name, r in results.items():
            self._open().record("veille", name, r.get("model", ""), pid, "PUBLIC", bool(r.get("ok")),
                                int(float(r.get("seconds") or 0) * 1000), r.get("error", ""),
                                reason="veille : toutes les IA configurées, en parallèle, puis consensus")

    def close(self) -> None:
        if self._ledger is not None:
            self._ledger.close()


# ---------- Statut, banc d'évaluation ----------

def status(env: Optional[Dict[str, str]] = None, path: str = DEFAULT_DB) -> List[Dict[str, Any]]:
    """Chaque modèle : configuré ou non, santé mesurée, disjoncteur."""
    env = os.environ if env is None else env
    led = Ledger(path, readonly=True) if os.path.exists(path) else None
    try:
        rows = []
        for m in registry(env):
            h = led.health(m.provider) if led else {"calls": 0, "failures": 0, "breaker": "CLOSED",
                                                     "p50_ms": None, "p95_ms": None}
            rows.append({"provider": m.provider, "label": m.label, "kind": m.kind, "model": m.model,
                         "host": m.host, "configured": m.configured, "privacy": m.max_privacy, **h})
        return rows
    finally:
        if led:
            led.close()


def describe(rows: List[Dict[str, Any]]) -> str:
    """Les modèles en une phrase : configurés, santé mesurée, disjoncteur."""
    conf = [r for r in rows if r["configured"]]
    if not conf:
        return (f"aucune IA configurée ({len(rows)} fournisseurs connus) : la veille lit les annonces "
                "officielles et les mots-clés, Rachelle répond seule")
    parts = []
    for r in conf:
        h = "aucun appel mesuré"
        if r["calls"]:
            h = f"{r['calls']} appel(s), {r['failures']} échec(s)"
            if r.get("p50_ms") is not None:
                h += f", médiane {fr(r['p50_ms'] / 1000, '.1f')} s"
        if r["breaker"] != "CLOSED":
            h += f", disjoncteur {BREAKER_LABELS[r['breaker']]}"
        parts.append(f"{r['label']} ({r['model']}) : {h}")
    return f"{len(conf)} IA configurée(s) : " + " ; ".join(parts)


BENCH = (("Quel pourcentage du capital le bot risque-t-il par achat ? Réponds seulement par un nombre.", "1"),
         ("Sur combien de jours le bot cherche-t-il la cassure du plus haut ? Réponds seulement par un nombre.", "30"),
         ("Le bot peut-il vendre à découvert ? Réponds seulement par oui ou non.", "non"),
         ("Combien font 17 × 23 ? Réponds seulement par un nombre.", "391"))
BENCH_SYSTEM = ("Réponds en un mot, en t'appuyant uniquement sur ces faits : TrendGuard achète des cryptos sur "
                "Binance Spot à la cassure du plus haut de 30 jours, risque 1 % du capital par achat, ne vend jamais "
                "à découvert. Si tu ne sais pas, réponds « inconnu ».")


def benchmark(call: Callable[[ModelManifest, str, str], Any], env: Optional[Dict[str, str]] = None,
              ledger: Optional[Ledger] = None) -> List[Dict[str, Any]]:
    """Banc d'évaluation : questions à réponse connue (faits du bot et
    calcul), posées à chaque modèle configuré ; justesse et durée mesurées.
    Aucun modèle configuré : liste vide, aucune mesure inventée."""
    env = os.environ if env is None else env
    pid = ("banc", prompt_version(BENCH_SYSTEM))
    out = []
    for m in registry(env):
        if not m.configured:
            continue
        good, ms = 0, []
        for q, expected in BENCH:
            t0 = time.time()
            try:
                text, _tok = call(m, BENCH_SYSTEM, q)
                good += expected in re.sub(r"[^0-9a-zà-ü]+", " ", (text or "").lower()).split()
                ok = True
            except Exception:
                ok = False
            ms.append(int((time.time() - t0) * 1000))
            if ledger is not None:
                ledger.record("banc", m.provider, m.model, pid, "PUBLIC", ok, ms[-1])
        out.append({"provider": m.provider, "model": m.model, "accuracy": round(good / len(BENCH), 2),
                    "p50_ms": int(statistics.median(ms))})
    return out


def regression(previous: Sequence[Dict[str, Any]], current: Sequence[Dict[str, Any]],
               tol: float = 0.25) -> List[str]:
    """Modèles moins justes qu'au banc précédent (au-delà de la tolérance)."""
    before = {(r["provider"], r["model"]): r["accuracy"] for r in previous}
    return [f"{r['provider']} ({r['model']}) : {fr(before[(r['provider'], r['model'])] * 100, '.0f')} % → "
            f"{fr(r['accuracy'] * 100, '.0f')} %"
            for r in current if (r["provider"], r["model"]) in before
            and r["accuracy"] < before[(r["provider"], r["model"])] - tol]


def with_model(models: Sequence[ModelManifest], overrides: Dict[str, str]) -> List[ModelManifest]:
    """Le registre avec le modèle réellement employé par un usage (Rachelle
    emploie Claude Sonnet, la veille Claude Opus)."""
    return [dataclasses.replace(m, model=overrides[m.provider]) if m.provider in overrides else m
            for m in models]


def main(argv: Optional[List[str]] = None) -> int:
    """Commande `modeles` : statut (défaut) ou banc d'évaluation."""
    from . import autonomy
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Modèles d'IA de TrendGuard (registre, santé, banc)")
    ap.add_argument("action", nargs="?", default="statut", choices=["statut", "banc"])
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    path = ledger_path(g.watch_db)
    if args.action == "statut":
        rows = status(path=path)
        print(describe(rows))
        for r in rows:
            state = "configuré" if r["configured"] else "non configuré"
            print(f"  {r['label']:<13} {r['model']:<24} {r['kind']:<6} {state}")
        if os.path.exists(path):
            led = Ledger(path, readonly=True)
            try:
                calls = led.last(5)
            finally:
                led.close()
            if calls:
                print("Derniers appels :")
            for c in calls:
                when = datetime.fromtimestamp(c["at"], timezone.utc).strftime("%d/%m %H:%M")
                print(f"  {when} UTC {c['purpose']} → {c['provider']} ({c['model']}) : "
                      + ("réussi" if c["ok"] else f"échec, {c['error']}") + (f" — {c['reason']}" if c["reason"] else ""))
        print("Ajouter une IA : python trendguard_bot.py watch set-key claude (clé saisie masquée) ; "
              "modèle local : TG_LLM_LOCAL_URL dans .env.")
        return 0
    if not any(m.configured for m in registry()):
        print("Aucun modèle configuré : rien à évaluer (aucune mesure n'est inventée).")
        return 0
    led = Ledger(path)
    try:
        rows = benchmark(resolver(), ledger=led)
    finally:
        led.close()
    saved = autonomy.sidecar(g.lock_file, ".modeles_banc.json")
    before = (autonomy.read_json(saved) or {}).get("rows") or []
    for r in rows:
        print(f"  {r['provider']:<12} {r['model']:<24} justesse {fr(r['accuracy'] * 100, '.0f')} %, "
              f"médiane {fr(r['p50_ms'] / 1000, '.1f')} s")
    worse = regression(before, rows)
    for w in worse:
        print(f"  RÉGRESSION : {w}")
    if saved:
        autonomy.write_json(saved, {"at": v29._utcnow_iso(), "rows": rows})
    return 1 if worse else 0
