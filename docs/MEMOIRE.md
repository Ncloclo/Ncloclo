# Mémoire et graphe de connaissances (étape 24 du prompt maître)

Ce que TrendGuard sait, relié et daté : cryptos, positions, trades, ordres,
décisions, retraits annoncés par Binance, lecture du marché, modèles, services,
contrats et procédures. La mémoire est reconstruite à la demande depuis les
sources de vérité ; elle n'est jamais stockée à part, donc elle ne peut pas
diverger d'elles.

Code : [`trendguard/memoire.py`](../trendguard/memoire.py). Tests :
[`tests/test_memoire.py`](../tests/test_memoire.py).

```text
python trendguard_bot.py memoire              # santé et contenu
python trendguard_bot.py memoire ETH          # tout ce que la mémoire sait d'ETH, avec ses sources
```

## D'où viennent les faits

| Mémoire | Entités | Source de vérité |
| --- | --- | --- |
| épisodique | positions, trades, retraits annoncés, lecture du marché du jour | état du bot |
| épisodique | trades, ordres d'achat et de vente, décisions | journal financier (lignée de chaque trade) |
| sémantique | cryptos, modèles, services et leurs dépendances, contrats | réglages, registres (étapes 18, 19, contrats) |
| procédurale | procédures et le service qu'elles réparent | plan de contrôle (étape 18) |

Chaque fait a sa **provenance** (la source qui l'a dit), ses **dates** (valide
du…
au…) et sa **nature** : observé, déduit (marqué comme tel, avec sa règle : par
exemple « ce trade s'est terminé en perte », déduit de son résultat) ou rapporté
(ce qu'une source extérieure dit, jamais un fait sur une crypto).

## Les questions

- **Tout sur une entité** : ses relations, chacune avec sa source et sa nature.
- **Ce qui était vrai à une date** : positions et trades ouverts ce jour-là.
- **Le chemin entre deux entités** : par exemple du bot à Internet, par la base
  et Binance.
- **Contradictions** entre sources (une position sans crypto connue…).
- **Un même trade vu par deux sources** (état du bot et journal financier) : relié
  par une déduction (même crypto, même jour de vente), jamais fusionné en silence.

## La santé de la mémoire (§59, §100)

| Famille | Poids | Mesure |
| --- | --- | --- |
| intégrité | 20 % | aucune relation sans extrémité |
| provenance | 15 % | aucun fait sans source |
| recherche | 15 % | chaque entité retrouvée |
| gouvernance | 10 % | chaque fait observé, déduit ou rapporté |
| sécurité | 15 % | aucun texte extérieur pris pour un fait ; aucune IA n'écrit dans la mémoire |
| raisonnement | 10 % | chaque déduction marquée, avec sa règle |
| cohérence dans le temps | 5 % | aucun fait qui finit avant de commencer |
| performance | 5 % | reconstruite en moins de 30 secondes |
| observabilité | 5 % | contenu et santé lisibles |

Un défaut d'intégrité, de provenance, de sécurité ou de dates est un P0 : la
mémoire n'est pas prête. Le verdict **READY_FOR_GOVERNED_LONG_TERM_MEMORY**
(contrat `MemoryHealthReport.v1`) n'autorise aucune réécriture silencieuse : la
mémoire se reconstruit, les sources gardent leur historique (journal financier
en ajout seul, audit chaîné).

## Ce qui ne s'applique pas

- **Base de graphe, recherche vectorielle, plusieurs locataires, chiffrement
  propre** (§28, §41-42, §48) : un seul PC, un graphe reconstruit en mémoire en
  moins d'une seconde ; les sources sont déjà sur ce PC seulement.
- **Extraction de connaissances par des IA, comité de vérification** (§67-69) :
  aucune IA n'écrit dans la mémoire.
- **Mémoire personnelle de l'utilisateur** (§10) : rien n'est gardé sur vous
  au-delà
  de vos réglages.
