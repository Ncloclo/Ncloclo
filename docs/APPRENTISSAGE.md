# Apprentissage continu et gouvernance des modèles (critères AC-001 à AC-070)

Mesuré le 2026-10-09 par `python trendguard_bot.py apprentissage` (étape 19 du
prompt maître, [`APPRENTISSAGE_CONTINU.md`](APPRENTISSAGE_CONTINU.md)). Lecture
seule.

## Verdict

- **Prêt pour un apprentissage continu contrôlé**
  (READY_FOR_CONTROLLED_CONTINUOUS_LEARNING) ; jamais une auto-amélioration sans
  contrôle.
- Note 100,0/100 (prête) ; critères P0 non satisfaits : 0.
- 11 modèles inscrits (10 actifs, 2 critique) ; champion : réglages d'origine ;
  aucun challenger à l'essai ; niveau Apprenti ; dérive des données : BTC, ETH,
  BNB, XRP, ADA, DOGE, TRX, LINK, LTC, BCH, ETC, NEO, XTZ, ALGO, DOT, AAVE, ICP.

## Registre des modèles

| Modèle | type | risque | état | ce qu'il ne peut pas faire | retour |
| --- | --- | --- | --- | --- | --- |
| Règle de tendance (cassure, stops, lecture du marché) | RÈGLE | critique | actif | acheter en réel sans la porte du réel ; changer seule ses plafonds | réglages d'origine : python trendguard_bot.py evolution revenir |
| Évolution encadrée (réglages de la règle) | CHALLENGER | élevé | en ombre | toucher au risque, aux positions, à l'arrêt d'urgence ou au réel | retour automatique si l'essai prend plus de 2 points de retard ; evolution revenir |
| Palier de risque par achat | RÈGLE | critique | actif | dépasser le plafond que vous avez fixé (TG_RISK_MAX_PCT : 2,00 %) | premier palier aussitôt à la moindre alerte |
| Profil prudent (risque réduit après une baisse) | RÈGLE | moyen | actif | augmenter le risque | réglage TG_DD_THROTTLE vide |
| Moteur de risque (VaR, ES, scénarios) | MODÈLE | élevé | actif | autoriser un achat ; augmenter une taille | désactiver le moteur : la porte refuse alors tout achat |
| Note de qualité des données | MODÈLE | moyen | actif | corriger une donnée | aucun : elle ne fait que refuser |
| Normale du carnet d'ordres (ruse d'achat) | MODÈLE | moyen | actif | acheter plus ou plus tôt | seuil fixe (sans apprentissage) |
| Noyau de savoir (sources jugées) | MODÈLE | moyen | actif | acheter ; vendre | réglage TG_SAVOIR=false |
| Analyse financière (prévisions, consultative) | MODÈLE | moyen | actif | décider un achat | aucun : consultative |
| Comité d'agents (avis) | MODÈLE | faible | actif | décider ; autoriser | aucun : consultatif |
| IA de la veille (conseil) | MODÈLE | moyen | actif | passer un ordre ; s'autoriser quoi que ce soit | retour au modèle suivant, ou à aucune IA |

## Dérive

Rendements du 2026-07-10 au 2026-10-07 contre l'époque d'apprentissage
(2018-2022) : indice de stabilité médian 0,333 (sous 0,10 : stable ; au-delà de
0,25 : dérive nette).

| Crypto | indice | volatilité récente / passée | dérive |
| --- | --- | --- | --- |
| TRX | 3,762 | 0,15 | nette |
| BNB | 1,392 | 0,30 | nette |
| LTC | 1,071 | 0,53 | nette |
| ETH | 1,066 | 0,53 | nette |
| BTC | 1,055 | 0,50 | nette |
| AAVE | 0,937 | 0,62 | nette |
| NEO | 0,891 | 0,52 | nette |
| LINK | 0,867 | 0,54 | nette |
| ICP | 0,456 | 0,49 | nette |
| XRP | 0,419 | 0,61 | nette |

Relation signal → résultat : dernière année +0,92 R par trade (23 trades), avant
+1,22 R (407) ; p = 0,63 : pas de rupture prouvée.

## Frontières de l'apprentissage

- **Seul** : normale du carnet d'ordres (ne fait que resserrer) ; calibrage des
  prévisions (probabilités, jamais les décisions) ; choix de l'IA par ses
  mesures (banc versionné, disjoncteur) ; jugement des sources du savoir sur les
  vrais cours ; détection de dérive (ce module).
- **Après épreuves** : réglages de la règle par l'évolution : épreuves, 30 jours
  d'essai, retour seul ; palier de risque par achat : un cran après épreuves,
  jusqu'à votre plafond, redescente aussitôt.
- **Vous seul** : plafonds de risque, positions, arrêt d'urgence ; politiques,
  autorisation, porte d'exécution ; armer le réel, paliers du réel ; clés et
  mots de passe ; fusion du code (Pull Request).
- Vérifié : tenu (évolution : bear_trail_atr, breakout_n, init_stop_atr,
  regime_sma, trail_atr ; plafond du palier de risque 2,00 % ; gelée en réel
  contrôlé, production limitée).

## Familles

| Famille | poids | note |
| --- | --- | --- |
| intégrité des données | 15 % | 100 |
| évaluation des modèles | 15 % | 100 |
| gouvernance | 15 % | 100 |
| sûreté | 15 % | 100 |
| surveillance de la dérive | 10 % | 100 |
| reproductibilité | 10 % | 100 |
| sécurité | 10 % | 100 |
| déploiement et retour | 5 % | 100 |
| observabilité | 5 % | 100 |

## Critères

| Critère | priorité | état | preuve |
| --- | --- | --- | --- |
| AC-001 Jeux de données versionnés (empreinte) | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-002 Lignée des données | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-003 Données à leur date (aucune information future) | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-004 Fuite de données détectée | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-005 Expériences tracées | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-006 Apprentissage reproductible | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-007 Registre des modèles | P0 | conforme | 11 modèles, chacun avec sa fiche ; 1 test(s) du dépôt |
| AC-008 Modèles versionnés | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-009 Fiche obligatoire | P0 | conforme | 11 modèles, chacun avec sa fiche ; 1 test(s) du dépôt |
| AC-010 Niveau de risque de chaque modèle | P1 | conforme | 1 faible, 6 moyen, 2 élevé, 2 critique ; 1 test(s) du dépôt |
| AC-011 Évaluation des modèles | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-012 Validation hors échantillon | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-013 Walk-forward | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-014 Robustesse | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-015 Scénarios extrêmes | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-016 Calibrage | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-017 Dérive des données détectée | P1 | conforme | indice de stabilité médian 0,333 du 2026-07-10 au 2026-10-07 ; dérive nette : BTC, ETH, BNB, XRP, ADA, DOGE, TRX, LINK, LTC, BCH, ETC, NEO, XTZ, ALGO, DOT, AAVE, ICP ; 1 test(s) du dépôt |
| AC-018 Dérive du concept détectée | P1 | conforme | dernière année +0,92 R sur 23 trades, avant +1,22 R sur 407 (p = 0,63) ; 1 test(s) du dépôt |
| AC-019 Dérive de performance détectée | P1 | conforme | glissement attendu 0,10 %, observé 3,19 % sur 3 sortie(s) ; 1 test(s) du dépôt |
| AC-020 Champion et challenger | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-021 Mode ombre | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-022 Essai progressif (canari) | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-023 Retour automatique | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-024 Modèle de secours | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-025 Intégrité des modèles | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-026 Artefacts signés | P2 | sans objet | pas d'artefact binaire : chaque modèle est du code versionné par git, fusionné par vous, contrôlé par GitHub |
| AC-027 Analyse de sécurité | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-028 Empoisonnement des données détecté | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-029 Retours validés | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-030 Récompense détournée détectée (chance, voisins) | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-031 Auto-amélioration gouvernée | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-032 Code validé avant installation | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-033 Prompts gouvernés | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-034 Recherche documentaire évaluée | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-035 IA évaluées en continu | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-036 Accord humain | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-037 Double accord | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-038 Séparation des tâches | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-039 Audit inaltérable | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-040 Reproductibilité | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-041 Mode sûr | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-042 Repli sur « pas de trade » | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-043 Intégration aux politiques | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-044 Intégration à l'autorisation | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-045 Porte d'exécution isolée | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-046 Arrêt d'urgence protégé | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-047 Aucune élévation de privilège | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-048 Retour éprouvé | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-049 Reprise après sinistre | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-050 Essais de chaos | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-051 Données abîmées : reprise | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-052 Fichier d'un modèle abîmé : reprise | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-053 Registre en panne : reprise | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-054 Déploiement raté : reprise | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-055 Surveillance complète | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-056 Alertes | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-057 Machine d'états de la gouvernance | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-058 Audit de l'apprentissage | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-059 Provenance du savoir | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-060 Contradictions résolues | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-061 Confiance calibrée | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-062 Incertitude gérée (inconnu jamais pris pour zéro) | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-063 Désaccord des modèles | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-064 Apprentissage des stratégies isolé | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-065 Aucun apprentissage avec de l'argent réel avant la production | P0 | conforme | évolution : 5 réglages permis, aucun plafond ; palier de risque borné à 2,00 % ; aucune automatisation ni IA ne change le risque, la porte ou le code ; évolution gelée en réel contrôlé et en production limitée ; 1 test(s) du dépôt |
| AC-066 Barrières de sûreté préservées | P0 | conforme | évolution : 5 réglages permis, aucun plafond ; palier de risque borné à 2,00 % ; aucune automatisation ni IA ne change le risque, la porte ou le code ; évolution gelée en réel contrôlé et en production limitée ; 1 test(s) du dépôt |
| AC-067 Aucune élévation de privilège autonome | P0 | conforme | évolution : 5 réglages permis, aucun plafond ; palier de risque borné à 2,00 % ; aucune automatisation ni IA ne change le risque, la porte ou le code ; évolution gelée en réel contrôlé et en production limitée ; 1 test(s) du dépôt |
| AC-068 Aucun plafond de risque changé seul | P0 | conforme | évolution : 5 réglages permis, aucun plafond ; palier de risque borné à 2,00 % ; aucune automatisation ni IA ne change le risque, la porte ou le code ; évolution gelée en réel contrôlé et en production limitée ; 2 test(s) du dépôt |
| AC-069 Aucune porte d'exécution changée seule | P0 | conforme | évolution : 5 réglages permis, aucun plafond ; palier de risque borné à 2,00 % ; aucune automatisation ni IA ne change le risque, la porte ou le code ; évolution gelée en réel contrôlé et en production limitée ; 1 test(s) du dépôt |
| AC-070 Apprentissage continu contrôlé | P0 | conforme | prouvé par 1 test(s) du dépôt |
