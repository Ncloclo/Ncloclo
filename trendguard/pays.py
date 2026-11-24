"""
Les pays du monde et leurs marchés (docs/PAYS.md) : chaque pays couvert par
MSCI, classé par région et par niveau de développement de son marché, avec
son principal indice boursier, sa devise face au dollar et les indicateurs
de la Banque mondiale.

Sources, toutes gratuites :
  - classement MSCI, revue d'accessibilité des marchés 2026 (annexe II,
    juin 2026) et résultats de la revue de classement 2026 (23/06/2026) :
    développé, émergent, frontière (dont « avancé »), autonome ; les
    changements annoncés sont notés ;
  - Banque mondiale (API ouverte, sans clé) : région, niveau de revenu,
    capitale, et dernière valeur connue de la croissance, de l'inflation, du
    chômage, du PIB, de la dette publique et du solde courant ; relue chaque
    semaine par le noyau de savoir ;
  - Yahoo Finance : indices et devises, lus chaque jour avec les autres
    marchés (multi_actifs).
Information seulement : aucun ordre réel, aucune décision du bot n'en dépend.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, Tuple

import pandas as pd

from . import multi_actifs as ma

KEY = "pays"                        # méta du noyau de savoir
REFRESH_DAYS = 7                    # la Banque mondiale publie une fois par an : une lecture par semaine
RETRY_HOURS = 6
MAX_AGE_YEARS = 5                   # un chiffre plus ancien n'est pas montré comme actuel
WB_COUNTRIES = "https://api.worldbank.org/v2/country?format=json&per_page=400"
WB_INDICATOR = "https://api.worldbank.org/v2/country/all/indicator/{ind}?format=json&mrnev=1&per_page=400"
# (clé, code de la Banque mondiale, libellé)
INDICATORS: Tuple[Tuple[str, str, str], ...] = (
    ("growth", "NY.GDP.MKTP.KD.ZG", "croissance du PIB"),
    ("inflation", "FP.CPI.TOTL.ZG", "inflation"),
    ("unemployment", "SL.UEM.TOTL.ZS", "chômage"),
    ("gdp", "NY.GDP.MKTP.CD", "PIB"),
    ("debt", "GC.DOD.TOTL.GD.ZS", "dette de l'État"),
    ("current", "BN.CAB.XOKA.GD.ZS", "solde courant"),
)
INCOME_FR = {"High income": "revenu élevé", "Upper middle income": "revenu intermédiaire supérieur",
             "Lower middle income": "revenu intermédiaire inférieur", "Low income": "faible revenu",
             "Not classified": "non classé"}
REGIONS = ("Amérique du Nord", "Amérique latine et Caraïbes", "Europe", "Moyen-Orient", "Afrique", "Asie", "Océanie")
CLASSES = ("Développé", "Émergent", "Frontière avancé", "Frontière", "Autonome")
_N, _L, _E, _M, _A, _S, _O = REGIONS
_D, _EM, _FA, _F, _AU = CLASSES
UEMOA = "UEMOA : un seul marché pour MSCI (bourse régionale BRVM, à Abidjan)"
# (ISO 2, pays, région, classement MSCI, changement annoncé ou remarque,
#  indice (code multi_actifs, vide si Yahoo n'en a pas l'historique), devise)
COUNTRIES: Tuple[Tuple[str, str, str, str, str, str, str], ...] = (
    ("US", "États-Unis", _N, _D, "", "sp500", "USD"),
    ("CA", "Canada", _N, _D, "", "ca_tsx", "CAD"),
    ("AT", "Autriche", _E, _D, "", "at_atx", "EUR"),
    ("BE", "Belgique", _E, _D, "", "be_bel20", "EUR"),
    ("DK", "Danemark", _E, _D, "", "dk_omxc25", "DKK"),
    ("FI", "Finlande", _E, _D, "", "fi_omxh25", "EUR"),
    ("FR", "France", _E, _D, "", "cac40", "EUR"),
    ("DE", "Allemagne", _E, _D, "", "dax", "EUR"),
    ("IE", "Irlande", _E, _D, "", "ie_iseq", "EUR"),
    ("IL", "Israël", _M, _D, "", "il_ta35", "ILS"),
    ("IT", "Italie", _E, _D, "", "ftsemib", "EUR"),
    ("NL", "Pays-Bas", _E, _D, "", "nl_aex", "EUR"),
    ("NO", "Norvège", _E, _D, "", "no_obx", "NOK"),
    ("PT", "Portugal", _E, _D, "", "pt_psi", "EUR"),
    ("ES", "Espagne", _E, _D, "", "ibex", "EUR"),
    ("SE", "Suède", _E, _D, "", "se_omxs30", "SEK"),
    ("CH", "Suisse", _E, _D, "", "ch_smi", "CHF"),
    ("GB", "Royaume-Uni", _E, _D, "", "ftse", "GBP"),
    ("AU", "Australie", _O, _D, "", "au_asx200", "AUD"),
    ("HK", "Hong Kong", _S, _D, "", "hangseng", "HKD"),
    ("JP", "Japon", _S, _D, "", "nikkei", "JPY"),
    ("NZ", "Nouvelle-Zélande", _O, _D, "", "nz_nzx50", "NZD"),
    ("SG", "Singapour", _S, _D, "", "sg_sti", "SGD"),
    ("BR", "Brésil", _L, _EM, "", "bovespa", "BRL"),
    ("CL", "Chili", _L, _EM, "", "cl_ech", "CLP"),
    ("CO", "Colombie", _L, _EM, "", "co_colcap", "COP"),
    ("MX", "Mexique", _L, _EM, "", "mx_ipc", "MXN"),
    ("PE", "Pérou", _L, _EM, "", "pe_epu", "PEN"),
    ("CZ", "Tchéquie", _E, _EM, "", "", "CZK"),
    ("EG", "Égypte", _A, _EM, "", "", "EGP"),
    ("GR", "Grèce", _E, _EM, "passe en marché développé à la revue de mai 2027", "gr_athex", "EUR"),
    ("HU", "Hongrie", _E, _EM, "", "", "HUF"),
    ("KW", "Koweït", _M, _EM, "", "kw_kwt", "KWD"),
    ("PL", "Pologne", _E, _EM, "", "pl_epol", "PLN"),
    ("QA", "Qatar", _M, _EM, "", "qa_qat", "QAR"),
    ("SA", "Arabie saoudite", _M, _EM, "", "sa_ksa", "SAR"),
    ("ZA", "Afrique du Sud", _A, _EM, "", "za_jse", "ZAR"),
    ("TR", "Turquie", _E, _EM, "sous surveillance (flottant, échanges coordonnés) : revue de novembre 2026",
     "tr_bist100", "TRY"),
    ("AE", "Émirats arabes unis", _M, _EM, "", "ae_uae", "AED"),
    ("CN", "Chine", _S, _EM, "", "shanghai", "CNY"),
    ("IN", "Inde", _S, _EM, "", "nifty", "INR"),
    ("ID", "Indonésie", _S, _EM, "sous surveillance : consultation possible vers frontière (revue de novembre 2026)",
     "id_jci", "IDR"),
    ("KR", "Corée du Sud", _S, _EM, "", "kr_kospi", "KRW"),
    ("MY", "Malaisie", _S, _EM, "", "my_klci", "MYR"),
    ("PH", "Philippines", _S, _EM, "", "ph_ephe", "PHP"),
    ("TW", "Taïwan", _S, _EM, "", "tw_taiex", "TWD"),
    ("TH", "Thaïlande", _S, _EM, "", "th_thd", "THB"),
    ("EE", "Estonie", _E, _FA, "", "ee_omxt", "EUR"),
    ("LV", "Lettonie", _E, _FA, "", "lv_omxr", "EUR"),
    ("LT", "Lituanie", _E, _FA, "", "lt_omxv", "EUR"),
    ("RO", "Roumanie", _E, _FA, "", "", "RON"),
    ("SI", "Slovénie", _E, _FA, "", "", "EUR"),
    ("BH", "Bahreïn", _M, _F, "", "", "BHD"),
    ("HR", "Croatie", _E, _F, "", "", "EUR"),
    ("IS", "Islande", _E, _F, "", "", "ISK"),
    ("JO", "Jordanie", _M, _F, "", "", "JOD"),
    ("KZ", "Kazakhstan", _S, _F, "", "", "KZT"),
    ("KE", "Kenya", _A, _F, "", "", "KES"),
    ("MU", "Maurice", _A, _F, "", "", "MUR"),
    ("MA", "Maroc", _A, _F, "", "", "MAD"),
    ("OM", "Oman", _M, _F, "", "", "OMR"),
    ("RS", "Serbie", _E, _F, "", "", "RSD"),
    ("TN", "Tunisie", _A, _F, "", "", "TND"),
    ("BJ", "Bénin", _A, _F, UEMOA, "", "XOF"),
    ("BF", "Burkina Faso", _A, _F, UEMOA, "", "XOF"),
    ("CI", "Côte d'Ivoire", _A, _F, UEMOA, "", "XOF"),
    ("GW", "Guinée-Bissau", _A, _F, UEMOA, "", "XOF"),
    ("ML", "Mali", _A, _F, UEMOA, "", "XOF"),
    ("NE", "Niger", _A, _F, UEMOA, "", "XOF"),
    ("SN", "Sénégal", _A, _F, UEMOA, "", "XOF"),
    ("TG", "Togo", _A, _F, UEMOA, "", "XOF"),
    ("BD", "Bangladesh", _S, _F, "autonome si les prix planchers reviennent", "", "BDT"),
    ("PK", "Pakistan", _S, _F, "", "", "PKR"),
    ("LK", "Sri Lanka", _S, _F, "", "", "LKR"),
    ("VN", "Viêt Nam", _S, _F, "", "vn_vn30", "VND"),
    ("AR", "Argentine", _L, _AU, "", "ar_argt", "ARS"),
    ("JM", "Jamaïque", _L, _AU, "", "", "JMD"),
    ("PA", "Panama", _L, _AU, "", "", "USD"),
    ("TT", "Trinité-et-Tobago", _L, _AU, "", "", "TTD"),
    ("BA", "Bosnie-Herzégovine", _E, _AU, "", "", "BAM"),
    ("BG", "Bulgarie", _E, _AU, "passe en marché frontière à la revue de mai 2027 (euro depuis le 01/01/2026)",
     "", "EUR"),
    ("LB", "Liban", _M, _AU, "", "", "LBP"),
    ("MT", "Malte", _E, _AU, "", "", "EUR"),
    ("NG", "Nigeria", _A, _AU, "", "", "NGN"),
    ("PS", "Palestine", _M, _AU, "", "", "ILS"),
    ("UA", "Ukraine", _E, _AU, "", "", "UAH"),
    ("ZW", "Zimbabwe", _A, _AU, "", "", "ZWG"),
)
# Paires déjà suivies dont le cours est « devise en dollars » (et non l'inverse).
DIRECT = {"EUR": "eurusd", "GBP": "gbpusd", "AUD": "audusd", "NZD": "nzdusd"}
INVERSE = {"JPY": "usdjpy", "CHF": "usdchf", "CAD": "usdcad"}
PEGGED = {"BAM": "arrimé à l'euro (1 € = 1,95583 BAM)", "XOF": "arrimé à l'euro (1 € = 655,957 FCFA)"}

Fetch = Callable[[str], bytes]


def flag(iso2: str) -> str:
    """Drapeau d'un pays (symboles régionaux Unicode)."""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2.upper()) if len(iso2) == 2 else ""


def currency_pair(cur: str) -> Tuple[str, bool]:
    """(code du cours, vrai si c'est le cours du dollar dans la devise) ; ("", …)
    pour le dollar lui-même ou une devise que Yahoo ne cote pas."""
    if cur in DIRECT:
        return DIRECT[cur], False
    if cur in INVERSE:
        return INVERSE[cur], True
    code = f"fx_{cur.lower()}"
    return (code, True) if code in ma.CURRENCY_CODES else ("", True)


def currency_view(cur: str, close: pd.DataFrame) -> Dict[str, Any]:
    """La devise face au dollar : cours, date et variation sur un an (positive
    quand la devise s'apprécie)."""
    out: Dict[str, Any] = {"code": cur, "note": PEGGED.get(cur, "")}
    if cur == "USD":
        return dict(out, note="le dollar américain lui-même")
    pair, inverse = currency_pair(cur)
    s = ma.sessions(close[pair]) if pair and pair in close.columns else pd.Series(dtype=float)
    if s.empty:
        return dict(out, value=None)
    year = s[s.index > s.index[-1] - pd.Timedelta(days=365)]
    first, last = float(year.iloc[0]), float(s.iloc[-1])
    change = (first / last - 1) * 100 if inverse else (last / first - 1) * 100
    return dict(out, value=last, inverse=inverse, date=str(s.index[-1].date()), change_1y=change)


# ══════════════════════════════════════════════════════════════════════
# BANQUE MONDIALE : lue chaque semaine par le noyau de savoir
# ══════════════════════════════════════════════════════════════════════

def parse_countries(raw: bytes) -> Dict[str, Dict[str, str]]:
    """Région, niveau de revenu et capitale de chaque pays (code ISO 2)."""
    data = json.loads(raw)
    rows = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
    out = {}
    for r in rows:
        iso = str(r.get("iso2Code") or "")
        income = ((r.get("incomeLevel") or {}).get("value") or "").strip()
        if len(iso) == 2 and income and income != "Aggregates":
            out[iso] = {"income": INCOME_FR.get(income, income), "region_wb": (r.get("region") or {}).get("value") or "",
                        "capital": r.get("capitalCity") or ""}
    return out


def parse_indicator(raw: bytes) -> Dict[str, Tuple[float, str]]:
    """Dernière valeur connue d'un indicateur pour chaque pays : (valeur, année)."""
    data = json.loads(raw)
    rows = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
    out = {}
    for r in rows:
        iso, value = str((r.get("country") or {}).get("id") or ""), r.get("value")
        if len(iso) == 2 and isinstance(value, (int, float)):
            out[iso] = (float(value), str(r.get("date") or ""))
    return out


def due(book: Any, now: datetime) -> bool:
    last = (book or {}).get("tried_at") if isinstance(book, dict) else None
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    hours = RETRY_HOURS if book.get("error") else REFRESH_DAYS * 24
    return (now - when).total_seconds() >= 3600 * hours


def refresh(memory: Any, now: datetime, fetch: Fetch, force: bool = False) -> Dict[str, Any]:
    """Relit la Banque mondiale une fois par semaine ; une panne est notée,
    jamais bloquante, et garde la lecture précédente."""
    from .market_watch import friendly_error
    book = memory.get(KEY)
    book = dict(book) if isinstance(book, dict) else {}
    if not (force or due(book, now)):
        return book
    book["tried_at"] = now.isoformat(timespec="seconds")
    try:
        countries = parse_countries(fetch(WB_COUNTRIES))
        if not countries:
            raise ValueError("liste des pays vide")
        values = {key: parse_indicator(fetch(WB_INDICATOR.format(ind=code))) for key, code, _l in INDICATORS}
        wanted = {iso for iso, *_ in COUNTRIES}
        def recent(year: str) -> bool:
            return year.isdigit() and int(year) >= now.year - MAX_AGE_YEARS
        book.update(fetched_at=book["tried_at"], error="", countries={
            iso: dict(countries.get(iso, {}), indicators={
                k: values[k][iso] for k in values if iso in values[k] and recent(values[k][iso][1])})
            for iso in sorted(wanted)})
    except Exception as e:
        book["error"] = friendly_error(e)
    memory.put(KEY, book)
    return book


# ══════════════════════════════════════════════════════════════════════
# PANNEAU : la page des pays (lecture seule)
# ══════════════════════════════════════════════════════════════════════

def countries_view(close: pd.DataFrame, book: Dict[str, Any], window: str = "1y") -> Dict[str, Any]:
    """Chaque pays : classement, indice (cours, courbe, tendance de la règle),
    devise face au dollar, indicateurs de la Banque mondiale."""
    window = window if window in ma.SPARK_SESSIONS else "1y"
    wb = book.get("countries") or {}
    rows = []
    for iso, name, region, msci, note, code, cur in COUNTRIES:
        index = None
        s = ma.sessions(close[code]) if code and code in close.columns else pd.Series(dtype=float)
        if not s.empty:
            d = ma.market_detail(code, s)
            curve = ma.spark(s, window)
            index = {"code": code, "name": ma.NAME.get(code, code), "close": d["close"], "date": d["date"],
                     "trend": d["trend"], "signal": d["signal"], "spark": curve,
                     "change": (curve[-1] / curve[0] - 1) * 100 if len(curve) > 1 else None}
        rows.append({"iso": iso, "name": name, "flag": flag(iso), "region": region, "msci": msci, "note": note,
                     "index": index, "currency": currency_view(cur, close), "wb": wb.get(iso) or {}})
    return {"rows": rows, "window": window, "regions": list(REGIONS), "classes": list(CLASSES),
            "fetched_at": book.get("fetched_at"), "error": book.get("error") or "",
            "source": "MSCI (revue 2026), Banque mondiale, Yahoo Finance"}


def classes_count() -> Dict[str, int]:
    """Nombre de pays par classement MSCI."""
    out: Dict[str, int] = {}
    for c in COUNTRIES:
        out[c[3]] = out.get(c[3], 0) + 1
    return out
