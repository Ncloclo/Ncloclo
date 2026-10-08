# Portefeuille : la taille des achats éprouvée

Tiré de `python trendguard_bot.py portefeuille etude` (étape 12 du prompt
maître, [`MOTEUR_PORTEFEUILLE.md`](MOTEUR_PORTEFEUILLE.md)) : la même règle, les
mêmes achats et les mêmes ventes, seule la taille des achats change. Frais et
glissement compris.

| Taille des achats | 2018-2022 rendement | baisse | Calmar | Sharpe | exposition | depuis 2023 rendement | baisse | Calmar | Sharpe | exposition |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| la règle : même risque jusqu'au stop | +44,0 % | −32,3 % | 1,36 | 1,19 | 15 % | +33,2 % | −25,2 % | 1,32 | 1,17 | 28 % |
| montant égal par achat | +27,5 % | −32,4 % | 0,85 | 0,99 | 12 % | +23,2 % | −26,2 % | 0,89 | 0,98 | 20 % |
| même risque, divisé par deux si l'achat suit les positions (corrélation > 0,7) | +38,4 % | −35,9 % | 1,07 | 1,12 | 14 % | +35,5 % | −25,2 % | 1,41 | 1,24 | 27 % |

## Verdict

- La règle reste : aucune autre taille ne la bat nettement sur les deux époques.
- La règle dimensionne déjà chaque achat en fonction de sa volatilité (même
  risque jusqu'au stop) : c'est une parité de risque position par position. Une
  autre taille ne change pas les trades, seulement ce que chacun pèse.
