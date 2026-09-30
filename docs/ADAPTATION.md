# TrendGuard — le bot peut-il « apprendre et s'adapter » ?

Étude reproductible : `python -m research.adaptation --cache data_binance`
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

- **perte ≈ 1 % du capital par trade** : −1,0 R en moyenne, pire −2,8 R lors
  d'un gap ;
- **gains moyens de plusieurs R** ;
- **35 à 50 % de trades gagnants**.

## 2. Ce qui s'adapte déjà, par construction

Ces adaptations sont fixées à l'avance et non apprises sur les résultats :

| Mécanisme | Réagit à |
| --- | --- |
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
| --- | --- | --- | --- | --- | --- | --- |
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
| --- | --- |
| Espérance des trades des 24 derniers mois (intervalle de confiance 90 %, bootstrap) | intervalle entièrement négatif → avantage disparu |
| Rendement sur 12 mois glissants vs historique | sous le 10e percentile |
| Trades réels du bot vs distribution historique (test statistique) | moins de 2 % de chances d'un résultat aussi faible |
| Série de pertes en cours | au-delà du 95e percentile attendu |
| Données (retard, trous, prix aberrants, liquidité) | décision du jour faussée |
| Système (horloge, latence, bot actif, disque, arrêt d'urgence) | ordres refusés ou retardés |
| Portefeuille (risque engagé, corrélation, krach de −20 % / −35 %) | concentration excessive |

Pour réduire le risque, la décision reste humaine : le réduire, activer le
profil prudent ou arrêter. Un bot qui change seul ses paramètres après une
mauvaise série fait, d'après le tableau ci-dessus, en moyenne moins bien.

Depuis le 29 septembre 2026, le bot peut régler lui-même sa cassure, ses stops
et sa lecture du marché, à condition de réussir des épreuves strictes (deux
époques, frais doublés, crises passées, plateau, hasard) puis 30 jours d'essai :
voir [`EVOLUTION.md`](EVOLUTION.md). Depuis le 30 septembre, il peut aussi
porter son risque par achat de 1 % à 2 %, par paliers et sous garde-fous
(section 6).

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

## 6. Palier de risque de 1 % à 2 %, et arrêt d'urgence (30 septembre 2026)

Demande du propriétaire : corriger « l'absence d'achats et de ventes », et
permettre au bot de passer de 1 % à 2 % de risque par achat selon sa propre
analyse, et inversement, avec prudence et sagesse. Étude reproductible :
`python -m research.palier --cache data_binance` (données Binance au 27
septembre 2026, profil prudent et arrêt d'urgence à −40 % du bot).

### Pas de panne : un budget plein et des stops pas encore touchés

Le 30 septembre, le bot détient 6 positions achetées les 26 et 27 septembre,
chacune risquant environ 1 % : 5,96 % engagés sur les 6 % permis. Un septième
achat dépasserait le plafond, d'où l'absence d'achat. Aucune vente non plus :
aucun cours n'a clôturé sous son stop (placés 7 à 11 % sous les prix d'achat).
Le raisonnement du jour le dit désormais en clair : « Budget de risque plein :
6,0 % engagés sur 6 % permis… » et « Vente la plus proche : LTC, stop à… ».

Libérer le budget quand les stops montent (compter le risque restant jusqu'au
stop actuel au lieu du risque de départ) fait acheter plus, mais moins bien :

| Budget compté avec | 2018-22 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar | trades |
| --- | --- | --- | --- | --- | --- | --- | --- |
| le risque de départ (bot) | +31,8 % | −20,6 % | 1,54 | +32,1 % | −24,2 % | 1,33 | 142 + 172 |
| le risque restant jusqu'au stop | +28,3 % | −21,9 % | 1,29 | +25,2 % | −24,0 % | 1,05 | 161 + 185 |

Écarté : en crypto, les positions baissent ensemble ; en tenir plus, c'est
rendre plus de gains au retournement.

### Plus de risque : plus de gains, mais le hasard frôle l'arrêt d'urgence

Paliers fixes, risque cumulé multiplié d'autant (même nombre de positions).
Dernière colonne : pire baisse atteinte 1 fois sur 20 en trois ans, sur 1 000
tirages de l'historique par blocs de 30 jours.

| Risque par achat | 2018-22 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar | hasard 1 fois sur 20 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 % | +31,8 % | −20,6 % | 1,54 | +32,1 % | −24,2 % | 1,33 | −34 % |
| 1,25 % | +39,0 % | −24,0 % | 1,63 | +35,6 % | −25,8 % | 1,38 | −39 % |
| 1,5 % | +47,4 % | −27,2 % | 1,74 | +38,2 % | −29,0 % | 1,32 | −43 % |
| 1,75 % | +56,1 % | −28,7 % | 1,96 | +35,5 % | −32,3 % | 1,10 | −42 % |
| 2 % | +55,5 % | −30,3 % | 1,83 | +35,4 % | −33,8 % | 1,05 | −45 % |

- Depuis 2023, le rendement plafonne vers 1,5 % pendant que la baisse continue
  de se creuser : au-delà, plus de risque ne rapporte plus rien.
- Dès 1,25 %, un tirage malchanceux sur 20 frôle l'arrêt d'urgence (−40 %) ;
  dès 1,5 %, il le dépasse. À 1 %, la marge est déjà mince (−34 %).

### Monter « dans les bons moments » : le piège du sur-ajustement, encore

Les règles qui montent le risque quand le marché est porteur (405 essayées,
réglées sur 2018-2022) brillent sur 2018-2022 et déçoivent depuis 2023 :

| Règle | 2018-22 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar |
| --- | --- | --- | --- | --- | --- | --- |
| aucune (bot actuel, 1 %) | +31,8 % | −20,6 % | 1,54 | +32,1 % | −24,2 % | 1,33 |
| BTC haussier ≥ 30 j, +0,25 tous les 15 j, 1 % dès 10 % de baisse | +50,5 % | −26,6 % | 1,90 | +26,8 % | −24,5 % | 1,09 |
| BTC haussier ≥ 60 j, +0,25 tous les 30 j, 1 % dès 10 % de baisse | +55,5 % | −26,6 % | 2,09 | +29,6 % | −23,5 % | 1,26 |
| BTC haussier, +0,25 tous les 7 j, sans condition de baisse | +54,3 % | −29,1 % | 1,87 | +22,0 % | −31,1 % | 0,71 |

### L'analyse du bot, rejouée pas à pas

Chaque fin de mois de 2020 à 2026, le bot ne voit que l'historique connu ce
jour-là, compare le palier du dessus au sien (deux moitiés de l'historique,
Calmar et rendement) et monte d'un cran si c'est mieux ; 30 jours d'essai.

| Variante | 2020-22 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar | au-dessus de 1 % | changements |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bot actuel, 1 % fixe | +45,6 % | −20,6 % | 2,21 | +27,3 % | −24,7 % | 1,10 | – | 0 |
| analyse seule, sans garde-fou | +53,5 % | −27,2 % | 1,97 | +27,5 % | −29,4 % | 0,93 | 64 % du temps | 15 |
| analyse + retour à 1 % dès 10 % de baisse | +46,4 % | −21,8 % | 2,13 | +27,3 % | −24,6 % | 1,11 | 4 % du temps | 25 |
| règles du bot (evolution.py) | +45,6 % | −20,6 % | 2,21 | +27,3 % | −24,7 % | 1,10 | 0 % du temps | 0 |

Sans garde-fou, le bot aurait monté son risque 64 % du temps, pour le même
rendement depuis 2023 et une pire baisse de −29 % au lieu de −25 %. Avec les
garde-fous retenus, il ne serait jamais monté de 2020 à 2026 : le hasard était
toujours trop près de l'arrêt d'urgence.

### Arrêt d'urgence : ne plus rester bloqué des années

Aux réglages du bot, l'arrêt d'urgence ne s'est jamais déclenché depuis 2018.
Avec l'essai à 5 % par achat, 20 % cumulé et 20 positions, il se déclenche et
bloque tout achat jusqu'à la commande `resume` :

| Réglages | Période | Arrêt d'urgence | Capital final | Pire baisse | Arrêts | Reprises | Jours sans achat |
| --- | --- | --- | --- | --- | --- | --- | --- |
| essai : 5 % / 20 % / 20 | 2018 → | bloqué | 87 527 | −40,7 % | 2023-03-22 | – | 1286 |
| essai : 5 % / 20 % / 20 | 2018 → | reprise après 60 j | 415 731 | −40,7 % | 2023-03-22, 2024-10-25 | 2023-05-21, 2024-12-24 | 120 |
| essai : 5 % / 20 % / 20 | 2023 → | bloqué | 18 810 | −40,7 % | 2024-10-25 | – | 703 |
| essai : 5 % / 20 % / 20 | 2023 → | reprise après 60 j | 43 689 | −40,7 % | 2024-10-25 | 2024-12-24 | 60 |

Le vrai bot, rejoué jour par jour depuis le 01/01/2023 avec ces réglages et la
reprise prudente, finit exactement au même capital : 43 689 USDT.

### Décision

1. **Palier de risque** (`TG_RISK_MAX_PCT=0.02` par défaut ; `0.01` le
   désactive). Le bot peut porter son risque par achat de 1 % à 2 %, un cran de
   0,25 % à la fois, risque cumulé multiplié d'autant. Il ne monte que si le
   marché est haussier, le capital à moins de 5 % de son plus haut, aucun autre
   changement à l'essai, et s'il réussit trois épreuves : meilleur sur les deux
   époques (Calmar au moins égal, rendement supérieur, 2018-2022 et depuis
   2023), pire baisse rejouée depuis 2018 d'au plus 30 %, pire baisse du hasard
   d'au plus 35 % (5 points sous l'arrêt d'urgence). Puis 30 jours d'essai sur
   le vrai marché. Il redescend aussitôt à 1 % à 10 % de baisse, en marché
   baissier ou à l'arrêt d'urgence (60 jours de repos), d'un cran quand
   l'analyse ne justifie plus le palier, et après un essai raté.
2. **Premier examen** (30 septembre 2026) : 1,25 % réussit les deux époques
   (Calmar 1,63 contre 1,54 et 1,33 contre 1,29) et la pire baisse (−28 %),
   mais pas le hasard (−39 % pour une limite de −35 %). Le bot garde 1 % et
   refait l'examen chaque nuit.
3. **Arrêt d'urgence levé seul, avec prudence** (`TG_KILL_RESUME_DAYS=60` ;
   `0` le désactive) : après 60 jours, si le marché est redevenu haussier et
   que le dernier auto-diagnostic ne conclut pas à la perte de l'avantage de la
   stratégie. Le plus haut repart du capital actuel (comme `resume`) et le
   risque par achat reste divisé par deux pendant 90 jours. Une seule reprise
   automatique par an : un deuxième arrêt dans l'année attend la commande
   `resume`.
4. **Budget de risque inchangé** : compté avec le risque de départ de chaque
   position.
