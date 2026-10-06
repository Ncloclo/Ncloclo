# Noyau cognitif de TrendGuard (étape 4 du prompt maître)

Le noyau cognitif transforme une demande en résultat vérifié, sans jamais
agir seul sur l'argent :

```text
demande → plan (graphe de tâches validé) → orchestration (outils autorisés,
délais, nouveaux essais selon la cause, annulation) → vérification croisée →
incertitude → synthèse → décision → trace
```

Code : [`trendguard/cognitif.py`](../trendguard/cognitif.py) (le moteur),
[`trendguard/expert.py`](../trendguard/expert.py) (sa première tâche : le
diagnostic expert). Tests : [`tests/test_cognitif.py`](../tests/test_cognitif.py).

## Sa première tâche : le diagnostic expert

Ce que vous demandez en écrivant « analyse et diagnostique expert », le bot le
fait désormais lui-même, en lecture seule :

    python trendguard_bot.py expert            # complet (télécharge les cours publics)
    python trendguard_bot.py expert --rapide   # sans réseau

Il tourne aussi seul chaque jour à 00:45 UTC (sans réseau), après le rapport
de la nuit, et Rachelle en résume le résultat (« diagnostic expert » dans sa
fenêtre).

| Tâche | Outil | Classe | Délai |
| --- | --- | --- | --- |
| état du bot | base du bot, lecture seule | données | 30 s |
| décision du jour | dernière décision contre la bougie attendue | calcul | 30 s |
| journal d'audit | chaîne d'empreintes | données | 30 s |
| journal financier | intégrité, achats, positions ouvertes, trades | données | 30 s |
| journal du bot | avertissements et erreurs des 24 heures | lecture | 30 s |
| PC | disque, mémoire, alimentation | lecture | 60 s |
| plantages de Windows | écrans bleus, arrêts brutaux | lecture | 60 s |
| Wi-Fi | coupures par réseau sur 24 heures | lecture | 60 s |
| stratégie | diagnostic complet sur les cours publics de Binance (réseau) | recherche | 10 min, un nouvel essai si le réseau flanche |

**Vérifications croisées** (deux sources indépendantes pour un même fait) :
décision du jour à l'heure ; données de la décision fraîches ; achats de
l'audit = achats du journal financier ; ventes de l'audit = trades du
journal ; positions de l'état du bot = ordres ouverts du journal. Un
désaccord devient un constat « élevé », jamais tranché au hasard.

**Décision** : `NO_ACTION` (rien d'anormal), `ESCALATE` (des points pour
vous), `RESEARCH_MORE` (trop d'inconnu pour conclure : plus du quart des
tâches manquent, ou une contradiction), `BLOCK` (une mesure de sûreté est
proposée : journal d'audit ou financier incohérent, PC instable avec de
l'argent réel). Une décision n'est jamais une autorisation : chaque mesure
proposée attend votre accord (niveau 4), le noyau ne l'applique pas.

## Règles tenues par construction

| Règle de l'étape 4 | Dans le code |
| --- | --- |
| Plan validé avant d'agir (§23) | cycle, dépendance absente, outil inconnu ou interdit, doublon, trop de tâches : plan refusé |
| Machines d'états (§25, §87) | `PENDING → READY → RUNNING → COMPLETED / FAILED / TIMEOUT`, `BLOCKED`, `CANCELLED` ; une transition impossible (ex. `COMPLETED → RUNNING`) lève une erreur |
| Outils sous politique (§34-38) | un outil n'existe que s'il est inscrit ; en marche autonome (niveau 1, analyse), seules les classes lecture, calcul, recherche et données sont permises ; aucun outil d'exécution, d'ordre ou d'administration : le noyau ne peut rien acheter, vendre ni transférer |
| Nouveaux essais selon la cause (§64) | seulement pour une panne passagère ; jamais pour une erreur de validation ou d'autorisation |
| Délais, budget, annulation (§62-65) | délai par tâche, budget de temps total, annulation propagée aux tâches restantes |
| Résultats partiels (§70) | une tâche qui échoue bloque ses dépendantes, les autres continuent ; le résultat est marqué PARTIEL |
| Incertitude (§53-54) | LOW, MEDIUM, HIGH, CRITICAL d'après les tâches manquantes et les contradictions ; aucune « confiance de 0,95 » inventée |
| Décision ≠ autorisation (§59-60) | `DecisionResult.authorized` est toujours faux ; une décision qui s'autoriserait elle-même est refusée |
| Trace lisible (§67-68) | `<bot>.expert.json` : plan, états et durées des tâches, vérifications, incertitude, décision, propositions ; aucune pensée privée |
| Sécurité des instructions (§42-44) | Rachelle refuse les injections (« oublie tes consignes », « prompt système », « mode développeur », « tu es maintenant… ») ; les textes d'Internet restent des données, jamais des consignes |

**Niveaux d'autonomie** (§60) appliqués à TrendGuard : 0 lecture et 1
analyse (diagnostic expert, rapport) ; 2 simulation (épreuves de
l'évolution, laboratoire) ; 3 action paper (le bot en paper) ; 4 votre
accord (mode sûr, réglages, mise à jour du code) ; 5 action réelle contrôlée
(mode réel armé par vous, puis porte d'exécution avant chaque ordre).

## Pas appliqué ici, et pourquoi

- **Agents multiples, routeur de modèles, débat d'IA** (§26-31, §51) : il
  n'y a aucune clé d'IA sur ce PC. Rachelle a déjà sa solution de repli :
  sans IA, ou si l'IA ne répond pas, elle répond avec sa base intégrée (mode
  dégradé sûr). Le débat attend des clés (`python trendguard_bot.py watch
  set-key claude`).
- **Mémoire vectorielle, RAG** (§10-17) : le savoir du bot est déjà gardé et
  jugé sur les cours réels (noyau de savoir) ; une base vectorielle n'aurait
  rien de plus à chercher.
- **API `/cognitive/*`** (§84) : le panneau n'écoute que ce PC ; le
  diagnostic se lance en une commande et Rachelle le résume.
- **IA sur le chemin des ordres** : jamais (§62-63, §78). Les décisions de
  trading restent des règles fixes et la porte d'exécution contrôle chaque
  achat.
