"""TrendGuard : stratégie (sans look-ahead, risque 1 %), parité exacte
bot ↔ backtest, exécution live sur simulateur multi-paires."""

import logging
import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import trend_strategy as ts
import trendguard_bot as tg
import v29
from fake_binance import FakeBinanceMulti

DAY = timedelta(days=1)
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
N_DAYS = 700
SIM_FROM = 420          # jours de chauffe avant la simulation


def synthetic_market(seed=3):
    """BTC en régimes haussier/baissier + 3 alts corrélées avec tendances
    propres ; volume USD constant."""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(250, 0.004), np.full(120, -0.004),
                            np.full(200, 0.005), np.full(130, -0.003)])
    btc = 30_000 * np.exp(np.cumsum(drift + rng.normal(0, 0.025, N_DAYS)))
    out = {"BTC": btc}
    for k, (name, px0) in enumerate((("ETH", 2000.0), ("XRP", 0.6),
                                     ("DOGE", 0.08))):
        idio = rng.normal(0.0005 * (k - 1), 0.035, N_DAYS)
        out[name] = px0 * np.exp(np.cumsum(1.3 * np.diff(np.log(btc),
                                                         prepend=np.log(btc[0]))
                                           + idio))
    dates = pd.date_range(START, periods=N_DAYS, freq="D", tz="UTC")
    close = pd.DataFrame({k.lower(): v for k, v in out.items()}, index=dates)
    volume = pd.DataFrame(1e8, index=dates, columns=close.columns)
    return close, volume


def feed(fb, close, volume):
    for a in close.columns:
        sym = f"{a.upper()}/USDT"
        bars = []
        for d, px in close[a].items():
            ms = int(d.timestamp() * 1000)
            bars.append([ms, px, px * 1.01, px * 0.99, px,
                         volume[a].loc[d] / px])
        fb.ohlcv[(sym, "1d")] = bars


def make_bot(run_mode, close, logger, **kw):
    prices = {f"{a.upper()}/USDT": float(close[a].iloc[0]) for a in close.columns}
    fb = FakeBinanceMulti(prices, quote_balance=10_000.0, step=0.00001,
                          tick=0.000001, min_notional=5.0)
    extra = {}
    kw.setdefault("auto_diagnose_days", 0)
    if run_mode == "live":
        extra = dict(enable_live_trading=True,
                     live_confirmation="I_UNDERSTAND_RISK")
    g = tg.GuardConfig(run_mode=run_mode,
                       universe=tuple(a.upper() for a in close.columns),
                       db_file=":memory:", log_file="/dev/null",
                       lock_file="/dev/null", **extra, **kw)
    store = v29.Store(":memory:", logger)
    bot = tg.TrendGuardBot(g, logger, fb, store,
                           v29.Notifier("", "", logger=logger))
    bot.sleep = lambda s: None
    return bot, fb


def run_days(bot, fb, close, volume, first, last):
    feed(fb, close, volume)
    for d in close.index[first:last]:
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))


@pytest.fixture
def logger():
    lg = logging.getLogger("test.tg")
    lg.setLevel(logging.WARNING)
    return lg


# ---------- Stratégie ----------

def test_features_are_causal():
    close, volume = synthetic_market()
    p = ts.TrendParams()
    full = ts.asset_features(close["eth"], p, volume["eth"])
    part = ts.asset_features(close["eth"].iloc[:500], p, volume["eth"].iloc[:500])
    cols = ["vol", "prior_high", "mom", "vol30"]
    assert np.allclose(full.iloc[499][cols].values.astype(float),
                       part.iloc[499][cols].values.astype(float), equal_nan=True)


def test_position_risk_is_one_percent():
    p = ts.TrendParams()
    snap = {"eth": {"close": 100.0, "prior_high": 95.0, "vol": 2.0, "mom": 1.0,
                    "age": 400, "vol30": 1e9}}
    plans = ts.plan_entries({}, snap, True, 10_000.0, 10_000.0, p)
    assert len(plans) == 1
    pl = plans[0]
    assert pl["stop"] == pytest.approx(100.0 - 3 * 2.0)
    # Perte au stop (frais + slippage compris) = 1 % de l'equity.
    loss = pl["qty"] * (pl["entry"] * (1 + p.fee) - pl["stop"] * (1 - p.fee - p.slippage))
    assert loss == pytest.approx(100.0, rel=1e-9)


def test_no_entry_in_bear_regime_and_caps():
    p = ts.TrendParams()
    snap = {f"a{i}": {"close": 100.0, "prior_high": 95.0, "vol": 2.0,
                      "mom": float(i), "age": 400, "vol30": 1e9}
            for i in range(12)}
    assert ts.plan_entries({}, snap, False, 10_000.0, 10_000.0, p) == []
    plans = ts.plan_entries({}, snap, True, 10_000.0, 10_000.0, p)
    assert len(plans) == 6                     # plafond de risque total 6 %
    assert plans[0]["asset"] == "a11"          # classé par momentum
    illiquid = dict(snap["a0"], vol30=1e5)
    assert not ts.entry_signal(illiquid, p)


def test_trailing_stop_only_rises_and_tightens_in_bear():
    p = ts.TrendParams()
    h = {"eth": ts.Holding("eth", 1.0, 100.0, 94.0, 100.0, None, 6.0, 100.0)}
    ts.update_positions(h, {"eth": {"close": 120.0, "vol": 2.0}}, True, p)
    assert h["eth"].stop == pytest.approx(120 - 5 * 2)
    ts.update_positions(h, {"eth": {"close": 115.0, "vol": 4.0}}, True, p)
    assert h["eth"].stop == pytest.approx(110.0)           # ne baisse jamais
    ts.update_positions(h, {"eth": {"close": 116.0, "vol": 2.0}}, False, p)
    assert h["eth"].stop == pytest.approx(120 - 2 * 2.0)   # resserré (bear)
    exits = ts.update_positions(h, {"eth": {"close": 115.0, "vol": 2.0}}, False, p)
    assert exits == [("eth", "STOP")]


def test_backtest_accounting_and_risk():
    close, volume = synthetic_market()
    res = ts.backtest(close, volume, ts.TrendParams(),
                      str(close.index[SIM_FROM].date()), str(close.index[-1].date()))
    assert res.metrics["trades"] > 3
    losers = [t["r"] for t in res.trades if t["r"] < 0]
    assert losers and all(r > -3.0 for r in losers)
    assert np.mean(losers) == pytest.approx(-1.0, abs=0.35)


# ---------- Parité bot ↔ backtest ----------

def test_paper_bot_matches_backtest_exactly(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, N_DAYS)
    res = ts.backtest(close, volume, ts.TrendParams(),
                      str(close.index[SIM_FROM].date()),
                      str(close.index[N_DAYS - 1].date()))
    bt = [(t["asset"], round(t["pnl"], 6)) for t in res.trades]
    live = [(t["asset"], round(t["pnl"], 6)) for t in bot.state["trades"]]
    assert len(bt) > 3
    assert live == bt
    book = bot.state["paper"]
    last = close.iloc[N_DAYS - 1]
    equity = book["cash"] + sum(h["qty"] * last[a]
                                for a, h in book["holdings"].items())
    assert equity == pytest.approx(res.equity.iloc[-1], rel=1e-9)


def test_bot_ignores_future_candles(logger):
    """Le simulateur expose TOUTES les bougies (y compris futures) : le bot
    doit n'utiliser que les bougies clôturées à `now`."""
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    bot.boot()
    feed(fb, close, volume)
    d = close.index[SIM_FROM]
    snap, bull, _ = bot._market_snapshot(d.to_pydatetime() + DAY + timedelta(minutes=5),
                                         str(d.date()))
    assert snap["btc"]["close"] == pytest.approx(close["btc"].loc[d])


def test_last_closed_day():
    now = datetime(2026, 5, 24, 0, 5, tzinfo=timezone.utc)
    assert tg.last_closed_day(now, 120) == "2026-05-23"
    early = datetime(2026, 5, 24, 0, 1, tzinfo=timezone.utc)
    assert tg.last_closed_day(early, 120) == "2026-05-22"


# ---------- Live (simulateur multi-paires) ----------

def test_live_bot_places_exchange_stops_and_trails(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("live", close, logger)
    feed(fb, close, volume)
    assert bot.boot()
    stops = {}
    for d in close.index[SIM_FROM:N_DAYS]:
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
        for base, s in bot.slots.items():
            p = s.ctx.position
            if p.in_position:
                key = (base, p.opened_at)
                lo, hi = stops.get(key, (p.sl_price, p.sl_price))
                stops[key] = (min(lo, p.sl_price), max(hi, p.sl_price))
                # Protection exchange : un STOP_LOSS, jamais de TP.
                orders = [o for o in fb.fakes[s.symbol].orders.values()
                          if o["status"] == "NEW" and o["side"] == "sell"]
                assert [o["type"] for o in orders] == ["STOP_LOSS"]
                assert abs(orders[0]["stop"] - p.sl_price) < 1e-9
                assert orders[0]["amount"] <= p.amount_held + 1e-9
                assert p.soft_stop > p.sl_price           # catastrophe sous clôture
    trades = bot.state["trades"]
    assert len(trades) > 3
    # Le stop exchange suit le trailing (au moins une remontée).
    assert any(hi > lo * 1.01 for lo, hi in stops.values())
    assert not any(s.ctx.risk.halted for s in bot.slots.values())
    assert not any(s.ctx.orphan_balance for s in bot.slots.values())
    # PnL live cohérent avec le backtest (frais en base, arrondis) à ±15 %.
    res = ts.backtest(close, volume, ts.TrendParams(),
                      str(close.index[SIM_FROM].date()),
                      str(close.index[N_DAYS - 1].date()))
    live_pnl = sum(t["pnl"] for t in trades)
    bt_pnl = sum(t["pnl"] for t in res.trades)
    assert len(trades) == len(res.trades)
    assert live_pnl == pytest.approx(bt_pnl, rel=0.10)


def test_live_catastrophe_stop_between_closes(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("live", close, logger)
    feed(fb, close, volume)
    assert bot.boot()
    for d in close.index[SIM_FROM:N_DAYS]:
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
        held = [s for s in bot.slots.values() if s.ctx.position.in_position]
        if held:
            break
    s = held[0]
    n = len(bot.state["trades"])
    crash = s.ctx.position.sl_price * 0.97
    fb.set_price(s.symbol, crash)                 # krach intrajournalier
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(hours=6))
    assert not s.ctx.position.in_position
    assert len(bot.state["trades"]) == n + 1
    t = bot.state["trades"][-1]
    assert t["pnl"] < 0 and t["r"] > -2.5


def test_paper_crash_exits_at_raised_catastrophe_stop(logger):
    """Paper : une fois le stop catastrophe remonté avec le trailing, un
    krach entre deux clôtures sort à ce niveau remonté, pas au niveau
    d'entrée."""
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    feed(fb, close, volume)
    book = bot.state["paper"]["holdings"]
    first: dict = {}
    raised = None
    for d in close.index[SIM_FROM:N_DAYS]:
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
        for a, h in book.items():
            first.setdefault((a, h["entry_date"]), h["disaster"])
            if h["disaster"] > first[(a, h["entry_date"])] * 1.01:
                raised = a
        if raised:
            break
    assert raised, "aucune remontée du stop catastrophe paper"
    h = book[raised]
    assert h["disaster"] < h["stop"]                 # toujours sous le stop de clôture
    n = len(bot.state["trades"])
    crash = h["disaster"] * 0.99
    fb.set_price(f"{raised.upper()}/USDT", crash)    # krach intrajournalier
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(hours=6))
    assert raised not in book
    t = bot.state["trades"][n]
    assert t["reason"] == "EXCHANGE_STOP" and t["exit"] == pytest.approx(crash)


def test_heartbeat_countdown_before_daily_decision(logger, caplog):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 1)
    d = close.index[SIM_FROM]
    lg = logging.getLogger("test.tg.hb2")
    bot.logger = lg
    with caplog.at_level(logging.INFO, logger="test.tg.hb2"):
        bot._last_heartbeat = 0.0                    # 00:01 UTC : décision à 00:02
        bot._heartbeat(d.to_pydatetime() + 2 * DAY + timedelta(minutes=1))
        bot.state["last_decision_day"] = "2000-01-01"   # décision reportée
        bot._last_heartbeat = 0.0
        bot._heartbeat(d.to_pydatetime() + 2 * DAY + timedelta(hours=3))
    beats = [r.getMessage() for r in caplog.records if "[HEARTBEAT]" in r.getMessage()]
    assert "prochaine décision dans 0 h 01" in beats[0]
    assert "en attente" in beats[1]


def test_kill_switch_blocks_entries(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, kill_drawdown=0.01)
    bot.boot()
    bot.state["peak_equity"] = 50_000.0
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 60)
    assert bot.state["halted"]
    assert bot.state["paper"]["holdings"] == {}


def test_live_requires_confirmation():
    with pytest.raises(ValueError):
        tg.GuardConfig(run_mode="live", enable_live_trading=True)


def test_tg_env_doc_complete():
    # Variables lues par le bot, ses alertes (alerts.py) et son panneau.
    import os
    root = os.path.dirname(os.path.abspath(tg.__file__))
    src = "".join(open(os.path.join(root, f), encoding="utf-8").read()
                  for f in ("trendguard_bot.py", "alerts.py", os.path.join("panel", "server.py"),
                            os.path.join("panel", "assistant.py")))
    used = set(re.findall(
        r'(?:os\.environ\.get\(|_env_[a-z]+\(|_env\(env,|env\.get\()\s*"([A-Z0-9_]+)"', src))
    assert used - set(tg.TG_ENV_DOC) == set()
    assert set(tg.TG_ENV_DOC) - used == set()


def test_replay_matches_backtest(tmp_path):
    """Le rejeu paper (vrai bot, jour par jour) = backtest de recherche."""
    close, volume = synthetic_market()
    for a in close.columns:
        pd.DataFrame({"time": close.index.strftime("%Y-%m-%d"),
                      "PriceUSD": close[a].values,
                      "volume_reported_spot_usd_1d": volume[a].values}
                     ).to_csv(tmp_path / f"{a}.csv", index=False)
    start = str(close.index[SIM_FROM].date())
    import io
    res = tg.replay(str(tmp_path), start, capital=10_000.0, out=io.StringIO())
    c2, v2 = ts.load_coinmetrics(str(tmp_path), ts.DEFAULT_UNIVERSE)
    bt = ts.backtest(c2, v2, ts.TrendParams(), start, str(close.index[-1].date()))
    assert [round(t["pnl"], 6) for t in res["trades"]] == \
        [round(t["pnl"], 6) for t in bt.trades]
    assert res["equity"].iloc[-1] == pytest.approx(bt.equity.iloc[-1], rel=1e-9)


def test_boot_without_network_fails_cleanly(logger):
    close, _ = synthetic_market()
    bot, fb = make_bot("paper", close, logger)

    def down():
        raise v29.ccxt.NetworkError("binance GET exchangeInfo")
    fb.load_markets = down
    assert bot.boot() is False


def test_health_check(tmp_path, logger, monkeypatch):
    close, volume = synthetic_market()
    db = str(tmp_path / "tg.db")
    g = tg.GuardConfig(run_mode="paper", db_file=db, log_file="/dev/null",
                       lock_file="/dev/null")
    assert tg.health_check(g, 600) == 1            # aucun cycle encore
    store = v29.Store(db, logger)
    state = {"last_cycle_ts": v29.time.time(), "halted": False,
             "last_decision_day": "2026-09-25"}
    store.set_kv(tg.TrendGuardBot.STATE_KEY, state)
    assert tg.health_check(g, 600) == 0
    state["last_cycle_ts"] -= 3600                 # bot bloqué depuis 1 h
    store.set_kv(tg.TrendGuardBot.STATE_KEY, state)
    assert tg.health_check(g, 600) == 1
    state.update(last_cycle_ts=v29.time.time(), halted=True, halt_reason="DD")
    store.set_kv(tg.TrendGuardBot.STATE_KEY, state)
    assert tg.health_check(g, 600) == 1
    store.close()


def test_cycle_records_heartbeat(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 1)
    assert abs(bot.state["last_cycle_ts"] - v29.time.time()) < 60


def test_heartbeat_logged_once_per_interval(caplog):
    close, volume = synthetic_market()
    lg = logging.getLogger("test.tg.hb")
    bot, fb = make_bot("paper", close, lg)
    assert bot.boot()
    with caplog.at_level(logging.INFO, logger="test.tg.hb"):
        run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 3)
    beats = [r.getMessage() for r in caplog.records if "[HEARTBEAT]" in r.getMessage()]
    assert len(beats) == 1                          # 15 min pas encore écoulées
    assert "equity" in beats[0] and "prochaine décision dans" in beats[0]


def test_set_env_var_replaces_and_keeps_file_private(tmp_path):
    import os, stat
    env = tmp_path / ".env"
    env.write_text("# BINANCE_API_SECRET=exemple\nRUN_MODE=paper\nBINANCE_API_SECRET=\n")
    tg.set_env_var(str(env), "BINANCE_API_SECRET", "S" * 64)
    lines = env.read_text().splitlines()
    assert lines == ["# BINANCE_API_SECRET=exemple", "RUN_MODE=paper",
                     "BINANCE_API_SECRET=" + "S" * 64]
    if os.name == "posix":   # Windows ignore les droits Unix (ACL du profil)
        assert stat.S_IMODE(os.stat(env).st_mode) == 0o600
    tg.set_env_var(str(env), "TG_RISK_PCT", "0.005")            # absente → ajoutée
    assert env.read_text().splitlines()[-1] == "TG_RISK_PCT=0.005"


def test_clean_api_secret():
    good = "aB3" * 21 + "x"
    assert tg.clean_api_secret(f'  "{good}"\n') == good
    for bad in ("", "abc", good + "!", good[:-1], "é" * 64):
        with pytest.raises(ValueError):
            tg.clean_api_secret(bad)


def test_set_secret_command_masks_input(tmp_path, monkeypatch, capsys):
    import getpass
    env = tmp_path / ".env"
    env.write_text("BINANCE_API_SECRET=\n")
    secret = "Z9" * 32
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": secret)
    assert tg.cmd_set_secret(str(env)) == 0
    assert f"BINANCE_API_SECRET={secret}" in env.read_text()
    assert secret not in capsys.readouterr().out                 # jamais affiché
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "trop-court")
    assert tg.cmd_set_secret(str(env)) == 1
    assert f"BINANCE_API_SECRET={secret}" in env.read_text()      # inchangé


def test_set_panel_password_is_masked_confirmed_and_exact(tmp_path, monkeypatch, capsys):
    import getpass
    from dotenv import dotenv_values
    env = tmp_path / ".env"
    env.write_text("RUN_MODE=paper\n")
    pw = "Mon#Pass $word 2026"
    answers = iter([pw, pw])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert tg.cmd_set_panel_password(str(env), ask=lambda q: "o") == 0
    values = dotenv_values(env)
    assert values["PANEL_PASSWORD"] == pw and values["PANEL_HOST"] == "0.0.0.0"
    assert pw not in capsys.readouterr().out                     # jamais affiché
    for first, second in (("Différent123!", "Autre12345!"), ("court1!", "court1!"),
                          ("aaaaaaaaaaaa", "aaaaaaaaaaaa"), ("avec'apostrophe1", "avec'apostrophe1")):
        answers = iter([first, second])
        assert tg.cmd_set_panel_password(str(env), ask=lambda q: "n") == 1
    assert dotenv_values(env)["PANEL_PASSWORD"] == pw            # inchangé


def test_capital_cap_sizes_on_capped_capital(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, max_capital=1_000.0)
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, N_DAYS)
    book = bot.state["paper"]
    trades = bot.state["trades"]
    assert trades
    # Le risque par trade est ~1 % de ~1 000, pas de 10 000.
    losers = [t["pnl"] for t in trades if t["reason"] == "STOP" and t["pnl"] < 0]
    assert losers and max(abs(x) for x in losers) < 40
    # Le bot n'a jamais investi plus que son capital (plafond + gains).
    assert 10_000 - book["cash"] <= 1_000 + max(0.0, bot.state["realized_pnl_total"]) + 1e-6


def test_capital_cap_kill_switch_uses_bot_equity(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, max_capital=1_000.0)
    bot.boot()
    bot.state["peak_equity"] = 1_000.0
    bot.state["realized_pnl_total"] = -500.0      # le bot a perdu 50 % de SON capital
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 1)
    assert bot.state["halted"]                    # alors que le compte ne perd que 5 %


def _verify(bot, fb, close, volume, day_idx=SIM_FROM + 60, **kw):
    import io
    feed(fb, close, volume)
    d = close.index[day_idx]
    for a in close.columns:
        fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
    out = io.StringIO()
    rc = tg.cmd_verify(bot.g, exchange=fb, out=out,
                       now=d.to_pydatetime() + DAY + timedelta(minutes=5), **kw)
    return rc, out.getvalue()


def test_verify_places_no_order_and_reports_plan(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    rc, text = _verify(bot, fb, close, volume)
    assert rc == 0, text
    assert "✅ Prêt pour le mode réel." in text
    assert "Retrait autorisé      : non ✓" in text
    assert "Valeur totale estimée : 10,000.00 USDT" in text
    assert not fb.open_orders() and all(not f.orders for f in fb.fakes.values())
    assert fb.free["USDT"] == pytest.approx(10_000.0)


def test_verify_fails_when_withdrawals_enabled(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    fb.restrictions["enableWithdrawals"] = True
    rc, text = _verify(bot, fb, close, volume)
    assert rc == 1 and "OUI ❌ à désactiver" in text


def test_verify_reports_rejected_key(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)

    def refused(params=None):
        raise v29.ccxt.AuthenticationError(
            'binance {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}')
    fb.sapi_get_account_apirestrictions = refused
    rc, text = _verify(bot, fb, close, volume)
    assert rc == 1 and "adresse IP non autorisée" in text


def test_resume_refused_while_bot_runs(tmp_path, monkeypatch, capsys):
    db, lock = str(tmp_path / "tg.db"), str(tmp_path / "tg.lock")
    monkeypatch.setenv("RUN_MODE", "paper")
    monkeypatch.setenv("TG_DB_FILE", db)
    monkeypatch.setenv("TG_LOCK_FILE", lock)
    store = v29.Store(db, logging.getLogger("t"))
    store.set_kv(tg.TrendGuardBot.STATE_KEY, {"halted": True, "halt_reason": "DD"})
    store.close()
    running = v29.acquire_instance_locks(lock, db)          # le bot tourne
    try:
        assert tg.main(["resume"]) == 1
        assert "Arrêtez d'abord le bot" in capsys.readouterr().out
    finally:
        v29.release_locks(running)
    assert tg.main(["resume"]) == 0                          # bot arrêté → OK
    st = v29.Store(db, logging.getLogger("t"))
    assert st.get_kv(tg.TrendGuardBot.STATE_KEY)["halted"] is False
    st.close()


def test_live_boot_refuses_foreign_bot_orders(logger):
    close, _ = synthetic_market()
    bot, fb = make_bot("live", close, logger)
    feed(fb, close, _)
    sym = "ETH/USDT"
    fb.free["ETH"] = 5.0
    fb.fakes[sym].create_order(sym, "STOP_LOSS", "sell", 5.0, None,
                               {"stopPrice": float(close["eth"].iloc[0]) * 0.5,
                                "newClientOrderId": "QB0000000001deadbeef"})
    assert bot.boot() is False
    assert fb.fakes[sym].open_protection_orders()          # ordre intact


def test_live_boot_adopts_with_explicit_recovery(logger):
    close, _ = synthetic_market()
    bot, fb = make_bot("live", close, logger, allow_recovery=True)
    feed(fb, close, _)
    assert bot.boot() is True


def test_verify_explains_missing_trading_permission(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    fb.restrictions["enableSpotAndMarginTrading"] = False

    def refused(params):
        raise v29.ccxt.AuthenticationError(
            'binance {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}')
    for f in fb.fakes.values():
        f.privatePostOrderTest = refused
    rc, text = _verify(bot, fb, close, volume)
    assert rc == 1
    assert "order/test refusé" in text and "Activer le trading Spot" in text


def test_verify_flags_insufficient_capital(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    fb.free["USDT"] = 15.0
    rc, text = _verify(bot, fb, close, volume)
    assert rc == 1 and "Capital insuffisant" in text


# ---------- Décision tardive (prix d'exécution ≠ clôture) ----------

def test_reprice_entry_keeps_plan_at_close_price():
    p = ts.TrendParams()
    snap = {"eth": {"close": 100.0, "prior_high": 95.0, "vol": 2.0, "mom": 1.0,
                    "age": 400, "vol30": 1e9}}
    plan = ts.plan_entries({}, snap, True, 10_000.0, 10_000.0, p)[0]
    same = ts.reprice_entry(plan, 100.0, 10_000.0, 10_000.0, p)
    assert same["qty"] == pytest.approx(plan["qty"], rel=1e-12)
    assert same["risk_quote"] == pytest.approx(plan["risk_quote"], rel=1e-12)


def test_reprice_entry_never_risks_more_than_planned():
    p = ts.TrendParams()
    snap = {"eth": {"close": 100.0, "prior_high": 95.0, "vol": 2.0, "mom": 1.0,
                    "age": 400, "vol30": 1e9}}
    plan = ts.plan_entries({}, snap, True, 10_000.0, 10_000.0, p)[0]
    up = ts.reprice_entry(plan, 110.0, 10_000.0, 10_000.0, p)
    assert up["qty"] < plan["qty"]
    loss_at_stop = up["qty"] * (up["entry"] * (1 + p.fee)
                                - plan["stop"] * (1 - p.fee - p.slippage))
    assert loss_at_stop == pytest.approx(plan["risk_quote"], rel=1e-9)
    # Retombé près du stop : cassure invalidée, pas d'entrée.
    assert ts.reprice_entry(plan, plan["stop"] + 0.4 * plan["vol"],
                            10_000.0, 10_000.0, p) is None


def test_late_paper_decision_buys_at_current_price(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    feed(fb, close, volume)
    assert bot.boot()
    for d in close.index[SIM_FROM:N_DAYS]:
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]) * 1.05)
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(hours=18))
        if bot.state["paper"]["holdings"]:
            break
    a, h = next(iter(bot.state["paper"]["holdings"].items()))
    c = float(close[a].loc[d])
    p = ts.TrendParams()
    assert h["entry"] == pytest.approx(c * 1.05 * (1 + p.slippage), rel=1e-9)
    loss = h["qty"] * (h["entry"] * (1 + p.fee) - h["stop"] * (1 - p.fee - p.slippage))
    assert loss <= p.risk_pct * 10_000 * 1.0001


def _day_of(iso_now: str):
    """Jour de bougie traité par un cycle lancé à `iso_now` (J+1 00:05)."""
    return (datetime.fromisoformat(iso_now) - DAY - timedelta(minutes=5)).date()


def test_missed_days_helper():
    idx = pd.date_range("2026-09-20", periods=8, freq="D", tz="UTC")
    assert tg.TrendGuardBot._missed_days(idx, None, "2026-09-26") == []
    assert tg.TrendGuardBot._missed_days(idx, "2026-09-25", "2026-09-26") == []
    assert tg.TrendGuardBot._missed_days(idx, "2026-09-22", "2026-09-26") == \
        ["2026-09-23", "2026-09-24", "2026-09-25"]


def test_stops_hit_while_bot_stopped_are_caught_up(logger):
    """Bot arrêté plusieurs jours (Codespace en veille, redémarrage…) : au
    retour, les stops franchis pendant l'arrêt sont exécutés et les stops des
    autres positions ont suivi le trailing, comme si le bot avait tourné."""
    close, volume = synthetic_market()
    daily, fb_a = make_bot("paper", close, logger)
    daily.boot()
    run_days(daily, fb_a, close, volume, SIM_FROM, N_DAYS)
    dates = [d.date() for d in close.index]
    trade = next(t for t in daily.state["trades"] if t["reason"] == "STOP"
                 and (_day_of(t["date"]) - _day_of(t["entry_date"])).days >= 4)
    entry_i = dates.index(_day_of(trade["entry_date"]))
    exit_i = dates.index(_day_of(trade["date"]))
    back_i = exit_i + 2                     # redémarrage 2 jours après le stop

    stopped, fb_b = make_bot("paper", close, logger)
    stopped.boot()
    run_days(stopped, fb_b, close, volume, SIM_FROM, entry_i + 1)
    assert trade["asset"] in stopped.state["paper"]["holdings"]
    run_days(stopped, fb_b, close, volume, back_i, back_i + 1)

    late = [t for t in stopped.state["trades"] if t["asset"] == trade["asset"]
            and t["entry_date"] == trade["entry_date"]]
    assert [t["reason"] for t in late] == ["STOP_LATE"]
    assert stopped.state["last_decision_day"] == str(dates[back_i])
    # Positions tenues par les deux bots (même entrée) : même stop.
    replay, fb_c = make_bot("paper", close, logger)
    replay.boot()
    run_days(replay, fb_c, close, volume, SIM_FROM, back_i + 1)
    ref = replay.state["paper"]["holdings"]
    for a, h in stopped.state["paper"]["holdings"].items():
        if a in ref and ref[a]["entry_date"] == h["entry_date"]:
            assert h["stop"] == pytest.approx(ref[a]["stop"])
            assert h["high"] == pytest.approx(ref[a]["high"])


def test_paper_disaster_stop_follows_trailing(logger):
    """Paper = réel : le stop catastrophe simulé remonte avec le trailing et
    reste sous le stop de clôture."""
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    bot.boot()
    first: dict = {}
    raised = False
    feed(fb, close, volume)
    for d in close.index[SIM_FROM:N_DAYS]:
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
        for a, h in bot.state["paper"]["holdings"].items():
            key = (a, h["entry_date"])
            first.setdefault(key, h["disaster"])
            assert h["disaster"] < h["stop"]
            raised = raised or h["disaster"] > first[key] * 1.01
    assert raised


def test_paper_disaster_stop_never_raised_above_market(logger):
    """_raise_paper_disaster (comme _raise_exchange_stops en réel) ne doit
    jamais remonter le stop catastrophe simulé au ras ou au-dessus du prix
    courant : Binance rejetterait un stop de vente déjà franchi. Dans ce
    cas, la sortie reste gérée par le stop de clôture du jour suivant."""
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    bot.state["paper"]["holdings"]["eth"] = {"disaster": 90.0}
    fb.set_price("ETH/USDT", 100.0)
    holdings = {"eth": ts.Holding(asset="eth", qty=1.0, entry=95.0, stop=99.5,
                                  high=100.0, entry_date="2026-01-01",
                                  risk_quote=1.0, cost=95.0)}
    # catastrophe_atr par défaut = 1.0 → cible = 99.5 - 1.0*0.01 = 99.49,
    # au-dessus de 99 % du dernier prix (99.0) : la remontée est refusée.
    bot._raise_paper_disaster(holdings, {"eth": {"vol": 0.01}})
    assert bot.state["paper"]["holdings"]["eth"]["disaster"] == 90.0


def test_live_catch_up_closes_position_and_its_exchange_stop(logger):
    close, volume = synthetic_market()
    daily, fb_a = make_bot("paper", close, logger)
    daily.boot()
    run_days(daily, fb_a, close, volume, SIM_FROM, N_DAYS)
    dates = [d.date() for d in close.index]
    trade = next(t for t in daily.state["trades"] if t["reason"] == "STOP"
                 and (_day_of(t["date"]) - _day_of(t["entry_date"])).days >= 4)
    entry_i = dates.index(_day_of(trade["entry_date"]))
    back_i = dates.index(_day_of(trade["date"])) + 2

    bot, fb = make_bot("live", close, logger)
    feed(fb, close, volume)
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, entry_i + 1)
    slot = bot.slots[trade["asset"].upper()]
    old_entry = slot.ctx.position.opened_at
    assert slot.ctx.position.in_position
    run_days(bot, fb, close, volume, back_i, back_i + 1)
    assert any(t["asset"] == trade["asset"] and "STOP_LATE" in t["reason"]
               for t in bot.state["trades"])
    p = slot.ctx.position
    # Ancienne position soldée ; une nouvelle entrée le même jour est
    # possible (cassure), protégée par un seul stop à sa taille.
    assert not p.in_position or p.opened_at != old_entry
    sells = [o for o in fb.fakes[slot.symbol].orders.values()
             if o["status"] == "NEW" and o["side"] == "sell"]
    assert len(sells) == (1 if p.in_position else 0)
    if p.in_position:
        assert sells[0]["amount"] <= p.amount_held + 1e-9
    assert not slot.ctx.risk.halted and not slot.ctx.orphan_balance


def test_format_markdown_rules():
    src = ("| a | b |\n|---|:--:|\n| 1 | 2 |\n\n```\nx = 1\n```\n\n"
           "- " + "mot " * 30 + ": fin ;\n")
    out = ts.format_markdown(src)
    assert "| --- | :---: |" in out
    assert "```text" in out
    lines = out.split("\n")
    assert all(len(line) <= 80 for line in lines if not line.startswith("|"))
    assert not any(line.lstrip().startswith((":", ";")) for line in lines)
    assert all(line.startswith("  ") for line in lines[lines.index(
        next(line for line in lines if line.startswith("- "))) + 1:] if line)
    assert ts.format_markdown(out) == out                 # idempotent
    # Liste collée à un paragraphe : ligne vide insérée (MD032).
    assert ts.format_markdown("Texte :\n- a\n- b") == "Texte :\n\n- a\n- b"


def test_repository_markdown_is_formatted():
    """README et rapport respectent les règles markdownlint du dépôt."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docs = ["README.md"] + [os.path.join("docs", n) for n in (
        "TRENDGUARD_REPORT.md", "ADAPTATION.md", "STRATEGIES.md")]
    names = [n for n in docs if os.path.exists(os.path.join(root, n))]
    if not names:
        pytest.skip("documentation absente (image Docker)")
    for name in names:
        text = open(os.path.join(root, name), encoding="utf-8").read().rstrip("\n")
        assert ts.format_markdown(text) == text, name
