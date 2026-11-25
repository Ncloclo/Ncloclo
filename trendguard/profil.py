"""
Votre profil (docs/PROFIL.md) : votre pays, votre fuseau horaire et votre
monnaie, pour que le bot parle de votre situation, où que vous soyez. Les
heures des bourses se lisent dans votre heure, l'accès aux bourses et aux
courtiers se juge depuis votre pays, le capital s'affiche dans votre monnaie.

Réglages dans .env (rien n'est envoyé à l'extérieur) :

    TG_PAYS=CI          votre pays, code ISO à deux lettres (Côte d'Ivoire par défaut)
    TG_FUSEAU=          votre fuseau horaire (ex. Europe/Paris) ; par défaut celui de votre pays
    TG_DEVISE=          votre monnaie (ex. XOF, EUR, NGN, USD) ; par défaut celle de votre pays

Noms des pays et fuseaux : base des fuseaux horaires (tzdata, sans réseau) ;
noms en français pour les pays suivis par le bot et pour l'Afrique.
"""

from __future__ import annotations

import functools
import os
import re
import unicodedata
from typing import Any, Dict, List, Mapping, Optional
from zoneinfo import ZoneInfo

from . import pays

COUNTRY_ENV, TZ_ENV, CURRENCY_ENV = "TG_PAYS", "TG_FUSEAU", "TG_DEVISE"
DEFAULT_COUNTRY = "CI"
UEMOA = ("BJ", "BF", "CI", "GW", "ML", "NE", "SN", "TG")       # franc CFA de la BCEAO (XOF)
CEMAC = ("CM", "CF", "CG", "GA", "GQ", "TD")                   # franc CFA de la BEAC (XAF)
EURO = ("AT", "BE", "HR", "CY", "EE", "FI", "FR", "DE", "GR", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PT",
        "SK", "SI", "ES", "AD", "MC", "SM", "VA", "ME", "XK")
# Pays à plusieurs fuseaux : celui de la capitale ou de la principale place
# financière (la base des fuseaux cite d'abord un autre).
MAIN_TZ = {"BR": "America/Sao_Paulo", "RU": "Europe/Moscow", "AU": "Australia/Sydney", "CA": "America/Toronto",
           "MX": "America/Mexico_City", "ID": "Asia/Jakarta", "KZ": "Asia/Almaty", "CD": "Africa/Kinshasa",
           "CL": "America/Santiago", "AR": "America/Argentina/Buenos_Aires", "ES": "Europe/Madrid",
           "PT": "Europe/Lisbon", "EC": "America/Guayaquil", "UA": "Europe/Kyiv", "NZ": "Pacific/Auckland",
           "DE": "Europe/Berlin", "MY": "Asia/Kuala_Lumpur", "MN": "Asia/Ulaanbaatar", "US": "America/New_York"}
# Noms français des pays d'Afrique que la liste des pays suivis (pays.py) ne contient pas.
FRENCH = {"DZ": "Algérie", "AO": "Angola", "BW": "Botswana", "BI": "Burundi", "CM": "Cameroun", "CV": "Cap-Vert",
          "CF": "Centrafrique", "KM": "Comores", "CG": "Congo", "CD": "République démocratique du Congo",
          "DJ": "Djibouti", "ER": "Érythrée", "SZ": "Eswatini", "ET": "Éthiopie", "GA": "Gabon", "GM": "Gambie",
          "GH": "Ghana", "GN": "Guinée", "GQ": "Guinée équatoriale", "LS": "Lesotho", "LR": "Libéria",
          "LY": "Libye", "MG": "Madagascar", "MW": "Malawi", "MR": "Mauritanie", "MZ": "Mozambique",
          "NA": "Namibie", "UG": "Ouganda", "RW": "Rwanda", "ST": "Sao Tomé-et-Principe", "SC": "Seychelles",
          "SL": "Sierra Leone", "SO": "Somalie", "SD": "Soudan", "SS": "Soudan du Sud", "TZ": "Tanzanie",
          "TD": "Tchad", "ZM": "Zambie", "ZW": "Zimbabwe", "RU": "Russie"}


def _tab(name: str) -> List[List[str]]:
    """Une table de la base des fuseaux horaires (tzdata, sinon celle du système)."""
    text = ""
    try:
        import importlib.resources as resources
        text = (resources.files("tzdata.zoneinfo") / name).read_text(encoding="utf-8")
    except (ImportError, OSError, ModuleNotFoundError):
        path = os.path.join("/usr/share/zoneinfo", name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
    return [line.split("\t") for line in text.splitlines() if line and not line.startswith("#")]


@functools.lru_cache(maxsize=1)
def names() -> Dict[str, str]:
    """Nom de chaque pays : en français pour les pays suivis et l'Afrique, en
    anglais (base des fuseaux) pour les autres."""
    out = {row[0]: row[1] for row in _tab("iso3166.tab") if len(row) >= 2}
    out.update(FRENCH)
    out.update({c[0]: c[1] for c in pays.COUNTRIES})
    return out


@functools.lru_cache(maxsize=1)
def zones() -> Dict[str, str]:
    """Fuseau horaire de chaque pays (le premier cité, sauf exceptions)."""
    out: Dict[str, str] = {}
    for row in _tab("zone.tab"):
        if len(row) >= 3:
            out.setdefault(row[0], row[2])
    out.update(MAIN_TZ)
    return out


def currency_of(iso: str) -> str:
    """Monnaie d'un pays : celle des pays suivis, le franc CFA (XOF ou XAF),
    l'euro ; sinon le dollar."""
    known = {c[0]: c[6] for c in pays.COUNTRIES if c[6]}
    if iso in known:
        return known[iso]
    if iso in UEMOA:
        return "XOF"
    if iso in CEMAC:
        return "XAF"
    return "EUR" if iso in EURO else "USD"


def zone_of(iso: str) -> str:
    """La zone monétaire et boursière d'un pays, quand elle compte : UEMOA,
    CEMAC, zone euro ou États-Unis."""
    return ("UEMOA" if iso in UEMOA else "CEMAC" if iso in CEMAC else "zone euro" if iso in EURO
            else "États-Unis" if iso == "US" else "")


def valid_tz(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ValueError, KeyError, OSError):
        return False
    return bool(name)


def display_currency(iso: str) -> str:
    """Monnaie d'affichage par défaut d'un pays (TG_DEVISE) : « FCFA » pour le
    franc CFA, le code ISO sinon."""
    cur = currency_of(iso)
    return "FCFA" if cur in ("XOF", "XAF") else cur


def of_config(gcfg: Any) -> Dict[str, Any]:
    """Le profil tiré de la configuration du bot (TG_PAYS, TG_FUSEAU, TG_DEVISE)."""
    out = profile({COUNTRY_ENV: getattr(gcfg, "pays", ""), TZ_ENV: getattr(gcfg, "fuseau", "")})
    return dict(out, currency=getattr(gcfg, "devise", "") or out["currency"])


def profile(env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Le profil de l'utilisateur : pays (code et nom), fuseau, monnaie, zone.
    Un réglage inconnu reprend la valeur par défaut, sans erreur."""
    env = os.environ if env is None else env
    iso = str(env.get(COUNTRY_ENV) or "").strip().upper()
    iso = iso if re.fullmatch(r"[A-Z]{2}", iso) and iso in names() else DEFAULT_COUNTRY
    tz = str(env.get(TZ_ENV) or "").strip()
    tz = tz if valid_tz(tz) else zones().get(iso, "UTC")
    return {"country": iso, "name": names().get(iso, iso), "tz": tz, "currency": currency_of(iso),
            "zone": zone_of(iso)}


def countries() -> List[Dict[str, str]]:
    """Tous les pays, par nom, pour choisir le sien."""
    def key(c: Dict[str, str]) -> str:              # « Éthiopie » parmi les E
        return unicodedata.normalize("NFKD", c["name"]).encode("ascii", "ignore").decode().lower()
    return sorted(({"iso": k, "name": v, "zone": zone_of(k)} for k, v in names().items()), key=key)
