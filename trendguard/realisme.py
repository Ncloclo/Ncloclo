"""
Exécution paper au plus près du réel : en paper, chaque achat et chaque vente
passe au vrai carnet d'ordres de Binance du moment, niveau par niveau, aux
vrais frais de Binance, en quantités arrondies aux règles de la paire. Les
stops sont surveillés sur les plus bas de chaque minute, comme un ordre
STOP_LOSS posé chez Binance.

Avant : prix du dernier échange, plus 0,1 % de glissement pour toutes les
cryptos, et stop vérifié une fois par minute sur ce seul prix, si bien qu'une
mèche entre deux relevés passait inaperçue.

Le rejeu et les études gardent l'ancien modèle, faute de carnet d'ordres dans
le passé : la parité rejeu = backtest reste exacte. Activé par
TG_PAPER_REEL=true, la valeur par défaut dans l'environnement ; désactivé
dans le code pour les essais et le rejeu.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

BOOK_LIMIT = 100            # niveaux lus de chaque côté du carnet
BNB_DISCOUNT = 0.25         # frais payés en BNB : 25 % de moins chez Binance
MINUTE_MS = 60_000
MINUTE_PAGES = 12           # au plus 12 × 1000 minutes relues (≈ 8 jours d'arrêt)


@dataclass(frozen=True)
class Fill:
    """Résultat d'un ordre au marché simulé sur le carnet du moment."""
    side: str            # "buy" (traverse les vendeurs) ou "sell" (traverse les acheteurs)
    price: float         # prix moyen obtenu
    qty: float           # quantité demandée
    best: float          # meilleur prix du côté traversé
    mid: float           # milieu du carnet
    complete: bool       # le carnet lu couvrait toute la quantité

    @property
    def cost_pct(self) -> float:
        """Coût d'exécution par rapport au milieu du carnet (demi-écart plus
        profondeur traversée), toujours positif ou nul."""
        if self.mid <= 0:
            return 0.0
        return max(0.0, (self.price / self.mid - 1) if self.side == "buy"
                   else (1 - self.price / self.mid))

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["cost_pct"] = self.cost_pct
        return d


def fee_rate(base_fee: float, bnb: bool) -> float:
    """Frais Binance d'un ordre au marché (taker) : 0,1 % au niveau de base,
    25 % de moins si les frais sont payés en BNB."""
    return base_fee * (1 - BNB_DISCOUNT) if bnb else base_fee


def _levels(raw: Optional[Sequence[Sequence[Any]]]) -> List[Tuple[float, float]]:
    out = []
    for lv in raw or []:
        try:
            p, q = float(lv[0]), float(lv[1])
        except (TypeError, ValueError, IndexError):
            continue
        if p > 0 and q > 0:
            out.append((p, q))
    return out


def walk(levels: Sequence[Tuple[float, float]], qty: float) -> Optional[Tuple[float, float]]:
    """(prix moyen, quantité exécutée) d'un ordre au marché de `qty` qui
    traverse les niveaux dans l'ordre ; None si rien n'est exécuté."""
    if qty <= 0:
        return None
    left, cost, done = qty, 0.0, 0.0
    for p, q in levels:
        take = min(q, left)
        cost += take * p
        done += take
        left -= take
        if left <= qty * 1e-12:
            break
    if done <= 0:
        return None
    return cost / done, done


def fill(book: Dict[str, Any], side: str, qty: float) -> Optional[Fill]:
    """Ordre au marché simulé sur un carnet `{"bids": [[prix, qté], …],
    "asks": […]}` ; None si le carnet est vide ou incohérent. Carnet lu
    trop court : le reste est compté au-delà du dernier niveau lu, aussi
    loin encore en proportion (hypothèse prudente, prix toujours positif)."""
    bids, asks = _levels(book.get("bids")), _levels(book.get("asks"))
    if not bids or not asks or qty <= 0 or side not in ("buy", "sell"):
        return None
    bid, ask = bids[0][0], asks[0][0]
    if ask < bid:
        return None
    levels = asks if side == "buy" else bids
    best = ask if side == "buy" else bid
    w = walk(levels, qty)
    if w is None:
        return None
    px, done = w
    complete = done >= qty * (1 - 1e-9)
    if not complete:
        last = levels[-1][0]
        worse = last * (last / best)          # au-delà du carnet lu, aussi loin encore
        px = (px * done + worse * (qty - done)) / qty
    return Fill(side, px, qty, best, (bid + ask) / 2, complete)


def read_book(exchange: Any, symbol: str) -> Optional[Dict[str, Any]]:
    """Carnet d'ordres du moment, ou None (pas de carnet, réseau, carnet vide)."""
    fetch = getattr(exchange, "fetch_order_book", None)
    if fetch is None:
        return None
    try:
        book = fetch(symbol, limit=BOOK_LIMIT)
    except Exception:
        return None
    if not isinstance(book, dict) or not book.get("bids") or not book.get("asks"):
        return None
    return book


def stop_touch(bars: Sequence[Sequence[Any]], stop: float,
               since_ms: int) -> Optional[Tuple[int, float]]:
    """Première minute, depuis `since_ms`, dont le plus bas touche `stop` :
    (début de la minute en ms, prix de déclenchement). Le prix vaut le stop,
    ou l'ouverture de la minute quand elle a ouvert dessous (trou de
    cotation : un STOP_LOSS s'exécute alors sous le stop)."""
    for b in bars:
        try:
            t, o, low = int(b[0]), float(b[1]), float(b[3])
        except (TypeError, ValueError, IndexError):
            continue
        if t + MINUTE_MS <= since_ms:
            continue                          # minute entièrement avant la surveillance
        if low <= stop:
            gap = o < stop and t >= since_ms
            return t, (o if gap else stop)
    return None


def minute_bars(exchange: Any, symbol: str, since_ms: int,
                now_ms: int) -> Optional[List[List[Any]]]:
    """Bougies d'une minute de `since_ms` à maintenant (plusieurs pages après
    un arrêt) ; None si elles sont illisibles ou absentes (Binance renvoie
    toujours au moins la minute en cours) : le bot revient alors au dernier
    prix relevé."""
    fetch = getattr(exchange, "fetch_ohlcv", None)
    if fetch is None:
        return None
    out: List[List[Any]] = []
    since = since_ms - since_ms % MINUTE_MS
    for _ in range(MINUTE_PAGES):
        try:
            page = fetch(symbol, "1m", since=since, limit=1000)
        except Exception:
            return None if not out else out
        page = [b for b in page or [] if int(b[0]) >= since]
        if not page:
            break
        out.extend(page)
        nxt = int(page[-1][0]) + MINUTE_MS
        if nxt > now_ms or len(page) < 1000:
            break
        since = nxt
    return out or None


def sell_price(trigger: Optional[float], book_fill: Fill) -> float:
    """Prix moyen d'une vente au marché. Sortie du jour (trigger None) : le
    carnet du moment. Stop déclenché au prix `trigger` : le même trajet dans
    le carnet, mais à partir du prix de déclenchement."""
    if trigger is None or book_fill.best <= 0:
        return book_fill.price
    return trigger * book_fill.price / book_fill.best
