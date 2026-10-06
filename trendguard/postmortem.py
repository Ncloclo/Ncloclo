"""
Analyse après chaque trade (prompt maître §37-38, docs/PLATEFORME.md) : à la
vente, le trade reçoit son régime de marché à l'achat, son meilleur et son
pire moment (gain et perte maximums en R, d'après les clôtures pendant la
détention), le glissement à la sortie et une leçon en clair. Le bilan des
leçons s'affiche dans le panneau et le rapport.

Ce que le bot en fait : rien de plus que le savoir. Ses règles ne changent que
par l'évolution encadrée (épreuves sur 8 ans de cours).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import pandas as pd

from .texte import fr

LESSONS = {
    "tendance": "tendance captée : le gain a couru",
    "faux_depart": "faux départ : la cassure n'a pas tenu, perte limitée par le stop (normal)",
    "gain_rendu": "gain rendu en partie : la position est montée haut puis est retombée sous le stop suiveur",
    "urgence": "sortie d'urgence (stop de secours ou retrait de la cote)",
    "ordinaire": "trade ordinaire",
}


def excursions(close: Optional[pd.Series], entry: float, qty: float, risk: float,
               start: Any, end: Any) -> Dict[str, Optional[float]]:
    """Meilleur et pire moment du trade, en R, d'après les clôtures connues
    pendant la détention : de la bougie du jour de l'achat à celle de la
    veille de la vente (la bougie d'un jour se clôt à minuit UTC)."""
    if close is None or not len(close) or not risk or not qty:
        return {"mfe_r": None, "mae_r": None}
    try:
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        s = s.tz_localize("UTC") if s.tzinfo is None else s
        e = e.tz_localize("UTC") if e.tzinfo is None else e
        window = close.loc[s.normalize():(e - pd.Timedelta(days=1)).normalize()].dropna()
    except (KeyError, TypeError, ValueError):
        return {"mfe_r": None, "mae_r": None}
    if not len(window):
        return {"mfe_r": None, "mae_r": None}
    hi, lo = float(window.max()), float(window.min())
    return {"mfe_r": round(max(0.0, (hi - entry) * qty / risk), 2),
            "mae_r": round(min(0.0, (lo - entry) * qty / risk), 2)}


def lesson(trade: Dict[str, Any]) -> str:
    """La leçon du trade (clé de LESSONS)."""
    r, mfe = float(trade.get("r") or 0.0), trade.get("mfe_r")
    if trade.get("reason") in ("EXCHANGE_STOP", "DELISTED"):
        return "urgence"
    if r >= 2:
        return "tendance"
    if mfe is not None and mfe >= 2 and r < mfe - 1.5:
        return "gain_rendu"
    if r < 0 and (mfe is None or mfe < 0.5):
        return "faux_depart"
    return "ordinaire"


def enrich(trade: Dict[str, Any], close: Optional[pd.Series], qty: float, risk: float,
           stop: Optional[float] = None, regime: Optional[str] = None) -> Dict[str, Any]:
    """Le trade, complété : excursions, glissement sous le stop, régime à
    l'achat, leçon."""
    out = dict(trade)
    if out.get("entry") and out.get("entry_date"):
        out.update(excursions(close, float(out["entry"]), qty, risk, out["entry_date"], out["date"]))
    if stop and out.get("exit") and float(out["exit"]) < stop:
        out["slippage_pct"] = round((stop - float(out["exit"])) / stop * 100, 2)
    if regime:
        out["regime"] = regime
    out["lesson"] = lesson(out)
    return out


def summary(trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Bilan des leçons des trades clos."""
    done = [t for t in trades if "lesson" in t]
    if not done:
        return {"trades": 0, "text": "aucun trade clos analysé pour l'instant"}
    counts: Dict[str, int] = {}
    for t in done:
        counts[t["lesson"]] = counts.get(t["lesson"], 0) + 1
    avg = sum(float(t.get("r") or 0) for t in done) / len(done)
    slips = [t["slippage_pct"] for t in done if t.get("slippage_pct")]
    text = (f"{len(done)} trade(s) analysé(s), {fr(avg, '+.2f')} R en moyenne ; "
            + ", ".join(f"{n} {LESSONS[k].split(' :')[0]}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])))
    if slips:
        text += f" ; glissement moyen sous le stop {fr(sum(slips) / len(slips), '.1f')} %"
    return {"trades": len(done), "avg_r": round(avg, 2), "lessons": counts, "text": text}
