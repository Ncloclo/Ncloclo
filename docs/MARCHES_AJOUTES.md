# Les marchés de la synthèse TradingView : enrichissent-ils le bot ?

Cours journaliers de Yahoo Finance jusqu'au 2026-11-24, règle de TrendGuard transposée (régime propre à chaque marché), paramètres gelés, coût réel de chaque marché. Régénérer : `python -m research.marches_ajoutes --cache data_multi`.

Marchés ajoutés le 24/11/2026 (26) : Platine (once, $), Palladium (once, $), Aluminium (tonne, $), Essence RBOB (gallon, $), Fioul (gallon, $), Blé, Maïs, Soja, Café, Sucre, Coton, NZD/USD, Indice du dollar (DXY), Russell 2000, Nasdaq Composite, FTSE MIB, IBEX 35, Shanghai Composite, Hang Seng, Nifty 50, Bovespa, Obligations d'État US à 1-3 ans (ETF SHY), TSMC (certificat américain), AMD, Anthropic, SpaceX. Ils sont suivis chaque jour dans les pages du panneau. Indicateurs lus pour la veille (pas des marchés à trader) : Volatilité du S&P 500 (VIX), Taux US à 3 mois, Taux US à 5 ans, Taux US à 10 ans, Taux US à 30 ans.

## Le portefeuille fictif hors cryptos, avec ou sans les marchés ajoutés

| Univers | 2018-2022 : CAGR | baisse | Calmar | trades | 2023 → 2026-11-24 : CAGR | baisse | Calmar | trades | 24/05/2026 → 2026-11-24 : CAGR | baisse | Calmar | trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Les 21 marchés du portefeuille fictif | +0,8 % | −15,6 % | 0,05 | 279 | +1,2 % | −19,8 % | 0,06 | 238 | −25,6 % | −16,5 % | −1,55 | 40 |
| Les 21 + 22 ajoutés (sans les actions) | −3,1 % | −41,3 % | −0,07 | 401 | +4,6 % | −19,6 % | 0,24 | 334 | −25,6 % | −14,8 % | −1,73 | 53 |

Espérance par trade : 2018-2022 : +0,18 R puis −0,14 R ; 2023 → 2026-11-24 : +0,21 R puis +0,16 R ; 24/05/2026 → 2026-11-24 : −0,71 R puis −0,58 R (les 21, puis l'univers élargi).

## Chaque marché ajouté dans l'univers élargi

| Marché | Catégorie | 2018-2022 : trades, total en R | 2023 → 2026-11-24 : trades, total en R | 24/05/2026 → 2026-11-24 : trades, total en R |
|---|---|---|---|---|
| Platine (once, $) | Matières premières | 6 trades, −2,8 R | 9 trades, −2,0 R | 1 trade, −1,2 R |
| Palladium (once, $) | Matières premières | 10 trades, +8,7 R | 5 trades, −1,3 R | 0 trade, +0,0 R |
| Aluminium (tonne, $) | Matières premières | 10 trades, −167,3 R | 12 trades, −9,3 R | 2 trades, −2,2 R |
| Essence RBOB (gallon, $) | Matières premières | 9 trades, +15,8 R | 7 trades, −2,9 R | 1 trade, −1,4 R |
| Fioul (gallon, $) | Matières premières | 12 trades, +9,8 R | 6 trades, +3,8 R | 1 trade, +1,1 R |
| Blé | Matières premières | 13 trades, +0,0 R | 3 trades, −0,2 R | 1 trade, +0,2 R |
| Maïs | Matières premières | 10 trades, +24,1 R | 5 trades, −1,0 R | 1 trade, +1,1 R |
| Soja | Matières premières | 10 trades, +12,7 R | 7 trades, −5,9 R | 2 trades, −1,5 R |
| Café | Matières premières | 11 trades, −8,4 R | 7 trades, +4,9 R | 2 trades, −2,3 R |
| Sucre | Matières premières | 6 trades, +3,7 R | 8 trades, −4,6 R | 2 trades, +0,2 R |
| Coton | Matières premières | 10 trades, −1,7 R | 6 trades, +7,6 R | 1 trade, +1,6 R |
| NZD/USD | Devises | 6 trades, +0,0 R | 1 trade, −0,3 R | 1 trade, −0,3 R |
| Indice du dollar (DXY) | Devises | 15 trades, −6,0 R | 5 trades, +3,7 R | 1 trade, +1,4 R |
| Russell 2000 | Indices | 8 trades, −1,1 R | 4 trades, −3,5 R | 1 trade, −0,1 R |
| Nasdaq Composite | Indices | 14 trades, +8,2 R | 10 trades, +5,6 R | 0 trade, +0,0 R |
| FTSE MIB | Indices | 13 trades, −4,0 R | 12 trades, +7,0 R | 1 trade, +0,6 R |
| IBEX 35 | Indices | 6 trades, −5,2 R | 16 trades, +0,1 R | 2 trades, −2,4 R |
| Shanghai Composite | Indices | 6 trades, +3,3 R | 6 trades, +6,6 R | 0 trade, +0,0 R |
| Hang Seng | Indices | 5 trades, +2,5 R | 5 trades, +5,9 R | 0 trade, +0,0 R |
| Nifty 50 | Indices | 14 trades, +12,7 R | 10 trades, +2,9 R | 0 trade, +0,0 R |
| Bovespa | Indices | 10 trades, −1,4 R | 10 trades, +12,1 R | 2 trades, −4,2 R |
| Obligations d'État US à 1-3 ans (ETF SHY) | Obligations | 6 trades, +5,8 R | 4 trades, +2,9 R | 0 trade, +0,0 R |

## Verdict (règle fixée avant les mesures)

- L'univers élargi bat les 21 au Calmar sur 2018-2022 et depuis 2023 : **non**.
- **Décision** : les marchés ajoutés restent suivis pour information (pages du panneau) ; le portefeuille fictif garde ses 21 marchés.

## Vérification des cours du bot contre TradingView

Cours de la synthèse TradingView relevés le 24/11/2026 à 18:09 UTC (séances américaines encore ouvertes, cours parfois différés) contre la dernière séance que le bot a lue chez Yahoo Finance.

| Marché | TradingView | Bot (Yahoo) | Séance du bot | Écart |
|---|---|---|---|---|
| S&P 500 | 7 207,14 | 7 200,18 | 2026-11-24 | −0,10 % |
| Nasdaq 100 | 26 237,7 | 26 220 | 2026-11-24 | −0,07 % |
| Dow Jones | 49 999,8 | 49 976,3 | 2026-11-24 | −0,05 % |
| Russell 2000 | 2 692,64 | 2 687,5 | 2026-11-24 | −0,19 % |
| Nasdaq Composite | 23 724,6 | 23 733,5 | 2026-11-24 | +0,04 % |
| CAC 40 | 8 040,49 | 8 040,49 | 2026-11-24 | +0,00 % |
| DAX | 25 017,8 | 25 017,8 | 2026-11-24 | +0,00 % |
| FTSE 100 | 10 467,1 | 10 467,1 | 2026-11-24 | +0,00 % |
| FTSE MIB | 48 121,2 | 48 121,2 | 2026-11-24 | +0,00 % |
| IBEX 35 | 19 015,8 | 19 015,8 | 2026-11-24 | +0,00 % |
| Nikkei 225 | 58 644,6 | 56 436,6 | 2026-11-20 | −3,76 % |
| Shanghai Composite | 3 548,37 | 3 538,95 | 2026-11-23 | −0,27 % |
| Hang Seng | 22 219,7 | 22 383,7 | 2026-11-23 | +0,74 % |
| Nifty 50 | 25 349,7 | 25 349,7 | 2026-11-23 | +0,00 % |
| Bovespa | 165 281 | 164 089 | 2026-11-24 | −0,72 % |
| Or | 3 565 | 3 563,4 | 2026-11-24 | −0,04 % |
| Argent | 40,84 | 40,655 | 2026-11-24 | −0,45 % |
| Platine | 1 256,8 | 1 256,7 | 2026-11-24 | −0,01 % |
| Cuivre | 5,372 | 5,4245 | 2026-11-24 | +0,98 % |
| Pétrole WTI | 81,31 | 80,48 | 2026-11-24 | −1,02 % |
| Gaz naturel | 3,355 | 3,737 | 2026-11-24 | +11,39 % |
| EUR/USD | 1,09729 | 1,09709 | 2026-11-24 | −0,02 % |
| GBP/USD | 1,3122 | 1,31144 | 2026-11-24 | −0,06 % |
| AUD/USD | 0,6646 | 0,664011 | 2026-11-24 | −0,09 % |
| NZD/USD | 0,54903 | 0,548546 | 2026-11-24 | −0,09 % |
| USD/JPY | 159,286 | 159,212 | 2026-11-24 | −0,05 % |
| USD/CHF | 0,835422 | 0,8353 | 2026-11-24 | −0,01 % |
| Indice du dollar (DXY) | 103,767 | 103,825 | 2026-11-24 | +0,06 % |
| VIX | 26,8 | 26,15 | 2026-11-24 | −2,43 % |
| Taux US à 5 ans (%) | 4,719 | 4,712 | 2026-11-24 | −0,15 % |
| Taux US à 10 ans (%) | 4,904 | 4,904 | 2026-11-24 | +0,00 % |
| Taux US à 30 ans (%) | 5,292 | 5,292 | 2026-11-24 | −0,00 % |
| Nvidia | 152,84 | 153,385 | 2026-11-24 | +0,36 % |
| Apple | 353,35 | 352,22 | 2026-11-24 | −0,32 % |
| Microsoft | 498,5 | 499,925 | 2026-11-24 | +0,29 % |
| Amazon | 252,61 | 253,37 | 2026-11-24 | +0,30 % |
| Alphabet | 279,2 | 278,885 | 2026-11-24 | −0,11 % |
| Tesla | 384,26 | 381,3 | 2026-11-24 | −0,77 % |
| LVMH (€) | 394,7 | 394,7 | 2026-11-24 | +0,00 % |

31 cours sur 39 concordent à 0,5 % près. Les écarts plus grands viennent de l'heure du relevé (séance encore ouverte, ou déjà close d'un jour à l'autre en Asie), pas d'une erreur de source.

## Limites

- Un backtest n'est pas une garantie de gain.
- Les marchés ajoutés sont ceux qu'une page de TradingView met en avant aujourd'hui : de grands marchés, choisis pour leur taille et non pour leurs résultats passés.
- Le calendrier commun mêle des places fermées à des jours différents ; un jour férié garde le dernier cours (5 séances au plus).
- Anthropic (introduite le 19/11/2026) et SpaceX (cotée depuis juin 2026) n'ont pas l'historique de 250 séances que la règle exige : suivies pour information seulement.
