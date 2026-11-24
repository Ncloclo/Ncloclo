"""La BRVM (trendguard/brvm.py) : pages publiques lues une fois par séance,
historique construit par le bot, page du panneau, pays de l'UEMOA.
Information seulement : aucun ordre réel."""

from datetime import datetime, timedelta, timezone

from test_panel import _cfg, _json, demo_server  # noqa: F401  (fixture du serveur de démonstration)

from panel.data import BotData
from trendguard import brvm, pays

INDICES = """<html><body><table>
<tr><th>Top 5</th><th>Cours</th><th>Variation</th></tr><tr><td>TTLC</td><td>2 985</td><td>6,42%</td></tr>
<tr><td>Valeur des transactions</td><td>2 238 217 762 FCFA</td><td></td></tr>
<tr><td>Capitalisation Actions</td><td>18 511 176 505 734 FCFA</td><td></td></tr>
<tr><td>Capitalisation des obligations</td><td>13 419 743 433 771 FCFA</td><td></td></tr>
<tr><th>Nom</th><th>Fermeture précédente</th><th>Fermeture</th><th>Variation (%)</th><th>Variation 31 décembre (%)</th></tr>
<tr><td>BRVM-30</td><td>230,86</td><td>228,62</td><td>-0,97</td><td>1,97</td></tr>
<tr><td>BRVM - COMPOSITE</td><td>476,54</td><td>{close}</td><td>-0,94</td><td>1,70</td></tr>
<tr><td>BRVM – COMPOSITE TOTAL RETURN</td><td>192,04</td><td>190,23</td><td>-0,94</td><td>1,70</td></tr>
</table></body></html>"""
STOCKS = """<table><tr><th>Symbole</th><th>Nom</th><th>Volume</th><th>Cours veille (FCFA)</th>
<th>Cours Ouverture (FCFA)</th><th>Cours Clôture (FCFA)</th><th>Variation (%)</th></tr>
<tr><td>SNTS</td><td>SONATEL</td><td>3 210</td><td>3 860</td><td>3 860</td><td>{close}</td><td>-0,26</td></tr>
<tr><td>UNXC</td><td>UNIWAX COTE D'IVOIRE</td><td>5 906</td><td>1 720</td><td>1 700</td><td>1 750</td><td>2,94</td></tr>
</table>"""


class _Memory:
    def __init__(self):
        self.meta = {}

    def get(self, key, default=None):
        return self.meta.get(key, default)

    def put(self, key, value):
        self.meta[key] = value


def _site(close_index="472,04", close_stock="3 850", calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        page = INDICES.format(close=close_index) if url == brvm.INDICES_URL else STOCKS.format(close=close_stock)
        return page.encode()
    return fetch


def test_the_public_pages_are_parsed():
    indices, activity = brvm.parse_indices(INDICES.format(close="472,04"))
    assert [r["name"] for r in indices] == ["BRVM-30", "BRVM - COMPOSITE", "BRVM - COMPOSITE TOTAL RETURN"]
    assert indices[1] == {"name": "BRVM - COMPOSITE", "prev": 476.54, "close": 472.04, "change": -0.94, "ytd": 1.7}
    assert activity == {"turnover": 2238217762.0, "stocks_cap": 18511176505734.0, "bonds_cap": 13419743433771.0}
    stocks = brvm.parse_stocks(STOCKS.format(close="3 850"))
    assert stocks[0] == {"symbol": "SNTS", "name": "Sonatel", "volume": 3210.0, "prev": 3860.0, "open": 3860.0,
                         "close": 3850.0, "change": -0.26}
    assert brvm.number("6,42%") == 6.42 and brvm.number("") is None


def test_one_reading_per_session_and_the_history_the_bot_builds(tmp_path):
    cache, memory, calls = str(tmp_path / "brvm"), _Memory(), []
    tuesday = datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc)
    book = brvm.refresh(memory, tuesday, _site(calls=calls), cache, pause=0)
    assert book["error"] == "" and book["session"] == "2026-11-24" and book["sessions"] == 1
    assert calls == [brvm.INDICES_URL, brvm.STOCKS_URL]                            # deux pages, pas plus
    assert brvm.history(cache, "snts").tolist() == [3850.0]
    assert brvm.history(cache, "idx_brvm_composite").tolist() == [472.04]
    assert not brvm.due(book, tuesday + timedelta(hours=3))                        # une lecture par séance
    wednesday = tuesday + timedelta(days=1)
    assert not brvm.due(book, wednesday.replace(hour=10))                          # séance pas encore close
    book = brvm.refresh(memory, wednesday, _site("475,10", "3 900"), cache, pause=0)
    assert book["sessions"] == 2 and brvm.history(cache, "snts").tolist() == [3850.0, 3900.0]
    thursday = wednesday + timedelta(days=1)                                       # jour férié : page inchangée
    book = brvm.refresh(memory, thursday, _site("475,10", "3 900"), cache, pause=0)
    assert book["sessions"] == 2 and book["session"] == "2026-11-25" and len(brvm.history(cache, "snts")) == 2
    saturday = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
    assert brvm.session_day(saturday) == "2026-11-27"

    def down(url):
        raise TimeoutError("timed out")
    failed = brvm.refresh(memory, saturday + timedelta(days=2, hours=5), down, cache, pause=0)
    assert failed["error"] and failed["stocks"]                                    # rien de perdu


def test_the_panel_page_and_the_waemu_countries(tmp_path):
    cache, memory = str(tmp_path / "brvm"), _Memory()
    book = brvm.refresh(memory, datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc), _site(), cache, pause=0)
    view = brvm.view(book, cache)
    assert view["sessions"] == 1 and len(view["stocks"]) == 2 and view["activity"]["stocks_cap"] > 1e13
    main = next(r for r in view["indices"] if r["name"] == "BRVM - COMPOSITE")
    assert main["main"] and main["spark"] == [472.04]
    comp = brvm.composite(book)
    assert comp["close"] == 472.04 and comp["session"] == "2026-11-24"
    import pandas as pd
    rows = pays.countries_view(pd.DataFrame(), {}, "1y", regional=comp)["rows"]
    ci = next(r for r in rows if r["iso"] == "CI")
    assert ci["index"]["name"].startswith("BRVM Composite") and ci["index"]["code"] == "" and ci["index"]["trend"] is None
    assert next(r for r in rows if r["iso"] == "GH" or r["iso"] == "NG")["index"] is None       # hors UEMOA
    data = BotData(_cfg(tmp_path, savoir=False), market=None)
    data.brvm_cache = cache
    assert data.brvm()["stocks"] == []                                             # sans noyau de savoir : rien lu


def test_demo_server_serves_the_brvm_page(demo_server):  # noqa: F811
    base, _app = demo_server
    code, page = _json(base + "/api/brvm")
    assert code == 200 and page["stocks"] and any(r["name"] == "BRVM - COMPOSITE" for r in page["indices"])
    countries = _json(base + "/api/countries")[1]["rows"]
    assert next(r for r in countries if r["iso"] == "SN")["index"]["name"].startswith("BRVM Composite")
