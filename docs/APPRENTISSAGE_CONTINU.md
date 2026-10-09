# Apprentissage continu et gouvernance des modèles (étape 19 du prompt maître)

Tout ce qui, dans TrendGuard, apprend ou décide à partir des données est
inscrit dans un registre, avec sa fiche, son niveau de risque et son état. La
dérive est mesurée, les frontières de l'apprentissage sont vérifiées, et rien
ne change seul ce qui protège l'argent. Ce module lit et mesure ; il ne change
rien.

Code : [`trendguard/apprentissage.py`](../trendguard/apprentissage.py) ;
l'apprentissage lui-même reste là où il était : l'évolution encadrée
([`EVOLUTION.md`](EVOLUTION.md)), la normale du carnet et le calibrage des
prévisions (`learning.py`), le banc des IA (`modeles.py`). Tests :
[`tests/test_apprentissage.py`](../tests/test_apprentissage.py),
[`tests/test_evolution.py`](../tests/test_evolution.py).

```text
python trendguard_bot.py apprentissage                    # registre, dérive, frontières, 70 critères
python trendguard_bot.py apprentissage --out docs/APPRENTISSAGE.md
```

Dernier examen : [`APPRENTISSAGE.md`](APPRENTISSAGE.md).

## Le registre des modèles

| Modèle | Risque | Ce qu'il ne peut pas faire |
| --- | --- | --- |
| Règle de tendance | critique | acheter en réel sans la porte du réel ; changer seule ses plafonds |
| Évolution encadrée (challenger) | élevé | toucher au risque cumulé, aux positions, à l'arrêt d'urgence, au réel |
| Palier de risque par achat | critique | dépasser le plafond que vous avez fixé (`TG_RISK_MAX_PCT`, 2 % au plus) |
| Profil prudent | moyen | augmenter le risque |
| Moteur de risque | élevé | autoriser un achat ; augmenter une taille |
| Note des données | moyen | corriger une donnée |
| Normale du carnet d'ordres | moyen | acheter plus ou plus tôt |
| Noyau de savoir | moyen | acheter ; vendre |
| Analyse financière | moyen | décider un achat |
| Comité d'agents | faible | décider ; autoriser |
| IA de la veille | moyen | passer un ordre ; s'autoriser quoi que ce soit |

Chaque fiche dit à quoi sert le modèle, ce qui lui est interdit, ses données,
sa validation, ses limites, son plan de retour, sa version et son empreinte ;
son état (actif, à l'essai, en ombre, suspendu…) est lu dans le bot, jamais
supposé. Le niveau de risque vient de facteurs écrits : impact financier,
autonomie, complexité, sensibilité à la dérive. Seule la règle décide d'un
achat ; un modèle critique a un propriétaire humain (vous) et un plan de retour
(contrat `ModelCard.v1`). Les états suivent une machine d'états : aucun saut
(un brouillon ne devient jamais actif d'un coup) ; couper d'urgence est
toujours permis.

## Champion et challenger

Le champion est le réglage en vigueur de la règle ; le challenger, l'essai de
l'évolution encadrée : épreuves sur l'historique (deux époques, crises, coûts,
voisins, chance), puis 30 jours d'essai en paper ; retiré seul s'il prend plus
de 2 points de retard.

## La dérive (§23)

- **Données** : rendements des 90 derniers jours contre ceux de l'époque
  d'apprentissage (2018-2022), par crypto : indice de stabilité de population
  (sous 0,10 stable, au-delà de 0,25 dérive nette) et rapport des volatilités.
- **Concept** : la relation signal → résultat ; résultat moyen des trades de la
  règle (en multiples du risque pris) sur la dernière année contre avant, test
  de Welch. Un écart sur peu de trades n'est pas une preuve de rupture.
- **Performance et exécution** : glissement attendu contre observé, écart entre
  le prix payé et le cours de décision (étape 17).

Sans cours en cache, la dérive est « non mesurable », jamais inventée.

## Les frontières de l'apprentissage (§49)

- **Seul** : normale du carnet (ne fait que resserrer), calibrage des
  prévisions (les probabilités, jamais les décisions), choix de l'IA par ses
  mesures, jugement des sources du savoir, détection de la dérive.
- **Après épreuves** : réglages de la règle par l'évolution (épreuves, 30 jours
  d'essai, retour seul) ; risque par achat, un cran à la fois, jusqu'au plafond
  que vous avez fixé, et redescente aussitôt à la première alerte.
- **Vous seul** : plafonds de risque, positions, arrêt d'urgence ; politiques,
  autorisation, porte d'exécution ; armer le réel et ses paliers ; clés ; fusion
  du code.

Vérifié à chaque examen : l'évolution ne règle aucun plafond, le palier de
risque est borné (2 % au plus), aucun automatisme, aucun agent ni aucune IA ne
peut changer le risque, lever l'arrêt d'urgence ou le mode sûr, armer le réel,
saisir des clés ou fusionner du code (moteur d'autorisation).

**Ajout de l'étape 19 : en réel contrôlé et en production limitée,
l'évolution est gelée** : aucun essai, aucun changement avec de l'argent réel
avant la production. Cela ne fait que réduire ; en paper, rien ne change.

## L'examen : AC-001 à AC-070, note, verdict (§66-67, §70)

Chaque critère est mesuré (registre, fiches, niveaux de risque, frontières,
dérive) ou prouvé par les tests du dépôt (manifestes reproductibles, aucune
information future, walk-forward, PBO, Sharpe dégonflé, calibrage de la VaR,
banc versionné des IA, registre des prompts, retour d'une mise à jour ratée…).
Note pondérée : données 15 %, évaluation 15 %, gouvernance 15 %, sûreté 15 %,
dérive 10 %, reproductibilité 10 %, sécurité 10 %, déploiement 5 %,
observabilité 5 %. Un P0 raté : NOT_READY. Le verdict
**READY_FOR_CONTROLLED_CONTINUOUS_LEARNING** n'est jamais une
auto-amélioration sans contrôle : l'intelligence n'est jamais son propre
mécanisme d'autorisation.

## Ce qui ne s'applique pas

- **Entraînement de réseaux, GPU, plateforme MLOps** (§12, §46) : aucun modèle
  appris par descente de gradient ; la règle est écrite, ses réglages sont
  éprouvés sur l'historique.
- **Signature d'artefacts** (§35) : aucun artefact binaire ; chaque modèle est
  du code versionné par git, fusionné par vous, contrôlé par GitHub.
- **Essai progressif en pourcentage du trafic** (§21) : un seul portefeuille ;
  l'essai de l'évolution dure 30 jours en paper, avec retour automatique.
- **Apprentissage avec de l'argent réel** : gelé jusqu'à la production.
