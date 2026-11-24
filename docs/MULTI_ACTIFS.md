# Au-delà des cryptos : or, pétrole, devises, indices, obligations

Historique journalier Yahoo Finance jusqu'au 2026-11-22 ; règle de TrendGuard transposée (`trendguard/multi_actifs.py`) : paramètres gelés, régime propre à chaque actif (au-dessus de sa moyenne de 150 séances), achats seulement, sans levier. Régénérer : `python -m research.multi_actifs --cache data_multi`.

Pourquoi deux modèles de coûts : avec les frais de Binance, le coût d'un aller-retour mange 23 à 45 % du risque de chaque trade sur les devises, les indices et les obligations (stop médian à 0,9-1,8 % du prix), contre 4,6 % pour TrendGuard sur les cryptos. Ces marchés coûtent 10 à 20 fois moins chez un courtier.

## Frais de Binance (0,1 % + 0,1 % de glissement par côté)

| Catégorie | Actifs | Règle 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | Achat-conservation 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Matières premières | 6 | +4,9 % | −15,3 % | 0,32 | +0,9 % | −18,4 % | 0,05 | +6,2 % | −41,5 % | 0,15 | +7,6 % | −36,0 % | 0,21 |
| Devises | 6 | −0,9 % | −9,4 % | −0,09 | −2,6 % | −10,9 % | −0,24 | −0,5 % | −7,0 % | −0,07 | +1,2 % | −3,8 % | 0,30 |
| Indices | 7 | −0,8 % | −13,8 % | −0,06 | +3,8 % | −12,5 % | 0,30 | +4,9 % | −33,1 % | 0,15 | +15,0 % | −16,9 % | 0,89 |
| Obligations | 2 | +0,7 % | −6,4 % | 0,10 | −0,9 % | −4,3 % | −0,20 | −3,3 % | −36,1 % | −0,09 | −3,0 % | −20,4 % | −0,15 |
| Actions (biais du survivant) | 9 | +15,9 % | −14,7 % | 1,08 | +10,7 % | −18,9 % | 0,57 | +20,3 % | −51,8 % | 0,39 | +34,6 % | −29,4 % | 1,18 |

Corrélation des rendements journaliers entre TrendGuard sur les cryptos et la règle hors cryptos : **0,05** (0,87 à 0,93 entre stratégies de cryptos).

| Portefeuille | 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | 24/05/2026 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|
| TrendGuard seule (cryptos) | +41,2 % | −25,4 % | 1,62 | +32,0 % | −33,6 % | 0,95 | +16,8 % | −10,5 % | 1,59 |
| Hors cryptos (sans les actions) | −1,5 % | −15,8 % | −0,10 | −0,3 % | −22,1 % | −0,01 | −30,4 % | −18,8 % | −1,62 |
| Moitié cryptos, moitié hors cryptos | +19,5 % | −19,4 % | 1,00 | +16,3 % | −19,6 % | 0,83 | −9,2 % | −9,7 % | −0,95 |

Espérance par trade sur 2018-2022 : hors cryptos −0,07 R, TrendGuard sur les cryptos +1,50 R.

- Hors cryptos rentable sur les deux périodes : **non**.
- Moitié-moitié meilleure que TrendGuard seule au Calmar sur les deux périodes : **non**.
- **Pas de proposition** : la règle fixée d'avance n'est pas satisfaite.

## Coût réel de chaque marché (devises 0,01 %, indices et obligations 0,02 %, matières premières et actions 0,05 % par côté)

| Catégorie | Actifs | Règle 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | Achat-conservation 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Matières premières | 6 | +7,6 % | −15,4 % | 0,50 | +2,0 % | −18,4 % | 0,11 | +6,2 % | −41,5 % | 0,15 | +7,6 % | −36,0 % | 0,21 |
| Devises | 6 | +0,8 % | −6,1 % | 0,12 | −0,6 % | −4,7 % | −0,12 | −0,5 % | −7,0 % | −0,07 | +1,2 % | −3,8 % | 0,30 |
| Indices | 7 | +1,2 % | −10,2 % | 0,11 | +5,6 % | −10,6 % | 0,53 | +4,9 % | −33,1 % | 0,15 | +15,0 % | −16,9 % | 0,89 |
| Obligations | 2 | +1,0 % | −5,5 % | 0,19 | −0,5 % | −3,4 % | −0,15 | −3,3 % | −36,1 % | −0,09 | −3,0 % | −20,4 % | −0,15 |
| Actions (biais du survivant) | 9 | +19,8 % | −15,8 % | 1,26 | +12,3 % | −18,2 % | 0,67 | +20,3 % | −51,8 % | 0,39 | +34,6 % | −29,4 % | 1,18 |

Corrélation des rendements journaliers entre TrendGuard sur les cryptos et la règle hors cryptos : **0,05** (0,87 à 0,93 entre stratégies de cryptos).

| Portefeuille | 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | 24/05/2026 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|
| TrendGuard seule (cryptos) | +41,2 % | −25,4 % | 1,62 | +32,0 % | −33,6 % | 0,95 | +16,8 % | −10,5 % | 1,59 |
| Hors cryptos (sans les actions) | +0,8 % | −15,6 % | 0,05 | +1,1 % | −19,4 % | 0,06 | −25,4 % | −16,0 % | −1,58 |
| Moitié cryptos, moitié hors cryptos | +20,9 % | −19,2 % | 1,09 | +17,0 % | −17,8 % | 0,95 | −6,0 % | −9,4 % | −0,64 |

Espérance par trade sur 2018-2022 : hors cryptos +0,18 R, TrendGuard sur les cryptos +1,50 R.

- Hors cryptos rentable sur les deux périodes : **oui**.
- Moitié-moitié meilleure que TrendGuard seule au Calmar sur les deux périodes : **non**.
- **Pas de proposition** : la règle fixée d'avance n'est pas satisfaite.

## Lecture

- La règle est calculée sur les séances réelles de chaque marché (correctif du 24/11/2026). Avant, les jours où une place était fermée comptaient comme des jours sans variation : ils diluaient la volatilité et rapprochaient les stops, et une fermeture de plus de 5 séances (semaine d'or en Chine) passait pour un retrait de la cote.
- Avec les coûts réels, la règle fixée d'avance n'est pas satisfaite : hors cryptos, la règle gagne peu, et le mélange moitié-moitié ne bat pas TrendGuard seule sur les deux périodes.
- Conclusion : ces marchés restent suivis en argent fictif pour information (pages du panneau, page Veille), sans argent réel tant que les preuves en direct ne montrent pas d'avantage.

## Limites

- Un backtest n'est pas une garantie de gain.
- Coûts réels supposés : typiques d'un compte de particulier, à confirmer chez un courtier.
- Rendements en monnaie locale (change supposé couvert) ; contrats à terme en continu (Yahoo), sans le coût de leur renouvellement.
- Actions : choisies parmi les plus grandes d'aujourd'hui, leur passé est flatté ; elles ne comptent dans aucun verdict.
- Le franc CFA (XOF) a une parité fixe avec l'euro : rien à suivre.
