"""Conditions réelles : horloge du PC décalée, connexion lente,
vérification publique sur Binance (sans clé ni ordre)."""

import dataclasses
import io
import time
from datetime import datetime, timedelta, timezone

import pytest

import diagnostics as dg
import trend_strategy as ts
import trendguard_bot as tg
import v29
from conftest import open_live_position
from test_trendguard import DAY, SIM_FROM, feed, make_bot, synthetic_market


# ---------- Client Binance ----------

def test_make_binance_spot_only_long_timeout_clock_corrected():
    ex = v29.make_binance()
    # ccxt fusionne ses propres réglages (ex. loadAllOptions) : seul le type
    # de marché demandé compte.
    assert ex.options["fetchMarkets"]["types"] == ["spot"]
    assert ex.options["adjustForTimeDifference"] is True
    assert ex.timeout == v29.BINANCE_TIMEOUT_MS >= 30_000
    assert not ex.apiKey
    keyed = v29.make_binance("k", "s", testnet=True)
    assert keyed.apiKey == "k" and keyed.secret == "s"
    assert "testnet" in str(keyed.urls["api"]).lower()


def test_resync_clock_is_safe():
    class Down:
        def fetch_time(self):
            raise v29.ccxt.NetworkError("timeout")
    v29.set_clock_offset_ms(1234)
    assert v29.resync_clock(Down()) is None
    assert v29.resync_clock(object()) is None
    assert v29.clock_offset_ms() == 1234          # dernier écart conservé


# ---------- Heure du bot = heure de Binance ----------

class _SlowLink:
    """Horloge et serveur simulés : chaque requête prend `rtt` secondes et
    le serveur a `offset_ms` d'avance sur le PC."""

    def __init__(self, rtts, offset_ms):
        self.t = 1_000_000.0
        self.rtts = list(rtts)
        self.offset_ms = offset_ms

    def time(self):
        return self.t

    def fetch_time(self):
        rtt = self.rtts.pop(0)
        mid = self.t + rtt / 2
        self.t += rtt
        return mid * 1000 + self.offset_ms


def test_clock_measure_uses_fastest_round_trip_midpoint():
    link = _SlowLink([3.2, 0.4, 1.5], offset_ms=1300)
    m = v29.measure_exchange_clock(link, samples=3, time_fn=link.time)
    assert m.offset_ms == pytest.approx(1300)
    assert m.latency_ms == pytest.approx(400) and m.uncertainty_ms == pytest.approx(200)
    assert m.samples == 3
    # La méthode de ccxt (heure de réception − heure du serveur) se trompe
    # d'un demi aller-retour : 1,6 s sur la mesure lente.
    t0 = link.t = 5_000_000.0
    link.rtts = [3.2]
    server = link.fetch_time()
    ccxt_offset = -(link.t * 1000 - server)
    assert abs(ccxt_offset - 1300) == pytest.approx(1600)
    assert t0


def test_sync_puts_bot_and_signed_orders_on_binance_time():
    from fake_binance import FakeBinance
    fb = FakeBinance()
    fb.server_offset_ms = 90_000                 # PC en retard de 90 s
    with pytest.raises(v29.ccxt.InvalidNonce):
        fb._check_timestamp()                     # avant : ordres refusés
    sync = v29.sync_exchange_clock(fb)
    assert sync.offset_ms == pytest.approx(90_000, abs=200)
    assert fb.options["timeDifference"] == pytest.approx(-90_000, abs=200)
    lag = (v29._utcnow() - datetime.now(timezone.utc)).total_seconds()
    assert lag == pytest.approx(90, abs=0.3)
    fb._check_timestamp()                         # après : acceptés
    assert "retard de 90,0 s" in v29.describe_clock(sync.offset_ms)


def test_order_retried_after_clock_resync(live_env):
    env = live_env
    env.fb.server_offset_ms = 20_000             # l'horloge du PC a dérivé
    res = open_live_position(env)
    assert res == v29.EntryResult.OPENED
    assert env.fb.time_calls >= 1
    assert v29.clock_offset_ms() == pytest.approx(20_000, abs=200)
    buys = [o for o in env.fb.orders.values() if o["side"] == "buy"]
    assert len(buys) == 1                        # jamais d'achat en double
    assert env.ctx.position.protection_mode != "NONE"
    assert not env.ctx.risk.halted


def test_persistent_clock_error_rejects_cleanly(live_env):
    env = live_env
    env.fb.server_offset_ms = 20_000

    def down():
        raise v29.ccxt.NetworkError("timeout")
    env.fb.fetch_time = down                     # recalage impossible
    res = open_live_position(env)
    assert res != v29.EntryResult.OPENED
    assert not env.ctx.position.in_position
    assert not [o for o in env.fb.orders.values() if o["side"] == "buy"]
    assert env.ctx.pending_order is None and not env.ctx.risk.halted


@pytest.mark.parametrize("mode", ["paper", "live"])
def test_bot_syncs_clock_at_boot_then_hourly(logger, mode):
    close, volume = synthetic_market()
    bot, fb = make_bot(mode, close, logger)
    feed(fb, close, volume)
    fb.set_server_offset(45_000)
    assert bot.boot()
    assert bot.state["clock_offset_ms"] == pytest.approx(45_000, abs=200)
    assert v29.clock_offset_ms() == pytest.approx(45_000, abs=200)
    calls = fb.time_calls
    d = close.index[SIM_FROM]
    now = d.to_pydatetime() + DAY + timedelta(minutes=5)
    bot.run_cycle(now=now)
    assert fb.time_calls == calls                # pas à chaque cycle
    bot._last_clock_sync -= 3601
    bot.run_cycle(now=now + timedelta(minutes=1))
    assert fb.time_calls > calls


def test_bot_decides_on_binance_time_not_pc_time(logger, monkeypatch):
    """PC en retard : chez Binance il est 00:03 (la bougie du jour est
    close), sur le PC pas encore. Le bot décide à l'heure de Binance."""
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    feed(fb, close, volume)
    pc_now = datetime.now(timezone.utc)
    binance_now = (pc_now + timedelta(days=1)).replace(hour=0, minute=3, second=0,
                                                       microsecond=0)
    fb.set_server_offset((binance_now - pc_now).total_seconds() * 1000)
    assert bot.boot()
    seen = {}
    monkeypatch.setattr(bot, "daily_decision",
                        lambda now, day: seen.update(now=now, day=day))
    bot.run_cycle()                               # heure réelle, sans `now`
    assert abs((seen["now"] - binance_now).total_seconds()) < 60
    assert seen["day"] == (binance_now - timedelta(days=1)).date().isoformat()
    assert seen["day"] != tg.last_closed_day(datetime.now(timezone.utc), 120)


def test_logs_are_stamped_with_binance_time(tmp_path):
    """Horodatage complet (secondes ET millisecondes) à l'heure de Binance,
    y compris pour un écart non entier : 1,7 s."""
    lg = tg.build_guard_logger(str(tmp_path / "t.log"))
    v29.set_clock_offset_ms(1700)
    stamps = []
    for _ in range(20):
        lg.info("repère")
        stamps.append(datetime.now() + timedelta(milliseconds=1700))
        time.sleep(0.013)
    for h in lg.handlers:
        h.flush()
    lines = (tmp_path / "t.log").read_text(encoding="utf-8").splitlines()
    for line, expected in zip(lines, stamps):
        logged = datetime.strptime(line.split(" [")[0], "%Y-%m-%d %H:%M:%S,%f")
        assert abs((logged - expected).total_seconds()) < 0.1, line
    for h in list(lg.handlers):
        h.close()
        lg.removeHandler(h)


def test_diagnosis_reports_clock_offset_as_handled():
    class Skewed:
        def fetch_time(self):
            return int(time.time() * 1000) + 3000   # PC 3 s en retard
    now = datetime.now(timezone.utc)
    findings = dg.check_system(Skewed(), {}, str(now.date()), "", None, now)
    clock = [f for f in findings if f.message.startswith("Horloge")][0]
    assert clock.level == "INFO" and "l'heure de Binance" in clock.message
    assert "retard" in clock.message

    class Far:
        def fetch_time(self):
            return int(time.time() * 1000) + 2 * 3600 * 1000
    clock = [f for f in dg.check_system(Far(), {}, str(now.date()), "", None, now)
             if f.message.startswith("Horloge")][0]
    assert clock.level == "ATTENTION" and clock.reco


# ---------- Vérification publique (sans clé API) ----------

def _entry_day(close, volume):
    res = ts.backtest(close, volume, ts.TrendParams(),
                      str(close.index[SIM_FROM].date()), str(close.index[-1].date()))
    first = min(t["entry_date"] for t in res.trades)
    return close.index.get_loc(first)


def _public_verify(bot, fb, close, volume, day_idx):
    feed(fb, close, volume)
    d = close.index[day_idx]
    for a in close.columns:
        fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
    out = io.StringIO()
    rc = tg.cmd_verify_public(bot.g, exchange=fb, out=out,
                              now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    return rc, out.getvalue()


def test_public_verify_builds_real_orders_without_sending(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    rc, text = _public_verify(bot, fb, close, volume, _entry_day(close, volume))
    assert rc == 0, text
    assert "mode public, aucun ordre" in text
    assert "stop STOP_LOSS de" in text           # au moins un achat préparé
    assert "✅ Tout est conforme côté marché." in text
    assert not fb.open_orders() and all(not f.orders for f in fb.fakes.values())
    assert fb.free["USDT"] == pytest.approx(10_000.0)


def test_public_verify_flags_missing_stop_type(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    for f in fb.fakes.values():
        f.order_types = ["LIMIT", "MARKET", "LIMIT_MAKER"]
    rc, text = _public_verify(bot, fb, close, volume, _entry_day(close, volume))
    assert rc == 1
    assert "aucun ordre stop disponible" in text or "Chargement" in text


def test_public_verify_flags_orders_under_minimum(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, paper_capital=60.0)
    rc, text = _public_verify(bot, fb, close, volume, _entry_day(close, volume))
    assert rc == 1
    assert "Capital simulé <" in text


def test_verify_without_keys_falls_back_to_public(monkeypatch, logger):
    close, _ = synthetic_market()
    bot, _fb = make_bot("paper", close, logger)
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    called = {}
    monkeypatch.setattr(tg, "cmd_verify_public",
                        lambda g, now=None, out=None: called.setdefault("ok", 0))
    out = io.StringIO()
    assert tg.cmd_verify(bot.g, out=out) == 0
    assert "ok" in called and "vérification publique" in out.getvalue()


# ---------- Panne de données : jamais de vente sur une panne réseau ----------

def _flaky_ohlcv(fb, symbol, failures):
    """Les `failures` prochains fetch_ohlcv de `symbol` échouent (timeout)."""
    real = fb.fetch_ohlcv
    left = {"n": failures}

    def fetch(sym, timeframe="1d", since=None, limit=500):
        if sym == symbol and left["n"] > 0:
            left["n"] -= 1
            raise v29.ccxt.RequestTimeout("binance GET klines timeout")
        return real(sym, timeframe, since, limit)
    fb.fetch_ohlcv = fetch
    return left


def _run_until_holding(bot, fb, close, volume):
    feed(fb, close, volume)
    for i in range(SIM_FROM, len(close)):
        d = close.index[i]
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
        if bot.state["paper"]["holdings"]:
            return i
    raise AssertionError("aucune position ouverte")


def test_transient_ohlcv_error_is_retried(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    i = _run_until_holding(bot, fb, close, volume)
    held = next(iter(bot.state["paper"]["holdings"]))
    left = _flaky_ohlcv(fb, f"{held.upper()}/USDT", failures=2)
    d = close.index[i + 1]
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert left["n"] == 0
    assert bot.state["last_decision_day"] == str(d.date())    # décidé malgré tout


def test_held_asset_without_data_is_never_sold(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    i = _run_until_holding(bot, fb, close, volume)
    held = next(iter(bot.state["paper"]["holdings"]))
    trades_before = len(bot.state["trades"])
    left = _flaky_ohlcv(fb, f"{held.upper()}/USDT", failures=10 ** 6)
    d = close.index[i + 1]
    now = d.to_pydatetime() + DAY + timedelta(minutes=5)
    bot.run_cycle(now=now)
    assert held in bot.state["paper"]["holdings"]            # pas vendue
    assert len(bot.state["trades"]) == trades_before
    assert bot.state["last_decision_day"] != str(d.date())   # reportée
    assert "decision_deferred_since" in bot.state
    left["n"] = 0                                            # données revenues
    bot.run_cycle(now=now + timedelta(minutes=1))
    assert bot.state["last_decision_day"] == str(d.date())
    assert "decision_deferred_since" not in bot.state


def test_unheld_asset_without_data_is_skipped(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    assert bot.boot()
    _flaky_ohlcv(fb, "DOGE/USDT", failures=10 ** 6)
    feed(fb, close, volume)
    d = close.index[SIM_FROM]
    for a in close.columns:
        fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
    real = fb.fetch_ohlcv
    fb.fetch_ohlcv = real                                    # feed a remplacé l'historique
    _flaky_ohlcv(fb, "DOGE/USDT", failures=10 ** 6)
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert bot.state["last_decision_day"] == str(d.date())
    assert "doge" not in bot.state["paper"]["holdings"]


def test_one_pair_network_error_does_not_block_other_protections(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("live", close, logger)
    feed(fb, close, volume)
    assert bot.boot()
    for i in range(SIM_FROM, len(close)):
        d = close.index[i]
        for a in close.columns:
            fb.set_price(f"{a.upper()}/USDT", float(close[a].loc[d]))
        bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
        held = [s for s in bot.slots.values() if s.ctx.position.in_position]
        if len(held) >= 2:
            break
    else:
        pytest.skip("pas deux positions simultanées dans ce marché synthétique")
    broken, other = held[0], held[1]
    real_ticker = fb.fetch_ticker

    def ticker(sym):
        if sym == broken.symbol:
            raise v29.ccxt.RequestTimeout("timeout")
        return real_ticker(sym)
    fb.fetch_ticker = ticker
    crash = other.ctx.position.sl_price * 0.97               # krach sur l'autre paire
    fb.set_price(other.symbol, crash)
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(hours=3))
    assert not other.ctx.position.in_position                 # protégée malgré la panne
    assert broken.ctx.position.in_position


# ---------- Diagnostic : l'état normal n'alerte pas ----------

def _book(close, risk_each, weight):
    return [{"asset": a, "qty": 10_000 * weight / close[a].iloc[-1],
             "entry": close[a].iloc[-1],
             "stop": close[a].iloc[-1] * (1 - risk_each / weight),
             "risk_quote": 10_000 * risk_each} for a in ("eth", "xrp", "doge")]


def test_full_book_at_design_cap_is_not_a_warning():
    close, _ = synthetic_market()
    prices = {a: float(close[a].iloc[-1]) for a in close.columns}
    normal = dg.check_portfolio(_book(close, 0.02, 0.22), close, prices, 10_000.0)
    risk = [f for f in normal if f.message.startswith("Perte si")][0]
    crash = [f for f in normal if f.message.startswith("Krach")]
    assert risk.level == "OK" and all(f.level == "INFO" for f in crash)
    heavy = dg.check_portfolio(_book(close, 0.05, 0.40), close, prices, 10_000.0)
    assert [f for f in heavy if f.message.startswith("Perte si")][0].level == "ATTENTION"
    assert any(f.level == "ATTENTION" and f.reco for f in heavy
               if f.message.startswith("Krach"))


def test_weekly_auto_diagnosis_includes_strategy_tournament(logger, monkeypatch):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger, auto_diagnose_days=7)
    feed(fb, close, volume)
    assert bot.boot()
    seen = {}
    monkeypatch.setattr(dg, "run_diagnosis", lambda *a, **k: seen.update(k) or
                        [dg.Finding("x", "OK", "ok")])
    d = close.index[SIM_FROM]
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert "alternatives" in seen["sections"]


def test_network_outage_defers_quickly_without_waiting_every_pair(logger):
    close, volume = synthetic_market()
    bot, fb = make_bot("paper", close, logger)
    feed(fb, close, volume)
    assert bot.boot()
    calls = []

    def down(sym, timeframe="1d", since=None, limit=500):
        calls.append(sym)
        raise v29.ccxt.RequestTimeout("timeout")
    fb.fetch_ohlcv = down
    d = close.index[SIM_FROM]
    bot.run_cycle(now=d.to_pydatetime() + DAY + timedelta(minutes=5))
    assert bot.state.get("last_decision_day") != str(d.date())
    assert "decision_deferred_since" in bot.state
    assert len(calls) <= 3                       # BTC d'abord : arrêt immédiat


def test_stalled_cycle_dumps_thread_stacks(logger, tmp_path, monkeypatch):
    """Un cycle bloqué (ici 0,3 s au lieu de 20 min) laisse la pile des
    threads dans <journal>.blocage.txt ; un cycle normal n'écrit rien."""
    close, volume = synthetic_market()
    log = tmp_path / "tg.log"
    bot, fb = make_bot("paper", close, logger)
    bot.g = dataclasses.replace(bot.g, log_file=str(log))
    monkeypatch.setattr(tg.TrendGuardBot, "STALL_DUMP_SEC", 0.3)
    calls = {"n": 0}

    def cycle(now=None):
        calls["n"] += 1
        if calls["n"] == 2:
            time.sleep(1.0)                      # blocage simulé
            tg._running = False
    monkeypatch.setattr(bot, "run_cycle", cycle)
    monkeypatch.setattr(tg, "_sleep", lambda s, should_stop=None: None)
    tg._running = True
    try:
        bot.run_forever()
    finally:
        tg._running = True
    dump = (tmp_path / "tg.log.blocage.txt").read_text(encoding="utf-8")
    assert "Timeout" in dump and "cycle" in dump and calls["n"] == 2


# ---------- Saisie guidée des clés API ----------

KEY, SECRET = "K" * 32 + "k" * 32, "S" * 32 + "s" * 32


class _Account:
    """Faux Binance : n'accepte que la paire (KEY, SECRET) sur `env`."""

    def __init__(self, key, secret, testnet, env="testnet", down=False):
        self.key, self.secret, self.testnet = key, secret, testnet
        self.env, self.down = env, down
        self.options = {}

    def fetch_time(self):
        return int(time.time() * 1000)

    def fetch_balance(self):
        if self.down is True or (self.down == "real" and not self.testnet):
            raise v29.ccxt.RequestTimeout("timeout")
        right_env = self.testnet == (self.env == "testnet")
        if self.key == KEY and right_env:
            if self.secret != SECRET:
                raise v29.ccxt.AuthenticationError(
                    'binance {"code":-1022,"msg":"Signature for this request is not valid."}')
            return {"total": {}}
        raise v29.ccxt.AuthenticationError(
            'binance {"code":-2015,"msg":"Invalid API-key, IP, or permissions for '
            'action, request ip: 41.202.1.2"}')


def _set_keys(tmp_path, answers, env="testnet", down=False):
    path = tmp_path / ".env"
    path.write_text("RUN_MODE=paper\n", encoding="utf-8")
    choice, *secrets = answers
    out = io.StringIO()
    rc = tg.cmd_set_keys(str(path), ask=lambda _p: secrets.pop(0),
                         read=lambda _p: choice,
                         factory=lambda k, s, t: _Account(k, s, t, env, down), out=out)
    return rc, path.read_text(encoding="utf-8"), out.getvalue()


def test_set_keys_saves_only_keys_accepted_by_binance(tmp_path):
    rc, env, text = _set_keys(tmp_path, ["1", KEY, SECRET])
    assert rc == 0 and f"BINANCE_API_KEY={KEY}" in env
    assert f"BINANCE_API_SECRET={SECRET}" in env and "BINANCE_TESTNET=true" in env
    assert "RUN_MODE=paper" in env and "✅" in text
    assert KEY not in text and SECRET not in text          # jamais affichées


def test_set_keys_fixes_swapped_keys_and_wrong_account(tmp_path):
    rc, env, text = _set_keys(tmp_path, ["1", SECRET, KEY])  # inversées
    assert rc == 0 and f"BINANCE_API_KEY={KEY}" in env and "inversées" in text
    rc, env, text = _set_keys(tmp_path, ["1", KEY, SECRET], env="real")
    assert rc == 0 and "BINANCE_TESTNET=false" in env and "compte réel" in text


def test_set_keys_refused_writes_nothing_and_shows_ip(tmp_path):
    rc, env, text = _set_keys(tmp_path, ["2", "A" * 64, "B" * 64])
    assert rc == 1 and env == "RUN_MODE=paper\n"
    assert "41.202.1.2" in text and "Rien n'a été modifié" in text
    assert "l'adresse IP ci-dessus" in text
    rc, env, text = _set_keys(tmp_path, ["1", KEY, "C" * 64])
    assert rc == 1 and "Secret Key incorrecte" in text


def test_set_keys_network_down_and_bad_format(tmp_path):
    rc, env, text = _set_keys(tmp_path, ["1", KEY, SECRET], down=True)
    assert rc == 1 and "injoignable" in text and env == "RUN_MODE=paper\n"
    rc, env, text = _set_keys(tmp_path, ["1", "trop-court", SECRET])
    assert rc == 1 and "64" in text


def test_set_keys_reports_wrong_secret_on_the_other_account(tmp_path):
    """Clé du compte réel, testnet choisi, secret faux : le diagnostic
    précis (secret incorrect, compte réel) prime sur « clé inconnue »."""
    rc, env, text = _set_keys(tmp_path, ["1", KEY, "C" * 64], env="real")
    assert rc == 1 and env == "RUN_MODE=paper\n"
    assert "Secret Key incorrecte" in text and "compte réel" in text


def test_set_keys_outage_midway_is_not_a_refusal(tmp_path):
    """Compte réel injoignable après les essais sur le testnet : pas de
    conclusion « clés refusées » (elles sont peut-être valides)."""
    rc, env, text = _set_keys(tmp_path, ["1", KEY, SECRET], env="real", down="real")
    assert rc == 1 and env == "RUN_MODE=paper\n"
    assert "Vérification impossible" in text and "refuse" not in text


def test_key_commands_write_the_env_file_the_bot_reads(monkeypatch):
    """Le bot lit le .env du dossier du projet : set-keys y écrit, quel que
    soit le dossier courant, même si une autre variable de .env est
    invalide."""
    import inspect
    import os
    assert tg.ENV_FILE == os.path.join(os.path.dirname(os.path.abspath(tg.__file__)),
                                       ".env")
    for fn in (tg.cmd_set_keys, tg.cmd_set_secret):
        assert inspect.signature(fn).parameters["env_path"].default == tg.ENV_FILE
    monkeypatch.setenv("RUN_MODE", "live")          # live sans confirmation : invalide
    monkeypatch.delenv("LIVE_TRADING_CONFIRMATION", raising=False)
    monkeypatch.setattr(tg, "cmd_set_keys", lambda: 0)
    assert tg.main(["set-keys"]) == 0


def test_paper_mode_ignores_testnet_flag(monkeypatch, tmp_path):
    """BINANCE_TESTNET ne concerne que le mode réel : en paper, les prix
    viennent du vrai marché (le testnet est un marché artificiel)."""
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    for mode, extra, expect_testnet in (
            ("paper", {}, False),
            ("live", {"enable_live_trading": True,
                      "live_confirmation": "I_UNDERSTAND_RISK"}, True)):
        g = tg.GuardConfig(run_mode=mode, binance_testnet=True, db_file=":memory:",
                           log_file=str(tmp_path / f"{mode}.log"),
                           lock_file=str(tmp_path / f"{mode}.lock"), **extra)
        bot = tg._build(g)
        try:
            api = str(bot.exchange.urls["api"]).lower()
            assert ("testnet" in api) is expect_testnet, mode
        finally:
            bot.store.close()
            for h in list(bot.logger.handlers):
                h.close()
                bot.logger.removeHandler(h)
