"""Lecture seule des données du bot pour le panneau : état, historique du
capital, positions, trades, veille et journal. La base SQLite est ouverte
en lecture seule : le bot peut tourner en même temps, rien n'est modifié."""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import v29
from trendguard import libre, savoir
from trendguard import market_watch as mw
from trendguard.journal import silent_logger

from .market import Market

STATE_KEY = "trendguard"


def _ro(path: str) -> Optional[sqlite3.Connection]:
    if not path or path == ":memory:" or not os.path.exists(path):
        return None
    uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def reasoning_view(st: Dict[str, Any]) -> Dict[str, Any]:
    """Raisonnement du bot (décision du jour, historique, achats différés)."""
    pending = [{"asset": a, "reason": e.get("reason"), "tries": e.get("tries"),
                "until": e.get("until")}
               for a, e in sorted((st.get("pending_entries") or {}).items())]
    return {"current": st.get("reasoning"),
            "history": list(reversed(st.get("reasoning_log") or []))[:30],
            "pending": pending}


def merge_buys(state: Dict[str, Any], holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tous les achats du bot, du plus ancien au plus récent : journal des
    achats (écrit par le bot à l'instant de chaque achat), complété par les
    trades clos et les positions ouvertes pour les achats plus anciens.
    Un même achat vu deux fois (à moins de 30 min d'écart) n'est gardé
    qu'une fois."""
    out: List[Dict[str, Any]] = []

    def add(asset: Any, date: Any, price: Any, qty: Any = None, cost: Any = None,
            note: str = "") -> None:
        t = _ts(date)
        if t is None or not asset or not price:
            return
        if any(b["asset"] == asset and abs(b["t"] - t) < 1800 for b in out):
            return
        out.append({"asset": asset, "date": date, "t": t, "price": float(price),
                    "qty": qty, "cost": cost, "note": note})
    for b in state.get("buys") or []:
        add(b.get("asset"), b.get("date"), b.get("price"), b.get("qty"), b.get("cost"),
            b.get("note") or "")
    for h in holdings:
        add(h.get("asset"), h.get("entry_date"), h.get("entry"), h.get("qty"), h.get("cost"))
    for tr in state.get("trades") or []:
        add(tr.get("asset"), tr.get("entry_date"), tr.get("entry"))
    out.sort(key=lambda b: b["t"])
    return out


def watch_summary(rep: Dict[str, Any]) -> Dict[str, Any]:
    """Ce que la page Veille montre du dernier rapport : indicateurs,
    actualités lues, état de chaque IA consultée."""
    ind = rep.get("indicators") or {}
    providers = [{"label": mw.PROVIDER_BY_NAME[n].label if n in mw.PROVIDER_BY_NAME else n,
                  "ok": bool(r.get("ok")), "error": r.get("error")}
                 for n, r in sorted((rep.get("providers") or {}).items())]
    return {"day": rep.get("day"), "generated": rep.get("generated"), "items": rep.get("items"),
            "fear_greed": ind.get("fear_greed"), "fear_greed_label": ind.get("fear_greed_label"),
            "usdc_usdt": ind.get("usdc_usdt"), "providers": providers,
            "errors": list(rep.get("errors") or [])}


def ai_summary() -> Dict[str, List[str]]:
    """IA de la veille : celles dont une clé est enregistrée (leur nom
    seulement, jamais la clé) et celles qu'on peut ajouter."""
    have = [p.label for p, _key, _model in mw.configured()]
    return {"configured": have, "possible": [p.label for p in mw.PROVIDERS]}


def _ts(value: Any) -> Optional[int]:
    """Horodatage ISO → secondes UNIX (UTC)."""
    dt = v29._parse_iso(str(value)) if value else None
    return int(dt.timestamp()) if dt else None


class BotData:
    def __init__(self, gcfg: Any, market: Market):
        self.g = gcfg
        self.market = market

    # ---------- État ----------

    def state(self) -> Dict[str, Any]:
        con = _ro(self.g.db_file)
        if con is None:
            return {}
        try:
            row = con.execute("SELECT value FROM kv WHERE key=?", (STATE_KEY,)).fetchone()
            return json.loads(row[0]) if row else {}
        except sqlite3.Error:
            return {}
        finally:
            con.close()

    def equity(self, days: int = 90, max_points: int = 1500) -> List[Dict[str, float]]:
        con = _ro(self.g.db_file)
        if con is None:
            return []
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            rows = con.execute("SELECT ts, equity FROM equity WHERE mode=? AND ts>=? "
                               "ORDER BY id", (self.g.run_mode, since)).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            con.close()
        pts = [{"t": t, "v": round(float(v), 2)} for ts, v in rows
               if v is not None and (t := _ts(ts)) is not None]
        step = max(1, len(pts) // max_points)
        return pts[::step] if step > 1 else pts

    # ---------- Positions et trades ----------

    def holdings(self, state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        state = self.state() if state is None else state
        out: List[Dict[str, Any]] = []
        if self.g.run_mode == "paper":
            for a, h in sorted((state.get("paper") or {}).get("holdings", {}).items()):
                out.append({"asset": a, "qty": h["qty"], "entry": h["entry"], "stop": h["stop"],
                            "disaster": h.get("disaster"), "risk": h["risk_quote"],
                            "cost": h.get("cost"), "entry_date": h.get("entry_date")})
            return out
        if not os.path.exists(self.g.db_file):
            return out
        store = v29.Store(self.g.db_file, silent_logger("trendguard.panel.store"))
        try:
            for base in self.g.universe:
                ctx = store.load_context(key=f"ctx:{base}/{self.g.quote}")
                if ctx and ctx.position.in_position:
                    p = ctx.position
                    out.append({"asset": base.lower(), "qty": p.amount_held, "entry": p.buy_price,
                                "stop": p.soft_stop or p.sl_price, "disaster": p.sl_price,
                                "risk": p.risk_quote_initial,
                                "cost": p.cost_basis * p.amount_held, "entry_date": p.opened_at})
        finally:
            store.close()
        return out

    def positions(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        rows = self.holdings(state)
        prices: Dict[str, Dict[str, float]] = {}
        stale = False
        if rows:
            try:
                prices, stale = self.market.tickers([r["asset"] for r in rows])
            except Exception:
                stale = True
        for r in rows:
            px = (prices.get(r["asset"]) or {}).get("price")
            r["price"] = px
            if px:
                r["pnl_pct"] = round((px / r["entry"] - 1) * 100, 2)
                r["r"] = round((px - r["entry"]) * r["qty"] / r["risk"], 2) if r["risk"] else None
                r["stop_dist_pct"] = round((px - r["stop"]) / px * 100, 2)
        return {"positions": rows, "stale": stale}

    def selection_request(self) -> Dict[str, Any]:
        from trendguard.selection import read_selection
        return read_selection(self.g)

    def save_selection(self, mode: str, manual: List[str]) -> Dict[str, Any]:
        from trendguard.selection import write_selection
        return write_selection(self.g, mode, manual)

    def buys(self, state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        state = self.state() if state is None else state
        return merge_buys(state, self.holdings(state))

    def trades(self, state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        state = self.state() if state is None else state
        return list(reversed(state.get("trades", [])))[:300]

    # ---------- Veille et journal ----------

    def watch(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self.state() if state is None else state
        text, rep = "", {}
        con = _ro(getattr(self.g, "watch_db", "") or mw.DEFAULT_DB)
        if con is not None:
            try:
                row = con.execute("SELECT report FROM reports ORDER BY day DESC LIMIT 1").fetchone()
                if row:
                    rep = json.loads(row[0])
                    text = mw.render(rep)
            except (sqlite3.Error, KeyError, ValueError):
                text, rep = "", {}
            finally:
                con.close()
        vetoes = [dict(v, asset=a) for a, v in sorted((state.get("vetoes") or {}).items())]
        return {"last": state.get("last_watch"), "vetoes": vetoes, "report_text": text,
                "report": watch_summary(rep), "ai": ai_summary(), "savoir": self.savoir()}

    def savoir(self) -> Optional[Dict[str, Any]]:
        """Noyau de savoir (lecture seule) : taille, dernière lecture,
        fiabilité des sources, avis du bot ; None s'il n'existe pas encore."""
        path = getattr(self.g, "savoir_db", "") or savoir.DEFAULT_DB
        if not getattr(self.g, "savoir", False) or not os.path.exists(path):
            return None
        try:
            memory = savoir.Memory(path, readonly=True)
        except sqlite3.Error:
            return None
        try:
            out = savoir.summary(memory, datetime.now(timezone.utc).date().isoformat())
            st = self.state()
            out["libre"] = libre.summary(memory.get("libre"), st.get("last_equity"), st.get("start_equity"))
            return out
        except (sqlite3.Error, ValueError, KeyError, TypeError):
            return None
        finally:
            memory.close()

    def reasoning(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return reasoning_view(self.state() if state is None else state)

    def log_tail(self, lines: int = 300) -> List[str]:
        path = self.g.log_file
        if not path or path == os.devnull or not os.path.exists(path):
            return []
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 256_000))
            data = fh.read().decode("utf-8", "replace")
        return data.splitlines()[-max(10, min(2000, lines)):]
