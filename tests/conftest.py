import logging
import os
import sys
import time
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

import v29  # noqa: E402
from fake_binance import FakeBinance  # noqa: E402


@pytest.fixture(autouse=True)
def _exchange_clock_reset():
    """L'heure du bot (écart avec Binance) est globale : remise à zéro
    avant et après chaque test."""
    v29.set_clock_offset_ms(0)
    yield
    v29.set_clock_offset_ms(0)


@pytest.fixture
def logger():
    lg = logging.getLogger("test.v29")
    lg.setLevel(logging.DEBUG)
    return lg


def make_cfg(run_mode="paper", **kw):
    base = dict(run_mode=run_mode, db_file=":memory:",
                log_file=os.devnull, lock_file=os.devnull,
                heartbeat_log_file=os.devnull)
    if run_mode == "live":
        base.update(enable_live_trading=True,
                    live_confirmation="I_UNDERSTAND_RISK")
    base.update(kw)
    return v29.Config(**base)


def build_env(run_mode="live", fb=None, logger=None, **cfg_kw):
    logger = logger or logging.getLogger("test.v29")
    fb = fb or FakeBinance()
    cfg = make_cfg(run_mode, **cfg_kw)
    ex = v29.ExchangeAdapter(cfg, logger, exchange=fb)
    ex.sleep = lambda s: None
    ex.load_markets()
    store = v29.Store(":memory:", logger)
    notifier = v29.Notifier("", "", logger=logger)
    risk = v29.RiskEngine(cfg, logger)
    eng = v29.ExecutionEngine(cfg, logger, ex, store, notifier, risk)
    ctx = v29.BotContext()
    if run_mode == "paper":
        ctx.portfolio.paper_cash = 1000.0
    ex.bind_paper_context(ctx)
    return SimpleNamespace(fb=fb, cfg=cfg, ex=ex, store=store, risk=risk,
                           eng=eng, ctx=ctx, logger=logger)


def fresh_closed(cfg, atr=0.002, adx=25.0):
    now_ms = int(time.time() * 1000)
    return {"ts": now_ms - v29._timeframe_ms(cfg.timeframe), "atr": atr,
            "adx": adx, "realized_vol": 0.5, "last_swing_low": float("nan")}


def buy_signal(module="trend", tier="A"):
    return v29.Signal("BUY", "TREND_UP", module, tier, 60, None)


def open_live_position(env, price=0.10, atr=0.002):
    env.fb.set_price(price)
    closed = fresh_closed(env.cfg, atr=atr)
    candle_ts = int(time.time() * 1000)
    equity = env.ex.get_equity(env.ctx, force=True)
    res = env.eng.enter(env.ctx, closed, buy_signal(), price, candle_ts, equity)
    return res


@pytest.fixture
def live_env(logger):
    return build_env("live", logger=logger)


@pytest.fixture
def paper_env(logger):
    return build_env("paper", logger=logger)
