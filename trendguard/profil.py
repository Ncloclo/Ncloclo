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
import gzip
import hashlib
import math
import os
import pathlib
import re
import unicodedata
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
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
def english_names() -> Dict[str, str]:
    """Nom anglais de chaque pays (base des fuseaux horaires)."""
    return {row[0]: row[1] for row in _tab("iso3166.tab") if len(row) >= 2}


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


# ══════════════════════════════════════════════════════════════════════
# LANGUES OFFICIELLES, FUSEAUX ET VILLES (le panneau décide sur l'appareil)
# ══════════════════════════════════════════════════════════════════════

# Langues écrites par le panneau lui-même : le français (langue d'origine de
# chaque texte) et l'anglais (dictionnaire complet). Les autres langues
# officielles passent par la traduction de l'appareil, quand il l'a.
UI_LANGUAGES = ("fr", "en")
# Langues officielles de chaque pays (CLDR, par Babel 2.19.0) : officielles ou
# officielles de fait, la plus parlée d'abord. « CI:fr », « CM:fr,en »…
OFFICIAL_TABLE = """
AD:ca AE:ar AF:fa,ps AG:en AI:en AL:sq AM:hy AO:pt AR:es AS:sm,en AT:de AU:en AW:nl,pap
AX:sv AZ:az BA:bs,hr,sr BB:en BD:bn BE:nl,fr,de BF:fr BG:bg BH:ar BI:rn,fr,en BJ:fr BL:fr
BM:en BN:ms BO:es,qu,ay BQ:nl BR:pt BS:en BT:dz BW:en,tn BY:ru,be BZ:en CA:en,fr CC:en
CD:fr CF:sg,fr CG:fr CH:de,gsw,fr,it CI:fr CK:en CL:es CM:fr,en CN:zh CO:es CR:es CU:es
CV:pt CW:pap,nl CX:en CY:el,tr CZ:cs DE:de DJ:fr,ar DK:da DM:en DO:es DZ:ar,fr EC:es,qu
EE:et EG:ar EH:ar ER:ti,en,ar ES:es ET:am FI:fi,sv FJ:en,hif,fj FK:en FM:en FO:fo FR:fr
GA:fr GB:en GD:en GE:ka GF:fr GG:en GH:en GI:en GL:kl GM:en GN:fr GP:fr GQ:es,fr,pt GR:el
GS:en GT:es GU:en,ch GW:pt GY:en HK:zh,en HN:es HR:hr HT:ht,fr HU:hu ID:id IE:en,ga
IL:he,ar IM:en,gv IN:hi,en IO:en IQ:ar IR:fa IS:is IT:it JE:en JM:en JO:ar JP:ja KE:sw,en
KG:ky,ru KH:km KI:en,gil KM:ar,zdj,wni,fr KN:en KP:ko KR:ko KW:ar KY:en KZ:ru,kk LA:lo
LB:ar LC:en LI:de,gsw LK:si,ta LR:en LS:st,en LT:lt LU:fr,lb,de LV:lv LY:ar MA:ar,fr,tzm
MC:fr MD:ro ME:sr MF:fr MG:mg,fr,en MH:en,mh MK:mk ML:fr MM:my MN:mn MO:zh,yue,pt MP:en
MQ:fr MR:ar MS:en MT:mt,en MU:fr,en MV:dv MW:ny,en MX:es MY:ms MZ:pt NA:en NC:fr NE:fr
NF:en NG:en,yo NI:es NL:nl NO:no,nb,nn NP:ne NR:en,na NU:niu,en NZ:en,mi OM:ar PA:es
PE:es,qu PF:fr,ty PG:tpi,en,ho PH:en,fil PK:ur,en PL:pl PM:fr PN:en PR:es,en PS:ar PT:pt
PW:pau,en PY:gn,es QA:ar RE:fr RO:ro RS:sr RU:ru RW:rw,en,fr SA:ar SB:en SC:fr,en SD:en,ar
SE:sv SG:en,zh,ms,ta SH:en SI:sl SJ:nb SK:sk SL:en SM:it SN:wo,fr SO:so,ar SR:nl SS:en
ST:pt SV:es SX:en,nl SY:ar SZ:en,ss TC:en TD:ar,fr TG:fr TH:th TJ:tg TK:tkl,en TL:tet,pt
TM:tk TN:ar,fr TO:to,en TR:tr TT:en TV:tvl,en TW:zh,nan,hak TZ:sw,en UA:uk,ru UG:sw,en
UM:en US:en UY:es UZ:uz VA:it VC:en VE:es VG:en VI:en VN:vi VU:bi,en,fr WF:fr WS:sm,en
YE:ar YT:fr ZA:en ZM:en ZW:sn,en,nd
"""
# Nom de chaque langue : en français, en anglais, dans la langue elle-même (CLDR).
LANGUAGE_NAMES: Dict[str, Tuple[str, str, str]] = {
    "am": ("amharique", "Amharic", "አማርኛ"),
    "ar": ("arabe", "Arabic", "العربية"),
    "ay": ("aymara", "Aymara", ""),
    "az": ("azerbaïdjanais", "Azerbaijani", "azərbaycan"),
    "be": ("biélorusse", "Belarusian", "беларуская"),
    "bg": ("bulgare", "Bulgarian", "български"),
    "bi": ("bichelamar", "Bislama", ""),
    "bn": ("bengali", "Bangla", "বাংলা"),
    "bs": ("bosniaque", "Bosnian", "bosanski"),
    "ca": ("catalan", "Catalan", "català"),
    "ch": ("chamorro", "Chamorro", ""),
    "cs": ("tchèque", "Czech", "čeština"),
    "da": ("danois", "Danish", "dansk"),
    "de": ("allemand", "German", "Deutsch"),
    "dv": ("maldivien", "Divehi", "ދިވެހިބަސް"),
    "dz": ("dzongkha", "Dzongkha", "རྫོང་ཁ"),
    "el": ("grec", "Greek", "Ελληνικά"),
    "en": ("anglais", "English", "English"),
    "es": ("espagnol", "Spanish", "español"),
    "et": ("estonien", "Estonian", "eesti"),
    "fa": ("persan", "Persian", "فارسی"),
    "fi": ("finnois", "Finnish", "suomi"),
    "fil": ("filipino", "Filipino", "Filipino"),
    "fj": ("fidjien", "Fijian", ""),
    "fo": ("féroïen", "Faroese", "føroyskt"),
    "fr": ("français", "French", "français"),
    "ga": ("irlandais", "Irish", "Gaeilge"),
    "gil": ("gilbertin", "Gilbertese", ""),
    "gn": ("guarani", "Guarani", "avañe’ẽ"),
    "gsw": ("suisse allemand", "Swiss German", "Schwiizertüütsch"),
    "gv": ("mannois", "Manx", "Gaelg"),
    "hak": ("hakka", "Hakka Chinese", ""),
    "he": ("hébreu", "Hebrew", "עברית"),
    "hi": ("hindi", "Hindi", "हिन्दी"),
    "hif": ("hindi fidjien", "Fiji Hindi", ""),
    "ho": ("hiri motu", "Hiri Motu", ""),
    "hr": ("croate", "Croatian", "hrvatski"),
    "ht": ("créole haïtien", "Haitian Creole", "Kreyòl Ayisyen"),
    "hu": ("hongrois", "Hungarian", "magyar"),
    "hy": ("arménien", "Armenian", "հայերեն"),
    "id": ("indonésien", "Indonesian", "Indonesia"),
    "is": ("islandais", "Icelandic", "íslenska"),
    "it": ("italien", "Italian", "italiano"),
    "ja": ("japonais", "Japanese", "日本語"),
    "ka": ("géorgien", "Georgian", "ქართული"),
    "kk": ("kazakh", "Kazakh", "қазақ тілі"),
    "kl": ("groenlandais", "Kalaallisut", "kalaallisut"),
    "km": ("khmer", "Khmer", "ខ្មែរ"),
    "ko": ("coréen", "Korean", "한국어"),
    "ky": ("kirghize", "Kyrgyz", "кыргызча"),
    "lb": ("luxembourgeois", "Luxembourgish", "Lëtzebuergesch"),
    "lo": ("lao", "Lao", "ລາວ"),
    "lt": ("lituanien", "Lithuanian", "lietuvių"),
    "lv": ("letton", "Latvian", "latviešu"),
    "mg": ("malgache", "Malagasy", "Malagasy"),
    "mh": ("marshallais", "Marshallese", ""),
    "mi": ("maori", "Māori", "Māori"),
    "mk": ("macédonien", "Macedonian", "македонски"),
    "mn": ("mongol", "Mongolian", "монгол"),
    "ms": ("malais", "Malay", "Melayu"),
    "mt": ("maltais", "Maltese", "Malti"),
    "my": ("birman", "Burmese", "မြန်မာ"),
    "na": ("nauruan", "Nauru", ""),
    "nan": ("minnan", "Min Nan Chinese", ""),
    "nb": ("norvégien bokmål", "Norwegian Bokmål", "norsk bokmål"),
    "nd": ("ndébélé du Nord", "North Ndebele", "isiNdebele"),
    "ne": ("népalais", "Nepali", "नेपाली"),
    "niu": ("niuéen", "Niuean", ""),
    "nl": ("néerlandais", "Dutch", "Nederlands"),
    "nn": ("norvégien nynorsk", "Norwegian Nynorsk", "norsk nynorsk"),
    "no": ("norvégien", "Norwegian", "norsk"),
    "ny": ("chewa", "Nyanja", "Nyanja"),
    "pap": ("papiamento", "Papiamento", "Papiamentu"),
    "pau": ("palau", "Palauan", ""),
    "pl": ("polonais", "Polish", "polski"),
    "ps": ("pachto", "Pashto", "پښتو"),
    "pt": ("portugais", "Portuguese", "português"),
    "qu": ("quechua", "Quechua", "Runasimi"),
    "rn": ("roundi", "Rundi", "Ikirundi"),
    "ro": ("roumain", "Romanian", "română"),
    "ru": ("russe", "Russian", "русский"),
    "rw": ("kinyarwanda", "Kinyarwanda", "Ikinyarwanda"),
    "sg": ("sango", "Sango", "Sängö"),
    "si": ("cingalais", "Sinhala", "සිංහල"),
    "sk": ("slovaque", "Slovak", "slovenčina"),
    "sl": ("slovène", "Slovenian", "slovenščina"),
    "sm": ("samoan", "Samoan", ""),
    "sn": ("shona", "Shona", "chiShona"),
    "so": ("somali", "Somali", "Soomaali"),
    "sq": ("albanais", "Albanian", "shqip"),
    "sr": ("serbe", "Serbian", "српски"),
    "ss": ("swati", "Swati", "siSwati"),
    "st": ("sotho du Sud", "Southern Sotho", "Sesotho"),
    "sv": ("suédois", "Swedish", "svenska"),
    "sw": ("swahili", "Swahili", "Kiswahili"),
    "ta": ("tamoul", "Tamil", "தமிழ்"),
    "tet": ("tétoum", "Tetum", ""),
    "tg": ("tadjik", "Tajik", "тоҷикӣ"),
    "th": ("thaï", "Thai", "ไทย"),
    "ti": ("tigrigna", "Tigrinya", "ትግርኛ"),
    "tk": ("turkmène", "Turkmen", "türkmen dili"),
    "tkl": ("tokelau", "Tokelauan", ""),
    "tn": ("tswana", "Tswana", "Setswana"),
    "to": ("tongien", "Tongan", "lea fakatonga"),
    "tpi": ("tok pisin", "Tok Pisin", "Tok Pisin"),
    "tr": ("turc", "Turkish", "Türkçe"),
    "tvl": ("tuvalu", "Tuvalu", ""),
    "ty": ("tahitien", "Tahitian", ""),
    "tzm": ("amazighe de l’Atlas central", "Central Atlas Tamazight", "Tamaziɣt n laṭlaṣ"),
    "uk": ("ukrainien", "Ukrainian", "українська"),
    "ur": ("ourdou", "Urdu", "اردو"),
    "uz": ("ouzbek", "Uzbek", "o‘zbek"),
    "vi": ("vietnamien", "Vietnamese", "Tiếng Việt"),
    "wni": ("wni", "wni", ""),
    "wo": ("wolof", "Wolof", "Wolof"),
    "yo": ("yoruba", "Yoruba", "Èdè Yorùbá"),
    "yue": ("cantonais", "Cantonese", "粵語"),
    "zdj": ("zdj", "zdj", ""),
    "zh": ("chinois", "Chinese", "中文"),
}

@functools.lru_cache(maxsize=1)
def official() -> Dict[str, Tuple[str, ...]]:
    """Langues officielles de chaque pays, la plus parlée d'abord."""
    out: Dict[str, Tuple[str, ...]] = {}
    for item in OFFICIAL_TABLE.split():
        iso, _sep, langs = item.partition(":")
        out[iso] = tuple(langs.split(","))
    return out


def languages_of(iso: str) -> List[str]:
    """Langues officielles d'un pays, la plus parlée d'abord ; [] si la
    table n'en donne pas (terres australes sans habitants)."""
    return list(official().get(str(iso or "").strip().upper(), ()))


def language_for(iso: str, device: Sequence[str] = (), can: Sequence[str] = UI_LANGUAGES) -> str:
    """La langue du panneau pour ce pays : parmi ses langues officielles,
    celle que l'appareil préfère, sinon la plus parlée, pourvu que le
    panneau sache l'afficher (`can` : le français, l'anglais et les langues
    que l'appareil sait traduire) ; sinon l'anglais, langue internationale."""
    langs = languages_of(iso)
    prefs = [str(tag).split("-")[0].lower() for tag in device]
    order = [code for code in prefs if code in langs] + [code for code in langs if code not in prefs]
    return next((code for code in order if code in can), "en")


def language_name(code: str, lang: str = "fr") -> str:
    """Nom d'une langue en français, en anglais ou dans la langue elle-même."""
    n = LANGUAGE_NAMES.get(code)
    if n is None:
        return code
    return n[0] if lang == "fr" else n[1] if lang == "en" else (n[2] or n[1])


def _tzif(tz: str) -> bytes:
    """Le fichier d'un fuseau (tzdata, sinon celui du système) ; b"" s'il
    est introuvable."""
    try:
        import importlib.resources as resources
        return (resources.files("tzdata.zoneinfo").joinpath(*tz.split("/"))).read_bytes()
    except (ImportError, OSError, ModuleNotFoundError, ValueError):
        path = os.path.join("/usr/share/zoneinfo", *tz.split("/"))
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return b""


@functools.lru_cache(maxsize=1)
def zone_table() -> List[Tuple[str, str, int]]:
    """Chaque fuseau de la base : (fuseau, pays, groupe). Deux fuseaux du même
    groupe ont toujours eu la même heure : ce sont des alias (Douala et Lagos,
    Dakar et Abidjan…), qu'un appareil peut annoncer l'un pour l'autre ; la
    langue de l'appareil départage alors les pays du groupe. Sans réseau."""
    groups: Dict[bytes, int] = {}
    out = []
    for row in _tab("zone.tab"):
        if len(row) < 3:
            continue
        data = _tzif(row[2])
        key = hashlib.sha1(data).digest() if data else row[2].encode()
        out.append((row[2], row[0], groups.setdefault(key, len(groups))))
    return out


CITIES = pathlib.Path(__file__).with_name("villes.tsv.gz")


@functools.lru_cache(maxsize=1)
def cities() -> List[Tuple[str, str, float, float]]:
    """Les villes de plus de 15 000 habitants (GeoNames, licence CC BY 4.0 ;
    research/villes.py) : (pays, ville, latitude, longitude)."""
    out: List[Tuple[str, str, float, float]] = []
    try:
        with gzip.open(CITIES, "rt", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                f = line.rstrip("\n").split("\t")
                if len(f) >= 4:
                    out.append((f[0], f[1], float(f[2]), float(f[3])))
    except (OSError, ValueError):
        return []                                  # table absente ou abîmée : pas de ville
    return out


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance sur le globe (km)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 12742 * math.asin(min(1.0, math.sqrt(h)))


NEAR_KM = 250                                       # au-delà : loin de toute ville, pays incertain


def nearest_city(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """La ville la plus proche d'une position : pays, ville, distance (km),
    et si elle est assez proche pour en déduire le pays. La position n'est
    ni gardée ni envoyée ailleurs : calcul sur ce PC."""
    lat, lon = float(lat), float(lon)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("position hors du globe")
    k = math.cos(math.radians(lat))
    best: Optional[Tuple[float, str, str, float, float]] = None
    for iso, name, la, lo in cities():
        dlon = abs(lo - lon)
        dlon = min(dlon, 360 - dlon) * k
        d2 = (la - lat) ** 2 + dlon * dlon
        if best is None or d2 < best[0]:
            best = (d2, iso, name, la, lo)
    if best is None:
        return None
    km = round(_km(lat, lon, best[3], best[4]))
    return {"country": best[1], "city": best[2], "km": km, "near": km <= NEAR_KM,
            "name": names().get(best[1], best[1])}


def geo_data(profile_: Mapping[str, Any]) -> Dict[str, Any]:
    """Ce que le panneau donne à l'appareil pour reconnaître lui-même son pays
    et sa langue : fuseaux (avec leur pays et leur groupe d'alias), langues
    officielles de chaque pays et leurs noms, pays. Rien de l'appareil ne
    revient au bot."""
    country = str(profile_.get("country") or DEFAULT_COUNTRY)
    return {"profile": dict(profile_, languages=languages_of(country)),
            "zones": [list(z) for z in zone_table()],
            "official": {iso: list(v) for iso, v in official().items()},
            "language_names": {k: {"fr": v[0], "en": v[1], "native": v[2]} for k, v in LANGUAGE_NAMES.items()},
            "builtin": list(UI_LANGUAGES), "countries": countries()}
