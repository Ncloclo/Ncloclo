# Gouvernance de l'architecture (documents transverses du prompt maître)

Trois documents transverses complètent les étapes : la RACI normalisée (un
seul responsable par responsabilité, frontières entre plans, doublons
supprimés), la matrice des autorités et l'ordre officiel (qui peut observer,
recommander, bloquer, autoriser, exécuter ; source de vérité de chaque
donnée ; frontières critiques), et les critères de sortie (définition de
« fini », dix portes de qualité, zéro échec silencieux, note de
préparation). Appliqués à TrendGuard : `gouvernance.py`, qui mesure et dit,
sans rien changer.

Code : [`trendguard/gouvernance.py`](../trendguard/gouvernance.py). Tests :
[`tests/test_gouvernance.py`](../tests/test_gouvernance.py).

```text
python trendguard_bot.py gouvernance                 # portes, RACI, autorités, sources de vérité, frontières
python trendguard_bot.py gouvernance --out docs/GOUVERNANCE_ETAT.md
```

Dernier état : [`GOUVERNANCE_ETAT.md`](GOUVERNANCE_ETAT.md). Chaque nuit, le
rapport donne la ligne « Gouvernance ».

## RACI normalisée

Chaque responsabilité critique a exactement un responsable (A) et au moins
un module qui fait le travail (R) ; elle n'apparaît qu'une fois, dans un seul
des onze plans (A fondation, B données et connaissances, C perception et
cognition, D intelligence du monde, E planification, F intelligence métier,
G sûreté et contrôle, H opérations réelles, I apprentissage, J sécurité, K
interface humaine). Les frontières « X ≠ Y » sont vérifiées : politique et
autorisation, autorisation et validation finale, validation finale et
exécution, risque et portefeuille, état du monde et simulation, recherche et
mémoire, sécurité et opérations… n'ont jamais le même responsable. Armer le
réel, changer le risque, lever le mode sûr : vous.

## Qui peut quoi, une source de vérité par donnée

La matrice des autorités dit, module par module, s'il peut observer,
recommander, bloquer, autoriser ou exécuter. Seul `bot_execution.py` envoie
un ordre ; seuls le moteur d'autorisation, la porte (un achat après un
contrôle approuvé) et vous autorisez ; la sécurité ne fait que bloquer.
Chaque donnée critique (journal financier, limites de risque, politiques,
autorisations, ordres, incidents, versions des modèles, connaissances, état
du monde, simulations, observations, audit) a un seul propriétaire ; seul
`donnees.py` touche aux tables du journal financier.

## Frontières critiques, vérifiées dans le code

- Aucun module d'intelligence ou de conseil (perception, jumeau, objectifs,
  monde, causal, recherche, mémoire, diagnostic, comité, IA, apprentissage,
  sécurité, contrôle, panneau) n'importe l'exécution des ordres.
- Un seul appel d'achat dans tout le code, après la porte et la validation
  finale ; le moteur de risque nourrit le contrôle, qui précède
  l'autorisation.
- Le jumeau est isolé de la production ; la perception n'a aucune dépendance
  interdite ; la sécurité ne crée aucune permission ; le plan de contrôle ne
  modifie aucune limite de risque.

## Les dix portes de qualité

| Porte | P0 | preuve mesurée |
| --- | --- | --- |
| fonctionnel | non | chaque composant de la feuille de route a ses tests |
| contrats | oui | chaque contrat cité est au registre, `CONTRATS.md` à jour |
| sécurité | oui | aucune adresse hors de la liste blanche, aucune capacité offensive |
| performance | non | WAIVED : pas de banc global, chaque moteur mesure son temps dans son examen |
| fiabilité | non | disponibilité sur 7 jours d'au moins 99 % (WAIVED sans historique) |
| intégrité des données | oui | journal d'audit chaîné intact, une source de vérité par donnée |
| observabilité | non | aucune exception large ignorée sans raison écrite (zéro échec silencieux) |
| reprise après panne | non | chaque service du plan de contrôle a sa procédure |
| reproductibilité | non | versions des bibliothèques figées (`requirements-docker.txt`) |
| gouvernance | oui | RACI, sources de vérité et frontières tenues |

Note = part des portes PASS parmi celles qui comptent (WAIVED exclues).
READY à partir de 95, CANDIDATE 90, VALIDATING 80, DEVELOPMENT 70, sinon
REJECTED ; un P0 en échec ou un contournement : BLOCKED quelle que soit la
note (contrat `GovernanceReport.v1`).

## Ce qui a été corrigé en l'appliquant

- Quatre services du plan de contrôle n'avaient pas de procédure de reprise
  (panneau, rapport, veille des annonces, savoir) : ajoutées (16 procédures).
- Sept exceptions larges étaient ignorées sans raison écrite : chacune dit
  maintenant pourquoi c'est sans danger (lecture d'avance, arrêt, prévision) ;
  aucun comportement changé.
- Contrôles GitHub : la suite de tests dépassait 20 minutes ; durée portée à
  40 minutes, chaque test en échec devient une annotation lisible sans se
  connecter, et le rapport ne télécharge plus jamais de cours (il lit
  seulement ceux déjà sur le PC).

## Ce qui ne s'applique pas

- **Dossier de preuves par module** (§34) : la feuille de route
  ([`FEUILLE_DE_ROUTE.md`](FEUILLE_DE_ROUTE.md)) donne pour chaque composant
  ses fichiers, tests, contrats et documents ; les examens donnent la note.
- **Communication par messages et événements entre services** (§18) : un
  seul programme ; les modules s'échangent des contrats versionnés.
