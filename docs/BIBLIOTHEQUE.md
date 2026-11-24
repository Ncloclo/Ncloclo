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

## Où le voir

- Panneau, page Veille : carte « Bibliothèque de recherche ».
- Comité d'agents : l'agent « chercheur » cite les deux pistes les plus
  pertinentes à chaque achat étudié. Il ne vote pas.
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
