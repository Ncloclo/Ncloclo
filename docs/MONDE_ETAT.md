# Modèle du monde et raisonnement

Mesuré le 2026-10-09 par `python trendguard_bot.py monde` (étape 25 du prompt
maître, [`MONDE.md`](MONDE.md)). Une hypothèse n'est jamais un fait ; une
simulation jamais une observation.

- **Prêt** (READY_FOR_ADVANCED_REASONING_AND_WORLD_MODEL) ; qualité 100/100.
- tendance haussière, 0 position(s) ; et si tout perdait 30 % : capital 95,97,
  baisse 4,0 %.

## L'état du monde

| Domaine | élément | valeur | nature | source | date |
| --- | --- | --- | --- | --- | --- |
| marché | tendance | haussière | interprété | lecture du marché de la règle | 2026-10-08 |
| marché | volatilité | normale | interprété | lecture du marché de la règle | 2026-10-08 |
| marché | appétit pour le risque | risk-on | interprété | lecture du marché de la règle | 2026-10-08 |
| marché | phase | reprise | interprété | lecture du marché de la règle | 2026-10-08 |
| données | qualité du jour | 100 | observé | note des données | 2026-10-08 |
| portefeuille | capital | 95,97 | observé | état du bot | 2026-10-08 |
| portefeuille | baisse depuis le plus haut | 4,03 | déduit | déduit du capital et du plus haut | 2026-10-08 |
| portefeuille | positions | 0 | observé | état du bot | 2026-10-08 |
| portefeuille | exposition (% du capital) | 0 | observé | moteur de portefeuille | 2026-10-08 |
| stratégie | crypto la plus proche d'une cassure | TRX (+3,6 %) | observé | raisonnement de la règle | 2026-10-08 |
| stratégie | garde « pas de trade » | laisse acheter | observé | garde du jour | 2026-10-08 |
| sûreté | arrêt d'urgence | non | observé | état du bot | 2026-10-08 |
| sûreté | mode sûr | non | observé | fichier du mode sûr | 2026-10-09 |
| système | dernier cycle (minutes) | 0,8 | observé | état du bot | 2026-10-09T10:16 |

## Ce qui a changé

Rien depuis le dernier relevé.

## Le raisonnement du jour

1. (interprété) le marché est lu comme : tendance haussière, volatilité normale,
   risk-on, reprise — lecture du marché (BTC, volatilité)
2. (observé) 21 cryptos examinées ; la plus proche d'une cassure : TRX (+3,6 %)
   — raisonnement de la règle
3. (déduit) la règle n'achète qu'une cassure du plus haut de 30 jours, en marché
   haussier, si la porte d'exécution l'autorise — fiche de la règle
4. (décision) aucun achat aujourd'hui — décision du jour

## Et si… (scénarios, sans probabilité)

| Choc | stops touchés | capital | baisse | arrêt d'urgence |
| --- | --- | --- | --- | --- |
| −30 % | aucun | 95,97 | 4,0 % | non |
| −20 % | aucun | 95,97 | 4,0 % | non |
| −10 % | aucun | 95,97 | 4,0 % | non |
| +10 % | aucun | 95,97 | 4,0 % | non |

Hypothèse : toutes les cryptos détenues bougent ensemble ; prix d'achat pris
pour prix du jour.

## Qualité du raisonnement

| Famille | poids | état | mesure |
| --- | --- | --- | --- |
| intégrité du modèle du monde | 15 % | conforme | 14 éléments du monde, chacun daté, sourcé et classé |
| justesse du raisonnement | 20 % | conforme | observations → interprétation → règle → décision |
| preuves et provenance | 10 % | conforme | chaque étape et chaque élément ont leur source |
| raisonnement causal | 10 % | conforme | relations de cause à effet : étape 26 (causal) |
| incertitude | 10 % | conforme | aucune probabilité inventée ; aucune valeur absente remplacée |
| scénarios | 10 % | conforme | 4 scénarios de choc, chacun marqué « scénario » |
| robustesse | 10 % | conforme | un choc plus fort ne donne jamais plus de capital |
| sécurité | 5 % | conforme | le raisonnement ne décide rien : la règle décide, la porte autorise |
| reproductibilité | 5 % | conforme | histoire de 3 jour(s), dans l'ordre |
| performance | 5 % | conforme | en 0,00 s |
