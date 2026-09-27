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

import market_watch as mw
import v29

from .market import Market

STATE_KEY = "trendguard"


def _ro(path: str) -> Optional[sqlite3.Connection]:
    if not path or path == ":memory:" or not os.path.exists(path):
        return None
    uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


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
        store = v29.Store(self.g.db_file, _quiet_logger())
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

    def trades(self, state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        state = self.state() if state is None else state
        return list(reversed(state.get("trades", [])))[:300]

    # ---------- Veille et journal ----------

    def watch(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self.state() if state is None else state
        text = ""
        con = _ro(getattr(self.g, "watch_db", "") or mw.DEFAULT_DB)
        if con is not None:
            try:
                row = con.execute("SELECT report FROM reports ORDER BY day DESC LIMIT 1").fetchone()
                if row:
                    text = mw.render(json.loads(row[0]))
            except (sqlite3.Error, KeyError, ValueError):
                text = ""
            finally:
                con.close()
        vetoes = [dict(v, asset=a) for a, v in sorted((state.get("vetoes") or {}).items())]
        return {"last": state.get("last_watch"), "vetoes": vetoes, "report_text": text}

    def log_tail(self, lines: int = 300) -> List[str]:
        path = self.g.log_file
        if not path or path == os.devnull or not os.path.exists(path):
            return []
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 256_000))
            data = fh.read().decode("utf-8", "replace")
        return data.splitlines()[-max(10, min(2000, lines)):]


def _quiet_logger():
    import logging
    lg = logging.getLogger("trendguard.panel.store")
    lg.handlers[:] = [logging.NullHandler()]
    lg.propagate = False
    return lg
