"""
Feuille de route et portes de mise en service (cadre de priorités et de
dépendances ; docs/FEUILLE_DE_ROUTE.md) : chaque composant du bot avec sa
priorité (P0 à P4), son état, ses dépendances typées, ses preuves (code,
tests, contrats, documentation, sécurité, observabilité), sa note de santé
des dépendances, et les portes à franchir, jusqu'à la porte du réel.

    fondation → données → intelligence → décision → risque → exécution

Règles :
- un état « en service » ne se déclare pas, il se prouve : fichiers, tests,
  contrats et documentation doivent exister (un test le vérifie) ;
- le graphe des dépendances est sans cycle, et un composant ne dépend jamais
  d'une couche en aval ; dans le code, aucun module d'intelligence ni de
  fondation ne charge un module qui passe des ordres (un test le vérifie) ;
- la porte du réel (porte 8) se mesure sur l'état du bot : tant qu'elle est
  fermée, la porte d'exécution refuse tout achat réel (les ventes restent
  permises) ; aucune option ne la contourne.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import v29

from . import autonomy, contrats
from .texte import fr

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRIORITIES = ("P0", "P1", "P2", "P3", "P4")
STATUSES = ("BACKLOG", "PLANNED", "READY", "IN_PROGRESS", "IMPLEMENTED", "TESTING", "VALIDATING", "APPROVED",
            "DEPLOYED", "MONITORED")
EXCEPTIONAL = ("BLOCKED", "FAILED", "QUARANTINED", "DEPRECATED", "ROLLED_BACK")
SATISFIED = ("APPROVED", "DEPLOYED", "MONITORED")
BUILT = ("IMPLEMENTED", "TESTING", "VALIDATING") + SATISFIED
DEP_TYPES = ("HARD", "SOFT", "RUNTIME", "DATA", "CONTRACT", "SECURITY", "VALIDATION")
DEP_LABELS = {"HARD": "dure", "SOFT": "souple", "RUNTIME": "à l'exécution", "DATA": "données", "CONTRACT": "contrat",
              "SECURITY": "sécurité", "VALIDATION": "validation"}
LAYERS = ("FONDATION", "DONNÉES", "INTELLIGENCE", "DÉCISION", "RISQUE", "EXÉCUTION", "TRANSVERSE")
THRESHOLDS = {"P0": 0.95, "P1": 0.85, "P2": 0.75, "P3": 0.5, "P4": 0.5}
STATUS_LABELS = {"BACKLOG": "à faire", "PLANNED": "prévu", "READY": "prêt à commencer", "IN_PROGRESS": "en cours",
                 "IMPLEMENTED": "écrit", "TESTING": "en essai", "VALIDATING": "en validation",
                 "APPROVED": "approuvé", "DEPLOYED": "en service", "MONITORED": "en service et surveillé",
                 "BLOCKED": "bloqué", "FAILED": "en échec", "QUARANTINED": "mis à l'écart",
                 "DEPRECATED": "abandonné", "ROLLED_BACK": "retiré"}
PAPER_DAYS_MIN = 60               # porte du réel : jours de l'essai paper en cours
PAPER_TRADES_MIN = 10             # porte du réel : trades paper clos
VERIFY_MAX_DAYS = 7               # porte du réel : vérification sans ordre récente
REPORT_MAX_DAYS = 2               # porte du réel : rapport quotidien récent
SECURITY_SECTIONS = ("Sécurité", "Centre de sécurité du panneau")


@dataclass(frozen=True)
class Component:
    """Un composant du bot (§7) : identifiant TASK-, priorité, état, couche,
    dépendances typées, preuves, propriétaire, niveau de risque, critère
    d'acceptation."""
    task_id: str
    name: str
    priority: str
    status: str
    layer: str
    deps: Tuple[Tuple[str, str], ...] = ()
    files: Tuple[str, ...] = ()
    tests: Tuple[str, ...] = ()
    contracts: Tuple[str, ...] = ()
    docs: Tuple[str, ...] = ()
    security: str = ""
    observability: str = ""
    owner: str = "bot"
    risk: str = "MEDIUM"
    acceptance: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"TASK-\d{6}", self.task_id):
            raise contrats.ContractError("INVALID_FIELD", f"{self.task_id} : identifiant TASK-000000 attendu")
        for name, value, allowed in (("priority", self.priority, PRIORITIES),
                                     ("status", self.status, STATUSES + EXCEPTIONAL),
                                     ("layer", self.layer, LAYERS), ("risk", self.risk, contrats.LEVELS4)):
            if value not in allowed:
                raise contrats.ContractError("INVALID_ENUM", f"{self.task_id} {name} : {value!r} inconnu")
        for _target, kind in self.deps:
            if kind not in DEP_TYPES:
                raise contrats.ContractError("INVALID_ENUM", f"{self.task_id} : dépendance {kind!r} inconnue")


def _c(n: int) -> str:
    return f"TASK-{n:06d}"


COMPONENTS: Tuple[Component, ...] = (
    Component(_c(1), "Contrats de données", "P0", "MONITORED", "FONDATION",
              files=("trendguard/contrats.py",), tests=("tests/test_contrats.py", "tests/test_contrats_donnees.py"),
              contracts=("Envelope.v1", "ErrorEnvelope.v2", "Confidence.v1", "OrderIntent.v1"),
              docs=("docs/CONTRATS.md",), security="validateur : champ inconnu, absent, mauvais type ou hors bornes "
              "refusé, jamais corrigé", observability="registre tiré du code, document tenu identique par un test",
              risk="HIGH", acceptance="chaque échange critique a un contrat versionné, ses tests négatifs, sa doc"),
    Component(_c(2), "Base de données et journal financier", "P0", "MONITORED", "FONDATION",
              deps=((_c(1), "CONTRACT"),), files=("trendguard/donnees.py",), tests=("tests/test_donnees.py",),
              contracts=("DecisionRecord.v1", "TradeRecord.v1"), docs=("docs/DONNEES.md",),
              security="tables en ajout seulement ; contraintes dans la base ; un achat sans contrôle refusé",
              observability="lignée de chaque trade ; vérifié chaque nuit ; sauvegarde relue", risk="HIGH",
              acceptance="migrations versionnées et réversibles ; chaque trade remonte à ses données"),
    Component(_c(3), "Sécurité de base", "P0", "MONITORED", "TRANSVERSE", deps=((_c(1), "CONTRACT"),),
              files=("trendguard/report_security.py", "panel/security.py", "trendguard/config.py",
                     "trendguard/cyber.py"),
              tests=("tests/test_report.py", "tests/test_panel.py", "tests/test_cyber.py"),
              contracts=("PanelCommand.v1", "SecurityPostureReport.v1"),
              docs=("docs/RAPPORT.md", "docs/CYBERSECURITE.md"), security="clés saisies masquées ; panneau sur ce PC seulement ; droits de "
              "la clé Binance vérifiés ; réel désarmé par défaut", observability="rapport de sécurité chaque nuit",
              risk="CRITICAL", acceptance="aucun secret dans le dépôt, les journaux ni les réponses"),
    Component(_c(4), "Observabilité et audit", "P0", "MONITORED", "TRANSVERSE",
              deps=((_c(1), "CONTRACT"), (_c(2), "DATA")),
              files=("trendguard/audit.py", "trendguard/report.py", "trendguard/report_health.py"),
              tests=("tests/test_contrats.py", "tests/test_report.py"), contracts=("AuditEvent.v2", "HealthReport.v1"),
              docs=("docs/RAPPORT.md",), security="journal d'audit chaîné : toute modification se voit",
              observability="journal du bot, audit corrélé et causé, rapport quotidien, panneau", risk="HIGH",
              acceptance="chaque opération critique reconstruite : quoi, qui, quand, pourquoi, avec quelle autorisation"),
    Component(_c(5), "Données de marché et qualité", "P0", "MONITORED", "DONNÉES", deps=((_c(1), "CONTRACT"),),
              files=("trendguard/qualite.py", "trendguard/bot.py"),
              tests=("tests/test_analyse.py", "tests/test_contrats_donnees.py"),
              contracts=("MarketData.v1", "OHLCV.v1"), docs=("docs/DONNEES.md",),
              security="bougies incohérentes écartées ; données absentes : décision reportée",
              observability="note de qualité sur 100 à chaque décision", risk="HIGH",
              acceptance="bougies clôturées seulement ; aucune décision sur des données abîmées ou futures"),
    Component(_c(6), "Noyau cognitif", "P1", "MONITORED", "INTELLIGENCE",
              deps=((_c(1), "CONTRACT"), (_c(2), "DATA")),
              files=("trendguard/cognitif.py", "trendguard/expert.py", "trendguard/memoire.py"),
              tests=("tests/test_cognitif.py", "tests/test_memoire.py"),
              contracts=("ExpertDiagnosis.v1", "MemoryHealthReport.v1"), docs=("docs/COGNITIF.md", "docs/MEMOIRE.md"),
              security="outils en lecture seule ; une proposition n'est jamais une autorisation",
              observability="diagnostic expert gardé et résumé chaque nuit",
              acceptance="le bot fait seul son diagnostic expert, propose sans agir"),
    Component(_c(7), "Socle multi-agents et comité", "P1", "MONITORED", "INTELLIGENCE",
              deps=((_c(6), "HARD"), (_c(1), "CONTRACT")), files=("trendguard/agents.py", "trendguard/comite.py"),
              tests=("tests/test_agents.py",), contracts=("CommitteeView.v1", "Confidence.v1"),
              docs=("docs/AGENTS.md", "docs/COMITE_ETUDE.md"),
              security="aucun agent n'accède aux ordres ; quarantaine, disjoncteur",
              observability="avis gardés dans le journal financier, comparés aux trades réels",
              acceptance="onze agents consultatifs, éprouvés sur 8 ans, sans effet sur les décisions"),
    Component(_c(8), "Socle multi-modèles d'IA", "P1", "DEPLOYED", "INTELLIGENCE",
              deps=((_c(6), "HARD"), (_c(7), "SOFT"), (_c(3), "SECURITY")),
              files=("trendguard/modeles.py",), tests=("tests/test_modeles.py",),
              contracts=("LLMExecution.v1", "ModelSelection.v1", "ModelConsensus.v1", "PromptVersion.v1",
                         "ModelBenchmark.v1"), docs=("docs/MODELES.md",),
              security="banc obligatoire, invites versionnées, réponses vérifiées, secrets jamais envoyés",
              observability="chaque appel tracé ; fiches et mesures de chaque modèle",
              acceptance="aucun modèle en service sans banc ; repli tracé ; mode dégradé sûr",
              note="aucune clé d'IA aujourd'hui : en service en mode dégradé sûr"),
    Component(_c(9), "Intelligence financière (savoir, veille, régimes, calendrier)", "P1", "MONITORED",
              "INTELLIGENCE", deps=((_c(5), "DATA"), (_c(8), "SOFT")),
              files=("trendguard/savoir.py", "trendguard/market_watch.py", "trendguard/regimes.py",
                     "trendguard/evenements.py"),
              tests=("tests/test_savoir.py", "tests/test_market_watch.py", "tests/test_analyse.py"),
              contracts=("KnowledgeHold.v1", "AIOpinion.v1", "ModelDisagreement.v1"),
              docs=("docs/SAVOIR.md", "docs/REGIMES.md"),
              security="sources jugées sur les cours réels ; seul un retrait annoncé par Binance bloque un achat",
              observability="noyau de savoir et veille sur la page Veille et dans le rapport",
              acceptance="ne peut que reporter un achat, jamais acheter ni vendre"),
    Component(_c(10), "Moteur quantitatif (laboratoire, risque d'un jour, résistance, attribution)", "P1",
              "DEPLOYED", "DÉCISION", deps=((_c(5), "DATA"), (_c(9), "SOFT")),
              files=("trendguard/risque.py", "trendguard/stress.py", "trendguard/attribution.py",
                     "trendguard/moteur_quant.py"),
              tests=("tests/test_analyse.py", "tests/test_moteur_quant.py"),
              contracts=("PortfolioAnalysis.v1", "QuantResult.v1"),
              docs=("docs/PLATEFORME.md", "docs/MOTEUR_QUANT.md", "docs/QUANT.md"),
              security="mesures seulement : aucune décision, aucun ordre",
              observability="analyse du portefeuille sur le panneau ; rapport du laboratoire reproductible",
              acceptance="chiffres recalculés par le bot, jamais par une IA ; lois éprouvées contre des valeurs "
              "connues ; même graine, même résultat"),
    Component(_c(11), "Stratégie et évolution encadrée", "P1", "MONITORED", "DÉCISION",
              deps=((_c(10), "SOFT"), (_c(9), "SOFT"), (_c(5), "DATA")),
              files=("trendguard/trend_strategy.py", "trendguard/evolution.py", "trendguard/garde.py",
                     "trendguard/moteur_strategie.py"),
              tests=("tests/test_trendguard.py", "tests/test_evolution.py", "tests/test_moteur_strategie.py"),
              contracts=("Signal.v1", "EntryPlan.v1", "EvolutionChange.v1", "StrategySpec.v1",
                         "StrategyDecision.v1"),
              docs=("docs/STRATEGIES.md", "docs/EVOLUTION.md", "docs/MOTEUR_STRATEGIE.md"),
              security="réglages changés seulement après épreuves et essai de 30 jours",
              observability="raisonnement du jour ; registre des expériences", risk="HIGH",
              acceptance="cassure de 30 jours, momentum, régime de BTC ; validée sur deux époques"),
    Component(_c(12), "Rejeu et études (backtest, deux époques, crises)", "P1", "DEPLOYED", "DÉCISION",
              deps=((_c(11), "HARD"), (_c(5), "DATA")),
              files=("trendguard/replay.py", "trendguard/strategy_lab.py", "trendguard/registre.py",
                     "research/robustness.py", "trendguard/moteur_backtest.py"),
              tests=("tests/test_backtest_paper.py", "tests/test_strategy_lab.py", "tests/test_robustness.py",
                     "tests/test_moteur_backtest.py"),
              contracts=("Experiment.v1", "BacktestManifest.v1", "BacktestResult.v1"),
              docs=("docs/ROBUSTESSE.md", "docs/EXAMEN.md", "docs/MOTEUR_BACKTEST.md"),
              security="études hors ligne, sans aucun ordre", observability="études reproductibles, registre",
              acceptance="chaque règle prouvée sur deux époques avant d'entrer dans le bot"),
    Component(_c(13), "Moteur de risque, garde « pas de trade », arrêt d'urgence", "P0", "MONITORED", "RISQUE",
              deps=((_c(11), "HARD"), (_c(10), "SOFT"), (_c(2), "DATA")),
              files=("trendguard/porte.py", "trendguard/garde.py", "trendguard/bot.py", "trendguard/moteur_risque.py"),
              tests=("tests/test_contrats.py", "tests/test_trendguard.py", "tests/test_moteur_risque.py"),
              contracts=("RiskCheck.v1", "NoTradeGate.v1", "KillSwitch.v1", "RiskAssessment.v1"),
              docs=("docs/CONTRATS.md", "docs/MOTEUR_RISQUE.md"),
              security="1 % de risque par achat, plafonds, arrêt d'urgence à −40 %, « pas de trade » valide",
              observability="chaque contrôle dans l'audit et le journal financier", risk="CRITICAL",
              acceptance="aucun achat sans contrôle déterministe approuvé"),
    Component(_c(14), "Portefeuille (taille, plafonds, sélection, moteur de portefeuille)", "P1", "MONITORED",
              "RISQUE", deps=((_c(13), "HARD"), (_c(11), "HARD")),
              files=("trendguard/trend_strategy.py", "trendguard/selection.py", "trendguard/moteur_portefeuille.py"),
              tests=("tests/test_selection.py", "tests/test_trendguard.py", "tests/test_moteur_portefeuille.py"),
              contracts=("EntryPlan.v1", "PortfolioDecision.v1"),
              docs=("docs/SELECTION.md", "docs/MOTEUR_PORTEFEUILLE.md", "docs/PORTEFEUILLE.md"),
              security="taille par le risque, plafonds de positions et de risque cumulé ; le moteur de portefeuille "
              "ne passe aucun ordre et ne rééquilibre rien",
              observability="positions et analyse sur le panneau ; état du portefeuille à chaque décision",
              acceptance="la taille de chaque achat respecte les plafonds ; contrainte impossible expliquée, jamais "
              "relâchée"),
    Component(_c(15), "Paper trading et ses critères d'acceptation", "P1", "MONITORED", "EXÉCUTION",
              deps=((_c(14), "HARD"), (_c(13), "HARD"), (_c(12), "VALIDATION"), (_c(4), "RUNTIME")),
              files=("trendguard/bot.py", "trendguard/bot_execution.py", "trendguard/acceptation.py"),
              tests=("tests/test_trendguard.py", "tests/test_backtest_paper.py", "tests/test_acceptation.py"),
              contracts=("TradeRecord.v1", "OrderIntent.v1", "PaperAcceptanceReport.v1"),
              docs=("docs/TRADING.md", "docs/ACCEPTATION_PAPER.md"),
              security="argent fictif ; même porte d'exécution qu'en réel ; un P0 raté bloque l'acceptation",
              observability="journal, audit, rapport ; verdict d'acceptation chaque nuit",
              acceptance="le bot tourne seul, en paper, avec les mêmes contrôles qu'en réel ; AC-001 à AC-044 "
              "mesurés"),
    Component(_c(16), "Politique, autorisation et porte d'exécution", "P0", "MONITORED", "RISQUE",
              deps=((_c(13), "HARD"), (_c(4), "SECURITY"), (_c(3), "SECURITY")),
              files=("trendguard/porte.py", "trendguard/politique.py", "trendguard/autorisation.py",
                     "trendguard/porte_examen.py"),
              tests=("tests/test_contrats.py", "tests/test_politique.py", "tests/test_autorisation.py",
                     "tests/test_porte_examen.py"),
              contracts=("ExecutionAuthorization.v1", "SafeModeState.v1", "OrderIntent.v1", "PolicyDecision.v1",
                         "AuthorizationDecision.v1", "FinalValidationResult.v1", "GateReadinessReport.v1"),
              docs=("docs/CONTRATS.md", "docs/MOTEUR_POLITIQUE.md", "docs/MOTEUR_AUTORISATION.md",
                    "docs/PORTE_EXECUTION.md"), security="autorisation de 5 minutes liée à un contrôle ; mode sûr ; "
              "réel armé ; porte du réel", observability="autorisation gardée avec l'achat", risk="CRITICAL",
              acceptance="aucun ordre sans autorisation ; aucune IA ni agent sur ce chemin"),
    Component(_c(17), "Connecteur Binance (ordres réels)", "P0", "TESTING", "EXÉCUTION",
              deps=((_c(16), "SECURITY"), (_c(3), "SECURITY")), files=("v29/exchange.py", "v29/execution.py"),
              tests=("tests/test_live_execution.py", "tests/test_fake_binance.py", "tests/test_real_conditions.py"),
              contracts=("Order.v1",), docs=("docs/TRADING.md",),
              security="identifiant client unique ; ordre ambigu : la paire s'arrête ; stop de secours chez Binance",
              observability="chaque ordre dans le journal et l'audit", risk="CRITICAL",
              acceptance="validé par la vérification sans ordre (verify) sur Binance réel",
              note="éprouvé contre un faux Binance et le testnet ; jamais employé en réel"),
    Component(_c(18), "Trading réel", "P0", "BLOCKED", "EXÉCUTION",
              deps=((_c(17), "HARD"), (_c(15), "VALIDATION"), (_c(16), "SECURITY"), (_c(13), "SECURITY"),
                    (_c(12), "VALIDATION"), (_c(4), "RUNTIME")),
              files=("trendguard/bot_execution.py", "trendguard/deploiement.py"),
              tests=("tests/test_live_execution.py", "tests/test_deploiement.py"),
              contracts=("Order.v1", "ExecutionAuthorization.v1", "LiveDeploymentReport.v1"),
              docs=("docs/PLATEFORME.md", "docs/DEPLOIEMENT_REEL.md"),
              security="porte du réel (porte 8) vérifiée par la porte d'exécution avant chaque achat réel",
              observability="audit, journal, rapport", owner="vous", risk="CRITICAL",
              acceptance="porte 8 ouverte : essai paper suffisant, sécurité sans défaut, vérification réussie ; "
              "puis paliers (réel simulé, réel contrôlé plafonné, production limitée), chacun par votre réglage",
              note="bloqué tant que la porte du réel est fermée : python trendguard_bot.py chantiers portes ; "
              "paliers : python trendguard_bot.py deploiement"),
    Component(_c(19), "Recherche autonome", "P2", "DEPLOYED", "INTELLIGENCE",
              deps=((_c(9), "SOFT"),), files=("trendguard/savoir.py", "trendguard/recherche.py"),
              tests=("tests/test_savoir.py", "tests/test_recherche.py"), contracts=("KnowledgeHold.v1", "ResearchReport.v1"),
              docs=("docs/SAVOIR.md", "docs/RECHERCHE.md"),
              security="lecture publique seulement", observability="bilan du savoir chaque nuit",
              acceptance="le bot lit et juge ses sources seul"),
    Component(_c(20), "Auto-évaluation (diagnostic expert, leçons des trades)", "P2", "MONITORED", "TRANSVERSE",
              deps=((_c(6), "HARD"), (_c(4), "DATA")),
              files=("trendguard/expert.py", "trendguard/postmortem.py", "trendguard/diagnostics.py"),
              tests=("tests/test_cognitif.py", "tests/test_diagnostics.py"),
              contracts=("ExpertDiagnosis.v1", "TradeRecord.v1"), docs=("docs/COGNITIF.md",),
              security="lecture seule", observability="rapport quotidien",
              acceptance="chaque nuit, le bot se juge et le dit"),
    Component(_c(21), "Auto-amélioration (évolution encadrée)", "P2", "MONITORED", "DÉCISION",
              deps=((_c(20), "SOFT"), (_c(12), "VALIDATION")),
              files=("trendguard/evolution.py", "trendguard/registre.py", "trendguard/apprentissage.py"),
              tests=("tests/test_evolution.py", "tests/test_apprentissage.py"),
              contracts=("EvolutionChange.v1", "Experiment.v1", "ModelCard.v1", "LearningGovernanceReport.v1"),
              docs=("docs/EVOLUTION.md", "docs/APPRENTISSAGE_CONTINU.md"),
              security="jamais le risque cumulé, les positions, l'arrêt d'urgence ni le réel ; gelée en réel "
              "contrôlé et en production limitée",
              observability="registre des expériences", acceptance="proposer, éprouver, essayer, puis adopter"),
    Component(_c(22), "Panneau de contrôle et Rachelle", "P2", "MONITORED", "TRANSVERSE",
              deps=((_c(3), "SECURITY"), (_c(4), "DATA"), (_c(8), "SOFT")),
              files=("panel/server.py", "panel/assistant.py", "panel/static/app.js", "panel/interface.py"),
              tests=("tests/test_panel.py", "tests/test_assistant.py", "tests/test_interface.py"),
              contracts=("PanelCommand.v1", "InterfaceReadinessReport.v1"),
              docs=("docs/README.md", "docs/INTERFACE.md"), security="mot de passe, ce PC seulement, Rachelle refuse "
              "les secrets ; chaque commande classée, aucune n'exécute un ordre",
              observability="toutes les pages en lecture", owner="vous",
              acceptance="tout se voit et se règle sans ligne de commande"),
    Component(_c(23), "Centre de commande 3D", "P4", "BACKLOG", "TRANSVERSE", deps=((_c(22), "SOFT"),),
              acceptance="vue 3D des agents, modèles et fournisseurs",
              note="non construit : le panneau suffit pour un seul PC ; jamais une dépendance d'un composant critique"),
    Component(_c(24), "Durcissement de production (PC, sauvegardes, reprise)", "P0", "IN_PROGRESS", "TRANSVERSE",
              deps=((_c(3), "SECURITY"), (_c(4), "RUNTIME")),
              files=("trendguard/autonomy.py", "trendguard/maintenance.py", "trendguard/report_security.py",
                     "trendguard/controle.py"),
              tests=("tests/test_autonomy.py", "tests/test_maintenance.py", "tests/test_controle.py"),
              contracts=("HealthReport.v1", "ControlPlaneReport.v1"),
              docs=("docs/RAPPORT.md", "docs/PLAN_DE_CONTROLE.md"),
              security="relance automatique, sauvegarde relue chaque nuit, superviseur borné, plan de contrôle",
              observability="rapport quotidien", owner="vous et le bot", risk="HIGH",
              acceptance="rapport quotidien sans point de sécurité à corriger",
              note="dépend aussi de vous : points « à corriger » du rapport quotidien"),
    Component(_c(25), "Cœur d'intelligence financière", "P1", "MONITORED", "INTELLIGENCE",
              deps=((_c(5), "DATA"), (_c(2), "DATA"), (_c(9), "SOFT"), (_c(7), "SOFT")),
              files=("trendguard/finance.py",), tests=("tests/test_finance.py",),
              contracts=("Instrument.v1", "Feature.v1", "Forecast.v1", "Scenario.v1", "FinancialSignal.v1",
                         "FinancialAnalysis.v1"), docs=("docs/FINANCE.md",),
              security="indicateurs sans regard vers le futur (test, base) ; une analyse n'est jamais une "
              "autorisation", observability="analyses, indicateurs et prévisions au journal ; calibration au rapport",
              acceptance="chaque crypto analysée chaque nuit ; prévisions évaluées à 30 jours ; aucun trade changé"),
)

GATES: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("1", "Fondations", (_c(1), _c(2), _c(3), _c(4))),
    ("2", "Cognition", (_c(6), _c(9), _c(20))),
    ("3", "Multi-agents et multi-modèles", (_c(7), _c(8))),
    ("4", "Intelligence financière", (_c(5), _c(9), _c(25), _c(10))),
    ("5", "Stratégie", (_c(11), _c(12))),
    ("6", "Risque", (_c(13), _c(14), _c(16))),
    ("7", "Paper", (_c(15), _c(4))),
)
NOT_APPLICABLE = {"4": "analyse fondamentale : sans objet (des cryptos n'ont ni bilan ni bénéfice) ; la "
                       "macroéconomie passe par le calendrier des annonces (docs/FINANCE.md)"}


def by_id() -> Dict[str, Component]:
    return {c.task_id: c for c in COMPONENTS}


def problems() -> List[str]:
    """Défauts du registre : dépendance inconnue, cycle, couche aval,
    composant P4 dont dépend un composant critique."""
    comps, out = by_id(), []
    order = {name: k for k, name in enumerate(LAYERS[:-1])}
    for c in COMPONENTS:
        for target, kind in c.deps:
            if target not in comps:
                out.append(f"{c.task_id} : dépendance inconnue {target}")
                continue
            t = comps[target]
            if c.layer in order and t.layer in order and order[t.layer] > order[c.layer]:
                out.append(f"{c.task_id} ({c.layer}) dépend de {target} ({t.layer}), en aval")
            if t.priority == "P4" and c.priority in ("P0", "P1") and kind == "HARD":
                out.append(f"{c.task_id} ({c.priority}) dépend d'un composant P4")
    try:
        topological()
    except ValueError as e:
        out.append(str(e))
    return out


def topological() -> List[str]:
    """Ordre de construction (graphe sans cycle) ; ValueError s'il y a un cycle."""
    comps, done, out = by_id(), set(), []
    visiting: set = set()

    def visit(t: str) -> None:
        if t in done:
            return
        if t in visiting:
            raise ValueError(f"cycle de dépendances autour de {t}")
        visiting.add(t)
        for d, _k in comps[t].deps:
            if d in comps:
                visit(d)
        visiting.discard(t)
        done.add(t)
        out.append(t)
    for c in COMPONENTS:
        visit(c.task_id)
    return out


def dependents(task_id: str) -> List[str]:
    return [c.task_id for c in COMPONENTS if any(d == task_id for d, _k in c.deps)]


def critical_path(target: str = _c(18)) -> List[str]:
    """Chemin critique (§19) : la plus longue chaîne de dépendances jusqu'à
    `target` (par défaut, le trading réel)."""
    comps = by_id()
    best: Dict[str, List[str]] = {}
    for t in topological():
        chains = [best[d] for d, _k in comps[t].deps if d in best]
        best[t] = (max(chains, key=len) if chains else []) + [t]
    return best.get(target, [])


def _exists(path: str) -> bool:
    return (ROOT / path).exists()


def evidence(c: Component) -> Dict[str, Any]:
    """Preuves d'un composant (§16) : ce qui existe vraiment dans le dépôt."""
    known = {k.contract_id for k in contrats.REGISTRY}
    return {"files": [f for f in c.files if not _exists(f)], "tests": [t for t in c.tests if not _exists(t)],
            "contracts": [k for k in c.contracts if k not in known], "docs": [d for d in c.docs if not _exists(d)]}


def health(c: Component) -> Dict[str, Any]:
    """Note de santé des dépendances (§24) : dépendances satisfaites,
    contrats valides, tests, sécurité, observabilité, mesurés dans le dépôt
    et le registre ; prêt si la note atteint le seuil de sa priorité."""
    comps, ev = by_id(), evidence(c)
    critical = c.priority in ("P0", "P1")
    deps = [comps[d].status in SATISFIED for d, _k in c.deps if d in comps]
    parts = {"dependency_completeness": sum(deps) / len(deps) if deps else 1.0,
             "contract_validity": ((len(c.contracts) - len(ev["contracts"])) / len(c.contracts)) if c.contracts
             else (0.0 if critical else 1.0),
             "test_readiness": ((len(c.tests) - len(ev["tests"])) / len(c.tests)) if c.tests else 0.0,
             "security_readiness": 1.0 if c.security else (0.0 if critical else 1.0),
             "observability_readiness": 1.0 if c.observability else (0.0 if critical else 1.0)}
    weights = {"dependency_completeness": 0.30, "contract_validity": 0.20, "test_readiness": 0.20,
               "security_readiness": 0.15, "observability_readiness": 0.15}
    score = round(sum(weights[k] * v for k, v in parts.items()), 3)
    proven = not any(ev.values()) and (not critical or (c.contracts and c.tests and c.security and c.observability))
    blocking = [d for d, k in c.deps if k != "SOFT" and d in comps and comps[d].status not in SATISFIED]
    return {**parts, "score": score, "ready": score >= THRESHOLDS[c.priority] and not blocking,
            "blocking": blocking, "proven": bool(proven),
            "missing": [f"{k} : {', '.join(v)}" for k, v in ev.items() if v]}


def gate_status(gate: Tuple[str, str, Tuple[str, ...]]) -> Dict[str, Any]:
    """Une porte (§25) : franchie si chacun de ses composants est en service,
    prouvé et prêt."""
    comps, items = by_id(), []
    for t in gate[2]:
        c, h = comps[t], health(comps[t])
        ok = c.status in SATISFIED and h["proven"] and h["ready"]
        items.append((c.name, ok, f"{STATUS_LABELS[c.status]}, santé {fr(h['score'] * 100, '.0f')} %"
                      + (f" ; manque {' ; '.join(h['missing'])}" if h["missing"] else "")))
    if gate[0] in NOT_APPLICABLE:
        items.append(("Analyse fondamentale", None, NOT_APPLICABLE[gate[0]]))
    return {"gate": gate[0], "name": gate[1], "ok": all(ok is not False for _n, ok, _d in items), "items": items}


# ---------- Porte du réel (porte 8), mesurée sur l'état du bot ----------

def _paper_sibling(path: str) -> str:
    """Le fichier du paper correspondant à un fichier du réel (base, verrou)."""
    head, tail = os.path.split(path or "")
    return os.path.join(head, tail.replace("_live", "_paper")) if "_live" in tail else path


def _read_state(db_file: str) -> Dict[str, Any]:
    if not db_file or db_file == ":memory:" or not os.path.exists(db_file):
        return {}
    try:
        con = sqlite3.connect(pathlib.Path(os.path.abspath(db_file)).as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            row = con.execute("SELECT value FROM kv WHERE key='trendguard'").fetchone()
        finally:
            con.close()
        return json.loads(row[0]) if row else {}
    except (sqlite3.Error, ValueError):
        return {}


def verification_path(gcfg: Any) -> str:
    """Résultat de la dernière vérification sans ordre (`verify`), à côté du
    verrou du paper (commun au paper et au réel)."""
    return autonomy.sidecar(_paper_sibling(getattr(gcfg, "lock_file", "")), ".verification.json")


def note_verification(gcfg: Any, ok: bool, testnet: bool) -> None:
    """Garde le verdict de `verify` pour la porte du réel."""
    path = verification_path(gcfg)
    if not path:
        return
    try:
        autonomy.write_json(path, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": bool(ok),
                                   "testnet": bool(testnet)})
    except OSError:                  # verdict non gardé : la porte du réel restera fermée
        pass


def _age_days(iso: Any, now: datetime) -> Optional[float]:
    dt = v29._parse_iso(str(iso)) if iso else None
    return None if dt is None else (now - dt).total_seconds() / 86400


def live_gate(gcfg: Any, state: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None,
              env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Porte du réel (§25, porte 8), mesurée : portes 1 à 7, essai paper
    (60 jours, 10 trades clos), stratégie sans réglage à l'essai, arrêt
    d'urgence et mode sûr, journaux intacts, alertes, sécurité du dernier
    rapport, vérification sans ordre récente sur Binance réel. Ouverte
    seulement si tout est conforme ; une mesure impossible la ferme."""
    from . import alerts, audit, donnees, evolution, porte
    now = now or datetime.now(timezone.utc)
    env = os.environ if env is None else env
    live = getattr(gcfg, "run_mode", "paper") == "live"
    items: List[Tuple[str, bool, str]] = []
    closed = [g for g in GATES if not gate_status(g)["ok"]]
    items.append(("Portes 1 à 7", not closed, "franchies" if not closed else
                  "fermées : " + ", ".join(f"{g[0]} ({g[1]})" for g in closed)))
    paper = _read_state(_paper_sibling(gcfg.db_file)) if live else (state if state is not None
                                                                     else _read_state(gcfg.db_file))
    days = _age_days(paper.get("started_at"), now)
    items.append(("Essai paper : durée", days is not None and days >= PAPER_DAYS_MIN,
                  f"{fr(days or 0, '.0f')} jour(s) sur {PAPER_DAYS_MIN} au moins"))
    n = len(paper.get("trades") or [])
    items.append(("Essai paper : trades clos", n >= PAPER_TRADES_MIN, f"{n} sur {PAPER_TRADES_MIN} au moins"))
    ev = evolution.summary(gcfg)
    trial = bool(ev.get("enabled") and (ev.get("probation") or (ev.get("risk") or {}).get("probation")))
    items.append(("Stratégie validée", not trial, "un réglage est à l'essai" if trial else "aucun réglage à l'essai"))
    st = state if state is not None else _read_state(gcfg.db_file)
    safe = porte.safe_mode(gcfg)
    items.append(("Arrêt d'urgence et mode sûr", not st.get("halted") and not safe.active,
                  "arrêt d'urgence déclenché" if st.get("halted") else "mode sûr actif" if safe.active else "prêts"))
    v = audit.verify(audit.path_for(gcfg))
    items.append(("Journal d'audit intact", v["ok"], audit.describe(v)))
    jok, jtext = True, "aucun journal encore"
    if gcfg.db_file and gcfg.db_file != ":memory:" and os.path.exists(gcfg.db_file):
        try:
            j = donnees.Journal(gcfg.db_file, readonly=True)
            try:
                jv = j.verify()
            finally:
                j.close()
            jok, jtext = jv["ok"], donnees.describe(jv)
        except (sqlite3.Error, ValueError) as e:
            jok, jtext = False, f"illisible ({type(e).__name__})"
    items.append(("Journal financier intact", jok, jtext))
    notifier = alerts.build_notifier(None, dict(env))
    items.append(("Alertes configurées", bool(getattr(notifier, "enabled", False)),
                  "au moins un canal" if getattr(notifier, "enabled", False) else
                  "aucun canal : python trendguard_bot.py alerts configurer"))
    rep = {}
    for lock in (gcfg.lock_file, _paper_sibling(gcfg.lock_file)):
        r = autonomy.read_json(autonomy.sidecar(lock, ".rapport.json")) if lock else {}
        if r and str(r.get("day", "")) > str(rep.get("day", "")):
            rep = r
    rdays = _age_days(rep.get("generated_at") or (rep.get("day") and rep["day"] + "T00:30:00+00:00"), now)
    bad = [c.get("label", "?") for s in rep.get("sections") or [] if s.get("title") in SECURITY_SECTIONS
           for c in s.get("checks") or [] if c.get("ok") is False]
    items.append(("Sécurité du dernier rapport", bool(rep) and rdays is not None and rdays <= REPORT_MAX_DAYS
                  and not bad, "aucun rapport récent" if not rep or rdays is None or rdays > REPORT_MAX_DAYS
                  else ("à corriger : " + ", ".join(bad[:4])) if bad else f"du {rep.get('day')}, sans défaut"))
    ver = autonomy.read_json(verification_path(gcfg))
    vdays = _age_days(ver.get("at"), now)
    vok = bool(ver.get("ok")) and not ver.get("testnet") and vdays is not None and vdays <= VERIFY_MAX_DAYS
    items.append(("Vérification sans ordre", vok, "réussie" + f" il y a {fr(vdays, '.0f')} jour(s)"
                  if vok else "à faire ou à refaire : python trendguard_bot.py verify (Binance réel, aucun ordre)"))
    return {"open": all(ok for _l, ok, _d in items), "items": items,
            "missing": [f"{label} : {detail}" for label, ok, detail in items if not ok]}


def summary(gcfg: Any, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Résumé pour le rapport, le panneau et Rachelle."""
    gates = [gate_status(g) for g in GATES]
    try:
        g8 = live_gate(gcfg, state)
    except Exception as e:                  # une mesure impossible ferme la porte
        g8 = {"open": False, "items": [], "missing": [f"évaluation impossible ({type(e).__name__})"]}
    return {"gates": [{"gate": g["gate"], "name": g["name"], "ok": g["ok"]} for g in gates], "live": g8,
            "components": len(COMPONENTS), "problems": problems()}


def describe_live(g8: Dict[str, Any]) -> str:
    """La porte du réel en une phrase."""
    if g8["open"]:
        return "ouverte : toutes les conditions du réel sont réunies"
    total = len(g8["items"]) or 1
    met = sum(1 for _l, ok, _d in g8["items"] if ok)
    return f"fermée : {met} condition(s) sur {total} réunies ; manque " + " ; ".join(g8["missing"][:3])


def render() -> str:
    """docs/FEUILLE_DE_ROUTE.md : composants, dépendances, santé, portes 1 à
    7 (mesurées dans le dépôt), conditions de la porte 8, chemin critique."""
    comps = by_id()
    lines = ["# Feuille de route : priorités, dépendances et portes", "",
             "Tiré du registre `trendguard/chantiers.py` (`python -m trendguard.chantiers` le réécrit) ; un test "
             "vérifie que ce document et le registre restent identiques. État du bot en temps réel : "
             "`python trendguard_bot.py chantiers`.", "",
             "| Tâche | Composant | Priorité | État | Couche | Santé | Prêt | Dépend de |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for c in COMPONENTS:
        h = health(c)
        deps = ", ".join(f"{d[-2:].lstrip('0')} ({DEP_LABELS[k]})" for d, k in c.deps) or "—"
        lines.append(f"| {c.task_id} | {c.name} | {c.priority} | {STATUS_LABELS[c.status]} | {c.layer.lower()} | "
                     f"{fr(h['score'] * 100, '.0f')} % | {'oui' if h['ready'] else 'non'} | {deps} |")
    lines += ["", "## Chemin critique jusqu'au réel", "",
              " → ".join(comps[t].name for t in critical_path()) + ".", "",
              "## Portes", ""]
    for g in GATES:
        s = gate_status(g)
        lines.append(f"- **Porte {s['gate']} ({s['name']})** : {'franchie' if s['ok'] else 'fermée'} — "
                     + " ; ".join(f"{n} {'✓' if ok else '—' if ok is None else '✗'}" for n, ok, _d in s["items"]))
    lines += [f"- **Porte 8 (réel)** : mesurée sur l'état du bot (`python trendguard_bot.py chantiers portes`) : "
              f"portes 1 à 7 ; essai paper d'au moins {PAPER_DAYS_MIN} jours et {PAPER_TRADES_MIN} trades clos ; "
              "aucun réglage à l'essai ; ni arrêt d'urgence ni mode sûr ; journaux d'audit et financier intacts ; "
              f"alertes configurées ; rapport quotidien de moins de {REPORT_MAX_DAYS} jours sans défaut de sécurité ; "
              f"vérification sans ordre réussie sur Binance réel depuis moins de {VERIFY_MAX_DAYS} jours. Fermée, "
              "elle bloque tout achat réel à la porte d'exécution ; aucune option ne la contourne.", "",
              "## Fiches", ""]
    for c in COMPONENTS:
        h = health(c)
        lines += [f"### {c.task_id} — {c.name}", "", "| Rubrique | |", "| --- | --- |",
                  f"| Priorité, état, risque | {c.priority}, {STATUS_LABELS[c.status]}, {c.risk.lower()} |",
                  f"| Propriétaire | {c.owner} |",
                  f"| Dépend de | {', '.join(f'{comps[d].name} ({DEP_LABELS[k]})' for d, k in c.deps) or 'rien'} |",
                  f"| Bloque | {', '.join(comps[t].name for t in dependents(c.task_id)) or 'rien'} |",
                  f"| Code | {', '.join(f'`{x}`' for x in c.files) or '—'} |",
                  f"| Tests | {', '.join(f'`{x}`' for x in c.tests) or '—'} |",
                  f"| Contrats | {', '.join(f'`{x}`' for x in c.contracts) or '—'} |",
                  f"| Documentation | {', '.join(f'[`{x[5:]}`]({x[5:]})' if x.startswith('docs/') else f'`{x}`' for x in c.docs) or '—'} |",
                  f"| Sécurité | {c.security or '—'} |", f"| Observabilité | {c.observability or '—'} |",
                  f"| Acceptation | {c.acceptance or '—'} |",
                  f"| Santé | {fr(h['score'] * 100, '.0f')} % (seuil {fr(THRESHOLDS[c.priority] * 100, '.0f')} %) |"]
        if c.note:
            lines.append(f"| Note | {c.note} |")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `chantiers` : [statut] | portes ; `python -m
    trendguard.chantiers` réécrit docs/FEUILLE_DE_ROUTE.md."""
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Feuille de route et portes de TrendGuard")
    ap.add_argument("action", nargs="?", default="statut", choices=["statut", "portes", "document"])
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    if args.action == "document":
        path = ROOT / "docs" / "FEUILLE_DE_ROUTE.md"
        path.write_text(render(), encoding="utf-8")
        print(f"{path} réécrit ({len(COMPONENTS)} composants)")
        return 0
    g = load_guard_config_from_env()
    if args.action == "statut":
        for c in COMPONENTS:
            h = health(c)
            print(f"  {c.task_id} {c.priority} {STATUS_LABELS[c.status]:<24} santé {fr(h['score'] * 100, '.0f'):>3} % "
                  f"{'prêt' if h['ready'] else 'pas prêt':<8} {c.name}")
        for p in problems():
            print(f"  ⚠️ {p}")
    for g_ in GATES:
        s = gate_status(g_)
        print(f"Porte {s['gate']} ({s['name']}) : {'franchie' if s['ok'] else 'FERMÉE'}")
    g8 = live_gate(g)
    print(f"Porte 8 (réel) : {describe_live(g8)}")
    if args.action == "portes":
        for label, ok, detail in g8["items"]:
            print(f"  {'✓' if ok else '✗'} {label} : {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
