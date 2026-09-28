"""
V29.6-QUANT — Bot hybride CEX (Binance Spot) + Wallet EVM.

════════════════════════════════════════════════════════════════════════
PRINCIPES D'HARMONISATION
════════════════════════════════════════════════════════════════════════
P1. Résultats nommés. P2. Pas de réentrance implicite. P3. Fail-closed/noisy.
P4. I/O cohérentes. P5. Sections ascendantes (0 → 17).
P6. Env unifiés. P7. Logs [CAT]. P8. Exception → log + action.
P9. Transitoires exclus. P10. Unités : quote/base/ratio.
P11. Le solde exchange fait foi : on ne vend/protège jamais plus que le
     solde disponible du bot (hors EXTERNAL_BASE_RESERVE).
P12. Toute exécution est idempotente : chaque fill est comptabilisé une
     seule fois (recorded_fills), chaque ordre sensible est précédé d'une
     intention persistée (pending_order) et résolu par client-id.

════════════════════════════════════════════════════════════════════════
CHANGELOG V29.6 (refonte corrective du diagnostic V29.5)
════════════════════════════════════════════════════════════════════════
Bloquants
  - Config par défaut valide (adaptive_freshness_max=45 ≤ max_signal_age_min).
  - Suite de tests déplacée dans tests/ (pytest) avec un simulateur
    Binance Spot ; plus aucun test tautologique.
Exécution live
  - Cache de soldes invalidé après chaque ordre (fin des panic-sells
    systématiques après chaque achat).
  - Frais d'achat prélevés en base pris en compte : quantité détenue =
    net reçu, plafonnée au solde réel.
  - Types d'ordres Spot valides (STOP_LOSS / STOP_LOSS_LIMIT selon
    exchangeInfo) + OCO natif uniquement (le pseudo-OCO à 2 ordres est
    impossible en Spot : le solde est bloqué par le premier ordre).
  - Self-test via POST /api/v3/order/test (aucun ordre réel, aucun solde requis).
  - Machine de protection unique : snapshot → fills comptés une fois →
    re-protection systématique ; une position live n'est jamais laissée nue
    (re-protection à chaque cycle + stop logiciel de dernier recours).
  - Annulation « sûre » : on relit chaque jambe APRÈS l'annulation (plus de
    fill perdu entre la vérification et l'annulation).
  - Sorties partielles live : annulation de la protection → vente → nouvelle
    protection sur le reliquat.
  - Time-exit : plus de TypeError, plus de position laissée sans stop.
  - Intentions d'ordres persistées + résolution par client-id (crash ou
    timeout réseau entre l'ordre et la sauvegarde).
  - Reconciliation fail-closed (une erreur d'API n'efface plus une position).
  - GET /api/v3/orderList sans paramètre `symbol`.
Blocages définitifs supprimés
  - Pause pertes consécutives : compteur dédié remis à zéro à la pause.
  - Plus de faux « double-sell » / orphelin à chaque sortie OCO.
  - Orphelin re-vérifié périodiquement ; commande `resume` après audit.
  - Poussière (< min_notional) sortie proprement des livres.
Risque / quant
  - Adaptatif « consec » dans le bon sens (Sharpe négatif → pause plus tôt).
  - Latence adaptative mesurée sur chaque bougie (plus de biais de survie).
  - Break-even au-dessus du coût de revient (frais + slippage).
  - R-multiple rapporté au risque initial (additif sur les jambes partielles).
  - Disjoncteurs fail-closed si l'equity est illisible ; DD hebdo réarmé
    chaque semaine.
  - Volatilité réalisée annualisée selon le timeframe.
Backtest / recherche
  - Même logique que le live : biais HTF/BTC (sans look-ahead), BE,
    trailing, time-exit, cooldowns, disjoncteurs, désactivation de module.
  - Plus de double comptage des partielles ; métriques sur TOUS les trades.
  - Walk-forward avec vraie optimisation in-sample et fenêtres sans perte
    de warm-up.
Blockchain / sécurité
  - Nonce : une seule sémantique (« prochain nonce libre ») → plus de trou.
  - Confirmations, effectiveGasPrice, suivi de toutes les tx RBF.
  - Encodage ERC-20 compatible web3 v6/v7/v8 ; montants en Decimal.
  - Secrets (URL RPC, clés) retirés des messages d'erreur.

════════════════════════════════════════════════════════════════════════
ORGANISATION DU PAQUET (une section de l'ancien v29.py = un module)
════════════════════════════════════════════════════════════════════════
  v29/constants.py          Constantes, chemins, dépendances optionnelles et documentation des variables d'environnement
  v29/utils.py              Utilitaires : environnement, secrets masqués, arrondis, heure de Binance, client Binance
  v29/config.py             Configuration du bot V29 (variables d'environnement)
  v29/models.py             Types partagés : positions, portefeuille, états, contexte du bot
  v29/infra.py              Infrastructure : journaux, alertes Telegram, verrou d'instance, console, heartbeat
  v29/store.py              Base SQLite : état, trades, intentions d'ordres, courbe du capital
  v29/exchange.py           Adaptateur Binance Spot : ordres, soldes, règles de marché
  v29/blockchain.py         Wallet EVM (optionnel)
  v29/adaptive.py           Moteur adaptatif du bot V29
  v29/indicators.py         Indicateurs techniques
  v29/signals.py            Signaux d'entrée du bot V29
  v29/risk.py               Risque : taille des positions, disjoncteurs, stops
  v29/execution.py          Moteur d'exécution : entrées, protections, sorties (commun à TrendGuard)
  v29/reconciliation.py     Réconciliation au démarrage avec l'exchange
  v29/backtest.py           Métriques, backtest, walk-forward et sensibilité du bot V29
  v29/runner.py             Boucle du bot V29
  v29/cli.py                Ligne de commande du bot V29 (python -m v29 …)

`import v29` donne accès à tous les noms, comme avant ; ligne de
commande : python -m v29 bot | backtest | walkforward | status | resume.
"""

from __future__ import annotations

from .constants import (  # noqa: F401
    Account, APP_DIR, CID_ENTRY_LIMIT, CID_ENTRY_MARKET, CID_EXIT_MARKET, CID_OCO_LIST,
    CID_OCO_SL, CID_OCO_TP, CID_STOP, _ENV_BOOL, ENV_DOC, ERC20_ABI, ExtraDataToPOAMiddleware,
    _LOG_ROOT, PROTECTION_CID_PREFIXES, SCHEMA_VERSION, VERSION_MODULE, Web3, WEB3_AVAILABLE,
)
from .utils import (  # noqa: F401
    _annualization_factor, _bars_per_day, BINANCE_TIMEOUT_MS, _cancel_client_id, _client_id,
    clock_offset_ms, ClockSync, _dataclass_from_dict, _day_key, _dec_round,
    describe_clock, ensure_utf8_stdio, _env_b, _env_f, _env_i, _env_s,
    _env_tuple_csv, _get_env_logger, make_binance, make_public_binance, measure_exchange_clock,
    _parse_iso, PUBLIC_DATA_API, PublicKlines, redact_address, redact_url, resync_clock,
    scrub_secrets, set_clock_offset_ms, sync_exchange_clock, _TF_UNITS_MS, _timeframe_ms,
    _today_utc, _utcnow, _utcnow_iso, _week_key, _week_start_utc,
)
from .config import (  # noqa: F401
    Config, dump_config_summary, load_config_from_env, _parse_symbol,
)
from .models import (  # noqa: F401
    AdaptiveState, AUTO_CLEARABLE_HALTS, BlockchainState, BotContext, BotState, CancelResult,
    clear_halt, EntryResult, Fill, halt_ctx, HaltKind, is_frozen, Portfolio, Position,
    ProtectionMode, ProtectionSnapshot, ProtectionState, RiskState, Signal,
)
from .infra import (  # noqa: F401
    acquire_instance_locks, build_heartbeat_logger, build_logger, Console, Heartbeat,
    JsonFormatter, Notifier, ProcessLock, release_locks,
)
from .store import (  # noqa: F401
    Store,
)
from .exchange import (  # noqa: F401
    AmbiguousOrder, ExchangeAdapter, _fnum, MarketRules, _NUMERIC_STR, _ORDER_STATUS_MAP,
    OrderResult,
)
from .blockchain import (  # noqa: F401
    BlockchainAdapter, BlockchainError, BlockchainPolicy, _to_wei_exact,
)
from .adaptive import (  # noqa: F401
    AdaptiveEngine, _isnan, _row_get,
)
from .indicators import (  # noqa: F401
    adx_calc, atr_calc, bollinger, compute_indicators, ema, macd_calc, obv_calc, rsi, vwap_calc,
)
from .signals import (  # noqa: F401
    _above_vwap, adaptive_rsi_bounds, _assign_tier, detect_regime, _essential_breakout,
    _essential_pullback, _essential_range, _essential_trend, generate_signal,
    generate_signal_from_rows, min_signal_bars, _module_enabled, module_enabled, _score_breakout,
    _score_pullback, _score_range, _score_trend, _SIGNAL_REQUIRED,
)
from .risk import (  # noqa: F401
    break_even_stop, detect_flash_move, in_cooldown, in_flash_cooldown, record_closed_trade,
    RiskEngine, RR_BY_MODULE, trailing_stop,
)
from .execution import (  # noqa: F401
    ExecutionEngine, _LEG_REASON, _update_extremes, update_extremes,
)
from .reconciliation import (  # noqa: F401
    reconcile, _recover_position,
)
from .backtest import (  # noqa: F401
    BacktestEngine, BacktestResult, _btc_vol_mult, build_bias_series, build_btc_vol_mult_series,
    compute_metrics, DEFAULT_WF_GRID, _empty_metrics, format_report, format_walkforward_report,
    INDICATOR_PARAMS, monte_carlo_trades, report_sensitivity, run_sensitivity, _series_to_list,
    walk_forward, walkforward_verdict, WalkForwardWindow,
)
from .runner import (  # noqa: F401
    BotRunner, _btc_annual_vol, _btc_bias, _cached_bias, _compute_subsystems, _handle_stop,
    _htf_bias, _init_benchmark, install_signal_handlers, OHLCV_LIMIT, run_bot, 
    _send_daily_report,
)
from .cli import (  # noqa: F401
    _add_data_args, _backtest_cfg, cmd_backtest, cmd_docs, cmd_resume, cmd_sensitivity,
    cmd_status, cmd_test, cmd_walkforward, cmd_wallet, fetch_historical, load_backtest_data,
    _load_ctx_for_cli, main, _parse_utc_date,
)
