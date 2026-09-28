"""Ancien bot V29.6 intraday (signaux multi-modules sur une paire, 1 h),
rangé à part du moteur d'exécution : aucun avantage démontré, conservé
pour mémoire et pour ses tests. python -m v29 bot | backtest | walkforward
| status | resume | docs."""

from __future__ import annotations

from .adaptive import (  # noqa: F401
    AdaptiveEngine,
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
    ERC20_ABI,
    WEB3_AVAILABLE,
    Account,
    BlockchainAdapter,
    BlockchainError,
    BlockchainPolicy,
    ExtraDataToPOAMiddleware,
    Web3,
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
