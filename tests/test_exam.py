"""Examen du bot (research/exam.py) : règles retirées à tour de rôle,
achats au hasard, pièges comptés, vrai contrôle du carnet d'ordres."""

from research import exam
from test_trendguard import SIM_FROM, synthetic_market


def _book(bid, ask, qty):
    return {"bids": [[bid, qty]], "asks": [[ask, qty], [ask * 1.02, qty * 100]]}


def test_order_book_check_is_the_bot_rule():
    ok = _book(99.95, 100.05, 100)                       # écart 0,1 %, 10 005 $ à moins de 1 %
    assert exam.book_check(ok, "ETH/USDT", 2_500, 0.005) is None
    assert "trop mince" in exam.book_check(ok, "ETH/USDT", 4_000, 0.005)
    assert "écart" in exam.book_check(_book(99, 101, 100), "ETH/USDT", 100, 0.005)
    spread, depth = exam.book_stats(ok)
    assert round(spread, 3) == 0.1 and round(depth) == 10_005


def test_random_hooks_keep_the_rules_they_do_not_test():
    snap = {"eth": {"close": 10.0, "prior_high": 12.0, "mom": 0.5},
            "ada": {"close": 1.0, "prior_high": 0.9, "mom": float("nan")}}
    every = exam.random_entries(1, 1.0).choose(0, snap)
    assert all(s["prior_high"] == 0.0 and s["mom"] > 0 for s in every.values())
    none = exam.random_entries(1, 0.0).choose(0, snap)
    assert all(s["mom"] < 0 for s in none.values())
    shuffled = exam.shuffled_momentum(1).choose(0, snap)
    assert shuffled["eth"]["mom"] > 0 and shuffled["ada"] is snap["ada"]


def test_traps_are_counted_per_trap():
    res = {t.split("::")[-1]: "passed" for _n, _r, tests in exam.TRAPS for t in tests}
    first = exam.TRAPS[0][2][0].split("::")[-1]
    res[first] = "failed"
    rows = exam.run_traps(runner=lambda ids: res)
    assert len(rows) == len(exam.TRAPS) and rows[0][2] == rows[0][3] - 1
    assert all(good == total for _n, _r, good, total in rows[1:])


def test_report_on_a_small_market(monkeypatch):
    monkeypatch.setattr(exam, "SEEDS", 2)
    close, volume = synthetic_market()
    mid = close.index[SIM_FROM + 140]
    pers = ((str(close.index[SIM_FROM].date()), str(mid.date())),
            (str(close.index[SIM_FROM + 141].date()), str(close.index[-1].date())))
    books = ("29/09/2026 à 16:00 UTC", [
        {"asset": "eth", "spread": 0.01, "depth": 90_000.0, "max_buy": 30_000.0, "why": None},
        {"asset": "ada", "error": "NetworkError"}])
    text = exam.report(close, volume, [("Piège", "réaction", 2, 2), ("Autre", "réaction", 0, 1)],
                       books, 2_500.0, pers)
    assert "**Bot complet**" in text and "Achats au hasard" in text and "Achat conservé" in text
    assert "1 pièges déjoués sur 2" in text and "✗ ÉCHEC" in text
    assert "1 carnets sur 1 laisseraient acheter" in text and "illisible (NetworkError)" in text
    assert "Sans lecture du marché" in text and "Ce qu'il ne sait pas faire" in text
