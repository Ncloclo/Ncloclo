# Le prompt maître appliqué à TrendGuard

Votre demande (6 octobre) : utiliser le « prompt maître ultime » (67
sections : plateforme IA cognitive, multi-IA, multi-agents, finance
quantitative, risque, trading) pour améliorer le bot.

Le prompt lui-même impose d'avancer par phases validées (§57, §58) et de ne
jamais passer du code au réel sans étapes (§54). Je l'ai donc appliqué comme
une **grille d'exigences** : ce que le bot faisait déjà, ce qui manquait et a
été ajouté le 6 octobre (en deux phases, à votre deuxième demande « améliore
avec ce prompt »), ce qui viendra ensuite, et ce qui ne s'applique pas. Pas de
réécriture : le bot tourne, ses tests passent, et ses règles sont éprouvées
sur 8 ans.

## Ce que le bot faisait déjà

| Exigence du prompt | Dans TrendGuard |
| --- | --- |
| Multi-IA derrière une même interface (§6) | veille : Claude, GPT, Gemini, DeepSeek, Mistral, Kimi, Perplexity, Grok ; poids de chaque IA selon sa fiabilité mesurée (`market_watch.py`) |
| Recherche, mémoire, apprentissage (§31, §39, §45) | noyau de savoir (`savoir.py`), apprentissage libre (`learning.py`), évolution encadrée (`evolution.py`) |
| Pas de fuite d'information (§16) | indicateurs causaux, décision à la clôture, exécution après ; mêmes fonctions pour le backtest et le bot ; actifs disparus inclus (biais du survivant) |
| Backtest réaliste (§19) | frais et glissement, plafonds du bot ; rendement, baisse maximale, Sharpe, Sortino, Calmar, espérance, facteur de profit |
| Hors échantillon, walk-forward (§20, §61) | choix sur 2018-2022, vérification depuis 2023, walk-forward ([`TRENDGUARD_REPORT.md`](TRENDGUARD_REPORT.md), [`ROBUSTESSE.md`](ROBUSTESSE.md)) |
| Monte-Carlo (§21) | épreuve du hasard (3 ans rejoués 1 000 fois), Monte-Carlo des trades |
| Moteur de risque (§23, §24, §30) | 1 % par achat, plafond cumulé, nombre de positions, 25 % par position, profil prudent, arrêt d'urgence |
| Modes et passage au réel (§28, §29) | rejeu, paper, testnet, réel verrouillé (deux réglages explicites), `verify` sans ordre |
| « NO TRADE » préféré (§25, §62) | aucun achat sans cassure, en marché baissier, budget plein, retrait annoncé par Binance, carnet d'ordres anormal |
| Exécution sûre (§27) | intention écrite avant chaque ordre, rapprochement au démarrage, stop de secours posé chez Binance |
| Recherche séparée de la production (§40, §64) | `research/`, laboratoire, épreuves de l'évolution ; bot libre en argent fictif |
| Familles de stratégies (§18) | tournoi de 12 stratégies, dont 5 de traders célèbres ([`STRATEGIES.md`](STRATEGIES.md)) |
| Tableau de bord, alertes, surveillance (§46, §47, §51) | panneau, alertes e-mail et WhatsApp, disponibilité, ressources du PC, plantages de Windows, rapport quotidien |
| Sécurité (§48) | clé au moindre privilège, secrets hors du dépôt public, panneau limité au PC, centre de sécurité |
| Tests et robustesse (§52, §53) | plus de 560 tests : pannes réseau, données manquantes, horloge, reprise après plantage ; contrôles GitHub |
| Pas d'invention (§60) | données réelles de Binance, études reproductibles, résultats écrits tels quels |

## Ajouté le 6 octobre

| Exigence du prompt | Ce qui a été fait | Effet sur les décisions |
| --- | --- | --- |
| Moteur de régimes de marché (§8), « dans quelles conditions la stratégie a-t-elle échoué ? » (§45) | `regimes.py` : tendance de BTC, volatilité, appétit pour le risque, phase (crise, reprise) ; étude [`REGIMES.md`](REGIMES.md) | aucun : information affichée et gardée avec chaque trade |
| Moteur « NO TRADE » et garde avant exécution (§29, §44, §53) | `garde.py` : données du jour, mouvement de BTC, perte du jour, place sur le disque ; un seul « non » et aucun achat ce jour-là | seulement dans l'anormal (voir l'étude ci-dessous) |
| Journal des trades et analyse après trade (§37, §38) | `postmortem.py` : régime à l'achat, meilleur et pire moment en R, glissement sous le stop, leçon en clair ; colonne « Leçon » dans le panneau, bilan dans le rapport | aucun : apprendre et expliquer |
| VaR et CVaR (§13, §23) | `risque.py` : risque d'un jour du portefeuille, année écoulée rejouée avec les positions actuelles | aucun : mesure affichée dans le raisonnement |

## Ajouté le 6 octobre, deuxième phase

| Exigence du prompt | Ce qui a été fait | Effet sur les décisions |
| --- | --- | --- |
| Qualité des données (§11) | `qualite.py` : note sur 100 des clôtures de l'année avant chaque décision (dernière bougie du jour, une bougie par jour, ni doublon ni trou, aucun prix manquant, nul ou négatif, pas de cours figé trois jours, mouvements de plus de 50 % en un jour signalés) | aucun : la garde bloque déjà les achats quand trop de clôtures manquent ; la note s'affiche dans le rapport, et dans le raisonnement dès qu'elle baisse |
| Tests de résistance élargis (§35) | `stress.py` : à chaque décision, ce que coûteraient aux positions du moment un krach de 20, 35 ou 50 % sans exécution des stops (ou Binance en panne pendant la chute), une crise de liquidité (ventes 10 % sous les stops), un pic de volatilité (tous les stops touchés, 2 % de glissement), le retrait de la cote de la plus grosse position, un décrochage de l'USDT de 10 % ; et si l'arrêt d'urgence se déclencherait | aucun : mesure (raisonnement, page Positions, rapport) |
| Attribution de performance (§36) | `attribution.py` : résultat par crypto (réalisé et en cours), par régime à l'achat, par type de sortie, par leçon ; part des gains venue des trois meilleurs trades ; « pourquoi ai-je gagné, pourquoi ai-je perdu ? » en clair | aucun : page Positions et rapport |
| Calendrier d'événements (§33) | `evenements.py` : annonces américaines à fort impact (Fed, inflation, emploi…) d'après le calendrier public de ForexFactory, relu au plus toutes les 6 heures par le noyau de savoir ; montrées dans la page Veille et dans le raisonnement 48 heures avant ; gardées pour mesurer, avec le temps, combien le bitcoin bouge ces jours-là | aucun : aucun achat n'est bloqué tant qu'un effet n'est pas prouvé (au moins 10 jours d'annonce mesurés, puis une étude) |
| Registre des expériences et des versions (§41, §42), carte du modèle (§50) | `registre.py` : chaque épreuve quotidienne de l'évolution et chaque fin d'essai notées (numéro, date, version du code, empreinte des données, réglages, résultats, conclusion) ; `registre rejouer <n°>` refait l'expérience et dit si les résultats sont identiques ; `registre carte` : usage, règles en vigueur, validation, limites, garde-fous | aucun : traçabilité ; l'évolution décide comme avant |

Commandes :

    python trendguard_bot.py registre                 # les dernières expériences
    python trendguard_bot.py registre rejouer E0001   # refaite à l'identique ?
    python trendguard_bot.py registre controle        # rejeu des réglages en vigueur, noté
    python trendguard_bot.py registre carte           # la carte du modèle

**Ce que dit l'étude des régimes** : depuis 2018, la stratégie n'a perdu en
moyenne dans aucun régime où elle achète. Elle gagne le moins en phase de
« reprise » (+0,29 R par trade) et quand l'appétit pour le risque est
« mitigé » (+0,51 R), le plus en tendance haussière et en « risk-on »
(+1,25 et +1,32 R).

**Seuils de la garde** (`python -m research.garde --cache data_binance`) :
une règle n'est gardée que si elle ne change rien à la stratégie sur 8 ans.

| Règle | 2018-22 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar | hasard 1 fois sur 20 | jours sans achat |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bot sans garde | +31,8 % | −20,6 % | 1,54 | +32,1 % | −24,2 % | 1,33 | −34 % | 0 |
| perte du jour ≥ 3 % | +31,3 % | −23,4 % | 1,34 | +30,9 % | −24,2 % | 1,28 | −35 % | 66 |
| perte du jour ≥ 5 % | +31,8 % | −20,6 % | 1,54 | +30,9 % | −24,2 % | 1,28 | −35 % | 27 |
| **perte du jour ≥ 8 %** (retenue) | +31,8 % | −20,6 % | 1,54 | +32,1 % | −24,2 % | 1,33 | −34 % | 5 |
| BTC bouge de ≥ 10 % en un jour | +32,1 % | −23,5 % | 1,37 | +32,4 % | −24,2 % | 1,34 | −35 % | 62 |
| **BTC bouge de ≥ 15 % en un jour** (retenue) | +31,8 % | −20,6 % | 1,54 | +32,1 % | −24,2 % | 1,33 | −34 % | 8 |

Des seuils plus stricts font moins bien : s'interdire d'acheter après une
mauvaise journée fait manquer des reprises. Les seuils retenus ne changent
aucun résultat passé et bloquent seulement l'exceptionnel (krach, donnée
aberrante).

## Étape 2 du prompt (V0 → V1) : contrats, porte d'exécution, audit, mode sûr

Votre deuxième document demande de passer à la construction technique :
contrats explicites entre les modules, contrôle déterministe du risque avant
tout ordre, audit, mode sûr, versions, tests de contrat. TrendGuard n'est pas
une plateforme à bâtir de zéro : il tourne déjà, en paper, avec plus de 600
tests. J'ai donc appliqué l'étape 2 là où elle rend le bot plus sûr, sans
rien réécrire, et sans changer une seule décision de trading (un test rejoue
le bot avec et sans la porte : mêmes trades).

| Exigence de l'étape 2 | Ce qui a été fait |
| --- | --- |
| Contrats explicites, versionnés, registre des contrats, matrice, niveaux de priorité (§3-5, §41, §44, §60-61, §71) | `contrats.py` : 17 contrats (producteur, consommateur, entrée, sortie, erreurs, droits, délai, nouveaux essais, unicité, trace, fichiers, niveau) ; [`CONTRATS.md`](CONTRATS.md) en est tiré et un test vérifie qu'ils restent identiques |
| Schémas stricts, erreurs standard (§4.3, §36, §64) | intention d'achat, décision de risque, autorisation, mode sûr : vérifiés à la création ; valeur manquante, mauvais type, hors limites ou autre version refusés, jamais corrigés en silence (enveloppe d'erreur : code, catégorie, nouvel essai possible) |
| Moteur de risque, porte d'exécution, chaîne Signal → Décision → Risque → Autorisation → Ordre sans raccourci (§26, §28, §29, §54) | `porte.py` : 17 contrôles fixes avant CHAQUE achat, en paper comme en réel (mode réel armé, porte du réel, arrêt d'urgence, mode sûr, garde du jour, décision du jour, crypto autorisée, doublon, positions, risque de l'achat, risque cumulé, taille, argent disponible, stop sous le prix, montant minimum, qualité des données au moins 50/100, évaluation du jour du moteur de risque, étape 11) ; APPROVED, REJECTED ou EMERGENCY_BLOCK avec la raison ; autorisation valable 5 minutes |
| Le LLM n'est jamais l'autorité finale (§62-63, §73) | déjà vrai et désormais écrit dans les contrats : les IA donnent un avis, le noyau de savoir peut seulement reporter un achat, la porte est faite de règles fixes |
| Unicité des opérations critiques (§38) | une seule intention d'achat par crypto et par décision (clé jour:crypto:BUY) ; en réel, l'intention est écrite avant l'ordre et résolue par l'identifiant client (moteur v29) |
| Traçabilité de bout en bout (§6, §31) | chaque achat et chaque trade portent l'identifiant de la décision du jour, du contrôle du risque et de l'autorisation |
| Audit infalsifiable (§35) | `audit.py` : une ligne par opération critique (contrôle de la porte, achat, vente, arrêt d'urgence et reprise, nouvel essai paper, réglages de l'évolution, sélection, mode sûr), chaînée à la précédente par son empreinte : une ligne modifiée ou retirée se voit (rapport quotidien, `python trendguard_bot.py audit`) ; un achat qu'on ne peut pas tracer est refusé |
| Mode sûr (§53) | `python trendguard_bot.py mode-sur on` : plus aucun achat ; le bot continue de lire, d'analyser, de protéger et de vendre ; alerte dans le tableau de bord, ligne dans le raisonnement et le rapport ; `mode-sur off` pour le lever |
| Tests de contrat (§43, §66) | `tests/test_contrats.py` : entrée valide, invalide, champ manquant, mauvais type, mauvaise version, demande non autorisée (réel non armé), délai dépassé (autorisation expirée), doublon, réponse incohérente ; porte dans le bot en paper et en réel |

**Déjà en place, sous un autre nom** : santé des services et registre (§45-47)
= superviseur, centre de sécurité du panneau, rapport quotidien ; données
périmées → pas d'achat (§39, §52) = garde « NO TRADE » et décision reportée
quand les bougies manquent ; promotion et retour arrière (§55-57) = paper
avant réel, essais de 30 jours de l'évolution, retour aux réglages d'origine,
mises à jour validées par vous ; observabilité (§58-59) = journaux, panneau,
rapport, registre des expériences.

**Sources de vérité (§48-49)** : une seule par donnée.

| Donnée | Source de vérité | Qui y écrit |
| --- | --- | --- |
| Positions, ordres (réel) | Binance, rapprochée au démarrage par le moteur v29 (base du bot) | moteur v29 |
| Positions (paper), trades, état du bot | base du bot (`trendguard_paper.db`) | le bot seul |
| Bougies | Binance (lecture publique) | personne |
| Réglages de la stratégie | `.env` + fichier de l'évolution | vous, l'évolution (réglages permis seulement) |
| Mode sûr | `<bot>.modesur.json` | vous (commande mode-sur) |
| Expériences | `<bot>.registre.json` | l'évolution, la commande registre |
| Audit | `<bot>.audit.jsonl` | le bot seul, en ajoutant |
| Savoir, bot libre, calendrier | `trendguard_savoir.db` | le noyau de savoir et le bot libre |
| Contrats | `trendguard/contrats.py` | le code, relu par les tests |

**Pas appliqué ici, et pourquoi** : passerelle d'API, WebSocket, identité
multi-locataires et double authentification (§8, §33) : un seul utilisateur,
un panneau limité à ce PC avec mot de passe et contrôle d'origine ; cœur
cognitif, planificateur, orchestrateur d'agents, routeur de modèles, RAG,
synthèse (§9-19) : la décision de trading doit rester faite de règles fixes
(§62-63), mettre un orchestrateur d'IA sur ce chemin irait contre l'étape 2
elle-même ; Pydantic, OpenAPI (§64) : des classes Python simples suffisent,
sans dépendance de plus. La base de données est l'objet de l'étape 3,
ci-dessous.

## Étape 3 du prompt : base de données et socle de données

Le troisième document demande un socle de données complet. Il pose lui-même
deux règles décisives : ne créer une table que si l'on sait pourquoi elle
existe, qui la possède, la lit, la modifie, quel contrat la protège et
combien de temps elle vit (§84) ; ne rien ajouter sans mesure (§85), avec
pour priorités l'exactitude, l'intégrité, la sécurité et la traçabilité
avant tout le reste (§86). Appliquées à TrendGuard, elles donnent 8 tables,
pas 200.

| Exigence de l'étape 3 | Ce qui a été fait |
| --- | --- |
| Lignée financière, « exigence fondamentale » (§38, §72, §78) | `donnees.py` : journal financier dans la base du bot ; chaque trade remonte à son ordre de vente et d'achat, à leurs exécutions, au contrôle du risque et à l'autorisation, à la décision du jour, au signal, à la version de la stratégie, à la qualité et à la source des données (`python trendguard_bot.py donnees lignee`) ; un test le vérifie pour chaque trade, en paper et en réel |
| Contraintes dans la base (§51), identifiants, dates en UTC (§53-54) | clés primaires et étrangères, CHECK (sens, quantités positives, statuts permis, achat impossible sans contrôle ni autorisation), UNIQUE (une intention, un ordre) ; UUID pour les ordres, exécutions et trades ; dates ISO en UTC |
| Historique jamais écrasé, audit en ajout seul (§34, §46) | contrôles du risque et exécutions protégés par des déclencheurs qui refusent toute modification ou suppression ; journal d'audit chaîné (étape 2) |
| Idempotence, rien de doublé après une panne (§69, §79) | clé d'unicité des ordres dans la base : un redémarrage au mauvais moment ne crée jamais un second ordre (testé) |
| Migrations versionnées, reproductibles, réversibles (§58, §83) | migrations numérotées avec retour arrière et empreinte ; une migration modifiée après son application est refusée |
| Registre des stratégies (§27) | chaque version des réglages en vigueur (évolution comprise) et du code, reliée à chaque décision |
| Qualité des données qui bloque (§24, §75-76) | la note des données du jour est gardée avec la décision, et la porte d'exécution refuse tout achat sous 50/100 |
| Sauvegardes réellement restaurées (§59) | la sauvegarde de la nuit est rouverte comme le ferait le bot, son état relu et son journal financier vérifié ; registre, audit et mode sûr sauvegardés avec elle |
| Catalogue des tables, schéma (§82 A-B) | [`DONNEES.md`](DONNEES.md), tiré du code et de la base elle-même (diagramme, fiche de chaque table, colonnes, index) ; un test le garde à jour |
| Propriété des données (§3, §48) | tables du domaine « fin_ », écrites par `donnees.py` seul ; un test refuse tout autre module qui y toucherait |

**PostgreSQL : pas maintenant, et pourquoi.** La base du bot est SQLite :
transactionnelle, avec clés étrangères, contraintes, déclencheurs et
journal d'écriture, sur un seul PC et pour un seul utilisateur. PostgreSQL
demanderait un serveur de plus à faire tourner, à surveiller, à sauvegarder
et à protéger par un mot de passe, sans gain mesuré (§85). Le journal est
écrit en SQL standard : le passage à PostgreSQL resterait possible le jour
où le bot tournerait sur un serveur ou pour plusieurs personnes.

**Pas de table pour ce qui n'existe pas dans le bot** (§84) : utilisateurs,
organisations, rôles, sessions, conversations, tâches, agents, modèles,
mémoire, base vectorielle, recherche, vérification, invites, réservoir
d'indicateurs, notifications, interrupteurs de fonctions. Les données qui
existent déjà ont leur place : noyau de savoir et calendrier
(`trendguard_savoir.db`), expériences (registre), réglages versionnés
(fichier de l'évolution, sa période d'essai et son retour arrière), alertes
(journal du bot). Pas de table de positions non plus : leur source de vérité
reste le portefeuille du bot ou Binance ; une seconde vérité serait
concurrente (§49). Pas de file d'événements, d'« outbox » ni de file
d'erreurs : il n'y a pas de bus d'événements à alimenter, chaque écriture
est une transaction de la même base.

## Étape 4 du prompt : noyau cognitif

Le quatrième document demande un « système nerveux central » : comprendre
une demande, planifier, orchestrer des tâches, vérifier, mesurer
l'incertitude, décider, sans jamais laisser une IA ou un agent agir seul.
Appliqué à TrendGuard : un noyau cognitif déterministe, dont la première
tâche est celle que vous demandez le plus souvent, « analyse et diagnostique
expert ». Le bot la fait désormais lui-même (`python trendguard_bot.py
expert`, et chaque jour à 00:45 UTC), en lecture seule ; Rachelle en résume
le résultat. Détail : [`COGNITIF.md`](COGNITIF.md).

| Exigence de l'étape 4 | Ce qui a été fait |
| --- | --- |
| Planificateur, graphe de tâches, validation du graphe (§20-23) | `cognitif.py` : plan en graphe ; cycle, dépendance absente, outil inconnu ou interdit : plan refusé ; tâches indépendantes lancées en parallèle |
| Orchestrateur, machines d'états, délais, nouveaux essais, annulation, résultats partiels (§24-25, §62-65, §70, §87) | états contrôlés (une transition impossible est une erreur) ; délai par tâche et budget total ; nouvel essai seulement pour une panne passagère ; annulation propagée ; résultat PARTIEL dit comme tel |
| Registre et politique des outils (§34-38) | outils inscrits avec classe, risque, délai, réseau ; en marche autonome, aucun outil d'exécution : le noyau ne peut ni acheter, ni vendre, ni transférer |
| Vérification, contradictions, incertitude (§45-54) | vérifications croisées entre sources indépendantes (audit contre journal financier, positions contre ordres ouverts, décision et données à l'heure) ; incertitude LOW à CRITICAL, sans confiance chiffrée inventée |
| Synthèse et décision, séparée de l'autorisation (§55-61) | NO_ACTION, ESCALATE, RESEARCH_MORE ou BLOCK ; une mesure proposée (par exemple le mode sûr) attend toujours votre accord |
| Trace lisible (§67-68) | plan, tâches, durées, vérifications, décision et propositions gardés dans `<bot>.expert.json` |
| Sécurité des instructions (§42-44, §97) | Rachelle refuse les tentatives d'injection d'instructions ; les textes d'Internet restent des données |

**Pas appliqué, et pourquoi** : agents multiples, routeur de modèles et débat
d'IA attendent des clés d'IA (aucune sur ce PC ; Rachelle répond déjà sans IA
en mode dégradé sûr) ; une mémoire vectorielle n'aurait rien de plus à
chercher que le noyau de savoir ; pas d'API réseau supplémentaire (le
panneau n'écoute que ce PC) ; et jamais d'IA sur le chemin des ordres.

## Étape 5 du prompt : socle multi-agents et comité d'agents financiers

Le cinquième document demande une « intelligence collective contrôlée » :
des agents spécialisés, déclarés et limités, qui débattent, se critiquent,
se vérifient et forment un consensus, sans jamais pouvoir passer un ordre.
Appliqué à TrendGuard : un socle multi-agents (`agents.py`) et un comité de
onze agents financiers (`comite.py`) qui donne chaque nuit son avis sur les
cryptos que la règle propose d'acheter. Détail : [`AGENTS.md`](AGENTS.md).

| Exigence de l'étape 5 | Ce qui a été fait |
| --- | --- |
| Registre, manifestes, cycle de vie (§4-5, §62) | chaque agent déclaré (version, rôle, lectures permises, poids, veto, délai) ; un agent qui demanderait de passer des ordres est refusé |
| Superviseur, équipe, délégation (§11-16) | équipe choisie par capacités ; analyses en parallèle puis contrôles, sur le moteur d'orchestration de l'étape 4 |
| Bus de messages, tableau noir cloisonné (§18-21) | messages tracés et non rejouables ; chaque agent ne lit que ses rubriques ; pas d'avis des autres avant le sien |
| Débat, désaccord, consensus, quorum (§23-26) | révision sur preuves seulement ; désaccord mesuré ; consensus pondéré, dit « faible » quand il l'est ; agents critiques obligatoires |
| Critique, équipe rouge, vérification indépendante, « pas de trade » (§27-29, §43) | onze agents : données, technique, quant, régime, sentiment, risque (veto), portefeuille, « pas de trade », critique, équipe rouge, vérificateur |
| Quarantaine, disjoncteur, panne (§45-46, §50, §74) | violation : quarantaine ; trois échecs : disjoncteur ; agent critique absent : BLOCAGE |
| Sécurité financière, quatre yeux (§44, §60-61, §82) | une recommandation n'est jamais une autorisation ; aucun agent n'a accès aux ordres ; la règle et la porte décident |
| Banc d'essai et évaluation (§53-54) | 8 scénarios de référence ; et le comité éprouvé sur 8 ans : [`COMITE_ETUDE.md`](COMITE_ETUDE.md) |

**Ce que dit l'évaluation sur 8 ans** : les achats que le comité approuve
font un peu mieux en moyenne (+1,20 R contre +1,08 R), mais n'acheter que sur
son avis aurait fait nettement moins bien (Calmar 0,83 au lieu de 1,54 sur
2018-2022, 1,20 au lieu de 1,33 depuis 2023). Il reste donc consultatif ; ses
avis sont mesurés sur les trades réels, et une règle ne sera proposée que
s'ils font mieux de façon nette.

## Étape 6 du prompt : socle multi-modèles d'IA

Le sixième document demande de ne jamais dépendre aveuglément d'une seule
IA : registre des fournisseurs et des modèles, routeur, santé, disjoncteur,
repli tracé, budget, confidentialité, versions des invites, consensus et
désaccord, banc d'évaluation, trace de chaque appel. Appliqué à TrendGuard :
`modeles.py`, par lequel passent Rachelle et la veille. Détail :
[`MODELES.md`](MODELES.md).

| Exigence de l'étape 6 | Ce qui a été fait |
| --- | --- |
| Registres, manifestes, capacités (§4-7) | 8 fournisseurs du code et un modèle local facultatif ; configuré seulement si sa clé est dans `.env` ; jamais la clé dans le registre |
| Routeur, santé, disjoncteur, repli (§8-15) | modèle choisi par réussite mesurée et préférence, raison notée ; 3 échecs de suite : mis de côté 15 minutes ; repli tracé, puis réponse intégrée sans IA |
| Plusieurs modèles, consensus, désaccord (§16-19, §65) | la veille interroge toutes les IA ; un désaccord net n'est plus moyenné, il est montré |
| Invites, jetons, budget (§27-33) | version de chaque invite notée et tenue par un test ; jetons notés quand ils sont donnés ; plafond par jour |
| Confidentialité, modèle local, sécurité (§38-43) | un texte qui ressemble à un secret ne sort jamais du PC ; un « modèle local » vers Internet est refusé ; aucune IA n'a d'outil |
| Banc, régression, trace (§44-49, §91) | `python trendguard_bot.py modeles banc` ; table `llm_executions` en ajout seulement ; rapport, page Veille, Rachelle |

**Seconde version du document** (gouvernance, invites, vérification,
coût) : appliquée elle aussi.

| Exigence de la seconde version | Ce qui a été fait |
| --- | --- |
| Cycle de vie, gouvernance (§41, §44-45) | aucun modèle en service sans banc d'évaluation réussi (lancé à la saisie d'une clé) ; banc raté, régression ou nouveau modèle : écarté jusqu'au prochain banc réussi |
| Routage par politique (§7-10) | note pondérée par usage sur mesures réelles (justesse au banc, réussite, rapidité) ; une mesure absente est dite « non mesurée » |
| Invites versionnées (§17-18) | version et empreinte de chaque invite ; un texte changé sans nouvelle version n'est jamais envoyé |
| Sorties vérifiées, invention (§16, §57-58) | la veille redemande une fois une réponse hors schéma ; Rachelle rejette une réponse qui cite un montant absent des données du bot |
| Coût, budget, mode, fiche, mesures (§23, §31, §43, §71) | coût seulement avec un prix que vous déclarez ; plafond par jour ; mode (hybride, extérieur, local, dégradé sûr) ; fiche et mesures de chaque modèle |
| Désaccord (§27) | chaque désaccord net est un contrat (positions, gravité, sans moyenne) ; alerte d'information pour une crypto détenue |

**Honnêtement** : aucune clé d'IA n'est sur ce PC. Les 8 fournisseurs sont
donc « non configurés », aucune mesure n'est affichée, et Rachelle comme la
veille fonctionnent sans IA, comme avant (mode dégradé sûr). Tout s'allume
dès qu'une clé est saisie et que son banc est réussi
(`python trendguard_bot.py watch set-key claude`).

## Spécification des contrats de données v1.0

Ce document demande que toute donnée qui passe d'un module à l'autre ait un
contrat : schéma, version, identité, date, provenance, validation,
classification et trace. L'étape 2 avait déjà posé les contrats du chemin
d'achat ; cette spécification les complète. Tout est dans
[`CONTRATS.md`](CONTRATS.md) (26 contrats, tirés du code), avec les
conventions communes.

| Exigence de la spécification | Ce qui a été fait |
| --- | --- |
| Types communs (§5, §10-14, §59) | confiance (0 à 1, méthode, jamais une probabilité d'avoir raison), incertitude, provenance, montant en décimal exact avec devise, résultat de validation, enveloppe de message, erreur au format commun |
| Versions, compatibilité, migration (§6-7, §55-56) | versions MAJEUR.MINEUR.CORRECTIF ; versions comprises déclarées ; l'erreur v1 de l'étape 2 est migrée en v2, jamais en silence |
| Classification (§9) | les 7 classes ; LOCAL_ONLY ne sort jamais du PC ; chaque contrat a la sienne |
| Validateur et registre (§64-66) | `contrats.validate` refuse un champ inconnu, absent, du mauvais type, hors bornes ou d'une valeur non permise ; le registre dit propriétaire, consommateurs, version, classification |
| Corrélation et cause (§4, §47-48) | le journal d'audit passe en v2 : type d'événement canonique (`Order.Buy.Executed`…), la décision relie tout, et chaque achat a pour cause son contrôle du risque ; montants en décimal avec devise |
| Bougies vérifiées (§41) | une bougie incohérente (plus haut sous la clôture, volume négatif…) écarte la crypto du jour ; pour BTC, la décision attend |
| Pas de regard vers le futur (§42) | chaque décision note la fin de ses données (`data_cutoff_at`) ; des données postérieures sont refusées, par le code puis par la base (migration 3) |
| Appels aux IA, consensus, avis (§24, §28-29, §11-12) | chaque appel à une IA a un identifiant et sa demande (repli compris), vérifiés par contrat ; le consensus des IA a un état (fort, modéré, faible, aucun, conflit) et des scores ; l'avis du comité porte sa confiance et son incertitude |
| Tests négatifs (§77) | champ absent, UUID, valeur non permise, quantité négative, devise, date sans fuseau, confiance hors bornes, jetons négatifs, version inconnue, classification inconnue, données périmées, ordre invalide, contrôle ou autorisation absents |

**Pas appliqué, et pourquoi** : Pydantic, FastAPI, PostgreSQL, bus
d'événements et dossier `contracts/` (un seul PC, un seul processus ; des
classes typées de la bibliothèque standard font le même travail sans
dépendance de plus) ; montants en décimal dans le moteur v29 (il calcule en
flottants arrondis à la précision de Binance ; le changer toucherait au
chemin des ordres sans gain prouvé : le décimal sert aux frontières) ;
identifiants lisibles gardés là où ils existent (`D-2026-10-06`) plutôt que
des UUID, pour que vous puissiez les lire.

## Cadre de priorités et de dépendances

Ce document demande qu'aucune étape ne soit dite terminée parce que son code
existe : priorités (P0 à P4), dépendances typées, états prouvés, note de santé,
et des portes à franchir dans l'ordre, jusqu'au réel. Appliqué à TrendGuard :
`chantiers.py`, et sa feuille de route tirée du code :
[`FEUILLE_DE_ROUTE.md`](FEUILLE_DE_ROUTE.md).

| Exigence du cadre | Ce qui a été fait |
| --- | --- |
| Priorités, états, dépendances typées (§2-8, §17) | 24 composants TASK-000001 à TASK-000024 : priorité, état, couche, dépendances (dure, souple, exécution, données, contrat, sécurité, validation), propriétaire, risque, critère d'acceptation |
| Graphe sans cycle, chemin critique, dépendance inversée interdite (§9, §19, §22) | ordre de construction calculé ; chemin critique jusqu'au réel ; un composant ne dépend jamais de l'aval ; dans le code, aucun module de fondation ni d'intelligence ne charge un module qui passe des ordres (test) |
| Définition de prêt et de terminé, santé (§15-16, §24) | un état « en service » se prouve (code, tests, contrats, documentation, sécurité, observabilité) ; note de santé pondérée et seuil par priorité |
| Portes de mise en service (§25) | portes 1 à 7 mesurées dans le dépôt, toutes franchies ; porte 8 (réel) mesurée chaque jour sur l'état du bot |
| Aucune porte contournée (§25, §27) | porte 8 fermée : la porte d'exécution refuse tout achat réel, même bot en mode réel ; aucune variable ne la désactive |
| « Pas de trade » valide, pas de bouchon caché (§14, §21) | « pas de trade » reste une issue normale ; le seul bouchon (marché de démonstration du panneau) est affiché comme tel |

**La porte du réel aujourd'hui** : fermée, et c'est voulu. Elle demande un
essai paper d'au moins 60 jours avec 10 trades clos, aucun réglage à l'essai,
ni arrêt d'urgence ni mode sûr, des journaux intacts, des alertes configurées,
un rapport quotidien sans défaut de sécurité et une vérification sans ordre
réussie sur Binance réel (`python trendguard_bot.py verify`). Où elle en est :
`python trendguard_bot.py chantiers portes`, le rapport quotidien, ou Rachelle
(« sommes-nous prêts pour le réel ? »).

## Étape 7 du prompt : cœur d'intelligence financière

Le septième document demande un « docteur en finance » : qualité des données,
indicateurs versionnés, anti-fuite, technique, quantitatif, sentiment, régime,
liens entre actifs, scénarios, prévisions, signal, classement, « pas de
trade », confiance, mémoire et calibration. Appliqué à TrendGuard :
`finance.py`, qui analyse chaque nuit chaque crypto, et
`python trendguard_bot.py finance aave`. Détail : [`FINANCE.md`](FINANCE.md).

| Exigence de l'étape 7 | Ce qui a été fait |
| --- | --- |
| Instruments, données, qualité (§6-10) | référentiel des cryptos ; note de qualité par crypto (complétude, fraîcheur, cohérence, fiabilité) |
| Indicateurs, anti-fuite (§14-16, §21-22) | 16 indicateurs versionnés avec la fin de leurs données ; recalculés sur les seules données connues : identiques (test) ; refusés par la base s'ils dépassent leur bougie |
| Régime, liens, sentiment, calendrier (§17-20) | quatre lectures du régime ; contagion entre cryptos ; sources prouvées ; grandes annonces (prudence seulement) |
| Scénarios, prévisions, calibration (§28-35) | fréquences d'un passé comparable avec intervalle et nombre de cas ; cinq scénarios dont la somme fait 1 ; chaque prévision comparée au résultat à 30 jours (Brier, direction, biais) |
| Signal, classement, « pas de trade » (§23-25) | signal de la règle au format commun ; classement indicatif aux pondérations versionnées ; raisons de s'abstenir au format commun |
| Explication, confiance, sécurité (§30, §37-38, §63) | preuves, risques, contradictions, confiances séparées, conditions d'invalidation ; jamais une autorisation |

**Ce qui ne s'applique pas** : analyse fondamentale et valorisation (les
cryptos n'ont ni bilan ni bénéfice), actions, obligations, devises, séries
macroéconomiques (non collectées), modèles ARIMA ou d'apprentissage profond
(aucun gain prouvé). L'analyse le dit à chaque fois, sans rien simuler.

## Étape 8 du prompt : moteur quantitatif

Le huitième document demande un laboratoire mathématique et statistique :
rendements, statistiques, lois, tests, séries temporelles, stationnarité,
volatilité, corrélations, facteurs, régression, cointégration, prévisions,
anomalies, régimes, Monte-Carlo, validation, reproductibilité, sans jamais
passer un ordre. Appliqué à TrendGuard : `moteur_quant.py`, et `python
trendguard_bot.py quant`. Détail : [`MOTEUR_QUANT.md`](MOTEUR_QUANT.md) ;
dernier rapport : [`QUANT.md`](QUANT.md).

| Exigence de l'étape 8 | Ce qui a été fait |
| --- | --- |
| Rendements, statistiques, lois, normalité (§7-10) | annualisation explicite sur 365 jours ; descriptives ; normale, Laplace, Student par maximum de vraisemblance, AIC, BIC, Kolmogorov-Smirnov ; Jarque-Bera, Anderson-Darling |
| Tests, tests multiples (§11-12) | Student, Welch, Mann-Whitney avec effet et taille ; Bonferroni, Holm, Benjamini-Hochberg |
| Séries, stationnarité, persistance (§13-15) | autocorrélations, Ljung-Box, ADF et KPSS (conclusion seulement s'ils s'accordent), ratio de variance |
| Volatilité, liens, facteurs (§16-24) | EWMA et GARCH comparés hors échantillon ; Pearson, Spearman, Kendall, jours de chute ; covariance rétrécie ; composantes principales ; bêta à BTC ; régression et ses diagnostics |
| Pouvoir prédictif de la règle (§28, §43, §52) | signaux étudiés contre un jour ordinaire (intervalles par mois, correction de Holm), momentum, trades en R |
| Anomalies, ruptures, régimes, reproductibilité (§39-41, §63-67) | anomalies classées, jamais corrigées ; ruptures de volatilité ; régimes cachés ; manifeste et empreinte du résultat |

**Ce que disent les chiffres** : les rendements ne sont pas normaux (queues
épaisses), la volatilité se regroupe, et le signal d'achat seul prédit peu ;
l'avantage de la règle vient de la gestion des trades : 41 % de gagnants, un
gain moyen de +4,37 R contre une perte moyenne de −0,97 R. Les 21 cryptos
forment environ 2,6 paris indépendants.

**Ce qui ne s'applique pas** : facteurs d'actions et macroéconomie (des
cryptos), arbitrage statistique comme stratégie (pas de vente à découvert),
ARIMA, apprentissage profond et Black-Litterman (aucun gain prouvé, pas de
vues).

## Étape 9 du prompt : moteur de stratégie

Le neuvième document demande un moteur de stratégies : registre, langage
déclaratif, compilation, validation, entrées et sorties, stops, taille,
Kelly, coûts, liquidité, sur-ajustement, fuite, « pas de trade »,
explication, cycle de vie. Appliqué à TrendGuard : `moteur_strategie.py`, la
règle écrite en fiche et vérifiée chaque nuit, et `python trendguard_bot.py
regle`. Détail : [`MOTEUR_STRATEGIE.md`](MOTEUR_STRATEGIE.md).

| Exigence de l'étape 9 | Ce qui a été fait |
| --- | --- |
| Registre, statuts, cycle de vie (§4-5, §40) | deux fiches (la règle, son profil prudent), « en essai » ; 18 statuts et les passages permis : jamais de brouillon vers le réel |
| Langage déclaratif, compilation (§6) | conditions sur une liste blanche d'indicateurs et d'opérateurs, aucun code exécuté ; valeur inconnue = condition fausse |
| Validation, « prête pour le backtest » (§41, §66) | 12 étapes : schéma, langage, dépendances, réglages dans leurs plages, coûts, liquidité, contraintes, verrous du code, déterminisme, fuite, équivalence, données verrouillées |
| Fidélité à la règle exécutée | 69 909 cas, 2 717 signaux, 0 écart sur 2017-2026 ; vérifiée à chaque décision, un écart est signalé |
| Kelly, sur-ajustement (§15, §27) | Kelly mesuré (≈ 33 % complet), jamais appliqué ; fractions plafonnées à 2 % ; grille de sur-ajustement |
| Décision expliquée, « pas de trade » (§37-39) | chaque crypto : candidate, ou la condition qui manque avec ses valeurs ; jamais une autorisation |

**Ce qui ne s'applique pas** : autres familles et ensembles (le laboratoire
en a essayé douze, aucune ne bat la règle sur les deux époques), optimisation
bayésienne ou génétique (réglages gelés, changés seulement par l'évolution
encadrée), probabilité et rendement attendus d'un trade (non estimés : laissés
vides).

## Étape 10 du prompt : moteur de backtest

Le dixième document demande un moteur de backtest qui prouve au lieu de
promettre : configuration et reproductibilité, données validées, anti-fuite,
exécution réaliste, comptabilité, hors échantillon, walk-forward, robustesse,
probabilité de sur-ajustement, Monte-Carlo, stress, capacité, régimes,
statistique, équipe rouge, verdict et limites. Appliqué à TrendGuard :
`moteur_backtest.py` autour de la boucle du bot, et `python trendguard_bot.py
validation`. Détail : [`MOTEUR_BACKTEST.md`](MOTEUR_BACKTEST.md) ; dernier
rapport : [`VALIDATION.md`](VALIDATION.md).

| Exigence de l'étape 10 | Ce qui a été fait |
| --- | --- |
| Reproductibilité (§5-6, §73) | manifeste : version git, empreintes des données, de la configuration, du code, de l'environnement et du résultat ; refait, identique |
| Données, anti-fuite (§7-9) | dix contrôles avant de simuler, note sur 100 ; un regard vers le futur fait rejeter |
| Exécution, coûts, retard, capacité (§14-21, §45-48) | glissement contre le bot, frais des deux côtés ; frais × 3, glissement + 200 %, achats 1 ou 2 jours en retard ; capacité de 10 000 à 100 millions d'USDT |
| Hors échantillon, walk-forward, robustesse, PBO (§31-38) | 2018-2022 puis depuis 2023 ; walk-forward glissant ; 27 réglages voisins ; probabilité de sur-ajustement 12 % |
| Statistique (§39-40) | Sharpe probabiliste, Sharpe dégonflé (27 et 100 essais), intervalles par blocs, corrections des tests multiples |
| Verdict, prêt pour le risque (§51-53, §81) | réglages en vigueur (profil prudent, 20 positions, 10 % cumulés) : VALIDE, prête pour le moteur de risque, note 98/100 ; un rapport rapide n'est jamais « prêt » |

**Ce qui ne s'applique pas** : ticks, carnet d'ordres, ordres limites et
exécutions partielles (bougies journalières, ordres au marché, moins de
0,03 % du volume du jour), seconde boucle vectorisée (une seule vérité), marge,
financement et emprunt (Spot, sans levier).

## Étape 11 du prompt : moteur de risque

Le onzième document demande un moteur de risque qui mesure, comprend,
agrège, limite, simule, contrôle, alerte et bloque si nécessaire. Appliqué à
TrendGuard : `moteur_risque.py`, une évaluation avant et après les achats de
chaque décision, gardée au journal financier, et `python trendguard_bot.py
risque`. Détail : [`MOTEUR_RISQUE.md`](MOTEUR_RISQUE.md).

| Exigence de l'étape 11 | Ce qui a été fait |
| --- | --- |
| Mesures (§9-12, §15-23) | VaR et perte moyenne au-delà (historique, normale, Student, Monte-Carlo ; 1 à 10 jours), volatilité et EWMA, queue (Hill, valeurs extrêmes), corrélations (trois mesures, en crise, chutes simultanées), concentration, liquidité, baisse, levier |
| Stress, stress inversé (§13-14, §69) | onze chocs, dont krach stops sautés, volatilité × 3, corrélations → 1, liquidité ÷ 2 ; baisse qui coûterait 10 à 50 % du capital ou déclencherait l'arrêt d'urgence |
| Budget, contributions, risque ajouté (§24-26, §50) | budget contre le plafond de la règle ; contribution de chaque crypto ; risque ajouté par les achats du jour |
| Limites, note, état, décision, validité (§39, §47-48, §55, §77-78, §83) | note avec ses dix composantes, état de normal à bloqué, machine à états, décision pour les achats, valable 24 heures |
| Calibrage (§86, §93) | la plus prudente de trois méthodes : bien calibrée sur le backtest de la règle (75 dépassements pour 70 attendus à 95 %, 16 pour 14 à 99 %) ; recalibrée sur le journal du bot |
| Sécurité par défaut (§46, §99) | sans évaluation valide du jour, la porte refuse tout achat (17e contrôle) ; les ventes restent permises ; « bloqué » seulement quand la règle n'achèterait pas ; aucun trade changé (test) |

**Ce qui ne s'applique pas** : grecques, taux, crédit, change, pays et
secteurs (cryptos au comptant contre de l'USDT), GARCH (EWMA à la place),
comité d'agents du risque avec IA (aucune clé), dérogations manuelles aux
limites (une limite change dans le code, tracée et testée).

## Étape 12 du prompt : moteur de portefeuille

Le douzième document demande un moteur qui construit, répartit, contraint,
rééquilibre et explique le portefeuille. Appliqué à TrendGuard :
`moteur_portefeuille.py`, l'état du portefeuille à chaque décision, et
`python trendguard_bot.py portefeuille`. Détail :
[`MOTEUR_PORTEFEUILLE.md`](MOTEUR_PORTEFEUILLE.md) ; étude :
[`PORTEFEUILLE.md`](PORTEFEUILLE.md).

| Exigence de l'étape 12 | Ce qui a été fait |
| --- | --- |
| État, concentration, contributions (§7-9, §27-32) | poids, exposition, liquidités, HHI et nombre effectif, risque engagé contre le plafond, volatilité, part de chaque crypto dans le risque, diversification |
| Optimiseurs (§13-20) | parts égales, inverse de la volatilité, variance minimale, parité de risque, Sharpe maximal, sur les mêmes cryptos, pour comparer |
| Contraintes (§22-26) | une contrainte impossible est expliquée, jamais relâchée ; poids validés (somme, bornes) |
| Rééquilibrage (§37-40) | dérive mesurée ; jamais proposé : il couperait les gagnants, d'où vient le résultat |
| Décision (§61) | dans les contraintes, à regarder (avec la raison) ou aucune position ; jamais un ordre ; aucun trade changé (test) |
| Taille des achats éprouvée (§13, §89-90) | même règle, seule la taille change : le montant égal fait nettement moins bien ; la règle reste |

**Ce qui ne s'applique pas** : couverture (Spot, sans vente à découvert),
plusieurs devises et comptes (un compte en USDT), Black-Litterman et
moyenne-CVaR (pas de vues ; la règle dimensionne des achats, elle ne choisit
pas des poids).

## Étape 13 du prompt : critères d'acceptation du paper

Le treizième document fixe 44 critères mesurables (AC-001 à AC-044) avant de
passer du paper au moteur de politique. Appliqué à TrendGuard :
`acceptation.py`, `python trendguard_bot.py acceptation`, une ligne chaque
nuit dans le rapport, et Rachelle. Détail :
[`ACCEPTATION_PAPER.md`](ACCEPTATION_PAPER.md) ; dernier verdict :
[`ACCEPTATION.md`](ACCEPTATION.md).

| Exigence de l'étape 13 | Ce qui a été fait |
| --- | --- |
| AC-001 à AC-044 (§3-46) | chacun mesuré sur le bot (comptabilité recalculée, lignée, audit, limites, latence mesurée…) ou prouvé par des tests du dépôt cités ; sans objet seulement avec sa raison |
| Note, P0 (§47) | note pondérée par famille ; un P0 raté ou impossible à mesurer bloque, quelle que soit la note |
| Observation, divergence (§48-50) | 30 jours et 30 événements au moins (justifié : ≈ 50 trades par an) ; paper rejoué par la boucle de backtest, chaque écart classé |
| Verdict (§52-54) | ACCEPTED et moteur de politique, ou BLOCKED et ce qui manque ; jamais une autorisation du réel |

**Le verdict du 8 octobre** : BLOQUÉ, seulement par la durée d'observation
(3 jours sur 30, 4 événements sur 30) ; note 96,7/100, aucun P0 raté ; les 3
achats du paper sont exactement ceux du backtest de la même période.

## Étape 14 du prompt : moteur de politiques

Le quatorzième document demande un moteur qui dit dans quelles conditions une
décision est permise, limitée, soumise à votre accord, bloquée ou interdite,
déclaratif, versionné, expliqué et sûr par défaut. Appliqué à TrendGuard :
`politique.py`, le registre des politiques évalué à chaque achat, et
`python trendguard_bot.py politique`. Détail :
[`MOTEUR_POLITIQUE.md`](MOTEUR_POLITIQUE.md).

| Exigence de l'étape 14 | Ce qui a été fait |
| --- | --- |
| Registre, langage, versions (§8-10, §47-48) | 19 politiques versionnées (type, gravité, catégorie, condition, action, raison) en langage fermé, sans code exécuté |
| Actions, conflits, décision (§11, §34-37) | huit actions, de « permis » à « compte gelé » ; la plus grave l'emporte ; décision expliquée et valable 5 minutes |
| Sécurité par défaut (§4) | une politique illisible bloque, jamais ne permet ; un registre incohérent est signalé |
| Même décision que la porte | 3 000 cas tirés au hasard, 0 écart ; vérifiée à chaque achat du bot, un écart est signalé ; la porte reste le seul point d'application |
| Simulation des politiques (§38-40) | politiques de risque rejouées sur les deux époques : le profil prudent protège nettement depuis 2023 |

**Ce qui ne s'applique pas** : dérogations (une règle change dans le code,
versionnée et testée), comité de politiques avec IA (aucune clé),
juridictions et horaires de marché (cryptos cotées sans interruption).

## Ce qui reste (votre accord d'abord)

1. **Débat des IA** (§43) : rôles haussier, baissier, critique et synthèse
   dans la veille. Il attend des clés d'IA : sans elles, il n'y a personne
   pour débattre (`python trendguard_bot.py watch set-key claude`, clé
   saisie masquée).
2. **Effet des annonces économiques** : quand le calendrier aura mesuré au
   moins 10 jours d'annonce, une étude dira si s'abstenir d'acheter ces
   jours-là aurait aidé ; une règle ne sera proposée que si elle est prouvée.
3. **Réel** (§22, §29) : seulement après des semaines de paper avec des
   trades vendus, des alertes qui arrivent, une machine fixe et de l'argent
   sur le compte.

## Ce qui ne s'applique pas, et pourquoi

| Exigence du prompt | Pourquoi pas ici |
| --- | --- |
| Analyse fondamentale d'entreprises (§12 : P/E, EBITDA, DCF…) | le bot trade des cryptos : ni bilan, ni bénéfice, ni dividende |
| Vente à découvert, couverture, levier (§17) | Binance Spot, achat seul, par choix de sécurité : on ne perd au pire que ce qu'on a acheté |
| Pile FastAPI, PostgreSQL, Redis, React, base vectorielle (§55) | pour un bot qui tourne sur un seul PC, SQLite et le panneau actuel suffisent ; tout réécrire coûterait des mois sans rendre le bot plus sûr ni plus rentable. L'architecture reste modulaire : un module par rôle |
| Agents « docteur en finance », « gérant de portefeuille »… (§3) | ces rôles sont tenus par des règles testées et par les IA consultées, pas par des promesses d'expertise |
| Macro complète (§7) | pas de données macro gratuites et fiables intégrées ; le régime de BTC, la veille et le calendrier des annonces américaines en tiennent lieu |

Les règles d'or du prompt sont celles du bot depuis le début : préférer « NO
TRADE » à un trade mal justifié (§62), ne jamais croire un bon backtest seul
(§61), ne jamais modifier la production sans validation (§39, §64), ne rien
inventer (§60).
