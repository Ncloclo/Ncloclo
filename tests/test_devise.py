"""Capital dans votre monnaie (trendguard/devise.py) : FCFA, frais de change,
impôt sur le gain d'un retrait complet."""

import logging
import time

import pytest

from test_trendguard import make_bot, synthetic_market
from trendguard import devise


def test_the_cfa_franc_follows_the_euro_at_a_fixed_rate():
    assert devise.per_usdt("USDT", None) == 1.0
    assert devise.per_usdt("EUR", 1.10) == pytest.approx(1 / 1.10)
    assert devise.per_usdt("FCFA", 1.10) == pytest.approx(655.957 / 1.10)
    assert devise.per_usdt("FCFA", None) is None


def test_any_currency_followed_by_the_bot_can_show_the_capital():
    assert {"NGN", "GBP", "JPY", "XAF", "KES", "INR", "BRL", "ZAR", "USD"} <= set(devise.codes())
    assert devise.per_usdt("NGN", None, 1500.0) == 1500.0                    # nairas pour 1 dollar
    assert devise.per_usdt("GBP", None, 1.25) == pytest.approx(0.8)          # dollars pour 1 livre
    assert devise.per_usdt("XAF", 1.10) == devise.per_usdt("FCFA", 1.10)     # même parité que le XOF
    assert devise.per_usdt("USD", None) == 1.0 and devise.per_usdt("NGN", None, None) is None
    assert devise.per_usdt("ABC", 1.1, 2.0) is None
    v = devise.view("NGN", 10_000.0, 10_000.0, 1500.0, 1500.0, 0.0, 0.0)
    assert v["label"] == "NGN" and v["decimals"] == 0 and "15 000 000 NGN" in devise.line(v)
    assert devise.view("GBP", 1.0, 1.0, 0.8, 0.8, 0.0, 0.0)["decimals"] == 2
    assert devise.label("XOF") == "FCFA" and devise.needs_fx("NGN") and not devise.needs_fx("EUR")


def test_the_rate_of_a_currency_comes_from_the_daily_market_reading(tmp_path):
    (tmp_path / "fx_ngn.csv").write_text("date,close\n2026-11-23,1490.0\n2026-11-24,1500.0\n", encoding="utf-8")
    (tmp_path / "gbpusd.csv").write_text("date,close\n2026-11-24,1.25\n", encoding="utf-8")
    assert devise.yahoo_rate("NGN", str(tmp_path)) == 1500.0 and devise.yahoo_rate("GBP", str(tmp_path)) == 1.25
    assert devise.yahoo_rate("KES", str(tmp_path)) is None and devise.yahoo_rate("EUR", str(tmp_path)) is None


def test_a_withdrawal_pays_the_exchange_fees_and_the_tax_on_the_gain_only():
    rate = 655.957 / 1.10
    v = devise.view("FCFA", 12_000.0, 10_000.0, rate, rate, 0.02, 0.10)
    invested = 10_000 * rate * 1.02
    gross = 12_000 * rate * 0.98
    assert v["invested"] == pytest.approx(invested)
    assert v["withdraw_gross"] == pytest.approx(gross)
    assert v["tax"] == pytest.approx((gross - invested) * 0.10)
    assert v["withdraw_net"] == pytest.approx(gross - (gross - invested) * 0.10)


def test_no_tax_on_a_loss_and_the_euro_moves_the_result():
    v = devise.view("FCFA", 9_000.0, 10_000.0, 600.0, 600.0, 0.02, 0.30)
    assert v["tax"] == 0.0 and v["gain_net"] < 0
    weaker_dollar = devise.view("FCFA", 10_000.0, 10_000.0, 580.0, 600.0, 0.0, 0.0)
    assert weaker_dollar["gain_net_pct"] == pytest.approx((580 / 600 - 1) * 100)


def test_the_daily_line_is_written_in_cfa_francs():
    v = devise.view("FCFA", 10_000.0, 10_000.0, 600.0, 600.0, 0.02, 0.0)
    line = devise.line(v)
    assert "FCFA" in line and "nets" in line and "frais de change 2,0 %" in line
    assert devise.line(devise.view("USDT", 1.0, 1.0, 1.0, 1.0, 0.0, 0.0)) is None


def test_the_bot_keeps_its_capital_in_cfa_francs(monkeypatch):
    close, _volume = synthetic_market()
    logger = logging.getLogger("test.devise")
    bot, fb = make_bot("paper", close, logger, devise="FCFA")
    assert bot.boot()
    monkeypatch.setattr(devise, "eur_usdt", lambda exchange: 1.10)
    bot.state["start_equity"] = 10_000.0
    bot._last_equity_log = 0.0
    bot._log_equity(10_500.0, 10_500.0)
    d = bot.state["devise"]
    assert d["code"] == "FCFA" and d["eurusdt"] == 1.10
    assert d["equity"] == pytest.approx(10_500 * 655.957 / 1.10)
    first_rate = d["rate_start"]
    # Plus tard, l'euro a bougé : le départ garde son taux d'origine.
    monkeypatch.setattr(devise, "eur_usdt", lambda exchange: 1.20)
    bot.state["devise"]["rate_ts"] = time.time() - 2 * devise.RATE_REFRESH_SEC
    bot._last_equity_log = 0.0
    bot._log_equity(10_500.0, 10_500.0)
    d = bot.state["devise"]
    assert d["rate_start"] == pytest.approx(first_rate)
    assert d["rate"] == pytest.approx(655.957 / 1.20)


def test_the_bot_keeps_its_capital_in_nairas(monkeypatch):
    close, _volume = synthetic_market()
    bot, _fb = make_bot("paper", close, logging.getLogger("test.devise"), devise="NGN")
    assert bot.boot()
    monkeypatch.setattr(devise, "eur_usdt", lambda exchange: pytest.fail("Binance n'est pas lu pour le naira"))
    monkeypatch.setattr(devise, "yahoo_rate", lambda code, cache=devise.MULTI_CACHE: 1500.0)
    bot.state["start_equity"] = 10_000.0
    bot._last_equity_log = 0.0
    bot._log_equity(10_500.0, 10_500.0)
    d = bot.state["devise"]
    assert d["code"] == "NGN" and d["fx"] == 1500.0 and d["equity"] == pytest.approx(10_500 * 1500.0)


def test_in_usdt_the_bot_converts_nothing():
    close, _volume = synthetic_market()
    bot, _fb = make_bot("paper", close, logging.getLogger("test.devise"))
    assert bot.boot()
    bot._last_equity_log = 0.0
    bot._log_equity(10_000.0, 10_000.0)
    assert "devise" not in bot.state
