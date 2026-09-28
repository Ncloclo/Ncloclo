"""Types partagés : positions, portefeuille, états, contexte du bot.

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime
from typing import Any, Dict, List, Optional

from .constants import _LOG_ROOT, SCHEMA_VERSION, VERSION_MODULE
from .utils import _dataclass_from_dict, _day_key, _parse_iso, _utcnow, _week_key


class BotState(str, enum.Enum):
    FLAT = "FLAT"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    HALTED = "HALTED"


class ProtectionMode(str, enum.Enum):
    NONE = "NONE"
    OCO = "OCO"
    STOP_ONLY = "STOP_ONLY"


class ProtectionState(str, enum.Enum):
    NONE = "NONE"          # aucun ordre de protection connu
    ACTIVE = "ACTIVE"      # un ordre stop est ouvert, rien d'exécuté
    FILLED = "FILLED"      # au moins une exécution détectée
    CANCELED = "CANCELED"  # ordres terminés sans exécution (annulés/expirés)
    UNKNOWN = "UNKNOWN"    # état illisible (API)


class EntryResult(str, enum.Enum):
    SKIPPED = "skipped"        # aucun ordre envoyé
    OPENED = "opened"          # position ouverte (et protégée en live)
    ORDER_SENT = "order_sent"  # un ordre est parti mais pas de position nette


class HaltKind:
    DAILY_DD = "DAILY_DD"      # réarmé automatiquement le jour suivant
    WEEKLY_DD = "WEEKLY_DD"    # réarmé automatiquement la semaine suivante
    INTEGRITY = "INTEGRITY"    # état incertain : gel des actions discrétionnaires
    MANUAL = "MANUAL"          # nécessite `resume` après audit


# Raisons INTEGRITY levées automatiquement une fois l'ambiguïté résolue.
AUTO_CLEARABLE_HALTS = frozenset({"AMBIGUOUS_BUY", "AMBIGUOUS_SELL",
                                  "AMBIGUOUS_ORDER",
                                  "PENDING_ORDER_UNRESOLVED"})


@dataclass
class Fill:
    qty: float
    price: float
    order_id: str
    leg: str = ""                     # TP | SL | STOP | MARKET | EXTERNAL
    fee_quote: Optional[float] = None  # None = inconnu (estimé au fee_rate)


@dataclass
class ProtectionSnapshot:
    state: ProtectionState
    fills: List[Fill] = field(default_factory=list)
    open_ids: List[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class CancelResult:
    all_terminal: bool
    fills: List[Fill] = field(default_factory=list)


@dataclass
class Position:
    in_position: bool = False
    buy_price: float = 0.0            # prix moyen d'exécution (quote/base)
    cost_basis: float = 0.0           # coût de revient net de frais (quote/base)
    sl_price: float = 0.0
    tp_price: float = 0.0
    amount_held: float = 0.0          # base réellement détenue par le bot
    initial_amount: float = 0.0
    risk_per_unit: float = 0.0
    risk_quote_initial: float = 0.0   # dénominateur du R-multiple
    rr_used: float = 0.0
    module: str = ""
    regime: str = ""
    tier: str = ""
    entry_score: int = 0
    entry_adx: Optional[float] = None
    entry_obi: Optional[float] = None
    entry_order_id: Optional[str] = None
    entry_timestamp_ms: Optional[int] = None
    opened_at: Optional[str] = None
    entry_candle_ts: Optional[int] = None
    sl_update_candle_ts: Optional[int] = None
    low_since_sl_update: Optional[float] = None
    high_since_entry: Optional[float] = None
    break_even_done: bool = False
    last_trailing_ts: float = 0.0
    time_exit_attempts: int = 0
    time_exit_last_attempt_ts: float = 0.0
    time_exit_cooldown_until: Optional[str] = None
    realized_pnl: float = 0.0
    soft_stop: float = 0.0            # stop évalué à la clôture (TrendGuard)
    highest_close: float = 0.0
    partial_exit_count: int = 0
    eff_risk_pct: Optional[float] = None
    entry_equity: float = 0.0
    protection_mode: str = ProtectionMode.NONE.value
    oco_order_id: Optional[str] = None         # orderListId (OCO natif)
    oco_client_id: Optional[str] = None
    oco_tp_client_id: Optional[str] = None
    oco_sl_client_id: Optional[str] = None
    oco_tp_order_id: Optional[str] = None
    oco_sl_order_id: Optional[str] = None
    standalone_stop_order_id: Optional[str] = None
    protection_confirmed_ts: float = 0.0
    oco_misses: int = 0
    oco_uncertain_since: Optional[str] = None
    recorded_fills: Dict[str, float] = field(default_factory=dict)
    legs: List[Dict[str, Any]] = field(default_factory=list)

    def has_protection_ids(self) -> bool:
        return bool(self.oco_order_id or self.oco_tp_order_id
                    or self.oco_sl_order_id or self.standalone_stop_order_id)

    def clear_protection_ids(self) -> None:
        self.oco_order_id = None
        self.oco_client_id = None
        self.oco_tp_client_id = None
        self.oco_sl_client_id = None
        self.oco_tp_order_id = None
        self.oco_sl_order_id = None
        self.standalone_stop_order_id = None
        self.protection_mode = ProtectionMode.NONE.value
        self.oco_misses = 0
        self.oco_uncertain_since = None


@dataclass
class Portfolio:
    paper_cash: float = 1000.0
    paper_base: float = 0.0
    stats_wins: int = 0
    stats_losses: int = 0
    stats_total_pnl: float = 0.0
    consec_losses: int = 0
    losses_since_pause: int = 0
    dust_base: float = 0.0
    last_trades: List[Dict[str, Any]] = field(default_factory=list)
    per_module: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    per_tier: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class RiskState:
    daily_start_equity: Optional[float] = None
    daily_start_date: Optional[str] = None
    weekly_start_equity: Optional[float] = None
    weekly_start_date: Optional[str] = None
    paused_until: Optional[str] = None
    cooldown_until: Optional[str] = None
    cooldown_reason: Optional[str] = None
    halted: bool = False
    halt_reason: Optional[str] = None
    halt_kind: Optional[str] = None
    halted_date: Optional[str] = None
    halted_week: Optional[str] = None
    flash_cooldown_until: Optional[str] = None


@dataclass
class BlockchainState:
    last_balance_eth: float = 0.0
    last_balance_token: float = 0.0
    last_nonce: int = 0
    last_pending_tx_hash: Optional[str] = None
    pending_tx_hashes: List[str] = field(default_factory=list)
    last_pending_tx_since: Optional[str] = None
    last_pending_tx_fee_wei: Optional[str] = None
    last_pending_tx_priority_wei: Optional[str] = None
    last_pending_tx_nonce: Optional[int] = None
    last_pending_tx_to: Optional[str] = None
    last_pending_tx_value_wei: Optional[str] = None
    last_pending_tx_data: Optional[str] = None
    last_pending_tx_gas: Optional[int] = None
    last_pending_tx_kind: Optional[str] = None
    rbf_attempts: int = 0
    rbf_exhausted_notified: bool = False
    total_gas_spent_eth: float = 0.0
    tx_count: int = 0
    last_block_seen: int = 0
    eip1559_supported: Optional[bool] = None
    last_sweep_ts: float = 0.0
    last_sweep_fail_ts: float = 0.0
    sweep_count: int = 0


@dataclass
class AdaptiveState:
    signal_latencies_ms: List[int] = field(default_factory=list)
    current_freshness_min: int = 45
    current_atr_min_pct: float = 0.005
    current_consec_pause: int = 5
    last_update_ts: float = 0.0
    last_latency_candle_ts: Optional[int] = None
    bootstrap_done: bool = False


@dataclass
class BotContext:
    schema_version: int = SCHEMA_VERSION
    state: str = BotState.FLAT.value
    position: Position = field(default_factory=Position)
    portfolio: Portfolio = field(default_factory=Portfolio)
    risk: RiskState = field(default_factory=RiskState)
    blockchain: BlockchainState = field(default_factory=BlockchainState)
    adaptive: AdaptiveState = field(default_factory=AdaptiveState)
    last_signal_candle_ts: Optional[int] = None
    last_decision_candle_ts: Optional[int] = None
    last_report_date: Optional[str] = None
    last_report_attempt_ts: float = 0.0
    benchmark_start_price: Optional[float] = None
    benchmark_start_equity: Optional[float] = None
    last_equity_snapshot_ts: float = 0.0
    last_orphan_check_ts: float = 0.0
    orphan_balance: bool = False
    orphan_balance_since: Optional[str] = None
    pending_order: Optional[Dict[str, Any]] = None
    flatten_in_progress: bool = False
    cycle_count: int = 0
    started_at: Optional[str] = None
    bot_version: str = VERSION_MODULE

    _TRANSIENT_FIELDS = frozenset({"flatten_in_progress"})
    _SCALAR_FIELDS = ("last_signal_candle_ts", "last_decision_candle_ts",
                      "last_report_date", "benchmark_start_price",
                      "benchmark_start_equity", "orphan_balance",
                      "orphan_balance_since", "pending_order", "started_at")
    _CAST_FIELDS = (("last_report_attempt_ts", float),
                    ("last_equity_snapshot_ts", float),
                    ("last_orphan_check_ts", float),
                    ("cycle_count", int))
    _SECTIONS = (("position", Position), ("portfolio", Portfolio),
                 ("risk", RiskState), ("blockchain", BlockchainState),
                 ("adaptive", AdaptiveState))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for f in self._TRANSIENT_FIELDS:
            if f in d:
                d[f] = False if isinstance(d[f], bool) else None
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotContext":
        if not isinstance(data, dict):
            return cls()
        try:
            raw_version = int(data.get("schema_version", 1))
        except Exception:
            raw_version = 1
        if raw_version < SCHEMA_VERSION:
            return cls._migrate(data, raw_version)
        if raw_version > SCHEMA_VERSION:
            log = logging.getLogger(_LOG_ROOT)
            log.critical(
                f"[MIGRATE] schéma {raw_version} > {SCHEMA_VERSION} : "
                f"contexte d'une version plus récente → HALT")
            ctx = cls._rebuild(data)
            halt_ctx(ctx, "SCHEMA_DOWNGRADE", HaltKind.MANUAL)
            return ctx
        return cls._rebuild(data)

    @classmethod
    def _migrate(cls, raw: Dict[str, Any], from_version: int) -> "BotContext":
        log = logging.getLogger(_LOG_ROOT)
        log.info(f"[MIGRATE] schéma {from_version} → {SCHEMA_VERSION}")
        try:
            ctx = cls._rebuild(raw)
            ctx.schema_version = SCHEMA_VERSION
            ctx.bot_version = VERSION_MODULE
            return ctx
        except Exception as e:
            log.critical(
                f"[MIGRATE-FAIL] {from_version}→{SCHEMA_VERSION}: {e} — "
                f"contexte préservé en mode dégradé + HALT")
            ctx = cls()
            ctx.schema_version = SCHEMA_VERSION
            for fname, klass in cls._SECTIONS:
                sec = raw.get(fname)
                if isinstance(sec, dict):
                    try:
                        setattr(ctx, fname, _dataclass_from_dict(klass, sec))
                    except Exception:
                        pass
            halt_ctx(ctx, "SCHEMA_MIGRATION_FAILED", HaltKind.MANUAL)
            return ctx

    @classmethod
    def _rebuild(cls, data: Dict[str, Any]) -> "BotContext":
        log = logging.getLogger(_LOG_ROOT)
        ctx = cls()
        try:
            ctx.schema_version = int(data.get("schema_version", SCHEMA_VERSION))
        except Exception:
            ctx.schema_version = SCHEMA_VERSION
        ctx.state = str(data.get("state", BotState.FLAT.value))
        for fname, klass in cls._SECTIONS:
            raw = data.get(fname)
            if not isinstance(raw, dict):
                continue
            valid = {f.name for f in fields(klass)}
            unknown = set(raw.keys()) - valid
            if unknown:
                log.debug(f"[REBUILD] {fname}: champs ignorés {sorted(unknown)}")
            try:
                setattr(ctx, fname, _dataclass_from_dict(klass, raw))
            except Exception as e:
                log.error(f"[REBUILD] {fname} corrompu → reset ({e})")
                setattr(ctx, fname, klass())
        try:
            lat = ctx.adaptive.signal_latencies_ms
            if not isinstance(lat, list):
                ctx.adaptive.signal_latencies_ms = []
            elif len(lat) > 2000:
                ctx.adaptive.signal_latencies_ms = lat[-500:]
                log.warning("[REBUILD] signal_latencies_ms tronqué à 500")
        except Exception:
            ctx.adaptive.signal_latencies_ms = []
        for k in cls._SCALAR_FIELDS:
            if k in data:
                try:
                    setattr(ctx, k, data[k])
                except Exception:
                    pass
        for k, cast in cls._CAST_FIELDS:
            if k in data:
                try:
                    setattr(ctx, k, cast(data[k]))
                except Exception:
                    pass
        ctx.flatten_in_progress = False
        cls._post_load_fixups(ctx, data)
        return ctx

    @staticmethod
    def _post_load_fixups(ctx: "BotContext", raw: Dict[str, Any]) -> None:
        """Rend un contexte V29.5 (schéma 10) cohérent avec V29.6."""
        p = ctx.position
        if p.in_position:
            if p.initial_amount <= 0:
                p.initial_amount = p.amount_held
            if p.cost_basis <= 0 and p.buy_price > 0:
                p.cost_basis = p.buy_price * 1.001
            if p.risk_quote_initial <= 0 and p.initial_amount > 0:
                per_unit = max(p.cost_basis - p.sl_price * 0.999,
                               p.risk_per_unit, p.buy_price * 0.001)
                p.risk_quote_initial = p.initial_amount * per_unit
        if not isinstance(p.recorded_fills, dict):
            p.recorded_fills = {}
        r = ctx.risk
        if r.halted and not r.halt_kind:
            reason = r.halt_reason or ""
            if reason.startswith("Daily DD"):
                r.halt_kind = HaltKind.DAILY_DD
            elif reason.startswith("Weekly DD"):
                r.halt_kind = HaltKind.WEEKLY_DD
                d = _parse_iso(r.halted_date) if r.halted_date else None
                r.halted_week = _week_key(d) if d else _week_key()
            else:
                r.halt_kind = HaltKind.INTEGRITY
        pf = ctx.portfolio
        if pf.losses_since_pause <= 0 and pf.consec_losses > 0 \
                and not r.paused_until:
            pf.losses_since_pause = pf.consec_losses
        b = ctx.blockchain
        if b.last_pending_tx_hash and not b.pending_tx_hashes:
            b.pending_tx_hashes = [b.last_pending_tx_hash]

    @classmethod
    def for_backtest(cls, initial_capital: float = 1000.0) -> "BotContext":
        ctx = cls()
        ctx.portfolio.paper_cash = initial_capital
        return ctx


def halt_ctx(ctx: BotContext, reason: str, kind: str = HaltKind.INTEGRITY,
             now: Optional[datetime] = None, orphan: bool = False) -> None:
    n = now or _utcnow()
    r = ctx.risk
    r.halted = True
    r.halt_reason = reason
    r.halt_kind = kind
    r.halted_date = _day_key(n)
    r.halted_week = _week_key(n)
    if orphan:
        ctx.orphan_balance = True
        ctx.orphan_balance_since = ctx.orphan_balance_since or n.isoformat()


def clear_halt(ctx: BotContext) -> None:
    r = ctx.risk
    r.halted = False
    r.halt_reason = None
    r.halt_kind = None
    r.halted_date = None
    r.halted_week = None


def is_frozen(ctx: BotContext) -> bool:
    """Gel des actions discrétionnaires (partielles, BE, trailing, time-exit)
    quand l'état est incertain. La protection reste maintenue."""
    return bool(ctx.risk.halted and ctx.risk.halt_kind in
                (HaltKind.INTEGRITY, HaltKind.MANUAL))


@dataclass
class Signal:
    action: str
    regime: str
    module: str
    tier: str
    score: int
    reject: Optional[str] = None

    @property
    def is_buy(self) -> bool:
        return self.action == "BUY"
