# Le comité d'agents aurait-il aidé ? (données Binance jusqu'au 2026-09-27)

Étude reproductible : `python -m research.comite --cache data_binance`. Boucle du bot, profil prudent ; avis du comité calculé avec les seules données connues le jour de chaque achat ; agent sentiment neutre (pas d'historique).

## 1. Les trades de la stratégie, selon l'avis qu'aurait donné le comité

| Avis du comité | Trades | Gagnants | R moyen | R total |
| --- | --- | --- | --- | --- |
| achat | 114 | 45 % | +1,20 | +137,1 |
| attendre | 199 | 39 % | +1,08 | +214,0 |

## 2. La stratégie seule, puis n'achetant que sur un avis « achat » du comité

| Variante | 2018-22 rendement | baisse | Calmar | depuis 2023 rendement | baisse | Calmar | trades |
| --- | --- | --- | --- | --- | --- | --- | --- |
| stratégie seule | +31,8 % | −20,6 % | 1,54 | +32,1 % | −24,2 % | 1,33 | 314 |
| achats filtrés par le comité | +19,2 % | −23,2 % | 0,83 | +22,4 % | −18,7 % | 1,20 | 211 |

**Conclusion** : le comité ne fait pas nettement mieux que la stratégie seule sur les deux époques : il reste consultatif, et ses avis sont mesurés sur les trades réels (journal financier).
