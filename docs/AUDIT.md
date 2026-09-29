# Audit et diagnostic expert de TrendGuard — 28 et 29 septembre 2026

Mise à jour de l'audit du matin, après les changements de la journée :
anticipation, centre de sécurité, code rangé par rôle, ancien bot V29 mis à
part, boucle de backtest unique, sélection des cryptos, clé Binance. Méthode :
diagnostic de la stratégie sur les données publiques de Binance (`python
trendguard_bot.py diagnose`), état du bot dans le panneau, journal des deux
premiers jours, vérification Binance avec la clé (`verify`, aucun ordre),
tests avec mesure de couverture, analyse statique (ruff, radon, bandit),
failles connues des dépendances (pip-audit), recherche de secrets dans git.

## Verdict

**Le bot est sain, prudent et conforme, mais pas encore prêt pour de l'argent
réel.** La stratégie est en bonne santé (diagnostic « tout est conforme ») et
le journal ne contient aucune erreur. Les 411 tests et les contrôles de GitHub
sont au vert, et les dépendances n'ont aucune faille connue. Avant le réel, il
manque trois choses qui dépendent de vous : les alertes (aucune n'est
configurée), une clé Binance neuve avec le droit de trading, et quelques
semaines de paper puis de testnet. Un risque de démarrage trouvé pendant ce
diagnostic est déjà corrigé : en paper, le bot ne transmet plus les clés à
Binance.

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
même si ses envois échouent.

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
| 1 | Configurer les alertes (Telegram, e-mail ou WhatsApp) | Vous | **en cours** (29/09) : e-mail enregistré, serveur corrigé (`pop3` → `smtp.gmail.com`), mais Gmail refuse le mot de passe habituel : créer un « mot de passe d'application » puis relancer `alerts configurer`. La configuration refuse désormais un serveur de réception et explique chaque échec en clair |
| 2 | Supprimer sur Binance les clés montrées dans la conversation ; en créer une neuve (lecture, et Trading Spot le jour du testnet), sans retrait, limitée à votre adresse IP | Vous | avant le testnet |
| 3 | Laisser tourner en paper jusqu'à 10 à 20 trades vendus, puis comparer à l'attendu (section « Réel vs attendu » du diagnostic) | Vous et le bot | plusieurs semaines |
| 4 | **Disponibilité** : PC branché et allumé en continu, veille désactivée sur secteur, capot fermé = « Ne rien faire » sur secteur ; ou un petit serveur | Vous | **priorité n° 1** (29/09 : 58 % de disponibilité) |
| 5 | Synchroniser l'horloge de Windows | Vous | droits d'administrateur nécessaires : Paramètres ▸ Heure et langue ▸ Synchroniser maintenant |
| 6 | Découper les fonctions les plus complexes et `app.js` | Code | **fait** : les six plus lourdes découpées, `app.js` en quatre modules ; un test d'horodatage fragile rendu fiable |
| 7 | Décider du sort de l'ancien bot V29 (le supprimer allégerait le dépôt) | Code | **décidé : gardé à part.** Son moteur d'exécution est aussi celui de TrendGuard ; le supprimer obligerait à retoucher le code des ordres réels pour peu de gain |
| 8 | Avant le réel : `verify` complet, quelques jours de testnet, au moins 100 USDT | Vous | le moment venu |
| 9 | Mesurer la disponibilité du bot dans le panneau et prévenir quand il a été arrêté plus d'une heure | Code | à faire |
| 10 | Centre de sécurité : « Alertes » à corriger si le dernier envoi a échoué | Code | à faire |

Fait depuis l'audit du matin : chemins de secours des ordres réels testés (et
un défaut corrigé), blocage des mots de passe ratés, écriture sûre du choix des
cryptos sous Windows, actualités encadrées pour l'IA de Rachelle, pip-audit et
ruff sur GitHub, code rangé en paquets avec une seule commande, ancien bot V29
mis à part, boucle de backtest unique (rapports des études identiques à l'octet
près), anticipation des ventes, achats et risques, centre de sécurité, sélection
auto (21) ou manuelle (10 plus rentables au départ), saisie des clés plus sûre,
clés jamais transmises en paper.
