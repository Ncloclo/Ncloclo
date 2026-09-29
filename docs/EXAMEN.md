# TrendGuard — examen : le bot est-il intelligent et rusé ?

Étude reproductible : `python -m research.exam --cache data_binance`. C'est un
diagnostic : **aucune règle n'est changée d'après cet examen.**

## 1. Intelligence : chaque règle compte-t-elle ?

Chaque règle est retirée à tour de rôle, sur l'historique Binance des 21 cryptos
du bot : 2018-2022, puis 2023 → 2026-09-27 (frais et glissement 0,1 % par côté,
1 % du capital risqué par achat). Une règle est utile si le Calmar (rendement
annuel divisé par la pire baisse) est meilleur avec elle.

| Variante | 2018-22 : CAGR | Baisse max | Sharpe | Calmar | 2023 → : CAGR | Baisse max | Sharpe | Calmar |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Bot complet** | +41,2 % | −25,4 % | 1,25 | 1,62 | +37,2 % | −33,6 % | 1,18 | 1,11 |
| Sans lecture du marché | +41,8 % | −36,5 % | 1,21 | 1,15 | +46,7 % | −31,3 % | 1,32 | 1,49 |
| Sans stops resserrés en marché baissier | +40,8 % | −30,6 % | 1,23 | 1,33 | +42,9 % | −34,5 % | 1,26 | 1,24 |
| Sans stop suiveur (vente au seul stop initial) | +22,1 % | −78,1 % | 0,64 | 0,28 | +30,4 % | −52,1 % | 0,79 | 0,58 |
| Sans filtres de liquidité et d'ancienneté | +43,9 % | −22,7 % | 1,30 | 1,93 | +44,6 % | −34,4 % | 1,30 | 1,30 |
| Sans plafonds (8 positions, 6 % de risque cumulé) | +46,0 % | −36,8 % | 1,18 | 1,25 | +37,7 % | −35,5 % | 1,16 | 1,06 |
| Élan ignoré : cryptos choisies au hasard (médiane de 10 tirages) | +33,8 % | −25,9 % | 1,10 | 1,29 | +37,7 % | −34,1 % | 1,20 | 1,11 |
| Achats au hasard, mêmes stops (médiane de 10 tirages) | +27,5 % | −40,9 % | 0,85 | 0,70 | +25,2 % | −41,5 % | 0,78 | 0,62 |
| Achat conservé : BTC seul | +4,3 % | −81,2 % | 0,44 | 0,05 | +54,5 % | −53,0 % | 1,17 | 1,03 |
| Achat conservé : les 21 cryptos à parts égales | +28,1 % | −86,2 % | 0,76 | 0,33 | +45,7 % | −54,8 % | 0,92 | 0,83 |

| Règle | 2018-22 | 2023 → | Verdict |
| --- | --- | --- | --- |
| N'acheter que si BTC est au-dessus de sa moyenne 150 jours | ✓ | ✗ | ± utile en 2018-2022, pas depuis 2023 |
| Resserrer les stops quand le marché baisse | ✓ | ✗ | ± utile en 2018-2022, pas depuis 2023 |
| Le stop suiveur qui monte avec le prix | ✓ | ✓ | ✓ utile sur les deux périodes |
| Éviter les cryptos peu échangées ou trop récentes | ✗ | ✗ | ✗ n'améliore pas ce rapport ici |
| Limiter le nombre de positions et le risque total | ✓ | ✓ | ✓ utile sur les deux périodes |
| Acheter en priorité les plus fortes tendances | ✓ | ✗ | ± utile en 2018-2022, pas depuis 2023 |

- **2 règles sur 6** améliorent le rapport rendement / pire baisse sur les deux
  périodes ; 3 sont utiles surtout en 2018-2022, période des chutes de 2018 et
  2022, et coûtent un peu ou rien depuis 2023, marché surtout haussier.
- **Achats au hasard** (même fréquence que les cassures, 10,3 % des jours, mêmes
  stops) : le bot fait mieux sur les deux périodes que **9 tirages sur 10**. Le
  signal d'achat apporte donc un vrai avantage, pas seulement les stops.
- Limites du test : il suppose le même glissement (0,1 %) pour toutes les
  cryptos, et les cryptos testées sont celles qui existent encore aujourd'hui
  sur Binance ; une crypto récente qui s'est effondrée ou a été retirée n'y
  figure pas. Le filtre de liquidité et d'ancienneté, qui protège justement de
  ces deux risques, ne peut donc pas montrer son intérêt ici.
- Une règle qui n'aide pas ici n'est pas retirée pour autant : la choisir
  d'après ces seuls chiffres serait du sur-ajustement.

## 2. Ruse : pièges joués contre le vrai code du bot

Chaque piège est joué par des tests automatiques sur un Binance simulé, avec le
code exact du bot.

| Piège | Réaction du bot | Résultat |
| --- | --- | --- |
| Carnet d'ordres anormal au moment d'acheter (écart trop grand, carnet trop mince) | achat différé, nouvel essai toutes les 5 min, abandon après 6 h | ✓ déjoué (2/2 tests) |
| Prix qui a filé depuis la clôture, ou retombé près du stop | quantité recalculée : jamais plus de risque que prévu | ✓ déjoué (2/2 tests) |
| Panne de cours ou d'Internet pendant la décision | décision reportée ; jamais de vente sur une simple panne | ✓ déjoué (3/3 tests) |
| Krach entre deux clôtures | stop catastrophe : vente sans attendre la clôture | ✓ déjoué (2/2 tests) |
| PC éteint pendant une ou plusieurs clôtures | stops et décisions manqués rattrapés au redémarrage ; arrêt signalé | ✓ déjoué (3/3 tests) |
| Marché qui se retourne à la baisse | plus aucun achat, stops resserrés | ✓ déjoué (2/2 tests) |
| Binance annonce le retrait d'une crypto | achats bloqués ; les ventes restent décidées par les stops | ✓ déjoué (2/2 tests) |
| Rumeur relayée par une seule IA | ignorée : il faut deux IA d'accord, et une IA ne bloque jamais seule | ✓ déjoué (1/1 tests) |
| Tentation de regarder l'avenir (bougie du jour pas encore close) | décision sur les seules bougies closes | ✓ déjoué (3/3 tests) |
| Horloge du PC fausse | le bot se cale sur l'heure de Binance | ✓ déjoué (2/2 tests) |
| Réponse de Binance perdue pendant un ordre | ordre retrouvé : jamais d'achat ni de vente en double | ✓ déjoué (3/3 tests) |
| Stop annulé hors du bot, ou lecture impossible du compte | stop reposé ; aucune protection retirée sur une panne de lecture | ✓ déjoué (2/2 tests) |
| Second bot sur le même compte | démarrage refusé, positions de l'autre jamais adoptées | ✓ déjoué (2/2 tests) |
| Chute du capital de 40 % | arrêt d'urgence : plus aucun achat | ✓ déjoué (1/1 tests) |

- **14 pièges déjoués sur 14.**

## 3. Carnets d'ordres réels : la ruse laisserait-elle acheter ?

Relevé du 29/09/2026 à 17:14 UTC, pour un achat de 2 500 $ (le plus gros
possible avec 10 000 $ : 25 % du capital). Le bot achète seulement si l'écart
achat/vente reste sous 0,5 % et si le carnet propose au moins 3 fois le montant
à moins de 1 % du meilleur prix.

| Crypto | Écart achat/vente | Proposé à moins de 1 % | Achat le plus gros accepté | Achat maintenant |
| --- | --- | --- | --- | --- |
| BTC | 0,000 % | 270 570 $ | 90 190 $ | oui |
| ETH | 0,000 % | 695 537 $ | 231 846 $ | oui |
| BNB | 0,001 % | 428 710 $ | 142 903 $ | oui |
| XRP | 0,007 % | 1 534 561 $ | 511 520 $ | oui |
| ADA | 0,041 % | 438 834 $ | 146 278 $ | oui |
| DOGE | 0,011 % | 1 324 986 $ | 441 662 $ | oui |
| TRX | 0,030 % | 1 377 312 $ | 459 104 $ | oui |
| LINK | 0,007 % | 331 761 $ | 110 587 $ | oui |
| LTC | 0,015 % | 475 637 $ | 158 546 $ | oui |
| BCH | 0,033 % | 457 766 $ | 152 589 $ | oui |
| XLM | 0,045 % | 392 986 $ | 130 995 $ | oui |
| ETC | 0,111 % | 128 846 $ | 42 949 $ | oui |
| ZEC | 0,001 % | 440 124 $ | 146 708 $ | oui |
| DASH | 0,033 % | 352 010 $ | 117 337 $ | oui |
| NEO | 0,080 % | 19 910 $ | 6 637 $ | oui |
| XTZ | 0,096 % | 82 499 $ | 27 500 $ | oui |
| ALGO | 0,080 % | 130 108 $ | 43 369 $ | oui |
| DOT | 0,085 % | 201 086 $ | 67 029 $ | oui |
| UNI | 0,011 % | 513 900 $ | 171 300 $ | oui |
| AAVE | 0,006 % | 120 296 $ | 40 099 $ | oui |
| ICP | 0,030 % | 106 269 $ | 35 423 $ | oui |

- 21 carnets sur 21 laisseraient acheter maintenant.
- Le plus petit « achat le plus gros accepté » est de 6 637 $ (NEO) : au-delà,
  sur cette crypto, le bot différerait l'achat pour ne pas payer trop cher.

## Conclusion

- **Intelligent** : le bot bat 9 achats au hasard sur 10. Indispensables sur les
  deux périodes : le stop suiveur qui monte avec le prix, limiter le nombre de
  positions et le risque total. Utiles surtout en 2018-2022 (chutes de 2018 et
  2022) : n'acheter que si BTC est au-dessus de sa moyenne 150 jours, resserrer
  les stops quand le marché baisse, acheter en priorité les plus fortes
  tendances.
- **Rusé** : 14 pièges déjoués sur 14.
- **Ce qu'il ne sait pas faire** : prévoir un krach ou la prochaine tendance ;
  éviter les séries de trades perdants, normales avec 35 à 50 % de trades
  gagnants (`docs/ROBUSTESSE.md`) ; surveiller le marché quand le PC est éteint.
