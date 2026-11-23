# Sentiment et effet de levier

Étude reproductible : `python -m research.sentiment --cache data_binance`
(données jusqu'au 2026-11-22).

Faut-il éviter d'acheter quand les spéculateurs sont trop acheteurs (taux de
financement des contrats à terme élevé) ou euphoriques (indice de peur et
d'avidité élevé) ? Les trades sont ceux de la stratégie, paramètres gelés ; les
indicateurs sont ceux connus à la clôture de la bougie de l'achat.

## Résultat des trades selon le financement (annualisé, moyenne de 7 jours)

| Tranche à l'achat | Trades 2018-2022 | R moyen | Gagnants | Trades depuis 2023 | R moyen | Gagnants |
| --- | --- | --- | --- | --- | --- | --- |
| négatif (les vendeurs paient) | 9 | +1,73 R | 33 % | 26 | +0,85 R | 38 % |
| 0 à 10 % par an | 12 | +1,26 R | 42 % | 109 | +0,71 R | 39 % |
| 10 à 30 % par an | 54 | +1,64 R | 43 % | 40 | +0,97 R | 35 % |
| plus de 30 % par an | 40 | +1,03 R | 38 % | 5 | −1,17 R | 0 % |

Trades sans indicateur connu à l'achat : 23 sur 318.

## Résultat des trades selon l'indice de peur et d'avidité

| Tranche à l'achat | Trades 2018-2022 | R moyen | Gagnants | Trades depuis 2023 | R moyen | Gagnants |
| --- | --- | --- | --- | --- | --- | --- |
| 0-24 : peur extrême | 0 | — | — | 0 | — | — |
| 25-49 : peur | 33 | +0,04 R | 30 % | 25 | +1,00 R | 52 % |
| 50-74 : avidité | 67 | +1,49 R | 43 % | 135 | +0,58 R | 35 % |
| 75-100 : avidité extrême | 38 | +2,78 R | 58 % | 20 | +1,48 R | 30 % |

Trades sans indicateur connu à l'achat : 0 sur 318.

## Filtres testés : pas d'achat au-dessus d'un seuil

Seuil choisi sur 2018-2022 (meilleur Calmar), puis vérifié depuis 2023. Un
filtre n'est retenu que s'il améliore le Calmar de plus de 0,05 dans les deux
périodes. Rendement annuel, baisse maximale, Calmar (rendement ÷ baisse) et
nombre de trades.

| Règle | 2018-2022 rendement | baisse | Calmar | trades | depuis 2023 rendement | baisse | Calmar | trades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BTC acheté et conservé | +4,3 % | −81,2 % | 0,05 | 0 | +39,9 % | −53,0 % | 0,75 | 0 |
| stratégie sans filtre | +41,2 % | −25,4 % | 1,62 | 138 | +32,0 % | −33,6 % | 0,95 | 180 |
| pas d'achat si financement > 20 % par an | +26,9 % | −20,0 % | 1,35 | 101 | +33,5 % | −29,9 % | 1,12 | 173 |
| pas d'achat si financement > 30 % par an | +34,6 % | −23,8 % | 1,45 | 110 | +33,0 % | −30,5 % | 1,08 | 176 |
| pas d'achat si financement > 50 % par an (choisi sur 2018-2022) | +43,8 % | −23,3 % | 1,88 | 121 | +32,1 % | −33,3 % | 0,96 | 179 |
| pas d'achat si financement > 100 % par an | +42,7 % | −25,2 % | 1,69 | 133 | +32,0 % | −33,6 % | 0,95 | 180 |
| pas d'achat si avidité > 70 | +17,6 % | −25,3 % | 0,70 | 96 | +28,8 % | −22,5 % | 1,28 | 137 |
| pas d'achat si avidité > 75 | +18,4 % | −22,3 % | 0,82 | 118 | +26,3 % | −30,8 % | 0,85 | 174 |
| pas d'achat si avidité > 80 | +23,8 % | −25,4 % | 0,94 | 126 | +31,1 % | −33,4 % | 0,93 | 179 |
| pas d'achat si avidité > 90 (choisi sur 2018-2022) | +31,9 % | −25,4 % | 1,26 | 129 | +31,5 % | −33,6 % | 0,94 | 180 |

## Verdict

- Filtre financement (seuil 50 % par an, choisi sur 2018-2022) : Calmar +0,26 en
  conception, +0,01 depuis 2023. N'améliore pas les deux périodes : **non
  retenu**, l'indicateur reste une information.
- Filtre avidité (seuil 90, choisi sur 2018-2022) : Calmar −0,37 en conception,
  −0,02 depuis 2023. N'améliore pas les deux périodes : **non retenu**,
  l'indicateur reste une information.

## Lecture

- Huit variantes ont été essayées (quatre seuils par indicateur) : un gain isolé
  peut venir du hasard. C'est pourquoi le seuil est choisi sur la première
  période et jugé sur la seconde.
- Le financement n'existe que depuis l'ouverture des contrats perpétuels
  (septembre 2019 pour BTC, plus tard pour d'autres) : avant, le filtre laisse
  acheter, comme la stratégie sans filtre.
- Une étude mesure ; elle ne change aucune règle du bot. Les indicateurs sont
  affichés dans la veille et transmis aux IA de la veille
  (trendguard/derives.py). Résultats passés : aucune garantie pour l'avenir.
