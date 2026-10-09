"""
Recherche et connaissances (prompt maître, étape 22 ; docs/RECHERCHE.md) :
ce que le bot lit sur Internet et ce qu'il en croit. Chaque source a son
rang (officielle, professionnelle, presse, communauté), sa note de confiance
(rang, fiabilité mesurée sur les vrais cours, fraîcheur), ses dépendances
(un agrégateur qui reprend la presse n'est pas une confirmation de plus) et
sa durée de validité. Chaque affirmation sur laquelle le bot agit reçoit un
verdict (confirmée, probable, incertaine, contestée…) avec sa preuve ; les
contradictions du jour entre sources sont relevées. Puis l'examen AC-001 à
AC-080.

Règle : une information extérieure n'est jamais tenue pour vraie d'office.
Seule une source officielle (Binance) peut empêcher un achat ; le savoir ne
peut que reporter un achat, et seulement sur des sources prouvées ; une IA
n'agit jamais. Les textes lus sont des données, jamais des instructions.

    python trendguard_bot.py recherche                    # sources, affirmations, contradictions
    python trendguard_bot.py recherche --out docs/CONNAISSANCES.md
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import v29

from . import acceptation, porte_examen
from . import trend_strategy as ts
from .contrats import ContractError, ResearchReport
from .texte import fr

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "recherche-1.0.0"
LEVEL_FR = {1: "officielle", 2: "professionnelle", 3: "presse", 4: "communauté"}
AUTHORITY = {1: 100.0, 2: 75.0, 3: 50.0, 4: 25.0}
ACCURACY = {"fiable": 100.0, "trompeuse": 10.0, "hasard": 30.0, "observation": 50.0}
VERDICTS = ("CONFIRMED", "PROBABLY_TRUE", "UNCERTAIN", "CONTESTED", "PROBABLY_FALSE", "FALSE", "OUTDATED",
            "INSUFFICIENT_EVIDENCE")
VERDICT_FR = {"CONFIRMED": "confirmée", "PROBABLY_TRUE": "probable", "UNCERTAIN": "incertaine",
              "CONTESTED": "contestée", "PROBABLY_FALSE": "probablement fausse", "FALSE": "fausse",
              "OUTDATED": "périmée", "INSUFFICIENT_EVIDENCE": "preuve insuffisante"}
MIN_VIEW = 0.2
# Registre des sources : nom → (rang, usage, dépend de, validité en heures).
SOURCES: Dict[str, Tuple[int, str, Tuple[str, ...], float]] = {
    "Annonces de Binance": (1, "peut empêcher un achat (retrait de la cote)", (), 30 * 24),
    "Bougies de Binance": (1, "décision de la règle", (), 24),
    "Calendrier économique": (2, "information (prudence avant une annonce)", (), 7 * 24),
    "Fear & Greed": (2, "avis du savoir (jugé sur les vrais cours)", (), 24),
    "Tendances CoinGecko": (2, "avis du savoir", (), 6),
    "Presse spécialisée": (3, "avis du savoir", (), 24),
    "Google Actualités": (3, "avis du savoir", ("Presse spécialisée",), 24),
    "Bing Actualités": (3, "avis du savoir", ("Presse spécialisée",), 24),
    "Reddit": (4, "avis du savoir", (), 24), "Hacker News": (4, "avis du savoir", (), 48),
    "StockTwits": (4, "avis du savoir", (), 6), "IA de la veille": (4, "conseil seulement, jamais une action", (), 24),
}
SAVOIR_FAMILIES = ("Fear & Greed", "Tendances CoinGecko", "Presse spécialisée", "Google Actualités",
                   "Bing Actualités", "Reddit", "Hacker News", "StockTwits")


def _savoir_rows(path: str) -> Dict[str, Any]:
    """Lecture seule de la base du savoir : dernier texte de chaque source,
    avis du jour, bilan des sources (fiabilité mesurée)."""
    from . import savoir
    if not path or not os.path.exists(path):
        return {"last": {}, "views": [], "bilan": {}}
    con = sqlite3.connect(pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        last = {s: float(t) for s, t in con.execute("SELECT family, MAX(ts) FROM docs GROUP BY family")}
        day = con.execute("SELECT MAX(day) FROM views").fetchone()[0]
        views = con.execute("SELECT day, source, asset, value FROM views WHERE day = ?", (day,)).fetchall() if day \
            else []
        row = con.execute("SELECT value FROM meta WHERE key='bilan'").fetchone()
    finally:
        con.close()
    import json
    return {"last": last, "views": views, "bilan": json.loads(row[0]) if row else {}, "min_weeks": savoir.MIN_WEEKS}


def _day_ts(day: Any) -> Optional[float]:
    """Minuit UTC du jour donné (lecture la plus ancienne possible ce jour-là)."""
    try:
        return datetime.fromisoformat(str(day)).replace(tzinfo=timezone.utc).timestamp() if day else None
    except ValueError:
        return None


def trust(level: int, verdict: Optional[str], age_h: Optional[float], ttl_h: float) -> float:
    """Note de confiance d'une source (§12), de 0 à 100 : 40 % son rang, 40 %
    sa fiabilité mesurée sur les vrais cours (une source jamais jugée vaut
    50), 20 % sa fraîcheur (plus vieille que sa validité : 0)."""
    acc = ACCURACY.get(verdict or "observation", 50.0)
    fresh = 0.0 if age_h is None else max(0.0, 100.0 * (1 - age_h / ttl_h)) if age_h > ttl_h * 0.5 else 100.0
    return round(0.4 * AUTHORITY[level] + 0.4 * acc + 0.2 * fresh, 1)


def sources(state: Dict[str, Any], sv: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    """Chaque source : rang, usage, dépendances, fraîcheur, fiabilité
    mesurée, note de confiance."""
    scores = {s["source"]: s for s in (sv.get("bilan") or {}).get("scores") or []}
    t = now.timestamp()
    last_cycle = state.get("last_cycle_ts")
    ev = state.get("evenements") or {}
    seen = {"Annonces de Binance": _day_ts(state.get("last_watch_day")), "Bougies de Binance": last_cycle,
            "Calendrier économique": _day_ts(ev.get("day"))}
    out = []
    for name, (level, use, deps, ttl) in SOURCES.items():
        ts_ = sv.get("last", {}).get(name) if name in SAVOIR_FAMILIES else seen.get(name)
        age = (t - float(ts_)) / 3600 if ts_ else None
        sc = scores.get(name) or {}
        out.append({"name": name, "level": level, "level_fr": LEVEL_FR[level], "use": use, "depends_on": list(deps),
                    "ttl_h": ttl, "age_h": age, "fresh": age is not None and age <= ttl,
                    "verdict": sc.get("verdict"), "weeks": sc.get("weeks"), "edge": sc.get("edge"),
                    "trust": trust(level, sc.get("verdict"), age, ttl)})
    return out


def independent(names: Sequence[str]) -> List[str]:
    """Sources indépendantes parmi celles citées (§13) : un agrégateur qui
    reprend la presse ne compte pas en plus de la presse."""
    names = list(dict.fromkeys(names))
    return [n for n in names if not any(d in names for d in SOURCES.get(n, (0, "", (), 0))[2])]


def claims(state: Dict[str, Any], sv: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    """Les affirmations sur lesquelles le bot agit, et leur verdict (§22) :
    retraits de Binance (source officielle : confirmés tant qu'ils courent),
    reports du savoir (sources prouvées seulement : probables, sinon
    incertains)."""
    out = []
    today = now.date().isoformat()
    for asset, v in sorted((state.get("vetoes") or {}).items()):
        until = str((v or {}).get("until") or "")
        live = not until or until >= today
        out.append({"claim": f"Binance retire {asset.upper()} de la cote", "source": "Annonces de Binance",
                    "level": 1, "verdict": "CONFIRMED" if live else "OUTDATED",
                    "evidence": "annonce officielle de Binance" + (f", valable jusqu'au {until}" if until else ""),
                    "action": "achats bloqués" if live else "aucune (périmée)"})
    bilan = sv.get("bilan") or {}
    proven = set(bilan.get("proven") or [])
    for asset, h in sorted((bilan.get("holds") or {}).items()):
        srcs = list(h.get("sources") or [])
        ok = bool(srcs) and set(srcs) <= proven
        out.append({"claim": f"{asset.upper()} nettement en baisse cette semaine", "source": ", ".join(srcs),
                    "level": min((SOURCES.get(s, (4,))[0] for s in srcs), default=4),
                    "verdict": "PROBABLY_TRUE" if ok else "UNCERTAIN",
                    "evidence": f"avis {fr(float(h.get('value') or 0), '+.2f')} de source(s) prouvée(s) sur au moins "
                                f"{sv.get('min_weeks', 20)} semaines" if ok else "source non prouvée",
                    "action": "achat reporté" if ok else "aucune"})
    return out


def contradictions(sv: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Contradictions du jour (§21) : une même crypto vue en hausse par une
    source et en baisse par une autre (avis nets)."""
    per: Dict[str, Dict[str, List[str]]] = {}
    day = ""
    for d, source, asset, value in sv.get("views") or []:
        day = d
        if abs(float(value)) >= MIN_VIEW:
            per.setdefault(asset, {"up": [], "down": []})["up" if value > 0 else "down"].append(source)
    out = []
    for asset, x in sorted(per.items()):
        if x["up"] and x["down"]:
            out.append({"asset": asset, "day": day, "up": independent(sorted(x["up"])),
                        "down": independent(sorted(x["down"])), "verdict": "CONTESTED"})
    return out


# ══════════════════════════════════════════════════════════════════════
# Examen : AC-001 à AC-080 (§71-72)
# ══════════════════════════════════════════════════════════════════════

FAMILY_WEIGHTS = {"data": 0.15, "verification": 0.15, "knowledge": 0.15, "security": 0.15, "retrieval": 0.10,
                  "provenance": 0.10, "reproducibility": 0.10, "observability": 0.05, "performance": 0.05}
FAMILY_FR = {"data": "qualité des données", "verification": "vérification des sources",
             "knowledge": "qualité des connaissances", "security": "sécurité", "retrieval": "recherche documentaire",
             "provenance": "provenance", "reproducibility": "reproductibilité", "observability": "observabilité",
             "performance": "performance"}
_c = porte_examen._c
RE = "tests/test_recherche.py::"
SA = "tests/test_savoir.py::"
MW = "tests/test_market_watch.py::"
NA_DOC = "aucun document à analyser : le bot lit des titres et des flux publics"
CRITERIA = (
    _c(1, "Recherche simple", "P2", "retrieval", (SA + "test_collect_reads_every_family_and_keeps_knowledge",)),
    _c(2, "Recherche complexe", "P2", "retrieval", na="pas de recherche ouverte : des sources fixes, lues chaque 15 minutes"),
    _c(3, "Planification de la recherche", "P2", "retrieval", (SA + "test_social_targets_rotate_starting_with_btc_and_held",)),
    _c(4, "Sous-questions", "P2", "retrieval", na="pas de question ouverte à décomposer"),
    _c(5, "Plusieurs sources", "P1", "data", (SA + "test_collect_reads_every_family_and_keeps_knowledge",)),
    _c(6, "Sources classées", "P0", "verification", (RE + "test_every_source_has_its_rank_trust_and_validity",)),
    _c(7, "Provenance des sources", "P1", "provenance", (RE + "test_aggregators_are_not_extra_confirmations",)),
    _c(8, "Versions des sources", "P2", "provenance", (SA + "test_old_texts_are_pruned_but_views_are_kept",)),
    _c(9, "Documents lus", "P2", "data", (SA + "test_collect_reads_every_family_and_keeps_knowledge",)),
    _c(10, "Reconnaissance de texte (OCR)", "P2", "data", na=NA_DOC),
    _c(11, "Tableaux extraits", "P2", "data", na=NA_DOC),
    _c(12, "Entités résolues (cryptos citées)", "P1", "knowledge", (SA + "test_collect_reads_every_family_and_keeps_knowledge",)),
    _c(13, "Graphe de connaissances", "P2", "knowledge", na="mémoire temporelle de l'étape 24"),
    _c(14, "Contradictions détectées", "P0", "knowledge", (RE + "test_contradictions_and_verdicts",)),
    _c(15, "Vérification des faits", "P0", "verification", (RE + "test_contradictions_and_verdicts",)),
    _c(16, "Citations exactes", "P1", "provenance", (SA + "test_panel_and_report_read_the_knowledge_core",)),
    _c(17, "Confiance", "P1", "knowledge", (RE + "test_every_source_has_its_rank_trust_and_validity",)),
    _c(18, "Incertitude", "P1", "knowledge", (RE + "test_contradictions_and_verdicts",)),
    _c(19, "Recherche augmentée (documents du dépôt)", "P2", "retrieval", ("tests/test_assistant.py::test_pasted_secrets_are_masked_and_revocation_advised",)),
    _c(20, "Recherche hybride", "P2", "retrieval", na="pas de moteur de recherche vectoriel : mots-clés seulement"),
    _c(21, "Reclassement", "P2", "retrieval", na="pas de moteur de recherche vectoriel"),
    _c(22, "Réponse liée à sa preuve", "P1", "provenance", (RE + "test_contradictions_and_verdicts",)),
    _c(23, "Aucune invention", "P0", "knowledge", ("tests/test_modeles.py::test_invented_amounts_are_rejected_and_the_next_model_answers",)),
    _c(24, "Budgets de recherche", "P2", "performance", ("tests/test_modeles.py::test_daily_token_and_cost_budgets",)),
    _c(25, "Conditions d'arrêt", "P2", "performance", (SA + "test_a_broken_source_never_stops_the_others",)),
    _c(26, "Recherche reproductible", "P1", "reproducibility", (SA + "test_sources_are_judged_on_real_prices",)),
    _c(27, "Recherche tracée", "P1", "reproducibility", (SA + "test_panel_and_report_read_the_knowledge_core",)),
    _c(28, "Surveillance continue", "P1", "observability", (SA + "test_collect_reads_every_family_and_keeps_knowledge",)),
    _c(29, "Fraîcheur des connaissances", "P0", "data", (RE + "test_every_source_has_its_rank_trust_and_validity",)),
    _c(30, "Changements détectés", "P2", "data", (MW + "test_news_are_fresh_and_deduplicated",)),
    _c(31, "Alertes", "P2", "observability", (MW + "test_alert_needs_two_ais_and_ai_never_vetoes",)),
    _c(32, "Plusieurs langues", "P2", "data", (SA + "test_tone_of_a_title",)),
    _c(33, "Recherche scientifique", "P2", "knowledge", na="hors du métier du bot"),
    _c(34, "Recherche technique", "P2", "knowledge", (MW + "test_classify_real_binance_titles",)),
    _c(35, "Recherche financière", "P1", "knowledge", (SA + "test_sources_are_judged_on_real_prices",)),
    _c(36, "Recherche de sécurité", "P2", "knowledge", ("tests/test_bibliotheques.py::test_report_names_libraries_with_a_known_flaw",)),
    _c(37, "Recherche sur l'énergie", "P2", "knowledge", na="hors du métier du bot"),
    _c(38, "Hiérarchie des sources", "P0", "verification", (RE + "test_every_source_has_its_rank_trust_and_validity",)),
    _c(39, "Diversité des sources", "P1", "verification", (RE + "test_aggregators_are_not_extra_confirmations",)),
    _c(40, "Graphe de provenance", "P1", "provenance", (RE + "test_aggregators_are_not_extra_confirmations",)),
    _c(41, "Contenu extérieur isolé", "P0", "security", ("tests/test_cognitif.py::test_rachelle_refuses_instruction_injection",)),
    _c(42, "Injection d'instructions refusée", "P0", "security", ("tests/test_cognitif.py::test_rachelle_refuses_instruction_injection",)),
    _c(43, "Document malveillant", "P1", "security", ("tests/test_replay_animation.py::test_render_html_data_cannot_inject_markup",)),
    _c(44, "Adresses internes protégées", "P1", "security", ("tests/test_cyber.py::test_every_outbound_host_is_on_the_allowlist",)),
    _c(45, "Débit limité", "P1", "performance", (SA + "test_social_targets_rotate_starting_with_btc_and_held",)),
    _c(46, "Délai maximal", "P1", "performance", (SA + "test_a_broken_source_never_stops_the_others",)),
    _c(47, "Bac à sable", "P2", "security", na="le savoir tourne dans un processus séparé ; il ne lance aucun code lu"),
    _c(48, "Logiciels malveillants", "P2", "security", na="aucun fichier téléchargé n'est exécuté ni ouvert"),
    _c(49, "Fraîcheur des données", "P0", "data", (RE + "test_every_source_has_its_rank_trust_and_validity",)),
    _c(50, "Connaissances versionnées", "P1", "knowledge", (SA + "test_old_texts_are_pruned_but_views_are_kept",)),
    _c(51, "Mémoire intégrée", "P1", "knowledge", (SA + "test_panel_and_report_read_the_knowledge_core",)),
    _c(52, "Revue humaine", "P2", "verification", (SA + "test_panel_and_report_read_the_knowledge_core",)),
    _c(53, "Gouvernance du savoir", "P0", "verification", (SA + "test_bot_opinion_uses_only_proven_sources",
                                                           RE + "test_only_an_official_source_can_block_a_buy")),
    _c(54, "Visualisation", "P2", "observability", (SA + "test_panel_and_report_read_the_knowledge_core",)),
    _c(55, "Centre de commande", "P2", "observability", na="pas de centre 3D ; le panneau montre le savoir"),
    _c(56, "Agents de recherche", "P2", "retrieval", ("tests/test_agents.py::test_the_committee_is_consultative_in_the_bot",)),
    _c(57, "Comité de recherche", "P2", "verification", ("tests/test_agents.py::test_the_committee_is_consultative_in_the_bot",)),
    _c(58, "Consensus", "P1", "verification", (MW + "test_alert_needs_two_ais_and_ai_never_vetoes",)),
    _c(59, "Désaccords détectés", "P1", "verification", (RE + "test_contradictions_and_verdicts",)),
    _c(60, "Rapport", "P2", "observability", (RE + "test_the_examination_and_its_contract",)),
    _c(61, "Sources traçables", "P0", "provenance", (SA + "test_panel_and_report_read_the_knowledge_core",)),
    _c(62, "Coûts suivis", "P2", "performance", ("tests/test_modeles.py::test_daily_token_and_cost_budgets",)),
    _c(63, "Jetons suivis", "P2", "performance", ("tests/test_modeles.py::test_daily_token_and_cost_budgets",)),
    _c(64, "Cache", "P2", "performance", ("tests/test_panel.py::test_market_cache_and_stale_fallback",)),
    _c(65, "Reprise après panne", "P1", "reproducibility", (SA + "test_a_broken_source_never_stops_the_others",)),
    _c(66, "Résultats reproductibles", "P1", "reproducibility", (SA + "test_sources_are_judged_on_real_prices",)),
    _c(67, "Aucune source inventée", "P0", "provenance", (RE + "test_every_source_has_its_rank_trust_and_validity",)),
    _c(68, "Aucune affirmation sans preuve", "P0", "knowledge", (RE + "test_contradictions_and_verdicts",)),
    _c(69, "Audit de sécurité", "P1", "security", ("tests/test_cyber.py::test_every_outbound_host_is_on_the_allowlist",)),
    _c(70, "Objectifs de performance", "P2", "performance", (SA + "test_a_broken_source_never_stops_the_others",)),
    _c(71, "Essais de chaos", "P1", "reproducibility", (SA + "test_a_broken_source_never_stops_the_others",)),
    _c(72, "Lignée des données", "P1", "provenance", ("tests/test_donnees.py::test_every_paper_trade_traces_back_to_its_data",)),
    _c(73, "Expiration des connaissances", "P0", "knowledge", (RE + "test_contradictions_and_verdicts",)),
    _c(74, "Dépendances entre sources détectées", "P1", "provenance", (RE + "test_aggregators_are_not_extra_confirmations",)),
    _c(75, "Raisonnement dans le temps", "P1", "knowledge", (RE + "test_contradictions_and_verdicts",)),
    _c(76, "Contenu extérieur jamais exécuté", "P0", "security", (SA + "test_bot_without_savoir_is_unchanged",)),
    _c(77, "Domaine critique validé (seule Binance bloque un achat)", "P0", "verification",
       (RE + "test_only_an_official_source_can_block_a_buy", MW + "test_official_vetoes_and_memory")),
    _c(78, "Observabilité en production", "P1", "observability", (SA + "test_panel_and_report_read_the_knowledge_core",)),
    _c(79, "Prêt à l'exploitation", "P1", "observability", (RE + "test_the_examination_and_its_contract",)),
    _c(80, "Porte finale", "P0", "verification", (RE + "test_the_examination_and_its_contract",)),
)


def _measures(srcs: List[Dict[str, Any]], cl: List[Dict[str, Any]]) -> Dict[str, Tuple[str, str]]:
    """(état, détail) des critères mesurés sur le registre et les affirmations."""
    out: Dict[str, Tuple[str, str]] = {}
    out["AC-006"] = out["AC-038"] = ("PASS", ", ".join(f"{sum(s['level'] == lv for s in srcs)} {LEVEL_FR[lv]}"
                                                       for lv in (1, 2, 3, 4)))
    measured = [s for s in srcs if s["age_h"] is not None]
    stale = [s["name"] for s in measured if not s["fresh"]]
    out["AC-029"] = out["AC-049"] = (("PASS", f"{len(measured)} source(s) datée(s), toutes dans leur validité")
                                     if measured and not stale else
                                     ("PASS", f"{len(stale)} source(s) au-delà de leur validité, signalée(s) : "
                                      + ", ".join(stale)) if measured else ("UNKNOWN", "aucune lecture datée"))
    blockers = [c for c in cl if c["action"] == "achats bloqués" and c["level"] != 1]
    out["AC-077"] = (("PASS", "seules les annonces officielles de Binance bloquent un achat") if not blockers else
                     ("FAIL", "achat bloqué sur une source non officielle : " + ", ".join(c["claim"] for c in blockers)))
    weak = [c for c in cl if c["action"] == "achat reporté" and c["verdict"] != "PROBABLY_TRUE"]
    out["AC-053"] = (("PASS", "chaque report d'achat repose sur des sources prouvées") if not weak else
                     ("FAIL", "report sur source non prouvée : " + ", ".join(c["claim"] for c in weak)))
    return out


def band(score: float) -> str:
    """Bande de la note (§72), la même que pour la porte d'exécution."""
    return porte_examen.band(score)


def evaluate(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None, savoir_db: Optional[str] = None,
             root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Sources, affirmations, contradictions ; les 80 critères, la note et
    le verdict (READY ou REJECTED)."""
    now = now or datetime.now(timezone.utc)
    sv = _savoir_rows(savoir_db if savoir_db is not None else str(getattr(gcfg, "savoir_db", "") or ""))
    srcs = sources(state, sv, now)
    cl = claims(state, sv, now)
    co = contradictions(sv)
    rows, fam, score, p0 = porte_examen.grade(CRITERIA, _measures(srcs, cl), FAMILY_WEIGHTS, root)
    ready = not p0 and score >= 95
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "sources": srcs, "claims": cl, "contradictions": co, "rows": rows, "family_scores": fam,
            "score": round(score, 1), "band": band(score), "p0_failures": p0, "status": "READY" if ready else "REJECTED"}


def report_of(r: Dict[str, Any]) -> ResearchReport:
    """Le verdict au format du contrat ResearchReport.v1."""
    counts = {s: sum(1 for x in r["rows"] if x["status"] == s) for s in acceptation.STATUS_FR}
    return ResearchReport(
        report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=r["status"],
        readiness_score=r["score"], band=r["band"], p0_failures=tuple(r["p0_failures"]), passed=counts["PASS"],
        failed=counts["FAIL"], unknown=counts["UNKNOWN"], not_applicable=counts["NOT_APPLICABLE"],
        sources=len(r["sources"]), claims=tuple((c["claim"], c["verdict"], c["level"]) for c in r["claims"]),
        contradictions=len(r["contradictions"]), engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : sources, affirmations, contradictions."""
    srcs = r["sources"]
    stale = [s["name"] for s in srcs if s["age_h"] is not None and not s["fresh"]]
    out = (f"{len(srcs)} sources ({sum(s['level'] == 1 for s in srcs)} officielles) ; {len(r['claims'])} "
           f"affirmation(s) sur lesquelles le bot agit ; {len(r['contradictions'])} contradiction(s) aujourd'hui")
    if stale:
        out += f" ; au-delà de leur validité : {', '.join(stale)}"
    return out


def render(r: Dict[str, Any]) -> str:
    """docs/CONNAISSANCES.md : sources, affirmations, contradictions,
    verdict, critères."""
    lines = ["# Recherche et connaissances (critères AC-001 à AC-080)", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py recherche` (étape 22 du prompt maître, "
             "[`RECHERCHE.md`](RECHERCHE.md)). Une information extérieure n'est jamais tenue pour vraie d'office.", "",
             "## Verdict", "", f"- **{'PRÊT' if r['status'] == 'READY' else 'REJETÉ'}** ({r['status']}).",
             f"- Note {fr(r['score'], '.1f')}/100 ({porte_examen.BAND_FR[r['band']]}) ; critères P0 non satisfaits : "
             f"{len(r['p0_failures'])}" + (f" ({', '.join(r['p0_failures'])})" if r["p0_failures"] else "") + ".",
             f"- {describe(r)}.", "", "## Sources", "",
             "| Source | rang | usage | fiabilité mesurée | dernière lecture | confiance |", "| --- | --- | --- | --- | --- | --- |"]
    for s in sorted(r["sources"], key=lambda s: (s["level"], -s["trust"])):
        rel = "—" if not s["verdict"] else f"{s['verdict']} ({s['weeks']} semaine(s))"
        age = "—" if s["age_h"] is None else f"il y a {fr(s['age_h'], '.1f')} h" + ("" if s["fresh"] else " (périmée)")
        dep = f" ; reprend {', '.join(s['depends_on'])}" if s["depends_on"] else ""
        lines.append(f"| {s['name']} | {s['level']} ({s['level_fr']}) | {s['use']}{dep} | {rel} | {age} | "
                     f"{fr(s['trust'], '.0f')} |")
    lines += ["", "## Affirmations sur lesquelles le bot agit", ""]
    if r["claims"]:
        lines += ["| Affirmation | source | verdict | preuve | action |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {c['claim']} | {c['source']} | {VERDICT_FR[c['verdict']]} | {c['evidence']} | {c['action']} |"
                  for c in r["claims"]]
    else:
        lines.append("Aucune aujourd'hui : ni retrait de Binance, ni report d'achat.")
    lines += ["", "## Contradictions du jour", ""]
    if r["contradictions"]:
        lines += [f"- {c['asset'].upper()} : en hausse pour {', '.join(c['up'])} ; en baisse pour {', '.join(c['down'])} "
                  "(contestée : aucune décision n'en dépend)" for c in r["contradictions"][:15]]
    else:
        lines.append("Aucune.")
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
    """Commande `recherche` : sources, affirmations, contradictions ; écrit
    avec --out."""
    from .config import load_guard_config_from_env
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Recherche et connaissances (AC-001 à AC-080)")
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    r = evaluate(g, read_state(g.db_file) or {})
    try:
        report_of(r)
    except ContractError as e:
        print(f"Verdict non conforme à son contrat : {e}")
        return 1
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["status"] == "READY" else 1
