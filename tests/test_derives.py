"""Contrats à terme de Binance (trendguard/derives.py) : lecture des réponses,
ligne de la veille, historique page par page. Sans réseau."""

import pytest

from trendguard import derives
from trendguard import market_watch as mw


def fake_fetch(routes):
    """Répond selon le début de l'adresse ; une exception est levée telle quelle."""
    seen = []

    def fetch(url):
        seen.append(url)
        for prefix, answer in routes.items():
            if url.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer(url) if callable(answer) else answer
        raise RuntimeError(f"adresse inattendue : {url}")
    fetch.seen = seen
    return fetch


PREMIUM = f"{derives.FAPI}/fapi/v1/premiumIndex"
OI = f"{derives.FAPI}/futures/data/openInterestHist"
LS = f"{derives.FAPI}/futures/data/globalLongShortAccountRatio"


def test_annual_rate():
    # 0,01 % par 8 heures (taux de base) : 3 paiements par jour, 365 jours.
    assert derives.annual(0.0001) == pytest.approx(0.1095)


def test_funding_rates_keeps_wanted_coins_only():
    fetch = fake_fetch({PREMIUM: [
        {"symbol": "BTCUSDT", "lastFundingRate": "0.00010000"},
        {"symbol": "ETHUSDT", "lastFundingRate": "-0.00005"},
        {"symbol": "SOLUSDT", "lastFundingRate": "0.0003"},       # hors univers
        {"symbol": "XRPUSDT", "lastFundingRate": ""},             # pas de taux
    ]})
    assert derives.funding_rates(fetch, ["BTC", "eth", "XRP"]) == {
        "btc": pytest.approx(0.0001), "eth": pytest.approx(-0.00005)}


def test_open_interest_change_and_long_share():
    fetch = fake_fetch({
        OI: [{"sumOpenInterestValue": "100"}, {"sumOpenInterestValue": "120"},
             {"sumOpenInterestValue": "130"}],
        LS: [{"longAccount": "0.62"}]})
    assert derives.open_interest_change(fetch) == pytest.approx(0.30)
    assert derives.long_share(fetch) == pytest.approx(0.62)
    empty = fake_fetch({OI: [{"sumOpenInterestValue": "100"}], LS: []})
    assert derives.open_interest_change(empty) is None
    assert derives.long_share(empty) is None


def test_indicators_survive_one_failed_source():
    fetch = fake_fetch({PREMIUM: [{"symbol": "BTCUSDT", "lastFundingRate": "0.0002"},
                                  {"symbol": "ETHUSDT", "lastFundingRate": "0.0004"}],
                        OI: ConnectionError("coupé"),
                        LS: [{"longAccount": "0.55"}]})
    out, errors = derives.indicators(fetch, ["BTC", "ETH"])
    assert out["funding_btc"] == pytest.approx(0.0002)
    assert out["funding_mean"] == pytest.approx(0.0003)
    assert out["long_share_btc"] == pytest.approx(0.55)
    assert "oi_btc_30d" not in out
    assert len(errors) == 1 and "oi_btc_30d" in errors[0]


def test_describe_line():
    assert derives.describe(None) is None
    assert derives.describe({"at": "2026-11-23T00:00+00:00"}) is None
    line = derives.describe({"funding_btc": 0.0001, "funding_mean": 0.0002,
                             "oi_btc_30d": 0.125, "long_share_btc": 0.6})
    assert line.startswith("Contrats à terme : financement BTC +0,0100 % par 8 h")
    assert "(+11,0 % par an)" in line
    assert "intérêt ouvert BTC +12,5 % sur 30 jours" in line
    assert "60 % des comptes acheteurs sur BTC" in line


def test_funding_history_pages_until_the_end():
    start, step = 1_600_000_000_000, 8 * 3600 * 1000

    def page(url):
        since = int(url.split("startTime=")[1].split("&")[0])
        k = (since - start + step - 1) // step
        n = 1000 if k == 0 else 3                  # une page pleine puis la fin
        return [{"fundingTime": start + (k + j) * step, "fundingRate": "0.0001"}
                for j in range(n)]

    fetch = fake_fetch({f"{derives.FAPI}/fapi/v1/fundingRate": page})
    rows = derives.funding_history(fetch, "BTCUSDT", start, start + 2000 * step)
    assert len(rows) == 1003
    assert [t for t, _ in rows] == sorted({t for t, _ in rows})       # ni trou ni doublon
    assert len(fetch.seen) == 2
    assert f"startTime={start + 999 * step + 1}" in fetch.seen[1]


def test_market_indicators_adds_futures_when_universe_given():
    routes = {PREMIUM: [{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}],
              OI: [{"sumOpenInterestValue": "100"}, {"sumOpenInterestValue": "90"}],
              LS: [{"longAccount": "0.5"}],
              "https://api.alternative.me/fng/": {"data": [
                  {"value": "72", "value_classification": "Greed"}]},
              "https://data-api.binance.vision/api/v3/ticker/price": {"price": "0.9998"}}
    out, errors = mw.market_indicators(fake_fetch(routes), ["BTC"])
    assert errors == []
    assert out["fear_greed"] == 72
    assert out["derives"]["funding_btc"] == pytest.approx(0.0001)
    assert out["derives"]["oi_btc_30d"] == pytest.approx(-0.10)
    # Sans univers : rien n'est demandé aux contrats à terme.
    fetch = fake_fetch(routes)
    out, _ = mw.market_indicators(fetch)
    assert "derives" not in out
    assert not any(u.startswith(derives.FAPI) for u in fetch.seen)
