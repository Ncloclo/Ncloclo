# TrendGuard + V29.6 — Bots de trading Binance Spot

> ⚠️ Logiciel de trading automatisé : risque de perte en capital. Les
> performances passées ne garantissent pas les performances futures. Validez
> toujours en **paper**, puis sur le **testnet Binance**, avant tout capital
> réel.

Le dépôt contient deux stratégies partageant le même moteur d'exécution
sécurisé :

| | **TrendGuard** (recommandée) | V29.6 intraday |
| --- | --- | --- |
| Fichiers | `trend_strategy.py`, `trendguard_bot.py` | `v29.py` |
| Style | Suivi de tendance, portefeuille multi-actifs, journalier | Signaux multi-modules, une paire, 1 h |
| Validation | Données réelles 2018→2026, hors échantillon, walk-forward | Aucun avantage démontré |
| Risque | 1 % du capital par trade | 1 % par trade |

## TrendGuard

### Règles

1. **Régime** : entrées autorisées seulement si BTC clôture au-dessus de sa
   moyenne 150 jours ; en régime baissier, les stops ouverts sont resserrés.
2. **Entrée** : clôture au-dessus du plus haut des 30 clôtures précédentes,
   momentum 90 j positif, liquidité suffisante ; candidats classés par momentum.
3. **Stop** : initial à 3 × volatilité, puis trailing « chandelier » à 5 ×
   volatilité sous le plus haut (2 × en régime baissier), évalué à la clôture.
4. **Taille** : **1 % du capital risqué par trade** ; max 8 positions, 6 % de
   risque cumulé, 25 % du capital par position.

### Résultats

Détails complets : [`docs/TRENDGUARD_REPORT.md`](docs/TRENDGUARD_REPORT.md).

Données Coin Metrics, 24 actifs dont plusieurs effondrés (FTT, EOS, NEO…).
Frais 0,1 % + slippage 0,1 % par côté. Paramètres choisis sur 2018-2022,
puis **gelés** et testés une seule fois sur 2023→mai 2026 :

| Période | CAGR | Max DD | Sharpe | Trades | Gagnants | Gain moy. | Perte moy. |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2018-2022 (conception) | +44,1 % | −24,6 % | 1,22 | 161 | 49 % | +4,2 R | −1,03 R |
| **2023-2026 (hors échantillon)** | **+40,0 %** | **−33,4 %** | **1,22** | 176 | 36 % | +3,9 R | −1,02 R |
| BTC achat-conservation 2023-2026 | +57,0 % | −49,1 % | 1,19 | — | — | — | — |

- Les 144 variantes de paramètres testées en conception sont toutes rentables :
  le résultat ne tient pas à un réglage chanceux.
- En walk-forward (ré-optimisation tous les 6 mois), la stratégie fait +45,8 %
  par an avec un drawdown max de −28 %.
- À ne pas attendre : un taux de réussite élevé. La stratégie gagne 36 à 49 % de
  ses trades, mais un gain moyen vaut environ 3,8 fois une perte moyenne.
- Il faut accepter des séries de 7 à 12 pertes consécutives et des drawdowns de
  25 à 35 %.
- Pour plus de stabilité, `TG_RISK_PCT=0.005` donne −18 % de drawdown pour +28 %
  par an.

### Auto-diagnostic et adaptation ([`docs/ADAPTATION.md`](docs/ADAPTATION.md))

`python trendguard_bot.py diagnose` analyse, sans passer d'ordre :

- **le système** : horloge et latence vers Binance, bot actif, disque ;
- **les données** : retards, trous, prix aberrants, liquidité ;
- **le marché** : régime BTC, hésitation, volatilité ;
- **les signaux du jour** ;
- **le portefeuille** : risque engagé, corrélations, scénarios de krach ;
- **la santé de la stratégie** : l'avantage statistique existe-t-il encore ?
  Avec la probabilité historique de finir en gain sur 12, 24 et 36 mois ;
- **les résultats réels du bot comparés à l'historique**, par un test
  statistique ;
- **les alternatives** : un tournoi de sept stratégies sur les 24 derniers mois
  indique si TrendGuard reste compétitive.

Le bot relance ce diagnostic tous les 7 jours (`TG_AUTO_DIAGNOSE_DAYS`) et
notifie en cas d'alerte.

Aucune règle ne s'auto-modifie : sur données réelles, les adaptations « apprises
des
résultats récents » font moins bien hors échantillon. Seul un **profil prudent**
optionnel est proposé (`TG_DD_THROTTLE=0.10:0.5` : risque divisé par 2 au-delà
de 10 %
de baisse). Il réduit la pire baisse de −33,6 % à −24,2 % sur 2023-2026, pour
+32 % par an au lieu de +37 %.

### Laboratoire de stratégies ([`docs/STRATEGIES.md`](docs/STRATEGIES.md))

`python strategy_lab.py --cache data_binance` répond, sur les données Binance, à
la question « le bot peut-il apprendre et adopter la meilleure stratégie ? » :

- **Tournoi de 7 stratégies** définies à l'avance : trois horizons de suivi de
  tendance, deux filtres de régime, une rotation de momentum et un retour à la
  moyenne. Toutes risquent 1 % par trade, avec les mêmes frais et plafonds.
  Aucune ne bat TrendGuard à la fois sur 2018-2022 et sur 2023-2026.
- **Taux de réussite élevé ≠ rentabilité** : le retour à la moyenne gagne 61 à
  69 % de ses trades pour une espérance quasi nulle.
- **Chef d'orchestre** : confier le capital, tous les 6 mois, à la meilleure
  stratégie des 24 derniers mois a fait moins bien que TrendGuard seule sur les
  deux périodes.
- **Le succès se mesure sur la durée** : 80 % des fenêtres de 12 mois finissent
  en gain et 92 % des fenêtres de 24 mois (2019-2026, sans garantie pour
  l'avenir).

### Panneau de contrôle (`python trendguard_bot.py panel`)

Application web locale, ouverte dans le navigateur (VS Code : « TrendGuard —
panneau de contrôle ») :

- **AUTO** démarre l'automatisation du bot, relancé seul s'il plante,
  **ARRÊTER** l'arrête proprement (fin du cycle en cours, état enregistré, stops
  Binance laissés en place) et sans relance. En mode réel, une confirmation est
  demandée ;
- **Actualités** : bandeau qui défile lentement en haut du tableau de bord (il
  s'arrête sous la souris) ; un clic ouvre la page Marchés et actualités :
  capitalisation crypto, dominance du bitcoin, indice Peur & Avidité, indices
  boursiers, or, pétrole, dollar et taux sur un mois, plus fortes hausses et
  baisses des cryptos du bot, et les articles des dernières 48 h (crypto et
  finance, en français et en anglais) avec les cryptos du bot qu'ils citent.
  Sources publiques sans clé : CoinDesk, Cointelegraph, Decrypt, Journal du
  Coin, Cryptoast, CNBC, Le Monde, Google Actualités, CoinGecko, alternative.me,
  Yahoo Finance ;
- **Rachelle, l'assistante** (bouton en bas à droite, sur toutes les pages) :
  fenêtre de dialogue polie et chaleureuse sur les objectifs du bot, les marchés
  crypto et financiers (avec les chiffres du moment), le trading, la connexion
  et la configuration d'un téléphone, l'utilisation de l'interface et l'accès au
  panneau. Réponses intégrées, sans clé ; si une IA de la veille est configurée
  (Claude de préférence), elle rédige les réponses dans le même périmètre, avec
  la même personnalité (`PANEL_ASSISTANT_IA=false` pour s'en passer). Garde-fou
  appliqué avant toute réponse : une clé, un mot de passe, un code ou une phrase
  de récupération collés sont masqués sans jamais quitter la page, avec le
  conseil de les révoquer ; toute demande qui toucherait à la sécurité (révéler
  une clé ou le `.env`, contourner une protection, ouvrir un port, activer les
  retraits, déplacer des fonds) est refusée poliment en une phrase. Rachelle ne
  voit que des données publiques et ne peut rien modifier ;
- **Ce que pense le bot** (tableau de bord) : sa décision du jour expliquée et
  les cryptos proches d'un signal d'achat ;
- **Graphiques** : capital, régime BTC et chaque position en temps réel ; un
  clic ouvre le détail (bougies, volume, achats et ventes, stops, zoom,
  intervalles de 15 min à 1 jour) ;
- **Cryptos** : les 21 paires avec cours, variation, volume, courbe de 48 h et
  la raison du choix du bot, filtres (détenues, surveillées, bloquées) et
  recherche ;
- **Positions**, **Veille**, **Journal** et **Réglages** (démarrage avec
  l'ordinateur, test des alertes, thème clair ou sombre, accès depuis un
  téléphone).

Le panneau lit la base du bot sans la modifier et ne passe aucun ordre
lui-même. Sans bot ni réseau, `--demo` affiche des données fictives.

Compatibilité : Windows, Linux et macOS (Python et un navigateur). Sur un
téléphone Android ou un iPhone connecté au même Wi-Fi : créer le mot de passe
avec `python trendguard_bot.py set-panel-password` (saisie masquée, accès Wi-Fi
proposé), lancer `python trendguard_bot.py panel --host 0.0.0.0` (ou redémarrer
l'ordinateur), ouvrir l'adresse affichée, puis « Ajouter à l'écran d'accueil » :
le
panneau s'ouvre comme une application. Il n'y a pas de fichier APK : il
faudrait publier le panneau sur Internet en HTTPS, ce qui exposerait la
commande du bot.

### Alertes par e-mail et WhatsApp (`alerts.py`)

`python alerts.py configurer` (saisie masquée des mots de passe) puis `python
alerts.py tester`. Les alertes partent sur Telegram, par e-mail (SMTP, par
exemple Gmail avec un mot de passe d'application) et sur WhatsApp (CallMeBot,
gratuit pour un usage personnel, ou Twilio). Par défaut, seules les alertes
critiques (arrêt d'urgence, retrait officiel d'une crypto détenue, alerte forte
de la veille) partent par e-mail et WhatsApp ; `ALERT_LEVEL=all` y ajoute le
résumé quotidien. Un canal en panne ne ralentit jamais le trading.

### Bot autonome, rusé, qui explique ses choix (`autonomy.py`)

**Autonome.** Le bouton AUTO du panneau lance un superviseur (`python
trendguard_bot.py supervise`) qui démarre le bot et le relance tout seul s'il
s'arrête sur une erreur (attente de 10 s, puis 30 s, 1 min… jusqu'à 10 min,
alerte au 3e plantage d'affilée) ou s'il ne donne plus signe de vie pendant 30
min. Avec `python trendguard_bot.py autostart on` (ou l'interrupteur « Démarrer
avec l'ordinateur » des Réglages), le bot et le panneau démarrent à chaque
ouverture de session : clé « Run » de Windows (sans droits administrateur),
services systemd de l'utilisateur sous Linux, LaunchAgents sous macOS. Tant que
le bot tourne, l'ordinateur ne se met pas en veille tout seul
(`TG_KEEP_AWAKE=false` pour l'autoriser ; l'écran peut s'éteindre, fermer le
capot reste possible). ARRÊTER (ou `python trendguard_bot.py stop`) est
respecté : aucune relance, même au prochain démarrage de l'ordinateur, jusqu'au
prochain AUTO.

**Rusé à l'achat, discipliné à la vente.** Avant chaque achat, le bot lit le
carnet d'ordres de Binance. Écart achat/vente supérieur à 0,5 %
(`TG_MAX_SPREAD`), carnet vide, ou moins de 3 fois le montant de l'achat proposé
à moins de 1 % du prix (krach éclair, manipulation, maintenance) : l'achat est
différé et réessayé toutes les 5 minutes pendant 6 heures
(`TG_ENTRY_RETRY_HOURS`), puis abandonné. Un prix momentanément illisible est
traité de la même façon, au lieu de faire perdre l'achat du jour. La taille est
toujours recalculée au prix réel : un achat différé ne risque jamais plus de 1
%. Les ventes, elles, ne sont jamais retardées. Le stop de clôture reste
invisible du marché (aucun ordre posé à ce niveau) et le stop catastrophe est
placé plus bas : une mèche qui « chasse les stops » ne fait pas sortir le bot.

**Il explique ses choix.** Chaque jour, la carte « Ce que pense le bot » du
tableau de bord résume sa décision (régime du marché, achats, ventes, cryptos
proches d'un signal d'achat), et chaque carte de la page Cryptos dit pourquoi le
bot détient, achète ou ignore cette crypto : pas de cassure (avec la hausse
encore nécessaire), tendance de fond négative, trop peu échangée, bloquée par la
veille, plafond de risque atteint. Le filtre « Surveillées » montre les
candidates.

Ce que le bot ne fait pas : changer seul ses règles après quelques résultats.
Les études du dépôt ([`docs/ADAPTATION.md`](docs/ADAPTATION.md),
[`docs/STRATEGIES.md`](docs/STRATEGIES.md)) montrent que ces « adaptations »
font moins bien que les règles fixes sur la période qu'elles n'ont pas vue. Son
intelligence est ailleurs : auto-diagnostic hebdomadaire de son avantage
statistique, veille officielle Binance et avis des IA, revue hebdomadaire par
Claude Code, qui propose les changements par PR sans jamais les appliquer seule.

### Animation du rejeu (`replay_animation.py`)

`python replay_animation.py` rejoue le vrai bot, jour après jour, sur les
clôtures réelles de Binance (depuis le 1er janvier 2025 par défaut), puis
ouvre une page HTML animée : le marché de chaque crypto avec les achats, les
ventes et les stops du bot, le régime BTC, le capital face au BTC conservé, la
décision de chaque jour étape par étape et le portefeuille paper actuel du bot.
Les prix et les décisions sont réels ; les ordres sont simulés (capital fictif
de 10 000 USDT). Aucune clé API, aucun ordre. Options : `--start`, `--end`,
`--capital`, `--out`, `--no-open`.

La page tient en un seul fichier, sans serveur : style, script et données y
sont intégrés. Elle ne fait aucune requête hormis les polices (politique de
sécurité du contenu) et se pilote au clavier : Espace, ← →, Début et Fin, et
les flèches sur un graphique pour lire chaque jour. Le gabarit est dans
`templates/` (HTML, CSS et JavaScript séparés).

En ligne : <https://ncloclo.github.io/Ncloclo/>, régénérée chaque nuit à
00:30 UTC par GitHub Actions (`.github/workflows/pages.yml`) à partir des
données publiques de Binance (`data-api.binance.vision`, sans clé).
Activation unique sur GitHub : Settings ▸ Pages ▸ Source : « GitHub Actions ».
La section du portefeuille paper n'y figure pas : la base du bot reste sur ce
PC.

### Veille de marché par IA (`market_watch.py`)

Chaque jour, pendant que le bot tourne :

1. **Annonces officielles Binance**, lues sans IA : une crypto dont Binance
   annonce le retrait de la cote, ou la suppression de sa paire USDT, n'est
   plus achetée pendant 90 jours. La veille ne vend jamais une position
   détenue : une alerte conseille de la vendre avant la date du retrait.
2. **Actualités** (Google Actualités, CoinDesk, Cointelegraph, Decrypt), indice
   Fear & Greed et parité USDC/USDT.
3. **Analyse par les IA, en parallèle** : Claude, GPT, Gemini, DeepSeek,
   Mistral, Kimi, Perplexity (recherche web en direct) et Grok, selon les clés
   présentes dans `.env`. Un événement doit citer une source réellement
   collectée qui nomme la crypto ; une alerte demande l'accord d'au moins deux
   IA.
4. **Mémoire** : les résumés des 7 derniers jours sont redonnés aux IA, et
   l'avis de chaque IA est comparé au cours réel 7 jours plus tard. Son poids
   dans le consensus suit sa fiabilité mesurée, après 30 avis vérifiés.

Aucune IA ne passe d'ordre ni ne bloque un achat : un message manipulateur
publié sur un forum n'a aucun effet sur le trading. Le rapport s'affiche dans
le journal (`[VEILLE]`), part sur Telegram en cas d'alerte et apparaît dans
`diagnose`.

```bash
python market_watch.py set-key claude    # clé d'IA en saisie masquée
python market_watch.py check             # teste chaque IA configurée
python market_watch.py                   # rapport du jour
python market_watch.py --no-ai           # sans IA (mots-clés seulement)
```

Sans clé d'IA, la veille fonctionne quand même : annonces officielles et
mots-clés. Chaque IA facture ses appels, à raison d'un rapport par jour. Reddit
refuse les requêtes sans application enregistrée : les réseaux sociaux et les
forums passent par la recherche web de Perplexity.

### Revue hebdomadaire par Claude Code

Chaque lundi, un agent Claude Code dans le cloud relit la veille, le
diagnostic et le laboratoire des stratégies, fait ses propres recherches sur
le web, revoit le code, puis ouvre une Pull Request avec un rapport en
français (`docs/revues/`). Il ne fusionne jamais : vous décidez. Il ne change
la stratégie que si une variante bat TrendGuard sur 2018-2022 **et** sur
2023 à aujourd'hui, jamais le risque de 1 % par trade.

Son environnement n'a pas accès à Binance : GitHub Actions
(`.github/workflows/donnees.yml`) prépare ses données le lundi à 00:40 UTC
sur la branche `donnees` (veille, diagnostic, laboratoire, historique
Binance). Réglages de l'agent : <https://claude.ai/code/routines>.

### Utilisation

```bash
pip install -r requirements.txt
python trend_strategy.py download --data data        # historique Coin Metrics
python trend_strategy.py research --data data         # régénère le rapport
python trendguard_bot.py docs                         # variables d'environnement
RUN_MODE=paper python trendguard_bot.py run           # paper, prix réels Binance
python trendguard_bot.py replay --data data --start 2025-06-01   # paper rejoué sur l'historique réel
python trendguard_bot.py status                       # état du portefeuille
python trendguard_bot.py diagnose                     # auto-diagnostic complet (lecture seule)
python trendguard_bot.py verify                       # sans clé : test réel des ordres du jour, sans envoi
python trendguard_bot.py set-keys                     # clés API vérifiées par Binance, saisie masquée
python strategy_lab.py --cache data_binance           # tournoi des stratégies + méta-apprentissage
python replay_animation.py                            # animation du bot sur les prix réels Binance
python trendguard_bot.py panel                        # panneau de contrôle (navigateur)
python trendguard_bot.py set-panel-password           # accès depuis un téléphone (saisie masquée)
python trendguard_bot.py supervise                    # bot relancé seul en cas de plantage
python trendguard_bot.py autostart on                 # démarrage avec l'ordinateur (off : retiré)
python trendguard_bot.py stop                         # arrêt propre, sans relance
python alerts.py configurer                           # alertes e-mail et WhatsApp
```

### Dans VS Code

1. Ouvrez le dossier du projet, puis installez l'extension **Python** (proposée
   automatiquement).
2. Lancez `Terminal ▸ Exécuter la tâche… ▸ Installer les dépendances`.
3. Copiez `.env.example` en `.env`. Les réglages par défaut conviennent pour le
   mode paper.
4. Ouvrez `Exécuter et déboguer`, choisissez une configuration, puis appuyez sur
   **F5** :
   - **TrendGuard — paper (prix réels Binance)** : le bot en continu, lancé
     sans débogueur. Sous débogueur, une exception ou un point d'arrêt le met
     en pause sans prévenir ;
   - **TrendGuard — un seul cycle** : une décision, puis arrêt ;
   - **TrendGuard — rejeu paper 12 mois** : télécharge l'historique et le
     rejoue ;
   - **TrendGuard — animation du rejeu** : page animée du bot sur les prix
     réels Binance, ouverte dans le navigateur ;
   - **TrendGuard — statut du portefeuille** ;
   - **Tests (pytest)**.

Live, testnet d'abord :

```bash
BINANCE_TESTNET=true RUN_MODE=live ENABLE_LIVE_TRADING=true \
LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_RISK \
BINANCE_API_KEY=… BINANCE_API_SECRET=… python trendguard_bot.py run
```

Compte réel :

1. Créez la clé API sur Binance **sans droit de retrait**, limitée à l'adresse
   IP du serveur.
2. Enregistrez l'API Key et la Secret Key par saisie masquée :
   `python trendguard_bot.py set-keys`. Dans VS Code, c'est la tâche
   « Binance — enregistrer les clés API ». Binance vérifie d'abord les clés
   (lecture du compte, aucun ordre) : des clés inversées ou appartenant au
   testnet sont corrigées automatiquement, et rien n'est écrit si Binance les
   refuse. Les clés ne passent ni par l'écran ni par l'historique du terminal.
   `BINANCE_TESTNET` ne concerne que le mode réel : le paper suit toujours le
   vrai marché.
3. Lancez `python trendguard_bot.py verify` : droits de la clé (retrait
   interdit, trading autorisé), soldes, validation des ordres par Binance et
   simulation des achats du jour. **Aucun ordre n'est passé.** Sans clé, la
   commande fait déjà un test réel en lecture seule : connexion, règles
   Binance des 21 paires, et ordres du jour construits aux pas de quantité et
   de prix réels (capital simulé : `TG_MAX_CAPITAL` ou `TG_PAPER_CAPITAL`).
4. Fixez `TG_MAX_CAPITAL`, le capital en USDT confié au bot. Le bot gère alors
   un sous-compte virtuel (ce plafond, plus ses propres gains et pertes), quel
   que soit le solde réel du compte. Le kill-switch s'applique à ce capital.

Le bot prend ses décisions une fois par jour, juste après la clôture de 00:00
UTC. Il appelle **exactement les mêmes fonctions** que le backtest. Un test
vérifie la parité exacte : mêmes trades, même PnL, même equity.

S'il a été arrêté plusieurs jours, le bot rattrape au redémarrage les clôtures
manquées : il réévalue chaque stop jour par jour (trailing compris) et vend au
prix actuel toute position dont le stop a été franchi pendant l'arrêt. Il ne
prend aucune entrée sur un jour passé.

Chaque position est protégée à deux niveaux :

- le **stop de clôture** de la stratégie, qui fixe le risque de 1 % ;
- un **stop catastrophe** `STOP_LOSS` posé sur Binance, 1 × volatilité plus
  bas, remonté avec le trailing. Il protège d'un krach entre deux clôtures.

Au-delà de 40 % de drawdown, un kill-switch bloque les entrées
(`TG_KILL_DRAWDOWN`). Pour le lever, arrêtez le bot puis lancez
`python trendguard_bot.py resume` : la commande est refusée tant que le bot
tourne, car il réécrirait son état au cycle suivant.

**Une seule instance par compte Binance.** Un verrou empêche deux bots de
partager la même base sur une même machine. Il ne peut rien contre deux
machines ou deux copies du projet. En mode réel, le bot refuse donc de
démarrer s'il trouve sur le compte des ordres à son nom qu'il ne connaît pas :
une autre instance tourne peut-être. Si c'est votre base qui a été perdue,
relancez une seule fois avec `TG_ALLOW_RECOVERY=true` pour reprendre ces
positions.

### Heure du bot = heure de Binance

Le bot ne se fie pas à l'horloge du PC. Il mesure l'écart avec l'heure du
serveur Binance au démarrage, puis toutes les heures (en paper comme en réel),
et vit à l'heure de Binance pour :

- la décision quotidienne et la clôture des bougies (00:00 UTC chez Binance) ;
- l'horodatage des ordres signés : Binance refuse ceux qui s'écartent de plus
  de 10 s. Un ordre refusé pour cette raison (-1021) est renvoyé une fois
  après recalage, avec le même identifiant ;
- les dates enregistrées et les journaux, millisecondes comprises.

La mesure compare l'heure du serveur au milieu de l'aller-retour réseau et
retient la requête la plus rapide. L'erreur reste sous un demi aller-retour
(± 0,2 s sur une connexion à 430 ms). La méthode de ccxt, elle, se trompe
d'un demi aller-retour de plus. Le démarrage et chaque battement de cœur
affichent l'écart, par exemple `heure Binance (PC en retard de 1,3 s sur
Binance)`.

Régler l'horloge du PC reste conseillé. Si le service de temps Windows est
arrêté, lancez ces commandes dans un PowerShell **administrateur** :

```powershell
sc.exe config w32time start= auto
net start w32time
w32tm /config /manualpeerlist:"time.windows.com,0x9" /syncfromflags:manual /update
w32tm /resync /force
```

### Pannes réseau

- Chaque bougie journalière est demandée trois fois avant d'être déclarée
  indisponible.
- Si les données d'une crypto **détenue** manquent, la décision du jour est
  reportée au cycle suivant, au lieu de vendre la position comme si elle avait
  été retirée de la cote. Le stop catastrophe posé sur Binance reste actif. Une
  notification part si le blocage dure plus d'une heure.
- Une paire injoignable ne bloque pas la surveillance des autres positions.
- Si un cycle reste bloqué plus de 20 minutes, la pile de chaque thread est
  écrite dans `<journal>.blocage.txt` (par exemple
  `trendguard_paper.log.blocage.txt`),
  pour savoir où le bot s'est arrêté. `diagnose` signale un bot qui tourne sans
  réussir ses cycles.

## Déploiement (Docker)

```bash
git clone https://github.com/Ncloclo/Ncloclo.git && cd Ncloclo
git checkout claude/v29-5-hybrid-bot-fz9gvm
cp .env.example .env              # paper par défaut ; éditer pour le live
docker compose up -d --build      # construit l'image (tests inclus) et démarre
```

| Action | Commande |
| --- | --- |
| Suivre le bot (une ligne `[HEARTBEAT]` toutes les 15 min) | `docker compose logs -f` |
| État du portefeuille | `docker compose exec trendguard python trendguard_bot.py status` |
| Santé (`healthy` / `unhealthy`) | `docker compose ps` |
| Mettre à jour | `git pull && docker compose up -d --build` |
| Arrêter (état conservé) | `docker compose down` |
| Lever le kill-switch | `docker compose stop && docker compose run --rm trendguard resume && docker compose start` |
| Repartir de zéro (efface le portefeuille paper) | `docker compose down -v` |

- L'image ne se construit que si toute la suite de tests passe.
- Le conteneur redémarre tout seul après un crash ou un redémarrage du serveur.
  L'état (base SQLite, logs) est conservé dans le volume `trendguard-data`.
- Docker considère le bot en mauvaise santé (`unhealthy`) si aucun cycle n'a
  réussi depuis 10 minutes, ou si le kill-switch est déclenché.
- Serveur conseillé : un petit VPS allumé en permanence (1 vCPU, 1 Go de RAM ;
  le bot utilise environ 230 Mo), situé dans un pays où Binance n'est pas
  restreint. Binance refuse notamment les adresses IP des États-Unis.

## Moteur d'exécution (commun, `v29.py`)

- Une position live n'est jamais laissée sans protection exchange : la
  protection est revérifiée à chaque cycle, avec un stop logiciel en dernier
  recours.
- Aucune vente marché n'est envoyée tant que l'annulation de la protection
  n'est pas confirmée. Chaque jambe est relue après annulation.
- Chaque exécution est comptée une seule fois (registre par `order_id`).
- Chaque ordre est précédé d'une intention persistée, résolue par client-id
  après un crash ou un timeout.
- Les quantités sont plafonnées au solde réel, et les frais prélevés en base
  sont pris en compte.
- La reconciliation au boot est *fail-closed*.

Le bot V29.6 intraday reste disponible : `python v29.py bot | backtest |
walkforward | status | resume` (voir `python v29.py docs`).

## Tests

```bash
python -m pytest tests -q      # 357 tests, simulateurs Binance Spot mono et multi-paires
```

À chaque envoi sur GitHub, `.github/workflows/checks.yml` lance ces tests, puis
vérifie la page d'animation et le panneau de contrôle dans Chromium : ESLint sur
les scripts et tests Playwright (`tests/web/`) sur une page d'exemple et le
panneau en démonstration, sans réseau.

## Limites connues

- Un GitHub Codespace s'arrête après une période d'inactivité, et le bot avec
  lui : le rattrapage limite les dégâts, mais les stops ne sont plus surveillés
  pendant l'arrêt. Pour faire tourner le bot en continu, utilisez Docker sur un
  serveur allumé en permanence (voir « Déploiement »).
- Les simulateurs reproduisent les mécanismes de risque de Binance Spot :
  soldes bloqués, frais en base, OCO (l'autre jambe expire dès une exécution
  partielle), carnet à profondeur finie, filtres de prix, de quantité et de
  nombre d'ordres, erreurs à statut inconnu (-1001, 503) et courses entre deux
  appels API. Ils ne remplacent pas le **testnet**, seul moyen de valider les
  vraies réponses de l'API avec des clés.
- La recherche utilise les clôtures USD de Coin Metrics, alors que le bot live
  utilise les clôtures USDT de Binance. Les deux séries sont très proches, sans
  être identiques.
- Les résultats dépendent pour une part notable de quelques actifs (ZEC, XRP,
  BTC sur 2023-2026). Sans ces trois-là, la stratégie reste positive mais tombe
  à +16 % par an. Gardez donc un univers large.
