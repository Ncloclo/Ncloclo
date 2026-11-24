# Combiner TrendGuard avec d'autres stratégies ?

Données Binance des 21 paires du bot jusqu'au 2026-11-22, frais et glissement de 0,1 % par côté, 1 % de risque par trade dans chaque manche. Régénérer : `python -m research.combinaisons --cache data_binance`.

Protocole fixé avant les mesures : combinaisons déclarées à l'avance, choix sur 2018-2022, vérification depuis 2023 ; une combinaison n'est proposée que si elle bat TrendGuard seule sur les deux périodes.

| Combinaison | 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | 24/05/2026 → : rendement | baisse |
|---|---|---|---|---|---|---|---|---|
| TrendGuard seule (référence) | +41,2 % | −25,4 % | 1,62 | +32,0 % | −33,6 % | 0,95 | +8,0 % | −10,5 % |
| TrendGuard + Tortues | +47,1 % | −32,0 % | 1,47 | +37,4 % | −33,0 % | 1,13 | +14,9 % | −14,7 % |
| TrendGuard + Bollinger | +30,2 % | −28,9 % | 1,05 | +38,3 % | −30,9 % | 1,24 | +13,3 % | −13,4 % |
| TrendGuard + Tortues + Bollinger | +37,6 % | −32,2 % | 1,17 | +39,8 % | −31,4 % | 1,26 | +16,1 % | −15,2 % |
| TrendGuard + Tendance rapide | +42,3 % | −27,5 % | 1,54 | +35,8 % | −33,9 % | 1,06 | +15,7 % | −14,0 % |

Corrélation des rendements journaliers des manches (2018 →) :

| | Cassure de Bollinger (J. Bollinger) | Tendance rapide | Tortues, système 1 (R. Dennis) | TrendGuard (référence) |
|---|---|---|---|---|
| Cassure de Bollinger (J. Bollinger) | 1,00 | 0,88 | 0,89 | 0,87 |
| Tendance rapide | 0,88 | 1,00 | 0,88 | 0,93 |
| Tortues, système 1 (R. Dennis) | 0,89 | 0,88 | 1,00 | 0,88 |
| TrendGuard (référence) | 0,87 | 0,93 | 0,88 | 1,00 |

## Verdict

- Choisie sur 2018-2022 (meilleur Calmar) : **TrendGuard seule (référence)**.
- Battent TrendGuard seule sur les deux périodes : **aucune**.
- **Aucune combinaison ne justifie de changer** : la règle fixée d'avance n'en retient aucune. TrendGuard reste seule.

## Limites

- Un backtest n'est pas une garantie de gain.
- Période jamais vue : portefeuilles lancés en 2018 et mesurés depuis le 24/05/2026, positions déjà ouvertes comprises ; un départ à zéro ce jour-là donne des chiffres un peu différents (`docs/PETIT_CAPITAL.md`).
- Les stratégies de suivi de tendance sont corrélées : elles perdent souvent ensemble, ce qui limite le gain de la diversification.
- Rendements des manches mêlés chaque jour : en réel, chaque manche aurait sa part du capital, rééquilibrée moins souvent ; avec 100 USDT, chaque manche achèterait des positions plus petites (minimum de Binance, `docs/PETIT_CAPITAL.md`).
