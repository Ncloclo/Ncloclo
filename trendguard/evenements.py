"""
Calendrier économique (prompt maître §33, docs/PLATEFORME.md) : les grandes
annonces américaines de la semaine (Fed, inflation, emploi…), qui font
souvent bouger le bitcoin.

Source : le calendrier public de ForexFactory (le fichier de la semaine, sans
clé), relu au plus toutes les 6 heures par le noyau de savoir (savoir.py).
Seules les annonces des États-Unis à fort impact sont gardées.

Ce que le bot en fait : il les montre (panneau, raisonnement) et les garde en
mémoire pour mesurer, avec le temps, combien le bitcoin bouge ces jours-là.
Aucun achat n'est bloqué ni retardé : aucune règle de ce genre n'a été
prouvée utile (principe du dépôt : seul ce qui est validé est exécuté).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from . import market_watch as mw
from .texte import fr

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
KEY = "evenements"              # méta-donnée du noyau de savoir
REFRESH_HOURS = 6               # le fichier change peu : relu au plus toutes les 6 heures
RETRY_HOURS = 1                 # après une panne
AHEAD_HOURS = 48                # annonces montrées dans le raisonnement
KEEP = 1000                     # annonces gardées pour la mesure (plusieurs années)
MIN_DAYS = 10                   # jours d'annonce mesurés avant de conclure
BASE_DAYS = 90                  # « jour ordinaire » : médiane des 90 jours précédents
COUNTRY, IMPACT = "USD", "High"
DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

# Noms en clair des annonces les plus suivies ; le premier qui correspond.
GLOSSARY = (("FOMC Meeting Minutes", "compte rendu de la Fed"),
            ("FOMC Statement", "décision de la Fed"),
            ("FOMC Press Conference", "conférence de presse de la Fed"),
            ("Federal Funds Rate", "taux directeur de la Fed"),
            ("Fed Chair", "discours du président de la Fed"),
            ("Core PCE", "inflation PCE sous-jacente"),
            ("PCE", "inflation PCE"),
            ("Core CPI", "inflation CPI sous-jacente"),
            ("CPI", "inflation CPI"),
            ("PPI", "prix à la production"),
            ("ADP", "créations d'emplois privés ADP"),
            ("Non-Farm Employment Change", "créations d'emplois"),
            ("Average Hourly Earnings", "salaires horaires"),
            ("Unemployment Rate", "taux de chômage"),
            ("Unemployment Claims", "inscriptions au chômage"),
            ("JOLTS", "offres d'emploi"),
            ("GDP", "croissance du PIB"),
            ("Retail Sales", "ventes au détail"),
            ("ISM Manufacturing", "activité de l'industrie ISM"),
            ("ISM Services", "activité des services ISM"),
            ("Consumer Confidence", "confiance des ménages"),
            ("Consumer Sentiment", "moral des ménages"))


def label(title: str) -> str:
    """Nom en clair d'une annonce, avec son nom d'origine."""
    for key, fr_name in GLOSSARY:
        if key.lower() in title.lower():
            return f"{fr_name} ({title})"
    return title


def _when(text: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def parse(raw: bytes) -> List[Dict[str, Any]]:
    """Annonces américaines à fort impact du fichier de la semaine, à l'heure
    UTC. Un texte venu d'Internet n'est qu'une donnée : tronqué, jamais
    interprété."""
    out = []
    for e in json.loads(raw.decode("utf-8")):
        if not isinstance(e, dict) or e.get("country") != COUNTRY or e.get("impact") != IMPACT:
            continue
        at = _when(e.get("date"))
        if at is None:
            continue
        out.append({"title": str(e.get("title") or "")[:120], "at": at.isoformat(timespec="minutes"),
                    "forecast": str(e.get("forecast") or "")[:20], "previous": str(e.get("previous") or "")[:20]})
    return sorted(out, key=lambda x: x["at"])


def due(book: Any, now: datetime) -> bool:
    """Le calendrier est-il à relire (6 heures après la dernière lecture,
    une heure après une panne) ?"""
    last = _when((book or {}).get("tried_at")) if isinstance(book, dict) else None
    if last is None:
        return True
    return now - last >= timedelta(hours=RETRY_HOURS if book.get("error") else REFRESH_HOURS)


def refresh(memory: Any, now: datetime, fetch: Callable[[str], bytes]) -> Dict[str, Any]:
    """Relit le calendrier de la semaine s'il le faut et l'ajoute à la mémoire
    des annonces (méta « evenements » du noyau de savoir). Une panne est notée,
    jamais bloquante."""
    book = memory.get(KEY)
    book = dict(book) if isinstance(book, dict) else {}
    if not due(book, now):
        return book
    book["tried_at"] = now.isoformat(timespec="seconds")
    try:
        week = parse(fetch(FEED))
    except Exception as e:
        book["error"] = mw.friendly_error(e)
        memory.put(KEY, book)
        return book
    known = {f"{x.get('at')}|{x.get('title')}": x for x in _history(book)}
    known.update({f"{x['at']}|{x['title']}": x for x in week})
    book.update(fetched_at=book["tried_at"], error="",
                history=sorted(known.values(), key=lambda x: str(x.get("at", "")))[-KEEP:])
    memory.put(KEY, book)
    return book


def _history(book: Any) -> List[Dict[str, Any]]:
    return [x for x in (book.get("history") or []) if isinstance(x, dict)] if isinstance(book, dict) else []


def window(book: Any, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Annonces gardées entre `start` et `end`."""
    out = []
    for x in _history(book):
        at = _when(x.get("at"))
        if at is not None and start <= at < end:
            out.append(dict(x, label=label(x.get("title", ""))))
    return out


def when_text(at: str) -> str:
    """« mercredi 7 à 18:00 UTC »."""
    d = _when(at)
    return f"{DAYS[d.weekday()]} {d.day} à {d:%H:%M} UTC" if d else at


def reactions(book: Any, btc: Optional[pd.Series], last_day: str) -> Dict[str, Any]:
    """Combien le bitcoin a bougé les jours d'annonce déjà clos : variation
    de la bougie du jour (en valeur absolue), en multiple de la médiane des
    90 jours précédents (un « jour ordinaire »)."""
    days = sorted({str(x.get("at", ""))[:10] for x in _history(book) if str(x.get("at", ""))[:10] <= last_day})
    ratios: List[float] = []
    if btc is not None and len(btc) > BASE_DAYS:
        move = btc.pct_change().abs()
        for d in days:
            t = pd.Timestamp(d, tz="UTC")
            if t not in move.index:
                continue
            base = float(move.loc[:t - pd.Timedelta(days=1)].tail(BASE_DAYS).median())
            if base > 0 and pd.notna(move.loc[t]):
                ratios.append(float(move.loc[t]) / base)
    n = len(ratios)
    if n < MIN_DAYS:
        return {"days": n, "ratio": None,
                "text": f"{n} jour(s) d'annonce mesuré(s) ; conclusion à partir de {MIN_DAYS}"}
    ratio = sum(ratios) / n
    more = sum(r > 1 for r in ratios) / n * 100
    return {"days": n, "ratio": round(ratio, 2),
            "text": (f"sur {n} jours d'annonce, le bitcoin a bougé {fr(ratio, '.1f')} fois plus qu'un "
                     f"jour ordinaire en moyenne (plus que d'habitude {fr(more, '.0f')} % des fois)")}


def day_view(book: Any, btc: Optional[pd.Series], now: datetime, last_day: str) -> Dict[str, Any]:
    """Pour la décision : annonces des 48 prochaines heures, mesure des
    réactions du bitcoin, ligne du raisonnement."""
    nxt = window(book, now, now + timedelta(hours=AHEAD_HOURS))
    r = reactions(book, btc, last_day)
    line = ""
    if nxt:
        line = ("Annonces économiques importantes (États-Unis) dans les 48 heures : "
                + " ; ".join(f"{x['label']} {when_text(x['at'])}" for x in nxt)
                + ". Information seulement : aucun achat n'est bloqué (effet non prouvé"
                + (f" ; {r['text']}" if r["ratio"] is not None else "") + ").")
    err = book.get("error") if isinstance(book, dict) else ""
    return {"day": last_day, "upcoming": nxt, "reaction": r, "line": line, "error": err or ""}
