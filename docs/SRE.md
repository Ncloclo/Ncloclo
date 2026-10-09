# Fiabilité et autoréparation encadrée (étape 32 du prompt maître)

Au-dessus du plan de contrôle ([`PLAN_DE_CONTROLE.md`](PLAN_DE_CONTROLE.md)),
ce module cherche les anomalies, prévoit les pannes, mesure la capacité,
calcule le rayon d'impact d'un service en panne et propose une remédiation
classée. Une alerte n'est pas un incident confirmé ; une cause corrélée n'est
pas une cause prouvée ; une prévision n'est pas une certitude.

Code : [`trendguard/sre.py`](../trendguard/sre.py). Tests :
[`tests/test_sre.py`](../tests/test_sre.py).

```text
python trendguard_bot.py sre                         # anomalies, prévisions, capacité, remédiations
python trendguard_bot.py sre --out docs/SRE_ETAT.md
```

Dernier état : [`SRE_ETAT.md`](SRE_ETAT.md). Chaque nuit, le rapport donne la
ligne « Fiabilité (SRE) » et garde la mesure du jour (place libre, taille de
la base) dans `<bot>.sre.json`, sur ce PC seulement.

## Ce qui est mesuré

| Exigence | Dans TrendGuard |
| --- | --- |
| Anomalies (§11) | arrêts imprévus sur 7 jours contre les 3 semaines d'avant (les arrêts demandés ne comptent jamais), écart d'horloge avec Binance, place sur le disque, âge de la dernière sauvegarde, note des données ; chacune avec sa référence, la valeur vue, sa gravité et sa preuve |
| Prévisions (§16) | disque plein (pente de la place libre sur les jours mesurés) ; arrêt imprévu à l'heure où ils commencent le plus souvent (probabilité = part des jours avec un arrêt sur 14) ; toujours marquées PREDICTION |
| Capacité (§17) | place libre, marge avant l'alerte, taille de la base et sa croissance par jour |
| Rayon d'impact (§14) | les services qui dépendent, directement ou non, du service en panne ; CRITICAL si un service de palier 0 est touché |
| Causes racines (§13) | soupçonnées tant qu'elles ne sont pas prouvées |

## Les remédiations (§19-21)

| Niveau | Exemples | Automatique ? |
| --- | --- | --- |
| LOW | relance du bot par son superviseur ; données, Binance, veille, savoir : le bot attend ou réessaie seul | oui, si c'est déjà la règle en place |
| MEDIUM | relancer le superviseur, corriger un canal d'alerte | non : vous |
| HIGH | libérer le disque, sauvegarde manquée, registre des politiques | non : vous |
| CRITICAL | base, journal financier, journal d'audit, arrêt d'urgence | jamais : vous seul |

Le contrat `SREReport.v1` refuse une prévision présentée comme une
certitude et une remédiation automatique qui ne serait pas LOW.

## La note (§39-40)

Intelligence SRE 15 %, incidents 10 %, causes racines 10 %, prévisions 10 %,
résilience 10 %, autoréparation encadrée 10 %, sécurité 15 %, observabilité
5 %, reprise 5 %, performance 5 %, gouvernance 5 %. Bandes READY (95),
RELEASE_CANDIDATE (90), VALIDATING (80), DEVELOPMENT (70), REJECTED ; un P0
(autoréparation risquée, autorisation propre) : NOT_READY.

## Ce qui ne s'applique pas

- **Kubernetes, plusieurs régions, bascule de centre de données, ingénierie
  du chaos en production** : un seul PC.
- **Remédiation automatique au-delà de la relance du bot** : tout le reste
  vous est proposé, jamais fait seul.
