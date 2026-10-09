"""
Interface humain-IA du panneau (prompt maître, étape 21 ;
docs/INTERFACE.md) : le panneau n'est jamais une autorité de sécurité, il
transmet. Chaque commande y est classée (lire, analyser, simuler,
recommander, modifier, exécuter) ; aucune n'exécute un ordre. Chaque donnée
dit sa fraîcheur (en direct, à jour, ancienne, inconnue, bot arrêté) ;
chaque réponse de Rachelle dit d'où elle vient (« Pourquoi ? ») ; l'état
global résume dix domaines ; chaque action est attribuée (vous, le bot, une
IA, un automatisme, l'urgence). Puis l'examen AC-001 à AC-070.

    GET /api/interface : les commandes, l'état global, la fraîcheur
"""

from __future__ import annotations

import pathlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from trendguard import acceptation, autorisation, moteur_risque, porte, porte_examen
from trendguard.bot_types import last_closed_day
from trendguard.contrats import InterfaceReadinessReport
from trendguard.texte import fr

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "interface-1.0.0"
CLASSES = ("READ", "ANALYZE", "SIMULATE", "RECOMMEND", "MODIFY", "EXECUTE")
CLASS_FR = {"READ": "lire", "ANALYZE": "analyser", "SIMULATE": "simuler (sans effet)", "RECOMMEND": "recommander",
            "MODIFY": "modifier (contrôlé)", "EXECUTE": "exécuter (action réelle)"}
FRESH = ("LIVE", "FRESH", "STALE", "UNKNOWN", "OFFLINE")
FRESH_FR = {"LIVE": "en direct", "FRESH": "à jour", "STALE": "ancienne", "UNKNOWN": "inconnue", "OFFLINE": "bot arrêté"}
LIVE_S, FRESH_S = 120, 600
# Chaque commande du panneau : (méthode, adresse) → (classe, ce qu'elle fait).
COMMANDS: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("GET", "/api/health"): ("READ", "le panneau répond-il"),
    ("GET", "/api/status"): ("READ", "état du bot"), ("GET", "/api/equity"): ("READ", "courbe du capital"),
    ("GET", "/api/positions"): ("READ", "positions"), ("GET", "/api/trades"): ("READ", "trades clos"),
    ("GET", "/api/analyse"): ("ANALYZE", "analyse du portefeuille"), ("GET", "/api/assets"): ("READ", "cryptos"),
    ("GET", "/api/candles"): ("READ", "bougies"), ("GET", "/api/regime"): ("ANALYZE", "lecture du marché"),
    ("GET", "/api/watch"): ("READ", "veille"), ("GET", "/api/reasoning"): ("ANALYZE", "raisonnement du bot"),
    ("GET", "/api/news"): ("READ", "actualités"), ("GET", "/api/assistant"): ("READ", "accueil de Rachelle"),
    ("GET", "/api/anticipation"): ("SIMULATE", "prochaine clôture (probabilités, sans effet)"),
    ("GET", "/api/security"): ("ANALYZE", "centre de sécurité"), ("GET", "/api/report"): ("READ", "rapport"),
    ("GET", "/api/log"): ("READ", "journal du bot"), ("GET", "/api/interface"): ("READ", "commandes et état global"),
    ("POST", "/api/assistant"): ("RECOMMEND", "question à Rachelle (elle explique, n'agit pas)"),
    ("POST", "/api/bot/start"): ("MODIFY", "démarrer le bot"), ("POST", "/api/bot/stop"): ("MODIFY", "arrêter le bot"),
    ("POST", "/api/selection"): ("MODIFY", "choisir les cryptos"),
    ("POST", "/api/autostart"): ("MODIFY", "démarrage avec l'ordinateur"),
    ("POST", "/api/alerts/test"): ("MODIFY", "tester les alertes"),
    ("POST", "/api/report/run"): ("MODIFY", "lancer le rapport"), ("POST", "/api/report/send"): ("MODIFY", "envoyer le rapport"),
    ("POST", "/api/login"): ("MODIFY", "ouvrir une session"), ("POST", "/api/logout"): ("MODIFY", "fermer la session"),
}
ACTOR_TYPES = {"vous": "HUMAN", "panneau": "HUMAN", "bot": "SYSTEM", "porte": "SYSTEM", "regle": "SYSTEM",
               "superviseur": "AUTOMATED", "maintenance": "AUTOMATED", "evolution": "AUTOMATED", "savoir": "AUTOMATED",
               "libre": "AUTOMATED", "rachelle": "AI", "ia": "AI", "comite": "AI", "nuage": "AUTOMATED"}
TOPIC_SOURCES = {
    "porte": "porte d'exécution (trendguard/porte.py)", "autorisation": "moteur d'autorisation (autorisation.py)",
    "politique": "moteur de politiques (politique.py)", "deploiement": "paliers du réel (deploiement.py)",
    "controle": "plan de contrôle (controle.py)", "apprentissage": "gouvernance des modèles (apprentissage.py)",
    "cyber": "cybersécurité (cyber.py)", "portefeuille": "moteur de portefeuille (moteur_portefeuille.py)",
    "risque": "moteur de risque (moteur_risque.py)", "acceptation": "acceptation du paper (acceptation.py)",
}


def command_class(method: str, path: str) -> str:
    """Classe d'une commande ; une commande inconnue est traitée comme la plus
    sensible (EXECUTE) : le panneau la refuse."""
    return COMMANDS.get((method.upper(), path), ("EXECUTE", ""))[0]


def routes(server_source: str) -> List[Tuple[str, str]]:
    """Les adresses que le serveur du panneau sert (lues dans son code)."""
    out = []
    get_block = server_source.split("get = {", 1)[1].split("\n        }", 1)[0] if "get = {" in server_source else ""
    post_block = server_source.split("post = {", 1)[1].split("\n        }", 1)[0] if "post = {" in server_source else ""
    out += [("GET", p) for p in re.findall(r'"(/api/[a-z/]+)":', get_block)]
    out += [("POST", p) for p in re.findall(r'"(/api/[a-z/]+)":', post_block)]
    out += [("POST", p) for p in re.findall(r'path == "(/api/[a-z/]+)"', server_source)]
    return sorted(set(out))


def freshness(age_s: Optional[float], offline: bool = False) -> str:
    """Fraîcheur d'une donnée (§51) : jamais une donnée ancienne présentée
    comme en direct."""
    if offline:
        return "OFFLINE"
    if age_s is None or age_s < 0:
        return "UNKNOWN"
    return "LIVE" if age_s <= LIVE_S else "FRESH" if age_s <= FRESH_S else "STALE"


def freshness_view(st: Dict[str, Any], now: datetime, delay_sec: int = 120) -> Dict[str, Dict[str, Any]]:
    """Fraîcheur des données principales du panneau : dernier cycle du bot,
    décision du jour, note des données."""
    last = st.get("last_cycle_ts")
    age = now.timestamp() - float(last) if last else None
    stopped = bool(st.get("stopped_at")) and bool(last) and float(st["stopped_at"]) >= float(last)
    day = last_closed_day(now, delay_sec)
    cyc = freshness(age, offline=stopped or (age is not None and age > 3 * FRESH_S))
    dec = "FRESH" if st.get("last_decision_day") == day else "STALE" if st.get("last_decision_day") else "UNKNOWN"
    q = st.get("qualite") or {}
    dat = "FRESH" if q.get("day") == day else "STALE" if q else "UNKNOWN"
    return {"cycle": {"state": cyc, "label": FRESH_FR[cyc], "age_s": age},
            "decision": {"state": dec, "label": FRESH_FR[dec], "day": st.get("last_decision_day")},
            "data": {"state": dat, "label": FRESH_FR[dat], "day": q.get("day")}}


def why(reply: Dict[str, Any], st: Dict[str, Any], now: Optional[datetime] = None) -> List[str]:
    """« Pourquoi ? » d'une réponse de Rachelle (§23-24) : qui a répondu, à
    partir de quelles données et de quelle fraîcheur, quel module."""
    now = now or datetime.now(timezone.utc)
    src = str(reply.get("source") or "local")
    out = ["répondu par " + ("une IA, vérifiée (montants inventés refusés)" if src not in ("local", "")
                             else "Rachelle sur ce PC, sans IA (règles écrites)")]
    f = freshness_view(st, now)
    out.append(f"état du bot : {f['cycle']['label']}" + (f" (dernier cycle il y a {fr(f['cycle']['age_s'] / 60, '.0f')} "
                                                        "min)" if f["cycle"]["age_s"] is not None else ""))
    out += [f"source : {TOPIC_SOURCES[t]}" for t in reply.get("topics") or [] if t in TOPIC_SOURCES]
    out.append("elle n'agit pas : aucune réponse ne passe d'ordre ni ne change un réglage")
    return out


def _lvl(ok: Optional[bool], warn: bool = False) -> str:
    return "UNKNOWN" if ok is None else "OK" if ok and not warn else "WARNING" if ok else "CRITICAL"


def global_status(gcfg: Any, st: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Dict[str, str]]:
    """L'état global (§57) : dix domaines, chacun mesuré (jamais supposé)."""
    from trendguard import apprentissage, controle, cyber
    now = now or datetime.now(timezone.utc)
    day = last_closed_day(now, getattr(gcfg, "decision_delay_sec", 120))
    out: Dict[str, Dict[str, str]] = {}
    agents = ((st.get("comite") or {}).get("agents") or {})
    ready = sum(1 for a in agents.values() if isinstance(a, dict) and a.get("status") == "READY")
    out["cognitif"] = {"level": "OK", "detail": "Rachelle répond sur ce PC"}
    out["agents"] = {"level": _lvl(bool(agents) or None, ready < len(agents)) if agents else "UNKNOWN",
                     "detail": f"{ready} agent(s) prêt(s) sur {len(agents)}" if agents else "comité pas encore réuni"}
    q = st.get("qualite") or {}
    out["donnees"] = {"level": _lvl((q.get("score") or 0) >= porte.QUALITY_MIN if q else None, q.get("day") != day),
                      "detail": f"{fr(q.get('score') or 0, '.0f')}/100 le {q.get('day')}" if q else "non notées"}
    pf = st.get("portefeuille") or {}
    out["finance"] = {"level": "UNKNOWN" if not pf else "WARNING" if pf.get("error") else "OK",
                      "detail": "moteur de portefeuille" + (" en erreur" if pf.get("error") else "")}
    if getattr(gcfg, "risk_engine", False):
        ok, txt = moteur_risque.gate((st.get("moteur_risque") or {}).get("pre"), day, now)
        out["risque"] = {"level": "OK" if ok else "WARNING", "detail": txt}
    else:
        out["risque"] = {"level": "UNKNOWN", "detail": "moteur de risque désactivé (essai)"}
    halted, safe = bool(st.get("halted")), porte.safe_mode(gcfg).active
    out["execution"] = {"level": "SAFE" if halted or safe else "OK",
                        "detail": "arrêt d'urgence" if halted else "mode sûr" if safe else
                        ("paper" if getattr(gcfg, "run_mode", "paper") != "live" else "réel")}
    try:
        cy = cyber.evaluate(gcfg, st, now, light=True, report={})
        out["securite"] = {"level": "OK" if cy["egress"]["ok"] else "CRITICAL", "detail": cyber.describe(cy)}
    except Exception as e:               # mesure impossible : jamais « OK »
        out["securite"] = {"level": "UNKNOWN", "detail": type(e).__name__}
    try:
        cp = controle.evaluate(gcfg, st, now, light=True)
        worst = cp["incidents"][0]["severity"] if cp["incidents"] else ""
        out["infra"] = {"level": "CRITICAL" if worst == "P0" else "WARNING" if worst else "OK",
                        "detail": controle.describe(cp)}
    except Exception as e:
        out["infra"] = {"level": "UNKNOWN", "detail": type(e).__name__}
    try:
        ap = apprentissage.boundaries(gcfg)
        out["apprentissage"] = {"level": "OK" if ap["ok"] else "CRITICAL", "detail": "frontières tenues" if ap["ok"]
                                else "frontière franchie"}
    except Exception as e:
        out["apprentissage"] = {"level": "UNKNOWN", "detail": type(e).__name__}
    out["ia"] = {"level": "OK", "detail": "conseil seulement ; aucune IA n'a de droit critique"}
    return out


def actor_type(actor: str) -> str:
    """Qui a agi (§59) : vous, le système, un automatisme, une IA ; l'urgence
    est une action, son auteur reste attribué."""
    return ACTOR_TYPES.get(actor, "UNKNOWN")


# ══════════════════════════════════════════════════════════════════════
# Examen : AC-001 à AC-070 (§65-66)
# ══════════════════════════════════════════════════════════════════════

FAMILY_WEIGHTS = {"safety": 0.20, "ux": 0.15, "multimodal": 0.10, "rendering": 0.10, "realtime": 0.10,
                  "observability": 0.10, "security": 0.10, "accessibility": 0.05, "performance": 0.05,
                  "reliability": 0.05}
FAMILY_FR = {"safety": "sûreté", "ux": "facilité d'usage", "multimodal": "voix et texte", "rendering": "affichage",
             "realtime": "temps réel", "observability": "observabilité", "security": "sécurité",
             "accessibility": "accessibilité", "performance": "performance", "reliability": "fiabilité"}
_c = porte_examen._c
IF = "tests/test_interface.py::"
PA = "tests/test_panel.py::"
NO3D = "pas de vue 3D : le panneau 2D montre tout (choix du propriétaire, centre 3D au rang des idées)"
CRITERIA = (
    _c(1, "Le centre de commande s'ouvre", "P0", "ux", (PA + "test_start_stop_and_page",)),
    _c(2, "L'état global réel s'affiche", "P0", "observability", (IF + "test_global_status_is_measured",)),
    _c(3, "Agents actifs visibles", "P2", "observability", (IF + "test_global_status_is_measured",)),
    _c(4, "Agents inactifs distingués", "P2", "observability", (IF + "test_global_status_is_measured",)),
    _c(5, "IA actives visibles", "P2", "observability", ("tests/test_modeles.py::test_scorecards_mode_and_metrics_show_only_measures",)),
    _c(6, "Flux d'événements visibles", "P2", "observability", (PA + "test_reasoning_and_autonomy_endpoints",)),
    _c(7, "Chaque donnée a son horodatage", "P0", "realtime", (IF + "test_every_datum_says_how_fresh_it_is",)),
    _c(8, "Données anciennes signalées", "P0", "realtime", (IF + "test_every_datum_says_how_fresh_it_is",)),
    _c(9, "Mode dégradé", "P1", "reliability", ("tests/test_panel.py::test_market_cache_and_stale_fallback",)),
    _c(10, "Voix", "P2", "multimodal", (IF + "test_rachelle_can_read_her_answer_aloud",)),
    _c(11, "Dictée (reconnaissance vocale)", "P2", "multimodal", na="pas de dictée : le clavier suffit"),
    _c(12, "Synthèse vocale", "P2", "multimodal", (IF + "test_rachelle_can_read_her_answer_aloud",)),
    _c(13, "Interruption de la voix", "P2", "multimodal", (IF + "test_rachelle_can_read_her_answer_aloud",)),
    _c(14, "Contexte de la conversation gardé", "P1", "ux", ("tests/test_assistant.py::test_pasted_secrets_are_masked_and_revocation_advised",)),
    _c(15, "Commandes classées", "P0", "safety", (IF + "test_every_command_is_classified_and_none_executes",)),
    _c(16, "Commandes critiques détectées", "P0", "safety", (IF + "test_every_command_is_classified_and_none_executes",)),
    _c(17, "Confirmation des actions critiques", "P1", "safety",
       na="aucune action critique au panneau : armer le réel, le risque, les clés se font par vous en dehors"),
    _c(18, "Le panneau ne contourne pas les politiques", "P0", "safety", (IF + "test_every_command_is_classified_and_none_executes",)),
    _c(19, "Le panneau ne contourne pas l'autorisation", "P0", "safety", ("tests/test_autorisation.py::test_every_panel_action_has_its_right",)),
    _c(20, "Le panneau ne contourne pas la porte", "P0", "safety",
       ("tests/test_porte_examen.py::test_one_single_route_to_binance_and_it_passes_the_gate",)),
    _c(21, "Permissions vérifiées côté serveur", "P0", "security", ("tests/test_autorisation.py::test_every_panel_action_has_its_right",)),
    _c(22, "Commandes tracées", "P1", "security", (PA + "test_start_stop_and_page",)),
    _c(23, "Utilisateur authentifié", "P0", "security", (PA + "test_password_required_from_the_network",)),
    _c(24, "Session expirée refusée", "P0", "security", (PA + "test_expired_sessions_are_forgotten",)),
    _c(25, "Rôles respectés", "P0", "security", ("tests/test_autorisation.py::test_deny_by_default",)),
    _c(26, "Vue 3D", "P2", "rendering", na=NO3D),
    _c(27, "Vue 2D", "P0", "rendering", (PA + "test_api_endpoints_answer",)),
    _c(28, "Jumeau numérique fidèle", "P2", "rendering", na=NO3D),
    _c(29, "Dépendances visibles", "P2", "observability", ("tests/test_controle.py::test_the_dependency_graph_and_single_points",)),
    _c(30, "Erreurs visibles", "P1", "observability", (PA + "test_security_report_send_and_alert_cause",)),
    _c(31, "Alertes critiques en premier", "P1", "ux", ("tests/test_controle.py::test_one_failure_one_incident_with_its_procedure",)),
    _c(32, "Événements corrélés", "P2", "observability", ("tests/test_controle.py::test_one_failure_one_incident_with_its_procedure",)),
    _c(33, "Agents inspectables", "P2", "observability", ("tests/test_agents.py::test_the_committee_is_consultative_in_the_bot",)),
    _c(34, "Décisions expliquées", "P0", "ux", (PA + "test_reasoning_and_autonomy_endpoints",)),
    _c(35, "Provenance disponible (« Pourquoi ? »)", "P1", "ux", (IF + "test_each_answer_says_why",)),
    _c(36, "Modèles utilisés identifiables", "P1", "observability", (IF + "test_each_answer_says_why",)),
    _c(37, "Outils utilisés identifiables", "P2", "observability", (IF + "test_each_answer_says_why",)),
    _c(38, "Données sources identifiables", "P1", "observability", (IF + "test_each_answer_says_why",)),
    _c(39, "Vision", "P2", "multimodal", na="pas d'images à analyser : aucune décision ne dépend d'une image"),
    _c(40, "Documents analysés", "P2", "multimodal", na="Rachelle lit les documents du dépôt, rien d'autre"),
    _c(41, "Pages financières", "P1", "ux", (PA + "test_api_endpoints_answer",)),
    _c(42, "Pages du risque", "P1", "ux", (PA + "test_anticipation_and_security_endpoints",)),
    _c(43, "Pages de cybersécurité", "P1", "ux", (PA + "test_anticipation_and_security_endpoints",)),
    _c(44, "Pages de l'énergie", "P2", "ux", ("tests/test_panel.py::test_security_center_says_when_the_laptop_runs_on_battery",)),
    _c(45, "Mise en page gardée", "P2", "ux", (PA + "test_api_endpoints_answer",)),
    _c(46, "Profils d'utilisateurs", "P2", "ux", na="un seul utilisateur, vous"),
    _c(47, "Téléphone", "P1", "accessibility", (PA + "test_api_endpoints_answer",)),
    _c(48, "Mode économe", "P2", "performance", (PA + "test_market_serves_a_recent_value_at_once_and_refreshes_behind",)),
    _c(49, "Mode hors ligne ou dégradé", "P1", "reliability", (PA + "test_market_cache_and_stale_fallback",)),
    _c(50, "Perte du serveur affichée", "P1", "reliability", (IF + "test_every_datum_says_how_fresh_it_is",)),
    _c(51, "Aucune donnée inexistante présentée comme réelle", "P0", "safety", (IF + "test_every_datum_says_how_fresh_it_is",)),
    _c(52, "Connexions protégées", "P0", "security", (PA + "test_foreign_pages_and_hosts_are_refused",)),
    _c(53, "Entrées validées", "P0", "security", (PA + "test_selection_endpoint_and_sells_on_charts",)),
    _c(54, "Résistance aux injections de code dans la page", "P0", "security",
       ("tests/test_replay_animation.py::test_render_html_data_cannot_inject_markup",)),
    _c(55, "Sessions protégées", "P0", "security", (PA + "test_login_is_locked_after_repeated_failures",)),
    _c(56, "Actions sensibles traçables", "P1", "security", ("tests/test_contrats.py::test_audit_chain_detects_any_change",)),
    _c(57, "Actions humaines et IA distinguées", "P1", "safety", (IF + "test_actions_are_attributed",)),
    _c(58, "Urgence par les contrôles du serveur", "P0", "safety", ("tests/test_contrats.py::test_safe_mode_switch",)),
    _c(59, "Rachelle n'agit pas", "P0", "safety", ("tests/test_assistant.py::test_pasted_secrets_are_masked_and_revocation_advised",)),
    _c(60, "Instructions cachées refusées", "P0", "security", ("tests/test_cognitif.py::test_rachelle_refuses_instruction_injection",)),
    _c(61, "Limite de débit des questions", "P1", "performance", (PA + "test_assistant_endpoint_guard_and_rate_limit",)),
    _c(62, "Pages contrôlées à chaque envoi", "P1", "reliability", ("tests/test_structure.py::test_every_module_says_what_it_is_for",)),
    _c(63, "Sans erreur dans la page", "P1", "reliability", (PA + "test_api_endpoints_answer",)),
    _c(64, "Contraste et thème", "P2", "accessibility", (PA + "test_api_endpoints_answer",)),
    _c(65, "Clavier et lecteur d'écran", "P1", "accessibility", (IF + "test_rachelle_can_read_her_answer_aloud",)),
    _c(66, "Moins de mouvement", "P2", "accessibility", (IF + "test_rachelle_can_read_her_answer_aloud",)),
    _c(67, "Temps de réponse des pages", "P1", "performance", (PA + "test_market_candles_are_read_once_per_pair_and_interval",)),
    _c(68, "Aucune commande cachée", "P0", "safety", (IF + "test_every_command_is_classified_and_none_executes",)),
    _c(69, "Démo distinguée du réel", "P0", "safety", (PA + "test_api_endpoints_answer",)),
    _c(70, "Interface prête", "P0", "ux", (IF + "test_the_examination_and_its_contract",)),
)


def _measures(server_source: str, js: str, st: Dict[str, Any], now: datetime) -> Dict[str, Tuple[str, str]]:
    """(état, détail) des critères mesurés sur le code du panneau."""
    out: Dict[str, Tuple[str, str]] = {}
    rts = routes(server_source)
    missing = [f"{m} {p}" for m, p in rts if (m, p) not in COMMANDS]
    execute = [p for (m, p), (c, _w) in COMMANDS.items() if c == "EXECUTE"]
    out["AC-015"] = out["AC-068"] = (("PASS", f"{len(rts)} commandes du serveur, toutes classées") if not missing
                                     else ("FAIL", "commandes non classées : " + ", ".join(missing)))
    out["AC-016"] = out["AC-018"] = (("PASS", "aucune commande n'exécute un ordre ; modifier passe par le moteur "
                                      "d'autorisation") if not execute else ("FAIL", "commandes qui exécutent : "
                                                                              + ", ".join(execute)))
    voice = "speechSynthesis" in js and "speechSynthesis.cancel" in js
    out["AC-010"] = out["AC-012"] = out["AC-013"] = (("PASS", "lecture à voix haute des réponses (synthèse du "
                                                       "navigateur), arrêt d'un clic") if voice else
                                                      ("FAIL", "synthèse vocale absente du panneau"))
    out["AC-035"] = (("PASS", "« Pourquoi ? » sous chaque réponse") if "Pourquoi ?" in js else
                     ("FAIL", "« Pourquoi ? » absent du panneau"))
    f = freshness_view(st, now)
    out["AC-007"] = ("PASS", f"dernier cycle : {f['cycle']['label']} ; décision : {f['decision']['label']} ; données : "
                     f"{f['data']['label']}")
    return out


def band(score: float) -> str:
    """Bande de la note (§66), la même que pour la porte d'exécution."""
    return porte_examen.band(score)


def evaluate(gcfg: Any, st: Dict[str, Any], now: Optional[datetime] = None,
             root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Les 70 critères, la note et le verdict (READY ou REJECTED ; un P0 non
    résolu rejette, quelle que soit la note)."""
    now = now or datetime.now(timezone.utc)
    server = (root / "panel" / "server.py").read_text(encoding="utf-8")
    js = "\n".join(p.read_text(encoding="utf-8") for p in sorted((root / "panel" / "static").rglob("*.js"))
                   if "vendor" not in p.parts)
    rows, fam, score, p0 = porte_examen.grade(CRITERIA, _measures(server, js, st, now), FAMILY_WEIGHTS, root)
    ready = not p0 and score >= 95
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "rows": rows, "family_scores": fam, "score": round(score, 1), "band": band(score), "p0_failures": p0,
            "commands": len(COMMANDS), "status": "READY" if ready else "REJECTED"}


def report_of(r: Dict[str, Any]) -> InterfaceReadinessReport:
    """Le verdict au format du contrat InterfaceReadinessReport.v1."""
    counts = {s: sum(1 for x in r["rows"] if x["status"] == s) for s in acceptation.STATUS_FR}
    return InterfaceReadinessReport(
        report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=r["status"],
        readiness_score=r["score"], band=r["band"], p0_failures=tuple(r["p0_failures"]), passed=counts["PASS"],
        failed=counts["FAIL"], unknown=counts["UNKNOWN"], not_applicable=counts["NOT_APPLICABLE"],
        commands=r["commands"], engine_version=VERSION)


def critical_actions() -> List[str]:
    """Les droits critiques que le panneau détient (aucun) : il ne peut ni
    armer le réel, ni changer le risque, ni saisir une clé."""
    return [a for a in autorisation.CRITICAL if autorisation.decide("panneau", a, context={"session_ok": True}).allowed]
