# Mission, objectifs et plan (étape 27 du prompt maître)

**Mission** : aller au réel en sécurité, quand les preuves le permettent.

La mission est découpée en objectifs mesurables, chacun avec son critère, son
état, qui doit agir (vous, le bot, le temps, le marché) et ses dépendances ;
le chemin critique dit ce qui décide de la date ; une estimation donne une
fourchette pour l'ouverture de la porte du réel. Un plan n'est pas une
exécution, une estimation n'est pas une promesse : ce module ne lance rien.

Code : [`trendguard/objectifs.py`](../trendguard/objectifs.py). Tests :
[`tests/test_objectifs.py`](../tests/test_objectifs.py).

```text
python trendguard_bot.py objectifs                   # mission, objectifs, chemin critique, estimation
python trendguard_bot.py objectifs --out docs/PLAN.md
```

Dernier plan : [`PLAN.md`](PLAN.md).

## Les objectifs

| Objectif | Qui agit |
| --- | --- |
| 60 jours d'essai paper | le temps |
| 10 trades clos | le marché |
| journaux d'audit et financier intacts | le bot |
| stratégie validée (aucun réglage à l'essai) | le bot |
| arrêt d'urgence et mode sûr prêts | vous (s'il faut reprendre) |
| alertes configurées, sécurité du rapport sans défaut | vous |
| vérification sans ordre sur Binance réel | vous, une fois l'essai assez long |
| paper accepté (AC-001 à AC-044) | le temps et le bot |
| porte du réel ouverte | le bot la mesure chaque jour |
| réel simulé (testnet), puis réel contrôlé | vous, par vos réglages |

États : atteint, en cours, attend une action, à venir, attend votre accord.
Chaque objectif a son avancement (par exemple 20 jours sur 60).

## L'estimation

Les jours de paper qui restent et les trades qui manquent, au rythme du
backtest (environ 49 trades par an) : au plus tôt (rythme une fois et demie
plus rapide), le plus probable, au plus tard (rythme deux fois plus lent). Le
chemin critique dit ce qui décide de la date (la durée de l'essai ou les
trades). Les actions qui vous reviennent s'y ajoutent.

## Ce qu'aucun plan ne fait

Armer le réel, changer les plafonds de risque, lever le mode sûr : à vous
seul, vérifié par le moteur d'autorisation à chaque calcul du plan. Aucune
exécution automatique (le contrat `PlanReport.v1` la refuse).

## La qualité (§69)

Intégrité des objectifs 15 %, justesse du plan 20 %, contraintes et sûreté 15 %,
risque et politiques 10 %, vérification 10 %, qui fait quoi 10 %,
replanification
5 %, ressources et calendrier 5 %, sécurité 5 %, observabilité 5 %. Verdict :
**READY_FOR_AUTONOMOUS_PLANNING**, qui veut dire « planifier », jamais
« exécuter ».

## Ce qui ne s'applique pas

- **Planification multi-agents, réseaux de tâches hiérarchiques, ordonnanceur
  de ressources** (§14, §20-22, §28-31) : peu d'objectifs, mesurés chaque jour ;
  aucun agent ne reçoit de tâche.
- **Énergie, ingénierie, recherche** (§41-44) : hors du métier du bot.
- **Exécution du plan** : jamais ; chaque objectif avance par le temps, le
  marché, le bot dans ses limites, ou vous.
