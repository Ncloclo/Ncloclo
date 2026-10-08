# Contrats entre les modules de TrendGuard

Tiré du registre `trendguard/contrats.py` (`python -m trendguard.contrats` le réécrit) ; un test vérifie que ce document et le registre restent identiques.

| Contrat | Producteur (propriétaire) | Consommateurs | Version | Classification | Niveau |
| --- | --- | --- | --- | --- | --- |
| `AuditEvent.v2` | bot (un seul écrivain) | rapport quotidien, commande audit, diagnostic expert | 2.0.0 | CONFIDENTIAL | critique |
| `ExecutionAuthorization.v1` | porte d'exécution | exécution (paper) ou moteur d'ordres v29 (réel) | 1.0.0 | INTERNAL | critique |
| `KillSwitch.v1` | décision du jour | porte d'exécution, panneau, rapport | 1.0.0 | INTERNAL | critique |
| `NoTradeGate.v1` | décision du jour | porte d'exécution, raisonnement | 1.0.0 | INTERNAL | critique |
| `Order.v1` | moteur v29 (réel) | Binance Spot | 1.0.0 | INTERNAL | critique |
| `OrderIntent.v1` | exécution (bot_execution.py) | porte d'exécution (porte.py) | 1.0.0 | INTERNAL | critique |
| `ReleaseGate.v1` | feuille de route (chantiers.py) | porte d'exécution (achats réels), rapport, Rachelle | 1.0.0 | INTERNAL | critique |
| `RiskCheck.v1` | exécution | porte d'exécution | 1.0.0 | INTERNAL | critique |
| `SafeModeState.v1` | vous (commande mode-sur) | porte d'exécution, décision du jour | 1.0.0 | INTERNAL | critique |
| `TradeRecord.v1` | exécution | journal des trades, attribution, apprentissage | 1.0.0 | INTERNAL | critique |
| `AIOpinion.v1` | IA consultées (market_watch.py) | veille, noyau de savoir | 1.0.0 | INTERNAL | cœur |
| `BacktestResult.v1` | moteur de backtest (moteur_backtest.py) | moteur de risque, vous | 1.0.0 | INTERNAL | cœur |
| `CommitteeView.v1` | comité (comite.py) | journal financier, Rachelle, panneau | 1.0.0 | INTERNAL | cœur |
| `Confidence.v1` | comité d'agents | Rachelle, panneau, journal | 1.0.0 | INTERNAL | cœur |
| `EntryPlan.v1` | stratégie | exécution | 1.0.0 | INTERNAL | cœur |
| `EvolutionChange.v1` | évolution (evolution.py) | bot, à la décision suivante | 1.0.0 | INTERNAL | cœur |
| `ExpertDiagnosis.v1` | diagnostic expert (expert.py) | rapport quotidien, Rachelle, panneau | 1.0.0 | INTERNAL | cœur |
| `FinancialAnalysis.v1` | cœur financier (finance.py) | journal financier, Rachelle, rapport, commande finance | 1.0.0 | INTERNAL | cœur |
| `FinancialSignal.v1` | cœur financier (finance.py) | analyses, journal | 1.0.0 | PUBLIC | cœur |
| `Forecast.v1` | cœur financier (finance.py) | analyses, journal financier, calibration | 1.0.0 | PUBLIC | cœur |
| `KnowledgeHold.v1` | noyau de savoir (savoir.py) | décision du jour | 1.0.0 | INTERNAL | cœur |
| `LLMExecution.v1` | exécution des modèles (modeles.py) | trace des IA, rapport, panneau | 1.0.0 | INTERNAL | cœur |
| `ModelBenchmark.v1` | banc (modeles.py) | routeur (approbation), fiche du modèle, rapport | 1.0.0 | INTERNAL | cœur |
| `ModelConsensus.v1` | veille (market_watch.py) | rapport de la veille, panneau, Rachelle | 1.0.0 | PUBLIC | cœur |
| `ModelDisagreement.v1` | veille (market_watch.py) | rapport de la veille, panneau, Rachelle | 1.0.0 | PUBLIC | cœur |
| `ModelSelection.v1` | routeur (modeles.py) | exécution des modèles | 1.0.0 | INTERNAL | cœur |
| `PortfolioAnalysis.v1` | décision du jour (risque.py, stress.py, attribution.py) | panneau, rapport, raisonnement | 1.0.0 | INTERNAL | cœur |
| `PromptVersion.v1` | socle des modèles (modeles.py) | exécution des modèles, veille, Rachelle, banc | 1.0.0 | INTERNAL | cœur |
| `Scenario.v1` | cœur financier (finance.py) | analyses, Rachelle | 1.0.0 | PUBLIC | cœur |
| `Signal.v1` | stratégie (trend_strategy.py) | plan d'achat | 1.0.0 | INTERNAL | cœur |
| `StrategyDecision.v1` | moteur de stratégie (moteur_strategie.py) | raisonnement, rapport, Rachelle | 1.0.0 | INTERNAL | cœur |
| `StrategySpec.v1` | moteur de stratégie (moteur_strategie.py) | validation, backtest, décisions, Rachelle, commande regle | 1.0.0 | PUBLIC | cœur |
| `BacktestManifest.v1` | moteur de backtest (moteur_backtest.py) | rapport de validation, vous | 1.0.0 | PUBLIC | données |
| `DecisionRecord.v1` | décision du jour | journal financier, lignée des trades | 1.0.0 | CONFIDENTIAL | données |
| `Experiment.v1` | évolution, études (registre.py) | rejeu, contrôle, rapport | 1.0.0 | INTERNAL | données |
| `Feature.v1` | cœur financier (finance.py) | analyses, journal financier | 1.0.0 | PUBLIC | données |
| `Instrument.v1` | cœur financier (finance.py) | analyses, panneau | 1.0.0 | PUBLIC | données |
| `MarketData.v1` | Binance (klines publiques) | décision du jour, régimes, qualité | 1.0.0 | INTERNAL | données |
| `OHLCV.v1` | Binance (klines publiques) | décision du jour, qualité des données | 1.0.0 | PUBLIC | données |
| `Envelope.v1` | tout module (contrats.py) | tout module | 1.0.0 | INTERNAL | support |
| `ErrorEnvelope.v2` | tout module | journal d'audit, rapport | 2.0.0 | INTERNAL | support |
| `HealthReport.v1` | rapport (report.py) | vous (e-mail, panneau) | 1.0.0 | INTERNAL | support |
| `PanelCommand.v1` | vous (panneau) | contrôle du bot | 1.0.0 | INTERNAL | support |

## Conventions communes

- Identifiants : UUID v4 pour les nouveaux contrats (messages, appels aux IA) ; les identifiants lisibles existants restent (décision `D-2026-10-06`, clé d'achat `jour:crypto:BUY`).
- Corrélation : l'identifiant de la décision relie le contrôle du risque, l'autorisation, l'ordre et la vente ; la cause (`causation_id`) dit quel événement a provoqué le suivant.
- Dates : UTC, ISO-8601 avec fuseau ; une heure sans fuseau est refusée.
- Versions : MAJEUR.MINEUR.CORRECTIF ; une évolution incompatible change le MAJEUR et fournit sa migration (ErrorEnvelope v1 → v2), jamais en silence.
- Classification : PUBLIC, INTERNAL, CONFIDENTIAL, SENSITIVE, RESTRICTED, SECRET, LOCAL_ONLY ; LOCAL_ONLY ne sort jamais du PC.
- Montants : `Money` (décimal exact et devise) aux frontières (journal d'audit) ; le moteur de trading v29 calcule en flottants arrondis à la précision de Binance.
- Unités : dans le nom du champ (`_usdt`, `_ms`, `_pct` en pour cent) ; un taux interne est une fraction (`risk_pct = 0.01` pour 1 %).
- Inconnu n'est pas zéro : une valeur inconnue est vide (`None`), jamais 0 ni faux (jetons non donnés par un fournisseur, rendement attendu).
- Confiance : un score de 0 à 1 avec sa méthode ; jamais une probabilité d'avoir raison, jamais une autorisation.
- Immuable : décisions, contrôles du risque, exécutions, avis du comité, appels aux IA et audit sont en ajout seulement ; une correction est une nouvelle ligne.
- Validation : `contrats.validate(schéma, données)` refuse un champ inconnu, absent, du mauvais type, hors bornes ou d'une valeur non permise ; rien n'est corrigé en silence.

## AuditEvent.v2

Trace infalsifiable de chaque opération critique.

| Rubrique | Contrat |
| --- | --- |
| Producteur | bot (un seul écrivain) |
| Consommateur | rapport quotidien, commande audit, diagnostic expert |
| Entrée | acteur, action et type d'événement (Domaine.Entité.Action), objet, avant, après (montants en décimal avec devise), raison, autorisation, résultat, corrélation (la décision) et cause |
| Sortie | ligne chaînée à la précédente par son empreinte ; les lignes v1 restent lisibles et vérifiées |
| Erreurs | journal illisible : achats refusés |
| Droits | écriture par le bot seul ; aucun module ne modifie ni n'efface |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | numéro unique et croissant |
| Trace (audit) | c'est l'audit |
| Fichiers | `trendguard/audit.py` |

## ExecutionAuthorization.v1

Autorisation d'envoyer l'ordre, liée au contrôle du risque.

| Rubrique | Contrat |
| --- | --- |
| Producteur | porte d'exécution |
| Consommateur | exécution (paper) ou moteur d'ordres v29 (réel) |
| Entrée | RiskDecision, mode, réel armé |
| Sortie | autorisée ou non, identifiant, version de la politique, expiration (5 min), restrictions |
| Erreurs | réel non armé, contrôle refusé |
| Droits | réel : ENABLE_LIVE_TRADING et LIVE_TRADING_CONFIRMATION |
| Délai | 5 minutes |
| Nouveaux essais | aucun |
| Unicité | liée à un seul contrôle |
| Trace (audit) | identifiant gardé avec l'achat |
| Fichiers | `trendguard/porte.py` |

## KillSwitch.v1

Arrêt d'urgence : baisse de 40 % depuis le plus haut.

| Rubrique | Contrat |
| --- | --- |
| Producteur | décision du jour |
| Consommateur | porte d'exécution, panneau, rapport |
| Entrée | capital, plus haut, réglage TG_KILL_DRAWDOWN |
| Sortie | plus aucun achat ; reprise prudente après 60 jours de marché haussier, ou commande resume |
| Erreurs | aucune |
| Droits | bot ; levée manuelle bot arrêté |
| Délai | à la décision |
| Nouveaux essais | aucun |
| Unicité | état unique |
| Trace (audit) | déclenchement et reprise |
| Fichiers | `trendguard/bot.py` |

## NoTradeGate.v1

Garde « NO TRADE » du jour.

| Rubrique | Contrat |
| --- | --- |
| Producteur | décision du jour |
| Consommateur | porte d'exécution, raisonnement |
| Entrée | données du jour, mouvement de BTC, perte du jour, place sur le disque |
| Sortie | liste des raisons de ne pas acheter aujourd'hui |
| Erreurs | mesure ratée : contrôle non bloquant |
| Droits | règles fixes |
| Délai | à la décision |
| Nouveaux essais | aucun |
| Unicité | une garde par jour |
| Trace (audit) | dans l'état du bot |
| Fichiers | `trendguard/garde.py` |

## Order.v1

Ordre d'achat au marché et stop de secours posé chez Binance.

| Rubrique | Contrat |
| --- | --- |
| Producteur | moteur v29 (réel) |
| Consommateur | Binance Spot |
| Entrée | quantité, identifiant client unique, stop de secours |
| Sortie | exécution (prix, quantité, frais) ou ordre ambigu |
| Erreurs | fonds insuffisants, ordre refusé, ambigu |
| Droits | clé API : lecture et trading Spot, retrait interdit |
| Délai | délai de ccxt |
| Nouveaux essais | jamais à l'aveugle : un ordre ambigu arrête la paire jusqu'au rapprochement |
| Unicité | intention écrite avant l'ordre, résolue par l'identifiant client au démarrage |
| Trace (audit) | achat et vente |
| Fichiers | `v29/execution.py`, `v29/exchange.py` |

## OrderIntent.v1

Intention d'achat tirée du plan du jour.

| Rubrique | Contrat |
| --- | --- |
| Producteur | exécution (bot_execution.py) |
| Consommateur | porte d'exécution (porte.py) |
| Entrée | crypto, quantité, prix d'entrée, stop, coût, risque, jour de la décision |
| Sortie | intention validée, clé d'unicité jour:crypto:BUY |
| Erreurs | MISSING_FIELD, WRONG_TYPE, OUT_OF_RANGE, INVALID_FIELD, WRONG_VERSION |
| Droits | bot seulement |
| Délai | immédiat |
| Nouveaux essais | aucun (l'achat différé repasse par la porte à chaque essai) |
| Unicité | un achat par crypto et par décision |
| Trace (audit) | contrôle de la porte |
| Fichiers | `trendguard/contrats.py`, `trendguard/bot_execution.py` |

## ReleaseGate.v1

Porte du réel (porte 8), mesurée sur l'état du bot.

| Rubrique | Contrat |
| --- | --- |
| Producteur | feuille de route (chantiers.py) |
| Consommateur | porte d'exécution (achats réels), rapport, Rachelle |
| Entrée | portes 1 à 7, essai paper, réglages, arrêt d'urgence, mode sûr, journaux, alertes, rapport, vérification |
| Sortie | ouverte ou fermée, et chaque condition manquante |
| Erreurs | mesure impossible : porte fermée |
| Droits | aucune option ne la contourne ; fermée, aucun achat réel |
| Délai | une fois par jour |
| Nouveaux essais | à la décision suivante |
| Unicité | une mesure par jour |
| Trace (audit) | raisonnement et rapport quotidien |
| Fichiers | `trendguard/chantiers.py` |

## RiskCheck.v1

Contrôle déterministe du risque avant tout achat.

| Rubrique | Contrat |
| --- | --- |
| Producteur | exécution |
| Consommateur | porte d'exécution |
| Entrée | OrderIntent, portefeuille du moment, réglages de risque, garde, mode sûr |
| Sortie | RiskDecision : APPROVED, REJECTED ou EMERGENCY_BLOCK, chaque contrôle et sa raison |
| Erreurs | contrat refusé = achat refusé |
| Droits | règles fixes, aucune IA |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | même entrée, même décision |
| Trace (audit) | chaque décision, approuvée ou refusée |
| Fichiers | `trendguard/porte.py` |

## SafeModeState.v1

Mode sûr : plus aucun achat, protection et ventes maintenues.

| Rubrique | Contrat |
| --- | --- |
| Producteur | vous (commande mode-sur) |
| Consommateur | porte d'exécution, décision du jour |
| Entrée | actif ou non, raison, date, qui |
| Sortie | achats refusés (EMERGENCY_BLOCK) tant qu'il est actif |
| Erreurs | fichier illisible : mode sûr actif |
| Droits | vous seul |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | état unique |
| Trace (audit) | activation et levée |
| Fichiers | `trendguard/porte.py` |

## TradeRecord.v1

Trade clos relié à sa décision et à son autorisation.

| Rubrique | Contrat |
| --- | --- |
| Producteur | exécution |
| Consommateur | journal des trades, attribution, apprentissage |
| Entrée | achat, vente, raison de la sortie |
| Sortie | résultat, R, meilleur et pire moment, glissement, régime, leçon, identifiants |
| Erreurs | trade sans clôtures : excursions inconnues |
| Droits | bot seulement |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | une vente ferme un seul trade |
| Trace (audit) | vente |
| Fichiers | `trendguard/postmortem.py`, `trendguard/bot.py` |

## AIOpinion.v1

Avis des IA sur le marché (veille).

| Rubrique | Contrat |
| --- | --- |
| Producteur | IA consultées (market_watch.py) |
| Consommateur | veille, noyau de savoir |
| Entrée | actualités du jour |
| Sortie | avis de −1 à +1 par crypto |
| Erreurs | IA en panne ou clé refusée : ignorée |
| Droits | clés saisies masquées ; une IA ne passe jamais d'ordre |
| Délai | 60 s par IA |
| Nouveaux essais | aucun |
| Unicité | un avis par jour |
| Trace (audit) | rapport de la veille |
| Fichiers | `trendguard/market_watch.py` |

## BacktestResult.v1

Verdict d'un backtest : valide, avec réserves, invalide ou rejeté ; prêt pour le moteur de risque ou recherche seulement.

| Rubrique | Contrat |
| --- | --- |
| Producteur | moteur de backtest (moteur_backtest.py) |
| Consommateur | moteur de risque, vous |
| Entrée | backtest de la règle, épreuves, statistique |
| Sortie | mesures par époque, Sharpe probabiliste et dégonflé, note de qualité, réserves, raisons de rejet, limites |
| Erreurs | rejet sans raison, « prêt » sans validité, limites sans « pas une garantie » : refusés |
| Droits | jamais une autorisation : la règle en service ne change pas |
| Délai | étude hors ligne |
| Nouveaux essais | aucun |
| Unicité | un résultat par manifeste |
| Trace (audit) | dans le rapport de validation |
| Fichiers | `trendguard/moteur_backtest.py` |

## CommitteeView.v1

Avis consultatif du comité d'agents sur une crypto.

| Rubrique | Contrat |
| --- | --- |
| Producteur | comité (comite.py) |
| Consommateur | journal financier, Rachelle, panneau |
| Entrée | marché, régime, portefeuille, politique |
| Sortie | recommandation, consensus, confiance (Confidence.v1), incertitude, raisons, votes |
| Erreurs | agent critique absent : BLOCAGE |
| Droits | jamais une autorisation |
| Délai | à la décision |
| Nouveaux essais | aucun |
| Unicité | un avis par crypto et par décision |
| Trace (audit) | journal financier (avis du comité), en ajout seulement |
| Fichiers | `trendguard/comite.py` |

## Confidence.v1

Confiance et incertitude d'un avis (score de 0 à 1, méthode, calibrée ou non, bases ; niveau, inconnues, impact).

| Rubrique | Contrat |
| --- | --- |
| Producteur | comité d'agents |
| Consommateur | Rachelle, panneau, journal |
| Entrée | avis des agents |
| Sortie | Confidence et Uncertainty |
| Erreurs | score hors de [0, 1], méthode absente, niveau inconnu : refusé |
| Droits | une confiance n'est jamais une probabilité d'avoir raison ni une autorisation |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | un avis, une confiance |
| Trace (audit) | avec l'avis |
| Fichiers | `trendguard/contrats.py`, `trendguard/comite.py` |

## EntryPlan.v1

Taille de chaque achat (1 % de risque) sous les plafonds.

| Rubrique | Contrat |
| --- | --- |
| Producteur | stratégie |
| Consommateur | exécution |
| Entrée | signaux, portefeuille, capital, argent disponible, réglages |
| Sortie | quantité, prix, stop, risque, coût par crypto |
| Erreurs | taille sous 10 USDT : pas d'achat |
| Droits | règles fixes |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | une décision par jour |
| Trace (audit) | raisonnement du jour |
| Fichiers | `trendguard/trend_strategy.py` |

## EvolutionChange.v1

Réglage adopté par l'évolution encadrée.

| Rubrique | Contrat |
| --- | --- |
| Producteur | évolution (evolution.py) |
| Consommateur | bot, à la décision suivante |
| Entrée | épreuves sur 8 ans de cours, réglages permis par le niveau |
| Sortie | réglage à l'essai 30 jours, confirmé ou annulé |
| Erreurs | épreuve ratée : rien ne change |
| Droits | jamais le risque cumulé, le nombre de positions, l'arrêt d'urgence ni le mode réel |
| Délai | une fois par jour |
| Nouveaux essais | aucun |
| Unicité | un seul changement à l'essai |
| Trace (audit) | registre des expériences |
| Fichiers | `trendguard/evolution.py`, `trendguard/registre.py` |

## ExpertDiagnosis.v1

Diagnostic expert du bot (noyau cognitif).

| Rubrique | Contrat |
| --- | --- |
| Producteur | diagnostic expert (expert.py) |
| Consommateur | rapport quotidien, Rachelle, panneau |
| Entrée | état, décision, audit, journal, journal du bot, PC, réseau |
| Sortie | verdict, constats avec gravité et preuves, propositions (jamais des actions) |
| Erreurs | outil en panne : constat « inconnu », jamais inventé |
| Droits | lecture seule |
| Délai | 00:45 UTC |
| Nouveaux essais | à la nuit suivante |
| Unicité | un diagnostic par nuit |
| Trace (audit) | fichier du diagnostic, rapport |
| Fichiers | `trendguard/expert.py`, `trendguard/cognitif.py` |

## FinancialAnalysis.v1

Analyse d'une crypto par le cœur financier (aide à la décision).

| Rubrique | Contrat |
| --- | --- |
| Producteur | cœur financier (finance.py) |
| Consommateur | journal financier, Rachelle, rapport, commande finance |
| Entrée | indicateurs, qualité, régime, liens entre cryptos, calendrier, sentiment, prévisions, comité |
| Sortie | signal, à surveiller ou pas de trade avec les raisons, classement indicatif, confiances, preuves, risques, contradictions, conditions d'invalidation |
| Erreurs | « pas de trade » sans raison : refusé |
| Droits | jamais une autorisation |
| Délai | à la décision |
| Nouveaux essais | aucun |
| Unicité | une analyse par crypto et par jour |
| Trace (audit) | journal financier |
| Fichiers | `trendguard/finance.py` |

## FinancialSignal.v1

Signal de la règle au format commun.

| Rubrique | Contrat |
| --- | --- |
| Producteur | cœur financier (finance.py) |
| Consommateur | analyses, journal |
| Entrée | indicateurs de la règle, régime de BTC |
| Sortie | direction, type, force, confiance, horizon, conditions d'entrée et de sortie, risque attendu, version de la règle |
| Erreurs | rendement attendu inconnu : vide |
| Droits | candidat seulement : la porte décide |
| Délai | à la décision |
| Nouveaux essais | aucun |
| Unicité | un signal par crypto et par jour |
| Trace (audit) | analyse |
| Fichiers | `trendguard/finance.py` |

## Forecast.v1

Prévision de fréquence à 30 jours.

| Rubrique | Contrat |
| --- | --- |
| Producteur | cœur financier (finance.py) |
| Consommateur | analyses, journal financier, calibration |
| Entrée | clôtures passées dans un marché comparable |
| Sortie | probabilité de hausse et son intervalle, rendement moyen et intervalle de 80 %, nombre de cas |
| Erreurs | moins de 12 cas : aucune prévision |
| Droits | jamais un prix annoncé ni un ordre |
| Délai | à la décision |
| Nouveaux essais | évaluée à l'échéance |
| Unicité | une prévision par crypto et par jour |
| Trace (audit) | journal financier (évaluation comprise) |
| Fichiers | `trendguard/finance.py` |

## KnowledgeHold.v1

Achat reporté par le noyau de savoir.

| Rubrique | Contrat |
| --- | --- |
| Producteur | noyau de savoir (savoir.py) |
| Consommateur | décision du jour |
| Entrée | avis des sources PROUVÉES sur les cours réels |
| Sortie | crypto à ne pas acheter aujourd'hui |
| Erreurs | noyau illisible : aucun report |
| Droits | peut seulement reporter un achat, jamais vendre ni acheter |
| Délai | à la décision |
| Nouveaux essais | aucun |
| Unicité | un report par crypto et par jour |
| Trace (audit) | reports vérifiés après 7 jours |
| Fichiers | `trendguard/savoir.py` |

## LLMExecution.v1

Un appel à un modèle d'IA.

| Rubrique | Contrat |
| --- | --- |
| Producteur | exécution des modèles (modeles.py) |
| Consommateur | trace des IA, rapport, panneau |
| Entrée | demande (identifiant), modèle, invite et sa version, classe de confidentialité |
| Sortie | identifiant d'exécution, réussi ou non, durée, jetons (inconnus : vides), erreur, repli, raison, empreinte des données envoyées |
| Erreurs | échec : repli sur le modèle suivant, tracé |
| Droits | clés jamais notées |
| Délai | 60 s |
| Nouveaux essais | repli sur un autre modèle (pas le même) |
| Unicité | identifiant unique par appel |
| Trace (audit) | table llm_executions, en ajout seulement |
| Fichiers | `trendguard/modeles.py` |

## ModelBenchmark.v1

Banc d'évaluation d'un modèle d'IA, versionné.

| Rubrique | Contrat |
| --- | --- |
| Producteur | banc (modeles.py) |
| Consommateur | routeur (approbation), fiche du modèle, rapport |
| Entrée | questions à réponse connue : faits, finance, calcul, piège à invention |
| Sortie | justesse par catégorie, durée, erreurs, approuvé ou non et pourquoi |
| Erreurs | appels en erreur : banc non concluant ; justesse sous 75 % ou régression : modèle écarté |
| Droits | aucun modèle en service sans banc réussi |
| Délai | 60 s par question |
| Nouveaux essais | banc à refaire |
| Unicité | un résultat par modèle et par banc |
| Trace (audit) | table llm_benchmarks, en ajout seulement |
| Fichiers | `trendguard/modeles.py` |

## ModelConsensus.v1

Consensus des IA de la veille.

| Rubrique | Contrat |
| --- | --- |
| Producteur | veille (market_watch.py) |
| Consommateur | rapport de la veille, panneau, Rachelle |
| Entrée | avis validés de chaque IA, fiabilité mesurée |
| Sortie | état (STRONG, MODERATE, WEAK, NONE, CONFLICT), accord et désaccord de 0 à 1, désaccords par crypto |
| Erreurs | désaccord net : pas d'avis moyen |
| Droits | conseil seulement : aucun effet sur les ordres |
| Délai | à la veille |
| Nouveaux essais | aucun |
| Unicité | un consensus par jour |
| Trace (audit) | rapport de la veille |
| Fichiers | `trendguard/market_watch.py` |

## ModelDisagreement.v1

Désaccord net entre IA sur un sujet (une crypto, le climat).

| Rubrique | Contrat |
| --- | --- |
| Producteur | veille (market_watch.py) |
| Consommateur | rapport de la veille, panneau, Rachelle |
| Entrée | positions de chaque IA (−1 à +1) |
| Sortie | sujet, type, gravité, positions, issue (NO_DECISION : pas de moyenne) |
| Erreurs | moins de deux positions, type ou gravité inconnus : refusé |
| Droits | conseil seulement |
| Délai | à la veille |
| Nouveaux essais | aucun |
| Unicité | un désaccord par sujet et par rapport |
| Trace (audit) | rapport de la veille |
| Fichiers | `trendguard/contrats.py`, `trendguard/market_watch.py` |

## ModelSelection.v1

Choix du modèle d'IA pour une demande, avec ses raisons.

| Rubrique | Contrat |
| --- | --- |
| Producteur | routeur (modeles.py) |
| Consommateur | exécution des modèles |
| Entrée | modèles connus, confidentialité, santé, budget |
| Sortie | modèles retenus dans l'ordre, raison de chacun, modèles écartés et pourquoi |
| Erreurs | aucun modèle permis : réponse intégrée sans IA |
| Droits | règles fixes |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | même état, même choix |
| Trace (audit) | raison notée avec chaque appel |
| Fichiers | `trendguard/modeles.py` |

## PortfolioAnalysis.v1

Analyse du portefeuille : risque d'un jour, tests de résistance, attribution.

| Rubrique | Contrat |
| --- | --- |
| Producteur | décision du jour (risque.py, stress.py, attribution.py) |
| Consommateur | panneau, rapport, raisonnement |
| Entrée | positions, clôtures, trades clos |
| Sortie | VaR et CVaR à 95 %, pertes par scénario, résultat par crypto |
| Erreurs | données insuffisantes : non mesuré |
| Droits | mesures seulement, aucune décision |
| Délai | à la décision |
| Nouveaux essais | aucun |
| Unicité | une analyse par décision |
| Trace (audit) | dans l'état du bot |
| Fichiers | `trendguard/risque.py`, `trendguard/stress.py`, `trendguard/attribution.py` |

## PromptVersion.v1

Invite enregistrée : version, empreinte exacte, statut.

| Rubrique | Contrat |
| --- | --- |
| Producteur | socle des modèles (modeles.py) |
| Consommateur | exécution des modèles, veille, Rachelle, banc |
| Entrée | texte de l'invite |
| Sortie | « version#empreinte » notée avec chaque appel |
| Erreurs | texte changé sans nouvelle version, ou invite non active : IA non appelée |
| Droits | une invite ne change que par une revue du code (et son test) |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | une empreinte par version |
| Trace (audit) | trace des appels |
| Fichiers | `trendguard/modeles.py` |

## Scenario.v1

Scénarios à 30 jours : fort recul, crise, baisse, central, hausse.

| Rubrique | Contrat |
| --- | --- |
| Producteur | cœur financier (finance.py) |
| Consommateur | analyses, Rachelle |
| Entrée | clôtures passées dans un marché comparable |
| Sortie | probabilité (leur somme fait 1), rendement, volatilité et baisse moyens, conditions d'invalidation |
| Erreurs | somme différente de 1 ou scénario manquant : refusé |
| Droits | information |
| Délai | à la demande |
| Nouveaux essais | aucun |
| Unicité | un jeu par crypto et par jour |
| Trace (audit) | analyse |
| Fichiers | `trendguard/finance.py` |

## Signal.v1

Cassure du plus haut de 30 jours, momentum 90 jours.

| Rubrique | Contrat |
| --- | --- |
| Producteur | stratégie (trend_strategy.py) |
| Consommateur | plan d'achat |
| Entrée | clôtures, volatilité, régime de BTC |
| Sortie | cryptos à acheter, classées |
| Erreurs | données insuffisantes : pas de signal |
| Droits | règles fixes |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | même bougie, même signal |
| Trace (audit) | raisonnement du jour |
| Fichiers | `trendguard/trend_strategy.py` |

## StrategyDecision.v1

Décision de la règle pour une crypto : candidate, pas de trade, tenue ou vendue.

| Rubrique | Contrat |
| --- | --- |
| Producteur | moteur de stratégie (moteur_strategie.py) |
| Consommateur | raisonnement, rapport, Rachelle |
| Entrée | fiche compilée, indicateurs du jour, régime de BTC, positions |
| Sortie | chaque condition avec ses valeurs, raisons au format commun, taille et stop d'un achat, coût aller-retour |
| Erreurs | « pas de trade » sans raison, candidate incomplète : refusées |
| Droits | jamais une autorisation : la porte décide |
| Délai | à la décision |
| Nouveaux essais | aucun |
| Unicité | une décision par crypto et par jour |
| Trace (audit) | écart avec la règle exécutée signalé au journal du bot et au rapport |
| Fichiers | `trendguard/moteur_strategie.py` |

## StrategySpec.v1

Fiche déclarative de la règle : langage sûr, versionnée, verrouillée sur le code.

| Rubrique | Contrat |
| --- | --- |
| Producteur | moteur de stratégie (moteur_strategie.py) |
| Consommateur | validation, backtest, décisions, Rachelle, commande regle |
| Entrée | réglages en vigueur |
| Sortie | conditions d'achat et de vente, stops, taille, contraintes, coûts, liquidité, réglages et plages validées, verrous du code |
| Erreurs | fiche hors du langage permis : refusée, raison dite |
| Droits | lecture seule : une fiche ne passe aucun ordre |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | même fiche, même empreinte |
| Trace (audit) | version et empreinte dans chaque décision |
| Fichiers | `trendguard/moteur_strategie.py` |

## BacktestManifest.v1

Manifeste d'un backtest : tout ce qu'il faut pour le refaire à l'identique.

| Rubrique | Contrat |
| --- | --- |
| Producteur | moteur de backtest (moteur_backtest.py) |
| Consommateur | rapport de validation, vous |
| Entrée | configuration, données, fiche de la règle, code |
| Sortie | version du code, empreintes des données, de la configuration, du code, de l'environnement et du résultat, graine |
| Erreurs | résultat refait différent : INVALID |
| Droits | lecture seule : aucun ordre |
| Délai | étude hors ligne |
| Nouveaux essais | aucun |
| Unicité | mêmes entrées, même empreinte du résultat |
| Trace (audit) | dans le rapport de validation |
| Fichiers | `trendguard/moteur_backtest.py` |

## DecisionRecord.v1

Décision du jour dans le journal financier, avec la date limite de ses données (data_cutoff_at).

| Rubrique | Contrat |
| --- | --- |
| Producteur | décision du jour |
| Consommateur | journal financier, lignée des trades |
| Entrée | jour, mode, réglages, régime, capital, gardes, qualité, fin des données utilisées |
| Sortie | identifiant D-jour ; contrainte : les données s'arrêtent avant la décision (pas de regard vers le futur) |
| Erreurs | données postérieures à la décision : refusées par la base |
| Droits | bot seulement |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | une décision par jour |
| Trace (audit) | journal financier |
| Fichiers | `trendguard/donnees.py`, `trendguard/bot.py` |

## Experiment.v1

Expérience notée au registre (réglages, données, version du code, résultats).

| Rubrique | Contrat |
| --- | --- |
| Producteur | évolution, études (registre.py) |
| Consommateur | rejeu, contrôle, rapport |
| Entrée | réglages, période, données, code |
| Sortie | identifiant E0001…, empreinte des données, mesures, rejouable à l'identique |
| Erreurs | rejeu différent : signalé |
| Droits | écriture par le bot |
| Délai | à chaque épreuve |
| Nouveaux essais | rejeu sur demande |
| Unicité | un identifiant par expérience |
| Trace (audit) | fichier du registre |
| Fichiers | `trendguard/registre.py` |

## Feature.v1

Indicateur versionné du magasin (technique, quantitatif).

| Rubrique | Contrat |
| --- | --- |
| Producteur | cœur financier (finance.py) |
| Consommateur | analyses, journal financier |
| Entrée | bougies clôturées |
| Sortie | valeur (vide si non mesurable), version, fin des données |
| Erreurs | données postérieures à la bougie : refusé (regard vers le futur) |
| Droits | lecture seule |
| Délai | à la décision |
| Nouveaux essais | aucun |
| Unicité | un indicateur par crypto, jour et version |
| Trace (audit) | journal financier |
| Fichiers | `trendguard/finance.py`, `trendguard/donnees.py` |

## Instrument.v1

Référentiel des instruments (cryptos suivies).

| Rubrique | Contrat |
| --- | --- |
| Producteur | cœur financier (finance.py) |
| Consommateur | analyses, panneau |
| Entrée | liste des cryptos, première bougie, veto de la veille, règles de cotation connues |
| Sortie | identifiant stable place:marché:BASE-DEVISE, classe, devise, calendrier 24/7, état |
| Erreurs | règle de cotation inconnue : vide, jamais inventée |
| Droits | lecture seule |
| Délai | à la décision |
| Nouveaux essais | aucun |
| Unicité | un identifiant par instrument |
| Trace (audit) | analyse du jour |
| Fichiers | `trendguard/finance.py` |

## MarketData.v1

Bougies journalières clôturées de Binance.

| Rubrique | Contrat |
| --- | --- |
| Producteur | Binance (klines publiques) |
| Consommateur | décision du jour, régimes, qualité |
| Entrée | 21 paires, bougies jusqu'à la dernière clôture |
| Sortie | clôtures et volumes, bougies en cours exclues ; bougies incohérentes (OHLCV.v1) : crypto écartée du jour |
| Erreurs | réseau : décision reportée, BTC manquant ou incohérent : reportée |
| Droits | lecture publique |
| Délai | 3 essais par paire |
| Nouveaux essais | 3 essais, attente croissante |
| Unicité | lecture seule |
| Trace (audit) | note de qualité sur 100 |
| Fichiers | `trendguard/bot.py`, `trendguard/qualite.py` |

## OHLCV.v1

Règles d'une bougie : plus haut ≥ ouverture, clôture et plus bas ; plus bas ≤ ouverture et clôture ; volume ≥ 0.

| Rubrique | Contrat |
| --- | --- |
| Producteur | Binance (klines publiques) |
| Consommateur | décision du jour, qualité des données |
| Entrée | bougies des 90 derniers jours de chaque crypto |
| Sortie | nombre de bougies incohérentes |
| Erreurs | une bougie incohérente : la crypto est écartée du jour (BTC : décision reportée) |
| Droits | lecture seule |
| Délai | à la décision |
| Nouveaux essais | à la décision suivante |
| Unicité | mêmes bougies, même verdict |
| Trace (audit) | journal du bot, qualité |
| Fichiers | `trendguard/contrats.py`, `trendguard/bot.py`, `trendguard/qualite.py` |

## Envelope.v1

Enveloppe commune d'un message entre modules.

| Rubrique | Contrat |
| --- | --- |
| Producteur | tout module (contrats.py) |
| Consommateur | tout module |
| Entrée | type, schéma et version, producteur, contenu, classification, provenance |
| Sortie | identifiant, corrélation, cause, trace, horodatage UTC à la milliseconde |
| Erreurs | UUID, version, date ou classification invalides : refusé |
| Droits | selon la classification |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | identifiant unique du message |
| Trace (audit) | selon le contenu |
| Fichiers | `trendguard/contrats.py` |

## ErrorEnvelope.v2

Erreur au format commun (code, catégorie, gravité, nouvel essai permis).

| Rubrique | Contrat |
| --- | --- |
| Producteur | tout module |
| Consommateur | journal d'audit, rapport |
| Entrée | erreur d'un contrat |
| Sortie | code, catégorie, message, nouvel essai permis, gravité, source, détails, date, corrélation ; une erreur v1 est migrée |
| Erreurs | catégorie ou gravité inconnues : refusé |
| Droits | aucun |
| Délai | immédiat |
| Nouveaux essais | selon « retryable » |
| Unicité | aucune |
| Trace (audit) | selon l'erreur |
| Fichiers | `trendguard/contrats.py` |

## HealthReport.v1

Rapport quotidien et centre de sécurité.

| Rubrique | Contrat |
| --- | --- |
| Producteur | rapport (report.py) |
| Consommateur | vous (e-mail, panneau) |
| Entrée | état du bot, PC, journal, GitHub |
| Sortie | constats conformes, à corriger, informations |
| Erreurs | source illisible : information |
| Droits | lecture seule |
| Délai | 00:30 UTC |
| Nouveaux essais | rattrapé au retour du PC |
| Unicité | un rapport par jour |
| Trace (audit) | rapports gardés 14 jours |
| Fichiers | `trendguard/report.py` |

## PanelCommand.v1

Commandes du panneau : marche, arrêt, sélection, démarrage.

| Rubrique | Contrat |
| --- | --- |
| Producteur | vous (panneau) |
| Consommateur | contrôle du bot |
| Entrée | en-tête du panneau, origine, mot de passe |
| Sortie | fait ou refusé, message |
| Erreurs | origine inconnue, mot de passe raté (blocage après 5 échecs) |
| Droits | ce PC seulement |
| Délai | immédiat |
| Nouveaux essais | aucun |
| Unicité | état cible (marche ou arrêt) |
| Trace (audit) | journal du bot |
| Fichiers | `panel/server.py`, `panel/control.py` |
