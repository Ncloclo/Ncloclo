"""Infrastructure : journaux, alertes Telegram, verrou d'instance, console, heartbeat.

Partie du moteur V29 (paquet v29, anciennement v29.py).
"""
from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import queue
import re
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional

import numpy as np

from .constants import _LOG_ROOT
from .utils import _parse_iso, scrub_secrets, _utcnow, _utcnow_iso
from .config import Config
from .models import AdaptiveState, BotContext


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def build_logger(cfg: Config) -> logging.Logger:
    parent = logging.getLogger(_LOG_ROOT)
    parent.handlers.clear()
    parent.setLevel(logging.INFO)
    fh = RotatingFileHandler(cfg.log_file, maxBytes=10_000_000,
                              backupCount=10, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    fh.setLevel(logging.INFO)
    parent.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"))
    sh.setLevel(logging.INFO)
    parent.addHandler(sh)
    parent.propagate = False
    env_lg = logging.getLogger(f"{_LOG_ROOT}.env")
    env_lg.handlers.clear()
    env_lg.propagate = True
    lg = logging.getLogger(f"{_LOG_ROOT}.{cfg.base}_{cfg.quote}")
    lg.setLevel(logging.DEBUG)
    lg.handlers.clear()
    lg.propagate = True
    return lg


def build_heartbeat_logger(cfg: Config) -> logging.Logger:
    lg = logging.getLogger(f"{_LOG_ROOT}.{cfg.base}_{cfg.quote}.heartbeat")
    lg.setLevel(logging.INFO)
    lg.handlers.clear()
    fh = RotatingFileHandler(cfg.heartbeat_log_file, maxBytes=5_000_000,
                             backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    fh.setLevel(logging.INFO)
    lg.addHandler(fh)
    lg.propagate = False
    return lg


class Notifier:
    """Notifications Telegram. Par défaut ASYNCHRONES (thread dédié) :
    un envoi lent ne retarde jamais une action de trading (panic, stop)."""

    def __init__(self, token: str, chat: str,
                 logger: Optional[logging.Logger] = None,
                 dedup_sec: int = 60, fail_backoff_sec: int = 30,
                 async_mode: bool = True):
        self.token = token
        self.chat = chat
        self.logger = logger
        self.dedup_sec = dedup_sec
        self.fail_backoff_sec = fail_backoff_sec
        self._last_success: Dict[str, float] = {}
        self._last_fail: Dict[str, float] = {}
        self._last_purge_ts = time.time()
        self._lock = threading.Lock()
        self._q: "queue.Queue" = queue.Queue(maxsize=500)
        self._thread: Optional[threading.Thread] = None
        if async_mode and token and chat:
            self._thread = threading.Thread(target=self._worker,
                                            name="notifier", daemon=True)
            self._thread.start()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat)

    def __call__(self, msg: str, dedup_key: Optional[str] = None,
                 critical: bool = False, sync: bool = False) -> bool:
        if critical and self.logger:
            self.logger.error(f"[NOTIFY] {msg}")
        if not self.enabled:
            if critical and self.logger:
                self.logger.error(f"[NOTIFIER-OFF] {msg}")
            return True
        key = dedup_key or hashlib.sha1(msg.encode()).hexdigest()
        now = time.time()
        with self._lock:
            if now - self._last_purge_ts > 300 or len(self._last_success) > 1000:
                cutoff = now - 2 * max(self.dedup_sec, self.fail_backoff_sec)
                self._last_success = {k: v for k, v in self._last_success.items()
                                      if v > cutoff}
                self._last_fail = {k: v for k, v in self._last_fail.items()
                                   if v > cutoff}
                self._last_purge_ts = now
            if now - self._last_fail.get(key, 0) < self.fail_backoff_sec:
                return False
            if now - self._last_success.get(key, 0) < self.dedup_sec:
                return True
        if sync or self._thread is None:
            return self._send(msg, key, critical)
        try:
            self._q.put_nowait((msg, key, critical))
            return True
        except queue.Full:
            if self.logger:
                self.logger.warning(f"[NOTIFIER-FULL] {msg[:200]}")
            return False

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            try:
                self._send(*item)
            except Exception:
                pass

    def _send(self, msg: str, key: str, critical: bool) -> bool:
        now = time.time()
        try:
            import urllib.parse
            import urllib.request
            data = urllib.parse.urlencode({"chat_id": self.chat,
                                           "text": msg}).encode()
            with urllib.request.urlopen(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data=data, timeout=5,
            ) as resp:
                body = resp.read().decode(errors="replace")
            try:
                ok = bool(json.loads(body).get("ok", False))
            except Exception:
                ok = True
        except Exception as e:
            ok = False
            if self.logger:
                level = logging.ERROR if critical else logging.WARNING
                self.logger.log(level, "[NOTIFIER-KO] "
                                + scrub_secrets(str(e), [self.token])
                                + f" — msg: {msg[:200]}")
        with self._lock:
            if ok:
                self._last_success[key] = now
            else:
                self._last_fail[key] = now
        return ok

    def close(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass
            self._thread.join(timeout)
            self._thread = None


class ProcessLock:
    """Verrou d'instance unique posé par le système d'exploitation (flock
    sous Unix, msvcrt.locking sous Windows) : il est libéré automatiquement
    si le process meurt, même brutalement. Aucun verrou « périmé » possible."""

    _HELD_ERRNOS = {errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES,
                    getattr(errno, "EDEADLK", -1), getattr(errno, "EDEADLOCK", -1)}
    # Windows : les octets verrouillés sont illisibles par les autres
    # process. On verrouille un octet loin du texte d'identité (au-delà de
    # la fin du fichier, ce que Windows autorise) pour qu'il reste lisible.
    _MSVCRT_OFFSET = 1 << 20

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.handle = None
        self._mode: Optional[str] = None

    def holder(self) -> str:
        """Identité du détenteur écrite dans le fichier (pid, machine, date)."""
        try:
            with open(self.path, encoding="utf-8") as fh:
                return fh.read().strip() or "détenteur inconnu"
        except OSError:
            return "détenteur inconnu"

    def _lock(self, handle) -> None:
        try:
            import fcntl
        except ImportError:
            fcntl = None
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._mode = "fcntl"
            return
        try:
            import msvcrt
        except ImportError:
            raise SystemExit("Aucun mécanisme de verrouillage disponible sur ce "
                             "système : démarrage refusé.")
        handle.seek(self._MSVCRT_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        self._mode = "msvcrt"

    def acquire(self) -> None:
        try:
            handle = open(self.path, "a+", encoding="utf-8")
        except OSError as e:
            raise SystemExit(f"Verrou impossible à créer ({self.path}) : "
                             f"{e.strerror or e}")
        try:
            self._lock(handle)
        except OSError as e:
            handle.close()
            if e.errno in self._HELD_ERRNOS:
                raise SystemExit(f"Une autre instance du bot tourne déjà "
                                 f"({self.holder()}) — verrou {self.path}")
            raise SystemExit(f"Verrouillage impossible sur {self.path} "
                             f"({e.strerror or e}) : ce système de fichiers ne "
                             f"gère pas les verrous (partage réseau ?). Placez "
                             f"les fichiers d'état sur un disque local.")
        except BaseException:
            handle.close()
            raise
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} machine={socket.gethostname()} "
                     f"depuis={_utcnow_iso()}\n")
        handle.flush()
        self.handle = handle

    def release(self) -> None:
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            if self._mode == "fcntl":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif self._mode == "msvcrt":
                import msvcrt
                handle.seek(self._MSVCRT_OFFSET)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            handle.close()
            self._mode = None


def acquire_instance_locks(lock_file: str, db_file: str) -> List[ProcessLock]:
    """Verrou configuré + verrou attaché à la base de données (chemin
    absolu) : deux instances ne peuvent jamais partager la même base, même
    avec des LOCK_FILE différents ou lancées depuis des dossiers différents."""
    paths: List[str] = []
    candidates = [lock_file]
    if db_file and db_file != ":memory:":
        candidates.append(db_file + ".lock")
    for p in candidates:
        if p and p != os.devnull:
            ap = os.path.abspath(p)
            if ap not in paths:
                paths.append(ap)
    locks: List[ProcessLock] = []
    try:
        for p in paths:
            lk = ProcessLock(p)
            lk.acquire()
            locks.append(lk)
    except BaseException:
        release_locks(locks)
        raise
    return locks


def release_locks(locks: List[ProcessLock]) -> None:
    for lk in reversed(locks):
        lk.release()


class Console:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    GREY = "\033[90m"
    BG_RED = "\033[41m"

    def __init__(self, enabled: bool):
        self.enabled = bool(enabled) and sys.stdout.isatty()

    def c(self, text: str, color: str) -> str:
        if not self.enabled:
            return text
        return f"{color}{text}{self.RESET}"


class Heartbeat:
    SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    SPARK = "▁▂▃▄▅▆▇█"
    REGIME_ICON = {"TREND_UP": "🟢↑", "TREND_DOWN": "🔴↓",
                    "RANGE": "🟡↔", "UNCLEAR": "⚪?"}

    def __init__(self, cfg: Config, logger: logging.Logger,
                 hb_logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger
        self.hb_logger = hb_logger
        self.console = Console(cfg.heartbeat_use_colors)
        self._start_ts = time.time()
        self._frame = 0
        self._last_equity = 0.0
        self._equity_history: deque = deque(
            maxlen=cfg.heartbeat_equity_window)
        self._last_equity_snapshot = 0.0

    def _fmt_duration(self, seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _sparkline(self, values: List[float]) -> str:
        if len(values) < 2:
            return ""
        try:
            arr = np.array(values, dtype=float)
            arr = arr[np.isfinite(arr)]
            if len(arr) < 2:
                return ""
            lo, hi = float(arr.min()), float(arr.max())
            if hi - lo < 1e-9:
                return self.SPARK[0] * len(arr)
            norm = (arr - lo) / (hi - lo)
            return "".join(
                self.SPARK[min(len(self.SPARK) - 1,
                                int(v * (len(self.SPARK) - 1)))]
                for v in norm)
        except Exception:
            return ""

    def _colored_pnl(self, value: float, fmt: str = "+.3f") -> str:
        s = f"{value:{fmt}}"
        if value > 0:
            return self.console.c(s, Console.GREEN)
        if value < 0:
            return self.console.c(s, Console.RED)
        return self.console.c(s, Console.GREY)

    def _fmt_subsystems(self, subs: Dict[str, str]) -> str:
        if not subs:
            return ""
        parts = []
        for k, v in subs.items():
            v_up = str(v).upper()
            parts_set = set(v_up.replace("+", "|").split("|"))
            bad = parts_set & {"KO", "HALTED", "UNPROTECTED", "LOST", "NONE",
                                "HALT", "ORPHAN", "TX_PENDING", "PENDING",
                                "FROZEN"}
            good = parts_set & {"OK", "OCO", "STOP_ONLY"}
            off = parts_set & {"OFF", "INIT", "NOOP", "—", ""}
            if bad:
                parts.append(f"{k}={self.console.c(v, Console.RED)}")
            elif good:
                parts.append(f"{k}={self.console.c(v, Console.GREEN)}")
            elif off:
                parts.append(f"{k}={self.console.c(v, Console.GREY)}")
            else:
                parts.append(f"{k}={self.console.c(v, Console.YELLOW)}")
        return " ".join(parts)

    def tick(self, ctx: BotContext, regime: str, equity: Optional[float],
             live_price: float, adaptive: Optional[AdaptiveState] = None,
             benchmark_start_equity: Optional[float] = None,
             subsystems: Optional[Dict[str, str]] = None) -> str:
        if equity is not None:
            if abs(equity - self._last_equity_snapshot) > 1e-9:
                self._equity_history.append(equity)
                self._last_equity_snapshot = equity
        eq_delta = 0.0
        if equity is not None and self._last_equity > 0:
            eq_delta = equity - self._last_equity
        if equity is not None:
            self._last_equity = equity
        total_pnl_pct = 0.0
        if (equity is not None and benchmark_start_equity
                and benchmark_start_equity > 0):
            total_pnl_pct = (equity / benchmark_start_equity - 1) * 100
        self._frame = (self._frame + 1) % len(self.SPINNER)
        spin = self.SPINNER[self._frame] if self.cfg.heartbeat_spinner else "*"
        uptime = self._fmt_duration(time.time() - self._start_ts)
        eq_str = "n/a" if equity is None else f"{equity:.2f}"
        delta_str = ""
        if eq_delta != 0.0:
            sign = "+" if eq_delta > 0 else ""
            delta_str = " (" + self._colored_pnl(eq_delta, f"{sign}.3f") + ")"
        regime_icon = self.REGIME_ICON.get(regime, "⚪")
        pos_str = "FLAT"
        if ctx.position.in_position:
            pos_str = f"LONG {ctx.position.module}/{ctx.position.tier}"
        flags = []
        if ctx.risk.halted:
            flags.append(self.console.c(
                f"HALTED[{ctx.risk.halt_kind or '?'}]", Console.BG_RED))
        if ctx.risk.paused_until:
            flags.append(self.console.c("PAUSED", Console.YELLOW))
        if ctx.orphan_balance:
            flags.append(self.console.c("ORPHAN", Console.YELLOW))
        if ctx.pending_order:
            flags.append(self.console.c("ORDER_PENDING", Console.YELLOW))
        if ctx.blockchain.last_pending_tx_hash:
            flags.append(self.console.c("TX_PENDING", Console.CYAN))
        flag_str = (" " + " ".join(flags)) if flags else ""
        line1 = (f"{spin} #{ctx.cycle_count:>5} {uptime} "
                 f"eq={eq_str}{delta_str} px={live_price:.5f} "
                 f"{regime_icon} {regime} | {pos_str}{flag_str}")
        wins = int(ctx.portfolio.stats_wins)
        losses = int(ctx.portfolio.stats_losses)
        total_trades = wins + losses
        wr = (wins / total_trades * 100) if total_trades else 0.0
        total_pnl = float(ctx.portfolio.stats_total_pnl)
        dd_str = "-"
        if ctx.risk.daily_start_equity and equity:
            dd = (equity / ctx.risk.daily_start_equity - 1) * 100
            dd_str = f"{dd:+.2f}%"
        consec_str = ""
        if ctx.portfolio.consec_losses > 0:
            consec_str = self.console.c(
                f" consec={ctx.portfolio.consec_losses}",
                Console.RED if ctx.portfolio.consec_losses >= 3
                else Console.YELLOW)
        line2 = (f"  ├─ PnL: {self._colored_pnl(total_pnl, '+.3f')} "
                 f"({total_pnl_pct:+.2f}%) W/L: {wins}/{losses} "
                 f"({wr:.0f}%) DD(j): {dd_str}{consec_str}")
        adapt_str = ""
        if adaptive:
            adapt_str = (f"fresh={adaptive.current_freshness_min}min "
                         f"atr={adaptive.current_atr_min_pct*100:.2f}% "
                         f"consec={adaptive.current_consec_pause}")
        spark = self._sparkline(list(self._equity_history))
        spark_str = f" equity: {spark}" if spark else ""
        line3 = f"  ├─{spark_str}  {adapt_str}"
        line4 = "  └─ FLAT"
        if ctx.position.in_position:
            p = ctx.position
            pnl_live = (live_price * (1 - self.cfg.fee_rate) - p.cost_basis) \
                * p.amount_held + p.realized_pnl
            pnl_str = self._colored_pnl(pnl_live, "+.4f")
            opened = _parse_iso(p.opened_at)
            held_sec = (_utcnow() - opened).total_seconds() if opened else 0.0
            held_str = self._fmt_duration(held_sec)
            sl_dist_pct = ((p.buy_price - p.sl_price) / p.buy_price * 100
                           if p.buy_price else 0.0)
            be_flag = self.console.c(" BE", Console.GREEN) \
                if p.break_even_done else ""
            line4 = (f"  └─ {p.module}/{p.tier} entry={p.buy_price:.5f} "
                     f"sl={p.sl_price:.5f} ({-sl_dist_pct:+.2f}%) "
                     f"tp={p.tp_price:.5f} held={p.amount_held:.2f} "
                     f"pnl={pnl_str} age={held_str}{be_flag}")
        block_lines = [line1, line2, line3]
        if subsystems:
            block_lines.append(f"  ├─ {self._fmt_subsystems(subsystems)}")
        block_lines.append(line4)
        block = "\n".join(block_lines)
        plain = re.sub(r"\033\[[0-9;]*m", "", block)
        self.hb_logger.info(plain)
        if sys.stdout.isatty():
            print(block, flush=True)
        return plain
