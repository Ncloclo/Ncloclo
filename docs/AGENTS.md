# Socle multi-agents et comité d'agents financiers (étape 5 du prompt maître)

Plusieurs agents spécialisés analysent ensemble, se critiquent et se
vérifient, sous un superviseur, sans jamais pouvoir agir sur l'argent.

```text
mission → superviseur → équipe → analyses indépendantes (tableau noir cloisonné)
→ critique, équipe rouge, vérificateur indépendant → débat (révision sur preuves)
→ désaccord mesuré → consensus pondéré, quorum → recommandation (jamais une autorisation)
```

Code : [`trendguard/agents.py`](../trendguard/agents.py) (le socle),
[`trendguard/comite.py`](../trendguard/comite.py) (le comité financier),
[`research/comite.py`](../research/comite.py) (son évaluation sur 8 ans).
Tests : [`tests/test_agents.py`](../tests/test_agents.py).

## Le comité d'agents financiers

Chaque nuit, à la décision de 00:02 UTC, le comité examine les cryptos que la
règle du bot propose d'acheter (6 au plus). Pour une crypto précise, à tout
moment : `python trendguard_bot.py comite aave`. Rachelle répond à « que
penses-tu de AAVE ? » avec l'avis du jour.

| Agent | Famille | Lit | Poids | Veto | Critique |
| --- | --- | --- | --- | --- | --- |
| données | analyse | marché, qualité | 0 | oui | oui |
| technique (cassure du plus haut de 30 jours) | analyse | marché | 1 | | oui |
| quant (momentum, volatilité) | analyse | marché | 1 | | |
| régime (BTC, volatilité, phase) | analyse | régime | 1 | | oui |
| sentiment (sources prouvées du noyau de savoir) | analyse | marché, savoir | 0,5 | | |
| risque (liquidité, positions, budget de risque, stop) | analyse | marché, portefeuille, politique | 1 | oui | oui |
| portefeuille (corrélation avec les positions) | analyse | marché, portefeuille | 0,5 | | |
| « pas de trade » (toutes les raisons de s'abstenir) | analyse | marché, régime, politique | 0 | oui | |
| critique (avis tranché sans preuve, signaux contraires) | contrôle | avis | 0 | | |
| équipe rouge (historique perdant, volatilité extrême) | contrôle | avis, marché, régime, historique | 0 | | |
| vérificateur indépendant (recalcule cassure et momentum sans les indicateurs du bot) | contrôle | avis, marché | 0 | | |

**Recommandation**, dans cet ordre : agent critique absent ou violation →
BLOCAGE ; un veto → PAS DE TRADE ; données sous 50/100 → PLUS DE RECHERCHE ;
désaccord fort → ATTENDRE ; déjà détenue → CONSERVER ; consensus de +30 au
moins et cassure confirmée → ACHAT ; sinon ATTENDRE.

## Ce que vaut le comité : mesuré sur 8 ans

[`COMITE_ETUDE.md`](COMITE_ETUDE.md) (`python -m research.comite --cache
data_binance`) : sur les 313 trades de la stratégie depuis 2018, ceux que le
comité aurait approuvés font un peu mieux (+1,20 R en moyenne, 45 % de
gagnants) que ceux où il aurait dit « attendre » (+1,08 R, 39 %). Mais
n'acheter que sur son avis aurait coûté cher : Calmar 0,83 au lieu de 1,54
sur 2018-2022, 1,20 au lieu de 1,33 depuis 2023, car les trades « attendre »
rapportent encore +214 R au total. **Le comité reste donc consultatif** : il
explique et met en garde, la règle décide. Ses avis sont gardés dans le
journal financier (table `fin_committee_views`), et le rapport compare chaque
avis aux trades réels ; une règle ne serait proposée que si l'écart devenait
net sur les deux époques.

## Garanties du socle

| Exigence de l'étape 5 | Dans le code |
| --- | --- |
| Registre et manifestes (§4-5) | identifiant, version, rôle, famille, capacités, lectures permises, poids, veto, délai ; un manifeste qui demanderait de passer des ordres est refusé |
| Équipe et superviseur (§11-13) | équipe choisie par capacités ; analyses en parallèle, puis contrôles ; le superviseur n'exécute aucune opération |
| Tableau noir cloisonné, pas d'effet moutonnier (§18-19, §23) | un agent ne lit que ce que son manifeste permet ; les analystes ne voient pas les avis des autres avant d'avoir rendu le leur |
| Bus de messages (§20-21) | messages horodatés, empreinte du contenu, identifiant unique : un message rejoué n'est pas traité deux fois ; hors du bus, rien ne passe |
| Sortie structurée (§17) | position, score, preuves, hypothèses, incertitudes, veto, objections ; un avis tranché sans preuve est refusé |
| Débat, désaccord, consensus (§23-26) | révision sur preuves seulement (objection retenue : poids réduit ou annulé) ; désaccord mesuré ; consensus pondéré par l'expertise et la fiabilité mesurée ; un consensus faible est dit faible ; quorum des agents critiques |
| Critique, équipe rouge, vérification indépendante (§27-29) | trois agents de contrôle ; le vérificateur recalcule autrement |
| Quarantaine, disjoncteur (§45-46) | lecture interdite ou usurpation d'identité : quarantaine immédiate ; trois échecs de suite : disjoncteur ; remise en service seulement après examen |
| Panne d'un agent (§50, §74) | agent non critique en panne : mission dégradée, poursuivie ; agent critique absent (risque, données, technique, régime) : BLOCAGE |
| Pas de trade, quatre yeux, anti-collusion (§43-44, §60-61) | l'agent « pas de trade » cherche les raisons de s'abstenir ; une recommandation n'est jamais une autorisation (refusée par construction) ; aucun agent n'a accès aux ordres ; la règle du bot puis la porte d'exécution décident, comme avant |
| Banc d'essai (§54) | 8 scénarios de référence au résultat connu (`python trendguard_bot.py comite --banc`) : tendance nette, marché baissier, crypto peu échangée, données périmées, garde du jour, déjà détenue, signaux contraires, agent du risque en panne |
| Mesures des agents (§53) | exécutions, échecs, durée moyenne, état, version ; fiabilité mesurée dans le consensus |

## Pas appliqué ici, et pourquoi

- **Agents fondés sur des IA, débat entre IA** : aucune clé d'IA sur ce PC.
  Les agents du comité sont des règles écrites et testées ; un agent IA
  pourra s'ajouter par un manifeste quand une clé existera, sans changer le
  socle.
- **Analyse fondamentale, macroéconomie** : sans objet pour des cryptos sans
  bilan ; le régime de BTC et le calendrier des annonces en tiennent lieu.
- **Agents de cybersécurité offensive, agents d'ingénierie** : le centre de
  sécurité et le rapport surveillent le PC et le code ; l'amélioration
  quotidienne et la revue hebdomadaire (dans le cloud) proposent des
  changements que vous validez.
- **Kafka, Redis, Temporal, API réseau** : tout tourne dans le processus du
  bot, sur un seul PC ; rien ne le justifie (§85 de l'étape 3).
