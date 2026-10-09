# Perception et observations

Mesuré le 2026-10-09 par `python trendguard_bot.py perception` (étape 29 du
prompt maître, [`PERCEPTION.md`](PERCEPTION.md)). La perception observe ; elle
ne décide pas.

- **READY** ; note 100/100.
- 234 observation(s) de 7 source(s) ; 63 fait(s) confirmé(s) par deux sources, 0
  conflit(s), 0 crypto(s) inconnue(s) ; cours le plus récent : bougie du
  2026-10-08.

## Les sources

| Source | rang | modalité | observations | d'où |
| --- | --- | --- | --- | --- |
| bougies du bot | 1 | cours journaliers | 21 | état du bot : bougies de Binance de la dernière décision |
| cache de l'évolution | 1 | cours journaliers | 126 | data_evolution : bougies de Binance de l'évolution encadrée |
| cache des études | 2 | cours journaliers | 63 | data_binance : bougies de Binance des études |
| note des données | 1 | contrôle des données | 1 | état du bot : contrôle des bougies du jour |
| carnets d'ordres | 2 | carnet d'ordres | 21 | état du bot : écart et profondeur mesurés au fil des jours |
| horloge | 1 | horloge | 1 | état du bot : écart avec l'heure de Binance |
| calendrier économique | 2 | calendrier | 1 | état du bot : annonces américaines des 48 heures |

Les trois séries de cours viennent de Binance : elles se recoupent (même jour,
même cours), elles ne sont pas indépendantes. Rang 1 : fait foi en cas de
désaccord.

Alignement : ALIGNED 66, STALE 147, UNDATED 21 (FUTURE : refusée ; UNDATED :
jamais comparée ; STALE : marquée).

## Le cours de chaque crypto (fait le plus récent)

| Crypto | bougie | clôture | état | confiance | source |
| --- | --- | --- | --- | --- | --- |
| BTC | 2026-10-08 | 81 754,45 | SINGLE | moyenne | bougies du bot |
| ETH | 2026-10-08 | 2 474,8 | SINGLE | moyenne | bougies du bot |
| BNB | 2026-10-08 | 736,08 | SINGLE | moyenne | bougies du bot |
| XRP | 2026-10-08 | 1,3805 | SINGLE | moyenne | bougies du bot |
| ADA | 2026-10-08 | 0,2322 | SINGLE | moyenne | bougies du bot |
| DOGE | 2026-10-08 | 0,08404 | SINGLE | moyenne | bougies du bot |
| TRX | 2026-10-08 | 0,3328 | SINGLE | moyenne | bougies du bot |
| LINK | 2026-10-08 | 12,707 | SINGLE | moyenne | bougies du bot |
| LTC | 2026-10-08 | 63,16 | SINGLE | moyenne | bougies du bot |
| BCH | 2026-10-08 | 271,1 | SINGLE | moyenne | bougies du bot |
| XLM | 2026-10-08 | 0,1921 | SINGLE | moyenne | bougies du bot |
| ETC | 2026-10-08 | 8,05 | SINGLE | moyenne | bougies du bot |
| ZEC | 2026-10-08 | 1 187,02 | SINGLE | moyenne | bougies du bot |
| DASH | 2026-10-08 | 50,58 | SINGLE | moyenne | bougies du bot |
| NEO | 2026-10-08 | 2,355 | SINGLE | moyenne | bougies du bot |
| XTZ | 2026-10-08 | 0,2891 | SINGLE | moyenne | bougies du bot |
| ALGO | 2026-10-08 | 0,1174 | SINGLE | moyenne | bougies du bot |
| DOT | 2026-10-08 | 1,09 | SINGLE | moyenne | bougies du bot |
| UNI | 2026-10-08 | 7,12 | SINGLE | moyenne | bougies du bot |
| AAVE | 2026-10-08 | 166,17 | SINGLE | moyenne | bougies du bot |
| ICP | 2026-10-08 | 2,913 | SINGLE | moyenne | bougies du bot |

## Conflits entre sources

Aucun : les sources qui parlent du même jour sont d'accord à 0,5 % près.

## Manifeste des dépendances (statut et type)

| Étape | module | statut | type | condition | importé |
| --- | --- | --- | --- | --- | --- |
| 03 | `trendguard/donnees.py` | OBLIGATOIRE | DATA / RUNTIME | — | non |
| 06 | `trendguard/contrats.py` | OBLIGATOIRE | CONTRACT / GOVERNANCE | — | oui |
| 20 | `trendguard/apprentissage.py` | OBLIGATOIRE | GOVERNANCE / MODEL | — | non |
| 21 | `trendguard/cyber.py` | OBLIGATOIRE | SECURITY / RUNTIME | — | non |
| 07 | `trendguard/modeles.py` | CONDITIONNELLE | MODEL / RUNTIME | LLM_FEATURE_ENABLED | non |
| 19 | `trendguard/controle.py` | ACTIVATION | DEPLOYMENT / RUNTIME | PRODUCTION_ACTIVATION | non |
| 22 | `panel/interface.py` | OPTIONNELLE | INTEGRATION / CONSUMER | — | non |
| 23 | `trendguard/recherche.py` | OPTIONNELLE | INTEGRATION / PRODUCER | — | non |
| 24 | `trendguard/memoire.py` | OPTIONNELLE | INTEGRATION / CONSUMER | — | non |
| 25 | `trendguard/monde.py` | OPTIONNELLE | INTEGRATION / CONSUMER | — | non |
| 26 | `trendguard/causal.py` | OPTIONNELLE | INTEGRATION / CONSUMER | — | non |
| 27 | `trendguard/objectifs.py` | OPTIONNELLE | INTEGRATION / CONSUMER | — | non |
| 28 | `trendguard/jumeau.py` | OPTIONNELLE | INTEGRATION / CONSUMER | — | non |
| 15 | `trendguard/politique.py` | INTERDITE | RUNTIME | — | non |
| 16 | `trendguard/autorisation.py` | INTERDITE | RUNTIME | — | non |
| 17 | `trendguard/porte.py` | INTERDITE | RUNTIME | — | non |
| 18 | `trendguard/bot_execution.py` | INTERDITE | RUNTIME | — | non |
| 18 | `trendguard/deploiement.py` | INTERDITE | RUNTIME | — | non |

## Qualité

| Famille | poids | état | mesure |
| --- | --- | --- | --- |
| justesse de la perception | 20 % | conforme | note des bougies 100/100 ; 0 conflit(s) entre sources |
| fusion des sources | 15 % | conforme | 63 fait(s) vu(s) par plusieurs sources, la source de plus haut rang l'emporte, jamais une moyenne |
| preuve et provenance | 15 % | conforme | 149 fait(s), chacun avec sa source ; 0 sans source |
| alignement dans le temps | 10 % | conforme | aucune bougie non close utilisée ; horloge : écart 743 ms corrigé, incertitude 201 ms, mesuré il y a 1,0 h |
| incertitude dite | 10 % | conforme | 0 crypto(s) INCONNUE(S), dites et jamais devinées |
| robustesse | 10 % | conforme | sans les bougies du bot : aucune valeur inventée, le reste tient |
| sécurité et dépendances | 10 % | conforme | aucune dépendance interdite, aucun cycle, aucune bibliothèque réseau |
| performance | 5 % | conforme | en 0,18 s |
| observabilité | 5 % | conforme | 234 observation(s), chacune avec son identifiant et sa provenance |
