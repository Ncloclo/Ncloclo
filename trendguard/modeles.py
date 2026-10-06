"""
Socle multi-modèles d'IA (prompt maître, étape 6 et sa seconde version ;
docs/MODELES.md) : registre des fournisseurs et des modèles, cycle de vie,
registre des invites versionnées, routeur à politique pondérée, santé,
disjoncteur, repli tracé, vérification des réponses, budgets de jetons et de
coût, confidentialité, banc d'évaluation versionné, fiche et mesures de
chaque modèle.

    demande → invite enregistrée et active → confidentialité → routeur
    (modèles approuvés au banc, permis, sains, dans les budgets, classés par
    la politique de l'usage) → appel → vérification de la réponse → échec
    ou réponse rejetée : repli tracé vers le suivant → aucun :
    MODEL_UNAVAILABLE, l'appelant répond sans IA (mode dégradé sûr)

Règles :
- aucun modèle n'est inventé : le registre ne contient que les fournisseurs
  du code et, s'il est configuré à une adresse locale, un modèle local ; un
  modèle sans clé est « non configuré » et n'est jamais présenté comme
  disponible ; aucune mesure n'est inventée (sans appel ni banc, pas de
  chiffre ; une mesure absente est dite « non mesurée » et compte pour
  neutre) ; un coût n'est calculé qu'avec un prix que vous déclarez ;
- aucun modèle n'entre en service sans banc d'évaluation réussi (la saisie
  d'une clé le lance) ; un banc raté ou une régression l'écarte ;
- une invite enregistrée ne change pas sans nouvelle version : sinon elle
  est refusée, et l'IA n'est pas appelée ;
- une clé n'apparaît jamais dans le registre, les traces ni les journaux ;
- confidentialité : un texte qui ressemble à un secret est LOCAL_ONLY, jamais
  envoyé à un fournisseur extérieur ;
- chaque appel est tracé (contrat LLMExecution.v1) dans trendguard_modeles.db,
  en ajout seulement ; santé, disjoncteur, budgets et mesures en découlent ;
- un modèle ne décide ni n'agit : sa réponse n'est qu'un texte, vérifiée,
  sans aucun chemin vers un ordre (la règle du bot puis la porte d'exécution
  décident).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import v29

from . import contrats
from . import market_watch as mw
from .contrats import ContractError
from .texte import fr

DEFAULT_DB = os.path.join(v29.APP_DIR, "trendguard_modeles.db")
PRIVACY = ("PUBLIC", "INTERNAL", "LOCAL_ONLY")
BREAKER_FAILS = 3                 # échecs de suite : disjoncteur ouvert
BREAKER_COOLDOWN = 15 * 60        # secondes avant un nouvel essai (demi-ouvert)
BREAKER_LABELS = {"CLOSED": "fermé", "OPEN": "ouvert", "HALF_OPEN": "demi-ouvert"}
DAILY_TOKENS = 300_000            # budget de jetons par jour (TG_LLM_JETONS_JOUR)
LOCAL_MODEL = "llama3.1"
RACHELLE_CLAUDE_MODEL = "claude-sonnet-5"      # Rachelle répond vite (PANEL_ASSISTANT_MODEL)
UNAVAILABLE = "MODEL_UNAVAILABLE"
SECRET_LIKE = (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
               re.compile(r"\b(?=[A-Za-z0-9_\-+=]*\d)(?=[A-Za-z0-9_\-+=]*[A-Za-z])[A-Za-z0-9_\-+=]{32,}"),
               re.compile(r"(?i)\b(mot de passe|password|api[ _-]?key|secret)\s*[:=]\s*\S{6,}"))
RETRYABLE = ("délai dépassé", "réseau injoignable", "quota ou limite de débit atteints")
# Politiques de routage (§8 des deux versions) : poids des mesures réelles
# selon l'usage ; une mesure absente compte pour 0,5 (neutre), jamais inventée.
POLICIES: Dict[str, Dict[str, float]] = {
    "rachelle": {"quality": 0.4, "reliability": 0.4, "speed": 0.2},    # réponse rapide
    "veille": {"quality": 0.6, "reliability": 0.4, "speed": 0.0},      # analyse de fond
    "defaut": {"quality": 0.5, "reliability": 0.5, "speed": 0.0},
}
SLOW_MS = 30_000                  # médiane à partir de laquelle la rapidité vaut 0
LIFECYCLE_LABELS = {"REGISTERED": "enregistré, sans clé", "TESTING": "à évaluer (banc)", "APPROVED": "approuvé",
                    "ACTIVE": "en service", "DEGRADED": "dégradé (disjoncteur)"}
MODE_LABELS = {"HYBRID": "hybride (IA extérieures et modèle local)", "CLOUD_ONLY": "IA extérieures seulement",
               "LOCAL_ONLY": "modèle local seulement",
               "DEGRADED": "dégradé sûr (aucune IA en service : réponses intégrées)"}
EMPTY_HEALTH: Dict[str, Any] = {"calls": 0, "failures": 0, "breaker": "CLOSED", "p50_ms": None, "p95_ms": None,
                                "health_score": None}


def privacy_class(text: str, base: str = "PUBLIC") -> str:
    """LOCAL_ONLY si le texte ressemble à un secret (clé, mot de passe) ;
    sinon la classe de la demande : PUBLIC (actualités) ou INTERNAL
    (données du bot, envoyées seulement aux IA dont vous avez mis la clé)."""
    return "LOCAL_ONLY" if any(p.search(text or "") for p in SECRET_LIKE) else base


def prompt_version(text: str) -> str:
    """Empreinte d'un texte (invite, données envoyées) : 12 caractères."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def ledger_path(watch_db: str) -> str:
    """La trace des appels, à côté de la mémoire de la veille."""
    return os.path.join(os.path.dirname(watch_db), "trendguard_modeles.db") if watch_db else DEFAULT_DB


# ---------- Registre des invites (§17-19 ; §27-28 de la première version) ----------

PROMPT_STATUSES = ("DRAFT", "TESTING", "APPROVED", "ACTIVE", "DEPRECATED", "BLOCKED")


@dataclass(frozen=True)
class PromptVersion:
    """Une invite enregistrée : version lisible, empreinte exacte du texte,
    statut, fichier où elle vit, sortie attendue. Changer le texte sans
    nouvelle version est refusé (pas de modification silencieuse)."""
    prompt_id: str
    version: str
    sha: str
    status: str
    source: str
    output: str = "texte"

    def __post_init__(self) -> None:
        if not contrats.SEMVER.match(self.version):
            raise ContractError("INVALID_VERSION", f"{self.prompt_id} : version MAJEUR.MINEUR.CORRECTIF attendue")
        if self.status not in PROMPT_STATUSES:
            raise ContractError("INVALID_ENUM", f"{self.prompt_id} : statut {self.status!r} inconnu")

    @property
    def label(self) -> str:
        return f"{self.prompt_id} v{self.version}"


PROMPTS: Tuple[PromptVersion, ...] = (
    PromptVersion("veille", "1.0.0", "e131eba32e6a", "ACTIVE", "trendguard/market_watch.py",
                  "objet JSON (market_watch.SCHEMA), validé et sourcé"),
    PromptVersion("rachelle", "1.0.0", "9f29d5a69fae", "ACTIVE", "panel/assistant.py",
                  "texte, montants vérifiés"),
    PromptVersion("banc", "2.0.0", "ba4b78a4e40b", "ACTIVE", "trendguard/modeles.py", "un mot ou un nombre"),
)


def prompt_label(prompt_id: str, text: str) -> str:
    """La version d'une invite pour la trace : « 1.0.0#empreinte ». Une
    invite enregistrée dont le texte a changé sans nouvelle version, ou qui
    n'est pas ACTIVE, est refusée (ContractError) ; une invite libre (essais)
    n'a que son empreinte."""
    sha = prompt_version(text)
    entries = [p for p in PROMPTS if p.prompt_id == prompt_id]
    if not entries:
        return sha
    for p in entries:
        if p.sha == sha:
            if p.status != "ACTIVE":
                raise ContractError("PROMPT_NOT_ACTIVE", f"invite {p.label} : statut {p.status}", category="POLICY")
            return f"{p.version}#{sha}"
    raise ContractError("PROMPT_CHANGED", f"invite « {prompt_id} » modifiée sans nouvelle version "
                                          f"(empreinte {sha}) : l'IA n'est pas appelée", category="POLICY")


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

    @property
    def model_id(self) -> str:
        """Identifiant interne stable (§4) : fournisseur:modèle, jamais le nom affiché."""
        return f"{self.provider}:{self.model}"

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


def bench_targets(env: Optional[Dict[str, str]] = None) -> List[ModelManifest]:
    """Les modèles à évaluer : ceux du registre qui sont configurés, plus le
    Claude de Rachelle s'il diffère de celui de la veille."""
    env = os.environ if env is None else env
    models = [m for m in registry(env) if m.configured]
    claude = next((m for m in models if m.provider == "claude"), None)
    rachelle = (env.get("PANEL_ASSISTANT_MODEL") or RACHELLE_CLAUDE_MODEL).strip()
    if claude is not None and rachelle != claude.model:
        models.append(dataclasses.replace(claude, label=f"{claude.label} (Rachelle)", model=rachelle))
    return models


# ---------- Prix déclarés, coût ----------

def prices(env: Optional[Dict[str, str]] = None) -> Dict[str, Tuple[Decimal, Decimal]]:
    """Prix que vous déclarez, en USD par million de jetons (entrée/sortie),
    par exemple TG_LLM_PRIX_CLAUDE=3/15. Sans prix déclaré, le coût reste
    inconnu : jamais estimé. Un prix illisible est ignoré."""
    env = os.environ if env is None else env
    out = {}
    for key, raw in env.items():
        m = re.fullmatch(r"TG_LLM_PRIX_([A-Z]+)", key.upper())
        if not m:
            continue
        try:
            a, b = (Decimal(x.strip().replace(",", ".")) for x in str(raw).split("/"))
        except (ValueError, InvalidOperation):
            continue
        if a.is_finite() and b.is_finite() and a >= 0 and b >= 0:
            out[m.group(1).lower()] = (a, b)
    return out


def cost_of(tokens: Tuple[Optional[int], Optional[int]],
            price: Optional[Tuple[Decimal, Decimal]]) -> Optional[Decimal]:
    """Coût d'un appel en USD (jetons réels × prix déclaré), ou None si l'un
    des deux est inconnu."""
    if price is None or tokens[0] is None or tokens[1] is None:
        return None
    return ((tokens[0] * price[0] + tokens[1] * price[1]) / Decimal(1_000_000)).quantize(Decimal("0.000001"))


def _env_limit(env: Dict[str, str], name: str, default: Optional[str]) -> Optional[Decimal]:
    """Un plafond lu dans l'environnement ; illisible : 0 (on n'appelle plus,
    plutôt que d'ignorer un plafond mal écrit)."""
    raw = env.get(name) or default
    if raw is None:
        return None
    try:
        v = Decimal(str(raw).strip().replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        return Decimal(0)
    return v if v.is_finite() and v >= 0 else Decimal(0)


# ---------- Trace des appels et des bancs ----------

def _append_only(conn: sqlite3.Connection, table: str) -> None:
    for op in ("UPDATE", "DELETE"):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{op.lower()} BEFORE {op} ON {table} "
                     f"BEGIN SELECT RAISE(ABORT, '{table} : ajout seulement'); END")


class Ledger:
    """Trace de chaque appel à un modèle et de chaque banc, en ajout
    seulement (WAL : le bot et le panneau y écrivent tous deux)."""

    def __init__(self, path: str = DEFAULT_DB, readonly: bool = False):
        self.path = path or ":memory:"
        if readonly:
            self.conn = sqlite3.connect(pathlib.Path(self.path).resolve().as_uri() + "?mode=ro", uri=True,
                                        timeout=30)
            return
        self.conn = sqlite3.connect(self.path, timeout=30)
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        classes = ", ".join(f"'{c}'" for c in contrats.CLASSIFICATIONS)
        self.conn.execute(f"""CREATE TABLE IF NOT EXISTS llm_executions (
            id INTEGER PRIMARY KEY, at REAL NOT NULL, purpose TEXT NOT NULL, provider TEXT NOT NULL,
            model TEXT NOT NULL, prompt_id TEXT NOT NULL, prompt_version TEXT NOT NULL,
            privacy TEXT NOT NULL CHECK (privacy IN ({classes})),
            ok INTEGER NOT NULL CHECK (ok IN (0, 1)), error TEXT, latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
            input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
            output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0), fallback_from TEXT,
            reason TEXT, input_hash TEXT, execution_id TEXT UNIQUE, request_id TEXT, cost_usd TEXT)""")
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(llm_executions)")}
        for col in ("execution_id", "request_id", "cost_usd"):      # trace d'une version précédente
            if col not in have:
                self.conn.execute(f"ALTER TABLE llm_executions ADD COLUMN {col} TEXT")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS llm_benchmarks (
            id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, at REAL NOT NULL, bench_version TEXT NOT NULL,
            provider TEXT NOT NULL, model TEXT NOT NULL, accuracy REAL NOT NULL CHECK (accuracy BETWEEN 0 AND 1),
            categories TEXT NOT NULL CHECK (json_valid(categories)), p50_ms INTEGER NOT NULL CHECK (p50_ms >= 0),
            cases INTEGER NOT NULL CHECK (cases > 0), errors INTEGER NOT NULL CHECK (errors >= 0),
            passed INTEGER NOT NULL CHECK (passed IN (0, 1)), note TEXT)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS llm_exec_provider ON llm_executions(provider, id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS llm_exec_request ON llm_executions(request_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS llm_bench_model ON llm_benchmarks(provider, model, id)")
        _append_only(self.conn, "llm_executions")
        _append_only(self.conn, "llm_benchmarks")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def record(self, purpose: str, provider: str, model: str, prompt: Tuple[str, str], privacy: str,
               ok: bool, latency_ms: int, error: str = "",
               tokens: Tuple[Optional[int], Optional[int]] = (None, None), fallback_from: str = "",
               reason: str = "", input_hash: str = "", at: Optional[float] = None,
               request_id: str = "", cost_usd: Optional[Decimal] = None) -> contrats.LLMExecution:
        """Un appel, vérifié par son contrat (LLMExecution.v1) puis noté :
        `prompt` est (identifiant de l'invite, version) ; `reason` dit
        pourquoi ce modèle ; `input_hash` est l'empreinte des données
        envoyées (jamais leur contenu) ; `request_id` relie les essais d'une
        même demande (repli compris) ; `cost_usd` seulement s'il est connu."""
        status = "COMPLETED" if ok else "TIMEOUT" if error == "délai dépassé" else "FAILED"
        ex = contrats.LLMExecution(contrats.new_id(), request_id or contrats.new_id(), purpose, provider, model,
                                   prompt[0], prompt[1], privacy, status, int(latency_ms), tokens[0], tokens[1],
                                   error, fallback_from, reason, input_hash)
        self.conn.execute("INSERT INTO llm_executions (at, purpose, provider, model, prompt_id, prompt_version, "
                          "privacy, ok, error, latency_ms, input_tokens, output_tokens, fallback_from, reason, "
                          "input_hash, execution_id, request_id, cost_usd) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (at or time.time(), purpose, provider, model, prompt[0], prompt[1], privacy, int(ok),
                           error or None, ex.latency_ms, ex.input_tokens, ex.output_tokens, fallback_from or None,
                           reason or None, input_hash or None, ex.execution_id, ex.request_id,
                           None if cost_usd is None else format(cost_usd, "f")))
        self.conn.commit()
        return ex

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

    def cost_since(self, since: float) -> Optional[Decimal]:
        """Coût connu des appels depuis `since` (USD) ; None si aucun appel
        n'a de coût connu (prix non déclaré)."""
        vals = [Decimal(r[0]) for r in self.conn.execute(
            "SELECT cost_usd FROM llm_executions WHERE at >= ? AND cost_usd IS NOT NULL", (since,))]
        return sum(vals, Decimal(0)) if vals else None

    def health(self, provider: str, now: Optional[float] = None) -> Dict[str, Any]:
        """Santé mesurée (jamais inventée) sur les 20 derniers appels :
        nombre, échecs, note de santé (part des réussites), durée médiane et
        95e centile, état du disjoncteur (ouvert après 3 échecs de suite,
        demi-ouvert 15 minutes plus tard : un essai permis, fermé au premier
        succès)."""
        now = now or time.time()
        rows = list(self.conn.execute("SELECT at, ok, latency_ms FROM llm_executions WHERE provider=? "
                                      "ORDER BY id DESC LIMIT 20", (provider,)))
        if not rows:
            return dict(EMPTY_HEALTH)
        streak = 0
        for _at, ok, _ms in rows:
            if ok:
                break
            streak += 1
        breaker = "CLOSED"
        if streak >= BREAKER_FAILS:
            breaker = "OPEN" if now - rows[0][0] < BREAKER_COOLDOWN else "HALF_OPEN"
        lat = sorted(ms for _a, ok, ms in rows if ok)
        failures = sum(1 for _a, ok, _m in rows if not ok)
        return {"calls": len(rows), "failures": failures, "breaker": breaker,
                "p50_ms": int(statistics.median(lat)) if lat else None,
                "p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
                "health_score": round(1 - failures / len(rows), 2)}

    def bench_record(self, run_id: str, version: str, row: Dict[str, Any], at: Optional[float] = None) -> None:
        """Le résultat d'un banc pour un modèle (en ajout seulement)."""
        self.conn.execute("INSERT INTO llm_benchmarks (run_id, at, bench_version, provider, model, accuracy, "
                          "categories, p50_ms, cases, errors, passed, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (run_id, at or time.time(), version, row["provider"], row["model"], row["accuracy"],
                           json.dumps(row["categories"], sort_keys=True), row["p50_ms"], row["cases"],
                           row["errors"], int(bool(row["passed"])), row.get("note") or None))
        self.conn.commit()

    def _bench(self, sql: str, args: Tuple[Any, ...]) -> Optional[Dict[str, Any]]:
        try:
            cur = self.conn.execute(sql, args)
        except sqlite3.OperationalError:          # trace d'avant les bancs (lecture seule)
            return None
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(zip([c[0] for c in cur.description], row))
        d["categories"] = json.loads(d["categories"])
        return d

    def bench_last(self, provider: str, model: str) -> Optional[Dict[str, Any]]:
        """Dernier banc concluant (aucun appel en erreur) de ce modèle."""
        return self._bench("SELECT * FROM llm_benchmarks WHERE provider=? AND model=? AND errors=0 "
                           "ORDER BY id DESC LIMIT 1", (provider, model))

    def bench_reference(self, provider: str, version: str) -> Optional[Dict[str, Any]]:
        """Dernier banc réussi de ce fournisseur avec la même version du
        banc : la référence d'une régression (même modèle plus tard, ou
        nouveau modèle du même fournisseur)."""
        return self._bench("SELECT * FROM llm_benchmarks WHERE provider=? AND bench_version=? AND passed=1 "
                           "ORDER BY id DESC LIMIT 1", (provider, version))

    def metrics(self, since: float = 0.0) -> Dict[str, Any]:
        """Mesures du socle (§71), tirées de la trace : demandes, appels,
        réussites, échecs, replis, réponses rejetées, sorties hors schéma,
        jetons, coût connu, latences."""
        rows = list(self.conn.execute("SELECT ok, error, fallback_from, latency_ms, input_tokens, output_tokens, "
                                      "cost_usd, request_id FROM llm_executions WHERE at >= ?", (since,)))
        lat = sorted(r[3] for r in rows if r[0])
        costs = [Decimal(r[6]) for r in rows if r[6]]
        return {"llm_requests_total": len({r[7] or f"x{k}" for k, r in enumerate(rows)}),
                "llm_calls_total": len(rows), "llm_success_total": sum(1 for r in rows if r[0]),
                "llm_failure_total": sum(1 for r in rows if not r[0]),
                "llm_fallback_total": sum(1 for r in rows if r[2]),
                "llm_rejected_total": sum(1 for r in rows if (r[1] or "").startswith("réponse rejetée")),
                "llm_schema_failure_total": sum(1 for r in rows if "JSON" in (r[1] or "")),
                "llm_tokens_total": sum((r[4] or 0) + (r[5] or 0) for r in rows),
                "llm_cost_total_usd": sum(costs, Decimal(0)) if costs else None,
                "llm_latency_p50_ms": int(statistics.median(lat)) if lat else None,
                "llm_latency_p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None}


# ---------- Cycle de vie, routage, exécution avec repli ----------

def _pct(v: Optional[float]) -> str:
    return "non mesurée" if v is None else f"{fr(v * 100, '.0f')} %"


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d/%m/%Y")


def lifecycle(m: ModelManifest, ledger: Ledger,
              h: Optional[Dict[str, Any]] = None) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """Où en est un modèle (§44) : enregistré (sans clé), à évaluer (jamais
    évalué, ou banc raté), approuvé, en service, dégradé ; avec la raison et
    son dernier banc concluant. Seuls les approuvés, en service et dégradés
    (demi-ouvert) peuvent répondre."""
    if not m.configured:
        return "REGISTERED", "non configuré (aucune clé)", None
    bench = ledger.bench_last(m.provider, m.model)
    if bench is None:
        return "TESTING", "jamais évalué : python trendguard_bot.py modeles banc", None
    if not bench["passed"]:
        return "TESTING", (f"banc raté le {_day(bench['at'])} ({bench['note'] or 'justesse ' + _pct(bench['accuracy'])})"
                           " : écarté jusqu'à un banc réussi"), bench
    h = h or ledger.health(m.provider)
    if h["breaker"] != "CLOSED":
        return "DEGRADED", f"disjoncteur {BREAKER_LABELS[h['breaker']]}", bench
    return (("ACTIVE" if h["calls"] else "APPROVED"),
            f"banc réussi le {_day(bench['at'])} (justesse {_pct(bench['accuracy'])})", bench)


def provider_status(state: str, bench: Optional[Dict[str, Any]], configured: bool) -> str:
    """État de fournisseur (§3) tiré du cycle de vie : sans clé DISABLED,
    banc raté BLOCKED, jamais évalué UNKNOWN, disjoncteur DEGRADED, sinon
    ACTIVE."""
    if not configured:
        return "DISABLED"
    if state == "TESTING":
        return "BLOCKED" if bench is not None else "UNKNOWN"
    return "DEGRADED" if state == "DEGRADED" else "ACTIVE"


def routing_score(h: Dict[str, Any], bench: Optional[Dict[str, Any]], policy: str) -> Tuple[float, str]:
    """Note de routage (§8) selon la politique de l'usage, à partir des seules
    mesures réelles : justesse au banc, réussite récente, rapidité ; une
    mesure absente compte pour 0,5 (neutre) et le dit."""
    weights = POLICIES.get(policy, POLICIES["defaut"])
    values = {"quality": bench["accuracy"] if bench else None,
              "reliability": h["health_score"] if h.get("calls") else None,
              "speed": max(0.0, 1 - h["p50_ms"] / SLOW_MS) if h.get("p50_ms") is not None else None}
    score = sum(w * (0.5 if values[k] is None else values[k]) for k, w in weights.items())
    names = {"quality": "justesse au banc", "reliability": "réussite récente", "speed": "rapidité"}
    parts = [f"{names[k]} {_pct(values[k])}" for k, w in weights.items() if w]
    return round(score, 3), f"note {fr(score, '.2f')} ({', '.join(parts)} ; politique {policy})"


@dataclass(frozen=True)
class Route:
    selected: List[str]
    refused: Dict[str, str]
    why: Dict[str, str] = field(default_factory=dict)


def route(models: Sequence[ModelManifest], privacy: str, ledger: Ledger, budget_left: int,
          needs: Sequence[str] = ("texte",), prefer: Sequence[str] = ("claude",), policy: str = "defaut",
          cost_left: Optional[Decimal] = None) -> Route:
    """Modèles candidats, du meilleur au moins bon, et pourquoi les autres
    sont écartés (non configuré, non approuvé au banc, confidentialité,
    capacité, disjoncteur, budgets). Classement : note de la politique de
    l'usage, puis préférence déclarée, puis ordre du registre."""
    ok, refused, why = [], {}, {}
    for order, m in enumerate(models):
        h = ledger.health(m.provider)
        state, reason, bench = lifecycle(m, ledger, h)
        if state in ("REGISTERED", "TESTING"):
            refused[m.provider] = reason
        elif not m.accepts(privacy):
            refused[m.provider] = f"confidentialité {privacy} : fournisseur extérieur interdit"
        elif not set(needs) <= set(m.capabilities):
            refused[m.provider] = "capacité absente"
        elif h["breaker"] == "OPEN":
            refused[m.provider] = "disjoncteur ouvert (échecs répétés)"
        elif m.kind == "CLOUD" and budget_left <= 0:
            refused[m.provider] = "budget de jetons du jour épuisé"
        elif m.kind == "CLOUD" and cost_left is not None and cost_left <= 0:
            refused[m.provider] = "budget de coût du jour épuisé"
        else:
            score, text = routing_score(h, bench, policy)
            rank = list(prefer).index(m.provider) if m.provider in prefer else len(prefer)
            ok.append((-score, rank, order, m.provider))
            why[m.provider] = text + (", préféré" if rank < len(prefer) else "")
    ok.sort()
    for k, x in enumerate(ok, 1):
        why[x[-1]] = f"rang {k} sur {len(ok)} : {why[x[-1]]}"
    return Route([x[-1] for x in ok], refused, why)


class Rejected(Exception):
    """Réponse refusée par la vérification (montant inventé, sortie hors schéma…)."""


@dataclass
class Execution:
    """Résultat d'une demande : la réponse vérifiée, ou MODEL_UNAVAILABLE
    (aucun modèle permis n'a répondu) avec chaque essai et sa raison."""
    ok: bool
    text: str = ""
    provider: str = ""
    model: str = ""
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    tokens: Tuple[Optional[int], Optional[int]] = (None, None)
    request_id: str = ""
    cost_usd: Optional[Decimal] = None
    code: str = ""


def _tokens(raw: Any) -> Tuple[Optional[int], Optional[int]]:
    """Jetons donnés par le fournisseur ; illisibles : inconnus (jamais 0)."""
    vals = (tuple(raw) if isinstance(raw, (tuple, list)) else ()) + (None, None)
    a, b = (t if isinstance(t, int) and not isinstance(t, bool) and t >= 0 else None for t in vals[:2])
    return a, b


def execute(purpose: str, prompt: Tuple[str, str], user_text: str, call: Callable[[ModelManifest], Any],
            env: Optional[Dict[str, str]] = None, ledger: Optional[Ledger] = None,
            base_privacy: str = "PUBLIC", models: Optional[Sequence[ModelManifest]] = None,
            verify: Optional[Callable[[str], str]] = None) -> Execution:
    """Appelle le meilleur modèle permis ; une réponse est vérifiée
    (`verify` rend la raison d'un rejet, ou "") ; en cas d'échec ou de rejet,
    le suivant (repli tracé : modèle d'origine, raison, modèle de repli).
    `prompt` est (identifiant, texte fixe de l'invite) ; `call(manifest)`
    renvoie (texte, (jetons d'entrée, de sortie)). Aucun modèle :
    MODEL_UNAVAILABLE, l'appelant donne sa réponse intégrée (mode dégradé
    sûr) ; jamais un modèle non permis à la place."""
    env = os.environ if env is None else env
    own = ledger is None
    ledger = ledger or Ledger(env.get("TG_LLM_DB") or DEFAULT_DB)
    try:
        try:
            pid = (prompt[0], prompt_label(prompt[0], prompt[1]))
        except ContractError as e:
            return Execution(False, attempts=[{"provider": "", "refused": str(e)}], code=UNAVAILABLE)
        privacy = privacy_class(user_text, base_privacy)
        day0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        budget = int(_env_limit(env, "TG_LLM_JETONS_JOUR", str(DAILY_TOKENS)) or 0) - ledger.tokens_since(day0)
        limit = _env_limit(env, "TG_LLM_BUDGET_USD_JOUR", None)
        cost_left = None if limit is None else limit - (ledger.cost_since(day0) or Decimal(0))
        known = {m.provider: m for m in (registry(env) if models is None else models)}
        r = route(list(known.values()), privacy, ledger, budget, policy=purpose if purpose in POLICIES else "defaut",
                  cost_left=cost_left)
        attempts: List[Dict[str, Any]] = [{"provider": p, "refused": why} for p, why in r.refused.items()
                                          if "non configuré" not in why]
        digest = prompt_version(user_text)
        request_id = contrats.new_id()
        price_table = prices(env)
        previous = ""
        for name in r.selected:
            m = known[name]
            reason = r.why[name] + (f" ; repli après l'échec de {previous}" if previous else "")
            t0 = time.time()
            tokens: Tuple[Optional[int], Optional[int]] = (None, None)
            try:
                text, raw = call(m)
                tokens = _tokens(raw)
                if not (text or "").strip():
                    raise ValueError("réponse vide")
                problem = verify(text) if verify else ""
                if problem:
                    raise Rejected(problem)
            except Exception as e:
                why = f"réponse rejetée : {e}" if isinstance(e, Rejected) else mw.friendly_error(e)
                ledger.record(purpose, name, m.model, pid, privacy, False, int((time.time() - t0) * 1000), why,
                              tokens=tokens, fallback_from=previous, reason=reason, input_hash=digest,
                              request_id=request_id, cost_usd=cost_of(tokens, price_table.get(name)))
                attempts.append({"provider": name, "error": why, "retryable": why in RETRYABLE,
                                 "rejected": isinstance(e, Rejected)})
                previous = name
                continue
            cost = cost_of(tokens, price_table.get(name))
            ledger.record(purpose, name, m.model, pid, privacy, True, int((time.time() - t0) * 1000),
                          tokens=tokens, fallback_from=previous, reason=reason, input_hash=digest,
                          request_id=request_id, cost_usd=cost)
            attempts.append({"provider": name, "ok": True})
            return Execution(True, text, name, m.model, attempts, tokens, request_id, cost)
        return Execution(False, attempts=attempts, request_id=request_id, code=UNAVAILABLE)
    finally:
        if own:
            ledger.close()


# ---------- Vérification des réponses (§58 : chiffres inventés) ----------

_NUM = r"(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,](\d+))?"
AMOUNT_RE = re.compile(_NUM + r"\s*(?:USDT|USDC|USD|\$|€|dollars?|euros?)(?![A-Za-z])", re.I)
NUMBER_RE = re.compile(r"(?<![\w.,])" + _NUM)


def _value(intpart: str, frac: Optional[str]) -> float:
    return float(re.sub(r"\D", "", intpart) + ("." + frac if frac else ""))


def invented_amounts(answer: str, sources: str, tolerance: float = 0.01) -> List[str]:
    """Montants (USDT, $, €…) écrits par une IA mais absents des données
    qu'on lui a données (à 1 % près, arrondi compris) : chiffres inventés.
    Un calcul fait par l'IA compte aussi : le calcul de référence reste celui
    du bot, jamais celui d'un modèle."""
    known = [_value(m.group(1), m.group(2)) for m in NUMBER_RE.finditer(sources or "")]
    out = []
    for m in AMOUNT_RE.finditer(answer or ""):
        v = _value(m.group(1), m.group(2))
        if not any(abs(v - k) <= max(0.01, tolerance * abs(k)) for k in known):
            out.append(m.group(0).strip())
    return out


# ---------- Adaptateurs ----------

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
    """La veille passe par le socle : son invite doit être celle enregistrée ;
    une IA non approuvée au banc ou au disjoncteur ouvert n'est pas appelée ;
    chaque appel est tracé avec la version de l'invite (la veille ne connaît
    pas les jetons : ils restent vides, jamais estimés)."""

    def __init__(self, path: str, env: Optional[Dict[str, str]] = None):
        self.path = path
        self.env = os.environ if env is None else env
        self.request_id = contrats.new_id()
        self._ledger: Optional[Ledger] = None

    def _open(self) -> Ledger:
        if self._ledger is None:
            self._ledger = Ledger(self.path)
        return self._ledger

    def screen(self, providers: List[Tuple[mw.Provider, str, str]]
               ) -> Tuple[List[Tuple[mw.Provider, str, str]], Dict[str, Dict[str, Any]]]:
        """Les IA à interroger, et celles écartées avec leur raison."""
        try:
            prompt_label("veille", mw.SYSTEM)
            blocked = ""
        except ContractError as e:
            blocked = str(e)
        manifests = {m.provider: m for m in registry(self.env)}
        kept, skipped = [], {}
        led = self._open()
        for p, k, model in providers:
            man = manifests.get(p.name)
            h = led.health(p.name)
            state, reason, _b = (lifecycle(dataclasses.replace(man, model=model), led, h) if man
                                 else ("REGISTERED", "inconnue du registre", None))
            why = blocked or (reason if state in ("REGISTERED", "TESTING") else "") or (
                "disjoncteur ouvert (échecs répétés)" if h["breaker"] == "OPEN" else "")
            if why:
                skipped[p.name] = {"ok": False, "model": model, "error": why, "seconds": 0.0}
            else:
                kept.append((p, k, model))
        return kept, skipped

    def record(self, results: Dict[str, Dict[str, Any]]) -> None:
        """Chaque appel de la veille dans la trace (une demande par rapport)."""
        try:
            pid = ("veille", prompt_label("veille", mw.SYSTEM))
        except ContractError:
            pid = ("veille", prompt_version(mw.SYSTEM))
        for name, r in results.items():
            self._open().record("veille", name, r.get("model", ""), pid, "PUBLIC", bool(r.get("ok")),
                                int(float(r.get("seconds") or 0) * 1000),
                                "" if r.get("ok") else (r.get("error") or "échec sans détail"),
                                reason="veille : toutes les IA approuvées, en parallèle, puis consensus"
                                       + (" (réponse réparée une fois)" if r.get("repaired") else ""),
                                request_id=self.request_id)

    def close(self) -> None:
        if self._ledger is not None:
            self._ledger.close()


# ---------- Banc d'évaluation (§38-41 ; §44-46 de la première version) ----------

BENCH_VERSION = "2.0.0"           # nouvelle version : comparée seulement à elle-même
BENCH_MIN = 0.75                  # justesse minimale pour être approuvé
REGRESSION_TOL = 0.25             # baisse tolérée par rapport à la référence
BENCH: Tuple[Tuple[str, str, str], ...] = (
    ("faits", "Quel pourcentage du capital le bot risque-t-il par achat ? Réponds seulement par un nombre.", "1"),
    ("faits", "Sur combien de jours le bot cherche-t-il la cassure du plus haut ? Réponds seulement par un nombre.",
     "30"),
    ("faits", "Le bot peut-il vendre à découvert ? Réponds seulement par oui ou non.", "non"),
    ("faits", "Le bot achète-t-il quand le bitcoin est sous sa moyenne de 150 jours ? Réponds seulement par oui ou "
              "non.", "non"),
    ("finance", "Capital de 10000 USDT, 1 % de risque par achat, stop 10 % sous le prix d'achat : combien d'USDT "
                "investir ? Réponds seulement par un nombre.", "1000"),
    ("finance", "Un prix passe de 50 à 60 : quelle est la hausse en pour cent ? Réponds seulement par un nombre.",
     "20"),
    ("calcul", "Combien font 17 × 23 ? Réponds seulement par un nombre.", "391"),
    ("invention", "Quel était le cours exact du bitcoin hier ? Si les faits ne le disent pas, réponds inconnu.",
     "inconnu"),
)
BENCH_SYSTEM = ("Réponds en un mot, en t'appuyant uniquement sur ces faits : TrendGuard achète des cryptos sur "
                "Binance Spot à la cassure du plus haut de 30 jours, seulement quand le bitcoin est au-dessus de sa "
                "moyenne de 150 jours ; il risque 1 % du capital par achat et ne vend jamais à découvert. Si tu ne "
                "sais pas, réponds « inconnu ».")


def bench_version() -> str:
    """Version du banc : numéro et empreinte des faits et des questions."""
    return f"{BENCH_VERSION}#{prompt_version(BENCH_SYSTEM + ''.join(q + a for _c, q, a in BENCH))}"


def answer_tokens(text: str) -> List[str]:
    """Mots d'une réponse, en minuscules, nombres réunis (« 1 000 » → « 1000 »)."""
    t = re.sub(r"(?<=\d)[\s  .,](?=\d{3}\b)", "", (text or "").lower())
    return re.sub(r"[^0-9a-zà-ü]+", " ", t).split()


def benchmark(call: Callable[[ModelManifest, str, str], Any], models: Sequence[ModelManifest],
              ledger: Optional[Ledger] = None) -> List[Dict[str, Any]]:
    """Banc versionné : questions à réponse connue (faits du bot, finance,
    calcul, piège à invention) posées à chaque modèle ; justesse par
    catégorie, durée, erreurs. Approuvé : justesse d'au moins 75 % sans
    régression de plus de 25 points face à la référence du fournisseur ; un
    banc avec des appels en erreur n'est pas concluant. Appels et résultats
    gardés dans la trace. Aucun modèle : liste vide, aucune mesure inventée."""
    version = bench_version()
    pid = ("banc", prompt_label("banc", BENCH_SYSTEM))
    run_id = contrats.new_id()
    out = []
    for m in models:
        if not m.configured:
            continue
        cats: Dict[str, List[int]] = {}
        ms, errors = [], 0
        for cat, question, expected in BENCH:
            t0 = time.time()
            error, good = "", 0
            try:
                text, _tok = call(m, BENCH_SYSTEM, question)
                good = int(expected in answer_tokens(text))
            except Exception as e:
                error = mw.friendly_error(e)
                errors += 1
            ms.append(int((time.time() - t0) * 1000))
            cats.setdefault(cat, []).append(good)
            if ledger is not None:
                ledger.record("banc", m.provider, m.model, pid, "PUBLIC", not error, ms[-1], error, request_id=run_id)
        acc = round(sum(sum(v) for v in cats.values()) / len(BENCH), 2)
        ref = ledger.bench_reference(m.provider, version) if ledger is not None else None
        note = ""
        if errors:
            note = f"{errors} appel(s) en erreur : banc non concluant, à refaire"
        elif ref is not None and acc < ref["accuracy"] - REGRESSION_TOL:
            note = f"régression : {_pct(ref['accuracy'])} → {_pct(acc)} (référence {ref['model']})"
        elif acc < BENCH_MIN:
            note = f"justesse {_pct(acc)} sous le seuil de {_pct(BENCH_MIN)}"
        row = {"provider": m.provider, "label": m.label, "model": m.model, "model_id": m.model_id, "accuracy": acc,
               "categories": {c: round(sum(v) / len(v), 2) for c, v in cats.items()},
               "p50_ms": int(statistics.median(ms)), "cases": len(BENCH), "errors": errors,
               "passed": not note, "note": note, "version": version}
        if ledger is not None:
            ledger.bench_record(run_id, version, row)
        out.append(row)
    return out


def bench_line(r: Dict[str, Any]) -> str:
    """Le résultat d'un banc en une ligne."""
    cats = ", ".join(f"{c} {_pct(v)}" for c, v in sorted(r["categories"].items()))
    return (f"{r['label']} ({r['model']}) : justesse {_pct(r['accuracy'])} ({cats}), médiane "
            f"{fr(r['p50_ms'] / 1000, '.1f')} s — " + ("approuvé" if r["passed"] else f"NON approuvé : {r['note']}"))


def evaluate_new_key(provider: str, env: Dict[str, str], path: str, out: Any = None) -> int:
    """Banc lancé juste après la saisie d'une clé (gouvernance §45 : aucun
    modèle en service sans évaluation) ; 0 si tous ses modèles sont approuvés."""
    out = out or sys.stdout
    targets = [m for m in bench_targets(env) if m.provider == provider]
    if not targets:
        return 1
    print(f"Banc d'évaluation ({len(BENCH)} questions par modèle)…", file=out)
    led = Ledger(path)
    try:
        rows = benchmark(resolver(env), targets, led)
    finally:
        led.close()
    for r in rows:
        print("  " + bench_line(r), file=out)
    return 0 if rows and all(r["passed"] for r in rows) else 1


def with_model(models: Sequence[ModelManifest], overrides: Dict[str, str]) -> List[ModelManifest]:
    """Le registre avec le modèle réellement employé par un usage (Rachelle
    emploie Claude Sonnet, la veille Claude Opus)."""
    return [dataclasses.replace(m, model=overrides[m.provider]) if m.provider in overrides else m
            for m in models]


# ---------- Statut, mode, fiches, mesures ----------

def status(env: Optional[Dict[str, str]] = None, path: str = DEFAULT_DB) -> List[Dict[str, Any]]:
    """Chaque modèle (celui de Rachelle compris) : configuré ou non, cycle de
    vie, état, santé et banc mesurés, prix déclaré."""
    env = os.environ if env is None else env
    led = Ledger(path, readonly=True) if os.path.exists(path) else Ledger("")
    price_table = prices(env)
    try:
        rows = []
        for m in registry(env) + [m for m in bench_targets(env) if m.label.endswith("(Rachelle)")]:
            h = led.health(m.provider)
            state, reason, bench = lifecycle(m, led, h)
            price = price_table.get(m.provider)
            rows.append({"provider": m.provider, "label": m.label, "kind": m.kind, "model": m.model,
                         "model_id": m.model_id, "host": m.host, "configured": m.configured,
                         "privacy": m.max_privacy, **h, "lifecycle": state, "lifecycle_reason": reason,
                         "status": provider_status(state, bench, m.configured),
                         "bench": None if bench is None else {k: bench[k] for k in (
                             "accuracy", "categories", "at", "passed", "note", "bench_version")},
                         "price": None if price is None else f"{price[0]}/{price[1]}"})
        return rows
    finally:
        led.close()


def mode(rows: List[Dict[str, Any]]) -> str:
    """Mode de fonctionnement (§31) : hybride, extérieur seulement, local
    seulement, ou dégradé sûr (aucun modèle en service)."""
    kinds = {r["kind"] for r in rows if r["configured"] and r["lifecycle"] in ("APPROVED", "ACTIVE", "DEGRADED")}
    if kinds == {"CLOUD", "LOCAL"}:
        return "HYBRID"
    return {"CLOUD": "CLOUD_ONLY", "LOCAL": "LOCAL_ONLY"}.get(next(iter(kinds)), "DEGRADED") if kinds else "DEGRADED"


def describe(rows: List[Dict[str, Any]]) -> str:
    """Les modèles en une phrase : mode, cycle de vie, santé mesurée."""
    conf = [r for r in rows if r["configured"]]
    if not conf:
        return (f"aucune IA configurée ({len(rows)} fournisseurs connus) : la veille lit les annonces "
                "officielles et les mots-clés, Rachelle répond seule")
    parts = []
    for r in conf:
        h = LIFECYCLE_LABELS[r["lifecycle"]]
        if r["calls"]:
            h += f", {r['calls']} appel(s), {r['failures']} échec(s)"
            if r.get("p50_ms") is not None:
                h += f", médiane {fr(r['p50_ms'] / 1000, '.1f')} s"
        if r["breaker"] != "CLOSED":
            h += f", disjoncteur {BREAKER_LABELS[r['breaker']]}"
        parts.append(f"{r['label']} ({r['model']}) : {h}")
    return (f"{len(conf)} IA configurée(s), mode {MODE_LABELS[mode(rows)]} : " + " ; ".join(parts))


def scorecard(row: Dict[str, Any]) -> List[str]:
    """Fiche d'un modèle (§43) : seulement des mesures réelles, « non
    mesurée » sinon."""
    b = row.get("bench")
    cats = (" (" + ", ".join(f"{c} {_pct(v)}" for c, v in sorted(b["categories"].items())) + ")") if b else ""
    lat = ("non mesurée" if row.get("p50_ms") is None else
           f"médiane {fr(row['p50_ms'] / 1000, '.1f')} s, 95e centile {fr(row['p95_ms'] / 1000, '.1f')} s")
    return [f"{row['label']} — {row['model_id']} ({row['kind']}, {LIFECYCLE_LABELS[row['lifecycle']]}, "
            f"état {row['status']})",
            f"  justesse au banc : {_pct(b['accuracy']) if b else 'non mesurée'}{cats}",
            f"  fiabilité : {_pct(row.get('health_score'))} sur {row['calls']} appel(s)",
            f"  latence : {lat}",
            "  coût : " + (f"prix déclaré {row['price']} USD par million de jetons" if row.get("price")
                           else "prix non déclaré (TG_LLM_PRIX_…) : coût inconnu"),
            f"  confidentialité acceptée : {row['privacy']} ; {row['lifecycle_reason']}"]


def main(argv: Optional[List[str]] = None) -> int:
    """Commande `modeles` : statut (défaut : mode, cycle de vie, fiches,
    mesures, derniers appels) ou banc d'évaluation."""
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Modèles d'IA de TrendGuard (registre, santé, banc)")
    ap.add_argument("action", nargs="?", default="statut", choices=["statut", "banc"])
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    path = ledger_path(g.watch_db)
    if args.action == "banc":
        targets = bench_targets()
        if not targets:
            print("Aucun modèle configuré : rien à évaluer (aucune mesure n'est inventée).")
            return 0
        led = Ledger(path)
        try:
            rows = benchmark(resolver(), targets, led)
        finally:
            led.close()
        for r in rows:
            print("  " + bench_line(r))
        return 0 if all(r["passed"] for r in rows) else 1
    rows = status(path=path)
    print(describe(rows))
    for r in rows:
        if r["configured"]:
            print("\n".join(scorecard(r)))
        else:
            print(f"  {r['label']:<13} {r['model']:<24} {r['kind']:<6} non configuré")
    if os.path.exists(path):
        led = Ledger(path, readonly=True)
        try:
            calls, m = led.last(5), led.metrics(time.time() - 86_400)
        finally:
            led.close()
        if m["llm_calls_total"]:
            cost = m["llm_cost_total_usd"]
            print(f"24 dernières heures : {m['llm_requests_total']} demande(s), {m['llm_calls_total']} appel(s), "
                  f"{m['llm_failure_total']} échec(s), {m['llm_fallback_total']} repli(s), "
                  f"{m['llm_rejected_total']} réponse(s) rejetée(s), {m['llm_tokens_total']} jeton(s), coût "
                  + (f"{fr(float(cost), '.4f')} USD" if cost is not None else "inconnu (prix non déclaré)"))
        if calls:
            print("Derniers appels :")
        for c in calls:
            when = datetime.fromtimestamp(c["at"], timezone.utc).strftime("%d/%m %H:%M")
            print(f"  {when} UTC {c['purpose']} → {c['provider']} ({c['model']}) : "
                  + ("réussi" if c["ok"] else f"échec, {c['error']}") + (f" — {c['reason']}" if c["reason"] else ""))
    print("Ajouter une IA : python trendguard_bot.py watch set-key claude (clé saisie masquée, puis banc "
          "d'évaluation) ; modèle local : TG_LLM_LOCAL_URL dans .env, puis python trendguard_bot.py modeles banc.")
    return 0
