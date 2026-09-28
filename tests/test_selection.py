"""Sélection des cryptos : classement par bénéfice de la stratégie (achats
ET ventes, sans regard vers le futur), auto-sélection des 10 plus rentables
ou sélection manuelle, respectées par le bot."""

import dataclasses
import json
import logging

import numpy as np
import pandas as pd
import pytest

import trendguard_bot as tg
from test_trendguard import SIM_FROM, make_bot, run_days, synthetic_market
from trendguard import autonomy
from trendguard import trend_strategy as ts

P = ts.TrendParams()


@pytest.fixture
def logger():
    lg = logging.getLogger("test.selection")
    lg.setLevel(logging.WARNING)
    return lg


def test_track_records_follow_the_bot_rules_and_include_the_open_trade():
    close, volume = synthetic_market()
    cols, reg = ts.precompute(close, volume, P)
    rec = ts.asset_track_records(cols, reg, P)
    assert set(rec) == set(close.columns)
    for a, trades in rec.items():
        for t in trades[:-1]:
            assert t["exit_i"] is not None and t["entry_i"] < t["exit_i"]
            assert bool(reg[t["entry_i"]])                  # achat en régime haussier seulement
            assert t["r"] > -3                              # perte limitée par le stop
    assert any(t["r"] > 2 for ts_ in rec.values() for t in ts_)   # des gains de plusieurs R


def test_scores_never_look_into_the_future():
    close, volume = synthetic_market()
    cols, reg = ts.precompute(close, volume, P)
    rec = ts.asset_track_records(cols, reg, P)
    closes = {a: c["close"] for a, c in cols.items()}
    t = next(t for tr in rec.values() for t in tr if t["exit_i"] and t["exit_i"] - t["entry_i"] > 20)
    a = next(a for a, tr in rec.items() if t in tr)
    mid = t["entry_i"] + 10
    s_mid = ts.selection_scores(rec, close.index, mid, 730, closes, P)[a]
    # Même résultat si l'on recalcule l'historique en s'arrêtant à ce jour-là.
    cut = {k: {f: v[:mid + 1] for f, v in c.items()} for k, c in cols.items()}
    rec_cut = ts.asset_track_records(cut, reg[:mid + 1], P)
    s_cut = ts.selection_scores(rec_cut, close.index[:mid + 1], mid, 730)[a]
    assert s_mid["total_r"] == pytest.approx(s_cut["total_r"])
    assert s_mid["trades"] == s_cut["trades"]


def test_rank_selection_keeps_members_until_they_clearly_fall_behind():
    scores = {a: {"total_r": float(20 - k), "trades": 5} for k, a in enumerate("abcdefghijklmnop")}
    eligible = list(scores)
    top = ts.rank_selection(scores, eligible, 10)
    assert top == list("abcdefghij")
    # « l » (rang 12) déjà choisie : gardée (hystérésis 3) ; « o » (rang 15) : sortie.
    kept = ts.rank_selection(scores, eligible, 10, keep=["l", "o"])
    assert "l" in kept and "o" not in kept and len(kept) == 10
    assert ts.rank_selection(scores, ["a", "b"], 10) == ["a", "b"]


def _bot(tmp_path, close, logger, **kw):
    bot, fb = make_bot("paper", close, logger, **kw)
    bot.g = dataclasses.replace(bot.g, lock_file=str(tmp_path / "tg.lock"))
    assert bot.boot()
    return bot, fb


def test_manual_selection_is_respected_and_held_positions_are_kept(tmp_path, logger):
    close, volume = synthetic_market()
    (tmp_path / "ref").mkdir()
    ref, fb0 = _bot(tmp_path / "ref", close, logger)
    run_days(ref, fb0, close, volume, SIM_FROM, SIM_FROM + 150)
    bought = {b["asset"] for b in ref.state["buys"]}
    assert "eth" in bought
    bot, fb = _bot(tmp_path, close, logger)
    tg.write_selection(bot.g, "manual", [a for a in close.columns if a != "eth"])
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 150)
    assert "eth" not in {b["asset"] for b in bot.state["buys"]}
    assert bot.state["selection"]["mode"] == "manual" and "eth" not in bot.state["selection"]["active"]
    assert bot.state["reasoning"]["assets"]["eth"]["status"] in ("unselected", "held", "sold")
    # Décocher une crypto détenue ne la vend pas : elle reste gérée par son stop.
    held = next(iter(bot.state["paper"]["holdings"]))
    tg.write_selection(bot.g, "manual", [a for a in close.columns if a not in ("eth", held)])
    run_days(bot, fb, close, volume, SIM_FROM + 150, SIM_FROM + 151)
    assert held in bot.state["paper"]["holdings"] or any(
        t["asset"] == held and t["reason"].startswith("STOP") for t in bot.state["trades"])


def test_auto_selection_buys_only_the_most_profitable(tmp_path, logger):
    close, volume = synthetic_market()
    bot, fb = _bot(tmp_path, close, logger, rank_cryptos=True)
    tg.write_selection(bot.g, "auto", [])
    bot.AUTO_SELECT_N = 2
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 150)
    sel = bot.state["selection"]
    assert sel["mode"] == "auto" and len(sel["auto"]) == 2 and sel["active"] == sel["auto"]
    assert len(sel["ranking"]) == len(close.columns)
    ranked = [r["asset"] for r in sel["ranking"] if r["eligible"]]
    assert set(sel["auto"]) <= set(ranked[:2 + bot.AUTO_SELECT_HYSTERESIS])
    assert bot.active_now() == set(sel["auto"])


def test_selection_file_roundtrip_and_validation(tmp_path):
    g = tg.GuardConfig(lock_file=str(tmp_path / "tg.lock"))
    first = tg.read_selection(g)
    assert first["mode"] == "manual" and len(first["manual"]) == 21 and not first["saved"]
    tg.write_selection(g, "auto", [])
    assert tg.read_selection(g)["mode"] == "auto"
    saved = tg.write_selection(g, "manual", ["BTC", "eth"])
    assert saved["manual"] == ["btc", "eth"]
    data = json.load(open(autonomy.sidecar(g.lock_file, ".selection.json"), encoding="utf-8"))
    assert data["mode"] == "manual" and data["manual"] == ["btc", "eth"]
    with pytest.raises(ValueError):
        tg.write_selection(g, "manual", ["inconnue"])
    with pytest.raises(ValueError):
        tg.write_selection(g, "partout", [])


def test_research_study_runs_on_a_small_market():
    from research import selection as rs
    close, volume = synthetic_market()
    pre = ts.precompute(close, volume, P)
    rec = ts.asset_track_records(pre[0], pre[1], P)
    start, end = str(close.index[260].date()), str(close.index[-1].date())
    ref = rs.run(close, pre, rec, P, start, end)
    top = rs.run(close, pre, rec, P, start, end, top=2)
    tp = rs.run(close, pre, rec, P, start, end, tp_r=3.0, tp_frac=0.5)
    for m in (ref, top, tp):
        assert np.isfinite(m["cagr_pct"]) and m["n_trades"] > 0
    assert isinstance(close.index, pd.DatetimeIndex)


def test_one_backtest_loop_for_the_bot_and_every_study():
    """Les études passent leurs variantes à ts.backtest (crochets) au lieu
    de recopier la boucle : sans crochet, résultat identique au centime."""
    close, volume = synthetic_market()
    pre = ts.precompute(close, volume, P)
    start, end = str(close.index[260].date()), str(close.index[-1].date())
    ref = ts.backtest(close, volume, P, start, end, pre=pre)
    same = ts.backtest(close, None, P, start, end, pre=pre, hooks=ts.BacktestHooks())
    assert same.equity.equals(ref.equity) and same.trades == ref.trades
    # Prise de bénéfice : la moitié vendue à +1 R, le reste suit le stop.
    tp = ts.backtest(close, None, P, start, end, pre=pre,
                     hooks=ts.BacktestHooks(take_profit=(1.0, 0.5)))
    assert tp.trades and len(tp.trades) == len(ref.trades)
    assert not tp.equity.equals(ref.equity)
    # Aucun achat permis : capital intact, aucun trade.
    none = ts.backtest(close, None, P, start, end, pre=pre,
                       hooks=ts.BacktestHooks(choose=lambda i, snap: {}))
    assert none.trades == [] and none.equity.iloc[-1] == 10_000.0
    # Plafond de risque à 0 : aucun achat non plus.
    capped = ts.backtest(close, None, P, start, end, pre=pre,
                         hooks=ts.BacktestHooks(risk_cap=lambda trades: 0.0))
    assert capped.trades == []


def test_one_position_size_for_bot_backtest_and_lab():
    """Une seule formule de taille : risque visé jusqu'au stop, plafond par
    position, cash disponible, minimum de 10 USDT."""
    entry, stop, unit = ts.entry_levels(100.0, 5.0, P)
    assert entry == 100.0 * (1 + P.slippage) and stop == ts.initial_stop(100.0, 5.0, P)
    qty, cost = ts.size_position(entry, unit, 100.0, 10_000.0, 10_000.0, P)
    assert qty * unit == pytest.approx(100.0)                  # 1 % de 10 000 USDT
    qty2, cost2 = ts.size_position(entry, unit, 100.0, 10_000.0, 50.0, P)
    assert cost2 == pytest.approx(50.0) and qty2 < qty          # limité par le cash
    assert ts.size_position(entry, unit, 100.0, 10_000.0, 5.0, P) is None   # < 10 USDT
