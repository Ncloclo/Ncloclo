"""
Socle de données du bot (prompt maître, étape 3 ; docs/DONNEES.md) : le
journal financier, en tables reliées, dans la base du bot.

    décision du jour → signal → contrôle du risque → ordre → exécution → trade

Chaque trade remonte ainsi jusqu'à sa décision, au contrôle du risque et à
l'autorisation qui l'ont permis, au signal, à la version de la stratégie et
à la qualité des données du jour (lignée financière, `lineage`).

Règles du socle :
- tables du domaine « fin_ », écrites par ce module seul (dépôt unique :
  aucun autre module n'y écrit de SQL ; un test le vérifie) ;
- clés, clés étrangères, contraintes CHECK et UNIQUE dans la base elle-même ;
  une même intention d'achat ne peut pas produire deux ordres (clé
  d'unicité) ; contrôles du risque et exécutions en ajout seulement
  (déclencheurs qui refusent toute modification ou suppression) ;
- dates en UTC (ISO 8601) ; identifiants uniques (UUID pour les ordres, les
  exécutions et les trades) ;
- schéma versionné par des migrations numérotées, chacune avec son retour
  arrière et son empreinte : une migration déjà appliquée puis modifiée est
  refusée (non-régression).

Pas de table de positions : leur source de vérité reste le portefeuille du
bot (paper) ou Binance (réel) ; une seconde vérité serait concurrente. Une
position ouverte est un ordre d'achat exécuté sans trade de sortie.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import hashlib
import json
import pathlib
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import v29

DATA_SOURCE = "Binance, bougies journalières clôturées (klines publiques)"


class MigrationError(RuntimeError):
    """Migration appliquée puis modifiée, ou retour arrière impossible."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: Tuple[str, ...]
    down: Tuple[str, ...]

    @property
    def checksum(self) -> str:
        return hashlib.sha256("\n".join(self.up).encode("utf-8")).hexdigest()[:16]


def _append_only(table: str) -> Tuple[str, ...]:
    return tuple(f"CREATE TRIGGER {table}_{op}_refuse BEFORE {op.upper()} ON {table} "
                 f"BEGIN SELECT RAISE(ABORT, '{table} : ajout seulement'); END"
                 for op in ("update", "delete"))


MIGRATIONS: Tuple[Migration, ...] = (
    Migration(1, "journal financier", up=(
        """CREATE TABLE fin_strategy_versions (
            id TEXT PRIMARY KEY,
            params TEXT NOT NULL CHECK (json_valid(params)),
            code_version TEXT NOT NULL,
            first_seen TEXT NOT NULL)""",
        """CREATE TABLE fin_decisions (
            id TEXT PRIMARY KEY,
            day TEXT NOT NULL UNIQUE CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
            mode TEXT NOT NULL CHECK (mode IN ('paper', 'live')),
            strategy_version_id TEXT NOT NULL REFERENCES fin_strategy_versions(id),
            bull INTEGER NOT NULL CHECK (bull IN (0, 1)),
            regime TEXT,
            equity REAL NOT NULL CHECK (equity >= 0),
            halted INTEGER NOT NULL CHECK (halted IN (0, 1)),
            safe_mode INTEGER NOT NULL CHECK (safe_mode IN (0, 1)),
            garde_blocked TEXT NOT NULL CHECK (json_valid(garde_blocked)),
            data_quality REAL CHECK (data_quality IS NULL OR data_quality BETWEEN 0 AND 100),
            data_source TEXT NOT NULL,
            created_at TEXT NOT NULL)""",
        """CREATE TABLE fin_signals (
            id INTEGER PRIMARY KEY,
            decision_id TEXT NOT NULL REFERENCES fin_decisions(id),
            asset TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('LONG', 'EXIT')),
            close REAL CHECK (close IS NULL OR close > 0),
            breakout_level REAL,
            momentum REAL,
            volatility REAL,
            outcome TEXT NOT NULL,
            detail TEXT,
            UNIQUE (decision_id, asset, direction))""",
        """CREATE TABLE fin_risk_checks (
            id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL REFERENCES fin_decisions(id),
            asset TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('APPROVED', 'APPROVED_WITH_LIMIT', 'REVIEW', 'REJECTED',
                                                   'EMERGENCY_BLOCK')),
            qty REAL NOT NULL CHECK (qty >= 0),
            expected_loss REAL NOT NULL CHECK (expected_loss >= 0),
            open_risk_pct REAL NOT NULL,
            exposure_pct REAL NOT NULL,
            checks TEXT NOT NULL CHECK (json_valid(checks)),
            reasons TEXT NOT NULL CHECK (json_valid(reasons)),
            authorization_id TEXT,
            policy_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK (status = 'APPROVED' OR authorization_id IS NULL))""",
        *_append_only("fin_risk_checks"),
        """CREATE TABLE fin_orders (
            id TEXT PRIMARY KEY,
            decision_id TEXT REFERENCES fin_decisions(id),
            risk_check_id TEXT REFERENCES fin_risk_checks(id),
            authorization_id TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            asset TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
            qty REAL NOT NULL CHECK (qty > 0),
            price REAL NOT NULL CHECK (price > 0),
            stop REAL,
            mode TEXT NOT NULL CHECK (mode IN ('paper', 'live')),
            status TEXT NOT NULL CHECK (status IN ('FILLED', 'SENT', 'REJECTED')),
            reason TEXT,
            created_at TEXT NOT NULL,
            CHECK (side = 'SELL' OR (decision_id IS NOT NULL AND risk_check_id IS NOT NULL
                                     AND authorization_id IS NOT NULL)))""",
        """CREATE TABLE fin_executions (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES fin_orders(id),
            qty REAL NOT NULL CHECK (qty > 0),
            price REAL NOT NULL CHECK (price > 0),
            fees REAL NOT NULL CHECK (fees >= 0),
            executed_at TEXT NOT NULL)""",
        *_append_only("fin_executions"),
        """CREATE TABLE fin_trades (
            id TEXT PRIMARY KEY,
            entry_order_id TEXT REFERENCES fin_orders(id),
            exit_order_id TEXT NOT NULL UNIQUE REFERENCES fin_orders(id),
            asset TEXT NOT NULL,
            opened_at TEXT,
            closed_at TEXT NOT NULL,
            entry REAL,
            exit REAL,
            pnl REAL NOT NULL,
            r REAL,
            mfe_r REAL,
            mae_r REAL,
            slippage_pct REAL,
            regime TEXT,
            lesson TEXT,
            reason TEXT NOT NULL)""",
        "CREATE INDEX fin_signals_decision ON fin_signals(decision_id)",
        "CREATE INDEX fin_risk_checks_decision ON fin_risk_checks(decision_id)",
        "CREATE INDEX fin_risk_checks_asset ON fin_risk_checks(asset, created_at)",
        "CREATE INDEX fin_orders_decision ON fin_orders(decision_id)",
        "CREATE INDEX fin_orders_asset ON fin_orders(asset, side, status)",
        "CREATE INDEX fin_executions_order ON fin_executions(order_id)",
        "CREATE INDEX fin_trades_entry ON fin_trades(entry_order_id)",
        "CREATE INDEX fin_trades_closed ON fin_trades(closed_at)",
    ), down=(
        "DROP TABLE IF EXISTS fin_trades", "DROP TABLE IF EXISTS fin_executions",
        "DROP TABLE IF EXISTS fin_orders", "DROP TABLE IF EXISTS fin_risk_checks",
        "DROP TABLE IF EXISTS fin_signals", "DROP TABLE IF EXISTS fin_decisions",
        "DROP TABLE IF EXISTS fin_strategy_versions",
    )),
)


@dataclass(frozen=True)
class Table:
    """Fiche d'une table (règle de l'étape 3 : pourquoi elle existe, qui la
    possède, la lit, la modifie, quel contrat la protège, combien de temps)."""
    name: str
    purpose: str
    readers: str
    writers: str
    contract: str
    retention: str
    index_reason: str = ""


TABLES: Tuple[Table, ...] = (
    Table("fin_schema_migrations", "version du schéma : migrations appliquées et leur empreinte",
          "ce module au démarrage", "ce module (migrate, rollback)", "migrations numérotées", "toujours"),
    Table("fin_strategy_versions", "registre des versions de la stratégie (réglages en vigueur, version du code)",
          "lignée, rapport", "bot, à chaque décision (nouvelle version seulement)", "EvolutionChange.v1", "toujours"),
    Table("fin_decisions", "décision du jour : marché, garde, mode sûr, arrêt d'urgence, qualité et source des données",
          "lignée, rapport, panneau", "bot, une fois par jour", "NoTradeGate.v1, KillSwitch.v1, MarketData.v1",
          "toujours (une ligne par jour)"),
    Table("fin_signals", "signaux du jour (achats signalés, ventes) et ce qu'il en est advenu",
          "lignée", "bot, à la décision", "Signal.v1", "toujours", "par décision : lignée d'un trade"),
    Table("fin_risk_checks", "chaque contrôle de la porte d'exécution, approuvé ou refusé (jamais modifié)",
          "lignée, rapport", "bot (porte d'exécution), en ajout seulement", "RiskCheck.v1, ExecutionAuthorization.v1",
          "toujours", "par décision ; par crypto et date pour l'historique"),
    Table("fin_orders", "ordres d'achat (avec contrôle et autorisation) et de vente, clé d'unicité",
          "lignée, rapport", "bot (exécution)", "OrderIntent.v1, Order.v1", "toujours",
          "par décision ; par crypto, sens et état pour retrouver l'achat d'une position"),
    Table("fin_executions", "exécutions de chaque ordre : quantité, prix, frais (jamais modifiées)",
          "lignée", "bot (exécution), en ajout seulement", "Order.v1", "toujours", "par ordre"),
    Table("fin_trades", "trades clos reliés à leur ordre d'achat et de vente, analyse après trade",
          "lignée, attribution, rapport", "bot, à la vente", "TradeRecord.v1", "toujours",
          "par ordre d'achat (lignée) ; par date de clôture (bilans)"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)


class Journal:
    """Le journal financier (dépôt du domaine « fin_ ») : migrations,
    écritures en transaction, lignée et contrôles d'intégrité."""

    def __init__(self, path: str, code_version: str = "inconnue", readonly: bool = False):
        self.path = path or ":memory:"
        self.code_version = code_version
        self.readonly = readonly
        if readonly:                    # rapport, panneau : lecture seule, aucune migration
            uri = pathlib.Path(self.path).resolve().as_uri() + "?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
        else:
            self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        if not readonly:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.migrate()

    def close(self) -> None:
        self.conn.close()

    # ---- migrations ----

    def _tx(self) -> "_Tx":
        return _Tx(self.conn)

    def applied(self) -> Dict[int, str]:
        """Migrations appliquées : version → empreinte."""
        exists = self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='fin_schema_migrations'").fetchone()
        if not exists:
            if self.readonly:
                return {}
            self.conn.execute("CREATE TABLE fin_schema_migrations (version INTEGER PRIMARY KEY, "
                              "name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
        return {r["version"]: r["checksum"] for r in self.conn.execute("SELECT * FROM fin_schema_migrations")}

    def migrate(self, migrations: Sequence[Migration] = MIGRATIONS) -> List[int]:
        """Applique les migrations manquantes, dans l'ordre, chacune en une
        transaction ; refuse une migration appliquée puis modifiée."""
        done = self.applied()
        for m in migrations:
            if m.version in done and done[m.version] != m.checksum:
                raise MigrationError(f"migration {m.version} ({m.name}) modifiée après son application")
        new = []
        for m in sorted(migrations, key=lambda m: m.version):
            if m.version in done:
                continue
            with self._tx():
                for sql in m.up:
                    self.conn.execute(sql)
                self.conn.execute("INSERT INTO fin_schema_migrations VALUES (?,?,?,?)",
                                  (m.version, m.name, m.checksum, _now()))
            new.append(m.version)
        return new

    def rollback(self, to_version: int, migrations: Sequence[Migration] = MIGRATIONS) -> List[int]:
        """Retour arrière jusqu'à la version `to_version` (0 : schéma vide)."""
        done = self.applied()
        undone = []
        for m in sorted(migrations, key=lambda m: -m.version):
            if m.version <= to_version or m.version not in done:
                continue
            with self._tx():
                for sql in m.down:
                    self.conn.execute(sql)
                self.conn.execute("DELETE FROM fin_schema_migrations WHERE version=?", (m.version,))
            undone.append(m.version)
        return undone

    # ---- écritures (une transaction chacune) ----

    def strategy_version(self, params: Dict[str, Any]) -> str:
        """Version de la stratégie (empreinte des réglages et du code),
        enregistrée la première fois qu'elle sert."""
        body = _json(params)
        sid = "S-" + hashlib.sha256(f"{body}|{self.code_version}".encode("utf-8")).hexdigest()[:12]
        with self._tx():
            self.conn.execute("INSERT OR IGNORE INTO fin_strategy_versions VALUES (?,?,?,?)",
                              (sid, body, self.code_version, _now()))
        return sid

    def record_decision(self, day: str, mode: str, params: Dict[str, Any], bull: bool, regime: Optional[str],
                        equity: float, halted: bool, safe_mode: bool, garde_blocked: Sequence[str],
                        data_quality: Optional[float]) -> str:
        """La décision du jour (une seule par jour : la première gardée)."""
        sid = self.strategy_version(params)
        did = f"D-{day}"
        with self._tx():
            self.conn.execute(
                "INSERT OR IGNORE INTO fin_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (did, day, mode, sid, int(bool(bull)), regime, max(0.0, float(equity)), int(bool(halted)),
                 int(bool(safe_mode)), _json(list(garde_blocked)), data_quality, DATA_SOURCE, _now()))
        return did

    def record_signals(self, decision_id: str, rows: Sequence[Dict[str, Any]]) -> int:
        """Signaux du jour et leur issue (achetée, différée, refusée…)."""
        with self._tx():
            for r in rows:
                self.conn.execute(
                    "INSERT OR REPLACE INTO fin_signals (decision_id, asset, direction, close, breakout_level, "
                    "momentum, volatility, outcome, detail) VALUES (?,?,?,?,?,?,?,?,?)",
                    (decision_id, r["asset"], r["direction"], r.get("close"), r.get("breakout_level"),
                     r.get("momentum"), r.get("volatility"), r["outcome"], r.get("detail")))
        return len(rows)

    def record_risk_check(self, decision_id: str, asset: str, decision: Any, authorization: Any) -> None:
        """Un contrôle de la porte d'exécution (jamais modifié ensuite)."""
        with self._tx():
            self.conn.execute(
                "INSERT INTO fin_risk_checks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (decision.risk_check_id, decision_id, asset, decision.status, decision.approved_size,
                 decision.expected_loss, decision.open_risk_pct, decision.exposure_pct,
                 _json([list(c) for c in decision.limit_checks]), _json(list(decision.blocking_reasons)),
                 (authorization.authorization_id or None) if decision.status == "APPROVED" else None,
                 authorization.policy_version, _now()))

    def record_order(self, key: str, asset: str, side: str, qty: float, price: float, mode: str, status: str,
                     decision_id: Optional[str] = None, risk_check_id: Optional[str] = None,
                     authorization_id: Optional[str] = None, stop: Optional[float] = None,
                     reason: str = "", fees: float = 0.0, at: Optional[str] = None) -> str:
        """Un ordre et, s'il est exécuté, son exécution, dans la même
        transaction. Idempotent : une clé déjà vue renvoie l'ordre existant
        sans rien écrire de plus."""
        row = self.conn.execute("SELECT id FROM fin_orders WHERE idempotency_key=?", (key,)).fetchone()
        if row:
            return row["id"]
        oid = f"O-{uuid.uuid4().hex[:16]}"
        at = at or _now()
        with self._tx():
            self.conn.execute("INSERT INTO fin_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (oid, decision_id, risk_check_id, authorization_id, key, asset, side, float(qty),
                               float(price), stop, mode, status, reason or None, at))
            if status == "FILLED":
                self.conn.execute("INSERT INTO fin_executions VALUES (?,?,?,?,?,?)",
                                  (f"X-{uuid.uuid4().hex[:16]}", oid, float(qty), float(price),
                                   max(0.0, float(fees)), at))
        return oid

    def record_trade(self, trade: Dict[str, Any], qty: float, mode: str, fee_rate: float) -> Optional[str]:
        """La vente (ordre et exécution) et le trade clos, relié à l'ordre
        d'achat de la position quand il est connu."""
        exit_px = float(trade.get("exit") or 0.0)
        if qty <= 0 or exit_px <= 0:
            return None
        key = f"{trade['asset']}:{trade.get('entry_date') or trade['date']}:SELL"
        entry_key = (trade.get("trace") or {}).get("key")
        entry = self.conn.execute("SELECT id FROM fin_orders WHERE idempotency_key=?", (entry_key,)).fetchone() \
            if entry_key else None
        exit_id = self.record_order(key, trade["asset"], "SELL", qty, exit_px, mode, "FILLED",
                                    reason=str(trade.get("reason") or ""), fees=qty * exit_px * fee_rate,
                                    at=trade["date"])
        if self.conn.execute("SELECT 1 FROM fin_trades WHERE exit_order_id=?", (exit_id,)).fetchone():
            return None
        tid = f"T-{uuid.uuid4().hex[:16]}"
        with self._tx():
            self.conn.execute(
                "INSERT INTO fin_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, entry["id"] if entry else None, exit_id, trade["asset"], trade.get("entry_date"),
                 trade["date"], trade.get("entry"), exit_px, float(trade["pnl"]), trade.get("r"),
                 trade.get("mfe_r"), trade.get("mae_r"), trade.get("slippage_pct"), trade.get("regime"),
                 trade.get("lesson"), str(trade.get("reason") or "")))
        return tid

    # ---- lecture ----

    def lineage(self, trade_id: str) -> Dict[str, Any]:
        """La lignée financière d'un trade : vente, achat, contrôle du
        risque, décision, version de la stratégie, signal, données."""
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()  # noqa: E731
        t = q("SELECT * FROM fin_trades WHERE id=?", trade_id)
        if t is None:
            raise KeyError(trade_id)
        out: Dict[str, Any] = {"trade": dict(t), "exit_order": dict(q("SELECT * FROM fin_orders WHERE id=?",
                                                                       t["exit_order_id"]))}
        if t["entry_order_id"]:
            o = q("SELECT * FROM fin_orders WHERE id=?", t["entry_order_id"])
            out["entry_order"] = dict(o)
            out["executions"] = [dict(r) for r in self.conn.execute(
                "SELECT * FROM fin_executions WHERE order_id IN (?,?)", (o["id"], t["exit_order_id"]))]
            out["risk_check"] = dict(q("SELECT * FROM fin_risk_checks WHERE id=?", o["risk_check_id"]))
            d = q("SELECT * FROM fin_decisions WHERE id=?", o["decision_id"])
            out["decision"] = dict(d)
            out["strategy"] = dict(q("SELECT * FROM fin_strategy_versions WHERE id=?", d["strategy_version_id"]))
            s = q("SELECT * FROM fin_signals WHERE decision_id=? AND asset=? AND direction='LONG'", d["id"], t["asset"])
            out["signal"] = dict(s) if s else None
            out["data"] = {"source": d["data_source"], "quality": d["data_quality"], "day": d["day"]}
        return out

    def trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Les derniers trades du journal (les plus récents d'abord)."""
        return [dict(r) for r in self.conn.execute("SELECT * FROM fin_trades ORDER BY closed_at DESC LIMIT ?",
                                                   (limit,))]

    def counts(self) -> Dict[str, int]:
        return {t.name: int(self.conn.execute(f"SELECT COUNT(*) FROM {t.name}").fetchone()[0])  # noqa: S608
                for t in TABLES}

    def verify(self) -> Dict[str, Any]:
        """Intégrité du journal : clés étrangères, achats sans contrôle ou
        sans exécution, déclencheurs d'ajout seul, migrations."""
        applied = self.applied()
        if not applied:
            return {"ok": True, "problems": [], "counts": {t.name: 0 for t in TABLES}, "schema": 0}
        problems: List[str] = []
        fk = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            problems.append(f"{len(fk)} lien(s) orphelin(s) (clés étrangères)")
        bad = self.conn.execute("SELECT COUNT(*) FROM fin_orders o WHERE o.status='FILLED' AND NOT EXISTS "
                                "(SELECT 1 FROM fin_executions x WHERE x.order_id=o.id)").fetchone()[0]
        if bad:
            problems.append(f"{bad} ordre(s) exécuté(s) sans exécution")
        bad = self.conn.execute("SELECT COUNT(*) FROM fin_orders o JOIN fin_risk_checks r ON r.id=o.risk_check_id "
                                "WHERE o.side='BUY' AND o.status='FILLED' AND r.status <> 'APPROVED'").fetchone()[0]
        if bad:
            problems.append(f"{bad} achat(s) exécuté(s) sans contrôle approuvé")
        triggers = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        for t in ("fin_risk_checks", "fin_executions"):
            if not {f"{t}_update_refuse", f"{t}_delete_refuse"} <= triggers:
                problems.append(f"{t} : protection d'ajout seul absente")
        if applied != {m.version: m.checksum for m in MIGRATIONS}:
            problems.append("schéma différent des migrations du code")
        return {"ok": not problems, "problems": problems, "counts": self.counts(),
                "schema": max(applied) if applied else 0}


class _Tx:
    """Transaction explicite (BEGIN IMMEDIATE … COMMIT, annulée en cas d'erreur)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type: Any, *_a: Any) -> None:
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")


def describe(v: Dict[str, Any]) -> str:
    """Le bilan de `verify`, en clair."""
    c = v["counts"]
    text = (f"schéma v{v['schema']} ; {c['fin_decisions']} décision(s), {c['fin_risk_checks']} contrôle(s) du "
            f"risque, {c['fin_orders']} ordre(s), {c['fin_trades']} trade(s)")
    return text + (" ; intégrité vérifiée" if v["ok"] else " ; PROBLÈMES : " + " ; ".join(v["problems"]))


def catalog() -> str:
    """docs/DONNEES.md : schéma (diagramme), fiche de chaque table, colonnes
    et contraintes tirées de la base elle-même."""
    j = Journal(":memory:")
    try:
        lines = ["# Socle de données de TrendGuard (journal financier)", "",
                 "Tiré de `trendguard/donnees.py` (`python -m trendguard.donnees catalogue` le réécrit) ; un test vérifie "
                 "que ce document et le schéma restent identiques.", "",
                 "```mermaid", "erDiagram",
                 "    fin_strategy_versions ||--o{ fin_decisions : version",
                 "    fin_decisions ||--o{ fin_signals : signaux",
                 "    fin_decisions ||--o{ fin_risk_checks : controles",
                 "    fin_decisions ||--o{ fin_orders : ordres",
                 "    fin_risk_checks ||--o| fin_orders : autorise",
                 "    fin_orders ||--o{ fin_executions : executions",
                 "    fin_orders ||--o| fin_trades : achat",
                 "    fin_orders ||--|| fin_trades : vente", "```", ""]
        for t in TABLES:
            cols = j.conn.execute(f"PRAGMA table_info({t.name})").fetchall()
            idx = [r[1] for r in j.conn.execute(f"PRAGMA index_list({t.name})") if not r[1].startswith("sqlite_")]
            lines += [f"## `{t.name}`", "", f"{t.purpose[:1].upper()}{t.purpose[1:]}.", "",
                      "| Rubrique | |", "| --- | --- |",
                      "| Propriétaire | `trendguard/donnees.py` (domaine « fin_ ») |",
                      f"| Lue par | {t.readers} |", f"| Écrite par | {t.writers} |",
                      f"| Contrat | {t.contract} |", f"| Durée de vie | {t.retention} |",
                      f"| Index | {', '.join(f'`{i}`' for i in idx) or 'clé primaire seulement'}"
                      + (f" ({t.index_reason})" if t.index_reason else "") + " |", "",
                      "| Colonne | Type | Obligatoire | Clé |", "| --- | --- | --- | --- |"]
            for c in cols:
                lines.append(f"| `{c[1]}` | {c[2] or '—'} | {'oui' if c[3] or c[5] else 'non'} | "
                             f"{'primaire' if c[5] else ''} |")
            lines.append("")
        return "\n".join(lines)
    finally:
        j.close()


@functools.lru_cache(maxsize=None)
def code_version() -> str:
    """Version du code en service (git), lue une fois par processus."""
    from .registre import git_version
    return git_version()


def path_for(gcfg: Any) -> str:
    """Le journal vit dans la base du bot (sauvegardée chaque nuit avec elle)."""
    db = getattr(gcfg, "db_file", "") or ":memory:"
    return db


def main(argv: Optional[List[str]] = None) -> int:
    """Commande `donnees` : vérifier (défaut), lignée d'un trade, réécrire
    docs/DONNEES.md."""
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Socle de données de TrendGuard (journal financier)")
    ap.add_argument("action", nargs="?", default="verifier", choices=["verifier", "lignee", "catalogue"])
    ap.add_argument("trade", nargs="?", default="", help="(lignee) identifiant du trade, ou rien pour le dernier")
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    if args.action == "catalogue":
        path = pathlib.Path(__file__).resolve().parent.parent / "docs" / "DONNEES.md"
        path.write_text(catalog(), encoding="utf-8")
        print(f"{path} réécrit")
        return 0
    j = Journal(path_for(load_guard_config_from_env()))
    try:
        if args.action == "verifier":
            v = j.verify()
            print(f"Journal financier : {describe(v)}")
            return 0 if v["ok"] else 1
        tid = args.trade or next((t["id"] for t in j.trades(1)), "")
        if not tid:
            print("Aucun trade clos dans le journal pour l'instant.")
            return 0
        print(json.dumps(j.lineage(tid), ensure_ascii=False, indent=1, default=str))
        return 0
    finally:
        j.close()


def params_of(p: Any) -> Dict[str, Any]:
    """Réglages de la stratégie, écrivables dans le registre des versions."""
    return json.loads(_json(dataclasses.asdict(p)))


if __name__ == "__main__":
    raise SystemExit(main())
