# TrendGuard — les résultats tiennent-ils quand tout se dégrade ?

Étude reproductible : `python -m research.robustness --cache data_binance`
(données journalières Binance des 21 paires du bot, frais 0,1 % et glissement
0,1 % par côté, 1 % du capital risqué par achat). C'est un diagnostic : **aucune
règle n'est changée d'après ces tests.**

Périodes : **2018-2022** et **2023 → 2026-09-27** (l'historique Binance ne
permet les premiers achats qu'en 2019). CAGR : rendement annuel ; Calmar :
rendement annuel divisé par la pire baisse.

## 1. Coûts plus élevés et profil prudent

| Test | 2018-22 : CAGR | Baisse max | Sharpe | Calmar | 2023 → : CAGR | Baisse max | Sharpe | Calmar |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Référence (réglages du bot) | +41,2 % | −25,4 % | 1,25 | 1,62 | +37,2 % | −33,6 % | 1,18 | 1,11 |
| Frais et glissement × 2 (0,4 % par côté) | +32,8 % | −21,6 % | 1,13 | 1,52 | +32,8 % | −33,8 % | 1,09 | 0,97 |
| Frais et glissement × 3 | +31,1 % | −21,6 % | 1,11 | 1,43 | +27,5 % | −33,7 % | 0,98 | 0,82 |
| Profil prudent (risque ÷ 2 après −10 %) | +31,8 % | −20,6 % | 1,12 | 1,54 | +32,1 % | −24,2 % | 1,20 | 1,33 |

- Frais et glissement doublés : +32,8 % puis +32,8 % par an ; triplés : +31,1 %
  puis +27,5 % par an. Le bot reste rentable sur les deux périodes même avec des
  frais triplés.
- Profil prudent : pire baisse de −24,2 % au lieu de −33,6 % depuis 2023, pour
  +32,1 % par an au lieu de +37,2 % (`docs/ADAPTATION.md`).

## 2. Réglages voisins (27 combinaisons)

Cassure de 20, 30 ou 40 jours ; stop initial à 2,5, 3 ou 3,5 fois la
volatilité ; stop suiveur à 4, 5 ou 6 fois.

| Mesure | 2018-22 | 2023 → |
| --- | --- | --- |
| Combinaisons rentables | 27 / 27 | 27 / 27 |
| Calmar : le plus faible | 1,16 | 0,74 |
| Calmar : médiane | 1,55 | 1,09 |
| Calmar : le plus fort | 3,49 | 1,36 |
| Rang des réglages du bot | 12 / 27 | 10 / 27 |

- 27 combinaisons sur 27 sont rentables sur les deux périodes : les résultats
  forment un plateau, pas un pic isolé trouvé par hasard. Choisir la meilleure
  combinaison après coup serait du sur-ajustement.

## 3. Dépendance aux plus grands gagnants

Bénéfice de chaque crypto de 2018 à aujourd'hui (réglages du bot) : ZEC +33 538
$, XRP +26 582 $, ADA +23 524 $, BTC +18 829 $, BNB +13 160 $… ; en perte : XTZ
−3 035 $, DOT −3 483 $, AAVE −5 553 $, LTC −10 975 $. La meilleure fait 20 % des
gains, les trois meilleures 50 %.

| Test | 2018-22 : CAGR | Baisse max | Sharpe | Calmar | 2023 → : CAGR | Baisse max | Sharpe | Calmar |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Toutes les cryptos | +41,2 % | −25,4 % | 1,25 | 1,62 | +37,2 % | −33,6 % | 1,18 | 1,11 |
| Sans ZEC | +36,0 % | −21,1 % | 1,19 | 1,71 | +26,0 % | −33,6 % | 0,92 | 0,77 |
| Sans ZEC, XRP, ADA | +31,9 % | −34,5 % | 1,04 | 0,92 | +21,2 % | −32,6 % | 0,82 | 0,65 |

- C'est le principe du suivi de tendance : quelques grandes tendances font
  l'essentiel du résultat, et on ne sait pas à l'avance lesquelles. Même sans
  ses trois meilleures cryptos, retirées après coup (test sévère), le bot reste
  rentable sur les deux périodes : c'est aussi pourquoi la sélection auto garde
  les 21.

## 4. Hasard (Monte-Carlo)

Trois ans rejoués 5 000 fois au hasard, par blocs de 30 jours de la vraie courbe
du capital : les positions ouvertes en même temps et leurs corrélations sont
conservées.

| Mesure sur 3 ans | Valeur |
| --- | --- |
| Résultat médian | +155 % |
| Mauvais cas (1 fois sur 20) | +5 % |
| Chance de finir en perte | 4,0 % |
| Pire baisse médiane | −26 % |
| Pire baisse, 1 fois sur 20 | −41 % |
| Baisse de plus de 20 % / 30 % / 40 % | 84 % / 30 % / 6 % des cas |
| Série de trades perdants de suite, sur 100 trades (médiane ; 1 fois sur 20) | 8 ; 12 |

- Des séries de 8 à 12 trades perdants de suite sont normales avec 35 à 50 % de
  trades gagnants : ce n'est pas le signe d'une panne.
- Le passé rejoué au hasard n'est pas une prévision : un marché inédit peut
  faire pire.

## 5. Année par année

| Année | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Rendement | +43 % | +63 % | +165 % | −9 % | +47 % | +65 % | +7 % | +26 % |

- Pire mois : −13,2 % (01/2024). Année en perte : 2022 (marché baissier : le bot
  n'achète plus et resserre ses stops, la perte reste limitée).

## Conclusion

- **Coûts** : rentable sur les deux périodes même avec des frais triplés.
- **Réglages** : 27 combinaisons voisines sur 27 rentables sur les deux
  périodes : plateau.
- **Gagnants** : les trois meilleures cryptos font 50 % des gains ; sans elles,
  le bot reste rentable.
- **À prévoir sur 3 ans** : une pire baisse autour de −26 %, jusqu'à −41 % une
  fois sur 20, et des séries de 12 trades perdants. Aucune règle n'est changée
  d'après ces tests.
