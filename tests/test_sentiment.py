"""Étude sentiment (research/sentiment.py) : indicateurs sans regard vers le
futur, filtre d'achat, rapport. Sans réseau."""

import math

import numpy as np
import pandas as pd
import pytest

from research import sentiment as se
from test_trendguard import synthetic_market

def test_daily_funding_uses_only_rates_known_at_the_close():
    days = pd.date_range("2024-01-01", periods=12, freq="D", tz="UTC")
    times = pd.date_range("2024-01-01", periods=12 * 3, freq="8h", tz="UTC")
    rates = pd.Series(0.0001, index=times)
    rates.iloc[-1] = 0.01            # payé le 12/01 à 16:00, après la clôture du 11/01
    out = se.daily_funding(rates, days)
    assert out.iloc[:6].isna().all()                         # moins de 7 jours connus
    day11, day12 = out.loc[days[10]], out.loc[days[11]]
    assert day11 == pytest.approx(0.0001 * 3 * 365)          # le taux du 12/01 est ignoré
    assert day12 > day11                                     # connu à la clôture du 12/01


def test_daily_greed_is_the_value_of_the_day():
    days = pd.date_range("2024-01-01", periods=4, freq="D", tz="UTC")
    fng = pd.Series([20.0, 80.0], index=pd.DatetimeIndex(
        ["2024-01-02", "2024-01-04"], tz="UTC"))
    out = se.daily_greed(fng, days)
    assert math.isnan(out.iloc[0])
    assert list(out.iloc[1:]) == [20.0, 20.0, 80.0]


def test_skip_filter_drops_only_known_values_above_the_limit():
    index = pd.date_range("2024-01-01", periods=2, freq="D", tz="UTC")
    feat = {"eth": pd.Series([0.5, np.nan], index=index),
            "*": pd.Series([90.0, 10.0], index=index)}
    plans = [{"asset": "eth"}, {"asset": "xrp"}]
    keep = se.skip_filter(feat, index, 0.3)
    assert keep(0, plans, {}, 0.0) == []                       # eth 0,5 et xrp 90 > 0,3
    assert keep(1, plans, {}, 0.0) == [{"asset": "eth"}]       # eth inconnu, xrp 10 > 0,3
    assert se.skip_filter(feat, index, 100)(0, plans, {}, 0.0) == plans


def test_buckets_ignore_unknown_values():
    out = se.buckets([0.05, 0.2, math.nan, -0.1], [1.0, -1.0, 5.0, 2.0], se.FUNDING_EDGES)
    assert [(lab[:3], n) for lab, n, _r, _w in out] == [
        ("nég", 1), ("0 à", 1), ("10 ", 1), ("plu", 0)]
    assert out[0][2] == pytest.approx(2.0) and out[0][3] == pytest.approx(100.0)


def test_load_funding_does_not_cache_a_failed_download(tmp_path, capsys):
    def boom(url):
        raise ConnectionError("coupé")
    assert se.load_funding(str(tmp_path), ["BTC"], fetch=boom, end_ms=1, sleep=lambda s: None) == {}
    assert not (tmp_path / "financement" / "btc.csv").exists()
    assert "indisponible" in capsys.readouterr().err


def test_report_on_a_synthetic_market():
    close, volume = synthetic_market()
    times = pd.date_range(close.index[0], close.index[-1], freq="8h", tz="UTC")
    rng = np.random.default_rng(1)
    funding = {a: pd.Series(rng.normal(0.0001, 0.0002, len(times)), index=times)
               for a in close.columns}
    fng = pd.Series(rng.integers(5, 95, len(close)).astype(float), index=close.index)
    text = se.report(close, volume, funding, fng, start=str(close.index[420].date()),
                     design_end=str(close.index[560].date()))
    assert "| BTC acheté et conservé |" in text
    assert "| stratégie sans filtre |" in text
    assert text.count("(choisi sur") == 2
    assert "## Verdict" in text
    assert all(len(line) <= 80 for line in text.splitlines() if not line.startswith("|"))
