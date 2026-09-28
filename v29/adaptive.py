"""Moteur adaptatif du bot V29.

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import Config
from .models import AdaptiveState, BotContext
from .utils import _timeframe_ms


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        v = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return v


def _isnan(x: Any) -> bool:
    try:
        return x is None or bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


class AdaptiveEngine:
    """Ajuste trois paramètres de filtrage. Il ne peut que RESSERRER la
    fraîcheur (bornée par max_signal_age_min) et la pause (bornée à ±2)."""

    def __init__(self, cfg: Config, logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger

    def record_latency(self, state: AdaptiveState, latency_ms: int) -> None:
        if not self.cfg.adaptive_enabled:
            return
        state.signal_latencies_ms.append(int(max(0, latency_ms)))
        max_len = self.cfg.adaptive_latency_window * 2
        if len(state.signal_latencies_ms) > max_len:
            state.signal_latencies_ms = state.signal_latencies_ms[-max_len:]

    def observe_closed_candle(self, state: AdaptiveState, closed_ts: int,
                              now_ms: Optional[int] = None) -> None:
        """Latence mesurée sur CHAQUE nouvelle bougie fermée (et non sur
        les seules entrées acceptées → pas de biais de survie)."""
        if state.last_latency_candle_ts == int(closed_ts):
            return
        state.last_latency_candle_ts = int(closed_ts)
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        close_ms = int(closed_ts) + _timeframe_ms(self.cfg.timeframe)
        self.record_latency(state, now_ms - close_ms)

    def _p95_latency_min(self, state: AdaptiveState) -> Optional[float]:
        if len(state.signal_latencies_ms) < self.cfg.adaptive_min_samples:
            return None
        arr = np.array(state.signal_latencies_ms[
            -self.cfg.adaptive_latency_window:], dtype=float)
        return float(np.percentile(arr, 95)) / 60_000

    def update(self, ctx: BotContext, closed: Any) -> None:
        if not self.cfg.adaptive_enabled:
            return
        state = ctx.adaptive
        p95_min = self._p95_latency_min(state)
        if p95_min is not None:
            target = max(self.cfg.adaptive_freshness_min,
                         min(self.cfg.adaptive_freshness_max,
                             int(p95_min * 2) + 10))
            if target != state.current_freshness_min:
                self.logger.info(
                    f"[ADAPT] freshness {state.current_freshness_min} → "
                    f"{target} (p95={p95_min:.1f}min)")
                state.current_freshness_min = target
            state.bootstrap_done = True
        else:
            state.current_freshness_min = self.cfg.adaptive_freshness_max
            state.bootstrap_done = False
        if self.cfg.adaptive_atr_scaling and closed is not None:
            atr_pct = _row_get(closed, "atr_pct")
            avg = _row_get(closed, "atr_pct_avg")
            if not _isnan(atr_pct) and not _isnan(avg) and float(avg) > 0:
                ratio = float(atr_pct) / float(avg)
                target_atr = self.cfg.atr_min_pct * max(
                    0.7, min(1.5, 1.0 / max(0.5, ratio)))
                if abs(target_atr - state.current_atr_min_pct) > 0.0005:
                    state.current_atr_min_pct = target_atr
        if self.cfg.adaptive_consec_scaling:
            recent = ctx.portfolio.last_trades[-30:]
            base = self.cfg.consec_loss_pause
            if len(recent) >= 15:
                arr = np.array([float(t.get("r", 0) or 0) for t in recent])
                sd = float(arr.std())
                sharpe = float(arr.mean() / sd) if sd > 1e-9 else 0.0
                # Sharpe négatif → pause PLUS TÔT (seuil plus bas).
                target = int(round(float(np.clip(
                    base * (1.0 + sharpe * 0.3), max(2, base - 2), base + 2))))
                if target != state.current_consec_pause:
                    self.logger.info(
                        f"[ADAPT] consec_pause {state.current_consec_pause} "
                        f"→ {target} (sharpe={sharpe:.2f})")
                    state.current_consec_pause = target
            else:
                state.current_consec_pause = base
        state.last_update_ts = time.time()

    def effective_freshness(self, state: AdaptiveState, base: int) -> int:
        if not self.cfg.adaptive_enabled:
            return base
        return min(state.current_freshness_min, base)

    def effective_atr_min(self, state: AdaptiveState, base: float) -> float:
        if not self.cfg.adaptive_enabled or not self.cfg.adaptive_atr_scaling:
            return base
        return state.current_atr_min_pct

    def effective_consec_pause(self, state: AdaptiveState, base: int) -> int:
        if not self.cfg.adaptive_enabled or not self.cfg.adaptive_consec_scaling:
            return base
        return state.current_consec_pause
