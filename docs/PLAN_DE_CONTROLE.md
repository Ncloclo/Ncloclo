# Plan de contrôle de la production (étape 18 du prompt maître)

Une vue d'exploitation de tout TrendGuard, en lecture seule : la santé de
chaque service, ses dépendances, les objectifs de service et leur budget
d'erreur, les incidents du moment regroupés par cause avec leur procédure, les
sauvegardes (âge, restauration d'essai), les changements et la configuration,
et ce que le superviseur a le droit de faire. Il voit, il n'agit pas.

```text
services → santé (vivant, prêt, métier) → dépendances (cascade, point unique)
→ objectifs et budget d'erreur → incidents P0 à P4 par cause, avec procédure
→ changements, configuration, sauvegardes (RPO, RTO) → superviseur borné → verdict
```

Code : [`trendguard/controle.py`](../trendguard/controle.py). Tests :
[`tests/test_controle.py`](../tests/test_controle.py).

```text
python trendguard_bot.py controle                     # l'état du jour, incidents notés (ouverts, clos)
python trendguard_bot.py controle --out docs/CONTROLE.md
```

Dernier état : [`CONTROLE.md`](CONTROLE.md). Chaque nuit, le rapport en donne
une ligne (« Plan de contrôle »), et Rachelle le lit.

## Les services

| Palier | Services |
| --- | --- |
| 0 — sûreté critique | base du bot, porte d'exécution, moteur de risque, arrêt d'urgence et mode sûr, journal d'audit, journal financier |
| 1 — finance critique | ordinateur, Internet, Binance, boucle du bot, superviseur, données du jour, sauvegardes |
| 2 — intelligence | alertes, veille des annonces |
| 3 — support | rapport de la nuit, noyau de savoir, panneau |

Chaque service est jugé vivant, prêt et correct pour le métier : un bot qui
tourne sans avoir pris sa décision du jour est dégradé ; une mesure impossible
est « non mesuré », jamais une réussite. Les dépendances donnent la cascade :
quand Binance tombe, le bot, les données du jour et le moteur de risque sont
touchés, mais un seul incident est ouvert, celui de Binance, avec la liste de ce
qu'il touche. Points uniques de défaillance mesurés : l'ordinateur d'abord,
puis Internet et Binance.

## Incidents et procédures

Gravité selon le palier du service en panne (0 : P0, 1 : P1…) ; un arrêt
d'urgence sur ordre inconnu est un P0, un arrêt d'urgence sur baisse un P1.
Chaque incident a sa procédure versionnée (empreinte de son texte) : bot
arrêté, arrêt d'urgence, ordre inconnu chez Binance, journal abîmé, sauvegarde,
Binance indisponible, données abîmées, clé exposée, alertes en panne, écart de
la porte, moteur de risque sans évaluation, disque plein. La commande
`controle` note chaque incident à sa première apparition et le clôt quand il
disparaît (`<bot>.incidents.json`).

## Objectifs de service

| Objectif | Cible |
| --- | --- |
| bot en marche sur 7 jours | 99 % |
| décision du jour prise après la clôture | chaque jour |
| données du jour d'au moins 50 sur 100 | chaque jour |
| sauvegarde de moins de 26 heures (RPO) | toujours |
| rapport de la nuit fait | chaque nuit |

Un budget d'erreur épuisé recommande de geler les changements ; rien n'est
appliqué seul. Le RTO est mesuré : le temps de rouvrir la dernière sauvegarde,
d'en relire l'état et de vérifier son journal financier (cible 60 secondes).

## Le superviseur et l'autonomie

Le superviseur relance le bot après un plantage et peut poser le mode sûr ; le
moteur d'autorisation lui refuse tout le reste (changer le risque, lever
l'arrêt d'urgence ou le mode sûr, autoriser un achat, acheter, armer le réel,
saisir des clés, fusionner du code, changer les réglages). Niveaux d'autonomie
: aucun automatisme sans limite (L5) ; le plus haut est le bot (L4), qui achète
et vend sous la porte d'exécution.

## L'examen : AC-001 à AC-060, note, verdict (§45-47, §52)

Chaque critère est mesuré (disponibilité, santé, graphe, objectifs, capacité,
RPO, RTO, superviseur) ou prouvé par les tests du dépôt. Note pondérée : sûreté
20 %, fiabilité 15 %, observabilité 10 %, incidents 10 %, reprise 10 %,
sécurité 10 %, changements 10 %, capacité 5 %, rapprochement 5 %, opérations
humaines 5 %. Un P0 raté, ou un incident P0 ouvert : NOT_READY. Le verdict
**READY_FOR_PRODUCTION_CONTROLLED_OPERATIONS** n'est jamais une exploitation
autonome sans limite.

## Exigences de l'étape 18 → TrendGuard

| Exigence | Dans TrendGuard |
| --- | --- |
| Registre des services, paliers (§5) | 18 services, du palier 0 au palier 3 |
| Dépendances (§6) | graphe, cascade, cycles, dépendances extérieures, points uniques de défaillance |
| Santé (§7) | vivant, prêt, correct pour le métier ; non mesuré n'est jamais une réussite |
| SLO, budget d'erreur (§8-9) | 5 objectifs mesurés, budget consommé, gel recommandé |
| Alertes, incidents (§11-13) | alertes dédupliquées (canaux), un incident par cause, P0 à P4, ouverts et clos |
| Procédures (§14) | 12 procédures versionnées |
| Superviseur borné, autonomie (§15-17, §44) | relance et mode sûr seulement ; tout le reste refusé par le moteur d'autorisation ; aucun L5 |
| Changements, configuration (§18-20) | code par Pull Request fusionnée par vous, contrôles GitHub, retour à la version précédente ; empreinte de la configuration et versions des règles |
| Capacité (§22) | disque et mémoire |
| Reprise après sinistre (§25-26) | sauvegarde chaque nuit, restauration d'essai, RPO et RTO mesurés |
| Mise en production (§32-33) | paliers du réel (étape 17), porte du réel, porte d'exécution |

## Ce qui ne s'applique pas

- **Mise à l'échelle, files de messages, plusieurs régions, bascule de site**
  (§23, §27-28) : un seul ordinateur ; en réel, les stops restent posés chez
  Binance pendant une panne.
- **Accès d'urgence (« break-glass »)** (§30) : un seul propriétaire ; chaque
  action passe par vos outils masqués.
- **API et événements du plan de contrôle** (§40-41) : la commande `controle`,
  le rapport de la nuit et Rachelle en tiennent lieu.
- **Options activables avec expiration** (§21) : les réglages se changent dans
  l'environnement, par vous ; leur empreinte est suivie.
