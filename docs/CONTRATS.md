# Contrats entre les modules de TrendGuard

Tiré du registre `trendguard/contrats.py` (`python -m trendguard.contrats` le réécrit) ; un test vérifie que ce document et le registre restent identiques.

| Contrat | Producteur | Consommateur | Niveau |
| --- | --- | --- | --- |
| `AuditEvent.v1` | bot (un seul écrivain) | rapport quotidien, commande audit | critique |
| `ExecutionAuthorization.v1` | porte d'exécution | exécution (paper) ou moteur d'ordres v29 (réel) | critique |
| `KillSwitch.v1` | décision du jour | porte d'exécution, panneau, rapport | critique |
| `NoTradeGate.v1` | décision du jour | porte d'exécution, raisonnement | critique |
| `Order.v1` | moteur v29 (réel) | Binance Spot | critique |
| `OrderIntent.v1` | exécution (bot_execution.py) | porte d'exécution (porte.py) | critique |
| `RiskCheck.v1` | exécution | porte d'exécution | critique |
| `SafeModeState.v1` | vous (commande mode-sur) | porte d'exécution, décision du jour | critique |
| `TradeRecord.v1` | exécution | journal des trades, attribution, apprentissage | critique |
| `AIOpinion.v1` | IA consultées (market_watch.py) | veille, noyau de savoir | cœur |
| `EntryPlan.v1` | stratégie | exécution | cœur |
| `EvolutionChange.v1` | évolution (evolution.py) | bot, à la décision suivante | cœur |
| `KnowledgeHold.v1` | noyau de savoir (savoir.py) | décision du jour | cœur |
| `Signal.v1` | stratégie (trend_strategy.py) | plan d'achat | cœur |
| `MarketData.v1` | Binance (klines publiques) | décision du jour, régimes, qualité | données |
| `HealthReport.v1` | rapport (report.py) | vous (e-mail, panneau) | support |
| `PanelCommand.v1` | vous (panneau) | contrôle du bot | support |

## AuditEvent.v1

Trace infalsifiable de chaque opération critique.

| Rubrique | Contrat |
| --- | --- |
| Producteur | bot (un seul écrivain) |
| Consommateur | rapport quotidien, commande audit |
| Entrée | acteur, action, objet, avant, après, raison, autorisation, résultat |
| Sortie | ligne chaînée à la précédente par son empreinte |
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

## MarketData.v1

Bougies journalières clôturées de Binance.

| Rubrique | Contrat |
| --- | --- |
| Producteur | Binance (klines publiques) |
| Consommateur | décision du jour, régimes, qualité |
| Entrée | 21 paires, bougies jusqu'à la dernière clôture |
| Sortie | clôtures et volumes, bougies en cours exclues |
| Erreurs | réseau : décision reportée, BTC manquant : reportée |
| Droits | lecture publique |
| Délai | 3 essais par paire |
| Nouveaux essais | 3 essais, attente croissante |
| Unicité | lecture seule |
| Trace (audit) | note de qualité sur 100 |
| Fichiers | `trendguard/bot.py`, `trendguard/qualite.py` |

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
