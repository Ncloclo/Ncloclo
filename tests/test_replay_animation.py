"""Animation du rejeu : même décisions que le backtest, page autonome,
portefeuille paper lu sans modifier la base du bot."""

import json
import logging
import os

import pytest

import replay_animation as ra
import trend_strategy as ts
import trendguard_bot as tg
import v29
from test_trendguard import N_DAYS, SIM_FROM, synthetic_market


@pytest.fixture(scope="module")
def replay():
    close, volume = synthetic_market()
    start = str(close.index[SIM_FROM].date())
    return close, volume, start, ra.build_replay(close, volume, start)


def test_replay_matches_backtest_and_is_consistent(replay):
    close, volume, start, data = replay
    bt = ts.backtest(close, volume, ts.TrendParams(), start,
                     str(close.index[N_DAYS - 1].date()))
    closed = [e for e in data["episodes"] if e["d1"] is not None]
    assert [round(e["pnl"], 2) for e in closed] == [round(t["pnl"], 2) for t in bt.trades]
    assert data["equity"][-1] == pytest.approx(bt.equity.iloc[-1], rel=1e-6)
    n = len(data["dates"])
    assert n == N_DAYS - SIM_FROM and len(data["equity"]) == len(data["events"]) == n
    for e in data["episodes"]:
        held = (e["d1"] if e["d1"] is not None else n) - e["d0"]
        assert len(e["stops"]) == len(e["dis"]) == held          # un stop par jour détenu
        assert all(b >= a for a, b in zip(e["stops"], e["stops"][1:]))   # ne descend jamais
        assert all(d < s for d, s in zip(e["dis"], e["stops"]))   # catastrophe sous clôture
    buys = sum(len(ev["e"]) for ev in data["events"])
    sells = sum(len(ev["x"]) for ev in data["events"])
    assert buys == len(data["episodes"]) and sells == len(closed) == data["metrics"]["trades"]


def _page(data, **extra):
    if not os.path.exists(ra.TEMPLATE):
        pytest.skip("gabarit absent (image Docker)")
    return ra.render_html({**data, "generated": "2026-09-27 12:00 UTC",
                           "paper_live": None, **extra})


def _payload(page):
    block = page.split('<script type="application/json" id="replay-data">', 1)[1]
    return json.loads(block.split("</script>", 1)[0])


def test_render_html_is_a_standalone_page(replay):
    *_, data = replay
    page = _page(data)
    assert page.startswith("<!doctype html>") and "<title>Rejeu TrendGuard</title>" in page
    # Style et script intégrés : rien à charger à côté de la page.
    assert ra.PLACEHOLDER not in page and ra.CSS_TAG not in page and ra.JS_TAG not in page
    assert "<style>" in page and '"use strict"' in page
    assert "Content-Security-Policy" in page and "connect-src" not in page   # default-src 'none'
    assert _payload(page)["dates"] == data["dates"]


def test_render_html_data_cannot_inject_markup(replay):
    """Un texte des données (nom, date…) ne peut ni fermer le bloc JSON ni
    ajouter une balise à la page."""
    *_, data = replay
    evil = "</script><script>alert(1)</script><!--"
    page = _page(data, generated=evil)
    assert evil not in page and page.count("<script") == 2
    assert _payload(page)["generated"] == evil


def test_paper_portfolio_read_only(tmp_path):
    close, _ = synthetic_market()
    db = str(tmp_path / "tg.db")
    store = v29.Store(db, logging.getLogger("t"))
    store.set_kv(tg.TrendGuardBot.STATE_KEY, {
        "last_decision_day": "2026-09-25", "paper": {"cash": 5_000.0, "holdings": {"eth": {
            "qty": 1.5, "entry": 2000.0, "stop": 1800.0, "high": 2100.0, "disaster": 1700.0,
            "entry_date": "2026-09-26T00:02:00+00:00", "risk_quote": 100.0, "cost": 3000.0}}}})
    store.close()
    before = os.path.getmtime(db)
    live = ra.read_paper_portfolio(db, close)
    assert live["last_decision_day"] == "2026-09-25"
    assert live["holdings"][0]["a"] == "eth" and live["holdings"][0]["date"] == "2026-09-26"
    assert live["holdings"][0]["last"] == pytest.approx(close["eth"].iloc[-1], rel=1e-5)
    assert os.path.getmtime(db) == before
    assert ra.read_paper_portfolio(str(tmp_path / "absente.db"), close) is None
