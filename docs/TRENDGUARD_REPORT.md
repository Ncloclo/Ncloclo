# TrendGuard — rapport de recherche

Données : Coin Metrics (clôtures USD journalières), 24 actifs dont des actifs effondrés/retirés (FTT, EOS, NEO, XTZ…) pour limiter le biais du survivant. Frais 0.1 % + slippage 0.1 % par côté. Capital initial 10 000 USD.

Protocole : conception et choix des paramètres sur **2018-01-01 → 2022-12-31** uniquement ; période **2023-01-01 → 2026-05-23** conservée intacte et évaluée une seule fois avec les paramètres gelés.

## Paramètres retenus

```
breakout_n = 30
atr_n = 20
init_stop_atr = 3.0
trail_atr = 5.0
regime_sma = 150
bear_trail_atr = 2.0
mom_n = 90
risk_pct = 0.01
max_positions = 8
max_total_risk = 0.06
max_position_pct = 0.25
min_history = 250
min_volume_usd = 5000000.0
fee = 0.001
slippage = 0.001
```

## 1. Robustesse in-sample (grille de 144 combinaisons, 2018-2022)

- Combinaisons rentables : **100 %** ; Sharpe > 0,5 : **100 %**
- Sharpe : P10 = 0.88, médiane = 1.11, P90 = 1.33
- Les paramètres retenus sont au **centre du plateau** (et non au meilleur point, ce qui serait du sur-ajustement).

## 2. Résultats

| Stratégie | CAGR | Total | Max DD | Sharpe | Calmar | Trades | Gagnants | Gain moy. | Perte moy. | Espérance | PF |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TrendGuard in-sample 2018-2022 | +44.1 % | +519.6 % | -24.6 % | 1.22 | 1.79 | 161 | 49.1 % | +4.23 R | -1.03 R | +1.55 R | 2.50 |
| **TrendGuard hors échantillon** | +40.0 % | +212.6 % | -33.4 % | 1.22 | 1.20 | 176 | 35.8 % | +3.89 R | -1.02 R | +0.74 R | 1.99 |
| Achat-conservation BTC (OOS) | +57.0 % | +361.4 % | -49.1 % | 1.19 | 1.16 | — | — | — | — | — | — |
| Panier équipondéré (OOS) | +29.0 % | +137.3 % | -55.2 % | 0.73 | 0.53 | — | — | — | — | — | — |
| Achat-conservation BTC (IS) | +4.2 % | +22.7 % | -81.4 % | 0.43 | 0.05 | — | — | — | — | — | — |

Rendement par année (IS puis OOS chaînés) : 2018 : -4.5 %, 2019 : +32.9 %, 2020 : +69.4 %, 2021 : +216.3 %, 2022 : -8.9 %, 2023 : +35.8 %, 2024 : +66.9 %, 2025 : +31.6 %, 2026 : +4.8 %

## 3. Tests de résistance (hors échantillon)

| Scénario | CAGR | Total | Max DD | Sharpe | Calmar | Trades | Gagnants | Gain moy. | Perte moy. | Espérance | PF |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Frais et slippage × 2 | +34.1 % | +170.5 % | -33.6 % | 1.10 | 1.02 | 175 | 34.9 % | +3.72 R | -1.00 R | +0.64 R | 1.83 |
| Risque 0,5 % par trade | +27.7 % | +129.0 % | -18.0 % | 1.26 | 1.54 | 206 | 35.4 % | +4.24 R | -1.00 R | +0.86 R | 2.13 |
| Sans les 3 meilleurs actifs (zec, xrp, btc) | +16.1 % | +66.0 % | -34.0 % | 0.66 | 0.47 | 173 | 31.2 % | +3.56 R | -1.04 R | +0.40 R | 1.35 |

Contribution par actif (OOS, USD) : ZEC +8,864, XRP +3,682, BTC +2,399, DOGE +1,978, BNB +1,661, LINK +1,583, BCH +1,501, ADA +989, XLM +727, XMR +386, DASH +241, TRX +230, NEO +219, ALGO +92, EOS -2, UNI -84, ICP -334, DOT -361, ETC -424, FTT -633, ETH -788, AAVE -1,073, LTC -1,934

## 4. Walk-forward (ré-optimisation glissante)

Optimisation du Sharpe sur les 3 années précédentes (grille 27 combinaisons), test sur les 6 mois suivants, 2021 → 2026-05-23 :

- Courbe OOS chaînée : CAGR **+45.8 %**, max DD -28.3 %, Sharpe 1.23
- Fenêtres de 6 mois positives : 64 % (11 fenêtres)

| Test | breakout | stop init. | trailing | Sharpe IS | Rendement OOS | Trades |
|---|---|---|---|---|---|---|
| 2021-01-01 → 2021-07-01 | 30 | 3.0 | 6.0 | 1.26 | +253.9 % | 29 |
| 2021-07-01 → 2022-01-01 | 20 | 2.0 | 6.0 | 2.05 | +1.4 % | 25 |
| 2022-01-01 → 2022-07-01 | 20 | 2.0 | 6.0 | 1.98 | -1.7 % | 6 |
| 2022-07-01 → 2023-01-01 | 20 | 4.0 | 6.0 | 1.83 | -5.7 % | 5 |
| 2023-01-01 → 2023-07-01 | 20 | 4.0 | 6.0 | 1.84 | +10.1 % | 25 |
| 2023-07-01 → 2024-01-01 | 20 | 2.0 | 6.0 | 1.93 | +20.5 % | 22 |
| 2024-01-01 → 2024-07-01 | 50 | 2.0 | 6.0 | 1.47 | -2.2 % | 30 |
| 2024-07-01 → 2025-01-01 | 50 | 4.0 | 6.0 | 0.62 | +33.7 % | 23 |
| 2025-01-01 → 2025-07-01 | 50 | 4.0 | 5.0 | 1.08 | -0.8 % | 17 |
| 2025-07-01 → 2026-01-01 | 50 | 4.0 | 5.0 | 1.07 | +28.9 % | 29 |
| 2026-01-01 → 2026-05-23 | 50 | 3.0 | 5.0 | 1.36 | +3.5 % | 5 |

## 5. Monte Carlo (5 000 séquences de 100 trades)

- Rendement sur 100 trades : P5 +34.1 % / médiane +155.5 % / P95 +533.5 %
- Drawdown max : médiane 10.0 % / P95 17.6 %
- Probabilité de perte après 100 trades : 0.4 %
- Série de pertes consécutives : médiane 7, P95 12 (à accepter psychologiquement)
- ⚠️ Le Monte Carlo suppose des trades indépendants : les positions simultanées étant corrélées (crypto), il SOUS-ESTIME le drawdown. Référence réaliste : le drawdown historique (-25 à -35 %).

## 6. Ce qu'il faut attendre (et ne pas attendre)

- Taux de réussite ≈ 36-49 % : la performance vient du ratio gain/perte ≈ 3.8, pas d'un taux de réussite élevé. Une stratégie annonçant à la fois un taux de réussite très élevé et des gains très élevés est presque toujours sur-ajustée.
- Perte par trade ≈ 1 % du capital (moyenne -1.02 R) ; le pire trade (-2.67 R) vient d'un gap sous le stop : le stop est évalué à la clôture.
- Drawdowns de 25-35 % possibles ; mois sans nouvelle entrée quand BTC est sous sa moyenne 150 j (capital protégé en USDT).
- Performances passées ≠ performances futures. À valider en paper puis testnet avant tout capital réel.
