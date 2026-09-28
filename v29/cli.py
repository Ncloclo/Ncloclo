"""Ligne de commande du bot V29 (python -m v29 …).

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .constants import APP_DIR, ENV_DOC, _LOG_ROOT, VERSION_MODULE
from .utils import ensure_utf8_stdio, make_binance, redact_address, redact_url, _timeframe_ms
from .config import Config, load_config_from_env, _parse_symbol
from .models import BotContext, BotState, clear_halt, Position
from .infra import acquire_instance_locks, build_logger, release_locks
from .store import Store
from .blockchain import BlockchainAdapter, BlockchainError
from .indicators import compute_indicators
from .signals import min_signal_bars
from .backtest import (
    BacktestEngine, build_bias_series, build_btc_vol_mult_series, format_report,
    format_walkforward_report, monte_carlo_trades, report_sensitivity, run_sensitivity,
    walk_forward,
)
from .runner import run_bot


def _parse_utc_date(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fetch_historical(symbol: str, timeframe: str, start: str, end: str,
                     exchange: Any = None) -> pd.DataFrame:
    ex = exchange or make_binance()
    since = int(_parse_utc_date(start).timestamp() * 1000)
    end_ms = int(_parse_utc_date(end).timestamp() * 1000)
    all_bars: List[List[Any]] = []
    while since < end_ms:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        all_bars.extend(b[:6] for b in batch)
        nxt = int(batch[-1][0]) + 1
        if nxt <= since:
            break
        since = nxt
    df = pd.DataFrame(all_bars, columns=["ts", "open", "high", "low", "close",
                                         "volume"])
    df = df[df["ts"] < end_ms]
    df = df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def load_backtest_data(cfg: Config, start: str, end: str,
                       csv_path: Optional[str] = None,
                       with_bias: bool = True
                       ) -> Tuple[pd.DataFrame, Dict[str, Any], int]:
    """Charge l'historique avec une période de chauffe AVANT `start`
    (indicateurs et EMA HTF stabilisés) et construit les séries de biais
    sans look-ahead. Retourne (df, series, index_de_départ)."""
    tf_ms = _timeframe_ms(cfg.timeframe)
    warm_bars = min_signal_bars(cfg) + cfg.trend_ema * 3
    start_dt = _parse_utc_date(start)
    warm_start = (start_dt - timedelta(milliseconds=warm_bars * tf_ms)).isoformat()
    if csv_path:
        df = pd.read_csv(csv_path)
        df = df[["ts", "open", "high", "low", "close", "volume"]]
        df = df.sort_values("ts").reset_index(drop=True)
        with_bias = False
    else:
        df = fetch_historical(cfg.symbol, cfg.timeframe, warm_start, end)
    df = compute_indicators(df, cfg)
    start_idx = int(np.searchsorted(df["ts"].values,
                                    int(start_dt.timestamp() * 1000)))
    series: Dict[str, Any] = {}
    if with_bias:
        htf_ms = _timeframe_ms(cfg.htf_timeframe)
        htf_start = (start_dt - timedelta(
            milliseconds=cfg.htf_ema * 3 * htf_ms)).isoformat()
        if cfg.htf_bias_enabled:
            htf = fetch_historical(cfg.symbol, cfg.htf_timeframe, htf_start, end)
            series["htf_bias_series"] = build_bias_series(
                df["ts"], htf, cfg.htf_ema, cfg.htf_buffer, cfg.htf_buffer,
                cfg.htf_timeframe)
        if cfg.btc_bias_enabled:
            btc = fetch_historical(cfg.btc_symbol, cfg.htf_timeframe,
                                   htf_start, end)
            series["btc_bias_series"] = build_bias_series(
                df["ts"], btc, cfg.btc_ema, cfg.btc_bull_buffer,
                cfg.btc_bear_buffer, cfg.htf_timeframe)
        if cfg.btc_vol_filter_enabled:
            btc1h = fetch_historical(cfg.btc_symbol, "1h", warm_start, end)
            series["btc_vol_mult_series"] = build_btc_vol_mult_series(
                df["ts"], btc1h, cfg)
    return df, series, start_idx


def _backtest_cfg(args) -> Config:
    symbol, base, quote = _parse_symbol(args.symbol)
    return Config(run_mode="paper", symbol=symbol, base=base, quote=quote,
                  timeframe=args.timeframe,
                  htf_timeframe=getattr(args, "htf_timeframe", "4h"),
                  sizing_mode=getattr(args, "sizing_mode", "fixed"),
                  intrabar_partial_mode=getattr(args, "intrabar", "ohlc"))


def cmd_backtest(args):
    cfg = _backtest_cfg(args)
    print(f"Chargement {cfg.symbol} {cfg.timeframe} de {args.start} à {args.end}…")
    df, series, start_idx = load_backtest_data(
        cfg, args.start, args.end, args.csv, with_bias=not args.no_bias)
    print(f"{len(df)} barres (dont chauffe), biais={sorted(series) or 'aucun'}")
    res = BacktestEngine(cfg, initial_capital=args.capital).run(
        df, start=start_idx, **series)
    print(format_report({k: getattr(res, k) for k in (
        "total_return_pct", "sharpe", "sortino", "max_drawdown_pct",
        "profit_factor", "win_rate", "expectancy_r", "num_trades")}))
    print("Par module :")
    for mod, info in res.module_stats.items():
        t = info.get("trades", 0)
        w = info.get("wins", 0)
        wr = w / t * 100 if t else 0
        print(f"  {mod}: {w}W/{t - w}L ({wr:.0f}%) pnl={info.get('pnl', 0):.4f}")
    mc = monte_carlo_trades(res.trades)
    if mc:
        print(f"\nMonte Carlo (2000 tirages des trades) : rendement p5/p50/p95 = "
              f"{mc['ret_p5']:+.1f}% / {mc['ret_p50']:+.1f}% / "
              f"{mc['ret_p95']:+.1f}% | DD max p50/p95 = {mc['dd_p50']:.1f}% / "
              f"{mc['dd_p95']:.1f}% | P(perte) = {mc['prob_loss']:.0f}%")
    if res.num_trades < 30:
        print("\n⚠️ Moins de 30 trades : résultats statistiquement non significatifs.")
    return 0


def cmd_walkforward(args):
    cfg = _backtest_cfg(args)
    df, series, _ = load_backtest_data(cfg, args.start, args.end, args.csv,
                                       with_bias=not args.no_bias)
    windows = walk_forward(df, cfg, is_months=args.is_months,
                           oos_months=args.oos_months,
                           step_months=args.step_months, series=series)
    print(format_walkforward_report(windows))
    return 0


def cmd_sensitivity(args):
    cfg = _backtest_cfg(args)
    df, series, start_idx = load_backtest_data(cfg, args.start, args.end,
                                               args.csv,
                                               with_bias=not args.no_bias)
    grid = {
        "adx_trend_threshold": [20.0, 22.0, 25.0, 28.0],
        "atr_min_pct": [0.003, 0.005, 0.008],
        "break_even_trigger": [0.008, 0.01, 0.015],
        "trail_atr_mult": [1.5, 2.0, 2.5],
    }
    print(report_sensitivity(run_sensitivity(df, cfg, grid, series, start_idx)))
    return 0


def cmd_wallet(args):
    cfg = load_config_from_env()
    logger = build_logger(cfg)
    print("=" * 60)
    print("Configuration blockchain")
    print("=" * 60)
    print(f"enabled          : {cfg.blockchain_enabled}")
    if not cfg.blockchain_enabled:
        print("→ Définir BLOCKCHAIN_ENABLED=true pour activer.")
        return 0
    print(f"chain            : {cfg.blockchain_chain}")
    print(f"chain_id         : {cfg.blockchain_chain_id}")
    print(f"rpc_url          : {redact_url(cfg.blockchain_rpc_url)}")
    print(f"dry_run          : {cfg.blockchain_dry_run}")
    print(f"max_tx_value_eth : {cfg.blockchain_max_tx_value_eth}")
    print(f"confirmations    : {cfg.blockchain_confirmations}")
    print(f"whitelist        : {len(cfg.blockchain_whitelist)} adresse(s)")
    print(f"track_token      : {cfg.blockchain_track_token or '(none)'}")
    print(f"sweep_enabled    : {cfg.blockchain_sweep_enabled}")
    if cfg.blockchain_sweep_enabled:
        print(f"sweep_target     : {redact_address(cfg.blockchain_sweep_target)}")
        print(f"sweep_trigger    : {cfg.blockchain_sweep_trigger_eth}")
    print()
    try:
        adapter = BlockchainAdapter(cfg, logger, None, None)
        print(f"address          : {adapter.address}")
        print(f"balance_native   : {adapter.balance_native():.6f}")
        if adapter._token_contract is not None:
            print(f"balance_token    : {adapter.balance_token():.6f} "
                  f"({cfg.blockchain_track_token_symbol})")
        print(f"eip1559_support  : {adapter._eip1559_supported}")
    except BlockchainError as e:
        print(f"❌ {e}")
        return 1
    return 0


def cmd_docs(args):
    print("=" * 72)
    print(f"DOCUMENTATION VARIABLES D'ENVIRONNEMENT ({VERSION_MODULE})")
    print("=" * 72)
    for k, doc in sorted(ENV_DOC.items()):
        print(f"  {k:<35} {doc}")
    print("\nExemples :")
    print("  PAPER        : RUN_MODE=paper python -m v29 bot")
    print("  TESTNET      : BINANCE_TESTNET=true RUN_MODE=live \\")
    print("                 ENABLE_LIVE_TRADING=true \\")
    print("                 LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_RISK \\")
    print("                 BINANCE_API_KEY=... BINANCE_API_SECRET=... \\")
    print("                 python -m v29 bot")
    print("  BACKTEST     : python -m v29 backtest --start 2023-01-01 --end 2025-01-01")
    print("  APRÈS HALT   : python -m v29 status   puis   python -m v29 resume")
    return 0


def _load_ctx_for_cli() -> Tuple[Config, Store, Optional[BotContext]]:
    cfg = load_config_from_env()
    lg = logging.getLogger(f"{_LOG_ROOT}.cli")
    if not lg.handlers:
        lg.addHandler(logging.StreamHandler())
    store = Store(cfg.db_file, lg)
    return cfg, store, store.load_context()


def cmd_status(args):
    cfg, store, ctx = _load_ctx_for_cli()
    try:
        if ctx is None:
            print("Aucun contexte en base.")
            return 0
        p = ctx.position
        print(f"Version   : {ctx.bot_version} (schéma {ctx.schema_version})")
        print(f"État      : {ctx.state} | cycles={ctx.cycle_count}")
        print(f"Position  : {'LONG' if p.in_position else 'FLAT'}"
              + (f" {p.amount_held} @ {p.buy_price} SL={p.sl_price} "
                 f"TP={p.tp_price} prot={p.protection_mode}"
                 if p.in_position else ""))
        print(f"Halt      : {ctx.risk.halted} {ctx.risk.halt_kind or ''} "
              f"{ctx.risk.halt_reason or ''}")
        print(f"Pause     : {ctx.risk.paused_until or '-'} | cooldown="
              f"{ctx.risk.cooldown_until or '-'}")
        print(f"Orphelin  : {ctx.orphan_balance} | dust={ctx.portfolio.dust_base}")
        print(f"Pending   : {json.dumps(ctx.pending_order) if ctx.pending_order else '-'}")
        print(f"Stats     : W={ctx.portfolio.stats_wins} L={ctx.portfolio.stats_losses} "
              f"PnL={ctx.portfolio.stats_total_pnl:.4f}")
        if ctx.blockchain.last_pending_tx_hash:
            print(f"Tx EVM    : {ctx.blockchain.last_pending_tx_hash}")
        return 0
    finally:
        store.close()


def cmd_resume(args):
    """Lève les blocages après audit manuel. Le bot doit être arrêté."""
    cfg = load_config_from_env()
    locks = acquire_instance_locks(cfg.lock_file, cfg.db_file)
    try:
        _cfg, store, ctx = _load_ctx_for_cli()
        try:
            if ctx is None:
                print("Aucun contexte en base.")
                return 0
            if ctx.pending_order and not args.force_clear_pending:
                print("❌ Intention d'ordre en attente : vérifier l'ordre sur "
                      "l'exchange puis relancer avec --force-clear-pending.")
                return 1
            clear_halt(ctx)
            ctx.risk.paused_until = None
            ctx.risk.cooldown_until = None
            ctx.risk.cooldown_reason = None
            ctx.orphan_balance = False
            ctx.orphan_balance_since = None
            ctx.portfolio.losses_since_pause = 0
            if args.force_clear_pending:
                ctx.pending_order = None
            if args.clear_chain_pending:
                ctx.blockchain.last_pending_tx_hash = None
                ctx.blockchain.pending_tx_hashes = []
                ctx.blockchain.last_pending_tx_nonce = None
                ctx.blockchain.rbf_attempts = 0
            if args.flat:
                ctx.position = Position()
                ctx.state = BotState.FLAT.value
            store.save_context(ctx, cfg.run_mode, force=True)
            store.log_event("manual_resume", "WARNING", vars(args), cfg.run_mode,
                            durable=True)
            print("✅ Blocages levés. Au prochain démarrage, la reconciliation "
                  "revérifiera l'exchange.")
            return 0
        finally:
            store.close()
    finally:
        release_locks(locks)


def cmd_test(args):
    try:
        import pytest
    except ImportError:
        print("pytest requis : pip install pytest")
        return 1
    return pytest.main([os.path.join(APP_DIR, "tests"), "-q"])



def _add_data_args(p: argparse.ArgumentParser, start: str, end: str) -> None:
    p.add_argument("--symbol", default="TRX/USDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--htf-timeframe", dest="htf_timeframe", default="4h")
    p.add_argument("--start", default=start)
    p.add_argument("--end", default=end)
    p.add_argument("--csv", default=None,
                   help="OHLCV local (ts,open,high,low,close,volume) ; "
                        "désactive les biais externes")
    p.add_argument("--no-bias", action="store_true",
                   help="Ne pas télécharger HTF/BTC (biais forcés à UP)")
    p.add_argument("--intrabar", default="ohlc",
                   choices=["ohlc", "optimistic", "conservative"])


def main():
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description=f"V29-QUANT {VERSION_MODULE}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bot", help="Lance le bot")
    sub.add_parser("docs", help="Documentation variables d'environnement")
    sub.add_parser("wallet", help="État configuration blockchain")
    sub.add_parser("test", help="Tests (pytest)")
    sub.add_parser("status", help="État persistant du bot")
    p_res = sub.add_parser("resume", help="Lève halt/pause/orphelin après audit")
    p_res.add_argument("--force-clear-pending", action="store_true")
    p_res.add_argument("--clear-chain-pending", action="store_true")
    p_res.add_argument("--flat", action="store_true",
                       help="Oublier la position locale (après vente manuelle)")

    p_bt = sub.add_parser("backtest", help="Backtest historique")
    _add_data_args(p_bt, "2023-01-01", "2025-01-01")
    p_bt.add_argument("--capital", type=float, default=1000.0)
    p_bt.add_argument("--sizing-mode", dest="sizing_mode", default="fixed")

    p_wf = sub.add_parser("walkforward", help="Walk-forward (optimisation IS/OOS)")
    _add_data_args(p_wf, "2022-01-01", "2025-01-01")
    p_wf.add_argument("--is-months", type=int, default=6)
    p_wf.add_argument("--oos-months", type=int, default=3)
    p_wf.add_argument("--step-months", type=int, default=3)

    p_sens = sub.add_parser("sensitivity", help="Sensibilité")
    _add_data_args(p_sens, "2023-01-01", "2025-01-01")

    args = parser.parse_args()
    handlers = {"docs": cmd_docs, "wallet": cmd_wallet, "test": cmd_test,
                "status": cmd_status, "resume": cmd_resume,
                "backtest": cmd_backtest, "walkforward": cmd_walkforward,
                "sensitivity": cmd_sensitivity}
    if args.command == "bot":
        run_bot()
        return
    sys.exit(handlers[args.command](args))
