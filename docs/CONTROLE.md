# Plan de contrôle de la production (critères AC-001 à AC-060)

Mesuré le 2026-10-09 par `python trendguard_bot.py controle` (étape 18 du prompt
maître, [`PLAN_DE_CONTROLE.md`](PLAN_DE_CONTROLE.md)). Lecture seule : il voit,
il n'agit pas.

## Verdict

- **Prêt pour une exploitation contrôlée**
  (READY_FOR_PRODUCTION_CONTROLLED_OPERATIONS) ; jamais une exploitation
  autonome sans limite.
- Note 97,9/100 (prête) ; critères P0 non satisfaits : 0.
- 17 service(s) sur 18 en bonne santé, 1 dégradé(s), 0 en panne ; 1 incident(s),
  le plus grave P2 : Alertes (e-mail, messages) : dégradé.

## Services

| Service | palier | état | détail |
| --- | --- | --- | --- |
| Ordinateur (disque, mémoire) | 1 (finance critique) | en bonne santé | 14,2 Go libres, mémoire 65 % |
| Boucle du bot (décision, stops, achats, ventes) | 1 (finance critique) | en bonne santé | dernier cycle il y a 1 min |
| Binance (cours, bougies, ordres) | 1 (finance critique) | en bonne santé | horloge de Binance relue il y a 34 min |
| Connexion Internet | 1 (finance critique) | en bonne santé | Binance joignable |
| Superviseur (relance du bot) | 1 (finance critique) | en bonne santé | en marche, 0 relance(s) |
| Données de marché du jour (qualité) | 1 (finance critique) | en bonne santé | qualité du 2026-10-08 : 100/100 |
| Porte d'exécution (politiques, autorisation) | 0 (sûreté critique) | en bonne santé | porte.v3, politiques-1.2.0, autorisation-1.0.0 |
| Moteur de risque (évaluation du jour) | 0 (sûreté critique) | en bonne santé | risque normal, achats permis |
| Arrêt d'urgence et mode sûr | 0 (sûreté critique) | en bonne santé | prêts (ni arrêt d'urgence, ni mode sûr) |
| Journal d'audit chaîné | 0 (sûreté critique) | en bonne santé | intact : 3 événement(s), dernier le 2026-10-08 à 17:20 |
| Journal financier | 0 (sûreté critique) | en bonne santé | schéma v5 ; 3 décision(s), 0 contrôle(s) du risque, 3 ordre(s), 3 trade(s) ; intégrité vérifiée |
| Base du bot (état, contexte des ordres) | 0 (sûreté critique) | en bonne santé | base saine |
| Sauvegardes et restauration d'essai | 1 (finance critique) | en bonne santé | 10 sauvegarde(s), la dernière il y a 24 h ; restauration d'essai en 0,02 s |
| Rapport de la nuit | 3 (support) | en bonne santé | rapport du 2026-10-08 |
| Alertes (e-mail, messages) | 2 (intelligence) | dégradé | en panne : email (cause dans le panneau) |
| Veille des annonces de Binance | 2 (intelligence) | en bonne santé | annonces lues le 2026-10-08 |
| Noyau de savoir | 3 (support) | en bonne santé | lu le 2026-10-08 |
| Panneau de contrôle | 3 (support) | en bonne santé | répond |

## Incidents

| Incident | gravité | détail | procédure |
| --- | --- | --- | --- |
| Alertes (e-mail, messages) : dégradé | P2 (dégradé) | en panne : email (cause dans le panneau) | Alertes en panne |

## Objectifs de service

| Objectif | cible | mesuré | budget consommé |
| --- | --- | --- | --- |
| bot en marche sur 7 jours | 99,0 % | 87,4 % | épuisé |
| décision du jour prise après la clôture | 100,0 % | 100,0 % | 0 % |
| données du jour d'au moins 50 sur 100 | 100,0 % | 100,0 % | 0 % |
| sauvegarde de moins de 26 heures | 100,0 % | 100,0 % | 0 % |
| rapport de la nuit fait | 100,0 % | 100,0 % | 0 % |

## Reprise, changements, superviseur

- Sauvegardes : 10, la dernière il y a 23,9 h (RPO), restaurée à l'essai en 0,02
  s (RTO).
- Code : d26bc1a (modifications locales) ; configuration c162f22aa041eaae ;
  porte porte.v3, politiques politiques-1.2.0, autorisation autorisation-1.0.0,
  risque risque-1.0.0, limites limites-1.0.0.
- Superviseur : peut lire, analyser, recommander, relancer le bot après un
  plantage, poser le mode sûr ; refusé : 10 action(s) critique(s) sur 10.

## Familles

| Famille | poids | note |
| --- | --- | --- |
| sûreté | 20 % | 100 |
| fiabilité | 15 % | 86 |
| observabilité | 10 % | 100 |
| réponse aux incidents | 10 % | 100 |
| reprise après sinistre | 10 % | 100 |
| sécurité | 10 % | 100 |
| gestion des changements | 10 % | 100 |
| capacité | 5 % | 100 |
| rapprochement | 5 % | 100 |
| opérations humaines | 5 % | 100 |

## Critères

| Critère | priorité | état | preuve |
| --- | --- | --- | --- |
| AC-001 Supervision en continu (bot en marche 99 % du temps) | P1 | non conforme | bot en marche 87,4 % des 7 derniers jours (objectif 99 %) |
| AC-002 Santé de chaque service (vivant, prêt, métier) | P0 | conforme | 18 services mesurés ; 1 test(s) du dépôt |
| AC-003 Graphe des dépendances | P1 | conforme | 18 services, aucun cycle ; points uniques de défaillance : pc, base, internet, binance, bot ; 1 test(s) du dépôt |
| AC-004 Objectifs de service mesurés | P1 | conforme | 5 objectifs de service mesurés ; 1 test(s) du dépôt |
| AC-005 Budgets d'erreur | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-006 Alertes dédupliquées, incidents regroupés par cause | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-007 P0 signalé en priorité | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-008 Cycle de vie des incidents (ouvert, clos) | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-009 Procédures versionnées | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-010 Procédures sans effet de bord en double | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-011 Audit complet | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-012 Aucun secret dans les journaux | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-013 Configuration versionnée | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-014 Retour à la version précédente | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-015 Options activables sûres | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-016 Capacité surveillée (disque, mémoire) | P1 | conforme | 14,2 Go libres, mémoire 65 % ; 1 test(s) du dépôt |
| AC-017 Contre-pression | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-018 Disjoncteurs | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-019 Sauvegarde automatique | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-020 Restauration éprouvée | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-021 Bascule de secours | P2 | sans objet | un seul ordinateur et un seul courtier : pas de site de secours ; en réel, les stops restent posés chez Binance |
| AC-022 Reprise après sinistre | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-023 RPO mesuré | P1 | conforme | dernière sauvegarde il y a 23,9 h (objectif 26 h) ; 1 test(s) du dépôt |
| AC-024 RTO mesuré | P1 | conforme | restauration d'essai en 0,02 s (objectif 60 s) ; 1 test(s) du dépôt |
| AC-025 Superviseur en marche | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-026 Le superviseur ne s'autorise rien | P0 | conforme | refusé au superviseur : 10 action(s) critique(s) sur 10 ; 1 test(s) du dépôt |
| AC-027 Le superviseur ne contourne pas la porte | P0 | conforme | refusé au superviseur : 10 action(s) critique(s) sur 10 ; 1 test(s) du dépôt |
| AC-028 Le superviseur ne change pas le risque | P0 | conforme | refusé au superviseur : 10 action(s) critique(s) sur 10 ; 1 test(s) du dépôt |
| AC-029 Le superviseur ne lève pas l'arrêt d'urgence | P0 | conforme | refusé au superviseur : 10 action(s) critique(s) sur 10 ; 1 test(s) du dépôt |
| AC-030 Niveaux d'autonomie tenus | P0 | conforme | aucun automatisme sans limite (L5) ; le plus haut : L4, le bot, sous la porte d'exécution ; 1 test(s) du dépôt |
| AC-031 Gestion des changements | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-032 Changements critiques approuvés par vous | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-033 Surveillance de la sécurité | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-034 Changement des clés | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-035 Audit inaltérable | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-036 Retour d'expérience après incident | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-037 Essais de chaos | P1 | conforme | prouvé par 2 test(s) du dépôt |
| AC-038 Essais de bascule | P2 | sans objet | pas de bascule possible (un seul ordinateur, un seul courtier) |
| AC-039 Horloge synchronisée | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-040 Rapprochement surveillé | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-041 Santé de Binance surveillée | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-042 Fraîcheur des données surveillée | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-043 Santé de la stratégie surveillée | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-044 Santé des modèles surveillée | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-045 Utilisation du risque surveillée | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-046 Baisse du capital surveillée | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-047 Exécutions anormales détectées | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-048 Déploiements observés | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-049 Tableau de bord | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-050 Arrêt d'urgence opérationnel | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-051 Mode sûr opérationnel | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-052 Escalade vers vous | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-053 Accès d'urgence contrôlé | P2 | sans objet | pas d'accès d'urgence : vous êtes le seul propriétaire, chaque action passe par vos outils masqués |
| AC-054 Aucune élévation de privilège autonome | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-055 Aucun chemin de contrôle caché | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-056 Chaque action traçable | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-057 Reprise reproductible | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-058 Portes de mise en production tenues | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-059 Un P0 bloque la production | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-060 Audit d'exploitation complet | P1 | conforme | prouvé par 1 test(s) du dépôt |
