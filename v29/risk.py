"""Risque : taille des positions, disjoncteurs, stops.

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .config import Config
from .models import BotContext, HaltKind, Position, clear_halt, halt_ctx
from .utils import _day_key, _isnan, _parse_iso, _row_get, _timeframe_ms, _utcnow, _week_key

RR_BY_MODULE = {"trend": 2.5, "pullback": 2.0, "breakout": 2.0, "range": 1.5}


class RiskEngine:
    def __init__(self, cfg: Config, logger: Optional[logging.Logger] = None):
        self.cfg = cfg
        self.logger = logger or logging.getLogger("risk.null")
        if logger is None and not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())

    @staticmethod
    def _extract_r_multiples(trades: List[Dict[str, Any]]) -> List[float]:
        rs = []
        for t in trades:
            try:
                if t.get("r") is not None:
                    rs.append(float(t["r"]))
                    continue
            except Exception:
                pass
            rq = float(t.get("risk_quote", 0) or 0)
            pnl = float(t.get("pnl", 0) or 0)
            if rq > 1e-12:
                rs.append(pnl / rq)
        return rs

    def kelly_base(self, ctx: BotContext, module: str) -> float:
        trades = [t for t in ctx.portfolio.last_trades
                  if t.get("module") == module][-self.cfg.kelly_window:]
        rs = self._extract_r_multiples(trades)
        if len(rs) < self.cfg.kelly_min_trades:
            return self.cfg.risk_base_pct
        wins = [x for x in rs if x > 0]
        losses = [abs(x) for x in rs if x <= 0]
        if not losses:
            return self.cfg.risk_max_pct
        if not wins:
            return self.cfg.risk_min_pct
        w = len(wins) / len(rs)
        aw = sum(wins) / len(wins)
        al = sum(losses) / len(losses)
        if al <= 1e-12:
            return self.cfg.risk_base_pct
        rr = aw / al
        f = w - (1 - w) / rr
        # Kelly en fraction du risque : borné ensuite par risk_min/max.
        return max(0.0, f * self.cfg.kelly_fraction)

    def vol_target_base(self, realized_vol: Optional[float]) -> float:
        if realized_vol is None or _isnan(realized_vol) or realized_vol <= 1e-6:
            return self.cfg.risk_base_pct
        return self.cfg.risk_base_pct * (self.cfg.vol_target_annual
                                         / float(realized_vol))

    def regime_multiplier(self, regime: str) -> float:
        return self.cfg.regime_mult_map.get(regime, 0.5)

    def dd_derisk_multiplier(self, ctx: BotContext,
                             equity: Optional[float]) -> float:
        ref = float(ctx.risk.daily_start_equity or 0)
        if equity is None or ref <= 0:
            return 1.0
        dd = max(0.0, (ref - equity) / ref)
        curve = sorted(self.cfg.dd_derisk_curve, key=lambda x: x[0])
        for threshold, mult in curve:
            if dd <= threshold:
                return mult
        return curve[-1][1] if curve else 1.0

    def signal_freshness_mult(self, closed_ts: int,
                              max_age_min: Optional[int] = None,
                              now_ms: Optional[int] = None
                              ) -> Tuple[float, Optional[str]]:
        max_age = (max_age_min if max_age_min is not None
                   else self.cfg.max_signal_age_min)
        candle_close_ts = int(closed_ts) + _timeframe_ms(self.cfg.timeframe)
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        age_min = max(0.0, (now_ms - candle_close_ts) / 60_000)
        if age_min > max_age:
            return 0.0, f"signal_stale_{age_min:.0f}min"
        ratio = age_min / max(max_age, 1)
        return max(0.5, 1.0 - self.cfg.signal_age_decay_pct * ratio), None

    def module_sharpe(self, ctx: BotContext,
                      module: str) -> Tuple[float, int]:
        trades = [t for t in ctx.portfolio.last_trades
                  if t.get("module") == module][-self.cfg.module_sharpe_window:]
        rs = self._extract_r_multiples(trades)
        if len(rs) < self.cfg.module_sharpe_min_trades:
            return 0.0, len(rs)
        arr = np.array(rs, dtype=float)
        if arr.std() <= 1e-9:
            return 0.0, len(rs)
        return float(arr.mean() / arr.std()), len(rs)

    def base_risk(self, ctx: BotContext, module: str,
                  realized_vol: Optional[float] = None) -> float:
        mode = self.cfg.sizing_mode
        if mode == "kelly":
            base = self.kelly_base(ctx, module)
        elif mode == "vol_target":
            base = self.vol_target_base(realized_vol)
        else:
            recent = [t for t in ctx.portfolio.last_trades
                      if t.get("module") == module][-20:]
            if len(recent) < 8:
                base = self.cfg.risk_base_pct
            else:
                wr = sum(1 for t in recent
                         if float(t.get("pnl", 0) or 0) > 0) / len(recent)
                base = (self.cfg.risk_max_pct if wr >= 0.55
                        else self.cfg.risk_min_pct if wr < 0.40
                        else self.cfg.risk_base_pct)
        trades = [t for t in ctx.portfolio.last_trades
                  if t.get("module") == module][-30:]
        if len(trades) >= 5:
            rs = self._extract_r_multiples(trades)
            if rs:
                exp_r = float(np.mean(rs))
                wins = [x for x in rs if x > 0]
                losses = [x for x in rs if x <= 0]
                total_losses_abs = abs(sum(losses))
                if total_losses_abs > 0:
                    pf = sum(wins) / total_losses_abs
                elif wins:
                    pf = 10.0
                else:
                    pf = 0.0
                base *= 1.0 + max(-0.35, min(0.20, exp_r * 0.35))
                if pf < 1.0:
                    base *= 0.70
                elif pf > 1.50:
                    base *= 1.05
        cl = int(ctx.portfolio.consec_losses or 0)
        if cl >= 2:
            base *= 0.75
        if cl >= 3:
            base *= 0.60
        if cl >= 4:
            base *= 0.50
        return max(self.cfg.risk_min_pct,
                   min(self.cfg.risk_max_pct,
                       self.cfg.risk_absolute_max_pct, base))

    def position_size(self, price: float, sl_dist: float, equity: float,
                      risk_pct: float,
                      max_notional: Optional[float] = None) -> float:
        """Quantité telle que la perte au stop (frais + slippage inclus)
        vaille equity × risk_pct, plafonnée par le notionnel disponible."""
        if price <= 0 or sl_dist <= 0 or equity <= 0:
            return 0.0
        eff = min(max(risk_pct, 0.0), self.cfg.risk_absolute_max_pct)
        risk_amount = equity * eff
        fee_buf = (price * self.cfg.fee_rate
                   + (price - sl_dist) * self.cfg.fee_rate)
        slip_buf = price * (self.cfg.entry_slippage_buffer_pct
                            + self.cfg.exit_slippage_buffer_pct)
        unit_risk = sl_dist + fee_buf + slip_buf
        if unit_risk <= 0:
            return 0.0
        raw = risk_amount / unit_risk
        cap_notional = equity * self.cfg.max_position_capital_pct
        if max_notional is not None:
            cap_notional = min(cap_notional, max_notional)
        return max(0.0, min(raw, cap_notional / price))

    def sl_distance(self, module: str, atr_val: float, price: float,
                    closed: Any, cfg: Config) -> Optional[float]:
        if _isnan(atr_val) or atr_val <= 0 or price <= 0:
            return None
        profiles = {
            "trend": {"sl_mult": 1.5, "use_swing": True},
            "pullback": {"sl_mult": 1.2, "use_swing": False},
            "breakout": {"sl_mult": 1.8, "use_swing": True},
            "range": {"sl_mult": 1.0, "use_swing": False},
            "recovered": {"sl_mult": 1.5, "use_swing": False},
        }
        prof = profiles.get(module, profiles["trend"])
        atr_sl = atr_val * prof["sl_mult"]
        if prof["use_swing"]:
            sw = _row_get(closed, "last_swing_low")
            if not _isnan(sw):
                d = price - float(sw)
                if 0 < d < price * cfg.max_sl_dist_pct:
                    atr_sl = max(atr_sl, d + atr_val * 0.3)
        return max(min(atr_sl, price * cfg.max_sl_dist_pct), price * 0.001)

    def check_circuit_breakers(self, ctx: BotContext, equity: Optional[float],
                               consec_pause: Optional[int] = None,
                               now: Optional[datetime] = None
                               ) -> Tuple[bool, Optional[str]]:
        """Retourne (bloque_les_entrées, raison). Fail-closed : une equity
        illisible bloque les entrées (sans halt)."""
        n = now or _utcnow()
        day, week = _day_key(n), _week_key(n)
        r = ctx.risk
        if r.halted and r.halt_kind == HaltKind.DAILY_DD and r.halted_date != day:
            clear_halt(ctx)
        if r.halted and r.halt_kind == HaltKind.WEEKLY_DD \
                and r.halted_week != week:
            clear_halt(ctx)
        if r.halted:
            return True, r.halt_reason or "HALTED"
        if r.paused_until:
            pu = _parse_iso(r.paused_until)
            if pu is not None and n < pu:
                return True, r.cooldown_reason or "PAUSED"
            r.paused_until = None
            r.cooldown_reason = None
        if equity is None or equity <= 0:
            return True, "EQUITY_UNAVAILABLE"
        if r.daily_start_date != day or not r.daily_start_equity:
            r.daily_start_date = day
            r.daily_start_equity = equity
        if r.weekly_start_date != week or not r.weekly_start_equity:
            r.weekly_start_date = week
            r.weekly_start_equity = equity
        d_eq = float(r.daily_start_equity or 0)
        w_eq = float(r.weekly_start_equity or 0)
        if d_eq > 0 and equity <= d_eq * (1 - self.cfg.max_daily_dd):
            reason = f"Daily DD dépassé ({(equity/d_eq-1)*100:.2f}%)"
            halt_ctx(ctx, reason, HaltKind.DAILY_DD, now=n)
            return True, reason
        if w_eq > 0 and equity <= w_eq * (1 - self.cfg.max_weekly_dd):
            reason = f"Weekly DD dépassé ({(equity/w_eq-1)*100:.2f}%)"
            halt_ctx(ctx, reason, HaltKind.WEEKLY_DD, now=n)
            return True, reason
        threshold = (consec_pause if consec_pause is not None
                     else self.cfg.consec_loss_pause)
        if ctx.portfolio.losses_since_pause >= threshold:
            r.paused_until = (n + timedelta(
                hours=self.cfg.consec_loss_pause_hours)).isoformat()
            r.cooldown_reason = (f"{ctx.portfolio.losses_since_pause} "
                                 f"pertes consécutives")
            # La pause « consomme » la série : pas de ré-armement infini.
            ctx.portfolio.losses_since_pause = 0
            return True, r.cooldown_reason
        return False, None


def in_cooldown(ctx: BotContext, now: Optional[datetime] = None) -> bool:
    until = _parse_iso(ctx.risk.cooldown_until)
    if until is None:
        ctx.risk.cooldown_until = None
        return False
    if (now or _utcnow()) < until:
        return True
    ctx.risk.cooldown_until = None
    ctx.risk.cooldown_reason = None
    return False


def in_flash_cooldown(ctx: BotContext, now: Optional[datetime] = None) -> bool:
    until = _parse_iso(ctx.risk.flash_cooldown_until)
    if until is None:
        ctx.risk.flash_cooldown_until = None
        return False
    if (now or _utcnow()) < until:
        return True
    ctx.risk.flash_cooldown_until = None
    return False


def detect_flash_move(ref_close: float, price: float, ctx: BotContext,
                      cfg: Config, now: Optional[datetime] = None) -> bool:
    if ref_close <= 0 or price <= 0:
        return False
    if abs(price / ref_close - 1) >= cfg.flash_move_pct:
        ctx.risk.flash_cooldown_until = (
            (now or _utcnow()) + timedelta(minutes=cfg.flash_cooldown_min)
        ).isoformat()
        return True
    return False


def record_closed_trade(ctx: BotContext, cfg: Config, risk: RiskEngine,
                        trade: Dict[str, Any], now: Optional[datetime] = None,
                        logger: Optional[logging.Logger] = None) -> None:
    """Statistiques communes live / paper / backtest pour un trade CLOS
    (toutes jambes confondues). trade: pnl, r, module, tier, reason,
    entry_adx, risk_quote, entry_price, exit_price."""
    n = now or _utcnow()
    pf = ctx.portfolio
    pnl = float(trade.get("pnl", 0.0))
    if pnl > 0:
        pf.stats_wins += 1
        pf.consec_losses = 0
        pf.losses_since_pause = 0
    else:
        pf.stats_losses += 1
        pf.consec_losses += 1
        pf.losses_since_pause += 1
    pf.stats_total_pnl += pnl
    entry = dict(trade)
    entry.setdefault("ts", n.isoformat())
    pf.last_trades.append(entry)
    by_module: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in pf.last_trades:
        by_module[t.get("module") or "_orphan"].append(t)
    capped: List[Dict[str, Any]] = []
    for trades in by_module.values():
        capped.extend(trades[-cfg.last_trades_per_module:])
    capped.sort(key=lambda x: x.get("ts") or "")
    pf.last_trades = capped
    module = trade.get("module") or ""
    if module:
        info = pf.per_module.setdefault(
            module, {"trades": 0, "wins": 0, "pnl": 0.0,
                     "disabled_until": None})
        info["trades"] += 1
        info["wins"] += int(pnl > 0)
        info["pnl"] += pnl
        sharpe, cnt = risk.module_sharpe(ctx, module)
        if cnt >= cfg.module_sharpe_min_trades:
            info["sharpe"] = sharpe
            if sharpe < cfg.module_min_sharpe and not info.get("disabled_until"):
                info["disabled_until"] = (
                    n + timedelta(hours=cfg.module_reactivation_hours)
                ).isoformat()
                if logger:
                    logger.warning(
                        f"[MODULE] {module} désactivé: Sharpe={sharpe:.2f}")
    tier = trade.get("tier") or ""
    if tier:
        ti = pf.per_tier.setdefault(tier, {"trades": 0, "wins": 0, "pnl": 0.0})
        ti["trades"] += 1
        ti["wins"] += int(pnl > 0)
        ti["pnl"] += pnl
    reason = str(trade.get("reason") or "")
    entry_adx = trade.get("entry_adx")
    if pnl < 0 and "BARRIER" in reason:
        if entry_adx is None or _isnan(entry_adx) \
                or float(entry_adx) < cfg.post_loss_skip_adx:
            ctx.risk.cooldown_until = (
                n + timedelta(hours=cfg.post_loss_cooldown_hours)).isoformat()
            ctx.risk.cooldown_reason = f"Post-loss ({reason})"


def break_even_stop(p: Position, cfg: Config) -> float:
    """Stop de break-even couvrant réellement le coût de revient :
    une sortie au stop (frais + slippage) ne doit pas être une perte."""
    net_be = p.cost_basis / max(1e-9, 1 - cfg.fee_rate
                                - cfg.exit_slippage_buffer_pct)
    return max(p.buy_price * (1 + cfg.break_even_offset), net_be)


def trailing_stop(p: Position, atr: float, cfg: Config) -> Optional[float]:
    if _isnan(atr) or atr <= 0 or p.high_since_entry is None:
        return None
    return float(p.high_since_entry) - cfg.trail_atr_mult * float(atr)
