# Raisonnement et modèle du monde (étape 25 du prompt maître)

Ce que le bot voit du monde à un instant donné, ce qui a changé, comment il a
raisonné pour la décision du jour, et ce qui se passerait si le marché
chutait. Une hypothèse n'est jamais présentée comme un fait, une prévision
jamais comme une certitude, une simulation jamais comme une observation. Ce
module raisonne ; il ne décide rien.

Code : [`trendguard/monde.py`](../trendguard/monde.py). Tests :
[`tests/test_monde.py`](../tests/test_monde.py).

```text
python trendguard_bot.py monde                       # état, changements, raisonnement, scénarios
python trendguard_bot.py monde --out docs/MONDE_ETAT.md
```

Dernier état : [`MONDE_ETAT.md`](MONDE_ETAT.md).

## L'état du monde

Marché (tendance, volatilité, appétit pour le risque, phase), données (qualité
du jour), portefeuille (capital, baisse depuis le plus haut, positions,
exposition), stratégie (crypto la plus proche d'une cassure, garde « pas de
trade »), sûreté (arrêt d'urgence, mode sûr), système (dernier cycle). Chaque
élément a sa date, sa source et sa **nature** :

| Nature | Exemple |
| --- | --- |
| observé | le capital, la note des données, le nombre de positions |
| interprété | « marché haussier » (lecture de BTC par la règle) |
| déduit | la baisse depuis le plus haut, calculée |
| scénario | « et si tout perdait 20 % » |
| décision | « aucun achat aujourd'hui » |

Une valeur absente reste absente. Chaque relevé de la commande garde un
instantané (les 90 derniers) et dit ce qui a changé depuis le précédent ;
l'histoire jour par jour vient du journal financier (marché, capital, garde,
arrêt d'urgence, mode sûr, qualité des données).

## Le raisonnement du jour

Étape par étape, avec sa source : la lecture du marché (interprétation), les
cryptos examinées et la plus proche d'une cassure (observation), la règle
(achat sur cassure du plus haut de 30 jours, en marché haussier, si la porte
l'autorise), les signaux retenus par le marché ou les plafonds, puis la
décision. Seulement ce qui est vérifiable, aucune « chaîne de pensée » cachée.

## Et si… (scénarios)

Chocs de −30, −20, −10 et +10 % sur les cryptos détenues (hypothèse écrite :
elles bougent ensemble, comme en crise) : stops touchés, capital, baisse depuis
le plus haut, arrêt d'urgence atteint ou non ; au-delà de −20 %, la branche
« marché devenu baissier : plus aucun achat ». **Aucune probabilité** : ce sont
des « et si », pas des prévisions.

## La qualité du raisonnement (§61, §100)

| Famille | Poids |
| --- | --- |
| intégrité du modèle du monde | 15 % |
| justesse du raisonnement | 20 % |
| preuves et provenance | 10 % |
| raisonnement causal (étape 26) | 10 % |
| incertitude (aucune probabilité inventée) | 10 % |
| scénarios | 10 % |
| robustesse (un choc plus fort ne donne jamais plus de capital) | 10 % |
| sécurité, reproductibilité, performance | 5 % chacune |

Les 80 critères du prompt sont regroupés dans ces familles, chacune mesurée.
Un défaut du modèle du monde, du raisonnement, des preuves ou de la sécurité
est un P0. Verdict : **READY_FOR_ADVANCED_REASONING_AND_WORLD_MODEL**
(contrat `WorldModelReport.v1`).

## Ce qui ne s'applique pas

- **Monde de l'énergie, de l'ingénierie, de la cybersécurité** (§46-51) : le
  bot ne raisonne que sur son marché et lui-même ; la cybersécurité a son étape
  (20).
- **Comité de raisonnement, IA qui raisonnent** (§53-54) : aucune IA ne décide ;
  le comité d'agents donne seulement son avis.
- **Prévisions probabilistes du marché** (§24-25) : la règle ne prévoit pas,
  elle
  suit ; les probabilités de l'anticipation (prochaine clôture) restent des
  probabilités calibrées, montrées comme telles.
