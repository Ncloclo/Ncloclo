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

### Auto-diagnostic et adaptation ([`docs/ADAPTATION.md`](docs/ADAPTATION.md))

`python trendguard_bot.py diagnose` analyse, sans passer d'ordre :
- **le système** : horloge et latence vers Binance, bot actif, disque ;
- **les données** : retards, trous, prix aberrants, liquidité ;
- **le marché** : régime BTC, hésitation, volatilité ;
- **les signaux du jour** ;
- **le portefeuille** : risque engagé, corrélations, scénarios de krach ;
- **la santé de la stratégie** : l'avantage statistique existe-t-il encore ?
- **les résultats réels du bot comparés à l'historique**, par un test statistique.

Le bot relance ce diagnostic tous les 7 jours (`TG_AUTO_DIAGNOSE_DAYS`) et notifie en cas d'alerte.

Aucune règle ne s'auto-modifie : sur données réelles, les adaptations « apprises des
résultats récents » font moins bien hors échantillon. Seul un **profil prudent**
optionnel est proposé (`TG_DD_THROTTLE=0.10:0.5` : risque divisé par 2 au-delà de 10 %
de baisse). Il réduit la pire baisse de −33,6 % à −24,2 % sur 2023-2026, pour
+32 % par an au lieu de +37 %.

### Utilisation

```bash
pip install -r requirements.txt
python trend_strategy.py download --data data        # historique Coin Metrics
python trend_strategy.py research --data data         # régénère le rapport
python trendguard_bot.py docs                         # variables d'environnement
RUN_MODE=paper python trendguard_bot.py run           # paper, prix réels Binance
python trendguard_bot.py replay --data data --start 2025-06-01   # paper rejoué sur l'historique réel
python trendguard_bot.py status                       # état du portefeuille
python trendguard_bot.py diagnose                     # auto-diagnostic complet (lecture seule)
```

### Dans VS Code

1. Ouvrez le dossier du projet, puis installez l'extension **Python** (proposée automatiquement).
2. Lancez `Terminal ▸ Exécuter la tâche… ▸ Installer les dépendances`.
3. Copiez `.env.example` en `.env`. Les réglages par défaut conviennent pour le mode paper.
4. Ouvrez `Exécuter et déboguer`, choisissez une configuration, puis appuyez sur **F5** :
   - **TrendGuard — paper (prix réels Binance)** : le bot en continu ;
   - **TrendGuard — un seul cycle** : une décision, puis arrêt ;
   - **TrendGuard — rejeu paper 12 mois** : télécharge l'historique et le rejoue ;
   - **TrendGuard — statut du portefeuille** ;
   - **Tests (pytest)**.

Live, testnet d'abord :

```bash
BINANCE_TESTNET=true RUN_MODE=live ENABLE_LIVE_TRADING=true \
LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_RISK \
BINANCE_API_KEY=… BINANCE_API_SECRET=… python trendguard_bot.py run
```

Compte réel :

1. Créez la clé API sur Binance **sans droit de retrait**, limitée à l'adresse
   IP du serveur.
2. Mettez la clé dans `.env` (`BINANCE_API_KEY=`), puis enregistrez le secret
   par saisie masquée : `python trendguard_bot.py set-secret`. Dans VS Code,
   c'est la tâche « Binance — enregistrer la clé secrète ». Le secret ne passe
   ni par l'écran ni par l'historique du terminal.
3. Lancez `python trendguard_bot.py verify` : droits de la clé (retrait
   interdit, trading autorisé), soldes, validation des ordres par Binance et
   simulation des achats du jour. **Aucun ordre n'est passé.**
4. Fixez `TG_MAX_CAPITAL`, le capital en USDT confié au bot. Le bot gère alors
   un sous-compte virtuel (ce plafond, plus ses propres gains et pertes), quel
   que soit le solde réel du compte. Le kill-switch s'applique à ce capital.

Le bot prend ses décisions une fois par jour, juste après la clôture de 00:00
UTC. Il appelle **exactement les mêmes fonctions** que le backtest. Un test
vérifie la parité exacte : mêmes trades, même PnL, même equity.

Chaque position est protégée à deux niveaux :

- le **stop de clôture** de la stratégie, qui fixe le risque de 1 % ;
- un **stop catastrophe** `STOP_LOSS` posé sur Binance, 1 × volatilité plus
  bas, remonté avec le trailing. Il protège d'un krach entre deux clôtures.

Au-delà de 40 % de drawdown, un kill-switch bloque les entrées
(`TG_KILL_DRAWDOWN`). Pour le lever, arrêtez le bot puis lancez
`python trendguard_bot.py resume` : la commande est refusée tant que le bot
tourne, car il réécrirait son état au cycle suivant.

**Une seule instance par compte Binance.** Un verrou empêche deux bots de
partager la même base sur une même machine. Il ne peut rien contre deux
machines ou deux copies du projet. En mode réel, le bot refuse donc de
démarrer s'il trouve sur le compte des ordres à son nom qu'il ne connaît pas :
une autre instance tourne peut-être. Si c'est votre base qui a été perdue,
relancez une seule fois avec `TG_ALLOW_RECOVERY=true` pour reprendre ces
positions.

## Déploiement (Docker)

```bash
git clone https://github.com/Ncloclo/Ncloclo.git && cd Ncloclo
git checkout claude/v29-5-hybrid-bot-fz9gvm
cp .env.example .env              # paper par défaut ; éditer pour le live
docker compose up -d --build      # construit l'image (tests inclus) et démarre
```

| Action | Commande |
|---|---|
| Suivre le bot (une ligne `[HEARTBEAT]` toutes les 15 min) | `docker compose logs -f` |
| État du portefeuille | `docker compose exec trendguard python trendguard_bot.py status` |
| Santé (`healthy` / `unhealthy`) | `docker compose ps` |
| Mettre à jour | `git pull && docker compose up -d --build` |
| Arrêter (état conservé) | `docker compose down` |
| Lever le kill-switch | `docker compose stop && docker compose run --rm trendguard resume && docker compose start` |
| Repartir de zéro (efface le portefeuille paper) | `docker compose down -v` |

- L'image ne se construit que si toute la suite de tests passe.
- Le conteneur redémarre tout seul après un crash ou un redémarrage du serveur.
  L'état (base SQLite, logs) est conservé dans le volume `trendguard-data`.
- Docker considère le bot en mauvaise santé (`unhealthy`) si aucun cycle n'a
  réussi depuis 10 minutes, ou si le kill-switch est déclenché.
- Serveur conseillé : un petit VPS allumé en permanence (1 vCPU, 1 Go de RAM ;
  le bot utilise environ 230 Mo), situé dans un pays où Binance n'est pas
  restreint. Binance refuse notamment les adresses IP des États-Unis.

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
python -m pytest tests -q      # 164 tests, simulateurs Binance Spot mono et multi-paires
```

## Limites connues

- Les simulateurs reproduisent les mécanismes de risque de Binance Spot :
  soldes bloqués, frais en base, OCO (l'autre jambe expire dès une exécution
  partielle), carnet à profondeur finie, filtres de prix, de quantité et de
  nombre d'ordres, erreurs à statut inconnu (-1001, 503) et courses entre deux
  appels API. Ils ne remplacent pas le **testnet**, seul moyen de valider les
  vraies réponses de l'API avec des clés.
- La recherche utilise les clôtures USD de Coin Metrics, alors que le bot live
  utilise les clôtures USDT de Binance. Les deux séries sont très proches, sans
  être identiques.
- Les résultats dépendent pour une part notable de quelques actifs (ZEC, XRP,
  BTC sur 2023-2026). Sans ces trois-là, la stratégie reste positive mais tombe
  à +16 % par an. Gardez donc un univers large.
