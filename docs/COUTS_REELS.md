# Coûts réels d'exécution sur Binance

Étude reproductible : `python -m research.couts --cache data_binance` (carnets
relevés le 2026-11-23, 21 paires, médiane de 3 relevés de 1000 niveaux par
côté).

Les backtests du dépôt comptent 0,1 % de glissement par ordre pour toutes les
cryptos. Ici, chaque crypto paie le coût mesuré dans son vrai carnet d'ordres
(demi-écart plus profondeur traversée), qui grandit avec le montant de l'ordre.
Les frais Binance (0,1 % par ordre) s'ajoutent dans les deux cas.

## Coût mesuré d'un ordre au marché

| Crypto | Écart achat/vente | Profondeur à 1 % | 1 000 USDT | 10 000 USDT | 100 000 USDT | 1 000 000 USDT |
| --- | --- | --- | --- | --- | --- | --- |
| BTC | 0,000 % | 9 538 783 USDT | 0,000 % | 0,000 % | 0,002 % | 0,014 % |
| ETH | 0,001 % | 6 557 397 USDT | 0,000 % | 0,000 % | 0,002 % | 0,031 % |
| BNB | 0,002 % | 2 380 087 USDT | 0,001 % | 0,006 % | 0,027 % | 0,180 % |
| XRP | 0,010 % | 1 400 986 USDT | 0,005 % | 0,006 % | 0,023 % | 0,131 % |
| TRX | 0,032 % | 1 628 271 USDT | 0,016 % | 0,016 % | 0,034 % | 0,162 % |
| ZEC | 0,001 % | 2 428 504 USDT | 0,001 % | 0,010 % | 0,047 % | 0,250 % |
| LINK | 0,011 % | 483 372 USDT | 0,005 % | 0,020 % | 0,092 % | carnet trop mince |
| UNI | 0,020 % | 313 189 USDT | 0,010 % | 0,026 % | 0,120 % | 3,390 % |
| DOGE | 0,017 % | 987 826 USDT | 0,009 % | 0,016 % | 0,048 % | 0,560 % |
| LTC | 0,024 % | 196 128 USDT | 0,012 % | 0,025 % | 0,105 % | carnet trop mince |
| ADA | 0,066 % | 534 017 USDT | 0,033 % | 0,033 % | 0,094 % | 3,953 % |
| BCH | 0,049 % | 345 301 USDT | 0,024 % | 0,059 % | 0,158 % | carnet trop mince |
| AAVE | 0,010 % | 141 898 USDT | 0,016 % | 0,051 % | 0,213 % | carnet trop mince |
| DASH | 0,055 % | 119 421 USDT | 0,039 % | 0,077 % | 0,378 % | carnet trop mince |
| XLM | 0,075 % | 272 228 USDT | 0,038 % | 0,062 % | 0,194 % | 16,943 % |
| ICP | 0,047 % | 131 312 USDT | 0,043 % | 0,095 % | 0,319 % | carnet trop mince |
| ETC | 0,167 % | 82 565 USDT | 0,084 % | 0,084 % | 0,471 % | carnet trop mince |
| DOT | 0,143 % | 146 093 USDT | 0,072 % | 0,081 % | 0,270 % | carnet trop mince |
| ALGO | 0,116 % | 86 764 USDT | 0,058 % | 0,162 % | 0,623 % | carnet trop mince |
| NEO | 0,125 % | 23 879 USDT | 0,086 % | 0,237 % | carnet trop mince | carnet trop mince |
| XTZ | 0,139 % | 53 354 USDT | 0,075 % | 0,211 % | 4,553 % | carnet trop mince |

Coût moyen d'un achat et d'une vente au marché, par ordre, en plus des frais
Binance. Profondeur à 1 % : montant proposé à la vente à moins de 1 % du
meilleur prix. « carnet trop mince » : les 1 000 niveaux lus ne suffisent pas
pour ce montant.

## La stratégie avec les coûts réels (capital 10 000 USDT)

| Période et coûts | Rendement par an | Baisse max | Calmar | Trades | Coût moyen payé par ordre |
| --- | --- | --- | --- | --- | --- |
| 2018-2022 (conception), glissement fixe du dépôt | +41,2 % | −25,4 % | 1,62 | 138 | 0,10 % (fixe) |
| 2018-2022 (conception), coûts réels | +41,7 % | −25,1 % | 1,66 | 139 | 0,036 % |
| 2018-2022 (conception), coûts réels × 2 | +41,5 % | −25,2 % | 1,64 | 139 | 0,071 % |
| 2018-2022 (conception), coûts réels × 3 | +41,3 % | −25,4 % | 1,63 | 139 | 0,105 % |
| 2023 → 2026-11-22, glissement fixe du dépôt | +32,0 % | −33,6 % | 0,95 | 180 | 0,10 % (fixe) |
| 2023 → 2026-11-22, coûts réels | +33,8 % | −33,6 % | 1,00 | 182 | 0,015 % |
| 2023 → 2026-11-22, coûts réels × 2 | +31,6 % | −33,7 % | 0,94 | 182 | 0,030 % |
| 2023 → 2026-11-22, coûts réels × 3 | +32,7 % | −33,8 % | 0,97 | 180 | 0,045 % |

## Capacité : jusqu'à quel capital ?

Même stratégie de 2018 à 2026-11-22, coûts réels qui grandissent avec la taille
des ordres, et la règle du bot : pas d'achat quand le carnet propose moins de 3
fois le montant de l'achat à moins de 1 % du prix (profondeur mesurée
aujourd'hui).

| Capital de départ | Rendement par an | Baisse max | Calmar | Trades | Coût moyen payé par ordre |
| --- | --- | --- | --- | --- | --- |
| 10 000 USDT | +37,4 % | −33,7 % | 1,11 | 326 | 0,029 % |
| 10 000 USDT (coûts × 2) | +37,7 % | −33,8 % | 1,11 | 324 | 0,059 % |
| 100 000 USDT | +35,6 % | −29,3 % | 1,21 | 284 | 0,050 % |
| 100 000 USDT (coûts × 2) | +35,5 % | −29,5 % | 1,20 | 282 | 0,100 % |
| 1 000 000 USDT | +27,0 % | −19,7 % | 1,37 | 155 | 0,069 % |
| 1 000 000 USDT (coûts × 2) | +26,7 % | −19,1 % | 1,40 | 155 | 0,135 % |
| 5 000 000 USDT | +13,2 % | −14,0 % | 0,94 | 87 | 0,048 % |
| 5 000 000 USDT (coûts × 2) | +13,5 % | −14,0 % | 0,97 | 85 | 0,097 % |

## Lecture

- À 10 000 USDT, un ordre du bot coûte en moyenne bien moins que les 0,1 % de
  glissement comptés par les backtests du dépôt : cette hypothèse est prudente,
  et les résultats tiennent même avec des coûts trois fois plus élevés.
- Un écart d'environ un point entre deux lignes proches (coûts × 2 un peu
  meilleurs que × 1, par exemple) vient de l'enchaînement des trades : un prix
  d'achat différent change la taille des positions, puis les achats suivants.
- Quand le capital grandit, les petites cryptos (NEO, XTZ, ALGO, ETC…) ne
  peuvent plus absorber les ordres : la règle du bot les écarte, il y a moins de
  trades et le rendement baisse, mais les coûts payés restent faibles.
- Les coûts mesurés sont ceux d'un instant : un carnet change d'heure en heure.
  Le bot décide à 00:02 UTC, une heure plutôt liquide.
- Les carnets étaient plus minces en 2018-2020 qu'aujourd'hui : les lignes « × 2
  » et « × 3 » couvrent ce risque.
- La capacité ne compte que le coût des ordres. Au-delà de quelques millions
  d'USDT, la stratégie déplacerait elle-même les prix des petites cryptos, ce
  que ce modèle ne capture pas entièrement.
- Une étude mesure ; elle ne change aucune règle du bot. En paper, le bot paie
  déjà les coûts réels de chaque ordre (TG_PAPER_REEL, trendguard/realisme.py).
