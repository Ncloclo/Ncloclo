# TrendGuard — rapport quotidien : sécurité et diagnostic expert

Chaque jour à **00:30 UTC**, après la décision de 00:02, les leçons de
l'apprentissage libre et les épreuves de l'évolution encadrée, le bot fait une
analyse profonde de lui-même : le fond (santé, stratégie, compétences acquises),
la forme (code, journal, panneau) et sa sécurité. Il applique seul les
protections sûres, garde le rapport et vous l'envoie. Si le PC était éteint à
00:30, le rapport est fait dès son retour. Code : `trendguard/report.py` ;
`TG_RAPPORT=false` dans `.env` pour l'arrêter.

## Où le lire

- **Panneau ▸ Réglages ▸ Rapport quotidien** : score, points à corriger, bouton
  « Rapport complet », et « Générer maintenant ». Le centre de sécurité affiche
  aussi la date et le verdict du dernier rapport.
- **E-mail** : le rapport complet. **WhatsApp** (et Telegram) : un résumé, avec
  les trois premières choses à faire.
- `python trendguard_bot.py rapport` : le dernier rapport ; `python
  trendguard_bot.py rapport maintenant` : une analyse tout de suite.
- Archives : dossier `rapports/` (30 jours) ; aucun secret n'y figure, seulement
  leur présence et leur état.

## Ce qu'il analyse

| Partie | Contrôles |
| --- | --- |
| Sécurité | secrets hors de GitHub (fichier `.env` jamais publié), droits du fichier des secrets, aucune clé ni aucun mot de passe dans les fichiers publiés ni dans les journaux, droits de la clé Binance (retrait interdit, restriction IP), sauvegarde et intégrité de la base, pare-feu et antivirus de Windows |
| Centre de sécurité du panneau | accès, mots de passe ratés, alertes (dernier envoi), garde-fou de Rachelle, évolution encadrée |
| Santé du bot (le fond) | panneau en marche, bot en marche, dernier cycle, décision du jour à l'heure, disponibilité sur 7 jours, relance automatique, démarrage avec l'ordinateur, arrêt d'urgence, risque configuré dans les limites sages, mode, espace disque, veille du PC |
| Stratégie (le fond) | le diagnostic expert complet (données, marché, signaux, portefeuille, santé de la stratégie, réel contre attendu, stratégies concurrentes, veille) |
| Compétences acquises | apprentissage libre (carnets, prévisions, ruse), précision des prévisions avant et après correction, niveau et essais de l'évolution encadrée |
| Code, journal et panneau (la forme) | erreurs et avertissements du journal sur 24 h, code identique à la version publiée, origine du code, contrôles GitHub de la version installée (tests, qualité, sécurité), qualité du code, propositions d'amélioration en attente de votre validation |

## Ce que le bot fait seul, et ce qu'il ne fait jamais

**Seul, parce que c'est sûr et réversible** :

- sauvegarde quotidienne de sa base et de ses réglages (dossier `sauvegardes/`,
  14 jours gardés), vérifiée par un contrôle d'intégrité ;
- droits du fichier des secrets réservés à votre compte s'ils étaient trop
  larges (Windows : accès retiré à « Tout le monde », « Utilisateurs » et
  « Utilisateurs authentifiés » ; Linux et macOS : 600) ;
- un secret retrouvé dans un journal y est masqué par des étoiles, sans changer
  la taille du fichier ;
- le panneau envoie en plus des en-têtes qui empêchent tout autre site de le
  piloter ou de le lire.

**Jamais, avec sagesse** : il ne touche pas aux règles ni au risque (l'évolution
encadrée s'en charge, sous épreuves), ni aux clés, ni au mode réel, ni au code,
ni aux réglages de Windows (veille, pare-feu, antivirus). Ces points deviennent
des recommandations, classées par importance.

## L'amélioration quotidienne du code

Chaque nuit à 00:30 UTC, une routine Claude Code dans le cloud fait aussi le
diagnostic expert du fond et de la forme du dépôt : défauts, sécurité (panneau,
secrets, dépendances), fiabilité, design et lisibilité du panneau,
documentation. Elle propose au plus une amélioration par jour, testée, sous
forme de Pull Request sur GitHub, que vous validez vous-même ; si rien n'est
clairement utile, elle ne propose rien. Elle ne fusionne jamais, ne touche ni
aux secrets, ni aux règles de trading, ni au risque. Le rapport quotidien liste
les propositions en attente.
