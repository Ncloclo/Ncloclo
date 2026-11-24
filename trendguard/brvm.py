"""
La BRVM (Bourse régionale des valeurs mobilières, à Abidjan), bourse des
huit pays de l'UEMOA (docs/BRVM.md). Le bot lit chaque jour, après la séance,
les deux pages publiques de son site : ses indices (BRVM Composite, BRVM 30,
Prestige, Principal, indices sectoriels), l'activité du marché et le cours
de chaque action cotée. Son robots.txt permet ces pages et demande 10
secondes entre deux lectures : le bot en lit deux par jour, à 11 secondes
d'écart, sous son nom (« TrendGuard veille ; lecture seule »).

La BRVM ne publie pas d'historique téléchargeable : le bot construit le sien,
séance après séance (data_brvm/). Sa règle de tendance s'y appliquera quand
il sera assez long (150 séances pour la moyenne, 250 pour un achat).
Information seulement : aucun ordre réel (il faudrait une SGI agréée).
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

INDICES_URL = "https://www.brvm.org/fr/indices"
STOCKS_URL = "https://www.brvm.org/fr/cours-actions/0"
CRAWL_DELAY = 11                    # robots.txt de la BRVM : 10 secondes entre deux lectures
KEY = "brvm"                        # méta du noyau de savoir
READ_AFTER = (16, 30)               # heure UTC (Abidjan) : la séance est close
RETRY_HOURS = 3
MAIN = ("BRVM - COMPOSITE", "BRVM-30", "BRVM - PRESTIGE", "BRVM - PRINCIPAL")
Fetch = Callable[[str], bytes]


def number(text: str) -> Optional[float]:
    """« 18 511 176 505 734 FCFA », « 2 985 », « -0,94 », « 6,42% » → nombre."""
    t = re.sub(r"[\s  ]|FCFA|%", "", text or "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _rows(page: str) -> List[List[str]]:
    """Les lignes des tableaux d'une page, cellule par cellule, en texte."""
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S | re.I)]
        if any(cells):
            out.append(cells)
    return out


def parse_indices(page: str) -> Tuple[List[Dict[str, Any]], Dict[str, Optional[float]]]:
    """Indices (nom, clôture de la veille, clôture, variation du jour et
    depuis le 31 décembre) et activité du marché (en FCFA)."""
    indices, activity = [], {}
    for cells in _rows(page):
        if len(cells) >= 5 and cells[0].upper().startswith("BRVM") and number(cells[2]) is not None:
            indices.append({"name": cells[0].replace("–", "-"), "prev": number(cells[1]), "close": number(cells[2]),
                            "change": number(cells[3]), "ytd": number(cells[4])})
        elif cells and cells[0] in ("Valeur des transactions", "Capitalisation Actions",
                                    "Capitalisation des obligations") and len(cells) > 1:
            activity[{"Valeur des transactions": "turnover", "Capitalisation Actions": "stocks_cap",
                      "Capitalisation des obligations": "bonds_cap"}[cells[0]]] = number(cells[1])
    return indices, activity


def parse_stocks(page: str) -> List[Dict[str, Any]]:
    """Chaque action cotée : symbole, nom, volume, cours de la veille,
    d'ouverture et de clôture (FCFA), variation du jour (%)."""
    out = []
    for cells in _rows(page):
        if len(cells) == 7 and re.fullmatch(r"[A-Z]{3,6}", cells[0]) and number(cells[5]) is not None:
            out.append({"symbol": cells[0], "name": cells[1].title(), "volume": number(cells[2]),
                        "prev": number(cells[3]), "open": number(cells[4]), "close": number(cells[5]),
                        "change": number(cells[6])})
    return out


def session_day(now: datetime) -> str:
    """La séance que montre la page : aujourd'hui après la clôture d'un jour
    de semaine, sinon le dernier jour de semaine passé."""
    day = now if (now.hour, now.minute) >= READ_AFTER else now - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.date().isoformat()


def due(book: Any, now: datetime) -> bool:
    """À relire une fois par séance, après la clôture ; trois heures après une
    panne."""
    if not isinstance(book, dict) or not book.get("tried_at"):
        return True
    try:
        when = datetime.fromisoformat(book["tried_at"])
    except ValueError:
        return True
    if book.get("error"):
        return (now - when).total_seconds() >= 3600 * RETRY_HOURS
    return session_day(now) != book.get("read_for")


def _fingerprint(indices: List[Dict[str, Any]], stocks: List[Dict[str, Any]]) -> str:
    raw = ";".join(f"{r['name']}:{r['prev']}:{r['close']}" for r in indices) + "|" + ";".join(
        f"{s['symbol']}:{s['prev']}:{s['close']}:{s['volume']}" for s in stocks)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _append(cache: str, code: str, day: str, close: Optional[float], volume: Optional[float] = None) -> None:
    """Ajoute la séance au fichier du titre (une ligne par jour, sans doublon)."""
    if close is None:
        return
    path = os.path.join(cache, f"{code}.csv")
    if os.path.exists(path):
        df = pd.read_csv(path)
        if day in set(df["date"].astype(str)):
            return
    line = f"{day},{close},{'' if volume is None else volume}\n"
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        if new:
            fh.write("date,close,volume\n")
        fh.write(line)


def refresh(memory: Any, now: datetime, fetch: Fetch, cache: str, force: bool = False,
            pause: float = CRAWL_DELAY) -> Dict[str, Any]:
    """Une lecture par séance : indices, activité, actions ; la séance entre
    dans l'historique du bot, sauf si la page n'a pas changé (jour férié).
    Une panne est notée, jamais bloquante."""
    from .market_watch import friendly_error
    book = memory.get(KEY)
    book = dict(book) if isinstance(book, dict) else {}
    if not (force or due(book, now)):
        return book
    book["tried_at"] = now.isoformat(timespec="seconds")
    try:
        indices, activity = parse_indices(fetch(INDICES_URL).decode("utf-8", "replace"))
        if pause:
            time.sleep(pause)
        stocks = parse_stocks(fetch(STOCKS_URL).decode("utf-8", "replace"))
        if not indices or not stocks:
            raise ValueError("pages de la BRVM illisibles (présentation changée ?)")
        print_ = _fingerprint(indices, stocks)
        day = session_day(now)
        fresh = print_ != book.get("fingerprint")
        if fresh:
            os.makedirs(cache, exist_ok=True)
            for r in indices:
                _append(cache, "idx_" + slug(r["name"]), day, r["close"])
            for s in stocks:
                _append(cache, s["symbol"].lower(), day, s["close"], s["volume"])
            book["sessions"] = int(book.get("sessions") or 0) + 1
        book.update(fetched_at=book["tried_at"], error="", read_for=day, fingerprint=print_,
                    session=day if fresh else book.get("session", day), indices=indices, activity=activity,
                    stocks=stocks)
    except Exception as e:
        book["error"] = friendly_error(e)
    memory.put(KEY, book)
    return book


def history(cache: str, code: str) -> pd.Series:
    """Clôtures gardées d'un titre ou d'un indice (vide si aucune)."""
    path = os.path.join(cache, f"{code}.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=["date"])
    s = df.set_index("date")["close"].astype(float).sort_index()
    s.index = pd.DatetimeIndex(s.index).tz_localize("UTC")
    return s


def composite(book: Any) -> Optional[Dict[str, Any]]:
    """Le BRVM Composite de la dernière séance lue (pour les pays de l'UEMOA)."""
    rows = (book or {}).get("indices") if isinstance(book, dict) else None
    row = next((r for r in rows or [] if r["name"].upper() == "BRVM - COMPOSITE"), None)
    return dict(row, session=book.get("session")) if row else None


def view(book: Any, cache: str) -> Dict[str, Any]:
    """La page BRVM du panneau : indices (avec la courbe gardée), activité,
    actions, et la longueur de l'historique construit."""
    book = book if isinstance(book, dict) else {}
    indices = []
    for r in book.get("indices") or []:
        s = history(cache, "idx_" + slug(r["name"]))
        indices.append(dict(r, main=r["name"].upper() in MAIN, spark=[round(float(v), 4) for v in s.iloc[-120:]]))
    return {"indices": indices, "activity": book.get("activity") or {}, "stocks": book.get("stocks") or [],
            "session": book.get("session"), "fetched_at": book.get("fetched_at"), "error": book.get("error") or "",
            "sessions": int(book.get("sessions") or 0), "source": "brvm.org (pages publiques)"}
