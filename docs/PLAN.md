# Mission, objectifs et plan

Mesuré le 2026-10-09 par `python trendguard_bot.py objectifs` (étape 27 du
prompt maître, [`OBJECTIFS.md`](OBJECTIFS.md)). Un plan n'est pas une
exécution ; une estimation n'est pas une promesse.

**Mission** : aller au réel en sécurité, quand les preuves le permettent.

- porte du réel : 6 point(s) sur 10 ; estimation de son ouverture entre le
  2026-12-04 et le 2027-01-21 (au plus tôt dans 57 jours ; une estimation, pas
  une promesse) ; 2 action(s) à votre main.
- **Prête** (READY_FOR_AUTONOMOUS_PLANNING) ; note 100/100.

## Les objectifs

| Objectif | critère | état | qui agit | priorité | avancement |
| --- | --- | --- | --- | --- | --- |
| Portes 1 à 7 | franchies | atteint | le code (Pull Requests fusionnées par vous) | P1 | 100 % |
| Essai paper : durée | 3 jour(s) sur 60 au moins | en cours | le temps | P0 | 6 % |
| Essai paper : trades clos | 3 sur 10 au moins | en cours | le marché | P0 | 30 % |
| Stratégie validée | aucun réglage à l'essai | atteint | le bot (fin des essais de l'évolution) | P1 | 100 % |
| Arrêt d'urgence et mode sûr | prêts | atteint | vous | P0 | 100 % |
| Journal d'audit intact | intact : 3 événement(s), dernier le 2026-10-08 à 17:20 | atteint | le bot | P0 | 100 % |
| Journal financier intact | schéma v5 ; 3 décision(s), 0 contrôle(s) du risque, 3 ordre(s), 3 trade(s) ; intégrité vérifiée | atteint | le bot | P0 | 100 % |
| Alertes configurées | au moins un canal | atteint | vous | P1 | 100 % |
| Sécurité du dernier rapport | à corriger : Alertes | attend une action | vous | P1 | 0 % |
| Vérification sans ordre | à faire ou à refaire : python trendguard_bot.py verify (Binance réel, aucun ordre) | attend une action | vous | P0 | 0 % |
| paper accepté (AC-001 à AC-044) | note 94,7/100 sous 95; période d'observation : 3 jour(s) sur 30 et 6 événement(s) sur 30 au moins (stratégie de tendance journalière : environ 50 trades par an au backtest (430 depuis 2018), 100 événements demanderaient près d'un an); écart paper/backtest non mesuré | en cours | le temps et le bot | P0 | 0 % |
| porte du réel | tous les points ci-dessus | à venir | le bot (mesure chaque jour) | P0 | 64 % |
| réel simulé (testnet) | votre réglage, après la porte précédente | à venir | vous | P1 | 0 % |
| réel contrôlé | votre réglage, après la porte précédente | à venir | vous | P1 | 0 % |

## Chemin critique

Essai paper : durée → Vérification sans ordre → porte du réel → réel simulé
(testnet) → réel contrôlé

## Estimation (prévision, pas une garantie)

- Jours de paper restants : 56 ; trades clos manquants : 7.
- Ouverture de la porte du réel : au plus tôt le 2026-12-04, plus probablement
  vers le 2026-12-04, au plus tard le 2027-01-21 si le marché donne deux fois
  moins de trades que le backtest.
- Ce qui décide de la date : durée de l'essai paper.

## Ce que vous seul pouvez faire

- Sécurité du dernier rapport : à corriger : Alertes
- Vérification sans ordre : à faire ou à refaire : python trendguard_bot.py
  verify (Binance réel, aucun ordre)
- Et toujours à vous seul : armer le réel (deux réglages), changer les plafonds
  de risque, lever le mode sûr.

## Qualité

| Famille | poids | état | mesure |
| --- | --- | --- | --- |
| intégrité des objectifs | 15 % | conforme | 14 objectifs, chacun avec son critère, son état et qui agit |
| justesse du plan | 20 % | conforme | chaque dépendance existe |
| contraintes et sûreté | 15 % | conforme | armer le réel, le risque, le mode sûr : vous seul ; rien lancé |
| risque et politiques | 10 % | conforme | le plan suit la porte du réel et les paliers ; la porte d'exécution reste l'autorité |
| vérification | 10 % | conforme | l'estimation est une fourchette, pas une promesse |
| qui fait quoi | 10 % | conforme | qui agit pour chaque objectif (vous, le bot, le temps, le marché) |
| replanification | 5 % | conforme | recalculé à chaque appel depuis les mesures du jour |
| ressources et calendrier | 5 % | conforme | fourchette ordonnée |
| sécurité | 5 % | conforme | aucune IA, aucun agent ne peut modifier un objectif critique |
| observabilité | 5 % | conforme | chemin critique lisible |
