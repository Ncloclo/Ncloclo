# Interface humain-IA du panneau (étape 21 du prompt maître)

Le panneau n'est jamais une autorité de sécurité : il transmet vos actions et
montre ce que le bot sait. L'étape 21 l'a rendu plus explicite : chaque
commande a sa classe, chaque donnée dit sa fraîcheur, chaque réponse de Rachelle
dit d'où elle vient, et Rachelle peut lire ses réponses à voix haute. Le centre
de commande 3D n'est pas construit (il reste au rang des idées) : le panneau
2D montre tout.

Code : [`panel/interface.py`](../panel/interface.py),
[`panel/server.py`](../panel/server.py),
[`panel/static/js/assistant.js`](../panel/static/js/assistant.js).
Tests : [`tests/test_interface.py`](../tests/test_interface.py),
[`tests/test_panel.py`](../tests/test_panel.py), et les essais des pages par
GitHub (Playwright).

## Les commandes et leur classe

| Classe | Commandes |
| --- | --- |
| lire | état, capital, positions, trades, cryptos, bougies, veille, actualités, rapport, journal |
| analyser | analyse du portefeuille, lecture du marché, raisonnement, centre de sécurité |
| simuler (sans effet) | prochaine clôture (probabilités) |
| recommander | une question à Rachelle (elle explique, n'agit pas) |
| modifier (contrôlé) | démarrer ou arrêter le bot, choisir les cryptos, démarrage avec l'ordinateur, tester les alertes, lancer ou envoyer le rapport, session |
| exécuter (action réelle) | **aucune** : le panneau ne passe jamais un ordre |

Chaque commande « modifier » passe par le moteur d'autorisation (étape 15) ;
une commande inconnue est traitée comme la plus sensible et refusée. Le panneau
ne détient aucun droit critique : armer le réel, changer le risque, saisir une
clé ou lever l'arrêt d'urgence se font par vous, hors du panneau, avec les
outils masqués.

## La fraîcheur des données

En direct (moins de 2 minutes), à jour (moins de 10 minutes), ancienne,
inconnue,
ou « bot arrêté » : le badge à côté du dernier cycle du tableau de bord le dit.
La décision du jour et la note des données ont aussi leur fraîcheur
(`/api/status`, `/api/interface`). Une donnée ancienne n'est jamais montrée
comme en direct.

## « Pourquoi ? »

Sous chaque réponse de Rachelle, un repli « Pourquoi ? » (au clavier aussi) :
qui a répondu (Rachelle sur ce PC, sans IA, ou une IA vérifiée), la fraîcheur de
l'état du bot lu, le module d'où vient l'information (porte d'exécution, moteur
de risque, plan de contrôle…), et le rappel qu'elle n'agit pas.

## La voix

Un bouton 🔊 lit la réponse à voix haute (synthèse vocale du navigateur, sur ce
PC, en français) ; un second clic l'arrête. Rien n'est envoyé ailleurs. Pas de
dictée : le clavier suffit.

## L'état global

Dix domaines mesurés, jamais supposés : intelligence, agents, données, finance,
risque, exécution (« sûr » si l'arrêt d'urgence ou le mode sûr est posé),
sécurité, infrastructure, apprentissage, IA (`/api/interface`). Chaque action
est attribuée : vous, le système, un automatisme, une IA.

## L'examen : AC-001 à AC-070, note, verdict (§65-66)

Mesurés sur le code du panneau : toutes les commandes du serveur classées,
aucune qui exécute, voix, « Pourquoi ? », fraîcheur ; prouvés par les tests du
dépôt : mot de passe, sessions expirées, blocage après 5 échecs, hôtes et
origines étrangers refusés, injection de code refusée, droit de chaque action…
Un P0 non résolu : REJECTED. L'interface n'est jamais une autorité de sécurité
(contrat `InterfaceReadinessReport.v1`).

## Ce qui ne s'applique pas

- **Centre de commande 3D, jumeau numérique 3D, effets visuels** (§3-15, §27-28,
  §55-56) : non construits ; le panneau 2D suffit pour un seul PC, et la 3D ne
  doit jamais être le seul accès à une information critique.
- **Mot de réveil, reconnaissance du locuteur, dictée, vision** (§17-18, §35) :
  le clavier et la souris suffisent ; aucune décision ne dépend d'une image.
- **Profils d'utilisateurs, plusieurs écrans** (§32, §54) : un seul utilisateur,
  vous.
- **Confirmation des actions critiques** (§22) : aucune action critique au
  panneau.
