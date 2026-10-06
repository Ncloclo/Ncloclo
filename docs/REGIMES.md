# Régimes de marché : où la stratégie gagne, où elle échoue

Étude reproductible : `python -m research.regimes --cache data_binance`
(prompt maître, §8 et §45 : « dans quelles conditions cette stratégie a-t-elle
échoué ? »). Les trades de TrendGuard, rejoués avec les réglages du bot sur
les données Binance, sont classés selon le régime du jour de l'achat. Le bot
ne décide rien sur ces lectures : sa règle de marché reste BTC au-dessus de
sa moyenne 150 jours ; elles servent à comprendre et à expliquer.

## Données : 21 cryptos, 2018-01-01 → 2026-09-27, 313 trades

### Tendance de BTC le jour de l'achat

| Régime | Trades | Gagnants | R moyen | R total |
| --- | --- | --- | --- | --- |
| haussière | 222 | 42 % | +1,25 | +277,7 |
| sans tendance | 91 | 38 % | +0,81 | +73,4 |

### Volatilité de BTC le jour de l'achat

| Régime | Trades | Gagnants | R moyen | R total |
| --- | --- | --- | --- | --- |
| normale | 170 | 38 % | +0,98 | +167,0 |
| faible | 123 | 46 % | +1,30 | +159,7 |
| forte | 20 | 35 % | +1,22 | +24,4 |

### Appétit pour le risque le jour de l'achat

| Régime | Trades | Gagnants | R moyen | R total |
| --- | --- | --- | --- | --- |
| risk-on | 203 | 43 % | +1,32 | +268,6 |
| risk-off | 61 | 38 % | +0,94 | +57,6 |
| mitigé | 49 | 35 % | +0,51 | +24,9 |

### Phase du marché le jour de l'achat

| Régime | Trades | Gagnants | R moyen | R total |
| --- | --- | --- | --- | --- |
| normale | 228 | 41 % | +1,31 | +299,5 |
| reprise | 57 | 32 % | +0,29 | +16,8 |
| crise | 28 | 57 % | +1,25 | +34,9 |

## Ce qu'il faut en retenir

Dans aucun régime (10 trades au moins), la stratégie n'a perdu en moyenne.
Les régimes où elle gagne le moins : reprise (+0,29 R par trade, 32 % de
gagnants), mitigé (+0,51 R par trade, 35 % de gagnants).

Le régime ne suffit pas à décider : un trade gagnant sur trois ou quatre paie
les autres, dans tous les régimes où la stratégie achète. Le filtre qui compte
déjà est la tendance de BTC (aucun achat en marché baissier).
