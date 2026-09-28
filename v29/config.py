"""Configuration du bot V29 (variables d'environnement).

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Tuple

from .constants import APP_DIR, VERSION_MODULE
from .utils import (
    _env_b,
    _env_f,
    _env_i,
    _env_s,
    _env_tuple_csv,
    _timeframe_ms,
    redact_address,
    redact_url,
)


@dataclass(frozen=True)
class Config:
    # ── Marché ──
    symbol: str = "TRX/USDT"
    btc_symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    htf_timeframe: str = "4h"
    base: str = "TRX"
    quote: str = "USDT"
    # ── Mode ──
    run_mode: str = "paper"
    live_confirmation: str = ""
    live_confirmation_required: str = "I_UNDERSTAND_RISK"
    enable_live_trading: bool = False
    binance_testnet: bool = False
    # ── Risque ──
    risk_base_pct: float = 0.010
    risk_min_pct: float = 0.005
    risk_max_pct: float = 0.015
    risk_absolute_max_pct: float = 0.020
    max_sl_dist_pct: float = 0.05
    max_position_capital_pct: float = 0.95
    min_cash_reserve_pct: float = 0.05
    external_base_reserve: float = 0.0
    sizing_mode: str = "fixed"
    kelly_fraction: float = 0.25
    kelly_min_trades: int = 10
    kelly_window: int = 30
    vol_target_annual: float = 0.20
    tier_mults: Tuple[Tuple[str, float], ...] = (
        ("S", 1.20), ("A", 1.00), ("B", 0.65))
    tier_thresholds: Tuple[Tuple[str, int], ...] = (
        ("S", 70), ("A", 50), ("B", 30))
    regime_risk_mult: Tuple[Tuple[str, float], ...] = (
        ("TREND_UP", 1.00), ("TREND_DOWN", 0.00),
        ("RANGE", 0.65), ("UNCLEAR", 0.45))
    dd_derisk_curve: Tuple[Tuple[float, float], ...] = (
        (0.005, 1.00), (0.010, 0.85), (0.020, 0.60), (0.030, 0.35))
    max_signal_age_min: int = 45
    signal_age_decay_pct: float = 0.30
    module_min_sharpe: float = 0.15
    module_sharpe_window: int = 25
    module_sharpe_min_trades: int = 15
    module_reactivation_hours: int = 72
    # ── Sorties partielles (fractions de la quantité INITIALE) ──
    partial_exit_enabled: bool = True
    partial_exit_r1: float = 1.0
    partial_exit_pct1: float = 0.40
    partial_exit_r2: float = 2.0
    partial_exit_pct2: float = 0.30
    min_remaining_notional_mult: float = 1.5
    # ── Filtre volatilité BTC ──
    btc_vol_filter_enabled: bool = True
    btc_vol_window_hours: int = 24
    btc_vol_threshold_annual: float = 1.00
    btc_vol_size_reduction: float = 0.60
    # ── Disjoncteurs ──
    max_daily_dd: float = 0.03
    max_weekly_dd: float = 0.08
    consec_loss_pause: int = 5
    consec_loss_pause_hours: int = 24
    post_loss_cooldown_hours: int = 6
    post_loss_skip_adx: float = 30.0
    # ── Break-even / trailing / time-exit ──
    break_even_trigger: float = 0.01
    break_even_offset: float = 0.004
    trailing_enabled: bool = True
    trail_atr_mult: float = 2.0
    trailing_min_raise_pct: float = 0.005
    trailing_min_interval_sec: int = 300
    max_trade_age_hours: int = 48
    time_exit_min_r_mult: float = 0.5
    time_exit_max_attempts: int = 3
    time_exit_cooldown_hours: int = 4
    time_exit_min_retry_sec: int = 120
    # ── Indicateurs ──
    trend_ema: int = 200
    fast_ema: int = 9
    slow_ema: int = 21
    atr_period: int = 14
    atr_min_pct: float = 0.005
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0
    adx_period: int = 14
    vol_ma_period: int = 20
    vwap_period: int = 24
    obv_ema_span: int = 20
    swing_window: int = 5
    atr_rank_window: int = 200
    adx_trend_threshold: float = 22.0
    adx_range_threshold: float = 20.0
    # ── Biais ──
    btc_bias_enabled: bool = True
    btc_ema: int = 200
    btc_bull_buffer: float = 0.005
    btc_bear_buffer: float = 0.005
    htf_bias_enabled: bool = True
    htf_ema: int = 200
    htf_buffer: float = 0.005
    cache_ttl_sec: int = 900
    # ── Filtres de signal ──
    rsi_adaptive: bool = True
    vol_confirm_ratio: float = 1.3
    vol_breakout_ratio: float = 1.8
    vol_capitulation: float = 3.0
    pullback_rsi_lo: float = 40.0
    pullback_rsi_hi: float = 58.0
    pullback_max_dist_atr: float = 2.0
    pullback_prev_bullish_buffer: float = 1.005
    max_dist_ema_fast_pct: float = 0.06
    flash_move_pct: float = 0.05
    flash_move_lookback: int = 5
    flash_cooldown_min: int = 30
    # ── Exécution / protection ──
    use_oco: bool = True
    stop_only_protection: bool = False   # stratégies sans TP (TrendGuard)
    break_even_enabled: bool = True
    time_exit_enabled: bool = True
    stop_limit_offset_pct: float = 0.01
    software_stop_enabled: bool = True
    recovery_require_verified_entry: bool = True
    recovery_adopt_orders: bool = False
    protection_unknown_max: int = 6
    protection_unknown_max_sec: int = 120
    pending_order_timeout_sec: int = 300
    use_l2_filter: bool = False
    use_smart_buy: bool = False
    l2_depth: int = 20
    l2_cache_ttl_sec: int = 2
    obi_toxic_threshold: float = -0.55
    max_spread_tolerance: float = 0.005
    chaser_max_attempts: int = 3
    chaser_max_slippage_pct: float = 0.002
    chaser_wait_sec: int = 3
    self_test_conditional_orders: bool = True
    fee_rate: float = 0.001
    entry_slippage_buffer_pct: float = 0.0005
    exit_slippage_buffer_pct: float = 0.001
    paper_slippage_pct: float = 0.0005
    paper_fee_rate: float = 0.001
    # ── Boucle ──
    loop_interval_sec: int = 30
    network_backoff_init: int = 5
    network_backoff_max: int = 180
    network_backoff_mult: float = 2.0
    equity_cache_ttl_sec: int = 30
    balance_cache_ttl_sec: int = 5
    orphan_recheck_sec: int = 300
    report_hour_utc: int = 20
    report_retry_throttle_sec: int = 300
    intrabar_partial_mode: str = "ohlc"
    heartbeat_every_cycles: int = 10
    heartbeat_spinner: bool = True
    heartbeat_use_colors: bool = True
    heartbeat_log_file: str = ""
    heartbeat_equity_window: int = 30
    # ── Adaptatif ──
    adaptive_enabled: bool = True
    adaptive_latency_window: int = 50
    adaptive_freshness_min: int = 15
    adaptive_freshness_max: int = 45
    adaptive_atr_scaling: bool = True
    adaptive_consec_scaling: bool = True
    adaptive_min_samples: int = 10
    last_trades_per_module: int = 50
    reconcile_trades_window_hours: int = 72
    max_consecutive_api_errors: int = 3
    # ── Blockchain ──
    blockchain_enabled: bool = False
    blockchain_chain: str = "ethereum"
    blockchain_rpc_url: str = ""
    blockchain_chain_id: int = 1
    blockchain_dry_run: bool = True
    blockchain_max_tx_value_eth: float = 0.01
    blockchain_min_gas_reserve_eth: float = 0.005
    blockchain_max_priority_fee_gwei: float = 5.0
    blockchain_max_fee_gwei: float = 100.0
    blockchain_confirmations: int = 2
    blockchain_tx_timeout_sec: int = 600
    blockchain_whitelist_enabled: bool = True
    blockchain_whitelist: Tuple[str, ...] = ()
    blockchain_track_token: str = ""
    blockchain_track_token_symbol: str = ""
    blockchain_replace_fee_boost_pct: float = 0.15
    blockchain_max_rbf_attempts: int = 3
    blockchain_sweep_enabled: bool = False
    blockchain_sweep_target: str = ""
    blockchain_sweep_trigger_eth: float = 0.05
    blockchain_sweep_keep_eth: float = 0.01
    blockchain_sweep_fail_cooldown_sec: int = 1800
    # ── Fichiers ──
    db_file: str = ""
    log_file: str = ""
    lock_file: str = ""

    def __post_init__(self):
        if self.run_mode not in {"paper", "live"}:
            raise ValueError("run_mode doit être 'paper' ou 'live'.")
        if _timeframe_ms(self.htf_timeframe) <= _timeframe_ms(self.timeframe):
            raise ValueError("HTF_TIMEFRAME doit être supérieur à TIMEFRAME.")
        if self.sizing_mode not in {"fixed", "kelly", "vol_target"}:
            raise ValueError(
                f"sizing_mode={self.sizing_mode!r} : fixed | kelly | vol_target.")
        if not (0 < self.risk_min_pct <= self.risk_base_pct <= self.risk_max_pct):
            raise ValueError("Bornes de risque incohérentes.")
        if self.risk_max_pct > self.risk_absolute_max_pct:
            raise ValueError("risk_max_pct > risk_absolute_max_pct.")
        if not (0 < self.max_daily_dd <= self.max_weekly_dd < 1):
            raise ValueError("Il faut 0 < max_daily_dd <= max_weekly_dd < 1.")
        if not (0 < self.max_position_capital_pct <= 1):
            raise ValueError("max_position_capital_pct doit être dans ]0, 1].")
        if self.external_base_reserve < 0:
            raise ValueError("external_base_reserve doit être >= 0.")
        if self.partial_exit_enabled:
            if not (0 < self.partial_exit_pct1 and 0 < self.partial_exit_pct2
                    and self.partial_exit_pct1 + self.partial_exit_pct2 < 1):
                raise ValueError(
                    "partial_exit_pct1 + partial_exit_pct2 doit être < 1.")
            if not (0 < self.partial_exit_r1 < self.partial_exit_r2):
                raise ValueError("Il faut 0 < partial_exit_r1 < partial_exit_r2.")
        round_trip = 2 * self.fee_rate + self.exit_slippage_buffer_pct
        if self.break_even_offset < round_trip:
            raise ValueError(
                f"break_even_offset={self.break_even_offset} < coûts "
                f"aller-retour {round_trip:.4f} : un break-even serait une perte.")
        if self.break_even_trigger <= self.break_even_offset:
            raise ValueError("break_even_trigger doit être > break_even_offset.")
        if not (0 < self.stop_limit_offset_pct < 0.1):
            raise ValueError("stop_limit_offset_pct doit être dans ]0, 0.1[.")
        if self.intrabar_partial_mode not in {"ohlc", "optimistic",
                                                "conservative"}:
            raise ValueError("intrabar_partial_mode invalide.")
        if {k for k, _ in self.tier_mults} != {k for k, _ in
                                                self.tier_thresholds}:
            raise ValueError("tier_mults et tier_thresholds : clés différentes.")
        if self.adaptive_freshness_min >= self.adaptive_freshness_max:
            raise ValueError("adaptive_freshness_min >= max.")
        if self.adaptive_freshness_max > self.max_signal_age_min:
            raise ValueError(
                "adaptive_freshness_max > max_signal_age_min : "
                "l'adaptatif ne peut que resserrer, pas desserrer.")
        if self.run_mode == "live":
            if not self.enable_live_trading:
                raise ValueError("LIVE refusé: ENABLE_LIVE_TRADING=true requis.")
            if self.live_confirmation != self.live_confirmation_required:
                raise ValueError(
                    "LIVE refusé: LIVE_TRADING_CONFIRMATION requis.")
            if not self.use_oco and not self.stop_only_protection:
                raise ValueError("LIVE refusé: use_oco=true requis.")
        if self.blockchain_enabled:
            # Wallet EVM : partie de l'ancien bot V29 (v29/intraday/).
            from .intraday.blockchain import WEB3_AVAILABLE, ExtraDataToPOAMiddleware
            if not WEB3_AVAILABLE:
                raise ValueError("blockchain_enabled=true mais web3 absent.")
            if not self.blockchain_rpc_url:
                raise ValueError("blockchain_enabled=true mais RPC URL absente.")
            if self.blockchain_chain_id <= 0:
                raise ValueError("blockchain_chain_id invalide.")
            if self.blockchain_max_priority_fee_gwei > self.blockchain_max_fee_gwei:
                raise ValueError("max_priority_fee > max_fee.")
            if self.blockchain_confirmations < 1:
                raise ValueError("blockchain_confirmations doit être >= 1.")
            if self.blockchain_chain.lower() in ("bsc", "polygon") \
                    and ExtraDataToPOAMiddleware is None:
                raise ValueError("Chaîne POA requise mais web3.py trop ancien.")
            if self.blockchain_sweep_enabled:
                if not self.blockchain_sweep_target:
                    raise ValueError("sweep_enabled mais target vide.")
                if self.blockchain_sweep_trigger_eth <= self.blockchain_sweep_keep_eth:
                    raise ValueError(
                        "sweep_trigger_eth doit être > sweep_keep_eth.")
                if (self.blockchain_whitelist_enabled
                        and self.blockchain_sweep_target.lower()
                        not in {a.lower() for a in self.blockchain_whitelist}):
                    raise ValueError(
                        "BLOCKCHAIN_SWEEP_TARGET absent de BLOCKCHAIN_WHITELIST.")
        if not self.db_file:
            object.__setattr__(self, "db_file",
                               os.path.join(APP_DIR, f"v29_{self.base}_{self.quote}_{self.run_mode}.db"))
        if not self.log_file:
            object.__setattr__(self, "log_file",
                               os.path.join(APP_DIR, f"v29_{self.base}_{self.quote}_{self.run_mode}.log"))
        if not self.lock_file:
            object.__setattr__(self, "lock_file",
                               os.path.join(APP_DIR, f"v29_{self.base}_{self.quote}_{self.run_mode}.lock"))
        if not self.heartbeat_log_file:
            object.__setattr__(self, "heartbeat_log_file",
                               os.path.join(APP_DIR, f"v29_{self.base}_{self.quote}_heartbeat.log"))

    @property
    def tier_mult_map(self) -> Dict[str, float]:
        return dict(self.tier_mults)

    @property
    def tier_threshold_map(self) -> Dict[str, int]:
        return dict(self.tier_thresholds)

    @property
    def regime_mult_map(self) -> Dict[str, float]:
        return dict(self.regime_risk_mult)

    @property
    def round_trip_cost_pct(self) -> float:
        return 2 * self.fee_rate + self.exit_slippage_buffer_pct


def _parse_symbol(raw: str) -> Tuple[str, str, str]:
    s = (raw or "").strip().upper()
    parts = s.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1] or ":" in s:
        raise ValueError(f"SYMBOL={raw!r} invalide (format attendu BASE/QUOTE).")
    return s, parts[0], parts[1]


def load_config_from_env() -> Config:
    symbol, base, quote = _parse_symbol(_env_s("SYMBOL", "TRX/USDT"))
    return Config(
        symbol=symbol, base=base, quote=quote,
        btc_symbol=_env_s("BTC_SYMBOL", "BTC/USDT").upper(),
        timeframe=_env_s("TIMEFRAME", "1h"),
        htf_timeframe=_env_s("HTF_TIMEFRAME", "4h"),
        run_mode=_env_s("RUN_MODE", "paper").lower(),
        live_confirmation=_env_s("LIVE_TRADING_CONFIRMATION", ""),
        enable_live_trading=_env_b("ENABLE_LIVE_TRADING", False),
        binance_testnet=_env_b("BINANCE_TESTNET", False),
        risk_base_pct=_env_f("RISK_BASE_PCT", 0.010),
        risk_min_pct=_env_f("RISK_MIN_PCT", 0.005),
        risk_max_pct=_env_f("RISK_MAX_PCT", 0.015),
        sizing_mode=_env_s("SIZING_MODE", "fixed").lower(),
        max_daily_dd=_env_f("MAX_DAILY_DD", 0.03),
        max_weekly_dd=_env_f("MAX_WEEKLY_DD", 0.08),
        external_base_reserve=_env_f("EXTERNAL_BASE_RESERVE", 0.0),
        use_l2_filter=_env_b("USE_L2_FILTER", False),
        use_smart_buy=_env_b("USE_SMART_BUY", False),
        use_oco=_env_b("USE_OCO", True),
        self_test_conditional_orders=_env_b(
            "SELF_TEST_CONDITIONAL_ORDERS", True),
        recovery_require_verified_entry=_env_b(
            "RECOVERY_REQUIRE_VERIFIED_ENTRY", True),
        recovery_adopt_orders=_env_b("RECOVERY_ADOPT_ORDERS", False),
        intrabar_partial_mode=_env_s("INTRABAR_PARTIAL_MODE", "ohlc").lower(),
        adaptive_enabled=_env_b("ADAPTIVE_ENABLED", True),
        heartbeat_every_cycles=_env_i("HEARTBEAT_EVERY_CYCLES", 10),
        heartbeat_spinner=_env_b("HEARTBEAT_SPINNER", True),
        heartbeat_use_colors=_env_b("HEARTBEAT_USE_COLORS", True),
        heartbeat_log_file=_env_s("HEARTBEAT_LOG_FILE", ""),
        blockchain_enabled=_env_b("BLOCKCHAIN_ENABLED", False),
        blockchain_chain=_env_s("BLOCKCHAIN_CHAIN", "ethereum").lower(),
        blockchain_rpc_url=_env_s("BLOCKCHAIN_RPC_URL", ""),
        blockchain_chain_id=_env_i("BLOCKCHAIN_CHAIN_ID", 1),
        blockchain_dry_run=_env_b("BLOCKCHAIN_DRY_RUN", True),
        blockchain_max_tx_value_eth=_env_f("BLOCKCHAIN_MAX_TX_VALUE_ETH", 0.01),
        blockchain_min_gas_reserve_eth=_env_f(
            "BLOCKCHAIN_MIN_GAS_RESERVE_ETH", 0.005),
        blockchain_max_priority_fee_gwei=_env_f(
            "BLOCKCHAIN_MAX_PRIORITY_FEE_GWEI", 5.0),
        blockchain_max_fee_gwei=_env_f("BLOCKCHAIN_MAX_FEE_GWEI", 100.0),
        blockchain_confirmations=_env_i("BLOCKCHAIN_CONFIRMATIONS", 2),
        blockchain_tx_timeout_sec=_env_i("BLOCKCHAIN_TX_TIMEOUT_SEC", 600),
        blockchain_whitelist_enabled=_env_b(
            "BLOCKCHAIN_WHITELIST_ENABLED", True),
        blockchain_whitelist=_env_tuple_csv("BLOCKCHAIN_WHITELIST"),
        blockchain_track_token=_env_s("BLOCKCHAIN_TRACK_TOKEN", ""),
        blockchain_track_token_symbol=_env_s(
            "BLOCKCHAIN_TRACK_TOKEN_SYMBOL", ""),
        blockchain_sweep_enabled=_env_b("BLOCKCHAIN_SWEEP_ENABLED", False),
        blockchain_sweep_target=_env_s("BLOCKCHAIN_SWEEP_TARGET", "").lower(),
        blockchain_sweep_trigger_eth=_env_f(
            "BLOCKCHAIN_SWEEP_TRIGGER_ETH", 0.05),
        blockchain_sweep_keep_eth=_env_f("BLOCKCHAIN_SWEEP_KEEP_ETH", 0.01),
        db_file=_env_s("DB_FILE", ""),
        log_file=_env_s("LOG_FILE", ""),
        lock_file=_env_s("LOCK_FILE", ""),
    )


def dump_config_summary(cfg: Config) -> str:
    lines = ["═" * 72, f"CONFIGURATION {VERSION_MODULE}", "═" * 72]
    lines.append(f"  Marché         : {cfg.symbol} "
                 f"({cfg.timeframe} / HTF {cfg.htf_timeframe})")
    lines.append(f"  Mode           : {cfg.run_mode.upper()}"
                 + (" [LIVE]" if cfg.run_mode == "live" else "")
                 + (" [TESTNET]" if cfg.binance_testnet else ""))
    lines.append(f"  Sizing         : {cfg.sizing_mode} "
                 f"(base={cfg.risk_base_pct*100:.2f}% "
                 f"min={cfg.risk_min_pct*100:.2f}% "
                 f"max={cfg.risk_max_pct*100:.2f}%)")
    lines.append(f"  Disjoncteurs   : DD j={cfg.max_daily_dd*100:.1f}% "
                 f"hebdo={cfg.max_weekly_dd*100:.1f}% "
                 f"pause={cfg.consec_loss_pause} pertes")
    lines.append(f"  Protection     : OCO natif={cfg.use_oco} "
                 f"stop logiciel={cfg.software_stop_enabled} "
                 f"self-test={cfg.self_test_conditional_orders}")
    lines.append(f"  Filtres        : L2={cfg.use_l2_filter} "
                 f"smartbuy={cfg.use_smart_buy} "
                 f"partials={cfg.partial_exit_enabled}")
    lines.append(f"  Break-even     : trigger={cfg.break_even_trigger*100:.2f}% "
                 f"offset={cfg.break_even_offset*100:.2f}% "
                 f"(coûts A/R={cfg.round_trip_cost_pct*100:.2f}%)")
    lines.append(f"  Adaptive       : {cfg.adaptive_enabled} "
                 f"(freshness {cfg.adaptive_freshness_min}-"
                 f"{cfg.adaptive_freshness_max} min)")
    if cfg.external_base_reserve > 0:
        lines.append(f"  Réserve base   : {cfg.external_base_reserve} "
                     f"{cfg.base} (hors bot)")
    if cfg.blockchain_enabled:
        lines.append(f"  Blockchain     : {cfg.blockchain_chain} "
                     f"(id={cfg.blockchain_chain_id}) "
                     f"dry_run={cfg.blockchain_dry_run}")
        lines.append(f"    RPC          : {redact_url(cfg.blockchain_rpc_url)}")
        lines.append(f"    Whitelist    : {len(cfg.blockchain_whitelist)} "
                     f"adresse(s)")
        if cfg.blockchain_sweep_enabled:
            lines.append(f"    SWEEP        : actif → "
                         f"{redact_address(cfg.blockchain_sweep_target)}")
    else:
        lines.append("  Blockchain     : désactivée")
    lines.append("═" * 72)
    return "\n".join(lines)
