# Ingénierie et exploitation

Mesuré le 2026-10-09 par `python trendguard_bot.py ingenierie` (étapes 30 et 31
du prompt maître, [`INGENIERIE.md`](INGENIERIE.md)).

- **VALIDATING** ; note 85/100.
- 154 derniers changements, 154 expliqués, 152 écrits avec une IA ; 800 tests
  (92 % des modules testés) ; aucune dérive des bibliothèques ; aucune
  auto-modification.

## Les derniers changements du code

| Version | date | changement | IA |
| --- | --- | --- | --- |
| `e9d9f96` | 2026-10-09 | Documents transverses : gouvernance de l'architecture ; RACI normalisée (un seul responsab | oui |
| `e155935` | 2026-10-09 | Étape 29 du prompt et ses corrections : perception multi-sources ; observations datées et  | oui |
| `80fc439` | 2026-10-09 | Étape 28 du prompt : jumeau numérique ; le paper rejoué par la boucle de backtest, Monte-C | oui |
| `e166f46` | 2026-10-09 | Étape 27 du prompt : mission et objectifs ; la porte du réel en objectifs mesurables, qui  | oui |
| `dc15a44` | 2026-10-09 | Étape 26 du prompt : intelligence causale ; graphe de la règle éprouvé par intervention da | oui |
| `457b1c6` | 2026-10-09 | Étape 25 du prompt : raisonnement et modèle du monde ; état classé et daté, ce qui a chang | oui |
| `546ef7c` | 2026-10-09 | Étape 24 du prompt : mémoire et graphe de connaissances ; reconstruits depuis les sources  | oui |
| `6006520` | 2026-10-09 | Étape 22 du prompt : recherche et connaissances ; rang, confiance et validité de chaque so | oui |
| `7b977f6` | 2026-10-09 | Étape 21 du prompt : interface humain-IA ; commandes classées (aucune n'exécute un ordre), | oui |
| `ef68c83` | 2026-10-09 | Étape 20 : état du jour de la cybersécurité (docs/CYBER.md), mesuré sur un dépôt propre | oui |

Étapes du prompt maître retrouvées dans l'historique : 2, 3, 4, 5, 6, 7, 8, 9,
10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 24, 25, 26, 27, 28, 29.

## Les tests

800 fonctions de test dans 65 fichiers ; 76 modules sur 83 importés par au moins
un test (une mesure par module, pas par ligne).
Sans test direct : `bot_execution`, `bot_routines`, `explain`, `journal`,
`replay`, `security`, `selection`.

## La chaîne de contrôle et la mise à jour

| Contrôle GitHub | présent |
| --- | --- |
| style (ruff) | oui |
| tests (pytest) | oui |
| audit des bibliothèques | oui |
| pages web (Playwright) | oui |

Durée maximale : 40 minutes. Mise à jour sur ce PC : seule une Pull Request
fusionnée par vous, aux contrôles au vert, en avance rapide, contrôle de
démarrage, puis retour automatique à la version précédente en cas d'échec ;
jamais en réel sans votre commande.

Commandes git qui écrivent, par fichier : `trendguard/maintenance.py` (merge,
reset).

## Exploitation de ce PC

- Python 3.13.2, Windows 11, environnement isolé du bot (.venv).
- Bibliothèques : 6 figées sur 6 ; 6 bibliothèques épinglées, toutes à la
  version testée.

## Qualité

| Famille | poids | état | preuve |
| --- | --- | --- | --- |
| justesse (construction, auto-modification) | 20 % | conforme | 98 fichiers lus sans erreur ; aucune auto-modification |
| tests et couverture | 15 % | conforme | 800 tests dans 65 fichiers ; 92 % des modules importés par un test |
| sécurité | 15 % | NON CONFORME | version e9d9f96, 13 fichier(s) modifié(s) hors Pull Request ; .env jamais suivi par git |
| intégrité de l'architecture | 10 % | conforme | 1 appel d'achat, après la porte et la validation finale |
| chaîne de contrôle | 10 % | conforme | contrôles GitHub : style (ruff), tests (pytest), audit des bibliothèques, pages web (Playwright) ; 40 min au plus |
| reproductibilité | 10 % | conforme | 6/6 bibliothèques figées ; 6 bibliothèques épinglées, toutes à la version testée |
| traçabilité des changements | 5 % | conforme | 154/154 changements expliqués |
| performance | 5 % | conforme | en 3,7 s |
| retour arrière | 5 % | conforme | version précédente connue ; mise à jour défaillante : retour automatique |
| gouvernance des mises à jour | 5 % | conforme | seule une Pull Request fusionnée par vous, aux contrôles au vert, s'installe ; jamais en réel sans vous |
