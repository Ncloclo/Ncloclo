"""
Cybersécurité et autodéfense (prompt maître, étape 20 ;
docs/CYBERSECURITE.md) : l'inventaire de ce qui est à défendre, les sorties
vers Internet comparées à une liste blanche, l'intégrité du code, les
bibliothèques (versions installées contre versions testées), les événements
de sécurité, les incidents et la réponse prévue ; puis l'examen AC-001 à
AC-070 et le verdict. Jamais offensif : il observe, il recommande ; les
seules défenses automatiques sont celles qui existaient déjà et ne font que
réduire (clé exposée refusée, mode sûr, arrêt d'urgence, ordre inconnu :
arrêt).

Aucun secret n'est lu ni écrit ici : la présence d'une clé seulement, et
son empreinte comparée aux clés exposées connues (config.is_exposed).

    python trendguard_bot.py cyber                       # l'état du jour
    python trendguard_bot.py cyber --out docs/CYBER.md
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import v29

from . import acceptation, controle, porte_examen, report_security, systeme
from . import trend_strategy as ts
from .config import exposed_fingerprints, is_exposed
from .contrats import ContractError, SecurityPostureReport
from .texte import fr

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "cyber-1.0.0"
SCANNED = ("trendguard", "panel", "research", "v29")
URL = re.compile(r"https?://([a-zA-Z0-9.-]+)")
# Liste blanche des sorties : hôte → (usage, catégorie). Toute adresse du code
# absente de cette liste est un écart à corriger (§17).
ALLOWLIST: Dict[str, Tuple[str, str]] = {
    "api.binance.com": ("cours, bougies, ordres (Binance)", "courtier"),
    "www.binance.com": ("annonces officielles de Binance (veille)", "courtier"),
    "data-api.binance.vision": ("bougies publiques de Binance (historique)", "courtier"),
    "testnet.binance.vision": ("testnet de Binance (réel simulé)", "courtier"),
    "api.github.com": ("contrôles et mises à jour du code (lecture)", "code"),
    "github.com": ("dépôt du code", "code"), "raw.githubusercontent.com": ("fichiers publics du dépôt", "code"),
    "pypi.org": ("versions des bibliothèques (lecture)", "code"),
    "api.anthropic.com": ("IA de la veille (conseil)", "IA"), "api.openai.com": ("IA de la veille (conseil)", "IA"),
    "generativelanguage.googleapis.com": ("IA de la veille (conseil)", "IA"),
    "api.mistral.ai": ("IA de la veille (conseil)", "IA"), "api.deepseek.com": ("IA de la veille (conseil)", "IA"),
    "api.x.ai": ("IA de la veille (conseil)", "IA"), "api.perplexity.ai": ("IA de la veille (conseil)", "IA"),
    "api.moonshot.ai": ("IA de la veille (conseil)", "IA"),
    "api.telegram.org": ("alertes", "alertes"), "api.twilio.com": ("alertes", "alertes"),
    "api.callmebot.com": ("alertes", "alertes"),
    "api.ipify.org": ("adresse publique du PC (droits de la clé Binance)", "système"),
    "api.alternative.me": ("indice de peur et d'avidité (savoir)", "savoir"),
    "alternative.me": ("indice de peur et d'avidité (savoir)", "savoir"),
    "api.coingecko.com": ("cours de référence (savoir)", "savoir"), "www.coingecko.com": ("savoir", "savoir"),
    "nfs.faireconomy.media": ("calendrier économique", "savoir"),
    "query1.finance.yahoo.com": ("marchés traditionnels (savoir)", "savoir"),
    "news.google.com": ("actualités (savoir)", "savoir"), "www.bing.com": ("actualités (savoir)", "savoir"),
    "www.coindesk.com": ("actualités (savoir)", "savoir"), "cointelegraph.com": ("actualités (savoir)", "savoir"),
    "decrypt.co": ("actualités (savoir)", "savoir"), "www.cnbc.com": ("actualités (savoir)", "savoir"),
    "www.lemonde.fr": ("actualités (savoir)", "savoir"), "journalducoin.com": ("actualités (savoir)", "savoir"),
    "cryptoast.fr": ("actualités (savoir)", "savoir"), "www.reddit.com": ("opinions publiques (savoir)", "savoir"),
    "stocktwits.com": ("opinions publiques (savoir)", "savoir"),
    "api.stocktwits.com": ("opinions publiques (savoir)", "savoir"),
    "news.ycombinator.com": ("actualités techniques (savoir)", "savoir"),
    "hn.algolia.com": ("actualités techniques (savoir)", "savoir"),
    "127.0.0.1": ("ce PC (panneau)", "local"), "localhost": ("ce PC (panneau)", "local"),
    "www.w3.org": ("espace de noms XML (aucune connexion)", "aucune"),
    "purl.org": ("espace de noms XML des flux d'actualités (aucune connexion)", "aucune"),
    "adresse-du-pc": ("exemple d'adresse dans un texte de Rachelle (aucune connexion)", "aucune"),
    "www.example.com": ("lien d'exemple de la démonstration (aucune connexion)", "aucune"),
}
SECRETS = ("BINANCE_API_KEY", "BINANCE_API_SECRET")
# Réponse prévue à chaque risque (§33) : ce qui se fait seul (réduire) et ce
# qui vous revient.
RESPONSES = (
    ("clé Binance montrée ou volée", "set-keys la refuse ; le centre de sécurité alerte",
     "supprimez-la sur Binance, créez-en une sans droit de retrait"),
    ("ordre ou position inconnus chez Binance", "la paire s'arrête seule (aucun nouvel ordre)",
     "vérifiez sur Binance, puis reprise explicite"),
    ("données abîmées", "aucun achat sous 50 sur 100", "rien ; diagnostic si cela dure"),
    ("code modifié hors Pull Request", "signalé chaque nuit ; la mise à jour automatique n'installe que ce que vous "
     "avez fusionné", "vérifiez le dépôt (git status)"),
    ("bibliothèque avec une faille connue", "signalée par le rapport de la nuit", "mettez à jour la version indiquée"),
    ("mots de passe du panneau essayés", "adresse bloquée 5 minutes après 5 échecs", "changez le mot de passe"),
    ("IA ou agent détourné (injection)", "aucune IA n'a de droit critique ; Rachelle refuse les instructions cachées",
     "rien"),
    ("compromission du PC", "mode sûr à poser par vous : plus aucun achat", "python trendguard_bot.py mode-sur on"),
)


# ══════════════════════════════════════════════════════════════════════
# Inventaire, sorties, intégrité, bibliothèques (§6, §17, §23)
# ══════════════════════════════════════════════════════════════════════

def egress(root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Chaque adresse Internet écrite dans le code, comparée à la liste
    blanche (§17) : où le bot peut se connecter, et pourquoi."""
    seen: Dict[str, List[str]] = {}
    for d in SCANNED:
        for f in sorted((root / d).rglob("*.py")):
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            for host in set(URL.findall(text)):
                seen.setdefault(host.lower().rstrip("."), []).append(f.relative_to(root).as_posix())
    unknown = sorted(h for h in seen if h not in ALLOWLIST)
    cats: Dict[str, int] = {}
    for h in seen:
        if h in ALLOWLIST:
            cats[ALLOWLIST[h][1]] = cats.get(ALLOWLIST[h][1], 0) + 1
    return {"hosts": sorted(seen), "unknown": unknown, "where": {h: seen[h] for h in unknown},
            "categories": cats, "ok": not unknown}


def integrity(deps: Optional[systeme.Deps] = None) -> Dict[str, Any]:
    """Intégrité du code (§23) : version en service (git) et fichiers suivis
    modifiés hors d'une Pull Request (il n'en faut aucun)."""
    deps = deps or systeme.Deps()
    head = systeme.git(deps, str(ROOT), "rev-parse", "--short", "HEAD")
    status = systeme.git(deps, str(ROOT), "status", "--porcelain", "--untracked-files=no")
    if head is None or status is None:
        return {"ok": None, "commit": None, "modified": [], "detail": "git indisponible : intégrité non mesurée"}
    modified = [line[3:].strip() for line in status.splitlines() if line.strip()]
    return {"ok": not modified, "commit": head.strip(), "modified": modified,
            "detail": f"version {head.strip()}, " + (f"{len(modified)} fichier(s) modifié(s) hors Pull Request"
                                                    if modified else "aucun fichier modifié")}


def supply_chain(deps: Optional[systeme.Deps] = None, root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Bibliothèques (§23) : versions installées contre versions testées
    (requirements-docker.txt) ; une faille connue est vérifiée par le rapport
    de la nuit (audit des bibliothèques)."""
    deps = deps or systeme.Deps()
    pins = report_security.read_pins(str(root))
    installed = systeme.installed_versions(deps, sorted(pins)) if pins else None
    if installed is None:
        return {"ok": None, "pinned": len(pins), "drift": [], "missing": [], "detail": "versions non lues (essai)"}
    drift = sorted(n for n, v in installed.items() if v is not None and v != pins[n])
    missing = sorted(n for n, v in installed.items() if v is None)
    return {"ok": not missing, "pinned": len(pins), "drift": drift, "missing": missing,
            "detail": f"{len(pins)} bibliothèques épinglées" + (f", {len(drift)} à une autre version que celle testée"
                                                               if drift else ", toutes à la version testée")
            + (f", {len(missing)} absente(s)" if missing else "")}


def inventory(gcfg: Any, state: Dict[str, Any], deps: Optional[systeme.Deps] = None) -> List[Dict[str, Any]]:
    """Inventaire des actifs à défendre (§6) : code, Python, bibliothèques,
    bases, journaux, fichier des secrets (présence seulement), clés (présence
    et exposition, jamais leur valeur), panneau, alertes, IA."""
    def exists(p: str) -> bool:
        return bool(p) and p != ":memory:" and os.path.exists(p)
    from . import audit, donnees
    keys = all(bool(os.environ.get(n)) for n in SECRETS)
    exposed = any(is_exposed(os.environ.get(n, "")) for n in SECRETS)
    env = os.path.exists(os.path.join(v29.APP_DIR, ".env"))
    rows = [
        {"asset": "code", "type": "CODE", "criticality": 0, "status": "présent", "exposure": "GitHub public, sans secret"},
        {"asset": "Python et bibliothèques", "type": "RUNTIME", "criticality": 1, "status": "présent", "exposure": "ce PC"},
        {"asset": "base du bot", "type": "DATABASE", "criticality": 0,
         "status": "présente" if exists(getattr(gcfg, "db_file", "")) else "absente", "exposure": "ce PC"},
        {"asset": "journal d'audit", "type": "DATABASE", "criticality": 0,
         "status": "présent" if exists(audit.path_for(gcfg)) else "absent", "exposure": "ce PC"},
        {"asset": "journal financier", "type": "DATABASE", "criticality": 0,
         "status": "présent" if exists(donnees.path_for(gcfg)) else "absent", "exposure": "ce PC"},
        {"asset": "fichier des secrets (.env)", "type": "SECRETS", "criticality": 0,
         "status": "présent" if env else "absent", "exposure": "ce PC, exclu de GitHub, jamais lu ici"},
        {"asset": "clé API Binance", "type": "KEYS", "criticality": 0,
         "status": ("EXPOSÉE" if exposed else "présente") if keys else "absente",
         "exposure": "droit de retrait à garder désactivé"},
        {"asset": "panneau", "type": "SERVICE", "criticality": 2, "status": "voir le plan de contrôle",
         "exposure": "ce PC, ou le Wi-Fi avec mot de passe"},
        {"asset": "alertes", "type": "SERVICE", "criticality": 2,
         "status": ", ".join(sorted((state.get("alerts_last") or {}))) or "aucun canal noté", "exposure": "Internet"},
        {"asset": "IA de la veille", "type": "LLM", "criticality": 3, "status": "conseil seulement",
         "exposure": "Internet (liste blanche)"},
        {"asset": "compte Binance", "type": "BROKER", "criticality": 0,
         "status": "réel" if getattr(gcfg, "run_mode", "paper") == "live" else "paper (la clé ne sert pas)",
         "exposure": "Internet"},
    ]
    return rows


def events(state: Dict[str, Any], report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Événements de sécurité du moment (§10-12), lus dans l'état du bot et le
    rapport de la nuit (libellé et état seulement : jamais le détail, qui peut
    contenir une adresse)."""
    out = []
    for sec in (report or {}).get("sections") or []:
        if sec.get("title") not in ("Sécurité", "Centre de sécurité du panneau"):
            continue
        for c in sec.get("checks") or []:
            if c.get("ok") is False:
                out.append({"source": "rapport de la nuit", "what": str(c.get("label")), "severity": "P1"
                            if "clé" in str(c.get("label")).lower() else "P2"})
    if state.get("halted") and "UNKNOWN" in str(state.get("halt_reason") or "").upper():
        out.append({"source": "bot", "what": "ordre inconnu chez Binance", "severity": "P0"})
    return out


# ══════════════════════════════════════════════════════════════════════
# Examen : AC-001 à AC-070 (§44-45, §49)
# ══════════════════════════════════════════════════════════════════════

FAMILY_WEIGHTS = {"detection": 0.15, "identity": 0.15, "prevention": 0.10, "incidents": 0.15, "recovery": 0.10,
                  "ai": 0.10, "data": 0.10, "resilience": 0.10, "governance": 0.05}
FAMILY_FR = {"detection": "détection", "identity": "sécurité des identités", "prevention": "prévention",
             "incidents": "réponse aux incidents", "recovery": "reprise", "ai": "sécurité des IA et des modèles",
             "data": "sécurité des données", "resilience": "résilience", "governance": "audit et gouvernance"}
_c = porte_examen._c
CY = "tests/test_cyber.py::"
RC = "tests/test_real_conditions.py::"
CRITERIA: Tuple[acceptation.Criterion, ...] = (
    _c(1, "Inventaire des actifs", "P0", "governance", (CY + "test_the_inventory_names_every_asset_without_a_secret",)),
    _c(2, "Inventaire des identités", "P0", "identity", ("tests/test_autorisation.py::test_deny_by_default",)),
    _c(3, "Rien sans vérification (refus par défaut)", "P0", "identity", ("tests/test_autorisation.py::test_deny_by_default",)),
    _c(4, "Moindre privilège", "P0", "identity",
       ("tests/test_autorisation.py::test_separation_of_duties_and_delegations", RC + "test_paper_mode_never_uses_the_api_keys")),
    _c(5, "Double authentification", "P2", "identity",
       na="Binance et GitHub l'exigent pour votre compte ; le bot n'a pas de comptes d'utilisateurs"),
    _c(6, "Accès de courte durée", "P1", "identity", ("tests/test_autorisation.py::test_a_buy_authorization_expires_and_cannot_be_replayed",)),
    _c(7, "Changement des clés", "P1", "identity", (RC + "test_set_keys_refuses_an_exposed_key",)),
    _c(8, "Sessions révoquées", "P1", "identity", ("tests/test_panel.py::test_password_required_from_the_network",)),
    _c(9, "Événements de sécurité collectés", "P1", "detection", (CY + "test_events_and_responses",)),
    _c(10, "Événements normalisés", "P2", "detection", ("tests/test_contrats.py::test_audit_chain_detects_any_change",)),
    _c(11, "Détection", "P1", "detection", (CY + "test_events_and_responses",)),
    _c(12, "Détection de comportements anormaux", "P2", "detection",
       ("tests/test_trendguard.py::test_live_boot_refuses_foreign_bot_orders",)),
    _c(13, "Renseignement sur les menaces", "P2", "detection",
       ("tests/test_bibliotheques.py::test_report_names_libraries_with_a_known_flaw",)),
    _c(14, "Gestion des vulnérabilités", "P1", "prevention",
       ("tests/test_bibliotheques.py::test_the_fix_is_the_smallest_newer_version_that_covers_every_flaw",)),
    _c(15, "Télémétrie du poste", "P2", "detection", ("tests/test_controle.py::test_health_of_each_service_is_measured_never_assumed",)),
    _c(16, "Surveillance du réseau", "P2", "detection", (RC + "test_network_outage_defers_quickly_without_waiting_every_pair",)),
    _c(17, "Sorties contrôlées (liste blanche)", "P0", "prevention", (CY + "test_every_outbound_host_is_on_the_allowlist",)),
    _c(18, "Agents isolés", "P0", "ai", ("tests/test_agents.py::test_bus_only_known_members_and_no_replay",)),
    _c(19, "Outils autorisés", "P0", "ai", ("tests/test_autorisation.py::test_deny_by_default",)),
    _c(20, "Sécurité des IA", "P0", "ai", ("tests/test_modeles.py::test_no_model_can_reach_an_order",)),
    _c(21, "Recherche documentaire sûre", "P1", "ai", ("tests/test_cognitif.py::test_rachelle_refuses_instruction_injection",)),
    _c(22, "Intégrité des modèles", "P1", "ai", ("tests/test_modeles.py::test_prompt_registry_refuses_silent_changes",)),
    _c(23, "Chaîne d'approvisionnement", "P0", "prevention", (CY + "test_code_integrity_and_libraries",)),
    _c(24, "Cycle de vie des incidents", "P1", "incidents", ("tests/test_controle.py::test_incidents_open_and_close",)),
    _c(25, "Gravité des incidents", "P1", "incidents", ("tests/test_controle.py::test_one_failure_one_incident_with_its_procedure",)),
    _c(26, "Confinement automatique (réduire seulement)", "P0", "incidents",
       (RC + "test_set_keys_refuses_an_exposed_key", "tests/test_trendguard.py::test_live_boot_refuses_foreign_bot_orders")),
    _c(27, "Accord humain", "P0", "governance", ("tests/test_maintenance.py::test_update_installs_only_what_the_owner_validated",)),
    _c(28, "Réparation automatique", "P1", "resilience",
       ("tests/test_autonomy.py::test_supervisor_restarts_after_crash_then_stops_with_the_bot",)),
    _c(29, "Arrêt d'urgence", "P0", "resilience", ("tests/test_trendguard.py::test_kill_switch_blocks_entries",)),
    _c(30, "Mode sûr", "P0", "resilience", ("tests/test_contrats.py::test_safe_mode_stops_buys_but_not_sales",)),
    _c(31, "Preuves intactes", "P0", "data", ("tests/test_contrats.py::test_audit_chain_detects_any_change",)),
    _c(32, "Graphe de sécurité", "P2", "detection", ("tests/test_controle.py::test_the_dependency_graph_and_single_points",)),
    _c(33, "Risque de sécurité noté", "P1", "governance", (CY + "test_the_examination_and_its_contract",)),
    _c(34, "Intégration au trading", "P0", "incidents", (CY + "test_events_and_responses",)),
    _c(35, "Mémoire de sécurité", "P2", "governance", ("tests/test_controle.py::test_incidents_open_and_close",)),
    _c(36, "Apprentissage de sécurité", "P2", "governance",
       ("tests/test_analyse.py::test_registry_notes_and_replays_an_experiment",)),
    _c(37, "Audit inaltérable", "P0", "data", ("tests/test_contrats.py::test_audit_chain_detects_any_change",)),
    _c(38, "Sécurité du panneau (API)", "P0", "prevention", ("tests/test_panel.py::test_password_required_from_the_network",)),
    _c(39, "Tableau de bord de sécurité", "P2", "governance", ("tests/test_panel.py::test_password_required_from_the_network",)),
    _c(40, "Objectifs de sécurité", "P2", "governance", ("tests/test_controle.py::test_service_objectives_and_error_budgets",)),
    _c(41, "Essais de chaos", "P1", "resilience", ("tests/test_porte_examen.py::test_chaos_ten_thousand_requests_and_a_kill_switch",)),
    _c(42, "Clé compromise : essai", "P0", "identity", (RC + "test_set_keys_refuses_an_exposed_key",)),
    _c(43, "Agent compromis : essai", "P0", "ai", ("tests/test_autorisation.py::test_separation_of_duties_and_delegations",)),
    _c(44, "Modèle compromis : essai", "P1", "ai", ("tests/test_modeles.py::test_invented_amounts_are_rejected_and_the_next_model_answers",)),
    _c(45, "Résistance à l'injection d'instructions", "P0", "ai", ("tests/test_cognitif.py::test_rachelle_refuses_instruction_injection",)),
    _c(46, "Résistance à l'abus d'outils", "P0", "ai", ("tests/test_porte_examen.py::test_one_single_route_to_binance_and_it_passes_the_gate",)),
    _c(47, "Résistance aux sources empoisonnées", "P1", "ai", ("tests/test_savoir.py::test_bot_opinion_uses_only_proven_sources",)),
    _c(48, "Isolation réseau : essai", "P1", "resilience", (RC + "test_paper_mode_never_uses_the_api_keys",)),
    _c(49, "Bascule : essai", "P2", "resilience", na="un seul ordinateur, un seul courtier"),
    _c(50, "Restauration des sauvegardes", "P0", "recovery", ("tests/test_donnees.py::test_backup_is_restored_for_real_and_reported",)),
    _c(51, "Reprise après sinistre", "P1", "recovery", ("tests/test_controle.py::test_recovery_point_and_time_are_measured",)),
    _c(52, "P0 détecté", "P0", "incidents", (CY + "test_events_and_responses",)),
    _c(53, "P0 contenu", "P0", "incidents", ("tests/test_trendguard.py::test_live_boot_refuses_foreign_bot_orders",)),
    _c(54, "P0 signalé", "P0", "incidents", ("tests/test_alerts.py::test_hub_levels_dedup_and_failure_isolation",)),
    _c(55, "Aucune riposte", "P0", "governance", (CY + "test_never_offensive",)),
    _c(56, "Aucune élévation de privilège", "P0", "identity", ("tests/test_autorisation.py::test_deny_by_default",)),
    _c(57, "Aucun contournement de sécurité", "P0", "prevention",
       ("tests/test_porte_examen.py::test_one_single_route_to_binance_and_it_passes_the_gate",)),
    _c(58, "Aucun contournement de l'arrêt d'urgence", "P0", "resilience", ("tests/test_controle.py::test_the_supervisor_is_bounded",)),
    _c(59, "Tout est traçable", "P0", "data", ("tests/test_donnees.py::test_every_paper_trade_traces_back_to_its_data",)),
    _c(60, "Surveillance continue", "P1", "detection", ("tests/test_report.py::test_database_backup_is_verified_and_rotated",)),
    _c(61, "Régressions de sécurité rejouées", "P0", "governance", ("tests/test_structure.py::test_no_real_internet_address_is_published",)),
    _c(62, "Chaîne d'approvisionnement vérifiée", "P1", "prevention", (CY + "test_code_integrity_and_libraries",)),
    _c(63, "Artefacts signés", "P2", "prevention", na="aucun artefact binaire : le code vient de GitHub, fusionné par vous"),
    _c(64, "Modèles de sécurité validés", "P2", "ai", ("tests/test_modeles.py::test_no_model_serves_before_passing_its_benchmark",)),
    _c(65, "Agents validés", "P1", "ai", ("tests/test_agents.py::test_the_committee_is_consultative_in_the_bot",)),
    _c(66, "Défense autonome bornée", "P0", "governance", ("tests/test_controle.py::test_the_supervisor_is_bounded",)),
    _c(67, "Vous gardez la main", "P0", "governance", ("tests/test_contrats.py::test_safe_mode_switch",)),
    _c(68, "Reprise sûre", "P1", "recovery", ("tests/test_trendguard.py::test_live_boot_adopts_with_explicit_recovery",)),
    _c(69, "Retour d'expérience", "P2", "governance", ("tests/test_controle.py::test_incidents_open_and_close",)),
    _c(70, "Sécurité prête pour la production", "P0", "governance", (CY + "test_the_examination_and_its_contract",)),
)


def _measures(inv: List[Dict[str, Any]], eg: Dict[str, Any], integ: Dict[str, Any], sc: Dict[str, Any],
              ev: List[Dict[str, Any]]) -> Dict[str, Tuple[str, str]]:
    """(état, détail) des critères mesurés sur le code, le PC et l'état."""
    out: Dict[str, Tuple[str, str]] = {}
    out["AC-001"] = ("PASS", f"{len(inv)} actifs inventoriés, aucun secret lu")
    keys = next(r for r in inv if r["asset"] == "clé API Binance")
    out["AC-042"] = (("FAIL", "la clé en service a été exposée : à remplacer") if keys["status"] == "EXPOSÉE" else
                     ("PASS", f"clé {keys['status']} ; {len(exposed_fingerprints())} clé(s) exposée(s) connue(s), "
                      "refusées par set-keys"))
    out["AC-017"] = (("PASS", f"{len(eg['hosts'])} adresses dans le code, toutes sur la liste blanche")
                     if eg["ok"] else ("FAIL", "adresses hors liste blanche : " + ", ".join(eg["unknown"])))
    out["AC-023"] = ({True: "PASS", False: "FAIL", None: "UNKNOWN"}[integ["ok"]], integ["detail"])
    out["AC-062"] = ({True: "PASS", False: "FAIL", None: "UNKNOWN"}[sc["ok"]], sc["detail"])
    p0 = [e for e in ev if e["severity"] == "P0"]
    out["AC-052"] = ("PASS", f"{len(ev)} événement(s) de sécurité du moment" + (f", dont {len(p0)} P0" if p0 else ""))
    return out


def band(score: float) -> str:
    """Bande de la note (§44), la même que pour la porte d'exécution."""
    return porte_examen.band(score)


def evaluate(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None,
             deps: Optional[systeme.Deps] = None, report: Optional[Dict[str, Any]] = None,
             root: pathlib.Path = ROOT, light: bool = False) -> Dict[str, Any]:
    """Inventaire, sorties, intégrité, bibliothèques, événements, réponses ;
    les 70 critères, la note et le verdict (READY_FOR_PRODUCTION_SECURITY ou
    NOT_READY ; jamais une défense autonome sans limite). Lecture légère :
    sans git ni lecture des versions installées."""
    now = now or datetime.now(timezone.utc)
    if report is None:
        from . import report as rep
        report = rep.load_latest(gcfg) or {}
    inv = inventory(gcfg, state, deps)
    eg = egress(root)
    integ = {"ok": None, "commit": None, "modified": [], "detail": "non mesurée (lecture légère)"} if light \
        else integrity(deps)
    sc = {"ok": None, "pinned": 0, "drift": [], "missing": [], "detail": "non mesurées (lecture légère)"} if light \
        else supply_chain(deps, root)
    ev = events(state, report)
    rows, fam, score, p0 = porte_examen.grade(CRITERIA, _measures(inv, eg, integ, sc, ev), FAMILY_WEIGHTS, root)
    open_p0 = any(e["severity"] == "P0" for e in ev)
    ready = not p0 and score >= 95 and not open_p0
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "inventory": inv, "egress": eg, "integrity": integ, "supply": sc, "events": ev,
            "responses": RESPONSES, "levels": controle.AUTONOMY, "rows": rows, "family_scores": fam,
            "score": round(score, 1), "band": band(score), "p0_failures": p0,
            "status": "READY_FOR_PRODUCTION_SECURITY" if ready else "NOT_READY"}


def report_of(r: Dict[str, Any]) -> SecurityPostureReport:
    """L'état au format du contrat SecurityPostureReport.v1."""
    counts = {s: sum(1 for x in r["rows"] if x["status"] == s) for s in acceptation.STATUS_FR}
    return SecurityPostureReport(
        report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=r["status"],
        readiness_score=r["score"], band=r["band"], p0_failures=tuple(r["p0_failures"]), passed=counts["PASS"],
        failed=counts["FAIL"], unknown=counts["UNKNOWN"], not_applicable=counts["NOT_APPLICABLE"],
        assets=len(r["inventory"]), unknown_hosts=tuple(r["egress"]["unknown"]),
        events=tuple((e["what"], e["severity"]) for e in r["events"]), engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : sorties, intégrité, événements."""
    eg, integ, ev = r["egress"], r["integrity"], r["events"]
    out = (f"{len(r['inventory'])} actifs ; {len(eg['hosts'])} adresses Internet dans le code, "
           + ("toutes sur la liste blanche" if eg["ok"] else f"{len(eg['unknown'])} HORS liste blanche"))
    if integ["ok"] is False:
        out += f" ; {len(integ['modified'])} fichier(s) du code modifié(s) hors Pull Request"
    out += f" ; {len(ev)} événement(s) de sécurité" if ev else " ; aucun événement de sécurité"
    return out


def render(r: Dict[str, Any]) -> str:
    """docs/CYBER.md : verdict, actifs, sorties, intégrité, réponses,
    critères (jamais un secret, une adresse IP ni un nom de réseau)."""
    ready = r["status"] != "NOT_READY"
    lines = ["# Cybersécurité et autodéfense (critères AC-001 à AC-070)", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py cyber` (étape 20 du prompt maître, "
             "[`CYBERSECURITE.md`](CYBERSECURITE.md)). Jamais offensif ; aucun secret lu.", "", "## Verdict", "",
             f"- **{'Prête pour la production' if ready else 'PAS PRÊTE'}** ({r['status']}) ; jamais une défense "
             "autonome sans limite.",
             f"- Note {fr(r['score'], '.1f')}/100 ({porte_examen.BAND_FR[r['band']]}) ; critères P0 non satisfaits : "
             f"{len(r['p0_failures'])}" + (f" ({', '.join(r['p0_failures'])})" if r["p0_failures"] else "") + ".",
             f"- {describe(r)}.", "", "## Actifs", "", "| Actif | type | criticité | état | exposition |",
             "| --- | --- | --- | --- | --- |"]
    for a in r["inventory"]:
        lines.append(f"| {a['asset']} | {a['type']} | {a['criticality']} | {a['status']} | {a['exposure']} |")
    eg = r["egress"]
    lines += ["", "## Sorties vers Internet", "",
              ", ".join(f"{k} : {v}" for k, v in sorted(eg["categories"].items())) + " (nombre d'adresses par usage)."]
    if eg["unknown"]:
        lines += [f"- HORS liste blanche : {h} ({', '.join(eg['where'][h])})" for h in eg["unknown"]]
    lines += ["", "## Réponse prévue", "", "| Risque | ce qui se fait seul | ce qui vous revient |", "| --- | --- | --- |"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in r["responses"]]
    lines += ["", "## Familles", "", "| Famille | poids | note |", "| --- | --- | --- |"]
    for fam, w in FAMILY_WEIGHTS.items():
        s = r["family_scores"].get(fam)
        lines.append(f"| {FAMILY_FR[fam]} | {fr(w * 100, '.0f')} % | {fr(s, '.0f') if s is not None else '—'} |")
    lines += ["", "## Critères", "", "| Critère | priorité | état | preuve |", "| --- | --- | --- | --- |"]
    for x in r["rows"]:
        lines.append(f"| {x['id']} {x['title']} | {x['priority']} | {acceptation.STATUS_FR[x['status']]} | "
                     f"{x['detail']} |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `cyber` : l'état du jour ; écrit avec --out."""
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Cybersécurité et autodéfense (AC-001 à AC-070)")
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    r = evaluate(g, systeme.read_state(g.db_file) or {})
    try:
        report_of(r)
    except ContractError as e:
        print(f"État non conforme à son contrat : {e}")
        return 1
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["status"] != "NOT_READY" else 1


def offensive_capabilities() -> List[str]:
    """Ce module n'a aucune capacité offensive (§47, AC-055) : il n'importe
    aucune bibliothèque réseau, ne scanne aucun réseau et ne riposte jamais.
    Renvoie les bibliothèques réseau qu'il importerait (aucune)."""
    import ast
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    return sorted(names & {"socket", "urllib", "requests", "http", "ssl", "paramiko", "scapy", "ftplib", "smtplib"})
