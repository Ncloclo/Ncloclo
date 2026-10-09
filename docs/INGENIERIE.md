# Ingénierie et exploitation (étapes 30 et 31 du prompt maître)

Comment le code du bot change et comment il arrive sur ce PC, mesuré : chaque
changement tracé, les tests et ce qu'ils couvrent, la chaîne de contrôle
GitHub, les versions des bibliothèques figées contre celles installées, le
retour arrière, et la règle d'auto-modification. Un code généré n'est pas un
code validé ; un test passé n'est pas un logiciel correct ; ce module ne dit
« testé » que preuve à l'appui.

Code : [`trendguard/ingenierie.py`](../trendguard/ingenierie.py). Tests :
[`tests/test_ingenierie.py`](../tests/test_ingenierie.py).

```text
python trendguard_bot.py ingenierie                  # changements, tests, chaîne, dérive, retour arrière
python trendguard_bot.py ingenierie --out docs/INGENIERIE_ETAT.md
```

Dernier état : [`INGENIERIE_ETAT.md`](INGENIERIE_ETAT.md). Chaque nuit, le
rapport donne la ligne « Ingénierie et exploitation ».

## Étape 30 : l'ingénierie

| Exigence | Dans TrendGuard |
| --- | --- |
| Changement tracé (§13, §29) | historique git : version, sujet, auteur, date, co-auteur IA déclaré ; les étapes du prompt retrouvées dans l'historique |
| Tests (§19, §22) | fonctions de test, fichiers, modules du bot importés directement par un test (une mesure par module, pas par ligne) ; composants de la feuille de route sans test |
| Construction (§21) | chaque fichier Python se lit sans erreur |
| Chaîne de contrôle (§30, §47) | contrôles GitHub à chaque envoi : style (ruff), tests, audit des bibliothèques, pages web ; durée maximale |
| Retour arrière (§31) | version précédente connue ; mise à jour défaillante : retour automatique |
| Auto-modification (§25, §37-38) | aucun fichier .py écrit, aucune bibliothèque installée, aucune commande git qui écrit, hors de la mise à jour validée |

## Étape 31 : l'exploitation

| Exigence | Dans TrendGuard |
| --- | --- |
| État voulu contre état observé (§12) | versions figées (`requirements-docker.txt`) contre versions installées sur ce PC : toute dérive est dite |
| Nomenclature (SBOM, §21) | la liste figée des bibliothèques et leurs versions |
| Secrets (§19) | `.env` jamais suivi par git ; saisie masquée seulement |
| Environnements (§23) | paper, réel simulé (testnet), réel contrôlé, production : paliers du réel ([`DEPLOIEMENT_REEL.md`](DEPLOIEMENT_REEL.md)) |
| Déploiement contrôlé (§24, §56) | seule une Pull Request fusionnée par vous, aux contrôles au vert, en avance rapide, contrôle de démarrage, retour automatique, jamais en réel sans vous (`maintenance.py`) |
| Reprise (§33) | sauvegardes de la nuit et restauration d'essai ([`PLAN_DE_CONTROLE.md`](PLAN_DE_CONTROLE.md)) |

## La note

Justesse 20 %, tests 15 %, sécurité 15 %, architecture 10 %, chaîne de
contrôle 10 %, reproductibilité 10 %, traçabilité 5 %, performance 5 %,
retour arrière 5 %, gouvernance des mises à jour 5 %. READY à partir de 95,
RELEASE_CANDIDATE 90, VALIDATING 80, DEVELOPMENT 70, sinon REJECTED ; un
défaut P0 (auto-modification, chemin d'achat hors de la porte, mise à jour
sans ses garde-fous) : NOT_READY. Le contrat `EngineeringReport.v1` refuse
toute autorité de production.

## Ce qui ne s'applique pas

- **Kubernetes, conteneurs en production, nuage, Terraform, plusieurs
  régions** : un seul PC ; une image Docker existe pour les essais.
- **Agents d'ingénierie autonomes qui modifient le code** : le code change
  seulement par vos sessions et vos Pull Requests ; le bot ne s'écrit jamais.
- **Couverture par ligne de code** : la mesure est par module ; GitHub
  exécute toute la suite à chaque envoi.
