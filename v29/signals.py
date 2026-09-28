"""Signaux d'entrée du bot V29.

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .utils import _parse_iso, _utcnow
from .config import Config
from .models import BotContext, Signal
from .adaptive import _isnan, _row_get


_SIGNAL_REQUIRED = ("ema_fast", "ema_slow", "ema_trend", "atr", "atr_pct",
                    "adx", "plus_di", "minus_di", "bb_width", "bb_width_avg",
                    "bb_upper", "bb_lower", "rsi", "macd", "macd_signal",
                    "macd_hist", "obv_slope", "ema_trend_slope")


def detect_regime(c: Any, cfg: Config) -> str:
    if any(_isnan(_row_get(c, k)) for k in ("adx", "bb_width", "bb_width_avg",
                                            "plus_di", "minus_di")):
        return "UNCLEAR"
    if float(c["adx"]) >= cfg.adx_trend_threshold:
        if c["plus_di"] > c["minus_di"]:
            return "TREND_UP"
        if c["minus_di"] > c["plus_di"]:
            return "TREND_DOWN"
    if (float(c["adx"]) <= cfg.adx_range_threshold
            and float(c["bb_width"]) < float(c["bb_width_avg"]) * 1.2):
        return "RANGE"
    return "UNCLEAR"


def adaptive_rsi_bounds(rank, cfg: Config) -> Tuple[float, float]:
    if not cfg.rsi_adaptive or _isnan(rank):
        return 45.0, 70.0
    if rank > 0.75:
        return 35.0, 65.0
    if rank > 0.50:
        return 40.0, 68.0
    if rank > 0.25:
        return 45.0, 70.0
    return 50.0, 75.0


def _assign_tier(score: int, cfg: Config) -> Optional[str]:
    for tier, threshold in sorted(cfg.tier_thresholds, key=lambda x: -x[1]):
        if score >= threshold:
            return tier
    return None


def _above_vwap(c: Any) -> int:
    v = _row_get(c, "vwap_24")
    return int(not _isnan(v) and c["close"] > v)


def _score_trend(c: Any, p: Any) -> int:
    cross = False
    try:
        cross = (not _isnan(p["macd"])) and (not _isnan(p["macd_signal"])) \
            and (p["macd"] < p["macd_signal"]) \
            and (c["macd"] > c["macd_signal"])
    except Exception:
        cross = False
    return min(int(sum([
        12 * int(c["macd"] > c["macd_signal"]),
        18 * int(bool(cross)),
        5 * int(c["macd_hist"] > p["macd_hist"]),
        12 * int(c["adx"] >= 30),
        8 * int(c["adx"] >= 35),
        10 * int(c["ema_trend_slope"] > 0),
        5 * int(c["vol_ratio"] >= 1.8),
        10 * int(c["obv_slope"] > 0),
        10 * _above_vwap(c),
        10 * int(c["close"] > c["ema_fast"]),
    ])), 100)


def _score_breakout(c: Any, p: Any) -> int:
    return min(int(sum([
        20 * int(c["close"] > c["bb_upper"]),
        20 * int(c["vol_ratio"] >= 2.5),
        15 * int(c["atr"] > p["atr"] * 1.05),
        15 * int(c["obv_slope"] > 0),
        15 * int(c["adx"] >= 25),
        15 * _above_vwap(c),
    ])), 100)


def _score_range(c: Any, p: Any) -> int:
    return min(int(sum([
        20 * int(c["rsi"] <= 30),
        15 * int(c["rsi"] <= 25),
        20 * int(c["close"] <= c["bb_lower"] * 1.005),
        15 * int(c["obv_slope"] > 0),
        10 * int(c["vol_ratio"] < 1.5),
        10 * int(p["close"] < p["open"]),
        10 * int(c["close"] > p["close"]),
    ])), 100)


def _score_pullback(c: Any, p: Any) -> int:
    dist = abs(c["close"] - c["ema_slow"]) / c["atr"] if c["atr"] > 0 else 999
    return min(int(sum([
        12 * int(c["rsi"] <= 45),
        12 * int(c["rsi"] <= 40),
        12 * int(c["close"] > c["open"]),
        10 * int(p["close"] < p["open"]),
        12 * int(dist <= 0.8),
        6 * int(0.8 < dist <= 1.5),
        10 * int(c["adx"] >= 25),
        6 * int(c["adx"] >= 30),
        10 * int(c["obv_slope"] > 0),
        10 * _above_vwap(c),
    ])), 100)


def _essential_trend(c, p, rank, cfg, atr_min_pct=None):
    atr_min = atr_min_pct if atr_min_pct is not None else cfg.atr_min_pct
    if c["ema_fast"] <= c["ema_slow"]:
        return False, "ema_align"
    if c["ema_slow"] <= c["ema_trend"]:
        return False, "below_trend_ema"
    if c["adx"] < cfg.adx_trend_threshold:
        return False, "adx_low"
    if c["atr_pct"] < atr_min:
        return False, "atr_low"
    lo, hi = adaptive_rsi_bounds(rank, cfg)
    if not (lo - 5 <= c["rsi"] <= hi + 5):
        return False, "rsi_extreme"
    if c["vol_ratio"] < cfg.vol_confirm_ratio:
        return False, "vol_low"
    if c["vol_ratio"] >= cfg.vol_capitulation and c["close"] < c["open"]:
        return False, "capitulation"
    return True, None


def _essential_breakout(c, p, cfg):
    if p["bb_width"] > c["bb_width_avg"] * 1.1:
        return False, "no_squeeze"
    if p["close"] > p["bb_upper"]:
        return False, "already_broken"
    if c["close"] <= c["bb_upper"]:
        return False, "no_breakout"
    if c["atr"] <= p["atr"]:
        return False, "atr_flat"
    if c["vol_ratio"] < cfg.vol_breakout_ratio:
        return False, "vol_weak"
    return True, None


def _essential_range(c, p, cfg):
    if c["close"] > c["bb_lower"] * 1.005:
        return False, "not_near_bottom"
    if c["rsi"] > 35:
        return False, "rsi_high"
    if c["close"] <= p["close"]:
        return False, "no_reversal"
    if c["vol_ratio"] >= cfg.vol_capitulation:
        return False, "vol_too_high"
    if c["atr_pct"] <= cfg.atr_min_pct * 0.5:
        return False, "atr_too_low"
    return True, None


def _essential_pullback(c, p, cfg, atr_min_pct=None):
    atr_min = atr_min_pct if atr_min_pct is not None else cfg.atr_min_pct
    if c["ema_fast"] <= c["ema_slow"]:
        return False, "ema_align"
    if c["ema_slow"] <= c["ema_trend"]:
        return False, "below_trend_ema"
    if not (cfg.pullback_rsi_lo <= c["rsi"] <= cfg.pullback_rsi_hi):
        return False, "rsi_not_pullback"
    if _isnan(c["atr"]) or c["atr"] <= 0:
        return False, "no_atr"
    if c["atr_pct"] < atr_min:
        return False, "atr_low"
    if abs(c["close"] - c["ema_slow"]) > c["atr"] * cfg.pullback_max_dist_atr:
        return False, "far_from_ema"
    if c["close"] <= p["close"]:
        return False, "no_bounce"
    if p["close"] >= p["open"] * cfg.pullback_prev_bullish_buffer:
        return False, "prev_too_bullish"
    return True, None


def module_enabled(ctx: BotContext, mod: str,
                   now: Optional[datetime] = None) -> bool:
    info = ctx.portfolio.per_module.get(mod) or {}
    du = _parse_iso(info.get("disabled_until"))
    if du is None:
        if info.get("disabled_until"):
            info["disabled_until"] = None
        return True
    if (now or _utcnow()) >= du:
        info["disabled_until"] = None
        return True
    return False


_module_enabled = module_enabled


def min_signal_bars(cfg: Config) -> int:
    return max(cfg.trend_ema, cfg.atr_period, cfg.bb_period,
               cfg.adx_period * 3, cfg.vol_ma_period, cfg.vwap_period,
               cfg.atr_rank_window // 2) + 20


def generate_signal_from_rows(c: Any, p: Any, n_bars: int, htf_bias, btc_bias,
                              ctx: BotContext, cfg: Config,
                              atr_min_pct: Optional[float] = None,
                              now: Optional[datetime] = None) -> Signal:
    """c = dernière bougie FERMÉE, p = la précédente (dict ou Series)."""
    if n_bars < min_signal_bars(cfg):
        return Signal("NONE", "UNCLEAR", "", "", 0, "min_candles")
    if any(_isnan(_row_get(c, col)) for col in _SIGNAL_REQUIRED):
        return Signal("NONE", "UNCLEAR", "", "", 0, "indicators_nan")
    if cfg.htf_bias_enabled and htf_bias != "UP":
        return Signal("NONE", "UNCLEAR", "", "", 0, "htf_not_up")
    if cfg.btc_bias_enabled and btc_bias == "DOWN":
        return Signal("NONE", "UNCLEAR", "", "", 0, "btc_down")
    if (c["close"] - c["ema_fast"]) / c["ema_fast"] > cfg.max_dist_ema_fast_pct:
        return Signal("NONE", "UNCLEAR", "", "", 0, "extended")
    regime = detect_regime(c, cfg)
    candidates: List[Tuple[str, str, int]] = []
    rejects: Dict[str, Tuple[int, str]] = {}

    def _try(mod: str):
        if not module_enabled(ctx, mod, now):
            rejects[mod] = (0, "module_disabled")
            return
        if mod == "trend":
            if regime != "TREND_UP":
                return
            ok, r = _essential_trend(c, p, _row_get(c, "atr_rank"), cfg,
                                     atr_min_pct)
            score_fn = _score_trend
        elif mod == "pullback":
            if regime != "TREND_UP":
                return
            ok, r = _essential_pullback(c, p, cfg, atr_min_pct)
            score_fn = _score_pullback
        elif mod == "breakout":
            if regime not in ("UNCLEAR", "RANGE"):
                return
            ok, r = _essential_breakout(c, p, cfg)
            score_fn = _score_breakout
        elif mod == "range":
            if regime != "RANGE":
                return
            ok, r = _essential_range(c, p, cfg)
            score_fn = _score_range
        else:
            return
        if not ok:
            rejects[mod] = (0, r or "rejected")
            return
        s = score_fn(c, p)
        tier = _assign_tier(s, cfg)
        if not tier:
            rejects[mod] = (s, "score_low")
            return
        candidates.append((mod, tier, s))

    for m in ("trend", "pullback", "breakout", "range"):
        _try(m)

    if not candidates:
        priority = {"TREND_UP": ["trend", "pullback"],
                    "RANGE": ["range", "breakout"],
                    "UNCLEAR": ["breakout"]}.get(regime, [])
        for mod in priority:
            if mod in rejects:
                s, r = rejects[mod]
                return Signal("NONE", regime, mod, "", s, r)
        if rejects:
            mod = next(iter(rejects))
            s, r = rejects[mod]
            return Signal("NONE", regime, mod, "", s, r)
        return Signal("NONE", regime, "", "", 0, "no_module")

    candidates.sort(key=lambda x: ({"S": 3, "A": 2, "B": 1}.get(x[1], 0),
                                   x[2]), reverse=True)
    mod, tier, s = candidates[0]
    return Signal("BUY", regime, mod, tier, s, None)


def generate_signal(df: pd.DataFrame, htf_bias, btc_bias,
                    ctx: BotContext, cfg: Config,
                    atr_min_pct: Optional[float] = None,
                    now: Optional[datetime] = None) -> Signal:
    if len(df) < 3:
        return Signal("NONE", "UNCLEAR", "", "", 0, "min_candles")
    return generate_signal_from_rows(df.iloc[-2], df.iloc[-3], len(df),
                                     htf_bias, btc_bias, ctx, cfg,
                                     atr_min_pct, now)
