# Jumeau numérique et simulation

Mesuré le 2026-10-09 par `python trendguard_bot.py jumeau` (étape 28 du prompt
maître, [`JUMEAU.md`](JUMEAU.md)). Données : réelles b81a83b610d1. Une
simulation n'est jamais une observation.

- **PRÊT** (READY) ; note 100/100.
- jumeau du paper : 3 achat(s) sur 3 identiques ; pire baisse 1 fois sur 20 sur
  trois ans : 41,7 % (Monte-Carlo convergé).

## Le jumeau du paper

Du 2026-10-05 au 2026-10-07 : 3 achat(s) identique(s) sur 3 ; écarts expliqués
0, inexpliqués 0.

## Monte-Carlo (blocs de 30 jours, trois ans)

| Tirages | rendement médian | pire baisse 1 fois sur 20 |
| --- | --- | --- |
| 250 | +153,1 % | 41,17 % |
| 500 | +155,8 % | 40,40 % |
| 1000 | +162,3 % | 41,38 % |
| 2000 | +155,3 % | 41,69 % |
| 4000 | +154,8 % | 41,74 % |

Graine 7 ; convergé à 2000 tirages (écart de moins de 1 %).

## Crises et hausses rejouées

| Période | type | rendement | pire baisse | trades |
| --- | --- | --- | --- | --- |
| Marché baissier de 2018 | crise | +0,0 % | 0,0 % | 0 |
| Krach du Covid (mars 2020) | crise | −1,8 % | −2,1 % | 2 |
| Chute de mai 2021 | crise | +1,4 % | −14,5 % | 11 |
| Effondrement de LUNA (mai 2022) | crise | +0,0 % | 0,0 % | 0 |
| Faillite de FTX (novembre 2022) | crise | −6,4 % | −6,5 % | 4 |
| Hausse de 2020-2021 | hausse | +310,8 % | −24,9 % | 59 |
| Hausse de 2023-2024 | hausse | +72,3 % | −12,8 % | 38 |

## Sensibilité aux réglages (±10 %)

| Réglage | valeur | rendement annuel | pire baisse |
| --- | --- | --- | --- |
| stop initial (en ATR) | 2,7 (−10 %) | +0,70 pts | −2,67 pts |
| stop initial (en ATR) | 3,3 (+10 %) | −3,56 pts | +1,61 pts |
| stop suiveur (en ATR) | 4,5 (−10 %) | −3,67 pts | −1,56 pts |
| stop suiveur (en ATR) | 5,5 (+10 %) | +4,81 pts | +3,08 pts |
| cassure (jours) | 27 (−10 %) | +2,47 pts | +0,89 pts |
| cassure (jours) | 33 (+10 %) | +1,43 pts | −0,93 pts |
| filtre BTC (moyenne, jours) | 135 (−10 %) | +1,28 pts | −1,31 pts |
| filtre BTC (moyenne, jours) | 165 (+10 %) | −3,37 pts | +2,00 pts |

Écart à la règle actuelle, un réglage changé à la fois ; pour la pire baisse, un
chiffre négatif veut dire une baisse plus profonde. Un réglage n'est jamais
changé d'après ce tableau seul : l'évolution encadrée l'éprouve d'abord sur deux
époques.

## Panne injectée

Trois jours de cours effacés : le jumeau tourne, écart de +0,00 point sur le
rendement annuel.

## Qualité

| Famille | poids | état | mesure |
| --- | --- | --- | --- |
| fidélité du modèle | 20 % | conforme | achats du paper retrouvés par le jumeau ; écarts expliqués |
| synchronisation | 15 % | conforme | 100 % des achats identiques |
| validation et calibrage | 15 % | conforme | Monte-Carlo convergé à 2000 tirages (écart < 1 %) |
| justesse de la simulation | 15 % | conforme | même jumeau, mêmes données : même résultat ; une panne de données ne le fait pas tomber |
| scénarios et crises | 10 % | conforme | 7 crise(s) ou hausse(s) rejouée(s) |
| reproductibilité | 10 % | conforme | graine 7, données réelles b81a83b610d1 |
| sécurité et isolation | 5 % | conforme | aucune bibliothèque réseau, aucune écriture dans le bot |
| performance | 5 % | conforme | en 10,9 s |
| observabilité | 5 % | conforme | chaque résultat dit ses données (réelles ou synthétiques) et sa graine |
