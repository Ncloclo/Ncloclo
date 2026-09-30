"""Apprentissage libre : le bot apprend, s'adapte et affine sa ruse en
continu, sans attendre ni demander (docs/APPRENTISSAGE.md).

Libre, rapide et précis : chaque relevé est appris et appliqué aussitôt.

- Carnets d'ordres : l'écart achat/vente et la profondeur NORMAUX de chaque
  crypto, relevés toutes les 10 minutes le temps de les apprendre (quelques
  heures), puis toutes les heures, et à chaque achat. La ruse diffère un
  achat dès que le carnet s'écarte de SA normale, pas seulement au-delà du
  seuil fixe de 0,5 %.
- Prévisions : chaque probabilité annoncée (vente, achat, marché), relevée
  12, 6, 3 et 1 heure avant la clôture, est comparée à ce qui s'est passé.
  Les suivantes sont corrigées par cette expérience, l'expérience récente
  pesant plus.
- Ruse : bilan des achats différés (achetés plus tard et à quel prix, ou
  abandonnés).

Ce qui n'est pas libre : les règles (cassure, stops, lecture du marché) ne
changent que par l'évolution encadrée (evolution.py), et le risque jamais.
L'apprentissage ne peut que rendre le bot plus prudent : un seuil appris
n'est jamais plus large que le seuil fixe, et les probabilités corrigées
n'avancent aucune décision (c'est la clôture qui décide).
"""

from __future__ import annotations

import statistics
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from .texte import fr

BOOK_ALPHA = 0.05        # poids d'un nouveau relevé (≈ les 20 derniers comptent)
BOOK_MIN_N = 24          # relevés avant de se fier à la normale (≈ un jour)
SPREAD_MULT = 3.0        # écart anormal : plus de 3 × la normale de la crypto…
SPREAD_FLOOR = 0.002     # … et jamais sous 0,2 % (glissement supposé par les tests)
DEPTH_DRAIN = 0.25       # carnet vidé : moins du quart de sa profondeur normale
SPIKE_CLIP = 5.0         # un relevé aberrant pèse au plus comme 5 × la normale

FORECAST_HORIZONS = (1.0, 3.0, 6.0, 12.0)    # heures avant la clôture
CAL_BINS = 10
CAL_MIN_N = 8.0          # prévisions par tranche avant de corriger
CAL_PRIOR = 8.0          # poids du modèle dans la correction (prudence)
CAL_DECAY = 0.99         # chaque soir, l'expérience passée pèse 1 % de moins
KINDS = ("sell", "buy", "bear")


def new() -> Dict[str, Any]:
    return {"books": {}, "cal": {}, "brier": {}, "forecast": {},
            "ruse": {"deferred": 0, "bought": 0, "abandoned": 0, "cancelled": 0,
                     "gain_sum": 0.0, "gain_n": 0}}


def ensure(L: Any) -> Dict[str, Any]:
    """Mémoire d'apprentissage complétée (état d'une version précédente)."""
    if not isinstance(L, dict):
        L = {}
    for k, v in new().items():
        if not isinstance(L.get(k), type(v)):
            L[k] = v
    return L


# ══════════════════════════════════════════════════════════════════════
# Carnets d'ordres : la normale de chaque crypto, et la ruse qui s'y règle
# ══════════════════════════════════════════════════════════════════════

def book_stats(ob: Dict[str, Any], band: float = 0.01) -> Optional[Tuple[float, float]]:
    """(écart achat/vente relatif, montant proposé à la vente à moins de
    `band` du meilleur prix) ; None si le carnet est vide ou illisible."""
    bids, asks = ob.get("bids") or [], ob.get("asks") or []
    if not bids or not asks:
        return None
    bid, ask = float(bids[0][0]), float(asks[0][0])
    if bid <= 0 or ask <= 0:
        return None
    depth = sum(float(p) * float(q) for p, q, *_ in asks if float(p) <= ask * (1 + band))
    return (ask - bid) / ((ask + bid) / 2), depth


def observe_book(L: Dict[str, Any], asset: str, spread: float, depth: float) -> None:
    """Apprend la normale : médiane des premiers relevés, puis moyenne
    glissante où un relevé aberrant (krach éclair) pèse peu."""
    b = L["books"].setdefault(asset, {"n": 0, "first": []})
    b["n"] = int(b.get("n", 0)) + 1
    if b.get("spread") is None:
        b.setdefault("first", []).append([float(spread), float(depth)])
        if len(b["first"]) >= BOOK_MIN_N:
            b["spread"] = statistics.median(s for s, _d in b["first"])
            b["depth"] = statistics.median(d for _s, d in b["first"])
            b.pop("first", None)
        return
    s = min(float(spread), SPIKE_CLIP * max(b["spread"], 1e-6))
    d = min(max(float(depth), b["depth"] / SPIKE_CLIP), SPIKE_CLIP * max(b["depth"], 1e-6))
    b["spread"] += BOOK_ALPHA * (s - b["spread"])
    b["depth"] += BOOK_ALPHA * (d - b["depth"])


def _normal(L: Dict[str, Any], asset: str) -> Optional[Dict[str, Any]]:
    b = (L.get("books") or {}).get(asset)
    return b if b and b.get("spread") is not None else None


def spread_limit(L: Dict[str, Any], asset: str, cap: float) -> float:
    """Écart achat/vente au-delà duquel l'achat est différé : 3 × la normale
    apprise de la crypto (au moins 0,2 %), jamais plus large que `cap`."""
    b = _normal(L, asset)
    if b is None:
        return cap
    return min(cap, max(SPREAD_FLOOR, SPREAD_MULT * b["spread"]))


def depth_drained(L: Dict[str, Any], asset: str, depth: float, quote: str = "USDT") -> Optional[str]:
    """Raison de différer si le carnet est vidé par rapport à sa normale."""
    b = _normal(L, asset)
    if b is None or depth >= DEPTH_DRAIN * b["depth"]:
        return None
    return (f"carnet d'ordres vidé ({fr(depth, ',.0f')} {quote} proposés à moins de 1 % du "
            f"prix, contre {fr(b['depth'], ',.0f')} d'habitude)")


# ══════════════════════════════════════════════════════════════════════
# Prévisions : comparer chaque probabilité à ce qui s'est passé
# ══════════════════════════════════════════════════════════════════════

def forecast_bucket(hours: float) -> Optional[float]:
    """Relevé des prévisions à 12, 6, 3 et 1 heure de la clôture."""
    if hours <= 0:
        return None
    for h in FORECAST_HORIZONS:
        if hours <= h:
            return h
    return None


def has_snapshot(L: Dict[str, Any], for_day: str, bucket: float) -> bool:
    fs = L.get("forecast") or {}
    return fs.get("for_day") == for_day and str(bucket) in (fs.get("snaps") or {})


def record_forecast(L: Dict[str, Any], f: Dict[str, Any], for_day: str, bucket: float) -> None:
    """Probabilités BRUTES du modèle (avant correction) pour la clôture du
    jour `for_day`."""
    fs = L.get("forecast") or {}
    if fs.get("for_day") != for_day:
        fs = {"for_day": for_day, "snaps": {}}
    reg = f.get("regime") or {}
    fs["snaps"][str(bucket)] = {
        "sell": {s["asset"]: float(s.get("prob_model", s["prob"])) for s in f.get("sells") or []},
        "buy": {b["asset"]: float(b.get("prob_model", b["prob"])) for b in f.get("buys") or []},
        "bear": (float(reg.get("prob_bear_model", reg["prob_bear"]))
                 if reg.get("prob_bear") is not None else None)}
    L["forecast"] = fs


def _bin(p: float) -> int:
    return min(max(int(p * CAL_BINS), 0), CAL_BINS - 1)


def calibrate(L: Optional[Dict[str, Any]], kind: str, p: float) -> float:
    """Probabilité corrigée par l'expérience : fréquence observée dans la
    tranche de `p`, tempérée par le modèle tant que l'expérience est mince."""
    bins = ((L or {}).get("cal") or {}).get(kind)
    if not bins:
        return p
    n, hits = bins[_bin(p)]
    if n < CAL_MIN_N:
        return p
    return min(max((hits + CAL_PRIOR * p) / (n + CAL_PRIOR), 0.001), 0.999)


def calibrator(L: Optional[Dict[str, Any]]) -> Optional[Callable[[str, float], float]]:
    """Correction à passer à anticipation.forecast, ou None sans expérience."""
    L = L if isinstance(L, dict) else None
    if not L or not any(n >= CAL_MIN_N for bins in (L.get("cal") or {}).values() for n, _h in bins):
        return None
    return lambda kind, p: calibrate(L, kind, p)


def evaluate(L: Dict[str, Any], day: str, sold: Iterable[str], signals: Iterable[str],
             bear: bool) -> int:
    """À la décision du jour `day` : chaque prévision relevée pour cette
    clôture devient une leçon. Retourne le nombre de leçons."""
    fs = L.get("forecast") or {}
    if fs.get("for_day") != day:
        if fs.get("for_day") and fs["for_day"] < day:
            L["forecast"] = {}          # prévisions d'une clôture passée sans décision
        return 0
    sold, signals = set(sold), set(signals)
    for kind in KINDS:
        bins = L["cal"].setdefault(kind, [[0.0, 0.0] for _ in range(CAL_BINS)])
        for b in bins:
            b[0] *= CAL_DECAY
            b[1] *= CAL_DECAY
        br = L["brier"].setdefault(kind, {"n": 0.0, "raw": 0.0, "cal": 0.0})
        for k in br:
            br[k] *= CAL_DECAY
    lessons = []
    for snap in (fs.get("snaps") or {}).values():
        lessons += [("sell", p, a in sold) for a, p in (snap.get("sell") or {}).items()]
        lessons += [("buy", p, a in signals) for a, p in (snap.get("buy") or {}).items()]
        if snap.get("bear") is not None:
            lessons.append(("bear", snap["bear"], bear))
    # Mesure honnête : chaque prévision du soir est jugée avec la correction
    # connue AVANT ce soir, pas avec celle qu'apprennent ses voisines.
    corrected = [calibrate(L, kind, p) for kind, p, _h in lessons]
    for (kind, p, happened), pc in zip(lessons, corrected):
        o = 1.0 if happened else 0.0
        br = L["brier"][kind]
        br["n"] += 1
        br["raw"] += (p - o) ** 2
        br["cal"] += (pc - o) ** 2
        b = L["cal"][kind][_bin(p)]
        b[0] += 1
        b[1] += o
    L["forecast"] = {}
    return len(lessons)


# ══════════════════════════════════════════════════════════════════════
# Ruse : bilan des achats différés
# ══════════════════════════════════════════════════════════════════════

def note_deferral(L: Dict[str, Any], outcome: str, gain: Optional[float] = None) -> None:
    """outcome : deferred (premier report), bought, abandoned, cancelled ;
    gain : prix d'achat obtenu par rapport au premier essai (+ = moins cher)."""
    r = L["ruse"]
    r[outcome] = int(r.get(outcome, 0)) + 1
    if outcome == "bought" and gain is not None:
        r["gain_sum"] = float(r.get("gain_sum", 0.0)) + float(gain)
        r["gain_n"] = int(r.get("gain_n", 0)) + 1


# ══════════════════════════════════════════════════════════════════════
# Résumé (panneau, Rachelle)
# ══════════════════════════════════════════════════════════════════════

def summary(L: Any) -> Dict[str, Any]:
    """Ce que le bot a appris, pour le panneau et le rapport : normale des
    carnets, précision des prévisions (brute et corrigée), résultats de la
    ruse, écarts les plus larges, et une phrase de synthèse."""
    L = ensure(dict(L) if isinstance(L, dict) else {})
    books = L["books"]
    learned = {a: b for a, b in books.items() if b.get("spread") is not None}
    samples = sum(int(b.get("n", 0)) for b in books.values())
    brier = {k: v for k, v in L["brier"].items() if v.get("n", 0) >= 1}
    n_fc = sum(v["n"] for v in brier.values())
    raw = sum(v["raw"] for v in brier.values()) / n_fc if n_fc else None
    cal = sum(v["cal"] for v in brier.values()) / n_fc if n_fc else None
    r = L["ruse"]
    gain = r["gain_sum"] / r["gain_n"] * 100 if r.get("gain_n") else None
    parts = [f"carnets : normale apprise pour {len(learned)} crypto(s) sur {len(books)} "
             f"({samples} relevés)" if books else "carnets : premiers relevés dans l'heure"]
    if n_fc:
        parts.append(f"prévisions : {fr(n_fc, '.0f')} comparées à la clôture, erreur (Brier) "
                     f"{fr(raw, '.3f')} brute, {fr(cal, '.3f')} corrigée")
    else:
        parts.append("prévisions : premières leçons après la prochaine clôture")
    if r.get("deferred"):
        parts.append(f"ruse : {r['deferred']} achat(s) différé(s), {r['bought']} acheté(s) plus tard"
                     + (f" ({fr(gain, '+.2f')} % en moyenne)" if gain is not None else "")
                     + f", {r['abandoned']} abandonné(s)")
    tight = sorted(((a, b["spread"]) for a, b in learned.items()), key=lambda x: -x[1])[:3]
    return {"books": len(learned), "books_seen": len(books), "samples": samples,
            "forecasts": round(n_fc, 1), "brier_raw": raw, "brier_cal": cal,
            "ruse": {k: r[k] for k in ("deferred", "bought", "abandoned", "cancelled")},
            "ruse_gain_pct": gain,
            "widest": [{"asset": a, "spread_pct": round(s * 100, 4)} for a, s in tight],
            "text": " · ".join(parts)}
