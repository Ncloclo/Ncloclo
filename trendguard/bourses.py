"""
Les bourses du monde (docs/BOURSES.md) : la structure de cotation de chaque
grande place, comme la BRVM pour l'UEMOA. Pour chacune : ville, pays ou zone,
monnaie, heures de séance, indices phares, régulateur, accès depuis la Côte
d'Ivoire et ce que le bot en lit.

- Indices lus chaque jour chez Yahoo Finance (multi_actifs). La règle du bot
  est mesurée sur l'indice principal de chaque place, face à
  l'achat-conservation, avec le protocole de l'étude des pays (paramètres
  gelés, 2018-2022 puis depuis 2023).
- Pages officielles lues après chaque séance, comme celles de la BRVM, là où
  leur robots.txt le permet et où les cours sont publiés simplement : Nigeria
  (NGX : cours de chaque action, indice All-Share), Kenya (NSE : indices et
  activité), Maroc (Bourse de Casablanca : MASI, MASI 20, plus fortes hausses
  et baisses), Afrique centrale (BVMAC : bulletin officiel de la cote). Le bot
  construit leur historique séance après séance (data_bourses/).
- Fiches lues par l'assistante du panneau. Information seulement : aucune
  décision du bot n'en dépend, aucun ordre n'est passé.

    python trendguard_bot.py bourses                 # état des places et des lectures
    python trendguard_bot.py bourses lire            # pages officielles relues tout de suite
"""

from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

import v29

from . import brvm
from . import multi_actifs as ma
from . import trend_strategy as ts
from .texte import fr

KEY = "bourses"                         # méta du noyau de savoir
READ_AFTER = (17, 0)                    # heure UTC : Lagos, Nairobi, Casablanca et Douala ont clos
RETRY_HOURS = 3                         # après une lecture ratée
PAUSE = 3                               # secondes entre deux lectures d'un même site
DESIGN = ("2018-01-01", "2022-12-31")   # protocole de l'étude des pays (research/pays.py)
VERIFY_START = "2023-01-01"
CACHE = os.path.join(v29.APP_DIR, "data_bourses")
BRVM_CACHE = os.path.join(v29.APP_DIR, "data_brvm")
Fetch = Callable[[str], bytes]

AFRIQUE, AMERIQUES, EUROPE, ASIE, OCEANIE = "Afrique", "Amériques", "Europe", "Asie et Moyen-Orient", "Océanie"
REGIONS = (AFRIQUE, AMERIQUES, EUROPE, ASIE, OCEANIE)
WEEK = (0, 1, 2, 3, 4)                  # du lundi au vendredi
GULF = (6, 0, 1, 2, 3)                  # du dimanche au jeudi
FOREIGN = ("par un courtier international régulé qui donne accès à cette bourse et accepte les résidents "
           "ivoiriens : à vérifier avant d'ouvrir un compte")
US = ("par un courtier international régulé (SEC et FINRA aux États-Unis, FCA, CySEC…) qui accepte les "
      "résidents ivoiriens ; formulaire W-8BEN à l'inscription. Les SGI de la BRVM n'y donnent pas accès")
INDICATIVE = "horaires indicatifs : les sources ne concordent pas, vérifiez sur le site de la bourse"


def _x(bid: str, name: str, region: str, countries: Tuple[str, ...], city: str, currency: str, tz: str,
       sessions: Tuple[Tuple[str, str], ...], regulator: str, site: str,
       indices: Tuple[Tuple[str, str], ...], days: Tuple[int, ...] = WEEK, official: str = "",
       hours_note: str = "", access: str = FOREIGN, note: str = "") -> Dict[str, Any]:
    return {"id": bid, "name": name, "region": region, "countries": countries, "city": city, "currency": currency,
            "tz": tz, "sessions": sessions, "days": days, "regulator": regulator, "site": site, "indices": indices,
            "official": official, "hours_note": hours_note, "access": access, "note": note}


# Les grandes places de cotation : (nom de l'indice, code multi_actifs s'il
# est lu chez Yahoo Finance, sinon ""). Heures locales de la séance (pauses
# comprises) ; les jours fériés et les changements exceptionnels sont ceux du
# calendrier de chaque bourse.
EXCHANGES: Tuple[Dict[str, Any], ...] = (
    # ---------- Afrique ----------
    _x("brvm", "BRVM (Bourse régionale des valeurs mobilières)", AFRIQUE,
       ("BJ", "BF", "CI", "GW", "ML", "NE", "SN", "TG"), "Abidjan", "XOF", "Africa/Abidjan", (("09:00", "15:00"),),
       "AMF-UMOA (ancien CREPMF)", "https://www.brvm.org", (("BRVM Composite", ""), ("BRVM 30", "")),
       official="brvm", hours_note="préouverture 9 h, cotation continue 9 h 45 - 14 h, fixing de clôture 14 h 30, "
                                   "fin de séance 15 h (site de la BRVM)",
       access="par une SGI agréée par l'AMF-UMOA : compte-titres en FCFA, sans change",
       note="bourse commune des huit pays de l'UEMOA"),
    _x("bvmac", "BVMAC (Bourse des valeurs mobilières de l'Afrique centrale)", AFRIQUE,
       ("CM", "CF", "CG", "GA", "GQ", "TD"), "Douala", "XAF", "Africa/Douala", (),
       "COSUMAF", "https://www.bvm-ac.org", (("BVMAC All Share", ""),), official="bvmac",
       hours_note="cotation chaque jour ouvré (bulletin officiel de la cote) ; heures non lues",
       access="par un intermédiaire agréé par la COSUMAF", note="bourse commune des six pays de la CEMAC"),
    _x("jse", "JSE (Johannesburg Stock Exchange)", AFRIQUE, ("ZA",), "Johannesburg", "ZAR", "Africa/Johannesburg",
       (("09:00", "17:00"),), "FSCA", "https://www.jse.co.za",
       (("FTSE/JSE Top 40", "za_top40"), ("FTSE/JSE All Share", "za_jse")),
       note="première bourse d'Afrique par la capitalisation"),
    _x("ngx", "NGX (Nigerian Exchange)", AFRIQUE, ("NG",), "Lagos", "NGN", "Africa/Lagos", (("09:00", "16:00"),),
       "SEC Nigeria", "https://ngxgroup.com", (("NGX All-Share (ASI)", ""),), official="ngx",
       hours_note="9 h - 16 h depuis le 27/04/2026 (séance allongée, d'après la presse nigériane)"),
    _x("casablanca", "Bourse de Casablanca", AFRIQUE, ("MA",), "Casablanca", "MAD", "Africa/Casablanca",
       (("09:30", "15:30"),), "AMMC", "https://www.casablanca-bourse.com", (("MASI", ""), ("MASI 20", "")),
       official="casablanca", hours_note="cotation continue jusqu'à 15 h 20, puis fixing de clôture"),
    _x("egx", "EGX (Egyptian Exchange)", AFRIQUE, ("EG",), "Le Caire", "EGP", "Africa/Cairo", (("10:00", "14:30"),),
       "FRA", "https://www.egx.com.eg", (("EGX 30", ""),), days=GULF,
       hours_note="du dimanche au jeudi ; site injoignable depuis ce PC et indice sans historique chez Yahoo"),
    _x("nse_ke", "NSE (Nairobi Securities Exchange)", AFRIQUE, ("KE",), "Nairobi", "KES", "Africa/Nairobi",
       (("09:30", "15:00"),), "CMA Kenya", "https://www.nse.co.ke",
       (("NSE All Share (NASI)", ""), ("NSE 20", "")), official="nse_ke", hours_note=INDICATIVE),
    _x("gse", "GSE (Ghana Stock Exchange)", AFRIQUE, ("GH",), "Accra", "GHS", "Africa/Accra", (),
       "SEC Ghana", "https://gse.com.gh", (("GSE Composite (GSE-CI)", ""),),
       hours_note="heures non vérifiées ; pas de page de cours à jour lisible simplement"),
    # ---------- Amériques ----------
    _x("nyse", "NYSE (New York Stock Exchange)", AMERIQUES, ("US",), "New York", "USD", "America/New_York",
       (("09:30", "16:00"),), "SEC", "https://www.nyse.com",
       (("NYSE Composite", "us_nya"), ("Dow Jones", "dow"), ("S&P 500", "sp500")), access=US,
       note="la bourse historique de Wall Street : grandes entreprises établies"),
    _x("nasdaq", "Nasdaq", AMERIQUES, ("US",), "New York", "USD", "America/New_York", (("09:30", "16:00"),),
       "SEC", "https://www.nasdaq.com", (("Nasdaq Composite", "nasdaqcomp"), ("Nasdaq 100", "nasdaq100")),
       access=US, note="premier marché entièrement électronique (1971) : technologie et croissance"),
    _x("cboe", "Cboe (Chicago Board Options Exchange)", AMERIQUES, ("US",), "Chicago", "USD", "America/New_York",
       (("09:30", "16:15"),), "SEC", "https://www.cboe.com", (("VIX (volatilité du S&P 500)", "vix"),), access=US,
       hours_note="options sur actions ; horaires indicatifs", note="options et indice de volatilité VIX"),
    _x("tsx", "TSX (Toronto Stock Exchange)", AMERIQUES, ("CA",), "Toronto", "CAD", "America/Toronto",
       (("09:30", "16:00"),), "ACVM (Autorités canadiennes en valeurs mobilières)", "https://www.tsx.com",
       (("S&P/TSX Composite", "ca_tsx"),)),
    _x("b3", "B3 (Brasil, Bolsa, Balcão)", AMERIQUES, ("BR",), "São Paulo", "BRL", "America/Sao_Paulo",
       (("10:00", "17:00"),), "CVM", "https://www.b3.com.br", (("Ibovespa", "bovespa"),),
       hours_note="horaires ajustés deux fois par an (heure d'été américaine) : indicatifs"),
    _x("bmv", "BMV (Bolsa Mexicana de Valores)", AMERIQUES, ("MX",), "Mexico", "MXN", "America/Mexico_City",
       (("08:30", "15:00"),), "CNBV", "https://www.bmv.com.mx", (("S&P/BMV IPC", "mx_ipc"),)),
    # ---------- Europe ----------
    _x("euronext", "Euronext", EUROPE, ("FR", "NL", "BE", "PT", "IE", "IT", "NO"),
       "Paris, Amsterdam, Bruxelles, Lisbonne, Dublin, Milan, Oslo", "EUR", "Europe/Paris", (("09:00", "17:30"),),
       "AMF (France), AFM (Pays-Bas), FSMA (Belgique), CMVM (Portugal), Banque centrale d'Irlande, CONSOB (Italie), "
       "Finanstilsynet (Norvège)", "https://www.euronext.com",
       (("CAC 40", "cac40"), ("AEX", "nl_aex"), ("BEL 20", "be_bel20"), ("FTSE MIB", "ftsemib"), ("PSI", "pt_psi"),
        ("ISEQ", "ie_iseq"), ("OBX", "no_obx")),
       hours_note="Oslo : 9 h - 16 h 20 (heure locale)", note="bourses de sept pays européens réunies"),
    _x("lse", "London Stock Exchange (LSE)", EUROPE, ("GB",), "Londres", "GBP", "Europe/London",
       (("08:00", "16:30"),), "FCA", "https://www.londonstockexchange.com", (("FTSE 100", "ftse"),)),
    _x("xetra", "Deutsche Börse (Xetra, Francfort)", EUROPE, ("DE",), "Francfort", "EUR", "Europe/Berlin",
       (("09:00", "17:30"),), "BaFin", "https://www.deutsche-boerse.com", (("DAX 40", "dax"),)),
    _x("six", "SIX Swiss Exchange", EUROPE, ("CH",), "Zurich", "CHF", "Europe/Zurich", (("09:00", "17:30"),),
       "FINMA", "https://www.six-group.com", (("SMI", "ch_smi"),)),
    _x("bme", "Bolsa de Madrid (BME, groupe SIX)", EUROPE, ("ES",), "Madrid", "EUR", "Europe/Madrid",
       (("09:00", "17:30"),), "CNMV", "https://www.bolsasymercados.es", (("IBEX 35", "ibex"),)),
    # ---------- Asie et Moyen-Orient ----------
    _x("jpx", "Bourse de Tokyo (Japan Exchange Group)", ASIE, ("JP",), "Tokyo", "JPY", "Asia/Tokyo",
       (("09:00", "11:30"), ("12:30", "15:30")), "FSA (Japon)", "https://www.jpx.co.jp",
       (("Nikkei 225", "nikkei"), ("TOPIX", ""))),
    _x("sse", "Bourse de Shanghai (SSE)", ASIE, ("CN",), "Shanghai", "CNY", "Asia/Shanghai",
       (("09:30", "11:30"), ("13:00", "15:00")), "CSRC", "https://www.sse.com.cn",
       (("SSE Composite", "shanghai"), ("CSI 300", "")),
       access="particuliers étrangers : en général par Stock Connect (courtier de Hong Kong) ; à vérifier"),
    _x("szse", "Bourse de Shenzhen (SZSE)", ASIE, ("CN",), "Shenzhen", "CNY", "Asia/Shanghai",
       (("09:30", "11:30"), ("13:00", "15:00")), "CSRC", "https://www.szse.cn", (("SZSE Component", "cn_szse"),),
       access="particuliers étrangers : en général par Stock Connect (courtier de Hong Kong) ; à vérifier"),
    _x("hkex", "HKEX (Bourse de Hong Kong)", ASIE, ("HK",), "Hong Kong", "HKD", "Asia/Hong_Kong",
       (("09:30", "12:00"), ("13:00", "16:00")), "SFC", "https://www.hkex.com.hk", (("Hang Seng", "hangseng"),)),
    _x("nse_in", "NSE (National Stock Exchange of India)", ASIE, ("IN",), "Bombay", "INR", "Asia/Kolkata",
       (("09:15", "15:30"),), "SEBI", "https://www.nseindia.com", (("Nifty 50", "nifty"),),
       access="particuliers étrangers : accès direct restreint ; souvent par des fonds cotés ailleurs"),
    _x("bse_in", "BSE (Bombay Stock Exchange)", ASIE, ("IN",), "Bombay", "INR", "Asia/Kolkata",
       (("09:15", "15:30"),), "SEBI", "https://www.bseindia.com", (("Sensex", "in_sensex"),),
       access="particuliers étrangers : accès direct restreint ; souvent par des fonds cotés ailleurs"),
    _x("krx", "KRX (Korea Exchange)", ASIE, ("KR",), "Séoul et Busan", "KRW", "Asia/Seoul", (("09:00", "15:30"),),
       "FSC et FSS", "https://global.krx.co.kr", (("KOSPI", "kr_kospi"),)),
    _x("tadawul", "Saudi Exchange (Tadawul)", ASIE, ("SA",), "Riyad", "SAR", "Asia/Riyadh", (("10:00", "15:00"),),
       "CMA", "https://www.saudiexchange.sa", (("TASI", ""), ("Fonds MSCI Arabie saoudite (KSA, en $)", "sa_ksa")),
       days=GULF, hours_note="du dimanche au jeudi ; le TASI n'a pas d'historique journalier chez Yahoo"),
    # ---------- Océanie ----------
    _x("asx", "ASX (Australian Securities Exchange)", OCEANIE, ("AU",), "Sydney", "AUD", "Australia/Sydney",
       (("10:00", "16:00"),), "ASIC", "https://www.asx.com.au", (("S&P/ASX 200", "au_asx200"),)),
)
# Pays ou zone de chaque place, en toutes lettres.
ZONES = {
    "brvm": "UEMOA : Bénin, Burkina Faso, Côte d'Ivoire, Guinée-Bissau, Mali, Niger, Sénégal, Togo",
    "bvmac": "CEMAC : Cameroun, Centrafrique, Congo, Gabon, Guinée équatoriale, Tchad",
    "jse": "Afrique du Sud", "ngx": "Nigeria", "casablanca": "Maroc", "egx": "Égypte", "nse_ke": "Kenya",
    "gse": "Ghana", "nyse": "États-Unis", "nasdaq": "États-Unis", "cboe": "États-Unis", "tsx": "Canada",
    "b3": "Brésil", "bmv": "Mexique", "euronext": "France, Pays-Bas, Belgique, Portugal, Irlande, Italie, Norvège",
    "lse": "Royaume-Uni", "xetra": "Allemagne", "six": "Suisse", "bme": "Espagne", "jpx": "Japon", "sse": "Chine",
    "szse": "Chine", "hkex": "Hong Kong", "nse_in": "Inde", "bse_in": "Inde", "krx": "Corée du Sud",
    "tadawul": "Arabie saoudite", "asx": "Australie"}
for _ex in EXCHANGES:
    _ex["zone"] = ZONES[_ex["id"]]
BY_ID = {x["id"]: x for x in EXCHANGES}
DAYS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

# Investir depuis la Côte d'Ivoire : (sujet, texte, source). D'après le texte
# fourni par l'utilisateur le 25/11/2026, corrigé et sourcé là où c'était
# possible ; le reste est marqué « à vérifier ».
GUIDE: Tuple[Tuple[str, str, str], ...] = (
    ("BRVM (UEMOA)", "Par une SGI agréée par l'AMF-UMOA : compte-titres en FCFA, sans change.", "BRVM"),
    ("Autres bourses", "Par un courtier international régulé (SEC et FINRA aux États-Unis, FCA au Royaume-Uni, "
     "CySEC dans l'Union européenne…) qui accepte les résidents ivoiriens ; les SGI de la BRVM n'y donnent pas "
     "accès. Méfiance envers les courtiers non régulés et les offres limitées aux CFD (produits à effet de "
     "levier).", "texte fourni"),
    ("Courtiers cités", "Interactive Brokers, XTB, TradeStation Global, Charles Schwab : leur accueil des "
     "résidents ivoiriens, leurs frais et leur dépôt minimum sont à vérifier sur leur site.", "à vérifier"),
    ("Ouverture du compte", "En ligne, souvent en 24 à 72 heures : passeport (une carte d'identité est parfois "
     "refusée par les contrôles automatiques), justificatif de domicile de moins de 3 mois, et pour les actions "
     "américaines le formulaire W-8BEN, qui atteste que vous n'êtes pas résident fiscal des États-Unis.",
     "texte fourni"),
    ("Envoi des fonds", "Virement international (SWIFT) en dollars ou en euros depuis une banque locale : la "
     "réglementation des changes de la BCEAO demande l'origine des fonds et le motif du transfert ; chaque "
     "virement coûte des frais fixes, d'où l'intérêt de virements peu fréquents. Certains courtiers acceptent "
     "une carte bancaire internationale (frais de change de la banque).",
     "texte fourni ; frais à vérifier auprès de votre banque"),
    ("Impôts", "Les États-Unis ne taxent pas en général les plus-values d'actions des non-résidents ; leurs "
     "dividendes subissent une retenue à la source de 30 % par défaut, faute de convention fiscale entre la "
     "Côte d'Ivoire et les États-Unis. Les gains se déclarent en Côte d'Ivoire.",
     "texte fourni ; à vérifier avec un conseiller fiscal"),
    ("Le bot", "Il n'ouvre aucun compte et ne passe aucun ordre sur ces bourses : il en lit les indices et les "
     "cours pour son savoir. Ses comptes de démonstration sont dans la page Courtiers.", "TrendGuard"),
)


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _en(text: str) -> Optional[float]:
    """Nombre écrit à l'anglaise : « 4,122.09 », « -6.35 »."""
    return _num(re.sub(r"[,\s  ]", "", text or ""))


# ══════════════════════════════════════════════════════════════════════
# HEURES DE SÉANCE
# ══════════════════════════════════════════════════════════════════════

def _at(day: date, hm: str, tz: ZoneInfo) -> datetime:
    h, m = (int(x) for x in hm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz)


def hm(text: str) -> str:
    """« 09:00 » → « 9 h » ; « 09:30 » → « 9 h 30 »."""
    h, m = text.split(":")
    return f"{int(h)} h" + (f" {m}" if m != "00" else "")


def _abidjan(t: datetime) -> str:
    return hm(t.astimezone(timezone.utc).strftime("%H:%M"))


def status(ex: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """La place est-elle en séance ? Heure locale, prochaine ouverture ou
    clôture, en heure locale et en heure d'Abidjan (UTC). Les jours fériés ne
    sont pas connus du bot."""
    tz = ZoneInfo(ex["tz"])
    local = now.astimezone(tz)
    out: Dict[str, Any] = {"local": local.strftime("%H:%M"), "open": None, "next": ""}
    if not ex["sessions"]:
        out["next"] = "heures de séance non lues"
        return out
    if local.weekday() in ex["days"]:
        for a, b in ex["sessions"]:
            start, end = _at(local.date(), a, tz), _at(local.date(), b, tz)
            if start <= local < end:
                out.update(open=True, next=f"ferme à {hm(b)} ({_abidjan(end)} à Abidjan)")
                return out
    for k in range(8):
        day = local.date() + timedelta(days=k)
        if day.weekday() not in ex["days"]:
            continue
        for a, _b in ex["sessions"]:
            start = _at(day, a, tz)
            if start > local:
                when = "aujourd'hui" if k == 0 else "demain" if k == 1 else DAYS_FR[day.weekday()]
                out.update(open=False, next=f"ouvre {when} à {hm(a)} ({_abidjan(start)} à Abidjan)")
                return out
    out["open"] = False
    return out


# ══════════════════════════════════════════════════════════════════════
# PAGES OFFICIELLES (comme la BRVM) : Nigeria, Kenya, Maroc, Afrique centrale
# ══════════════════════════════════════════════════════════════════════

NGX_EQUITIES = ("https://doclib.ngxgroup.com/REST/api/statistics/equities/"
                "?market=&sector=&orderby=&pageSize=300&pageNo=0")
NGX_SNAPSHOT = "https://doclib.ngxgroup.com/REST/api/mrkstat/mrksnapshot"
NSE_KE_URL = "https://www.nse.co.ke/dataservices/market-statistics/"
CASABLANCA_URL = "https://www.casablanca-bourse.com/"
BVMAC_PDF = "https://www.bvm-ac.org/wp-content/uploads/{folder}/BOC-{day}.pdf"
NSE_KE_INDICES = {"NSE ALL SHARE INDEX": "NSE All Share (NASI)", "NSE 20 SHARE INDEX": "NSE 20",
                  "NSE 25 SHARE INDEX": "NSE 25", "NSE 10 SHARE INDEX": "NSE 10",
                  "BANKING SECTOR INDEX": "Indice des banques"}
NSE_KE_ACTIVITY = {"MARKET CAPITALIZATION (BILLIONS)": "cap_bn", "TOTAL SHARE TRADED": "volume",
                   "EQUITY TURNOVER": "value", "TOTAL EQUITY DEALS": "deals"}
CASA_BLOCK = re.compile(r'class="block-title">\s*<a[^>]*>([^<]+)</a>\s*</div>\s*<div class="variation-value[^"]*">'
                        r'(.*?)</div>.*?<div class="main-value[^"]*">([^<]+)</div>', re.S)
BOC_HEAD = re.compile(r"BULLETIN OFFICIEL DE LA COTE N°\s*(\d+)\s+DU\s+(\d{2})/(\d{2})/(\d{4})")
BOC_INDEX = re.compile(r"BVMAC ALL SHARE INDEX\s+([\d  ]+,\d+)\s+([−+-]?\d+,\d+)\s*%")
BOC_STOCK = re.compile(r"^\s*(?P<name>\S.*?)\s+(?P<isin>[A-Z]{2}000001\d{4})\s+(?P<mnemo>\S+)\s+(?P<short>\S.*?)\s+"
                       r"(?P<price>\d{1,3}(?:[  ]\d{3})*,\d{2})\s+\d", re.M)


def parse_ngx(equities: Any, snapshot: Any) -> Dict[str, Any]:
    """NGX : la liste des cours de son site (JSON) et son instantané du marché
    (indice All-Share, échanges, capitalisation). La séance est celle des
    cours eux-mêmes (TradeDate). Pendant la séance, la liste du jour n'a pas
    encore de cours de clôture : « pending »."""
    stocks, days = [], set()
    rows = equities if isinstance(equities, list) else []
    for e in rows:
        sym, close, prev = str(e.get("Symbol") or "").strip(), _num(e.get("ClosePrice")), _num(e.get("PrevClosingPrice"))
        if not sym or close is None:
            continue
        pct = _num(e.get("PercChange"))
        if pct is None and prev:
            pct = (close / prev - 1) * 100
        stocks.append({"symbol": sym, "name": sym, "close": close, "prev": prev, "change": pct,
                       "volume": _num(e.get("Volume")), "sector": str(e.get("Sector") or "").capitalize()})
        if e.get("TradeDate"):
            days.add(str(e["TradeDate"])[:10])
    snap = snapshot if isinstance(snapshot, dict) else {}
    asi = _num(snap.get("ASI"))
    return {"session": max(days) if days else None, "stocks": stocks, "pending": bool(rows) and not stocks,
            "indices": [{"name": "NGX All-Share (ASI)", "close": asi, "change": None}] if asi else [],
            "activity": {"deals": _num(snap.get("DEALS")), "volume": _num(snap.get("VOLUME")),
                         "value": _num(snap.get("VALUE")), "cap": _num(snap.get("CAP"))}}


def parse_nse_ke(page: str) -> Dict[str, Any]:
    """NSE (Nairobi) : indices du jour (valeur, variation en points) et
    activité du marché, tels que la page des statistiques les affiche."""
    indices, activity = [], {}
    for cells in brvm.table_rows(page):
        if len(cells) != 3:
            continue
        key, value, points = cells[0].strip().upper(), _en(cells[1]), _en(cells[2])
        if key in NSE_KE_INDICES and value:
            base = value - (points or 0.0)
            indices.append({"name": NSE_KE_INDICES[key], "close": value,
                            "change": points / base * 100 if points is not None and base else None})
        elif key in NSE_KE_ACTIVITY and value is not None:
            activity[NSE_KE_ACTIVITY[key]] = value
    return {"session": None, "indices": indices, "stocks": [], "activity": activity}


def parse_casablanca(page: str) -> Dict[str, Any]:
    """Bourse de Casablanca : MASI et MASI 20 (valeur, variation du jour), et
    les plus fortes hausses et baisses de la page d'accueil."""
    indices = []
    for name, var, value in CASA_BLOCK.findall(page):
        v, c = brvm.number(value), brvm.number(re.sub(r"<[^>]+>", " ", var))
        if v:
            indices.append({"name": html.unescape(name).strip(), "close": v, "change": c})
    seen: Dict[str, Dict[str, Any]] = {}
    for table in re.findall(r"<table.*?</table>", page, re.S | re.I):
        rows = brvm.table_rows(table)
        if not rows or len(rows[0]) != 3 or not rows[0][1].lower().startswith("cours"):
            continue                                    # tableau des volumes ou des contrats à terme
        for cells in rows[1:]:
            close, change = brvm.number(cells[1]) if len(cells) == 3 else None, brvm.number(cells[-1])
            if close is not None and change is not None and "FUTURE" not in cells[0].upper():
                seen.setdefault(cells[0].title(), {"name": cells[0].title(), "close": close, "change": change})
    return {"session": None, "indices": indices, "activity": {},
            "stocks": sorted(seen.values(), key=lambda m: -m["change"])}


def pdf_text(raw: bytes, pages: int = 10) -> str:
    """Texte des premières pages d'un PDF (bibliothèque libre pypdf)."""
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(raw))
    return "\n".join((p.extract_text() or "") for p in reader.pages[:pages])


def parse_bvmac(text: str) -> Dict[str, Any]:
    """Bulletin officiel de la cote de la BVMAC : numéro et date, indice BVMAC
    All Share (valeur, variation du jour), cours de chaque action (tableau
    des capitalisations)."""
    head, idx = BOC_HEAD.search(text), BOC_INDEX.search(text)
    stocks: Dict[str, Dict[str, Any]] = {}
    for m in BOC_STOCK.finditer(text):
        stocks.setdefault(m["isin"], {"symbol": m["mnemo"], "name": m["short"].strip(), "isin": m["isin"],
                                      "close": brvm.number(m["price"]), "change": None})
    return {"session": f"{head[4]}-{head[3]}-{head[2]}" if head else None, "bulletin": int(head[1]) if head else None,
            "indices": [{"name": "BVMAC All Share", "close": brvm.number(idx[1]), "change": brvm.number(idx[2])}]
            if idx else [], "stocks": list(stocks.values()), "activity": {}}


def session_day(now: datetime) -> str:
    """La séance que montrent les pages : aujourd'hui après 17:00 UTC un jour
    de semaine, sinon le dernier jour de semaine passé."""
    day = now if (now.hour, now.minute) >= READ_AFTER else now - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.date().isoformat()


def due(part: Any, now: datetime) -> bool:
    """Une lecture par séance, après 17:00 UTC ; trois heures après une
    lecture ratée."""
    if not isinstance(part, dict) or not part.get("tried_at"):
        return True
    try:
        when = datetime.fromisoformat(str(part["tried_at"]))
    except ValueError:
        return True
    if part.get("error"):
        return (now - when).total_seconds() >= 3600 * RETRY_HOURS
    return session_day(now) != part.get("read_for")


def _fingerprint(got: Dict[str, Any]) -> str:
    raw = ";".join(f"{r['name']}:{r['close']}" for r in got["indices"]) + "|" + ";".join(
        f"{s['symbol'] if 'symbol' in s else s['name']}:{s['close']}" for s in got["stocks"])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _read_bvmac(fetch: Fetch, book: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """Le dernier bulletin de la BVMAC pas encore lu (5 jours ouvrés au plus) :
    son adresse se déduit de sa date (dossier du mois, ou du mois suivant
    quand il est mis en ligne le mois d'après). Un seul PDF lu par séance."""
    from .market_watch import HttpError
    last = book.get("session") or ""
    day, tried = datetime.fromisoformat(session_day(now)).date(), 0
    while tried < 5:
        if day.weekday() < 5:
            tried += 1
            if day.isoformat() <= last:
                break
            nxt = (day.replace(day=1) + timedelta(days=32)).replace(day=1)
            for folder in (f"{day:%Y/%m}", f"{nxt:%Y/%m}"):
                try:
                    return parse_bvmac(pdf_text(fetch(BVMAC_PDF.format(folder=folder, day=f"{day:%Y%m%d}"))))
                except HttpError as e:
                    if e.status != 404:
                        raise
        day -= timedelta(days=1)
    return {"session": None, "indices": [], "stocks": [], "activity": {}}


def read(source: str, fetch: Fetch, book: Dict[str, Any], now: datetime, pause: float = PAUSE) -> Dict[str, Any]:
    """Une lecture de la page officielle `source`."""
    if source == "ngx":
        eq = json.loads(fetch(NGX_EQUITIES))
        if pause:
            time.sleep(pause)
        return parse_ngx(eq, json.loads(fetch(NGX_SNAPSHOT)))
    if source == "nse_ke":
        return parse_nse_ke(fetch(NSE_KE_URL).decode("utf-8", "replace"))
    if source == "casablanca":
        return parse_casablanca(fetch(CASABLANCA_URL).decode("utf-8", "replace"))
    if source == "bvmac":
        return _read_bvmac(fetch, book, now)
    raise ValueError(f"source inconnue : {source}")


OFFICIAL = ("ngx", "nse_ke", "casablanca", "bvmac")


def read_official(memory_book: Dict[str, Any], source: str, fetch: Fetch, now: datetime, cache: str,
                  force: bool = False, pause: float = PAUSE) -> Dict[str, Any]:
    """Une page officielle, une fois par séance : la séance lue entre dans
    l'historique du bot (data_bourses/), sauf si rien n'a changé depuis la
    dernière lecture (jour férié). La variation d'un indice que la page ne
    donne pas se calcule sur la séance précédente gardée."""
    from .market_watch import friendly_error
    book = dict(memory_book.get(source) or {})
    if not (force or due(book, now)):
        return book
    book["tried_at"] = now.isoformat(timespec="seconds")
    try:
        got = read(source, fetch, book, now, pause)
        if got.get("pending"):                                     # NGX en pleine séance
            raise ValueError("séance en cours : cours de clôture pas encore publiés")
        if not got["indices"] and not got["stocks"]:
            if source == "bvmac" and book.get("session"):          # pas de nouveau bulletin
                book.update(error="", read_for=session_day(now))
                return book
            raise ValueError("page illisible (présentation changée ?)")
        day = got.get("session") or session_day(now)
        sign = _fingerprint(got)
        fresh = sign != book.get("fingerprint") and day != book.get("session")
        if fresh:
            os.makedirs(cache, exist_ok=True)
            for r in got["indices"]:
                code = f"{source}_idx_{brvm.slug(r['name'])}"
                if r.get("change") is None:
                    prev = brvm.history(cache, code)
                    if not prev.empty and r["close"]:
                        r["change"] = (r["close"] / float(prev.iloc[-1]) - 1) * 100
                brvm.append_session(cache, code, day, r["close"])
            for s in got["stocks"]:
                if "symbol" in s:
                    brvm.append_session(cache, f"{source}_{brvm.slug(s['symbol'])}", day, s["close"], s.get("volume"))
            book["sessions"] = int(book.get("sessions") or 0) + 1
        book.update(got, fetched_at=book["tried_at"], error="", read_for=session_day(now), fingerprint=sign,
                    session=day if fresh else book.get("session", day))
    except Exception as e:
        book["error"] = friendly_error(e)
    return book


# ══════════════════════════════════════════════════════════════════════
# LA RÈGLE SUR CHAQUE PLACE (protocole de l'étude des pays)
# ══════════════════════════════════════════════════════════════════════

def index_codes(ex: Dict[str, Any]) -> List[str]:
    """Codes multi_actifs des indices de la place lus chez Yahoo, le principal
    d'abord (le VIX n'est pas un indice d'actions)."""
    return [c for _n, c in ex["indices"] if c and c != "vix"]


def study(close: pd.DataFrame, code: str, end: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """La règle du bot (paramètres gelés, coût réel des indices) sur un indice,
    face à l'achat-conservation : rendement annuel, baisse maximale et Calmar,
    sur 2018-2022 puis depuis 2023. Elle « tient » si son Calmar est meilleur
    sur les deux périodes. None sans historique depuis 2018."""
    if code not in close.columns:
        return None
    s = close[code].dropna()
    if s.empty or s.index[0] > pd.Timestamp("2017-12-31", tz="UTC"):
        return None
    end = end or str(close.index[-1].date())
    out: Dict[str, Any] = {}
    for label, (a, b) in (("design", DESIGN), ("verify", (VERIFY_START, end))):
        rule = ma.backtest(close[[code]], a, b, real_costs=True).metrics
        hold = ts.buy_and_hold(close[[code]], [code], a, b)
        out[label] = {"rule_cagr": rule.get("cagr_pct", 0.0), "rule_dd": rule.get("max_dd_pct", 0.0),
                      "rule_calmar": rule.get("calmar", 0.0), "trades": int(rule.get("trades", 0)),
                      "hold_cagr": hold.get("cagr_pct", 0.0), "hold_dd": hold.get("max_dd_pct", 0.0),
                      "hold_calmar": hold.get("calmar", 0.0)}
    out["holds"] = all(out[k]["rule_calmar"] > out[k]["hold_calmar"] for k in ("design", "verify"))
    out["end"] = end
    return out


def study_all(load: Callable[[str], pd.DataFrame], end: Optional[str] = None) -> Dict[str, Any]:
    """L'étude sur l'indice principal de chaque place lue chez Yahoo. Chaque
    indice est chargé seul (`load`), sur son propre calendrier de séances :
    le résultat ne dépend pas des autres marchés chargés avec lui."""
    rows = {}
    for ex in EXCHANGES:
        for code in index_codes(ex):                 # le premier indice qui a son historique depuis 2018
            got = study(load(code), code, end)
            if got:
                rows[ex["id"]] = dict(got, code=code)
                break
    return rows


# ══════════════════════════════════════════════════════════════════════
# SUIVI QUOTIDIEN, PANNEAU, ASSISTANTE
# ══════════════════════════════════════════════════════════════════════

def refresh(memory: Any, now: datetime, fetch: Fetch, cache: str = CACHE, multi_cache: str = "",
            force: bool = False, pause: float = PAUSE, sources: Sequence[str] = OFFICIAL) -> Dict[str, Any]:
    """Pages officielles une fois par séance, après 17:00 UTC ; étude de la
    règle sur chaque place une fois par lecture des marchés (multi_actifs).
    Une panne est notée, jamais bloquante : rien de ce qui a été lu n'est
    perdu."""
    book = memory.get(KEY)
    book = dict(book) if isinstance(book, dict) else {}
    official = dict(book.get("official") or {})
    for src in sources:
        official[src] = read_official(official, src, fetch, now, cache, force, pause)
    book["official"] = official
    mbook = memory.get(ma.KEY)
    stamp = (mbook or {}).get("fetched_at") if isinstance(mbook, dict) else None
    if multi_cache and stamp and stamp != (book.get("study") or {}).get("for"):
        rows = study_all(lambda code: ma.load(multi_cache, [code]))
        if rows:
            book["study"] = {"for": stamp, "rows": rows}
    memory.put(KEY, book)
    return book


def _index_rows(ex: Dict[str, Any], close: pd.DataFrame, summary: Dict[str, Dict[str, Any]],
                off: Dict[str, Any], cache: str, brvm_cache: str) -> List[Dict[str, Any]]:
    out = []
    for name, code in ex["indices"]:
        s = ma.sessions(close[code]) if code and code in close.columns else pd.Series(dtype=float)
        if len(s) >= 2:                                 # deux séances réelles au moins : une variation
            last, prev = float(s.iloc[-1]), float(s.iloc[-2])
            out.append({"name": name, "code": code, "close": last, "change": (last / prev - 1) * 100,
                        "date": str(s.index[-1].date()), "trend": (summary.get(code) or {}).get("trend"),
                        "signal": (summary.get(code) or {}).get("signal"), "spark": ma.spark(close[code], "1y", 60),
                        "source": "Yahoo Finance"})
            continue
        row = next((r for r in off.get("indices") or [] if brvm.slug(r["name"]) == brvm.slug(name)), None)
        if row:
            hist = (brvm.history(brvm_cache, "idx_" + brvm.slug(row["name"])) if ex["official"] == "brvm"
                    else brvm.history(cache, f"{ex['official']}_idx_{brvm.slug(row['name'])}"))
            out.append({"name": row["name"], "code": "", "close": row.get("close"), "change": row.get("change"),
                        "date": off.get("session"), "trend": None, "signal": None,
                        "spark": [round(float(v), 4) for v in hist.iloc[-60:]], "source": "page officielle"})
        else:
            out.append({"name": name, "code": "", "close": None, "change": None, "date": None, "trend": None,
                        "signal": None, "spark": [], "source": "non lu"})
    return out


def view(book: Any, close: pd.DataFrame, multi_book: Any, now: datetime, brvm_book: Any = None,
         cache: str = CACHE, brvm_cache: str = BRVM_CACHE) -> Dict[str, Any]:
    """Page « Bourses du monde » : chaque place, son état (en séance ou non),
    ses indices et leur tendance pour la règle, son régulateur, son accès
    depuis la Côte d'Ivoire, ce que le bot en lit et l'étude de la règle."""
    book = book if isinstance(book, dict) else {}
    official = dict(book.get("official") or {})
    if isinstance(brvm_book, dict):
        official["brvm"] = brvm_book
    summary = {r["code"]: r for r in ((multi_book or {}).get("rows") or []) if isinstance(r, dict)} \
        if isinstance(multi_book, dict) else {}
    studies = (book.get("study") or {}).get("rows") or {}
    rows = []
    for ex in EXCHANGES:
        off = official.get(ex["official"]) or {} if ex["official"] else {}
        rows.append({k: v for k, v in ex.items() if k not in ("sessions", "days", "tz")} | {
            "hours": " et ".join(f"{hm(a)} - {hm(b)}" for a, b in ex["sessions"]),
            "days_text": "du dimanche au jeudi" if ex["days"] == GULF else "du lundi au vendredi",
            "status": status(ex, now), "indices": _index_rows(ex, close, summary, off, cache, brvm_cache),
            "reads": "page officielle, après chaque séance" if ex["official"] else
                     "indices chez Yahoo Finance, chaque jour" if any(c for _n, c in ex["indices"]) else "rien",
            "official_state": {"session": off.get("session"), "error": off.get("error") or "",
                               "sessions": int(off.get("sessions") or 0), "stocks": len(off.get("stocks") or []),
                               "movers": sorted((s for s in off.get("stocks") or [] if s.get("change") is not None),
                                                key=lambda s: -abs(s["change"]))[:3]} if ex["official"] else None,
            "study": studies.get(ex["id"])})
    return {"regions": list(REGIONS), "rows": rows, "guide": [{"topic": t, "text": x, "source": s} for t, x, s in GUIDE],
            "now": now.isoformat(timespec="seconds"), "study_for": (book.get("study") or {}).get("for")}


def render(v: Dict[str, Any]) -> str:
    """État en texte : chaque place, sa séance, ses indices, ses lectures."""
    lines = ["Bourses du monde : structure de cotation de chaque place (information seulement)"]
    for region in v["regions"]:
        lines += ["", region]
        for r in (x for x in v["rows"] if x["region"] == region):
            st = r["status"]
            state = "en séance" if st.get("open") else "fermée" if st.get("open") is False else "heures non lues"
            lines.append(f"  {r['name']:<58} {state} ({st.get('next') or '–'})")
            for i in r["indices"]:
                if i["close"] is not None:
                    lines.append(f"      {i['name']:<30} {fr(i['close'], ',.2f'):>14}"
                                 + (f"  {fr(i['change'], '+.2f')} %" if i.get("change") is not None else ""))
            o = r.get("official_state")
            if o:
                when = "/".join(reversed(o["session"].split("-"))) if o["session"] else "–"
                lines.append(f"      page officielle : séance du {when}, {o['stocks']} titres, "
                             f"historique de {o['sessions']} séance(s)" + (f" ; lecture ratée : {o['error']}"
                                                                           if o["error"] else ""))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Ligne de commande : état des places (défaut) ou lecture des pages
    officielles tout de suite (lire)."""
    v29.ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Bourses du monde (information seulement)")
    ap.add_argument("action", nargs="?", default="etat", choices=["etat", "lire"])
    args = ap.parse_args(argv)
    from . import savoir
    from .config import load_guard_config_from_env
    gcfg = load_guard_config_from_env()
    now = datetime.now(timezone.utc)
    memory = savoir.Memory(gcfg.savoir_db or savoir.DEFAULT_DB)
    multi_cache = os.path.join(v29.APP_DIR, "data_multi")
    try:
        book = refresh(memory, now, lambda url: savoir.http_get(url, 120), CACHE, multi_cache,
                       force=args.action == "lire") if args.action == "lire" else memory.get(KEY)
        codes = list(dict.fromkeys(c for x in EXCHANGES for _n, c in x["indices"] if c))
        print(render(view(book, ma.load(multi_cache, codes), memory.get(ma.KEY), now, memory.get(brvm.KEY))))
    finally:
        memory.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
