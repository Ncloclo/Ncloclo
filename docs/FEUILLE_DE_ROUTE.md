# Feuille de route : priorités, dépendances et portes

Tiré du registre `trendguard/chantiers.py` (`python -m trendguard.chantiers` le réécrit) ; un test vérifie que ce document et le registre restent identiques. État du bot en temps réel : `python trendguard_bot.py chantiers`.

| Tâche | Composant | Priorité | État | Couche | Santé | Prêt | Dépend de |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TASK-000001 | Contrats de données | P0 | en service et surveillé | fondation | 100 % | oui | — |
| TASK-000002 | Base de données et journal financier | P0 | en service et surveillé | fondation | 100 % | oui | 1 (contrat) |
| TASK-000003 | Sécurité de base | P0 | en service et surveillé | transverse | 100 % | oui | 1 (contrat) |
| TASK-000004 | Observabilité et audit | P0 | en service et surveillé | transverse | 100 % | oui | 1 (contrat), 2 (données) |
| TASK-000005 | Données de marché et qualité | P0 | en service et surveillé | données | 100 % | oui | 1 (contrat) |
| TASK-000006 | Noyau cognitif | P1 | en service et surveillé | intelligence | 100 % | oui | 1 (contrat), 2 (données) |
| TASK-000007 | Socle multi-agents et comité | P1 | en service et surveillé | intelligence | 100 % | oui | 6 (dure), 1 (contrat) |
| TASK-000008 | Socle multi-modèles d'IA | P1 | en service | intelligence | 100 % | oui | 6 (dure), 7 (souple), 3 (sécurité) |
| TASK-000009 | Intelligence financière (savoir, veille, régimes, calendrier) | P1 | en service et surveillé | intelligence | 100 % | oui | 5 (données), 8 (souple) |
| TASK-000010 | Moteur quantitatif (laboratoire, risque d'un jour, résistance, attribution) | P1 | en service | décision | 100 % | oui | 5 (données), 9 (souple) |
| TASK-000011 | Stratégie et évolution encadrée | P1 | en service et surveillé | décision | 100 % | oui | 10 (souple), 9 (souple), 5 (données) |
| TASK-000012 | Rejeu et études (backtest, deux époques, crises) | P1 | en service | décision | 100 % | oui | 11 (dure), 5 (données) |
| TASK-000013 | Moteur de risque, garde « pas de trade », arrêt d'urgence | P0 | en service et surveillé | risque | 100 % | oui | 11 (dure), 10 (souple), 2 (données) |
| TASK-000014 | Portefeuille (taille, plafonds, sélection, moteur de portefeuille) | P1 | en service et surveillé | risque | 100 % | oui | 13 (dure), 11 (dure) |
| TASK-000015 | Paper trading et ses critères d'acceptation | P1 | en service et surveillé | exécution | 100 % | oui | 14 (dure), 13 (dure), 12 (validation), 4 (à l'exécution) |
| TASK-000016 | Politique, autorisation et porte d'exécution | P0 | en service et surveillé | risque | 100 % | oui | 13 (dure), 4 (sécurité), 3 (sécurité) |
| TASK-000017 | Connecteur Binance (ordres réels) | P0 | en essai | exécution | 100 % | oui | 16 (sécurité), 3 (sécurité) |
| TASK-000018 | Trading réel | P0 | bloqué | exécution | 95 % | non | 17 (dure), 15 (validation), 16 (sécurité), 13 (sécurité), 12 (validation), 4 (à l'exécution) |
| TASK-000019 | Recherche autonome | P2 | en service | intelligence | 100 % | oui | 9 (souple) |
| TASK-000020 | Auto-évaluation (diagnostic expert, leçons des trades) | P2 | en service et surveillé | transverse | 100 % | oui | 6 (dure), 4 (données) |
| TASK-000021 | Auto-amélioration (évolution encadrée) | P2 | en service et surveillé | décision | 100 % | oui | 20 (souple), 12 (validation) |
| TASK-000022 | Panneau de contrôle et Rachelle | P2 | en service et surveillé | transverse | 100 % | oui | 3 (sécurité), 4 (données), 8 (souple) |
| TASK-000023 | Centre de commande 3D | P4 | à faire | transverse | 80 % | oui | 22 (souple) |
| TASK-000024 | Durcissement de production (PC, sauvegardes, reprise) | P0 | en cours | transverse | 100 % | oui | 3 (sécurité), 4 (à l'exécution) |
| TASK-000025 | Cœur d'intelligence financière | P1 | en service et surveillé | intelligence | 100 % | oui | 5 (données), 2 (données), 9 (souple), 7 (souple) |

## Chemin critique jusqu'au réel

Contrats de données → Base de données et journal financier → Noyau cognitif → Socle multi-agents et comité → Socle multi-modèles d'IA → Intelligence financière (savoir, veille, régimes, calendrier) → Moteur quantitatif (laboratoire, risque d'un jour, résistance, attribution) → Stratégie et évolution encadrée → Moteur de risque, garde « pas de trade », arrêt d'urgence → Politique, autorisation et porte d'exécution → Connecteur Binance (ordres réels) → Trading réel.

## Portes

- **Porte 1 (Fondations)** : franchie — Contrats de données ✓ ; Base de données et journal financier ✓ ; Sécurité de base ✓ ; Observabilité et audit ✓
- **Porte 2 (Cognition)** : franchie — Noyau cognitif ✓ ; Intelligence financière (savoir, veille, régimes, calendrier) ✓ ; Auto-évaluation (diagnostic expert, leçons des trades) ✓
- **Porte 3 (Multi-agents et multi-modèles)** : franchie — Socle multi-agents et comité ✓ ; Socle multi-modèles d'IA ✓
- **Porte 4 (Intelligence financière)** : franchie — Données de marché et qualité ✓ ; Intelligence financière (savoir, veille, régimes, calendrier) ✓ ; Cœur d'intelligence financière ✓ ; Moteur quantitatif (laboratoire, risque d'un jour, résistance, attribution) ✓ ; Analyse fondamentale —
- **Porte 5 (Stratégie)** : franchie — Stratégie et évolution encadrée ✓ ; Rejeu et études (backtest, deux époques, crises) ✓
- **Porte 6 (Risque)** : franchie — Moteur de risque, garde « pas de trade », arrêt d'urgence ✓ ; Portefeuille (taille, plafonds, sélection, moteur de portefeuille) ✓ ; Politique, autorisation et porte d'exécution ✓
- **Porte 7 (Paper)** : franchie — Paper trading et ses critères d'acceptation ✓ ; Observabilité et audit ✓
- **Porte 8 (réel)** : mesurée sur l'état du bot (`python trendguard_bot.py chantiers portes`) : portes 1 à 7 ; essai paper d'au moins 60 jours et 10 trades clos ; aucun réglage à l'essai ; ni arrêt d'urgence ni mode sûr ; journaux d'audit et financier intacts ; alertes configurées ; rapport quotidien de moins de 2 jours sans défaut de sécurité ; vérification sans ordre réussie sur Binance réel depuis moins de 7 jours. Fermée, elle bloque tout achat réel à la porte d'exécution ; aucune option ne la contourne.

## Fiches

### TASK-000001 — Contrats de données

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P0, en service et surveillé, high |
| Propriétaire | bot |
| Dépend de | rien |
| Bloque | Base de données et journal financier, Sécurité de base, Observabilité et audit, Données de marché et qualité, Noyau cognitif, Socle multi-agents et comité |
| Code | `trendguard/contrats.py` |
| Tests | `tests/test_contrats.py`, `tests/test_contrats_donnees.py` |
| Contrats | `Envelope.v1`, `ErrorEnvelope.v2`, `Confidence.v1`, `OrderIntent.v1` |
| Documentation | [`CONTRATS.md`](CONTRATS.md) |
| Sécurité | validateur : champ inconnu, absent, mauvais type ou hors bornes refusé, jamais corrigé |
| Observabilité | registre tiré du code, document tenu identique par un test |
| Acceptation | chaque échange critique a un contrat versionné, ses tests négatifs, sa doc |
| Santé | 100 % (seuil 95 %) |

### TASK-000002 — Base de données et journal financier

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P0, en service et surveillé, high |
| Propriétaire | bot |
| Dépend de | Contrats de données (contrat) |
| Bloque | Observabilité et audit, Noyau cognitif, Moteur de risque, garde « pas de trade », arrêt d'urgence, Cœur d'intelligence financière |
| Code | `trendguard/donnees.py` |
| Tests | `tests/test_donnees.py` |
| Contrats | `DecisionRecord.v1`, `TradeRecord.v1` |
| Documentation | [`DONNEES.md`](DONNEES.md) |
| Sécurité | tables en ajout seulement ; contraintes dans la base ; un achat sans contrôle refusé |
| Observabilité | lignée de chaque trade ; vérifié chaque nuit ; sauvegarde relue |
| Acceptation | migrations versionnées et réversibles ; chaque trade remonte à ses données |
| Santé | 100 % (seuil 95 %) |

### TASK-000003 — Sécurité de base

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P0, en service et surveillé, critical |
| Propriétaire | bot |
| Dépend de | Contrats de données (contrat) |
| Bloque | Socle multi-modèles d'IA, Politique, autorisation et porte d'exécution, Connecteur Binance (ordres réels), Panneau de contrôle et Rachelle, Durcissement de production (PC, sauvegardes, reprise) |
| Code | `trendguard/report_security.py`, `panel/security.py`, `trendguard/config.py`, `trendguard/cyber.py` |
| Tests | `tests/test_report.py`, `tests/test_panel.py`, `tests/test_cyber.py` |
| Contrats | `PanelCommand.v1`, `SecurityPostureReport.v1` |
| Documentation | [`RAPPORT.md`](RAPPORT.md), [`CYBERSECURITE.md`](CYBERSECURITE.md) |
| Sécurité | clés saisies masquées ; panneau sur ce PC seulement ; droits de la clé Binance vérifiés ; réel désarmé par défaut |
| Observabilité | rapport de sécurité chaque nuit |
| Acceptation | aucun secret dans le dépôt, les journaux ni les réponses |
| Santé | 100 % (seuil 95 %) |

### TASK-000004 — Observabilité et audit

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P0, en service et surveillé, high |
| Propriétaire | bot |
| Dépend de | Contrats de données (contrat), Base de données et journal financier (données) |
| Bloque | Paper trading et ses critères d'acceptation, Politique, autorisation et porte d'exécution, Trading réel, Auto-évaluation (diagnostic expert, leçons des trades), Panneau de contrôle et Rachelle, Durcissement de production (PC, sauvegardes, reprise) |
| Code | `trendguard/audit.py`, `trendguard/report.py`, `trendguard/report_health.py` |
| Tests | `tests/test_contrats.py`, `tests/test_report.py` |
| Contrats | `AuditEvent.v2`, `HealthReport.v1` |
| Documentation | [`RAPPORT.md`](RAPPORT.md) |
| Sécurité | journal d'audit chaîné : toute modification se voit |
| Observabilité | journal du bot, audit corrélé et causé, rapport quotidien, panneau |
| Acceptation | chaque opération critique reconstruite : quoi, qui, quand, pourquoi, avec quelle autorisation |
| Santé | 100 % (seuil 95 %) |

### TASK-000005 — Données de marché et qualité

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P0, en service et surveillé, high |
| Propriétaire | bot |
| Dépend de | Contrats de données (contrat) |
| Bloque | Intelligence financière (savoir, veille, régimes, calendrier), Moteur quantitatif (laboratoire, risque d'un jour, résistance, attribution), Stratégie et évolution encadrée, Rejeu et études (backtest, deux époques, crises), Cœur d'intelligence financière |
| Code | `trendguard/qualite.py`, `trendguard/bot.py` |
| Tests | `tests/test_analyse.py`, `tests/test_contrats_donnees.py` |
| Contrats | `MarketData.v1`, `OHLCV.v1` |
| Documentation | [`DONNEES.md`](DONNEES.md) |
| Sécurité | bougies incohérentes écartées ; données absentes : décision reportée |
| Observabilité | note de qualité sur 100 à chaque décision |
| Acceptation | bougies clôturées seulement ; aucune décision sur des données abîmées ou futures |
| Santé | 100 % (seuil 95 %) |

### TASK-000006 — Noyau cognitif

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P1, en service et surveillé, medium |
| Propriétaire | bot |
| Dépend de | Contrats de données (contrat), Base de données et journal financier (données) |
| Bloque | Socle multi-agents et comité, Socle multi-modèles d'IA, Auto-évaluation (diagnostic expert, leçons des trades) |
| Code | `trendguard/cognitif.py`, `trendguard/expert.py`, `trendguard/memoire.py`, `trendguard/monde.py`, `trendguard/causal.py` |
| Tests | `tests/test_cognitif.py`, `tests/test_memoire.py`, `tests/test_monde.py`, `tests/test_causal.py` |
| Contrats | `ExpertDiagnosis.v1`, `MemoryHealthReport.v1`, `WorldModelReport.v1`, `CausalReport.v1` |
| Documentation | [`COGNITIF.md`](COGNITIF.md), [`MEMOIRE.md`](MEMOIRE.md), [`MONDE.md`](MONDE.md), [`CAUSAL.md`](CAUSAL.md) |
| Sécurité | outils en lecture seule ; une proposition n'est jamais une autorisation |
| Observabilité | diagnostic expert gardé et résumé chaque nuit |
| Acceptation | le bot fait seul son diagnostic expert, propose sans agir |
| Santé | 100 % (seuil 85 %) |

### TASK-000007 — Socle multi-agents et comité

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P1, en service et surveillé, medium |
| Propriétaire | bot |
| Dépend de | Noyau cognitif (dure), Contrats de données (contrat) |
| Bloque | Socle multi-modèles d'IA, Cœur d'intelligence financière |
| Code | `trendguard/agents.py`, `trendguard/comite.py` |
| Tests | `tests/test_agents.py` |
| Contrats | `CommitteeView.v1`, `Confidence.v1` |
| Documentation | [`AGENTS.md`](AGENTS.md), [`COMITE_ETUDE.md`](COMITE_ETUDE.md) |
| Sécurité | aucun agent n'accède aux ordres ; quarantaine, disjoncteur |
| Observabilité | avis gardés dans le journal financier, comparés aux trades réels |
| Acceptation | onze agents consultatifs, éprouvés sur 8 ans, sans effet sur les décisions |
| Santé | 100 % (seuil 85 %) |

### TASK-000008 — Socle multi-modèles d'IA

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P1, en service, medium |
| Propriétaire | bot |
| Dépend de | Noyau cognitif (dure), Socle multi-agents et comité (souple), Sécurité de base (sécurité) |
| Bloque | Intelligence financière (savoir, veille, régimes, calendrier), Panneau de contrôle et Rachelle |
| Code | `trendguard/modeles.py` |
| Tests | `tests/test_modeles.py` |
| Contrats | `LLMExecution.v1`, `ModelSelection.v1`, `ModelConsensus.v1`, `PromptVersion.v1`, `ModelBenchmark.v1` |
| Documentation | [`MODELES.md`](MODELES.md) |
| Sécurité | banc obligatoire, invites versionnées, réponses vérifiées, secrets jamais envoyés |
| Observabilité | chaque appel tracé ; fiches et mesures de chaque modèle |
| Acceptation | aucun modèle en service sans banc ; repli tracé ; mode dégradé sûr |
| Santé | 100 % (seuil 85 %) |
| Note | aucune clé d'IA aujourd'hui : en service en mode dégradé sûr |

### TASK-000009 — Intelligence financière (savoir, veille, régimes, calendrier)

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P1, en service et surveillé, medium |
| Propriétaire | bot |
| Dépend de | Données de marché et qualité (données), Socle multi-modèles d'IA (souple) |
| Bloque | Moteur quantitatif (laboratoire, risque d'un jour, résistance, attribution), Stratégie et évolution encadrée, Recherche autonome, Cœur d'intelligence financière |
| Code | `trendguard/savoir.py`, `trendguard/market_watch.py`, `trendguard/regimes.py`, `trendguard/evenements.py` |
| Tests | `tests/test_savoir.py`, `tests/test_market_watch.py`, `tests/test_analyse.py` |
| Contrats | `KnowledgeHold.v1`, `AIOpinion.v1`, `ModelDisagreement.v1` |
| Documentation | [`SAVOIR.md`](SAVOIR.md), [`REGIMES.md`](REGIMES.md) |
| Sécurité | sources jugées sur les cours réels ; seul un retrait annoncé par Binance bloque un achat |
| Observabilité | noyau de savoir et veille sur la page Veille et dans le rapport |
| Acceptation | ne peut que reporter un achat, jamais acheter ni vendre |
| Santé | 100 % (seuil 85 %) |

### TASK-000010 — Moteur quantitatif (laboratoire, risque d'un jour, résistance, attribution)

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P1, en service, medium |
| Propriétaire | bot |
| Dépend de | Données de marché et qualité (données), Intelligence financière (savoir, veille, régimes, calendrier) (souple) |
| Bloque | Stratégie et évolution encadrée, Moteur de risque, garde « pas de trade », arrêt d'urgence |
| Code | `trendguard/risque.py`, `trendguard/stress.py`, `trendguard/attribution.py`, `trendguard/moteur_quant.py` |
| Tests | `tests/test_analyse.py`, `tests/test_moteur_quant.py` |
| Contrats | `PortfolioAnalysis.v1`, `QuantResult.v1` |
| Documentation | [`PLATEFORME.md`](PLATEFORME.md), [`MOTEUR_QUANT.md`](MOTEUR_QUANT.md), [`QUANT.md`](QUANT.md) |
| Sécurité | mesures seulement : aucune décision, aucun ordre |
| Observabilité | analyse du portefeuille sur le panneau ; rapport du laboratoire reproductible |
| Acceptation | chiffres recalculés par le bot, jamais par une IA ; lois éprouvées contre des valeurs connues ; même graine, même résultat |
| Santé | 100 % (seuil 85 %) |

### TASK-000011 — Stratégie et évolution encadrée

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P1, en service et surveillé, high |
| Propriétaire | bot |
| Dépend de | Moteur quantitatif (laboratoire, risque d'un jour, résistance, attribution) (souple), Intelligence financière (savoir, veille, régimes, calendrier) (souple), Données de marché et qualité (données) |
| Bloque | Rejeu et études (backtest, deux époques, crises), Moteur de risque, garde « pas de trade », arrêt d'urgence, Portefeuille (taille, plafonds, sélection, moteur de portefeuille) |
| Code | `trendguard/trend_strategy.py`, `trendguard/evolution.py`, `trendguard/garde.py`, `trendguard/moteur_strategie.py` |
| Tests | `tests/test_trendguard.py`, `tests/test_evolution.py`, `tests/test_moteur_strategie.py` |
| Contrats | `Signal.v1`, `EntryPlan.v1`, `EvolutionChange.v1`, `StrategySpec.v1`, `StrategyDecision.v1` |
| Documentation | [`STRATEGIES.md`](STRATEGIES.md), [`EVOLUTION.md`](EVOLUTION.md), [`MOTEUR_STRATEGIE.md`](MOTEUR_STRATEGIE.md) |
| Sécurité | réglages changés seulement après épreuves et essai de 30 jours |
| Observabilité | raisonnement du jour ; registre des expériences |
| Acceptation | cassure de 30 jours, momentum, régime de BTC ; validée sur deux époques |
| Santé | 100 % (seuil 85 %) |

### TASK-000012 — Rejeu et études (backtest, deux époques, crises)

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P1, en service, medium |
| Propriétaire | bot |
| Dépend de | Stratégie et évolution encadrée (dure), Données de marché et qualité (données) |
| Bloque | Paper trading et ses critères d'acceptation, Trading réel, Auto-amélioration (évolution encadrée) |
| Code | `trendguard/replay.py`, `trendguard/strategy_lab.py`, `trendguard/registre.py`, `research/robustness.py`, `trendguard/moteur_backtest.py` |
| Tests | `tests/test_backtest_paper.py`, `tests/test_strategy_lab.py`, `tests/test_robustness.py`, `tests/test_moteur_backtest.py` |
| Contrats | `Experiment.v1`, `BacktestManifest.v1`, `BacktestResult.v1` |
| Documentation | [`ROBUSTESSE.md`](ROBUSTESSE.md), [`EXAMEN.md`](EXAMEN.md), [`MOTEUR_BACKTEST.md`](MOTEUR_BACKTEST.md) |
| Sécurité | études hors ligne, sans aucun ordre |
| Observabilité | études reproductibles, registre |
| Acceptation | chaque règle prouvée sur deux époques avant d'entrer dans le bot |
| Santé | 100 % (seuil 85 %) |

### TASK-000013 — Moteur de risque, garde « pas de trade », arrêt d'urgence

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P0, en service et surveillé, critical |
| Propriétaire | bot |
| Dépend de | Stratégie et évolution encadrée (dure), Moteur quantitatif (laboratoire, risque d'un jour, résistance, attribution) (souple), Base de données et journal financier (données) |
| Bloque | Portefeuille (taille, plafonds, sélection, moteur de portefeuille), Paper trading et ses critères d'acceptation, Politique, autorisation et porte d'exécution, Trading réel |
| Code | `trendguard/porte.py`, `trendguard/garde.py`, `trendguard/bot.py`, `trendguard/moteur_risque.py` |
| Tests | `tests/test_contrats.py`, `tests/test_trendguard.py`, `tests/test_moteur_risque.py` |
| Contrats | `RiskCheck.v1`, `NoTradeGate.v1`, `KillSwitch.v1`, `RiskAssessment.v1` |
| Documentation | [`CONTRATS.md`](CONTRATS.md), [`MOTEUR_RISQUE.md`](MOTEUR_RISQUE.md) |
| Sécurité | 1 % de risque par achat, plafonds, arrêt d'urgence à −40 %, « pas de trade » valide |
| Observabilité | chaque contrôle dans l'audit et le journal financier |
| Acceptation | aucun achat sans contrôle déterministe approuvé |
| Santé | 100 % (seuil 95 %) |

### TASK-000014 — Portefeuille (taille, plafonds, sélection, moteur de portefeuille)

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P1, en service et surveillé, medium |
| Propriétaire | bot |
| Dépend de | Moteur de risque, garde « pas de trade », arrêt d'urgence (dure), Stratégie et évolution encadrée (dure) |
| Bloque | Paper trading et ses critères d'acceptation |
| Code | `trendguard/trend_strategy.py`, `trendguard/selection.py`, `trendguard/moteur_portefeuille.py` |
| Tests | `tests/test_selection.py`, `tests/test_trendguard.py`, `tests/test_moteur_portefeuille.py` |
| Contrats | `EntryPlan.v1`, `PortfolioDecision.v1` |
| Documentation | [`SELECTION.md`](SELECTION.md), [`MOTEUR_PORTEFEUILLE.md`](MOTEUR_PORTEFEUILLE.md), [`PORTEFEUILLE.md`](PORTEFEUILLE.md) |
| Sécurité | taille par le risque, plafonds de positions et de risque cumulé ; le moteur de portefeuille ne passe aucun ordre et ne rééquilibre rien |
| Observabilité | positions et analyse sur le panneau ; état du portefeuille à chaque décision |
| Acceptation | la taille de chaque achat respecte les plafonds ; contrainte impossible expliquée, jamais relâchée |
| Santé | 100 % (seuil 85 %) |

### TASK-000015 — Paper trading et ses critères d'acceptation

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P1, en service et surveillé, medium |
| Propriétaire | bot |
| Dépend de | Portefeuille (taille, plafonds, sélection, moteur de portefeuille) (dure), Moteur de risque, garde « pas de trade », arrêt d'urgence (dure), Rejeu et études (backtest, deux époques, crises) (validation), Observabilité et audit (à l'exécution) |
| Bloque | Trading réel |
| Code | `trendguard/bot.py`, `trendguard/bot_execution.py`, `trendguard/acceptation.py` |
| Tests | `tests/test_trendguard.py`, `tests/test_backtest_paper.py`, `tests/test_acceptation.py` |
| Contrats | `TradeRecord.v1`, `OrderIntent.v1`, `PaperAcceptanceReport.v1` |
| Documentation | [`TRADING.md`](TRADING.md), [`ACCEPTATION_PAPER.md`](ACCEPTATION_PAPER.md) |
| Sécurité | argent fictif ; même porte d'exécution qu'en réel ; un P0 raté bloque l'acceptation |
| Observabilité | journal, audit, rapport ; verdict d'acceptation chaque nuit |
| Acceptation | le bot tourne seul, en paper, avec les mêmes contrôles qu'en réel ; AC-001 à AC-044 mesurés |
| Santé | 100 % (seuil 85 %) |

### TASK-000016 — Politique, autorisation et porte d'exécution

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P0, en service et surveillé, critical |
| Propriétaire | bot |
| Dépend de | Moteur de risque, garde « pas de trade », arrêt d'urgence (dure), Observabilité et audit (sécurité), Sécurité de base (sécurité) |
| Bloque | Connecteur Binance (ordres réels), Trading réel |
| Code | `trendguard/porte.py`, `trendguard/politique.py`, `trendguard/autorisation.py`, `trendguard/porte_examen.py` |
| Tests | `tests/test_contrats.py`, `tests/test_politique.py`, `tests/test_autorisation.py`, `tests/test_porte_examen.py` |
| Contrats | `ExecutionAuthorization.v1`, `SafeModeState.v1`, `OrderIntent.v1`, `PolicyDecision.v1`, `AuthorizationDecision.v1`, `FinalValidationResult.v1`, `GateReadinessReport.v1` |
| Documentation | [`CONTRATS.md`](CONTRATS.md), [`MOTEUR_POLITIQUE.md`](MOTEUR_POLITIQUE.md), [`MOTEUR_AUTORISATION.md`](MOTEUR_AUTORISATION.md), [`PORTE_EXECUTION.md`](PORTE_EXECUTION.md) |
| Sécurité | autorisation de 5 minutes liée à un contrôle ; mode sûr ; réel armé ; porte du réel |
| Observabilité | autorisation gardée avec l'achat |
| Acceptation | aucun ordre sans autorisation ; aucune IA ni agent sur ce chemin |
| Santé | 100 % (seuil 95 %) |

### TASK-000017 — Connecteur Binance (ordres réels)

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P0, en essai, critical |
| Propriétaire | bot |
| Dépend de | Politique, autorisation et porte d'exécution (sécurité), Sécurité de base (sécurité) |
| Bloque | Trading réel |
| Code | `v29/exchange.py`, `v29/execution.py` |
| Tests | `tests/test_live_execution.py`, `tests/test_fake_binance.py`, `tests/test_real_conditions.py` |
| Contrats | `Order.v1` |
| Documentation | [`TRADING.md`](TRADING.md) |
| Sécurité | identifiant client unique ; ordre ambigu : la paire s'arrête ; stop de secours chez Binance |
| Observabilité | chaque ordre dans le journal et l'audit |
| Acceptation | validé par la vérification sans ordre (verify) sur Binance réel |
| Santé | 100 % (seuil 95 %) |
| Note | éprouvé contre un faux Binance et le testnet ; jamais employé en réel |

### TASK-000018 — Trading réel

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P0, bloqué, critical |
| Propriétaire | vous |
| Dépend de | Connecteur Binance (ordres réels) (dure), Paper trading et ses critères d'acceptation (validation), Politique, autorisation et porte d'exécution (sécurité), Moteur de risque, garde « pas de trade », arrêt d'urgence (sécurité), Rejeu et études (backtest, deux époques, crises) (validation), Observabilité et audit (à l'exécution) |
| Bloque | rien |
| Code | `trendguard/bot_execution.py`, `trendguard/deploiement.py` |
| Tests | `tests/test_live_execution.py`, `tests/test_deploiement.py` |
| Contrats | `Order.v1`, `ExecutionAuthorization.v1`, `LiveDeploymentReport.v1` |
| Documentation | [`PLATEFORME.md`](PLATEFORME.md), [`DEPLOIEMENT_REEL.md`](DEPLOIEMENT_REEL.md) |
| Sécurité | porte du réel (porte 8) vérifiée par la porte d'exécution avant chaque achat réel |
| Observabilité | audit, journal, rapport |
| Acceptation | porte 8 ouverte : essai paper suffisant, sécurité sans défaut, vérification réussie ; puis paliers (réel simulé, réel contrôlé plafonné, production limitée), chacun par votre réglage |
| Santé | 95 % (seuil 95 %) |
| Note | bloqué tant que la porte du réel est fermée : python trendguard_bot.py chantiers portes ; paliers : python trendguard_bot.py deploiement |

### TASK-000019 — Recherche autonome

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P2, en service, medium |
| Propriétaire | bot |
| Dépend de | Intelligence financière (savoir, veille, régimes, calendrier) (souple) |
| Bloque | rien |
| Code | `trendguard/savoir.py`, `trendguard/recherche.py` |
| Tests | `tests/test_savoir.py`, `tests/test_recherche.py` |
| Contrats | `KnowledgeHold.v1`, `ResearchReport.v1` |
| Documentation | [`SAVOIR.md`](SAVOIR.md), [`RECHERCHE.md`](RECHERCHE.md) |
| Sécurité | lecture publique seulement |
| Observabilité | bilan du savoir chaque nuit |
| Acceptation | le bot lit et juge ses sources seul |
| Santé | 100 % (seuil 75 %) |

### TASK-000020 — Auto-évaluation (diagnostic expert, leçons des trades)

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P2, en service et surveillé, medium |
| Propriétaire | bot |
| Dépend de | Noyau cognitif (dure), Observabilité et audit (données) |
| Bloque | Auto-amélioration (évolution encadrée) |
| Code | `trendguard/expert.py`, `trendguard/postmortem.py`, `trendguard/diagnostics.py` |
| Tests | `tests/test_cognitif.py`, `tests/test_diagnostics.py` |
| Contrats | `ExpertDiagnosis.v1`, `TradeRecord.v1` |
| Documentation | [`COGNITIF.md`](COGNITIF.md) |
| Sécurité | lecture seule |
| Observabilité | rapport quotidien |
| Acceptation | chaque nuit, le bot se juge et le dit |
| Santé | 100 % (seuil 75 %) |

### TASK-000021 — Auto-amélioration (évolution encadrée)

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P2, en service et surveillé, medium |
| Propriétaire | bot |
| Dépend de | Auto-évaluation (diagnostic expert, leçons des trades) (souple), Rejeu et études (backtest, deux époques, crises) (validation) |
| Bloque | rien |
| Code | `trendguard/evolution.py`, `trendguard/registre.py`, `trendguard/apprentissage.py` |
| Tests | `tests/test_evolution.py`, `tests/test_apprentissage.py` |
| Contrats | `EvolutionChange.v1`, `Experiment.v1`, `ModelCard.v1`, `LearningGovernanceReport.v1` |
| Documentation | [`EVOLUTION.md`](EVOLUTION.md), [`APPRENTISSAGE_CONTINU.md`](APPRENTISSAGE_CONTINU.md) |
| Sécurité | jamais le risque cumulé, les positions, l'arrêt d'urgence ni le réel ; gelée en réel contrôlé et en production limitée |
| Observabilité | registre des expériences |
| Acceptation | proposer, éprouver, essayer, puis adopter |
| Santé | 100 % (seuil 75 %) |

### TASK-000022 — Panneau de contrôle et Rachelle

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P2, en service et surveillé, medium |
| Propriétaire | vous |
| Dépend de | Sécurité de base (sécurité), Observabilité et audit (données), Socle multi-modèles d'IA (souple) |
| Bloque | Centre de commande 3D |
| Code | `panel/server.py`, `panel/assistant.py`, `panel/static/app.js`, `panel/interface.py` |
| Tests | `tests/test_panel.py`, `tests/test_assistant.py`, `tests/test_interface.py` |
| Contrats | `PanelCommand.v1`, `InterfaceReadinessReport.v1` |
| Documentation | [`README.md`](README.md), [`INTERFACE.md`](INTERFACE.md) |
| Sécurité | mot de passe, ce PC seulement, Rachelle refuse les secrets ; chaque commande classée, aucune n'exécute un ordre |
| Observabilité | toutes les pages en lecture |
| Acceptation | tout se voit et se règle sans ligne de commande |
| Santé | 100 % (seuil 75 %) |

### TASK-000023 — Centre de commande 3D

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P4, à faire, medium |
| Propriétaire | bot |
| Dépend de | Panneau de contrôle et Rachelle (souple) |
| Bloque | rien |
| Code | — |
| Tests | — |
| Contrats | — |
| Documentation | — |
| Sécurité | — |
| Observabilité | — |
| Acceptation | vue 3D des agents, modèles et fournisseurs |
| Santé | 80 % (seuil 50 %) |
| Note | non construit : le panneau suffit pour un seul PC ; jamais une dépendance d'un composant critique |

### TASK-000024 — Durcissement de production (PC, sauvegardes, reprise)

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P0, en cours, high |
| Propriétaire | vous et le bot |
| Dépend de | Sécurité de base (sécurité), Observabilité et audit (à l'exécution) |
| Bloque | rien |
| Code | `trendguard/autonomy.py`, `trendguard/maintenance.py`, `trendguard/report_security.py`, `trendguard/controle.py` |
| Tests | `tests/test_autonomy.py`, `tests/test_maintenance.py`, `tests/test_controle.py` |
| Contrats | `HealthReport.v1`, `ControlPlaneReport.v1` |
| Documentation | [`RAPPORT.md`](RAPPORT.md), [`PLAN_DE_CONTROLE.md`](PLAN_DE_CONTROLE.md) |
| Sécurité | relance automatique, sauvegarde relue chaque nuit, superviseur borné, plan de contrôle |
| Observabilité | rapport quotidien |
| Acceptation | rapport quotidien sans point de sécurité à corriger |
| Santé | 100 % (seuil 95 %) |
| Note | dépend aussi de vous : points « à corriger » du rapport quotidien |

### TASK-000025 — Cœur d'intelligence financière

| Rubrique | |
| --- | --- |
| Priorité, état, risque | P1, en service et surveillé, medium |
| Propriétaire | bot |
| Dépend de | Données de marché et qualité (données), Base de données et journal financier (données), Intelligence financière (savoir, veille, régimes, calendrier) (souple), Socle multi-agents et comité (souple) |
| Bloque | rien |
| Code | `trendguard/finance.py` |
| Tests | `tests/test_finance.py` |
| Contrats | `Instrument.v1`, `Feature.v1`, `Forecast.v1`, `Scenario.v1`, `FinancialSignal.v1`, `FinancialAnalysis.v1` |
| Documentation | [`FINANCE.md`](FINANCE.md) |
| Sécurité | indicateurs sans regard vers le futur (test, base) ; une analyse n'est jamais une autorisation |
| Observabilité | analyses, indicateurs et prévisions au journal ; calibration au rapport |
| Acceptation | chaque crypto analysée chaque nuit ; prévisions évaluées à 30 jours ; aucun trade changé |
| Santé | 100 % (seuil 85 %) |
