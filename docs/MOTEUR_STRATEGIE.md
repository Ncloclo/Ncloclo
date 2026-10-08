# Moteur de stratégie (étape 9 du prompt maître)

La règle du bot est désormais aussi écrite comme une **fiche** : des données,
pas du code, qui disent quand acheter, quand vendre, combien, avec quels coûts
et quelles limites. La fiche ne remplace pas la règle : elle la décrit, et le
bot vérifie chaque nuit qu'elle donne exactement les mêmes signaux. Sur les
données Binance de 2017 à 2026 : 69 909 cas (crypto × jour), 2 717 signaux
d'achat, **0 écart**.

```text
fiche déclarative → compilation sûre (liste blanche, aucun code exécuté)
→ validation en 12 étapes → PRÊTE POUR LE BACKTEST ou BLOQUÉE
→ chaque nuit : chaque crypto candidate, « pas de trade » (la condition qui manque),
  tenue ou vendue → comparée à la règle exécutée : un écart est signalé, jamais corrigé en silence
```

Code : [`trendguard/moteur_strategie.py`](../trendguard/moteur_strategie.py).
Tests : [`tests/test_moteur_strategie.py`](../tests/test_moteur_strategie.py).

```text
python trendguard_bot.py regle            # la fiche de la règle en vigueur
python trendguard_bot.py regle valider    # validation sur l'historique Binance, et le Kelly mesuré
python trendguard_bot.py regle etats      # cycle de vie d'une stratégie
```

Rachelle répond à « pourquoi le bot n'achète pas ETH ? » : les candidates du
jour, et pour ETH la condition qui manque, avec ses valeurs (« pas de cassure :
2 689 > 2 776 faux »). Le rapport quotidien dit si la fiche est restée fidèle.

## La fiche, en clair (version 1.0.0)

Achat, toutes les conditions :

- BTC au-dessus de sa moyenne de 150 jours (régime) ;
- cassure du plus haut des 30 clôtures précédentes ;
- momentum de 90 jours positif ;
- au moins 250 jours d'historique ;
- volume moyen de 30 jours d'au moins 5 000 000 dollars ;
- volatilité mesurée.

Quand la place manque, les plus forts momentums d'abord. Vente : clôture sous
le stop de la veille, ou crypto retirée de la cote. Stop initial à 3
volatilités sous la clôture, stop suiveur à 5 volatilités sous la plus haute
clôture (2 en régime baissier), qui ne descend jamais. Taille : 1 % du capital
risqué jusqu'au stop, frais et glissement compris ; 25 % du capital au plus
par position ; 8 positions et 6 % de risque cumulé au plus ; sans levier ni
vente à découvert. Coûts : 0,1 % de frais et 0,1 % de glissement par côté,
soit 0,4 % pour un aller-retour.

Deux fiches au registre : la règle (`trendguard.cassure`) et son profil
prudent (`trendguard.cassure.prudent`, risque divisé par 2 au-delà de −10 %
depuis le plus haut). Le bot prend celle qui correspond à ses réglages. Toutes
deux sont « en essai » (PAPER) : aucune n'est approuvée pour le réel.

## Exigences de l'étape 9 → TrendGuard

| Exigence de l'étape 9 | Dans TrendGuard |
| --- | --- |
| Registre des stratégies (§4) | identifiant, nom, famille, version, statut, description, univers, horizons, règles, taille, coûts, liquidité, contraintes, réglages et plages, dépendances, preuves (études), parent d'une variante |
| Statuts et cycle de vie (§5, §40) | les 18 statuts et les passages permis ; brouillon → réel impossible ; un échec renvoie au développement ; rejetée ou abandonnée : fin |
| Langage déclaratif (§6) | conditions « indicateur, opérateur, valeur » réunies par « toutes » ou « une des » ; indicateurs et opérateurs d'une liste blanche ; réglages nommés (`$breakout_n`) ; aucun `eval`, imbrication et taille bornées ; une valeur inconnue rend la condition fausse |
| Validation (§41) et « prête pour le backtest » (§66) | schéma, langage, dépendances, réglages dans leurs plages, coûts, liquidité, contraintes, verrous du code, déterminisme, fuite vers le futur, équivalence avec la règle exécutée, données verrouillées (empreinte) |
| Verrous et versions (§25, §54) | empreintes du code des indicateurs et des règles dans la fiche : si ce code change, la validation la bloque jusqu'à une nouvelle version ; une fiche testée n'est jamais écrasée |
| Entrée, sortie, stops, taille (§9-14) | décrits par la fiche, exécutés par les fonctions validées du bot (les mêmes qu'au backtest) |
| Kelly (§15) | mesuré, jamais appliqué : sur 2018-2022, Kelly complet ≈ 33 % du capital par trade ; les fractions (0,10 à 1) sont plafonnées à 2 % ; la règle risque 1 %, 33 fois moins. Le Kelly suppose des trades indépendants, ce que des cryptos corrélées ne sont pas |
| Régime, marché, coûts, liquidité, capacité (§16-21) | régime de BTC, volume minimum, coûts dans chaque décision (aller-retour 0,4 %) ; capacité mesurée par le moteur de backtest (étape 10) |
| Portefeuille, contraintes, réglages (§22-24) | positions, risque cumulé, taille ; 15 réglages avec plage, pas, unité, source et statut : ceux de la règle dans la plage validée par la recherche 2018-2022 (gelés), ceux du risque (risque par achat, positions, risque cumulé, taille) dans une plage permise, vos choix signalés (aujourd'hui 20 positions et 10 % cumulés, au lieu de 8 et 6 %) |
| Sur-ajustement, fuite (§27-29) | grille de lecture (réglages, règles, trades, chute hors échantillon, réglages voisins) ; indicateurs et régime recalculés sans l'avenir : identiques (un indicateur qui regarde demain est attrapé, test) |
| Décision par crypto, explication, « pas de trade » (§37-39) | contrat `StrategyDecision.v1` : chaque condition avec ses valeurs, raisons précises (pas de cassure, momentum négatif, historique court, volume faible, BTC sous sa moyenne), coût, taille et stop d'un achat ; jamais une autorisation |
| Interfaces backtest, risque, portefeuille, politique (§42-45) | la fiche alimente le moteur de backtest ; aucune autorité d'exécution : la porte d'exécution décide de chaque achat |
| Agents et comité (§46-47) | le comité d'agents de l'étape 5 donne déjà un avis consultatif sur chaque crypto proposée par la règle |
| API, événements, base, observabilité (§49-52) | commande `regle` ; écart signalé au journal du bot et au rapport ; signaux et décisions déjà au journal financier (`fin_signals`, `fin_decisions`) |
| Sécurité (§54) | injection dans la fiche impossible (liste blanche, aucun code exécuté) ; aucune commande d'ordre |
| Tests (§55) | 14 tests : langage, valeur inconnue, équivalence, validation, verrous, fuite, décisions, cycle de vie, Kelly, sur-ajustement, bot inchangé (même moteur en panne), Rachelle et rapport |

## Ce qui ne s'applique pas

- **Autres familles** (retour à la moyenne, arbitrage, facteurs, macro,
  événements, §7) : le laboratoire ([`STRATEGIES.md`](STRATEGIES.md)) en a
  essayé douze ; aucune ne bat la règle sur les deux époques, et confier le
  capital à la meilleure du moment n'a pas fait mieux. Elles ne sont pas en
  service.
- **Agrégation de signaux et ensembles** (§8, §34) : une seule règle en
  service, qui combine ses conditions par un « et ».
- **Optimisation bayésienne ou génétique** (§26) : les réglages sont gelés
  depuis la recherche 2018-2022 ; ils ne changent que par l'évolution encadrée
  (épreuves, puis 30 jours d'essai).
- **Probabilité et rendement attendus d'un trade** (§39) : la règle n'en
  estime pas ; ces champs restent vides plutôt qu'inventés.
- **FastAPI, PostgreSQL, PyTorch** (§57-58) : un seul PC ; commande, journal
  et tests suffisent.
