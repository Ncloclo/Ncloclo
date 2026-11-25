"""Votre profil (trendguard/profil.py) : pays, fuseau horaire et monnaie, pour
que le bot parle de votre situation, où que vous soyez. Sans réseau : la base
des fuseaux horaires (tzdata) est sur le PC."""


import pytest

from trendguard import profil
from trendguard.config import GuardConfig, load_guard_config_from_env


def test_every_country_has_a_name_a_time_zone_and_a_currency():
    names = profil.names()
    assert len(names) == 249 and names["CI"] == "Côte d'Ivoire" and names["CM"] == "Cameroun"
    assert names["DE"] == "Allemagne" and names["JP"] == "Japon"                    # pays suivis : en français
    assert profil.profile({}) == {"country": "CI", "name": "Côte d'Ivoire", "tz": "Africa/Abidjan",
                                  "currency": "XOF", "zone": "UEMOA"}
    assert profil.profile({"TG_PAYS": "fr"})["tz"] == "Europe/Paris"
    cm = profil.profile({"TG_PAYS": "CM"})
    assert cm["currency"] == "XAF" and cm["zone"] == "CEMAC" and cm["tz"] == "Africa/Douala"
    assert profil.profile({"TG_PAYS": "BR"})["tz"] == "America/Sao_Paulo"           # pas Noronha
    assert profil.profile({"TG_PAYS": "NG"})["currency"] == "NGN"
    assert profil.profile({"TG_PAYS": "ZZ"})["country"] == "CI"                     # inconnu : par défaut
    assert profil.profile({"TG_PAYS": "US", "TG_FUSEAU": "America/Los_Angeles"})["tz"] == "America/Los_Angeles"
    assert profil.profile({"TG_PAYS": "US", "TG_FUSEAU": "Mars/Olympus"})["tz"] == "America/New_York"
    assert profil.display_currency("SN") == "FCFA" and profil.display_currency("DE") == "EUR"
    first = [c["name"] for c in profil.countries()][:3]
    assert first[0] == "Afghanistan" and len(profil.countries()) == 249


def test_the_bot_configuration_carries_the_profile(monkeypatch):
    for k in ("TG_PAYS", "TG_FUSEAU", "TG_DEVISE"):
        monkeypatch.delenv(k, raising=False)
    g = load_guard_config_from_env()
    assert (g.pays, g.fuseau, g.devise) == ("CI", "", "FCFA")                       # rien ne change par défaut
    monkeypatch.setenv("TG_PAYS", "NG")
    g = load_guard_config_from_env()
    assert g.devise == "NGN" and profil.of_config(g)["name"] == "Nigeria"
    monkeypatch.setenv("TG_DEVISE", "usd")
    assert load_guard_config_from_env().devise == "USD"
    for bad in ({"devise": "ABC"}, {"pays": "C1"}, {"fuseau": "Mars/Olympus"}):
        with pytest.raises(ValueError):
            GuardConfig(**bad)
