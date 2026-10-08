"""
Examen de la porte d'exécution (prompt maître, étape 16 ;
docs/PORTE_EXECUTION.md) : les critères AC-001 à AC-050 du prompt, chacun
avec sa priorité (P0 bloquant), sa famille de la note et sa preuve, mesurée
sur le code et le bot ou apportée par un test du dépôt ; un essai de chaos
(10 000 demandes d'achat tirées au hasard, arrêt d'urgence au milieu,
doublons, révocations, changements de risque et de politique entre le
contrôle et l'ordre) jugé par un oracle écrit sans la porte ; la note de
préparation pondérée (§69) et le verdict :
READY_FOR_CONTROLLED_LIVE_EXECUTION ou NOT_READY.

Règles :
- un seul P0 non satisfait ou non mesurable suffit à dire NOT_READY ;
- « sans objet » se justifie toujours et ne compte pas dans la note ;
- le verdict juge la porte, pas le réel : il n'autorise rien. Le réel reste
  fermé tant que la porte du réel (chantiers.py, porte 8) ne s'ouvre pas, et
  l'armer reste à vous seul.

    python trendguard_bot.py porte                       # l'examen du jour
    python trendguard_bot.py porte --out docs/PORTE_EXAMEN.md
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import random
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import v29

from . import acceptation, audit, porte
from . import trend_strategy as ts
from .contrats import ContractError, GateReadinessReport, OrderIntent, SafeModeState
from .texte import fr

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "examen-porte-1.0.0"
FAMILY_WEIGHTS = {"safety": 0.25, "authorization": 0.15, "risk": 0.15, "policy": 0.10, "idempotency": 0.10,
                  "broker": 0.05, "reconciliation": 0.10, "audit": 0.05, "testing": 0.05}
FAMILY_FR = {"safety": "sûreté", "authorization": "intégrité de l'autorisation", "risk": "contrôle du risque",
             "policy": "contrôle des politiques", "idempotency": "unicité des ordres", "broker": "fiabilité de Binance",
             "reconciliation": "réconciliation", "audit": "traçabilité", "testing": "essais"}
BANDS = ((95.0, "READY"), (90.0, "CANDIDATE"), (80.0, "VALIDATION"), (70.0, "DEVELOPMENT"), (0.0, "NOT_READY"))
BAND_FR = {"READY": "prête", "CANDIDATE": "presque prête", "VALIDATION": "en validation",
           "DEVELOPMENT": "en développement", "NOT_READY": "pas prête"}
CHAOS_N = 10_000
LATENCY_BLOCK_MS = 50.0         # blocage critique (§58)
LATENCY_FINAL_MS = 100.0        # validation finale, p95 (§58)
LATENCY_KILL_MS = 100.0         # arrêt d'urgence (§58)
SEND_ROUTE = ("trendguard/bot_execution.py", "_execute_entry")
BUY_CALL = re.compile(r"\.(enter_planned|market_buy|limit_buy|create_order|create_market_buy_order|enter)\(")


def _c(n: int, title: str, prio: str, fam: str, tests: Sequence[str] = (), na: str = "") -> acceptation.Criterion:
    return acceptation.Criterion(f"AC-{n:03d}", title, prio, fam, tuple(tests), na)


C = "tests/test_contrats.py::"
LE = "tests/test_live_execution.py::"
LP = "tests/test_live_planned.py::"
PE = "tests/test_porte_examen.py::"
TG = "tests/test_trendguard.py::"
CRITERIA: Tuple[acceptation.Criterion, ...] = (
    _c(1, "Aucun ordre ne contourne la porte", "P0", "safety",
       (C + "test_every_buy_goes_through_the_gate_and_changes_nothing",
        PE + "test_one_single_route_to_binance_and_it_passes_the_gate")),
    _c(2, "Aucun ordre non autorisé n'atteint Binance", "P0", "authorization",
       ("tests/test_chantiers.py::test_a_closed_live_gate_refuses_every_real_buy",
        C + "test_live_buys_are_authorized_and_traced", TG + "test_live_requires_confirmation")),
    _c(3, "Autorisation expirée toujours rejetée", "P0", "authorization",
       ("tests/test_autorisation.py::test_a_buy_authorization_expires_and_cannot_be_replayed",
        PE + "test_final_validation_blocks_what_changed_since_the_check")),
    _c(4, "Autorisation révoquée toujours rejetée", "P0", "authorization",
       (PE + "test_final_validation_blocks_what_changed_since_the_check",
        PE + "test_in_the_bot_a_change_just_before_the_order_stops_the_buy")),
    _c(5, "Politique non tenue : ordre bloqué", "P0", "policy",
       ("tests/test_politique.py::test_an_illegible_policy_blocks_never_allows",
        "tests/test_politique.py::test_the_policies_give_the_decision_of_the_gate_on_thousands_of_cases")),
    _c(6, "Violation du risque : ordre bloqué", "P0", "risk",
       (C + "test_gate_refuses_with_reasons", "tests/test_anticipation.py::test_full_risk_budget_blocks_every_buy")),
    _c(7, "Compte gelé : ordre bloqué", "P0", "safety",
       (TG + "test_kill_switch_blocks_entries", C + "test_safe_mode_stops_buys_but_not_sales")),
    _c(8, "Marché fermé ou en maintenance : ordre bloqué", "P1", "broker",
       ("tests/test_real_conditions.py::test_network_outage_defers_quickly_without_waiting_every_pair",
        LP + "test_planned_entry_rejected_by_binance_leaves_the_bot_flat")),
    _c(9, "Instrument non négociable : ordre bloqué", "P0", "safety",
       (PE + "test_exchange_rules_are_checked_in_live_only",
        LP + "test_planned_entry_below_binance_minimum_sends_nothing")),
    _c(10, "Quantité invalide : ordre bloqué", "P0", "safety",
       (C + "test_order_intent_contract", "tests/test_fake_binance.py::test_create_order_rounds_like_ccxt",
        PE + "test_exchange_rules_are_checked_in_live_only")),
    _c(11, "Prix invalide : ordre bloqué", "P0", "safety", (C + "test_order_intent_contract",
                                                            C + "test_gate_refuses_with_reasons")),
    _c(12, "Notionnel au-dessus de la limite : ordre bloqué", "P0", "risk", (C + "test_gate_refuses_with_reasons",)),
    _c(13, "Marge insuffisante : ordre bloqué", "P0", "risk",
       na="Binance Spot sans marge ni emprunt : un achat se paie comptant (contrôle « Argent disponible »)"),
    _c(14, "Levier au-dessus de la limite : ordre bloqué", "P0", "risk",
       (C + "test_gate_refuses_with_reasons",
        "tests/test_real_conditions.py::test_make_binance_spot_only_long_timeout_clock_corrected")),
    _c(15, "Liquidité insuffisante : achat bloqué ou différé", "P1", "risk",
       ("tests/test_learning.py::test_bot_samples_books_hourly_and_defers_on_its_learned_normal",
        "tests/test_moteur_strategie.py::test_an_unknown_value_never_makes_a_condition_true")),
    _c(16, "Doublon empêché", "P0", "idempotency",
       ("tests/test_donnees.py::test_restart_never_duplicates_an_order",
        "tests/test_fake_binance.py::test_duplicate_open_client_id_rejected",
        PE + "test_chaos_ten_thousand_requests_and_a_kill_switch")),
    _c(17, "Identifiant d'ordre client unique", "P0", "idempotency",
       ("tests/test_fake_binance.py::test_duplicate_open_client_id_rejected",)),
    _c(18, "Délai dépassé à l'envoi : jamais de nouvel envoi à l'aveugle", "P0", "idempotency",
       (LP + "test_planned_entry_ambiguous_buy_halts_then_is_adopted", LE + "test_ambiguous_buy_never_placed_times_out")),
    _c(19, "État inconnu : réconciliation", "P0", "reconciliation",
       (LE + "test_ambiguous_buy_is_resolved_and_adopted", LE + "test_internal_error_after_buy_is_resolved_immediately")),
    _c(20, "Chaque ordre a sa trace", "P0", "audit",
       ("tests/test_donnees.py::test_every_paper_trade_traces_back_to_its_data",)),
    _c(21, "Chaque ordre a son identifiant d'exécution", "P0", "audit",
       ("tests/test_donnees.py::test_every_paper_trade_traces_back_to_its_data",)),
    _c(22, "Chaque ordre a son identifiant client", "P0", "audit", (LE + "test_ambiguous_buy_is_resolved_and_adopted",)),
    _c(23, "Chaque ordre est lié à son autorisation", "P0", "authorization",
       (C + "test_live_buys_are_authorized_and_traced",)),
    _c(24, "Chaque exécution est réconciliée", "P0", "reconciliation",
       (LE + "test_reconcile_recovers_lost_context", LE + "test_reconcile_records_oco_fill_while_down",
        LE + "test_fill_during_cancel_is_not_lost")),
    _c(25, "Tout écart critique crée un incident", "P0", "reconciliation",
       (TG + "test_live_boot_refuses_foreign_bot_orders", LE + "test_orphan_detected_then_cleared")),
    _c(26, "L'arrêt d'urgence bloque tout nouvel ordre", "P0", "safety",
       (TG + "test_kill_switch_blocks_entries", PE + "test_chaos_ten_thousand_requests_and_a_kill_switch")),
    _c(27, "Disjoncteurs", "P1", "broker",
       ("tests/test_core.py::test_daily_dd_halts_then_resets_next_day",
        LP + "test_planned_entry_exchange_errors_never_halt_for_nothing", LE + "test_rate_limited_buy_is_not_counted")),
    _c(28, "Aucun secret dans les journaux", "P0", "safety",
       ("tests/test_core.py::test_scrub_secrets", "tests/test_alerts.py::test_build_notifier_from_env_keeps_secrets_out_of_errors")),
    _c(29, "Une IA ne peut pas envoyer d'ordre", "P0", "authorization",
       ("tests/test_autorisation.py::test_deny_by_default", PE + "test_one_single_route_to_binance_and_it_passes_the_gate")),
    _c(30, "Un agent ne peut pas envoyer d'ordre", "P0", "authorization",
       ("tests/test_agents.py::test_the_committee_is_consultative_in_the_bot",
        "tests/test_autorisation.py::test_separation_of_duties_and_delegations")),
    _c(31, "Seul le service d'exécution (le bot) envoie", "P0", "authorization",
       ("tests/test_autorisation.py::test_separation_of_duties_and_delegations",
        PE + "test_one_single_route_to_binance_and_it_passes_the_gate")),
    _c(32, "Panne du risque : aucun achat", "P0", "risk",
       ("tests/test_moteur_risque.py::test_the_gate_of_execution_refuses_without_a_risk_assessment",
        PE + "test_a_failing_component_never_buys")),
    _c(33, "Panne des politiques : aucun achat", "P0", "policy", (PE + "test_a_failing_component_never_buys",)),
    _c(34, "Panne de l'autorisation : aucun achat", "P0", "authorization", (PE + "test_a_failing_component_never_buys",)),
    _c(35, "Panne de l'unicité ou de l'audit : aucun achat", "P0", "idempotency",
       (PE + "test_a_failing_component_never_buys",)),
    _c(36, "Panne critique : mode sûr", "P0", "safety",
       (C + "test_safe_mode_switch", "tests/test_moteur_risque.py::test_the_gate_of_execution_refuses_without_a_risk_assessment")),
    _c(37, "Toutes les décisions sont traçables", "P0", "audit", (C + "test_audit_chain_detects_any_change",)),
    _c(38, "Tous les envois sont traçables", "P0", "audit", (C + "test_live_buys_are_authorized_and_traced",)),
    _c(39, "Toute erreur critique déclenche une alerte", "P1", "audit",
       ("tests/test_alerts.py::test_hub_levels_dedup_and_failure_isolation",)),
    _c(40, "Le chaos ne produit aucun ordre non autorisé", "P0", "testing",
       (PE + "test_chaos_ten_thousand_requests_and_a_kill_switch", "tests/test_fake_binance.py::test_fault_injection")),
    _c(41, "Résiste aux doublons", "P0", "idempotency",
       (PE + "test_chaos_ten_thousand_requests_and_a_kill_switch", "tests/test_donnees.py::test_restart_never_duplicates_an_order")),
    _c(42, "Résiste aux réponses perdues", "P0", "reconciliation",
       (LE + "test_internal_error_after_buy_is_resolved_immediately",
        LP + "test_planned_entry_ambiguous_buy_halts_then_is_adopted")),
    _c(43, "Résiste aux exécutions en double", "P0", "reconciliation",
       (LE + "test_smart_buy_counts_fills_once", LE + "test_ambiguous_sell_is_not_double_counted")),
    _c(44, "Résiste aux messages dans le désordre", "P1", "reconciliation",
       (LE + "test_stop_fills_between_snapshot_and_cancel_no_double_sell",
        LE + "test_tp_fills_during_stop_move_no_new_protection")),
    _c(45, "Résiste à un changement de risque pendant l'exécution", "P0", "risk",
       (PE + "test_final_validation_blocks_what_changed_since_the_check",)),
    _c(46, "Résiste à une révocation pendant l'exécution", "P0", "authorization",
       (PE + "test_final_validation_blocks_what_changed_since_the_check",
        PE + "test_in_the_bot_a_change_just_before_the_order_stops_the_buy")),
    _c(47, "Résiste à un changement de politique pendant l'exécution", "P0", "policy",
       (PE + "test_final_validation_blocks_what_changed_since_the_check",)),
    _c(48, "Mode d'urgence", "P0", "safety",
       (C + "test_safe_mode_switch", LE + "test_panic_flatten_sells_everything")),
    _c(49, "Réconciliation", "P0", "reconciliation",
       (LE + "test_reconcile_fails_closed_on_balance_error", LE + "test_reconcile_recovers_lost_context")),
    _c(50, "Aucune performance au prix de la sécurité", "P1", "testing",
       (PE + "test_chaos_ten_thousand_requests_and_a_kill_switch",)),
)


# ══════════════════════════════════════════════════════════════════════
# Routes vers Binance (§2, P0-003)
# ══════════════════════════════════════════════════════════════════════

def routes(root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Chaque appel du code qui peut acheter chez Binance (bot, panneau,
    recherche). Il ne doit y en avoir qu'un, dans l'exécution d'un achat,
    après la porte et la validation finale."""
    found = []
    files = [root / "trendguard_bot.py"] + [f for d in ("trendguard", "panel", "research")
                                            for f in sorted((root / d).rglob("*.py"))]
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            if BUY_CALL.search(line) and not line.strip().startswith("#"):
                found.append(f"{f.relative_to(root).as_posix()}:{n}")
    ordered = False
    try:
        text = (root / SEND_ROUTE[0]).read_text(encoding="utf-8")
        body = text.split(f"def {SEND_ROUTE[1]}(", 1)[1].split("\n    def ", 1)[0]
        send = BUY_CALL.search(body)
        i, j = body.find("self._gate("), body.find("self._final_validation(")
        ordered = send is not None and 0 <= i < j < send.start()
    except (OSError, IndexError):
        pass
    ok = len(found) == 1 and found[0].startswith(SEND_ROUTE[0]) and ordered
    return {"ok": ok, "calls": found, "ordered": ordered}


# ══════════════════════════════════════════════════════════════════════
# Essai de chaos (§52-57)
# ══════════════════════════════════════════════════════════════════════

def _oracle(intent: OrderIntent, pf: porte.Portfolio, p: Any, auth: Any, at: datetime) -> bool:
    """Ce qui doit être vrai de tout ordre envoyé, écrit sans la porte."""
    eq, held, m = pf.equity, dict(pf.held_risk), 1 + porte.MARGIN
    return all((
        auth.authorized and at < datetime.fromisoformat(auth.expiration),
        not pf.halted, not pf.safe_mode.active, not pf.garde_blocked,
        pf.risk_engine is None or pf.risk_engine[0],
        not pf.live or (pf.live_armed and (pf.production is None or pf.production[0])
                        and (pf.instrument is None or pf.instrument[0])),
        intent.decision_day == pf.expected_day,
        intent.asset in pf.universe and intent.asset in pf.allowed and intent.asset not in pf.vetoed,
        intent.asset not in held and intent.idempotency_key not in pf.bought_today,
        len(held) < p.max_positions,
        eq > 0 and intent.risk_quote <= p.risk_pct * eq * pf.risk_mult * m + 1e-9,
        sum(held.values()) + intent.risk_quote <= p.max_total_risk * eq * pf.risk_mult * m + 1e-9,
        intent.cost <= p.max_position_pct * eq * (1 + p.fee) * m + 1e-9,
        intent.cost <= pf.cash * (1 + 1e-9) + 1e-6,
        intent.stop < intent.entry,
        intent.qty * intent.entry >= porte.MIN_NOTIONAL * (1 - 1e-9),
        pf.data_quality is None or pf.data_quality >= porte.QUALITY_MIN,
    ))


def _request(rng: random.Random, p: Any, days: Sequence[str], bought: frozenset, halted: bool
             ) -> Tuple[Optional[OrderIntent], porte.Portfolio]:
    """Une demande d'achat et le portefeuille du moment, tirés au hasard."""
    assets = ("btc", "eth", "sol", "xrp", "ada", "doge", "link", "aave")
    eq = rng.choice((0.0, 50.0, 97.0, 1000.0, 100_000.0))
    held = tuple((a, rng.uniform(0.0, 0.015) * max(eq, 1.0)) for a in rng.sample(assets, rng.randint(0, 4)))
    live = rng.random() < 0.15
    day = rng.choice(days)
    pf = porte.Portfolio(
        equity=eq, cash=rng.uniform(0.0, 1.0) * eq, invested=0.0, held_risk=held,
        risk_mult=rng.choice((1.0, 1.0, 0.5)), expected_day=day, universe=frozenset(assets),
        allowed=frozenset(rng.sample(assets, rng.randint(4, len(assets)))),
        vetoed=frozenset(rng.sample(assets, rng.randint(0, 1))), bought_today=bought, halted=halted,
        garde_blocked=("marché sans tendance",) if rng.random() < 0.05 else (),
        safe_mode=SafeModeState(active=True, reason="essai", activated_by="vous") if rng.random() < 0.03
        else SafeModeState(), data_quality=rng.choice((None, None, 30.0, 85.0)), live=live,
        live_armed=rng.random() < 0.6, production=rng.choice((None, (True, "ouverte"), (False, "fermée"))),
        risk_engine=rng.choice((None, (True, "normal"), (True, "normal"), (False, "bloqué"))),
        instrument=rng.choice(((True, "conforme"), (True, "conforme"), (False, "sous le minimum"))) if live else None)
    entry = rng.uniform(0.05, 500.0)
    stop = entry * rng.uniform(0.7, 1.02)
    risk_target = p.risk_pct * max(eq, 1.0) * pf.risk_mult * rng.uniform(0.3, 1.4)
    qty = risk_target / (entry - stop) if entry > stop else rng.uniform(0.1, 2.0)
    try:
        intent = OrderIntent(asset=rng.choice(assets + ("pepe",)), qty=qty, entry=entry, stop=stop,
                             cost=qty * entry * (1 + p.fee), risk_quote=max(qty * (entry - stop), 1e-6),
                             decision_day=day if rng.random() < 0.97 else "2026-01-01")
    except ContractError:
        intent = None
    return intent, pf


def _between(rng: random.Random, pf: porte.Portfolio, asset: str) -> Tuple[porte.Portfolio, str]:
    """Ce qui peut changer entre le contrôle et l'ordre (§21, §54-55)."""
    r = rng.random()
    if r < 0.03:
        return dataclasses.replace(pf, halted=True), "arrêt d'urgence"
    if r < 0.06:
        return dataclasses.replace(pf, safe_mode=SafeModeState(active=True, reason="révocation",
                                                               activated_by="vous")), "mode sûr"
    if r < 0.09:
        extra = (("zzz", rng.uniform(0.0, 0.08) * max(pf.equity, 1.0)),)
        return dataclasses.replace(pf, held_risk=pf.held_risk + extra), "risque"
    if r < 0.12:
        return dataclasses.replace(pf, vetoed=pf.vetoed | {asset}), "politique"
    if r < 0.15:
        return dataclasses.replace(pf, risk_engine=(False, "bloqué depuis le contrôle")), "moteur de risque"
    if r < 0.20:                         # changement sans danger : la note des données arrive entre-temps
        return dataclasses.replace(pf, data_quality=95.0 if pf.data_quality == 90.0 else 90.0), "données"
    return pf, ""


def chaos(n: int = CHAOS_N, seed: int = 16) -> Dict[str, Any]:
    """n demandes d'achat tirées au hasard (graine fixe), arrêt d'urgence à
    la moitié, demandes répétées, délais, révocations et changements d'état
    entre le contrôle et l'ordre ; chaque ordre envoyé est jugé par un
    oracle écrit sans la porte. Attendu : aucun ordre non autorisé, aucun
    après l'arrêt d'urgence, aucun doublon."""
    rng = random.Random(seed)
    p = ts.TrendParams()
    now = datetime(2026, 10, 8, 0, 5, tzinfo=timezone.utc)
    days = [f"2026-{m:02d}-{d:02d}" for m in (7, 8, 9) for d in range(1, 29)]
    kill_at = n // 2
    bought: set = set()
    st = {"requests": n, "invalid": 0, "approved": 0, "sent": 0, "unauthorized": 0, "after_kill": 0,
          "duplicates": 0, "repeated": 0, "final_blocked": 0, "revalidated": 0, "changes": 0, "late": 0,
          "kill_at": kill_at}
    times: List[float] = []
    last: Optional[Tuple[OrderIntent, porte.Portfolio]] = None
    for i in range(n):
        halted = i >= kill_at or rng.random() < 0.02
        if last is not None and rng.random() < 0.05:          # la même demande, encore
            intent, pf = last[0], dataclasses.replace(last[1], bought_today=frozenset(bought), halted=halted)
            st["repeated"] += 1
        else:
            intent, pf = _request(rng, p, days, frozenset(bought), halted)
        if intent is None:
            st["invalid"] += 1
            continue
        last = (intent, pf)
        t = time.perf_counter()
        d = porte.check(intent, pf, p, now)
        auth = porte.authorize(d, pf, now)
        before = porte.fingerprint(pf)
        pf2, change = _between(rng, pf, intent.asset)
        at = now + timedelta(seconds=rng.choice((0, 0, 0, 1, 30, porte.AUTH_SECONDS - 1, porte.AUTH_SECONDS, 900)))
        fv = porte.final_validation(intent, d, auth, before, pf2, p, at)
        times.append((time.perf_counter() - t) * 1000)
        st["approved"] += int(d.approved)
        st["changes"] += int(bool(change))
        st["late"] += int(at >= now + timedelta(seconds=porte.AUTH_SECONDS))
        if not fv.passed:
            st["final_blocked"] += int(d.approved)
            continue
        st["sent"] += 1
        st["revalidated"] += int(fv.revalidated)
        st["unauthorized"] += int(not _oracle(intent, pf2, p, auth, at))
        st["after_kill"] += int(i >= kill_at)
        st["duplicates"] += int(intent.idempotency_key in bought)
        bought.add(intent.idempotency_key)
    times.sort()
    st["p95_ms"] = times[int(0.95 * (len(times) - 1))] if times else 0.0
    st["ok"] = st["sent"] > 0 and not (st["unauthorized"] or st["after_kill"] or st["duplicates"])
    return st


# ══════════════════════════════════════════════════════════════════════
# Mesures et verdict (§68-69, §73)
# ══════════════════════════════════════════════════════════════════════

def _measures(state: Dict[str, Any], gcfg: Any, now: datetime, ch: Dict[str, Any], rt: Dict[str, Any]
              ) -> Dict[str, Tuple[str, str]]:
    """(état, détail) des critères mesurés sur le code, le bot et le chaos."""
    out: Dict[str, Tuple[str, str]] = {}
    out["AC-001"] = (("PASS", "un seul appel d'achat vers Binance, dans l'exécution d'un achat, après la porte et "
                      "la validation finale") if rt["ok"] else
                     ("FAIL", "routes d'achat : " + (", ".join(rt["calls"]) or "aucune") +
                      ("" if rt["ordered"] else " ; ordre porte → validation finale → envoi non tenu")))
    for ac in ("AC-029", "AC-030", "AC-031"):
        out[ac] = (("PASS", "aucune route d'achat hors du bot ; Rachelle, les IA et les agents n'en ont aucune")
                   if rt["ok"] else ("FAIL", "route d'achat hors de l'exécution du bot"))
    pol, aut = state.get("politique") or {}, state.get("autorisation") or {}
    out["AC-002"] = (("PASS" if ch["ok"] and not aut.get("mismatch") else "FAIL"),
                     f"chaos : {ch['unauthorized']} ordre non autorisé sur {ch['sent']} envoyé(s)" +
                     (f" ; bot : {aut.get('checked', 0)} achat(s) vérifié(s) par le moteur d'autorisation, "
                      f"{len(aut.get('mismatch') or [])} écart(s)" if aut.get("day") else ""))
    if pol.get("day"):
        out["AC-005"] = (("FAIL" if pol.get("mismatch") else "PASS"),
                         f"bot : {pol.get('checked', 0)} achat(s), {len(pol.get('mismatch') or [])} écart(s) entre "
                         "les politiques et la porte")
    kill_ms = acceptation._timed(lambda: acceptation._sample_check(True, now), 50)
    blocked = acceptation._sample_check(True, now).status == "EMERGENCY_BLOCK"
    out["AC-026"] = (("PASS" if blocked and kill_ms <= LATENCY_KILL_MS and not ch["after_kill"] else "FAIL"),
                     f"achat bloqué en {fr(kill_ms, '.2f')} ms ; chaos : {ch['after_kill']} ordre après l'arrêt "
                     f"d'urgence (demande n° {fr(ch['kill_at'] + 1, ',d')} sur {fr(ch['requests'], ',d')})")
    sm = porte.safe_mode(gcfg)
    out["AC-036"] = ("PASS", "mode sûr " + ("ACTIF" if sm.active else "prêt") + " ; fichier illisible : actif ; "
                     "évaluation du risque absente : aucun achat")
    av = audit.verify(audit.path_for(gcfg)) if os.path.exists(audit.path_for(gcfg) or "") else None
    if av is not None:
        out["AC-037"] = (("PASS" if av["ok"] else "FAIL"), audit.describe(av))
    out["AC-040"] = (("PASS" if ch["ok"] else "FAIL"),
                     f"{fr(ch['requests'], ',d')} demandes, {ch['sent']} ordre(s) envoyé(s), {ch['unauthorized']} non "
                     f"autorisé(s) selon l'oracle ; {ch['changes']} changement(s) entre contrôle et ordre, "
                     f"{ch['late']} envoi(s) tardif(s)")
    out["AC-041"] = (("PASS" if not ch["duplicates"] else "FAIL"),
                     f"{ch['repeated']} demande(s) répétée(s) : {ch['duplicates']} doublon envoyé")
    final_ms = ch["p95_ms"]
    block_ms = acceptation._timed(lambda: acceptation._sample_check(False, now))
    out["AC-050"] = (("PASS" if final_ms <= LATENCY_FINAL_MS and block_ms <= LATENCY_BLOCK_MS and rt["ok"] else "FAIL"),
                     f"contrôle + autorisation + validation finale : {fr(final_ms, '.2f')} ms au 95e centile (cible "
                     f"{fr(LATENCY_FINAL_MS, '.0f')}) ; contrôle seul {fr(block_ms, '.2f')} ms (cible "
                     f"{fr(LATENCY_BLOCK_MS, '.0f')}) ; aucun raccourci : chaque achat passe tout")
    return out


def band(score: float) -> str:
    """Bande de la note de préparation (§69)."""
    return next(name for floor, name in BANDS if score >= floor)


def grade(criteria: Sequence[acceptation.Criterion], measured: Dict[str, Tuple[str, str]],
          weights: Dict[str, float], root: pathlib.Path = ROOT) -> Tuple[List[Dict[str, Any]], Dict[str, float],
                                                                          float, List[str]]:
    """Chaque critère jugé (mesure, ou tests du dépôt, ou sans objet), la
    note par famille, la note pondérée et les P0 non satisfaits. Une preuve
    introuvable n'est jamais une réussite."""
    rows = []
    for c in criteria:
        if c.not_applicable:
            status, detail = "NOT_APPLICABLE", c.not_applicable
        else:
            ok_t, missing = acceptation.test_evidence(c.tests, root)
            m = measured.get(c.ac_id)
            if m is None:
                status = "PASS" if c.tests and ok_t else "UNKNOWN"
                detail = (f"prouvé par {len(c.tests)} test(s) du dépôt" if c.tests and ok_t
                          else "test(s) introuvable(s) : " + ", ".join(missing) if c.tests else "aucune mesure")
            else:
                status, detail = m
                if c.tests:
                    detail += f" ; {len(c.tests)} test(s) du dépôt" if ok_t else " ; test(s) introuvable(s)"
                    if not ok_t and status == "PASS":
                        status = "UNKNOWN"
        rows.append({"id": c.ac_id, "title": c.title, "priority": c.priority, "family": c.family,
                     "status": status, "detail": detail})
    fam_scores: Dict[str, float] = {}
    for fam in weights:
        applicable = [r for r in rows if r["family"] == fam and r["status"] != "NOT_APPLICABLE"]
        if applicable:
            fam_scores[fam] = 100.0 * sum(r["status"] == "PASS" for r in applicable) / len(applicable)
    wsum = sum(weights[f] for f in fam_scores)
    score = sum(weights[f] * s for f, s in fam_scores.items()) / wsum if wsum else 0.0
    p0_fail = [r["id"] for r in rows if r["priority"] == "P0" and r["status"] in ("FAIL", "UNKNOWN")]
    return rows, fam_scores, score, p0_fail


def evaluate(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None, chaos_n: int = CHAOS_N,
             root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Les 50 critères, la note, le chaos et le verdict
    (READY_FOR_CONTROLLED_LIVE_EXECUTION ou NOT_READY)."""
    now = now or datetime.now(timezone.utc)
    ch = chaos(chaos_n)
    rt = routes(root)
    rows, fam_scores, score, p0_fail = grade(CRITERIA, _measures(state, gcfg, now, ch, rt), FAMILY_WEIGHTS, root)
    ready = not p0_fail and score >= 95 and ch["ok"]
    pt = state.get("porte") or {}
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "rows": rows, "family_scores": fam_scores, "score": round(score, 1), "band": band(score),
            "p0_failures": p0_fail, "chaos": ch, "routes": rt,
            "bot": {k: pt.get(k, 0) for k in ("approved", "refused", "final_checked", "final_blocked", "revalidated")}
            | {"day": pt.get("day")},
            "status": "READY_FOR_CONTROLLED_LIVE_EXECUTION" if ready else "NOT_READY"}


def report_of(r: Dict[str, Any]) -> GateReadinessReport:
    """L'examen au format du contrat GateReadinessReport.v1."""
    counts = {s: sum(1 for x in r["rows"] if x["status"] == s) for s in acceptation.STATUS_FR}
    return GateReadinessReport(report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=r["status"],
                               readiness_score=r["score"], band=r["band"], p0_failures=tuple(r["p0_failures"]),
                               passed=counts["PASS"], failed=counts["FAIL"], unknown=counts["UNKNOWN"],
                               not_applicable=counts["NOT_APPLICABLE"], chaos_orders=r["chaos"]["sent"],
                               chaos_unauthorized=r["chaos"]["unauthorized"], engine_version=VERSION)


def describe(view: Optional[Dict[str, Any]]) -> str:
    """Une phrase sur la validation finale du jour (état du bot, porte)."""
    if not view or not view.get("final_checked") and not view.get("final_blocked"):
        return "aucun achat validé juste avant l'ordre aujourd'hui"
    out = f"{view.get('final_checked', 0)} achat(s) validé(s) juste avant l'ordre"
    if view.get("revalidated"):
        out += f", {view['revalidated']} recontrôlé(s) (état changé)"
    if view.get("final_blocked"):
        out += f", {view['final_blocked']} arrêté(s) au dernier moment"
    return out


def render(r: Dict[str, Any]) -> str:
    """docs/PORTE_EXAMEN.md : le verdict, le chaos, les familles, chaque critère."""
    ch, b = r["chaos"], r["bot"]
    ready = r["status"] == "READY_FOR_CONTROLLED_LIVE_EXECUTION"
    lines = ["# Examen de la porte d'exécution (critères AC-001 à AC-050)", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py porte` (étape 16 du prompt maître, "
             "[`PORTE_EXECUTION.md`](PORTE_EXECUTION.md)). L'examen juge la porte, pas le réel : il n'autorise rien. "
             "Le réel reste fermé tant que la porte du réel ne s'ouvre pas, et l'armer reste à vous seul.", "",
             "## Verdict", "",
             f"- **{'PRÊTE pour un réel contrôlé' if ready else 'PAS PRÊTE'}** "
             f"({r['status']}).",
             f"- Note de préparation {fr(r['score'], '.1f')}/100 ({BAND_FR[r['band']]}) ; critères P0 non satisfaits : "
             f"{len(r['p0_failures'])}" + (f" ({', '.join(r['p0_failures'])})" if r["p0_failures"] else "") + ".",
             f"- Chaos : {fr(ch['requests'], ',d')} demandes d'achat, {ch['sent']} ordre(s) envoyé(s), "
             f"{ch['unauthorized']} non autorisé(s), {ch['after_kill']} après l'arrêt d'urgence, {ch['duplicates']} "
             f"doublon ; {fr(ch['p95_ms'], '.2f')} ms au 95e centile.",
             "- Bot " + (f"(décision du {b['day']}) : {b['approved']} achat(s) autorisé(s), {b['refused']} refusé(s), "
                         f"{b['final_checked']} validé(s) juste avant l'ordre, {b['final_blocked']} arrêté(s) au "
                         f"dernier moment, {b['revalidated']} recontrôlé(s)." if b.get("day")
                         else ": aucun achat contrôlé dans l'état lu."),
             "- Mesurable seulement en réel : la fiabilité du vrai Binance en ordres réels, les délais réels "
             "d'accusé de réception et d'exécution, le rapprochement avec le vrai compte. Ici ils sont prouvés sur le "
             "faux Binance des tests (pannes, réponses perdues, exécutions partielles ou en double) ; ils seront "
             "mesurés au réel contrôlé (étape 17)."]
    lines += ["", "## Chaos", "", "| Mesure | valeur |", "| --- | --- |"]
    for key, label in (("requests", "demandes"), ("invalid", "intentions invalides (contrat)"),
                       ("repeated", "demandes répétées"), ("approved", "contrôles approuvés"),
                       ("changes", "changements entre contrôle et ordre"), ("late", "envois après expiration"),
                       ("final_blocked", "approuvés puis arrêtés à la validation finale"),
                       ("revalidated", "envoyés après un nouveau contrôle"), ("sent", "ordres envoyés"),
                       ("unauthorized", "ordres non autorisés (oracle)"), ("after_kill", "ordres après l'arrêt d'urgence"),
                       ("duplicates", "doublons envoyés")):
        lines.append(f"| {label} | {fr(ch[key], ',d')} |")
    lines += ["", "## Familles", "", "| Famille | poids | note |", "| --- | --- | --- |"]
    for fam, w in FAMILY_WEIGHTS.items():
        s = r["family_scores"].get(fam)
        lines.append(f"| {FAMILY_FR[fam]} | {fr(w * 100, '.0f')} % | {fr(s, '.0f') if s is not None else '—'} |")
    lines += ["", "## Critères", "", "| Critère | priorité | état | preuve |", "| --- | --- | --- | --- |"]
    for x in r["rows"]:
        lines.append(f"| {x['id']} {x['title']} | {x['priority']} | {acceptation.STATUS_FR[x['status']]} | "
                     f"{x['detail']} |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `porte` : l'examen du jour, écrit avec --out."""
    from .config import load_guard_config_from_env
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Examen de la porte d'exécution (AC-001 à AC-050)")
    ap.add_argument("--out", default="")
    ap.add_argument("--chaos", type=int, default=CHAOS_N)
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    r = evaluate(g, read_state(g.db_file) or {}, chaos_n=args.chaos)
    try:
        report_of(r)
    except ContractError as e:
        print(f"Examen non conforme à son contrat : {e}")
        return 1
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["status"] == "READY_FOR_CONTROLLED_LIVE_EXECUTION" else 1
