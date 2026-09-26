# Laboratoire de stratégies — le bot peut-il « apprendre la meilleure stratégie » ?

Étude reproductible : `python strategy_lab.py --cache data_binance`
(`--cm data` ajoute les données Coin Metrics, qui incluent des actifs
effondrés). Frais 0,1 % et slippage 0,1 % par côté, 1 % du capital risqué
par trade, mêmes plafonds de portefeuille pour toutes les stratégies.

Protocole : les sept stratégies sont écrites **avant** de regarder leurs
résultats. La sélection se fait sur 2018-2022, puis est vérifiée sur
2023 → aujourd'hui, période qui n'a servi à aucun choix.

## Données : Binance, paires tradées par le bot (21 actifs, jusqu'au 2026-09-25)

### 1. Tournoi (1 % de risque par trade pour toutes)

| Stratégie | Famille | Choix 2018-22 : CAGR | Baisse max | Sharpe | Calmar | Gagnants | Espérance | Vérif. 2023 → : CAGR | Baisse max | Sharpe | Calmar | Gagnants | Espérance |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| TrendGuard (référence) | Suivi de tendance | +41,2 % | -25,4 % | 1,25 | 1,62 | 44 % | +1,50 R | +37,2 % | -33,6 % | 1,18 | 1,11 | 35 % | +0,70 R |
| Tendance rapide | Suivi de tendance | +43,0 % | -29,7 % | 1,26 | 1,45 | 44 % | +1,47 R | +47,9 % | -34,2 % | 1,38 | 1,40 | 40 % | +0,89 R |
| Tendance lente | Suivi de tendance | +27,5 % | -24,5 % | 0,99 | 1,12 | 43 % | +1,09 R | +29,1 % | -26,9 % | 1,09 | 1,08 | 35 % | +0,75 R |
| Régime « largeur de marché » | Suivi de tendance | +35,5 % | -32,7 % | 1,14 | 1,08 | 44 % | +1,26 R | +23,9 % | -23,5 % | 0,92 | 1,02 | 34 % | +0,53 R |
| Régime BTC et largeur | Suivi de tendance | +35,2 % | -30,6 % | 1,14 | 1,15 | 46 % | +1,27 R | +23,9 % | -23,5 % | 0,92 | 1,02 | 34 % | +0,53 R |
| Rotation momentum | Momentum relatif | +35,3 % | -29,3 % | 1,09 | 1,21 | 39 % | +1,16 R | +15,4 % | -38,5 % | 0,65 | 0,40 | 32 % | +0,21 R |
| Retour à la moyenne | Contrarien (fort taux de réussite) | +7,5 % | -11,2 % | 0,52 | 0,67 | 69 % | +0,16 R | -0,4 % | -19,4 % | 0,03 | -0,02 | 61 % | +0,00 R |

- Choisie sur 2018-2022 (meilleur Calmar) : **TrendGuard (référence)**.
- Battent la référence sur les deux périodes : **aucune**.
- Le retour à la moyenne gagne **69 % puis 61 %** de ses trades, contre 44 % et 35 % pour TrendGuard, mais son espérance est de +0,16 R puis +0,00 R par trade : un taux de réussite élevé ne fait pas une stratégie rentable.

### 2. Méta-apprentissage : suivre la stratégie qui marche le mieux ?

Tous les 6 mois depuis 2020, le chef d'orchestre regarde les 24 mois précédents, et seulement eux.

| Portefeuille | 2020-22 : CAGR | Baisse max | Sharpe | Calmar | 2023 → : CAGR | Baisse max | Sharpe | Calmar |
|---|---|---|---|---|---|---|---|---|
| TrendGuard seule (référence) | +57,9 % | -25,4 % | 1,43 | 2,28 | +37,2 % | -33,6 % | 1,18 | 1,11 |
| Chef d'orchestre : meilleure des 7 sur 24 mois | +48,8 % | -29,7 % | 1,30 | 1,64 | +35,4 % | -35,1 % | 1,14 | 1,01 |
| Chef d'orchestre : 7 pondérées par Sharpe 24 mois | +50,4 % | -27,1 % | 1,41 | 1,86 | +28,0 % | -29,4 % | 1,08 | 0,95 |
| Chef d'orchestre : meilleur horizon de tendance | +59,7 % | -29,7 % | 1,45 | 2,01 | +45,3 % | -35,1 % | 1,32 | 1,29 |
| Répartition fixe : 3 horizons de tendance | +56,0 % | -26,4 % | 1,42 | 2,12 | +38,3 % | -31,6 % | 1,26 | 1,21 |
| Répartition fixe : les 7 stratégies | +47,5 % | -24,6 % | 1,44 | 1,93 | +25,5 % | -28,1 % | 1,07 | 0,91 |

Stratégie confiée par le chef d'orchestre (7 candidates) : 2020-01 → Régime « largeur de marché », 2020-07 → Régime « largeur de marché », 2021-01 → Tendance rapide, 2021-07 → Tendance rapide, 2022-01 → Tendance rapide, 2022-07 → Tendance rapide, 2023-01 → TrendGuard (référence), 2023-07 → Retour à la moyenne, 2024-01 → TrendGuard (référence), 2024-07 → Tendance rapide, 2025-01 → Tendance rapide, 2025-07 → Régime « largeur de marché », 2026-01 → Tendance lente, 2026-07 → Tendance rapide

- Portefeuilles qui battent TrendGuard seule sur les deux périodes (rendement divisé par la pire baisse) : **aucun**.
- **Chef d'orchestre : meilleur horizon de tendance** rapporte plus sur les deux périodes, mais avec des baisses maximales de -29,7 % et -35,1 % (référence : -25,4 % et -33,6 %) et un rapport rendement / pire baisse moins bon sur au moins une période : plus de risque, pas un meilleur apprentissage.

### 3. Probabilité de finir en gain selon la durée (TrendGuard, 2019 →)

| Durée | Fenêtres historiques en gain | en perte | 5 % pires | Médiane | Bootstrap : en gain | en perte | 5 % pires |
|---|---|---|---|---|---|---|---|
| 1 mois | 37 % | 31 % | -7,8 % | +0,0 % | 37 % | 31 % | -7,5 % |
| 3 mois | 58 % | 31 % | -9,1 % | +3,2 % | 64 % | 35 % | -12,5 % |
| 6 mois | 71 % | 27 % | -8,8 % | +7,4 % | 74 % | 26 % | -15,2 % |
| 12 mois | 80 % | 20 % | -8,0 % | +31,1 % | 84 % | 16 % | -14,9 % |
| 24 mois | 92 % | 8 % | -5,8 % | +113,8 % | 94 % | 6 % | -4,9 % |
| 36 mois | 100 % | 0 % | +26,1 % | +159,0 % | 97 % | 3 % | +14,0 % |

Les fenêtres ni en gain ni en perte sont celles passées entièrement en USDT (régime baissier). Les fenêtres se chevauchent : sur 7,7 ans d'historique, il n'y a que 3,9 périodes indépendantes de 24 mois et 2,6 de 36 mois. Les pourcentages sur longue durée sont donc des indications, pas des probabilités précises ; le bootstrap (10 000 trajectoires recomposées par blocs de 30 jours) en donne une estimation plus prudente.

## Ce que le bot en retient

- **Il réévalue les alternatives en continu, mais n'en change pas seul.**
  `python trendguard_bot.py diagnose` (et le diagnostic automatique hebdomadaire)
  classe les sept stratégies sur les 24 derniers mois et affiche la probabilité
  historique de gain par durée. Confier le capital à la meilleure des sept
  stratégies récentes a fait moins bien que TrendGuard seule sur les deux
  périodes (section 2) : ce classement est une information, pas un ordre.
- **« 99 % de réussite et 1 % de perte maximale » n'existe pas trade par
  trade.** La section 1 le montre : la stratégie au meilleur taux de réussite
  (retour à la moyenne) ne gagne presque rien. La limite de 1 % s'applique à
  chaque trade (TrendGuard : −1,0 R en moyenne), pas au portefeuille, qui
  traverse des baisses de 25 à 35 %.
- **Le « succès » se mesure sur la durée.** Sur un mois, la stratégie est plus
  souvent à plat (en USDT) ou en perte qu'en gain. Sur deux à trois ans, la
  quasi-totalité des fenêtres historiques finissent en gain (section 3), sans
  garantie pour l'avenir.
