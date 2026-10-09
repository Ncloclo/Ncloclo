# Exécution réelle par paliers (étape 17 du prompt maître)

Le moteur d'exécution réelle de TrendGuard est v29 (gestion des ordres,
protection, rapprochement avec Binance), éprouvé par ses tests sur un faux
Binance fidèle. L'étape 17 l'a examiné point par point et ajouté ce qui manquait
pour aller au réel prudemment : des paliers, des plafonds pour les premiers
mois, la mesure de la qualité des exécutions. Aucune promotion automatique :
seul un réglage que vous changez fait monter d'un palier.

```text
ombre → paper → réel simulé (testnet) → réel contrôlé → production limitée → production
```

Code : [`trendguard/deploiement.py`](../trendguard/deploiement.py), la porte
d'exécution ([`trendguard/porte.py`](../trendguard/porte.py), « Plafonds du
palier »), v29 ([`v29/execution.py`](../v29/execution.py),
[`v29/reconciliation.py`](../v29/reconciliation.py)). Tests :
[`tests/test_deploiement.py`](../tests/test_deploiement.py),
[`tests/test_live_execution.py`](../tests/test_live_execution.py),
[`tests/test_live_planned.py`](../tests/test_live_planned.py).

```text
python trendguard_bot.py deploiement                         # palier, portes, 60 critères
python trendguard_bot.py deploiement --out docs/DEPLOIEMENT.md
```

Dernier examen : [`DEPLOIEMENT.md`](DEPLOIEMENT.md).

## Les paliers et leurs portes

| Palier | Ce qu'il est | Porte pour y entrer (mesurée) | Votre réglage |
| --- | --- | --- | --- |
| Ombre | rejeu et backtest de la règle, sans portefeuille | — | — |
| Paper | argent fictif, vrai marché | backtest validé (`docs/VALIDATION.md`) | `RUN_MODE=paper` |
| Réel simulé | testnet de Binance : vrais ordres, faux argent | paper accepté (AC-001 à AC-044) | `RUN_MODE=live`, `BINANCE_TESTNET=true`, les deux réglages du réel |
| Réel contrôlé | vrai argent, plafonné | porte du réel ouverte (60 jours de paper, 10 trades…), porte d'exécution prête (AC-001 à AC-050) | `BINANCE_TESTNET=false`, `TG_MAX_CAPITAL` fixé |
| Production limitée | plafonds relâchés | 30 jours en réel, 10 trades réels clos, aucun arrêt d'urgence en cours | `TG_PALIER_REEL=limite` |
| Production | plafonds de la règle seulement | 90 jours en réel, 30 trades réels clos | `TG_PALIER_REEL=production` |

Une porte ouverte ne fait pas monter : elle dit seulement que les conditions
mesurées sont réunies ; c'est vous qui changez le réglage. Sans
`TG_PALIER_REEL`, le réel commence toujours au réel contrôlé.

## Les plafonds du palier (réel seulement)

| Palier | achats par jour | montant acheté par jour | capital confié au bot |
| --- | --- | --- | --- |
| Réel contrôlé | 2 au plus | 40 % du capital au plus | `TG_MAX_CAPITAL` exigé |
| Production limitée | 4 au plus | 75 % du capital au plus | `TG_MAX_CAPITAL` exigé |
| Production | plafonds de la règle | plafonds de la règle | au choix |

Ils s'ajoutent aux règles de la porte d'exécution (risque par achat et cumulé,
25 % du capital par position, positions au plus, argent disponible, arrêt
d'urgence…) et ne font que réduire : un achat au-delà est refusé, avec la
raison. En paper, rien ne change. Contrôle « Plafonds du palier » de la porte
(`porte.v3`), politique POL-LIVE-STAGE (`politiques-1.2.0`).

## La qualité des exécutions (§40-41)

À chaque achat, l'écart entre le prix payé et le cours de la décision (en
points de base, + = payé plus cher) ; en réel, en plus, le délai entre l'envoi
de l'ordre et son exécution. Les 100 dernières mesures sont gardées dans l'état
du bot, lues par le rapport, Rachelle et l'examen. En paper, l'écart comprend
le mouvement du marché entre la clôture et l'achat, et le glissement du modèle ;
le délai réel se mesure à partir du testnet.

## L'examen : AC-001 à AC-060, note, verdict (§53, §55, §62)

Chaque critère du prompt est mesuré (une seule route d'achat, palier lu dans
vos réglages, qualité des exécutions, une base par mode) ou prouvé par les
tests du dépôt (ordre inconnu retrouvé, réponse perdue, exécutions partielles
ou en double, rapprochement des positions, des soldes et des frais, reprise
après plantage, horloge de Binance…). Note pondérée : sûreté 25 %,
rapprochement 15 %, fiabilité 15 %, gestion des ordres 10 %, résilience face à
Binance 10 %, risque et politiques 10 %, observabilité 5 %, sécurité 5 %,
performance 5 %. Un P0 raté ou non mesurable : NOT_READY.

Le verdict **READY_FOR_STAGED_LIVE_EXECUTION** dit que le chemin par paliers
peut commencer ; jamais une production sans restriction. Ce qui ne se mesure
qu'en réel (délai d'exécution réel) est noté « non mesurable » jusqu'au
testnet.

## Exigences de l'étape 17 → TrendGuard

| Exigence | Dans TrendGuard |
| --- | --- |
| Gestion des ordres, états (§5, §13) | v29 : FLAT → OPENING → OPEN → CLOSING, HALTED ; intention enregistrée avant l'envoi |
| Exactement une exécution, réponse perdue (§15-16, §50) | identifiant client unique, recherche par identifiant, introuvable → arrêt puis adoption après vérification ; horloge refusée → même identifiant |
| Exécutions partielles, frais, positions, soldes (§17-20) | quantité exécutée comptée, frais en BNB ou en crypto, positions et soldes de Binance font foi |
| Rapprochement continu, écart critique (§21-22, §51) | au démarrage et à chaque cycle ; ordre ou position inconnus : arrêt |
| Reprise, instantanés (§25-27) | reprise du contexte après plantage, superviseur, sauvegardes vérifiées et restaurées pour de vrai |
| Débit, contre-pression (§28-29) | limites d'ordres conditionnels de Binance respectées, panne réseau : achats différés sans bloquer les protections |
| Arrêt d'urgence, tout vendre (§30-31) | arrêt d'urgence, vente d'urgence de tout, mode sûr |
| Modes de sécurité (§32) | mode sûr, arrêt d'urgence, achats différés (dégradé), ventes toujours possibles |
| Paliers, réel contrôlé, aucune promotion automatique (§33-35, §56-57) | `deploiement.py` : palier lu dans vos réglages, portes mesurées, plafonds appliqués par la porte d'exécution |
| Sécurité, identité, IA (§36-38) | clé sans retrait, secrets masqués, une seule route d'achat ; aucune IA ni agent avec un droit critique |
| Qualité d'exécution (§40-41) | écart au cours de décision et délai mesurés à chaque achat |
| Horloge (§43) | heure de Binance, resynchronisée chaque heure |
| Critères, note, verdict (§53, §55, §62) | `deploiement.py`, contrat `LiveDeploymentReport.v1`, [`DEPLOIEMENT.md`](DEPLOIEMENT.md) |

## Ce qui ne s'applique pas

- **Plusieurs courtiers, routage intelligent, bascule** (§9-10, §23-24) : un
  seul courtier, Binance Spot ; une panne de Binance diffère les achats et
  laisse les stops posés chez lui.
- **Ordres parents et enfants, algorithmes TWAP, VWAP** (§6-8) : un achat est
  un seul ordre au marché, moins de 0,03 % du volume du jour.
- **Événements en flux, base dédiée, API du moteur** (§14, §44-46) : le journal
  financier, le journal d'audit et le contexte de v29 en tiennent lieu ; aucune
  API ne permet d'acheter.
- **Plusieurs comptes** (§46-47) : un seul compte, une base par mode ; le bot
  libre a son propre portefeuille fictif.
