# Documentation TrendGuard — sommaire

Tous les documents du dossier `docs/`, rangés par question. Le mode d'emploi
du bot (installation, commandes, panneau) est dans le
[`README.md`](../README.md) à la racine.

## Comprendre le bot

| Document | Ce qu'on y trouve |
| --- | --- |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | comment le code est rangé, qui dépend de qui, règles communes |
| [`APPRENTISSAGE.md`](APPRENTISSAGE.md) | ce que le bot apprend librement (carnets, ruse, prévisions) |
| [`EVOLUTION.md`](EVOLUTION.md) | les réglages que le bot peut changer seul, sous épreuves |
| [`RAPPORT.md`](RAPPORT.md) | le rapport quotidien de 00:30 UTC : sécurité, diagnostic, envoi |

## Les preuves : études reproductibles

| Document | Question posée |
| --- | --- |
| [`TRENDGUARD_REPORT.md`](TRENDGUARD_REPORT.md) | la stratégie est-elle rentable hors échantillon ? |
| [`ROBUSTESSE.md`](ROBUSTESSE.md) | les résultats tiennent-ils quand tout se dégrade ? |
| [`EXAMEN.md`](EXAMEN.md) | le bot est-il intelligent et rusé ? |
| [`SELECTION.md`](SELECTION.md) | quelles cryptos trader, et quand vendre ? |
| [`ADAPTATION.md`](ADAPTATION.md) | le bot peut-il « apprendre et s'adapter » ? |
| [`STRATEGIES.md`](STRATEGIES.md) | le bot peut-il « apprendre la meilleure stratégie » ? |
| [`REGIMES.md`](REGIMES.md) | dans quelles conditions de marché la stratégie gagne, où échoue-t-elle ? |
| [`PORTEFEUILLE.md`](PORTEFEUILLE.md) | la taille des achats de la règle est-elle la bonne ? |
| [`QUANT.md`](QUANT.md) | que disent les statistiques sur les cryptos suivies ? |
| [`COMITE_ETUDE.md`](COMITE_ETUDE.md) | le comité d'agents aurait-il aidé à mieux acheter ? |

Chacune indique en tête la commande qui la régénère, sauf
`TRENDGUARD_REPORT.md` (régénéré par `python trendguard_bot.py strategy
research --data data`). Ce sont des diagnostics : aucune étude ne change une
règle du bot.

## La plateforme cognitive : agents, IA et connaissance

| Document | Ce qu'on y trouve |
| --- | --- |
| [`COGNITIF.md`](COGNITIF.md) | le noyau cognitif : plan en graphe de tâches, orchestration, vérification croisée ; la décision n'est jamais autorisée seule |
| [`AGENTS.md`](AGENTS.md) | le socle multi-agents et le comité d'agents financiers : avis motivés, toujours consultatifs |
| [`MODELES.md`](MODELES.md) | le socle multi-modèles d'IA : registre, banc d'essai obligatoire, routeur, repli tracé |
| [`FINANCE.md`](FINANCE.md) | le cœur d'intelligence financière : analyse de chaque crypto, consultatif |
| [`MEMOIRE.md`](MEMOIRE.md) | la mémoire et le graphe de connaissances, reconstruits depuis les sources de vérité |
| [`MONDE.md`](MONDE.md), [`MONDE_ETAT.md`](MONDE_ETAT.md) | le modèle du monde et le raisonnement du jour, avec sa dernière mesure |
| [`CAUSAL.md`](CAUSAL.md), [`CAUSES.md`](CAUSES.md) | l'intelligence causale : ce qui cause quoi dans la règle, et ce qui est prouvé |
| [`PERCEPTION.md`](PERCEPTION.md), [`PERCEPTION_ETAT.md`](PERCEPTION_ETAT.md) | la perception multi-sources, recoupée, avec sa dernière mesure |
| [`RECHERCHE.md`](RECHERCHE.md), [`CONNAISSANCES.md`](CONNAISSANCES.md) | ce que le bot lit sur Internet, et ce qu'il en croit |
| [`SAVOIR.md`](SAVOIR.md) | le noyau de savoir : lecture d'Internet et des IA, avis propre |
| [`LIBRE.md`](LIBRE.md) | le bot libre : second portefeuille fictif qui apprend seul, à côté du bot principal |
| [`TRADING.md`](TRADING.md) | ce que le bot a appris des grands traders et de leurs stratégies |
| [`INTERFACE.md`](INTERFACE.md) | l'interface humain-IA du panneau : commandes classées, fraîcheur des données |
| [`CONTRATS.md`](CONTRATS.md) | les contrats entre les modules : types communs, validateur, migrations |
| [`DONNEES.md`](DONNEES.md) | le socle de données : schéma du journal financier |
| [`JUMEAU.md`](JUMEAU.md), [`JUMEAU_ETAT.md`](JUMEAU_ETAT.md) | le jumeau numérique : le paper rejoué par le backtest, isolé de la production |
| [`OBJECTIFS.md`](OBJECTIFS.md), [`PLAN.md`](PLAN.md) | la mission, les objectifs mesurables et le chemin critique vers le réel |
| [`FEUILLE_DE_ROUTE.md`](FEUILLE_DE_ROUTE.md) | les tâches du bot, leurs priorités et leurs dépendances |
| [`PLATEFORME.md`](PLATEFORME.md) | le « prompt maître » appliqué à TrendGuard, vue d'ensemble |

## Les moteurs consultatifs : jamais une décision seuls

Chaque moteur éclaire une décision ; aucun n'achète ni ne vend : la règle
décide, la porte d'exécution autorise.

| Document | Ce qu'on y trouve |
| --- | --- |
| [`MOTEUR_STRATEGIE.md`](MOTEUR_STRATEGIE.md) | la règle en fiche déclarative, versionnée et verrouillée sur le code |
| [`MOTEUR_QUANT.md`](MOTEUR_QUANT.md) | statistiques, lois, anomalies et régimes cachés des cryptos suivies |
| [`MOTEUR_BACKTEST.md`](MOTEUR_BACKTEST.md), [`VALIDATION.md`](VALIDATION.md) | les preuves autour de la boucle de backtest, et son dernier verdict |
| [`MOTEUR_RISQUE.md`](MOTEUR_RISQUE.md) | VaR, pertes au-delà, volatilité et stress, avant et après chaque achat |
| [`MOTEUR_PORTEFEUILLE.md`](MOTEUR_PORTEFEUILLE.md) | poids, exposition et concentration du portefeuille, consultatif |
| [`MOTEUR_POLITIQUE.md`](MOTEUR_POLITIQUE.md) | le registre des 21 politiques d'achat, comparées à la porte d'exécution |
| [`MOTEUR_AUTORISATION.md`](MOTEUR_AUTORISATION.md) | qui peut faire quoi, et ce que vous seul pouvez faire |
| [`PORTE_EXECUTION.md`](PORTE_EXECUTION.md), [`PORTE_EXAMEN.md`](PORTE_EXAMEN.md) | la dernière barrière avant un ordre d'achat, et son examen |

## Sécurité, conformité et passage au réel

| Document | Ce qu'on y trouve |
| --- | --- |
| [`ACCEPTATION_PAPER.md`](ACCEPTATION_PAPER.md), [`ACCEPTATION.md`](ACCEPTATION.md) | les critères qui doivent être réunis avant tout passage au réel |
| [`DEPLOIEMENT_REEL.md`](DEPLOIEMENT_REEL.md), [`DEPLOIEMENT.md`](DEPLOIEMENT.md) | l'exécution réelle par paliers, jamais de promotion automatique |
| [`CYBERSECURITE.md`](CYBERSECURITE.md), [`CYBER.md`](CYBER.md) | cybersécurité et autodéfense, jamais offensif, aucun secret exposé |
| [`PLAN_DE_CONTROLE.md`](PLAN_DE_CONTROLE.md), [`CONTROLE.md`](CONTROLE.md) | le plan de contrôle de la production, en lecture seule |
| [`APPRENTISSAGE_CONTINU.md`](APPRENTISSAGE_CONTINU.md) | le registre des modèles qui apprennent, leur niveau de risque |
| [`GOUVERNANCE.md`](GOUVERNANCE.md), [`GOUVERNANCE_ETAT.md`](GOUVERNANCE_ETAT.md) | la gouvernance de l'architecture : qui peut quoi, une source de vérité par donnée |
| [`INGENIERIE.md`](INGENIERIE.md), [`INGENIERIE_ETAT.md`](INGENIERIE_ETAT.md) | les changements du code tracés, les tests et leur couverture |
| [`SRE.md`](SRE.md), [`SRE_ETAT.md`](SRE_ETAT.md) | la fiabilité : anomalies, prévisions de panne, remédiations |

## Contrôles et suivi

| Document | Ce qu'on y trouve |
| --- | --- |
| [`AUDIT.md`](AUDIT.md) | audit et diagnostic expert du 28 au 30 septembre 2026 : état, constats, plan d'action |
| [`DIAGNOSTIC_CODE.md`](DIAGNOSTIC_CODE.md) | analyse du code du 30 septembre 2026 : mesures, harmonisation, structure |
| [`revues/`](revues/) | revues hebdomadaires écrites par la routine du lundi |
