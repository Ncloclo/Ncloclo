# Recherche et connaissances (étape 22 du prompt maître)

Ce que le bot lit sur Internet et ce qu'il en croit. Le noyau de savoir
(`savoir.py`, [`SAVOIR.md`](SAVOIR.md)) lit déjà la presse, les moteurs
d'actualités, les forums, StockTwits, les tendances et l'indice Fear & Greed
toutes les 15 minutes, et juge chaque source sur les vrais cours ; la veille
(`market_watch.py`) lit les annonces officielles de Binance. L'étape 22 ajoute
le jugement de ce savoir : rang, confiance et validité de chaque source,
verdict de chaque affirmation sur laquelle le bot agit, contradictions du jour.

Code : [`trendguard/recherche.py`](../trendguard/recherche.py). Tests :
[`tests/test_recherche.py`](../tests/test_recherche.py),
[`tests/test_savoir.py`](../tests/test_savoir.py),
[`tests/test_market_watch.py`](../tests/test_market_watch.py).

```text
python trendguard_bot.py recherche                        # sources, affirmations, contradictions
python trendguard_bot.py recherche --out docs/CONNAISSANCES.md
```

Dernier état : [`CONNAISSANCES.md`](CONNAISSANCES.md).

## Les sources

| Rang | Sources | Ce qu'elles peuvent faire |
| --- | --- | --- |
| 1 — officielle | annonces de Binance, bougies de Binance | empêcher un achat (retrait de la cote) ; décider (la règle) |
| 2 — professionnelle | calendrier économique, Fear & Greed, tendances CoinGecko | informer ; avis du savoir |
| 3 — presse | presse spécialisée, Google Actualités, Bing Actualités | avis du savoir |
| 4 — communauté | Reddit, Hacker News, StockTwits, IA de la veille | avis du savoir ; les IA conseillent seulement |

**Confiance**, de 0 à 100 : 40 % le rang, 40 % la fiabilité mesurée sur les
vrais
cours (fiable 100, en observation 50, hasard 30, trompeuse 10), 20 % la
fraîcheur.
**Validité** : de 6 heures (tendances, StockTwits) à 30 jours (annonce de
retrait) ; une source au-delà de sa validité est signalée. **Dépendances** :
Google et Bing Actualités reprennent la presse ; quand ils disent la même chose
qu'elle, ce n'est pas une confirmation de plus.

## Les affirmations et leur verdict

| Affirmation | Source | Verdict |
| --- | --- | --- |
| « Binance retire X de la cote » | annonce officielle de Binance | confirmée tant qu'elle court, périmée ensuite |
| « X nettement en baisse cette semaine » (report d'achat) | sources prouvées du savoir (20 semaines au moins) | probable ; incertaine sans source prouvée |

Les contradictions du jour sont relevées : une même crypto vue en hausse par
une source et en baisse par une autre (sources indépendantes seulement). Elles
sont « contestées » : aucune décision n'en dépend.

## Les règles

- Une information extérieure n'est jamais tenue pour vraie d'office.
- Seule une annonce officielle de Binance peut empêcher un achat (vérifié à
  chaque examen) ; aucune IA ne bloque un achat.
- Le savoir ne peut que reporter un achat, sur des sources prouvées ; jamais
  acheter, vendre, ni changer une règle.
- Les textes lus sont des données, jamais des instructions.

## L'examen : AC-001 à AC-080, note, verdict (§71-72)

Mesurés : rangs, fraîcheur, retraits sur source officielle, reports sur
sources prouvées ; prouvés par les tests du dépôt (lecture de chaque famille,
une source en panne n'arrête pas les autres, sources jugées sur les vrais
cours, avis du bot sur sources prouvées seulement, deux IA nécessaires et jamais
de veto par une IA, injection d'instructions refusée…). Note pondérée : données
15 %, vérification des sources 15 %, connaissances 15 %, sécurité 15 %,
recherche documentaire 10 %, provenance 10 %, reproductibilité 10 %,
observabilité 5 %, performance 5 %.

## Ce qui ne s'applique pas

- **Recherche ouverte, sous-questions, recherche scientifique, énergie**
  (§5-9, §19, §54) : des sources fixes, lues à intervalles réguliers, pour le
  seul métier du bot.
- **Documents, OCR, tableaux, logiciels malveillants** (§15-18, §48) : le bot
  lit
  des titres et des flux publics ; aucun fichier n'est ouvert ni exécuté.
- **Recherche vectorielle, reclassement** (§29-31) : mots-clés seulement.
- **Graphe de connaissances** (§25-28) : étape 24.
