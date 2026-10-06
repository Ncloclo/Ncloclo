# Audit et diagnostic expert de TrendGuard — 28 septembre au 6 octobre 2026

Cinq diagnostics successifs : l'audit du 28, le diagnostic approfondi du 29
(disponibilité, robustesse, alertes), celui du 30 à 10 h (alimentation,
bibliothèques du PC, état en direct), celui de 13 h, après la mise à jour des
bibliothèques (PC, contrôles croisés), et celui de 17 h, après la
restructuration du code (épreuves de résistance). Puis, à 21 h, le palier de
risque et la reprise prudente de l'arrêt d'urgence, à 22 h un sixième
diagnostic, clé Binance enfin acceptée, le 1er octobre à 2 h la sécurité
renforcée et le rapport après chaque compétence acquise, à 5 h un septième
diagnostic, à 14 h un huitième (disque, mémoire, PC déplacé), à 16 h un
neuvième (Wi-Fi) et le 6 octobre un dixième (faux arrêt d'urgence, écran
bleu). Méthode :
diagnostic de la stratégie sur les données publiques de
Binance (`python trendguard_bot.py diagnose`), état du bot dans le panneau,
journal du bot, du superviseur et de Windows, vérification Binance (`verify`,
aucun ordre), tests, analyse statique, failles connues des dépendances
(pip-audit), recherche de secrets dans git.

## Verdict

**Le bot, sa stratégie et son code sont sains ; ce qui l'entoure ne l'est pas
encore.** Au 1er octobre à 16 h, la stratégie est conforme, le journal ne
contient aucune erreur du bot, les tests et les contrôles de GitHub sont au
vert, les bibliothèques du bot sans faille connue. Les faiblesses sont autour
du bot : le PC portable (déplacé deux fois par jour, 71 % de disponibilité sur
7 jours, disque et mémoire presque pleins) et les alertes qui n'arrivent pas.
La clé Binance est refusée dès que le PC change de réseau. Le bot n'est pas
prêt pour de l'argent réel : il lui faut des alertes qui arrivent, une machine
fixe allumée en permanence avec une adresse fixe, de l'argent sur le compte,
et plusieurs semaines de paper avec des trades vendus.

## Diagnostic approfondi du 6 octobre, 0 h

**Verdict : le bot repart sain, après une vraie panne ; c'est désormais le PC
qui inquiète : un écran bleu de Windows le 5 octobre.** Rapport du bot (5/10,
23 h 28) : 36 contrôles conformes sur 49 (sécurité : 19 sur 23), 4 points à
corriger : alertes, disponibilité, risque configuré, disque.

### État du bot (06/10, 0 h 05)

- Nouvel essai paper depuis le 5 octobre à 22 h 42, avec 100 USDT (votre
  réglage). Première décision à 00:03 : trois achats, AAVE, ICP et ADA, de 10 à
  13 USDT chacun, 1 USDT risqué par achat ; stops à 7 à 10 % sous les cours.
- L'ancien essai (10 000 USDT, du 26 septembre au 5 octobre) est archivé : six
  positions, environ +3,7 % non réalisés à l'archivage, aucune vente.
- Bot libre : premier jour, 100 USDT fictifs placés en cinq positions de 20
  USDT (DOGE, AAVE, ETC, XLM, BNB), surtout sur l'avis de StockTwits.
- Noyau de savoir : 3 661 connaissances, 739 lues dans la journée ; aucune
  source prouvée (Fear & Greed : pas mieux que le hasard).
- Mémoire et disque un peu mieux : 18,7 Go libres (8 %) après le redémarrage
  du PC, qui a rendu la place du fichier d'échange.

### Constats du 6 octobre

| N° | Constat | Gravité | Suite |
| --- | --- | --- | --- |
| J1 | **Faux arrêt d'urgence du 4 au 5 octobre.** Le capital du paper est passé de 10 000 à 100 USDT (avec un plafond de 100) alors que six positions taillées pour 10 000 étaient ouvertes : le bot a comparé 100 USDT à son plus haut de 10 074 et s'est arrêté (−97,7 %), sans aucune perte réelle ; aucun achat possible pendant près de deux jours | **Élevée** | **Corrigé** le 5/10 à 22 h 42 : un changement de capital ouvre un nouvel essai paper (l'ancien archivé) ou, pour le plafond seul, fait repartir le plus haut du capital actuel. Premiers achats du nouvel essai cette nuit |
| J2 | **Écran bleu de Windows** le 5 octobre à 14 h 05, juste après une sortie de veille : erreur 0x1A, une page relue depuis le fichier d'échange ne correspondait plus à ce qui avait été écrit (contrôle d'intégrité). Cause possible : mémoire vive, disque (ici un disque dur accéléré par une mémoire Intel Optane, déclaré sain) ou pilote. Un seul cas en 30 jours ; le bot est reparti avec Windows | **Élevée** si cela se répète | Vous : diagnostic de la mémoire de Windows (`mdsched`), mises à jour de Windows et du pilote Intel Optane/RST. Code : le rapport surveille les écrans bleus (J10) |
| J3 | Risque configuré par vous : 10 % cumulé et 20 positions (au lieu de 6 % et 8). Avec 100 USDT, chaque position fait 10 à 25 USDT ; DASH et ZEC ne peuvent pas être achetées (stop trop loin : moins de 10 USDT par position) | Votre choix | Rien ; le rapport le signale chaque jour |
| J4 | Bot libre : il a tout investi le premier jour, surtout sur l'avis de StockTwits, dont les auteurs sont presque toujours optimistes ; il a acheté ETC, que le bot principal exclut comme trop peu échangée. Ses poids apprendront qui a raison | Information | Code, à votre choix : même filtre de liquidité que le bot principal (J11) |
| J5 | Disponibilité : 88 % sur 7 jours (contre 71 % le 1er octobre) ; arrêts du 5 octobre : l'écran bleu, puis 2 h 05 de veille l'après-midi | Moyenne | PC branché, à poste fixe pour le réel |
| J6 | Alertes toujours muettes : l'e-mail est en pause après 6 refus du mot de passe | **Élevée** | Vous : mot de passe d'application Gmail |
| J7 | Clé Binance refusée la nuit (partage de connexion du téléphone, adresse changeante) ; StockTwits refuse souvent les lectures, Hacker News dépasse ses délais sur cette connexion | Faible en paper | Rien ; constats déjà suivis (I1, I2) |

### Améliorations proposées (votre accord d'abord)

- **J10** : le rapport et le centre de sécurité surveillent les plantages de
  Windows (écran bleu, arrêt brutal) : date, code d'erreur expliqué en
  français, conseil, et alerte s'il y en a plusieurs en une semaine.
- **J11** : le bot libre applique le même filtre de liquidité que le bot
  principal (5 millions de dollars échangés par jour au moins), pour rester
  réaliste le jour où il jouerait de l'argent réel.

**Suite donnée le 6 octobre : J10 et J11 appliquées.** Le rapport lit le
journal de Windows et liste les plantages des 30 derniers jours, écran bleu
expliqué en clair (celui du 5 octobre : « une page relue depuis le fichier
d'échange ne correspondait plus à ce qui avait été écrit ») ; information au
premier, à corriger dès deux en 7 jours ; le centre de sécurité du panneau le
reprend. Le bot libre n'achète plus que des cryptos assez échangées ; ETC,
déjà détenue, se vendra selon ses règles.

## Diagnostic approfondi du 1er octobre, 16 h

**Verdict : rien n'a bougé dans le bot depuis 14 h, et la cause des coupures
est trouvée : c'est le Wi-Fi du bureau.** Rapport du bot : 38 contrôles
conformes sur 47 (sécurité : 21 sur 23), 4 points à corriger, les mêmes qu'à
14 h, tous autour du PC : alertes, disponibilité, disque, mémoire.

### État du bot (01/10, 16 h)

- En marche depuis 13 h 17, 0 relance, PC sur secteur ; capital 9 976 USDT
  (−0,2 % depuis le départ, −1,0 % sous le plus haut), 6 positions, budget de
  risque plein.
- Mémoire du bot stable (211 Mo pour le bot, 122 Mo pour le panneau, mêmes
  valeurs à six minutes d'écart) ; aucune erreur du bot dans le journal.
- Sauvegarde de la nuit vérifiée, en lecture seule : intègre, mêmes positions,
  mêmes quantités, mêmes stops et même argent disponible que la base en
  service. Une restauration rendrait le bot exactement là où il en est.
- Correction du panneau de 15 h (H9) en service : le panneau sert la nouvelle
  version, 17 tests du navigateur au vert sur GitHub.
- Clé Binance de nouveau acceptée (retrait interdit, trading Spot seulement,
  restriction d'adresse active) : le PC est revenu sur le réseau dont
  l'adresse est autorisée (voir I1).

### Constats de 16 h

| N° | Constat | Gravité | Suite |
| --- | --- | --- | --- |
| I1 | **Les coupures viennent du Wi-Fi du bureau.** Journal de Windows : sur l'un des deux réseaux du bureau (réseau A), le pilote Wi-Fi a coupé la connexion 7 fois entre 12 h 21 et 14 h 49 (de 4 secondes à 1 minute), et entre ces coupures la connexion restait dégradée : depuis 11 h, le bot n'a pas pu lire les prix pendant 27 minutes différentes, toutes sur ce réseau. L'adresse de ce réseau n'est pas autorisée sur Binance. Depuis 15 h 32, le PC est sur l'autre réseau (réseau B) : signal à 100 %, plus un seul échec (20 minutes d'observation, à confirmer), et son adresse est celle autorisée sur Binance | Moyenne | Vous : au bureau, rester sur le réseau B ; dans Paramètres ▸ Réseau et Internet ▸ Wi-Fi, décocher « Se connecter automatiquement » pour le réseau A |
| I2 | **La nuit, le bot passe par le partage de connexion de votre téléphone** (hier de 23 h 56 à 8 h 17) : la décision de 00:02 dépend du téléphone (allumé, à portée, avec des données), l'adresse change, et la clé Binance y est refusée | Faible en paper, **bloquant pour le réel** | En paper : rien à faire. Pour le réel : une machine fixe, sur une connexion fixe (ligne 13) |
| I3 | 27 réseaux Wi-Fi mémorisés, dont des hôtels et des téléphones : Windows peut s'y connecter seul. Les réseaux sont classés « public » (pare-feu strict) et le panneau n'écoute que ce PC : risque faible | Faible | Vous : oublier les réseaux qui ne servent plus |
| I4 | Disque libre à 6 % et mémoire réservée à 95 % : inchangés depuis 14 h | **Élevée** | H1 et H2 : de votre côté |
| I5 | Toujours aucune alerte ne vous arrive | **Élevée** | H5 : mot de passe d'application Gmail |

### Amélioration proposée (votre accord d'abord)

- **I6** : le rapport lit aussi le journal du Wi-Fi : coupures par réseau sur
  24 heures, réseau stable à préférer, et connexion par un téléphone signalée
  à l'heure de la décision. Les noms des réseaux restent dans le rapport (sur
  ce PC, et dans l'e-mail qui vous est envoyé), jamais dans les fichiers
  publiés.

Toujours en attente de votre accord : H7 (où part la place du disque et quels
programmes prennent la mémoire) et H8 (adresses publiques retirées des fichiers
publiés).

## Diagnostic approfondi du 1er octobre, 14 h

**Verdict : le bot, sa stratégie et son code vont bien ; c'est le PC qui
fatigue.** Rapport du bot : 37 contrôles conformes sur 47 (sécurité : 20 sur
23), 4 points à corriger, tous autour du PC : alertes, disponibilité, disque,
mémoire. Diagnostic de la stratégie conforme, sauf la place sur le disque.

### État du bot (01/10, 14 h)

- En marche depuis la mise à jour de 13 h 17 (ccxt 4.5.85), sans une erreur ;
  relance automatique active (0 relance) ; PC sur secteur, batterie à 100 %.
- Capital 10 016 USDT (+0,2 % depuis le départ, −0,6 % sous le plus haut) ;
  6 positions, budget de risque plein (6,0 % sur 6 %) ; si tous les stops
  étaient touchés : −5,6 % du capital.
- Cette nuit : LTC à 0,75 % au-dessus de son stop, 35 % de chances d'être
  vendu (ce serait le premier trade clos, environ −1 % du capital) ; ETH, BTC
  et TRX, les achats les plus proches, à plus de 3 % d'une cassure.
- Stratégie (Binance, 2019 → 30/09) : +33,6 % par an, pire baisse −24,7 %,
  Sharpe 1,19 ; 24 derniers mois : +1,19 R par trade (intervalle 90 % : +0,37
  à +2,11) ; 12 mois glissants : +34 %, percentile 65. Tournoi de 7 stratégies
  sur 2 ans : TrendGuard 3e, juste derrière les deux premières (Sharpe 1,64
  contre 1,72), rien ne justifie d'en changer.
- Bibliothèques : ccxt 4.5.85 et `urllib3` 2.8.0 depuis 13 h (F9 réglé),
  aucune faille connue dans les 47 bibliothèques ; 531 tests et contrôles
  GitHub au vert.
- Heure juste à 0,2 s près : Windows la synchronise de lui-même par moments
  (service démarré à la demande, 3 fois en 3 jours) ; la ligne 5 du plan
  n'est plus nécessaire.
- Restent de votre côté : propositions n° 3 et n° 4 à fermer sur GitHub, clés
  exposées à supprimer sur Binance, compte réel sans USDT.

### Constats de 14 h

| N° | Constat | Gravité | Suite |
| --- | --- | --- | --- |
| H1 | **Disque libre à 6 %** (14,7 Go sur 240), 6 Go perdus depuis la veille au soir. En cause : la mémoire presque pleine (H2) fait grossir le fichier d'échange de Windows (11,1 Go) ; s'y ajoutent le fichier d'hibernation (6,4 Go) et le dossier Téléchargements (25 Go). Sous 10 %, les mises à jour de Windows peuvent échouer ; à zéro, le bot ne peut plus écrire sa base | **Élevée** | Vous : trier le dossier Téléchargements (sans toucher au dossier `claude`, qui contient le bot), fermer des onglets (H2), puis redémarrer le PC, qui rend la place du fichier d'échange. Code : dire dans le rapport où part la place (H7) |
| H2 | **Mémoire réservée à 94 %** (23,1 Go sur 24,5 possibles) : Chrome (47 processus, 7,2 Go), les fenêtres intégrées d'Edge (WebView2, 3,3 Go), VS Code (2,6 Go) ; le bot, moins de 1 Go. Pleine, Windows ferme des programmes, parfois le bot (la relance automatique le reprend) | **Élevée** | Vous : fermer des onglets de Chrome. Code : nommer dans le rapport les programmes qui prennent le plus de mémoire (H7) |
| H3 | **Le PC voyage** : débranché et capot fermé deux fois par jour (1 h 32 de veille ce matin, 1 h 37 hier soir) : 71 % de disponibilité sur 7 jours. Et il change de réseau : troisième adresse publique en 24 heures, la clé Binance est de nouveau refusée (`verify` à 14 h) | Faible en paper, **bloquant pour le réel** | En paper : rien à faire (décisions rattrapées, clé refusée = information). Pour le réel : une machine fixe, allumée en permanence, à adresse fixe (ligne 13) ; en réel, les stops de secours posés sur Binance protègent même PC éteint |
| H4 | Réseau instable : prix indisponibles à 12 h 21, 13 h 48 et de 14 h 03 à 14 h 06, contrôle de la clé du rapport hors délai ; latence de 400 à 450 ms vers Binance. Tout est rattrapé au cycle suivant | Faible | Rien à faire ; compté dans le rapport |
| H5 | **Toujours aucune alerte ne vous arrive** : e-mail en pause après 3 refus du mot de passe (un essai par jour), WhatsApp pas configuré ; la fenêtre « alerts configurer » attend depuis la veille à 18 h 39 | **Élevée** | Vous : mot de passe d'application Gmail dans cette fenêtre, puis CallMeBot pour WhatsApp |
| H6 | Vie privée : le dépôt est public et contient vos adresses publiques passées (quatre lignes de cet audit, un test) | Faible (adresses changeantes) | Code : les remplacer par des adresses d'exemple, et un test qui refuse toute adresse réelle dans les fichiers publiés (H8) |
| H9 | **Panneau ▸ Cryptos, sélection manuelle : une case cochée pouvait être effacée**, et la sélection enregistrée n'était alors pas celle affichée : case cochée juste après « Tout décocher », « Tout cocher » ou « Les 10 plus rentables », ou pendant l'actualisation automatique de la liste (toutes les 15 secondes). Rejoué : « Tout décocher », puis ETH coché, et 9 cryptos enregistrées au lieu d'une. Trouvé par les contrôles GitHub (test du navigateur en échec à 14 h 32, deux fois de suite) | Moyenne ; sans effet aujourd'hui (sélection auto, cases verrouillées) | **Corrigé** (01/10, 15 h) : une case en attente n'est plus effacée, les boutons mettent les cases à jour tout de suite, les enregistrements passent un par un, dans l'ordre ; un test du navigateur rejoue la course (il échoue sur l'ancien code) |

Code : 70 modules, 25 554 lignes ; les fonctions les plus chargées du bot
sont la veille du marché (30 embranchements), le contrôle du système du
diagnostic (28), la maintenance (28) et l'évolution quotidienne (25). À
découper sans urgence, à l'occasion d'une autre modification.

### Améliorations proposées à 14 h (votre accord d'abord)

- **H7** : quand le disque ou la mémoire se remplissent, le rapport et le
  centre de sécurité disent où part la place (Téléchargements, fichier
  d'échange, hibernation, fichiers temporaires, corbeille : des tailles,
  jamais des noms de fichiers), combien de place a été perdue depuis le
  rapport précédent, et quels programmes prennent le plus de mémoire.
- **H8** : vos adresses publiques retirées des fichiers publiés (remplacées
  par des adresses d'exemple), et un test qui empêche d'en publier une autre.

## Le paper trade déjà (1er octobre, 13 h)

Votre demande : que le paper commence à trader. Il trade déjà : six achats le
26 septembre (AAVE, ADA, ICP, LINK, LTC, XLM), suivis tous les quarts d'heure,
décision chaque nuit à 00:02 UTC. Depuis, rien ne bouge, sans panne :

- **budget plein** : chaque position risque 1 % du capital, six positions font
  les 6 % permis ; un achat de plus attend une vente ;
- **aucune vente** : aucun cours n'a clôturé sous son stop. Le plus proche,
  LTC, est à 0,7 % de son stop (38 % de chances d'être vendu à la clôture de
  ce soir, d'après le bot) ;
- **aucune cassure** : même avec de la place, rien ne s'achèterait ce soir ;
  la plus proche, ETH, doit encore monter de 2,8 % pour dépasser son plus
  haut de 30 jours.

Rien n'est changé : les trois façons de le faire acheter plus (budget libéré
quand les stops montent, 8 % cumulé, 0,75 % par achat) font 10 % de trades en
plus mais gagnent moins depuis 2023, et deux dépassent la limite du hasard
([`ADAPTATION.md`](ADAPTATION.md), section 6). Rythme normal du bot : 3
trades par mois, gardés 15 jours (médiane) ; entre deux mouvements, 3 jours
d'habitude et 10 jours ou plus une fois sur dix. Pour voir chaque achat et
chaque vente au moment où ils se font : les alertes par e-mail (mot de passe
d'application Gmail) ou par WhatsApp.

Au passage, le contrôle des failles de GitHub a échoué comme prévu (F9) :
ccxt 4.5.85, publiée ce matin, accepte enfin `urllib3` 2.8.0. Les
bibliothèques du bot sont mises à jour (ccxt 4.5.85, `urllib3` 2.8.0, rien
d'autre ne change), les tests passent, le bot est relancé avec elles.

## Clés exposées retirées (1er octobre, 7 h)

Votre demande : supprimer les clés exposées signalées par le rapport. Le
rapport n'a jamais contenu de clé : la ligne « Clé partagée par erreur » était
un rappel. Recherche sur tout le PC, sans jamais afficher une clé (empreintes et
comptes seulement) :

- **la clé que le bot utilise n'est pas une clé exposée** (ni la clé API, ni
  son secret) ; aucune clé exposée dans le dépôt ni dans son historique sur
  GitHub, ni dans les journaux, les rapports, les sauvegardes ou l'historique
  des commandes ;
- trois clés exposées restaient en clair sur le PC : dans trois conversations
  de Claude Code (49 fois) et dans le fichier `key.txt` du Bureau (une clé et
  son secret). Elles y sont remplacées par des étoiles, sans rien changer
  d'autre (le reste de `key.txt` est intact, les conversations restent
  lisibles) ;
- le bot garde seulement leurs empreintes (irréversibles), dans un fichier
  privé exclu de GitHub : `set-keys` refuse désormais une clé exposée, et le
  centre de sécurité dit « la clé en service n'a jamais été montrée » (ou le
  contraire, en rouge).

**Ce que seul vous pouvez faire** : supprimer ces trois clés sur Binance
(Gestion des API ▸ Supprimer), si ce n'est pas déjà fait. Tant qu'elles
existent sur Binance, quelqu'un qui les a lues pourrait s'en servir (sans
pouvoir retirer d'argent : le retrait était interdit).

## Diagnostic approfondi du 1er octobre, 5 h

**Verdict : le bot et sa stratégie vont bien, la sécurité est au vert ; ce qui
l'arrête, c'est le PC qui s'endort sur batterie.** Rapport du bot : 37 contrôles
conformes sur 47 (sécurité : 19 sur 23), 3 points à corriger, tous de votre
côté ; diagnostic de la stratégie conforme, sauf la place sur le disque.

### État du bot (01/10, 5 h)

- En marche depuis la mise à jour de 2 h 12, sans une erreur ni un
  avertissement depuis ; relance automatique active (0 relance) ; PC sur
  secteur, batterie à 100 %.
- Capital 10 155 USDT, nouveau plus haut (+1,6 % depuis le départ) ; 6
  positions, budget de risque plein (5,9 % sur 6 %).
- Cette nuit : LTC à 1 % au-dessus de son stop, 29 % de chances d'être vendu ;
  TRX, ICP et XLM à moins de 3 % d'une cassure (TRX ne pourrait être acheté
  qu'après une vente).
- Stratégie : +33,6 % par an depuis 2019, pire baisse −24,7 % ; 24 derniers
  mois : +1,19 R par trade (intervalle 90 % : +0,37 à +2,11) ; 12 mois
  glissants : +34 %, percentile 65.
- Clé Binance acceptée (adresse autorisée), aucun droit superflu ; disque
  chiffré, Windows à jour, aucun secret trouvé.

### Constats de 5 h

| N° | Constat | Gravité | Suite |
| --- | --- | --- | --- |
| G1 | **Le PC s'endort sur batterie** (journal de Windows) : chargeur débranché à 22 h 19 et capot fermé, veille 5 secondes après (jusqu'à 23 h 56, bot arrêté 1 h 37) ; puis trois mises en veille pour inactivité sur batterie (0 h 16, 0 h 29, 1 h 06), au bout de 10 minutes sans toucher le PC, malgré l'anti-veille du bot ; même scénario le 30/09 à 7 h 02 | **Élevée** | Vous : laisser le chargeur branché, surtout la nuit. Code : prévenir par e-mail et WhatsApp dès que le PC passe sur batterie (G6) |
| G2 | Aucune alerte n'est encore arrivée : Gmail refuse toujours le mot de passe (dernier refus le 01/10 à 0 h 38) ; la fenêtre « alerts configurer » attend depuis la veille | **Élevée** | Vous : mot de passe d'application Gmail, puis CallMeBot pour WhatsApp |
| G3 | L'adresse de la connexion a changé deux fois en douze heures (adresse A, puis adresse B) : la clé Binance sera refusée à chaque changement, et le rapport le comptera comme « à corriger » alors qu'en paper elle ne sert à rien | Faible en paper | Code : en paper, une clé refusée devient une information (toujours critique en réel) (G7) |
| G4 | Deux propositions de la routine de nuit, n° 3 et n° 4, sont en conflit et inutiles : la n° 4 (disque mesuré pareil partout) a été faite directement à 2 h | Aucune | Vous : les fermer sur GitHub |
| G5 | Code : les fonctions les plus chargées du bot sont maintenant la commande `rapport` (29 embranchements) et les contrôles de santé (31) ; le palier de risque et le rapport, découpés, ont quitté la liste | Faible | Code : les découper à leur tour (G8) |

### Améliorations proposées à 5 h (votre accord d'abord)

- **G6** : alerte « PC sur batterie, branchez le chargeur » envoyée dès le
  passage sur batterie (lecture de l'alimentation sans PowerShell, une fois par
  minute), et « chargeur rebranché » ensuite.
- **G7** : en paper, une clé Binance refusée reste visible mais ne compte plus
  comme un point à corriger ; en réel, elle reste critique.
- **G8** : commande `rapport` et contrôles de santé découpés en fonctions
  courtes, comme le palier et le rapport.

**Suite donnée le 1er octobre, 6 h : G6, G7 et G8 appliquées.** Le bot relit
l'alimentation une fois par minute (sans PowerShell) et prévient par e-mail et
WhatsApp après une minute sur batterie, une seule fois par débranchement, puis
quand le chargeur est rebranché ; une alerte ratée faute de réseau, au réveil,
part dès qu'il revient. En paper, une clé Binance refusée reste visible, avec
l'adresse à autoriser, sans compter comme un point à corriger. La commande
`rapport` passe par une table d'actions courtes, et les contrôles de santé
sont cinq fonctions (marche, décision du jour, arrêt d'urgence, risque, mode).

## Suite donnée le 1er octobre, 2 h : sécurité renforcée, rapport après chaque compétence

Votre demande : une analyse profonde du fond et de la forme après chaque
compétence acquise et chaque nuit à 00:30, toutes les sécurités possibles avec
sagesse, de façon autonome, un rapport détaillé dans les réglages du panneau et
par e-mail et WhatsApp, les recommandations appliquées, le code harmonisé.

**La nuit du 30 septembre.** Le PC est passé sur batterie (86 % à 00:30, 61 % à
1 h 35) et s'est endormi de 22 h 18 à 23 h 56 : bot arrêté 1 h 37. La clé
Binance, acceptée à 22 h, est de nouveau refusée : l'adresse de la connexion a
changé en trois heures (adresse A, puis adresse B). La décision de
minuit est prise : LTC a fini au-dessus de son stop, rien n'est vendu ; le
palier reste à 1 %.

**Ce qui a été fait (constats E1 à E3 et sécurité) :**

| Amélioration | Effet |
| --- | --- |
| Vraie cause d'un échec d'alerte | une coupure du réseau au réveil ne masque plus le mot de passe Gmail refusé : le panneau et le rapport montrent les deux (constat E1), même après un redémarrage du bot |
| Alertes critiques renvoyées | ratée faute de réseau, une alerte critique est renvoyée toutes les 5 minutes pendant 6 heures, marquée « envoyée en retard » (constat E2) ; un mot de passe refusé ne se renvoie pas |
| Disque : une seule règle | diagnostic, rapport et panneau disent la même chose, dans la même unité et avec le même seuil (constat E3) |
| Clé Binance : moindre privilège | droits superflus signalés (marge, contrats à terme, options, transferts), âge de la clé (à renouveler après 180 jours) ; refusée : l'adresse actuelle du PC à autoriser est écrite dans le rapport ; `verify` les montre aussi |
| Secrets dans l'historique des commandes | une clé tapée dans PowerShell, le terminal ou Python est masquée seule chaque nuit, comme dans les journaux |
| Dossier hors du nuage | alerte si le dossier du bot est synchronisé par OneDrive, Dropbox ou Google Drive |
| Chiffrement du disque | lu sans droits d'administrateur : actif sur ce PC |
| Mises à jour de Windows | dernière mise à jour de sécurité (le 10/09 sur ce PC), à corriger au-delà de 45 jours, redémarrage en attente signalé |
| Rapport après une compétence acquise | réglage adopté, confirmé ou annulé, palier de risque changé : analyse et rapport envoyés aussitôt, si le rapport de la nuit n'en a pas rendu compte |
| Rapport plus clair | pourquoi ce rapport, score de la sécurité à part, ce qui a changé depuis le précédent (points corrigés, nouveaux points, compétences) ; dans le texte, l'e-mail et WhatsApp |
| Panneau ▸ Réglages ▸ Rapport de sécurité | contrôles en direct, puis la sécurité de la dernière analyse et ses changements, bouton « Envoyer par e-mail et WhatsApp » (`rapport envoyer`) |
| Harmonisation | palier de risque découpé en quatre étapes courtes, rapport en fonctions plus petites (sécurité, score, changements), masquage des secrets en une seule fonction, README sans clé dans une commande |

Le bot n'installe ni ne change aucun réglage de sécurité de Windows : il les lit
et recommande. Il n'agit seul que sur ce qui est sûr et réversible (sauvegarde,
droits du fichier des secrets, secrets masqués, veille sur secteur).

## Diagnostic approfondi du 30 septembre, 22 h

**Verdict : le bot fait exactement ce que dit sa stratégie, sans une erreur
depuis sa mise à jour de 21 h ; la connexion à Binance est enfin complète.**
Rapport du bot : 32 contrôles conformes sur 42, 3 à corriger (alertes,
disponibilité, disque), tous de votre côté ; diagnostic de la stratégie : 27
sur 27.

### État du bot (30/09, 22 h 15)

- En marche depuis la mise à jour de 21 h 07, relance automatique active (0
  relance), démarrage avec Windows actif, PC sur secteur (batterie à 100 %).
- Capital 9 987 USDT (−0,1 % depuis le départ, −0,9 % sous le plus haut) ;
  6 positions sur 8 : AAVE, ADA, ICP, LINK, LTC, XLM, achetées le 26/09.
- Budget de risque plein : 6,0 % engagés sur 6 % ; si tous les stops étaient
  touchés maintenant : −5,4 % du capital.
- Cette nuit : LTC à 0,4 % au-dessus de son stop, 34 % de chances d'être vendu
  à la clôture de minuit ; ce serait le premier trade clos (environ −1 %) et
  une place libérée pour un achat. Les autres stops sont à 4,9 % ou plus
  sous le cours.

- Palier de risque : 1 % par achat ; 1,25 % refusé ce soir (hasard −39 % pour
  une limite de −35 %). Arrêt d'urgence prêt, reprise prudente armée.
- Mémoire : 72 % réservés (contre 83 à 85 % avant le redémarrage de 18 h 20).

### Ce qui a été vérifié et tient (30/09, 22 h)

| Contrôle | Résultat |
| --- | --- |
| Parité bot ↔ stratégie | le backtest des mêmes jours achète exactement les 6 mêmes cryptos, avec les mêmes stops de départ ; seuls les prix d'achat diffèrent (achat tardif du 26/09 à 22 h 53, recalculé au prix réel) |
| Stratégie (Binance, 2019 → 29/09) | +33,5 % par an, pire baisse −24,7 %, Sharpe 1,19, 313 trades, espérance +1,12 R ; 24 derniers mois : +1,19 R (intervalle 90 % : +0,37 à +2,11) |
| Rendement sur 12 mois glissants | +33,3 %, percentile 64 de l'historique |
| Marché | BTC haussier depuis 42 jours, 18 % au-dessus de sa moyenne 150 jours ; volatilité basse (percentile 26) |
| Binance | clé acceptée : retrait interdit, trading Spot autorisé, restriction IP active ; validation des ordres OK ; latence 338 ms ; heure corrigée (PC en avance de 0,5 s) |
| Journal depuis 21 h 07 | aucune erreur, aucun avertissement |
| Sécurité | secrets hors de GitHub et des journaux, pare-feu et antivirus actifs, panneau limité au PC |
| Code | identique à la version publiée (68e1077), contrôles GitHub au vert, ruff sans remarque |

### Constats de 22 h

| N° | Constat | Gravité | Suite |
| --- | --- | --- | --- |
| E1 | **Aucune alerte n'est jamais arrivée par e-mail.** Dernière cause affichée : « serveur introuvable » (réseau pas encore prêt au réveil du PC, 30/09 à 08 h 29), qui masque la vraie cause, toujours là : Gmail refuse le mot de passe (29/09 à 23 h 02) et exige un « mot de passe d'application ». La fenêtre « alerts configurer » attend ce mot de passe depuis l'après-midi | **Élevée** | Vous : créer le mot de passe d'application Gmail et le saisir dans cette fenêtre. Code : afficher la vraie cause, pas la dernière coupure (E2) |
| E2 | Une alerte critique ratée à cause d'une coupure du réseau est perdue : l'alerte « le bot a été arrêté 1 h 27 » du réveil de 08 h 29 n'a jamais été renvoyée | Moyenne | Code : garder ces alertes et les renvoyer quand le réseau revient, marquées « en retard » |
| E3 | Le diagnostic dit « ✅ disque libre 22,5 Go » quand le rapport et le panneau disent « ✗ 20,9 Go (9 %) » : même disque, deux unités (Go décimaux contre binaires) et deux seuils (1 Go contre 10 % ou 2 Go) | Faible | Code : une seule mesure et un seul seuil, ceux du rapport |
| E4 | Coupures d'Internet ou de Binance : 4 épisodes en 24 h (23 h 15, 08 h 29 au réveil, 19 h 18, et quelques délais dépassés), rattrapés au cycle suivant, sans décision manquée | Faible | Rien à faire ; les surveiller dans le rapport |
| E5 | La clé API en service est celle dont l'identifiant a été montré dans une conversation (son secret, jamais) | Faible en paper | Avant le réel : en créer une neuve et supprimer celle-ci |
| E6 | Compte Binance réel : 44,28 TRX (≈ 15 USDT) et 0 USDT | Aucune en paper | Avant le réel : au moins 100 USDT ; vendre les TRX ou les laisser (le bot n'achètera pas de TRX) |
| E7 | Proposition n° 3 sur GitHub toujours ouverte, en conflit | Aucune | La fermer sur GitHub |

## Palier de risque et arrêt d'urgence (30 septembre, 21 h)

Votre demande : corriger « la panne » d'absence d'achats et de ventes, et
laisser le bot passer de 1 % à 2 % par achat selon sa propre analyse, et
inversement, avec prudence et sagesse. Preuves chiffrées :
[`ADAPTATION.md`](ADAPTATION.md), section 6 (`python -m research.palier`).

**Pas de panne.** Les 6 positions achetées le 26 septembre risquent
chacune environ 1 % : 5,96 % engagés sur 6 % permis, il n'y a pas de place pour
une septième. Aucun cours n'a clôturé sous son stop, d'où l'absence de vente
(LTC était sous le sien à 21 h : vendu à la clôture de minuit s'il y reste).
Libérer le budget quand les stops montent ferait acheter plus, mais moins bien
(Calmar 1,29 au lieu de 1,54 sur 2018-2022, 1,05 au lieu de 1,33 depuis 2023) :
écarté. Le raisonnement du jour, dans le panneau, le dit désormais : budget de
risque plein, et vente la plus proche.

**Palier de risque (en service).** Le bot peut porter son risque par achat de
1 % à 2 %, un cran de 0,25 % à la fois, seulement si son analyse de la nuit le
justifie : meilleur sur les deux époques, pire baisse rejouée d'au plus 30 %,
pire baisse du hasard d'au plus 35 % (1 fois sur 20 en trois ans), capital à
moins de 5 % de son plus haut, marché haussier ; puis 30 jours d'essai. Il
redescend aussitôt à 1 % à 10 % de baisse, en marché baissier ou à l'arrêt
d'urgence. Premier examen : 1,25 % rapporterait plus sur les deux époques, mais
un tirage malchanceux sur 20 ferait perdre 39 %, presque l'arrêt d'urgence :
le bot garde 1 % et refait l'examen chaque nuit. Rejouée pas à pas de 2020 à
2026, l'analyse sans ces garde-fous aurait monté le risque 64 % du temps, pour
le même rendement depuis 2023 et une pire baisse de −29 % au lieu de −25 %.

**Arrêt d'urgence levé seul, avec prudence.** Il bloquait les achats jusqu'à
une commande manuelle, bot arrêté (703 jours sans achat dans l'essai « 5 % /
20 % / 20 » depuis 2023). Il se lève maintenant seul après 60 jours, si le
marché est redevenu haussier et que l'auto-diagnostic ne conclut pas à la perte
de l'avantage de la stratégie, une fois par an au plus, avec un risque divisé
par deux pendant 90 jours. Dans cet essai, en partant du 1er janvier 2023 :
43 689 USDT au lieu de 18 810, chiffre confirmé par le vrai bot rejoué jour par
jour. Aux réglages du bot, l'arrêt d'urgence ne s'est jamais déclenché depuis
2018.

## Diagnostic approfondi du 30 septembre, 17 h

**Verdict : trois heures après la restructuration, le bot tourne sans une seule
erreur, et il se relève seul d'un plantage en 45 secondes.** Ce passage-ci a
surtout cherché ce que les changements du jour auraient pu abîmer : rien. Il a
aussi trouvé 2,8 Go laissés sur le disque par les vérifications des jours
précédents, retirés. Enfin, trois failles publiées cet après-midi dans une
bibliothèque de connexion font passer les contrôles GitHub au rouge (F9) : pas
encore corrigeables, peu exploitables contre ce bot. Heures en UTC.

### État du bot (30/09, 17 h)

| Mesure | Valeur |
| --- | --- |
| Capital | 10 093,46 USDT (+0,9 %) ; plus haut du jour 10 232, plus bas 9 972 |
| Positions | ICP +9,2 %, AAVE +4,2 %, XLM +4,4 %, LINK +2,6 %, ADA −1,4 %, LTC −6,7 % |
| Ce soir | LTC à 1,1 % de son stop : vente probable à 28 % (ce serait le premier trade clos, environ −100 USDT) ; DOT à 2,1 % d'un signal, bloqué par le plafond de risque |
| Journal depuis midi | aucun avertissement, aucune erreur ; aucune remarque des nouvelles bibliothèques dans la sortie du bot |
| Windows depuis midi | aucune mise en veille, aucun plantage de Python, aucun redémarrage en attente |
| Disponibilité | 87,7 % sur 24 h, 67,9 % sur 7 jours |
| Mémoire du PC | 85 % réservés (89 % à 13 h) ; bot, superviseur et panneau : 100 à 210 Mo chacun, au lieu de 325 à 430 |
| Code | 69 modules, 24 023 lignes ; 520 tests (507 réussis, 13 sans objet sur ce PC), ruff sans remarque ; contrôles GitHub au vert jusqu'à 17 h 10, puis rouges à cause de F9 |

### Ce qui a été éprouvé

| Épreuve | Résultat |
| --- | --- |
| Plantage simulé : le processus du bot arrêté de force à 17 h 03 | le superviseur le voit en 3 secondes, le relance 10 secondes plus tard ; le bot reprend ses cycles 45 secondes après l'arrêt, avec ses 6 positions |
| Répétition de la nuit, étape évolution (`evolution examen`, sans rien changer) | mêmes résultats, au chiffre près, que la nuit dernière avec les anciennes bibliothèques : 10 réglages essayés, aucun adopté |
| Répétition de la nuit, étape rapport (14 h 01, sans envoi) | 32 contrôles conformes sur 41 ; les quatre fichiers du rapport restructuré fonctionnent en conditions réelles |
| Décisions quotidiennes avec les nouvelles bibliothèques | 1 365 jours rejoués par le vrai bot (du 01/01/2023 au 27/09/2026) sans incident |
| Suite de tests complète sur la version en service | 507 réussis, aucun échec |
| Sauvegardes | relues : intégrité correcte, 6 positions |

### Constats de 17 h

| N° | Constat | Gravité | Suite |
| --- | --- | --- | --- |
| F1 | **2,8 Go laissés sur le disque par les vérifications** : 31 profils temporaires du navigateur (2,3 Go) créés par les captures d'écran des 29 et 30, et le cache de téléchargement de pip (0,5 Go) | Moyenne : le disque était tombé à 15,2 Go libres | **Corrigé** : retirés, 17,9 Go libres ; les outils de capture nettoient désormais derrière eux |
| F2 | **Disque toujours à 7 % de libre** : le rapport et le centre de sécurité le signalent maintenant | Moyenne | Vous : libérer de la place (téléchargements, corbeille, nettoyage de disque de Windows) |
| F3 | **Aucune alerte ne vous parvient** (inchangé) | Élevée | Mot de passe d'application Gmail, dans la fenêtre « TrendGuard - Alertes » |
| F4 | **Le service « Temps Windows » est arrêté** : l'horloge du PC ne se synchronise jamais | Faible : le bot prend l'heure de Binance | Vous, avec les droits d'administrateur : démarrer le service « Temps Windows » et le mettre en démarrage automatique (plan, ligne 5) |
| F5 | Proposition n° 3 toujours ouverte et en conflit sur GitHub | Faible | Vous : « Close pull request » |
| F6 | Deux fichiers annexes vides laissés dans `sauvegardes/` par la relecture de 13 h | Faible | **Corrigé** : retirés ; la relecture se fait maintenant sur une copie en mémoire |
| F7 | Trois fenêtres ou pages d'essai restent ouvertes : démonstration (port 8799), page de l'essai « 5 % / 20 % / 20 » (port 8899), fenêtre des alertes (324 Mo réservés) | Faible | À fermer quand elles ne servent plus |
| F8 | Le compteur du superviseur affiche « 1 relance » : c'est l'exercice de 17 h 03, pas une panne | Information | Revient à zéro à la prochaine relance du superviseur |
| F9 | **Trois failles publiées cet après-midi dans `urllib3` 2.7.0**, la bibliothèque de connexion utilisée pour parler à Binance ; corrigées en 2.8.0. Les contrôles GitHub passent au rouge à 17 h 10 (étape pip-audit). Impossible à corriger aujourd'hui : ccxt 4.5.84, la dernière version, exige exactement `urllib3` 2.7.0 | Faible en pratique : deux failles touchent les proxys HTTPS (le bot n'en utilise pas), une la lecture de très grosses réponses (le bot ne parle qu'à Binance, en HTTPS). Mais tant que les contrôles sont rouges, une mise à jour validée ne s'installe pas seule | Mettre ccxt et `urllib3` à jour dès que ccxt accepte la 2.8.0 (plan, ligne 18) ; d'ici là, à votre choix : attendre, ou accepter ces trois failles pour quelques jours, par écrit, pour que les contrôles repassent au vert |

### Suite donnée le 30 septembre à 17 h 40

- **F9** : ccxt n'a toujours pas de version qui accepte `urllib3` 2.8.0 (la
  sienne, 4.5.84, exige la 2.7.0 ; ses dépendances sont épinglées exprès).
  Plutôt que d'accepter ces failles à la main, le contrôle les classe seul :
  une faille qu'une bibliothèque épinglée empêche de corriger est affichée
  sans bloquer ; dès que la nouvelle version de ccxt accepte la correction, le
  contrôle de GitHub échoue et le rapport quotidien la marque à corriger. Les
  contrôles GitHub repassent au vert.
- **Bibliothèques du PC** : `urllib3` 2.8.0 et `pyjwt` 2.15.1 installés (une
  nouvelle faille publiée aujourd'hui touchait aussi `pyjwt` 2.14.0) ; les
  logiciels qui s'en servent (freqtrade, requests, ccxt du PC) fonctionnent.
  Reste `curl-cffi`, retenue par yfinance.
- **F2, disque** : 1,3 Go de plus rendus (caches de npm et de VS Code) : 18,7 Go
  libres (8 %). Pour dépasser 10 %, il faut libérer environ 5 Go dans vos
  téléchargements (26 Go, dont un dossier « Compressed » de 11 Go et
  « pcsx2 » de 7,5 Go), ou désactiver la veille prolongée (fichier de 6,4 Go),
  ce qui demande les droits d'administrateur.
- **F4, horloge** : le service « Temps Windows » ne peut être démarré qu'avec
  les droits d'administrateur (essai refusé) : Services ▸ Temps Windows ▸
  Démarrage automatique ▸ Démarrer.

### Redémarrage du PC à 18 h 20 : premier essai grandeur nature

Vous avez redémarré le PC à 18 h 20. C'était le premier vrai redémarrage depuis
que le bot a ses propres bibliothèques : le superviseur, le bot et le panneau
sont repartis seuls à l'ouverture de session, en passant bien par le Python de
l'installation puis par l'environnement propre du bot. Le bot a repris ses
cycles à 18 h 30 avec ses 6 positions, rien de perdu ; 11 minutes d'arrêt en
tout, sous le seuil d'une heure du compteur de disponibilité.

Le redémarrage a fermé la démonstration (port 8799), la page de l'essai (port
8899) et la fenêtre des alertes (F7). La page de l'essai et la fenêtre des
alertes, qui attend toujours le mot de passe d'application Gmail, ont été
rouvertes ; la démonstration reste fermée. Le service « Temps Windows » est
toujours arrêté après le redémarrage (F4) : il ne démarre pas seul sur ce PC.

### Essai « 5 % par achat, 20 % cumulé, 20 positions » (votre question du 30/09)

Simulé sur l'historique réel de Binance, sans rien changer au bot, puis
confirmé par le vrai bot rejoué jour par jour depuis le 01/01/2023 (page locale
<http://127.0.0.1:8899>, dossier `rapports/essai-5-20-20/`) :

| Depuis le 01/01/2018, 10 000 USDT au départ | Gain par an | Pire baisse | Capital final |
| --- | --- | --- | --- |
| Réglages actuels (1 %, 6 %, 8 positions) | +29,8 % | −24,7 % | 97 686 USDT |
| Réglages demandés, sur le papier | +55,3 % | −41,5 % | 467 797 USDT |
| Réglages demandés, avec l'arrêt d'urgence du bot | +28,2 % | −40,7 % | 87 527 USDT |

Avec ces réglages, l'arrêt d'urgence (−40 %) se déclenche : le 22/03/2023 en
partant de 2018, le 25/10/2024 en partant de 2023 ; ensuite, plus aucun achat.
Le bot ne tient jamais 20 positions (6 au plus : 20 % divisé par 5 %, et 25 % du
capital par position), et son code refuse plus de 2 % par achat. Conclusion :
même stratégie avec un levier plus fort, qui bute sur l'arrêt d'urgence ; les
réglages actuels sont gardés. Au passage, la page du rejeu affiche maintenant
la vraie taille des achats et l'arrêt d'urgence quand c'est lui qui bloque.

## Diagnostic approfondi du 30 septembre, 13 h

**Verdict : la mise à jour des bibliothèques n'a rien cassé, et la stratégie
donne exactement les mêmes chiffres qu'avant ; les faiblesses sont toutes du
côté du PC et des alertes.** Méthode, en plus de celle de 10 h : état interne du
bot relu dans sa base et recalculé avec les prix en direct, courbe du capital
(211 relevés), sauvegardes rouvertes, répétition des ordres du jour sans clé,
mémoire, disque, horloge, redémarrages et plantages relevés dans Windows,
réglages publics du dépôt GitHub. Heures en UTC.

### État du bot (30/09, 13 h)

| Mesure | Valeur |
| --- | --- |
| Capital | 10 232,04 USDT (+2,32 %), son plus haut ; pire baisse relevée −3,0 % (le 30 à 03 h 32) |
| Marché depuis le début du paper | BTC +1,2 % ; moyenne des 21 cryptos 0,0 % ; moyenne des 6 détenues +4,0 % |
| Positions | ICP +10,4 %, XLM +6,2 %, AAVE +6,0 %, LINK +4,4 %, ADA +0,4 %, LTC −5,8 % |
| Prochaine clôture | LTC à 2,2 % de son stop : vente probable à 17 % (36 % ce matin) ; DOT à 1,5 % d'un signal d'achat, bloqué par le plafond de risque (6 % engagés sur 6 %) |
| Comptes | capital recalculé à la main à partir des quantités et des prix en direct : identique à celui du bot ; base saine |
| Disponibilité | 87,7 % sur 24 h, 66,4 % sur 7 jours ; aucune mise en veille depuis la correction du capot (09 h 29) ; aucun plantage de Python dans le journal de Windows depuis le 26 |
| Journal du 30 | 0 erreur du bot ; 4 délais de réseau dépassés, tous rattrapés au cycle suivant ; 5 relances demandées (moins d'une minute chacune) |
| Bibliothèques | celles du bot : versions testées, aucune faille connue sur 70 ; celles du PC : 1 sur 226 |
| Code | 65 modules, 23 747 lignes, 1 051 fonctions ; 508 tests (495 réussis, 13 sans objet sur ce PC), ruff sans remarque, contrôles GitHub au vert |
| Rapport de 12 h 50 | 32 contrôles conformes sur 40, 3 à corriger (clé Binance, alertes, disponibilité) |

### Ce qui a été vérifié et tient (30/09, 13 h)

| Contrôle | Résultat |
| --- | --- |
| Stratégie avec les nouvelles bibliothèques | chiffres identiques à ceux de 10 h, au trade près : +33,5 % par an, pire baisse −24,7 %, 313 trades, +1,12 R ; 12 derniers mois +33,3 % ; tournoi inchangé. Verdict du diagnostic : tout est conforme |
| Ordres du jour, répétés sans clé ni envoi | Binance joignable (405 ms), règles des 21 paires conformes, ordres d'achat et de stop constructibles |
| Achats du 26/09 (faits 23 h après la clôture du signal, au démarrage) | payés 0,65 % moins cher que cette clôture : ce retard n'a rien coûté |
| Sauvegardes | les deux bases gardées se rouvrent : intégrité correcte, 6 positions et liquidités identiques à la base en service |
| Secrets | aucune clé dans les 152 fichiers publiés ni dans les 88 versions de l'historique ; `.env`, base, journaux et dossier `.venv` jamais publiés |
| Accès | panneau et démonstration à l'écoute sur ce PC seulement (127.0.0.1) ; pare-feu et antivirus actifs |
| Démarrage avec l'ordinateur | commande rejouée : elle passe par les bibliothèques du bot et ne lance rien en double |
| Saisie masquée des secrets | essayée dans une vraie fenêtre, à travers le passage aux bibliothèques du bot : rien ne s'affiche, tout est reçu |

### Constats de 13 h

| N° | Constat | Gravité | Suite |
| --- | --- | --- | --- |
| E1 | **Aucune alerte ne vous parvient toujours.** Gmail refuse le mot de passe depuis hier ; après 5 essais en 16 heures (chaque rapport en fait un), il coupe maintenant la connexion | Élevée | Créer le mot de passe d'application (myaccount.google.com/apppasswords) et le saisir dans la fenêtre « TrendGuard - Alertes », rouverte dans la barre des tâches. **Code, fait le 30/09** : après trois refus, un seul essai par jour, pour ne pas faire bloquer votre compte |
| E2 | **Mémoire du PC presque pleine** : 20,4 Go réservés sur 22,9 possibles (89 %), 2,3 Go libres. Chrome en prend 6,0 Go (44 processus), VS Code 4,8 Go. Le bot et ses compagnons en réservent 1,75 Go, dont 0,65 Go pour la démonstration et la fenêtre des alertes. Si la mémoire sature, Windows peut arrêter le bot (le superviseur le relance) | Moyenne | Vous : fermer des onglets de Chrome, et la démonstration quand elle ne sert plus. **Code, fait le 30/09** : chaque processus du bot réserve trois fois moins de mémoire (104 Mo au lieu de 341), et le rapport comme le centre de sécurité préviennent quand 90 % de la mémoire est réservé |
| E3 | **Disque presque plein** : 17 Go libres sur 240 (7 %). Le rapport ne le signale que sous 2 Go ; Windows a besoin de place pour ses mises à jour | Moyenne | Vous : libérer de la place. **Code, fait le 30/09** : le rapport et le centre de sécurité préviennent sous 10 % |
| E4 | **Après un redémarrage de Windows, le bot attend l'ouverture de votre session.** Aucun redémarrage en attente aujourd'hui ; Windows redémarre seul entre 1 h et 8 h après une mise à jour : le bot resterait arrêté jusqu'au matin | Moyenne en paper, bloquant pour le réel | Petit serveur (plan, ligne 13) ; d'ici là, rouvrir la session après chaque mise à jour de Windows |
| E5 | **Proposition n° 3 devenue inutile et en conflit** : elle retirait du README un nombre de tests périmé, corrigé entre-temps pendant la mise à jour. GitHub ne peut plus la fusionner | Faible | Vous : la fermer sur GitHub (« Close pull request ») |
| E6 | Horloge de Windows jamais synchronisée (source : horloge interne), en retard de 1,1 s | Faible : le bot prend l'heure de Binance | Plan, ligne 5 |
| E7 | L'arrêt d'urgence et le profil prudent sont évalués une fois par jour, à la clôture, comme dans le backtest. Entre deux clôtures, ce sont les stops qui protègent. Le panneau affiche donc un « plus haut » de clôture (10 074,83) inférieur au capital en direct | Information | **Fait le 30/09** : le plus haut affiché n'est jamais sous le capital en direct |
| E8 | `verify` s'arrête dès que la clé est refusée, sans répéter les ordres ; la répétition ci-dessus a dû être lancée sans clé | Faible | **Fait le 30/09** : la clé refusée est signalée, puis le marché est vérifié sans elle |
| E9 | Le dossier `.venv` est utilisé dès qu'il existe, même incomplet (installation interrompue) : le bot ne démarrerait pas | Faible : dossier complet et contrôlé chaque nuit | **Fait le 30/09** : un dossier incomplet n'est pas utilisé, le bot garde les bibliothèques du PC |
| E10 | Branche principale du dépôt non protégée sur GitHub. Le bot n'installe que ce que vous avez fusionné vous-même, et le refuse sinon | Information | Avant le réel : exiger les contrôles GitHub avant toute fusion |
| E11 | Réseau : Wi-Fi partagé (signal 100 %), adresse publique changeante (adresse A à 13 h), latence de 330 à 400 ms vers Binance | Information | Confirme D4 : clé à adresse fixe impossible sur cette connexion |

## Diagnostic approfondi du 30 septembre, 10 h

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
| D4 | **Clé Binance refusée** (erreur −2015). L'adresse du PC sur Internet change (adresse C à la première vérification, adresse A le 30) : une clé limitée à une adresse ne peut pas tenir sur cette connexion | Aucune en paper ; bloquante pour le réel | Pour le réel : une machine à adresse fixe (petit serveur), qui règle aussi D2 |
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
| Tests sur le PC avec ces bibliothèques | jamais lancés avec les versions testées | 495 réussis, aucun échec |
| Bibliothèques du PC (autres logiciels) | 15 sur 226 avec une faille connue | 1 sur 226 : 14 corrections compatibles installées ; les logiciels qui en dépendent se chargent comme avant (73 modules essayés avant et après) |
| Rapport quotidien | ne contrôlait pas les bibliothèques du PC | deux contrôles : « Bibliothèques du bot » (versions testées) et « Failles connues des bibliothèques » |

Le bot et le panneau ont été relancés à 12 h 06 avec ces bibliothèques (moins
d'une minute d'arrêt). Toute commande `python trendguard_bot.py …` passe
d'elle-même par le dossier `.venv`, le démarrage avec l'ordinateur aussi ; si ce
dossier disparaît, le bot tourne avec les bibliothèques du PC et le rapport le
signale. Une bibliothèque du PC garde une faille connue, sans effet sur le bot :
`curl-cffi`, retenue par yfinance, qui n'accepte pas la version corrigée. La
liste des versions d'avant est gardée dans le dossier `sauvegardes/` : tout peut
être remis comme avant.

Le premier rapport généré ensuite a montré un défaut resté caché : lancé depuis
un terminal PowerShell 7, le bot ne pouvait plus lire les droits du fichier des
secrets (« vérification impossible »). Corrigé le jour même : ce contrôle
fonctionne de nouveau, quelle que soit la façon dont le bot a été lancé.

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
| S1 | Clés montrées dans une conversation : l'ancienne clé, puis la nouvelle clé API (collée plusieurs fois ; son secret, lui, n'a pas été montré) | **Élevée avant le réel** | **Fait de notre côté le 01/10** : les trois clés exposées sont retirées de ce PC (conversations de Claude Code, fichier `key.txt` du Bureau), la clé en service n'en fait pas partie, `set-keys` les refuse et le centre de sécurité le vérifie. Reste à vous : les supprimer sur Binance |
| S2 | La clé enregistrée n'a pas le droit « Trading Spot » | Aucune en paper | **Changé le 30/09** : droit coché par vous, avec la restriction IP ; retrait toujours interdit |
| S3 | Pas de restriction d'adresse IP sur la clé | Moyenne | **Fait le 30/09** : restriction active ; à mettre à jour quand l'adresse de la connexion change |
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
| 2 | Clé Binance : de nouvelles clés sont enregistrées (supprimer sur Binance les anciennes, montrées dans la conversation, si ce n'est pas fait) ; la nouvelle était refusée parce que l'adresse du PC sur Internet change | Vous | **fait** (30/09, 22 h) : adresse du PC autorisée, clé acceptée ; à refaire quand l'adresse change ; pour le réel, une machine à adresse fixe (ligne 13) et une clé neuve (constat E5) |
| 3 | Laisser tourner en paper jusqu'à 10 à 20 trades vendus, puis comparer à l'attendu (section « Réel vs attendu » du diagnostic) | Vous et le bot | plusieurs semaines |
| 4 | **Disponibilité** : PC branché et allumé en continu, veille désactivée sur secteur, capot fermé = « Ne rien faire » sur secteur ; ou un petit serveur | Vous et le bot | **en partie fait** : veille sur secteur « Jamais » (29/09), capot fermé « ne rien faire » (30/09, appliqués par le bot). 65 % sur 7 jours au 30/09. Reste à vous : PC branché, et fermer le capot plutôt qu'appuyer sur le bouton d'alimentation |
| 5 | Synchroniser l'horloge de Windows | Vous | **plus nécessaire** (01/10, 14 h) : le service « Temps Windows » démarre de lui-même quand il le faut (3 synchronisations en 3 jours), heure juste à 0,2 s près ; le bot se cale de toute façon sur l'heure de Binance |
| 6 | Découper les fonctions les plus complexes et `app.js` | Code | **fait** : les six plus lourdes découpées, `app.js` en quatre modules ; un test d'horodatage fragile rendu fiable |
| 7 | Décider du sort de l'ancien bot V29 (le supprimer allégerait le dépôt) | Code | **décidé : gardé à part.** Son moteur d'exécution est aussi celui de TrendGuard ; le supprimer obligerait à retoucher le code des ordres réels pour peu de gain |
| 8 | Avant le réel : `verify` complet, quelques jours de testnet, au moins 100 USDT | Vous | le moment venu |
| 9 | Mesurer la disponibilité du bot dans le panneau et prévenir quand il a été arrêté plus d'une heure | Code | fait (29/09) |
| 10 | Centre de sécurité : « Alertes » à corriger si le dernier envoi a échoué | Code | fait (29/09) |
| 11 | Mettre les bibliothèques Python du PC aux versions testées sur GitHub, et corriger les 6 qui ont des failles connues (aiohttp, cryptography, requests, urllib3, anyio, setuptools) ; tests, puis relance du bot | Code, avec votre accord | **fait** (30/09) : le bot a ses propres bibliothèques (dossier `.venv`), aux versions testées et sans faille connue ; sur le PC, 14 corrections de sécurité compatibles, 1 restante sans effet sur le bot (`curl-cffi`, retenue par yfinance) |
| 12 | Rapport quotidien : contrôler les bibliothèques installées sur le PC (versions testées, failles connues) | Code | **fait** (30/09) : deux contrôles dans la section Sécurité du rapport |
| 13 | Avant le réel : faire tourner le bot sur une machine allumée en permanence, à adresse fixe (petit serveur) | Vous | le moment venu ; règle la disponibilité et la clé Binance |
| 14 | Fermer la proposition n° 3 sur GitHub (devenue inutile, en conflit) ; fermer la démonstration du port 8799 quand elle ne sert plus | Vous | quand vous voulez |
| 15 | Alimentation du portable signalée (tableau de bord, centre de sécurité, rapport) ; réglage caché du capot lu | Code | fait (30/09) |
| 16 | Libérer de la mémoire (onglets de Chrome) et de la place sur le disque | Vous | **en partie fait** (30/09, 17 h 40) : 4,1 Go de fichiers temporaires et de caches retirés, 18,7 Go libres sur 240 ; il en faut 24 pour repasser au-dessus de 10 % |
| 17 | Six améliorations du code : superviseur allégé ; rapport qui prévient quand la mémoire ou le disque se remplissent ; `verify` qui continue sans clé ; repli si le dossier `.venv` est incomplet ; « plus haut » en direct dans le panneau ; e-mail mis en pause après trois refus du mot de passe | Code, avec votre accord | **fait** (30/09) : les six sont en service ; code restructuré (rapport en quatre fichiers, centre de sécurité du panneau à part) et harmonisé, détail dans [`DIAGNOSTIC_CODE.md`](DIAGNOSTIC_CODE.md) |
| 18 | `urllib3` 2.8.0 (trois failles corrigées) avec la version de ccxt qui l'accepte | Code | **fait** (01/10, 13 h) : ccxt 4.5.85, publiée le matin, l'accepte ; le contrôle a échoué comme prévu, les bibliothèques du bot sont à jour |
| 19 | Palier de risque de 1 % à 2 % choisi par l'analyse du bot, arrêt d'urgence levé seul avec prudence, raisonnement qui dit pourquoi rien ne bouge | Code, à votre demande | **fait** (30/09, 21 h) : en service ; premier examen, le bot garde 1 % ([`ADAPTATION.md`](ADAPTATION.md), section 6) |
| 20 | Alertes : afficher la vraie cause d'un échec (mot de passe refusé) plutôt que la dernière coupure du réseau ; renvoyer les alertes critiques perdues pendant une coupure | Code, avec votre accord | **fait** (01/10) |
| 21 | Disque : une seule mesure et un seul seuil dans le diagnostic, le rapport et le panneau | Code, avec votre accord | **fait** (01/10) |
| 22 | Sécurité renforcée (clé Binance au moindre privilège, historique des commandes, dossier hors du nuage, chiffrement, mises à jour de Windows), rapport après chaque compétence acquise, rapport de sécurité dans le panneau et renvoyé à la demande | Code, à votre demande | **fait** (01/10) |
| 23 | Prévenir par e-mail et WhatsApp quand le PC passe sur batterie ; en paper, une clé refusée devient une information ; commande `rapport` et contrôles de santé découpés | Code, avec votre accord | **fait** (01/10, 6 h) : G6, G7, G8 |
| 24 | Fermer les propositions n° 3 et n° 4 sur GitHub (en conflit, déjà faites) | Vous | quand vous voulez |
| 25 | Faire acheter plus le bot (budget libéré quand les stops montent, 8 % cumulé, 0,75 % par achat) | Étude, à votre demande | **écarté** (01/10, 13 h) : plus de trades, mais moins bon ([`ADAPTATION.md`](ADAPTATION.md), section 6) |
| 26 | Libérer le disque (6 % libre) et la mémoire (94 %) : trier Téléchargements (25 Go, sans toucher au dossier `claude`, qui contient le bot), fermer des onglets de Chrome (7,2 Go), puis redémarrer le PC | Vous | **dès que possible** (01/10, 14 h) : H1, H2 |
| 27 | Rapport qui dit où part la place du disque et quels programmes prennent la mémoire ; adresses publiques retirées des fichiers publiés | Code, avec votre accord | **fait** (01/10, 21 h) : H7, H8 ; un test refuse désormais toute adresse réelle dans les fichiers publiés |
| 28 | Panneau ▸ Cryptos : une case cochée ne doit jamais être effacée par une actualisation | Code | **fait** (01/10, 15 h) : H9 |
| 29 | Au bureau, rester sur le réseau Wi-Fi stable (B) et décocher « Se connecter automatiquement » pour l'autre ; oublier les réseaux Wi-Fi qui ne servent plus | Vous | dès que possible (01/10, 16 h) : I1, I3 |
| 30 | Rapport qui lit le journal du Wi-Fi (coupures par réseau, réseau à préférer, partage de connexion d'un téléphone) | Code, avec votre accord | **fait** (01/10, 21 h) : I6 |
| 31 | Noyau de savoir : presse, moteurs de recherche, forums, réseau social, tendances et avis des IA lus toutes les heures, chaque source jugée sur les cours réels ; l'avis des sources prouvées peut seulement reporter un achat | Code, à votre demande | **fait** (01/10, 20 h) : [`SAVOIR.md`](SAVOIR.md) |
| 32 | Bot libre (second portefeuille fictif qui agit librement sur le noyau de savoir et révise ses propres règles), lecture d'Internet toutes les 15 minutes ; capital changé sans faux arrêt d'urgence ; cinq stratégies de traders célèbres au tournoi ; guide du trading | Code, à votre demande | **fait** (05/10) : [`LIBRE.md`](LIBRE.md), [`TRADING.md`](TRADING.md) ; refusés : lecture à la seconde, dark web et Tor, Kali Linux, modèle d'IA à entraîner |
| 33 | Écran bleu de Windows du 5/10 : diagnostic de la mémoire (`mdsched`), mises à jour de Windows et du pilote Intel Optane/RST | Vous | dès que possible (06/10) : J2 |
| 34 | Rapport qui surveille les plantages de Windows ; filtre de liquidité pour le bot libre | Code, avec votre accord | **fait** (06/10) : J10, J11 |

Fait depuis l'audit du matin : chemins de secours des ordres réels testés (et
un défaut corrigé), blocage des mots de passe ratés, écriture sûre du choix des
cryptos sous Windows, actualités encadrées pour l'IA de Rachelle, pip-audit et
ruff sur GitHub, code rangé en paquets avec une seule commande, ancien bot V29
mis à part, boucle de backtest unique (rapports des études identiques à l'octet
près), anticipation des ventes, achats et risques, centre de sécurité, sélection
auto (21) ou manuelle (10 plus rentables au départ), saisie des clés plus sûre,
clés jamais transmises en paper.
