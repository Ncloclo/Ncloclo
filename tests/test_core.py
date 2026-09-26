"""Tests unitaires : configuration, risque, disjoncteurs, adaptatif,
persistance, utilitaires."""

import logging
import math
import re
import time
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

import v29
from conftest import make_cfg


# ---------- Configuration ----------

def test_default_config_is_valid():
    """V29.5 : Config() levait ValueError (freshness 90 > 45)."""
    cfg = v29.Config()
    assert cfg.adaptive_freshness_max <= cfg.max_signal_age_min


def test_config_rejects_break_even_below_costs():
    with pytest.raises(ValueError, match="break_even_offset"):
        v29.Config(break_even_offset=0.002)


def test_config_rejects_invalid_sizing_mode():
    with pytest.raises(ValueError):
        v29.Config(sizing_mode="kely")


def test_config_rejects_htf_not_above_ltf():
    with pytest.raises(ValueError):
        v29.Config(timeframe="4h", htf_timeframe="1h")


def test_live_requires_confirmation():
    with pytest.raises(ValueError):
        v29.Config(run_mode="live", enable_live_trading=True)


@pytest.mark.skipif(not v29.WEB3_AVAILABLE, reason="web3 absent")
def test_sweep_target_must_be_whitelisted():
    with pytest.raises(ValueError, match="WHITELIST"):
        v29.Config(blockchain_enabled=True, blockchain_rpc_url="http://x",
                   blockchain_sweep_enabled=True,
                   blockchain_sweep_target="0x" + "22" * 20,
                   blockchain_whitelist=("0x" + "33" * 20,))


def test_parse_symbol():
    assert v29._parse_symbol("trx/usdt") == ("TRX/USDT", "TRX", "USDT")
    with pytest.raises(ValueError):
        v29._parse_symbol("TRXUSDT")


def test_env_helpers(monkeypatch):
    monkeypatch.setenv("T_F", "abc")
    monkeypatch.setenv("T_I", "x")
    monkeypatch.setenv("T_B", "")
    assert v29._env_f("T_F", 1.5) == 1.5
    assert v29._env_i("T_I", 7) == 7
    assert v29._env_b("T_B", True) is True        # vide → défaut
    monkeypatch.setenv("T_S", "  ")
    assert v29._env_s("T_S", "paper") == "paper"  # vide → défaut


def test_env_doc_complete_and_no_orphan():
    src = open(v29.__file__, encoding="utf-8").read()
    pattern = r'(?:os\.environ\.get|_env_[a-z]+|_env_tuple_csv)\(\s*"([A-Z0-9_]+)"'
    used = set(re.findall(pattern, src))
    documented = set(v29.ENV_DOC)
    assert used - documented == set()
    assert documented - used == set()


def test_timeframe_ms():
    assert v29._timeframe_ms("15m") == 900_000
    assert v29._timeframe_ms("1h") == 3_600_000
    assert v29._timeframe_ms("1w") == 604_800_000
    assert v29._timeframe_ms("1M") == 30 * 86_400_000     # mois ≠ minute
    with pytest.raises(ValueError):
        v29._timeframe_ms("1x")


def test_annualization():
    assert abs(v29._annualization_factor("1h") - 8766.0) < 1.0
    assert abs(v29._annualization_factor("15m") / v29._annualization_factor("1h") - 4) < 1e-9


# ---------- Sécurité / utilitaires ----------

def test_redact_url():
    r = v29.redact_url("https://user:pass@mainnet.infura.io/v3/abcdef1234567890?k=1")
    assert "infura.io" in r and "abcdef" not in r
    assert "user" not in r and "pass" not in r and "k=1" not in r
    r2 = v29.redact_url("https://ab12cd34ef56gh78ij90kl.rpc.example.com/")
    assert "ab12cd34" not in r2


def test_scrub_secrets():
    url = "https://mainnet.infura.io/v3/SECRETKEY123456"
    msg = f"HTTPSConnectionPool: Max retries exceeded with url: /v3/SECRETKEY123456 ({url})"
    out = v29.scrub_secrets(msg, [url])
    assert "SECRETKEY123456" not in out


def test_redact_address():
    a = "0x1234567890abcdef1234567890abcdef12345678"
    r = v29.redact_address(a)
    assert r.startswith("0x1234") and r.endswith("5678") and "…" in r


# ---------- Risque ----------

def test_position_size_risk_budget():
    cfg = v29.Config()
    risk = v29.RiskEngine(cfg)
    amt = risk.position_size(1.0, 0.02, 1000.0, 0.01)
    loss = amt * (0.02 + 1.0 * 0.001 + 0.98 * 0.001 + 0.0015)
    assert abs(loss - 10.0) < 1e-6


def test_position_size_capped_by_notional():
    risk = v29.RiskEngine(v29.Config())
    amt = risk.position_size(1.0, 0.001, 1000.0, 0.01, max_notional=100.0)
    assert amt <= 100.0 + 1e-9


def test_kelly_bounds():
    cfg = v29.Config()
    risk = v29.RiskEngine(cfg)
    ctx = v29.BotContext()
    ctx.portfolio.last_trades = [{"module": "trend", "pnl": -5, "r": -1.0}] * 20
    assert risk.kelly_base(ctx, "trend") == cfg.risk_min_pct
    ctx.portfolio.last_trades = [{"module": "trend", "pnl": 5, "r": 1.0}] * 20
    assert risk.kelly_base(ctx, "trend") == cfg.risk_max_pct


def test_regime_and_dd_multipliers():
    cfg = v29.Config()
    risk = v29.RiskEngine(cfg)
    assert (risk.regime_multiplier("TREND_UP"), risk.regime_multiplier("RANGE"),
            risk.regime_multiplier("UNCLEAR")) == (1.0, 0.65, 0.45)
    ctx = v29.BotContext()
    ctx.risk.daily_start_equity = 1000.0
    assert risk.dd_derisk_multiplier(ctx, 980.0) == 0.60


def test_breakeven_trades_not_counted_as_wins():
    cfg = v29.Config()
    risk = v29.RiskEngine(cfg)
    ctx = v29.BotContext()
    ctx.portfolio.last_trades = [{"module": "trend", "pnl": 0.0, "r": 0.0}] * 20
    assert risk.base_risk(ctx, "trend") <= cfg.risk_min_pct + 1e-12


def test_signal_freshness():
    cfg = v29.Config()
    risk = v29.RiskEngine(cfg)
    now_ms = int(time.time() * 1000)
    m, reason = risk.signal_freshness_mult(now_ms - 3_600_000)
    assert reason is None and 0.9 <= m <= 1.0
    m2, reason2 = risk.signal_freshness_mult(now_ms - 3_600_000 - 50 * 60_000)
    assert m2 == 0.0 and reason2.startswith("signal_stale")


# ---------- Disjoncteurs ----------

def test_consecutive_loss_pause_is_not_permanent():
    """V29.5 : la pause se ré-armait indéfiniment (deadlock)."""
    cfg = v29.Config()
    risk = v29.RiskEngine(cfg)
    ctx = v29.BotContext()
    ctx.portfolio.consec_losses = 5
    ctx.portfolio.losses_since_pause = 5
    t0 = v29._utcnow()
    blocked, _ = risk.check_circuit_breakers(ctx, 1000.0, now=t0)
    assert blocked and ctx.risk.paused_until
    blocked, _ = risk.check_circuit_breakers(ctx, 1000.0, now=t0 + timedelta(hours=1))
    assert blocked
    blocked, _ = risk.check_circuit_breakers(ctx, 1000.0,
                                             now=t0 + timedelta(hours=24, minutes=1))
    assert not blocked


def test_equity_unavailable_blocks_entries():
    risk = v29.RiskEngine(v29.Config())
    ctx = v29.BotContext()
    blocked, reason = risk.check_circuit_breakers(ctx, None)
    assert blocked and reason == "EQUITY_UNAVAILABLE"
    assert not ctx.risk.halted


def test_daily_dd_halts_then_resets_next_day():
    cfg = v29.Config()
    risk = v29.RiskEngine(cfg)
    ctx = v29.BotContext()
    t0 = v29._utcnow().replace(hour=10)
    risk.check_circuit_breakers(ctx, 1000.0, now=t0)
    blocked, _ = risk.check_circuit_breakers(ctx, 960.0, now=t0 + timedelta(hours=1))
    assert blocked and ctx.risk.halt_kind == v29.HaltKind.DAILY_DD
    blocked, _ = risk.check_circuit_breakers(ctx, 960.0, now=t0 + timedelta(days=1))
    assert not blocked


def test_weekly_dd_resets_next_week():
    cfg = v29.Config()
    risk = v29.RiskEngine(cfg)
    ctx = v29.BotContext()
    t0 = v29._utcnow()
    risk.check_circuit_breakers(ctx, 1000.0, now=t0)
    ctx.risk.daily_start_equity = 900.0      # évite le DD journalier
    ctx.risk.daily_start_date = v29._day_key(t0)
    blocked, _ = risk.check_circuit_breakers(ctx, 910.0, now=t0)
    assert blocked and ctx.risk.halt_kind == v29.HaltKind.WEEKLY_DD
    blocked, _ = risk.check_circuit_breakers(ctx, 910.0, now=t0 + timedelta(days=7))
    assert not blocked


def test_record_closed_trade_stats_and_cooldown():
    cfg = v29.Config()
    risk = v29.RiskEngine(cfg)
    ctx = v29.BotContext()
    v29.record_closed_trade(ctx, cfg, risk, {"pnl": -2.0, "r": -1.0,
                                             "module": "trend", "tier": "A",
                                             "reason": "BARRIER_SL",
                                             "entry_adx": 20.0})
    assert ctx.portfolio.consec_losses == 1 and ctx.portfolio.losses_since_pause == 1
    assert ctx.risk.cooldown_until is not None
    v29.record_closed_trade(ctx, cfg, risk, {"pnl": 3.0, "r": 1.5,
                                             "module": "trend", "tier": "A",
                                             "reason": "BARRIER_TP"})
    assert ctx.portfolio.consec_losses == 0 and ctx.portfolio.stats_wins == 1


# ---------- Adaptatif ----------

def test_adaptive_consec_direction():
    """V29.5 : Sharpe négatif → PLUS de pertes tolérées (inversé)."""
    cfg = v29.Config()
    eng = v29.AdaptiveEngine(cfg, logging.getLogger("t"))
    bad = v29.BotContext()
    bad.portfolio.last_trades = [{"r": -1.0}] * 12 + [{"r": 0.5}] * 8
    eng.update(bad, None)
    good = v29.BotContext()
    good.portfolio.last_trades = [{"r": 1.5}] * 14 + [{"r": -1.0}] * 6
    eng.update(good, None)
    assert bad.adaptive.current_consec_pause < cfg.consec_loss_pause
    assert good.adaptive.current_consec_pause > cfg.consec_loss_pause


def test_adaptive_freshness_bootstrap_and_tightening():
    cfg = v29.Config()
    eng = v29.AdaptiveEngine(cfg, logging.getLogger("t"))
    ctx = v29.BotContext()
    eng.update(ctx, None)
    assert ctx.adaptive.current_freshness_min == cfg.adaptive_freshness_max
    base = 1_700_000_000_000
    tf = v29._timeframe_ms(cfg.timeframe)
    for i in range(30):
        ts = base + i * tf
        eng.observe_closed_candle(ctx.adaptive, ts, now_ms=ts + tf + 20_000)
        eng.observe_closed_candle(ctx.adaptive, ts, now_ms=ts + tf + 99_000_000)
    assert len(ctx.adaptive.signal_latencies_ms) == 30    # 1 mesure / bougie
    eng.update(ctx, None)
    assert ctx.adaptive.current_freshness_min == cfg.adaptive_freshness_min


# ---------- Signal ----------

def test_essential_range_filters():
    cfg = v29.Config()
    c = {"close": 0.95, "rsi": 28.0, "bb_lower": 0.96, "vol_ratio": 3.5,
         "atr_pct": 0.01, "open": 0.96}
    p = {"close": 0.94}
    assert v29._essential_range(c, p, cfg) == (False, "vol_too_high")
    c2 = dict(c, vol_ratio=1.2, atr_pct=0.001)
    assert v29._essential_range(c2, p, cfg) == (False, "atr_too_low")
    c3 = dict(c, vol_ratio=1.2)
    assert v29._essential_range(c3, p, cfg) == (True, None)


def test_indicators_are_causal():
    cfg = v29.Config()
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 600)))
    df = pd.DataFrame({"ts": np.arange(600) * 3_600_000, "open": close,
                       "high": close * 1.005, "low": close * 0.995,
                       "close": close, "volume": rng.uniform(1, 2, 600)})
    full = v29.compute_indicators(df, cfg)
    part = v29.compute_indicators(df.iloc[:400], cfg)
    cols = ["ema_trend", "rsi", "atr", "adx", "bb_upper", "realized_vol",
            "atr_rank", "vwap_24", "obv_slope"]
    a = full.iloc[399][cols].astype(float).values
    b = part.iloc[399][cols].astype(float).values
    assert np.allclose(a, b, equal_nan=True)


def test_realized_vol_uses_timeframe():
    cfg15 = v29.Config(timeframe="15m", htf_timeframe="1h")
    rng = np.random.default_rng(2)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, 800)))
    df = pd.DataFrame({"ts": np.arange(800) * 900_000, "open": close,
                       "high": close, "low": close, "close": close,
                       "volume": 1.0})
    out = v29.compute_indicators(df, cfg15)
    expected = 0.002 * math.sqrt(v29._annualization_factor("15m"))
    assert abs(out["realized_vol"].iloc[-200:].mean() / expected - 1) < 0.2


# ---------- Contexte / persistance ----------

def test_transient_fields():
    ctx = v29.BotContext()
    ctx.flatten_in_progress = True
    assert ctx.to_dict()["flatten_in_progress"] is False
    ctx2 = v29.BotContext.from_dict({"schema_version": v29.SCHEMA_VERSION,
                                     "flatten_in_progress": True})
    assert ctx2.flatten_in_progress is False


def test_migration_from_v295_context():
    raw = {
        "schema_version": 10, "state": "OPEN",
        "position": {"in_position": True, "buy_price": 0.1, "sl_price": 0.097,
                     "tp_price": 0.1075, "amount_held": 2900.0,
                     "risk_per_unit": 0.003, "oco_order_id": "42",
                     "oco_atomic": True, "realized_r": 0.0},
        "portfolio": {"consec_losses": 3},
        "risk": {"halted": True, "halt_reason": "Daily DD dépassé (-3.1%)",
                 "halted_date": "2026-01-01"},
        "blockchain": {"last_pending_tx_hash": "0xabc"},
    }
    ctx = v29.BotContext.from_dict(raw)
    p = ctx.position
    assert ctx.schema_version == v29.SCHEMA_VERSION
    assert p.initial_amount == 2900.0 and p.cost_basis > p.buy_price
    assert p.risk_quote_initial > 0
    assert ctx.risk.halt_kind == v29.HaltKind.DAILY_DD
    assert ctx.portfolio.losses_since_pause == 3
    assert ctx.blockchain.pending_tx_hashes == ["0xabc"]


def test_newer_schema_is_halted():
    ctx = v29.BotContext.from_dict({"schema_version": v29.SCHEMA_VERSION + 1})
    assert ctx.risk.halted and ctx.risk.halt_reason == "SCHEMA_DOWNGRADE"


def test_from_dict_resilient():
    ctx = v29.BotContext.from_dict({"schema_version": v29.SCHEMA_VERSION,
                                    "position": "not_a_dict",
                                    "portfolio": {"paper_cash": 5000.0},
                                    "adaptive": {"signal_latencies_ms": list(range(3000))}})
    assert ctx.portfolio.paper_cash == 5000.0
    assert len(ctx.adaptive.signal_latencies_ms) <= 500


def test_store_roundtrip_and_rollback(logger):
    st = v29.Store(":memory:", logger)
    ctx = v29.BotContext()
    ctx.portfolio.paper_cash = 1234.5
    assert st.save_context(ctx, "test", force=True)
    assert st.load_context().portfolio.paper_cash == 1234.5
    with pytest.raises(RuntimeError):
        with st.transaction():
            st.conn.execute("INSERT INTO events(ts,event,severity,payload,mode)"
                            " VALUES('t','in_tx','INFO','{}','test')")
            raise RuntimeError("crash")
    assert st.conn.execute("SELECT COUNT(*) FROM events WHERE event='in_tx'"
                           ).fetchone()[0] == 0
    st.close()


def test_store_corrupt_context_fails_closed(logger):
    st = v29.Store(":memory:", logger)
    st.conn.execute("INSERT OR REPLACE INTO kv(key,value) VALUES('context','{bad')")
    with pytest.raises(RuntimeError):
        st.load_context()
    st.close()


def test_store_schema_migration(logger):
    st = v29.Store(":memory:", logger)
    st.conn.execute("DROP TABLE blockchain_txs")
    st.conn.execute("""CREATE TABLE blockchain_txs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
        chain TEXT NOT NULL, chain_id INTEGER NOT NULL, tx_hash TEXT NOT NULL,
        from_addr TEXT, to_addr TEXT, value_eth REAL, token_addr TEXT,
        token_amount REAL, gas_used INTEGER, gas_price_wei TEXT, status INTEGER,
        dry_run INTEGER NOT NULL, mode TEXT NOT NULL)""")
    st._init_schema()
    cols = {r[1] for r in st.conn.execute("PRAGMA table_info(blockchain_txs)")}
    assert {"rbf", "pending"} <= cols
    st.close()


# ---------- Notifier / heartbeat ----------

def test_notifier_critical_logged():
    records = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record)

    lg = logging.getLogger("test.notifier")
    lg.handlers = [H()]
    lg.propagate = False
    v29.Notifier("", "", logger=lg)("alerte critique", critical=True)
    assert any(r.levelno == logging.ERROR and "alerte critique" in r.getMessage()
               for r in records)


def test_heartbeat_tick(paper_env):
    cfg = make_cfg("paper")
    hb_logger = logging.getLogger("test.hb")
    hb_logger.addHandler(logging.NullHandler())
    hb = v29.Heartbeat(cfg, logging.getLogger("t"), hb_logger)
    ctx = v29.BotContext()
    ctx.position = v29.Position(in_position=True, buy_price=0.1, cost_basis=0.1001,
                                sl_price=0.097, tp_price=0.1075, amount_held=100,
                                opened_at=v29._utcnow_iso(), module="trend", tier="A")
    out = hb.tick(ctx, "TREND_UP", 1023.4, 0.101, ctx.adaptive, 1000.0,
                  subsystems=v29._compute_subsystems(ctx, None))
    assert "LONG trend/A" in out and "PROT=NONE" in out
