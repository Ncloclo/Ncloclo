"""
Flux de stablecoins : l'argent prêt à entrer sur les cryptos. L'offre totale
des stablecoins adossés au dollar (DefiLlama, API publique sans clé) : quand
elle grossit, de l'argent frais est disponible pour acheter ; quand elle
fond, de l'argent sort des cryptos.

Information seulement : lue avec le noyau de savoir au plus une fois par
jour (méta « flux » de sa base), montrée par l'agent « flux » du comité, qui
ne vote pas tant qu'une étude n'a pas prouvé qu'elle aide la stratégie.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .texte import fr

FEED = "https://stablecoins.llama.fi/stablecoincharts/all"
KEY = "flux"                    # méta-donnée du noyau de savoir
REFRESH_HOURS = 24              # un point par jour
RETRY_HOURS = 6                 # après une panne
KEEP = 400                      # jours gardés


def parse(raw: bytes) -> List[Tuple[str, float]]:
    """[(jour, offre en dollars)] des stablecoins adossés au dollar, du plus
    ancien au plus récent ; un point illisible est ignoré."""
    out = []
    for row in json.loads(raw):
        try:
            usd = float((row.get("totalCirculating") or {})["peggedUSD"])
            day = datetime.fromtimestamp(int(row["date"]), timezone.utc).date().isoformat()
        except (KeyError, TypeError, ValueError):
            continue
        if usd > 0:
            out.append((day, usd))
    return sorted(out)


def due(book: Any, now: datetime) -> bool:
    """À relire : une fois par jour, six heures après une panne."""
    last = (book or {}).get("tried_at") if isinstance(book, dict) else None
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    return now - when >= timedelta(hours=RETRY_HOURS if book.get("error") else REFRESH_HOURS)


def refresh(memory: Any, now: datetime, fetch: Callable[[str], bytes], force: bool = False) -> Dict[str, Any]:
    """Relit l'offre de stablecoins s'il le faut ; une panne est notée, jamais
    bloquante, et la série déjà lue est gardée."""
    from .market_watch import friendly_error
    book = memory.get(KEY)
    book = dict(book) if isinstance(book, dict) else {}
    if not (force or due(book, now)):
        return book
    book["tried_at"] = now.isoformat(timespec="seconds")
    try:
        series = parse(fetch(FEED))
    except Exception as e:
        book["error"] = friendly_error(e)
        memory.put(KEY, book)
        return book
    if not series:
        book["error"] = "réponse vide"
        memory.put(KEY, book)
        return book
    book.update(fetched_at=book["tried_at"], error="", series=[list(x) for x in series[-KEEP:]])
    memory.put(KEY, book)
    return book


def change(series: List[List[Any]], days: int) -> Optional[float]:
    """Variation de l'offre sur `days` jours (0,02 = +2 %), ou None."""
    if len(series) <= days:
        return None
    old, new = float(series[-1 - days][1]), float(series[-1][1])
    return new / old - 1 if old > 0 else None


def view(book: Any) -> Dict[str, Any]:
    """Ce que voit l'agent flux : dernier jour, offre, variations 7 et 30 jours."""
    series = (book or {}).get("series") if isinstance(book, dict) else None
    if not series:
        return {}
    return {"day": series[-1][0], "usd": float(series[-1][1]),
            "change_7d": change(series, 7), "change_30d": change(series, 30)}


def describe(v: Optional[Dict[str, Any]]) -> Optional[str]:
    """Une ligne lisible, ou None sans données."""
    if not v:
        return None
    text = f"stablecoins en dollars : {fr(v['usd'] / 1e9, ',.1f')} milliards de dollars"
    c30 = v.get("change_30d")
    if c30 is not None:
        sens = ("argent frais disponible" if c30 > 0.01 else "argent qui sort des cryptos" if c30 < -0.01
                else "stable")
        text += f", {fr(c30 * 100, '+.1f')} % sur 30 jours ({sens})"
    return text
