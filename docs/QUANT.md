# Laboratoire quantitatif de TrendGuard

Tiré des cours journaliers de Binance (2017-08-17 → 2026-10-07, 21 cryptos) par
`python trendguard_bot.py quant` (étape 8 du prompt maître,
[`MOTEUR_QUANT.md`](MOTEUR_QUANT.md)). Consultatif : rien ici ne change la
règle.

## Conclusions

- Rendements journaliers non normaux pour 21 cryptos sur 21 ; la loi de Student
  (queues épaisses) les décrit le mieux pour 19 sur 21.
- Cours non stationnaires pour 18 sur 21, rendements stationnaires pour 21 sur
  21 : les modèles travaillent sur les rendements, jamais sur les cours.
- La volatilité se regroupe (Ljung-Box sur les carrés) pour 21 cryptos sur 21 :
  un jour agité en annonce d'autres, d'où les stops proportionnels à la
  volatilité.
- Ratio de variance à 30 jours au-dessus de 1 (les mouvements se prolongent)
  pour 13 cryptos sur 21, significatif après correction de Holm pour 0.
- Après un signal d'achat de la règle, le rendement moyen à 30 jours dépasse
  celui d'un jour ordinaire de +5,4 points, pas prouvé statistiquement
  (intervalle −2,1 à +11,6, p de Holm 0,306, 2 666 signaux) : le signal seul
  prédit peu.
- Coefficient d'information du momentum à 30 jours : −0,009 en moyenne (p
  0,798) : aucun pouvoir prédictif mesurable à lui seul.
- L'avantage vient de la gestion des trades : 41 % de gagnants seulement, mais
  un gain moyen de +4,37 R contre une perte moyenne de −0,97 R (asymétrie
  +4,6) ; les 10 % meilleurs trades font 102 % du résultat ; moyenne +1,20 R par
  trade (intervalle +0,57 à +1,81, 430 trades) : couper vite les pertes, laisser
  courir les gains.
- Sur trois ans, 61 % des mouvements des 21 cryptos viennent d'un facteur
  commun : elles forment environ 2,6 paris indépendants, pas 21.
- Prévision de la volatilité du lendemain (hors échantillon) : la meilleure est
  « EWMA » (perte QLIKE la plus basse).
- Régimes cachés de BTC : calme 33 % de volatilité annuelle, agité 105 % ;
  d'accord avec le régime de la règle 53 % des jours (deux mesures différentes :
  volatilité contre tendance).

## 1. Rendements et lois

| Crypto | depuis | rendement annualisé | volatilité annualisée | asymétrie | aplatissement (excès) | pire jour | meilleur jour | loi | normalité |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BTC | 2017-08-17 | +38 % | 67 % | −0,95 | 15,9 | −50 % | +20 % | Laplace | non normale |
| ETH | 2017-08-17 | +26 % | 87 % | −0,88 | 12,1 | −59 % | +23 % | Laplace | non normale |
| BNB | 2017-11-06 | +100 % | 93 % | +0,33 | 21,7 | −58 % | +53 % | Student | non normale |
| XRP | 2018-05-04 | +6 % | 98 % | +0,59 | 18,8 | −54 % | +55 % | Student | non normale |
| ADA | 2018-04-17 | +1 % | 99 % | +0,15 | 10,2 | −53 % | +54 % | Student | non normale |
| DOGE | 2019-07-05 | +54 % | 125 % | +6,30 | 143,1 | −49 % | +159 % | Student | non normale |
| TRX | 2018-06-11 | +26 % | 85 % | +0,25 | 32,9 | −57 % | +67 % | Student | non normale |
| LINK | 2019-01-16 | +53 % | 107 % | −0,36 | 12,3 | −65 % | +49 % | Student | non normale |
| LTC | 2017-12-13 | −15 % | 91 % | −0,60 | 9,3 | −49 % | +28 % | Student | non normale |
| BCH | 2019-11-28 | +5 % | 96 % | −0,12 | 19,8 | −62 % | +46 % | Student | non normale |
| XLM | 2018-05-31 | −5 % | 98 % | +1,09 | 16,8 | −46 % | +56 % | Student | non normale |
| ETC | 2018-06-12 | −6 % | 98 % | −0,04 | 12,5 | −57 % | +36 % | Student | non normale |
| ZEC | 2019-03-21 | +52 % | 112 % | −0,14 | 8,4 | −56 % | +49 % | Student | non normale |
| DASH | 2019-03-28 | −7 % | 107 % | +0,43 | 11,5 | −49 % | +46 % | Student | non normale |
| NEO | 2017-11-20 | −26 % | 107 % | −0,34 | 7,9 | −50 % | +37 % | Student | non normale |
| XTZ | 2019-09-24 | −13 % | 102 % | −0,51 | 15,0 | −64 % | +40 % | Student | non normale |
| ALGO | 2019-06-22 | −33 % | 111 % | −0,58 | 12,5 | −70 % | +41 % | Student | non normale |
| DOT | 2020-08-18 | −15 % | 100 % | +0,25 | 9,9 | −48 % | +45 % | Student | non normale |
| UNI | 2020-09-17 | +15 % | 114 % | +1,18 | 13,5 | −41 % | +69 % | Student | non normale |
| AAVE | 2020-10-15 | +26 % | 109 % | +0,00 | 3,7 | −42 % | +28 % | Student | non normale |
| ICP | 2021-05-11 | −58 % | 108 % | +0,13 | 6,1 | −36 % | +35 % | Student | non normale |

BTC, lois ajustées (maximum de vraisemblance) :

| Loi | log-vraisemblance | AIC | BIC | Kolmogorov-Smirnov | p |
| --- | --- | --- | --- | --- | --- |
| normale | 6 425 | −12 846 | −12 833 | 0,096 | < 0,001 |
| Laplace | 6 920 | −13 835 | −13 823 | 0,024 | 0,037 |
| Student | 6 914 | −13 821 | −13 803 | 0,020 | 0,142 |

Degrés de liberté de la loi de Student : 2,2 (plus c'est bas, plus les queues
sont épaisses ; une normale aurait l'infini).

## 2. Stationnarité, autocorrélation, persistance

| Série (BTC) | ADF | seuil 5 % | KPSS | seuil 5 % | conclusion |
| --- | --- | --- | --- | --- | --- |
| log du cours | −1,44 (p > 10 %) | −2,86 | 9,472 | 0,463 | non stationnaire |
| rendements | −40,08 (p < 1 %) | −2,86 | 0,065 | 0,463 | stationnaire |

Autocorrélations de BTC aux retards 1 à 5 : rendements −0,050, +0,045, +0,003,
+0,011, +0,024 ; valeur absolue des rendements +0,176, +0,123, +0,118, +0,180,
+0,147.

| Crypto | VR 5 j | VR 10 j | VR 30 j | z (30 j) | p de Holm (30 j) | Ljung-Box carrés p |
| --- | --- | --- | --- | --- | --- | --- |
| BTC | 0,98 | 1,02 | 1,15 | +1,03 | 1,000 | < 0,001 |
| ETH | 0,98 | 1,03 | 1,15 | +1,09 | 1,000 | < 0,001 |
| BNB | 1,03 | 1,03 | 1,35 | +1,72 | 1,000 | < 0,001 |
| XRP | 0,96 | 1,03 | 1,09 | +0,56 | 1,000 | < 0,001 |
| ADA | 0,98 | 1,01 | 1,21 | +1,50 | 1,000 | < 0,001 |
| DOGE | 1,02 | 1,04 | 1,16 | +0,61 | 1,000 | < 0,001 |
| TRX | 0,86 | 0,80 | 0,79 | −1,19 | 1,000 | < 0,001 |
| LINK | 0,92 | 0,93 | 0,96 | −0,24 | 1,000 | < 0,001 |
| LTC | 0,91 | 0,88 | 0,84 | −1,13 | 1,000 | < 0,001 |
| BCH | 0,91 | 0,94 | 0,91 | −0,49 | 1,000 | < 0,001 |
| XLM | 1,00 | 1,06 | 1,07 | +0,41 | 1,000 | < 0,001 |
| ETC | 1,02 | 1,04 | 1,14 | +0,81 | 1,000 | < 0,001 |
| ZEC | 1,04 | 1,06 | 1,18 | +1,18 | 1,000 | < 0,001 |
| DASH | 1,08 | 1,02 | 0,96 | −0,27 | 1,000 | < 0,001 |
| NEO | 0,93 | 0,95 | 1,00 | +0,02 | 1,000 | < 0,001 |
| XTZ | 0,86 | 0,86 | 0,84 | −0,94 | 1,000 | < 0,001 |
| ALGO | 0,95 | 0,96 | 1,01 | +0,07 | 1,000 | < 0,001 |
| DOT | 0,96 | 0,91 | 1,02 | +0,11 | 1,000 | < 0,001 |
| UNI | 0,86 | 0,83 | 0,95 | −0,29 | 1,000 | < 0,001 |
| AAVE | 1,01 | 1,00 | 1,00 | +0,01 | 1,000 | < 0,001 |
| ICP | 1,04 | 1,02 | 0,99 | −0,04 | 1,000 | < 0,001 |

## 3. Volatilité de BTC

GARCH(1,1) : α 0,104, β 0,869, persistance 0,973, demi-vie d'un choc 26 jours ;
volatilité de long terme 77 %, prévue demain 45 % (annualisée) ; EWMA actuelle
35 %.

Prévision du lendemain, hors échantillon (1 002 jours) :

| Modèle | perte QLIKE | erreur quadratique (× 10⁶) |
| --- | --- | --- |
| GARCH(1,1) | −6,3994 | 2,07 |
| EWMA | −6,4472 | 1,99 |
| historique 30 jours | −6,3821 | 2,03 |

## 4. Liens entre cryptos (trois ans)

21 cryptos, 1 095 jours communs. Covariance échantillon : conditionnement 216 ;
rétrécie (Ledoit-Wolf, δ = 0,01) : 184 ; les deux positives. Corrélation moyenne
0,57 ; première composante 61 % de la variance, deuxième 5 % ; paris
indépendants 2,6.

| Crypto | corrélation à BTC (Pearson) | Spearman | Kendall | les jours où BTC chute de 5 % | les autres jours | bêta | part expliquée par BTC | α annualisé | p (BH) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ETH | 0,82 | 0,80 | 0,67 | 0,57 | 0,79 | 1,15 | 67 % | −27 % | 0,563 |
| BNB | 0,67 | 0,69 | 0,59 | 0,63 | 0,62 | 0,74 | 45 % | +16 % | 0,630 |
| XRP | 0,65 | 0,72 | 0,65 | 0,73 | 0,60 | 1,06 | 43 % | −5 % | 0,882 |
| ADA | 0,72 | 0,74 | 0,60 | 0,48 | 0,68 | 1,35 | 51 % | −49 % | 0,553 |
| DOGE | 0,77 | 0,76 | 0,58 | 0,43 | 0,74 | 1,45 | 60 % | −40 % | 0,563 |
| TRX | 0,26 | 0,39 | 0,27 | 0,31 | 0,21 | 0,32 | 7 % | +33 % | 0,588 |
| LINK | 0,69 | 0,73 | 0,61 | 0,46 | 0,65 | 1,25 | 48 % | −27 % | 0,630 |
| LTC | 0,61 | 0,64 | 0,54 | 0,35 | 0,57 | 0,92 | 38 % | −33 % | 0,588 |
| BCH | 0,63 | 0,67 | 0,46 | 0,42 | 0,59 | 1,10 | 40 % | −31 % | 0,630 |
| XLM | 0,54 | 0,68 | 0,54 | 0,52 | 0,49 | 0,98 | 29 % | −16 % | 0,808 |
| ETC | 0,70 | 0,71 | 0,56 | 0,40 | 0,67 | 1,13 | 49 % | −61 % | 0,323 |
| ZEC | 0,43 | 0,48 | 0,36 | 0,38 | 0,42 | 1,07 | 19 % | +92 % | 0,516 |
| DASH | 0,48 | 0,59 | 0,40 | 0,27 | 0,46 | 1,08 | 23 % | −16 % | 0,808 |
| NEO | 0,64 | 0,67 | 0,50 | 0,35 | 0,59 | 1,19 | 40 % | −79 % | 0,323 |
| XTZ | 0,61 | 0,68 | 0,47 | 0,54 | 0,55 | 1,09 | 37 % | −65 % | 0,445 |
| ALGO | 0,64 | 0,67 | 0,49 | 0,42 | 0,60 | 1,24 | 41 % | −40 % | 0,588 |
| DOT | 0,65 | 0,68 | 0,49 | 0,46 | 0,61 | 1,16 | 42 % | −85 % | 0,323 |
| UNI | 0,59 | 0,63 | 0,50 | 0,35 | 0,55 | 1,30 | 35 % | −27 % | 0,707 |
| AAVE | 0,63 | 0,63 | 0,51 | 0,51 | 0,59 | 1,26 | 40 % | −14 % | 0,808 |
| ICP | 0,53 | 0,59 | 0,40 | 0,38 | 0,50 | 1,12 | 28 % | −39 % | 0,630 |

ETH et BTC (log des cours, depuis 2017-08-17) : statistique d'Engle-Granger
−2,42 (p > 10 %) : pas de cointégration prouvée ; écart actuel +0,8 écart-type
de sa moyenne d'un an. Aucune stratégie d'arbitrage n'en est tirée.

## 5. La règle a-t-elle un pouvoir prédictif ?

Signal d'achat de la règle (cassure du plus haut de 30 jours, momentum positif,
liquidité), rendement des jours suivants contre celui d'un jour ordinaire de la
même crypto achetable :

| Signal | horizon | signaux | rendement moyen | médian | positifs | jour ordinaire | écart | intervalle 95 % | p de Holm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| règle (régime haussier) | 10 j | 2 717 | +3,5 % | +0,6 % | 52 % | +0,0 % | +3,4 | +0,1 à +6,4 | 0,111 |
| règle (régime haussier) | 30 j | 2 666 | +5,2 % | +2,8 % | 54 % | −0,2 % | +5,4 | −2,1 à +11,6 | 0,306 |
| règle (régime haussier) | 60 j | 2 608 | +2,4 % | −2,2 % | 48 % | −0,5 % | +2,8 | −7,0 à +12,9 | 0,586 |
| cassure seule | 10 j | 3 018 | +2,9 % | +0,3 % | 51 % | +0,0 % | +2,9 | −0,3 à +5,8 | 0,222 |
| cassure seule | 30 j | 2 967 | +4,4 % | +2,3 % | 53 % | −0,2 % | +4,6 | −2,0 à +10,5 | 0,342 |
| cassure seule | 60 j | 2 908 | +2,0 % | −2,0 % | 48 % | −0,5 % | +2,5 | −6,8 à +11,4 | 0,617 |

Momentum de la règle (90 jours) et rendement des 30 jours suivants, 97 dates
sans chevauchement (2018-08-22 → 2026-08-10) : coefficient d'information moyen
−0,009 (t −0,26, p 0,798) ; momentum positif moins négatif +0,0 points (p 0,977,
51 dates).

Trades du backtest de la règle depuis 2018-01-01 (430 trades, frais et
glissement compris), en multiples du risque pris :

| Moyenne | médiane | gagnants | gain moyen | perte moyenne | asymétrie | part des 10 % meilleurs | t (moyenne = 0) | intervalle 95 % (mois) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| +1,20 R | −0,47 R | 41 % | +4,37 R | −0,97 R | +4,6 | 102 % | +5,06 (p < 0,001) | +0,57 à +1,81 R |

## 6. Anomalies, ruptures, régimes cachés

Rendements à plus de 10 écarts robustes : 86 (événements de marché 72, erreurs
de données probables 0, inexpliqués 14).

| Crypto | jour | rendement | écart robuste | lecture |
| --- | --- | --- | --- | --- |
| DOGE | 2021-01-28 | +159 % | +50 | marché |
| TRX | 2024-12-03 | +67 % | +31 | marché |
| TRX | 2020-03-12 | −57 % | −27 | marché |
| BTC | 2020-03-12 | −50 % | −23 | marché |
| DOGE | 2021-04-16 | +70 % | +22 | marché |
| BNB | 2020-03-12 | −58 % | −22 | marché |
| BNB | 2021-02-19 | +53 % | +20 | marché |
| ETH | 2020-03-12 | −59 % | −20 | marché |
| DOGE | 2021-01-02 | +62 % | +20 | marché |
| BCH | 2020-03-12 | −62 % | −19 | marché |

Ruptures de la volatilité de BTC (Inclán-Tiao) : 2017-12-06 (93 % → 130 %) ;
2018-04-13 (130 % → 67 %) ; 2018-08-11 (67 % → 39 %) ; 2018-11-14 (39 % → 68
%) ; 2019-04-02 (68 % → 92 %) ; 2019-07-19 (92 % → 58 %) ; 2020-03-08 (58 % →
134 %) ; 2020-06-07 (134 % → 50 %) ; 2020-12-16 (50 % → 94 %) ; 2021-06-28 (94 %
→ 69 %) ; 2022-06-20 (69 % → 62 %) ; 2022-11-12 (62 % → 49 %) ; 2023-03-30 (49 %
→ 41 %) ; 2023-06-29 (41 % → 30 %) ; 2023-10-16 (30 % → 46 %) ; 2024-02-26 (46 %
→ 65 %) ; 2024-05-27 (65 % → 50 %) ; 2025-04-12 (50 % → 31 %) ; 2025-09-25 (31 %
→ 40 %) ; 2026-01-29 (40 % → 60 %) ; 2026-04-30 (60 % → 38 %).

Modèle de Markov caché à deux états (BTC) : calme 33 % de volatilité, durée
moyenne 8 jours ; agité 105 %, 4 jours ; BTC calme 70 % du temps. Accord avec le
régime de la règle (BTC au-dessus de sa moyenne de 150 jours) : 53 %.

## 7. Manifeste

Exécution `3ab21baf-87a2-403c-8cf3-59a26bb179f3`, moteur quant-1.0.0, code
`b888347-dirty` (empreinte `3998c767b2663e2d`), données `021a4bf4a60a0d77`,
graine 7 (numpy PCG64), numpy 2.5.3, pandas 3.0.6 ; empreinte du résultat
`752f268062c0a3f1` (refait, identique).

## 8. Limites

- Une mesure du passé n'est ni une certitude ni une promesse.
- Cours journaliers de Binance depuis 2017 : vingt et une cryptos encore cotées
  (biais du survivant), ni carnet d'ordres ni données intrajournalières.
- Les rendements des cryptos ne suivent pas une loi normale : les tests qui la
  supposent sont donnés à côté de tests qui ne la supposent pas (rangs,
  rééchantillonnage par blocs).
- Une relation statistique n'est pas une cause, ni une promesse : elle a été
  mesurée sur le passé.
