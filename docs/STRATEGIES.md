# Laboratoire de stratégies — le bot peut-il « apprendre la meilleure stratégie » ?

Étude reproductible : `python trendguard_bot.py lab --cache data_binance`
(`--cm data` ajoute les données Coin Metrics, qui incluent des actifs
effondrés). Frais 0,1 % et slippage 0,1 % par côté, 1 % du capital risqué
par trade, mêmes plafonds de portefeuille pour toutes les stratégies.

Protocole : les douze stratégies sont écrites **avant** de regarder leurs
résultats. La sélection se fait sur 2018-2022, puis est vérifiée sur
2023 → aujourd'hui, période qui n'a servi à aucun choix. Cinq d'entre elles
sont les stratégies publiées de traders célèbres (Tortues de Richard Dennis,
croisement 50/200 de Paul Tudor Jones, bandes de John Bollinger, double
momentum de Gary Antonacci ; le retour à la moyenne est le RSI(2) de Larry
Connors), écrites telles qu'ils les ont décrites, adaptées seulement à ce
que permet Binance Spot (achat seul, même filtre de marché que TrendGuard).
Leur histoire et ce qu'il faut en retenir : [`TRADING.md`](TRADING.md).

## Données : Binance, paires tradées par le bot (21 actifs, jusqu'au 2026-09-27)

### 1. Tournoi (1 % de risque par trade pour toutes)

| Stratégie | Famille | Choix 2018-22 : CAGR | Baisse max | Sharpe | Calmar | Gagnants | Espérance | Vérif. 2023 → : CAGR | Baisse max | Sharpe | Calmar | Gagnants | Espérance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TrendGuard (référence) | Suivi de tendance | +41,2 % | -25,4 % | 1,25 | 1,62 | 44 % | +1,50 R | +37,2 % | -33,6 % | 1,18 | 1,11 | 35 % | +0,70 R |
| Tendance rapide | Suivi de tendance | +43,0 % | -29,7 % | 1,26 | 1,45 | 44 % | +1,47 R | +47,9 % | -34,2 % | 1,38 | 1,40 | 40 % | +0,89 R |
| Tendance lente | Suivi de tendance | +27,5 % | -24,5 % | 0,99 | 1,12 | 43 % | +1,09 R | +29,1 % | -26,9 % | 1,09 | 1,08 | 35 % | +0,75 R |
| Régime « largeur de marché » | Suivi de tendance | +35,5 % | -32,7 % | 1,14 | 1,08 | 44 % | +1,26 R | +23,9 % | -23,5 % | 0,92 | 1,02 | 34 % | +0,53 R |
| Régime BTC et largeur | Suivi de tendance | +35,2 % | -30,6 % | 1,14 | 1,15 | 46 % | +1,27 R | +23,9 % | -23,5 % | 0,92 | 1,02 | 34 % | +0,53 R |
| Rotation momentum | Momentum relatif | +35,3 % | -29,3 % | 1,09 | 1,21 | 39 % | +1,16 R | +15,5 % | -38,5 % | 0,65 | 0,40 | 32 % | +0,21 R |
| Retour à la moyenne | Contrarien (fort taux de réussite) | +7,5 % | -11,2 % | 0,52 | 0,67 | 69 % | +0,16 R | -0,5 % | -19,4 % | 0,02 | -0,03 | 61 % | +0,00 R |
| Tortues, système 1 (R. Dennis) | Traders célèbres : suivi de tendance | +51,6 % | -42,0 % | 1,14 | 1,23 | 36 % | +1,94 R | +51,0 % | -36,5 % | 1,25 | 1,40 | 31 % | +0,89 R |
| Tortues, système 2 (R. Dennis) | Traders célèbres : suivi de tendance | +41,4 % | -57,6 % | 1,00 | 0,72 | 32 % | +3,75 R | +36,9 % | -37,7 % | 1,00 | 0,98 | 29 % | +1,10 R |
| Croisement 50/200 (P. T. Jones) | Traders célèbres : moyennes mobiles | +32,1 % | -59,1 % | 0,79 | 0,54 | 22 % | +6,98 R | +33,6 % | -39,6 % | 0,87 | 0,85 | 25 % | +1,47 R |
| Cassure de Bollinger (J. Bollinger) | Traders célèbres : volatilité | +19,5 % | -32,7 % | 0,75 | 0,60 | 40 % | +0,67 R | +52,5 % | -28,4 % | 1,45 | 1,85 | 40 % | +0,90 R |
| Double momentum (G. Antonacci) | Traders célèbres : momentum relatif et absolu | +20,6 % | -43,2 % | 0,70 | 0,48 | 38 % | +7,48 R | +19,7 % | -20,3 % | 0,85 | 0,97 | 34 % | +0,23 R |

- Choisie sur 2018-2022 (meilleur Calmar) : **TrendGuard (référence)**.
- Battent la référence sur les deux périodes : **aucune**.
- Le retour à la moyenne gagne **69 % puis 61 %** de ses trades, contre 44 % et
  35 % pour TrendGuard, mais son espérance est de +0,16 R puis +0,00 R par
  trade : un taux de réussite élevé ne fait pas une stratégie rentable.

### 2. Méta-apprentissage : suivre la stratégie qui marche le mieux ?

Tous les 6 mois depuis 2020, le chef d'orchestre regarde les 24 mois précédents,
et seulement eux.

| Portefeuille | 2020-22 : CAGR | Baisse max | Sharpe | Calmar | 2023 → : CAGR | Baisse max | Sharpe | Calmar |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TrendGuard seule (référence) | +57,9 % | -25,4 % | 1,43 | 2,28 | +37,2 % | -33,6 % | 1,18 | 1,11 |
| Chef d'orchestre : meilleure des 12 sur 24 mois | +48,8 % | -29,7 % | 1,30 | 1,64 | +28,8 % | -35,1 % | 0,95 | 0,82 |
| Chef d'orchestre : 12 pondérées par Sharpe 24 mois | +54,1 % | -32,6 % | 1,37 | 1,66 | +35,0 % | -30,2 % | 1,24 | 1,16 |
| Chef d'orchestre : meilleur horizon de tendance | +59,7 % | -29,7 % | 1,45 | 2,01 | +45,4 % | -35,1 % | 1,32 | 1,29 |
| Répartition fixe : 3 horizons de tendance | +56,0 % | -26,4 % | 1,42 | 2,12 | +38,3 % | -31,6 % | 1,26 | 1,21 |
| Répartition fixe : les 12 stratégies | +53,1 % | -31,3 % | 1,38 | 1,69 | +32,1 % | -28,0 % | 1,20 | 1,15 |

Stratégie confiée par le chef d'orchestre (12 candidates) : 2020-01 → Régime
« largeur de marché », 2020-07 → Régime « largeur de marché », 2021-01 →
Tendance rapide, 2021-07 → Tendance rapide, 2022-01 → Tendance rapide, 2022-07 →
Tendance rapide, 2023-01 → Tortues, système 1 (R. Dennis), 2023-07 → Retour à la
moyenne, 2024-01 → TrendGuard (référence), 2024-07 → Tendance rapide, 2025-01 →
Tendance rapide, 2025-07 → Régime « largeur de marché », 2026-01 → Cassure de
Bollinger (J. Bollinger), 2026-07 → Cassure de Bollinger (J. Bollinger)

- Portefeuilles qui battent TrendGuard seule sur les deux périodes (rendement
  divisé par la pire baisse) : **aucun**.
- **Chef d'orchestre : meilleur horizon de tendance** rapporte plus sur les deux
  périodes, mais avec des baisses maximales de -29,7 % et -35,1 % (référence :
  -25,4 % et -33,6 %) et un rapport rendement / pire baisse moins bon sur au
  moins une période : plus de risque, pas un meilleur apprentissage.

### 3. Probabilité de finir en gain selon la durée (TrendGuard, 2019 →)

| Durée | Fenêtres historiques en gain | en perte | 5 % pires | Médiane | Bootstrap : en gain | en perte | 5 % pires |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 mois | 37 % | 31 % | -7,8 % | +0,0 % | 37 % | 31 % | -7,4 % |
| 3 mois | 58 % | 31 % | -9,1 % | +3,2 % | 64 % | 35 % | -12,5 % |
| 6 mois | 71 % | 27 % | -8,8 % | +7,4 % | 74 % | 26 % | -15,1 % |
| 12 mois | 80 % | 20 % | -8,0 % | +31,2 % | 84 % | 16 % | -15,0 % |
| 24 mois | 92 % | 8 % | -5,8 % | +113,9 % | 93 % | 7 % | -5,3 % |
| 36 mois | 100 % | 0 % | +26,1 % | +159,0 % | 97 % | 3 % | +16,1 % |

Les fenêtres ni en gain ni en perte sont celles passées entièrement en USDT
(régime baissier). Les fenêtres se chevauchent : sur 7,7 ans d'historique, il
n'y a que 3,9 périodes indépendantes de 24 mois et 2,6 de 36 mois. Les
pourcentages sur longue durée sont donc des indications, pas des probabilités
précises ; le bootstrap (10 000 trajectoires recomposées par blocs de 30 jours)
en donne une estimation plus prudente.

## Ce que le bot en retient

- **Il réévalue les alternatives en continu, mais n'en change pas seul.**
  `python trendguard_bot.py diagnose` (et le diagnostic automatique
  hebdomadaire) classe les douze stratégies sur les 24 derniers mois et
  affiche la probabilité historique de gain par durée. Ce classement est une
  information, pas un ordre : une stratégie ne remplace TrendGuard que si
  elle fait mieux sur les deux périodes (section 1), et confier le capital à
  la meilleure du moment n'a pas fait mieux que de s'y tenir (section 2).
- **« 99 % de réussite et 1 % de perte maximale » n'existe pas trade par
  trade.** La section 1 le montre : la stratégie au meilleur taux de réussite
  (retour à la moyenne) ne gagne presque rien. La limite de 1 % s'applique à
  chaque trade (TrendGuard : −1,0 R en moyenne), pas au portefeuille, qui
  traverse des baisses de 25 à 35 %.
- **Le « succès » se mesure sur la durée.** Sur un mois, la stratégie est plus
  souvent à plat (en USDT) ou en perte qu'en gain. Sur deux à trois ans, la
  quasi-totalité des fenêtres historiques finissent en gain (section 3), sans
  garantie pour l'avenir.
