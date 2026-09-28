"""Adaptateur Binance Spot : ordres, soldes, règles de marché.

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

import ccxt
import numpy as np
import pandas as pd

from .config import Config
from .constants import CID_OCO_LIST, CID_OCO_SL, CID_OCO_TP
from .models import BotContext
from .utils import _client_id, _dec_round, make_binance, resync_clock


class AmbiguousOrder(Exception):
    """L'ordre a peut-être été exécuté mais son état est inconnaissable
    pour l'instant. `client_id` permet de le résoudre plus tard."""

    def __init__(self, message: str, client_id: Optional[str] = None):
        super().__init__(message)
        self.client_id = client_id


@dataclass
class MarketRules:
    min_amount: float = 0.0
    max_amount: Optional[float] = None
    min_cost: float = 10.0
    step_size: float = 0.0
    tick_size: float = 0.0
    order_types: Tuple[str, ...] = ()
    oco_allowed: bool = True


@dataclass
class OrderResult:
    order_id: str
    client_id: str = ""
    status: str = ""          # open | closed | canceled
    filled: float = 0.0       # base (brut)
    average: float = 0.0      # quote/base
    cost: float = 0.0         # quote
    fee_base: float = 0.0
    fee_quote: float = 0.0
    fee_other: float = 0.0    # frais payés dans un autre actif (ex: BNB)
    fee_known: bool = False
    order_type: str = ""
    side: str = ""
    list_id: Optional[str] = None
    amount: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.status == "open"


_ORDER_STATUS_MAP = {
    "new": "open", "open": "open", "partially_filled": "open",
    "pending_new": "open", "pending_cancel": "open",
    "filled": "closed", "closed": "closed",
    "canceled": "canceled", "cancelled": "canceled", "expired": "canceled",
    "rejected": "canceled", "expired_in_match": "canceled",
}


_NUMERIC_STR = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def _fnum(x: Any) -> float:
    """Valeur d'API (nombre, chaîne, None…) → float fini, 0.0 si absente ou
    invalide. Les valeurs absentes sont filtrées AVANT float() : aucune
    exception n'est levée (le débogueur ne s'arrête plus ici)."""
    if x is None:
        return 0.0
    if isinstance(x, str):
        x = x.strip()
        if not _NUMERIC_STR.fullmatch(x):
            return 0.0
    elif not isinstance(x, (int, float, Decimal, np.number)):
        return 0.0
    try:
        v = float(x)
    except OverflowError:          # entier trop grand pour un float
        return 0.0
    return v if math.isfinite(v) else 0.0


class ExchangeAdapter:
    def __init__(self, cfg: Config, logger: logging.Logger,
                 exchange: Any = None):
        self.cfg = cfg
        self.logger = logger
        if exchange is not None:
            self.exchange = exchange
        else:
            self.exchange = make_binance(
                os.environ.get("BINANCE_API_KEY", "").strip(),
                os.environ.get("BINANCE_API_SECRET", "").strip(),
                cfg.binance_testnet)
        self.sleep: Callable[[float], None] = time.sleep
        self.rules = MarketRules()
        self._market_id = ""
        self._paper_ctx: Optional[BotContext] = None
        self._balance_cache: Dict[str, Any] = {"ts": 0.0, "free": {}, "total": {}}
        self._equity_cache: Dict[str, Any] = {"ts": 0.0, "value": None}
        self._l2_cache = {"ts": 0.0, "micro": None, "obi": 0.0, "spread": 1.0}
        self._htf_cache = {"ts": 0.0, "bias": None, "error_count": 0}
        self._btc_cache = {"ts": 0.0, "bias": None, "error_count": 0}
        self._btc_vol_cache = {"ts": 0.0, "vol": None, "error_count": 0}

    # ---------- Marché ----------

    def load_markets(self) -> None:
        self.exchange.load_markets()
        if self.cfg.symbol not in self.exchange.markets:
            raise RuntimeError(f"Marché {self.cfg.symbol} indisponible.")
        m = self.exchange.market(self.cfg.symbol)
        if not m.get("spot", True):
            raise RuntimeError(f"{self.cfg.symbol} n'est pas Spot.")
        if m.get("active") is False:
            raise RuntimeError(f"{self.cfg.symbol} inactif.")
        self._market_id = m["id"]
        limits = m.get("limits", {}) or {}
        info = m.get("info", {}) or {}
        self.rules.min_amount = float(
            (limits.get("amount") or {}).get("min") or 0)
        self.rules.max_amount = (limits.get("amount") or {}).get("max")
        self.rules.min_cost = float(
            (limits.get("cost") or {}).get("min") or 0)
        for f in (info.get("filters", []) or []):
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                self.rules.step_size = float(f.get("stepSize") or 0)
                self.rules.min_amount = max(
                    self.rules.min_amount, float(f.get("minQty") or 0))
            elif ft == "PRICE_FILTER":
                self.rules.tick_size = float(f.get("tickSize") or 0)
            elif ft in {"MIN_NOTIONAL", "NOTIONAL"}:
                if f.get("minNotional") is not None:
                    self.rules.min_cost = max(
                        self.rules.min_cost, float(f["minNotional"]))
        if self.rules.min_cost <= 0:
            self.rules.min_cost = 10.0
        self.rules.order_types = tuple(
            str(t).upper() for t in (info.get("orderTypes") or ()))
        self.rules.oco_allowed = bool(info.get("ocoAllowed", True))

    @property
    def stop_order_type(self) -> str:
        """Type stop Spot valide pour ce marché (jamais STOP_LOSS_MARKET,
        qui n'existe pas en Spot)."""
        types = set(self.rules.order_types)
        if not types or "STOP_LOSS" in types:
            return "STOP_LOSS"
        if "STOP_LOSS_LIMIT" in types:
            return "STOP_LOSS_LIMIT"
        raise RuntimeError(f"Aucun type stop Spot supporté: {sorted(types)}")

    def round_amount(self, amount: float) -> float:
        if amount <= 0:
            return 0.0
        if self.rules.step_size > 0:
            return float(_dec_round(amount, self.rules.step_size, ROUND_DOWN))
        try:
            return float(self.exchange.amount_to_precision(
                self.cfg.symbol, amount))
        except Exception:
            return amount

    def round_price(self, price: float, direction: str = "nearest") -> float:
        if price <= 0:
            return 0.0
        if self.rules.tick_size > 0:
            rounding = {"down": ROUND_DOWN, "up": ROUND_UP}.get(direction)
            if rounding is not None:
                return float(_dec_round(price, self.rules.tick_size, rounding))
        try:
            return float(self.exchange.price_to_precision(
                self.cfg.symbol, price))
        except Exception:
            return price

    def _qty_str(self, amount: float) -> str:
        try:
            return str(self.exchange.amount_to_precision(self.cfg.symbol, amount))
        except Exception:
            return format(Decimal(str(amount)).normalize(), "f")

    def _px_str(self, price: float) -> str:
        try:
            return str(self.exchange.price_to_precision(self.cfg.symbol, price))
        except Exception:
            return format(Decimal(str(price)).normalize(), "f")

    def min_notional(self) -> float:
        return float(self.rules.min_cost or 10.0)

    # ---------- Soldes (le solde exchange fait foi) ----------

    def bind_paper_context(self, ctx: BotContext) -> None:
        self._paper_ctx = ctx

    def refresh_balances(self, ctx: Optional[BotContext] = None,
                         force: bool = False) -> None:
        """Lève en cas d'échec API (fail-closed) : un solde inconnu n'est
        jamais remplacé par 0."""
        now = time.time()
        if (not force and now - self._balance_cache["ts"]
                <= self.cfg.balance_cache_ttl_sec):
            return
        if self.cfg.run_mode == "paper":
            pc = ctx or self._paper_ctx
            if pc is None:
                raise RuntimeError("Solde paper demandé sans contexte.")
            free = {self.cfg.quote: float(pc.portfolio.paper_cash),
                    self.cfg.base: float(pc.portfolio.paper_base)}
            self._balance_cache = {"ts": now, "free": free,
                                   "total": dict(free)}
            return
        bal = self.exchange.fetch_balance()
        self._balance_cache = {"ts": now,
                               "free": dict(bal.get("free") or {}),
                               "total": dict(bal.get("total") or {})}

    def invalidate_balances(self) -> None:
        self._balance_cache["ts"] = 0.0
        self._equity_cache["ts"] = 0.0

    def get_free_balance(self, currency: str,
                         ctx: Optional[BotContext] = None,
                         force: bool = False) -> float:
        self.refresh_balances(ctx, force=force)
        return float(self._balance_cache["free"].get(currency, 0) or 0)

    def get_total_balance(self, currency: str,
                          ctx: Optional[BotContext] = None,
                          force: bool = False) -> float:
        self.refresh_balances(ctx, force=force)
        return float(self._balance_cache["total"].get(currency, 0) or 0)

    def _reserve(self) -> float:
        return (self.cfg.external_base_reserve
                if self.cfg.run_mode == "live" else 0.0)

    def bot_free_base(self, ctx: Optional[BotContext] = None,
                      force: bool = True) -> float:
        """Base librement vendable par le bot (hors réserve externe)."""
        free = self.get_free_balance(self.cfg.base, ctx, force=force)
        return max(0.0, free - self._reserve())

    def bot_total_base(self, ctx: Optional[BotContext] = None,
                       force: bool = False) -> float:
        total = self.get_total_balance(self.cfg.base, ctx, force=force)
        return max(0.0, total - self._reserve())

    def get_ticker(self) -> Dict[str, float]:
        t = self.exchange.fetch_ticker(self.cfg.symbol)
        last = _fnum(t.get("last"))
        bid = _fnum(t.get("bid")) or last
        ask = _fnum(t.get("ask")) or last
        if last <= 0 and bid > 0 and ask > 0:
            last = (bid + ask) / 2
        if last <= 0:
            raise ccxt.ExchangeError(f"Ticker {self.cfg.symbol} illisible")
        return {"bid": bid, "ask": ask, "last": last}

    def get_equity(self, ctx: BotContext,
                   force: bool = False) -> Optional[float]:
        now = time.time()
        if (not force and self._equity_cache["value"] is not None
                and now - self._equity_cache["ts"]
                < self.cfg.equity_cache_ttl_sec):
            return float(self._equity_cache["value"])
        try:
            last = self.get_ticker()["last"]
            if self.cfg.run_mode == "paper":
                eq = (float(ctx.portfolio.paper_cash)
                      + float(ctx.portfolio.paper_base) * last)
            else:
                bal = self.exchange.fetch_balance()
                total = bal.get("total", {}) or {}
                cash = _fnum(total.get(self.cfg.quote))
                base = max(0.0, _fnum(total.get(self.cfg.base)) - self._reserve())
                eq = cash + base * last
            self._equity_cache = {"ts": now, "value": float(eq)}
            return float(eq)
        except Exception as e:
            self.logger.warning(f"[CEX] equity KO: {e}")
            return None

    def get_l2_state(self, force: bool = False
                     ) -> Tuple[Optional[float], float, float]:
        if not self.cfg.use_l2_filter and not self.cfg.use_smart_buy:
            return None, 0.0, 1.0
        now = time.time()
        if (not force and now - self._l2_cache["ts"]
                < self.cfg.l2_cache_ttl_sec):
            return (self._l2_cache["micro"], self._l2_cache["obi"],
                    self._l2_cache["spread"])
        try:
            ob = self.exchange.fetch_order_book(self.cfg.symbol,
                                                 limit=self.cfg.l2_depth)
            bids = ob.get("bids") or []
            asks = ob.get("asks") or []
            if not bids or not asks:
                return None, 0.0, 1.0
            best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
            if best_bid <= 0:
                return None, 0.0, 1.0
            spread = (best_ask - best_bid) / best_bid
            bid_notional = sum(float(p) * float(q) for p, q, *_ in bids)
            ask_notional = sum(float(p) * float(q) for p, q, *_ in asks)
            total = bid_notional + ask_notional
            obi = 0.0 if total <= 0 else (bid_notional - ask_notional) / total
            bid_qty = sum(float(q) for _, q, *_ in bids)
            ask_qty = sum(float(q) for _, q, *_ in asks)
            qt = bid_qty + ask_qty
            micro = (best_bid if qt <= 0
                     else best_bid * (ask_qty / qt) + best_ask * (bid_qty / qt))
            self._l2_cache.update({"ts": now, "micro": micro, "obi": obi,
                                    "spread": spread})
            return micro, obi, spread
        except Exception as e:
            self.logger.warning(f"[CEX] L2 KO: {e}")
            return None, 0.0, 1.0

    def fetch_ohlcv(self, timeframe: str, limit: int = 400) -> pd.DataFrame:
        return self.fetch_ohlcv_htf(self.cfg.symbol, timeframe, limit)

    def fetch_ohlcv_htf(self, symbol: str, timeframe: str,
                        limit: int) -> pd.DataFrame:
        bars = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        cols = ["ts", "open", "high", "low", "close", "volume"]
        if not bars:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame([b[:6] for b in bars], columns=cols)

    def _raw(self, *names: str):
        for name in names:
            fn = getattr(self.exchange, name, None)
            if callable(fn):
                return fn
        return None

    # ---------- Ordres : parsing ----------

    def _parse_order(self, order: Dict[str, Any]) -> OrderResult:
        if not isinstance(order, dict):
            raise ccxt.ExchangeError(f"Ordre illisible: {order!r}")
        info = order.get("info") or {}
        if "orderId" in order and "id" not in order:
            info = order
        oid = order.get("id")
        if oid is None:
            oid = info.get("orderId")
        cid = order.get("clientOrderId") or info.get("clientOrderId") or ""
        raw_status = str(order.get("status") or info.get("status") or "").lower()
        status = _ORDER_STATUS_MAP.get(raw_status, raw_status)
        filled = _fnum(order.get("filled")) or _fnum(info.get("executedQty"))
        cost = _fnum(order.get("cost")) or _fnum(info.get("cummulativeQuoteQty"))
        avg = _fnum(order.get("average")) or _fnum(info.get("avgPrice"))
        if avg <= 0 and filled > 0 and cost > 0:
            avg = cost / filled
        fee_base = fee_quote = fee_other = 0.0
        fee_known = False
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        for f in fees:
            if not f or f.get("cost") is None:
                continue
            fee_known = True
            c = _fnum(f.get("cost"))
            cur = f.get("currency")
            if cur == self.cfg.base:
                fee_base += c
            elif cur == self.cfg.quote:
                fee_quote += c
            else:
                fee_other += c
        if not fee_known:
            for fl in info.get("fills") or []:
                fee_known = True
                c = _fnum(fl.get("commission"))
                cur = fl.get("commissionAsset")
                if cur == self.cfg.base:
                    fee_base += c
                elif cur == self.cfg.quote:
                    fee_quote += c
                else:
                    fee_other += c
        if filled <= 0:
            fee_known = True
        lid = info.get("orderListId", order.get("orderListId"))
        list_id = None if lid in (None, -1, "-1") else str(lid)
        return OrderResult(
            order_id=str(oid) if oid is not None else "",
            client_id=str(cid), status=status, filled=filled,
            average=avg, cost=cost, fee_base=fee_base, fee_quote=fee_quote,
            fee_other=fee_other, fee_known=fee_known,
            order_type=str(order.get("type") or info.get("type") or "").upper(),
            side=str(order.get("side") or info.get("side") or "").lower(),
            list_id=list_id,
            amount=_fnum(order.get("amount")) or _fnum(info.get("origQty")),
            price=_fnum(order.get("price")) or _fnum(info.get("price")),
            stop_price=(_fnum(order.get("stopPrice"))
                        or _fnum(order.get("triggerPrice"))
                        or _fnum(info.get("stopPrice"))))

    def fetch_order_result(self, order_id: Optional[str] = None,
                           client_id: Optional[str] = None) -> OrderResult:
        params: Dict[str, Any] = {}
        if client_id and not order_id:
            params["origClientOrderId"] = client_id
        o = self.exchange.fetch_order(str(order_id) if order_id else "",
                                      self.cfg.symbol, params)
        return self._parse_order(o)

    def fetch_order(self, order_id: str) -> Dict[str, Any]:
        return self.exchange.fetch_order(order_id, self.cfg.symbol)

    def _lookup_by_cid(self, cid: str, attempts: int = 3
                       ) -> Optional[OrderResult]:
        for i in range(attempts):
            try:
                return self.fetch_order_result(client_id=cid)
            except ccxt.OrderNotFound:
                pass
            except Exception as e:
                self.logger.warning(f"[ORD] lookup {cid} KO: {e}")
            self.sleep(0.5 * (i + 1))
        return None

    # ---------- Ordres : envoi ----------

    def new_client_id(self, prefix: str) -> str:
        return _client_id(prefix)

    def _submit(self, otype: str, side: str, amount: float,
                price: Optional[float], extra: Optional[Dict[str, Any]],
                cid: str) -> OrderResult:
        params = {"newClientOrderId": cid, "newOrderRespType": "FULL"}
        params.update(extra or {})
        try:
            try:
                o = self.exchange.create_order(self.cfg.symbol, otype, side,
                                               amount, price, dict(params))
            except ccxt.InvalidNonce as e:
                # -1021 : horodatage hors de la fenêtre de réception. Binance
                # rejette la requête AVANT tout traitement : on resynchronise
                # l'horloge et on renvoie une fois le même ordre (même
                # client-id, donc jamais de doublon).
                offset = resync_clock(self.exchange)
                self.logger.warning(f"[ORD] horodatage refusé ({e}) → "
                                    f"horloge resynchronisée ({offset} ms), "
                                    f"nouvel envoi de {cid}")
                o = self.exchange.create_order(self.cfg.symbol, otype, side,
                                               amount, price, dict(params))
        except ccxt.InvalidNonce as e:
            self.invalidate_balances()
            raise ccxt.InvalidOrder(f"Ordre rejeté (horodatage): {e}") from e
        except ccxt.OperationFailed as e:
            # Réseau, 5xx, -1007 et -1001 : Binance documente ces réponses
            # comme « statut d'exécution INCONNU » (l'ordre a pu passer).
            # ccxt classe -1001 en OperationFailed, hors NetworkError.
            self.invalidate_balances()
            self.logger.warning(
                f"[ORD] {otype} {side} statut inconnu ({type(e).__name__}) "
                f"→ recherche {cid}")
            found = self._lookup_by_cid(cid)
            if found is None:
                raise AmbiguousOrder(f"{otype} {side} ambigu: {cid}", cid) from e
            return found
        self.invalidate_balances()
        res = self._parse_order(o)
        if not res.client_id:
            res.client_id = cid
        return res

    def _complete_market(self, res: OrderResult, cid: str) -> OrderResult:
        for i in range(4):
            if res.status in ("closed", "canceled") and (
                    res.filled <= 0 or res.average > 0):
                return res
            self.sleep(0.3 * (i + 1))
            try:
                res = self.fetch_order_result(order_id=res.order_id or None,
                                              client_id=cid)
            except Exception as e:
                self.logger.warning(f"[ORD] relecture {cid} KO: {e}")
        if res.filled > 0 and res.average > 0:
            return res
        raise AmbiguousOrder(f"MARKET non vérifiable: {cid}", cid)

    def market_buy(self, amount: float, cid: str) -> OrderResult:
        amount = self.round_amount(amount)
        if amount <= 0:
            raise ValueError("Amount <= 0")
        ref = self.get_ticker()["ask"]
        if amount * ref < self.min_notional():
            raise ValueError("Sous min_notional")
        res = self._submit("market", "buy", amount, None, None, cid)
        return self._complete_market(res, cid)

    def market_sell(self, amount: float, cid: str,
                    ref_price: Optional[float] = None) -> OrderResult:
        amount = self.round_amount(amount)
        if amount <= 0:
            raise ValueError("Amount <= 0")
        ref = ref_price or self.get_ticker()["bid"]
        if amount * ref < self.min_notional():
            raise ValueError("Sous min_notional")
        res = self._submit("market", "sell", amount, None, None, cid)
        return self._complete_market(res, cid)

    def limit_buy(self, amount: float, price: float, cid: str) -> OrderResult:
        amount = self.round_amount(amount)
        price = self.round_price(price, "down")
        if amount <= 0 or price <= 0:
            raise ValueError("limit_buy: amount/price invalides")
        return self._submit("limit", "buy", amount, price,
                            {"timeInForce": "GTC"}, cid)

    def place_stop_loss(self, amount: float, stop_price: float,
                        cid: str) -> OrderResult:
        amount = self.round_amount(amount)
        stop = self.round_price(stop_price, "down")
        if amount <= 0 or stop <= 0:
            raise ValueError("stop: amount/stop invalides")
        otype = self.stop_order_type
        if otype == "STOP_LOSS":
            return self._submit("STOP_LOSS", "sell", amount, None,
                                {"stopPrice": stop}, cid)
        limit = self.round_price(stop * (1 - self.cfg.stop_limit_offset_pct),
                                 "down")
        return self._submit("STOP_LOSS_LIMIT", "sell", amount, limit,
                            {"stopPrice": stop, "timeInForce": "GTC"}, cid)

    def cancel_order(self, order_id: Optional[str] = None,
                     client_id: Optional[str] = None) -> bool:
        """True si l'ordre est annulé OU déjà terminal (l'appelant DOIT
        relire le statut pour connaître d'éventuels fills)."""
        if not order_id and not client_id:
            return True
        params: Dict[str, Any] = {}
        if client_id and not order_id:
            params["origClientOrderId"] = client_id
        try:
            self.exchange.cancel_order(str(order_id) if order_id else "",
                                       self.cfg.symbol, params)
            self.invalidate_balances()
            return True
        except ccxt.OrderNotFound:
            self.invalidate_balances()
            return True
        except Exception as e:
            self.logger.warning(f"[ORD] cancel {order_id or client_id} KO: {e}")
            return False

    # ---------- OCO natif ----------

    def place_oco(self, amount: float, tp: float,
                  sl_stop: float) -> Dict[str, Any]:
        """Pose un OCO natif SELL (LIMIT_MAKER au-dessus, stop en dessous).
        Lève en cas d'échec ; AmbiguousOrder si l'état est inconnu."""
        amount = self.round_amount(amount)
        tp = self.round_price(tp, "up")
        sl = self.round_price(sl_stop, "down")
        if amount <= 0 or tp <= 0 or sl <= 0 or tp <= sl:
            raise ValueError(f"OCO invalide: qty={amount} tp={tp} sl={sl}")
        if not self.rules.oco_allowed:
            raise ccxt.InvalidOrder("OCO non autorisé sur ce marché")
        list_cid = _client_id(CID_OCO_LIST)
        tp_cid = _client_id(CID_OCO_TP)
        sl_cid = _client_id(CID_OCO_SL)
        below_type = self.stop_order_type
        stop_limit = self.round_price(
            sl * (1 - self.cfg.stop_limit_offset_pct), "down")
        fn = self._raw("privatePostOrderListOco", "private_post_orderlist_oco")
        if fn is not None:
            params = {
                "symbol": self._market_id, "side": "SELL",
                "quantity": self._qty_str(amount),
                "aboveType": "LIMIT_MAKER", "abovePrice": self._px_str(tp),
                "belowType": below_type, "belowStopPrice": self._px_str(sl),
                "listClientOrderId": list_cid,
                "aboveClientOrderId": tp_cid,
                "belowClientOrderId": sl_cid,
                "newOrderRespType": "FULL"}
            if below_type == "STOP_LOSS_LIMIT":
                params["belowPrice"] = self._px_str(stop_limit)
                params["belowTimeInForce"] = "GTC"
        else:
            fn = self._raw("privatePostOrderOco", "private_post_order_oco")
            if fn is None:
                raise ccxt.NotSupported("Endpoint OCO absent")
            params = {
                "symbol": self._market_id, "side": "SELL",
                "quantity": self._qty_str(amount),
                "price": self._px_str(tp), "stopPrice": self._px_str(sl),
                "listClientOrderId": list_cid,
                "limitClientOrderId": tp_cid,
                "stopClientOrderId": sl_cid,
                "newOrderRespType": "FULL"}
            if below_type == "STOP_LOSS_LIMIT":
                params["stopLimitPrice"] = self._px_str(stop_limit)
                params["stopLimitTimeInForce"] = "GTC"
        try:
            resp = fn(params)
        except ccxt.OperationFailed as e:        # statut inconnu (voir _submit)
            self.invalidate_balances()
            resp = self._lookup_list_by_cid(list_cid)
            if resp is None:
                raise AmbiguousOrder(f"OCO ambigu: {list_cid}", list_cid) from e
        self.invalidate_balances()
        out = self.parse_order_list(resp, tp_cid, sl_cid)
        out["list_client_order_id"] = out.get("list_client_order_id") or list_cid
        return out

    def parse_order_list(self, resp: Dict[str, Any],
                         tp_cid: Optional[str] = None,
                         sl_cid: Optional[str] = None) -> Dict[str, Any]:
        lid = resp.get("orderListId") if isinstance(resp, dict) else None
        if lid is None:
            raise ccxt.ExchangeError("Réponse OCO sans orderListId")
        tp_id = sl_id = None
        unclassified: List[str] = []
        entries = list(resp.get("orderReports") or []) \
            + list(resp.get("orders") or [])
        for o in entries:
            oid = o.get("orderId")
            if oid is None:
                continue
            oid = str(oid)
            c = str(o.get("clientOrderId") or "")
            t = str(o.get("type") or "").upper()
            if (tp_cid and c == tp_cid) or c.startswith(CID_OCO_TP) \
                    or t in ("LIMIT_MAKER", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"):
                tp_id = tp_id or oid
            elif (sl_cid and c == sl_cid) or c.startswith(CID_OCO_SL) \
                    or t.startswith("STOP_LOSS"):
                sl_id = sl_id or oid
            elif oid not in unclassified:
                unclassified.append(oid)
        unclassified = [u for u in unclassified if u not in (tp_id, sl_id)]
        return {"order_list_id": str(lid),
                "list_client_order_id": resp.get("listClientOrderId"),
                "list_status": str(resp.get("listOrderStatus") or ""),
                "tp_order_id": tp_id, "sl_order_id": sl_id,
                "tp_client_order_id": tp_cid, "sl_client_order_id": sl_cid,
                "unclassified_order_ids": unclassified}

    def query_order_list(self, list_id: Optional[str] = None,
                         list_cid: Optional[str] = None) -> Dict[str, Any]:
        """GET /api/v3/orderList — n'accepte PAS de paramètre `symbol`."""
        fn = self._raw("privateGetOrderList", "private_get_orderlist")
        if fn is None:
            raise ccxt.NotSupported("Endpoint orderList absent")
        if list_id is not None:
            return fn({"orderListId": str(list_id)})
        if list_cid:
            return fn({"origClientOrderId": list_cid})
        raise ValueError("query_order_list: identifiant requis")

    def _lookup_list_by_cid(self, list_cid: str,
                            attempts: int = 3) -> Optional[Dict[str, Any]]:
        for i in range(attempts):
            try:
                resp = self.query_order_list(list_cid=list_cid)
                if resp and resp.get("orderListId") is not None:
                    return resp
            except ccxt.OrderNotFound:
                pass
            except Exception as e:
                self.logger.warning(f"[OCO] lookup {list_cid} KO: {e}")
            self.sleep(0.5 * (i + 1))
        return None

    def cancel_order_list(self, list_id: str) -> bool:
        """True si annulée ou déjà terminée (relire les jambes ensuite)."""
        if not list_id:
            return True
        fn = self._raw("privateDeleteOrderList", "private_delete_orderlist")
        if fn is None:
            return False
        try:
            fn({"symbol": self._market_id, "orderListId": str(list_id)})
            self.invalidate_balances()
            return True
        except ccxt.OrderNotFound:
            self.invalidate_balances()
            return True
        except Exception as e:
            self.logger.warning(f"[OCO] cancel list {list_id} KO: {e}")
            return False

    # ---------- Lecture compte ----------

    def fetch_open_orders(self) -> List[Dict[str, Any]]:
        """Lève en cas d'erreur (fail-closed)."""
        return list(self.exchange.fetch_open_orders(self.cfg.symbol) or [])

    def fetch_my_trades(self, since_ms: int, limit: int = 500
                        ) -> List[Dict[str, Any]]:
        return list(self.exchange.fetch_my_trades(
            self.cfg.symbol, since=since_ms, limit=limit) or [])

    # ---------- Self-test (sans ordre réel) ----------

    def self_test_conditional_orders(self) -> bool:
        if (self.cfg.run_mode != "live"
                or not self.cfg.self_test_conditional_orders):
            return True
        problems: List[str] = []
        types = set(self.rules.order_types)
        if types and "LIMIT_MAKER" not in types:
            problems.append("LIMIT_MAKER non supporté")
        if types and not ({"STOP_LOSS", "STOP_LOSS_LIMIT"} & types):
            problems.append("aucun type stop supporté")
        if self.cfg.use_oco and not self.rules.oco_allowed:
            problems.append("OCO non autorisé sur ce marché")
        if (self._raw("privatePostOrderListOco", "private_post_orderlist_oco")
                is None and self._raw("privatePostOrderOco",
                                      "private_post_order_oco") is None):
            problems.append("endpoint OCO absent (ccxt trop ancien ?)")
        fn = self._raw("privatePostOrderTest", "private_post_order_test")
        if fn is None:
            problems.append("endpoint order/test absent")
        elif not problems:
            try:
                last = self.get_ticker()["last"]
                stop = self.round_price(last * 0.9, "down")
                qty = self.round_amount(max(self.rules.min_amount,
                                            self.min_notional() * 1.5 / stop))
                otype = self.stop_order_type
                params = {"symbol": self._market_id, "side": "SELL",
                          "type": otype, "quantity": self._qty_str(qty),
                          "stopPrice": self._px_str(stop)}
                if otype == "STOP_LOSS_LIMIT":
                    params["price"] = self._px_str(self.round_price(
                        stop * (1 - self.cfg.stop_limit_offset_pct), "down"))
                    params["timeInForce"] = "GTC"
                fn(params)
            except Exception as e:
                problems.append(f"order/test refusé: {e}")
        if problems:
            self.logger.critical(f"[SELFTEST] KO: {'; '.join(problems)}")
            return False
        self.logger.info(f"[SELFTEST] OK (stop={self.stop_order_type}, "
                         f"oco={self.rules.oco_allowed})")
        return True
