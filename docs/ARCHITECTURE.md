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

## Bibliothèques : l'environnement propre du bot

Les bibliothèques du bot sont dans le dossier `.venv`, à côté du code, aux
versions de `requirements-docker.txt` : celles de l'image Docker et des
contrôles GitHub. Les autres logiciels du PC gardent les leurs, et une mise à
jour des uns ne casse plus les autres.

- `trendguard_bot.py` relance toute commande dans ce dossier avant de lire le
  reste du code (`trendguard/environnement.py`, bibliothèque standard
  seulement). Sous Linux et macOS, le processus est remplacé ; sous Windows, qui
  ne sait pas le faire, le premier processus attend la fin de la commande et
  transmet son code de sortie.
- Un processus lancé par le bot (superviseur, bot, rapport, panneau relancé)
  reste dans l'environnement de son parent : la variable `TRENDGUARD_ENV`, posée
  par le premier, passe aux suivants.
- Sans dossier `.venv`, s'il est incomplet (installation interrompue) ou s'il
  ne peut pas être lancé, le bot tourne avec les bibliothèques du PC. La clé
  « Démarrer avec l'ordinateur » garde donc le Python de l'installation : le
  démarrage ne dépend pas de ce dossier.
- Chaque processus du bot n'utilise qu'un fil de calcul (`limit_math_threads`).
  numpy réserve environ 30 Mo de mémoire par fil dès son chargement : avec huit
  fils, 341 Mo par processus ; avec un seul, 104 Mo, pour les mêmes résultats.
- Le rapport quotidien compare les bibliothèques installées aux versions testées
  et cherche leurs failles connues (`pip-audit`) ; une faille que ccxt, qui
  épingle ses bibliothèques, empêche encore de corriger n'est qu'une
  information, jusqu'à ce que sa version publiée accepte la correction. Les
  installer reste une
  recommandation : README, « Bibliothèques du bot ».

## Dossiers

| Dossier | Rôle |
| --- | --- |
| `trendguard_bot.py` | point d'entrée unique ; ré-exporte les noms du paquet `trendguard` |
| `trendguard/` | le bot TrendGuard |
| `v29/` | moteur d'exécution Binance (ordres, stops, base, verrou) ; ancien bot V29.6 rangé dans `v29/intraday/` |
| `panel/` | panneau de contrôle (serveur local et interface web) |
| `research/` | études reproductibles, lecture seule : adaptation, palier de risque, sélection, robustesse, examen ; `commun.py` leur donne les mêmes données, options et écriture du rapport |
| `tests/` | tests Python (`pytest`) et navigateur (`tests/web/`, Playwright) |
| `templates/` | gabarit de la page d'animation du rejeu |
| `docs/` | rapports de recherche, audit, revues hebdomadaires ; sommaire dans `docs/README.md` |

Les fichiers de fonctionnement (base `*.db`, journaux `*.log`, verrous,
choix du panneau) restent à la racine, à côté de `trendguard_bot.py`, et ne
sont jamais envoyés sur GitHub. VS Code les masque dans l'explorateur
(`.vscode/settings.json`).

## Le paquet `trendguard/`

| Module | Rôle |
| --- | --- |
| `trend_strategy.py` | règles d'achat et de vente, backtest de recherche, sélection des cryptos |
| `config.py` | configuration (`GuardConfig`), variables d'environnement documentées, fichier `.env`, journal |
| `bot.py` | `TrendGuardBot` : démarrage, cycle, décision quotidienne, sélection, boucle |
| `bot_routines.py` | routines du cycle : disponibilité, veille, horloge, évolution et rapport lancés, anticipation, apprentissage, heartbeat |
| `bot_execution.py` | exécution : entretien des positions, ventes, stops remontés, achats rusés (carnet anormal, achat différé) |
| `bot_types.py` | types partagés du bot : `Slot`, `DecisionDeferred`, `last_closed_day` |
| `selection.py` | cryptos achetables, choisies dans le panneau |
| `explain.py` | raisonnement du jour, en phrases simples |
| `anticipation.py` | ventes, achats et risques probables à la prochaine clôture |
| `autonomy.py` | superviseur, démarrage avec l'ordinateur, anti-veille, arrêt demandé |
| `uptime.py` | disponibilité : arrêts de plus d'une heure, leur cause, temps de marche |
| `learning.py` | apprentissage libre : normale des carnets, ruse réglée sur elle, prévisions corrigées par l'expérience |
| `evolution.py` | évolution encadrée : réglages ajustés par le bot sous épreuves, niveaux, essais de 30 jours |
| `report.py` | rapport quotidien : assemblage, archive, envoi, ligne de commande |
| `report_security.py` | contrôles de sécurité du rapport : secrets, base, clé Binance, Windows, alimentation, bibliothèques ; protections sûres appliquées seules |
| `report_health.py` | santé du fond et de la forme : panneau, bot, disque et mémoire du PC, journal, stratégie, compétences, code |
| `report_render.py` | mise en page du rapport : texte, résumé court, page de l'e-mail |
| `maintenance.py` | recommandations appliquées seules : veille du PC, mises à jour validées par le propriétaire |
| `systeme.py` | accès au système partagé : commandes, git, GitHub, réglages de Windows, bibliothèques installées, disque et mémoire, état en lecture seule |
| `environnement.py` | environnement propre du bot (dossier `.venv`) : commande relancée dedans s'il est complet, un seul fil de calcul, Python du démarrage avec l'ordinateur |
| `texte.py` | nombres écrits à la française (`fr`, `fr_plain`) pour le bot, le panneau, les rapports et les études |
| `journal.py` | journaux muets des simulations et des vérifications (`silent_logger`) |
| `alerts.py` | alertes Telegram, e-mail et WhatsApp ; un canal dont le mot de passe est refusé trois fois n'essaie plus qu'une fois par jour |
| `market_watch.py` | veille : annonces officielles de Binance, actualités, avis des IA |
| `regimes.py` | régimes de marché : tendance de BTC, volatilité, appétit pour le risque, phase ; information et journal des trades, aucune décision |
| `garde.py` | garde « NO TRADE » avant les achats du jour : données, mouvement de BTC, perte du jour, disque ; seuils sans effet sur 8 ans de résultats |
| `postmortem.py` | analyse après chaque trade : régime à l'achat, meilleur et pire moment en R, glissement, leçon |
| `risque.py` | risque d'un jour du portefeuille (VaR et CVaR historiques) |
| `stress.py` | tests de résistance des positions du moment : krachs sans stops, crise de liquidité, pic de volatilité, retrait de la cote, décrochage de l'USDT |
| `qualite.py` | qualité des données : note sur 100 des clôtures avant chaque décision |
| `attribution.py` | attribution des résultats : par crypto, régime, type de sortie, leçon ; pourquoi le bot a gagné ou perdu |
| `evenements.py` | calendrier économique : grandes annonces américaines (information), réaction du bitcoin mesurée avec le temps |
| `registre.py` | registre des expériences (évolution, contrôles) rejouables à l'identique, carte du modèle |
| `contrats.py` | contrats entre les modules (registre, données vérifiées à la création) ; docs/CONTRATS.md en est tiré |
| `porte.py` | porte d'exécution : contrôle déterministe du risque et autorisation avant chaque achat ; mode sûr |
| `audit.py` | journal d'audit chaîné : chaque opération critique, toute modification détectée |
| `cognitif.py` | noyau cognitif : plan en graphe de tâches validé, orchestration (états, délais, nouveaux essais selon la cause, annulation), vérification croisée, incertitude, décision jamais autorisée seule |
| `expert.py` | diagnostic expert par le noyau cognitif (lecture seule), chaque jour à 00:45 UTC et à la demande |
| `donnees.py` | socle de données : journal financier (décisions, signaux, contrôles du risque, ordres, exécutions, trades) dans la base du bot, migrations, lignée de chaque trade |
| `libre.py` | bot libre : second portefeuille fictif qui apprend de chaque source, se fait son avis, agit seul et révise ses propres règles chaque semaine, à côté du bot principal |
| `savoir.py` | noyau de savoir : presse, moteurs de recherche, forums, réseau social, tendances et avis des IA lus toutes les 15 minutes, chaque source jugée sur les cours réels ; l'avis des sources prouvées peut seulement reporter un achat |
| `watch_claude.py` | avis de Claude pour la veille |
| `diagnostics.py` | auto-diagnostic hebdomadaire (lecture seule) |
| `strategy_lab.py` | laboratoire des stratégies |
| `replay.py` | rejeu paper sur un historique réel ; bot simulé commun au rejeu et à l'animation (`simulated_bot`) |
| `replay_animation.py` | page d'animation du rejeu |
| `cli.py` | ligne de commande |

## Le panneau `panel/`

| Fichier | Rôle |
| --- | --- |
| `server.py` | serveur local : pages, API (une table de routes), sécurité des accès, anticipation |
| `security.py` | centre de sécurité : état des protections en direct ; alimentation, disque et mémoire repris du rapport quotidien |
| `data.py`, `market.py` | lecture de la base du bot (sans la modifier) ; cours publics de Binance, lus d'avance et gardés : une page n'attend jamais le réseau |
| `control.py` | bouton AUTO / ARRÊTER (superviseur), démarrage avec l'ordinateur |
| `assistant.py`, `news.py` | Rachelle ; actualités et marchés |
| `demo.py` | données fictives pour `--demo` et les tests |
| `static/app.js` | les pages de l'interface (module ES) |
| `static/js/core.js` | outils communs : formats, DOM, temps de chargement, API, notifications |
| `static/js/charts.js` | graphiques, flèches d'achats et de ventes, niveaux d'entrée et de stop |
| `static/js/assistant.js` | fenêtre de Rachelle, garde-fou des secrets côté navigateur |
| `static/js/rapport.js` | rapport quotidien et centre de sécurité (onglet Réglages) |

## Le paquet `v29/`

L'ancien fichier `v29.py` (7 483 lignes) mélangeait le moteur d'exécution
utilisé par TrendGuard et l'ancien bot V29.6 intraday. Ils sont maintenant
séparés : `v29/` ne contient que le moteur, et l'ancien bot est rangé dans
`v29/intraday/`, chargé seulement quand on s'en sert.

| Moteur (`v29/`) | Rôle |
| --- | --- |
| `constants.py` | constantes, dossier du programme, fichier `.env` |
| `utils.py` | variables d'environnement, secrets masqués, heure de Binance, client Binance |
| `config.py` | configuration par paire |
| `models.py` | types partagés : positions, portefeuille, contexte |
| `infra.py` | journaux, alertes Telegram, verrou d'instance, heartbeat |
| `store.py` | base SQLite : état, trades, intentions d'ordres |
| `exchange.py` | adaptateur Binance Spot : ordres, soldes, règles de marché |
| `risk.py` | taille des positions, disjoncteurs, stops |
| `execution.py` | moteur d'exécution : entrées, protections, sorties |
| `reconciliation.py` | réconciliation avec Binance au démarrage |

| Ancien bot (`v29/intraday/`) | Rôle |
| --- | --- |
| `adaptive.py`, `indicators.py`, `signals.py` | moteur adaptatif, indicateurs et signaux |
| `blockchain.py` | wallet EVM (optionnel, avec web3) |
| `backtest.py` | métriques, backtest, walk-forward du bot V29 |
| `runner.py`, `cli.py` | boucle et ligne de commande (`python -m v29`) |

`import v29` donne accès à tous les noms du moteur (`v29.Store`,
`v29.ExecutionEngine`…) sans charger l'ancien bot ni web3 ; l'ancien bot :
`from v29 import intraday`. Un test le vérifie.

## Une seule boucle de backtest

`trendguard/trend_strategy.py` contient LA boucle de backtest (`backtest`),
avec les mêmes fonctions que le bot (`update_positions`, `plan_entries`) et
une seule formule de taille de position (`entry_levels`, `size_position`).
Les études la réutilisent avec des crochets (`BacktestHooks`) au lieu de la
recopier :

| Étude | Crochet utilisé |
| --- | --- |
| profil prudent, espérance récente, corrélation, entrées par jour (`research/adaptation.py`) | `risk_cap`, `filter_plans` |
| auto-sélection des plus rentables, prise de bénéfice (`research/selection.py`) | `choose`, `take_profit` |
| palier de risque rejoué pas à pas, arrêt d'urgence levé seul (`research/palier.py`) | `risk_scale` |
| variantes de tendance et de régime (`trendguard/strategy_lab.py`) | paramètres et régime |

Seules les deux stratégies aux règles différentes du laboratoire (rotation,
retour à la moyenne) ont leur simulateur (`strategy_lab.simulate`), qui
calcule la taille des positions avec la même formule. Une correction faite
dans la boucle vaut ainsi pour le bot, le backtest et toutes les études :
les trois rapports régénérés après cette unification sont identiques, à
l'octet près.

## Dépendances

```text
research ──► trendguard ──► v29
panel ─────► trendguard ──► v29
trendguard_bot.py ─► trendguard
```

- `v29` ne connaît ni TrendGuard ni le panneau ; son moteur ne dépend pas
  de l'ancien bot (`v29/intraday/`).
- Dans `trendguard`, `config.py`, `selection.py` et `explain.py` ne
  dépendent pas du bot ; `bot.py` s'appuie sur eux, sur la stratégie,
  l'anticipation, la veille et le diagnostic ; `cli.py` assemble le tout et
  n'est importé que par les points d'entrée (`trendguard_bot.py`,
  `python -m panel`).
- Le rapport quotidien est en quatre fichiers : `report.py` assemble les
  contrôles de `report_security.py` et de `report_health.py`, puis les met en
  page avec `report_render.py`. Le panneau reprend les mêmes constats
  (`panel/security.py`, dont hérite `PanelApp`) : un seuil ou un texte ne
  s'écrit qu'une fois.
- Les fichiers JSON du bot se lisent et s'écrivent par deux fonctions
  (`autonomy.read_json`, `autonomy.write_json`), et tout envoi d'alerte passe
  par une seule (`AlertHub._send`).
- Le bot est une seule classe en trois fichiers : `bot.py` (le cœur),
  `bot_routines.py` et `bot_execution.py` (deux parties ajoutées par
  héritage), avec les types communs dans `bot_types.py`.
- Le panneau lit la base du bot sans la modifier et ne passe aucun ordre.
- Le code du bot n'importe jamais `research` : le laboratoire et les études
  chargent leurs données par la même fonction (`evolution.load_history`, via
  `research/commun.py` pour les études).

## Règles communes

- **Langue** : commentaires, documentation, messages et journaux en
  français ; noms de variables et de fonctions en anglais.
- **Imports** : bibliothèque standard, puis bibliothèques externes, puis
  modules du projet (`v29`, `trendguard`, `panel`, `research`), puis imports
  relatifs. Ordre vérifié par `ruff` (`pyproject.toml`), comme les imports
  inutilisés et les erreurs courantes.
- **Journaux** : une étiquette entre crochets par sujet (`[ORD]`, `[STOP]`,
  `[VEILLE]`, `[RUSE]`, `[ANTICIPATION]`…).
- **Nombres** : dans une phrase affichée, écrits à la française par
  `texte.fr` (virgule décimale, espace des milliers, vrai signe moins). Le
  point décimal reste dans les journaux techniques de la forme `clé=valeur`,
  la consigne envoyée aux IA et les nombres envoyés à Binance.
- **Simulations** : un seul bot simulé (`replay.simulated_bot`) et un seul
  journal muet (`journal.silent_logger`) pour le rejeu, l'animation et les
  vérifications.
- **Fonctions** : une docstring en français pour toute fonction publique
  longue ; une fonction qui dépasse une centaine de lignes est découpée.
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
python trendguard_bot.py rapport failles         # failles connues des versions testées
```

Sur un PC qui a le dossier `.venv`, ces trois commandes se lancent avec son
Python (`.venv\Scripts\python` sous Windows, `.venv/bin/python` ailleurs) : les
tests tournent alors avec les bibliothèques du bot.
