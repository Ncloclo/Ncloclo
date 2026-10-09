# Intelligence causale

Mesuré le 2026-10-09 par `python trendguard_bot.py causal` (étape 26 du prompt
maître, [`CAUSAL.md`](CAUSAL.md)). Corrélation n'est pas causalité ; une
intervention dans le simulateur n'est pas une expérience réelle.

- **PRÊT** (READY) ; note 100/100.
- 9 liens causaux, 7 identifiés par intervention ; 1 incident(s) (cause racine :
  alertes).

## Le graphe causal de la règle et ses preuves

| Cause | effet | mécanisme | niveau de preuve | pourquoi |
| --- | --- | --- | --- | --- |
| tendance de BTC | permission d'acheter | BTC sous sa moyenne : aucun achat | 4 (effet identifié par intervention (simulateur)) | do(marché toujours haussier) : filtre de BTC supprimé : le nombre de trades change dans le même sens sur chaque époque (+30,00, +18,00) |
| cassure du plus haut | signal d'achat | un achat seulement sur cassure du plus haut de N jours | 4 (effet identifié par intervention (simulateur)) | do(cassure du plus haut de 50 jours au lieu de 30) : le nombre de trades change dans le même sens sur chaque époque (−6,00, −29,00) |
| volatilité | taille de la position | stop à k × volatilité, taille = risque / distance du stop | 3 (hypothèse causale) | lien écrit par construction de la règle, pas encore éprouvé |
| risque par achat | taille de la position | taille proportionnelle au risque accepté | 4 (effet identifié par intervention (simulateur)) | do(risque par achat divisé par deux) : la baisse maximale change dans le même sens sur chaque époque (+8,18, +4,85) |
| taille de la position | baisse maximale | plus gros, plus de baisse | 4 (effet identifié par intervention (simulateur)) | do(risque par achat divisé par deux) : la baisse maximale change dans le même sens sur chaque époque (+8,18, +4,85) |
| stop suiveur | durée et résultat des trades | un stop plus large laisse courir et rend plus | 3 (hypothèse causale) | do(stop suiveur plus large de 2 × volatilité) : effet de signe instable selon l'époque (+1,28, −0,04) |
| frais et glissement | résultat net | chaque achat et chaque vente paient | 4 (effet identifié par intervention (simulateur)) | do(frais et glissement doublés) : le rendement annuel change dans le même sens sur chaque époque (−3,96, −4,28) |
| permission d'acheter | nombre de trades | sans permission, pas d'achat | 4 (effet identifié par intervention (simulateur)) | do(marché toujours haussier) : filtre de BTC supprimé : le nombre de trades change dans le même sens sur chaque époque (+30,00, +18,00) |
| signal d'achat | nombre de trades | chaque signal accepté devient un trade | 4 (effet identifié par intervention (simulateur)) | do(cassure du plus haut de 50 jours au lieu de 30) : le nombre de trades change dans le même sens sur chaque époque (−6,00, −29,00) |

## Interventions dans le simulateur

Écart avec la règle telle quelle, époque par époque (rendement annuel et baisse
maximale en points, trades, résultat moyen en R).

| Intervention | époque | rendement | baisse | trades | résultat moyen | Simpson |
| --- | --- | --- | --- | --- | --- | --- |
| do(marché toujours haussier) : filtre de BTC supprimé | 2018-2022 | +0,81 | −8,40 | +30 | −0,183 | non |
| do(marché toujours haussier) : filtre de BTC supprimé | 2023-fin | +6,69 | +0,35 | +18 | +0,062 | non |
| do(cassure du plus haut de 50 jours au lieu de 30) | 2018-2022 | −11,85 | +2,27 | −6 | −0,291 | non |
| do(cassure du plus haut de 50 jours au lieu de 30) | 2023-fin | −2,19 | +2,42 | −29 | +0,051 | non |
| do(risque par achat divisé par deux) | 2018-2022 | −24,18 | +8,18 | +35 | −0,276 | non |
| do(risque par achat divisé par deux) | 2023-fin | −10,31 | +4,85 | +55 | +0,184 | non |
| do(stop suiveur plus large de 2 × volatilité) | 2018-2022 | +12,15 | +4,93 | −41 | +1,283 | non |
| do(stop suiveur plus large de 2 × volatilité) | 2023-fin | −6,88 | −4,04 | −24 | −0,040 | non |
| do(frais et glissement doublés) | 2018-2022 | −3,96 | −0,51 | +1 | −0,109 | non |
| do(frais et glissement doublés) | 2023-fin | −4,28 | −0,84 | +4 | −0,089 | non |
| témoin négatif : do(rien) — même règle, mêmes données | 2018-2022 | +0,00 | +0,00 | +0 | +0,000 | non |
| témoin négatif : do(rien) — même règle, mêmes données | 2023-fin | +0,00 | +0,00 | +0 | +0,000 | non |

## Causes racines des incidents

- Alertes (e-mail, messages) : dégradé : cause alertes

## Qualité

| Famille | poids | état | mesure |
| --- | --- | --- | --- |
| validité causale | 20 % | conforme | aucun lien au-delà du niveau 4 sans expérience réelle |
| preuve et identification | 15 % | conforme | chaque lien dit sa preuve et sa méthode |
| intégrité du graphe | 10 % | conforme | graphe sans cycle, version 937e5a65bdbb |
| sûreté des interventions | 15 % | conforme | interventions dans le simulateur seulement : jamais sur le bot ni chez Binance |
| contrefactuels | 10 % | conforme | interventions mesurées |
| robustesse | 10 % | conforme | témoin négatif : effet nul |
| cohérence dans le temps | 5 % | conforme | chaque époque éprouvée séparément ; paradoxe de Simpson cherché année par année : aucune inversion |
| reproductibilité | 5 % | conforme | même règle, mêmes données : même résultat |
| sécurité | 5 % | conforme | aucune connexion : le simulateur seulement, jamais le bot ni Binance |
| performance | 5 % | conforme | en 8,9 s |
