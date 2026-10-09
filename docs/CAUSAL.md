# Intelligence causale (étape 26 du prompt maître)

Ce qui cause quoi dans la règle, et ce qui est prouvé. Une corrélation n'est
pas une cause ; une intervention dans le simulateur n'est pas une expérience
réelle. Ce module ne change rien au bot : ses interventions restent dans le
simulateur.

Code : [`trendguard/causal.py`](../trendguard/causal.py). Tests :
[`tests/test_causal.py`](../tests/test_causal.py).

```text
python trendguard_bot.py causal                      # graphe, interventions sur les cours en cache, preuves
python trendguard_bot.py causal --out docs/CAUSES.md
```

Dernier état : [`CAUSES.md`](CAUSES.md).

## Le graphe causal de la règle

| Cause | Effet | Mécanisme |
| --- | --- | --- |
| tendance de BTC | permission d'acheter | BTC sous sa moyenne : aucun achat |
| cassure du plus haut | signal d'achat | un achat seulement sur cassure du plus haut de N jours |
| volatilité | taille de la position | stop à k × volatilité, taille = risque / distance du stop |
| risque par achat | taille de la position | taille proportionnelle au risque accepté |
| taille de la position | baisse maximale | plus gros, plus de baisse |
| stop suiveur | durée et résultat des trades | un stop plus large laisse courir |
| frais et glissement | résultat net | chaque achat et chaque vente paient |
| permission, signal | nombre de trades | sans permission ni signal, pas de trade |

Le graphe est versionné (empreinte de ses liens) et sans cycle.

## Les interventions : do(cause)

Dans le simulateur (la boucle de backtest du bot), mêmes données, **une seule
cause changée**, sur chaque époque séparément (2018-2022, puis 2023 à
aujourd'hui) : filtre de BTC supprimé, cassure de 50 jours au lieu de 30, risque
par achat divisé par deux, stop suiveur plus large, frais doublés. Un **témoin
négatif** (aucun changement) doit donner un effet nul, et la même règle sur les
mêmes données doit donner le même résultat. Le **paradoxe de Simpson** est
cherché année par année : un effet moyen de signe contraire à celui de la
plupart des années se lit année par année, jamais en bloc.

## Les niveaux de preuve (§49)

| Niveau | Sens | Dans TrendGuard |
| --- | --- | --- |
| 3 | hypothèse causale | un lien écrit, pas encore éprouvé |
| 4 | effet identifié par intervention | l'intervention change le résultat dans le même sens sur chaque époque |
| 5-6 | expérience réelle, répliquée | jamais atteints ici : une simulation n'est pas une expérience réelle (le contrat le refuse) |

## Les causes racines

Chaque incident du plan de contrôle (étape 18) remonte au service en panne le
plus en amont : quand Binance tombe, la cause racine est Binance, pas le bot qui
n'a pas décidé. La cause racine n'est pas le premier événement observé.

## La qualité (§73-74)

Validité causale 20 %, preuve 15 %, graphe 10 %, sûreté des interventions 15 %,
contrefactuels 10 %, robustesse (témoin négatif) 10 %, cohérence dans le temps
5 %, reproductibilité 5 %, sécurité 5 %, performance 5 %. Graphe non versionné
ou
avec cycle, preuve absente, résultat non reproductible : P0.

## Ce qui ne s'applique pas

- **Découverte causale automatique, variables instrumentales, différence de
  différences, contrôle synthétique** (§8, §27-31) : le graphe de la règle est
  connu, puisqu'il est écrit ; on l'éprouve par intervention, ce qui vaut mieux
  qu'une découverte statistique.
- **Expériences réelles** (§32-33) : jamais avec de l'argent réel ; le paper
  trading en tient lieu, observé par l'acceptation (étape 13).
- **Énergie, ingénierie, cybersécurité causale** (§38-40) : hors du métier du
  bot ; les incidents ont leur cause racine.
