# Le bot gagnerait-il à utiliser un levier ?

Historique Binance des 21 cryptos du bot jusqu'au 2026-11-22 ; règle, paramètres et régime BTC inchangés. Régénérer : `python -m research.levier --cache data_binance`. Le bot refait la même étude chaque semaine (`python trendguard_bot.py levier etude`), sur son univers et l'historique le plus récent ; son verdict s'affiche dans la page Veille du panneau.

## Ce que fait le levier

Le levier multiplie la taille de chaque position : à 2×, une perte au stop coûte 2 % du capital au lieu de 1 %, et le bot emprunte dès que ses positions dépassent son capital. Gains et pertes sont multipliés ensemble, et l'emprunt coûte des intérêts (marge) ou un financement (contrats perpétuels).

Mesure : la stratégie rejouée sans levier, puis ses rendements journaliers multipliés par le levier de la veille, moins le coût de l'emprunt (10 % par an sur la part empruntée). Exposition moyenne sans levier : 2018-2022 : 17 % ; 2023 → 2026-11-22 : 33 % ; 24/05/2026 → 2026-11-22 : 24 %.

## Politiques et règle, fixées avant les mesures

- Levier fixe : 1,25×, 1,5×, 2×, 3×.
- Adaptatif : 1,5× seulement en marché haussier (BTC au-dessus de sa moyenne de 150 jours) et tant que la stratégie sans levier est à moins de 10 % de son sommet, 1× sinon ; décidé à la clôture de la veille.
- Une politique n'est permise que si, sur 2018-2022 ET depuis 2023, son Calmar n'est pas moins bon que sans levier et sa pire baisse reste au-dessus de −30 % (10 points avant l'arrêt d'urgence du bot, à −40 %). Parmi les politiques permises : celle dont le plus faible des deux Calmar est le meilleur. La période jamais vue informe et ne juge pas.

## Résultats

| Politique | 2018-2022 : CAGR | pire baisse | Calmar | jours d'emprunt | 2023 → 2026-11-22 : CAGR | pire baisse | Calmar | jours d'emprunt | 24/05/2026 → 2026-11-22 : CAGR | pire baisse | Calmar | jours d'emprunt | Verdict de la règle |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sans levier | +41,2 % | −25,4 % | 1,62 | 0 % | +32,0 % | −33,6 % | 0,95 | 0 % | +19,9 % | −10,5 % | 1,89 | 0 % | référence |
| 1,25× fixe | +51,4 % | −31,5 % | 1,63 | 5 % | +38,9 % | −40,3 % | 0,97 | 19 % | +24,1 % | −13,1 % | 1,84 | 21 % | refusé : pire baisse −40 %, sous −30 % |
| 1,5× fixe | +61,3 % | −37,4 % | 1,64 | 9 % | +45,1 % | −46,4 % | 0,97 | 26 % | +27,8 % | −15,6 % | 1,78 | 24 % | refusé : pire baisse −46 %, sous −30 % |
| 2× fixe | +79,2 % | −48,7 % | 1,63 | 18 % | +55,3 % | −57,1 % | 0,97 | 32 % | +34,2 % | −20,4 % | 1,68 | 27 % | refusé : pire baisse −57 %, sous −30 % |
| 3× fixe | +104,3 % | −67,9 % | 1,53 | 23 % | +65,0 % | −73,2 % | 0,89 | 39 % | +42,8 % | −29,5 % | 1,45 | 27 % | refusé : Calmar moins bon que sans levier ; pire baisse −73 %, sous −30 % |
| adaptatif : 1,5× en marché haussier, à moins de 10 % du sommet | +47,9 % | −39,7 % | 1,21 | 7 % | +38,3 % | −37,8 % | 1,01 | 19 % | +28,6 % | −15,6 % | 1,83 | 24 % | refusé : Calmar moins bon que sans levier ; pire baisse −40 %, sous −30 % |

## Verdict (règle fixée avant les mesures)

- **Aucun levier (1×) : aucune politique ne passe la règle**.
- Coût d'emprunt doublé (20 % par an) : aucun levier (1×) : aucune politique ne passe la règle.
- Rien n'est appliqué : le bot trade au comptant, sans emprunt. Un levier réel exigerait votre accord exprès, le testnet d'abord et un moteur d'emprunt (marge ou contrats à terme) qui n'existe pas encore ; les verrous du réel ne changent pas.

## Lecture

- Le levier fixe multiplie le rendement et la pire baisse ensemble : le Calmar va de 1,53 à 1,64 sur 2018-2022 et de 0,89 à 0,97 depuis 2023 (sans levier : 1,62 et 0,95).
- Sans levier, la pire baisse atteint déjà −33,6 % : au-delà de −30 %, il ne reste pas la marge exigée avant l'arrêt d'urgence (−40 %).
- À 1,25× seulement, la pire baisse aurait été de −40,3 % : l'arrêt d'urgence du bot aurait été touché.
- La politique adaptative fait moins bien que sans levier au Calmar sur 2018-2022. Explication probable, non vérifiée : elle emprunte près des sommets, là où arrivent les retournements que subit la règle de tendance.
- Sur la période jamais vue, 1,25× aurait rapporté +24,1 % par an contre +19,9 % sans levier : six mois, trop peu pour juger, et la règle ne la regarde pas.

## Limites

- Un backtest n'est pas une garantie de gain.
- Approximation : des positions multipliées par le levier et rééquilibrées chaque jour. Les chutes dans la journée ne se voient pas à la clôture : une position à levier peut être liquidée avant que le stop ne joue.
- Le coût de l'emprunt varie : le financement des contrats perpétuels dépasse souvent 30 % par an dans l'euphorie ; 10 % est une moyenne supposée, 20 % le test de résistance.
- Le levier multiplie aussi les plafonds de risque gelés du bot (1 % de risque par trade, 6 % au total, 25 % du capital par position) : c'est un changement de la règle, pas un réglage.
