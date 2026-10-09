# Cybersécurité et autodéfense (critères AC-001 à AC-070)

Mesuré le 2026-10-09 par `python trendguard_bot.py cyber` (étape 20 du prompt
maître, [`CYBERSECURITE.md`](CYBERSECURITE.md)). Jamais offensif ; aucun secret
lu.

## Verdict

- **Prête pour la production** (READY_FOR_PRODUCTION_SECURITY) ; jamais une
  défense autonome sans limite.
- Note 100,0/100 (prête) ; critères P0 non satisfaits : 0.
- 11 actifs ; 42 adresses Internet dans le code, toutes sur la liste blanche ; 1
  événement(s) de sécurité.

## Actifs

| Actif | type | criticité | état | exposition |
| --- | --- | --- | --- | --- |
| code | CODE | 0 | présent | GitHub public, sans secret |
| Python et bibliothèques | RUNTIME | 1 | présent | ce PC |
| base du bot | DATABASE | 0 | présente | ce PC |
| journal d'audit | DATABASE | 0 | présent | ce PC |
| journal financier | DATABASE | 0 | présent | ce PC |
| fichier des secrets (.env) | SECRETS | 0 | présent | ce PC, exclu de GitHub, jamais lu ici |
| clé API Binance | KEYS | 0 | présente | droit de retrait à garder désactivé |
| panneau | SERVICE | 2 | voir le plan de contrôle | ce PC, ou le Wi-Fi avec mot de passe |
| alertes | SERVICE | 2 | email | Internet |
| IA de la veille | LLM | 3 | conseil seulement | Internet (liste blanche) |
| compte Binance | BROKER | 0 | paper (la clé ne sert pas) | Internet |

## Sorties vers Internet

IA : 7, alertes : 3, aucune : 4, code : 4, courtier : 2, local : 1, savoir : 20,
système : 1 (nombre d'adresses par usage).

## Réponse prévue

| Risque | ce qui se fait seul | ce qui vous revient |
| --- | --- | --- |
| clé Binance montrée ou volée | set-keys la refuse ; le centre de sécurité alerte | supprimez-la sur Binance, créez-en une sans droit de retrait |
| ordre ou position inconnus chez Binance | la paire s'arrête seule (aucun nouvel ordre) | vérifiez sur Binance, puis reprise explicite |
| données abîmées | aucun achat sous 50 sur 100 | rien ; diagnostic si cela dure |
| code modifié hors Pull Request | signalé chaque nuit ; la mise à jour automatique n'installe que ce que vous avez fusionné | vérifiez le dépôt (git status) |
| bibliothèque avec une faille connue | signalée par le rapport de la nuit | mettez à jour la version indiquée |
| mots de passe du panneau essayés | adresse bloquée 5 minutes après 5 échecs | changez le mot de passe |
| IA ou agent détourné (injection) | aucune IA n'a de droit critique ; Rachelle refuse les instructions cachées | rien |
| compromission du PC | mode sûr à poser par vous : plus aucun achat | python trendguard_bot.py mode-sur on |

## Familles

| Famille | poids | note |
| --- | --- | --- |
| détection | 15 % | 100 |
| sécurité des identités | 15 % | 100 |
| prévention | 10 % | 100 |
| réponse aux incidents | 15 % | 100 |
| reprise | 10 % | 100 |
| sécurité des IA et des modèles | 10 % | 100 |
| sécurité des données | 10 % | 100 |
| résilience | 10 % | 100 |
| audit et gouvernance | 5 % | 100 |

## Critères

| Critère | priorité | état | preuve |
| --- | --- | --- | --- |
| AC-001 Inventaire des actifs | P0 | conforme | 11 actifs inventoriés, aucun secret lu ; 1 test(s) du dépôt |
| AC-002 Inventaire des identités | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-003 Rien sans vérification (refus par défaut) | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-004 Moindre privilège | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-005 Double authentification | P2 | sans objet | Binance et GitHub l'exigent pour votre compte ; le bot n'a pas de comptes d'utilisateurs |
| AC-006 Accès de courte durée | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-007 Changement des clés | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-008 Sessions révoquées | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-009 Événements de sécurité collectés | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-010 Événements normalisés | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-011 Détection | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-012 Détection de comportements anormaux | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-013 Renseignement sur les menaces | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-014 Gestion des vulnérabilités | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-015 Télémétrie du poste | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-016 Surveillance du réseau | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-017 Sorties contrôlées (liste blanche) | P0 | conforme | 42 adresses dans le code, toutes sur la liste blanche ; 1 test(s) du dépôt |
| AC-018 Agents isolés | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-019 Outils autorisés | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-020 Sécurité des IA | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-021 Recherche documentaire sûre | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-022 Intégrité des modèles | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-023 Chaîne d'approvisionnement | P0 | conforme | version ca48a25, aucun fichier modifié ; 1 test(s) du dépôt |
| AC-024 Cycle de vie des incidents | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-025 Gravité des incidents | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-026 Confinement automatique (réduire seulement) | P0 | conforme | prouvé par 2 test(s) du dépôt |
| AC-027 Accord humain | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-028 Réparation automatique | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-029 Arrêt d'urgence | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-030 Mode sûr | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-031 Preuves intactes | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-032 Graphe de sécurité | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-033 Risque de sécurité noté | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-034 Intégration au trading | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-035 Mémoire de sécurité | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-036 Apprentissage de sécurité | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-037 Audit inaltérable | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-038 Sécurité du panneau (API) | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-039 Tableau de bord de sécurité | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-040 Objectifs de sécurité | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-041 Essais de chaos | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-042 Clé compromise : essai | P0 | conforme | clé présente ; 4 clé(s) exposée(s) connue(s), refusées par set-keys ; 1 test(s) du dépôt |
| AC-043 Agent compromis : essai | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-044 Modèle compromis : essai | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-045 Résistance à l'injection d'instructions | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-046 Résistance à l'abus d'outils | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-047 Résistance aux sources empoisonnées | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-048 Isolation réseau : essai | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-049 Bascule : essai | P2 | sans objet | un seul ordinateur, un seul courtier |
| AC-050 Restauration des sauvegardes | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-051 Reprise après sinistre | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-052 P0 détecté | P0 | conforme | 1 événement(s) de sécurité du moment ; 1 test(s) du dépôt |
| AC-053 P0 contenu | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-054 P0 signalé | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-055 Aucune riposte | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-056 Aucune élévation de privilège | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-057 Aucun contournement de sécurité | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-058 Aucun contournement de l'arrêt d'urgence | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-059 Tout est traçable | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-060 Surveillance continue | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-061 Régressions de sécurité rejouées | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-062 Chaîne d'approvisionnement vérifiée | P1 | conforme | 6 bibliothèques épinglées, toutes à la version testée ; 1 test(s) du dépôt |
| AC-063 Artefacts signés | P2 | sans objet | aucun artefact binaire : le code vient de GitHub, fusionné par vous |
| AC-064 Modèles de sécurité validés | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-065 Agents validés | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-066 Défense autonome bornée | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-067 Vous gardez la main | P0 | conforme | prouvé par 1 test(s) du dépôt |
| AC-068 Reprise sûre | P1 | conforme | prouvé par 1 test(s) du dépôt |
| AC-069 Retour d'expérience | P2 | conforme | prouvé par 1 test(s) du dépôt |
| AC-070 Sécurité prête pour la production | P0 | conforme | prouvé par 1 test(s) du dépôt |
