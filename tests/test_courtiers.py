"""Courtiers et plateformes (trendguard/courtiers.py) : annuaire, cours
comparés à Binance, comptes de démonstration (MetaTrader 5, Alpaca, testnets)
et plan miroir à blanc. Démonstration seulement : un compte réel est refusé,
aucun ordre n'est envoyé. Faux terminal, faux Alpaca, fausses plateformes :
aucun réseau."""

import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import ccxt
import pytest
from test_panel import _cfg, _json, demo_server  # noqa: F401  (fixture du serveur de démonstration)

from panel.data import BotData
from trendguard import courtiers, cyber
from trendguard import market_watch as mw

NOW = datetime(2026, 11, 25, 2, 0, tzinfo=timezone.utc)


class _Memory:
    def __init__(self):
        self.meta = {}

    def get(self, key, default=None):
        return self.meta.get(key, default)

    def put(self, key, value):
        self.meta[key] = json.loads(json.dumps(value))           # comme la vraie base : du JSON


# ---------- Annuaire ----------

def test_the_directory_covers_every_kind_and_affirms_nothing_without_a_source():
    rows = courtiers.directory()
    kinds = {k: [r for r in rows if r["kind"] == k] for k in courtiers.KINDS}
    assert len(kinds[courtiers.MT5_KIND]) == 15 and len(kinds[courtiers.STOCKS_KIND]) == 1
    assert len(kinds[courtiers.BRVM_KIND]) == 1 and len(kinds[courtiers.CRYPTO_KIND]) == len(ccxt.exchanges)
    for r in kinds[courtiers.MT5_KIND] + kinds[courtiers.STOCKS_KIND] + kinds[courtiers.BRVM_KIND]:
        assert urllib.parse.urlsplit(r["site"]).hostname in cyber.ALLOWLIST and r["site"].startswith("https://")
    assert all(r["demo"] == "oui, gratuit" for r in kinds[courtiers.MT5_KIND])
    assert all(r["availability"] == courtiers.VERIFY for r in kinds[courtiers.CRYPTO_KIND])   # rien d'affirmé
    by = {r["id"]: r for r in kinds[courtiers.CRYPTO_KIND]}
    assert set(courtiers.TESTNETS) | courtiers.COMPARE_IDS <= set(by)
    assert all(by[p]["testnet"] and by[p]["branchable"] for p in courtiers.TESTNETS)
    assert by["kraken"]["testnet"] is False and by["kraken"]["compared"] and not by["kraken"]["reached"]
    assert "plateforme du bot" in by["binance"]["connection"]
    reached = courtiers.directory({"platforms": [{"id": "kraken", "ok": True}, {"id": "okx", "ok": False}]})
    assert {r["id"] for r in reached if r.get("reached")} == {"kraken"}


# ---------- Adresses : la liste blanche, vérifiée à chaque requête ----------

class _Stop(Exception):
    """Arrête une requête juste avant le réseau, une fois son adresse notée."""


def _recorded(ex):
    seen = []

    def fetch(url, method="GET", headers=None, body=None):
        seen.append(urllib.parse.urlsplit(url).hostname)
        raise _Stop()
    ex.fetch = fetch
    ex.seen = seen
    return ex


def test_a_request_outside_the_allowed_addresses_never_leaves():
    ex = courtiers.guard(_recorded(courtiers.exchange("kraken")), set())
    with pytest.raises(PermissionError, match="api.kraken.com"):
        ex.load_markets()
    assert ex.seen == []                                              # rien n'est parti


def test_each_compared_platform_starts_on_an_allowed_address():
    for pid in ["binance", *sorted(courtiers.COMPARE_IDS)]:
        ex = courtiers.guard(_recorded(courtiers.exchange(pid)), cyber.ALLOWLIST)
        with pytest.raises(_Stop):
            ex.load_markets()
        assert ex.seen and ex.seen[0] in cyber.ALLOWLIST, (pid, ex.seen)


def test_a_testnet_only_reaches_its_testnet():
    for pid, (_name, hosts) in courtiers.TESTNETS.items():
        ex = courtiers.testnet_exchange(pid, "cle", "secret", "phrase",
                                        lambda p, cfg=None: _recorded(courtiers.exchange(p, cfg)))
        with pytest.raises(_Stop):
            ex.fetch_balance()
        assert ex.seen[0] in hosts and ex.seen[0] in cyber.ALLOWLIST, (pid, ex.seen)
    real = courtiers.guard(_recorded(courtiers.exchange("bybit", {"apiKey": "k", "secret": "s"})),
                           courtiers.TESTNETS["bybit"][1])
    with pytest.raises(PermissionError, match="api.bybit.com"):     # sans le mode test : refusé avant de partir
        real.fetch_balance()
    okx = courtiers.testnet_exchange("okx", "k", "s", "p", lambda p, cfg=None: _recorded(courtiers.exchange(p, cfg)))
    okx.headers.pop("x-simulated-trading")                           # même adresse que le réel : l'en-tête compte
    with pytest.raises(PermissionError, match="démonstration"):
        okx.fetch_balance()
    with pytest.raises(PermissionError):
        courtiers.testnet_exchange("kraken", "k", "s")              # pas de testnet branchable


# ---------- Cours comparés ----------

class _Platform:
    """Une plateforme d'exemple : cours fixés, volume, panne ou liste refusée."""

    def __init__(self, pid, prices, volume=1e6, fail=None, refuse_list=False):
        self.id, self.prices, self.volume, self.fail, self.refuse_list = pid, prices, volume, fail, refuse_list
        self.markets = {f"{a.upper()}/USDT": {"spot": True, "active": True} for a in prices}
        self.has = {"fetchTickers": True}
        self.calls = []

    def fetch(self, *_a):
        return None

    def load_markets(self):
        if self.fail:
            raise self.fail

    def fetch_tickers(self, symbols=None):
        self.calls.append(symbols)
        if symbols and self.refuse_list:
            raise ccxt.BadRequest("one symbol at most")
        out = {}
        for s in symbols or list(self.markets):
            p = self.prices[s.split("/")[0].lower()]
            out[s] = {"bid": p * 0.9999, "ask": p * 1.0001, "last": p, "quoteVolume": self.volume}
        return out


def _platforms():
    return {"binance": _Platform("binance", {"btc": 100.0, "eth": 10.0, "one": 1.0, "zec": 50.0}, volume=3e6),
            "okx": _Platform("okx", {"btc": 100.2, "eth": 10.0, "one": 5.0, "zec": 50.5}),
            "bybit": _Platform("bybit", {"btc": 99.9, "eth": 10.01, "zec": 51.0}, refuse_list=True),
            "kucoin": _Platform("kucoin", {"btc": 100.0}, fail=ccxt.NetworkError("down")),
            "gate": _Platform("gate", {"btc": 130.0 / 1.3, "eth": 10.3}, volume=5_000)}


def test_prices_are_compared_to_binance_with_the_median_of_the_others():
    plats = _platforms()
    names = (("okx", "OKX"), ("bybit", "Bybit"), ("kucoin", "KuCoin"), ("gate", "Gate"))
    r = courtiers.compare(["BTC", "eth", "one", "zec"], lambda pid, cfg=None: plats[pid], NOW, cache="", platforms=names)
    rows = {x["asset"]: x for x in r["rows"]}
    btc = rows["btc"]
    assert btc["count"] == 2 and btc["median"] == pytest.approx(100.05)              # Gate trop mince : écarté
    assert btc["gap_pct"] == pytest.approx((100 / 100.05 - 1) * 100)
    assert btc["quotes"]["gate"]["thin"] and btc["widest"]["id"] == "okx"
    assert btc["binance_share"] == pytest.approx(3e6 / (3e6 + 2e6 + 5_000) * 100)
    assert rows["one"]["other_token"] == ["okx"] and rows["one"]["gap_pct"] is None    # autre jeton, même symbole
    assert rows["zec"]["gap_pct"] == pytest.approx((50 / 50.75 - 1) * 100) and r["notable"] == ["zec"]
    assert plats["bybit"].calls[0] and plats["bybit"].calls[1] is None               # liste refusée : tout, puis tri
    status = {p["id"]: p for p in r["platforms"]}
    assert not status["kucoin"]["ok"] and status["kucoin"]["error"] == "plateforme injoignable"
    assert status["okx"]["listed"] == 4 and r["error"] == "" and r["fetched_at"] == NOW.isoformat()
    assert r["median_gap_pct"] == pytest.approx(statistics_median([abs(x["gap_pct"]) for x in r["rows"]
                                                                   if x["gap_pct"] is not None]))


def statistics_median(values):
    import statistics
    return statistics.median(values)


def test_prices_every_six_hours_accounts_every_hour_and_nothing_lost_on_failure():
    memory, plats = _Memory(), _platforms()
    factory = lambda pid, cfg=None: plats.get(pid) or _Platform(pid, {})   # noqa: E731
    book = courtiers.refresh(memory, NOW, ["btc", "zec"], env={}, factory=factory, cache="")
    assert book["prices"]["error"] == "" and len(book["prices"]["rows"]) == 2
    assert book["accounts"]["mt5"]["state"] == "absent" and book["accounts"]["testnets"] == []
    later = NOW + timedelta(hours=2)
    assert not courtiers.due(book["prices"], later, courtiers.PRICES_EVERY_HOURS)
    assert courtiers.due(book["accounts"], later, courtiers.ACCOUNTS_EVERY_HOURS)
    plats["binance"].fail = ccxt.RequestTimeout("timed out")
    failed = courtiers.refresh(memory, NOW + timedelta(hours=6), ["btc", "zec"], env={}, factory=factory, cache="")
    assert failed["prices"]["error"].startswith("Binance illisible") and failed["prices"]["rows"] == book["prices"]["rows"]
    assert failed["prices"]["fetched_at"] == book["prices"]["fetched_at"]          # les derniers cours restent
    assert courtiers.due(failed["prices"], NOW + timedelta(hours=7), courtiers.PRICES_EVERY_HOURS)   # nouvel essai


def test_markets_are_kept_a_week_on_disk_without_raw_answers(tmp_path):
    ex = courtiers.exchange("kraken")
    ex.load_markets = lambda: ex.set_markets([{"id": "XBTUSDT", "symbol": "BTC/USDT", "base": "BTC", "quote": "USDT",
                                               "spot": True, "active": True, "info": {"brut": "x" * 1000}}])
    courtiers.load_markets(ex, NOW, str(tmp_path))
    saved = json.loads((tmp_path / "marches_kraken.json").read_text(encoding="utf-8"))
    assert "info" not in saved["markets"]["BTC/USDT"] and saved["ccxt"] == ccxt.__version__
    again = courtiers.exchange("kraken")
    again.load_markets = lambda: pytest.fail("relu sur Internet alors que la liste a moins d'une semaine")
    courtiers.load_markets(again, NOW + timedelta(days=6), str(tmp_path))
    assert "BTC/USDT" in again.markets
    stale = courtiers.exchange("kraken")
    stale.load_markets = lambda: stale.set_markets([])
    courtiers.load_markets(stale, NOW + timedelta(days=8), str(tmp_path))
    assert stale.markets == {}                                          # plus d'une semaine : relue


# ---------- MetaTrader 5 : compte démo seulement ----------

class _MT5:
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
    ORDER_TYPE_BUY = POSITION_TYPE_BUY = 0
    SYMBOL_TRADE_MODE_FULL = 4

    def __init__(self, mode=0, ok=True):
        self.mode, self.ok, self.calls, self.closed = mode, ok, [], False

    def initialize(self, **kw):
        self.calls.append(("initialize", kw))
        return self.ok

    def last_error(self):
        return (-6, "Authorization failed")

    def account_info(self):
        self.calls.append(("account_info",))
        return SimpleNamespace(trade_mode=self.mode, server="MetaQuotes-Demo", company="MetaQuotes Ltd.",
                               currency="USD", balance=10_000.0, equity=10_100.0, leverage=100, trade_allowed=True,
                               name="Nom du titulaire")

    def positions_get(self):
        self.calls.append(("positions_get",))
        return (SimpleNamespace(symbol="XAUUSDm", type=0, volume=0.05, price_open=2600.0, profit=12.5),)

    def symbols_get(self):
        self.calls.append(("symbols_get",))
        return tuple(SimpleNamespace(name=n) for n in ("XAUUSDm", "GOLDEURm", "EURUSDm", "US500m", "US300m", "BTCUSDm"))

    def symbol_select(self, _s, _on):
        return True

    def symbol_info(self, _s):
        return SimpleNamespace(volume_min=0.01, volume_step=0.01, volume_max=100.0, trade_mode=4)

    def symbol_info_tick(self, s):
        return SimpleNamespace(ask=2650.0 if s.startswith("XAU") else 1.05 if s.startswith("EUR") else 6000.0)

    def order_calc_profit(self, _kind, s, vol, a, b):
        return (b - a) * vol * (100 if s.startswith("XAU") else 100_000 if s.startswith("EUR") else 1)

    def shutdown(self):
        self.closed = True


ENV_MT5 = {"MT5_LOGIN": "1234567", "MT5_PASSWORD": "mot-de-passe-demo", "MT5_SERVER": "MetaQuotes-Demo"}


def test_a_demo_account_is_read_and_its_symbols_found():
    mt5 = _MT5()
    s = courtiers.mt5_status(ENV_MT5, mt5, running=lambda: True)
    assert s["state"] == "demo" and mt5.closed
    assert s["account"]["login"] == "•••567" and "name" not in s["account"]             # ni nom ni numéro complet
    assert mt5.calls[0] == ("initialize", {"login": 1234567, "password": "mot-de-passe-demo",
                                           "server": "MetaQuotes-Demo", "timeout": 60_000})
    assert s["symbols"]["or"] == "XAUUSDm" and s["symbols"]["eurusd"] == "EURUSDm" and s["symbols"]["sp500"] == "US500m"
    assert s["symbols"]["dow"] is None and s["symbols"]["cuivre"] is None             # « US300m » n'est pas le Dow
    assert s["specs"]["or"]["lot_value"] == pytest.approx(265_000.0) and s["positions"][0]["side"] == "achat"
    assert "mot-de-passe-demo" not in json.dumps(s)


def test_a_real_or_contest_account_is_refused_before_anything_is_read():
    for mode, word in ((2, "réel"), (1, "concours")):
        mt5 = _MT5(mode=mode)
        s = courtiers.mt5_status(ENV_MT5, mt5, running=lambda: True)
        assert s["state"] == "refuse" and word in s["message"] and mt5.closed
        assert [c[0] for c in mt5.calls] == ["initialize", "account_info"]               # ni positions ni symboles
        assert "account" not in s


def test_the_hourly_reading_never_opens_the_terminal_and_errors_are_plain():
    mt5 = _MT5()
    assert courtiers.mt5_status(ENV_MT5, mt5, running=lambda: False)["state"] == "ferme" and not mt5.calls
    assert courtiers.mt5_status(ENV_MT5, mt5, running=lambda: False, launch=True)["state"] == "demo"
    bad = courtiers.mt5_status(ENV_MT5, _MT5(ok=False), running=lambda: True)
    assert bad["state"] == "erreur" and "mot de passe" in bad["message"]
    assert "introuvable" in courtiers._mt5_error((-10003, "IPC initialize failed, MetaTrader 5 x64 not found"))
    assert courtiers.mt5_status({}, mt5)["state"] == "absent"
    assert courtiers.mt5_status(dict(ENV_MT5, MT5_LOGIN="abc"), mt5)["state"] == "erreur"


def test_a_closed_terminal_keeps_the_last_reading():
    memory = _Memory()
    plats = _platforms()
    factory = lambda pid, cfg=None: plats.get(pid) or _Platform(pid, {})   # noqa: E731
    courtiers.refresh(memory, NOW, ["btc"], env=ENV_MT5, factory=factory, mt5_module=_MT5(), running=lambda: True,
                      cache="", parts=("accounts",))
    book = courtiers.refresh(memory, NOW + timedelta(hours=1), ["btc"], env=ENV_MT5, factory=factory,
                             mt5_module=_MT5(), running=lambda: False, cache="", parts=("accounts",))
    mt5 = book["accounts"]["mt5"]
    assert mt5["state"] == "ferme" and mt5["account"]["equity"] == 10_100.0 and mt5["seen_at"].startswith("20")
    assert "prices" not in book                                                     # seulement ce qui était demandé


def test_find_symbol_prefers_the_exact_name_then_a_short_suffix():
    assert courtiers.find_symbol(("XAUUSD", "GOLD"), ["GOLD", "GOLDEUR"]) == "GOLD"
    assert courtiers.find_symbol(("US500",), ["US500.cash", "US500m"]) == "US500m"
    assert courtiers.find_symbol(("US30",), ["US300"]) is None                         # un chiffre de plus : autre chose
    assert courtiers.find_symbol(("WTI",), ["WTIX"]) is None                           # nom trop court pour un suffixe
    assert courtiers.find_symbol(("EURUSD",), ["#EURUSD"]) == "#EURUSD"


# ---------- Alpaca : compte papier seulement ----------

def _alpaca(fail=None):
    seen = []

    def get(url, timeout, headers):
        seen.append(url)
        assert url.startswith((courtiers.ALPACA_PAPER, courtiers.ALPACA_DATA))
        assert headers == {"APCA-API-KEY-ID": "PKCLE", "APCA-API-SECRET-KEY": "secret-papier"}
        if fail:
            raise fail
        path = urllib.parse.urlsplit(url).path
        body = {"/v2/account": {"account_number": "PA3ABCDEF", "status": "ACTIVE", "currency": "USD", "cash": "100000",
                                "equity": "100250.5", "buying_power": "200000", "trading_blocked": False},
                "/v2/positions": [{"symbol": "SPY", "qty": "2", "avg_entry_price": "600", "market_value": "1210",
                                   "unrealized_plpc": "0.0083"}],
                "/v2/clock": {"is_open": True},
                "/v2/stocks/snapshots": {"SPY": {"latestTrade": {"p": 605.0}}, "GLD": {"dailyBar": {"c": 245.0}}}}[path]
        return json.dumps(body).encode()
    get.seen = seen
    return get


ENV_ALPACA = {"ALPACA_API_KEY": "PKCLE", "ALPACA_API_SECRET": "secret-papier"}


def test_the_alpaca_paper_account_is_read_and_the_real_one_is_out_of_reach():
    get = _alpaca()
    s = courtiers.alpaca_status(ENV_ALPACA, get)
    assert s["state"] == "demo" and s["market_open"] and s["account"]["number"] == "•••DEF"
    assert s["account"]["equity"] == pytest.approx(100_250.5) and s["positions"][0]["pnl_pct"] == pytest.approx(0.83)
    assert s["prices"] == {"SPY": 605.0, "GLD": 245.0} and "feed=iex" in get.seen[-1]
    with pytest.raises(PermissionError):
        courtiers.alpaca_get("https://api.alpaca.markets/v2/account", "k", "s", get)   # le compte réel : jamais
    refused = courtiers.alpaca_status(ENV_ALPACA, _alpaca(mw.HttpError(401, "unauthorized")))
    assert refused["state"] == "erreur" and "compte papier" in refused["message"]
    assert courtiers.alpaca_status({}, get)["state"] == "absent"


# ---------- Testnets des plateformes crypto ----------

class _Testnet:
    def __init__(self, pid, cfg=None, test=True, error=None):
        self.id, self.cfg, self.error, self.sandbox, self.headers = pid, cfg, error, False, {}
        self.urls = {"test": {"public": "https://testnet"}} if test else {}

    def fetch(self, *_a):
        return None

    def set_sandbox_mode(self, on):
        self.sandbox = on

    def fetch_balance(self):
        assert self.sandbox, "lecture sans le mode test"
        if self.error:
            raise self.error
        return {"total": {"USDT": 1000.0, "BTC": 0.5, "XRP": 0.0}}


def test_a_testnet_is_read_in_test_mode_only():
    env = {"CCXT_BYBIT_KEY": "cle-test", "CCXT_BYBIT_SECRET": "secret-test"}
    s = courtiers.testnet_status(env, "bybit", lambda pid, cfg=None: _Testnet(pid, cfg))
    assert s["state"] == "demo" and s["balances"] == {"USDT": 1000.0, "BTC": 0.5} and s["name"] == "Bybit"
    assert courtiers.testnet_status({}, "bybit") is None
    auth = courtiers.testnet_status(env, "bybit", lambda pid, cfg=None: _Testnet(pid, cfg, error=ccxt.AuthenticationError("x")))
    assert auth["state"] == "erreur" and "testnet" in auth["message"]
    none = courtiers.testnet_status(env, "bybit", lambda pid, cfg=None: _Testnet(pid, cfg, test=False))
    assert none["state"] == "refuse"
    assert courtiers.needs_password("okx") and not courtiers.needs_password("bybit")


# ---------- Plan miroir (à blanc) ----------

def _multi():
    return {"fetched_at": "2026-11-24T23:00:00+00:00", "portfolio": {"start": "2026-11-24", "equity": 10_000.0, "positions": [
        {"code": "or", "name": "Or", "category": "Matières premières", "stop": 2500.0, "price": 2650.0, "weight_pct": 12.0},
        {"code": "sp500", "name": "S&P 500", "category": "Indices", "stop": 5500.0, "price": 6000.0, "weight_pct": 18.0},
        {"code": "tlt", "name": "TLT", "category": "Obligations", "stop": 80.0, "price": 90.0, "weight_pct": 9.0},
        {"code": "dax", "name": "DAX", "category": "Indices", "stop": 20_000.0, "price": 21_000.0}]}}


def test_the_mirror_plan_sizes_each_position_and_sends_nothing():
    mt5 = courtiers.mt5_status(ENV_MT5, _MT5(), running=lambda: True)
    alp = courtiers.alpaca_status(ENV_ALPACA, _alpaca())
    plan = courtiers.mirror(_multi(), {"mt5": mt5, "alpaca": alp})
    rows = {r["code"]: r for r in plan["rows"]}
    assert plan["sent"] is False and plan["since"] == "2026-11-24"
    assert rows["or"]["mt5"]["lots"] == 0.0 and "trop petit" in rows["or"]["mt5"]["note"]   # 1 212 $ < 0,01 lot
    assert rows["sp500"]["mt5"] == {"symbol": "US500m", "lots": 0.3, "note": ""}            # 1 818 $ / 6 000 $
    assert rows["or"]["alpaca"]["qty"] == pytest.approx(100_250.5 * 0.12 / 245.0, abs=1e-4)
    assert rows["sp500"]["alpaca"]["qty"] == pytest.approx(100_250.5 * 0.18 / 605.0, abs=1e-4)
    assert rows["sp500"]["alpaca"]["symbol"] == "SPY" and rows["or"]["alpaca"]["note"] == "fonds coté qui suit ce marché"
    assert rows["tlt"]["mt5"]["note"] == "pas de symbole MetaTrader 5 prévu"
    assert rows["tlt"]["alpaca"] == {"symbol": "TLT", "qty": None, "note": "cours indisponible"}
    assert rows["dax"]["mt5"]["note"] == "introuvable chez ce courtier" and rows["dax"]["alpaca"]["note"] == "pas de fonds coté prévu"
    off = courtiers.mirror(_multi(), {})
    assert "non branché" in off["rows"][0]["mt5"]["note"] and off["rows"][0]["alpaca"]["note"] == "compte papier non branché"
    spec = {"lot_value": 6000.0, "min": 0.1, "step": 0.1, "max": 0.2}
    assert courtiers.lots(1838.25, dict(spec, max=None)) == (0.3, "") and courtiers.lots(5000.0, spec) == (0.2, "")
    assert courtiers.lots(100.0, None) == (None, "valeur d'un lot inconnue")


# ---------- Saisie d'un compte (set-key) ----------

def _ask(answers):
    it = iter(answers)
    return lambda _prompt: next(it)


def test_set_key_writes_only_an_account_checked_as_demo(tmp_path, monkeypatch):
    from trendguard import config as tgc
    env, out, memory = tmp_path / ".env", [], _Memory()
    printer = SimpleNamespace(write=out.append, flush=lambda: None)
    ok = courtiers.cmd_set_key("mt5", str(env), ask=_ask(["mot-de-passe-demo"]), read=_ask(["1234567", "MetaQuotes-Demo"]),
                               out=printer, check=lambda t, e: courtiers.mt5_status(e, _MT5(), running=lambda: True),
                               memory=memory)
    text = env.read_text(encoding="utf-8")
    assert ok == 0 and "MT5_LOGIN=1234567" in text and "MT5_PASSWORD=mot-de-passe-demo" in text
    assert memory.get(courtiers.KEY)["accounts"]["mt5"]["state"] == "demo"
    assert "mot-de-passe-demo" not in "".join(out)                                    # jamais affiché
    env.unlink()
    refused = courtiers.cmd_set_key("mt5", str(env), ask=_ask(["x"]), read=_ask(["7654321", "Broker-Live"]), out=printer,
                                    check=lambda t, e: courtiers.mt5_status(e, _MT5(mode=2), running=lambda: True),
                                    memory=memory)
    assert refused == 1 and not env.exists() and "Rien n'a été enregistré" in "".join(out)
    monkeypatch.setattr(tgc, "is_exposed", lambda v: v == "cle-montree")
    assert courtiers.cmd_set_key("alpaca", str(env), ask=_ask(["cle-montree", "s"]), out=printer,
                                 check=lambda t, e: pytest.fail("vérifiée alors qu'elle est exposée")) == 1
    assert courtiers.cmd_set_key("binance", str(env), out=printer) == 2 and not env.exists()
    asked = []
    courtiers.cmd_set_key("okx", str(env), ask=lambda p: asked.append(p) or "valeur", out=printer,
                          check=lambda t, e: {"state": "erreur", "message": "x"})
    assert len(asked) == 3 and "passphrase" in asked[-1]                              # OKX : phrase secrète en plus


# ---------- Panneau ----------

def test_the_panel_page_shows_how_to_connect_without_any_value_of_env(tmp_path):
    data = BotData(_cfg(tmp_path, savoir=False), market=None)
    page = data.brokers()
    assert page["accounts"]["mt5"]["state"] in ("absent", "attente") and page["prices"] is None
    assert len(page["directory"]) == 15 + 1 + len(ccxt.exchanges) + 1 and page["mirror"]["rows"] == []
    v = courtiers.view({}, None, dict(ENV_MT5, ALPACA_API_KEY="PKCLE"))
    assert v["accounts"]["mt5"]["state"] == "attente" and v["accounts"]["alpaca"]["state"] == "absent"
    assert "mot-de-passe-demo" not in json.dumps(v) and "PKCLE" not in json.dumps(v)
    assert "set-key alpaca" in v["accounts"]["alpaca"]["message"]


def test_demo_server_serves_the_brokers_page(demo_server):  # noqa: F811
    base, _app = demo_server
    code, page = _json(base + "/api/brokers")
    assert code == 200 and page["accounts"]["mt5"]["state"] == "demo" and page["accounts"]["alpaca"]["state"] == "absent"
    assert page["prices"]["notable"] == ["zec"] and len(page["prices"]["rows"]) == 21
    assert [r["code"] for r in page["mirror"]["rows"]] == ["or", "sp500", "eurusd", "tlt"]
    assert page["mirror"]["rows"][1]["mt5"]["lots"] == pytest.approx(0.3)
