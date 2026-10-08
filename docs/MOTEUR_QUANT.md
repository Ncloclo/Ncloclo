# Moteur quantitatif (étape 8 du prompt maître)

Le laboratoire mathématique et statistique de TrendGuard : il mesure avant de
prédire et vérifie avant de croire, sur les cours réels de Binance depuis 2017.
Il est consultatif : aucune de ses mesures ne change la règle en service, et il
ne passe aucun ordre (il ne charge aucun module qui en passe).

```text
rendements (simples, log, cumulés, annualisés sur 365 jours) → descriptives
→ lois (normale, Laplace, Student) et normalité → tests d'hypothèse, tests multiples
→ autocorrélation, stationnarité (ADF, KPSS), persistance (ratio de variance)
→ volatilité (historique, EWMA, GARCH, prévisions comparées hors échantillon)
→ corrélations, covariance rétrécie, composantes principales
→ régression et diagnostics, modèle factoriel (bêta à BTC), cointégration
→ la règle a-t-elle un pouvoir prédictif ? (cassures, momentum, trades)
→ anomalies, ruptures de volatilité, régimes cachés → manifeste, conclusions
```

Code : [`trendguard/moteur_quant.py`](../trendguard/moteur_quant.py).
Tests : [`tests/test_moteur_quant.py`](../tests/test_moteur_quant.py).
Dernier rapport : [`QUANT.md`](QUANT.md).

```text
python trendguard_bot.py quant                                   # rapport complet (≈ 30 secondes)
python trendguard_bot.py quant --cache data_binance --out docs/QUANT.md
```

## Ce que disent les chiffres

Le rapport écrit ses conclusions lui-même, à partir des mesures ; aucune n'est
rédigée à l'avance. Les principales, au 7 octobre 2026 :

- les rendements des cryptos ne suivent pas une loi normale (21 sur 21) : leurs
  queues sont épaisses ; d'où une VaR qui ne la suppose pas (étape 11) ;
- la volatilité se regroupe : un jour agité en annonce d'autres ; d'où des stops
  proportionnels à la volatilité ;
- la tendance existe mais elle est faible : ratio de variance à 30 jours
  au-dessus de 1 pour 13 cryptos sur 21, jamais significatif après correction ;
- le signal d'achat seul prédit peu : +5,4 points de rendement moyen à 30 jours
  contre un jour ordinaire, intervalle −2,1 à +11,6, non prouvé ; le momentum
  seul n'a pas de pouvoir prédictif mesurable ;
- **l'avantage vient de la gestion des trades** : 41 % de gagnants seulement,
  mais un gain moyen de +4,37 R contre une perte moyenne de −0,97 R ; les 10 %
  meilleurs trades font tout le résultat : couper vite les pertes et laisser
  courir les gains, c'est la règle ; c'est aussi pourquoi on ne la juge jamais
  sur quelques trades ;
- les 21 cryptos forment environ 2,6 paris indépendants : un facteur commun
  explique 61 % de leurs mouvements (ce que l'étape 12 en tire pour le
  portefeuille).

## Exigences de l'étape 8 → TrendGuard

| Exigence de l'étape 8 | Dans TrendGuard |
| --- | --- |
| Cœur mathématique et numérique (§5-6) | numpy ; lois gamma et bêta incomplètes, Student, Kolmogorov écrites ici et éprouvées contre des valeurs connues ; valeurs non finies écartées et comptées, jamais remplacées ; conditionnement des matrices |
| Rendements (§7) | simples, logarithmiques, cumulés ; annualisation toujours avec sa fréquence (365 jours) ; cours nul ou négatif refusé |
| Statistiques, lois, normalité (§8-10) | moyenne, médiane, variance, quantiles, écart interquartile, MAD, asymétrie, aplatissement ; normale, Laplace, Student par maximum de vraisemblance, AIC, BIC, Kolmogorov-Smirnov ; Jarque-Bera, Anderson-Darling ; NORMAL, NON_NORMAL ou données insuffisantes |
| Tests d'hypothèse, tests multiples (§11-12) | Student, Welch, Mann-Whitney : hypothèses, statistique, p, seuil, décision, effet, taille ; Bonferroni, Holm, Benjamini-Hochberg |
| Séries, stationnarité, autocorrélation (§13-15) | autocorrélations simples et partielles, Ljung-Box ; ADF (retards par l'AIC, valeurs de MacKinnon) et KPSS, une conclusion seulement s'ils s'accordent |
| Volatilité (§16) | historique, EWMA, GARCH(1,1) par maximum de vraisemblance ; prévisions du lendemain comparées hors échantillon (QLIKE) |
| Corrélation, covariance, dépendance (§17-19) | Pearson, Spearman, Kendall ; corrélation les jours de chute de BTC ; covariance rétrécie de Ledoit et Wolf, symétrie, positivité, conditionnement |
| Régression et diagnostics, facteurs, composantes principales (§20-24) | moindres carrés : erreurs types (Newey-West en option), intervalles, R², AIC, Durbin-Watson, Breusch-Pagan, VIF ; bêta à BTC, part expliquée, α et son test ; composantes principales et paris indépendants |
| Cointégration, retour à la moyenne (§26-27) | Engle-Granger, demi-vie, z-score (ETH contre BTC) ; mesuré, aucune stratégie d'arbitrage |
| Momentum, signal, significativité (§28, §43, §52) | coefficient d'information du momentum ; étude d'événement des signaux de la règle avec intervalles par grappes mensuelles et correction de Holm ; trades en R |
| Anomalies, ruptures, régimes (§39-41) | écarts robustes au-delà de 10 MAD : marché, erreur de données probable ou inexpliqué, jamais corrigés ; ruptures de volatilité (Inclán-Tiao) ; modèle de Markov caché à deux états comparé au régime de la règle |
| Monte-Carlo, bootstrap, graine (§33-35) | rééchantillonnage par grappes à graine (PCG64) ; Monte-Carlo des étapes 10 et 11 |
| Expériences, reproductibilité, audit (§63-67) | manifeste : version du code, empreintes des données et du code, graine ; empreinte du résultat identique quand on refait |
| Contrat, sorties standard (§75, §89) | `QuantResult.v1` : empreintes, conclusions, réserves, limites dont « une mesure du passé n'est ni une certitude ni une promesse » ; jamais une autorisation |
| Interdiction d'exécuter (§87-88, §93) | aucun accès aux ordres ; aucune IA dans les calculs |

## Déjà fait par une autre étape

- **Stress, VaR, CVaR, valeurs extrêmes, Monte-Carlo du portefeuille** (§36-38)
  : moteur de risque (étape 11).
- **Sur-ajustement, fuite, validation temporelle, Sharpe dégonflé, coûts,
  capacité** (§46-52, §57-59) : moteurs de stratégie et de backtest (étapes 9
  et 10).
- **Prévisions probabilistes, calibration, signal au format commun** (§30-31,
  §43, §61) : cœur d'intelligence financière (étape 7).
- **Optimisation de portefeuille** (§54-56) : moteur de portefeuille
  (étape 12).
- **Comité d'agents, désaccord** (§68-71) : comité de l'étape 5, dont un agent
  quantitatif.

## Ce qui ne s'applique pas

- **Facteurs d'actions** (valeur, taille, qualité, §22) et **macroéconomie** :
  des cryptos n'ont ni bilan ni bénéfice ; le facteur commun est BTC.
- **Arbitrage statistique comme stratégie** (§42) : la cointégration est
  mesurée ; une stratégie demanderait la vente à découvert, absente du Spot.
- **ARIMA, apprentissage profond, Black-Litterman** (§29, §55) : aucun gain
  prouvé, et pas de vues à mélanger ; le prompt lui-même préfère un modèle
  simple et robuste (§102).
- **Classement par grappes** (§25) : les composantes principales montrent un
  seul facteur dominant ; des grappes n'ajouteraient rien de mesurable.
- **API, Kafka, tables dédiées** (§72-74) : une étude hors ligne ; son rapport
  et son manifeste suffisent.
