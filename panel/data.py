"""Lecture seule des données du bot pour le panneau : état, historique du
capital, positions, trades, veille et journal. La base SQLite est ouverte
en lecture seule : le bot peut tourner en même temps, rien n'est modifié."""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

import v29
from trendguard import (
    attribution,
    bibliotheque,
    bourses,
    brvm,
    courtiers,
    evenements,
    levier,
    libre,
    modeles,
    multi_actifs,
    pays,
    pistes,
    profil,
    savoir,
)
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
            # Contrats à terme de Binance (trendguard/derives.py) : financement,
            # intérêt ouvert, comptes acheteurs.
            "derives": ind.get("derives"),
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


def markets_view(key: str, close: pd.DataFrame, book: Dict[str, Any], window: str = "1y") -> Dict[str, Any]:
    """Page d'une classe d'actifs : chaque marché lu, sa courbe sur `window`,
    son état pour la règle et ses positions fictives (lecture seule)."""
    category = dict(multi_actifs.CLASSES).get(key)
    if category is None:
        raise ValueError("classe d'actifs inconnue")
    window = window if window in multi_actifs.SPARK_SESSIONS else "1y"
    held = multi_actifs.held_positions(book)
    rows, missing = [], []
    for code in multi_actifs.codes_of(category):
        s = close[code].dropna() if code in close.columns else pd.Series(dtype=float)
        if s.empty:
            missing.append(multi_actifs.NAME.get(code, code))
            continue
        rows.append(dict(multi_actifs.market_detail(code, s), spark=multi_actifs.spark(s, window),
                         positions=held.get(code, [])))
    return {"key": key, "category": category, "window": window, "rows": rows, "missing": missing,
            "fetched_at": book.get("fetched_at"), "error": book.get("error") or ""}


def market_view(code: str, close: pd.DataFrame, book: Dict[str, Any], days: int = 0) -> Dict[str, Any]:
    """Fenêtre de détail d'un marché : son état et les points de son
    graphique (clôture, moyenne de 150 séances, niveau de cassure)."""
    if code not in multi_actifs.NAME:
        raise ValueError("marché inconnu")
    s = close[code].dropna() if code in close.columns else pd.Series(dtype=float)
    if s.empty:
        raise ValueError("historique pas encore lu : première lecture avec le noyau de savoir")
    detail = dict(multi_actifs.market_detail(code, s), positions=multi_actifs.held_positions(book).get(code, []))
    return {"detail": detail, "points": multi_actifs.market_points(s, max(0, min(int(days), 4000))),
            "sma": multi_actifs.REGIME_N, "breakout_n": 30}


class BotData:
    def __init__(self, gcfg: Any, market: Market):
        self.g = gcfg
        self.market = market
        self.multi_cache = os.path.join(v29.APP_DIR, "data_multi")       # suivi quotidien (savoir.py)
        self.brvm_cache = os.path.join(v29.APP_DIR, "data_brvm")         # séances de la BRVM gardées par le bot
        self._multi: Optional[Tuple[Tuple[float, ...], pd.DataFrame]] = None

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

    def analyse(self, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Analyse du portefeuille (page Positions) : d'où viennent les
        résultats (positions au cours du moment), risque d'un jour, tests de
        résistance et qualité des données de la dernière décision."""
        state = self.state() if state is None else state
        pos = self.positions(state)["positions"]
        open_pnl = {p["asset"]: (p["price"] - p["entry"]) * p["qty"] for p in pos if p.get("price")}
        return {"attribution": attribution.attribution(state.get("trades") or [], open_pnl),
                "risque": state.get("risque_jour") or {}, "stress": state.get("stress") or {},
                "qualite": state.get("qualite") or {}}

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
                "report": watch_summary(rep), "ai": ai_summary(), "savoir": self.savoir(),
                "evenements": self.evenements(state), "modeles": self.modeles(),
                "bibliotheque": self.bibliotheque(), "marches": self.marches(), "levier": self.levier(),
                "macro": self.macro()}

    def macro(self) -> Optional[Dict[str, Any]]:
        """Indicateurs de marché du jour (lecture seule) : VIX, dollar, taux
        américains ; None tant qu'ils n'ont pas été lus."""
        view = self._multi_book().get("macro")
        return dict(view, line=multi_actifs.describe_macro(view)) if isinstance(view, dict) and view else None

    def _from_savoir(self, *keys: str) -> Optional[List[Any]]:
        """Valeurs gardées dans le noyau de savoir (lecture seule) ; None sans
        noyau de savoir ou s'il est illisible."""
        path = getattr(self.g, "savoir_db", "") or savoir.DEFAULT_DB
        if not getattr(self.g, "savoir", False) or not os.path.exists(path):
            return None
        try:
            memory = savoir.Memory(path, readonly=True)
        except sqlite3.Error:
            return None
        try:
            return [memory.get(k) for k in keys]
        except (sqlite3.Error, ValueError):
            return None
        finally:
            memory.close()

    def levier(self) -> Optional[Dict[str, Any]]:
        """Étude du levier (lecture seule) : levier permis par la règle du bot
        et chaque politique étudiée ; jamais appliqué. None sans noyau de savoir."""
        got = self._from_savoir(levier.KEY)
        if got is None:
            return None
        book = got[0] if isinstance(got[0], dict) else {}
        return {"day": book.get("day"), "allowed": book.get("allowed", 1.0), "policy": book.get("policy") or "",
                "text": book.get("text") or "", "rows": book.get("rows") or [],
                "periods": book.get("periods") or [], "rate": book.get("rate", levier.BORROW_RATE),
                "exposure_pct": book.get("exposure_pct") or []}

    def _multi_close(self) -> pd.DataFrame:
        """Clôtures de tous les marchés hors cryptos, sur le calendrier commun du
        suivi quotidien ; relues seulement quand un fichier change."""
        codes = multi_actifs.ALL_CODES + multi_actifs.CURRENCY_CODES
        files = [os.path.join(self.multi_cache, f"{c}.csv") for c in codes]
        sig = tuple(os.path.getmtime(f) if os.path.exists(f) else 0.0 for f in files)
        if self._multi is None or self._multi[0] != sig:
            self._multi = (sig, multi_actifs.load(self.multi_cache, codes))
        return self._multi[1]

    def _multi_book(self) -> Dict[str, Any]:
        got = self._from_savoir(multi_actifs.KEY)
        return got[0] if got and isinstance(got[0], dict) else {}

    def countries(self, window: str = "1y") -> Dict[str, Any]:
        """Page des pays du monde (lecture seule) : classement MSCI, indice,
        devise, indicateurs de la Banque mondiale."""
        got = self._from_savoir(pays.KEY, brvm.KEY)
        book = got[0] if got and isinstance(got[0], dict) else {}
        return pays.countries_view(self._multi_close(), book, window, regional=brvm.composite(got[1] if got else None))

    def brvm(self) -> Dict[str, Any]:
        """Page BRVM (lecture seule) : indices, activité, actions de la
        dernière séance lue, et l'historique construit par le bot."""
        got = self._from_savoir(brvm.KEY)
        return brvm.view(got[0] if got else None, self.brvm_cache)

    def exchanges(self) -> Dict[str, Any]:
        """Page « Bourses du monde » (lecture seule) : chaque place, sa séance,
        ses indices (Yahoo, pages officielles), son régulateur, son accès
        depuis votre pays (TG_PAYS) et l'étude de la règle."""
        got = self._from_savoir(bourses.KEY, multi_actifs.KEY, brvm.KEY)
        close = self._multi_close()
        vix = multi_actifs.load(self.multi_cache, ["vix"])
        if not vix.empty:
            close = close.join(vix, how="outer") if not close.empty else vix
        return bourses.view(got[0] if got else None, close, got[1] if got else None, datetime.now(timezone.utc),
                            got[2] if got else None, brvm_cache=self.brvm_cache, profile=profil.of_config(self.g))

    def brokers(self) -> Dict[str, Any]:
        """Page « Courtiers » (lecture seule) : comptes de démonstration, plan
        miroir à blanc, cours comparés et annuaire. Du fichier .env, seule la
        présence des identifiants est regardée, jamais leur valeur."""
        got = self._from_savoir(courtiers.KEY, multi_actifs.KEY)
        env = dict(os.environ, TG_PAYS=getattr(self.g, "pays", "") or "", TG_FUSEAU=getattr(self.g, "fuseau", "") or "")
        return courtiers.view(got[0] if got else None, got[1] if got else None, env)

    def markets(self, key: str, window: str = "1y") -> Dict[str, Any]:
        """Page d'une classe d'actifs (lecture seule)."""
        return markets_view(key, self._multi_close(), self._multi_book(), window)

    def market_info(self, code: str, days: int = 0) -> Dict[str, Any]:
        """Détail d'un marché hors cryptos (lecture seule)."""
        return market_view(code, self._multi_close(), self._multi_book(), days)

    def marches(self) -> Optional[Dict[str, Any]]:
        """Tous les marchés hors cryptos (lecture seule) : état de chacun pour
        la règle et portefeuille en argent fictif ; None sans noyau de savoir."""
        got = self._from_savoir(multi_actifs.KEY)
        if got is None:
            return None
        book = got[0] if isinstance(got[0], dict) else {}
        return {"rows": book.get("rows") or [], "portfolio": book.get("portfolio"), "binance": book.get("binance"),
                "fetched_at": book.get("fetched_at"), "error": book.get("error") or "",
                "unread": [multi_actifs.NAME.get(c, c) for c in book.get("unread") or []]}

    def bibliotheque(self) -> Optional[Dict[str, Any]]:
        """Bibliothèque de recherche (lecture seule) : pistes tirées des
        travaux récents d'arXiv, avec la question à vérifier ; None sans
        noyau de savoir."""
        got = self._from_savoir(bibliotheque.KEY, pistes.KEY)
        if got is None:
            return None
        book, tested = got
        book = book if isinstance(book, dict) else {}
        return {"pistes": bibliotheque.pistes(book, verdicts=tested), "kept": len(book.get("papers") or []),
                "fetched_at": book.get("fetched_at"), "error": book.get("error") or "",
                "tested_on": (tested or {}).get("day") if isinstance(tested, dict) else None}

    def modeles(self) -> Dict[str, Any]:
        """Modèles d'IA (lecture seule) : santé mesurée des modèles configurés,
        jamais une clé ni une mesure inventée."""
        rows = modeles.status(path=modeles.ledger_path(getattr(self.g, "watch_db", "")))
        return {"text": modeles.describe(rows), "mode": modeles.mode(rows),
                "rows": [dict({k: r[k] for k in ("label", "model", "kind", "calls", "failures", "p50_ms", "breaker",
                                                  "lifecycle", "lifecycle_reason", "health_score")},
                              accuracy=(r["bench"] or {}).get("accuracy"))
                         for r in rows if r["configured"]]}

    def evenements(self, state: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Calendrier économique (lecture seule) : annonces américaines
        importantes de la veille à dans 7 jours, réaction du bitcoin mesurée à
        la dernière décision ; None sans noyau de savoir."""
        path = getattr(self.g, "savoir_db", "") or savoir.DEFAULT_DB
        if not getattr(self.g, "savoir", False) or not os.path.exists(path):
            return None
        try:
            memory = savoir.Memory(path, readonly=True)
        except sqlite3.Error:
            return None
        try:
            book = memory.get(evenements.KEY)
        except (sqlite3.Error, ValueError):
            return None
        finally:
            memory.close()
        state = self.state() if state is None else state
        now = datetime.now(timezone.utc)
        book = book if isinstance(book, dict) else {}
        return {"events": evenements.window(book, now - timedelta(days=1), now + timedelta(days=7)),
                "reaction": (state.get("evenements") or {}).get("reaction"),
                "fetched_at": book.get("fetched_at"), "error": book.get("error") or ""}

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
