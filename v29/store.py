"""Base SQLite : état, trades, intentions d'ordres, courbe du capital.

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

from .utils import _utcnow_iso
from .models import BotContext


class Store:
    def __init__(self, path: str, logger: logging.Logger):
        self.path = path
        self.logger = logger
        self.conn: Optional[sqlite3.Connection] = None
        self._digests: Dict[str, str] = {}
        self._closed = False
        self.healthy = True
        self._open()
        self._init_schema()

    def _open(self) -> None:
        self.conn = sqlite3.connect(self.path, isolation_level=None,
                                    timeout=30, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA wal_autocheckpoint=1000")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Store is closed")
        if self.conn is None:
            self._open()
            self._init_schema()

    @contextmanager
    def transaction(self, immediate: bool = True):
        self._ensure_open()
        mode = "BEGIN IMMEDIATE" if immediate else "BEGIN"
        for attempt in range(3):
            try:
                self.conn.execute(mode)
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 2:
                    time.sleep(0.1 * (2 ** attempt))
                    continue
                raise
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            try:
                self.conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def _exec(self, sql: str, params: tuple = (), retries: int = 3) -> None:
        self._ensure_open()
        for attempt in range(retries):
            try:
                self.conn.execute(sql, params)
                return
            except sqlite3.IntegrityError as e:
                self.logger.error(f"[SQL-INTEG] {e} | sql={sql[:80]}")
                raise
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < retries - 1:
                    time.sleep(0.1 * (2 ** attempt))
                    continue
                raise

    def checkpoint(self, mode: str = "PASSIVE") -> None:
        if mode not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            mode = "PASSIVE"
        try:
            self._ensure_open()
            self.conn.execute(f"PRAGMA wal_checkpoint({mode})")
        except Exception as e:
            self.logger.warning(f"[SQL-CKPT] {e}")

    def _migrate_columns(self, table: str, columns: Dict[str, str]) -> None:
        if not table.isidentifier():
            raise RuntimeError(f"Nom de table invalide: {table!r}")
        try:
            existing = {row[1] for row in self.conn.execute(
                f"PRAGMA table_info({table})").fetchall()}
        except Exception as e:
            raise RuntimeError(f"PRAGMA table_info({table}) KO: {e}") from e
        to_add = [(col, ddl) for col, ddl in columns.items()
                  if col not in existing]
        if not to_add:
            return
        for col, _ in to_add:
            if not col.isidentifier():
                raise RuntimeError(f"Nom de colonne invalide: {col!r}")
        try:
            with self.transaction(immediate=True):
                for col, ddl in to_add:
                    self.logger.info(f"[SQL-MIGRATE] {table}: +{col}")
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        except sqlite3.Error as e:
            raise RuntimeError(
                f"Migration {table} impossible ({to_add}): {e}") from e

    def _init_schema(self) -> None:
        c = self.conn
        c.execute("CREATE TABLE IF NOT EXISTS kv "
                  "(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, event TEXT NOT NULL, severity TEXT NOT NULL,
            payload TEXT, mode TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, entry REAL, exit REAL, amount REAL,
            pnl REAL, pnl_pct REAL, r_mult REAL,
            cumulative_pnl REAL, cumulative_r REAL,
            barrier TEXT, module TEXT, tier TEXT, score INTEGER, entry_obi REAL,
            entry_order_id TEXT, exit_order_id TEXT,
            regime TEXT, eff_risk_pct REAL, mode TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, signal TEXT, module TEXT, regime TEXT,
            score INTEGER, reject TEXT, payload TEXT, mode TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS equity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, equity REAL, cash REAL, base_qty REAL,
            base_price REAL, halted INTEGER, mode TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS blockchain_txs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, chain TEXT NOT NULL, chain_id INTEGER NOT NULL,
            tx_hash TEXT NOT NULL, from_addr TEXT, to_addr TEXT,
            value_eth REAL, token_addr TEXT, token_amount REAL,
            gas_used INTEGER, gas_price_wei TEXT, status INTEGER,
            dry_run INTEGER NOT NULL, mode TEXT NOT NULL,
            rbf INTEGER DEFAULT 0, pending INTEGER DEFAULT 0)""")
        self._migrate_columns("blockchain_txs", {
            "rbf": "INTEGER DEFAULT 0",
            "pending": "INTEGER DEFAULT 0",
        })
        for idx in (
            "CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts)",
            "CREATE INDEX IF NOT EXISTS idx_trades_module ON trades(module)",
            "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
            "CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts)",
            "CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity(ts)",
            "CREATE INDEX IF NOT EXISTS idx_bctx_hash ON blockchain_txs(tx_hash)",
            "CREATE INDEX IF NOT EXISTS idx_bctx_ts ON blockchain_txs(ts)",
        ):
            c.execute(idx)

    def load_context(self, key: str = "context") -> Optional[BotContext]:
        """Retourne None si aucun contexte. Lève si le contexte existe mais
        est illisible (fail-closed : on ne repart pas d'un état vierge
        alors qu'une position peut être ouverte)."""
        self._ensure_open()
        row = self.conn.execute(
            "SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        try:
            return BotContext.from_dict(json.loads(row[0]))
        except Exception as e:
            self.logger.critical(f"[DB] Context corrompu: {e}")
            raise RuntimeError(f"Contexte DB illisible: {e}") from e

    def save_context(self, ctx: BotContext, mode: str,
                     force: bool = False, key: str = "context") -> bool:
        try:
            payload = json.dumps(ctx.to_dict(), sort_keys=True,
                                 separators=(",", ":"), default=str)
        except Exception as e:
            self.logger.critical(f"[DB] sérialisation contexte KO: {e}")
            self.healthy = False
            return False
        digest = hashlib.sha256(payload.encode()).hexdigest()
        if not force and digest == self._digests.get(key):
            return True
        try:
            with self.transaction(immediate=True):
                self.conn.execute(
                    "INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)",
                    (key, payload))
            self._digests[key] = digest
            if not self.healthy:
                self.logger.warning("[DB] save_context rétabli")
            self.healthy = True
            return True
        except Exception as e:
            self.logger.critical(f"[DB] save_context KO: {e}")
            self.healthy = False
            return False

    def get_kv(self, key: str) -> Optional[Dict[str, Any]]:
        self._ensure_open()
        row = self.conn.execute("SELECT value FROM kv WHERE key=?",
                                (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_kv(self, key: str, value: Dict[str, Any]) -> bool:
        try:
            payload = json.dumps(value, sort_keys=True, default=str)
            with self.transaction(immediate=True):
                self.conn.execute(
                    "INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)",
                    (key, payload))
            return True
        except Exception as e:
            self.logger.critical(f"[DB] set_kv({key}) KO: {e}")
            self.healthy = False
            return False

    def log_event(self, event: str, severity: str,
                  payload: Dict[str, Any], mode: str,
                  durable: bool = False) -> None:
        try:
            self._exec(
                "INSERT INTO events(ts,event,severity,payload,mode)"
                " VALUES(?,?,?,?,?)",
                (_utcnow_iso(), event, severity,
                 json.dumps(payload, default=str), mode))
            if durable:
                self.checkpoint(mode="FULL")
        except Exception as e:
            self.logger.warning(f"[DB] event log KO: {e}")

    def log_trade(self, payload: Dict[str, Any], mode: str) -> None:
        try:
            self._exec(
                """INSERT INTO trades(ts,entry,exit,amount,pnl,pnl_pct,r_mult,
                   cumulative_pnl,cumulative_r,barrier,module,tier,score,
                   entry_obi,entry_order_id,exit_order_id,regime,eff_risk_pct,
                   mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (_utcnow_iso(),
                 payload.get("entry"), payload.get("exit"),
                 payload.get("amount"), payload.get("pnl"),
                 payload.get("pnl_pct"), payload.get("r_mult"),
                 payload.get("cumulative_pnl"), payload.get("cumulative_r"),
                 payload.get("barrier"), payload.get("module") or "",
                 payload.get("tier") or "", int(payload.get("score") or 0),
                 payload.get("entry_obi"), payload.get("entry_order_id"),
                 payload.get("exit_order_id"), payload.get("regime") or "",
                 payload.get("eff_risk_pct"), mode))
        except Exception as e:
            self.logger.warning(f"[DB] trade log KO: {e}")

    def log_decision(self, payload: Dict[str, Any], mode: str) -> None:
        try:
            self._exec(
                """INSERT INTO decisions(ts,signal,module,regime,score,
                   reject,payload,mode) VALUES(?,?,?,?,?,?,?,?)""",
                (_utcnow_iso(),
                 payload.get("signal") or "", payload.get("module") or "",
                 payload.get("regime") or "", int(payload.get("score") or 0),
                 payload.get("reject") or "",
                 json.dumps(payload, default=str), mode))
        except Exception as e:
            self.logger.warning(f"[DB] decision log KO: {e}")

    def log_equity(self, equity: float, cash: float, base_qty: float,
                   base_price: float, halted: bool, mode: str) -> None:
        try:
            self._exec(
                "INSERT INTO equity(ts,equity,cash,base_qty,base_price,"
                "halted,mode) VALUES(?,?,?,?,?,?,?)",
                (_utcnow_iso(), equity, cash,
                 base_qty, base_price, int(halted), mode))
        except Exception as e:
            self.logger.warning(f"[DB] equity log KO: {e}")

    def log_blockchain_tx(self, payload: Dict[str, Any], mode: str) -> None:
        try:
            self._exec(
                """INSERT INTO blockchain_txs(ts,chain,chain_id,tx_hash,
                   from_addr,to_addr,value_eth,token_addr,token_amount,
                   gas_used,gas_price_wei,status,dry_run,mode,rbf,pending)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (_utcnow_iso(),
                 payload.get("chain"), int(payload.get("chain_id")),
                 payload.get("tx_hash"), payload.get("from_addr"),
                 payload.get("to_addr"), payload.get("value_eth"),
                 payload.get("token_addr"), payload.get("token_amount"),
                 payload.get("gas_used"), payload.get("gas_price_wei"),
                 payload.get("status"), int(payload.get("dry_run", 1)), mode,
                 int(payload.get("rbf", 0)), int(payload.get("pending", 0))))
        except Exception as e:
            self.logger.warning(f"[DB] blockchain tx log KO: {e}")

    def log_panic_sequence(self, reason: str, extra: Dict[str, Any],
                            mode: str) -> None:
        try:
            with self.transaction(immediate=True):
                self.conn.execute(
                    "INSERT INTO events(ts,event,severity,payload,mode)"
                    " VALUES(?,?,?,?,?)",
                    (_utcnow_iso(),
                     "panic_flatten", "CRITICAL",
                     json.dumps({"reason": reason, **extra}, default=str),
                     mode))
            self.checkpoint(mode="FULL")
        except Exception as e:
            self.logger.warning(f"[DB] panic sequence KO: {e}")

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self.conn is not None:
                try:
                    self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
                self.conn.close()
        except Exception:
            pass
        self.conn = None
        self._closed = True
