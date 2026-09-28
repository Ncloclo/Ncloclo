"""Métriques, backtest, walk-forward et sensibilité du bot V29.

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import dataclasses
import itertools
import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .adaptive import AdaptiveEngine, _isnan
from .config import Config
from .constants import _LOG_ROOT
from .indicators import compute_indicators
from .models import BotContext, BotState, Position, Signal
from .risk import (
    RR_BY_MODULE,
    RiskEngine,
    break_even_stop,
    detect_flash_move,
    in_cooldown,
    in_flash_cooldown,
    record_closed_trade,
    trailing_stop,
)
from .signals import generate_signal_from_rows, min_signal_bars
from .utils import _annualization_factor, _timeframe_ms


def compute_metrics(equity_curve: List[float],
                    trades: List[Dict[str, Any]],
                    timeframe: str = "1h") -> Dict[str, Any]:
    if len(equity_curve) < 2:
        return _empty_metrics()
    eq = np.array(equity_curve, dtype=float)
    initial = eq[0]
    final = eq[-1]
    returns = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1.0)
    returns = returns[np.isfinite(returns)]
    total_return = (final / initial - 1) * 100 if initial > 0 else 0.0
    bpy = _annualization_factor(timeframe)
    sharpe = ((returns.mean() / returns.std()) * math.sqrt(bpy)
              if len(returns) > 1 and returns.std() > 0 else 0.0)
    downside = returns[returns < 0]
    sortino = ((returns.mean() / downside.std()) * math.sqrt(bpy)
               if len(downside) > 1 and downside.std() > 0 else 0.0)
    cummax = np.maximum.accumulate(eq)
    dd = (eq - cummax) / np.where(cummax > 0, cummax, 1.0)
    max_dd = abs(float(dd.min())) * 100 if len(dd) else 0.0
    base = {"total_return_pct": float(total_return), "sharpe": float(sharpe),
            "sortino": float(sortino), "max_drawdown_pct": float(max_dd)}
    if not trades:
        base.update({"profit_factor": 0.0, "win_rate": 0.0,
                     "expectancy_r": 0.0, "avg_trade_r": 0.0, "num_trades": 0})
        return base
    pnls = [float(t.get("pnl", 0)) for t in trades]
    rs = [float(t.get("r", 0)) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_wins = sum(wins)
    total_losses = abs(sum(losses))
    pf = ((total_wins / total_losses) if total_losses > 0
          else (10.0 if total_wins > 0 else 0.0))
    base.update({
        "profit_factor": float(min(pf, 10.0)),
        "win_rate": len(wins) / len(pnls) * 100,
        "expectancy_r": float(np.mean(rs)),
        "avg_trade_r": float(np.mean(rs)),
        "num_trades": len(trades)})
    return base


def monte_carlo_trades(trades: List[Dict[str, Any]], n_sims: int = 2000,
                       seed: int = 42) -> Optional[Dict[str, float]]:
    """Bootstrap (tirage avec remise) des rendements par trade : distribution
    du rendement final et du drawdown max, indépendante de l'ordre historique
    des trades (risque de séquence)."""
    rets = np.array([float(t.get("ret", 0.0)) for t in trades], dtype=float)
    if len(rets) < 10:
        return None
    rng = np.random.default_rng(seed)
    samples = rng.choice(rets, size=(n_sims, len(rets)), replace=True)
    paths = np.cumprod(1.0 + samples, axis=1)
    peaks = np.maximum.accumulate(np.concatenate(
        [np.ones((n_sims, 1)), paths], axis=1), axis=1)[:, 1:]
    max_dd = ((peaks - paths) / peaks).max(axis=1)
    final = paths[:, -1] - 1.0
    return {"ret_p5": float(np.percentile(final, 5) * 100),
            "ret_p50": float(np.percentile(final, 50) * 100),
            "ret_p95": float(np.percentile(final, 95) * 100),
            "dd_p50": float(np.percentile(max_dd, 50) * 100),
            "dd_p95": float(np.percentile(max_dd, 95) * 100),
            "prob_loss": float((final < 0).mean() * 100)}


def _empty_metrics() -> Dict[str, Any]:
    out = {k: 0.0 for k in ["total_return_pct", "sharpe", "sortino",
                             "max_drawdown_pct", "profit_factor", "win_rate",
                             "expectancy_r", "avg_trade_r"]}
    out["num_trades"] = 0
    return out


def format_report(m: Dict[str, Any]) -> str:
    return f"""
📊 PERFORMANCE
─────────────────────────────────
Rendement total     : {m['total_return_pct']:+.2f}%
Sharpe (annualisé)  : {m['sharpe']:.2f}
Sortino (annualisé) : {m['sortino']:.2f}
Max Drawdown        : {m['max_drawdown_pct']:.2f}%
Profit Factor       : {m['profit_factor']:.2f}
Win Rate            : {m['win_rate']:.1f}%
Expectancy          : {m['expectancy_r']:+.3f} R
Trades              : {m['num_trades']}
─────────────────────────────────
"""


@dataclass
class BacktestResult:
    trades: List[Dict[str, Any]] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    timestamps: List[int] = field(default_factory=list)
    initial_capital: float = 1000.0
    final_equity: float = 0.0
    total_return_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    avg_trade_r: float = 0.0
    num_trades: int = 0
    module_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tier_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)


def _series_to_list(s: Optional[Any], n: int) -> Optional[List[Any]]:
    if s is None:
        return None
    vals = list(s.values) if hasattr(s, "values") else list(s)
    if len(vals) != n:
        raise ValueError(f"Série de biais de longueur {len(vals)} ≠ {n}")
    return vals


class BacktestEngine:
    """Simulation barre à barre partageant les règles du live : biais
    HTF/BTC, disjoncteurs, cooldowns, désactivation de module, partielles,
    break-even, trailing, time-exit. Décisions prises à l'ouverture de la
    barre i avec l'information disponible jusqu'à la clôture de i-1."""

    def __init__(self, cfg: Config, initial_capital: float = 1000.0):
        self.cfg = cfg
        self.initial_capital = float(initial_capital)
        self.risk = RiskEngine(cfg, logger=None)
        bt_logger = logging.getLogger(f"{_LOG_ROOT}.backtest")
        bt_logger.addHandler(logging.NullHandler())
        bt_logger.propagate = False
        self.adaptive = AdaptiveEngine(cfg, bt_logger)
        self.slip = cfg.paper_slippage_pct
        self.fee = cfg.paper_fee_rate
        self.min_notional = 10.0
        self.tf_ms = _timeframe_ms(cfg.timeframe)

    def run(self, df: pd.DataFrame,
            htf_bias_series: Optional[Any] = None,
            btc_bias_series: Optional[Any] = None,
            btc_vol_mult_series: Optional[Any] = None,
            start: Optional[int] = None, end: Optional[int] = None,
            rows: Optional[List[Dict[str, Any]]] = None) -> BacktestResult:
        cfg = self.cfg
        if rows is None:
            if "atr" not in df.columns:
                df = compute_indicators(df, cfg)
            rows = df.to_dict("records")
        n = len(rows)
        warm = min_signal_bars(cfg)
        i0 = max(warm, int(start) if start is not None else warm)
        i1 = min(n, int(end) if end is not None else n)
        if i1 - i0 < 2:
            raise ValueError("Pas assez de données après warm-up.")
        htf = _series_to_list(htf_bias_series, n)
        btc = _series_to_list(btc_bias_series, n)
        vmul = _series_to_list(btc_vol_mult_series, n)
        ctx = BotContext.for_backtest(self.initial_capital)
        pf = ctx.portfolio
        trades: List[Dict[str, Any]] = []
        equity_curve = [self.initial_capital]
        timestamps = [int(rows[i0 - 1]["ts"])]
        for i in range(i0, i1):
            bar, prev, prev2 = rows[i], rows[i - 1], rows[i - 2]
            now = datetime.fromtimestamp(int(bar["ts"]) / 1000, tz=timezone.utc)
            if ctx.position.in_position:
                self._manage(ctx, bar, prev, now, trades)
            if cfg.adaptive_enabled:
                self.adaptive.update(ctx, prev)
            eq_open = pf.paper_cash + pf.paper_base * float(bar["open"])
            halted, _ = self.risk.check_circuit_breakers(
                ctx, eq_open, self.adaptive.effective_consec_pause(
                    ctx.adaptive, cfg.consec_loss_pause), now=now)
            if (not ctx.position.in_position and not halted
                    and not in_cooldown(ctx, now)
                    and not in_flash_cooldown(ctx, now)):
                j = i - 1 - cfg.flash_move_lookback
                flash = j >= 0 and detect_flash_move(
                    float(rows[j]["close"]), float(bar["open"]), ctx, cfg, now)
                if not flash:
                    sig = generate_signal_from_rows(
                        prev, prev2, i + 1,
                        htf[i] if htf is not None else "UP",
                        btc[i] if btc is not None else "UP",
                        ctx, cfg,
                        atr_min_pct=self.adaptive.effective_atr_min(
                            ctx.adaptive, cfg.atr_min_pct), now=now)
                    if sig.is_buy:
                        mult = float(vmul[i]) if vmul is not None \
                            and not _isnan(vmul[i]) else 1.0
                        self._open(ctx, sig, bar, prev, now, eq_open, mult)
                        if ctx.position.in_position:
                            self._intrabar(ctx, bar, now, trades,
                                           check_gap=False)
                            if ctx.position.in_position:
                                ctx.position.high_since_entry = max(
                                    float(ctx.position.high_since_entry),
                                    float(bar["high"]))
            equity_curve.append(pf.paper_cash + pf.paper_base * float(bar["close"]))
            timestamps.append(int(bar["ts"]))
        if ctx.position.in_position:
            last = rows[i1 - 1]
            self._exit(ctx, float(last["close"]) * (1 - self.slip),
                       ctx.position.amount_held, "END_OF_DATA",
                       datetime.fromtimestamp(int(last["ts"]) / 1000,
                                              tz=timezone.utc), trades)
            equity_curve[-1] = pf.paper_cash + pf.paper_base * float(last["close"])
        return self._build_result(ctx, trades, equity_curve, timestamps)

    # ---------- Entrée ----------

    def _open(self, ctx: BotContext, sig: Signal, bar: Dict[str, Any],
              signal_row: Dict[str, Any], now: datetime, equity: float,
              btc_vol_mult: float) -> None:
        cfg = self.cfg
        pf = ctx.portfolio
        entry_px = float(bar["open"]) * (1 + self.slip)
        sl_dist = self.risk.sl_distance(sig.module, float(signal_row["atr"]),
                                        entry_px, signal_row, cfg)
        if sl_dist is None:
            return
        risk_pct = self.risk.base_risk(ctx, sig.module,
                                       signal_row.get("realized_vol"))
        eff = min(risk_pct * cfg.tier_mult_map.get(sig.tier, 1.0)
                  * self.risk.regime_multiplier(sig.regime)
                  * self.risk.dd_derisk_multiplier(ctx, equity)
                  * btc_vol_mult, cfg.risk_absolute_max_pct)
        if eff <= 0:
            return
        max_notional = pf.paper_cash * (1 - cfg.min_cash_reserve_pct)
        amount = self.risk.position_size(entry_px, sl_dist, equity, eff,
                                         max_notional)
        if amount * entry_px < self.min_notional:
            return
        cost = amount * entry_px
        fee = cost * self.fee
        if cost + fee > pf.paper_cash:
            amount = pf.paper_cash / (entry_px * (1 + self.fee)) * 0.999
            cost = amount * entry_px
            fee = cost * self.fee
            if amount * entry_px < self.min_notional:
                return
        pf.paper_cash -= cost + fee
        pf.paper_base += amount
        cost_basis = (cost + fee) / amount
        sl = entry_px - sl_dist
        rr = RR_BY_MODULE.get(sig.module, 2.0)
        risk_quote = amount * (cost_basis - sl * (1 - self.fee))
        adx = signal_row.get("adx")
        ctx.position = Position(
            in_position=True, buy_price=entry_px, cost_basis=cost_basis,
            sl_price=sl, tp_price=entry_px + sl_dist * rr, amount_held=amount,
            initial_amount=amount, risk_per_unit=sl_dist,
            risk_quote_initial=max(risk_quote, amount * sl_dist * 0.5),
            rr_used=rr, module=sig.module, regime=sig.regime, tier=sig.tier,
            entry_score=sig.score,
            entry_adx=None if _isnan(adx) else float(adx),
            opened_at=now.isoformat(), entry_timestamp_ms=int(bar["ts"]),
            entry_candle_ts=int(bar["ts"]), sl_update_candle_ts=int(bar["ts"]),
            high_since_entry=entry_px, low_since_sl_update=entry_px,
            eff_risk_pct=eff * 100, entry_equity=equity)
        ctx.state = BotState.OPEN.value

    # ---------- Gestion ----------

    def _manage(self, ctx: BotContext, bar: Dict[str, Any],
                prev: Dict[str, Any], now: datetime,
                trades: List[Dict[str, Any]]) -> None:
        self._update_stops(ctx, prev, float(bar["open"]))
        self._intrabar(ctx, bar, now, trades, check_gap=True)
        p = ctx.position
        if not p.in_position:
            return
        close_ms = int(bar["ts"]) + self.tf_ms
        age_h = (close_ms - int(p.entry_timestamp_ms or bar["ts"])) / 3_600_000
        if (age_h >= self.cfg.max_trade_age_hours and not p.break_even_done
                and float(bar["close"]) < p.buy_price
                + p.risk_per_unit * self.cfg.time_exit_min_r_mult):
            self._exit(ctx, float(bar["close"]) * (1 - self.slip),
                       p.amount_held, "BARRIER_TIME", now, trades)
            return
        p.high_since_entry = max(float(p.high_since_entry), float(bar["high"]))

    def _update_stops(self, ctx: BotContext, prev: Dict[str, Any],
                      bar_open: float) -> None:
        cfg = self.cfg
        p = ctx.position
        if (not p.break_even_done and float(p.high_since_entry or 0)
                >= p.buy_price * (1 + cfg.break_even_trigger)):
            be = break_even_stop(p, cfg)
            if be <= p.sl_price:
                p.break_even_done = True
            elif be < bar_open * 0.999:
                p.sl_price = be
                p.break_even_done = True
        if (cfg.trailing_enabled and p.break_even_done
                and float(p.high_since_entry or 0) > p.buy_price + p.risk_per_unit):
            ts = trailing_stop(p, prev.get("atr"), cfg)
            if (ts is not None and ts > p.sl_price * (1 + cfg.trailing_min_raise_pct)
                    and ts < bar_open * 0.999):
                p.sl_price = ts

    def _intrabar(self, ctx: BotContext, bar: Dict[str, Any], now: datetime,
                  trades: List[Dict[str, Any]], check_gap: bool) -> None:
        cfg = self.cfg
        p = ctx.position
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if check_gap and o <= p.sl_price:
            self._exit(ctx, o * (1 - self.slip), p.amount_held, "BARRIER_SL",
                       now, trades)
            return
        if check_gap and o >= p.tp_price:
            self._exit(ctx, p.tp_price, p.amount_held, "BARRIER_TP", now,
                       trades, maker=True)
            return
        mode = cfg.intrabar_partial_mode
        if mode == "conservative":
            sl_first, max_partials = True, 1
        elif mode == "optimistic":
            sl_first, max_partials = False, 2
        else:
            # Règle OHLC : l'extrême le plus proche de l'ouverture est
            # atteint en premier.
            sl_first, max_partials = (o - l) <= (h - o), 2

        def down_leg() -> None:
            if ctx.position.in_position and l <= ctx.position.sl_price:
                self._exit(ctx, ctx.position.sl_price * (1 - self.slip),
                           ctx.position.amount_held, "BARRIER_SL", now, trades)

        def up_leg() -> None:
            done = 0
            levels = [(cfg.partial_exit_r1, cfg.partial_exit_pct1),
                      (cfg.partial_exit_r2, cfg.partial_exit_pct2)]
            while (cfg.partial_exit_enabled and ctx.position.in_position
                   and ctx.position.partial_exit_count < 2
                   and done < max_partials):
                q = ctx.position
                r_level, pct = levels[q.partial_exit_count]
                target = q.buy_price + q.risk_per_unit * r_level
                if h < target:
                    break
                qty = min(q.initial_amount * pct, q.amount_held)
                if (qty * target < self.min_notional
                        or (q.amount_held - qty) * target
                        < self.min_notional * cfg.min_remaining_notional_mult):
                    q.partial_exit_count += 1
                    continue
                self._exit(ctx, target * (1 - self.slip), qty,
                           f"R{q.partial_exit_count + 1}", now, trades)
                done += 1
            if ctx.position.in_position and h >= ctx.position.tp_price:
                self._exit(ctx, ctx.position.tp_price, ctx.position.amount_held,
                           "BARRIER_TP", now, trades, maker=True)

        if sl_first:
            down_leg()
            up_leg()
        else:
            up_leg()
            down_leg()

    def _exit(self, ctx: BotContext, price: float, qty: float, reason: str,
              now: datetime, trades: List[Dict[str, Any]],
              maker: bool = False) -> None:
        p = ctx.position
        pf = ctx.portfolio
        if not p.in_position or qty <= 0 or price <= 0:
            return
        qty = min(qty, p.amount_held)
        if p.amount_held - qty > 0 and (p.amount_held - qty) * price \
                < self.min_notional:
            qty = p.amount_held
        proceeds = qty * price
        fee = proceeds * self.fee
        pf.paper_cash += proceeds - fee
        pf.paper_base = max(0.0, pf.paper_base - qty)
        pnl = proceeds - fee - qty * p.cost_basis
        p.realized_pnl += pnl
        p.amount_held = max(0.0, p.amount_held - qty)
        if reason in ("R1", "R2"):
            p.partial_exit_count += 1
        p.legs.append({"ts": now.isoformat(), "reason": reason, "qty": qty,
                       "price": price, "pnl": pnl})
        if p.amount_held > 1e-12:
            return
        pf.paper_base = 0.0 if pf.paper_base < 1e-9 else pf.paper_base
        total_r = p.realized_pnl / p.risk_quote_initial \
            if p.risk_quote_initial > 0 else 0.0
        trade = {"ts": now.isoformat(), "pnl": p.realized_pnl, "r": total_r,
                 "module": p.module, "tier": p.tier, "reason": reason,
                 "entry_adx": p.entry_adx, "risk_quote": p.risk_quote_initial,
                 "entry_price": p.buy_price, "exit_price": price,
                 "entry_ts": p.entry_timestamp_ms, "legs": len(p.legs),
                 "ret": (p.realized_pnl / p.entry_equity
                         if p.entry_equity > 0 else 0.0)}
        record_closed_trade(ctx, self.cfg, self.risk, trade, now)
        trades.append(trade)
        ctx.position = Position()
        ctx.state = BotState.FLAT.value

    def _build_result(self, ctx: BotContext, trades: List[Dict[str, Any]],
                      equity_curve: List[float],
                      timestamps: List[int]) -> BacktestResult:
        m = compute_metrics(equity_curve, trades, timeframe=self.cfg.timeframe)
        return BacktestResult(
            trades=trades, equity_curve=equity_curve, timestamps=timestamps,
            initial_capital=self.initial_capital,
            final_equity=equity_curve[-1] if equity_curve else self.initial_capital,
            module_stats=ctx.portfolio.per_module,
            tier_stats=ctx.portfolio.per_tier, **m)


# ---------- Séries de biais sans look-ahead ----------

def build_bias_series(ltf_ts: pd.Series, htf: pd.DataFrame, ema_span: int,
                      up_buf: float, down_buf: float,
                      htf_timeframe: str) -> pd.Series:
    """Biais HTF connu à l'OUVERTURE de chaque barre LTF : seules les barres
    HTF clôturées (ts + durée <= ts LTF) sont utilisées."""
    h = htf[["ts", "close"]].copy().sort_values("ts").reset_index(drop=True)
    e = h["close"].astype(float).ewm(span=ema_span, adjust=False).mean()
    close = h["close"].astype(float)
    bias = np.where(close > e * (1 + up_buf), "UP",
                    np.where(close < e * (1 - down_buf), "DOWN", ""))
    right = pd.DataFrame({
        "avail_ts": (h["ts"].astype("int64") + _timeframe_ms(htf_timeframe)),
        "bias": bias}).sort_values("avail_ts")
    left = pd.DataFrame({"ts": ltf_ts.astype("int64").values,
                         "_i": np.arange(len(ltf_ts))}).sort_values("ts")
    m = pd.merge_asof(left, right, left_on="ts", right_on="avail_ts",
                      direction="backward").sort_values("_i")
    vals = [v if v in ("UP", "DOWN") else None for v in m["bias"].tolist()]
    return pd.Series(vals, index=ltf_ts.index, dtype=object)


def build_btc_vol_mult_series(ltf_ts: pd.Series, btc_1h: pd.DataFrame,
                              cfg: Config) -> pd.Series:
    b = btc_1h[["ts", "close"]].copy().sort_values("ts").reset_index(drop=True)
    ret = np.log(b["close"].astype(float) / b["close"].astype(float).shift(1))
    vol = ret.rolling(cfg.btc_vol_window_hours).std() * math.sqrt(24 * 365)
    right = pd.DataFrame({"avail_ts": b["ts"].astype("int64") + 3_600_000,
                          "vol": vol}).sort_values("avail_ts")
    left = pd.DataFrame({"ts": ltf_ts.astype("int64").values,
                         "_i": np.arange(len(ltf_ts))}).sort_values("ts")
    m = pd.merge_asof(left, right, left_on="ts", right_on="avail_ts",
                      direction="backward").sort_values("_i")
    mult = [_btc_vol_mult(None if _isnan(v) else float(v), cfg)
            for v in m["vol"].tolist()]
    return pd.Series(mult, index=ltf_ts.index, dtype=float)


def _btc_vol_mult(vol: Optional[float], cfg: Config) -> float:
    if vol is None:
        return 1.0
    if vol >= cfg.btc_vol_threshold_annual:
        return cfg.btc_vol_size_reduction
    half = cfg.btc_vol_threshold_annual * 0.5
    if vol <= half:
        return 1.0
    ratio = (vol - half) / (cfg.btc_vol_threshold_annual - half)
    return 1.0 - ratio * (1.0 - cfg.btc_vol_size_reduction)


# ---------- Walk-forward / sensibilité ----------

INDICATOR_PARAMS = frozenset({
    "trend_ema", "fast_ema", "slow_ema", "atr_period", "rsi_period",
    "macd_fast", "macd_slow", "macd_signal", "bb_period", "bb_std",
    "adx_period", "vol_ma_period", "vwap_period", "obv_ema_span",
    "swing_window", "atr_rank_window", "timeframe"})

DEFAULT_WF_GRID: Dict[str, List[Any]] = {
    "adx_trend_threshold": [20.0, 22.0, 25.0],
    "trail_atr_mult": [1.5, 2.0, 2.5],
}


@dataclass
class WalkForwardWindow:
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    is_sharpe: float
    oos_sharpe: float
    is_pf: float
    oos_pf: float
    is_wr: float
    oos_wr: float
    is_trades: int
    oos_trades: int
    degradation: float
    params: Dict[str, Any] = field(default_factory=dict)


def walk_forward(df: pd.DataFrame, cfg: Config, is_months: int = 6,
                 oos_months: int = 3, step_months: int = 2,
                 initial_capital: float = 1000.0,
                 param_grid: Optional[Dict[str, List[Any]]] = None,
                 series: Optional[Dict[str, Any]] = None,
                 min_is_trades: int = 10) -> List[WalkForwardWindow]:
    """Walk-forward ANCRÉ sur l'historique complet (pas de perte de
    warm-up) : optimisation in-sample sur la grille, puis évaluation
    out-of-sample avec les paramètres retenus."""
    grid = param_grid or DEFAULT_WF_GRID
    bad = set(grid) & INDICATOR_PARAMS
    if bad:
        raise ValueError(f"Paramètres d'indicateurs non supportés en WF: {bad}")
    df = df.reset_index(drop=True)
    if "atr" not in df.columns:
        df = compute_indicators(df, cfg)
    dt = pd.to_datetime(df["ts"], unit="ms", utc=True)
    rows = df.to_dict("records")
    series = series or {}
    keys = list(grid)
    combos = list(itertools.product(*[grid[k] for k in keys]))
    warm = min_signal_bars(cfg)
    if len(df) <= warm + 10:
        return []
    t = dt.iloc[warm]
    end = dt.iloc[-1]
    results: List[WalkForwardWindow] = []
    while True:
        is_start = t
        is_end = is_start + pd.DateOffset(months=is_months)
        oos_end = is_end + pd.DateOffset(months=oos_months)
        if oos_end > end:
            break
        a = int(dt.searchsorted(is_start))
        b = int(dt.searchsorted(is_end))
        c = int(dt.searchsorted(oos_end))
        t = t + pd.DateOffset(months=step_months)
        if b - a < 200 or c - b < 50:
            continue
        best: Optional[Tuple[float, Tuple[Any, ...], BacktestResult]] = None
        for combo in combos:
            ccfg = dataclasses.replace(cfg, **dict(zip(keys, combo)))
            res = BacktestEngine(ccfg, initial_capital).run(
                df, start=a, end=b, rows=rows, **series)
            score = res.sharpe if res.num_trades >= min_is_trades else -math.inf
            if best is None or score > best[0]:
                best = (score, combo, res)
        assert best is not None
        params = dict(zip(keys, best[1]))
        res_is = best[2]
        res_oos = BacktestEngine(dataclasses.replace(cfg, **params),
                                 initial_capital).run(
            df, start=b, end=c, rows=rows, **series)
        deg = (res_oos.sharpe / res_is.sharpe) if res_is.sharpe > 0 else 0.0
        results.append(WalkForwardWindow(
            is_start=is_start.isoformat(), is_end=is_end.isoformat(),
            oos_start=is_end.isoformat(), oos_end=oos_end.isoformat(),
            is_sharpe=res_is.sharpe, oos_sharpe=res_oos.sharpe,
            is_pf=res_is.profit_factor, oos_pf=res_oos.profit_factor,
            is_wr=res_is.win_rate, oos_wr=res_oos.win_rate,
            is_trades=res_is.num_trades, oos_trades=res_oos.num_trades,
            degradation=deg, params=params))
    return results


def walkforward_verdict(windows: List[WalkForwardWindow]) -> str:
    if not windows:
        return "❌ PAS DE DONNÉES"
    degs = [w.degradation for w in windows if w.is_sharpe > 0.3]
    if not degs:
        return "❌ IS SHARPE TROP FAIBLE"
    median_deg = statistics.median(degs)
    pct_bad = sum(1 for d in degs if d < 0.5) / len(degs)
    oos_pos = sum(1 for w in windows if w.oos_sharpe > 0) / len(windows)
    if median_deg >= 0.7 and pct_bad <= 0.2 and oos_pos >= 0.6:
        return f"✅ ROBUSTE (dégradation médiane {median_deg:.2f})"
    if median_deg >= 0.5 and pct_bad <= 0.4:
        return f"⚠️ LIMITE (dégradation médiane {median_deg:.2f})"
    return f"❌ OVERFITTÉE (dégradation médiane {median_deg:.2f})"


def format_walkforward_report(windows: List[WalkForwardWindow]) -> str:
    if not windows:
        return "Aucune fenêtre valide."
    lines = ["Walk-forward (optimisation IS → validation OOS):", "─" * 100]
    lines.append(f"{'IS start':<12} {'IS Sharpe':>10} {'OOS Sharpe':>11} "
                 f"{'Dégrad':>8} {'IS PF':>7} {'OOS PF':>7} {'OOS WR':>7} "
                 f"{'OOS N':>6}  params")
    for w in windows:
        lines.append(
            f"{w.is_start[:10]:<12} {w.is_sharpe:>10.2f} "
            f"{w.oos_sharpe:>11.2f} {w.degradation:>8.2f} "
            f"{w.is_pf:>7.2f} {w.oos_pf:>7.2f} {w.oos_wr:>6.1f}% "
            f"{w.oos_trades:>6}  {w.params}")
    lines.append("─" * 100)
    lines.append(walkforward_verdict(windows))
    return "\n".join(lines)


def run_sensitivity(df: pd.DataFrame, base_cfg: Config,
                    param_grid: Dict[str, List[Any]],
                    series: Optional[Dict[str, Any]] = None,
                    start: Optional[int] = None) -> pd.DataFrame:
    series = series or {}
    raw_cols = ["ts", "open", "high", "low", "close", "volume"]
    base_df = df if "atr" in df.columns else compute_indicators(df, base_cfg)
    base_rows = base_df.to_dict("records")
    results = []
    for param_name, values in param_grid.items():
        for val in values:
            try:
                cfg = dataclasses.replace(base_cfg, **{param_name: val})
                if param_name in INDICATOR_PARAMS:
                    d = compute_indicators(df[raw_cols], cfg)
                    res = BacktestEngine(cfg).run(d, start=start, **series)
                else:
                    res = BacktestEngine(cfg).run(base_df, start=start,
                                                  rows=base_rows, **series)
                results.append({"param": param_name, "value": val,
                                "sharpe": res.sharpe, "pf": res.profit_factor,
                                "wr": res.win_rate, "trades": res.num_trades,
                                "max_dd": res.max_drawdown_pct})
            except Exception as e:
                results.append({"param": param_name, "value": val,
                                "sharpe": 0.0, "pf": 0.0, "wr": 0.0,
                                "trades": 0, "max_dd": 0.0, "error": str(e)})
    return pd.DataFrame(results)


def report_sensitivity(results: pd.DataFrame) -> str:
    lines = ["Analyse de sensibilité", "=" * 70]
    for param, group in results.groupby("param"):
        lines.append(f"\n{param}:")
        for _, row in group.iterrows():
            marker = "⚠️" if row["trades"] < 20 else "  "
            err = f" ERREUR: {row['error']}" if "error" in row and \
                isinstance(row.get("error"), str) else ""
            lines.append(f"  {marker} {str(row['value']):<12} → "
                         f"Sharpe={row['sharpe']:.2f} PF={row['pf']:.2f} "
                         f"WR={row['wr']:.1f}% trades={int(row['trades'])}{err}")
        sharpe_std = group["sharpe"].std()
        if (group["sharpe"] <= 0).all():
            lines.append("  → 🔴 AUCUN EDGE : Sharpe ≤ 0 pour toutes les valeurs "
                         "(stabilité ≠ rentabilité)")
        elif sharpe_std > 0.5:
            lines.append(f"  → 🔴 CRITIQUE : Sharpe varie de {sharpe_std:.2f}")
        elif sharpe_std > 0.2:
            lines.append(f"  → 🟡 MODÉRÉ : Sharpe varie de {sharpe_std:.2f}")
        else:
            lines.append("  → 🟢 STABLE")
    return "\n".join(lines)
