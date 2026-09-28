# Architecture du code TrendGuard

Ce document décrit comment le code est rangé, qui dépend de qui, et les règles
communes à tous les fichiers. Il sert de carte pour lire ou modifier le bot.

## Une seule commande

Tout passe par `python trendguard_bot.py <commande>` : le bot, le panneau,
l'autonomie, les clés, et les outils (alertes, veille, stratégie, laboratoire,
animation). `python trendguard_bot.py --help` donne la liste complète.

| Avant | Maintenant |
| --- | --- |
| `python alerts.py configurer` | `python trendguard_bot.py alerts configurer` |
| `python market_watch.py set-key claude` | `python trendguard_bot.py watch set-key claude` |
| `python trend_strategy.py download --data data` | `python trendguard_bot.py strategy download --data data` |
| `python strategy_lab.py --cache data_binance` | `python trendguard_bot.py lab --cache data_binance` |
| `python replay_animation.py` | `python trendguard_bot.py animation` |
| `python research_selection.py` | `python -m research.selection` |
| `python v29.py backtest` | `python -m v29 backtest` |

Les commandes du bot (`run`, `panel`, `supervise`, `stop`, `set-keys`…), la
clé « Démarrer avec l'ordinateur » et les tâches VS Code ne changent pas.

## Dossiers

| Dossier | Rôle |
| --- | --- |
| `trendguard_bot.py` | point d'entrée unique ; ré-exporte les noms du paquet `trendguard` |
| `trendguard/` | le bot TrendGuard |
| `v29/` | moteur d'exécution Binance commun et ancien bot V29.6 |
| `panel/` | panneau de contrôle (serveur local et interface web) |
| `research/` | études reproductibles, lecture seule |
| `tests/` | tests Python (`pytest`) et navigateur (`tests/web/`, Playwright) |
| `templates/` | gabarit de la page d'animation du rejeu |
| `docs/` | rapports de recherche, audit, revues hebdomadaires |

Les fichiers de fonctionnement (base `*.db`, journaux `*.log`, verrous,
choix du panneau) restent à la racine, à côté de `trendguard_bot.py`, et ne
sont jamais envoyés sur GitHub. VS Code les masque dans l'explorateur
(`.vscode/settings.json`).

## Le paquet `trendguard/`

| Module | Rôle |
| --- | --- |
| `trend_strategy.py` | règles d'achat et de vente, backtest de recherche, sélection des cryptos |
| `config.py` | configuration (`GuardConfig`), variables d'environnement documentées, fichier `.env`, journal |
| `bot.py` | `TrendGuardBot` : décision quotidienne, ordres, stops, reprise après arrêt |
| `selection.py` | cryptos achetables, choisies dans le panneau |
| `explain.py` | raisonnement du jour, en phrases simples |
| `anticipation.py` | ventes, achats et risques probables à la prochaine clôture |
| `autonomy.py` | superviseur, démarrage avec l'ordinateur, anti-veille, arrêt demandé |
| `alerts.py` | alertes Telegram, e-mail et WhatsApp |
| `market_watch.py` | veille : annonces officielles de Binance, actualités, avis des IA |
| `watch_claude.py` | avis de Claude pour la veille |
| `diagnostics.py` | auto-diagnostic hebdomadaire (lecture seule) |
| `strategy_lab.py` | laboratoire des stratégies |
| `replay.py` | rejeu paper sur un historique réel |
| `replay_animation.py` | page d'animation du rejeu |
| `cli.py` | ligne de commande |

## Le paquet `v29/`

Chaque section de l'ancien fichier `v29.py` (7 483 lignes) est devenue un
module. Les sections ne dépendent que des sections précédentes, dans cet ordre :

| Module | Rôle |
| --- | --- |
| `constants.py` | constantes, dossier du programme, dépendances optionnelles, `.env` |
| `utils.py` | variables d'environnement, secrets masqués, heure de Binance, client Binance |
| `config.py` | configuration du bot V29 |
| `models.py` | types partagés : positions, portefeuille, contexte |
| `infra.py` | journaux, alertes Telegram, verrou d'instance, heartbeat |
| `store.py` | base SQLite : état, trades, intentions d'ordres |
| `exchange.py` | adaptateur Binance Spot : ordres, soldes, règles de marché |
| `blockchain.py` | wallet EVM (optionnel) |
| `adaptive.py`, `indicators.py`, `signals.py` | moteur adaptatif, indicateurs et signaux du bot V29 |
| `risk.py` | taille des positions, disjoncteurs, stops |
| `execution.py` | moteur d'exécution : entrées, protections, sorties (utilisé par TrendGuard) |
| `reconciliation.py` | réconciliation avec Binance au démarrage |
| `backtest.py` | métriques, backtest, walk-forward du bot V29 |
| `runner.py`, `cli.py` | boucle et ligne de commande du bot V29 (`python -m v29`) |

`import v29` donne toujours accès à tous ces noms (`v29.Store`,
`v29.ExecutionEngine`…).

## Dépendances

```text
research ──► trendguard ──► v29
panel ─────► trendguard ──► v29
trendguard_bot.py ─► trendguard
```

- `v29` ne connaît ni TrendGuard ni le panneau.
- Dans `trendguard`, `config.py`, `selection.py` et `explain.py` ne
  dépendent pas du bot ; `bot.py` s'appuie sur eux, sur la stratégie,
  l'anticipation, la veille et le diagnostic ; `cli.py` assemble le tout et
  n'est importé que par les points d'entrée (`trendguard_bot.py`,
  `python -m panel`).
- Le panneau lit la base du bot sans la modifier et ne passe aucun ordre.
- Dans le code du bot, `research` n'est importé que par le laboratoire, au
  moment de le lancer.

## Règles communes

- **Langue** : commentaires, documentation, messages et journaux en
  français ; noms de variables et de fonctions en anglais.
- **Imports** : bibliothèque standard, puis bibliothèques externes, puis
  modules du projet (`v29`, `trendguard`, `panel`, `research`), puis imports
  relatifs. Ordre vérifié par `ruff` (`pyproject.toml`), comme les imports
  inutilisés et les erreurs courantes.
- **Journaux** : une étiquette entre crochets par sujet (`[ORD]`, `[STOP]`,
  `[VEILLE]`, `[RUSE]`, `[ANTICIPATION]`…).
- **Configuration** : chaque variable d'environnement lue est documentée
  (`TG_ENV_DOC` dans `trendguard/config.py`, `ENV_DOC` dans
  `v29/constants.py`) ; un test vérifie qu'aucune n'est oubliée.
- **Secrets** : jamais écrits dans un journal ni affichés ; saisie masquée
  seulement (`set-keys`, `set-panel-password`, `alerts configurer`,
  `watch set-key`).
- **Argent** : paper par défaut, 1 % de risque par achat, arrêt d'urgence ;
  le mode réel exige une double confirmation.
- **Tests** : chaque changement de comportement vient avec un test ; la suite
  complète et les tests navigateur tournent sur GitHub à chaque envoi.

## Vérifier

```bash
python -m pytest tests -q            # tests Python
python -m ruff check .               # style, imports, erreurs courantes
python -m pip_audit -r requirements-docker.txt   # failles connues des dépendances
```
