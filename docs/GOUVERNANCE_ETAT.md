# Gouvernance de l'architecture

Mesuré le 2026-10-09 par `python trendguard_bot.py gouvernance` (documents
transverses du prompt maître, [`GOUVERNANCE.md`](GOUVERNANCE.md)). Lecture
seule.

- **VALIDATING** ; note 89/100.
- VALIDATING (89/100) ; portes de qualité : 8 PASS, 1 FAIL (fiabilité), 1
  WAIVED ; RACI : 0 écart(s) ; contournements : 0.

## Les dix portes de qualité

| Porte | P0 | résultat | preuve |
| --- | --- | --- | --- |
| fonctionnel | non | PASS | 25 composants, chacun avec ses tests |
| contrats | oui | PASS | 66 contrats au registre, document à jour |
| sécurité | oui | PASS | aucune adresse hors de la liste blanche, aucune capacité offensive |
| performance | non | WAIVED | pas de banc global : chaque moteur mesure son temps dans son examen |
| fiabilité | non | FAIL | disponibilité sur 7 jours 87,4 % (objectif 99 %) |
| intégrité des données | oui | PASS | journal d'audit intact (3 événements), une source de vérité par donnée |
| observabilité | non | PASS | aucune exception large ignorée sans raison écrite |
| reprise après panne | non | PASS | 18 services, chacun avec sa procédure de reprise |
| reproductibilité | non | PASS | versions des bibliothèques figées (requirements-docker.txt) |
| gouvernance | oui | PASS | RACI : 32 responsabilités, un seul responsable chacune ; frontières tenues |

## RACI normalisée (un seul responsable par responsabilité)

| Plan | responsabilité | A | R | C | I |
| --- | --- | --- | --- | --- | --- |
| A Fondation | Stockage canonique (base, journal financier) | `donnees.py` | `donnees.py` | `controle.py` | `report_health.py` |
| A Fondation | Contrats de données | `contrats.py` | `contrats.py` | — | `chantiers.py` |
| A Fondation | Journal d'audit | `audit.py` | `audit.py` | `cyber.py` | vous |
| B Données et connaissances | Qualité des données | `qualite.py` | `qualite.py`, `garde.py` | `perception.py` | `report_health.py` |
| B Données et connaissances | Recherche externe et vérification des sources | `recherche.py` | `recherche.py`, `savoir.py`, `market_watch.py` | `modeles.py` | — |
| B Données et connaissances | Mémoire durable | `memoire.py` | `memoire.py` | `recherche.py` | — |
| C Perception et cognition | Perception | `perception.py` | `perception.py` | `qualite.py` | — |
| C Perception et cognition | Raisonnement (diagnostic) | `cognitif.py` | `cognitif.py`, `expert.py` | `monde.py` | vous |
| C Perception et cognition | Orchestration des agents | `comite.py` | `comite.py`, `agents.py` | `cognitif.py` | — |
| C Perception et cognition | Modèles d'IA (LLM) | `modeles.py` | `modeles.py` | `apprentissage.py` | — |
| D Intelligence du monde | État du monde | `monde.py` | `monde.py`, `regimes.py` | `perception.py` | — |
| D Intelligence du monde | Modèle causal | `causal.py` | `causal.py` | `trend_strategy.py` | — |
| D Intelligence du monde | Simulation (jumeau) | `jumeau.py` | `jumeau.py`, `trend_strategy.py` | `acceptation.py` | — |
| E Planification | Objectifs et plans | `objectifs.py` | `objectifs.py` | `acceptation.py`, `autorisation.py` | vous |
| F Intelligence métier | Intelligence financière | `finance.py` | `finance.py`, `evenements.py`, `savoir.py` | `regimes.py` | — |
| F Intelligence métier | Quantification (risque d'un jour, résistance) | `moteur_quant.py` | `moteur_quant.py`, `risque.py`, `stress.py` | — | — |
| F Intelligence métier | Stratégie (la règle) | `trend_strategy.py` | `trend_strategy.py`, `moteur_strategie.py` | `evolution.py` | vous |
| F Intelligence métier | Backtest et études | `moteur_backtest.py` | `moteur_backtest.py`, `replay.py`, `strategy_lab.py` | — | — |
| F Intelligence métier | Allocation du portefeuille | `moteur_portefeuille.py` | `moteur_portefeuille.py`, `selection.py` | `moteur_risque.py` | — |
| F Intelligence métier | Essai paper et son acceptation | `acceptation.py` | `bot.py`, `acceptation.py` | — | vous |
| G Sûreté et contrôle | Risque | `moteur_risque.py` | `moteur_risque.py`, `garde.py` | `moteur_portefeuille.py` | vous |
| G Sûreté et contrôle | Règles de politique | `politique.py` | `politique.py` | `moteur_risque.py` | — |
| G Sûreté et contrôle | Autorité et permissions | `autorisation.py` | `autorisation.py` | `politique.py` | vous |
| G Sûreté et contrôle | Validation finale avant l'ordre | `porte.py` | `porte.py`, `bot_execution.py` | `autorisation.py` | — |
| H Opérations réelles | Exécution des ordres | `bot_execution.py` | `bot_execution.py` | `porte.py` | vous |
| H Opérations réelles | Paliers du réel | `deploiement.py` | `deploiement.py` | `acceptation.py` | vous |
| H Opérations réelles | Opérations de production | `controle.py` | `controle.py`, `autonomy.py`, `maintenance.py` | `cyber.py` | vous |
| H Opérations réelles | Alertes | `alerts.py` | `alerts.py` | — | vous |
| I Apprentissage | Apprentissage et évolution encadrée | `apprentissage.py` | `apprentissage.py`, `evolution.py`, `learning.py` | `jumeau.py` | vous |
| J Sécurité | Cybersécurité | `cyber.py` | `cyber.py`, `report_security.py` | `controle.py` | vous |
| K Interface humaine | Interface humaine | `interface.py` | `server.py`, `assistant.py`, `interface.py` | — | vous |
| K Interface humaine | Armer le réel, changer le risque, lever le mode sûr | vous | vous | `autorisation.py` | — |

## Qui peut quoi

| Module | observer | recommander | bloquer | autoriser | exécuter |
| --- | --- | --- | --- | --- | --- |
| `qualite.py` | oui | non | données abîmées : décision reportée | non | non |
| `recherche.py` | oui | oui | non (seule une annonce de Binance bloque, via la garde) | non | non |
| `memoire.py` | oui | oui | non | non | non |
| `perception.py` | oui | oui | non | non | non |
| `monde.py` | oui | oui | non | non | non |
| `causal.py` | oui | oui | non | non | non |
| `jumeau.py` | oui | oui | non | non | non |
| `cognitif.py` | oui | oui | non | non | non |
| `objectifs.py` | oui | oui | non | non | non |
| `moteur_risque.py` | oui | oui | BLOCAGE DU RISQUE | non | non |
| `politique.py` | oui | oui | signale un écart (la porte applique) | non | non |
| `autorisation.py` | oui | non | REFUS D'AUTORISATION | oui (actions de l'opérateur) | non |
| `porte.py` | oui | non | BLOCAGE FINAL | un achat, après un contrôle approuvé | non |
| `bot_execution.py` | oui | non | échec d'exécution | non | oui (seul à envoyer un ordre) |
| `cyber.py` | oui | oui | BLOCAGE DE SÉCURITÉ | non | défensif uniquement |
| `controle.py` | oui | oui | opérationnel (redémarrage) | non | opérations autorisées |
| `apprentissage.py` | oui | oui | modèle dégradé ; évolution gelée en réel contrôlé | non | non |
| vous | oui | oui | arrêt d'urgence, mode sûr | oui (armer le réel, le risque) | non (le bot exécute) |

## Une source de vérité par donnée

| Donnée | propriétaire | où |
| --- | --- | --- |
| Bougies validées | `qualite.py` | état du bot (note des données) ; Binance fait foi |
| Journal financier (achats, ventes, frais) | `donnees.py` | base du bot, tables fin_ |
| Limites de risque | `config.py` | réglages TG_RISK_* (vous seul) |
| Règles de politique | `politique.py` | registre des politiques (code versionné) |
| Autorisations | `autorisation.py` | décisions du moteur d'autorisation |
| Ordres et positions | `bot_execution.py` | état du bot (paper) ; Binance (réel) |
| Incidents | `controle.py` | <bot>.incidents.json |
| Versions des modèles | `apprentissage.py` | fiches des modèles (code versionné) |
| Connaissances | `memoire.py` | reconstruites depuis les sources, jamais copiées |
| État du monde | `monde.py` | <bot>.monde.json |
| Résultats de simulation | `jumeau.py` | à la demande, jamais en production |
| Observations | `perception.py` | à la demande, depuis l'état du bot et les caches |
| Journal d'audit | `audit.py` | <bot>.audit.jsonl (chaîné) |

## Frontières critiques (vérifiées dans le code)

| Frontière | tenue | preuve |
| --- | --- | --- |
| perception → ordres | oui | aucun lien direct |
| jumeau → ordres | oui | aucun lien direct |
| objectifs → ordres | oui | aucun lien direct |
| monde → ordres | oui | aucun lien direct |
| causal → ordres | oui | aucun lien direct |
| recherche → ordres | oui | aucun lien direct |
| memoire → ordres | oui | aucun lien direct |
| cognitif → ordres | oui | aucun lien direct |
| expert → ordres | oui | aucun lien direct |
| comite → ordres | oui | aucun lien direct |
| agents → ordres | oui | aucun lien direct |
| modeles → ordres | oui | aucun lien direct |
| savoir → ordres | oui | aucun lien direct |
| finance → ordres | oui | aucun lien direct |
| apprentissage → ordres | oui | aucun lien direct |
| cyber → ordres | oui | aucun lien direct |
| controle → ordres | oui | aucun lien direct |
| assistant → ordres | oui | aucun lien direct |
| interface → ordres | oui | aucun lien direct |
| server → ordres | oui | aucun lien direct |
| tout le code → Binance (achat) | oui | 1 appel(s) d'achat ; après la porte et la validation finale : oui |
| risque → porte → autorisation | oui | le moteur de risque nourrit le contrôle, contrôle avant autorisation |
| jumeau → production | oui | aucune bibliothèque réseau, aucune écriture dans le bot |
| perception → décision | oui | aucune dépendance interdite |
| sécurité → exécution | oui | bloque, gèle, mode sûr ; aucune permission créée |
| plan de contrôle → risque | oui | aucune limite de risque modifiée |
