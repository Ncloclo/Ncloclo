"""Pays du monde (trendguard/pays.py) : classement MSCI de juin 2026,
indices et devises suivis, Banque mondiale lue chaque semaine, page du
panneau. Information seulement : aucun ordre réel."""

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from test_panel import _cfg, _json, demo_server  # noqa: F401  (fixture du serveur de démonstration)

from panel.data import BotData
from trendguard import multi_actifs as ma
from trendguard import pays

NOW = datetime(2026, 11, 24, 23, 0, tzinfo=timezone.utc)


class _Memory:
    def __init__(self):
        self.meta = {}

    def get(self, key, default=None):
        return self.meta.get(key, default)

    def put(self, key, value):
        self.meta[key] = value


def test_the_msci_2026_classification_is_complete_and_coherent():
    isos = [c[0] for c in pays.COUNTRIES]
    assert len(isos) == len(set(isos)) == 87
    assert pays.classes_count() == {"Développé": 23, "Émergent": 24, "Frontière avancé": 5, "Frontière": 23,
                                    "Autonome": 12}
    assert {c[2] for c in pays.COUNTRIES} == set(pays.REGIONS)
    assert all(c[5] in ma.ALL_CODES for c in pays.COUNTRIES if c[5])                # chaque indice est suivi
    assert sum(1 for c in pays.COUNTRIES if c[5]) == 49
    waemu = [c for c in pays.COUNTRIES if c[4] == pays.UEMOA]
    assert {c[0] for c in waemu} == {"BJ", "BF", "CI", "GW", "ML", "NE", "SN", "TG"}
    assert all(c[3] == "Frontière" and c[6] == "XOF" for c in waemu)
    greece = next(c for c in pays.COUNTRIES if c[0] == "GR")
    assert greece[3] == "Émergent" and "mai 2027" in greece[4]                        # changement annoncé
    no_rate = {c[6] for c in pays.COUNTRIES if not pays.currency_pair(c[6])[0]}
    assert no_rate == {"USD", "BAM", "ZWG"}
    assert pays.flag("CI") == "🇨🇮" and pays.flag("") == ""


def _series(values, start="2025-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values), tz="UTC"), dtype=float)


def test_a_currency_change_is_read_in_the_right_direction():
    n = 300
    close = pd.DataFrame({"fx_ngn": _series(np.linspace(1000, 1500, n)),        # le dollar vaut plus de nairas
                          "eurusd": _series(np.linspace(1.00, 1.10, n))})        # l'euro vaut plus de dollars
    ngn = pays.currency_view("NGN", close)
    eur = pays.currency_view("EUR", close)
    assert ngn["inverse"] and ngn["change_1y"] < 0                              # le naira s'est déprécié
    assert not eur["inverse"] and eur["change_1y"] > 0                          # l'euro s'est apprécié
    assert pays.currency_view("USD", close)["note"] and pays.currency_view("BAM", close)["value"] is None
    assert "655,957" in pays.currency_view("XOF", close)["note"]


def _wb(rows):
    return json.dumps([{"page": 1, "pages": 1, "total": len(rows)}, rows]).encode()


def test_world_bank_is_read_weekly_and_old_figures_are_left_out():
    countries = _wb([{"id": "CIV", "iso2Code": "CI", "name": "Cote d'Ivoire", "capitalCity": "Yamoussoukro",
                      "region": {"value": "Sub-Saharan Africa "}, "incomeLevel": {"value": "Lower middle income"}},
                     {"id": "WLD", "iso2Code": "1W", "name": "World", "region": {"value": "Aggregates"},
                      "incomeLevel": {"value": "Aggregates"}}])

    def fetch(url):
        if "/country?" in url:
            return countries
        year = "1995" if "GC.DOD" in url else "2025"                            # dette trop ancienne
        return _wb([{"country": {"id": "CI", "value": "Cote d'Ivoire"}, "date": year, "value": 6.5},
                    {"country": {"id": "1W", "value": "World"}, "date": year, "value": 3.1}])
    memory = _Memory()
    book = pays.refresh(memory, NOW, fetch)
    ci = book["countries"]["CI"]
    assert book["error"] == "" and ci["income"] == "revenu intermédiaire inférieur" and ci["capital"] == "Yamoussoukro"
    assert ci["indicators"]["growth"] == (6.5, "2025") and "debt" not in ci["indicators"]
    assert set(book["countries"]) == {c[0] for c in pays.COUNTRIES} and memory.get(pays.KEY) == book
    assert not pays.due(book, NOW + timedelta(days=6)) and pays.due(book, NOW + timedelta(days=7))

    def down(url):
        raise TimeoutError("timed out")
    failed = pays.refresh(memory, NOW + timedelta(days=8), down)
    assert failed["error"] and failed["countries"]["CI"] == ci                     # rien de perdu


def test_countries_view_and_panel_page(tmp_path):
    n = 400
    close = pd.DataFrame({"cac40": _series(np.linspace(7000, 8000, n)), "eurusd": _series(np.linspace(1.0, 1.1, n))})
    book = {"fetched_at": "2026-11-24T23:00:00+00:00",
            "countries": {"FR": {"income": "revenu élevé", "indicators": {"growth": [0.8, "2025"]}}}}
    view = pays.countries_view(close, book, "3m")
    assert len(view["rows"]) == 87 and view["regions"] == list(pays.REGIONS)
    fr_ = next(r for r in view["rows"] if r["iso"] == "FR")
    assert fr_["index"]["code"] == "cac40" and fr_["index"]["trend"] and len(fr_["index"]["spark"]) == 64
    assert fr_["currency"]["change_1y"] > 0 and fr_["wb"]["income"] == "revenu élevé"
    assert next(r for r in view["rows"] if r["iso"] == "CI")["index"] is None
    folder = tmp_path / "multi"
    folder.mkdir()
    close["cac40"].rename("close").to_frame().to_csv(folder / "cac40.csv", index_label="date")
    data = BotData(_cfg(tmp_path, savoir=False), market=None)
    data.multi_cache = str(folder)
    page = data.countries("1y")
    assert next(r for r in page["rows"] if r["iso"] == "FR")["index"]["code"] == "cac40"
    assert page["fetched_at"] is None


def test_demo_server_serves_the_countries_page(demo_server):  # noqa: F811
    base, _app = demo_server
    code, page = _json(base + "/api/countries?window=5y")
    assert code == 200 and len(page["rows"]) == 87
    assert sum(1 for r in page["rows"] if r["index"]) == 49
    assert all(r["wb"].get("income") for r in page["rows"])
