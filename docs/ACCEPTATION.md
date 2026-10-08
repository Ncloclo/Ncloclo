# Acceptation du paper (critères AC-001 à AC-044)

Mesuré le 2026-10-08 par `python trendguard_bot.py acceptation` (étape 13 du
prompt maître, [`ACCEPTATION_PAPER.md`](ACCEPTATION_PAPER.md)). Le paper valide
la préparation ; il n'autorise jamais le réel.

## Verdict

- **BLOQUÉ** : étape suivante correction et observation.
- Note de préparation 96,7/100 (prêt) ; critères P0 non satisfaits : 0.
- Observation : 3 jour(s), 4 événement(s) (1 trade(s) clos, 2 position(s)
  ouverte(s)) ; il en faut 30 jours et 30 événements au moins (90 jours
  recommandés).
- Manque : période d'observation : 3 jour(s) sur 30 et 4 événement(s) sur 30 au
  moins (stratégie de tendance journalière : environ 50 trades par an au
  backtest (430 depuis 2018), 100 événements demanderaient près d'un an).

## Familles

| Famille | poids | note |
| --- | --- | --- |
| sécurité | 20 % | 100 |
| comptabilité | 15 % | 100 |
| exécution | 15 % | 100 |
| risque | 15 % | 100 |
| données | 10 % | 100 |
| reproductibilité | 10 % | 100 |
| observabilité | 5 % | 67 |
| sécurité informatique | 5 % | 100 |
| performance | 5 % | 67 |

## Critères

| Critère | priorité | état | preuve |
| --- | --- | --- | --- |
| AC-001 Démarrage contrôlé (compte, capital, essai daté) | P0 | conforme | essai commencé le 2026-10-05, capital de départ connu ; 1 test(s) du dépôt |
| AC-002 Aucun accès financier réel | P0 | conforme | mode paper : argent fictif, les clés Binance ne servent pas ; 2 test(s) du dépôt |
| AC-003 États du compte et transitions | P0 | conforme | compte actif ; 3 test(s) du dépôt |
| AC-004 Journal (ledger) en ajout seul | P0 | conforme | schéma v5 ; 2 décision(s), 0 contrôle(s) du risque, 1 ordre(s), 1 trade(s) ; intégrité vérifiée ; 1 test(s) du dépôt |
| AC-005 Conservation comptable | P0 | conforme | liquidités + coût des positions − (capital de départ + résultat réalisé) = 0,0000 ; 1 test(s) du dépôt |
| AC-006 Notionnel de chaque position | P0 | conforme | 2 position(s) : coût = quantité × prix × (1 + frais) ; 1 test(s) du dépôt |
| AC-007 Résultat réalisé de chaque trade | P0 | conforme | 1 trade(s) clos recalculé(s) à 0,01 près ; 1 test(s) du dépôt |
| AC-008 Valorisation au marché tenue à jour | P1 | conforme | capital revalorisé il y a 1 minute(s) |
| AC-009 Prix moyen des exécutions | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-010 Cycle de vie des ordres | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-011 Idempotence : jamais deux fois le même ordre | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-012 Exécutions partielles | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-013 Glissement mesuré sur chaque exécution | P0 | conforme | glissement noté sur 1 sortie(s) ; achats au cours + 0,1 % (modèle) |
| AC-014 Coûts de transaction | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-015 Horodatage et ordre des événements | P0 | conforme | aucune sortie avant son achat, aucun achat daté dans le futur ; 1 test(s) du dépôt |
| AC-016 Aucune information future | P0 | conforme | prouvé par 3 test(s) du dépôt |
| AC-017 Carnet d'ordres simulé | P1 | sans objet | mode carnet non activé : ordres au marché sur bougies journalières ; le carnet est seulement lu pour différer un achat (apprentissage) |
| AC-018 Liquidité minimale | P0 | conforme | volume moyen de 30 jours d'au moins 5 millions de dollars exigé avant tout achat ; 1 test(s) du dépôt |
| AC-019 Contrôle du risque avant chaque achat | P0 | conforme | schéma v5 ; 2 décision(s), 0 contrôle(s) du risque, 1 ordre(s), 1 trade(s) ; intégrité vérifiée ; 1 test(s) du dépôt |
| AC-020 Limites de risque bloquantes | P0 | conforme | 2 position(s) pour 20 au plus, risque engagé 2,1 % pour 10 % ; 2 test(s) du dépôt |
| AC-021 Levier | P0 | conforme | exposition 24 % du capital, jamais de levier |
| AC-022 Marge | P0 | sans objet | Binance Spot sans marge ni emprunt |
| AC-023 Arrêt d'urgence (kill switch) | P0 | conforme | arrêt d'urgence à −40 % ; achat bloqué en 0,20 ms ; 2 test(s) du dépôt |
| AC-024 Mode sûr | P0 | conforme | mode sûr prêt ; sans évaluation du risque valide, aucun achat ; 2 test(s) du dépôt |
| AC-025 Reproductibilité déterministe | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-026 Reproductibilité des tirages (graine) | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-027 Backtest et paper comparés | P1 | conforme | 3 achat(s) identique(s) ; écarts expliqués 0, inexpliqués 0 |
| AC-028 Attendu et observé (calibrage) | P1 | conforme | glissement des sorties (%) : attendu 0,10, observé 3,35 sur 1 sortie(s) |
| AC-029 Latence du contrôle | P1 | conforme | contrôle du risque d'un achat : 0,14 ms au 95e centile (cible 50 ms) |
| AC-030 Disponibilité ≥ 99,9 % | P1 | non conforme | 88,0 % sur 7 jours (cible 99,9 %) |
| AC-031 Traçabilité de chaque ordre | P0 | conforme | 0 trade(s) remontent à leur ordre, leur contrôle du risque et leur décision ; antérieurs au journal financier, signalés et exclus : ICP acheté le 2026-10-06, AAVE (ouverte) achetée le 2026-10-06, ADA (ouverte) achetée le 2026-10-06 ; 1 test(s) du dépôt |
| AC-032 Audit complet | P0 | conforme | intact : 1 événement(s), dernier le 2026-10-07 à 02:01 ; 1 test(s) du dépôt |
| AC-033 Comptes isolés | P0 | conforme | une base par mode (paper, testnet, réel) : trendguard_paper.db |
| AC-034 Portefeuilles isolés | P0 | conforme | bot libre dans sa propre base (savoir) : il ne touche jamais le portefeuille principal ; 1 test(s) du dépôt |
| AC-035 Qualité des données avant usage | P0 | conforme | qualité des données du 2026-10-07 : 100/100 (achat refusé sous 50) ; 1 test(s) du dépôt |
| AC-036 Donnée manquante jamais transformée en zéro | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-037 Scénarios extrêmes | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-038 Pannes de composants (chaos) | P0 | conforme | prouvé par 3 test(s) du dépôt |
| AC-039 Permissions | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-040 Entrées malveillantes | P0 | conforme | prouvé par 3 test(s) du dépôt |
| AC-041 Couverture des tests | P1 | non mesurable | 683 tests dans le dépôt ; la couverture en lignes n'est pas mesurée (outil de couverture absent) |
| AC-042 Invariants (tests de propriétés) | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-043 Tests de régression à chaque envoi | P0 | conforme | tous les tests rejoués par GitHub à chaque envoi (.github/workflows/checks.yml) |
| AC-044 Rejeu déterministe | P0 | conforme | prouvé par 2 test(s) du dépôt |

## Paper et backtest de la même période

Du 2026-10-05 au 2026-10-07 : 3 achat(s) en paper, 3 au backtest, 3
identique(s).
