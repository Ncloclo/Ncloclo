# Hors cryptos, mais sur Binance : or, euro, actions tokenisées

Sous-jacents rejoués (Yahoo Finance) jusqu'au 2026-11-22, frais du bot sur Binance (0,1 % + 0,1 % de glissement par côté), règle de TrendGuard transposée (régime propre à chaque actif). Régénérer : `python -m research.binance_hors_cryptos --cache data_multi`.

Écartés avant les mesures : fonds à levier : SOXLB, SOXSB, KORUB, SNXXB, TQQQB ; sociétés non cotées : SpaceX (SPCXB), Anthropic (ANTB) ; cotations trop récentes : Circle (CRCLB), Nebius (NBISB), SanDisk (SNDKB).

## Chaque actif seul

| Actif | Règle 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | Achat-conservation 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Or (PAXG, XAUT) | +0,1 % | −5,3 % | 0,01 | +1,1 % | −3,5 % | 0,32 | +6,9 % | −21,2 % | 0,33 | +18,4 % | −33,9 % | 0,54 |
| Euro (EUR/USDT) | −0,1 % | −1,6 % | −0,05 | −0,6 % | −3,1 % | −0,19 | −2,3 % | −23,3 % | −0,10 | +0,6 % | −8,8 % | 0,07 |
| Nvidia (NVDAB) | +2,0 % | −10,1 % | 0,19 | +3,3 % | −9,8 % | 0,34 | +24,8 % | −66,4 % | 0,37 | +74,0 % | −47,5 % | 1,56 |
| Micron (MUB) | +0,8 % | −7,8 % | 0,10 | +4,6 % | −8,8 % | 0,52 | +4,0 % | −53,7 % | 0,07 | +89,2 % | −57,8 % | 1,54 |
| Alphabet (GOOGLB) | +0,0 % | −5,5 % | 0,00 | +2,7 % | −7,1 % | 0,38 | +10,9 % | −44,3 % | 0,25 | +32,8 % | −34,1 % | 0,96 |
| Intel (INTCB) | −2,3 % | −11,2 % | −0,21 | +3,9 % | −4,8 % | 0,80 | −10,6 % | −63,4 % | −0,17 | +18,6 % | −64,3 % | 0,29 |
| Strategy (MSTRB) | +1,6 % | −10,3 % | 0,16 | +5,7 % | −10,2 % | 0,56 | +1,5 % | −89,3 % | 0,02 | +55,1 % | −83,6 % | 0,66 |
| Bloom Energy (BEB) | +1,1 % | −5,5 % | 0,20 | +1,7 % | −5,7 % | 0,29 | −100,0 % | −92,5 % | −1,08 | +67,7 % | −66,5 % | 1,02 |
| Fonds Nasdaq 100 (QQQB) | +1,4 % | −2,9 % | 0,48 | +1,2 % | −4,8 % | 0,26 | +11,3 % | −35,6 % | 0,32 | +23,7 % | −22,9 % | 1,04 |

## La manche « hors cryptos sur Binance » (tous ensemble, un seul portefeuille)

| | 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|
| Règle | +6,5 % | −20,4 % | 0,32 | +24,3 % | −19,6 % | 1,24 |

Espérance par trade sur 2018-2022 : +0,19 R (TrendGuard sur les cryptos : +1,50 R). Corrélation des rendements journaliers avec TrendGuard sur les cryptos : 0,19.

## Mélangée à TrendGuard sur les cryptos

| Portefeuille | 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | 24/05/2026 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|
| TrendGuard seule (cryptos) | +41,2 % | −25,4 % | 1,62 | +32,0 % | −33,6 % | 0,95 | +16,8 % | −10,5 % | 1,59 |
| 75 % cryptos, 25 % hors cryptos Binance | +32,9 % | −19,4 % | 1,69 | +31,3 % | −23,5 % | 1,33 | +4,3 % | −9,3 % | 0,46 |
| 50 % cryptos, 50 % hors cryptos Binance | +24,2 % | −17,4 % | 1,39 | +29,7 % | −14,9 % | 1,99 | −7,1 % | −8,2 % | −0,87 |

## Verdict (règle fixée avant les mesures)

- Manche rentable sur les deux périodes : **oui**.
- Mélange 75/25 meilleur que TrendGuard seule au Calmar sur les deux périodes : **oui**.
- **Proposition** : suivre cette manche en paper sur Binance (prix réels, argent fictif), à côté du bot ; aucun ordre réel sans votre accord.

## Lecture

- La manche gagne peu sur 2018-2022 et beaucoup depuis 2023 : ce sont les années où les actions retenues (les plus échangées sur Binance aujourd'hui) ont flambé. Ce biais de popularité flatte la vérification.
- Le mélange améliore surtout la baisse maximale ; sur la période jamais vue, il fait moins bien que TrendGuard seule.
- D'où : un suivi en argent fictif (page Veille, « manche Binance ») avant toute décision.

## Limites

- Un backtest n'est pas une garantie de gain.
- Actions choisies parmi celles que Binance liste aujourd'hui : leur passé est flatté.
- Un jeton suit son action avec un écart possible (horaires, dividendes, frais du jeton) ; l'or tokenisé suit l'once d'or à quelques dixièmes de pourcent près.
- Le week-end, les jetons d'actions bougent sans que l'action ne cote : le backtest, au rythme des séances, ne le voit pas.
