"""Évolution encadrée (trendguard/evolution.py) : candidats permis par le
niveau, fichier incapable de forcer autre chose, routine quotidienne (essai,
promotion, retour arrière, tempête), épreuves sur un petit marché, palier de
risque (1 % → 2 %, montée lente, descente immédiate, un changement à la
fois), et application par le bot au seul moment de la décision."""

import dataclasses
import json
import logging
import os
from datetime import date, timedelta

import numpy as np
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
        json.dump({"params": {"regime_sma": 175}, "level": 2, "last_text": "Essai réussi.",
                   "risk": {"step": 1.25, "last_text": "Palier relevé à 1,25 % par achat."}}, fh)
    app = ps.build_app(g, demo=True)
    s = app.status()
    assert s["evolution"]["name"] == "Compagnon" and s["rules"]["regime_sma"] == 175
    assert s["evolution"]["changes"] == [{"param": ev.LABELS["regime_sma"], "from": "150", "to": "175"}]
    assert s["evolution"]["risk"]["pct"] == pytest.approx(1.25) and s["evolution"]["risk"]["max_pct"] == 2.0
    checks = {c["label"]: c for c in app.security_view()["checks"]}
    row = checks["Évolution encadrée"]
    assert row["ok"] is None and "niveau 2 sur 4" in row["detail"]
    assert "palier de risque 1,25 % par achat (2 % au plus" in row["detail"]
    assert "reprise automatique après 60 jours" in checks["Arrêt d'urgence"]["detail"]
    ctx = {"status": s}
    ans = pa.local_answer("Le bot peut-il évoluer ?", ctx)["answer"]
    assert "Compagnon" in ans and "150 → 175" in ans and "Essai réussi" in ans
    assert "Palier actuel : 1,25 % par achat" in ans
    assert "Palier actuel : 1,25 %" in pa.local_answer("Quel risque prend-il ?", ctx)["answer"]
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
    assert len(launched) == 1 and launched[0][-4:] == ["quotidien", "--tempete", "--baisse", "0.2000"]
    bot.state = {"peak_equity": 10_000.0, "last_equity": 9_950.0, "last_regime_bull": True}
    bot._launch_evolution("2026-10-02")
    assert launched[1][-5:] == ["quotidien", "--baisse", "0.0050", "--marche", "haussier"]


# ---------- Palier de risque ----------

class RiskJudge:
    """Juge factice du palier : plus de risque, plus de rendement ; Calmar
    meilleur (`better`) ou non ; pire baisse de `worst` % ; résultat d'un
    essai donné par `window`."""

    def __init__(self, better=True, worst=20.0, window=lambda p: 0.0):
        self.better, self.worst, self.window = better, worst, window

    def period(self, p, i):
        k = p.risk_pct / 0.01 - 1
        return {"calmar": 1.5 + (0.1 if self.better else -0.1) * k, "cagr_pct": 30.0 + 10 * k}

    def equity(self, p):
        low = 200 * (1 - self.worst / 100)
        v = np.concatenate([np.linspace(100, 200, 50), np.linspace(200, low, 10), np.linspace(low, 300, 50)])
        return pd.Series(v, index=pd.date_range("2020-01-01", periods=len(v), tz="UTC"))

    def window_return(self, p, since, until):
        return self.window(p)


def test_risk_steps_are_bounded_and_the_file_cannot_force_more(tmp_path):
    p = ts.TrendParams()
    assert ev.risk_steps(p) == [1.0, 1.25, 1.5, 1.75, 2.0]
    assert ev.risk_steps(p, 0.015) == [1.0, 1.25, 1.5] and ev.risk_steps(p, 0.01) == [1.0]
    assert ev.risk_steps(dataclasses.replace(p, risk_pct=0.015)) == [1.0, 1.25]   # 2 % au plus
    q = ev.at_step(p, 2.0).validate()
    assert (q.risk_pct, q.max_total_risk, q.max_positions) == (0.02, 0.12, p.max_positions)
    g = tg.GuardConfig(lock_file=str(tmp_path / "tg.lock"), db_file=":memory:",
                       log_file=os.devnull, evolution=True, risk_max_pct=0.015)
    for raw, want in ((1.5, 1.5), (1.75, 1.0), (5, 1.0), (True, 1.0), ("2", 1.0), (None, 1.0)):
        with open(ev.state_path(g), "w", encoding="utf-8") as fh:
            json.dump({"risk": {"step": raw, "probation": {"since": 3}}}, fh)
        assert ev.risk_step_for(g) == want
    assert ev.risk_step_for(dataclasses.replace(g, evolution=False)) == 1.0
    assert ev.summary(g)["risk"] == {"step": 1.0, "pct": 1.0, "base_pct": 1.0, "max_pct": 1.5,
                                     "probation": None, "rest_until": None, "last_text": None}
    assert ev.risk_steps(p, 0.005) == [1.0]                  # sous TG_RISK_PCT : aucun palier
    for bad in ({"risk_max_pct": 0.03}, {"risk_max_pct": 0.0}, {"kill_resume_days": -1}):
        with pytest.raises(ValueError):
            tg.GuardConfig(**bad)


def test_risk_step_climbs_slowly_on_trials_and_falls_at_once(monkeypatch):
    monkeypatch.setattr(ev, "block_luck", lambda eq, **kw: (50.0, 30.0))   # hasard : −30 %
    base = ts.TrendParams()
    steps = ev.risk_steps(base)
    st, alerts, d0 = {}, [], date(2026, 10, 1)

    def run(day, judge=None, dd=0.01, bull=True, storm=False, busy=False):
        ev.run_risk(st, base, d0 + timedelta(days=day), lambda: judge or RiskJudge(), steps, 0.40,
                    dd, bull, storm=storm, busy=busy, notify=alerts.append)
        return st["risk"]

    r = run(0)
    assert r["step"] == 1.25 and r["probation"] == {"since": "2026-10-01", "old": 1.0, "new": 1.25}
    assert len(alerts) == 1 and "1,25 %" in alerts[0] and "Hasard" in alerts[0]
    assert "jour 9 sur 30" in run(9)["last_text"]
    r = run(10, dd=0.12)                                   # baisse de 12 % : retour immédiat
    assert r["step"] == 1.0 and r["probation"] is None and "Retour immédiat à 1 %" in r["last_text"]
    assert run(20)["step"] == 1.0 and "Repos jusqu'au 2026-12-10" in st["risk"]["last_text"]
    assert run(71)["step"] == 1.25                         # repos fini : nouvel essai
    r = run(101, judge=RiskJudge(window=lambda p: -10.0 if p.risk_pct > 0.01 else -5.0))
    assert r["step"] == 1.0 and "Essai raté" in r["last_text"] and len(alerts) == 4
    # Montée refusée : chaque épreuve, puis chaque condition, dit pourquoi.
    st.clear()
    r = run(0, judge=RiskJudge(better=False))
    assert r["step"] == 1.0 and "refusé" in r["last_text"] and "Deux époques" in r["last_text"]
    assert "Pire baisse" in run(1, judge=RiskJudge(worst=35.0))["last_text"]
    monkeypatch.setattr(ev, "block_luck", lambda eq, **kw: (50.0, 39.0))
    assert "« Hasard »" in run(2)["last_text"] and "−39 %" in st["risk"]["last_text"]
    monkeypatch.setattr(ev, "block_luck", lambda eq, **kw: (50.0, 30.0))
    assert "6,0 % sous son plus haut" in run(3, dd=0.06)["last_text"]
    assert "marché baissier" in run(4, bull=False)["last_text"]
    assert "un seul changement" in run(5, busy=True)["last_text"]
    assert "inconnue" in run(6, dd=None)["last_text"]
    assert run(7)["step"] == 1.25 and len(alerts) == 5      # tout est réuni : un cran
    r = run(8, bull=False)                                  # marché baissier : 1 %, sans repos
    assert r["step"] == 1.0 and r.get("rest_until") is None and len(alerts) == 6
    # L'analyse ne justifie plus le palier : un cran plus bas.
    st.clear()
    st["risk"] = {"step": 1.5}
    r = run(0, judge=RiskJudge(better=False))
    assert r["step"] == 1.25 and "ne justifie plus 1,5 %" in r["last_text"]
    st.clear()
    st["risk"] = {"step": 2.0}
    assert "le plus haut permis : 2 %" in run(0)["last_text"]
    assert "tempête" in run(1, storm=True)["last_text"] and st["risk"]["step"] == 1.0
    st.clear()
    ev.run_risk(st, base, d0, lambda: RiskJudge(), [1.0], 0.40, 0.0, True)   # TG_RISK_MAX_PCT=0.01
    assert st["risk"]["step"] == 1.0 and "le plus haut permis : 1 %" in st["risk"]["last_text"]


def test_daily_routine_changes_one_thing_at_a_time(tmp_path, monkeypatch):
    base = ts.TrendParams()
    path = str(tmp_path / "tg.evolution.json")
    monkeypatch.setattr(ev, "block_luck", lambda eq, **kw: (50.0, 30.0))
    monkeypatch.setattr(ev, "search", lambda j, cur, lv: {
        "chosen": {"trail_atr": 6.0}, "trials": [("Deux époques", True, "")], "tried": 10,
        "passed_first": 1})
    risk = {"steps": ev.risk_steps(base), "kill": 0.40, "dd": 0.0, "bull": True}
    d0 = date(2026, 10, 1)
    loads = []

    def judge():
        loads.append(1)
        return RiskJudge()

    st = ev.run_daily(base, path, d0, judge, risk=risk)
    assert st["params"] == {"trail_atr": 6.0} and st["risk"]["step"] == 1.0
    assert "un seul changement" in st["risk"]["last_text"] and len(loads) == 1
    st = ev.run_daily(base, path, d0 + timedelta(days=31), judge, risk=risk)
    assert st["probation"] is None and st["risk"]["step"] == 1.25     # réglage confirmé, puis palier
    st = ev.run_daily(base, path, d0 + timedelta(days=40), judge, risk=risk)
    assert "palier de risque est à l'essai" in st["last_text"] and st["params"] == {"trail_atr": 6.0}
    st = ev.reset(path, d0 + timedelta(days=41))
    assert st["risk"]["step"] == 1.0 and st["risk"]["probation"] is None and "palier" in st["last_text"]


def test_risk_trials_on_a_small_market():
    close, volume = synthetic_market()
    i = close.index
    j = ev.Judge(close, volume,
                 periods=((str(i[SIM_FROM].date()), str(i[SIM_FROM + 140].date())),
                          (str(i[SIM_FROM + 141].date()), str(i[-1].date()))), crises=[])
    trials = ev.risk_trials(j, ts.TrendParams(), 1.0, 1.25, 0.40)
    assert trials and trials[0][0] == "Deux époques"
    assert all(isinstance(ok, bool) and detail for _n, ok, detail in trials)
    assert all(ok for _n, ok, _d in trials[:-1])            # s'arrête à la première ratée


def test_bot_applies_the_risk_step_and_cuts_it_at_the_first_alert(tmp_path, logger):
    g = tg.GuardConfig(run_mode="paper", universe=("BTC",), db_file=":memory:",
                       log_file=os.devnull, lock_file=str(tmp_path / "tg.lock"),
                       auto_diagnose_days=0, evolution=True)
    with open(ev.state_path(g), "w", encoding="utf-8") as fh:
        json.dump({"risk": {"step": 1.5}}, fh)
    bot = tg.TrendGuardBot(g, logger, None, v29.Store(":memory:", logger), lambda *a, **k: True)
    bot._apply_evolution()
    assert bot.risk_step == 1.5 and bot.p.risk_pct == g.params.risk_pct
    day = "2026-10-01"
    assert bot._risk_step(day, 9_500.0, 10_000.0, True) == 1.5        # baisse de 5 % : gardé
    assert bot._risk_step(day, 8_900.0, 10_000.0, True) == 1.0        # baisse de 11 % : 1 %
    assert bot._risk_step(day, 10_000.0, 10_000.0, False) == 1.0      # marché baissier
    bot.state = {"halted": True}
    assert bot._risk_step(day, 10_000.0, 10_000.0, True) == 1.0
    bot.state = {"auto_resumed_at": "2026-09-01"}                     # reprise en douceur
    assert bot._gentle(day) == 0.5 and bot._risk_step(day, 10_000.0, 10_000.0, True) == 1.0
    assert bot._gentle("2026-12-01") == 1.0
