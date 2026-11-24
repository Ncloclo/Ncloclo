# TrendGuard (Ncloclo)

Bot de trading Binance Spot en Python. Le bot recommandé est **TrendGuard** :
suivi de tendance sur un portefeuille de 21 crypto-actifs, une décision par
jour, en mode paper par défaut. Mode d'emploi complet dans `README.md`, sommaire
des documents dans `docs/README.md`, organisation du code dans
`docs/ARCHITECTURE.md`.

## Règles de sécurité (prioritaires)

- **Argent réel** :
  - le mode réel reste derrière ses verrous (`ENABLE_LIVE_TRADING=true` +
    `LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_RISK`, puis la porte du réel) ;
  - ne jamais affaiblir ces verrous ni passer en réel sans demande explicite de
    l'utilisateur ;
  - toujours valider en paper, puis sur le testnet (`BINANCE_TESTNET=true`).
- **Clés** :
  - ne jamais écrire une clé d'API, un secret ou un mot de passe dans le code,
    les tests, la documentation ou un commit ;
  - les clés vivent dans `.env` (ignoré par git), avec une clé Binance **sans
    droit de retrait**.
- **Backtests** : ne jamais présenter un résultat comme une garantie de gain.
  Toujours le comparer à BTC conservé sans rien faire, sur la même période.
- **Paramètres gelés** : ceux de TrendGuard ont été choisis sur 2018-2022 et
  gelés le 23/05/2026. Ne pas les régler jusqu'à obtenir un meilleur chiffre sur
  une période. Toute idée nouvelle passe par une étude reproductible
  (`research/`), jugée hors échantillon, avant d'être proposée.
- **Panneau** : il écoute sur `127.0.0.1`. Ne l'ouvrir au réseau (`--host
  0.0.0.0`) qu'avec son mot de passe (`set-panel-password`).

## État des connaissances

### Le poids des frais dépend de la distance du stop

Poids des frais dans le risque d'un trade = frais aller-retour / distance du
stop.

- **Bot-trading (BTC/USDT, intraday, novembre 2026)** : stops serrés à 0,4 %,
  donc les frais (0,2 % aller-retour) mangeaient la moitié du risque, et toutes
  les stratégies perdaient environ 10 % à toutes les cadences.
- **TrendGuard (mesuré le 23/11/2026 sur 3 270 entrées Binance depuis 2018)** :
  - stop initial (3 × volatilité) : en médiane à 8,7 % du prix ; P10 4,7 % ; P90
    14,1 % ;
  - frais + glissement aller-retour (0,4 %) : environ 4,6 % du risque en
    médiane, 8,6 % pour les stops les plus serrés.
- **En pratique** : ne jamais proposer de stop serré. Toute nouvelle stratégie
  doit vérifier ce rapport avant d'être testée plus loin.

### Cadences (bot-trading, BTC/USDT, frais 0,1 % par ordre, évaluation du 12/11/2026)

- **5 min, 15 min, 1 h** : toujours perdant (−1 % à −10 %). Ne pas proposer ces
  cadences.
- **4 h, sur 9 ans** : `tendance` +44 % (+54 % puis +9 % par moitié),
  `croisement` +28 %, `prudente` +25 %. BTC sans rien faire : +1165 %.
- **1 jour, sur 9 ans** : +9 % à +13 %, contre +779 % pour BTC conservé.
- **Conclusion** : une seule paire ne suffit pas. L'avantage de TrendGuard vient
  du portefeuille multi-actifs journalier, avec un filtre de régime BTC. Le bot
  V29.6 intraday (`v29/intraday/`) n'a aucun avantage démontré.

### TrendGuard (prix Binance, 21 paires du bot)

Chiffres du 23/11/2026, issus de `trend_strategy.backtest` avec les paramètres
par défaut sur `data_binance`.

- **2018-2022 (conception)** : +41 %/an, baisse max −25 %, 138 trades. Sur la
  même période, BTC conservé fait +24 % au total.
- **2023 → 22/11/2026** : +32 %/an (+194 %), baisse max −34 %, 180 trades. Sur
  la même période, BTC conservé fait +269 % au total (baisse max −53 %).
- **24/05 → 22/11/2026 (période jamais vue au moment du gel)** : +9,5 %, contre
  −20,5 % pour BTC. 12 trades seulement : non probant.
- **Profil des trades** : 37 à 44 % de gagnants, un gain moyen d'environ 3,8 à
  4,5 R pour une perte moyenne d'environ 1 R (1 % du capital). Il faut
  s'attendre à des séries de 8 à 13 pertes. Une part notable des gains vient de
  quelques actifs (ZEC, XRP, BTC).

### Perte du jour : 8 %, pas 3 % (vérifié le 23/11/2026)

`trendguard/garde.py` interdit les achats après une perte du jour de 8 %. Le
seuil de 3 % de bot-trading a été rejoué jusqu'au 22/11/2026 avec `python -m
research.garde --cache data_binance`, et il dégrade la stratégie.

| Période | Seuil 8 % | Seuil 3 % |
| --- | --- | --- |
| 2018-2022 | Calmar 1,54 | Calmar 1,34 (baisse max −23,4 % contre −20,6 %) |
| Depuis 2023 | Calmar 1,13 | Calmar 1,08 |
| Jours sans achat | 5 | 66 |

S'interdire d'acheter après une mauvaise journée fait manquer des reprises. Ne
pas reproposer ce seuil sans nouvelle étude.

### Petit capital : minimum de Binance compté au stop (24/11/2026)

`ts.size_position` refuse un achat si la position, revendue à son stop, vaudrait
moins de 5 USDT + 5 % (filtre NOTIONAL de Binance, qui vaut aussi pour la vente).
L'ancien minimum fixe de 10 USDT à l'achat refusait, avec 100 USDT, les achats à
stop large. Étude : `python -m research.petit_capital --cache data_binance`
(`docs/PETIT_CAPITAL.md`), fiche de la stratégie passée en version 1.1.0.

| Capital | 2018-2022 | 2023 → 22/11/2026 | 24/05 → 22/11/2026 |
| --- | --- | --- | --- |
| 10 000 USDT (inchangé) | +460 % | +194 % | +9,5 % |
| 100 USDT, ancienne règle | +454 % | +219 % | +2,6 % |
| 100 USDT, règle actuelle | +454 % | +195 % | +9,5 % |

- Le minimum vient des règles de Binance, pas des résultats. Depuis 2023,
  l'ancienne règle faisait mieux à 100 USDT par chance : les achats refusés ont
  plutôt perdu.
- Sous 50 USDT, de plus en plus d'achats sont refusés ; à 20 USDT, aucun ne
  passe. Ne pas conseiller moins de 100 USDT.

### Bibliothèque de recherche et agent chercheur (24/11/2026)

`trendguard/bibliotheque.py` lit chaque jour les travaux d'arXiv (q-fin) qui
touchent aux règles du bot ; l'agent « chercheur » du comité les cite sans
voter. Une piste ne change jamais une règle sans étude hors échantillon.
Diagnostic complet : `docs/INTELLIGENCE.md`. Refusés et pourquoi : lire le dark
web, réécrire seul son code, agir sur une information non prouvée.

- Agents macro, dérivés, flux (stablecoins, `trendguard/flux.py`) : poids 0
  tant qu'une étude n'a pas prouvé leur utilité.
- `trendguard/pistes.py` : chaque semaine, variantes déclarées par thème,
  rejouées sur les données du bot ; une variante n'est « à étudier » que si
  elle bat la règle sur 2018-2022 ET depuis 2023. 24/11/2026 : la règle tient
  partout.
- Combinaisons (`python -m research.combinaisons --cache data_binance`) :
  TrendGuard + Tortues / Bollinger / Tendance rapide font mieux depuis 2023
  (Calmar 1,06 à 1,26 contre 0,95) mais moins bien sur 2018-2022 (1,05 à 1,54
  contre 1,62) ; corrélations 0,87 à 0,93. Aucune retenue : ne pas reproposer
  sans nouvelles données.

## Commandes (Windows, depuis la racine)

- **Installation** (Python 3.12, la version de la CI, géré par uv) : `uv python
  install 3.12`, puis `& "$env:APPDATA\uv\python\cpython-3.12-windows-x86_64-none\python.exe"
  -m venv .venv`, puis `just install`. Jamais un Python rangé dans le dossier
  d'un autre logiciel : le `.venv` en dépendait jusqu'au 24/11/2026.
- **`just`** : `check` (Ruff, tests Python, pages web dans Chrome), `lint`,
  `test`, `web`, `dev` (panneau), `journal`, `statut`.
- **Journal en direct** : `.venv\Scripts\python trendguard_bot.py journal`.
  Le bot lancé par le panneau (AUTO) ou le superviseur tourne sans fenêtre et
  écrit dans `trendguard_paper.log` ; lancés depuis un terminal, `supervise`
  et `run` affichent ce journal quand le bot tourne déjà.
- **Jamais de console pour un processus de fond sous Windows** : `pythonw.exe`
  (`autonomy.gui_python`). Un programme console lancé « caché » ouvre une
  fenêtre vide sous Windows 11 (Windows Terminal), et la fermer arrête le
  programme.
- **Bot paper** : `.venv\Scripts\python trendguard_bot.py run`. État : `status`.
  Panneau : `panel` (<http://127.0.0.1:8765>).
- **Rejeu sur l'historique réel** : `trendguard_bot.py replay --data data
  --start 2025-06-01`.
- **Études** : `.venv\Scripts\python -m research.<etude> --cache data_binance`.
  Rapport principal : `trendguard_bot.py strategy research --data data`.
- **Contrôles** : `.venv\Scripts\python -m pytest tests -q` et
  `.venv\Scripts\python -m ruff check .`.
- **Tests navigateur** (panneau et page d'animation) : dans Chromium et Google
  Chrome (`tests/web/playwright.config.js`, deux projets ; la CI lance les
  deux). Sur le PC : `npm install`, puis `node
  node_modules/@playwright/test/cli.js test -c tests/web/playwright.config.js
  --project chrome`.

## Conventions

- Python 3.12, Ruff (100 colonnes), noms et textes en français.
- Branches : `amelioration/AAAA-MM-JJ` pour une amélioration, `revue/AAAA-MM-JJ`
  pour la revue hebdomadaire.
- Une étude ne change pas une règle du bot : elle mesure. Une règle ne change
  qu'après une étude reproductible, documentée dans `docs/`.
- Pas d'auto-modification du code par le bot. Les réglages qu'il peut changer
  seul sont décrits dans `docs/EVOLUTION.md`.
