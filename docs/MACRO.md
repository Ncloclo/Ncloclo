# Les indicateurs de marché auraient-ils aidé TrendGuard ?

Historique Binance des 21 cryptos du bot jusqu'au 2026-11-22 ; VIX, indice du dollar et taux américains (Yahoo Finance) jusqu'au 2026-11-24. Règle, paramètres et régime BTC inchangés. Régénérer : `python -m research.macro --cache data_binance --multi data_multi`.

## Filtres déclarés avant les mesures

Aucun nouvel achat de crypto ce jour-là si :

- Stress : VIX au-dessus de 30 ;
- Dollar fort : DXY au-dessus de sa moyenne de 150 séances ;
- Choc de taux : taux US à 10 ans en hausse de plus de 0,5 point en 60 séances ;
- Courbe inversée : taux à 10 ans sous le taux à 3 mois ;

Seuils d'usage courant, sans réglage. Valeur connue à la clôture des cryptos (00:00 UTC) : la séance américaine du même jour, sinon la précédente.

## Résultats

| Stratégie | 2018-2022 : CAGR | baisse | Calmar | trades | jours bloqués | 2023 → 2026-11-22 : CAGR | baisse | Calmar | trades | jours bloqués | 24/05/2026 → 2026-11-22 : CAGR | baisse | Calmar | trades | jours bloqués |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| TrendGuard sans filtre | +41,2 % | −25,4 % | 1,62 | 138 | 0 % | +32,0 % | −33,6 % | 0,95 | 180 | 0 % | +19,9 % | −10,5 % | 1,89 | 12 | 0 % |
| Stress : VIX au-dessus de 30 | +45,0 % | −25,4 % | 1,77 | 134 | 11 % | +32,0 % | −33,6 % | 0,95 | 180 | 2 % | +19,9 % | −10,5 % | 1,89 | 12 | 2 % |
| Dollar fort : DXY au-dessus de sa moyenne de 150 séances | +25,0 % | −19,7 % | 1,27 | 91 | 64 % | +21,4 % | −27,0 % | 0,79 | 133 | 47 % | +22,5 % | −7,8 % | 2,88 | 7 | 87 % |
| Choc de taux : taux US à 10 ans en hausse de plus de 0,5 point en 60 séances | +36,9 % | −19,7 % | 1,88 | 123 | 14 % | +26,9 % | −32,9 % | 0,82 | 170 | 11 % | +19,9 % | −10,5 % | 1,89 | 12 | 12 % |
| Courbe inversée : taux à 10 ans sous le taux à 3 mois | +40,1 % | −25,4 % | 1,58 | 137 | 10 % | +16,7 % | −22,4 % | 0,74 | 80 | 50 % | +19,9 % | −10,5 % | 1,89 | 12 | 0 % |

## Trades selon l'indicateur le jour de l'achat (stratégie sans filtre)

| Indicateur | Période | Condition vraie : trades, R moyen | Condition fausse : trades, R moyen |
|---|---|---|---|
| Stress | 2018-2022 | 7, −0,65 R | 131, +1,61 R |
| Stress | 2023 → 2026-11-22 | 0, +0,00 R | 180, +0,73 R |
| Dollar fort | 2018-2022 | 65, +1,24 R | 73, +1,73 R |
| Dollar fort | 2023 → 2026-11-22 | 51, +1,08 R | 129, +0,60 R |
| Choc de taux | 2018-2022 | 20, +1,88 R | 118, +1,43 R |
| Choc de taux | 2023 → 2026-11-22 | 20, +3,09 R | 160, +0,44 R |
| Courbe inversée | 2018-2022 | 5, +0,57 R | 133, +1,53 R |
| Courbe inversée | 2023 → 2026-11-22 | 107, +0,85 R | 73, +0,56 R |

## Verdict (règle fixée avant les mesures)

- Stress : Calmar meilleur sur 2018-2022 et depuis 2023 : **non**.
- Dollar fort : Calmar meilleur sur 2018-2022 et depuis 2023 : **non**.
- Choc de taux : Calmar meilleur sur 2018-2022 et depuis 2023 : **non**.
- Courbe inversée : Calmar meilleur sur 2018-2022 et depuis 2023 : **non**.
- **Aucun filtre retenu** : la règle actuelle tient. Les indicateurs restent une information.

## Lecture

- Stress : mieux sur 2018-2022 (Calmar 1,62 → 1,77), pareil depuis 2023 (0,95 → 0,95) ; aucun achat n'a eu lieu sous cette condition depuis 2023 : l'effet vu sur 2018-2022 reste à vérifier.
- Dollar fort : moins bien sur 2018-2022 (Calmar 1,62 → 1,27), moins bien depuis 2023 (0,95 → 0,79).
- Choc de taux : mieux sur 2018-2022 (Calmar 1,62 → 1,88), moins bien depuis 2023 (0,95 → 0,82).
- Courbe inversée : moins bien sur 2018-2022 (Calmar 1,62 → 1,58), moins bien depuis 2023 (0,95 → 0,74).

## Ce que le bot en fait

- Il lit chaque jour, après la clôture de New York, le VIX, l'indice du dollar et les taux américains (3 mois, 5, 10 et 30 ans) : page Veille du panneau, et une ligne pour l'agent macro du comité, qui informe sans voter.

## Limites

- Un backtest n'est pas une garantie de gain.
- Une seule paire de périodes, quatre filtres : un filtre qui passerait pourrait encore le devoir au hasard ; d'où l'étude en direct avant toute application.
- Les marchés américains ferment le week-end : le samedi et le dimanche, la valeur est celle du vendredi.
