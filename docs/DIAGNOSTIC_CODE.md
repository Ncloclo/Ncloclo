# Diagnostic du code TrendGuard — 30 septembre 2026

Analyse profonde du code, puis harmonisation (textes, nombres, fonctions) et
structuration. Mesures reproductibles : `python -m research.diagnostic_code`.

**Ce qui n'a pas bougé** : aucune règle de trading, aucun réglage de risque,
aucune clé. Le moteur d'exécution `v29/`, qui porte les ordres, est resté tel
quel. Le bot décide, achète et vend exactement comme avant.

## Méthode

Tout le code Python est lu avec `ast` (paquets `trendguard`, `v29`, `panel`,
`research`) :

- taille des modules et des fonctions ;
- complexité d'une fonction : son nombre de branches (`if`, boucles, `try`,
  conditions composées) ;
- code répété : 6 lignes identiques d'affilée à deux endroits ;
- noms définis mais jamais utilisés, tests compris ;
- sens des imports entre paquets ;
- textes affichés : plusieurs mots pour une même chose, typographie, format
  des nombres.

Les mêmes mesures ont été refaites après les changements.

## Mesures

| Mesure | Avant | Après |
| --- | --- | --- |
| Modules Python | 58 | 64 |
| Lignes (hors tests) | 22 668 | 23 132 |
| Plus gros fichier du bot | `bot.py`, 1 590 lignes | `trend_strategy.py`, 996 lignes |
| Fonction la plus complexe hors `v29` | 42 branches | 32 branches |
| Fonctions publiques de 25 lignes ou plus sans docstring | 49 | 0 |
| Zones de code répété hors `v29` | 5 | 0 |
| Noms jamais utilisés hors `v29` | 3 | 0 |
| Imports à contresens (`trendguard` → `research`) | 1 | 0 |
| Formats de nombres écrits à la main | 18 | 1 |
| Script du panneau `app.js` | 1 272 lignes | 1 156 lignes |
| Tests | 471 | 478 |

Le nombre de lignes augmente : docstrings ajoutées, en-têtes des nouveaux
fichiers et outil de diagnostic (190 lignes).

## Points forts constatés

- Les dépendances vont dans un seul sens : `research` et `panel` s'appuient
  sur `trendguard`, qui s'appuie sur `v29`.
- Une seule boucle de backtest sert au bot, aux études et au laboratoire.
- Les accès extérieurs (Binance, git, Windows) sont remplaçables dans les
  tests.
- Aucun secret dans le code, les journaux ou les rapports.

## Harmonisation

### Textes

- « kill-switch » devient partout « arrêt d'urgence » : journal, résumé
  quotidien, ligne de commande, README.
- La ligne de vie du journal (`[HEARTBEAT]`), le résumé de la décision et les
  commandes `status`, `health` et `replay` parlent français.
- L'en-tête du point d'entrée (`trendguard_bot.py`) cite les outils
  `evolution` et `rapport`.

### Nombres

Une seule fonction écrit les nombres à la française, `texte.fr` : virgule
décimale, espace des milliers, vrai signe moins.

- Elle remplace dix-sept formatages écrits à la main (anticipation,
  raisonnement du jour, apprentissage, Rachelle, panneau, rapport, études).
- Les phrases affichées passent par elle : diagnostic, veille, vérification
  avant le mode réel, rejeu, raisons de différer un achat. On lit
  « +2,1 % », « 84 000 » et « −0,5 », plus « +2.1 % » ni « 84,000 » ; un pas
  de cotation s'écrit « 0,00001 », plus « 1e-05 ».
- Trois endroits gardent le point décimal, exprès : les journaux techniques
  de la forme `clé=valeur`, la consigne envoyée aux IA de la veille (elles
  répondent en JSON) et les nombres envoyés à Binance.

### Fonctions

| Avant | Maintenant |
| --- | --- |
| trois études recopiaient le chargement des données et l'écriture du rapport | `research/commun.py` |
| six journaux de simulation ou de vérification réglés à la main | `journal.silent_logger` |
| le rejeu et l'animation construisaient chacun leur bot simulé | `replay.simulated_bot` |
| le laboratoire empruntait ses données aux études | `evolution.load_history`, comme elles |
| trois fonctions jamais appelées | retirées |
| 49 fonctions publiques longues sans explication | 47 ont une docstring, 2 ont été raccourcies |

## Structuration

- **Le bot** : `bot.py` (1 590 lignes) est réparti en quatre fichiers. Le
  cœur reste dans `bot.py` (démarrage, cycle, décision, boucle) ; les routines
  du cycle vont dans `bot_routines.py`, l'exécution des ordres dans
  `bot_execution.py`, les types communs dans `bot_types.py`. C'est toujours
  une seule classe, `TrendGuardBot`.
- **La vérification publique** (`verify` sans clé) : une fonction de 132
  lignes devient une fonction de 73 lignes et deux fonctions d'étape.
- **Le serveur du panneau** : la classe des requêtes HTTP sort de la fonction
  de 139 lignes qui l'enfermait ; la connexion et la lecture du corps ont
  chacune leur méthode.
- **L'interface du panneau** : le rapport quotidien et le centre de sécurité
  ont leur module, `static/js/rapport.js`, comme les graphiques et Rachelle.
- **Les documents** : un sommaire (`docs/README.md`) ; l'architecture à jour
  (`docs/ARCHITECTURE.md`).
- **Les règles tiennent seules** : `tests/test_structure.py` vérifie à chaque
  envoi le sens des dépendances, la docstring de chaque module et de chaque
  fonction publique longue, l'absence de code inutilisé et l'usage de
  `texte.fr`.

## Un défaut corrigé au passage

Une requête trop volumineuse envoyée au panneau était refusée sans être lue.
Sous Windows, la connexion pouvait alors être coupée avant que le refus
n'arrive : le navigateur voyait une erreur de réseau au lieu du message
« requête trop volumineuse ». Le corps est maintenant lu sans être gardé,
jusqu'à 1 Mo, puis refusé. Ce défaut, rare, a fait échouer une fois le test
concerné pendant ce travail.

## Laissé volontairement

- **Le moteur `v29/`** : ses six zones répétées, une septième partagée avec
  `alerts.py` (la fermeture des envois), ses fonctions longues et sa fonction
  inutilisée (`tier_threshold_map`). Il est stable et porte les ordres réels :
  on n'y touche pas sans nécessité.
- **Les quatre fonctions qui écrivent un rapport d'étude** (de 122 à 145
  lignes) : du texte assemblé ligne après ligne, sans imbrication. Les
  découper ne les rendrait pas plus claires.
- **`docs/TRENDGUARD_REPORT.md`** et son générateur gardent le point
  décimal : c'est le rapport de recherche d'origine, régénérable seulement
  avec les données Coin Metrics. Le laboratoire garde aussi son format, pour
  que `docs/STRATEGIES.md` se régénère à l'identique.
- **Les fonctions courtes sans docstring** (les réponses de Rachelle, les
  routes du panneau) : leur nom dit ce qu'elles font.
- **Les revues datées** (`docs/revues/`) gardent leurs mots d'origine.

## Refaire ce diagnostic

```bash
python -m research.diagnostic_code          # les mesures
python -m pytest tests/test_structure.py -q # les règles
python -m ruff check .                      # style et imports
```
