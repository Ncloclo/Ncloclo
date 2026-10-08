# Moteur de portefeuille (étape 12 du prompt maître)

À chaque décision, après les achats, le bot regarde son portefeuille comme un
tout : ce que pèse chaque crypto, la part investie et la part en liquidités, la
concentration, le risque engagé jusqu'aux stops contre le plafond, la
volatilité de l'ensemble et la part de chaque crypto dans ce risque. Il calcule
aussi ce que d'autres répartitions des mêmes cryptos auraient donné, pour
comparer. Il est consultatif : il ne passe aucun ordre, ne rééquilibre rien et
ne change pas la règle.

```text
positions et cours → poids, exposition, liquidités, concentration (HHI, nombre effectif)
→ risque engagé contre le plafond, volatilité, contributions au risque, diversification
→ contraintes (positions, 25 % par position, risque cumulé ; impossible = expliqué, jamais relâché)
→ mêmes cryptos réparties autrement : parts égales, inverse de la volatilité,
  variance minimale, parité de risque, Sharpe maximal → dérive, rotation, coût
→ décision : dans les contraintes, à regarder, aucune position (jamais un ordre)
```

Code :
[`trendguard/moteur_portefeuille.py`](../trendguard/moteur_portefeuille.py).
Tests :
[`tests/test_moteur_portefeuille.py`](../tests/test_moteur_portefeuille.py).
Étude : [`PORTEFEUILLE.md`](PORTEFEUILLE.md).

```text
python trendguard_bot.py portefeuille                 # l'état du dernier jour
python trendguard_bot.py portefeuille etude           # la taille des achats éprouvée sur deux époques
```

## Ce que fait déjà la règle

La règle donne à chaque achat **le même risque jusqu'à son stop** (1 % du
capital) : une crypto agitée reçoit un petit montant, une crypto calme un plus
gros. C'est une parité de risque, position par position. Elle ne rééquilibre
jamais : une position vit de son achat à son stop, et c'est voulu. Le
laboratoire quantitatif (étape 8) a montré que les 10 % meilleurs trades font
tout le résultat ; vendre une partie des gagnants pour revenir à des poids
cibles couperait précisément ceux-là.

## Ce que dit l'étude

Même règle, mêmes achats et mêmes ventes ; seule la taille des achats change
(frais et glissement compris) :

- **montant égal par achat** : nettement moins bien sur les deux époques
  (Calmar 0,85 et 0,89 contre 1,36 et 1,32) ; le risque égal jusqu'au stop
  compte ;
- **même risque, divisé par deux quand la crypto suit déjà les positions
  détenues** (corrélation de plus de 0,7) : moins bien sur 2018-2022, un peu
  mieux depuis 2023 ; pas net sur les deux époques, donc pas proposé.

Une autre taille ne serait proposée que si elle battait la règle sur les deux
époques d'au moins 10 % de Calmar, et même alors, seule l'évolution encadrée
pourrait l'essayer, avec ses épreuves et ses 30 jours d'essai.

## Exigences de l'étape 12 → TrendGuard

| Exigence de l'étape 12 | Dans TrendGuard |
| --- | --- |
| État du portefeuille, positions (§7-9) | à chaque décision : valeur, poids, risque jusqu'au stop, volatilité, bêta à BTC, part du risque de chaque crypto ; exposition, liquidités, nombre de positions |
| Allocation stratégique, tactique, par stratégie (§10-12) | une seule classe d'actifs (cryptos au comptant contre de l'USDT) et une seule stratégie : l'allocation est la taille de chaque achat, fixée par le risque |
| Optimiseurs (§13-20) | parts égales, inverse de la volatilité, variance minimale, parité de risque (corrélations comprises), Sharpe maximal (rendements attendus rétrécis : très incertains) ; long seulement, sous le plafond par crypto |
| Contraintes, conflits, solveur, validation (§22-26) | positions, 25 % par position, risque cumulé, ni levier ni liquidités négatives ; une contrainte impossible est expliquée (« 2 positions × 25 % < 70 % investis »), jamais relâchée ; projection exacte, somme et bornes vérifiées |
| Covariance, corrélation, diversification, contributions (§27-30) | covariance de 90 jours ; corrélation moyenne des positions ; HHI et nombre effectif ; ratio de diversification ; part de chaque crypto dans le risque (la somme fait 1) |
| Budget de risque, risque ajouté (§31-32) | risque engagé contre le plafond de la règle ; risque ajouté par les achats du jour : moteur de risque (étape 11) |
| Liquidité, capacité, coûts, rotation (§33-36) | rotation et coût pour passer à une autre répartition ; liquidité et capacité : moteurs de risque et de backtest |
| Rééquilibrage, dérive (§37-40) | dérive mesurée (une position qui a monté peut dépasser 25 % : « à regarder ») ; rééquilibrage jamais proposé : il couperait les gagnants |
| Simulation, stress, robustesse (§48-55) | moteur de risque (stress, stress inversé) et laboratoire quantitatif ; l'étude éprouve la taille des achats sur deux époques |
| Décision, contrat, manifeste (§61, §67, §69) | `PortfolioDecision.v1` : dans les contraintes, à regarder (avec la raison) ou aucune position ; jamais une autorisation |
| Dans le bot, sécurité (§72-74) | après les achats, dans l'état du bot, le raisonnement, le rapport et Rachelle ; une panne ne bloque rien ; aucun trade changé (test) |

## Ce qui ne s'applique pas

- **Couverture** (§41-42) : Binance Spot, sans vente à découvert ni produits
  dérivés ; la seule couverture est l'USDT, déjà là quand la règle n'achète
  pas.
- **Plusieurs devises, plusieurs comptes, plusieurs portefeuilles** (§43-45) :
  un compte, une devise (USDT).
- **Black-Litterman, moyenne-CVaR, optimisation robuste** (§18-20) : pas de
  vues à mélanger ; la règle ne choisit pas des poids, elle dimensionne des
  achats ; la parité de risque et la variance minimale servent de référence.
- **Comité de portefeuille avec IA, débat** (§62-64) : le comité d'agents de
  l'étape 5, sans IA (aucune clé).
- **Impôts, financement** : hors du bot.
