# Hors cryptos, mais sur Binance : or, euro, actions tokenisées

Sous-jacents rejoués (Yahoo Finance) jusqu'au 2026-11-22, frais du bot sur Binance (0,1 % + 0,1 % de glissement par côté), règle de TrendGuard transposée (régime propre à chaque actif). Régénérer : `python -m research.binance_hors_cryptos --cache data_multi`.

Écartés avant les mesures : fonds à levier : SOXLB, SOXSB, KORUB, SNXXB, TQQQB ; sociétés non cotées : SpaceX (SPCXB), Anthropic (ANTB) ; cotations trop récentes : Circle (CRCLB), Nebius (NBISB), SanDisk (SNDKB).

## Chaque actif seul

| Actif | Règle 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | Achat-conservation 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Or (PAXG, XAUT) | +0,4 % | −6,1 % | 0,06 | +1,2 % | −3,6 % | 0,35 | +6,9 % | −21,2 % | 0,33 | +18,4 % | −33,9 % | 0,54 |
| Euro (EUR/USDT) | +0,2 % | −1,8 % | 0,10 | −0,5 % | −2,6 % | −0,21 | −2,3 % | −23,3 % | −0,10 | +0,6 % | −8,8 % | 0,07 |
| Nvidia (NVDAB) | +1,7 % | −8,0 % | 0,21 | +3,7 % | −8,5 % | 0,43 | +24,8 % | −66,4 % | 0,37 | +74,0 % | −47,5 % | 1,56 |
| Micron (MUB) | +0,9 % | −7,8 % | 0,11 | +4,2 % | −7,0 % | 0,60 | +4,0 % | −53,7 % | 0,07 | +89,2 % | −57,8 % | 1,54 |
| Alphabet (GOOGLB) | +1,1 % | −3,9 % | 0,28 | +4,4 % | −4,4 % | 0,98 | +10,9 % | −44,3 % | 0,25 | +32,8 % | −34,1 % | 0,96 |
| Intel (INTCB) | −0,9 % | −6,1 % | −0,14 | +2,7 % | −6,5 % | 0,42 | −10,6 % | −63,4 % | −0,17 | +18,6 % | −64,3 % | 0,29 |
| Strategy (MSTRB) | +0,9 % | −10,3 % | 0,09 | +5,9 % | −9,2 % | 0,63 | +1,5 % | −89,3 % | 0,02 | +55,1 % | −83,6 % | 0,66 |
| Bloom Energy (BEB) | +0,3 % | −4,8 % | 0,06 | +4,5 % | −6,4 % | 0,71 | −100,0 % | −92,5 % | −1,08 | +67,7 % | −66,5 % | 1,02 |
| Fonds Nasdaq 100 (QQQB) | +1,8 % | −3,0 % | 0,60 | +2,6 % | −3,1 % | 0,82 | +11,3 % | −35,6 % | 0,32 | +23,7 % | −22,9 % | 1,04 |

## La manche « hors cryptos sur Binance » (tous ensemble, un seul portefeuille)

| | 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|
| Règle | +6,6 % | −14,2 % | 0,47 | +29,3 % | −17,0 % | 1,72 |

Espérance par trade sur 2018-2022 : +0,28 R (TrendGuard sur les cryptos : +1,50 R). Corrélation des rendements journaliers avec TrendGuard sur les cryptos : 0,22.

## Mélangée à TrendGuard sur les cryptos

| Portefeuille | 2018-2022 : CAGR | baisse | Calmar | 2023 → : CAGR | baisse | Calmar | 24/05/2026 → : CAGR | baisse | Calmar |
|---|---|---|---|---|---|---|---|---|---|
| TrendGuard seule (cryptos) | +41,2 % | −25,4 % | 1,62 | +32,0 % | −33,6 % | 0,95 | +16,8 % | −10,5 % | 1,59 |
| 75 % cryptos, 25 % hors cryptos Binance | +32,9 % | −18,9 % | 1,74 | +32,5 % | −23,9 % | 1,36 | +4,9 % | −9,3 % | 0,53 |
| 50 % cryptos, 50 % hors cryptos Binance | +24,3 % | −15,9 % | 1,53 | +32,3 % | −15,2 % | 2,12 | −6,1 % | −8,0 % | −0,76 |

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
