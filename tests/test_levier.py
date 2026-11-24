"""Levier étudié par le bot (trendguard/levier.py) : calcul, règle fixée
d'avance, absence de regard vers le futur, routine hebdomadaire et lecture
par le panneau. Le levier n'est jamais appliqué : le bot trade au comptant."""

import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from test_trendguard import synthetic_market
from trendguard import autonomy, levier, savoir
from trendguard.bot_routines import RoutinesMixin

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
DAYS = pd.date_range("2026-01-01", periods=3, freq="D", tz="UTC")


def _per(calmar, dd, cagr=20.0):
    return {"cagr_pct": cagr, "max_dd_pct": dd, "calmar": calmar, "borrow_pct": 0.0}


def _rows(*policies):
    """Référence sans levier (Calmar 1, baisse −20 %) puis les politiques
    données : (nom, levier, [(Calmar, baisse) par période])."""
    rows = [{"name": levier.BASE, "lev": 1.0, "adaptive": False, "periods": [_per(1.0, -20.0), _per(1.0, -20.0)]}]
    rows += [{"name": n, "lev": lev, "adaptive": False, "periods": [_per(c, d) for c, d in per]}
             for n, lev, per in policies]
    for r in rows:
        r["why"] = "" if r is rows[0] else levier.refusal(r, rows[0])
    return rows


def test_leverage_multiplies_returns_and_charges_only_the_borrowed_part():
    equity = pd.Series([100.0, 110.0, 99.0], index=DAYS)
    exposure = pd.Series([0.8, 0.3, 0.5], index=DAYS)
    plain = levier.levered(equity, exposure, 1.0)
    assert plain.tolist() == pytest.approx([0.0, 0.1, -0.1])          # sans levier : rien d'emprunté
    two = levier.levered(equity, exposure, 2.0)
    # Jour 1 : exposition de la veille 80 % × 2 = 160 % → 60 % empruntés ;
    # jour 2 : 30 % × 2 = 60 % → rien d'emprunté.
    assert two.tolist() == pytest.approx([0.0, 0.2 - 0.6 * levier.BORROW_RATE / 365, -0.2])
    assert levier.borrowing_days(equity, exposure, 2.0) == pytest.approx(100 / 3)
    assert levier.borrowing_days(equity, exposure, 1.0) == 0.0


def test_a_leverage_series_applies_the_previous_close_decision():
    equity = pd.Series([100.0, 110.0, 99.0], index=DAYS)
    exposure = pd.Series([0.8, 0.3, 0.5], index=DAYS)
    decided = pd.Series([3.0, 1.0, 1.0], index=DAYS)                     # 3× décidé à la 1re clôture
    out = levier.levered(equity, exposure, decided)
    assert out.tolist() == pytest.approx([0.0, 0.3 - (0.8 * 3 - 1) * levier.BORROW_RATE / 365, -0.1])


def test_the_adaptive_policy_levers_only_near_the_top_of_a_bull_market():
    days = pd.date_range("2026-01-01", periods=5, freq="D", tz="UTC")
    equity = pd.Series([100.0, 105.0, 93.0, 99.0, 104.0], index=days)    # −11,4 % au 3e jour
    bull = pd.Series([True, True, True, False, True], index=days)
    lev = levier.adaptive(equity, bull)
    assert lev.tolist() == [levier.ADAPTIVE, levier.ADAPTIVE, 1.0, 1.0, levier.ADAPTIVE]


def test_no_look_ahead_the_future_never_changes_past_levered_returns():
    close, volume = synthetic_market()
    equity = close["btc"] / close["btc"].iloc[0] * 1000
    exposure = pd.Series(0.9, index=close.index)
    bull = close["btc"] > close["btc"].rolling(50).mean()
    before = levier.levered(equity, exposure, levier.adaptive(equity, bull))
    later = equity.copy()
    later.iloc[-1] *= 0.5                                                  # un krach le dernier jour
    after = levier.levered(later, exposure, levier.adaptive(later, bull))
    pd.testing.assert_series_equal(before.iloc[:-1], after.iloc[:-1])


def test_stats_and_ruin():
    days = pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC")
    assert levier.stats(pd.Series([0.0, 0.5, -1.2, 0.1], index=days))["max_dd_pct"] == -100.0
    flat = levier.stats(pd.Series(0.0, index=days))
    assert flat == {"cagr_pct": 0.0, "max_dd_pct": 0.0, "calmar": 0.0}


def test_the_rule_allows_leverage_only_if_it_holds_on_both_periods():
    rows = _rows(("1,25× fixe", 1.25, [(1.1, -25.0), (1.05, -28.0)]),
                 ("2× fixe", 2.0, [(1.2, -25.0), (0.9, -28.0)]),           # Calmar moins bon depuis 2023
                 ("3× fixe", 3.0, [(1.3, -31.0), (1.2, -20.0)]))           # trop près de l'arrêt d'urgence
    assert rows[1]["why"] == ""
    assert rows[2]["why"] == "Calmar moins bon que sans levier"
    assert rows[3]["why"] == "pire baisse −31 %, sous −30 %"
    assert levier.decide(rows) == (1.25, "1,25× fixe", "levier permis : 1,25× fixe")


def test_the_rule_picks_the_best_worst_calmar_then_the_smaller_leverage():
    best = _rows(("1,25× fixe", 1.25, [(1.1, -25.0), (1.05, -28.0)]),
                 ("1,5× fixe", 1.5, [(1.2, -26.0), (1.1, -29.0)]))
    assert levier.decide(best)[0] == 1.5
    tie = _rows(("1,25× fixe", 1.25, [(1.1, -25.0), (1.1, -28.0)]), ("1,5× fixe", 1.5, [(1.1, -26.0), (1.1, -29.0)]))
    assert levier.decide(tie)[0] == 1.25
    # Une période jamais vue (troisième) informe, mais ne juge pas.
    extra = _rows(("1,25× fixe", 1.25, [(1.1, -25.0), (1.05, -28.0), (0.1, -60.0)]))
    assert levier.decide(extra)[0] == 1.25


def test_no_policy_passing_means_no_leverage():
    rows = _rows(("1,25× fixe", 1.25, [(1.1, -25.0), (1.05, -40.3)]))
    assert rows[1]["why"] == "pire baisse −40 %, sous −30 %"
    assert levier.decide(rows) == (1.0, levier.BASE, "aucun levier (1×) : aucune politique ne passe la règle")


def test_evaluate_replays_the_strategy_once_per_period():
    close, volume = synthetic_market()
    periods = [("2024-06-01", "2024-12-31"), ("2025-01-01", "2025-11-30")]
    book = levier.evaluate(close, volume, periods)
    assert [r["name"] for r in book["rows"]] == [n for n, _l, _a in levier.policies()]
    assert book["rows"][0]["why"] == "" and all(p["borrow_pct"] == 0.0 for p in book["rows"][0]["periods"])
    assert book["periods"] == [list(p) for p in periods] and book["rate"] == levier.BORROW_RATE
    assert book["allowed"] == 1.0 or book["allowed"] in levier.FIXED + (levier.ADAPTIVE,)
    assert (book["allowed"], book["policy"], book["text"]) == levier.decide(book["rows"])
    assert len(book["exposure_pct"]) == 2 and all(0 <= e <= 100 for e in book["exposure_pct"])
    # Plus de levier, plus d'emprunt : jamais moins de jours empruntés.
    fixed = [r for r in book["rows"] if not r["adaptive"]]
    for k in range(2):
        days = [r["periods"][k]["borrow_pct"] for r in fixed]
        assert days == sorted(days)
    text = levier.render(dict(book, day="2026-11-24"))
    assert text.startswith("Levier, étude du 2026-11-24") and "Appliqué nulle part" in text


def test_due_once_a_week_and_render_before_any_study():
    assert levier.due(None, "2026-11-24") and levier.due({"day": "?"}, "2026-11-24")
    assert not levier.due({"day": "2026-11-24"}, "2026-11-30") and levier.due({"day": "2026-11-24"}, "2026-12-01")
    assert levier.render(None) == "Levier : pas encore étudié (aucun levier)."


def test_the_bot_restudies_leverage_once_a_week(monkeypatch):
    launched = []
    monkeypatch.setattr(autonomy, "launch_tool", lambda g, args, ext: launched.append((args, ext)) or True)
    bot = SimpleNamespace(g=SimpleNamespace(savoir=True), track_uptime=True, state={},
                          logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None),
                          LEVIER_MINUTE=RoutinesMixin.LEVIER_MINUTE)
    run = lambda when: RoutinesMixin._launch_levier(bot, when)              # noqa: E731
    run(NOW.replace(hour=1, minute=0))                                       # avant 01:30 UTC : rien
    run(NOW)
    run(NOW.replace(hour=23))                                                # même jour : rien de plus
    run(NOW.replace(day=30))                                                 # moins d'une semaine
    assert launched == [(["levier", "etude"], ".levier.log")]
    run(datetime(2026, 12, 1, 9, tzinfo=timezone.utc))                      # une semaine plus tard
    assert len(launched) == 2
    bot.g.savoir = False                                                     # sans noyau de savoir : rien
    run(datetime(2026, 12, 20, 9, tzinfo=timezone.utc))
    assert len(launched) == 2


def test_the_panel_reads_the_last_study(tmp_path):
    import trendguard_bot as tg
    from panel.data import BotData
    path = str(tmp_path / "savoir.db")
    g = tg.GuardConfig(run_mode="paper", db_file=str(tmp_path / "tg.db"), log_file=str(tmp_path / "tg.log"),
                       lock_file=str(tmp_path / "tg.lock"), savoir=True, savoir_db=path)
    memory = savoir.Memory(path)
    try:
        memory.put(levier.KEY, {"day": "2026-11-24", "allowed": 1.0, "policy": levier.BASE, "rows": _rows(),
                                "text": "aucun levier (1×) : aucune politique ne passe la règle",
                                "periods": [list(levier.DESIGN), ["2023-01-01", "2026-11-23"]], "rate": 0.1,
                                "exposure_pct": [17.0, 33.1]})
    finally:
        memory.close()
    view = BotData(g, market=None).levier()
    assert view is not None
    assert view["allowed"] == 1.0 and view["text"].startswith("aucun levier") and len(view["rows"]) == 1
    assert view["periods"][0] == list(levier.DESIGN) and view["exposure_pct"] == [17.0, 33.1]
    assert BotData(dataclasses.replace(g, savoir=False), market=None).levier() is None
