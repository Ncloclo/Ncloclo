"""Indicateurs techniques.

Partie de l'ancien bot V29.6 (v29/intraday/), rangé à part du moteur
d'exécution que TrendGuard utilise.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..config import Config
from ..utils import _annualization_factor, _bars_per_day


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def macd_calc(series: pd.Series, fast: int = 12, slow: int = 26,
              signal: int = 9):
    ef = series.ewm(span=fast, adjust=False).mean()
    es = series.ewm(span=slow, adjust=False).mean()
    line = ef - es
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def atr_calc(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def bollinger(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width = (upper - lower) / mid.replace(0, np.nan)
    return mid, upper, lower, width


def adx_calc(df: pd.DataFrame, period: int = 14):
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr_w = tr.ewm(alpha=1/period, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1/period, adjust=False).mean() / atr_w.replace(0, 1e-12)
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1/period, adjust=False).mean() / atr_w.replace(0, 1e-12)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, 1e-12)
    return dx.ewm(alpha=1/period, adjust=False).mean(), pdi, mdi


def obv_calc(df: pd.DataFrame, ema_span: int = 20):
    obv_line = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
    obv_ema = obv_line.ewm(span=ema_span, adjust=False).mean()
    return obv_line, obv_ema, obv_ema.diff(5)


def vwap_calc(df: pd.DataFrame, period: int = 24) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol_sum = df["volume"].rolling(period).sum().replace(0, np.nan)
    return (tp * df["volume"]).rolling(period).sum() / vol_sum


def compute_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Indicateurs strictement causaux (aucun shift négatif)."""
    df = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    c = df["close"]
    df["ema_fast"] = ema(c, cfg.fast_ema)
    df["ema_slow"] = ema(c, cfg.slow_ema)
    df["ema_trend"] = ema(c, cfg.trend_ema)
    df["ema_trend_slope"] = df["ema_trend"].pct_change(5)
    df["rsi"] = rsi(c, cfg.rsi_period)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd_calc(
        c, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    df["atr"] = atr_calc(df, cfg.atr_period)
    df["atr_pct"] = df["atr"] / c.replace(0, np.nan)
    df["atr_pct_avg"] = df["atr_pct"].rolling(200, min_periods=50).mean()
    df["atr_rank"] = df["atr_pct"].rolling(
        cfg.atr_rank_window, min_periods=50).rank(pct=True)
    df["bb_mid"], df["bb_upper"], df["bb_lower"], df["bb_width"] = bollinger(
        c, cfg.bb_period, cfg.bb_std)
    df["bb_width_avg"] = df["bb_width"].rolling(50).mean()
    df["adx"], df["plus_di"], df["minus_di"] = adx_calc(df, cfg.adx_period)
    df["vol_ma"] = df["volume"].rolling(cfg.vol_ma_period).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma"].replace(0, np.nan)
    df["obv"], df["obv_ema"], df["obv_slope"] = obv_calc(df, cfg.obv_ema_span)
    df["vwap_24"] = vwap_calc(df, cfg.vwap_period)
    df["last_swing_low"] = df["low"].rolling(
        cfg.swing_window, min_periods=cfg.swing_window).min().shift(1)
    df["ret"] = np.log(c / c.shift(1))
    bpd = _bars_per_day(cfg.timeframe)
    df["realized_vol"] = (df["ret"].rolling(max(bpd, 2)).std()
                          * math.sqrt(_annualization_factor(cfg.timeframe)))
    return df
