# TrendGuard + V29.6 — Bots de trading Binance Spot

> ⚠️ Logiciel de trading automatisé : risque de perte en capital. Les
> performances passées ne garantissent pas les performances futures. Validez
> toujours en **paper**, puis sur le **testnet Binance**, avant tout capital réel.

Le dépôt contient deux stratégies partageant le même moteur d'exécution sécurisé :

| | **TrendGuard** (recommandée) | V29.6 intraday |
|---|---|---|
| Fichiers | `trend_strategy.py`, `trendguard_bot.py` | `v29.py` |
| Style | Suivi de tendance, portefeuille multi-actifs, journalier | Signaux multi-modules, une paire, 1 h |
| Validation | Données réelles 2018→2026, hors échantillon, walk-forward | Aucun avantage démontré |
| Risque | 1 % du capital par trade | 1 % par trade |

## TrendGuard

### Règles

1. **Régime** : entrées autorisées seulement si BTC clôture au-dessus de sa
   moyenne 150 jours ; en régime baissier, les stops ouverts sont resserrés.
2. **Entrée** : clôture au-dessus du plus haut des 30 clôtures précédentes,
   momentum 90 j positif, liquidité suffisante ; candidats classés par momentum.
3. **Stop** : initial à 3 × volatilité, puis trailing « chandelier » à 5 ×
   volatilité sous le plus haut (2 × en régime baissier), évalué à la clôture.
4. **Taille** : **1 % du capital risqué par trade** ; max 8 positions, 6 % de
   risque cumulé, 25 % du capital par position.

### Résultats (détails dans [`docs/TRENDGUARD_REPORT.md`](docs/TRENDGUARD_REPORT.md))

Données Coin Metrics, 24 actifs dont plusieurs effondrés (FTT, EOS, NEO…).
Frais 0,1 % + slippage 0,1 % par côté. Paramètres choisis sur 2018-2022,
puis **gelés** et testés une seule fois sur 2023→mai 2026 :

| Période | CAGR | Max DD | Sharpe | Trades | Gagnants | Gain moy. | Perte moy. |
|---|---|---|---|---|---|---|---|
| 2018-2022 (conception) | +44,1 % | −24,6 % | 1,22 | 161 | 49 % | +4,2 R | −1,03 R |
| **2023-2026 (hors échantillon)** | **+40,0 %** | **−33,4 %** | **1,22** | 176 | 36 % | +3,9 R | −1,02 R |
| BTC achat-conservation 2023-2026 | +57,0 % | −49,1 % | 1,19 | — | — | — | — |

- Les 144 variantes de paramètres testées en conception sont toutes rentables : le résultat ne tient pas à un réglage chanceux.
- En walk-forward (ré-optimisation tous les 6 mois), la stratégie fait +45,8 % par an avec un drawdown max de −28 %.
- À ne pas attendre : un taux de réussite élevé. La stratégie gagne 36 à 49 % de ses trades, mais un gain moyen vaut environ 3,8 fois une perte moyenne.
- Il faut accepter des séries de 7 à 12 pertes consécutives et des drawdowns de 25 à 35 %.
- Pour plus de stabilité, `TG_RISK_PCT=0.005` donne −18 % de drawdown pour +28 % par an.

### Utilisation

```bash
pip install -r requirements.txt
python trend_strategy.py download --data data        # historique Coin Metrics
python trend_strategy.py research --data data         # régénère le rapport
python trendguard_bot.py docs                         # variables d'environnement
RUN_MODE=paper python trendguard_bot.py run           # paper, prix réels Binance
python trendguard_bot.py status                       # état du portefeuille
```

Live, testnet d'abord :

```bash
BINANCE_TESTNET=true RUN_MODE=live ENABLE_LIVE_TRADING=true \
LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_RISK \
BINANCE_API_KEY=… BINANCE_API_SECRET=… python trendguard_bot.py run
```

Le bot prend ses décisions une fois par jour, juste après la clôture de 00:00
UTC. Il appelle **exactement les mêmes fonctions** que le backtest. Un test
vérifie la parité exacte : mêmes trades, même PnL, même equity.

Chaque position est protégée à deux niveaux :

- le **stop de clôture** de la stratégie, qui fixe le risque de 1 % ;
- un **stop catastrophe** `STOP_LOSS` posé sur Binance, 1 × volatilité plus
  bas, remonté avec le trailing. Il protège d'un krach entre deux clôtures.

Au-delà de 40 % de drawdown, un kill-switch bloque les entrées
(`TG_KILL_DRAWDOWN`, levée via `python trendguard_bot.py resume`).

## Moteur d'exécution (commun, `v29.py`)

- Une position live n'est jamais laissée sans protection exchange : la
  protection est revérifiée à chaque cycle, avec un stop logiciel en dernier recours.
- Aucune vente marché n'est envoyée tant que l'annulation de la protection
  n'est pas confirmée. Chaque jambe est relue après annulation.
- Chaque exécution est comptée une seule fois (registre par `order_id`).
- Chaque ordre est précédé d'une intention persistée, résolue par client-id
  après un crash ou un timeout.
- Les quantités sont plafonnées au solde réel, et les frais prélevés en base sont pris en compte.
- La reconciliation au boot est *fail-closed*.

Le bot V29.6 intraday reste disponible : `python v29.py bot | backtest |
walkforward | status | resume` (voir `python v29.py docs`).

## Tests

```bash
python -m pytest tests -q      # 101 tests, simulateurs Binance Spot mono et multi-paires
```

## Limites connues

- Les simulateurs reproduisent fidèlement les mécanismes de risque :
  soldes bloqués, frais en base, OCO, erreurs réseau. Ils ne remplacent pas le
  **testnet**, car l'API Binance n'était pas accessible depuis l'environnement
  de développement.
- La recherche utilise les clôtures USD de Coin Metrics, alors que le bot live
  utilise les clôtures USDT de Binance. Les deux séries sont très proches, sans
  être identiques.
- Les résultats dépendent pour une part notable de quelques actifs (ZEC, XRP,
  BTC sur 2023-2026). Sans ces trois-là, la stratégie reste positive mais tombe
  à +16 % par an. Gardez donc un univers large.
