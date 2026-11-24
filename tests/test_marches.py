"""Pages des autres marchés dans le panneau, une par classe d'actifs
(matières premières, devises, indices, actions, obligations) : état de
chaque marché pour la règle, courbes, détail et positions fictives. Lecture
seule : jamais un ordre."""

import numpy as np
import pandas as pd
import pytest
from test_panel import _cfg, _json, demo_server  # noqa: F401  (fixture du serveur de démonstration)

from panel.data import BotData
from trendguard import multi_actifs as ma
from trendguard import savoir


def _series(n=900, drift=0.001, seed=1, start="2023-01-02"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n, tz="UTC")
    return pd.Series(100 * np.exp(np.cumsum(drift + rng.normal(0, 0.01, n))), index=idx)


def _cache(folder, codes):
    folder.mkdir(parents=True, exist_ok=True)
    for k, code in enumerate(codes):
        _series(seed=10 + k).rename("close").to_frame().to_csv(folder / f"{code}.csv", index_label="date")
    return str(folder)


def test_each_market_has_one_page_in_the_menu_order():
    assert [k for k, _c in ma.CLASSES] == ["commodities", "forex", "indices", "stocks", "bonds"]
    assert {c for _k, c in ma.CLASSES} == set(ma.CATEGORIES)
    every = [code for _k, c in ma.CLASSES for code in ma.codes_of(c)]
    assert sorted(every) == sorted(ma.ALL_CODES) and len(every) == len(set(every))
    assert ma.codes_of("Matières premières")[0] == "or"
    assert "mu" in ma.codes_of("Actions") and "qqq" in ma.codes_of("Indices")


def test_market_detail_agrees_with_the_daily_rule_summary():
    close = pd.DataFrame({"or": _series(seed=2), "wti": _series(drift=-0.001, seed=3)})
    daily = {r["code"]: r for r in ma.summary(close)}
    for code in close.columns:
        d = ma.market_detail(code, close[code])
        assert (d["trend"], d["signal"]) == (daily[code]["trend"], daily[code]["signal"])
        assert d["close"] == pytest.approx(float(close[code].iloc[-1]))
        assert d["date"] == str(close.index[-1].date())
    s, d = close["or"], ma.market_detail("or", close["or"])
    assert d["changes"]["1m"] == pytest.approx((s.iloc[-1] / s.iloc[-22] - 1) * 100)
    assert d["sma"] == pytest.approx(s.iloc[-150:].mean())
    assert d["breakout"] == pytest.approx(s.iloc[-31:-1].max())         # plus haut des 30 séances précédentes
    assert d["stop"] < d["close"] and d["vol_pct"] > 0
    assert d["binance"] == "Or (PAXG, XAUT)" and d["symbol"] == "GC=F"
    assert ma.market_detail("or", pd.Series(dtype=float))["close"] is None


def test_chart_points_and_card_curves():
    s = _series(n=600)
    pts = ma.market_points(s)
    assert len(pts) == 600 - 149                                         # la moyenne existe dès la 150e séance
    assert all(a["t"] < b["t"] for a, b in zip(pts, pts[1:]))
    assert pts[-1]["close"] == pytest.approx(s.iloc[-1], rel=1e-6) and pts[0]["high"] is not None
    assert 0 < len(ma.market_points(s, days=30)) <= 23
    for win in ma.SPARK_SESSIONS:
        curve = ma.spark(s, win)
        assert 2 <= len(curve) <= ma.SPARK_POINTS and curve[-1] == pytest.approx(s.iloc[-1], rel=1e-6)
    assert len(ma.spark(s, "1m")) == 22


def test_positions_of_both_fictitious_books():
    book = {"portfolio": {"positions": [{"code": "or", "entry": 2000.0, "stop": 1900.0, "pnl_pct": 5.0}]},
            "binance": {"positions": [{"code": "or", "entry": 2010.0, "stop": 1910.0, "pnl_pct": 4.5},
                                      {"code": "nvda", "entry": 100.0, "stop": 90.0, "pnl_pct": 20.0}]}}
    held = ma.held_positions(book)
    assert [p["book"] for p in held["or"]] == ["Portefeuille fictif hors cryptos", "Manche Binance fictive"]
    assert held["nvda"][0]["entry"] == 100.0 and ma.held_positions(None) == {}


def test_the_panel_reads_each_page_from_the_daily_cache(tmp_path):
    db = str(tmp_path / "savoir.db")
    memory = savoir.Memory(db)
    try:
        memory.put(ma.KEY, {"fetched_at": "2026-11-24T00:05:00+00:00", "error": "",
                            "portfolio": {"positions": [{"code": "or", "entry": 90.0, "stop": 80.0, "pnl_pct": 5.0}]}})
    finally:
        memory.close()
    data = BotData(_cfg(tmp_path, savoir=True, savoir_db=db), market=None)
    data.multi_cache = _cache(tmp_path / "multi", ma.codes_of("Matières premières"))
    page = data.markets("commodities", "3m")
    assert [r["code"] for r in page["rows"]] == ma.codes_of("Matières premières") and page["missing"] == []
    assert all(len(r["spark"]) == 64 for r in page["rows"]) and page["fetched_at"].startswith("2026-11-24")
    assert page["rows"][0]["positions"][0]["book"] == "Portefeuille fictif hors cryptos"
    fx = data.markets("forex")
    assert fx["rows"] == [] and len(fx["missing"]) == len(ma.codes_of("Devises"))
    one = data.market_info("or", 365)
    assert one["detail"]["code"] == "or" and one["points"] and one["sma"] == ma.REGIME_N
    assert one["detail"]["positions"][0]["entry"] == 90.0
    for bad in (lambda: data.markets("derives"), lambda: data.market_info("eurusd"), lambda: data.market_info("inconnu")):
        with pytest.raises(ValueError):
            bad()


def test_demo_server_serves_every_page(demo_server):  # noqa: F811
    base, _app = demo_server
    for key, category in ma.CLASSES:
        code, page = _json(f"{base}/api/markets?class={key}&window=5y")
        assert code == 200 and len(page["rows"]) == len(ma.codes_of(category)), key
        assert all(2 <= len(r["spark"]) <= ma.SPARK_POINTS for r in page["rows"])
    code, one = _json(base + "/api/market?code=or")
    assert code == 200 and one["detail"]["positions"] and len(one["points"]) > 250
    assert _json(base + "/api/markets?class=derives")[0] == 400
    assert _json(base + "/api/market?code=inconnu")[0] == 400
