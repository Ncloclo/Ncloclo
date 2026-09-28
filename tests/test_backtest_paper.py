"""Backtest (comptabilité, absence de look-ahead, walk-forward) et mode
paper (barrières, time-exit, boucle complète)."""

import time
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

import v29
from conftest import build_env, buy_signal, fresh_closed
from fake_binance import FakeBinance
from v29 import intraday as v29i


def synthetic_ohlcv(n=3000, seed=7, drift=0.0003, vol=0.01, tf_ms=3_600_000,
                    start_ms=1_600_000_000_000):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 0.1 * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.normal(0, vol / 2, n))
    high = np.maximum(open_, close) * (1 + wick)
    low = np.minimum(open_, close) * (1 - wick)
    return pd.DataFrame({"ts": start_ms + np.arange(n) * tf_ms, "open": open_,
                         "high": high, "low": low, "close": close,
                         "volume": rng.uniform(1e6, 2e6, n)})


@pytest.fixture
def forced_signals(monkeypatch):
    """Signal BUY toutes les 40 barres (quand à plat) pour exercer le moteur
    indépendamment de la stratégie."""
    orig = v29i.generate_signal_from_rows

    def fake(c, p, n_bars, htf, btc, ctx, cfg, atr_min_pct=None, now=None):
        if htf == "DOWN":
            return v29.Signal("NONE", "UNCLEAR", "", "", 0, "htf_not_up")
        if n_bars % 40 == 0:
            return v29.Signal("BUY", "TREND_UP", "trend", "A", 60, None)
        return v29.Signal("NONE", "UNCLEAR", "", "", 0, "no_module")

    for mod in (v29i.signals, v29i.backtest):
        monkeypatch.setattr(mod, "generate_signal_from_rows", fake)
    return orig


def test_backtest_accounting_identity(forced_signals):
    cfg = v29.Config(last_trades_per_module=3)
    df = v29i.compute_indicators(synthetic_ohlcv(), cfg)
    res = v29i.BacktestEngine(cfg, 1000.0).run(df)
    assert res.num_trades > 10
    # Aucun trade tronqué (V29.5 : métriques sur les 50 derniers/module).
    assert res.num_trades == len(res.trades) > cfg.last_trades_per_module
    # Identité comptable : equity finale = capital + Σ PnL des trades.
    total = sum(t["pnl"] for t in res.trades)
    assert abs(res.final_equity - (1000.0 + total)) < 1e-6
    # Les jambes partielles ne sont PAS des trades séparés (V29.5 : double
    # comptage du PnL partiel).
    assert any(t["legs"] > 1 for t in res.trades)
    assert all(t["reason"] not in ("R1", "R2") for t in res.trades)


def test_backtest_r_multiples_are_bounded(forced_signals):
    cfg = v29.Config()
    df = v29i.compute_indicators(synthetic_ohlcv(seed=3), cfg)
    res = v29i.BacktestEngine(cfg).run(df)
    sl_exits = [t["r"] for t in res.trades if t["reason"] == "BARRIER_SL"
                and t["legs"] == 1]
    # Un stop touché vaut ≈ -1R (frais/slippage inclus) ; un stop remonté au
    # break-even ou en trailing ne perd jamais (BE au-dessus du coût de revient).
    assert any(r < 0 for r in sl_exits) and any(r >= 0 for r in sl_exits)
    assert all(r >= 0 or -1.6 < r < -0.8 for r in sl_exits)


def test_backtest_uses_break_even_and_trailing(forced_signals):
    df = synthetic_ohlcv(seed=11, drift=0.0008)
    a = v29i.BacktestEngine(v29.Config(trail_atr_mult=1.5)).run(
        v29i.compute_indicators(df, v29.Config()))
    b = v29i.BacktestEngine(v29.Config(trail_atr_mult=3.0)).run(
        v29i.compute_indicators(df, v29.Config()))
    # V29.5 : BE/trailing ignorés par le backtest → sensibilité « STABLE ».
    assert [t["pnl"] for t in a.trades] != [t["pnl"] for t in b.trades]


def test_bias_series_has_no_lookahead():
    ltf = synthetic_ohlcv(n=500)
    htf_rows = []
    for i in range(0, 500, 4):
        chunk = ltf.iloc[i:i + 4]
        htf_rows.append({"ts": int(chunk["ts"].iloc[0]),
                         "close": float(chunk["close"].iloc[-1])})
    htf = pd.DataFrame(htf_rows)
    s = v29i.build_bias_series(ltf["ts"], htf, 20, 0.005, 0.005, "4h")
    # Contrôle indépendant : pour chaque barre LTF, biais recalculé avec les
    # seules barres HTF clôturées à son ouverture.
    ema = htf["close"].ewm(span=20, adjust=False).mean()
    for i in (37, 150, 333, 499):
        t = int(ltf["ts"].iloc[i])
        k = int(((htf["ts"] + 4 * 3_600_000) <= t).sum()) - 1
        if k < 0:
            assert s.iloc[i] is None
            continue
        c, e = float(htf["close"].iloc[k]), float(ema.iloc[k])
        exp = "UP" if c > e * 1.005 else "DOWN" if c < e * 0.995 else None
        assert s.iloc[i] == exp


def test_walk_forward_optimises_in_sample(forced_signals):
    cfg = v29.Config()
    df = v29i.compute_indicators(synthetic_ohlcv(n=6000), cfg)
    windows = v29i.walk_forward(df, cfg, is_months=2, oos_months=1,
                               step_months=2,
                               param_grid={"trail_atr_mult": [1.5, 2.5]},
                               min_is_trades=3)
    assert windows
    assert all("trail_atr_mult" in w.params for w in windows)
    assert v29i.walkforward_verdict(windows)


def test_sensitivity_runs(forced_signals):
    cfg = v29.Config()
    df = synthetic_ohlcv(n=2500)
    out = v29i.run_sensitivity(df, cfg, {"trail_atr_mult": [1.5, 2.5],
                                        "atr_period": [10, 14]})
    assert len(out) == 4 and "error" not in out.columns
    assert "Sharpe" in v29i.report_sensitivity(out)


# ---------- Paper ----------

def test_paper_entry_and_barriers_ignore_pre_entry_bars(paper_env):
    env = paper_env
    closed = fresh_closed(env.cfg)
    now_ms = int(time.time() * 1000)
    res = env.eng.enter(env.ctx, closed, buy_signal(), 0.10, now_ms, 1000.0)
    assert res == v29.EntryResult.OPENED
    p = env.ctx.position
    tf = v29._timeframe_ms(env.cfg.timeframe)
    # La bougie AVANT l'entrée a une mèche sous le SL : ne doit pas sortir.
    df = pd.DataFrame([
        {"ts": now_ms - tf, "open": 0.1, "high": 0.1, "low": p.sl_price * 0.99,
         "close": 0.1, "volume": 1.0},
        {"ts": now_ms, "open": 0.1, "high": 0.1005, "low": 0.0995,
         "close": 0.1, "volume": 1.0}])
    assert env.eng._paper_barriers(env.ctx, df, 0.1) is False
    assert env.ctx.position.in_position
    # Une bougie POSTÉRIEURE qui touche le TP ferme la position.
    df2 = pd.concat([df, pd.DataFrame([{"ts": now_ms + tf, "open": 0.1,
                                        "high": p.tp_price * 1.001, "low": 0.0999,
                                        "close": p.tp_price, "volume": 1.0}])])
    assert env.eng._paper_barriers(env.ctx, df2, 0.1)
    assert env.ctx.portfolio.last_trades[-1]["reason"] == "BARRIER_TP"
    assert env.ctx.portfolio.paper_cash > 1000.0


def test_paper_time_exit(paper_env):
    env = paper_env
    env.eng.enter(env.ctx, fresh_closed(env.cfg), buy_signal(), 0.10,
                  int(time.time() * 1000), 1000.0)
    p = env.ctx.position
    p.opened_at = (v29._utcnow() - timedelta(hours=49)).isoformat()
    assert env.eng.execute_time_exit(env.ctx, 0.0995)
    assert not env.ctx.position.in_position


def test_paper_partial_r_is_additive(paper_env):
    env = paper_env
    env.eng.enter(env.ctx, fresh_closed(env.cfg), buy_signal(), 0.10,
                  int(time.time() * 1000), 1000.0)
    p = env.ctx.position
    r1 = p.buy_price + p.risk_per_unit * 1.05
    env.eng.check_partial_exits(env.ctx, r1)
    assert env.ctx.position.partial_exit_count == 1
    env.eng.close_position(env.ctx, "BARRIER_TP", p.tp_price)
    t = env.ctx.portfolio.last_trades[-1]
    legs_r = sum(l["pnl"] for l in p.legs) / p.risk_quote_initial
    assert abs(t["r"] - legs_r) < 1e-9


def _feed(fb, n=1100, tf_ms=3_600_000, price=0.1):
    # La dernière bougie fermée a clôturé il y a 1 minute (indépendant de
    # l'heure d'exécution : pas de signal « périmé » selon la minute).
    last_open = int(time.time() * 1000) - 60_000
    df = synthetic_ohlcv(n=n, tf_ms=tf_ms, start_ms=last_open - (n - 1) * tf_ms,
                         drift=0.0, vol=0.002)
    scale = price / float(df["close"].iloc[-1])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] * scale
    fb.ohlcv[("TRX/USDT", "1h")] = df.values.tolist()
    fb.ohlcv[("TRX/USDT", "4h")] = df.iloc[::4].values.tolist()
    fb.ohlcv[("BTC/USDT", "4h")] = df.iloc[::4].values.tolist()
    fb.ohlcv[("BTC/USDT", "1h")] = df.values.tolist()
    fb.set_price(price)


def test_paper_bot_loop_opens_and_closes(monkeypatch, logger):
    fb = FakeBinance()
    _feed(fb)
    env = build_env("paper", fb=fb, logger=logger, htf_bias_enabled=False,
                    btc_bias_enabled=False)
    runner = v29i.BotRunner(env.cfg, logger, env.ex, env.store,
                           v29.Notifier("", "", logger=logger), env.risk,
                           env.eng, v29i.AdaptiveEngine(env.cfg, logger))
    monkeypatch.setattr(v29i.runner, "generate_signal",
                        lambda *a, **k: v29.Signal("BUY", "TREND_UP", "trend",
                                                   "A", 60, None))
    assert runner.boot()
    runner.ctx.portfolio.paper_cash = 1000.0
    runner.run_cycle()
    p = runner.ctx.position
    assert p.in_position
    assert runner.ctx.last_signal_candle_ts is not None
    fb.set_price(p.tp_price * 1.002)
    runner.run_cycle()
    assert not runner.ctx.position.in_position
    assert runner.ctx.portfolio.stats_wins == 1
    # Contexte persistant relisible.
    assert env.store.load_context().portfolio.stats_wins == 1


def test_live_bot_boot_and_cycle(monkeypatch, logger):
    fb = FakeBinance()
    _feed(fb)
    env = build_env("live", fb=fb, logger=logger, htf_bias_enabled=False,
                    btc_bias_enabled=False)
    runner = v29i.BotRunner(env.cfg, logger, env.ex, env.store,
                           v29.Notifier("", "", logger=logger), env.risk,
                           env.eng, v29i.AdaptiveEngine(env.cfg, logger))
    monkeypatch.setattr(v29i.runner, "generate_signal",
                        lambda *a, **k: v29.Signal("BUY", "TREND_UP", "trend",
                                                   "A", 60, None))
    assert runner.boot()                         # self-test via order/test
    runner.run_cycle()
    p = runner.ctx.position
    assert p.in_position and p.protection_mode == "OCO"
    runner.run_cycle()                            # même bougie : pas de 2e achat
    buys = [o for o in fb.orders.values() if o["side"] == "buy"]
    assert len(buys) == 1
    fb.set_price(p.sl_price * 0.998)
    runner.run_cycle()
    assert not runner.ctx.position.in_position
    assert runner.ctx.orphan_balance is False


def test_monte_carlo_distribution(forced_signals):
    cfg = v29.Config()
    df = v29i.compute_indicators(synthetic_ohlcv(seed=4), cfg)
    res = v29i.BacktestEngine(cfg).run(df)
    mc = v29i.monte_carlo_trades(res.trades, n_sims=500)
    assert mc is not None
    assert mc["ret_p5"] <= mc["ret_p50"] <= mc["ret_p95"]
    assert 0 <= mc["dd_p50"] <= mc["dd_p95"] <= 100
    # Le rendement composé des trades ≈ rendement réel du backtest.
    comp = (np.prod([1 + t["ret"] for t in res.trades]) - 1) * 100
    assert abs(comp - res.total_return_pct) < 1.0
