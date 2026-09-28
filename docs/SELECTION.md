# TrendGuard — quelles cryptos trader, et quand vendre ?

Étude reproductible : `python -m research.selection --cache data_binance`
(données journalières Binance des 21 paires du bot, frais 0,1 % et glissement
0,1 % par côté, 1 % du capital risqué par achat).

Protocole : chaque variante est jugée sur **2018-2022**, puis vérifiée sur
**2023 → 2026-09-27**, période qui n'a servi à aucun choix. Critère principal :
Calmar (rendement annuel divisé par la pire baisse). La sélection d'un jour
n'utilise que les données connues ce jour-là.

| Variante | 2018-22 : CAGR | Baisse max | Calmar | Trades | Gagnants | Moyenne | 2023 → : CAGR | Baisse max | Calmar | Trades | Gagnants | Moyenne |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Référence : les 21 cryptos, le gain court jusqu'au stop | +41,2 % | −25,4 % | 1,62 | 138 | 44 % | +1,50 R | +37,2 % | −33,6 % | 1,11 | 173 | 35 % | +0,70 R |
| Auto-sélection : 10 plus rentables sur 2 ans | +35,2 % | −19,5 % | 1,81 | 117 | 49 % | +1,51 R | +15,5 % | −28,5 % | 0,54 | 137 | 34 % | +0,33 R |
| Auto-sélection : 10 plus rentables sur 1 an | +32,3 % | −21,0 % | 1,54 | 122 | 45 % | +1,33 R | +28,1 % | −28,1 % | 1,00 | 135 | 36 % | +0,64 R |
| Auto-sélection : 14 plus rentables sur 2 ans | +42,8 % | −30,0 % | 1,43 | 133 | 44 % | +1,69 R | +33,1 % | −32,3 % | 1,02 | 154 | 36 % | +0,72 R |
| Prise de bénéfice : tout vendre à +3 R | +30,7 % | −23,1 % | 1,33 | 205 | 42 % | +0,73 R | +27,4 % | −26,2 % | 1,05 | 232 | 40 % | +0,45 R |
| Prise de bénéfice : tout vendre à +5 R | +32,9 % | −19,4 % | 1,70 | 170 | 42 % | +0,93 R | +29,5 % | −28,4 % | 1,04 | 206 | 35 % | +0,53 R |
| Prise de bénéfice : moitié vendue à +3 R | +30,7 % | −20,7 % | 1,48 | 136 | 44 % | +1,12 R | +28,5 % | −28,6 % | 1,00 | 170 | 36 % | +0,54 R |

## Lecture

- **Le bot vend déjà.** Chaque achat est revendu quand la clôture passe sous son
  stop suiveur, qui monte avec le prix : le gain est verrouillé au fur et à
  mesure et la vente a lieu quand la tendance s'essouffle. La référence
  ci-dessus compte ces achats ET ces ventes.
- **Auto-sélection des 10 plus rentables sur 2 ans** : meilleure sur la période
  de choix (Calmar 1,81 contre 1,62), nettement moins bonne ensuite : +15,5 %
  par an contre +37,2 % pour les 21 cryptos (Calmar 0,54 contre 1,11). Les
  cryptos qui ont le plus rapporté ces deux dernières années ne sont pas celles
  qui rapportent le plus ensuite : la prochaine grande tendance vient souvent
  d'une crypto délaissée.
- **Prise de bénéfice fixe** : vendre à un gain donné coupe les grandes
  tendances qui font le résultat. Moyenne par trade +0,45 R (tout vendre à +3
  R), +0,53 R (tout vendre à +5 R), +0,54 R (moitié vendue à +3 R) sur 2023 →,
  contre +0,70 R en laissant courir le gain.
- Variantes meilleures que la référence sur les DEUX périodes : **aucune**.

## Décision

- **Par défaut, le bot trade les 21 cryptos** et laisse courir ses gains
  jusqu'au stop suiveur (référence).
- Le panneau (page Cryptos) propose ce **réglage recommandé** (les 21 cryptos,
  toutes cochées) ou une **sélection manuelle** (aucune crypto cochée au
  départ). L'auto-sélection des 10 plus rentables, moins bonne sur 2023 →
  aujourd'hui, a été retirée du panneau. Une crypto décochée déjà détenue reste
  gérée jusqu'à sa vente normale.
- Aucune prise de bénéfice fixe n'est ajoutée : aucune ne bat la référence sur
  les deux périodes.
