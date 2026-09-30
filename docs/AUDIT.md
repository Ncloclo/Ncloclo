# Audit et diagnostic expert de TrendGuard — 28 au 30 septembre 2026

Trois diagnostics successifs : l'audit du 28, le diagnostic approfondi du 29
(disponibilité, robustesse, alertes) et celui du 30 (alimentation, bibliothèques
du PC, état en direct). Méthode : diagnostic de la stratégie sur les données
publiques de Binance (`python trendguard_bot.py diagnose`), état du bot dans le
panneau, journal du bot, du superviseur et de Windows, vérification Binance
(`verify`, aucun ordre), tests, analyse statique, failles connues des
dépendances (pip-audit), recherche de secrets dans git.

## Verdict

**Le bot, sa stratégie et son code sont sains ; ce qui l'entoure ne l'est pas
encore.** Au 30 septembre, la stratégie est conforme (27 contrôles sur 27), le
journal ne contient aucune erreur du bot, les tests et les contrôles de GitHub
sont au vert. Trois faiblesses restent autour du bot : le PC portable (batterie,
veille, arrêts : 65 % de disponibilité), les alertes qui n'arrivent pas et la
clé Binance refusée. La quatrième, des bibliothèques Python en retard sur le PC,
a été corrigée le jour même : le bot a maintenant les siennes, aux versions
testées. Le bot n'est pas prêt pour de l'argent réel : il lui faut des alertes
qui arrivent, une machine allumée en permanence avec une adresse fixe, et
plusieurs semaines de paper avec des trades vendus.

## Diagnostic approfondi du 30 septembre

**Verdict du jour : rien à corriger dans la stratégie ni dans le code ; deux
faiblesses matérielles (alimentation, veille) et un angle mort.** Méthode : les
649 lignes du journal du bot et les 116 du superviseur relues une à une, journal
de Windows (veilles, réveils, démarrages), processus, ports et mémoire en
direct, diagnostic complet de la stratégie, rapport quotidien (38 contrôles),
mesures du code (`python -m research.diagnostic_code`), contrôles GitHub, audit
des bibliothèques installées sur le PC, comparaison au marché. Heures en UTC.

### État du bot (30/09, 10 h)

| Mesure | Valeur |
| --- | --- |
| Mode | paper, depuis le 26 septembre à 22 h 53 (3,5 jours) |
| Capital | 10 076,50 USDT (+0,77 %) ; pire baisse relevée −3,1 % (plus bas 9 692, plus haut 10 292) |
| Marché sur la même période | BTC −0,6 % ; moyenne des 21 cryptos −2,0 % ; moyenne des 6 détenues +2,0 % |
| Positions | 6 sur 8 : ICP +7,7 %, XLM +5,4 %, AAVE +4,4 %, LINK +2,0 %, ADA −1,7 %, LTC −7,0 % |
| Prochaine clôture | LTC à 0,9 % de son stop : vente possible ce soir (36 %), ce serait le premier trade clos, environ −1 R |
| Risque engagé | 6 % sur 6 % permis : aucun achat possible (DOT signalé, bloqué) |
| Décisions de clôture | 3 sur 4 à l'heure (entre 00 h 02 et 00 h 04) ; celle du 27 avec 16 h de retard (PC éteint) |
| Journal | 0 erreur du bot ; coupures de réseau par salves, toutes rattrapées (29/09 : 12 h 29 à 12 h 41, 15 h 04 à 16 h, 23 h 15) |
| Réseau et horloge | latence vers Binance 410 ms ; horloge du PC en retard de 1,0 s, compensée |
| Processus | bot 0,1 % d'un cœur et 89 Mo ; panneau 1,8 % et 80 Mo ; ports ouverts sur ce PC seulement (127.0.0.1) |

Trois jours et demi ne disent rien de la stratégie : le bot fait mieux que le
marché sur la période, mais sans aucun trade vendu, cela ne prouve rien.

### Constats

| N° | Constat | Gravité | Suite |
| --- | --- | --- | --- |
| D1 | **Le portable tournait sur batterie** : de 90 % à 43 % en 1 h 45 pendant l'analyse. Batterie vide, le PC s'éteint et le bot s'arrête | **Critique** | **Chargeur rebranché pendant l'analyse** (batterie à 43 %). À garder branché : le tableau de bord, le centre de sécurité et le rapport signalent désormais un PC sur batterie |
| D2 | **Disponibilité : 65 % sur 7 jours** (84 % sur 24 h). Causes relevées dans le journal de Windows : PC éteint la nuit du 26 au 27 (16,7 h), mises en veille par le capot ou le bouton d'alimentation (29/09 à 21 h 33, 30/09 à 07 h 02), arrêt le 29 au matin | Élevée | **Corrigé en partie le 30/09** : Windows cachait le réglage du capot, le bot ne le voyait pas ; capot fermé sur secteur = « ne rien faire ». Reste à vous : PC branché ; ne pas appuyer sur le bouton d'alimentation (il met en veille) |
| D3 | **Aucune alerte ne vous parvient** : Gmail refuse le mot de passe (il faut un mot de passe d'application), WhatsApp et Telegram ne sont pas configurés. Deux alertes critiques sont restées dans le journal (arrêts du 29 et du 30). La fenêtre « Alertes — configurer » est ouverte depuis 01 h 45, sans saisie | Élevée | `python trendguard_bot.py alerts configurer`, puis « Tester » dans Réglages |
| D4 | **Clé Binance refusée** (erreur −2015). L'adresse du PC sur Internet change (102.209.218.110 à la première vérification, 160.120.68.43 le 30) : une clé limitée à une adresse ne peut pas tenir sur cette connexion | Aucune en paper ; bloquante pour le réel | Pour le réel : une machine à adresse fixe (petit serveur), qui règle aussi D2 |
| D5 | **Bibliothèques Python du PC en retard** sur celles que GitHub teste : pandas 2.3.3 (testée : 3.0.6), ccxt 4.5.44 (4.5.84), numpy 2.4.3 (2.5.3). Et 15 paquets installés sur 226 ont des failles connues, dont 6 utilisés par le bot : aiohttp, cryptography, requests, urllib3, anyio, setuptools | Moyenne : le bot n'écoute que sur ce PC, mais il parle à Binance avec ces bibliothèques | **Corrigé le 30/09** : le bot a ses propres bibliothèques, aux versions testées (voir « Suite donnée ») |
| D6 | **Angle mort du rapport quotidien** : « tests, qualité et sécurité au vert » décrit les contrôles de GitHub, faits avec des bibliothèques à jour ; celles du PC ne sont pas contrôlées | Moyenne | **Corrigé le 30/09** : deux contrôles ajoutés au rapport (voir « Suite donnée ») |
| D7 | Apprentissage : erreur de prévision 0,002 sur 51 prévisions. Le chiffre ne dit encore rien : aucune vente ni aucun achat n'a eu lieu depuis qu'il mesure | Information | Jugé à partir de 100 prévisions, comme prévu |
| D8 | Évolution encadrée : niveau 1, 10 réglages essayés chaque nuit, aucun adopté | Information | Le garde-fou tient : rien ne change sans réussir toutes les épreuves |
| D9 | Sauvegardes de la base sur le même disque que la base (2 jours gardés) | Faible en paper | Avant le réel : une copie hors du PC |
| D10 | Dépôt GitHub public : le code est visible de tous | Information | Aucun secret dedans (vérifié) ; base, journaux, rapports et `.env` ne sont jamais publiés |
| D11 | Proposition n° 3 en attente sur GitHub ; démonstration encore ouverte sur le port 8799 (126 Mo) | Faible | Fusionner ou fermer la proposition ; fermer la démonstration quand elle ne sert plus |
| D12 | Hors du bot : sur ce PC, les logiciels qui passent par `aiohttp` avec `aiodns` (freqtrade, par exemple) ne trouvent pas les serveurs DNS et ne joignent pas Binance. Vérifié avant et après les mises à jour du 30/09 : elles n'y changent rien | Information : le bot n'utilise pas ce chemin | À traiter seulement si vous utilisez ces logiciels sur ce PC |

### Suite donnée le 30 septembre (D5 et D6)

Mettre tout le PC aux versions testées aurait cassé d'autres logiciels installés
(freqtrade, vectorbt, numba), qui exigent pandas 2 et numpy avant 2.5. Le bot a
donc reçu son propre jeu de bibliothèques, dans le dossier `.venv` à côté du
code ; les autres logiciels gardent les leurs.

| Mesure | Avant | Après |
| --- | --- | --- |
| Bibliothèques du bot | celles du PC : ccxt 4.5.44, numpy 2.4.3, pandas 2.3.3 | les siennes, aux versions testées : ccxt 4.5.84, numpy 2.5.3, pandas 3.0.6 |
| Failles connues dans ce que le bot utilise | 6 bibliothèques | aucune (70 bibliothèques contrôlées) |
| Tests sur le PC avec ces bibliothèques | jamais lancés avec les versions testées | 493 réussis, aucun échec |
| Bibliothèques du PC (autres logiciels) | 15 sur 226 avec une faille connue | 1 sur 226 : 14 corrections compatibles installées ; les logiciels qui en dépendent se chargent comme avant (73 modules essayés avant et après) |
| Rapport quotidien | ne contrôlait pas les bibliothèques du PC | deux contrôles : « Bibliothèques du bot » (versions testées) et « Failles connues des bibliothèques » |

Le bot et le panneau ont été relancés à 12 h 06 avec ces bibliothèques (moins
d'une minute d'arrêt). Toute commande `python trendguard_bot.py …` passe
d'elle-même par le dossier `.venv`, le démarrage avec l'ordinateur aussi ; si ce
dossier disparaît, le bot tourne avec les bibliothèques du PC et le rapport le
signale.
Une bibliothèque du PC garde une faille connue, sans effet sur le bot :
`curl-cffi`, retenue par yfinance, qui n'accepte pas la version corrigée. La
liste des versions d'avant est gardée dans le dossier `sauvegardes/` : tout peut
être remis comme avant.

### Stratégie (clôture du 29, données Binance) : tout est conforme

| Mesure | Valeur |
| --- | --- |
| Backtest 2019 → 2026 (réglages actuels) | +33,5 % par an, pire baisse −24,7 %, Sharpe 1,19, 313 trades, 41 % gagnants, +1,12 R par trade |
| 12 derniers mois | +33,3 % (percentile 64 de l'historique) |
| 79 trades des 24 derniers mois | +1,19 R en moyenne (intervalle à 90 % : +0,37 à +2,11 R) |
| Marché | BTC haussier depuis 42 jours, +18,1 % au-dessus de sa moyenne 150 jours ; volatilité calme (41 % par an) |
| Portefeuille | perte si tous les stops sont touchés : 6,2 % du capital ; positions liées (corrélation 0,61) |
| Krach sans exécution des stops | −20 % : −13,2 % du capital ; −35 % : −23,0 % |
| Tournoi des 7 stratégies sur 2 ans | TrendGuard 3e ; écart faible avec les deux premières, aucun motif de changer |

### Code

64 modules Python, 23 510 lignes ; 491 tests Python et 18 tests navigateur,
tous au vert sur GitHub pour la version installée ; ruff : aucun problème ;
aucune fonction publique longue sans explication, aucun code inutilisé hors du
moteur `v29`, dépendances dans un seul sens (`tests/test_structure.py`). Détail
dans [`DIAGNOSTIC_CODE.md`](DIAGNOSTIC_CODE.md). Le même jour, le panneau réel a
été rendu aussi rapide que la démonstration (0,01 s par page au lieu de 1,5 à
7 s).

### Prêt pour l'argent réel ? Non, pas encore

1. des alertes qui arrivent (D3) ;
2. une machine allumée en permanence, à adresse fixe (D2, D4) ;
3. une clé Binance acceptée, avec le droit de trading et sans retrait (D4) ;
4. des bibliothèques à jour sur la machine qui tourne (D5) : fait le 30/09 ;
5. 10 à 20 trades vendus en paper, conformes à l'attendu, puis quelques jours
   de testnet.

## Diagnostic approfondi du 29 septembre

**Verdict du jour : la stratégie est robuste, mais le bot n'a tourné que 58 %
du temps.** Le point faible n'est ni la stratégie ni le code : c'est le PC,
éteint ou en veille près de la moitié du temps depuis le démarrage du bot.
Méthode : journal du bot et du superviseur minute par minute, journal de
Windows, nouvelle étude de robustesse sur l'historique Binance
([`ROBUSTESSE.md`](ROBUSTESSE.md)), test réel des alertes. Les heures sont
celles du PC, réglé sur UTC.

### Disponibilité du bot (26/09 22 h 53 → 29/09 13 h 24)

| Arrêt | Durée | Ce qui s'est passé |
| --- | --- | --- |
| 26/09 23 h 43 → 27/09 16 h 23 | 16,7 h | PC éteint ou en veille la nuit : la décision de la clôture du 26 n'a été prise qu'à 16 h 23, avec 16 h de retard (rattrapée au redémarrage) |
| 27/09 16 h 39 → 18 h 12 | 1,5 h | PC éteint ou en veille |
| 28/09 09 h 06 → 10 h 30 | 1,4 h | PC en veille : bot figé, relancé par le superviseur au réveil (« aucun signe de vie depuis 74 min ») |
| 28/09 20 h 54 → 23 h 41 | 2,8 h | PC en veille (« aucun signe de vie depuis 156 min ») |
| 29/09 07 h 17 → 10 h 53 | 3,6 h | PC en veille ou éteint (réveil bref à 9 h 09), redémarré à 10 h 41 |

Au total **26 heures d'arrêt sur 62,5 : 58 % de disponibilité**. Pendant ces
arrêts, en paper, le stop catastrophe n'est pas surveillé et les décisions de
clôture attendent le retour du PC. En réel, le stop catastrophe posé chez
Binance protégerait quand même, mais la règle principale (vente à la clôture
sous le stop suiveur) serait appliquée en retard. Depuis le démarrage, deux
décisions sur trois ont été prises à l'heure (00 h 02 et 00 h 03), une avec 16 h
de retard. L'anti-veille du bot empêche seulement la mise en veille
automatique : elle ne peut rien contre le capot fermé, la mise en veille
manuelle, l'arrêt ou le redémarrage du PC. S'y ajoutent de
courtes coupures d'Internet (quelques minutes, plusieurs fois par jour), que le
bot rattrape seul.

Corrigé le 29/09 (plan, ligne 9) : le bot note désormais chaque arrêt de plus
d'une heure et sa cause, envoie une alerte critique quand il n'a pas été
demandé, et le panneau affiche le temps de marche sur 24 h et 7 jours
(Réglages ▸ Autonomie, centre de sécurité).

### Robustesse de la stratégie ([`ROBUSTESSE.md`](ROBUSTESSE.md))

- **Coûts** : rentable même avec des frais et un glissement triplés (+31,1 %
  puis +27,5 % par an).
- **Réglages** : les 27 combinaisons voisines (cassure, stops) sont rentables
  sur les deux périodes : un plateau, pas un réglage chanceux.
- **Gagnants** : ZEC, XRP et ADA font 50 % des gains ; même sans elles, retirées
  après coup, le bot reste rentable (+31,9 % puis +21,2 % par an).
- **Hasard** (3 ans rejoués 5 000 fois par blocs de 30 jours) : résultat médian
  +155 %, 4 % de chances de finir en perte, pire baisse médiane −26 % et −41 %
  une fois sur 20 ; séries de 8 à 12 trades perdants de suite.
- **Année par année** : une seule année en perte (2022, −9 %, marché baissier) ;
  pire mois −13,2 % (janvier 2024).

### Alertes

L'e-mail est configuré mais aucune alerte n'est encore arrivée : le serveur
indiqué était « pop3 » (corrigé en `smtp.gmail.com`) et Gmail refuse le mot de
passe habituel (il faut un mot de passe d'application). Défaut trouvé : le
centre de sécurité affiche « Alertes » en vert dès qu'un canal est configuré,
même si ses envois échouent. Corrigé le 29/09 (plan, ligne 10) : « Alertes »
passe à corriger quand le dernier envoi d'un canal a échoué, avec la cause.

### Résultats du paper (3 jours)

Capital 10 250 USDT (+2,5 %), aucun trade vendu : AAVE +12 %, LINK +8 %, XLM
+7,5 %, ICP +4 %, ADA 0 %, LTC −5 %. Trois jours ne disent rien de la
stratégie : il faut 10 à 20 trades vendus pour comparer à l'attendu.

## 1. État du bot (28 septembre, 19 h)

| Mesure | Valeur |
| --- | --- |
| Mode | paper (argent fictif, prix réels), depuis le 26 septembre à 22 h 53 |
| Capital | 10 008,70 USDT (+0,09 % depuis le départ), baisse depuis le plus haut −0,09 % |
| Positions | 6 sur 8 : AAVE −4,0 %, ADA −1,8 %, ICP −4,8 %, LINK +7,6 %, LTC −4,2 %, XLM +7,0 % |
| Trades clos | aucun pour l'instant : trop tôt pour juger le bot sur ses propres résultats |
| Risque engagé | 5,99 % sur 6 % permis : aucun nouvel achat possible (DOT signalé, bloqué) |
| Pire cas ce soir | −5,7 % du capital si tous les stops étaient touchés |
| Cryptos achetables | sélection auto (21) ; 17 en pratique : ETC, NEO, XTZ, ALGO trop peu échangées sur Binance |
| Autonomie | relance automatique active (aucun plantage), démarrage avec l'ordinateur actif |
| Journal | 0 erreur ; 36 délais réseau vers Binance en 2 jours, tous rattrapés au cycle suivant |
| Horloge du PC | 2,3 s de retard sur Binance, compensé : le bot utilise l'heure de Binance |

Un blocage de 20 minutes a eu lieu le 27 septembre à 16 h 59 (écriture dans une
fenêtre de console figée). Il ne s'est pas reproduit depuis que le bot tourne
sans console, sous la supervision du panneau.

## 2. Stratégie

Diagnostic du 28 septembre (clôture du 27, données Binance) : **tout est
conforme**.

| Mesure | Valeur |
| --- | --- |
| Backtest 2019 → 2026 (réglages actuels) | +34,3 % par an, pire baisse −24,7 %, Sharpe 1,21, 313 trades, 41 % gagnants |
| 79 trades des 24 derniers mois | +1,19 R en moyenne (intervalle à 90 % : +0,37 à +2,11 R) |
| 12 derniers mois | +39,1 % (percentile 69 de l'historique) |
| Chance historique de finir en gain | 81 % sur 12 mois, 92 % sur 24 mois, 100 % sur 36 mois (indication, pas une garantie) |
| Marché | BTC haussier depuis 40 jours, +19,4 % au-dessus de sa moyenne 150 jours ; volatilité calme |
| Tournoi des 7 stratégies sur 2 ans | TrendGuard 3e ; « Tendance lente » et « Tendance rapide » devant |

| N° | Constat | Commentaire |
| --- | --- | --- |
| R1 | Positions très liées entre elles (corrélation moyenne 0,64) | Un krach instantané de −20 % sans exécution des stops coûterait −13,1 % du capital ; de −35 %, −22,9 %. C'est le vrai risque des cryptos : les stops limitent les baisses progressives, pas les chutes brutales |
| R2 | Tournoi : deux variantes de tendance devant TrendGuard sur 2 ans | Pas un motif de changement : une variante doit battre TrendGuard sur 2018-2022 ET sur 2023 → aujourd'hui (`docs/STRATEGIES.md`). La revue du lundi surveille ce classement |
| R3 | Biais du survivant | Les cryptos disparues manquent en partie à l'historique : prévoir en réel un peu moins que les chiffres ci-dessus |
| R4 | Sélection manuelle des 10 plus rentables | +15,5 % par an de 2023 à 2026 contre +37,2 % avec les 21 (`docs/SELECTION.md`) : la sélection auto reste la bonne |
| R5 | Arrêt d'urgence à −40 % | Profond, mais le profil prudent divise le risque par 2 dès −10 % |

## 3. Sécurité

| N° | Constat | Gravité | Recommandation |
| --- | --- | --- | --- |
| S1 | Clés montrées dans une conversation : l'ancienne clé, puis la nouvelle clé API (collée plusieurs fois ; son secret, lui, n'a pas été montré) | **Élevée avant le réel** | Supprimer ces clés sur Binance et en créer une neuve, saisie avec `set-keys` |
| S2 | La clé enregistrée n'a pas le droit « Trading Spot » | Aucune en paper | Le mode réel est impossible tant que ce droit n'est pas coché : bien pour l'instant |
| S3 | Pas de restriction d'adresse IP sur la clé | Moyenne | L'ajouter sur Binance avant le réel |
| S4 | En paper, les clés du `.env` étaient transmises à Binance : une clé supprimée aurait pu empêcher le bot de redémarrer | Moyenne | **Corrigé** : en paper, aucune clé n'est transmise (test ajouté) |
| S5 | Accès téléphone en HTTP : le mot de passe circule en clair sur le Wi-Fi | Moyenne | Wi-Fi privé seulement ; à distance, VPN (Tailscale) |
| S6 | bandit : 57 signalements (1 « élevé », 11 « moyens ») | Aucun réel | Vérifiés : SHA-1 pour éviter les doublons d'alertes, adresses web fixes en HTTPS, XML des actualités (Python 3.13 bloque les « bombes XML »), écoute Wi-Fi volontaire |

Déjà en place : retrait interdit sur la clé (vérifié par `verify` auprès de
Binance), clés en saisie masquée, fichier `.env` jamais envoyé sur GitHub,
aucun secret dans l'historique git, blocage de 5 min après 5 mots de passe
ratés, cookie `HttpOnly` et `SameSite=Strict`, contrôles d'origine et d'hôte,
garde-fou de Rachelle, aucune faille connue dans les dépendances (pip-audit).

## 4. Exploitation

| N° | Constat | Recommandation |
| --- | --- | --- |
| O1 | **Aucune alerte configurée** : ventes, alertes d'anticipation, arrêt d'urgence et plantages répétés ne vous parviennent pas | `python trendguard_bot.py alerts configurer` (5 minutes), puis `alerts tester` |
| O2 | Le bot dépend d'un PC portable : veille, capot fermé, coupure de courant ou mise à jour de Windows l'arrêtent | Branché sur secteur, ouverture de session automatique, ou un petit serveur allumé en permanence (`docker-compose.yml` prêt) |
| O3 | Horloge du PC en retard de 2,3 s | Sans effet sur le bot ; activer la synchronisation de Windows (README, « Heure du bot ») |
| O4 | Compte Binance : 14,90 USDT (en TRX) | Le mode réel demande au moins 100 USDT : sans objet tant que le bot est en paper |
| O5 | `docker-compose.yml` ne lance pas le panneau | Reporté, à concevoir avec le passage sur serveur |

## 5. Code

| Mesure | Valeur |
| --- | --- |
| Code Python | 18 600 lignes : `trendguard/` 7 550, `v29/` 5 170, ancien bot `v29/intraday/` 2 870, `panel/` 2 620, `research/` 310 |
| Interface web | 2 410 lignes (HTML, CSS, JavaScript) |
| Tests | 411 tests Python et 16 tests navigateur (Playwright), tous au vert |
| Couverture des tests | 77 % des lignes (75 % ce matin) |
| Analyse statique | ruff : 0 ; ESLint : 0 |
| Dépendances | pip-audit : aucune faille connue ; vérifié à chaque envoi sur GitHub |

Couverture des parties qui comptent le plus :

| Module | Couverture | Commentaire |
| --- | --- | --- |
| `trendguard/bot.py` | 91 % | le bot lui-même |
| `trendguard/anticipation.py`, `selection.py`, `config.py`, `replay.py` | 95 à 96 % | |
| `trendguard/trend_strategy.py` | 66 % | règles 100 % testées ; téléchargement et rapport de recherche non testés |
| `trendguard/cli.py` | 67 % | commandes `verify` et `diagnose` en partie |
| `v29/execution.py`, `exchange.py` | 77 % | chemins de secours des ordres réels testés ce matin (10 cas) |
| `v29/risk.py`, `models.py` | 89 %, 90 % | |
| `panel/server.py`, `assistant.py` | 86 %, 93 % | |
| `panel/data.py` | 69 % | lecture des positions en réel non testée |

Points forts : le bot appelle exactement les fonctions du backtest (vérifié au
centime près) ; une seule boucle de backtest et une seule formule de taille de
position pour le bot, les études et le laboratoire ; chaque ordre réel est
précédé de son intention enregistrée ; un seul bot à la fois ; code rangé par
rôle (`docs/ARCHITECTURE.md`) ; aucune règle ne change sans preuve sur deux
périodes.

Points faibles :

- **Complexité** : 40 fonctions au-dessus du seuil « difficile à maintenir »
  (radon D ou pire). Les six plus lourdes ont été découpées le soir même, sans
  changer leur comportement : la ligne de commande `main` (49 → 8),
  `v29.Config.__post_init__` (46 → 2), l'API du panneau (41 → 9, une table
  de routes), `cmd_verify` (40 → 11), `anticipation.forecast` (36 → 12) et
  `security_view` (33 → 5). Il en reste 34, surtout dans le moteur d'exécution
  (`_parse_order`, `_open_from_fill`) : on n'y touche pas sans nécessité, car
  c'est le code qui passe les ordres réels.
- **`panel/static/app.js`** : 1 520 lignes dans un seul fichier, découpé le
  soir même en modules : `app.js` (les pages, 1 120 lignes), `js/core.js`,
  `js/charts.js`, `js/assistant.js`.
- **Ancien bot V29** : 2 870 lignes gardées pour mémoire, sans avantage
  démontré. Il est à part et n'est plus chargé par TrendGuard, mais ses tests
  et sa maintenance ont un coût.

## 6. Plan d'action

| Priorité | Action | Qui | Quand |
| --- | --- | --- | --- |
| 0 | **Garder le portable branché** | Vous | rebranché le 30/09 pendant le diagnostic (il tournait sur batterie, tombée à 43 %) ; à garder ainsi |
| 1 | Configurer les alertes (Telegram, e-mail ou WhatsApp) | Vous | **toujours en cours** (30/09) : Gmail refuse le mot de passe habituel ; créer un « mot de passe d'application » (myaccount.google.com/apppasswords), puis `alerts configurer` et « Tester ». Deux alertes critiques ne vous sont pas parvenues |
| 2 | Clé Binance : de nouvelles clés sont enregistrées (supprimer sur Binance les anciennes, montrées dans la conversation, si ce n'est pas fait) ; la nouvelle est refusée parce que l'adresse du PC sur Internet change | Vous | sans effet en paper ; pour le réel, une machine à adresse fixe (ligne 13) |
| 3 | Laisser tourner en paper jusqu'à 10 à 20 trades vendus, puis comparer à l'attendu (section « Réel vs attendu » du diagnostic) | Vous et le bot | plusieurs semaines |
| 4 | **Disponibilité** : PC branché et allumé en continu, veille désactivée sur secteur, capot fermé = « Ne rien faire » sur secteur ; ou un petit serveur | Vous et le bot | **en partie fait** : veille sur secteur « Jamais » (29/09), capot fermé « ne rien faire » (30/09, appliqués par le bot). 65 % sur 7 jours au 30/09. Reste à vous : PC branché, et fermer le capot plutôt qu'appuyer sur le bouton d'alimentation |
| 5 | Synchroniser l'horloge de Windows | Vous | droits d'administrateur nécessaires : Paramètres ▸ Heure et langue ▸ Synchroniser maintenant |
| 6 | Découper les fonctions les plus complexes et `app.js` | Code | **fait** : les six plus lourdes découpées, `app.js` en quatre modules ; un test d'horodatage fragile rendu fiable |
| 7 | Décider du sort de l'ancien bot V29 (le supprimer allégerait le dépôt) | Code | **décidé : gardé à part.** Son moteur d'exécution est aussi celui de TrendGuard ; le supprimer obligerait à retoucher le code des ordres réels pour peu de gain |
| 8 | Avant le réel : `verify` complet, quelques jours de testnet, au moins 100 USDT | Vous | le moment venu |
| 9 | Mesurer la disponibilité du bot dans le panneau et prévenir quand il a été arrêté plus d'une heure | Code | fait (29/09) |
| 10 | Centre de sécurité : « Alertes » à corriger si le dernier envoi a échoué | Code | fait (29/09) |
| 11 | Mettre les bibliothèques Python du PC aux versions testées sur GitHub, et corriger les 6 qui ont des failles connues (aiohttp, cryptography, requests, urllib3, anyio, setuptools) ; tests, puis relance du bot | Code, avec votre accord | **fait** (30/09) : le bot a ses propres bibliothèques (dossier `.venv`), aux versions testées et sans faille connue ; sur le PC, 14 corrections de sécurité compatibles, 1 restante sans effet sur le bot (`curl-cffi`, retenue par yfinance) |
| 12 | Rapport quotidien : contrôler les bibliothèques installées sur le PC (versions testées, failles connues) | Code | **fait** (30/09) : deux contrôles dans la section Sécurité du rapport |
| 13 | Avant le réel : faire tourner le bot sur une machine allumée en permanence, à adresse fixe (petit serveur) | Vous | le moment venu ; règle la disponibilité et la clé Binance |
| 14 | Fusionner ou fermer la proposition n° 3 sur GitHub ; fermer la démonstration du port 8799 quand elle ne sert plus | Vous | quand vous voulez |
| 15 | Alimentation du portable signalée (tableau de bord, centre de sécurité, rapport) ; réglage caché du capot lu | Code | fait (30/09) |

Fait depuis l'audit du matin : chemins de secours des ordres réels testés (et
un défaut corrigé), blocage des mots de passe ratés, écriture sûre du choix des
cryptos sous Windows, actualités encadrées pour l'IA de Rachelle, pip-audit et
ruff sur GitHub, code rangé en paquets avec une seule commande, ancien bot V29
mis à part, boucle de backtest unique (rapports des études identiques à l'octet
près), anticipation des ventes, achats et risques, centre de sécurité, sélection
auto (21) ou manuelle (10 plus rentables au départ), saisie des clés plus sûre,
clés jamais transmises en paper.
