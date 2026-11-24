# Au-delà des cryptos : or, pétrole, devises, indices, obligations

Historique journalier Yahoo Finance jusqu'au 2026-11-22 ; règle de TrendGuard transposée (`trendguard/multi_actifs.py`) : paramètres gelés, régime propre à chaque actif (au-dessus de sa moyenne de 150 séances), achats seulement, sans levier. Régénérer : `python -m research.multi_actifs --cache data_multi`.

Pourquoi deux modèles de coûts : avec les frais de Binance, le coût d'un aller-retour mange 23 à 45 % du risque de chaque trade sur les devises, les indices et les obligations (stop médian à 0,9-1,8 % du prix), contre 4,6 % pour TrendGuard sur les cryptos. Ces marchés coûtent 10 à 20 fois moins chez un courtier.

## Frais de Binance (0,1 % + 0,1 % de glissement par côté)

| Catégorie | Actifs | Règle 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | Achat-conservation 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Matières premières | 6 | +3,7 % | −17,8 % | 0,21 | −1,9 % | −22,3 % | −0,09 | +6,2 % | −41,5 % | 0,15 | +7,6 % | −36,0 % | 0,21 |
| Devises | 6 | −1,5 % | −11,4 % | −0,13 | −3,0 % | −12,2 % | −0,25 | −0,5 % | −7,0 % | −0,07 | +1,2 % | −3,8 % | 0,30 |
| Indices | 7 | −1,6 % | −15,0 % | −0,11 | +1,2 % | −12,3 % | 0,10 | +4,9 % | −33,1 % | 0,15 | +15,0 % | −16,9 % | 0,89 |
| Obligations | 2 | +0,1 % | −7,5 % | 0,01 | −1,3 % | −5,7 % | −0,23 | −3,3 % | −36,1 % | −0,09 | −3,0 % | −20,4 % | −0,15 |
| Actions (biais du survivant) | 9 | +16,7 % | −16,6 % | 1,00 | +7,9 % | −20,8 % | 0,38 | +20,3 % | −51,8 % | 0,39 | +34,6 % | −29,4 % | 1,18 |

Corrélation des rendements journaliers entre TrendGuard sur les cryptos et la règle hors cryptos : **0,03** (0,87 à 0,93 entre stratégies de cryptos).

| Portefeuille | 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | 24/05/2026 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|
| TrendGuard seule (cryptos) | +41,2 % | −25,4 % | 1,62 | +32,0 % | −33,6 % | 0,95 | +16,8 % | −10,5 % | 1,59 |
| Hors cryptos (sans les actions) | −3,8 % | −27,6 % | −0,14 | −3,4 % | −26,5 % | −0,13 | −31,4 % | −19,3 % | −1,63 |
| Moitié cryptos, moitié hors cryptos | +18,2 % | −14,7 % | 1,24 | +14,5 % | −19,1 % | 0,76 | −9,8 % | −8,3 % | −1,18 |

Espérance par trade sur 2018-2022 : hors cryptos −0,16 R, TrendGuard sur les cryptos +1,50 R.

- Hors cryptos rentable sur les deux périodes : **non**.
- Moitié-moitié meilleure que TrendGuard seule au Calmar sur les deux périodes : **non**.
- **Pas de proposition** : la règle fixée d'avance n'est pas satisfaite.

## Coût réel de chaque marché (devises 0,01 %, indices et obligations 0,02 %, matières premières et actions 0,05 % par côté)

| Catégorie | Actifs | Règle 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | Achat-conservation 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Matières premières | 6 | +6,1 % | −16,5 % | 0,37 | −0,0 % | −22,3 % | −0,00 | +6,2 % | −41,5 % | 0,15 | +7,6 % | −36,0 % | 0,21 |
| Devises | 6 | +0,5 % | −7,3 % | 0,07 | −0,6 % | −4,1 % | −0,15 | −0,5 % | −7,0 % | −0,07 | +1,2 % | −3,8 % | 0,30 |
| Indices | 7 | +1,2 % | −10,1 % | 0,12 | +4,4 % | −10,3 % | 0,43 | +4,9 % | −33,1 % | 0,15 | +15,0 % | −16,9 % | 0,89 |
| Obligations | 2 | +0,5 % | −6,4 % | 0,08 | −0,8 % | −4,1 % | −0,19 | −3,3 % | −36,1 % | −0,09 | −3,0 % | −20,4 % | −0,15 |
| Actions (biais du survivant) | 9 | +16,4 % | −16,3 % | 1,01 | +12,6 % | −19,4 % | 0,65 | +20,3 % | −51,8 % | 0,39 | +34,6 % | −29,4 % | 1,18 |

Corrélation des rendements journaliers entre TrendGuard sur les cryptos et la règle hors cryptos : **0,03** (0,87 à 0,93 entre stratégies de cryptos).

| Portefeuille | 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | 24/05/2026 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|
| TrendGuard seule (cryptos) | +41,2 % | −25,4 % | 1,62 | +32,0 % | −33,6 % | 0,95 | +16,8 % | −10,5 % | 1,59 |
| Hors cryptos (sans les actions) | +0,0 % | −21,2 % | 0,00 | +2,0 % | −20,5 % | 0,10 | −27,0 % | −16,9 % | −1,60 |
| Moitié cryptos, moitié hors cryptos | +20,5 % | −11,9 % | 1,72 | +17,6 % | −16,8 % | 1,05 | −7,0 % | −8,0 % | −0,87 |

Espérance par trade sur 2018-2022 : hors cryptos +0,01 R, TrendGuard sur les cryptos +1,50 R.

- Hors cryptos rentable sur les deux périodes : **oui**.
- Moitié-moitié meilleure que TrendGuard seule au Calmar sur les deux périodes : **oui**.
- **Proposition** : suivre ce portefeuille en argent fictif, à côté du bot ; aucun ordre réel sans courtier.

## Lecture

- Avec les coûts réels, la règle fixée d'avance est satisfaite, mais de justesse : hors cryptos, la règle ne gagne presque rien (espérance par trade proche de zéro). Le gain du portefeuille moitié-moitié vient seulement de la diversification (corrélation proche de zéro avec les cryptos) : le rendement est divisé par deux, la baisse maximale un peu plus.
- Conclusion : suivre ces marchés en argent fictif (page Veille du panneau), sans y consacrer d'argent réel tant que les preuves en direct ne montrent pas d'avantage.

## Limites

- Un backtest n'est pas une garantie de gain.
- Coûts réels supposés : typiques d'un compte de particulier, à confirmer chez un courtier.
- Rendements en monnaie locale (change supposé couvert) ; contrats à terme en continu (Yahoo), sans le coût de leur renouvellement.
- Actions : choisies parmi les plus grandes d'aujourd'hui, leur passé est flatté ; elles ne comptent dans aucun verdict.
- Le franc CFA (XOF) a une parité fixe avec l'euro : rien à suivre.
