# TrendGuard — rapport quotidien : sécurité, diagnostic expert et recommandations appliquées

Chaque jour à **00:30 UTC**, après la décision de 00:02, les leçons de
l'apprentissage libre et les épreuves de l'évolution encadrée, le bot applique
d'abord lui-même les recommandations sûres, puis fait une analyse profonde de
lui-même : le fond (santé, stratégie, compétences acquises), la forme (code,
journal, panneau) et sa sécurité. Il garde le rapport et vous l'envoie. Si le PC
était éteint à 00:30, tout est fait dès son retour. Il refait aussi l'analyse et
vous envoie un rapport **aussitôt après une compétence acquise** (réglage
adopté, confirmé ou annulé par l'évolution encadrée, palier de risque changé)
quand le rapport de la nuit n'a pas pu en rendre compte. Chaque rapport dit
pourquoi il a été fait, donne le score de la sécurité à part et ce qui a changé
depuis le précédent : points corrigés, nouveaux points à corriger, compétences
et expérience acquises. Code : `trendguard/maintenance.py` (recommandations
appliquées) et `trendguard/report.py` (rapport), avec ses contrôles
(`report_security.py`, `report_health.py`) et sa mise en page
(`report_render.py`) ; `TG_RAPPORT=false` dans `.env` pour l'arrêter.

## Où le lire

- **Panneau ▸ Réglages**, en tête de page : le **Rapport quotidien** montre le
  verdict, le score, l'envoi par canal (e-mail, WhatsApp : reçu, refusé ou non
  configuré, avec la marche à suivre si le rapport ne vous parvient pas), les
  trois premières choses à faire, la tendance des derniers jours et les
  améliorations du code qui attendent votre validation ; boutons « Rapport
  complet » et « Générer maintenant ».
- **Panneau ▸ Réglages ▸ Rapport de sécurité**, juste à côté : les contrôles en
  direct (accès, clés, autonomie, alertes), puis la sécurité de la dernière
  analyse (secrets, clé Binance, sauvegarde, chiffrement, mises à jour de
  Windows…), avec ce qui a changé depuis la précédente, et le bouton « Envoyer
  par e-mail et WhatsApp » qui vous renvoie le dernier rapport.
- **E-mail** : le rapport complet, mis en page (couleurs, sections, lisible sur
  téléphone), avec une version texte pour les messageries qui n'affichent pas
  les pages. **WhatsApp** (et Telegram) : un résumé, avec les trois premières
  choses à faire. Si le serveur d'e-mail refuse le mot de passe trois fois de
  suite, l'e-mail n'est plus essayé qu'une fois par jour (chaque essai raté est
  une connexion refusée sur votre compte) ; il reprend dès qu'un bon mot de
  passe est enregistré (`python trendguard_bot.py alerts configurer`).
- `python trendguard_bot.py rapport` : le dernier rapport ; `python
  trendguard_bot.py rapport maintenant` : une analyse tout de suite ; `python
  trendguard_bot.py rapport envoyer` : le dernier rapport renvoyé par e-mail et
  WhatsApp.
- Archives : dossier `rapports/` (30 jours) ; aucun secret n'y figure, seulement
  leur présence et leur état.

## Les recommandations appliquées seules

**Corrections sûres et réversibles** (`TG_AUTOCORRECTION`, activées par
défaut) :

- **veille du PC, sur secteur seulement** : mise en veille et veille prolongée
  « Jamais », capot fermé « Ne rien faire » (réglage que Windows cache sur bien
  des portables : le bot le lit quand même). Le bot ne surveille rien quand le
  PC dort ; sur batterie, rien ne change, et le bouton d'alimentation reste à
  vous : le rapport dit seulement ce qu'il fait. Vos réglages d'origine sont
  gardés : `python trendguard_bot.py rapport restaurer` les remet et arrête ces
  corrections, `python trendguard_bot.py rapport corriger` les reprend ;
- sauvegarde quotidienne de la base et des réglages du bot (dossier
  `sauvegardes/`, 14 jours gardés), vérifiée par un contrôle d'intégrité ;
- droits du fichier des secrets réservés à votre compte s'ils étaient trop
  larges (Windows : accès retiré à « Tout le monde », « Utilisateurs » et
  « Utilisateurs authentifiés » ; Linux et macOS : 600) ;
- un secret retrouvé dans un journal ou dans l'historique des commandes
  (PowerShell, terminal, Python) y est masqué par des étoiles, sans changer la
  taille du fichier.

**Mises à jour validées par vous** (`TG_MISE_A_JOUR`, activées par défaut) : une
amélioration proposée en Pull Request et **fusionnée par vous sur GitHub** est
installée seule la nuit suivante, si les contrôles GitHub de cette version sont
au vert :

1. installation en avance rapide seulement (la nouvelle version prolonge celle
   du PC) ;
2. contrôle de démarrage sur le PC (compilation et chargement du bot, du panneau
   et du rapport) ;
3. redémarrage prévu du bot par son superviseur, et du panneau ;
4. vérification qu'ils tournent de nouveau (7 minutes au plus) ;
5. en cas de problème à l'une de ces étapes, retour automatique à la version
   précédente, et le rapport le signale.

**Jamais installé** : un changement publié sans votre validation (ce PC garde
vos clés Binance : c'est la seule porte d'entrée du code, et le rapport vous
alerte si un tel changement apparaît), une version dont les contrôles échouent,
une version qui ne prolonge pas celle du PC, une installation pendant que des
fichiers du bot sont modifiés sur le PC, ni une installation automatique en mode
réel (`python trendguard_bot.py rapport installer` le fait à votre demande).

**Jamais, avec sagesse** : le bot ne touche pas aux règles ni au risque
(l'évolution encadrée s'en charge, sous épreuves), ni aux clés, ni au mode réel,
ni au pare-feu ou à l'antivirus, ni à ses bibliothèques (il les contrôle, sans
les installer). Ce qu'il ne peut pas appliquer lui-même (une clé Binance à
corriger, un mot de passe d'application Gmail à créer, des bibliothèques à
mettre à jour) reste en tête des recommandations, classées par importance.

## Ce qu'il analyse

| Partie | Contrôles |
| --- | --- |
| Recommandations appliquées seules | correction de la veille du PC, mise à jour validée installée (ou pourquoi pas) |
| Sécurité | secrets hors de GitHub (fichier `.env` jamais publié), droits du fichier des secrets, aucune clé ni aucun mot de passe dans les fichiers publiés, les journaux ni l'historique des commandes, dossier du bot hors du nuage (OneDrive, Dropbox…), droits de la clé Binance (retrait interdit, restriction IP, aucun droit superflu comme la marge, les contrats à terme ou les transferts, âge de la clé ; refusée : l'adresse actuelle du PC à autoriser, à corriger en réel, simple information en paper où la clé ne sert à rien), sauvegarde et intégrité de la base, pare-feu et antivirus de Windows, chiffrement du disque, mises à jour de sécurité de Windows (45 jours au plus, redémarrage en attente), veille du PC, alimentation d'un portable (sur batterie, il s'endort capot fermé puis s'éteint : à brancher), bibliothèques du bot aux versions testées (`requirements-docker.txt`) et sans faille connue (`pip-audit`, sur celles réellement installées là où il tourne ; une faille qu'une bibliothèque épinglée, comme ccxt, empêche encore de corriger reste une information, jusqu'à ce que sa nouvelle version publiée accepte la correction) |
| Centre de sécurité du panneau | accès, mots de passe ratés, alertes (dernier envoi), clé partagée par erreur (la clé en service n'est pas l'une des clés exposées, reconnues par leur empreinte), garde-fou de Rachelle |
| Santé du bot (le fond) | panneau en marche, bot en marche, dernier cycle, décision du jour à l'heure, disponibilité sur 7 jours, relance automatique, démarrage avec l'ordinateur, arrêt d'urgence (et, s'il est déclenché, quand il pourra se lever seul), risque configuré dans les limites sages, mode, place sur le disque (à corriger sous 10 % ou 2 Go libres ; dans le rapport, où part la place : Téléchargements, fichier d'échange, hibernation, fichiers temporaires, corbeille, en tailles et jamais en noms de fichiers, et la place perdue depuis le rapport précédent) et mémoire du PC (à corriger quand 90 % est réservé aux programmes ; dans le rapport, les trois programmes qui en prennent le plus), Wi-Fi des dernières 24 heures (coupures par réseau, réseau à préférer, réseau en service à la décision de 00:02, partage de connexion d'un téléphone ; les noms des réseaux restent dans le rapport, jamais dans les fichiers publiés), plantages de Windows sur 30 jours (écran bleu expliqué en clair, arrêt brutal ; une information au premier, à corriger dès deux en 7 jours ; repris dans le centre de sécurité du panneau) |
| Stratégie (le fond) | le diagnostic expert complet (données, marché, signaux, portefeuille, santé de la stratégie, réel contre attendu, stratégies concurrentes, veille) |
| Risque et analyse du portefeuille | d'après la dernière décision, en information : qualité des données (note sur 100), risque d'un jour (VaR), pire test de résistance et ce qu'il ferait de l'arrêt d'urgence, attribution des résultats (pourquoi le bot a gagné ou perdu), calendrier des grandes annonces américaines des 48 heures et réaction mesurée du bitcoin, registre des expériences (nombre, dernière) |
| Compétences acquises | apprentissage libre (carnets, prévisions, ruse), précision des prévisions avant et après correction, niveau et essais de l'évolution encadrée, palier de risque (1 à 2 % par achat) et sa dernière analyse |
| Code, journal et panneau (la forme) | erreurs et avertissements du journal sur 24 h, code identique à la version publiée, origine du code, contrôles GitHub de la version installée (tests, qualité, sécurité), qualité du code, propositions d'amélioration en attente de votre validation |

Le panneau envoie aussi des en-têtes qui empêchent tout autre site de le piloter
ou de le lire.

## L'amélioration quotidienne du code

Chaque nuit à 00:30 UTC, une routine Claude Code dans le cloud fait le
diagnostic expert du fond et de la forme du dépôt : défauts, sécurité (panneau,
secrets, dépendances), fiabilité, harmonisation et structure du code, design et
lisibilité du panneau, documentation. Elle propose au plus une amélioration par
jour, testée, sous forme de Pull Request sur GitHub ; si rien n'est clairement
utile, elle ne propose rien. Elle ne fusionne jamais elle-même et ne touche ni
aux secrets, ni aux règles de trading, ni au risque. Vous validez d'un clic
(« Merge » sur GitHub) : le bot l'installe alors seul la nuit suivante, avec les
vérifications ci-dessus. Le rapport quotidien liste les propositions en attente.
