"""Évolution encadrée (trendguard/evolution.py) : candidats permis par le
niveau, fichier incapable de forcer autre chose, routine quotidienne (essai,
promotion, retour arrière, tempête), épreuves sur un petit marché, et
application par le bot au seul moment de la décision."""

import dataclasses
import json
import logging
import os
from datetime import date, timedelta

import pandas as pd
import pytest
from test_panel import _cfg

import trendguard_bot as tg
import v29
from panel import assistant as pa
from panel import server as ps
from test_trendguard import SIM_FROM, synthetic_market
from trendguard import autonomy
from trendguard import evolution as ev
from trendguard import trend_strategy as ts


@pytest.fixture
def logger():
    lg = logging.getLogger("test.evolution")
    lg.setLevel(logging.WARNING)
    return lg


def test_candidates_follow_the_level_and_never_touch_risk():
    p = ts.TrendParams()
    counts = [len(ev.candidates(p, lv)) for lv in ev.LEVELS]
    assert counts == [10, 20, 50, 180]                      # liberté croissante
    apprenti = ev.candidates(p, ev.LEVELS[0])
    assert all(len(c) == 1 and s == 1 for c, s in apprenti)
    assert {k for c, _s in ev.candidates(p, ev.LEVELS[3]) for k in c} == set(ev.SPACE)
    sizes = [(len(c), s) for c, s in ev.candidates(p, ev.LEVELS[2])]
    assert sizes == sorted(sizes)                            # le plus simple d'abord
    margins = [lv.margin for lv in ev.LEVELS]
    assert margins == sorted(margins) and [lv.crisis_tol for lv in ev.LEVELS] == \
        sorted([lv.crisis_tol for lv in ev.LEVELS], reverse=True)   # épreuves plus dures


def test_file_cannot_force_risk_or_unknown_values(tmp_path):
    assert ev.approved({"risk_pct": 0.05, "max_positions": 21, "breakout_n": 33,
                        "trail_atr": 6.0, "regime_sma": True, "init_stop_atr": "3"}) == {"trail_atr": 6.0}
    g = tg.GuardConfig(lock_file=str(tmp_path / "tg.lock"), db_file=":memory:",
                       log_file=os.devnull, evolution=True)
    with open(ev.state_path(g), "w", encoding="utf-8") as fh:
        json.dump({"params": {"trail_atr": 6.0, "risk_pct": 0.5}, "level": 99}, fh)
    p = ev.params_for(g)
    assert p.trail_atr == 6.0 and p.risk_pct == g.params.risk_pct
    assert ev.load_state(ev.state_path(g))["level"] == len(ev.LEVELS)
    assert ev.params_for(dataclasses.replace(g, evolution=False)) == g.params


class FakeJudge:
    def __init__(self, returns):
        self.returns = returns

    def window_return(self, p, since, until):
        return self.returns(p)


def test_daily_routine_trial_promotion_revert_and_storm(tmp_path, monkeypatch):
    base = ts.TrendParams()
    path = str(tmp_path / "tg.evolution.json")
    alerts = []
    picks = iter([{"trail_atr": 6.0}, {"breakout_n": 40}])
    monkeypatch.setattr(ev, "search", lambda j, cur, lv: {
        "chosen": next(picks), "trials": [("Deux époques", True, "")], "tried": 10, "passed_first": 1})
    d0 = date(2026, 10, 1)

    def run(day, returns=lambda p: 0.0, storm=False):
        return ev.run_daily(base, path, d0 + timedelta(days=day), lambda: FakeJudge(returns),
                            storm=storm, notify=alerts.append)

    st = run(0)
    assert st["params"] == {"trail_atr": 6.0} and st["probation"]["since"] == d0.isoformat()
    assert len(alerts) == 1 and "trail" not in alerts[0] and "stop suiveur" in alerts[0]
    assert run(0)["history"] == st["history"]                   # une seule routine par jour
    assert "jour 9 sur 30" in run(9)["last_text"]
    st = run(30, returns=lambda p: 5.0 if p.trail_atr == 6.0 else 4.0)
    assert st["level"] == 2 and st["probation"] is None and "Promotion" in st["last_text"]
    assert "Repos" in run(33)["last_text"]
    st = run(40)
    assert st["params"] == {"trail_atr": 6.0, "breakout_n": 40}
    st = run(70, returns=lambda p: 0.0 if p.breakout_n == 40 else 5.0)   # essai raté
    assert st["params"] == {"trail_atr": 6.0} and st["level"] == 1 and "Essai raté" in st["last_text"]
    assert "tempête" in run(200, storm=True)["last_text"].lower()
    st = ev.reset(path, d0 + timedelta(days=201))
    assert st["params"] == {} and st["level"] == 1 and len(alerts) == 4


def test_trials_on_a_small_market():
    close, volume = synthetic_market()
    i = close.index
    j = ev.Judge(close, volume,
                 periods=((str(i[SIM_FROM].date()), str(i[SIM_FROM + 140].date())),
                          (str(i[SIM_FROM + 141].date()), str(i[-1].date()))),
                 crises=[("Baisse test", str(i[SIM_FROM + 20].date()), str(i[SIM_FROM + 80].date()), "crise"),
                         ("Hausse test", str(i[SIM_FROM + 150].date()), str(i[SIM_FROM + 220].date()), "hausse")])
    cur = ts.TrendParams()
    new = dataclasses.replace(cur, trail_atr=6.0)
    lv = ev.LEVELS[0]
    for name, ok, detail in (ev.trial_epochs(j, cur, new, lv), ev.trial_costs(j, cur, new, lv),
                             ev.trial_riddles(j, cur, new, lv),
                             ev.trial_plateau(j, cur, new, lv, {"trail_atr": 6.0}),
                             ev.trial_luck(j, cur, new, lv)):
        assert isinstance(ok, bool) and detail
    assert ev.trial_riddles(j, cur, cur, lv)[1]                 # ne pas changer résout tout
    res = ev.search(j, cur, lv)
    assert res["tried"] == 10
    assert res["chosen"] is None or all(t[1] for t in res["trials"])


def test_history_cache_is_refreshed_only_when_stale(tmp_path):
    calls = []
    idx = pd.date_range("2026-01-01", periods=5, freq="D", tz="UTC")

    def fetch(_ex, bases, since):
        calls.append(since)
        close = pd.DataFrame({b.lower(): [1.0, 2.0, 3.0, 4.0, 5.0] for b in bases}, index=idx)
        return close, close * 1e6, {}

    now = pd.Timestamp("2026-01-06", tz="UTC").to_pydatetime()
    close, _v = ev.load_history(str(tmp_path), ["BTC", "ETH"], fetch=fetch, now=now)
    assert list(close.columns) == ["btc", "eth"] and len(calls) == 1
    ev.load_history(str(tmp_path), ["BTC", "ETH"], fetch=fetch, now=now)
    assert len(calls) == 1                                      # à jour : pas de téléchargement
    later = pd.Timestamp("2026-02-01", tz="UTC").to_pydatetime()
    ev.load_history(str(tmp_path), ["BTC", "ETH"], max_age_days=None, fetch=fetch, now=later)
    assert len(calls) == 1                                      # études : jamais rafraîchi
    ev.load_history(str(tmp_path), ["BTC", "ETH"], fetch=fetch, now=later)
    assert len(calls) == 2


def test_panel_and_assistant_follow_the_evolved_settings(tmp_path):
    g = _cfg(tmp_path, evolution=True)
    with open(ev.state_path(g), "w", encoding="utf-8") as fh:
        json.dump({"params": {"regime_sma": 175}, "level": 2, "last_text": "Essai réussi."}, fh)
    app = ps.build_app(g, demo=True)
    s = app.status()
    assert s["evolution"]["name"] == "Compagnon" and s["rules"]["regime_sma"] == 175
    assert s["evolution"]["changes"] == [{"param": ev.LABELS["regime_sma"], "from": "150", "to": "175"}]
    row = next(c for c in app.security_view()["checks"] if c["label"] == "Évolution encadrée")
    assert row["ok"] is None and "niveau 2 sur 4" in row["detail"]
    ctx = {"status": s}
    ans = pa.local_answer("Le bot peut-il évoluer ?", ctx)["answer"]
    assert "Compagnon" in ans and "150 → 175" in ans and "Essai réussi" in ans
    assert "175 derniers jours" in pa.local_answer("C'est quoi le régime ?", ctx)["answer"]
    off = ps.build_app(_cfg(tmp_path), demo=True)
    assert off.status()["evolution"] == {"enabled": False} and off.status()["rules"]["regime_sma"] == 150


def test_bot_applies_evolved_settings_only_when_enabled(tmp_path, logger, monkeypatch):
    g = tg.GuardConfig(run_mode="paper", universe=("BTC",), db_file=":memory:",
                       log_file=os.devnull, lock_file=str(tmp_path / "tg.lock"),
                       auto_diagnose_days=0, evolution=True)
    with open(ev.state_path(g), "w", encoding="utf-8") as fh:
        json.dump({"params": {"trail_atr": 6.0}}, fh)
    bot = tg.TrendGuardBot(g, logger, None, v29.Store(":memory:", logger), lambda *a, **k: True)
    bot._apply_evolution()
    assert bot.p.trail_atr == 6.0 and bot.p.risk_pct == g.params.risk_pct
    off = tg.TrendGuardBot(dataclasses.replace(g, evolution=False), logger, None,
                           v29.Store(":memory:", logger), lambda *a, **k: True)
    off._apply_evolution()
    assert off.p.trail_atr == g.params.trail_atr
    launched = []
    monkeypatch.setattr(autonomy.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    bot._launch_evolution("2026-10-01")
    assert launched == []                                       # cycle isolé : jamais lancé
    bot.track_uptime = True
    bot.state = {"peak_equity": 10_000.0, "last_equity": 8_000.0}
    bot._launch_evolution("2026-10-01")
    bot._launch_evolution("2026-10-01")
    assert len(launched) == 1 and launched[0][-2:] == ["quotidien", "--tempete"]
