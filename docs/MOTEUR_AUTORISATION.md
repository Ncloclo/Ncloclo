# Moteur d'autorisation (étape 15 du prompt maître)

Qui peut faire quoi, sur quelle ressource, à quelles conditions, et ce que
personne d'autre que vous ne peut faire. Refus par défaut : une identité, une
action ou une condition inconnue donne un refus, avec sa raison.

```text
identité → rôle → permission (action × ressource) → conditions du moment
→ PERMIS ou REFUSÉ, avec les raisons et les conditions vérifiées (valable 5 minutes)
```

Code : [`trendguard/autorisation.py`](../trendguard/autorisation.py).
Tests : [`tests/test_autorisation.py`](../tests/test_autorisation.py).

```text
python trendguard_bot.py autorisation                        # la matrice et la séparation des tâches
python trendguard_bot.py autorisation verifier rachelle ARM_LIVE
```

## Les identités et leurs droits

| Identité | Type | Droits |
| --- | --- | --- |
| vous | personne | tout ce qui engage : armer le réel (deux réglages explicites), plafonds de risque, mode sûr, reprise après l'arrêt d'urgence, clés et mots de passe (outils masqués), réglages, cryptos, fusion du code |
| le panneau | session ouverte par votre mot de passe | démarrer ou arrêter le bot, choisir les cryptos, lancer le rapport, tester les alertes ; chaque action vérifiée, une action sans droit déclaré est refusée |
| la règle | système | proposer un achat |
| la porte d'exécution | système | autoriser un achat, si le contrôle du risque est approuvé |
| le bot | service d'exécution | acheter (paper : contrôle approuvé et autorisation valable ; réel : en plus, réel armé par vous, porte du réel ouverte, évaluation du risque du jour valide), vendre, déclencher l'arrêt d'urgence, poser le mode sûr |
| l'évolution encadrée | automatisme | changer un réglage permis à son niveau, après ses épreuves (jamais les plafonds de risque, les positions, l'arrêt d'urgence ni le réel ; le risque par achat seulement jusqu'au plafond que vous avez fixé, `TG_RISK_MAX_PCT`) ; gelée en réel contrôlé (étape 19) |
| le noyau de savoir | automatisme | reporter un achat sur une annonce de Binance |
| le bot libre | automatisme | acheter dans son propre portefeuille fictif |
| la maintenance | automatisme | installer une mise à jour fusionnée par vous, contrôles au vert, en avance rapide, jamais en réel |
| le superviseur | automatisme | relancer le bot après un plantage, poser le mode sûr (étape 18) ; rien d'autre |
| Rachelle, les IA de la veille, le comité | agents et IA | lire, analyser, recommander |
| les routines dans le nuage | extérieur | lire, analyser, proposer du code (Pull Request), jamais fusionner |

## Ce qui est vérifié

- **Séparation des tâches** : la règle propose, la porte autorise, le bot
  exécute ; personne ne propose et n'autorise, personne n'autorise et
  n'exécute ; armer le réel, changer le risque, lever le mode sûr, reprendre
  après l'arrêt d'urgence, saisir des secrets et fusionner du code sont à vous
  seul ; seul le bot achète en réel.
- **Aucune IA, aucun agent avec un droit critique** : vérifié sur la matrice,
  et refusé par le contrat lui-même (`AuthorizationDecision.v1`) même si la
  matrice était mal écrite.
- **Délégations explicites, sans re-délégation** : de vous au panneau, à
  l'évolution et à la maintenance, jamais au-delà de vos propres droits.
- **Conditions exactes** : une condition doit valoir exactement « vrai » ; une
  condition absente ou ambiguë est un refus.
- **Autorisation d'achat** (porte d'exécution) : liée à son contrôle du risque,
  valable 5 minutes, à usage unique (la clé d'unicité de l'ordre interdit de la
  rejouer, et la base la refuse).
- **Dans le bot** : à chaque achat, le droit du bot est vérifié et comparé à la
  réponse de la porte ; un écart est signalé au journal du bot et au rapport ;
  aucun trade changé (test).

## Vous êtes seul : le « quatre yeux »

Le prompt demande qu'une opération sensible ait deux approbations distinctes.
Vous êtes le seul propriétaire : il n'y a pas de seconde personne. Le réel
demande donc deux réglages explicites distincts (le mode réel et une
confirmation écrite), puis la porte du réel, mesurée chaque jour et fermée tant
que ses conditions manquent (60 jours de paper, 10 trades clos, sécurité sans
défaut, vérification sans ordre réussie). Aucun automatisme, aucune IA ne peut
remplacer l'une de ces étapes.

## Exigences de l'étape 15 → TrendGuard

| Exigence de l'étape 15 | Dans TrendGuard |
| --- | --- |
| Identités, RBAC, ABAC, ressources, permissions (§5-9) | 14 identités typées, 13 rôles, 25 actions (superviseur ajouté à l'étape 18), conditions du moment, ressource nommée |
| Demande et décision (§10, §37) | `decide` : PERMIS ou REFUSÉ, raisons, conditions vérifiées, version, fin de validité |
| Politique et risque validés (§11-13) | le droit du bot d'acheter exige le contrôle approuvé de la porte (politiques, risque, moteur de risque) |
| Accord humain, quatre yeux, autonomie (§14-17) | armer le réel : deux réglages explicites et la porte du réel ; jamais d'automatisme ni d'IA |
| Limites, durée, usage unique, anti-rejeu (§18-23) | autorisation d'achat liée à son contrôle, 5 minutes, clé d'unicité en base |
| Révocation, urgence (§24-25) | arrêt d'urgence et mode sûr : plus aucun achat, à tout moment, sans IA |
| Séparation des tâches, agents, IA, délégation (§26-30) | vérifiées sur la matrice et par le contrat |
| Panneau | chaque action vérifiée ; refus renvoyé avec sa raison |

## Ce qui ne s'applique pas

- **Comptes multiples, organisations, sessions multiples** (§5) : un seul
  propriétaire, un panneau limité à ce PC avec mot de passe.
- **Jetons signés par une clé matérielle (KMS, HSM)** (§22-23) : l'autorisation
  d'achat ne quitte jamais le processus du bot ; son unicité est garantie par
  la base.
- **Dérogations** (§31) : aucune ; une règle change dans le code.
- **Double approbation par deux personnes** (§15-16) : un seul propriétaire ;
  voir « Vous êtes seul » ci-dessus.
