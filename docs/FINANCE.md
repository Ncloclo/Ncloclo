# Cœur d'intelligence financière (étape 7 du prompt maître)

Le « docteur en finance » du bot : chaque nuit, à la décision, il analyse
chaque crypto et dit « signal de la règle », « à surveiller » ou « pas de
trade », avec ses raisons, ses preuves, ses risques, ses contradictions et ce
qui invaliderait son analyse. Il aide à comprendre ; il ne décide pas : la
règle du bot, puis la porte d'exécution, décident comme avant (un test montre
qu'il ne change aucun trade).

```text
instruments → bougies → qualité → indicateurs versionnés (sans regard vers le futur)
→ technique, quantitatif, sentiment, régime, liens entre cryptos, calendrier
→ scénarios et prévisions de fréquence → signal de la règle → classement indicatif
→ « pas de trade », à surveiller ou signal → explication → journal financier
→ à 30 jours : chaque prévision comparée au résultat (calibration)
```

Code : [`trendguard/finance.py`](../trendguard/finance.py). Tests :
[`tests/test_finance.py`](../tests/test_finance.py). Pour une crypto, à tout
moment : `python trendguard_bot.py finance aave` (ou `--json`). Rachelle
répond à « analyse de AAVE ».

## Les moteurs, appliqués aux cryptos

| Exigence de l'étape 7 | Dans TrendGuard |
| --- | --- |
| Référentiel des instruments (§8) | chaque crypto : identifiant stable `binance:spot:AAVE-USDT`, classe, devise, calendrier 24/7, première bougie, état (en cotation, veto de Binance) ; une règle de cotation inconnue reste vide, jamais inventée |
| Données de marché (§6-7) | bougies journalières clôturées de Binance ; carnets d'ordres relevés pour l'écart achat/vente (apprentissage) |
| Qualité des données (§9-10) | note de 0 à 1 par crypto : complétude, fraîcheur, cohérence (prix nuls, bougies incohérentes), fiabilité (cours figés) ; chaque indicateur porte la fin de ses données |
| Technique, quantitatif (§14-15) | 16 indicateurs versionnés : rendements, momentum, écart au plus haut de 30 jours, volatilité, moyennes 50/200, RSI, baisse depuis le plus haut, volume, bêta et corrélation avec BTC, Sharpe, Sortino, asymétrie, aplatissement, VaR et CVaR d'un jour |
| Anti-fuite (§22) | un indicateur, la règle et le régime sont recalculés sur les seules données connues chaque jour testé : identiques (test) ; un calcul qui regarde le futur est détecté ; la base refuse un indicateur dont les données dépassent sa bougie |
| Sentiment, événements (§17-18) | sources prouvées du noyau de savoir ; calendrier des grandes annonces américaines (prudence affichée, aucun achat bloqué : effet non prouvé) |
| Régime (§19) | quatre lectures (tendance, volatilité, appétit, phase) dans le vocabulaire commun : BULL, BEAR, SIDEWAYS, HIGH_VOLATILITY, RISK_ON, CRISIS… |
| Liens entre cryptos (§20) | corrélation moyenne sur 90 jours et son rang dans l'année (contagion), corrélation de chacune avec BTC |
| Scénarios, prévisions (§28-29) | fréquences observées dans un passé comparable (même régime de BTC, fenêtres de 30 jours sans chevauchement) : probabilité de hausse et son intervalle, rendement moyen et intervalle de 80 %, cinq scénarios dont la somme fait 1 ; moins de 12 cas : aucune prévision |
| Signal (§23) | le signal de la règle au format commun : force, risque attendu (distance au stop), conditions d'entrée et de sortie, version de la règle ; rendement attendu vide (jamais estimé) ; candidat seulement |
| Classement (§25) | note indicative de 0 à 100, pondérations versionnées (technique, quantitatif, régime, risque, sentiment, liquidité, qualité) ; une composante non mesurée est dite et retirée |
| « Pas de trade » (§24) | raisons au format commun : données insuffisantes ou en retard, règle ou politique (BTC baissier, veto, garde, arrêt d'urgence), risque (volatilité extrême, liquidité), désaccord des agents, crise ; aucun signal : « à surveiller » |
| Confiance séparée (§30) | données, modèle (calibration mesurée), signal (comité), prévision (largeur de l'intervalle), décision (la plus faible) ; jamais une certitude |
| Mémoire, post-analyse, calibration (§33-35) | indicateurs, analyses et prévisions gardés dans le journal financier ; à 30 jours, chaque prévision est comparée au résultat : score de Brier, direction juste, biais, calibration par tranche ; jugée dégradée si elle ne bat pas le hasard |
| Note d'intelligence financière, dérive (§49-50) | moyenne des seules composantes mesurées (qualité, calibration, analyses complètes) ; dérive : volatilité du jour loin de son année |
| Explicabilité (§37) | décision, preuves, facteurs de risque, contradictions, confiance, qualité, conditions d'invalidation, en clair |
| Sécurité financière (§38, §63) | aucune analyse n'est une autorisation (refusée par contrat) ; aucun chemin vers un ordre |

## Pas appliqué ici, et pourquoi

- **Analyse fondamentale, valorisation (DCF, P/E, EV/EBITDA…)** : sans objet
  pour des cryptos, qui n'ont ni bilan ni bénéfice. L'analyse le dit à chaque
  fois, sans rien simuler.
- **Actions, obligations, devises, matières premières, dérivés** : le bot ne
  trade que des cryptos sur Binance Spot ; aucune de ces données n'est
  collectée.
- **Séries macroéconomiques (PIB, inflation, taux) et régimes économiques** :
  non collectées ; le calendrier des grandes annonces en tient lieu.
- **ARIMA, GARCH, apprentissage profond** : aucun gain prouvé ; une
  prévision de fréquence, honnête et mesurée, d'abord.
- **PostgreSQL, Redis, bus d'événements, API REST, Pydantic** : un seul PC ;
  le journal financier SQLite (migration 4), des classes typées et des
  commandes suffisent.
