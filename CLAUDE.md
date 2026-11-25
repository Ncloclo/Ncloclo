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

### Au-delà des cryptos (24/11/2026)

`python -m research.multi_actifs --cache data_multi` (`docs/MULTI_ACTIFS.md`) :
la règle transposée (régime propre à chaque actif) sur 21 marchés hors cryptos
(matières premières, devises, indices, obligations ; actions à part, biais du
survivant).

- Frais de Binance : perdante partout ; ils mangent 23 à 45 % du risque d'un
  trade sur devises, indices, obligations (stop à 0,9-1,8 % du prix).
- Correctif du 24/11/2026 : la règle se calcule sur les séances réelles de
  chaque marché (`multi_actifs.sessions`) ; une place fermée garde son cours 10
  séances. Avant, les jours fermés (cours recopié) diluaient la volatilité, et
  la semaine d'or de Shanghai passait pour un retrait de la cote (−36 R fictifs).
- Coûts réels, après correctif : espérance +0,18 R par trade sur 2018-2022
  (contre +1,50 R sur les cryptos) ; moitié-moitié avec les cryptos : Calmar
  1,09 puis 0,95 (contre 1,62 puis 0,95), règle fixée d'avance non satisfaite
  (elle l'était avant le correctif : 1,72 puis 1,05).
- Décision : plus de proposition ; le portefeuille fictif reste suivi pour
  information (choix de l'utilisateur : argent fictif partout) ; aucun ordre
  réel (il faudrait un courtier). Le XOF a une parité fixe avec l'euro.
- Panneau : une page par classe d'actifs (`panel/static/js/pages/marches.js`,
  `/api/markets`, `/api/market`), lue dans `data_multi/` et le noyau de
  savoir ; menu en trois groupes (Mon bot, Marchés, Analyse).

### Courtiers (25/11/2026)

- `trendguard/courtiers.py` (`docs/COURTIERS.md`) : annuaire (15 courtiers
  MetaTrader 5, Alpaca, 104 plateformes CCXT dont 48 à testnet, SGI de la
  BRVM ; disponibilité en Côte d'Ivoire jamais affirmée sans source).
- Cours des cryptos du bot sur dix plateformes face à Binance, toutes les 6 h
  (clé « courtiers » du noyau de savoir ; listes des marchés gardées une
  semaine dans `data_courtiers/`). Information seulement. Première lecture :
  Binance à 0,02 % de la médiane des autres.
- Comptes de démonstration seulement, lus chaque heure : MetaTrader 5
  (`trade_mode` démo exigé, sinon refus ; le terminal n'est jamais ouvert par
  la lecture de l'heure), Alpaca (adresse du compte papier seule),
  testnets CCXT (mode test obligatoire, adresses du testnet seules ; OKX :
  en-tête de démonstration exigé). Identifiants dans `.env` par
  `courtiers set-key`, vérifiés avant d'être écrits.
- Chaque requête CCXT passe par `courtiers.guard` : adresse hors de
  `cyber.ALLOWLIST` (ou du testnet) refusée avant de partir.
- Aucun ordre envoyé : plan miroir à blanc (portefeuille fictif hors cryptos
  recopié au même poids). Ordres de démonstration seulement après un premier
  essai réussi sur un vrai compte démo de l'utilisateur.

### BRVM (24/11/2026)

- `trendguard/brvm.py` (`docs/BRVM.md`) : les deux pages publiques de brvm.org
  (indices, actions) lues une fois par séance après 16:30 UTC, à 11 s d'écart
  (robots.txt : Crawl-delay 10), dans la collecte du noyau de savoir. Pas
  d'historique publié : le bot construit le sien (`data_brvm/`, première séance
  le 24/11/2026) ; jour férié = page inchangée, rien n'est ajouté.
- Page « BRVM » du panneau ; BRVM Composite sur les huit pays de l'UEMOA. Règle
  applicable après 150/250 séances ; aucun ordre réel (il faudrait une SGI).

### Pays du monde (24/11/2026)

- `trendguard/pays.py` : 87 pays, classement MSCI de juin 2026 (revue
  d'accessibilité, annexe II ; résultats du 23/06/2026) : 23 développés, 24
  émergents, 5 frontières avancés, 23 frontières (dont les 8 pays de l'UEMOA,
  bourse BRVM), 12 autonomes. Grèce développée et Bulgarie frontière en mai
  2027 ; Indonésie et Turquie sous surveillance (revue de novembre 2026).
- Indice de 49 pays chez Yahoo (`multi_actifs.WORLD` ; fonds MSCI cotés à New
  York quand l'indice local n'a pas d'historique), 51 devises
  (`multi_actifs.CURRENCIES`), Banque mondiale relue chaque semaine (clé
  « pays » du noyau de savoir ; chiffres de plus de 5 ans écartés).
- Ne pas confondre avec `trendguard/monde.py`, le modèle du monde (raisonnement).
- `python -m research.pays --cache data_multi` (`docs/PAYS.md`) : la règle bat
  l'achat-conservation seulement sur les frontières (4 indices : trop peu pour
  conclure) ; information seulement.

### Synthèse des marchés de TradingView (24/11/2026)

- Jamais de collecte automatique sur TradingView ni Investing.com : leurs
  conditions l'interdisent. Mêmes marchés lus chez Yahoo ; Investing.com
  seulement par ses flux RSS publics (source « Investing.com » du noyau de
  savoir, jugée comme les autres).
- 26 marchés ajoutés (`multi_actifs.ADDED` : platine, produits agricoles,
  indices d'Asie, d'Europe et du Brésil, NZD/USD, DXY, SHY, TSMC, AMD, Anthropic,
  SpaceX) et 5 indicateurs (VIX, taux US 3 mois à 30 ans). Étude
  `python -m research.marches_ajoutes --cache data_multi`
  (`docs/MARCHES_AJOUTES.md`) : l'univers élargi fait moins bien sur 2018-2022
  (Calmar −0,07 contre 0,05) ; suivis pour information, hors du portefeuille
  fictif (`ADDED_IN_PORTFOLIO = False`).
- Cours du bot contre TradingView : 31 sur 39 à 0,5 % près ; les autres lus en
  pleine séance. D'où la lecture quotidienne après la clôture de New York
  (22:30 UTC, `multi_actifs.due`).
- `python -m research.macro --cache data_binance --multi data_multi`
  (`docs/MACRO.md`) : VIX > 30, dollar fort, choc de taux, courbe inversée ;
  aucun filtre ne gagne sur les deux périodes. VIX > 30 aurait évité 7 achats
  perdants en 2018-2022, mais aucune occasion depuis 2023 : à revoir après la
  prochaine période de stress, pas avant. Information seulement (Veille, agent
  macro du comité, poids nul).

### Sur Binance au-delà des 21 cryptos (24/11/2026)

- Binance liste des actions tokenisées (« bStocks » : NVDAB, GOOGLB, QQQB…),
  l'or (PAXG, XAUT) et l'euro (EUR/USDT). `python -m research.binance_hors_cryptos
  --cache data_multi` : manche de 9 actifs (sous-jacents Yahoo, frais de
  Binance) +6,6 %/an puis +29,3 %/an, espérance +0,28 R ; 75 % cryptos et 25 %
  manche : Calmar 1,74 puis 1,36 (contre 1,62 puis 0,95), mais biais de
  popularité et période jamais vue moins bonne. Suivie en argent fictif
  (`multi_actifs.BINANCE`, page Veille). Écartés : fonds à levier, sociétés non
  cotées, cotations trop récentes.
- `python -m research.univers_crypto --cache data_univers` : 21 + 25 cryptos
  liquides (liste gardée dans `data_univers/candidats.json`) ; Calmar 1,87 puis
  1,16 (contre 1,62 puis 0,95), forte part de biais du survivant ; période
  jamais vue moins bonne. Appliqué au bot paper seulement, par `TG_UNIVERSE`
  dans `.env` (le code garde les 21 par défaut).

### Levier (24/11/2026)

`trendguard/levier.py` : chaque semaine (01:30 UTC), le bot rejoue sa stratégie
avec un levier fixe (1,25× à 3×) et une politique adaptative (1,5× en marché
haussier, à moins de 10 % du sommet), emprunt compté à 10 % par an. Une
politique n'est permise que si son Calmar n'est pas moins bon sur 2018-2022 ET
depuis 2023, et si sa pire baisse reste au-dessus de −30 %. Verdict dans la page
Veille ; jamais appliqué : le bot trade au comptant, sans moteur d'emprunt.

- `python -m research.levier --cache data_binance` (`docs/LEVIER.md`) : aucun
  levier permis, même avec un emprunt à 20 %. Jusqu'à 2×, le Calmar ne bouge
  presque pas (1,62 → 1,63 puis 0,95 → 0,97) : rendement et baisse sont
  multipliés ensemble. Sans levier, la baisse depuis 2023 atteint déjà −33,6 % ;
  à 1,25×, −40,3 % (arrêt d'urgence touché). L'adaptative fait pire (Calmar
  1,21 sur 2018-2022).
- Ne pas proposer de levier réel sans accord exprès : testnet d'abord, moteur
  d'emprunt à construire, verrous du réel inchangés.

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
