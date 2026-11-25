"""Bourses du monde (trendguard/bourses.py) : la structure de cotation de
chaque place (heures, indices, régulateur, accès depuis la Côte d'Ivoire), les
pages officielles lues comme celles de la BRVM (Nigeria, Kenya, Maroc,
Afrique centrale), l'étude de la règle sur chaque place, la page du panneau
et l'assistante. Pages d'exemple tirées des vraies : aucun réseau."""

import json
import math
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from test_panel import _cfg, _json, demo_server  # noqa: F401  (fixture du serveur de démonstration)

from panel import assistant
from panel.data import BotData
from trendguard import bourses, cyber
from trendguard import market_watch as mw
from trendguard import multi_actifs as ma

WED = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)          # mercredi, 10:00 à New York


class _Memory:
    def __init__(self):
        self.meta = {}

    def get(self, key, default=None):
        return self.meta.get(key, default)

    def put(self, key, value):
        self.meta[key] = json.loads(json.dumps(value))


# ---------- La structure de chaque place ----------

def test_every_exchange_is_described_coherently():
    ids = [x["id"] for x in bourses.EXCHANGES]
    assert len(ids) == len(set(ids)) == 28 and {x["region"] for x in bourses.EXCHANGES} == set(bourses.REGIONS)
    known = set(ma.ALL_CODES) | set(ma.INDICATOR_CODES)
    for x in bourses.EXCHANGES:
        ZoneInfo(x["tz"])                                                     # fuseau connu
        assert all(a < b for a, b in x["sessions"]) and x["regulator"] and x["zone"] and x["access"]
        assert all(c in known for _n, c in x["indices"] if c), x["id"]       # chaque indice lu est suivi
        assert x["official"] in ("", "brvm", *bourses.OFFICIAL)
        assert urllib.parse.urlsplit(x["site"]).hostname in cyber.ALLOWLIST and x["site"].startswith("https://")
    assert bourses.BY_ID["tadawul"]["days"] == bourses.GULF and bourses.BY_ID["egx"]["days"] == bourses.GULF
    assert "SGI" in bourses.BY_ID["brvm"]["access"] and "W-8BEN" in bourses.BY_ID["nyse"]["access"]
    assert [x["id"] for x in bourses.EXCHANGES if x["official"]] == ["brvm", "bvmac", "ngx", "casablanca", "nse_ke"]


def test_the_session_state_in_local_and_abidjan_time():
    nyse = bourses.status(bourses.BY_ID["nyse"], WED)
    assert nyse["open"] and nyse["local"] == "10:00" and nyse["next"] == "ferme à 16 h (21 h à Abidjan)"
    tokyo = bourses.status(bourses.BY_ID["jpx"], datetime(2026, 11, 25, 3, 0, tzinfo=timezone.utc))   # 12:00 à Tokyo
    assert tokyo["open"] is False and tokyo["next"] == "ouvre aujourd'hui à 12 h 30 (3 h 30 à Abidjan)"
    friday = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
    assert bourses.status(bourses.BY_ID["tadawul"], friday)["next"].startswith("ouvre dimanche à 10 h")
    assert bourses.status(bourses.BY_ID["brvm"], friday)["open"] is True                # Abidjan : 9 h - 15 h
    assert bourses.status(bourses.BY_ID["bvmac"], WED)["open"] is None                 # heures non lues
    assert bourses.hm("09:30") == "9 h 30" and bourses.hm("16:00") == "16 h"


# ---------- Pages officielles, comme la BRVM ----------

NGX_EQ = [{"Symbol": "GTCO", "PrevClosingPrice": 112.0, "ClosePrice": 111.0, "PercChange": -0.89, "Volume": 12838436.0,
           "Sector": "FINANCIAL SERVICES", "TradeDate": "2026-11-24T00:00:00"},
          {"Symbol": "MTNN ", "PrevClosingPrice": 837.0, "ClosePrice": 837.0, "PercChange": None, "Volume": 1702602.0,
           "Sector": "ICT", "TradeDate": "2026-11-24T00:00:00"},
          {"Symbol": "", "ClosePrice": None}]
NGX_SNAP = {"ASI": 245685.87, "DEALS": 31670.0, "VOLUME": 489504087.0, "VALUE": 28214436170.11, "CAP": 1.595e14}
NSE_PAGE = """<table><tr><th>Name</th><th>Value</th><th>Change</th></tr>
<tr><td>NSE ALL SHARE INDEX</td><td>235.94</td><td>-0.64</td></tr>
<tr><td>NSE 20 SHARE INDEX</td><td>4,122.09</td><td>-6.35</td></tr>
<tr><td>MARKET CAPITALIZATION (Billions)</td><td>3,988.95</td><td>-10.90</td></tr>
<tr><td>EQUITY TURNOVER</td><td>559,173,215.70</td><td>-206,489,806.55</td></tr></table>"""
CASA_PAGE = """<div class="data-block"><div class="block-header">
<div class="block-title"><a class="titre-slider" href="/live-market/indices">MASI</a></div>
<div class="variation-value negative"><i class="fas fa-arrow-down"></i> -0,70% </div></div>
<div class="main-value variation-item">16 764,82</div></div>
<table><tr><th>Instrument</th><th>Volume Carnet Central</th><th>Var%</th></tr>
<tr><td>BMCI</td><td>17 267 790</td><td>+4,98%</td></tr></table>
<table><tr><th>Instrument</th><th>Cours</th><th>VAR%</th></tr><tr><td>BMCI</td><td>569,00</td><td>+4,98%</td></tr></table>
<table><tr><th>Instrument</th><th>Cours</th><th>VAR%</th></tr><tr><td>MANAGEM</td><td>1 191,00</td><td>-4,34%</td></tr></table>
<table><tr><th>Instrument</th><th>Cours</th><th>Var%</th></tr><tr><td>MASI20 FUTURE DEC26</td><td>1 248,50</td><td>-0,51%</td></tr></table>"""
BOC_TEXT = """BULLETIN OFFICIEL DE LA COTE N° 2645 DU 24/11/2026
BVMAC ALL SHARE INDEX
1 124,02
0,00%
BVMAC-AS
GQ0000010050 BANGE BA NGE 228 085 23/11/2026 0 328 0 0 0 NC 228 085 228 085 250 894 205 277 0,00% 228 085
SEMC CM0000010009 SEMC SEMC 53 000,00 38 367 192 473 800,00 2025 25/06/2026 0,00 -  - 2 033 451 000 10 201 069 000
BANGE GQ0000010050 BANGE BANGE 228 085,00 50 000 558 960 4 830,00 2025 31/07/2026 0,00 -  - 11 404 250 000 127 490
BGFI HC GA0000010074 BHC BGFI HC 87 000,00 566 561 14 728 385 2 500,00 2025 18/05/2026 0,00 -  - 49 290 807 000 1 281
ETAT DU GABON EOG 6.25% NET 2022-2028 GA0000020313 EGA07 6,25 26/01/2023 175 000 000 000 17 500 000 4 000 100,00 6"""


def test_the_official_pages_are_parsed():
    ngx = bourses.parse_ngx(NGX_EQ, NGX_SNAP)
    assert ngx["session"] == "2026-11-24" and ngx["indices"] == [{"name": "NGX All-Share (ASI)", "close": 245685.87,
                                                                  "change": None}]
    assert [(s["symbol"], s["change"]) for s in ngx["stocks"]] == [("GTCO", -0.89), ("MTNN", 0.0)]
    assert ngx["pending"] is False and bourses.parse_ngx([dict(NGX_EQ[0], ClosePrice=None)], None)["pending"]
    ke = bourses.parse_nse_ke(NSE_PAGE)
    assert [i["name"] for i in ke["indices"]] == ["NSE All Share (NASI)", "NSE 20"]
    assert ke["indices"][1]["close"] == 4122.09 and ke["indices"][1]["change"] == pytest.approx(-6.35 / 4128.44 * 100)
    assert ke["activity"] == {"cap_bn": 3988.95, "value": 559173215.7}
    ca = bourses.parse_casablanca(CASA_PAGE)
    assert ca["indices"] == [{"name": "MASI", "close": 16764.82, "change": -0.7}]
    assert [(s["name"], s["close"]) for s in ca["stocks"]] == [("Bmci", 569.0), ("Managem", 1191.0)]   # ni volumes, ni contrats
    bv = bourses.parse_bvmac(BOC_TEXT)
    assert bv["session"] == "2026-11-24" and bv["bulletin"] == 2645
    assert bv["indices"] == [{"name": "BVMAC All Share", "close": 1124.02, "change": 0.0}]
    assert [(s["symbol"], s["close"]) for s in bv["stocks"]] == [("SEMC", 53000.0), ("BANGE", 228085.0), ("BHC", 87000.0)]


def _site(pages, calls):
    def fetch(url):
        calls.append(url)
        if url not in pages:
            raise mw.HttpError(404, "introuvable")
        body = pages[url]
        return body if isinstance(body, bytes) else (json.dumps(body) if not isinstance(body, str) else body).encode()
    return fetch


def test_one_reading_per_session_and_the_history_the_bot_builds(tmp_path, monkeypatch):
    monkeypatch.setattr(bourses, "pdf_text", lambda raw: raw.decode())
    boc = bourses.BVMAC_PDF.format(folder="2026/11", day="20261124")
    pages = {bourses.NGX_EQUITIES: NGX_EQ, bourses.NGX_SNAPSHOT: NGX_SNAP, bourses.NSE_KE_URL: NSE_PAGE,
             bourses.CASABLANCA_URL: CASA_PAGE, boc: BOC_TEXT.encode()}
    calls, memory, evening = [], _Memory(), datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)
    book = bourses.refresh(memory, evening, _site(pages, calls), str(tmp_path), pause=0)
    off = book["official"]
    assert all(off[s]["error"] == "" for s in bourses.OFFICIAL) and off["ngx"]["session"] == "2026-11-24"
    assert off["bvmac"]["session"] == "2026-11-24" and off["nse_ke"]["sessions"] == 1
    assert (tmp_path / "ngx_gtco.csv").exists() and (tmp_path / "bvmac_idx_bvmac_all_share.csv").exists()
    assert not bourses.due(off["ngx"], evening + timedelta(hours=2))                    # déjà lue pour cette séance
    # Le lendemain : la NGX a un nouvel indice, le Kenya rien de neuf (jour férié), la BVMAC pas encore de bulletin.
    NGX2 = [dict(e, TradeDate="2026-11-25T00:00:00") for e in NGX_EQ]
    pages.update({bourses.NGX_EQUITIES: NGX2, bourses.NGX_SNAPSHOT: dict(NGX_SNAP, ASI=248142.73)})
    calls.clear()
    nxt = bourses.refresh(memory, evening + timedelta(days=1), _site(pages, calls), str(tmp_path), pause=0)["official"]
    assert nxt["ngx"]["session"] == "2026-11-25" and nxt["ngx"]["sessions"] == 2
    assert nxt["ngx"]["indices"][0]["change"] == pytest.approx((248142.73 / 245685.87 - 1) * 100)   # sur la veille gardée
    assert nxt["nse_ke"]["sessions"] == 1 and nxt["nse_ke"]["session"] == "2026-11-24"   # page inchangée : rien d'ajouté
    assert nxt["bvmac"]["session"] == "2026-11-24" and nxt["bvmac"]["error"] == ""      # pas de nouveau bulletin
    assert bourses.BVMAC_PDF.format(folder="2026/12", day="20261125") in calls          # mis en ligne le mois d'après ?
    # En pleine séance, la NGX publie la liste du jour avec les seuls titres déjà échangés (cours provisoires) :
    # rien n'entre dans l'historique, rien de perdu, nouvel essai.
    open_day = [dict(e, TradeDate="2026-11-26T00:00:00", ClosePrice=None if e["Symbol"] == "GTCO" else 840.0)
                for e in NGX_EQ[:2]]
    pages.update({bourses.NGX_EQUITIES: open_day, bourses.NGX_SNAPSHOT: None})
    during = bourses.refresh(memory, evening + timedelta(days=2), _site(pages, calls), str(tmp_path), pause=0,
                             sources=("ngx",))["official"]["ngx"]
    assert "séance en cours" in during["error"] and during["session"] == "2026-11-25" and during["stocks"]
    assert len(bourses.brvm.history(str(tmp_path), "ngx_mtnn")) == 2                    # 24 et 25/11 seulement
    lagos_open = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)                     # 11 h à Lagos : en séance
    calls.clear()
    assert "séance en cours" in bourses.read_official({}, "ngx", _site(pages, calls), lagos_open, str(tmp_path),
                                                      pause=0)["error"] and calls == []    # rien n'est lu

    def down(url):
        raise TimeoutError("timed out")
    failed = bourses.refresh(memory, evening + timedelta(days=2), down, str(tmp_path), pause=0)["official"]
    assert failed["ngx"]["error"] and failed["ngx"]["stocks"] == nxt["ngx"]["stocks"]    # rien de perdu
    assert bourses.due(failed["ngx"], evening + timedelta(days=2, hours=3))


# ---------- La règle sur chaque place ----------

def _trend(n, drift, seed):
    import numpy as np
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.01, n)))


def test_the_rule_is_measured_on_each_exchange_with_the_countries_protocol():
    idx = pd.bdate_range("2016-01-04", "2026-11-24", tz="UTC")
    close = pd.DataFrame({"cac40": _trend(len(idx), 0.0004, 1), "nikkei": _trend(len(idx), -0.0002, 2)}, index=idx)
    late = close["nikkei"].copy()
    late[:"2018-06-30"] = math.nan
    close["dax"] = late                                                              # pas d'historique depuis 2018
    s = bourses.study(close, "cac40", end="2026-11-24")
    assert set(s) == {"design", "verify", "holds", "end"} and s["design"]["trades"] > 0
    assert s["holds"] == (s["design"]["rule_calmar"] > s["design"]["hold_calmar"]
                          and s["verify"]["rule_calmar"] > s["verify"]["hold_calmar"])
    assert bourses.study(close, "dax") is None and bourses.study(close, "absent") is None
    rows = bourses.study_all(lambda code: close[[code]].dropna() if code in close else pd.DataFrame())
    assert set(rows) == {"euronext", "jpx"} and rows["euronext"]["code"] == "cac40"


# ---------- Panneau et assistante ----------

def test_the_panel_page_and_what_the_assistant_knows(tmp_path):
    idx = pd.bdate_range("2025-01-01", periods=300, tz="UTC")
    close = pd.DataFrame({"cac40": _trend(300, 0.001, 3)}, index=idx)
    book = {"official": {"ngx": dict(bourses.parse_ngx(NGX_EQ, NGX_SNAP), session="2026-11-24", sessions=1, error="")}}
    v = bourses.view(book, close, {"rows": [{"code": "cac40", "trend": True, "signal": False}]}, WED)
    rows = {r["id"]: r for r in v["rows"]}
    assert len(rows) == 28 and rows["nyse"]["status"]["open"] and "zone" in rows["brvm"]
    cac = rows["euronext"]["indices"][0]
    assert cac["code"] == "cac40" and cac["trend"] is True and cac["source"] == "Yahoo Finance" and cac["spark"]
    assert rows["ngx"]["indices"][0]["close"] == 245685.87 and rows["ngx"]["official_state"]["stocks"] == 2
    assert rows["gse"]["indices"][0]["source"] == "non lu" and rows["gse"]["reads"] == "rien"
    assert [g["topic"] for g in v["guide"]][0] == "BRVM (UEMOA)"
    ctx = {"bourses": v, "courtiers": {}}
    lagos = assistant.local_answer("Quelle est la bourse du Nigeria ?", ctx)["answer"]
    assert "NGX" in lagos and "SEC Nigeria" in lagos and "245" in lagos
    usa = assistant.local_answer("Comment investir aux États-Unis depuis la Côte d'Ivoire ?", ctx)["answer"]
    assert "W-8BEN" in usa and "SGI" in usa
    demo = assistant.local_answer("Comment brancher un compte démo MetaTrader 5 ?", ctx)["answer"]
    assert "set-key mt5" in demo
    data = BotData(_cfg(tmp_path, savoir=False), market=None)
    assert len(data.exchanges()["rows"]) == 28


def test_demo_server_serves_the_exchanges_page(demo_server):  # noqa: F811
    base, _app = demo_server
    code, page = _json(base + "/api/exchanges")
    assert code == 200 and len(page["rows"]) == 28
    ngx = next(r for r in page["rows"] if r["id"] == "ngx")
    assert ngx["official_state"]["session"] and ngx["indices"][0]["close"]
