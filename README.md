# V29.6-QUANT — Bot hybride Binance Spot + wallet EVM

Bot de trading long-only sur Binance **Spot** (paires `BASE/QUOTE`), avec un
module optionnel de wallet EVM (suivi de solde, sweep). Le fichier unique
`v29.py` contient le bot, le backtest, le walk-forward et la CLI. Les tests
sont dans `tests/`.

> ⚠️ Logiciel de trading automatisé : risque de perte en capital. Validez
> toujours sur le **testnet Binance** puis en paper avant tout capital réel.

## Installation

```bash
pip install -r requirements.txt
python v29.py test          # 88 tests (simulateur Binance Spot inclus)
python v29.py docs          # variables d'environnement
```

## Commandes

| Commande | Rôle |
|---|---|
| `python v29.py bot` | Lance le bot (`RUN_MODE=paper` par défaut) |
| `python v29.py backtest --start 2023-01-01 --end 2025-01-01` | Backtest avec biais HTF/BTC sans look-ahead + Monte Carlo |
| `python v29.py walkforward` | Optimisation in-sample → validation out-of-sample |
| `python v29.py sensitivity` | Sensibilité des paramètres |
| `python v29.py status` | État persistant (position, halt, intentions d'ordre) |
| `python v29.py resume` | Lève halt / pause / orphelin **après audit manuel** (bot arrêté) |
| `python v29.py wallet` | Diagnostic du wallet EVM |

Les commandes de recherche acceptent `--csv fichier.csv` (colonnes
`ts,open,high,low,close,volume`) pour travailler hors ligne.

## Mise en production (ordre recommandé)

1. **Paper** : `RUN_MODE=paper python v29.py bot`.
2. **Testnet** : `BINANCE_TESTNET=true RUN_MODE=live ENABLE_LIVE_TRADING=true
   LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_RISK BINANCE_API_KEY=… BINANCE_API_SECRET=… python v29.py bot`.
   Le boot valide les types d'ordres via `POST /api/v3/order/test` (aucun ordre réel).
3. **Live** avec une clé API **sans droit de retrait**, capital réduit.
   Si vous détenez la devise de base hors du bot, déclarez-la dans
   `EXTERNAL_BASE_RESERVE` : le bot ne la vendra jamais.

## Invariants de sécurité (live)

- Une position ouverte est toujours protégée par un **OCO natif** (TP
  `LIMIT_MAKER` + `STOP_LOSS`), à défaut par un stop seul. Sinon, elle est liquidée.
  La protection est re-vérifiée à chaque cycle. Un stop logiciel sert de dernier recours.
- Aucune vente marché n'est envoyée tant que la protection n'est pas confirmée
  annulée. Chaque jambe est relue **après** l'annulation, pour ne jamais perdre un fill.
- Chaque exécution est comptée une seule fois (registre par `order_id`).
- Chaque ordre d'entrée ou de sortie est précédé d'une intention persistée,
  résolue par client-id après un crash ou un timeout réseau.
- Les quantités vendues ou protégées sont plafonnées au solde réel du bot.
  Les frais d'achat prélevés en base sont pris en compte.
- La reconciliation au boot est *fail-closed* : une erreur d'API interrompt
  le démarrage sans jamais effacer une position.

## Migration depuis V29.5

Le contexte SQLite V29.5 (schéma 10) est migré automatiquement vers le schéma 11 :
coût de revient, quantité initiale, risque initial et type de halt sont reconstitués.
Pour un OCO existant dont seuls les identifiants de liste sont connus, les
identifiants des jambes sont relus via `GET /api/v3/orderList`.

## Limites connues

- Le simulateur de `tests/fake_binance.py` reproduit fidèlement les points qui
  gouvernent le risque (soldes bloqués, frais en base, OCO, erreurs). Il ne
  remplace pas une validation sur le **testnet** : l'API réelle n'a pas pu être
  appelée depuis l'environnement de développement.
- Le simulateur suppose que `GET /api/v3/orderList` rejette le paramètre
  `symbol` (erreur -1104). Le bot ne l'envoie plus, quel que soit le cas.
- Les résultats de backtest sur moins de 30 trades ne sont pas significatifs.
  Le rapport le signale.
