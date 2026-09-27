"""Simulateur Binance Spot minimal mais fidèle sur les points qui comptent
pour la gestion du risque :

- soldes libres / bloqués (un ordre de vente ouvert bloque la base) ;
- frais d'achat prélevés en BASE (comportement par défaut sans BNB) ;
- types d'ordres Spot (STOP_LOSS_MARKET / TAKE_PROFIT_MARKET refusés) ;
- OCO natif (orderList) : une jambe exécutée → l'autre expire ;
  annuler une jambe annule la liste ;
- GET /api/v3/orderList refuse le paramètre `symbol` (-1104) ;
- filtres LOT_SIZE / PRICE_FILTER sur l'endpoint brut OCO (-1013) et
  paramètres obligatoires (-1102), client-id en double refusé (-2010) ;
- exécutions partielles (partial_fill) : dès qu'une jambe d'OCO est
  exécutée, même partiellement, l'autre expire ;
- injection de pannes avant/après placement (create_faults) :
  "before"/"after" (timeout), "internal_after" (-1001, statut inconnu),
  "unavailable_after" (HTTP 503), "ratelimit" (429, ordre non transmis).

- carnet à profondeur finie (book_depth) : les ordres au marché parcourent
  les niveaux (slippage) et le reliquat non exécutable EXPIRE ; les ordres
  au repos s'exécutent par tranches (exécutions partielles naturelles) ;
- filtres PERCENT_PRICE_BY_SIDE, MAX_NUM_ALGO_ORDERS (5 stops par paire)
  et MAX_NUM_ORDERS ;
- on_call(nom) : rappel au début de chaque appel API, pour provoquer une
  exécution ENTRE deux appels du bot (courses) ;
- identifiants d'ordres numérotés par paire (FakeBinanceMulti), comme sur
  Binance : deux paires peuvent avoir le même orderId ;
- horloge : le serveur a sa propre heure (server_offset_ms = heure du
  serveur − heure du PC) ; un ordre signé dont l'horodatage (heure du PC −
  options["timeDifference"], comme ccxt) s'écarte de plus de 10 s de
  l'heure du serveur est refusé (-1021) ; fetch_time() donne l'heure du
  serveur (avec un délai réseau simulé optionnel, latency_s) ;
- create_order_request : prépare une requête sans l'envoyer (comme ccxt).

Par défaut (book_depth=None) la liquidité est infinie.
"""

from __future__ import annotations

import itertools
import time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Callable, Dict, List, Optional, Tuple

import ccxt

SPOT_TYPES = ["LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS", "STOP_LOSS_LIMIT",
              "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"]
ALGO_TYPES = ("STOP_LOSS", "STOP_LOSS_LIMIT", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
LIMIT_PRICED = ("LIMIT", "LIMIT_MAKER", "STOP_LOSS_LIMIT", "TAKE_PROFIT_LIMIT")

_CCXT_STATUS = {"NEW": "open", "PARTIALLY_FILLED": "open", "FILLED": "closed",
                "CANCELED": "canceled", "EXPIRED": "expired"}


def _on_grid(value: str, step: float) -> bool:
    """True si `value` (chaîne envoyée à l'API) est un multiple exact de step."""
    return (Decimal(str(value)) / Decimal(str(step))) % 1 == 0


def _floor(x: float, step: float) -> float:
    q = (Decimal(str(x)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)
    return float(q * Decimal(str(step)))


class FakeBinance:
    def __init__(self, symbol: str = "TRX/USDT", price: float = 0.10,
                 quote_balance: float = 1000.0, base_balance: float = 0.0,
                 fee_rate: float = 0.001, fee_mode: str = "base",
                 step: float = 0.1, tick: float = 0.00001,
                 min_notional: float = 5.0,
                 order_types: Optional[List[str]] = None,
                 spread: float = 0.0002,
                 book_depth: Optional[float] = None, book_levels: int = 10,
                 level_step: float = 0.001,
                 percent_price: Tuple[float, float] = (0.2, 5.0),
                 max_algo_orders: int = 5, max_orders: int = 200):
        self.symbol = symbol
        self.base, self.quote = symbol.split("/")
        self.market_id = self.base + self.quote
        self.fee_rate = fee_rate
        self.fee_mode = fee_mode            # "base" | "bnb"
        self.step = step
        self.tick = tick
        self.min_notional_value = min_notional
        self.order_types = list(order_types or SPOT_TYPES)
        self.spread = spread
        self.book_depth = book_depth          # base par niveau ; None = infini
        self.book_levels = book_levels
        self.level_step = level_step          # écart de prix entre niveaux
        self.percent_price = percent_price    # (multiplicateur bas, haut)
        self.max_algo_orders = max_algo_orders
        self.max_orders = max_orders
        self.on_call: Optional[Callable[[str], None]] = None
        self._in_hook = False
        self.free: Dict[str, float] = {self.quote: quote_balance,
                                       self.base: base_balance, "BNB": 10.0}
        self.locked: Dict[str, float] = {self.quote: 0.0, self.base: 0.0,
                                         "BNB": 0.0}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.lists: Dict[str, Dict[str, Any]] = {}
        self.trades: List[Dict[str, Any]] = []
        self.ohlcv: Dict[Any, List[List[float]]] = {}
        self._ids = itertools.count(5_000_000_000)
        self._list_ids = itertools.count(1)
        self._trade_ids = itertools.count(1)
        self.create_faults: List[str] = []   # "before" | "after"
        self.fail_fetch_order_n = 0
        self.fail_balance = False
        self.fetch_includes_fees = False
        self.calls: List[str] = []
        self.markets: Dict[str, Any] = {}
        self.server_offset_ms = 0.0
        self.latency_s = 0.0
        self.recv_window_ms = 10_000
        self.options: Dict[str, Any] = {"timeDifference": 0}
        self.time_calls = 0
        self.set_price(price)

    # ---------- horloge ----------

    def fetch_time(self):
        """Heure du serveur, lue au milieu d'un aller-retour de latency_s."""
        self.time_calls += 1
        if self.latency_s:
            time.sleep(self.latency_s / 2)
        server = int(time.time() * 1000 + self.server_offset_ms)
        if self.latency_s:
            time.sleep(self.latency_s / 2)
        return server

    def load_time_difference(self):
        """Méthode de ccxt : heure de RÉCEPTION − heure du serveur."""
        server = self.fetch_time()
        self.options["timeDifference"] = int(time.time() * 1000) - server
        return self.options["timeDifference"]

    def _check_timestamp(self):
        signed = time.time() * 1000 - float(self.options.get("timeDifference", 0))
        server = time.time() * 1000 + self.server_offset_ms
        if abs(signed - server) > self.recv_window_ms:
            raise ccxt.InvalidNonce('binance {"code":-1021,"msg":"Timestamp for '
                                    'this request is outside of the recvWindow."}')

    # ---------- marché ----------

    def load_markets(self):
        self.markets = {self.symbol: self.market(self.symbol)}
        return self.markets

    def market(self, symbol):
        return {
            "id": self.market_id, "symbol": self.symbol, "base": self.base,
            "quote": self.quote, "spot": True, "active": True,
            "limits": {"amount": {"min": self.step, "max": 1e9},
                       "cost": {"min": self.min_notional_value}},
            "info": {"symbol": self.market_id, "ocoAllowed": True,
                     "orderTypes": list(self.order_types),
                     "filters": [
                         {"filterType": "LOT_SIZE", "stepSize": str(self.step),
                          "minQty": str(self.step)},
                         {"filterType": "PRICE_FILTER", "tickSize": str(self.tick)},
                         {"filterType": "NOTIONAL",
                          "minNotional": str(self.min_notional_value)},
                         {"filterType": "PERCENT_PRICE_BY_SIDE",
                          "bidMultiplierUp": str(self.percent_price[1]),
                          "bidMultiplierDown": str(self.percent_price[0]),
                          "askMultiplierUp": str(self.percent_price[1]),
                          "askMultiplierDown": str(self.percent_price[0]),
                          "avgPriceMins": 5},
                         {"filterType": "MAX_NUM_ALGO_ORDERS",
                          "maxNumAlgoOrders": self.max_algo_orders},
                         {"filterType": "MAX_NUM_ORDERS",
                          "maxNumOrders": self.max_orders}]}}

    def amount_to_precision(self, symbol, amount):
        return format(Decimal(str(_floor(float(amount), self.step))).normalize(), "f")

    def price_to_precision(self, symbol, price):
        q = (Decimal(str(price)) / Decimal(str(self.tick))).to_integral_value(
            rounding=ROUND_HALF_UP) * Decimal(str(self.tick))
        return format(q.normalize(), "f")

    def set_sandbox_mode(self, flag):
        self.sandbox = flag

    # ---------- prix & déclenchements ----------

    def _hook(self, name: str) -> None:
        """Rappel on_call(nom) au début de chaque appel API (non réentrant)."""
        cb = self.on_call
        if cb is not None and not self._in_hook:
            self._in_hook = True
            try:
                cb(name)
            finally:
                self._in_hook = False

    def set_price(self, last: float) -> None:
        self.last = float(last)
        self.bid = self.last * (1 - self.spread / 2)
        self.ask = self.last * (1 + self.spread / 2)
        self._process_triggers()

    def fetch_ticker(self, symbol):
        self._hook("fetch_ticker")
        return {"last": self.last, "bid": self.bid, "ask": self.ask}

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=500):
        bars = self.ohlcv.get((symbol, timeframe), [])
        if since is not None:
            bars = [b for b in bars if b[0] >= since]
            return [list(b) for b in bars[:limit]]
        return [list(b) for b in bars[-limit:]]

    def fetch_order_book(self, symbol, limit=20):
        return {"bids": [[self.bid, 1e6]], "asks": [[self.ask, 1e6]]}

    # ---------- soldes ----------

    def fetch_balance(self):
        self.calls.append("fetch_balance")
        self._hook("fetch_balance")
        if self.fail_balance:
            raise ccxt.NetworkError("balance timeout")
        total = {k: self.free.get(k, 0.0) + self.locked.get(k, 0.0)
                 for k in set(self.free) | set(self.locked)}
        return {"free": dict(self.free), "used": dict(self.locked),
                "total": total}

    def _lock(self, asset, qty):
        if self.free.get(asset, 0.0) + 1e-12 < qty:
            raise ccxt.InsufficientFunds(
                "binance -2010 Account has insufficient balance for requested action.")
        self.free[asset] -= qty
        self.locked[asset] = self.locked.get(asset, 0.0) + qty

    def _unlock(self, asset, qty):
        self.locked[asset] -= qty
        self.free[asset] += qty

    # ---------- ordres ----------

    def _new_order(self, otype, side, amount, price, stop, cid, list_id=None):
        oid = str(next(self._ids))
        o = {"id": oid, "cid": cid or f"auto{oid}", "type": otype, "side": side,
             "amount": float(amount), "price": price, "stop": stop,
             "status": "NEW", "filled": 0.0, "cost": 0.0, "fees": [],
             "list_id": list_id, "locked": 0.0, "lock_asset": None,
             "triggered": False, "ts": int(time.time() * 1000)}
        self.orders[oid] = o
        return o

    def _fill(self, o, qty, px, maker=False):
        qty = float(qty)
        if o["side"] == "buy":
            cost = qty * px
            if o["lock_asset"] == self.quote:
                release = min(o["locked"], qty * (o["price"] or px))
                self.locked[self.quote] -= release
                self.free[self.quote] += release
                o["locked"] -= release
            if self.free[self.quote] + 1e-9 < cost:
                raise ccxt.InsufficientFunds("-2010 insufficient quote")
            self.free[self.quote] -= cost
            if self.fee_mode == "base":
                fee = qty * self.fee_rate
                self.free[self.base] += qty - fee
                o["fees"].append({"currency": self.base, "cost": fee})
            else:
                fee = cost * self.fee_rate * 0.75 / 300.0
                self.free["BNB"] -= fee
                self.free[self.base] += qty
                o["fees"].append({"currency": "BNB", "cost": fee})
        else:
            if o["lock_asset"] == self.base:
                self.locked[self.base] -= qty
                o["locked"] -= qty
            else:
                if self.free[self.base] + 1e-9 < qty:
                    raise ccxt.InsufficientFunds("-2010 insufficient base")
                self.free[self.base] -= qty
            proceeds = qty * px
            if self.fee_mode == "base":
                fee = proceeds * self.fee_rate
                self.free[self.quote] += proceeds - fee
                o["fees"].append({"currency": self.quote, "cost": fee})
            else:
                fee = proceeds * self.fee_rate * 0.75 / 300.0
                self.free["BNB"] -= fee
                self.free[self.quote] += proceeds
                o["fees"].append({"currency": "BNB", "cost": fee})
        o["filled"] += qty
        o["cost"] += qty * px
        o["status"] = "FILLED" if o["filled"] >= o["amount"] - 1e-12 \
            else "PARTIALLY_FILLED"
        self.trades.append({"id": str(next(self._trade_ids)), "order": o["id"],
                            "side": o["side"], "amount": qty, "price": px,
                            "cost": qty * px,
                            "timestamp": int(time.time() * 1000)})

    def _expire_list_siblings(self, o):
        lst = self.lists[o["list_id"]]
        for sid in lst["orders"]:
            s = self.orders[sid]
            if sid != o["id"] and s["status"] in ("NEW", "PARTIALLY_FILLED"):
                s["status"] = "EXPIRED"

    def _avail(self, remaining: float) -> float:
        """Quantité exécutable en une fois pour un ordre au repos."""
        return remaining if self.book_depth is None else min(remaining, self.book_depth)

    def _book_fill(self, o, qty):
        """Exécution au marché : parcourt le carnet niveau par niveau
        (slippage) ; le reliquat non exécutable expire, comme sur Binance."""
        buy = o["side"] == "buy"
        best = self.ask if buy else self.bid
        left = float(qty)
        levels = 1 if self.book_depth is None else self.book_levels
        for i in range(levels):
            if left <= 1e-12:
                break
            px = best * (1 + i * self.level_step) if buy \
                else best * (1 - i * self.level_step)
            q = left if self.book_depth is None else min(left, self.book_depth)
            if buy and self.free[self.quote] < q * px:     # fonds épuisés
                q = self.free[self.quote] / px
                left = q
            if q <= 1e-12:
                break
            if buy:
                self._fill(o, q, px)
            else:
                self._fill_list_leg(o, q, px)
            left -= q
        if o["status"] in ("NEW", "PARTIALLY_FILLED") and o["type"] in ("MARKET", "STOP_LOSS"):
            o["status"] = "EXPIRED"                        # carnet épuisé
            if o["lock_asset"] and o["locked"] > 1e-12:
                self._unlock(o["lock_asset"], o["locked"])
                o["locked"] = 0.0
            if o.get("list_id"):
                self.lists[o["list_id"]]["status"] = "ALL_DONE"

    def _process_triggers(self):
        for o in list(self.orders.values()):
            if o["status"] not in ("NEW", "PARTIALLY_FILLED"):
                continue
            t = o["type"]
            rem = o["amount"] - o["filled"]
            if o["side"] == "sell" and t == "LIMIT_MAKER" and self.bid >= o["price"]:
                self._fill_list_leg(o, self._avail(rem), o["price"])
            elif o["side"] == "sell" and t in ("STOP_LOSS", "STOP_LOSS_LIMIT"):
                if not o["triggered"] and self.last <= o["stop"]:
                    o["triggered"] = True
                if o["triggered"]:
                    if t == "STOP_LOSS":                  # devient un ordre au marché
                        self._book_fill(o, rem)
                    elif self.bid >= o["price"]:
                        self._fill_list_leg(o, self._avail(rem), self.bid)
            elif o["side"] == "buy" and t == "LIMIT" and self.ask <= o["price"]:
                self._fill(o, self._avail(rem), o["price"], maker=True)

    def _fill_list_leg(self, o, qty, px):
        """Binance : dès qu'une jambe d'OCO est exécutée, même partiellement,
        l'autre jambe expire. La jambe exécutée garde son reliquat ouvert."""
        if o.get("list_id"):
            lst = self.lists[o["list_id"]]
            if lst["locked"] > 0:                  # 1re exécution de la liste
                o["lock_asset"], o["locked"] = self.base, lst["locked"]
                lst["locked"] = 0.0
                self._expire_list_siblings(o)
            self._fill(o, qty, px)
            if o["status"] == "FILLED":
                lst["status"] = "ALL_DONE"
        else:
            self._fill(o, qty, px)

    def partial_fill(self, order_id, qty, price=None):
        """Exécution partielle forcée d'un ordre ouvert (liquidité réduite)."""
        o = self.orders[str(order_id)]
        if o["status"] not in ("NEW", "PARTIALLY_FILLED"):
            raise ValueError(f"ordre {order_id} non ouvert")
        qty = min(float(qty), o["amount"] - o["filled"])
        px = price if price is not None else (
            o["price"] if o["price"] else (self.bid if o["side"] == "sell" else self.ask))
        if o["side"] == "sell":
            self._fill_list_leg(o, qty, px)
        else:
            self._fill(o, qty, px, maker=True)

    def _maybe_fault(self):
        if self.create_faults:
            return self.create_faults.pop(0)
        return None

    @staticmethod
    def _raise_pre(fault, what):
        if fault == "before":
            raise ccxt.RequestTimeout(f"timeout ({what} non transmis)")
        if fault == "ratelimit":
            raise ccxt.RateLimitExceeded('binance {"code":-1003,"msg":"Too many requests"}')

    @staticmethod
    def _raise_post(fault, what):
        if fault == "after":
            raise ccxt.RequestTimeout(f"timeout ({what} transmis)")
        if fault == "internal_after":
            raise ccxt.OperationFailed(
                'binance {"code":-1001,"msg":"Internal error; unable to process '
                'your request. Please try again."}')
        if fault == "unavailable_after":
            raise ccxt.ExchangeNotAvailable("binance 503 Service Unavailable")

    def _check_filters(self, new_types, priced):
        """MAX_NUM_ORDERS, MAX_NUM_ALGO_ORDERS et PERCENT_PRICE_BY_SIDE."""
        opened = [o for o in self.orders.values()
                  if o["status"] in ("NEW", "PARTIALLY_FILLED")]
        if len(opened) + len(new_types) > self.max_orders:
            raise ccxt.BadRequest('binance {"code":-1013,"msg":"Filter failure: MAX_NUM_ORDERS"}')
        algo = sum(o["type"] in ALGO_TYPES for o in opened) \
            + sum(t in ALGO_TYPES for t in new_types)
        if algo > self.max_algo_orders:
            raise ccxt.BadRequest(
                'binance {"code":-1013,"msg":"Filter failure: MAX_NUM_ALGO_ORDERS"}')
        lo, hi = self.percent_price
        for px in priced:
            if px is not None and not (self.last * lo <= px <= self.last * hi):
                raise ccxt.BadRequest(
                    'binance {"code":-1013,"msg":"Filter failure: PERCENT_PRICE_BY_SIDE"}')

    def _check_cid(self, *cids):
        for cid in cids:
            if cid and any(o["cid"] == cid and o["status"] in ("NEW", "PARTIALLY_FILLED")
                           for o in self.orders.values()):
                raise ccxt.InvalidOrder('binance {"code":-2010,"msg":"Duplicate order sent."}')

    def create_order_request(self, symbol, type, side, amount, price=None,
                             params=None):
        """Comme ccxt : requête préparée SANS envoi (type d'ordre du marché,
        prix et stopPrice obligatoires, quantité au pas)."""
        otype = str(type).upper()
        params = dict(params or {})
        if otype not in self.order_types:
            raise ccxt.InvalidOrder(
                f"binance {type} is not a valid order type for the {symbol} market")
        if otype in ("LIMIT", "STOP_LOSS_LIMIT", "TAKE_PROFIT_LIMIT", "LIMIT_MAKER") \
                and price is None:
            raise ccxt.ArgumentsRequired(f"binance {type} requires a price")
        if otype in ("STOP_LOSS", "STOP_LOSS_LIMIT") and params.get("stopPrice") is None:
            raise ccxt.InvalidOrder(f"binance {type} requires a stopPrice")
        req = {"symbol": self.market_id, "side": side.upper(), "type": otype,
               "quantity": self.amount_to_precision(symbol, amount)}
        if price is not None:
            req["price"] = self.price_to_precision(symbol, price)
        if params.get("stopPrice") is not None:
            req["stopPrice"] = self.price_to_precision(symbol, params.pop("stopPrice"))
        req.update(params)
        return req

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        self._hook("create_order")
        params = dict(params or {})
        self.calls.append(f"create_order:{type}:{side}")
        self._check_timestamp()
        otype = str(type).upper()
        if otype not in self.order_types:
            raise ccxt.InvalidOrder(
                f"binance {type} is not a valid order type for the {symbol} market")
        fault = self._maybe_fault()
        self._raise_pre(fault, "ordre")
        if otype in ("LIMIT", "STOP_LOSS_LIMIT", "TAKE_PROFIT_LIMIT", "LIMIT_MAKER") \
                and price is None:
            raise ccxt.BadRequest('binance {"code":-1102,"msg":"Mandatory parameter '
                                  "'price' was not sent\"}")
        if otype in ("STOP_LOSS", "STOP_LOSS_LIMIT") and params.get("stopPrice") is None:
            raise ccxt.BadRequest('binance {"code":-1102,"msg":"Mandatory parameter '
                                  "'stopPrice' was not sent\"}")
        # Comme ccxt : quantité tronquée au pas, prix arrondis au tick.
        amount = _floor(float(amount), self.step)
        cid = params.get("newClientOrderId")
        self._check_cid(cid)
        stop = (float(self.price_to_precision(symbol, params["stopPrice"]))
                if params.get("stopPrice") is not None else None)
        px = float(self.price_to_precision(symbol, price)) if price is not None else None
        ref = self.ask if side == "buy" else self.bid
        if amount * (px or ref) < self.min_notional_value:
            raise ccxt.InvalidOrder("-1013 Filter failure: NOTIONAL")
        self._check_filters([otype], [px] if otype in LIMIT_PRICED else [])
        if otype == "MARKET":
            need = (amount * self.ask) if side == "buy" else amount
            asset = self.quote if side == "buy" else self.base
            if self.free.get(asset, 0.0) + 1e-9 < need:
                raise ccxt.InsufficientFunds(
                    "binance -2010 Account has insufficient balance for requested action.")
        o = self._new_order(otype, side, amount, px, stop, cid)
        try:
            if otype == "MARKET":
                self._book_fill(o, amount)
            elif otype == "LIMIT" and side == "buy":
                self._lock(self.quote, amount * px)
                o["lock_asset"], o["locked"] = self.quote, amount * px
                if px >= self.ask:                          # preneur
                    self._fill(o, self._avail(amount), self.ask)
            elif otype in ("STOP_LOSS", "STOP_LOSS_LIMIT") and side == "sell":
                if stop is None or stop >= self.last:
                    raise ccxt.InvalidOrder("-2010 Stop price would trigger immediately.")
                self._lock(self.base, amount)
                o["lock_asset"], o["locked"] = self.base, amount
            else:
                raise ccxt.InvalidOrder(f"type {otype}/{side} non simulé")
        except Exception:
            del self.orders[o["id"]]
            raise
        self._raise_post(fault, "ordre")
        return self._ccxt(o, include_fees=True)

    def _ccxt(self, o, include_fees=False):
        filled = o["filled"]
        return {
            "id": o["id"], "clientOrderId": o["cid"],
            "status": _CCXT_STATUS[o["status"]], "type": o["type"].lower(),
            "side": o["side"], "amount": o["amount"], "filled": filled,
            "remaining": o["amount"] - filled, "price": o["price"],
            "stopPrice": o["stop"], "triggerPrice": o["stop"],
            "average": (o["cost"] / filled) if filled > 0 else None,
            "cost": o["cost"],
            "fees": [dict(f) for f in o["fees"]] if include_fees else [],
            "info": {"orderId": int(o["id"]), "clientOrderId": o["cid"],
                     "orderListId": int(o["list_id"]) if o["list_id"] else -1,
                     "type": o["type"], "status": o["status"],
                     "executedQty": str(filled),
                     "cummulativeQuoteQty": str(o["cost"])}}

    def _find(self, id, params):
        params = params or {}
        cid = params.get("origClientOrderId")
        if cid:
            for o in self.orders.values():
                if o["cid"] == cid:
                    return o
            raise ccxt.OrderNotFound("-2013 Order does not exist.")
        o = self.orders.get(str(id))
        if o is None:
            raise ccxt.OrderNotFound("-2013 Order does not exist.")
        return o

    def fetch_order(self, id, symbol=None, params=None):
        self.calls.append("fetch_order")
        self._hook("fetch_order")
        if self.fail_fetch_order_n > 0:
            self.fail_fetch_order_n -= 1
            raise ccxt.NetworkError("fetch_order timeout")
        return self._ccxt(self._find(id, params),
                          include_fees=self.fetch_includes_fees)

    def cancel_order(self, id, symbol=None, params=None):
        self.calls.append("cancel_order")
        self._hook("cancel_order")
        o = self._find(id, params)
        if o["status"] not in ("NEW", "PARTIALLY_FILLED"):
            raise ccxt.OrderNotFound("-2011 Unknown order sent.")
        if o.get("list_id"):
            self._cancel_list(o["list_id"])
        else:
            self._cancel_single(o)
        return self._ccxt(o)

    def _cancel_single(self, o):
        if o["lock_asset"] and o["locked"] > 0:
            self._unlock(o["lock_asset"], o["locked"])
            o["locked"] = 0.0
        o["status"] = "CANCELED"

    def _cancel_list(self, lid):
        lst = self.lists[lid]
        if lst["status"] != "EXECUTING":
            raise ccxt.OrderNotFound("-2011 Unknown order list sent.")
        for sid in lst["orders"]:
            s = self.orders[sid]
            if s["status"] in ("NEW", "PARTIALLY_FILLED"):
                s["status"] = "CANCELED"
                if s["lock_asset"] and s["locked"] > 0:
                    self._unlock(s["lock_asset"], s["locked"])
                    s["locked"] = 0.0
        if lst["locked"] > 0:
            self._unlock(self.base, lst["locked"])
            lst["locked"] = 0.0
        lst["status"] = "ALL_DONE"

    def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        self._hook("fetch_open_orders")
        return [self._ccxt(o) for o in self.orders.values()
                if o["status"] in ("NEW", "PARTIALLY_FILLED")]

    def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None):
        self._hook("fetch_my_trades")
        limit = min(int(limit or 500), 1000)
        if since is not None:     # Binance : trades >= startTime, ordre croissant
            return [dict(t) for t in self.trades if t["timestamp"] >= since][:limit]
        return [dict(t) for t in self.trades][-limit:]

    # ---------- endpoints bruts OCO ----------

    def privatePostOrderListOco(self, params):
        self._hook("privatePostOrderListOco")
        self.calls.append("oco")
        fault = self._maybe_fault()
        self._raise_pre(fault, "OCO")
        below_type = params["belowType"]
        for key in ("quantity", "abovePrice", "belowStopPrice") + (
                ("belowPrice", "belowTimeInForce") if below_type == "STOP_LOSS_LIMIT" else ()):
            if params.get(key) in (None, ""):
                raise ccxt.BadRequest(f'binance {{"code":-1102,"msg":"Mandatory '
                                      f"parameter '{key}' was not sent\"}}")
        # Endpoint brut : ccxt n'arrondit rien, Binance rejette hors grille.
        if not _on_grid(params["quantity"], self.step):
            raise ccxt.BadRequest('binance {"code":-1013,"msg":"Filter failure: LOT_SIZE"}')
        for key in ("abovePrice", "belowStopPrice", "belowPrice"):
            if params.get(key) and not _on_grid(params[key], self.tick):
                raise ccxt.BadRequest('binance {"code":-1013,"msg":"Filter failure: PRICE_FILTER"}')
        self._check_cid(params.get("aboveClientOrderId"), params.get("belowClientOrderId"))
        self._check_filters(["LIMIT_MAKER", below_type],
                            [float(params["abovePrice"])]
                            + ([float(params["belowPrice"])] if params.get("belowPrice") else []))
        qty = float(params["quantity"])
        above = float(params["abovePrice"])
        below_stop = float(params["belowStopPrice"])
        if params.get("aboveType") != "LIMIT_MAKER":
            raise ccxt.InvalidOrder("aboveType non simulé")
        if below_type not in self.order_types:
            raise ccxt.InvalidOrder(f"belowType {below_type} invalide")
        if above <= self.ask:
            raise ccxt.InvalidOrder("-2010 Order would immediately match and take.")
        if below_stop >= self.last:
            raise ccxt.InvalidOrder("-2010 Stop price would trigger immediately.")
        self._lock(self.base, qty)
        lid = str(next(self._list_ids))
        tp = self._new_order("LIMIT_MAKER", "sell", qty, above, None,
                             params.get("aboveClientOrderId"), lid)
        below_price = float(params["belowPrice"]) if params.get("belowPrice") else None
        sl = self._new_order(below_type, "sell", qty, below_price, below_stop,
                             params.get("belowClientOrderId"), lid)
        self.lists[lid] = {"id": lid, "cid": params.get("listClientOrderId"),
                           "orders": [tp["id"], sl["id"]], "status": "EXECUTING",
                           "locked": qty}
        self._raise_post(fault, "OCO")
        return self._list_resp(lid, reports=True)

    def _list_resp(self, lid, reports=False):
        lst = self.lists[lid]
        resp = {"orderListId": int(lid), "listClientOrderId": lst["cid"],
                "listOrderStatus": lst["status"],
                "orders": [{"symbol": self.market_id, "orderId": int(i),
                            "clientOrderId": self.orders[i]["cid"]}
                           for i in lst["orders"]]}
        if reports:
            resp["orderReports"] = [
                {"symbol": self.market_id, "orderId": int(i),
                 "clientOrderId": self.orders[i]["cid"],
                 "type": self.orders[i]["type"]} for i in lst["orders"]]
        return resp

    def privateGetOrderList(self, params):
        self._hook("privateGetOrderList")
        if "symbol" in params:
            raise ccxt.BadRequest(
                "-1104 Not all sent parameters were read; read '1' parameter(s) "
                "but was sent '2'.")
        if params.get("orderListId") is not None:
            lid = str(params["orderListId"])
            if lid not in self.lists:
                raise ccxt.OrderNotFound("-2011 Order list does not exist.")
            return self._list_resp(lid)
        cid = params.get("origClientOrderId")
        for lid, lst in self.lists.items():
            if lst["cid"] == cid:
                return self._list_resp(lid)
        raise ccxt.OrderNotFound("-2011 Order list does not exist.")

    def privateDeleteOrderList(self, params):
        self._hook("privateDeleteOrderList")
        lid = str(params["orderListId"])
        if lid not in self.lists:
            raise ccxt.OrderNotFound("-2011 Unknown order list sent.")
        self._cancel_list(lid)
        return self._list_resp(lid)

    def privatePostOrderTest(self, params):
        self.calls.append("order_test")
        if params.get("type") not in self.order_types:
            raise ccxt.InvalidOrder(f"-1116 Invalid orderType {params.get('type')}")
        return {}

    # ---------- utilitaires de test ----------

    def open_protection_orders(self):
        return [o for o in self.orders.values()
                if o["side"] == "sell" and o["status"] in ("NEW", "PARTIALLY_FILLED")]


class FakeBinanceMulti:
    """Plusieurs paires /USDT partageant UN compte (soldes communs), avec
    routage des appels ccxt par symbole. Identifiants d'ordres et de listes
    uniques par paire (comme Binance)."""

    def __init__(self, prices: Dict[str, float], quote_balance: float = 10_000.0,
                 **kw):
        self.free: Dict[str, float] = {"USDT": quote_balance, "BNB": 10.0}
        self.locked: Dict[str, float] = {"USDT": 0.0, "BNB": 0.0}
        self.fakes: Dict[str, FakeBinance] = {}
        self.ohlcv: Dict[Any, List[List[float]]] = {}
        self.markets: Dict[str, Any] = {}
        self.options: Dict[str, Any] = {"timeDifference": 0}
        self.fail_balance = False
        # GET /sapi/v1/account/apiRestrictions (droits de la clé API)
        self.restrictions: Dict[str, Any] = {
            "ipRestrict": True, "enableReading": True,
            "enableWithdrawals": False, "enableSpotAndMarginTrading": True}
        for k, (sym, px) in enumerate(prices.items()):
            fb = FakeBinance(symbol=sym, price=px, quote_balance=0.0, **kw)
            base = sym.split("/")[0]
            self.free.setdefault(base, 0.0)
            self.locked.setdefault(base, 0.0)
            fb.free = self.free
            fb.locked = self.locked
            fb.options = self.options      # un seul client ccxt : options communes
            # Comme Binance : orderId numéroté PAR PAIRE (collisions possibles
            # entre paires) ; orderListId unique (GET orderList sans symbol).
            fb._ids = itertools.count(5_000_000_000)
            fb._list_ids = itertools.count(1 + k * 100_000)
            self.fakes[sym] = fb

    # ---------- routage ----------
    def _f(self, symbol):
        return self.fakes[symbol]

    def _by_market_id(self, mid):
        for fb in self.fakes.values():
            if fb.market_id == mid:
                return fb
        raise ccxt.BadSymbol(f"symbole inconnu {mid}")

    def load_markets(self):
        self.markets = {s: fb.market(s) for s, fb in self.fakes.items()}
        return self.markets

    def market(self, symbol):
        return self._f(symbol).market(symbol)

    def amount_to_precision(self, symbol, amount):
        return self._f(symbol).amount_to_precision(symbol, amount)

    def price_to_precision(self, symbol, price):
        return self._f(symbol).price_to_precision(symbol, price)

    def set_sandbox_mode(self, flag):
        pass

    def set_price(self, symbol, px):
        self._f(symbol).set_price(px)

    def fetch_ticker(self, symbol):
        return self._f(symbol).fetch_ticker(symbol)

    def fetch_order_book(self, symbol, limit=20):
        return self._f(symbol).fetch_order_book(symbol, limit)

    def fetch_ohlcv(self, symbol, timeframe="1d", since=None, limit=500):
        bars = self.ohlcv.get((symbol, timeframe), [])
        if since is not None:
            bars = [b for b in bars if b[0] >= since]
            return [list(b) for b in bars[:limit]]
        return [list(b) for b in bars[-limit:]]

    def fetch_balance(self):
        if self.fail_balance:
            raise ccxt.NetworkError("balance timeout")
        return next(iter(self.fakes.values())).fetch_balance()

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        return self._f(symbol).create_order(symbol, type, side, amount, price, params)

    def create_order_request(self, symbol, type, side, amount, price=None,
                             params=None):
        return self._f(symbol).create_order_request(symbol, type, side, amount,
                                                    price, params)

    @property
    def _first(self):
        return next(iter(self.fakes.values()))

    def fetch_time(self):
        return self._first.fetch_time()

    def load_time_difference(self):
        return self._first.load_time_difference()

    def set_server_offset(self, ms: float) -> None:
        for fb in self.fakes.values():
            fb.server_offset_ms = ms

    @property
    def time_calls(self) -> int:
        return self._first.time_calls

    def fetch_order(self, id, symbol=None, params=None):
        return self._f(symbol).fetch_order(id, symbol, params)

    def cancel_order(self, id, symbol=None, params=None):
        return self._f(symbol).cancel_order(id, symbol, params)

    def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        if symbol:
            return self._f(symbol).fetch_open_orders(symbol)
        return [o for fb in self.fakes.values() for o in fb.fetch_open_orders()]

    def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None):
        return self._f(symbol).fetch_my_trades(symbol, since, limit)

    def privatePostOrderListOco(self, params):
        return self._by_market_id(params["symbol"]).privatePostOrderListOco(params)

    def privateGetOrderList(self, params):
        if "symbol" in params:
            raise ccxt.BadRequest("-1104 Not all sent parameters were read")
        for fb in self.fakes.values():
            try:
                return fb.privateGetOrderList(params)
            except ccxt.OrderNotFound:
                continue
        raise ccxt.OrderNotFound("-2011 Order list does not exist.")

    def privateDeleteOrderList(self, params):
        return self._by_market_id(params["symbol"]).privateDeleteOrderList(params)

    def privatePostOrderTest(self, params):
        return self._by_market_id(params["symbol"]).privatePostOrderTest(params)

    def sapi_get_account_apirestrictions(self, params=None):
        return dict(self.restrictions)

    def open_orders(self):
        return [o for fb in self.fakes.values() for o in fb.orders.values()
                if o["status"] in ("NEW", "PARTIALLY_FILLED")]
