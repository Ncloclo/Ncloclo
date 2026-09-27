"""Conditions réelles : horloge du PC décalée, connexion lente,
vérification publique sur Binance (sans clé ni ordre)."""

import dataclasses
import io
import time
from datetime import datetime, timedelta, timezone

import pytest

import diagnostics as dg
import trend_strategy as ts
import trendguard_bot as tg
import v29
from conftest import open_live_position
from fake_binance import FakeBinanceMulti
from test_trendguard import DAY, SIM_FROM, feed, make_bot, synthetic_market


# ---------- Client Binance ----------

def test_make_binance_spot_only_long_timeout_clock_corrected():
    ex = v29.make_binance()
    assert ex.options["fetchMarkets"] == {"types": ["spot"]}
    assert ex.options["adjustForTimeDifference"] is True
    assert ex.timeout == v29.BINANCE_TIMEOUT_MS >= 30_000
    assert not ex.apiKey
    keyed = v29.make_binance("k", "s", testnet=True)
    assert keyed.apiKey == "k" and keyed.secret == "s"
    assert "testnet" in str(keyed.urls["api"]).lower()


def test_resync_clock_is_safe():
    class Down:
        def load_time_difference(self):
            raise v29.ccxt.NetworkError("timeout")
    assert v29.resync_clock(Down()) is None
    assert v29.resync_clock(object()) is None


# ---------- Horloge décalée (-1021) ----------

def test_order_retried_after_clock_resync(live_env):
    env = live_env
    env.fb.clock_skewed = True                  # l'horloge a dérivé
    res = open_live_position(env)
    assert res == v29.EntryResult.OPENED
    assert env.fb.clock_syncs == 1
    buys = [o for o in env.fb.orders.values() if o["side"] == "buy"]
    assert len(buys) == 1                        # jamais d'achat en double
    assert env.ctx.position.protection_mode != "NONE"
    assert not env.ctx.risk.halted


def test_persistent_clock_error_rejects_cleanly(live_env):
    env = live_env
    env.fb.clock_skewed = True
    env.fb.load_time_difference = lambda: 0      # le recalage ne suffit pas
    res = open_live_position(env)
    assert res != v29.EntryResult.OPENED
    assert not env.ctx.position.in_position
    assert not [o for o in env.fb.orders.values() if o["side"] == "buy"]
    assert env.ctx.pending_order is None and not env.ctx.risk.halted


def _count_syncs(fb: FakeBinanceMulti) -> int:
    return next(iter(fb.fakes.values())).clock_syncs


def test_live_bot_resyncs_clock_hourly(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("live", close, logger)
    feed(fb, close, volume)
    assert bot.boot()
    d = close.index[SIM_FROM]
    now = d.to_pydatetime() + DAY + timedelta(minutes=5)
    bot.run_cycle(now=now)
    assert _count_syncs(fb) == 1 and bot.state["clock_offset_ms"] == 0
    bot.run_cycle(now=now + timedelta(minutes=1))
    assert _count_syncs(fb) == 1                 # pas à chaque cycle
    bot._last_clock_sync -= 3601
    bot.run_cycle(now=now + timedelta(minutes=2))
    assert _count_syncs(fb) == 2


def test_paper_bot_never_signs_so_never_resyncs(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    feed(fb, close, volume)
    assert bot.boot()
    d = close.index[SIM_FROM]
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert _count_syncs(fb) == 0


def test_diagnosis_reports_compensated_clock_offset():
    class Skewed:
        def fetch_time(self):
            return int(time.time() * 1000) + 3000   # Binance 3 s « en avance »
    now = datetime.now(timezone.utc)
    findings = dg.check_system(Skewed(), {}, str(now.date()), "", None, now)
    clock = [f for f in findings if f.message.startswith("Horloge")][0]
    assert clock.level == "INFO" and "compensé par le bot" in clock.message

    class Far:
        def fetch_time(self):
            return int(time.time() * 1000) + 300_000
    clock = [f for f in dg.check_system(Far(), {}, str(now.date()), "", None, now)
             if f.message.startswith("Horloge")][0]
    assert clock.level == "ALERTE" and clock.reco


# ---------- Vérification publique (sans clé API) ----------

def _entry_day(close, volume):
    res = ts.backtest(close, volume, ts.TrendParams(),
                      str(close.index[SIM_FROM].date()), str(close.index[-1].date()))
    first = min(t["entry_date"] for t in res.trades)
    return close.index.get_loc(first)


def _public_verify(bot, fb, close, volume, day_idx):
    feed(fb, close, volume)
    d = close.index[day_idx]
    for a in close.columns:
        fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
    out = io.StringIO()
    rc = tg.cmd_verify_public(bot.g, exchange=fb, out=out,
                              now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    return rc, out.getvalue()


def test_public_verify_builds_real_orders_without_sending(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    rc, text = _public_verify(bot, fb, close, volume, _entry_day(close, volume))
    assert rc == 0, text
    assert "mode public, aucun ordre" in text
    assert "stop STOP_LOSS de" in text           # au moins un achat préparé
    assert "✅ Tout est conforme côté marché." in text
    assert not fb.open_orders() and all(not f.orders for f in fb.fakes.values())
    assert fb.free["USDT"] == pytest.approx(10_000.0)


def test_public_verify_flags_missing_stop_type(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    for f in fb.fakes.values():
        f.order_types = ["LIMIT", "MARKET", "LIMIT_MAKER"]
    rc, text = _public_verify(bot, fb, close, volume, _entry_day(close, volume))
    assert rc == 1
    assert "aucun ordre stop disponible" in text or "Chargement" in text


def test_public_verify_flags_orders_under_minimum(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, paper_capital=60.0)
    rc, text = _public_verify(bot, fb, close, volume, _entry_day(close, volume))
    assert rc == 1
    assert "Capital simulé <" in text


def test_verify_without_keys_falls_back_to_public(monkeypatch, logger):
    close, _ = synthetic_market()
    bot, _fb = make_bot("paper", close, logger)
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    called = {}
    monkeypatch.setattr(tg, "cmd_verify_public",
                        lambda g, now=None, out=None: called.setdefault("ok", 0))
    out = io.StringIO()
    assert tg.cmd_verify(bot.g, out=out) == 0
    assert "ok" in called and "vérification publique" in out.getvalue()


# ---------- Panne de données : jamais de vente sur une panne réseau ----------

def _flaky_ohlcv(fb, symbol, failures):
    """Les `failures` prochains fetch_ohlcv de `symbol` échouent (timeout)."""
    real = fb.fetch_ohlcv
    left = {"n": failures}

    def fetch(sym, timeframe="1d", since=None, limit=500):
        if sym == symbol and left["n"] > 0:
            left["n"] -= 1
            raise v29.ccxt.RequestTimeout("binance GET klines timeout")
        return real(sym, timeframe, since, limit)
    fb.fetch_ohlcv = fetch
    return left


def _run_until_holding(bot, fb, close, volume):
    feed(fb, close, volume)
    for i in range(SIM_FROM, len(close)):
        d = close.index[i]
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
        if bot.state["paper"]["holdings"]:
            return i
    raise AssertionError("aucune position ouverte")


def test_transient_ohlcv_error_is_retried(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    i = _run_until_holding(bot, fb, close, volume)
    held = next(iter(bot.state["paper"]["holdings"]))
    left = _flaky_ohlcv(fb, f"{held.upper()}/USDT", failures=2)
    d = close.index[i + 1]
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert left["n"] == 0
    assert bot.state["last_decision_day"] == str(d.date())    # décidé malgré tout


def test_held_asset_without_data_is_never_sold(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    i = _run_until_holding(bot, fb, close, volume)
    held = next(iter(bot.state["paper"]["holdings"]))
    trades_before = len(bot.state["trades"])
    left = _flaky_ohlcv(fb, f"{held.upper()}/USDT", failures=10 ** 6)
    d = close.index[i + 1]
    now = d.to_pydatetime() + DAY + timedelta(minutes=5)
    bot.run_cycle(now=now)
    assert held in bot.state["paper"]["holdings"]            # pas vendue
    assert len(bot.state["trades"]) == trades_before
    assert bot.state["last_decision_day"] != str(d.date())   # reportée
    assert "decision_deferred_since" in bot.state
    left["n"] = 0                                            # données revenues
    bot.run_cycle(now=now + timedelta(minutes=1))
    assert bot.state["last_decision_day"] == str(d.date())
    assert "decision_deferred_since" not in bot.state


def test_unheld_asset_without_data_is_skipped(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    _flaky_ohlcv(fb, "DOGE/USDT", failures=10 ** 6)
    feed(fb, close, volume)
    d = close.index[SIM_FROM]
    for a in close.columns:
        fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
    real = fb.fetch_ohlcv
    fb.fetch_ohlcv = real                                    # feed a remplacé l'historique
    _flaky_ohlcv(fb, "DOGE/USDT", failures=10 ** 6)
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert bot.state["last_decision_day"] == str(d.date())
    assert "doge" not in bot.state["paper"]["holdings"]


def test_one_pair_network_error_does_not_block_other_protections(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("live", close, logger)
    feed(fb, close, volume)
    assert bot.boot()
    for i in range(SIM_FROM, len(close)):
        d = close.index[i]
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
        held = [s for s in bot.slots.values() if s.ctx.position.in_position]
        if len(held) >= 2:
            break
    else:
        pytest.skip("pas deux positions simultanées dans ce marché synthétique")
    broken, other = held[0], held[1]
    real_ticker = fb.fetch_ticker

    def ticker(sym):
        if sym == broken.symbol:
            raise v29.ccxt.RequestTimeout("timeout")
        return real_ticker(sym)
    fb.fetch_ticker = ticker
    crash = other.ctx.position.sl_price * 0.97               # krach sur l'autre paire
    fb.set_price(other.symbol, crash)
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(hours=3))
    assert not other.ctx.position.in_position                 # protégée malgré la panne
    assert broken.ctx.position.in_position


# ---------- Diagnostic : l'état normal n'alerte pas ----------

def _book(close, risk_each, weight):
    return [{"asset": a, "qty": 10_000 * weight / close[a].iloc[-1],
             "entry": close[a].iloc[-1],
             "stop": close[a].iloc[-1] * (1 - risk_each / weight),
             "risk_quote": 10_000 * risk_each} for a in ("eth", "xrp", "doge")]


def test_full_book_at_design_cap_is_not_a_warning():
    close, _ = synthetic_market()
    prices = {a: float(close[a].iloc[-1]) for a in close.columns}
    normal = dg.check_portfolio(_book(close, 0.02, 0.22), close, prices, 10_000.0)
    risk = [f for f in normal if f.message.startswith("Perte si")][0]
    crash = [f for f in normal if f.message.startswith("Krach")]
    assert risk.level == "OK" and all(f.level == "INFO" for f in crash)
    heavy = dg.check_portfolio(_book(close, 0.05, 0.40), close, prices, 10_000.0)
    assert [f for f in heavy if f.message.startswith("Perte si")][0].level == "ATTENTION"
    assert any(f.level == "ATTENTION" and f.reco for f in heavy
               if f.message.startswith("Krach"))


def test_weekly_auto_diagnosis_includes_strategy_tournament(logger, monkeypatch):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, auto_diagnose_days=7)
    feed(fb, close, volume)
    assert bot.boot()
    seen = {}
    monkeypatch.setattr(dg, "run_diagnosis", lambda *a, **k: seen.update(k) or
                        [dg.Finding("x", "OK", "ok")])
    d = close.index[SIM_FROM]
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert "alternatives" in seen["sections"]


def test_network_outage_defers_quickly_without_waiting_every_pair(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    feed(fb, close, volume)
    assert bot.boot()
    calls = []

    def down(sym, timeframe="1d", since=None, limit=500):
        calls.append(sym)
        raise v29.ccxt.RequestTimeout("timeout")
    fb.fetch_ohlcv = down
    d = close.index[SIM_FROM]
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert bot.state.get("last_decision_day") != str(d.date())
    assert "decision_deferred_since" in bot.state
    assert len(calls) <= 3                       # BTC d'abord : arrêt immédiat


def test_stalled_cycle_dumps_thread_stacks(logger, tmp_path, monkeypatch):
    """Un cycle bloqué (ici 0,3 s au lieu de 20 min) laisse la pile des
    threads dans <journal>.blocage.txt ; un cycle normal n'écrit rien."""
    close, volume = synthetic_market()
    log = tmp_path / "tg.log"
    bot, fb = make_bot("paper", close, logger)
    bot.g = dataclasses.replace(bot.g, log_file=str(log))
    monkeypatch.setattr(tg.TrendGuardBot, "STALL_DUMP_SEC", 0.3)
    calls = {"n": 0}

    def cycle(now=None):
        calls["n"] += 1
        if calls["n"] == 2:
            time.sleep(1.0)                      # blocage simulé
            tg._running = False
    monkeypatch.setattr(bot, "run_cycle", cycle)
    monkeypatch.setattr(tg, "_sleep", lambda s: None)
    tg._running = True
    try:
        bot.run_forever()
    finally:
        tg._running = True
    dump = (tmp_path / "tg.log.blocage.txt").read_text(encoding="utf-8")
    assert "Timeout" in dump and "cycle" in dump and calls["n"] == 2
