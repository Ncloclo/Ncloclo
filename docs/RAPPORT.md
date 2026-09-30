# TrendGuard — rapport quotidien : sécurité, diagnostic expert et recommandations appliquées

Chaque jour à **00:30 UTC**, après la décision de 00:02, les leçons de
l'apprentissage libre et les épreuves de l'évolution encadrée, le bot applique
d'abord lui-même les recommandations sûres, puis fait une analyse profonde de
lui-même : le fond (santé, stratégie, compétences acquises), la forme (code,
journal, panneau) et sa sécurité. Il garde le rapport et vous l'envoie. Si le PC
était éteint à 00:30, tout est fait dès son retour. Code :
`trendguard/maintenance.py` (recommandations appliquées) et
`trendguard/report.py` (analyse et rapport) ; `TG_RAPPORT=false` dans `.env`
pour l'arrêter.

## Où le lire

- **Panneau ▸ Réglages**, en tête de page : le **Rapport quotidien** montre le
  verdict, le score, l'envoi par canal (e-mail, WhatsApp : reçu, refusé ou non
  configuré, avec la marche à suivre si le rapport ne vous parvient pas), les
  trois premières choses à faire, la tendance des derniers jours et les
  améliorations du code qui attendent votre validation ; boutons « Rapport
  complet » et « Générer maintenant ». Le centre de sécurité, juste à côté,
  affiche aussi la date et le verdict du dernier rapport.
- **E-mail** : le rapport complet, mis en page (couleurs, sections, lisible sur
  téléphone), avec une version texte pour les messageries qui n'affichent pas
  les pages. **WhatsApp** (et Telegram) : un résumé, avec les trois premières
  choses à faire.
- `python trendguard_bot.py rapport` : le dernier rapport ; `python
  trendguard_bot.py rapport maintenant` : une analyse tout de suite.
- Archives : dossier `rapports/` (30 jours) ; aucun secret n'y figure, seulement
  leur présence et leur état.

## Les recommandations appliquées seules

**Corrections sûres et réversibles** (`TG_AUTOCORRECTION`, activées par
défaut) :

- **veille du PC, sur secteur seulement** : mise en veille et veille prolongée
  « Jamais », capot fermé « Ne rien faire ». Le bot ne surveille rien quand le
  PC dort ; sur batterie, rien ne change. Vos réglages d'origine sont gardés :
  `python trendguard_bot.py rapport restaurer` les remet et arrête ces
  corrections, `python trendguard_bot.py rapport corriger` les reprend ;
- sauvegarde quotidienne de la base et des réglages du bot (dossier
  `sauvegardes/`, 14 jours gardés), vérifiée par un contrôle d'intégrité ;
- droits du fichier des secrets réservés à votre compte s'ils étaient trop
  larges (Windows : accès retiré à « Tout le monde », « Utilisateurs » et
  « Utilisateurs authentifiés » ; Linux et macOS : 600) ;
- un secret retrouvé dans un journal y est masqué par des étoiles, sans changer
  la taille du fichier.

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
ni au pare-feu ou à l'antivirus. Ce qu'il ne peut pas appliquer lui-même (une
clé Binance à corriger, un mot de passe d'application Gmail à créer) reste en
tête des recommandations, classées par importance.

## Ce qu'il analyse

| Partie | Contrôles |
| --- | --- |
| Recommandations appliquées seules | correction de la veille du PC, mise à jour validée installée (ou pourquoi pas) |
| Sécurité | secrets hors de GitHub (fichier `.env` jamais publié), droits du fichier des secrets, aucune clé ni aucun mot de passe dans les fichiers publiés ni dans les journaux, droits de la clé Binance (retrait interdit, restriction IP), sauvegarde et intégrité de la base, pare-feu et antivirus de Windows, veille du PC |
| Centre de sécurité du panneau | accès, mots de passe ratés, alertes (dernier envoi), garde-fou de Rachelle |
| Santé du bot (le fond) | panneau en marche, bot en marche, dernier cycle, décision du jour à l'heure, disponibilité sur 7 jours, relance automatique, démarrage avec l'ordinateur, arrêt d'urgence, risque configuré dans les limites sages, mode, espace disque |
| Stratégie (le fond) | le diagnostic expert complet (données, marché, signaux, portefeuille, santé de la stratégie, réel contre attendu, stratégies concurrentes, veille) |
| Compétences acquises | apprentissage libre (carnets, prévisions, ruse), précision des prévisions avant et après correction, niveau et essais de l'évolution encadrée |
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
