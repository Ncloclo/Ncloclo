"""Boucle du bot V29.

Partie de l'ancien bot V29.6 (v29/intraday/), rangé à part du moteur
d'exécution que TrendGuard utilise.
"""
from __future__ import annotations

import logging
import math
import os
import signal
import sys
import time
from typing import Any, Callable, Dict, Optional

import ccxt
import numpy as np
import pandas as pd

from ..config import Config, dump_config_summary, load_config_from_env
from ..constants import VERSION_MODULE
from ..exchange import AmbiguousOrder, ExchangeAdapter
from ..execution import ExecutionEngine
from ..infra import (
    Heartbeat,
    Notifier,
    acquire_instance_locks,
    build_heartbeat_logger,
    build_logger,
    release_locks,
)
from ..models import BotContext, EntryResult, HaltKind, halt_ctx, is_frozen
from ..reconciliation import reconcile
from ..risk import RiskEngine, detect_flash_move, in_cooldown, in_flash_cooldown
from ..store import Store
from ..utils import (
    _today_utc,
    _utcnow,
    _utcnow_iso,
    describe_clock,
    scrub_secrets,
    sync_exchange_clock,
)
from .adaptive import AdaptiveEngine
from .backtest import _btc_vol_mult
from .blockchain import BlockchainAdapter, BlockchainError
from .indicators import compute_indicators
from .signals import detect_regime, generate_signal

OHLCV_LIMIT = 1000   # EMA200 : résidu d'initialisation < 0,01 % sur 1000 barres

_running = True


def _handle_stop(sig, frame):
    global _running
    print(f"\nSignal {sig} → arrêt propre")
    _running = False


def install_signal_handlers() -> None:
    """Installé par run_bot() uniquement (pas à l'import du module)."""
    for _s in ("SIGINT", "SIGTERM"):
        try:
            if hasattr(signal, _s):
                signal.signal(getattr(signal, _s), _handle_stop)
        except Exception:
            pass


def _cached_bias(cache: Dict[str, Any], cfg: Config, logger: logging.Logger,
                 label: str, fetch: Callable[[], pd.DataFrame], span: int,
                 up_buf: float, down_buf: float) -> Optional[str]:
    now = time.time()
    if now - cache["ts"] < cfg.cache_ttl_sec:
        return cache["bias"]
    try:
        df = fetch()
        if len(df) < span + 2:
            raise ValueError(f"historique insuffisant ({len(df)} barres)")
        close = df["close"].astype(float)
        ema_val = float(close.ewm(span=span, adjust=False).mean().iloc[-2])
        last_closed = float(close.iloc[-2])
        cache["bias"] = ("UP" if last_closed > ema_val * (1 + up_buf)
                         else "DOWN" if last_closed < ema_val * (1 - down_buf)
                         else None)
        cache["ts"] = now
        cache["error_count"] = 0
    except Exception as e:
        cache["error_count"] = int(cache.get("error_count", 0)) + 1
        # Fail-closed : biais inconnu = pas d'entrée ; nouvelle tentative
        # rapide tant que les erreurs restent rares.
        retry = 60 if cache["error_count"] < cfg.max_consecutive_api_errors else 300
        cache["ts"] = now - cfg.cache_ttl_sec + retry
        cache["bias"] = None
        logger.warning(f"[BIAS] {label} KO: {e}")
    return cache["bias"]


def _htf_bias(ex: ExchangeAdapter, cfg: Config,
              logger: logging.Logger) -> Optional[str]:
    if not cfg.htf_bias_enabled:
        return None
    return _cached_bias(
        ex._htf_cache, cfg, logger, "HTF",
        lambda: ex.fetch_ohlcv_htf(cfg.symbol, cfg.htf_timeframe,
                                   limit=min(1000, cfg.htf_ema * 3 + 20)),
        cfg.htf_ema, cfg.htf_buffer, cfg.htf_buffer)


def _btc_bias(ex: ExchangeAdapter, cfg: Config,
              logger: logging.Logger) -> Optional[str]:
    if not cfg.btc_bias_enabled:
        return None
    return _cached_bias(
        ex._btc_cache, cfg, logger, "BTC",
        lambda: ex.fetch_ohlcv_htf(cfg.btc_symbol, cfg.htf_timeframe,
                                   limit=min(1000, cfg.btc_ema * 3 + 20)),
        cfg.btc_ema, cfg.btc_bull_buffer, cfg.btc_bear_buffer)


def _btc_annual_vol(ex: ExchangeAdapter, cfg: Config,
                    logger: logging.Logger) -> Optional[float]:
    if not cfg.btc_vol_filter_enabled:
        return None
    now = time.time()
    c = ex._btc_vol_cache
    if now - c["ts"] < cfg.cache_ttl_sec:
        return c["vol"]
    try:
        df = ex.fetch_ohlcv_htf(cfg.btc_symbol, "1h",
                                limit=cfg.btc_vol_window_hours * 4)
        df = df.iloc[:-1]   # bougies fermées uniquement
        ret = np.log(df["close"].astype(float)
                     / df["close"].astype(float).shift(1)).dropna()
        recent = ret.tail(cfg.btc_vol_window_hours)
        if len(recent) < max(8, cfg.btc_vol_window_hours // 2):
            c.update({"ts": now, "vol": None})
            return None
        vol = float(recent.std() * math.sqrt(24 * 365))
        c.update({"ts": now, "vol": vol, "error_count": 0})
        return vol
    except Exception as e:
        c["error_count"] = int(c.get("error_count", 0)) + 1
        c["ts"] = now
        c["vol"] = None
        logger.warning(f"[VOL] BTC KO: {e}")
        return None


def _send_daily_report(ctx: BotContext, ex: ExchangeAdapter,
                       notifier: Notifier, cfg: Config,
                       bchain: Optional[BlockchainAdapter] = None) -> None:
    today = _today_utc()
    if ctx.last_report_date == today:
        return
    if _utcnow().hour < cfg.report_hour_utc:
        return
    if time.time() - ctx.last_report_attempt_ts < cfg.report_retry_throttle_sec:
        return
    ctx.last_report_attempt_ts = time.time()
    eq = ex.get_equity(ctx)
    total = ctx.portfolio.stats_wins + ctx.portfolio.stats_losses
    wr = ctx.portfolio.stats_wins / total * 100 if total else 0.0
    lines = [
        f"📊 Rapport {today} ({VERSION_MODULE})",
        f"Mode={cfg.run_mode} | Equity={'n/a' if eq is None else f'{eq:.2f}'}",
        f"Trades={total} | WR={wr:.1f}% | "
        f"PnL={ctx.portfolio.stats_total_pnl:.4f}",
        f"ConsecLoss={ctx.portfolio.consec_losses} | "
        f"Halted={ctx.risk.halted}"
        + (f" ({ctx.risk.halt_reason})" if ctx.risk.halted else ""),
    ]
    if bchain and bchain.enabled:
        lines.append(f"⛓️ {cfg.blockchain_chain} | "
                     f"bal={ctx.blockchain.last_balance_eth:.6f} | "
                     f"gas={ctx.blockchain.total_gas_spent_eth:.6f} | "
                     f"tx={ctx.blockchain.tx_count} | "
                     f"sweep={ctx.blockchain.sweep_count}")
    if notifier("\n".join(lines), dedup_key=f"report-{today}", sync=True):
        ctx.last_report_date = today


def _init_benchmark(ctx: BotContext, ex: ExchangeAdapter,
                    logger: logging.Logger) -> None:
    if ctx.benchmark_start_equity is None:
        init_eq = ex.get_equity(ctx, force=True)
        if init_eq is not None and init_eq > 0:
            ctx.benchmark_start_equity = float(init_eq)
            logger.info(f"[BENCH] equity initiale: {init_eq:.2f}")
    if ctx.benchmark_start_price is None:
        try:
            init_px = ex.get_ticker().get("last", 0)
            if init_px > 0:
                ctx.benchmark_start_price = float(init_px)
        except Exception as e:
            logger.warning(f"[BENCH] price init KO: {e}")


def _compute_subsystems(ctx: BotContext,
                        bchain: Optional[BlockchainAdapter]) -> Dict[str, str]:
    cex = "PENDING" if ctx.pending_order else "OK"
    chain = "OFF"
    if bchain and bchain.enabled:
        chain = "OK" if not ctx.blockchain.last_pending_tx_hash else "TX_PENDING"
    prot = ctx.position.protection_mode if ctx.position.in_position else "—"
    recon = "ORPHAN" if ctx.orphan_balance else "OK"
    bench = "OK" if ctx.benchmark_start_equity else "INIT"
    risk_flags = []
    if ctx.risk.halted:
        risk_flags.append("FROZEN" if is_frozen(ctx) else "HALT")
    if ctx.risk.paused_until:
        risk_flags.append("PAUSE")
    if ctx.risk.cooldown_until:
        risk_flags.append("COOL")
    return {"CEX": cex, "CHAIN": chain, "PROT": prot, "RECON": recon,
            "BENCH": bench, "RISK": "+".join(risk_flags) if risk_flags else "OK"}


class BotRunner:
    def __init__(self, cfg: Config, logger: logging.Logger,
                 ex: ExchangeAdapter, store: Store, notifier: Notifier,
                 risk: RiskEngine, exec_engine: ExecutionEngine,
                 adaptive: AdaptiveEngine, heartbeat: Optional[Heartbeat] = None,
                 bchain: Optional[BlockchainAdapter] = None):
        self.cfg = cfg
        self.logger = logger
        self.ex = ex
        self.store = store
        self.notifier = notifier
        self.risk = risk
        self.exec = exec_engine
        self.adaptive = adaptive
        self.heartbeat = heartbeat
        self.bchain = bchain
        self.ctx: Optional[BotContext] = None
        self.regime_now = "UNCLEAR"
        self._last_chain_refresh = 0.0
        self._last_sweep_check = 0.0

    def boot(self) -> bool:
        cfg = self.cfg
        try:
            self.ex.load_markets()
        except Exception as e:
            self.logger.error(f"[BOOT] load_markets KO: {e}")
            return False
        clock = sync_exchange_clock(self.ex.exchange)
        if clock is not None:
            self.logger.info(f"[CLOCK] {describe_clock(clock.offset_ms, clock.uncertainty_ms)}"
                             f" → le bot utilise l'heure de Binance")
        if cfg.run_mode == "live" and not self.ex.self_test_conditional_orders():
            self.logger.critical("[BOOT] self-test ordres KO → arrêt")
            return False
        try:
            ctx = self.store.load_context() or BotContext()
        except RuntimeError as e:
            self.logger.critical(f"[BOOT] {e} — restaurer la DB avant relance")
            return False
        self.ctx = ctx
        self.ex.bind_paper_context(ctx)
        if ctx.started_at is None:
            ctx.started_at = _utcnow_iso()
        ctx.bot_version = VERSION_MODULE
        if not (cfg.adaptive_freshness_min <= ctx.adaptive.current_freshness_min
                <= cfg.adaptive_freshness_max):
            ctx.adaptive.current_freshness_min = cfg.adaptive_freshness_max
        _init_benchmark(ctx, self.ex, self.logger)
        try:
            reconcile(ctx, cfg, self.ex, self.logger, self.exec)
        except Exception as e:
            self.logger.error(f"[BOOT] reconciliation KO (fail-closed): {e}")
            return False
        if self.bchain and self.bchain.enabled:
            self.bchain.refresh_state(ctx)
        if not self.store.save_context(ctx, cfg.run_mode, force=True):
            self.logger.critical("[BOOT] DB non inscriptible → arrêt")
            return False
        t = self.ex.get_ticker()
        self.logger.info(
            f"[BOOT] {cfg.symbol} last={t['last']} minQty={self.ex.rules.min_amount} "
            f"minNotional={self.ex.min_notional()} stop={self.ex.stop_order_type} "
            f"état={'LONG' if ctx.position.in_position else 'FLAT'}"
            + (f" HALT={ctx.risk.halt_reason}" if ctx.risk.halted else ""))
        return True

    def run_cycle(self) -> None:
        cfg = self.cfg
        ctx = self.ctx
        assert ctx is not None
        df = self.ex.fetch_ohlcv(cfg.timeframe, limit=OHLCV_LIMIT)
        if df.empty or len(df) < 60:
            self.logger.warning(f"[CYCLE] OHLCV insuffisant ({len(df)})")
            return
        df = compute_indicators(df, cfg)
        closed = df.iloc[-2]
        live_price = self.ex.get_ticker()["last"]
        current_candle_ts = int(df.iloc[-1]["ts"])
        ctx.cycle_count += 1
        try:
            self.adaptive.observe_closed_candle(ctx.adaptive, int(closed["ts"]))
            self.adaptive.update(ctx, closed)
            self.exec.resolve_pending(ctx)
            equity = self.ex.get_equity(ctx)
            halted, _reason = self.risk.check_circuit_breakers(
                ctx, equity, consec_pause=self.adaptive.effective_consec_pause(
                    ctx.adaptive, cfg.consec_loss_pause))
            if len(df) > cfg.flash_move_lookback + 1:
                detect_flash_move(
                    float(df.iloc[-1 - cfg.flash_move_lookback]["close"]),
                    live_price, ctx, cfg)
            self.regime_now = detect_regime(closed, cfg)
            if ctx.position.in_position:
                self.exec.manage_position(ctx, df, closed, live_price,
                                          current_candle_ts)
            else:
                self.exec.check_orphan(ctx, live_price)
                self.exec.sweep_dust(ctx, live_price)
                if (not halted and not ctx.orphan_balance
                        and not ctx.pending_order and self.store.healthy
                        and not in_cooldown(ctx) and not in_flash_cooldown(ctx)):
                    self._maybe_enter(ctx, df, closed, live_price,
                                      current_candle_ts, equity)
        finally:
            self.store.save_context(ctx, cfg.run_mode)
        self._post_cycle(ctx, live_price)

    def _maybe_enter(self, ctx: BotContext, df: pd.DataFrame, closed: Any,
                     live_price: float, current_candle_ts: int,
                     equity: Optional[float]) -> None:
        cfg = self.cfg
        htf = _htf_bias(self.ex, cfg, self.logger)
        btc = _btc_bias(self.ex, cfg, self.logger)
        btc_vol = _btc_annual_vol(self.ex, cfg, self.logger)
        btc_vol_mult = _btc_vol_mult(btc_vol, cfg)
        atr_eff = self.adaptive.effective_atr_min(ctx.adaptive, cfg.atr_min_pct)
        sig = generate_signal(df, htf, btc, ctx, cfg, atr_min_pct=atr_eff)
        closed_ts = int(closed["ts"])
        if ctx.last_decision_candle_ts != closed_ts or sig.is_buy:
            ctx.last_decision_candle_ts = closed_ts
            self.store.log_decision({
                "signal": sig.action, "module": sig.module,
                "regime": sig.regime, "score": sig.score,
                "reject": sig.reject, "htf_bias": htf, "btc_bias": btc,
                "btc_vol": btc_vol, "btc_vol_mult": btc_vol_mult,
                "atr_min_pct_eff": atr_eff,
                "freshness_eff": self.adaptive.effective_freshness(
                    ctx.adaptive, cfg.max_signal_age_min)}, cfg.run_mode)
        if sig.is_buy and current_candle_ts != ctx.last_signal_candle_ts:
            res = self.exec.enter(
                ctx, closed, sig, live_price, current_candle_ts, equity,
                btc_vol_mult=btc_vol_mult,
                freshness_max=self.adaptive.effective_freshness(
                    ctx.adaptive, cfg.max_signal_age_min))
            if res != EntryResult.SKIPPED:
                # Un ordre est parti : aucune nouvelle tentative sur cette bougie.
                ctx.last_signal_candle_ts = current_candle_ts

    def _post_cycle(self, ctx: BotContext, live_price: float) -> None:
        cfg = self.cfg
        if self.bchain and self.bchain.enabled:
            try:
                self.bchain.poll_pending_tx(ctx)
                if time.time() - self._last_chain_refresh > 300:
                    self.bchain.refresh_state(ctx)
                    self._last_chain_refresh = time.time()
                if cfg.blockchain_sweep_enabled \
                        and time.time() - self._last_sweep_check > 60:
                    self.bchain.maybe_sweep(ctx)
                    self._last_sweep_check = time.time()
            except Exception as e:
                # Le sous-système EVM ne doit jamais retarder le CEX.
                self.logger.error(f"[CHAIN] cycle KO: {scrub_secrets(str(e), [cfg.blockchain_rpc_url])}")
        if time.time() - ctx.last_equity_snapshot_ts >= 300:
            eq = self.ex.get_equity(ctx, force=True)
            if eq is not None:
                try:
                    if cfg.run_mode == "paper":
                        cash, bqty = (ctx.portfolio.paper_cash,
                                      ctx.portfolio.paper_base)
                    else:
                        cash = self.ex.get_total_balance(cfg.quote, ctx)
                        bqty = self.ex.bot_total_base(ctx)
                    self.store.log_equity(eq, cash, bqty, live_price,
                                          ctx.risk.halted, cfg.run_mode)
                except Exception as e:
                    self.logger.warning(f"[EQ] snapshot KO: {e}")
            ctx.last_equity_snapshot_ts = time.time()
        _send_daily_report(ctx, self.ex, self.notifier, cfg, self.bchain)
        if self.heartbeat and ctx.cycle_count % max(1, cfg.heartbeat_every_cycles) == 0:
            self.heartbeat.tick(ctx, self.regime_now, self.ex.get_equity(ctx),
                                live_price, ctx.adaptive,
                                ctx.benchmark_start_equity,
                                subsystems=_compute_subsystems(ctx, self.bchain))
        self.store.save_context(ctx, cfg.run_mode)

    def run_forever(self) -> None:
        cfg = self.cfg
        backoff = cfg.network_backoff_init
        while _running:
            try:
                self.run_cycle()
                backoff = cfg.network_backoff_init
                self._sleep(cfg.loop_interval_sec)
                continue
            except AmbiguousOrder as e:
                self.logger.critical(f"[CYCLE] ordre ambigu non géré: {e}")
                if self.ctx is not None:
                    halt_ctx(self.ctx, "AMBIGUOUS_ORDER", HaltKind.INTEGRITY)
                    self.store.save_context(self.ctx, cfg.run_mode, force=True)
                self.notifier(f"🚨 Ordre ambigu — halt: {e}", critical=True)
            except ccxt.NetworkError as e:
                self.logger.warning(f"[CYCLE] NetworkError: {e}")
            except ccxt.ExchangeError as e:
                self.logger.error(f"[CYCLE] ExchangeError: {e}")
            except Exception as e:
                self.logger.exception(f"[CYCLE] KO: {e}")
            self._sleep(backoff)
            backoff = min(backoff * cfg.network_backoff_mult,
                          cfg.network_backoff_max)

    @staticmethod
    def _sleep(seconds: float) -> None:
        end = time.time() + seconds
        while _running and time.time() < end:
            time.sleep(min(1.0, max(0.0, end - time.time())))


def run_bot():
    global _running
    _running = True
    try:
        cfg = load_config_from_env()
    except ValueError as e:
        print(f"Configuration invalide : {e}", file=sys.stderr)
        sys.exit(2)
    logger = build_logger(cfg)
    hb_logger = build_heartbeat_logger(cfg)
    notifier = Notifier(os.environ.get("TELEGRAM_TOKEN", ""),
                        os.environ.get("TELEGRAM_CHAT_ID", ""), logger=logger)
    logger.info(dump_config_summary(cfg))
    install_signal_handlers()
    locks = acquire_instance_locks(cfg.lock_file, cfg.db_file)
    store: Optional[Store] = None
    runner: Optional[BotRunner] = None
    try:
        store = Store(cfg.db_file, logger)
        ex = ExchangeAdapter(cfg, logger)
        risk = RiskEngine(cfg, logger)
        exec_engine = ExecutionEngine(cfg, logger, ex, store, notifier, risk)
        bchain: Optional[BlockchainAdapter] = None
        if cfg.blockchain_enabled:
            try:
                bchain = BlockchainAdapter(cfg, logger, store, notifier)
            except BlockchainError as e:
                logger.critical(f"[BOOT] blockchain init KO: {e}")
                return
        runner = BotRunner(cfg, logger, ex, store, notifier, risk,
                           exec_engine, AdaptiveEngine(cfg, logger),
                           Heartbeat(cfg, logger, hb_logger), bchain)
        if not runner.boot():
            return
        runner.run_forever()
    finally:
        if store is not None:
            if runner is not None and runner.ctx is not None:
                store.save_context(runner.ctx, cfg.run_mode, force=True)
            store.checkpoint(mode="TRUNCATE")
            store.close()
        notifier.close()
        release_locks(locks)
        logger.info("[BOOT] arrêt propre.")
