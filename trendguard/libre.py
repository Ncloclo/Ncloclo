"""
Bot libre : votre demande du 1er octobre, appliquée telle quelle, à côté du
bot principal (docs/LIBRE.md).

Un second portefeuille, toujours fictif, avec le même capital que le paper,
qui agit librement sur ce qu'il lit et sur ses propres choix, sans aucune des
épreuves du bot principal :

  - il apprend chaque jour, vite : chaque source du noyau de savoir (presse,
    moteurs de recherche, forums, réseau social, tendances, IA) gagne du
    poids quand son avis de la veille a vu juste sur le cours du jour, en
    perd sinon (pondération multiplicative) ;
  - il se fait son propre avis sur chaque crypto : les avis du jour de
    toutes les sources, pondérés par ce qu'il a appris ;
  - il agit seul : il achète ce qu'il voit monter, vend ce qu'il voit
    baisser, coupe une perte au stop ;
  - il améliore ses propres règles et les applique : chaque semaine, il
    rejoue ses avis passés avec d'autres seuils et d'autres stops, et adopte
    sur-le-champ les règles qui auraient le plus rapporté.

Il ne touche jamais au bot principal ni à l'argent réel : c'est une
expérience côte à côte. Le panneau montre les deux capitaux, pour juger sur
pièces lequel gagne.
"""

from __future__ import annotations

import itertools
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .texte import fr

MIN_VIEW = 0.2              # avis net d'une source
LEARN_RATE = 0.2            # vitesse d'apprentissage des poids
WEIGHT_MIN, WEIGHT_MAX = 0.05, 20.0
MAX_POSITIONS = 5
POSITION_PCT = 0.20         # part du capital par position
COST = 0.002                # frais + glissement, par côté
MIN_ORDER = 10.0            # en dessous, pas d'ordre (Binance : 5 USDT)
KEEP_DAYS = 120             # avis gardés pour se rejouer
TUNE_EVERY = 7              # jours entre deux révisions de ses règles
TUNE_MIN_DAYS = 14          # avis nécessaires avant la première révision
DEFAULT_RULES = {"buy": 0.3, "sell": -0.2, "stop": 0.08}
GRID = {"buy": (0.2, 0.3, 0.5), "sell": (-0.1, -0.2, -0.4), "stop": (0.05, 0.08, 0.12)}


def new(capital: float, day: str) -> Dict[str, Any]:
    return {"capital": float(capital), "cash": float(capital), "holdings": {}, "trades": [],
            "weights": {}, "rules": dict(DEFAULT_RULES), "opinions": {}, "equity": [],
            "started": day, "tuned": None, "notes": []}


def learn(weights: Dict[str, float], views: Dict[str, Dict[str, float]],
          moves: Dict[str, float]) -> Dict[str, float]:
    """Poids des sources après la journée : chacune est récompensée quand
    ses avis nets de la veille ont vu juste sur le cours du jour."""
    out = dict(weights)
    for source, by_asset in views.items():
        scores = [(1.0 if (v > 0) == (moves[a] > 0) else -1.0) * min(1.0, abs(v))
                  for a, v in by_asset.items() if abs(v) >= MIN_VIEW and a in moves and moves[a] != 0]
        if not scores:
            continue
        w = out.get(source, 1.0) * math.exp(LEARN_RATE * sum(scores) / len(scores))
        out[source] = min(WEIGHT_MAX, max(WEIGHT_MIN, w))
    return out


def opinion(weights: Dict[str, float], views: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """Avis du bot libre sur chaque crypto : toutes les sources, pondérées
    par ce qu'il a appris d'elles."""
    acc: Dict[str, List[Tuple[float, float]]] = {}
    for source, by_asset in views.items():
        w = weights.get(source, 1.0)
        for a, v in by_asset.items():
            if abs(v) >= MIN_VIEW:
                acc.setdefault(a, []).append((w, v))
    return {a: round(sum(w * v for w, v in rows) / sum(w for w, _v in rows), 3) for a, rows in acc.items()}


def equity(book: Dict[str, Any], prices: Dict[str, float]) -> float:
    return book["cash"] + sum(h["qty"] * prices.get(a, h["entry"]) for a, h in book["holdings"].items())


def trade_day(book: Dict[str, Any], day: str, prices: Dict[str, float], op: Dict[str, float],
              rules: Dict[str, float], record: bool = True) -> List[str]:
    """Une journée de décisions : ventes (stop touché ou avis baissier),
    puis achats des cryptos vues en hausse, les plus haut placées d'abord.
    Renvoie les actions, en clair. Une crypto vendue n'est pas rachetée le
    même jour."""
    acts, sold = [], set()
    for a in sorted(book["holdings"]):
        h, px = book["holdings"][a], prices.get(a)
        if px is None:
            continue
        why = ("stop" if px <= h["entry"] * (1 - rules["stop"])
               else "avis baissier" if op.get(a, 0.0) <= rules["sell"] else "")
        if not why:
            continue
        proceeds = h["qty"] * px * (1 - COST)
        book["cash"] += proceeds
        del book["holdings"][a]
        sold.add(a)
        if record:
            book["trades"] = (book["trades"] + [{"asset": a, "entry_date": h["date"], "date": day,
                                                 "pnl": round(proceeds - h["cost"], 2), "reason": why}])[-200:]
        acts.append(f"vend {a.upper()} ({why})")
    eq = equity(book, prices)
    for a, v in sorted(op.items(), key=lambda kv: -kv[1]):
        if v < rules["buy"] or len(book["holdings"]) >= MAX_POSITIONS:
            break
        px = prices.get(a)
        if a in book["holdings"] or a in sold or not px:
            continue
        cost = min(book["cash"], POSITION_PCT * eq)
        if cost < MIN_ORDER:
            break
        book["cash"] -= cost
        book["holdings"][a] = {"qty": cost * (1 - COST) / px, "entry": px, "cost": cost, "date": day}
        acts.append(f"achète {a.upper()} (avis {fr(v, '+.2f')})")
    return acts


def replay(opinions: Dict[str, Dict[str, float]], close: Any, rules: Dict[str, float],
           capital: float = 1000.0) -> float:
    """Rejoue les avis passés avec d'autres règles : capital final."""
    index = {str(d.date()): i for i, d in enumerate(close.index)}
    book = new(capital, "")
    last: Dict[str, float] = {}
    for day in sorted(opinions):
        if day not in index:
            continue
        row = close.iloc[index[day]]
        last = {a: float(row[a]) for a in close.columns if math.isfinite(float(row[a]))}
        trade_day(book, day, last, opinions[day], rules, record=False)
    return equity(book, last) if last else capital


def tune(opinions: Dict[str, Dict[str, float]], close: Any, current: Dict[str, float]) -> Dict[str, float]:
    """Les règles qui auraient le plus rapporté sur ses propres avis passés
    (les règles actuelles en cas d'égalité)."""
    best, best_eq = dict(current), replay(opinions, close, current)
    for buy, sell, stop in itertools.product(GRID["buy"], GRID["sell"], GRID["stop"]):
        rules = {"buy": buy, "sell": sell, "stop": stop}
        eq = replay(opinions, close, rules)
        if eq > best_eq + 1e-9:
            best, best_eq = rules, eq
    return best


def step(book: Dict[str, Any], day: str, close: Any, today: Dict[str, Dict[str, float]],
         yesterday: Dict[str, Dict[str, float]]) -> List[str]:
    """La journée du bot libre, à la décision du bot principal : il apprend
    de la veille, révise ses règles chaque semaine, se fait son avis, agit."""
    days = [str(d.date()) for d in close.index]
    if day not in days or day == book.get("last_day"):
        return []
    i = days.index(day)
    row = close.iloc[i]
    prices = {a: float(row[a]) for a in close.columns if math.isfinite(float(row[a]))}
    notes = []
    if i > 0:
        prev = close.iloc[i - 1]
        moves = {a: prices[a] / float(prev[a]) - 1 for a in prices
                 if math.isfinite(float(prev[a])) and float(prev[a]) > 0}
        book["weights"] = learn(book.get("weights") or {}, yesterday, moves)
    op = opinion(book["weights"], today)
    book["opinions"][day] = op
    cutoff = (date.fromisoformat(day) - timedelta(days=KEEP_DAYS)).isoformat()
    book["opinions"] = {d: o for d, o in book["opinions"].items() if d >= cutoff}
    due = not book.get("tuned") or (date.fromisoformat(day) - date.fromisoformat(book["tuned"])).days >= TUNE_EVERY
    if len(book["opinions"]) >= TUNE_MIN_DAYS and due:
        rules = tune(book["opinions"], close, book["rules"])
        book["tuned"] = day
        if rules != book["rules"]:
            notes.append(f"nouvelles règles adoptées : achat dès {fr(rules['buy'], '+.1f')}, vente sous "
                         f"{fr(rules['sell'], '+.1f')}, stop à −{fr(rules['stop'] * 100, '.0f')} %")
            book["rules"] = rules
    notes += trade_day(book, day, prices, op, book["rules"])
    book["equity"] = (book["equity"] + [[day, round(equity(book, prices), 2)]])[-400:]
    book["last_day"] = day
    book["notes"] = notes
    return notes


def summary(book: Optional[Dict[str, Any]], main_equity: Optional[float] = None,
            main_start: Optional[float] = None) -> Dict[str, Any]:
    """Ce que le panneau et le rapport montrent du bot libre, comparé au bot
    principal."""
    if not book:
        return {}
    eq = book["equity"][-1][1] if book.get("equity") else book["capital"]
    pct = (eq / book["capital"] - 1) * 100 if book["capital"] else 0.0
    main_pct = ((main_equity / main_start - 1) * 100 if main_equity and main_start else None)
    top = sorted((book.get("weights") or {}).items(), key=lambda kv: -kv[1])[:3]
    text = (f"Bot libre (argent fictif) : {fr(eq, ',.2f')} USDT ({fr(pct, '+.1f')} % depuis le "
            f"{book.get('started')}), {len(book['holdings'])} position(s)"
            + (f" ; bot principal : {fr(main_pct, '+.1f')} %" if main_pct is not None else "")
            + f" ; règles : achat dès {fr(book['rules']['buy'], '+.1f')}, stop à "
              f"−{fr(book['rules']['stop'] * 100, '.0f')} %")
    return {"equity": eq, "pct": round(pct, 2), "main_pct": None if main_pct is None else round(main_pct, 2),
            "positions": sorted(book["holdings"]), "rules": book["rules"], "notes": book.get("notes") or [],
            "trades": len(book.get("trades") or []), "top_sources": [[s, round(w, 2)] for s, w in top],
            "curve": book.get("equity") or [], "text": text}


def last_views(memory: Any, day: str) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]:
    """Avis des sources du jour et de la veille, lus dans le noyau de savoir."""
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    return memory.views_of(day), memory.views_of(prev)
