"""Ligne de commande : python trendguard_bot.py <commande>.

Partie du bot TrendGuard (paquet trendguard, point d'entrée : trendguard_bot.py).
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import ccxt

import v29

from . import alerts, autonomy, evolution
from . import bot as _bot
from . import diagnostics as dg
from . import trend_strategy as ts
from .bot import DecisionDeferred, TrendGuardBot, last_closed_day
from .config import (
    ENV_FILE,
    TG_ENV_DOC,
    GuardConfig,
    build_guard_logger,
    load_guard_config_from_env,
    set_env_var,
)
from .replay import replay


def _build(gcfg: GuardConfig) -> TrendGuardBot:
    logger = build_guard_logger(gcfg.log_file)
    live = gcfg.run_mode == "live"
    # Le testnet ne sert qu'au mode réel : en paper, les prix du testnet
    # (marché artificiel) fausseraient les décisions. En paper, aucune
    # requête privée : les clés ne sont pas transmises, une clé supprimée
    # sur Binance ne peut pas empêcher le bot de démarrer.
    exchange = v29.make_binance(
        os.environ.get("BINANCE_API_KEY", "").strip() if live else "",
        os.environ.get("BINANCE_API_SECRET", "").strip() if live else "",
        gcfg.binance_testnet and live)
    store = v29.Store(gcfg.db_file, logger)
    # Telegram, e-mail et WhatsApp (alerts.py, réglages dans .env).
    notifier = alerts.build_notifier(logger)
    return TrendGuardBot(gcfg, logger, exchange, store, notifier)


def clean_api_secret(raw: str) -> str:
    """Nettoie un secret collé (espaces, guillemets) et vérifie son format :
    Binance délivre des secrets HMAC de 64 caractères alphanumériques."""
    s = raw.strip().strip("\"'").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{64}", s):
        raise ValueError(f"format inattendu ({len(s)} caractères ; attendu : "
                         f"64 lettres et chiffres)")
    return s


def cmd_set_secret(env_path: str = ENV_FILE) -> int:
    """Saisie MASQUÉE du secret API (rien ne s'affiche à l'écran ni dans
    l'historique du terminal), puis écriture dans .env."""
    import getpass
    print("Collez votre clé SECRÈTE Binance puis appuyez sur Entrée.")
    print("(Rien ne s'affiche pendant la saisie : c'est normal.)")
    try:
        secret = clean_api_secret(getpass.getpass("Secret : "))
    except ValueError as e:
        print(f"❌ Secret refusé : {e}. Rien n'a été modifié.")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nAnnulé. Rien n'a été modifié.")
        return 1
    set_env_var(env_path, "BINANCE_API_SECRET", secret)
    print(f"✅ Secret enregistré dans {env_path} (fichier privé, jamais commité).")
    return 0


def panel_password_problem(pw: str) -> Optional[str]:
    if len(pw) < 10:
        return "10 caractères minimum"
    if pw != pw.strip():
        return "pas d'espace au début ni à la fin"
    if any(c in pw for c in "'\r\n"):
        return "l'apostrophe (') n'est pas acceptée"
    if len(set(pw)) < 5 or pw.lower() in ("motdepasse", "password12", "1234567890", "azertyuiop"):
        return "mot de passe trop simple"
    return None


def cmd_set_panel_password(env_path: str = ENV_FILE,
                           ask: Optional[Callable[[str], str]] = None) -> int:
    """Accès au panneau depuis un téléphone : mot de passe saisi MASQUÉ, deux
    fois, écrit dans .env ; accès Wi-Fi (PANEL_HOST=0.0.0.0) sur demande."""
    import getpass
    ask = ask or input
    print("Mot de passe du panneau (accès depuis un téléphone), 10 caractères minimum.")
    print("(Rien ne s'affiche pendant la saisie : c'est normal.)")
    try:
        pw = getpass.getpass("Mot de passe : ")
        if getpass.getpass("Confirmez : ") != pw:
            print("❌ Les deux saisies diffèrent. Rien n'a été modifié.")
            return 1
        problem = panel_password_problem(pw)
        if problem:
            print(f"❌ Mot de passe refusé : {problem}. Rien n'a été modifié.")
            return 1
        answer = ask("Autoriser l'accès depuis un téléphone sur le même Wi-Fi ? (o/N) ")
        lan = answer.strip().lower() in ("o", "oui", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        print("\nAnnulé. Rien n'a été modifié.")
        return 1
    set_env_var(env_path, "PANEL_PASSWORD", f"'{pw}'")     # guillemets simples : texte exact
    if lan:
        set_env_var(env_path, "PANEL_HOST", "0.0.0.0")
    print(f"✅ Mot de passe enregistré dans {env_path} (fichier privé, jamais commité).")
    if lan:
        print("✅ Accès Wi-Fi activé au prochain démarrage du panneau (redémarrage de "
              "l'ordinateur). Tout de suite : python trendguard_bot.py panel --host 0.0.0.0 "
              "--port 8766 (tâche VS Code « Panneau — accès téléphone »).")
    print("Changer le mot de passe puis redémarrer le panneau déconnecte tous les appareils.")
    return 0


def _auth_hint(msg: str) -> str:
    """Traduit un refus d'authentification Binance."""
    ip = re.search(r"request ip:\s*([0-9A-Fa-f.:]+)", msg)
    if "-2015" in msg:
        return ("clé inconnue sur ce compte, adresse IP non autorisée"
                + (f" (votre adresse IP vue par Binance : {ip.group(1)})" if ip else "")
                + " ou lecture du compte non autorisée")
    if "-1022" in msg:
        return "API Key reconnue mais Secret Key incorrecte"
    if "-2014" in msg:
        return "format d'API Key refusé"
    return msg[:160]


def check_api_keys(key: str, secret: str, testnet: bool,
                   factory: Callable[..., Any] = v29.make_binance
                   ) -> Tuple[bool, str, bool]:
    """Lecture du compte (aucun ordre). Retourne (acceptées, raison,
    refus d'authentification) ; une panne réseau n'est pas un refus."""
    ex = factory(key, secret, testnet)
    try:
        v29.sync_exchange_clock(ex, samples=3)
        ex.fetch_balance()
        return True, "", False
    except ccxt.AuthenticationError as e:
        return False, _auth_hint(str(e)), True
    except ccxt.NetworkError as e:
        return False, f"Binance injoignable ({type(e).__name__})", False
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:160]}", False


def cmd_set_keys(env_path: str = ENV_FILE, ask: Optional[Callable[[str], str]] = None,
                 read: Optional[Callable[[str], str]] = None,
                 factory: Callable[..., Any] = v29.make_binance, out=None) -> int:
    """Enregistre API Key + Secret Key (saisie MASQUÉE) après les avoir fait
    accepter par Binance (lecture du compte, aucun ordre). Corrige seul les
    deux erreurs courantes : clés inversées, clés du testnet déclarées
    réelles (ou l'inverse). Rien n'est écrit si Binance refuse."""
    import getpass
    ask = ask or getpass.getpass
    read = read or input
    out = out or sys.stdout
    say = lambda msg="": print(msg, file=out)       # noqa: E731
    say("Enregistrement des clés API Binance.")
    try:
        # Question VISIBLE : 1 ou 2 seulement. Une clé collée ici par erreur
        # est refusée (et signalée : elle s'affiche à l'écran).
        for _ in range(3):
            choice = read("Compte : 1 = testnet (clés de testnet.binance.vision), "
                          "2 = compte réel (clés de binance.com) [1] : ").strip() or "1"
            if choice in ("1", "2"):
                break
            say("⚠️  Répondez seulement 1 ou 2." + (
                " Ne collez pas la clé ici : cette question s'affiche à l'écran ; "
                "la clé se colle juste après, en saisie masquée." if len(choice) > 8 else ""))
        else:
            say("Annulé. Rien n'a été modifié.")
            return 1
        testnet = choice != "2"
        say("Collez maintenant chaque clé (clic droit ou Ctrl+V), puis Entrée. "
            "Rien ne s'affiche pendant la saisie : c'est normal.")
        fields = []
        for label in ("API Key", "Secret Key"):
            try:
                fields.append(clean_api_secret(ask(f"{label:<10} : ")))
            except ValueError as e:
                say(f"❌ {label} refusée : {e}. Rien n'a été modifié.")
                if label == "Secret Key":
                    say("   La Secret Key n'est montrée qu'une fois par Binance, à la "
                        "création de la clé : si vous ne l'avez plus, créez une "
                        "nouvelle clé API.")
                return 1
        key, secret = fields
    except (EOFError, KeyboardInterrupt):
        say("\nAnnulé. Rien n'a été modifié.")
        return 1
    if key == secret:
        say("❌ API Key et Secret Key identiques : ce sont deux valeurs "
            "différentes sur Binance. Rien n'a été modifié.")
        return 1
    where = lambda t: "testnet" if t else "compte réel"   # noqa: E731
    say(f"Vérification auprès de Binance ({where(testnet)}), sans aucun ordre…")
    attempts = [(key, secret, testnet, ""),
                (secret, key, testnet, "API Key et Secret Key étaient inversées"),
                (key, secret, not testnet,
                 f"ces clés sont celles du {where(not testnet)}"),
                (secret, key, not testnet,
                 f"clés inversées et appartenant au {where(not testnet)}")]
    refusals: List[Tuple[str, bool]] = []
    for k, sec, tn, fix in attempts:
        ok, reason, auth = check_api_keys(k, sec, tn, factory)
        if ok:
            set_env_var(env_path, "BINANCE_API_KEY", k)
            set_env_var(env_path, "BINANCE_API_SECRET", sec)
            set_env_var(env_path, "BINANCE_TESTNET", "true" if tn else "false")
            if fix:
                say(f"ℹ️  Corrigé automatiquement : {fix}.")
            say(f"✅ Clés acceptées par Binance ({where(tn)}) et enregistrées dans "
                f"{env_path} (fichier privé, jamais commité).")
            say("Étape suivante : python trendguard_bot.py verify "
                "(aucun ordre n'est passé).")
            return 0
        if not auth:
            # Panne réseau : les autres essais n'ont pas eu lieu, un refus
            # ne peut pas être conclu.
            say(f"❌ Vérification impossible : {reason}. Rien n'a été modifié ; "
                f"relancer une fois la connexion rétablie.")
            return 1
        refusals.append((reason, tn))
    # API Key reconnue (seul le secret est faux) : c'est le diagnostic le
    # plus précis. Sinon, le premier refus (compte choisi, adresse IP).
    reason, tn = next(((r, t) for r, t in refusals if "Secret Key incorrecte" in r),
                      refusals[0])
    say(f"❌ Binance refuse ces clés ({where(tn)}) : {reason}. Rien n'a été modifié.")
    if "adresse IP" in reason:
        # Binance n'indique pas toujours l'adresse vue (« request ip »).
        ip = ("l'adresse IP ci-dessus" if "vue par Binance" in reason
              else "l'adresse IP publique de ce PC")
        say("   Sur Binance ▸ Gestion des API : vérifier que la clé est active, que "
            f"« Activer la lecture » est coché, et autoriser {ip} "
            "(ou retirer la restriction IP le temps du test).")
    return 1


MIN_LIVE_CAPITAL = 100.0     # USDT : en dessous, la plupart des ordres < minimum


_ORDER_METHODS = ("create_order", "cancel_order", "privatePostOrderListOco",
                  "private_post_orderlist_oco", "privateDeleteOrderList",
                  "private_delete_orderlist")


def _forbid_orders(exchange: Any) -> None:
    """Garde-fou de `verify` : toute tentative d'ordre lève une erreur."""
    def forbidden(name):
        def _raise(*a, **k):
            raise RuntimeError(f"ordre interdit pendant la vérification ({name})")
        return _raise
    for name in _ORDER_METHODS:
        setattr(exchange, name, forbidden(name))


def _verify_rights(exchange: Any, say: Callable[..., None]
                   ) -> Tuple[Optional[Dict[str, Any]], Optional[bool]]:
    """Droits de la clé. Retourne (droits lus ou None, conforme) ; conforme
    vaut None si Binance refuse la clé (vérification arrêtée)."""
    say("\n── 1. Droits de la clé API")
    try:
        r = exchange.sapi_get_account_apirestrictions()
    except ccxt.AuthenticationError as e:
        say(f"  ❌ Clé refusée par Binance : {_auth_hint(str(e))}")
        say("     → python trendguard_bot.py set-keys vérifie les clés auprès de "
            "Binance et corrige les clés inversées ou du mauvais compte.")
        return None, None
    except Exception as e:
        say(f"  (lecture des droits impossible : {type(e).__name__})")
        return None, True
    withdraw = bool(r.get("enableWithdrawals"))
    trading = bool(r.get("enableSpotAndMarginTrading"))
    say(f"  Retrait autorisé      : {'OUI ❌ à désactiver sur Binance' if withdraw else 'non ✓'}")
    say(f"  Trading Spot autorisé : {'oui ✓' if trading else 'NON ❌ à activer sur Binance'}")
    say(f"  Restriction IP        : {'oui ✓' if r.get('ipRestrict') else 'non (conseillé)'}")
    return r, not withdraw and trading


def _verify_balances(exchange: Any, say: Callable[..., None]) -> None:
    say("\n── 2. Soldes")
    bal = exchange.fetch_balance()
    total = {a: float(q or 0) for a, q in (bal.get("total") or {}).items()
             if float(q or 0) > 0}
    value = 0.0
    for asset, qty in sorted(total.items()):
        px = 1.0 if asset in ("USDT", "USDC", "FDUSD") else 0.0
        if not px:
            try:
                px = float(exchange.fetch_ticker(f"{asset}/USDT")["last"] or 0)
            except Exception:
                px = 0.0
        value += qty * px
        say(f"  {asset:<8} {qty:>18.8f}  ≈ {qty * px:>12,.2f} USDT")
    say(f"  Valeur totale estimée : {value:,.2f} USDT")


def _quiet_logger(out: Any) -> logging.Logger:
    """Journal de la vérification : seules les causes d'échec s'affichent."""
    quiet = logging.getLogger("trendguard.verify")
    reasons = logging.StreamHandler(out)
    reasons.setLevel(logging.WARNING)
    reasons.setFormatter(logging.Formatter("  ⚠️  %(message)s"))
    quiet.handlers[:] = [reasons]
    quiet.setLevel(logging.WARNING)
    quiet.propagate = False
    return quiet


def _verify_decision(gcfg: GuardConfig, exchange: Any, rights: Optional[Dict[str, Any]],
                     now: Optional[datetime], out: Any, say: Callable[..., None]
                     ) -> Optional[Dict[str, Any]]:
    """Démarrage du bot en réel sur la clé (order/test, aucun ordre) et
    décision du jour simulée. None si le démarrage échoue."""
    say("\n── 3. Validation des ordres + 4. décision du jour (simulation)")
    live = dataclasses.replace(gcfg, run_mode="live", enable_live_trading=True,
                               live_confirmation="I_UNDERSTAND_RISK",
                               db_file=":memory:", log_file=os.devnull,
                               lock_file=os.devnull)
    quiet = _quiet_logger(out)
    store = v29.Store(":memory:", quiet)
    bot = TrendGuardBot(live, quiet, exchange, store,
                        v29.Notifier("", "", logger=quiet))
    try:
        if not bot.boot():
            say("  ❌ Démarrage impossible (causes ci-dessus).")
            if rights is not None and not rights.get("enableSpotAndMarginTrading"):
                say("     → la clé n'a pas le droit de trader : Binance ▸ Gestion "
                    "des API ▸ Modifier ▸ cocher « Activer le trading Spot et sur "
                    "marge ».")
            return None
        say("  Validation des types d'ordres (order/test) : OK ✓")
        now = now or v29._utcnow()
        day = last_closed_day(now, live.decision_delay_sec)
        try:
            snap, bull, prices = bot._market_snapshot(now, day)
        except DecisionDeferred as e:
            say(f"  ❌ Décision du jour impossible : {e}")
            return None
        equity, cash = bot._equity_and_cash(prices)
        eligible = {a: x for a, x in snap.items() if bot._can_enter(a)}
        return {"day": day, "bull": bull, "equity": equity, "cash": cash,
                "orphans": [b for b, sl in bot.slots.items() if sl.ctx.orphan_balance],
                "plans": ts.plan_entries(bot._holdings(), eligible, bull, equity, cash,
                                         live.params)}
    finally:
        store.close()


def _report_decision(d: Dict[str, Any], say: Callable[..., None]) -> bool:
    """Affiche la décision simulée ; False si le capital est insuffisant."""
    say(f"  Bougie du {d['day']} | régime BTC : "
        f"{'HAUSSIER' if d['bull'] else 'BAISSIER (aucun achat)'}")
    say(f"  Capital géré : {d['equity']:,.2f} USDT | USDT disponible : {d['cash']:,.2f}")
    ok = d["equity"] >= MIN_LIVE_CAPITAL
    if not ok:
        say(f"  ❌ Capital insuffisant : Binance impose ~5 USDT minimum par ordre ; "
            f"avec 1 % de risque par trade, il faut au moins "
            f"{MIN_LIVE_CAPITAL:.0f} USDT pour que les positions dépassent ce "
            f"minimum.")
    if d["orphans"]:
        say(f"  ⚠️  Cryptos détenues hors bot (achats bloqués sur ces paires) : "
            f"{', '.join(d['orphans'])}")
    spent = 0.0
    for p in d["plans"]:
        spent += p["cost"]
        say(f"  ↗ achat prévu {p['asset'].upper():<5} {p['qty']:.6g} ≈ "
            f"{p['cost']:,.2f} USDT | stop {p['stop']:.6g} | "
            f"risque {p['risk_quote']:.2f} USDT")
    if d["plans"]:
        say(f"  Total : {spent:,.2f} USDT ({spent / max(d['equity'], 1e-9) * 100:.0f} % "
            f"du capital géré)")
    else:
        say("  Aucun achat prévu aujourd'hui.")
    return ok


def cmd_verify(gcfg: GuardConfig, exchange: Any = None,
               now: Optional[datetime] = None, out=None) -> int:
    """Vérifications SANS AUCUN ORDRE avant le passage en réel : droits de
    la clé, soldes, validation des types d'ordres (order/test) et
    simulation de la décision du jour. Retourne 0 si tout est prêt."""
    out = out or sys.stdout
    say = lambda msg="": print(msg, file=out)       # noqa: E731
    if exchange is None:
        key = os.environ.get("BINANCE_API_KEY", "").strip()
        secret = os.environ.get("BINANCE_API_SECRET", "").strip()
        if not key or not secret:
            say("ℹ️  Pas de clé API dans .env : vérification publique seulement "
                "(droits, soldes et order/test demandent une clé ; pour les "
                "enregistrer : python trendguard_bot.py set-keys).\n")
            return cmd_verify_public(gcfg, now=now, out=out)
        exchange = v29.make_binance(key, secret, gcfg.binance_testnet)
    _forbid_orders(exchange)
    say(f"Vérification {'TESTNET' if gcfg.binance_testnet else 'BINANCE RÉEL'} "
        f"— aucun ordre ne sera passé")
    rights, ok = _verify_rights(exchange, say)
    if ok is None:
        return 1
    _verify_balances(exchange, say)
    decision = _verify_decision(gcfg, exchange, rights, now, out, say)
    if decision is None:
        return 1
    ok = _report_decision(decision, say) and ok
    say("\n" + ("✅ Prêt pour le mode réel." if ok else
                "❌ À corriger avant le mode réel (voir ci-dessus)."))
    return 0 if ok else 1


def cmd_verify_public(gcfg: GuardConfig, exchange: Any = None,
                      now: Optional[datetime] = None, out=None) -> int:
    """Vérification sur le VRAI Binance SANS clé API ni ordre : connexion,
    horloge, règles de marché des paires, puis construction complète des
    ordres que le bot passerait aujourd'hui (quantités arrondies aux pas
    Binance, montants minimums, stop catastrophe, requêtes ccxt préparées
    mais jamais envoyées). Retourne 0 si tout est conforme."""
    out = out or sys.stdout
    say = lambda msg="": print(msg, file=out)       # noqa: E731
    ok = True
    exchange = exchange or v29.make_binance(testnet=gcfg.binance_testnet)
    _forbid_orders(exchange)
    say(f"Vérification {'TESTNET' if gcfg.binance_testnet else 'BINANCE RÉEL'} "
        f"— mode public, aucun ordre")

    say("\n── 1. Connexion")
    if callable(getattr(exchange, "fetch_time", None)):
        clock = v29.sync_exchange_clock(exchange)
        if clock is None:
            say("  ❌ Binance injoignable (heure du serveur illisible)")
            return 1
        say(f"  Binance joignable ✓ (latence {clock.latency_ms:.0f} ms)")
        say(f"  Heure : {v29.describe_clock(clock.offset_ms, clock.uncertainty_ms)}"
            f" → le bot utilise l'heure de Binance")
    capital = gcfg.max_capital or gcfg.paper_capital
    sim = dataclasses.replace(gcfg, run_mode="paper", paper_capital=capital,
                              db_file=":memory:", log_file=os.devnull,
                              lock_file=os.devnull, auto_diagnose_days=0,
                              heartbeat_min=0)
    quiet = logging.getLogger("trendguard.verify")
    reasons = logging.StreamHandler(out)
    reasons.setLevel(logging.WARNING)
    reasons.setFormatter(logging.Formatter("  ⚠️  %(message)s"))
    quiet.handlers[:] = [reasons]
    quiet.setLevel(logging.WARNING)
    quiet.propagate = False
    store = v29.Store(":memory:", quiet)
    bot = TrendGuardBot(sim, quiet, exchange, store,
                        v29.Notifier("", "", logger=quiet))
    t0 = time.time()
    try:
        if not bot.boot():
            say("  ❌ Chargement des marchés impossible (causes ci-dessus).")
            return 1
        say(f"  Marchés chargés en {time.time() - t0:.1f} s ✓")

        say("\n── 2. Règles Binance des paires")
        missing = [b for b in gcfg.universe if b not in bot.slots]
        if missing:
            ok = False
            say(f"  ❌ Paires absentes ou suspendues : {', '.join(missing)}")
        for base, sl in sorted(bot.slots.items()):
            r = sl.ex.rules
            try:
                stop = sl.ex.stop_order_type
            except Exception as e:
                ok = False
                say(f"  ❌ {base:<5} aucun ordre stop disponible ({e})")
                continue
            say(f"  ✓ {base:<5} stop {stop:<15} minimum {r.min_cost:g} USDT, "
                f"pas de quantité {r.step_size:g}, pas de prix {r.tick_size:g}")

        now = now or v29._utcnow()
        day = last_closed_day(now, sim.decision_delay_sec)
        try:
            snap, bull, _prices = bot._market_snapshot(now, day)
        except DecisionDeferred as e:
            say(f"\n  ❌ Décision du jour impossible : {e}")
            return 1
        plans = ts.plan_entries({}, snap, bull, capital, capital, sim.params)
        say(f"\n── 3. Ordres que le bot passerait aujourd'hui (capital simulé "
            f"{capital:,.2f} USDT, bougie du {day}, régime BTC "
            f"{'HAUSSIER' if bull else 'BAISSIER'})")
        if capital < MIN_LIVE_CAPITAL:
            ok = False
            say(f"  ❌ Capital simulé < {MIN_LIVE_CAPITAL:.0f} USDT : la plupart des "
                f"ordres seraient sous le minimum de Binance.")
        build = getattr(exchange, "create_order_request", None)
        cash = capital
        for plan in plans:
            a = plan["asset"].upper()
            sl = bot.slots[a]
            t = sl.ex.get_ticker()
            adj = ts.reprice_entry(plan, float(t["ask"]), capital, cash, sim.params)
            if adj is None:
                say(f"  ↷ {a:<5} achat annulé : prix actuel trop proche du stop")
                continue
            errors: List[str] = []
            qty = sl.ex.round_amount(adj["qty"])
            notional = qty * float(t["ask"])
            if qty <= 0 or notional < sl.ex.min_notional() * 1.05:
                errors.append(f"achat de {notional:.2f} USDT sous le minimum "
                              f"({sl.ex.min_notional():g} USDT)")
            net = sl.ex.round_amount(qty * (1 - sim.params.fee))  # frais en base
            disaster = adj["stop"] - gcfg.catastrophe_atr * adj["vol"]
            if disaster <= 0:
                disaster = adj["stop"] * 0.5
            stop_px = sl.ex.round_price(disaster, "down")
            if not 0 < stop_px < float(t["last"]):
                errors.append(f"stop {stop_px:g} au-dessus du prix actuel")
            try:
                stop_type = sl.ex.stop_order_type
                if callable(build):
                    build(sl.symbol, "market", "buy", qty, None,
                          {"newClientOrderId": "TGVERIFY"})
                    params: Dict[str, Any] = {"stopPrice": stop_px}
                    price = None
                    if stop_type == "STOP_LOSS_LIMIT":
                        price = sl.ex.round_price(
                            stop_px * (1 - sl.cfg.stop_limit_offset_pct), "down")
                        params["timeInForce"] = "GTC"
                    build(sl.symbol, stop_type, "sell", net, price, params)
            except Exception as e:
                errors.append(f"requête refusée : {type(e).__name__}: {str(e)[:100]}")
            if errors:
                ok = False
                say(f"  ❌ {a:<5} " + " ; ".join(errors))
            else:
                say(f"  ✓ {a:<5} achat {qty:g} ≈ {notional:,.2f} USDT, puis stop "
                    f"{stop_type} de {net:g} à {stop_px:g} (stop de clôture "
                    f"{adj['stop']:.6g}, risque {adj['risk_quote']:.2f} USDT)")
            cash -= adj["cost"]
        if not plans:
            say("  Aucun achat prévu aujourd'hui.")
    finally:
        store.close()
    say("\n── 4. Reste à vérifier avec une clé API (python trendguard_bot.py verify)")
    say("  Droits de la clé (retrait interdit), soldes réels et validation des "
        "ordres signés par Binance (order/test).")
    say("\n" + ("✅ Tout est conforme côté marché." if ok else
                "❌ Points à corriger (voir ci-dessus)."))
    return 0 if ok else 1


def health_check(gcfg: GuardConfig, max_age_sec: int) -> int:
    """0 si le dernier cycle réussi date de moins de `max_age_sec` et que le
    kill-switch n'est pas déclenché ; 1 sinon (contrôle de santé Docker)."""
    try:
        store = v29.Store(gcfg.db_file, logging.getLogger("trendguard.health"))
        try:
            state = store.get_kv(TrendGuardBot.STATE_KEY) or {}
        finally:
            store.close()
    except Exception as e:
        print(f"KO : base illisible ({e})")
        return 1
    last = state.get("last_cycle_ts")
    if not last:
        print("KO : aucun cycle réussi")
        return 1
    age = time.time() - float(last)
    if age > max_age_sec:
        print(f"KO : dernier cycle il y a {age:.0f} s (> {max_age_sec} s)")
        return 1
    if state.get("halted"):
        print(f"KO : kill-switch — {state.get('halt_reason')}")
        return 1
    print(f"OK : dernier cycle il y a {age:.0f} s, décision du "
          f"{state.get('last_decision_day')}")
    return 0


def cmd_diagnose(gcfg: GuardConfig, out_path: Optional[str] = None,
                 exchange: Any = None, now: Optional[datetime] = None) -> int:
    """Diagnostic complet en lecture seule (aucun ordre, bot arrêté ou non)."""
    print(f"Analyse en cours ({len(gcfg.universe)} paires, historique Binance "
          f"depuis 2018)…", flush=True)
    findings, day = diagnose_findings(gcfg, exchange, now)
    text = dg.render(findings, f"({gcfg.run_mode.upper()}, {day})")
    print(text)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"\nRapport enregistré : {out_path}")
    return 1 if dg.verdict(findings) == "ALERTE" else 0


def diagnose_findings(gcfg: GuardConfig, exchange: Any = None, now: Optional[datetime] = None
                      ) -> Tuple[List[dg.Finding], str]:
    """Constats du diagnostic complet (lecture seule) et jour de la bougie
    analysée ; utilisé aussi par le rapport quotidien (report.py)."""
    if exchange is None:
        # Historique public (data-api.binance.vision) : sans la liste des
        # marchés (4,7 Mo) et accessible depuis n'importe quel serveur.
        exchange = v29.PublicKlines()
    _forbid_orders(exchange)
    if now is None:
        v29.sync_exchange_clock(exchange, samples=3)     # heure de Binance
        now = v29._utcnow()
    quiet = logging.getLogger("trendguard.diagnose")
    quiet.handlers[:] = [logging.NullHandler()]
    quiet.propagate = False
    state: Dict[str, Any] = {}
    holdings: List[Dict[str, Any]] = []
    if gcfg.db_file != ":memory:" and os.path.exists(gcfg.db_file):
        store = v29.Store(gcfg.db_file, quiet)
        try:
            state = store.get_kv(TrendGuardBot.STATE_KEY) or {}
            if gcfg.run_mode == "live":
                for base in gcfg.universe:
                    ctx = store.load_context(key=f"ctx:{base}/{gcfg.quote}")
                    if ctx and ctx.position.in_position:
                        p = ctx.position
                        holdings.append({"asset": base.lower(), "qty": p.amount_held,
                                         "entry": p.buy_price,
                                         "stop": p.soft_stop or p.sl_price,
                                         "risk_quote": p.risk_quote_initial})
        finally:
            store.close()
    if gcfg.run_mode == "paper" and "paper" in state:
        holdings = [{"asset": a, "qty": h["qty"], "entry": h["entry"],
                     "stop": h["stop"], "risk_quote": h["risk_quote"]}
                    for a, h in state["paper"]["holdings"].items()]
    running: Optional[bool] = None
    probe = v29.ProcessLock(gcfg.lock_file)
    try:
        probe.acquire()
        probe.release()
        running = False
    except SystemExit as e:
        running = "déjà" in str(e)
    equity = float(state.get("last_equity") or (
        gcfg.paper_capital if gcfg.run_mode == "paper" else 0.0))
    day = last_closed_day(now, gcfg.decision_delay_sec)
    findings = dg.run_diagnosis(exchange, evolution.params_for(gcfg), list(gcfg.universe), state,
                                holdings, equity, day, now, db_file=gcfg.db_file,
                                running=running, quote=gcfg.quote,
                                kill_drawdown=gcfg.kill_drawdown)
    return findings, day


# Outils du bot, lancés par la même commande (leurs propres options suivent) :
#   python trendguard_bot.py alerts configurer
TOOLS = {"alerts": ("alerts", "alertes : configurer | tester"),
         "watch": ("market_watch", "veille : [report] | check | set-key <ia> [--no-ai]"),
         "strategy": ("trend_strategy", "stratégie : download | research | backtest"),
         "lab": ("strategy_lab", "laboratoire des stratégies (--cache data_binance)"),
         "animation": ("replay_animation", "page d'animation du rejeu"),
         "evolution": ("evolution", "évolution encadrée : [statut] | examen | quotidien | revenir | regles"),
         "rapport": ("report", "rapport quotidien, sécurité et diagnostic : [dernier] | maintenant | quotidien")}


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="TrendGuard Bot (Binance Spot)",
        epilog="Outils : " + " ; ".join(f"{k} ({v[1]})" for k, v in TOOLS.items()))
    ap.add_argument("cmd", choices=["run", "once", "status", "resume", "docs",
                                    "health", "replay", "set-secret",
                                    "set-keys", "verify", "diagnose", "panel",
                                    "supervise", "stop", "autostart",
                                    "set-panel-password"])
    ap.add_argument("action", nargs="?", default="status", choices=["on", "off", "status"],
                    help="(autostart) on = activer, off = désactiver, status = état")
    ap.add_argument("--out", default=None, help="(diagnose) fichier du rapport")
    ap.add_argument("--data", default="data", help="(replay) dossier Coin Metrics")
    ap.add_argument("--start", default="2025-06-01", help="(replay) début")
    ap.add_argument("--end", default=None, help="(replay) fin")
    ap.add_argument("--capital", type=float, default=10_000.0,
                    help="(replay) capital initial USDT")
    ap.add_argument("--host", default=v29._env_s("PANEL_HOST", "127.0.0.1"),
                    help="(panel) 127.0.0.1 = ce PC ; 0.0.0.0 = réseau local")
    ap.add_argument("--port", type=int, default=v29._env_i("PANEL_PORT", 8765),
                    help="(panel) port web")
    ap.add_argument("--demo", action="store_true", help="(panel) données fictives")
    ap.add_argument("--no-open", action="store_true", help="(panel) sans ouvrir le navigateur")
    ap.add_argument("--login", action="store_true",
                    help="(supervise, panel) lancé à l'ouverture de session")
    return ap


def _run_tool(name: str, argv: List[str]) -> int:
    """Outil (alerts, watch, strategy, lab, animation) avec ses options."""
    tool = importlib.import_module(f"{__package__}.{TOOLS[name][0]}")
    prog = sys.argv[0]
    sys.argv[0] = f"{os.path.basename(prog)} {name}"     # aide : « trendguard_bot.py watch »
    try:
        return tool.main(argv)
    finally:
        sys.argv[0] = prog


def _print_replay(args: argparse.Namespace) -> int:
    res = replay(args.data, args.start, args.end, args.capital)
    m = res["metrics"]
    print("\n" + "═" * 64)
    print(f"REJEU PAPER {args.start} → {res['last_day']} (prix réels)")
    print("═" * 64)
    print(f"Capital : {args.capital:,.2f} → {res['equity'].iloc[-1]:,.2f} USDT "
          f"({m['total_return_pct']:+.1f} %)")
    print(f"Max drawdown : {m['max_dd_pct']:.1f} %  |  Sharpe : {m['sharpe']:.2f}")
    print(f"Trades clos : {m['trades']}  |  gagnants : {m['win_rate_pct']:.0f} %"
          f"  |  gain moy. {m['avg_win_r']:+.2f} R  |  perte moy. "
          f"{m['avg_loss_r']:+.2f} R")
    print(f"Régime BTC au dernier jour : "
          f"{'HAUSSIER' if res['regime_bull'] else 'BAISSIER (aucune entrée)'}")
    if not res["holdings"]:
        print("Positions ouvertes : aucune (100 % USDT)")
        return 0
    print("Positions ouvertes :")
    for a, h in res["holdings"].items():
        px = float(res["prices"][a])
        print(f"  {a.upper():<5} entrée {h['entry']:.6g} → {px:.6g} "
              f"({(px / h['entry'] - 1) * 100:+.1f} %)  stop {h['stop']:.6g}")
    return 0


def _print_docs() -> int:
    for k, v in sorted(TG_ENV_DOC.items()):
        print(f"  {k:<28} {v}")
    return 0


def _status(gcfg: GuardConfig, resume: bool) -> int:
    """État du portefeuille ; `resume` lève aussi le kill-switch."""
    locks: List[v29.ProcessLock] = []
    if resume:
        # resume modifie l'état : interdit pendant que le bot tourne (il
        # réécrirait son propre état au cycle suivant).
        try:
            locks = v29.acquire_instance_locks(gcfg.lock_file, gcfg.db_file)
        except SystemExit as e:
            print(f"❌ {e}\nArrêtez d'abord le bot, puis relancez resume "
                  f"(Docker : docker compose stop && docker compose run --rm "
                  f"trendguard resume && docker compose start).")
            return 1
    store = v29.Store(gcfg.db_file, logging.getLogger("trendguard.cli"))
    state = store.get_kv(TrendGuardBot.STATE_KEY) or {}
    if resume:
        state["halted"] = False
        state["halt_reason"] = None
        state["peak_equity"] = state.get("last_equity")
        store.set_kv(TrendGuardBot.STATE_KEY, state)
        print("✅ Kill-switch levé (pic d'equity réinitialisé).")
    trades = state.get("trades", [])
    wins = [t for t in trades if t["pnl"] > 0]
    print(f"Dernière décision : {state.get('last_decision_day')}")
    print(f"Equity            : {state.get('last_equity')}")
    print(f"Pic               : {state.get('peak_equity')}")
    print(f"Halt              : {state.get('halted')} {state.get('halt_reason') or ''}")
    print(f"Trades clos       : {len(trades)} (gagnants {len(wins)})")
    if trades:
        print(f"R moyen           : {sum(t['r'] for t in trades)/len(trades):+.2f}")
    if "paper" in state:
        print(f"Paper cash        : {state['paper']['cash']:.2f} | positions : "
              f"{', '.join(a.upper() for a in state['paper']['holdings']) or '-'}")
    store.close()
    v29.release_locks(locks)
    return 0


def _run(gcfg: GuardConfig, once: bool) -> int:
    """Le bot : un seul cycle (`once`) ou la boucle continue."""
    locks = v29.acquire_instance_locks(gcfg.lock_file, gcfg.db_file)
    bot = _build(gcfg)
    awake: Optional[autonomy.KeepAwake] = None
    try:
        bot.logger.info(f"TrendGuard — {gcfg.run_mode.upper()}"
                        f"{' TESTNET' if gcfg.binance_testnet and gcfg.run_mode == 'live' else ''} — "
                        f"{len(gcfg.universe)} actifs, risque "
                        f"{gcfg.params.risk_pct*100:.2f} %/trade")
        signal.signal(signal.SIGINT, _bot._stop)
        signal.signal(signal.SIGTERM, _bot._stop)
        if once:
            if not bot.boot():
                print("❌ Démarrage impossible (réseau / exchange) : voir le log.",
                      file=sys.stderr)
                return 1
            try:
                bot.run_cycle()
            except Exception as e:
                bot.logger.error(f"[ONCE] cycle KO: {e}")
                return 1
            return 0
        if gcfg.keep_awake:
            awake = autonomy.KeepAwake(bot.logger)
            awake.start()
        bot._touch_alive(force=True)
        delay = 30
        while _bot._running and not bot.stop_requested() and not bot.boot():
            bot.logger.warning(f"[BOOT] nouvelle tentative dans {delay} s")
            _bot._sleep(delay, bot.waiting)
            delay = min(delay * 2, 600)
        if _bot._running and not bot.stop_requested():
            bot.run_forever()
        # Nouvelle version installée : le superviseur relance aussitôt le bot.
        return autonomy.RESTART_CODE if bot._restart_flag else 0
    finally:
        if awake is not None:
            awake.stop()
        bot.store.close()
        bot.notifier.close()
        v29.release_locks(locks)


def main(argv: Optional[List[str]] = None) -> int:
    v29.ensure_utf8_stdio()
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in TOOLS:
        return _run_tool(argv[0], argv[1:])
    args = _parser().parse_args(argv)
    if args.login:
        os.chdir(v29.APP_DIR)            # clé Run de Windows : dossier courant quelconque
    # Sans configuration : les clés se saisissent avant la lecture du .env
    # (une autre variable invalide ne doit pas empêcher de les enregistrer).
    early = {"replay": lambda: _print_replay(args), "docs": _print_docs,
             "set-secret": lambda: cmd_set_secret(), "set-keys": lambda: cmd_set_keys(),
             "autostart": lambda: autonomy.cmd_autostart(args.action),
             "set-panel-password": lambda: cmd_set_panel_password()}
    if args.cmd in early:
        return early[args.cmd]()
    try:
        gcfg = load_guard_config_from_env()
    except ValueError as e:
        print(f"Configuration invalide : {e}", file=sys.stderr)
        return 2
    commands = {
        "panel": lambda: _panel(gcfg, args),
        "supervise": lambda: autonomy.run_supervisor(gcfg, login=args.login),
        "stop": lambda: autonomy.cmd_stop(gcfg),
        "verify": lambda: cmd_verify(gcfg),
        "diagnose": lambda: cmd_diagnose(gcfg, args.out),
        "health": lambda: health_check(gcfg, v29._env_i("TG_HEALTH_MAX_AGE_SEC", 600)),
        "status": lambda: _status(gcfg, resume=False),
        "resume": lambda: _status(gcfg, resume=True),
    }
    if args.cmd in commands:
        return commands[args.cmd]()
    return _run(gcfg, once=args.cmd == "once")


def _panel(gcfg: GuardConfig, args: argparse.Namespace) -> int:
    from panel import server as panel_server
    return panel_server.main(gcfg, args.host, args.port, args.demo,
                             not (args.no_open or args.login))
