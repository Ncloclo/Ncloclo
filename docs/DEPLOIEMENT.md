# Exécution réelle par paliers (critères AC-001 à AC-060)

Mesuré le 2026-10-08 par `python trendguard_bot.py deploiement` (étape 17 du
prompt maître, [`DEPLOIEMENT_REEL.md`](DEPLOIEMENT_REEL.md)). Aucune promotion
automatique : seul un réglage que vous changez fait monter d'un palier.

## Palier

- En vigueur : **paper (argent fictif, vrai marché)**.
- Suivant : réel simulé (testnet de Binance) ; porte fermée : paper accepté
  (AC-001 à AC-044).
- Exécutions : aucune exécution mesurée.

| Palier | état | porte pour y entrer |
| --- | --- | --- |
| ombre (rejeu et backtest, sans portefeuille) | franchi | — |
| paper (argent fictif, vrai marché) | en vigueur | ✔ backtest validé (docs/VALIDATION.md) |
| réel simulé (testnet de Binance) | à venir | ✘ paper accepté (AC-001 à AC-044) ; ✘ vous : RUN_MODE=live, BINANCE_TESTNET=true et les deux réglages du réel |
| réel contrôlé | à venir | ✘ porte du réel ouverte (porte 8) ; ✔ porte d'exécution prête (AC-001 à AC-050) ; ✘ vous : BINANCE_TESTNET=false et TG_MAX_CAPITAL fixé |
| production limitée | à venir | ✘ 30 jours en réel ; ✘ 10 trades réels clos ; ✔ aucun arrêt d'urgence en cours ; ✘ vous : TG_PALIER_REEL=limite |
| production | à venir | ✘ 90 jours en réel ; ✘ 30 trades réels clos ; ✘ vous : TG_PALIER_REEL=production |

## Verdict

- **Prête pour un réel par paliers** (READY_FOR_STAGED_LIVE_EXECUTION) : jamais
  une production sans restriction ; chaque palier reste à votre décision.
- Note 97,6/100 (prête) ; critères P0 non satisfaits : 0.

## Familles

| Famille | poids | note |
| --- | --- | --- |
| sûreté | 25 % | 100 |
| rapprochement | 15 % | 100 |
| fiabilité | 15 % | 100 |
| gestion des ordres | 10 % | 100 |
| résilience face à Binance | 10 % | 100 |
| risque et politiques | 10 % | 100 |
| observabilité | 5 % | 86 |
| sécurité | 5 % | 100 |
| performance | 5 % | 67 |

## Critères

| Critère | priorité | état | preuve |
| --- | --- | --- | --- |
| AC-001 Aucun ordre sans la porte d'exécution | P0 | conforme | une seule route d'achat vers Binance, après la porte et la validation finale ; 2 test(s) du dépôt |
| AC-002 Aucun ordre sans autorisation | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-003 Aucun ordre hors des politiques | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-004 Aucun ordre hors des limites de risque | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-005 Aucun dépassement de montant | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-006 Aucun dépassement de position | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-007 Aucun levier | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-008 Aucun ordre en double | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-009 Idempotence (même identifiant client au nouvel essai) | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-010 Ordres parents et enfants | P1 | sans objet | un achat est un seul ordre au marché (moins de 0,03 % du volume du jour) : rien à découper |
| AC-011 Adaptateur de Binance isolé | P1 | conforme | une seule route d'achat vers Binance, après la porte et la validation finale ; 1 test(s) du dépôt |
| AC-012 Santé de Binance vérifiée | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-013 Reprise de session | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-014 Reconnexion | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-015 Ordre inconnu retrouvé | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-016 Exécutions partielles | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-017 Exécutions dédupliquées | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-018 Positions rapprochées | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-019 Soldes rapprochés | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-020 Frais rapprochés | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-021 Rapprochement continu | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-022 Arrêt d'urgence | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-023 Tout vendre en urgence | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-024 Fail-closed | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-025 Limitation du débit | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-026 Contre-pression | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-027 Ordre des événements | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-028 Événements enregistrés avant l'envoi | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-029 Reprise après plantage | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-030 Reprise depuis une sauvegarde | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-031 Rejeu des événements | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-032 Bascule vers un autre courtier | P1 | sans objet | un seul courtier (Binance Spot) : une panne de Binance diffère les achats et laisse les stops posés chez lui |
| AC-033 Isolation de sécurité | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-034 Secrets protégés | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-035 Changement des clés | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-036 Horloge synchronisée | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-037 Auditabilité | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-038 Traçabilité | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-039 Délai d'exécution mesuré | P1 | non mesurable | mesuré seulement en réel : sur le testnet puis au réel contrôlé ; 1 test(s) du dépôt |
| AC-040 Glissement mesuré | P1 | conforme | glissement noté sur 3 sortie(s) (modèle du paper ; le réel est noté de même) ; 1 test(s) du dépôt |
| AC-041 Écart de mise en œuvre mesuré | P1 | non mesurable | aucun achat mesuré depuis l'étape 17 (écart entre le prix payé et le cours de décision) ; 1 test(s) du dépôt |
| AC-042 Incidents détectés | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-043 Incidents signalés | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-044 Mode sûr | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-045 Arrêt d'urgence immédiat | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-046 Comptes isolés | P0 | conforme | une base par mode (paper, réel) : trendguard_paper.db ; 1 test(s) du dépôt |
| AC-047 Stratégies isolées | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-048 Routage entre courtiers | P1 | sans objet | un seul courtier (Binance Spot) |
| AC-049 Courtier non autorisé bloqué | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-050 Instrument non autorisé bloqué | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-051 Aucune promotion automatique du paper au réel | P0 | conforme | palier paper (argent fictif, vrai marché), lu dans vos réglages ; le bot ne change jamais de palier seul ; 2 test(s) du dépôt |
| AC-052 Plafonds du réel contrôlé | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-053 Accord humain | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-054 Rapprochement à la reprise | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-055 Protection contre les exécutions en double | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-056 Événements dans le désordre | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-057 Réponse de Binance validée | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-058 Risque recalculé après l'exécution | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-059 Piste d'audit complète | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-060 Aucun contournement d'un P0 | P0 | conforme | une seule route d'achat vers Binance, après la porte et la validation finale ; 1 test(s) du dépôt |
