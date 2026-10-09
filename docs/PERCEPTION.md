# Perception et observations multi-sources (étape 29 du prompt maître)

La perception est ce que le bot voit du marché, source par source, avant
tout raisonnement : chaque donnée devient une observation datée, tracée et
alignée dans le temps ; quand deux sources parlent du même fait le même jour,
elles sont recoupées ; ce qui manque est dit INCONNU, jamais deviné. La
perception observe ; elle ne décide pas.

Code : [`trendguard/perception.py`](../trendguard/perception.py). Tests :
[`tests/test_perception.py`](../tests/test_perception.py).

```text
python trendguard_bot.py perception                  # observations, faits, conflits, dépendances
python trendguard_bot.py perception --out docs/PERCEPTION_ETAT.md
```

Dernier état : [`PERCEPTION_ETAT.md`](PERCEPTION_ETAT.md). Chaque nuit, le
rapport donne la ligne « Perception ».

## Les sources et leurs modalités

| Source | rang | modalité |
| --- | --- | --- |
| bougies du bot (dernière décision) | 1 | cours journaliers |
| cache de l'évolution (`data_evolution`) | 1 | cours journaliers |
| cache des études (`data_binance`) | 2 | cours journaliers |
| note des données | 1 | contrôle des données |
| carnets d'ordres | 2 | carnet d'ordres |
| horloge (écart avec Binance) | 1 | horloge |
| calendrier économique | 2 | calendrier |

Les trois séries de cours viennent de Binance : elles se recoupent, elles ne
sont pas indépendantes. Chaque observation passe par le contrat
`Observation.v1` : sans source ou sans provenance, elle est refusée.

## Les règles

- **Alignement dans le temps** (§16) : une bougie du jour J n'est close qu'à
  J+1 0 h UTC ; une bougie pas encore close est refusée (FUTURE) ; une donnée
  sans date (carnets d'ordres, moyennes) n'entre dans aucune comparaison
  (UNDATED) ; une bougie de plus de trois jours est marquée (STALE). L'horloge
  du PC doit avoir été comparée à celle de Binance depuis moins d'un jour, à
  moins d'une seconde près.
- **Fusion** (§18-19) : les observations du même fait le même jour sont
  recoupées ; d'accord à 0,5 % près : confirmé ; en désaccord : conflit, la
  source de plus haut rang l'emporte et l'autre valeur est gardée et montrée.
  Jamais une moyenne.
- **Incertitude** (§21, §37) : chaque fait a sa confiance (haute : confirmé ;
  moyenne : une seule source ; basse : conflit ou trop vieux) ; une crypto
  sans observation datée est INCONNUE. Un fait avec une valeur et sans source
  est une hallucination : le contrat `PerceptionReport.v1` le refuse.
- **Robustesse** (§49) : sans les bougies du bot, la perception tient et
  n'invente rien.

## Le manifeste des dépendances (corrections de l'étape 29)

Le statut dit si une dépendance est obligatoire ; le type dit sa nature ;
jamais l'un pour l'autre, un seul statut par dépendance.

| Étape | module | statut | type |
| --- | --- | --- | --- |
| 03 | `donnees.py` | OBLIGATOIRE | DATA |
| 06 | `contrats.py` | OBLIGATOIRE | CONTRACT |
| 20 | `apprentissage.py` | OBLIGATOIRE | GOVERNANCE |
| 21 | `cyber.py` | OBLIGATOIRE | SECURITY |
| 07 | `modeles.py` | CONDITIONNELLE (si une IA est utilisée) | MODEL |
| 19 | `controle.py` | ACTIVATION (pour la production) | DEPLOYMENT |
| 22 à 28 | interface, recherche, mémoire, monde, causal, objectifs, jumeau | OPTIONNELLE | INTEGRATION |
| 15 à 18 | politique, autorisation, porte, ordres | INTERDITE | — |

Vérifié à chaque appel, par lecture du code : aucune dépendance interdite
importée, aucun cycle (une dépendance obligatoire n'importe jamais la
perception), aucune bibliothèque réseau, la dépendance conditionnelle jamais
utilisée sans sa fonction. Numéros des documents de la RACI (19 plan de
contrôle, 20 gouvernance des modèles, 21 cybersécurité).

## La note (§52-53)

Justesse de la perception 20 %, fusion 15 %, preuve et provenance 15 %,
alignement dans le temps 10 %, incertitude 10 %, robustesse 10 %, sécurité et
dépendances 10 %, performance 5 %, observabilité 5 %. READY à partir de 95,
CANDIDATE 90, VALIDATING 80, DEVELOPMENT 70, sinon REJECTED ; un défaut P0
(provenance, alignement, dépendances) : NOT_READY quelle que soit la note.

## Ce qui ne s'applique pas

- **Images, vidéo, son, parole, documents scannés, capteurs, LiDAR,
  géospatial** (§7, §10-15, §17, §29) : le bot ne reçoit que des chiffres et
  des textes ; les textes (actualités, réseaux) sont jugés par la recherche
  ([`RECHERCHE.md`](RECHERCHE.md)).
- **RAG multimodal, agents multimodaux, centre 3D** (§22, §27-28) : la voix
  de Rachelle lit ses réponses ([`INTERFACE.md`](INTERFACE.md)) ; rien d'autre
  à percevoir.
- **Modèles de perception à gouverner** (§34) : aucun modèle appris ici,
  seulement des règles écrites et testées.
