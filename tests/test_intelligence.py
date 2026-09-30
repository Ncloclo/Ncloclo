"""Bot « intelligent et rusé » : raisonnement expliqué actif par actif, et
achats différés quand le carnet d'ordres est anormal (sans jamais risquer
plus que prévu)."""

import dataclasses
import logging
from datetime import timedelta

import ccxt
import pytest

import trendguard_bot as tg
from test_trendguard import DAY, SIM_FROM, feed, make_bot, run_days, synthetic_market
from trendguard import trend_strategy as ts

P = ts.TrendParams()


@pytest.fixture
def logger():
    lg = logging.getLogger("test.intelligence")
    lg.setLevel(logging.INFO)
    return lg


def _snap(close=100.0, prior_high=98.0, mom=1.2, age=400, vol30=2e7):
    return {"close": close, "vol": 2.0, "prior_high": prior_high, "mom": mom,
            "age": age, "vol30": vol30}


def test_explain_decision_gives_a_reason_for_every_asset():
    held = ts.Holding("eth", qty=1, entry=90.0, stop=85.0, high=100.0, entry_date=None,
                      risk_quote=100.0, cost=90.0)
    snap = {"btc": _snap(prior_high=103.0), "eth": _snap(), "xrp": _snap(mom=-0.5),
            "doge": _snap(vol30=1e6), "ada": _snap(prior_high=110.0), "bnb": _snap(),
            "dot": _snap(), "uni": _snap(age=30), "ltc": _snap()}
    notes = {"bnb": ("veto", "Achats bloqués par la veille : retrait annoncé")}
    r = tg.explain_decision("2026-09-26", True, 4.2, snap, {"eth": held}, [("ltc", "STOP")],
                            ["dot"], notes, False, 1.0, P)
    st = {a: x["status"] for a, x in r["assets"].items()}
    assert st == {"btc": "watch", "eth": "held", "xrp": "weak", "doge": "illiquid",
                  "ada": "wait", "bnb": "veto", "dot": "bought", "uni": "young", "ltc": "sold"}
    assert r["radar"] == ["btc"] and r["assets"]["btc"]["breakout_gap_pct"] == pytest.approx(3.0)
    assert "+3,0 %" in r["assets"]["btc"]["text"]       # format français
    assert r["lines"][0].startswith("Marché haussier") and "+4,2 %" in r["lines"][0]
    assert "1 achat(s) : DOT" in r["lines"][1] and "1 vente(s) : LTC" in r["lines"][1]
    # Signal d'achat sans achat : marché baissier, puis plafond atteint.
    bear = tg.explain_decision("d", False, -3.0, {"ada": _snap()}, {}, [], [], {}, False, 1.0, P)
    assert bear["assets"]["ada"]["status"] == "bear" and "baissier" in bear["lines"][0]
    full = tg.explain_decision("d", True, 3.0, {"ada": _snap()}, {}, [], [], {}, False, 0.5, P)
    assert full["assets"]["ada"]["status"] == "full"
    assert any("Profil prudent" in line for line in full["lines"])


def test_reasoning_says_why_nothing_moves():
    """Budget de risque plein, vente la plus proche, palier de risque : le
    raisonnement dit pourquoi le bot n'achète ni ne vend."""
    held = {a: ts.Holding(a, qty=1, entry=100.0, stop=stop, high=110.0, entry_date=None,
                          risk_quote=100.0, cost=100.0) for a, stop in (("eth", 90.0), ("xrp", 99.0))}
    snap = {"eth": _snap(close=105.0, prior_high=110.0), "xrp": _snap(close=100.0, prior_high=110.0),
            "ada": _snap()}
    p = dataclasses.replace(P, max_total_risk=0.02)              # 2 positions à 1 % : plein
    r = tg.explain_decision("d", True, 3.0, snap, held, [], [], {}, False, 1.0, p, 10_000.0)
    text = " ".join(r["lines"])
    assert "Budget de risque plein : 2,0 % engagés sur 2 % permis, 2 position(s)" in text
    assert "Vente la plus proche : XRP, stop à −1,0 % du cours" in text
    assert r["assets"]["ada"]["status"] == "full"
    r = tg.explain_decision("d", True, 3.0, snap, held, [], ["ada"], {}, False, 1.0, p, 10_000.0,
                            boost=1.5, extra=["Palier de risque : 1,5 % par achat."])
    assert "1,5 % du capital risqué" in r["assets"]["ada"]["text"]
    assert r["lines"][-1] == "Palier de risque : 1,5 % par achat."
    bear = tg.explain_decision("d", False, -3.0, snap, held, [], [], {}, False, 1.0, p, 10_000.0)
    assert not any("Budget" in line for line in bear["lines"])   # marché baissier : autre raison
    assert any("Vente la plus proche" in line for line in bear["lines"])


def test_daily_decision_records_its_reasoning(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 90)
    r = bot.state["reasoning"]
    assert r["day"] == bot.state["last_decision_day"]
    assert set(r["assets"]) == set(close.columns)
    for a in bot.state["paper"]["holdings"]:
        assert r["assets"][a]["status"] in ("held", "bought")
    assert r["lines"] and len(bot.state["reasoning_log"]) == 30   # 30 derniers jours


def _first_entry_day(close, volume, logger):
    """Premier jour où le bot de référence achète (même marché simulé)."""
    ref, fb = make_bot("paper", close, logger)
    assert ref.boot()
    feed(fb, close, volume)
    for i, d in enumerate(close.index[SIM_FROM:], SIM_FROM):
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        ref.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
        if ref.state["paper"]["holdings"]:
            return i, set(ref.state["paper"]["holdings"])
    raise AssertionError("aucune entrée")


def _bot_until(close, volume, logger, day_i, **kw):
    bot, fb = make_bot("paper", close, logger, **kw)
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, day_i)
    return bot, fb


def _cycle(bot, fb, close, day_i, minutes):
    d = close.index[day_i]
    for a in close.columns:
        fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=minutes))


def test_abnormal_order_book_defers_the_entry_then_buys(logger):
    close, volume = synthetic_market()
    day_i, bought = _first_entry_day(close, volume, logger)
    bot, fb = _bot_until(close, volume, logger, day_i)
    wide = {"on": True}

    def book(symbol, limit=20):
        f = fb._f(symbol)
        k = 0.01 if wide["on"] else 0.0001            # écart 2 % : carnet anormal
        return {"bids": [[f.last * (1 - k), 1e9]], "asks": [[f.last * (1 + k), 1e9]]}
    fb.fetch_order_book = book
    _cycle(bot, fb, close, day_i, 5)
    pend = bot.state["pending_entries"]
    assert set(pend) == bought and not bot.state["paper"]["holdings"]
    a = sorted(bought)[0]
    assert "écart achat/vente anormal" in pend[a]["reason"]
    assert bot.state["reasoning"]["assets"][a]["status"] == "deferred"
    _cycle(bot, fb, close, day_i, 8)                    # 3 min : trop tôt pour réessayer
    assert set(bot.state["pending_entries"]) == bought
    wide["on"] = False                                  # carnet redevenu normal
    _cycle(bot, fb, close, day_i, 11)
    assert set(bot.state["paper"]["holdings"]) == bought and not bot.state["pending_entries"]
    assert bot.state["reasoning"]["assets"][a]["status"] == "bought"
    assert {b["asset"]: b["note"] for b in bot.state["buys"]}[a] == "différé"
    # Même risque que l'achat immédiat du bot de référence.
    h = bot.state["paper"]["holdings"][a]
    assert h["risk_quote"] <= bot.state["last_equity"] * P.risk_pct * 1.0001


def test_thin_book_and_expiry_abandon_the_entry(logger):
    close, volume = synthetic_market()
    day_i, bought = _first_entry_day(close, volume, logger)
    bot, fb = _bot_until(close, volume, logger, day_i, entry_retry_hours=1.0)

    def thin(symbol, limit=20):
        f = fb._f(symbol)
        return {"bids": [[f.bid, 1.0]], "asks": [[f.ask, 1e-6]]}
    fb.fetch_order_book = thin
    _cycle(bot, fb, close, day_i, 5)
    a = sorted(bought)[0]
    assert "trop mince" in bot.state["pending_entries"][a]["reason"]
    _cycle(bot, fb, close, day_i, 5 + 61)               # délai d'une heure dépassé
    assert not bot.state["pending_entries"] and not bot.state["paper"]["holdings"]
    assert bot.state["reasoning"]["assets"][a]["status"] == "cancelled"


def test_retry_pending_reprices_against_todays_equity_not_the_stale_one(logger):
    """Un achat différé rejoué doit être jugé (plafond de risque, taille) sur
    le capital réel du moment, pas sur celui mémorisé au moment du report :
    entre-temps, une autre position a pu clôturer sur son stop."""
    close, volume = synthetic_market()
    day_i, bought = _first_entry_day(close, volume, logger)
    bot, fb = _bot_until(close, volume, logger, day_i)
    wide = {"on": True}

    def book(symbol, limit=20):
        f = fb._f(symbol)
        k = 0.01 if wide["on"] else 0.0001            # écart 2 % : carnet anormal
        return {"bids": [[f.last * (1 - k), 1e9]], "asks": [[f.last * (1 + k), 1e9]]}
    fb.fetch_order_book = book
    _cycle(bot, fb, close, day_i, 5)                  # achat différé (carnet anormal)
    a = sorted(bought)[0]
    pend = bot.state["pending_entries"]
    assert a in pend
    pend[a]["equity"] = 1e9                           # capital mémorisé désormais périmé
    pend[a]["next"] = 0.0                             # prêt à réessayer immédiatement
    seen = {}

    def fake_execute(plan, equity, now_, cash):
        seen["equity"] = equity
        return None
    bot._execute_entry = fake_execute
    now = close.index[day_i].to_pydatetime() + DAY + timedelta(minutes=8)
    bot._retry_pending(now)
    real_equity, _ = bot._equity_and_cash({})
    assert seen["equity"] == pytest.approx(real_equity)
    assert seen["equity"] != pytest.approx(1e9)


def test_retry_values_open_positions_at_their_current_price(logger):
    """Capital du nouvel essai = liquidités + positions au cours du moment
    (et non à leur prix d'achat)."""
    close, volume = synthetic_market()
    day_i, bought = _first_entry_day(close, volume, logger)
    bot, fb = _bot_until(close, volume, logger, day_i)
    fb.fetch_order_book = lambda symbol, limit=20: {"bids": [], "asks": []}
    _cycle(bot, fb, close, day_i, 5)
    a = sorted(bought)[0]
    held = next(x for x in close.columns if x != a)
    px = float(close[held].iloc[day_i])
    bot.state["paper"]["holdings"][held] = {
        "qty": 2.0, "entry": px / 2, "stop": px / 4, "high": px, "entry_date": "2024-01-01T00:00:00+00:00",
        "risk_quote": 1.0, "cost": px, "disaster": px / 5}
    bot.state["pending_entries"][a]["next"] = 0.0
    seen = {}
    bot._execute_entry = lambda plan, equity, now_, cash: seen.update(equity=equity)
    bot._retry_pending(close.index[day_i].to_pydatetime() + DAY + timedelta(minutes=8))
    cash = bot.state["paper"]["cash"]
    assert seen["equity"] == pytest.approx(cash + 2.0 * px)       # au cours, pas à px / 2


def test_deferred_entry_shows_the_latest_reason(logger):
    close, volume = synthetic_market()
    day_i, bought = _first_entry_day(close, volume, logger)
    bot, fb = _bot_until(close, volume, logger, day_i)
    fb.fetch_order_book = lambda symbol, limit=20: {"bids": [], "asks": []}
    _cycle(bot, fb, close, day_i, 5)
    a = sorted(bought)[0]
    assert "carnet d'ordres vide" in bot.state["reasoning"]["assets"][a]["text"]
    real = fb.fetch_ticker

    def down(symbol):
        if symbol == f"{a.upper()}/USDT":
            raise ccxt.NetworkError("coupure")
        return real(symbol)
    fb.fetch_ticker = down
    _cycle(bot, fb, close, day_i, 11)
    assert "prix indisponible" in bot.state["pending_entries"][a]["reason"]
    assert "prix indisponible" in bot.state["reasoning"]["assets"][a]["text"]


def test_price_outage_defers_instead_of_losing_the_entry(logger):
    close, volume = synthetic_market()
    day_i, bought = _first_entry_day(close, volume, logger)
    bot, fb = _bot_until(close, volume, logger, day_i)
    a = sorted(bought)[0]
    sym = f"{a.upper()}/USDT"
    real = fb.fetch_ticker
    down = {"on": True}

    def ticker(symbol):
        if symbol == sym and down["on"]:
            raise ccxt.NetworkError("coupure")
        return real(symbol)
    fb.fetch_ticker = ticker
    _cycle(bot, fb, close, day_i, 5)
    assert "prix indisponible" in bot.state["pending_entries"][a]["reason"]
    down["on"] = False
    _cycle(bot, fb, close, day_i, 11)
    assert a in bot.state["paper"]["holdings"]


def test_new_decision_replaces_yesterdays_deferred_entries(logger):
    close, volume = synthetic_market()
    day_i, bought = _first_entry_day(close, volume, logger)
    bot, fb = _bot_until(close, volume, logger, day_i)
    fb.fetch_order_book = lambda symbol, limit=20: {"bids": [], "asks": []}
    _cycle(bot, fb, close, day_i, 5)
    assert bot.state["pending_entries"]
    del fb.fetch_order_book                              # retour au carnet normal du simulateur
    _cycle(bot, fb, close, day_i + 1, 5)                 # clôture suivante : nouvelle décision
    assert not bot.state.get("pending_entries")


def test_every_purchase_is_logged_when_it_happens(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 150)
    buys = bot.state["buys"]
    assert buys and all(b["mode"] == "paper" and b["qty"] > 0 and b["cost"] > 0 for b in buys)
    for a, h in bot.state["paper"]["holdings"].items():
        assert any(b["asset"] == a and b["date"] == h["entry_date"]
                   and b["price"] == pytest.approx(h["entry"]) for b in buys)
    for tr in bot.state["trades"]:                           # achats déjà revendus aussi
        assert any(b["asset"] == tr["asset"] and b["date"] == tr["entry_date"] for b in buys)


def test_live_trades_keep_their_purchase_date_and_price(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("live", close, logger)
    assert bot.boot()
    run_days(bot, fb, close, volume, SIM_FROM, SIM_FROM + 200)
    trades = bot.state["trades"]
    assert trades
    for t in trades:
        assert t["entry_date"] and t["entry"] > 0 and t["exit"] > 0
    assert len(bot.state["buys"]) >= len(trades)
    assert all(b["mode"] == "live" for b in bot.state["buys"])


def test_invalid_ruse_settings_are_refused():
    with pytest.raises(ValueError):
        dataclasses.replace(tg.GuardConfig(), max_spread=0.0)
    with pytest.raises(ValueError):
        dataclasses.replace(tg.GuardConfig(), entry_retry_hours=-1)
