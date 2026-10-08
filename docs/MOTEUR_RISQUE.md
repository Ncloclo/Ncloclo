# Moteur de risque (étape 11 du prompt maître)

À chaque décision, **avant** les achats puis **après**, le bot évalue le risque
de son portefeuille : combien il risque, pourquoi, sur quelle crypto, avec
quelle incertitude, ce qui se passerait en cas de crise, quel choc
déclencherait l'arrêt d'urgence. L'évaluation est gardée au journal
financier, résumée dans le raisonnement du jour et le rapport quotidien, et
Rachelle la lit.

```text
positions, cours et volumes validés → volatilité (historique, EWMA, régime)
→ VaR et perte moyenne au-delà : historique, normale, Student, Monte-Carlo ; 1, 5 et 10 jours
→ queue (asymétrie, aplatissement, indice de Hill, valeurs extrêmes)
→ corrélations (Pearson, Spearman, Kendall, en crise, chutes simultanées)
→ concentration (HHI, paris indépendants, contrepartie) → liquidité (jours pour vendre, coût)
→ baisse depuis le plus haut → budget de risque → contributions de chaque crypto
→ stress et stress inversé → limites → note et composantes → état et décision
```

Code : [`trendguard/moteur_risque.py`](../trendguard/moteur_risque.py).
Tests : [`tests/test_moteur_risque.py`](../tests/test_moteur_risque.py).

```text
python trendguard_bot.py risque              # la dernière évaluation, et ce qu'elle pourrait oublier
python trendguard_bot.py risque calibrage    # la VaR du moteur éprouvée sur le backtest et sur le journal
```

## Ce qu'il applique, et ce qu'il ne fait que dire

Une seule chose est appliquée, par la porte d'exécution (17e contrôle,
« Moteur de risque ») : **sans évaluation valide du jour, aucun achat**.
Moteur en panne, données critiques invalides (capital inconnu, crypto
détenue sans cours), évaluation périmée (elle vaut 24 heures) : c'est le
mode sûr. Les ventes ne passent jamais par là : réduire le risque reste
toujours possible.

L'état « bloqué » ne survient que lorsque la règle n'achèterait de toute façon
pas : arrêt d'urgence déclenché, baisse de 40 % depuis le plus haut, budget de
risque dépassé. Un test le vérifie sur l'historique, et un autre montre que le
moteur ne change aucun trade du bot.

Les autres états (vigilance, risque élevé, critique) et les décisions
« achats permis », « dans les limites », « limités au budget restant »
**informent** : les limites de la règle restent celles que la porte applique
déjà (1 % par achat, plafond de risque cumulé, nombre de positions, 25 % par
position, arrêt d'urgence à −40 %). Une règle validée ne change pas sans
preuve.

## La VaR du moteur, éprouvée

Sur le backtest de la règle (réglages en vigueur, positions de chaque jour de
2019 à 2026), la VaR historique d'un an, seule, est dépassée trop souvent :
92 jours au lieu de 65 attendus à 95 %, 22 au lieu de 13 à 99 %. Les cryptos
achetées en pleine cassure deviennent souvent plus agitées que leur année
passée, et la queue des pertes est épaisse. Sept méthodes ont été comparées ;
la plus prudente des trois méthodes historique, normale et Student est bien
calibrée (71 et 16 dépassements, tests de Kupiec et de Christoffersen) :
c'est elle que le moteur retient. `python trendguard_bot.py risque calibrage`
refait l'épreuve sur toute la période (75 dépassements pour 70 attendus à
95 %, 16 pour 14 à 99 %, sans grappes), puis sur le propre journal du bot :
chaque VaR annoncée est comparée au résultat du lendemain.

## Exigences de l'étape 11 → TrendGuard

| Exigence de l'étape 11 | Dans TrendGuard |
| --- | --- |
| Données du risque (§6, §36) | cours et volumes validés de la décision ; prix d'une crypto détenue inconnu ou capital nul : bloqué ; qualité des données du jour dans la confiance |
| Volatilité (§9) | journalière, hebdomadaire, mensuelle, annualisée, EWMA (λ = 0,94), prévision ; expansion, compression, cassure, changement de régime |
| VaR et ES (§10-11) | historique, normale (variance-covariance), Student (degrés de liberté par les moments), Monte-Carlo à graine (10 000 tirages) ; 95 et 99 % ; 1, 5 et 10 jours ; ES ≥ VaR vérifié |
| Queue (§12) | asymétrie, aplatissement, indice de Hill, valeurs extrêmes (loi de Pareto généralisée au-delà du 95e centile), sauts de plus de 4 écarts-types |
| Stress et stress inversé (§13-14, §69) | krachs de 20, 35, 50 % stops sautés, crise de liquidité, pic de volatilité, retrait de la cote, décrochage de l'USDT, volatilité × 2 et × 3, corrélations → 1, liquidité ÷ 2, écart et glissement × 5, pire jour rejoué ; baisse uniforme qui coûterait 10, 20, 30, 50 % du capital ou déclencherait l'arrêt d'urgence, stops sautés ou tenus |
| Portefeuille, positions, concentration, corrélations (§15-18) | exposition, poids, HHI, nombre effectif de positions, ratio de diversification, paris indépendants ; corrélations moyennes (trois mesures), récentes, en crise, dépendance de queue ; contrepartie unique (Binance) signalée |
| Liquidité et liquidation (§19-20) | part du volume moyen de 30 jours, jours pour tout vendre à 25, 10, 5, 2 et 1 % du volume, coût de sortie (frais, glissement, impact), VaR ajustée de ce coût |
| Levier, marge (§21-22) | Binance Spot : levier brut = exposition / capital, toujours ≤ 1 ; marge sans objet |
| Baisse, budget de risque (§23-24) | baisse depuis le plus haut, points avant l'arrêt d'urgence ; budget : risque initial cumulé contre le plafond (paliers 70, 85, 100 %), perte si tous les stops étaient touchés |
| Contributions, risque ajouté (§25-26, §50) | contribution marginale et par composante (la somme fait le risque), seule, aux pires jours ; risque ajouté par les achats du jour (avant / après) |
| Régime, événements, incertitude (§37, §49, §79) | VaR des seuls jours du même régime de BTC ; grande annonce dans les 48 h (prudence, effet non prouvé) ; confiance des données et du modèle séparées, jamais une probabilité de gain |
| Limites, note, état, décision (§39, §47-48, §55, §83) | quatre limites et leur usage ; note de 0 à 1 aux pondérations versionnées, toujours avec ses dix composantes ; état normal, vigilance, élevé, critique, bloqué ; machine à états (recalcul obligatoire avant tout retour) ; validité 24 heures |
| Avant et après le trade, calibrage (§40-41, §86, §93) | évaluation avant les achats (la porte s'y réfère) et après ; VaR annoncée contre résultat du lendemain : dépassements, Kupiec, Christoffersen |
| Alertes, atténuation, arrêt d'urgence, mode sûr (§43-46) | alertes de INFO à EMERGENCY au rapport ; propositions seulement (aucune vente forcée par le moteur) ; arrêt d'urgence et mode sûr existants ; moteur en panne = mode sûr |
| Équipe rouge, explication (§53, §76) | ce que l'évaluation pourrait oublier (corrélation cachée, modèle non éprouvé, queue épaisse, contrepartie, événement jamais vu) ; une phrase claire par décision |
| Base, contrats, traçabilité (§56-60) | table `fin_risk_assessments`, en ajout seulement (migration 5) ; contrat `RiskAssessment.v1` (un blocage dit pourquoi, jamais une autorisation) ; version du modèle et empreinte des données |
| Tests (§66-70) | 15 tests : formules de VaR et d'ES, queue, corrélations, calibrage, VaR du moteur sur le backtest, évaluation complète, blocages, porte, machine à états, journal, bot inchangé, panne = aucun achat mais ventes maintenues, Rachelle et rapport, rejeu de l'historique |

## Ce qui ne s'applique pas

- **Grecques, taux, crédit, change, pays, secteurs** (§8, §29-31) : des
  cryptos au comptant contre de l'USDT ; le risque de change est le
  décrochage de l'USDT, éprouvé dans les tests de résistance.
- **GARCH** (§9) : la volatilité EWMA en tient lieu ; un GARCH demanderait
  une bibliothèque d'optimisation de plus, sans gain prouvé ici.
- **Comité d'agents du risque, débat** (§51-52) : le comité d'agents de
  l'étape 5 et l'équipe rouge du moteur en tiennent lieu, sans IA : aucune
  clé d'IA aujourd'hui.
- **Dérogations manuelles aux limites** (§65) : il n'y en a pas ; une limite
  change dans le code, par une modification tracée et testée.
- **API, Kafka, Redis, PostgreSQL** (§61, §71) : un seul PC ; commande,
  journal SQLite, rapport et panneau suffisent.
