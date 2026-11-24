# Bibliothèque de recherche

Votre demande (24 novembre) : un bot qui se forge une connaissance d'expert en
finance et en trading, au fur et à mesure, comme un chercheur confirmé.

Le noyau de savoir lisait déjà la presse, les moteurs de recherche, les forums
et les réseaux sociaux. Il lui manquait la recherche elle-même. Depuis le 24
novembre 2026, le bot lit aussi les travaux de finance quantitative publiés sur
arXiv. Code : `trendguard/bibliotheque.py`.

## Ce qu'il lit, une fois par jour

- **Source** : l'API publique d'arXiv (`export.arxiv.org`), sans clé.
- **Catégories** : trading et microstructure, gestion de portefeuille, risque,
  finance statistique et calculatoire.
- **Fréquence** : avec le noyau de savoir, au plus une fois par jour. Après
  une panne, nouvel essai six heures plus tard ; arXiv limite le débit.

## Ce qu'il en garde

Chaque travail reçoit une note de pertinence pour le bot, par thème : suivi de
tendance, momentum, volatilité et taille des positions, régimes de marché,
cryptos, coûts d'exécution, sentiment, pièges des backtests. Les cryptos et le
suivi de tendance, le cœur du bot, comptent double.

Les travaux pertinents (note d'au moins 3) sont gardés dans le noyau de savoir,
400 au plus. Chacun devient une piste : la partie du bot qu'il concerne et la
question à vérifier sur ses données. Exemple : « ce résultat sur le suivi de
tendance tient-il sur les 21 cryptos du bot, hors échantillon ? ».

## Le bot teste lui-même les pistes

Une fois par semaine (`python trendguard_bot.py bibliotheque tester`, code
`trendguard/pistes.py`), le bot pose la question de chaque thème à ses propres
données. Chaque thème a une famille de variantes déclarée à l'avance, rejouée
avec la boucle de backtest du bot, sur 2018-2022 puis depuis 2023 :

| Thème | Variantes essayées |
| --- | --- |
| suivi de tendance | cassure de 20 jours et momentum de 60 jours ; cassure de 55 jours et momentum de 180 jours |
| momentum | momentum de 60 jours ; momentum de 180 jours |
| volatilité et taille des positions | profil prudent ; stop initial à 2 ou à 4 volatilités |
| régimes de marché | BTC au-dessus de sa moyenne de 100 ou de 200 jours |
| coûts d'exécution | frais et glissement doublés (épreuve de résistance) |

Ce n'est pas la méthode exacte du travail lu : c'est sa question, posée aux
données du bot. Une variante qui fait mieux (Calmar) sur les deux périodes est
« à étudier » : une proposition, jamais appliquée seule. Les thèmes cryptos,
sentiment et méthode sont déjà mesurés en continu ailleurs (sélection des
cryptos, noyau de savoir, robustesse).

Premier test, le 24 novembre 2026 : la règle actuelle tient sur chaque thème.
Des variantes font mieux depuis 2023 (momentum de 60 jours : Calmar 1,24
contre 0,95) mais moins bien sur 2018-2022 (1,20 contre 1,62) ; avec des coûts
doublés, la stratégie reste rentable (Calmar 1,52 puis 0,83).

## Où le voir

- Panneau, page Veille : carte « Bibliothèque de recherche ».
- Comité d'agents : l'agent « chercheur » cite les deux pistes les plus
  pertinentes à chaque achat étudié, avec leur test par le bot. Il ne vote
  pas.
- Ligne de commande : `python trendguard_bot.py bibliotheque` (pistes) et
  `python trendguard_bot.py bibliotheque lire` (nouvelle lecture).

## Ce qu'il ne fait pas, et pourquoi

- **Une piste ne change jamais une règle.** Un résultat publié porte souvent
  sur d'autres marchés ou d'autres années, et beaucoup ne se répètent pas. Une
  idée ne change le bot qu'après une étude reproductible (`research/`), jugée
  hors échantillon.
- **Un texte lu est une donnée, jamais une instruction.** Le lien de chaque
  travail est refait depuis son identifiant arXiv vérifié, jamais repris tel
  quel du flux.
