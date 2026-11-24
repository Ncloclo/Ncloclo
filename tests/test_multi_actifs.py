"""Au-delà des cryptos (trendguard/multi_actifs.py) : historique Yahoo,
règle de TrendGuard transposée (régime propre à chaque actif), coûts réels,
suivi quotidien et portefeuille en argent fictif."""

import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from trendguard import multi_actifs as ma
from trendguard import trend_strategy as ts

NOW = datetime(2026, 11, 24, 23, 0, tzinfo=timezone.utc)


def _chart(closes, start=datetime(2016, 1, 4, 14, 30, tzinfo=timezone.utc), offset=-18000):
    """Réponse Yahoo : une séance par jour ouvré, clôtures (None = trou)."""
    days, d = [], start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(int(d.timestamp()))
        d += timedelta(days=1)
    return json.dumps({"chart": {"result": [{"meta": {"gmtoffset": offset, "currency": "USD"},
                                             "timestamp": days,
                                             "indicators": {"quote": [{"close": closes}]}}]}}).encode()


def _trend(n, drift, seed):
    rng = np.random.default_rng(seed)
    return [float(x) for x in 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.01, n)))]


def test_parse_chart_uses_the_local_trading_day_and_skips_holes():
    s = ma.parse_chart(_chart([10.0, None, 12.0]))
    assert list(s.values) == [10.0, 12.0] and str(s.index[0].date()) == "2016-01-04"
    assert ma.parse_chart(b'{"chart": {"result": null}}').empty


def test_download_and_load_keep_one_file_per_market(tmp_path):
    def fetch(url):
        return _chart([1.0, 2.0, 3.0] if "GC%3DF" in url else [5.0, 6.0])
    errors = ma.download(str(tmp_path), fetch, codes=["or", "eurusd"], pause=0)
    assert errors == {}
    close = ma.load(str(tmp_path), ["or", "eurusd"])
    assert list(close.columns) == ["or", "eurusd"] and close["or"].iloc[-1] == 3.0
    assert close["eurusd"].iloc[-1] == 6.0                       # trou comblé par le dernier cours

    def broken(url):
        raise TimeoutError("timed out")
    assert set(ma.download(str(tmp_path), broken, codes=["or"], pause=0)) == {"or"}


def test_the_rule_uses_each_market_own_trend_and_cheaper_real_costs():
    idx = pd.bdate_range("2016-01-04", periods=1400, tz="UTC")
    close = pd.DataFrame({"sp500": _trend(1400, 0.0008, 1), "eurusd": _trend(1400, -0.0006, 2)}, index=idx)
    trend = ma.own_trend(close)
    assert trend["sp500"][-1] and not trend["eurusd"][-1]
    assert not trend["sp500"][:ma.REGIME_N - 1].any()             # pas de régime sans 150 séances
    binance = ma.backtest(close, "2018-01-01", str(idx[-1].date()))
    real = ma.backtest(close, "2018-01-01", str(idx[-1].date()), real_costs=True)
    assert len(binance.trades) > 0 and real.equity.iloc[-1] > binance.equity.iloc[-1]
    assert any(t["asset"] == "sp500" for t in real.trades)       # la hausse est suivie


def test_backtest_results_expose_open_positions():
    idx = pd.bdate_range("2016-01-04", periods=900, tz="UTC")
    close = pd.DataFrame({"or": [100 * math.exp(0.002 * i) for i in range(900)]}, index=idx)
    r = ma.backtest(close, "2018-01-01", str(idx[-1].date()))
    assert set(r.holdings) == {"or"} and isinstance(r.holdings["or"], ts.Holding)


class _Memory:
    def __init__(self):
        self.meta = {}

    def get(self, key, default=None):
        return self.meta.get(key, default)

    def put(self, key, value):
        self.meta[key] = value


def test_daily_follow_up_and_fictitious_portfolio(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return _chart(_trend(900, 0.0005, len(calls)))
    memory = _Memory()
    book = ma.refresh(memory, NOW, fetch, str(tmp_path), 100.0, pause=0)
    markets = set(ma.ALL_CODES)                     # univers, manche Binance et marchés ajoutés le 24/11/2026
    reads = len(markets) + len(ma.INDICATOR_CODES)  # et les indicateurs de marché (VIX, taux)
    assert book["error"] == "" and len(book["rows"]) == len(markets) and len(calls) == reads
    assert set(book["macro"]) == {"vix", "dxy", "rates", "curve"}
    assert book["start"] == "2026-11-24" and book["portfolio"]["capital"] == 100.0
    assert book["binance"]["capital"] == 100.0 and book["binance"]["start"] == "2026-11-24"
    assert {r["category"] for r in book["rows"]} == set(ma.CATEGORIES)
    assert {r["code"] for r in book["rows"] if r["binance"]} == set(ma.BINANCE_NAME)
    ma.refresh(memory, NOW + timedelta(hours=3), fetch, str(tmp_path), 100.0, pause=0)
    assert len(calls) == reads                                                # une lecture par jour
    later = ma.refresh(memory, NOW + timedelta(days=2), fetch, str(tmp_path), 100.0, pause=0)
    assert later["start"] == "2026-11-24"                                     # le premier jour reste

    def down(url):
        raise TimeoutError("timed out")
    failed = ma.refresh(memory, NOW + timedelta(days=4), down, str(tmp_path / "vide"), 100.0, pause=0)
    assert failed["error"] and failed["rows"] == later["rows"]                # rien de perdu


def test_the_portfolio_replays_from_its_first_day_with_real_costs():
    idx = pd.bdate_range("2016-01-04", periods=1300, tz="UTC")
    close = pd.DataFrame({"or": _trend(1300, 0.001, 3), "nvda": _trend(1300, 0.002, 4)}, index=idx)
    pf = ma.portfolio(close, "2019-01-01", 100.0)
    assert pf["capital"] == 100.0 and pf["curve"] and pf["curve"][0][0] >= "2019-01-01"
    assert all(p["category"] != "Actions" for p in pf["positions"])           # actions hors du portefeuille
    assert ma.portfolio(close, "2030-01-01", 100.0)["equity"] == 100.0        # pas encore commencé


def test_rule_uses_real_sessions_and_a_closed_week_is_not_a_delisting():
    """Un marché fermé sept séances (Nouvel An chinois, semaine d'or) : ses
    jours de fermeture ne diluent pas sa volatilité, n'ouvrent aucune
    cassure et ne passent pas pour un retrait de la cote."""
    idx = pd.bdate_range("2016-01-04", periods=1200, tz="UTC")
    live = pd.Series(_trend(1200, 0.0008, 7), index=idx)
    shut = live.copy()
    shut.iloc[600:607] = np.nan
    frame = pd.DataFrame({"sp500": live, "shanghai": shut}).ffill(limit=ma.FILL_LIMIT)
    assert len(ma.sessions(frame["shanghai"])) == 1200 - 7
    f = ma.features(frame, ts.TrendParams())
    own = ts.asset_features(ma.sessions(frame["shanghai"]), ts.TrendParams())
    assert f["shanghai"]["vol"][650] == pytest.approx(own["vol"].loc[idx[650]])     # seules ses séances comptent
    assert np.isinf(f["shanghai"]["prior_high"][603])                                 # place fermée : pas de cassure
    assert np.isfinite(f["shanghai"]["close"][606])                                   # dernier cours gardé
    longer = live.copy()
    longer.iloc[600:612] = np.nan                                                     # douze séances : au-delà
    frame = pd.DataFrame({"x": longer}).ffill(limit=ma.FILL_LIMIT)
    assert np.isnan(ma.features(frame, ts.TrendParams())["x"]["close"][611])


def test_daily_reading_waits_for_the_new_york_close():
    """Un cours lu en pleine séance n'est pas une clôture : la lecture du jour
    se refait après 22:30 UTC, puis une fois par jour ; six heures après une
    panne."""
    utc = timezone.utc
    early = {"tried_at": "2026-11-24T17:11:00+00:00", "error": ""}
    assert not ma.due(early, datetime(2026, 11, 24, 22, 0, tzinfo=utc))
    assert ma.due(early, datetime(2026, 11, 24, 22, 31, tzinfo=utc))
    done = {"tried_at": "2026-11-24T22:40:00+00:00", "error": ""}
    assert not ma.due(done, datetime(2026, 11, 25, 21, 0, tzinfo=utc))
    assert ma.due(done, datetime(2026, 11, 25, 22, 35, tzinfo=utc))
    failed = {"tried_at": "2026-11-24T22:40:00+00:00", "error": "panne"}
    assert not ma.due(failed, datetime(2026, 11, 25, 4, 0, tzinfo=utc))
    assert ma.due(failed, datetime(2026, 11, 25, 4, 41, tzinfo=utc))


def test_macro_reading_of_the_day():
    idx = pd.bdate_range("2025-01-01", periods=300, tz="UTC")
    ind = pd.DataFrame({"vix": np.linspace(14, 31, 300), "dxy": np.linspace(110, 100, 300),
                        "us10y": np.linspace(4.0, 4.9, 300), "us3m": np.full(300, 5.0)}, index=idx)
    v = ma.macro_view(ind)
    assert v["vix"]["level"] == "stress" and v["dxy"]["strong"] is False
    assert v["rates"]["us10y"]["change_60"] == pytest.approx(0.9 * 60 / 299)
    assert v["curve"] == pytest.approx(-0.1)
    line = ma.describe_macro(v)
    assert line.startswith("VIX 31,0 (stress)") and "dollar sous sa moyenne" in line and "inversée" in line
    assert [ma.vix_level(x) for x in (14.9, 15, 25, 30)] == ["calme", "normal", "tendu", "stress"]
    assert ma.describe_macro({}) == "" and ma.macro_view(pd.DataFrame()) == {}
