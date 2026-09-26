# TrendGuard — le bot peut-il « apprendre et s'adapter » ?

Étude reproductible : `python research_adaptation.py --cache data_binance`
(données journalières Binance des 21 paires du bot, frais 0,1 % et slippage
0,1 % par côté).

## 1. Pourquoi pas « 99 % de réussite et 1 % de perte maximale »

L'espérance d'une stratégie vaut `taux de réussite × gain moyen − taux
d'échec × perte moyenne`. Un taux de réussite de 99 % combiné à des gains
élevés et à des pertes limitées à 1 % serait de l'argent gratuit : un marché
liquide le fait disparaître immédiatement.

Les stratégies qui affichent réellement 90 à 99 % de trades gagnants
(vente d'options, martingale, grille, « take-profit serré, stop large »)
gagnent peu souvent et perdent rarement mais énormément. C'est précisément ce
que la limite de 1 % par trade interdit.

TrendGuard fait le choix inverse et mesurable :

- **perte ≈ 1 % du capital par trade** : −1,0 R en moyenne, pire −2,8 R lors d'un gap ;
- **gains moyens de plusieurs R** ;
- **35 à 50 % de trades gagnants**.

## 2. Ce qui s'adapte déjà, par construction

Ces adaptations sont fixées à l'avance et non apprises sur les résultats :

| Mécanisme | Réagit à |
|---|---|
| Filtre de régime (BTC vs moyenne 150 j) | marché baissier → aucun achat, capital en USDT |
| Stop resserré en régime baissier (2 × vol au lieu de 5 ×) | retournement du marché |
| Stops et tailles proportionnels à la volatilité | actif calme ou agité → même risque de 1 % |
| Classement par momentum ajusté du risque | privilégie les tendances les plus solides |
| Filtre de liquidité (5 M$/j sur Binance) | actifs devenus illiquides exclus |
| Décision tardive : taille recalculée au prix réel | redémarrage en cours de journée |

## 3. Adapter le risque aux résultats récents : ce que disent les données

Protocole : chaque adaptation est jugée sur **2018-2022** (période de réglage),
puis vérifiée sur **2023 → septembre 2026**, une période qu'elle n'a pas vue.
Critère principal : Calmar, soit le rendement annuel divisé par la pire baisse.

| Variante | 2018-22 CAGR | 2018-22 baisse max | 2018-22 Calmar | 2023-26 CAGR | 2023-26 baisse max | 2023-26 Calmar |
|---|---|---|---|---|---|---|
| **Référence** | +41,2 % | −25,4 % | 1,62 | +37,2 % | −33,6 % | 1,11 |
| Baisse ≥ 10 % → risque × 0,5 | +31,8 % | −20,6 % | 1,54 | +32,1 % | −24,2 % | 1,33 |
| Baisse ≥ 20 % → risque × 0,5 | +42,1 % | −22,9 % | 1,84 | +31,3 % | −29,2 % | 1,07 |
| Espérance des 10 derniers trades < 0 → × 0,5 | +38,7 % | −20,5 % | **1,89** | +28,7 % | −28,7 % | 1,00 |
| Espérance des 30 derniers trades < 0 → × 0,5 | +42,1 % | −22,9 % | 1,84 | +21,9 % | −33,0 % | 0,66 |
| Risque corrélé ≤ 3 % | +17,7 % | −21,6 % | 0,82 | +31,9 % | −25,9 % | 1,23 |
| Max 3 entrées par jour | +40,6 % | −24,6 % | 1,65 | +37,2 % | −33,6 % | 1,11 |
| Risque fixe 0,7 % | +25,2 % | −16,2 % | 1,56 | +26,5 % | −25,7 % | 1,03 |
| Risque fixe 0,5 % | +17,5 % | −11,8 % | 1,48 | +18,2 % | −19,7 % | 0,93 |

Table complète (15 variantes, pire mois, Sharpe) : sortie du script.

### Lecture

- **Le piège du sur-ajustement se voit en direct.** La variante qui aurait été
  choisie sur 2018-2022 (« réduire le risque après 10 trades décevants »,
  Calmar 1,89) fait **moins bien** que la référence sur 2023-2026. Les autres
  règles « apprises des résultats récents » se comportent de la même façon.
- Les variantes qui brillent sur 2023-2026 (plafond de risque corrélé à 3 %)
  étaient mauvaises sur 2018-2022. Les retenir maintenant reviendrait à
  choisir en regardant la réponse.
- « Max 3 entrées par jour » ne change qu'un jour sur 8 ans : aucun effet réel.

### Décision

1. **Aucune adaptation n'est activée par défaut** : aucune ne bat la
   référence sur les deux périodes.
2. **Profil prudent proposé en option** (`TG_DD_THROTTLE=0.10:0.5`) : le risque
   par trade est divisé par deux tant que le capital est à plus de 10 % sous
   son plus haut. Ce n'est pas un avantage caché mais un **choix de confort**
   qui réduit la pire baisse (−20,6 % / −24,2 % au lieu de −25,4 % / −33,6 %)
   au prix du rendement. Il fait jeu égal avec un risque fixe de 0,7 % sur
   2018-2022 et mieux sur 2023-2026.

## 4. L'« apprentissage » retenu : l'auto-diagnostic

Plutôt que de modifier ses règles seul, le bot **mesure en continu si son
avantage statistique existe encore et alerte** (`python trendguard_bot.py
diagnose`, et automatiquement tous les 7 jours pendant qu'il tourne,
`TG_AUTO_DIAGNOSE_DAYS`) :

| Contrôle | Alerte si |
|---|---|
| Espérance des trades des 24 derniers mois (intervalle de confiance 90 %, bootstrap) | intervalle entièrement négatif → avantage disparu |
| Rendement sur 12 mois glissants vs historique | sous le 10e percentile |
| Trades réels du bot vs distribution historique (test statistique) | moins de 2 % de chances d'un résultat aussi faible |
| Série de pertes en cours | au-delà du 95e percentile attendu |
| Données (retard, trous, prix aberrants, liquidité) | décision du jour faussée |
| Système (horloge, latence, bot actif, disque, arrêt d'urgence) | ordres refusés ou retardés |
| Portefeuille (risque engagé, corrélation, krach de −20 % / −35 %) | concentration excessive |

La décision reste humaine : réduire le risque, activer le profil prudent ou
arrêter. Un bot qui change seul ses paramètres après une mauvaise série fait,
d'après le tableau ci-dessus, en moyenne moins bien.

Suite de l'étude : [`STRATEGIES.md`](STRATEGIES.md) teste l'autre forme
d'« apprentissage », changer de stratégie selon les résultats récents (tournoi
de sept stratégies et chef d'orchestre), avec la même conclusion.

## 5. Premier diagnostic réel (26 septembre 2026, Windows)

- Stratégie saine : espérance des 76 trades des 24 derniers mois
  **+1,16 R**, intervalle 90 % [+0,30 ; +2,11] ; rendement sur 12 mois +44,6 %
  (percentile 57).
- Régime BTC haussier depuis 38 jours (+19 % au-dessus de la moyenne 150 j).
- Horloge du PC en avance d'environ 1,3 s sur Binance, à synchroniser.
- ETC, NEO, XTZ et ALGO sont exclus des achats (liquidité Binance < 5 M$/j) ;
  la recherche Coin Metrics les incluait (volume tous marchés confondus).
