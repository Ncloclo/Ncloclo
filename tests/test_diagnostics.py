"""Auto-diagnostic (lecture seule) et profil prudent (TG_DD_THROTTLE)."""

import dataclasses
import io
import logging
from contextlib import redirect_stdout
from datetime import timedelta

import pytest

import diagnostics as dg
import trend_strategy as ts
import trendguard_bot as tg
import v29
from test_trendguard import (DAY, N_DAYS, SIM_FROM, feed, make_bot,
                             synthetic_market)


@pytest.fixture
def logger():
    lg = logging.getLogger("test.diag")
    lg.setLevel(logging.WARNING)
    return lg


def _env(logger, **kw):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, **kw)
    feed(fb, close, volume)
    now = close.index[-1].to_pydatetime() + DAY + timedelta(minutes=5)
    return close, volume, bot, fb, now


# ---------- Profil prudent ----------

def test_parse_dd_throttle():
    assert tg.parse_dd_throttle("") == ()
    assert tg.parse_dd_throttle("0.20:0.25, 0.10:0.5") == ((0.10, 0.5), (0.20, 0.25))
    with pytest.raises(ValueError):
        tg.parse_dd_throttle("10%")
    with pytest.raises(ValueError):
        ts.TrendParams(dd_throttle=((1.5, 0.5),)).validate()


def test_risk_multiplier_steps():
    p = ts.TrendParams(dd_throttle=((0.10, 0.5), (0.20, 0.25)))
    assert ts.risk_multiplier(100.0, 100.0, p) == 1.0
    assert ts.risk_multiplier(89.0, 100.0, p) == 0.5
    assert ts.risk_multiplier(79.0, 100.0, p) == 0.25
    assert ts.risk_multiplier(50.0, 100.0, ts.TrendParams()) == 1.0   # désactivé


def test_throttle_halves_risk_in_plan():
    p = ts.TrendParams()
    snap = {"eth": {"close": 100.0, "prior_high": 95.0, "vol": 2.0, "mom": 1.0,
                    "age": 400, "vol30": 1e9}}
    full = ts.plan_entries({}, snap, True, 10_000.0, 10_000.0, p)[0]
    half = ts.plan_entries({}, snap, True, 10_000.0, 10_000.0, p, 0.5)[0]
    assert half["risk_quote"] == pytest.approx(full["risk_quote"] / 2, rel=1e-12)


def test_paper_bot_with_throttle_matches_backtest(logger):
    close, volume = synthetic_market()
    p = dataclasses.replace(ts.TrendParams(), dd_throttle=((0.02, 0.5),))
    bot, fb = make_bot("paper", close, logger, params=p)
    assert bot.boot()
    feed(fb, close, volume)
    for d in close.index[SIM_FROM:N_DAYS]:
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    res = ts.backtest(close, volume, p, str(close.index[SIM_FROM].date()),
                      str(close.index[N_DAYS - 1].date()))
    assert [round(t["pnl"], 6) for t in bot.state["trades"]] == \
        [round(t["pnl"], 6) for t in res.trades]
    ref = ts.backtest(close, volume, ts.TrendParams(),
                      str(close.index[SIM_FROM].date()),
                      str(close.index[N_DAYS - 1].date()))
    assert [t["pnl"] for t in res.trades] != [t["pnl"] for t in ref.trades]


# ---------- Diagnostic ----------

def test_full_diagnosis_runs_and_reports(logger):
    close, volume, bot, fb, now = _env(logger)
    findings = dg.run_diagnosis(fb, ts.TrendParams(), [a.upper() for a in close.columns],
                                {}, [], 10_000.0, str(close.index[-1].date()), now)
    sections = {f.section for f in findings}
    assert {"Données", "Marché", "Signaux", "Portefeuille", "Stratégie",
            "Réel vs attendu"} <= sections
    text = dg.render(findings, "(test)")
    assert "VERDICT" in text and "99 %" in text


def test_stale_candle_is_an_alert(logger):
    close, volume, bot, fb, now = _env(logger)
    fb.ohlcv[("ETH/USDT", "1d")] = fb.ohlcv[("ETH/USDT", "1d")][:-2]
    findings = dg.run_diagnosis(fb, ts.TrendParams(), [a.upper() for a in close.columns],
                                {}, [], 10_000.0, str(close.index[-1].date()), now,
                                sections=("data",))
    stale = [f for f in findings if "en retard" in f.message]
    assert stale and stale[0].level == "ALERTE" and "ETH" in stale[0].message


def test_fetch_retries_transient_errors():
    calls = {"n": 0}

    class Flaky:
        def fetch_ohlcv(self, sym, tf, since=None, limit=1000):
            calls["n"] += 1
            if calls["n"] < 3:
                raise v29.ccxt.RequestTimeout("timeout")
            return [[since, 1, 1, 1, 1, 1]]
    out = dg._fetch_retry(Flaky(), "X/USDT", 0, sleep=lambda s: None)
    assert out and calls["n"] == 3


def test_live_results_incompatible_with_history_raise_alert():
    ref = [{"r": r} for r in ([3.0] * 40 + [-1.0] * 60)]       # espérance +0.6 R
    bad = [{"r": -1.0} for _ in range(15)]
    f = dg.live_vs_expected(bad, ref)[0]
    assert f.level == "ALERTE"
    ok = dg.live_vs_expected([{"r": r} for r in [3, -1, -1, 2, -1, -1, 4, -1, -1, 1]], ref)[0]
    assert ok.level == "OK"
    few = dg.live_vs_expected(bad[:3], ref)[0]
    assert few.level == "INFO"


def test_portfolio_concentration_warning():
    close, _ = synthetic_market()                   # alts très corrélées à BTC
    holdings = [{"asset": a, "qty": 10_000 / close[a].iloc[-1] / 4,
                 "entry": close[a].iloc[-1], "stop": close[a].iloc[-1] * 0.9,
                 "risk_quote": 100.0} for a in ("eth", "xrp", "doge")]
    prices = {a: float(close[a].iloc[-1]) for a in close.columns}
    findings = dg.check_portfolio(holdings, close, prices, 10_000.0)
    text = " ".join(f.message for f in findings)
    assert "Corrélation moyenne" in text and "Krach" in text


def test_portfolio_survives_held_asset_without_history():
    """Historique d'une position indisponible (déjà signalé dans « Données ») :
    le diagnostic continue au lieu d'échouer sur un KeyError."""
    close, _ = synthetic_market()
    holdings = [{"asset": a, "qty": 1.0, "entry": 10.0, "stop": 9.0,
                 "risk_quote": 1.0} for a in ("eth", "sol")]      # sol : absent
    findings = dg.check_portfolio(holdings, close, {"eth": 10.0}, 10_000.0)
    assert "2 position(s)" in findings[0].message


def test_portfolio_survives_zero_equity():
    """equity=0.0 (aucune décision live encore prise) avec des positions
    déjà en base : pas de ZeroDivisionError, diagnostic quand même rendu."""
    close, _ = synthetic_market()
    holdings = [{"asset": "eth", "qty": 1.0, "entry": 10.0, "stop": 9.0,
                 "risk_quote": 1.0}]
    findings = dg.check_portfolio(holdings, close, {"eth": 10.0}, 0.0)
    assert any("Perte si tous les stops sont touchés" in f.message for f in findings)


def test_strategy_edge_loss_is_detected(monkeypatch):
    close, volume = synthetic_market()
    real = ts.backtest

    def losing(*a, **k):
        res = real(*a, **k)
        last = res.equity.index[-1]
        res.trades = [{"r": -1.0, "exit_date": last - timedelta(days=i)}
                      for i in range(40)]
        return res
    monkeypatch.setattr(ts, "backtest", losing)
    findings, _ = dg.strategy_health(close, volume, ts.TrendParams())
    edge = [f for f in findings if "Espérance des" in f.message][0]
    assert edge.level == "ALERTE"


def test_auto_diagnosis_weekly_and_never_breaks_trading(logger, monkeypatch):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, auto_diagnose_days=7)
    assert bot.boot()
    feed(fb, close, volume)
    runs = []
    monkeypatch.setattr(dg, "run_diagnosis",
                        lambda *a, **k: runs.append(1) or [dg.Finding("x", "OK", "ok")])
    for d in close.index[SIM_FROM:SIM_FROM + 21]:
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert len(runs) == 3

    def boom(*a, **k):
        raise RuntimeError("panne du diagnostic")
    monkeypatch.setattr(dg, "run_diagnosis", boom)
    bot.state["last_auto_diag_day"] = None
    d = close.index[SIM_FROM + 21]
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert bot.state["last_decision_day"] == str(d.date())


def test_cmd_diagnose_end_to_end(tmp_path, logger):
    close, volume, bot, fb, now = _env(logger)
    db = str(tmp_path / "tg.db")
    g = tg.GuardConfig(run_mode="paper", universe=tuple(a.upper() for a in close.columns),
                       db_file=db, log_file=str(tmp_path / "tg.log"),
                       lock_file=str(tmp_path / "tg.lock"))
    store = v29.Store(db, logger)
    px = float(close["eth"].iloc[-1])
    store.set_kv(tg.TrendGuardBot.STATE_KEY, {
        "last_equity": 10_500.0, "last_decision_day": str(close.index[-1].date()),
        "trades": [{"r": 1.5, "pnl": 150.0}],
        "paper": {"cash": 8_000.0, "holdings": {"eth": {
            "qty": 1.0, "entry": px * 0.9, "stop": px * 0.85, "high": px,
            "entry_date": "2025-01-01", "risk_quote": 100.0, "cost": px * 0.9}}}})
    store.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = tg.cmd_diagnose(g, str(tmp_path / "rapport.txt"), exchange=fb, now=now)
    out = buf.getvalue()
    assert code in (0, 1)
    assert "ETH" in out and "Bot arrêté" in out
    assert (tmp_path / "rapport.txt").read_text(encoding="utf-8").startswith("═")
    with pytest.raises(RuntimeError):
        fb.create_order("ETH/USDT", "market", "buy", 1)      # aucun ordre possible
