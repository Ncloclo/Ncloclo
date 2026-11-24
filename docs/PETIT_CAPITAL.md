# Petit capital : 100 USDT comme 10 000 ?

Données Binance des 21 paires du bot jusqu'au 2026-11-22, paramètres gelés, frais et glissement de 0,1 % par côté. Régénérer : `python -m research.petit_capital --cache data_binance`.

## Le problème

Le bot risque 1 % du capital par achat jusqu'au stop initial, placé à 4,7-14,1 % du prix (P10 à P90 des entrées depuis 2018). Avec 100 USDT, cela fait 7 à 20 USDT par position (25 au plus). L'ancienne règle refusait tout achat sous 10 USDT : avec un petit capital, les cryptos les plus agitées n'étaient jamais achetées. Binance n'exige que 5 USDT par ordre (1 pour DOGE). Mais la revente est soumise au même minimum : une position qui vaut moins de 5 USDT à son stop ne peut plus être vendue.

Règle actuelle (`trend_strategy.size_position`) : pas d'achat si la position, revendue à son stop (frais et glissement déduits), vaudrait moins de 5 USDT + 5 %. Le minimum vient des règles de Binance, pas des résultats.

## Résultats

| Capital | Règle | 2018-2022 (conception) : rendement | baisse | trades | 2023 → 2026-11-22 : rendement | baisse | trades | 24/05/2026 → 2026-11-22 (jamais vue au gel) : rendement | baisse | trades | achats refusés |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10 000 USDT | ancienne (10 USDT à l'achat) | +460,1 % | −25,4 % | 138 | +194,3 % | −33,6 % | 180 | +9,5 % | −10,5 % | 12 | 10 / 98 / 0 |
| 10 000 USDT | actuelle (5 USDT + 5 % au stop) | +460,1 % | −25,4 % | 138 | +194,3 % | −33,6 % | 180 | +9,5 % | −10,5 % | 12 | 10 / 98 / 0 |
| 1 000 USDT | ancienne (10 USDT à l'achat) | +454,4 % | −25,4 % | 138 | +194,3 % | −33,6 % | 180 | +9,5 % | −10,5 % | 12 | 12 / 98 / 0 |
| 1 000 USDT | actuelle (5 USDT + 5 % au stop) | +454,4 % | −25,4 % | 138 | +194,3 % | −33,6 % | 180 | +9,5 % | −10,5 % | 12 | 12 / 98 / 0 |
| 100 USDT | ancienne (10 USDT à l'achat) | +453,5 % | −25,4 % | 136 | +218,8 % | −33,0 % | 167 | +2,6 % | −10,3 % | 12 | 25 / 312 / 17 |
| 100 USDT | actuelle (5 USDT + 5 % au stop) | +454,4 % | −25,4 % | 138 | +195,0 % | −33,6 % | 178 | +9,5 % | −10,5 % | 12 | 12 / 102 / 0 |
| 50 USDT | ancienne (10 USDT à l'achat) | +295,6 % | −28,4 % | 81 | +82,1 % | −23,2 % | 90 | −0,1 % | −4,2 % | 4 | 487 / 992 / 107 |
| 50 USDT | actuelle (5 USDT + 5 % au stop) | +406,1 % | −25,4 % | 133 | +205,8 % | −30,1 % | 155 | +1,8 % | −8,9 % | 11 | 49 / 427 / 63 |
| 30 USDT | ancienne (10 USDT à l'achat) | +0,0 % | 0,0 % | 0 | +0,0 % | 0,0 % | 0 | +0,0 % | 0,0 % | 0 | 1195 / 1534 / 123 |
| 30 USDT | actuelle (5 USDT + 5 % au stop) | +306,2 % | −27,6 % | 82 | +151,9 % | −24,9 % | 112 | −0,9 % | −5,0 % | 5 | 476 / 784 / 105 |
| 20 USDT | ancienne (10 USDT à l'achat) | +0,0 % | 0,0 % | 0 | +0,0 % | 0,0 % | 0 | +0,0 % | 0,0 % | 0 | 1195 / 1534 / 123 |
| 20 USDT | actuelle (5 USDT + 5 % au stop) | +0,0 % | 0,0 % | 0 | +0,0 % | 0,0 % | 0 | +0,0 % | 0,0 % | 0 | 1195 / 1534 / 123 |
| BTC conservé | sans rien faire | +23,6 % | −81,2 % | — | +268,7 % | −53,0 % | — | −20,5 % | −31,9 % | — | — |

Achats refusés : un par jour et par crypto signalée dont la taille passe sous le minimum, période par période, y compris faute d'argent disponible quand le portefeuille est déjà investi (d'où les refus à 10 000 USDT).

## Lecture

- **100 USDT, règle actuelle** : à 5,8 points au plus du rendement de 10 000 USDT sur chaque période (ancienne règle : 24,5 points).
- **2023 → 2026-11-22** : l'ancienne règle faisait mieux avec 100 USDT (+218,8 % contre +195,0 %). Les achats qu'elle refusait ont, sur cette période, plutôt perdu. C'est de la chance, pas une règle : ce n'est plus la stratégie validée avec 10 000 USDT.
- **Capital plancher** : de 10 000 à 100 USDT, l'écart avec 10 000 USDT reste sous 10 points sur chaque période. En dessous, de plus en plus d'achats sont refusés, et à 20 USDT plus aucun ne passe.

## Limites

- Un backtest n'est pas une garantie de gain, et le passé ne se répète pas.
- Le backtest n'arrondit pas la quantité au pas du lot de chaque paire. Le bot le fait à l'exécution et revérifie le minimum, à l'achat comme au stop (achat paper au carnet, porte du réel).
- Les frais de Binance sont proportionnels au montant : ils pèsent autant, en pourcentage, sur 100 USDT que sur 10 000.
