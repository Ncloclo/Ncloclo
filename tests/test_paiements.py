"""Paiements près de chez vous (trendguard/paiements.py), langues officielles,
fuseaux et villes (trendguard/profil.py), et leurs routes du panneau."""

import json

import pytest

from panel import data as pdata
from trendguard import paiements, profil

# ---------- Registre des moyens de paiement ----------

def test_every_registered_method_is_complete_and_known():
    assert set(paiements.COUNTRIES) <= set(profil.names())
    for zone in paiements.ZONES:
        assert zone in {profil.zone_of(c) for c in profil.names()}
    groups = [m for ms in paiements.COUNTRIES.values() for m in ms] + list(paiements.GLOBAL)
    groups += [m for ms in paiements.ZONES.values() for m in ms]
    for m in groups:
        assert m.kind in paiements.KIND_CODES, m.name
        assert m.name and m.name_en and m.fr and m.en, m.name
        assert bool(m.care[0]) == bool(m.care[1]), m.name      # une prudence se dit dans les deux langues
        assert "http" not in m.fr + m.en                         # aucun lien : on ne recommande pas d'adresse


def test_cote_d_ivoire_mobile_money_cards_and_zone():
    v = paiements.methods("CI")
    names = [m["name"] for m in v["methods"]]
    for n in ("Orange Money", "MTN Mobile Money (MoMo)", "Moov Money", "Wave", "Cartes GIM-UEMOA", "Binance P2P"):
        assert n in names, n
    assert v["known"] and v["zone"] == "UEMOA" and v["currency"] == "XOF" and v["languages"] == ["fr"]
    kinds = [m["kind"] for m in v["methods"]]
    assert kinds == sorted(kinds, key=paiements.KIND_CODES.index)       # rangés par famille
    mobile = [m for m in v["methods"] if m["kind"] == "mobile"]
    assert mobile[0]["scope"] == "pays"                                  # le pays avant la zone et le monde
    assert "d'Orange" in mobile[0]["fr"] and "by Orange" in mobile[0]["en"]


def test_nigeria_in_english_and_a_country_without_its_own_list():
    v = paiements.methods("NG")
    assert {"Paystack", "Flutterwave", "OPay"} <= {m["name_en"] for m in v["methods"]}
    assert v["name_en"] == "Nigeria" and v["languages"][0] == "en"
    iceland = paiements.methods("IS")
    assert not iceland["known"] and iceland["zone"] == ""
    assert {m["scope"] for m in iceland["methods"]} == {"monde"}


def test_view_refuses_an_unknown_country_and_never_asks_for_secrets():
    with pytest.raises(ValueError):
        paiements.view("ZZ")
    v = paiements.view("")
    assert v["country"] == v["profile"]["country"] and "CI" in v["covered"]
    first = v["safety"][0]
    assert "numéro de carte" in first["fr"] and "card number" in first["en"]


def test_command_line(capsys):
    assert paiements.main(["SN"]) == 0
    out = capsys.readouterr().out
    assert "Sénégal" in out and "Wave" in out and "Binance P2P" in out
    assert paiements.main(["ZZ"]) == 2


# ---------- Langues officielles ----------

def test_official_languages_of_every_country():
    langs = profil.official()
    assert len(langs) >= 240
    assert profil.languages_of("CI") == ["fr"] and profil.languages_of("CM") == ["fr", "en"]
    assert profil.languages_of("CA") == ["en", "fr"] and profil.languages_of("BR") == ["pt"]
    assert profil.languages_of("CN") == ["zh"] and profil.languages_of("de") == ["de"]
    assert all(code in profil.LANGUAGE_NAMES for v in langs.values() for code in v)


def test_panel_language_follows_the_country_and_what_it_can_show():
    assert profil.language_for("CI") == "fr"
    assert profil.language_for("SN") == "fr"                  # le wolof d'abord, que le panneau n'écrit pas
    assert profil.language_for("NG") == "en" and profil.language_for("RW") == "en"
    assert profil.language_for("CM", ["en-CM"]) == "en" and profil.language_for("CM", ["fr-FR"]) == "fr"
    assert profil.language_for("DE") == "en"                  # l'allemand, s'il ne sait pas l'afficher
    assert profil.language_for("DE", can=["fr", "en", "de"]) == "de"
    assert profil.language_for("MA") == "fr"                  # l'arabe, puis le français
    assert profil.language_for("MA", can=["fr", "en", "ar"]) == "ar"
    assert profil.language_name("zh") == "chinois" and profil.language_name("zh", "en") == "Chinese"
    assert profil.language_name("zh", "zh") == "中文" and profil.language_name("xx") == "xx"


def test_time_zone_aliases_share_a_group():
    group = {zone: g for zone, _iso, g in profil.zone_table()}
    assert group["Africa/Lagos"] == group["Africa/Douala"]     # même heure depuis toujours
    assert group["Africa/Abidjan"] == group["Africa/Dakar"]
    assert group["America/Toronto"] != group["America/New_York"]
    assert {iso for _z, iso, _g in profil.zone_table()} <= set(profil.names())


def test_nearest_city_without_any_outside_service():
    assert profil.nearest_city(5.35, -4.0)["city"] == "Abidjan"
    korhogo = profil.nearest_city(9.45, -5.63)
    assert korhogo["country"] == "CI" and korhogo["near"]       # le nord du pays, pas le Mali voisin
    assert profil.nearest_city(-23.55, -46.63)["city"] == "São Paulo"   # la ville, pas un quartier
    assert not profil.nearest_city(0.0, -160.0)["near"]          # en plein océan
    with pytest.raises(ValueError):
        profil.nearest_city(95, 0)
    with pytest.raises(ValueError):
        pdata.geo_locate("abc", "1")


def test_geo_data_is_small_and_self_sufficient():
    g = profil.geo_data(profil.profile({}))
    assert {"profile", "zones", "official", "language_names", "builtin", "countries"} <= set(g)
    assert g["profile"]["languages"] == ["fr"] and g["builtin"] == ["fr", "en"]
    assert len(json.dumps(g, ensure_ascii=False)) < 80_000
