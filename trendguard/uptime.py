"""Disponibilité du bot : le temps pendant lequel il a vraiment tourné.

Le bot note chaque arrêt de plus d'une heure quand il reprend (PC éteint ou
en veille, bot figé, Internet coupé, arrêt demandé) ; le panneau en tire le
temps de marche sur 24 heures et 7 jours. Au premier démarrage, les arrêts
passés sont reconstitués d'après le journal : le bot y écrit au moins une
ligne par quart d'heure, un silence de plus d'une heure est donc un arrêt.

Les arrêts demandés (bouton ARRÊTER, Ctrl+C) ne comptent pas contre la
disponibilité et ne déclenchent aucune alerte.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

GAP_SEC = 3600                  # un arrêt compte au-delà d'une heure
KEEP_SEC = 30 * 86400           # historique gardé 30 jours…
KEEP_MAX = 200                  # … et 200 arrêts au plus
GOOD_PCT = 95.0                 # disponibilité jugée bonne sur 7 jours
DAY_SEC, WEEK_SEC = 86400, 7 * 86400

USER, ASLEEP, OFF, NETWORK = "user", "asleep", "off", "network"
CAUSES = {
    USER: "arrêt demandé (bouton ARRÊTER ou Ctrl+C)",
    ASLEEP: "PC en veille ou bot figé",
    OFF: "PC éteint ou en veille, ou bot arrêté sans demande",
    NETWORK: "cycles en échec : Internet ou Binance injoignable",
}
ADVICE = {
    ASLEEP: "Pour l'éviter : PC branché, mise en veille sur « Jamais » et capot fermé = "
            "« Ne rien faire » quand il est branché (Paramètres Windows ▸ Alimentation).",
    OFF: "Pour l'éviter : PC branché et allumé, mise en veille sur « Jamais » et capot "
         "fermé = « Ne rien faire » quand il est branché (Paramètres Windows ▸ Alimentation).",
    NETWORK: "Vérifiez la connexion Internet du PC.",
}

_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def cause_of_gap(last_ok: float, now: float, started_at: float,
                 tries_since: Optional[float], stopped_at: Optional[float]) -> str:
    """Cause probable d'un trou entre deux cycles réussis. `tries_since` :
    premier essai de cycle de ce processus depuis le dernier réussi ;
    `stopped_at` : dernier arrêt propre (demandé)."""
    if tries_since is not None and now - tries_since >= (now - last_ok) / 2:
        return NETWORK          # le bot tournait, mais ses cycles échouaient
    if started_at <= last_ok:
        return ASLEEP           # même processus, figé pendant le trou
    if stopped_at and float(stopped_at) >= last_ok:
        return USER
    return OFF


def from_log(path: str, until: Optional[float]) -> Dict[str, Any]:
    """Historique reconstitué d'après le journal du bot (heure du PC) :
    {"since": premier horodatage, "events": arrêts terminés avant `until`,
    le dernier cycle réussi ; le trou en cours est noté par le bot}."""
    events: List[Dict[str, Any]] = []
    since: Optional[float] = None
    prev: Optional[float] = None
    stopping = False
    for p in (path + ".1", path):           # sauvegarde la plus récente, puis le journal
        try:
            f = open(p, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for line in f:
                m = _STAMP.match(line)
                if not m:
                    continue
                try:
                    t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                except (ValueError, OverflowError):
                    continue
                since = t if since is None else since
                if prev is not None and t - prev > GAP_SEC and (until is None or t <= until):
                    events.append({"start": prev, "end": t, "cause": USER if stopping else OFF})
                    stopping = False
                if "[ARRÊT]" in line:
                    stopping = True
                elif "] TrendGuard — " in line:
                    stopping = False
                prev = t
    return {"since": since, "events": events[-KEEP_MAX:]}


def add(up: Dict[str, Any], event: Dict[str, Any], now: float) -> None:
    """Ajoute un arrêt ; ceux de plus de 30 jours sont oubliés."""
    evs = [e for e in up.get("events") or [] if now - e["end"] < KEEP_SEC] + [event]
    up["events"] = evs[-KEEP_MAX:]


def crossed_close(event: Dict[str, Any], delay_sec: int = 0) -> bool:
    """Une clôture quotidienne (00:00 UTC + délai) est-elle tombée pendant l'arrêt ?"""
    return int((event["start"] - delay_sec) // DAY_SEC) != int((event["end"] - delay_sec) // DAY_SEC)


def fdur(sec: float) -> str:
    h, m = divmod(int(round(sec / 60)), 60)
    return f"{h} h {m:02d}" if h else f"{m} min"


def describe(event: Dict[str, Any]) -> str:
    """« arrêté 3 h 36 (du 29/09 07:17 au 10:53, heure du PC) : cause »."""
    a, b = time.localtime(event["start"]), time.localtime(event["end"])
    end = time.strftime("%H:%M" if a[:3] == b[:3] else "%d/%m %H:%M", b)
    return (f"arrêté {fdur(event['end'] - event['start'])} "
            f"(du {time.strftime('%d/%m %H:%M', a)} au {end}, heure du PC) : "
            f"{CAUSES.get(event.get('cause'), CAUSES[OFF])}")


def _overlap(events: List[Dict[str, Any]], start: float, now: float) -> float:
    return sum(max(0.0, min(e["end"], now) - max(e["start"], start)) for e in events)


def availability(events: List[Dict[str, Any]], since: Optional[float], now: float,
                 window: float) -> Optional[float]:
    """Temps de marche en % sur la fenêtre, hors arrêts demandés ; None si
    trop peu d'historique."""
    start = max(now - window, since or now)
    user = _overlap([e for e in events if e.get("cause") == USER], start, now)
    base = now - start - user
    if base < GAP_SEC:
        return None
    down = _overlap([e for e in events if e.get("cause") != USER], start, now)
    return round(max(0.0, 100 * (1 - down / base)), 1)


def _view(e: Dict[str, Any]) -> Dict[str, Any]:
    cause = e.get("cause") if e.get("cause") in CAUSES else OFF
    return {"start": e["start"], "end": e["end"], "minutes": round((e["end"] - e["start"]) / 60),
            "cause": cause, "text": CAUSES[cause], "requested": cause == USER,
            "ongoing": bool(e.get("ongoing"))}


def summary(up: Optional[Dict[str, Any]], last_cycle_ts: Optional[float],
            stopped_at: Optional[float], now: Optional[float] = None) -> Dict[str, Any]:
    """Pour le panneau : temps de marche sur 24 h et 7 jours, derniers
    arrêts, et le dernier arrêt imprévu terminé depuis moins de 24 h."""
    now = time.time() if now is None else now
    up = up or {}
    events = list(up.get("events") or [])
    if last_cycle_ts and now - float(last_cycle_ts) > GAP_SEC:     # arrêté en ce moment
        cause = USER if stopped_at and float(stopped_at) >= float(last_cycle_ts) else OFF
        events.append({"start": float(last_cycle_ts), "end": now, "cause": cause, "ongoing": True})
    since = up.get("since") or (float(last_cycle_ts) if last_cycle_ts else None)
    recent = [e for e in events if e.get("cause") != USER and not e.get("ongoing")
              and now - e["end"] < DAY_SEC]
    return {
        "day_pct": availability(events, since, now, DAY_SEC),
        "week_pct": availability(events, since, now, WEEK_SEC),
        "since": since, "tracked": bool(up),
        "events": [_view(e) for e in reversed(events[-5:])],
        "recent": _view(recent[-1]) if recent else None,
    }
