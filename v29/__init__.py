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

from .adaptive import (  # noqa: F401
    AdaptiveEngine,
    _isnan,
    _row_get,
)
from .backtest import (  # noqa: F401
    DEFAULT_WF_GRID,
    INDICATOR_PARAMS,
    BacktestEngine,
    BacktestResult,
    WalkForwardWindow,
    _btc_vol_mult,
    _empty_metrics,
    _series_to_list,
    build_bias_series,
    build_btc_vol_mult_series,
    compute_metrics,
    format_report,
    format_walkforward_report,
    monte_carlo_trades,
    report_sensitivity,
    run_sensitivity,
    walk_forward,
    walkforward_verdict,
)
from .blockchain import (  # noqa: F401
    BlockchainAdapter,
    BlockchainError,
    BlockchainPolicy,
    _to_wei_exact,
)
from .cli import (  # noqa: F401
    _add_data_args,
    _backtest_cfg,
    _load_ctx_for_cli,
    _parse_utc_date,
    cmd_backtest,
    cmd_docs,
    cmd_resume,
    cmd_sensitivity,
    cmd_status,
    cmd_test,
    cmd_walkforward,
    cmd_wallet,
    fetch_historical,
    load_backtest_data,
    main,
)
from .config import (  # noqa: F401
    Config,
    _parse_symbol,
    dump_config_summary,
    load_config_from_env,
)
from .constants import (  # noqa: F401
    _ENV_BOOL,
    _LOG_ROOT,
    APP_DIR,
    CID_ENTRY_LIMIT,
    CID_ENTRY_MARKET,
    CID_EXIT_MARKET,
    CID_OCO_LIST,
    CID_OCO_SL,
    CID_OCO_TP,
    CID_STOP,
    ENV_DOC,
    ERC20_ABI,
    PROTECTION_CID_PREFIXES,
    SCHEMA_VERSION,
    VERSION_MODULE,
    WEB3_AVAILABLE,
    Account,
    ExtraDataToPOAMiddleware,
    Web3,
)
from .exchange import (  # noqa: F401
    _NUMERIC_STR,
    _ORDER_STATUS_MAP,
    AmbiguousOrder,
    ExchangeAdapter,
    MarketRules,
    OrderResult,
    _fnum,
)
from .execution import (  # noqa: F401
    _LEG_REASON,
    ExecutionEngine,
    _update_extremes,
    update_extremes,
)
from .indicators import (  # noqa: F401
    adx_calc,
    atr_calc,
    bollinger,
    compute_indicators,
    ema,
    macd_calc,
    obv_calc,
    rsi,
    vwap_calc,
)
from .infra import (  # noqa: F401
    Console,
    Heartbeat,
    JsonFormatter,
    Notifier,
    ProcessLock,
    acquire_instance_locks,
    build_heartbeat_logger,
    build_logger,
    release_locks,
)
from .models import (  # noqa: F401
    AUTO_CLEARABLE_HALTS,
    AdaptiveState,
    BlockchainState,
    BotContext,
    BotState,
    CancelResult,
    EntryResult,
    Fill,
    HaltKind,
    Portfolio,
    Position,
    ProtectionMode,
    ProtectionSnapshot,
    ProtectionState,
    RiskState,
    Signal,
    clear_halt,
    halt_ctx,
    is_frozen,
)
from .reconciliation import (  # noqa: F401
    _recover_position,
    reconcile,
)
from .risk import (  # noqa: F401
    RR_BY_MODULE,
    RiskEngine,
    break_even_stop,
    detect_flash_move,
    in_cooldown,
    in_flash_cooldown,
    record_closed_trade,
    trailing_stop,
)
from .runner import (  # noqa: F401
    OHLCV_LIMIT,
    BotRunner,
    _btc_annual_vol,
    _btc_bias,
    _cached_bias,
    _compute_subsystems,
    _handle_stop,
    _htf_bias,
    _init_benchmark,
    _send_daily_report,
    install_signal_handlers,
    run_bot,
)
from .signals import (  # noqa: F401
    _SIGNAL_REQUIRED,
    _above_vwap,
    _assign_tier,
    _essential_breakout,
    _essential_pullback,
    _essential_range,
    _essential_trend,
    _module_enabled,
    _score_breakout,
    _score_pullback,
    _score_range,
    _score_trend,
    adaptive_rsi_bounds,
    detect_regime,
    generate_signal,
    generate_signal_from_rows,
    min_signal_bars,
    module_enabled,
)
from .store import (  # noqa: F401
    Store,
)
from .utils import (  # noqa: F401
    _TF_UNITS_MS,
    BINANCE_TIMEOUT_MS,
    PUBLIC_DATA_API,
    ClockSync,
    PublicKlines,
    _annualization_factor,
    _bars_per_day,
    _cancel_client_id,
    _client_id,
    _dataclass_from_dict,
    _day_key,
    _dec_round,
    _env_b,
    _env_f,
    _env_i,
    _env_s,
    _env_tuple_csv,
    _get_env_logger,
    _parse_iso,
    _timeframe_ms,
    _today_utc,
    _utcnow,
    _utcnow_iso,
    _week_key,
    _week_start_utc,
    clock_offset_ms,
    describe_clock,
    ensure_utf8_stdio,
    make_binance,
    make_public_binance,
    measure_exchange_clock,
    redact_address,
    redact_url,
    resync_clock,
    scrub_secrets,
    set_clock_offset_ms,
    sync_exchange_clock,
)
