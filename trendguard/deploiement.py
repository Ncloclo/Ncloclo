"""
Exécution réelle par paliers (prompt maître, étape 17 ;
docs/DEPLOIEMENT_REEL.md) : le chemin du paper au réel, palier par palier,
sans jamais de promotion automatique.

    OMBRE → PAPER → RÉEL SIMULÉ (testnet) → RÉEL CONTRÔLÉ → PRODUCTION LIMITÉE → PRODUCTION

Le palier en vigueur se lit dans vos réglages : RUN_MODE, BINANCE_TESTNET et,
en réel, TG_PALIER_REEL (controle par défaut, puis limite, puis production).
Seul un réglage que vous changez fait monter d'un palier ; le bot ne s'en
donne jamais un. Chaque passage a sa porte, mesurée, avec ce qui manque ; une
porte ouverte ne fait pas monter pour autant : c'est vous qui décidez.

Plafonds du palier, appliqués par la porte d'exécution en réel seulement
(porte.py, « Plafonds du palier ») : nombre d'achats par jour, montant acheté
dans la journée (part du capital) et capital confié au bot fixé par vous
(TG_MAX_CAPITAL). Ils ne font que réduire ; en paper, rien ne change.

L'exécution elle-même est celle de v29 (intention enregistrée avant
l'envoi, identifiant client unique, réponse perdue → recherche, état inconnu
→ arrêt, rapprochement avec Binance, protection immédiate) ; ce module
l'examine (critères AC-001 à AC-060, note §55) et mesure la qualité des
exécutions (écart au cours de décision, délai d'exécution en réel).

    python trendguard_bot.py deploiement           # palier, portes, examen
    python trendguard_bot.py deploiement --out docs/DEPLOIEMENT.md
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, Tuple

import v29

from . import acceptation, porte_examen
from . import trend_strategy as ts
from .contrats import ContractError, LiveDeploymentReport
from .texte import fr

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "deploiement-1.0.0"
STAGES = ("SHADOW", "PAPER", "SIMULATED_LIVE", "CONTROLLED_LIVE", "LIMITED_PRODUCTION", "PRODUCTION")
STAGE_FR = {"SHADOW": "ombre (rejeu et backtest, sans portefeuille)", "PAPER": "paper (argent fictif, vrai marché)",
            "SIMULATED_LIVE": "réel simulé (testnet de Binance)", "CONTROLLED_LIVE": "réel contrôlé",
            "LIMITED_PRODUCTION": "production limitée", "PRODUCTION": "production"}
SETTING_STAGE = {"controle": "CONTROLLED_LIVE", "limite": "LIMITED_PRODUCTION", "production": "PRODUCTION"}
# Plafonds du palier (réel seulement) : achats par jour, part du capital achetée
# dans la journée, capital confié au bot fixé par vous.
LIMITS = {"CONTROLLED_LIVE": {"buys_day": 2, "daily_pct": 0.40, "capital_cap": True},
          "LIMITED_PRODUCTION": {"buys_day": 4, "daily_pct": 0.75, "capital_cap": True}}
LIVE_DAYS = {"LIMITED_PRODUCTION": 30, "PRODUCTION": 90}      # jours en réel avant de proposer le palier
LIVE_TRADES = {"LIMITED_PRODUCTION": 10, "PRODUCTION": 30}    # trades réels clos
FAMILY_WEIGHTS = {"safety": 0.25, "reconciliation": 0.15, "reliability": 0.15, "orders": 0.10, "broker": 0.10,
                  "risk_policy": 0.10, "observability": 0.05, "security": 0.05, "performance": 0.05}
FAMILY_FR = {"safety": "sûreté", "reconciliation": "rapprochement", "reliability": "fiabilité",
             "orders": "gestion des ordres", "broker": "résilience face à Binance", "risk_policy": "risque et politiques",
             "observability": "observabilité", "security": "sécurité", "performance": "performance"}
QUALITY_KEEP = 100


# ══════════════════════════════════════════════════════════════════════
# Palier en vigueur et plafonds (§32-35)
# ══════════════════════════════════════════════════════════════════════

def current(gcfg: Any) -> str:
    """Le palier en vigueur, lu dans vos réglages (jamais décidé par le bot)."""
    if getattr(gcfg, "run_mode", "paper") != "live":
        return "PAPER"
    if getattr(gcfg, "binance_testnet", False):
        return "SIMULATED_LIVE"
    return SETTING_STAGE.get(str(getattr(gcfg, "live_stage", "controle")), "CONTROLLED_LIVE")


def stage_limits(stage: str, buys_today: int, bought_today: float, cost: float, equity: float,
                 max_capital: float) -> Tuple[bool, str]:
    """Plafonds du palier pour un achat réel (porte d'exécution) : achats du
    jour, part du capital achetée dans la journée, capital confié au bot
    fixé par vous. Hors réel contrôlé et production limitée : aucun plafond
    de plus que ceux de la règle."""
    lim = LIMITS.get(stage)
    if lim is None:
        return True, f"{STAGE_FR.get(stage, stage)} : plafonds de la règle seulement"
    if lim["capital_cap"] and not max_capital > 0:
        return False, f"{STAGE_FR[stage]} : capital confié au bot non fixé (TG_MAX_CAPITAL)"
    if buys_today + 1 > lim["buys_day"]:
        return False, f"{STAGE_FR[stage]} : {buys_today + 1} achats aujourd'hui pour {lim['buys_day']} au plus"
    cap = lim["daily_pct"] * equity
    if not equity > 0 or bought_today + cost > cap + 1e-9:
        return False, (f"{STAGE_FR[stage]} : {fr(bought_today + cost, '.2f')} achetés aujourd'hui pour "
                       f"{fr(cap, '.2f')} au plus ({fr(lim['daily_pct'] * 100, '.0f')} % du capital)")
    return True, (f"{STAGE_FR[stage]} : achat {buys_today + 1} sur {lim['buys_day']}, {fr(bought_today + cost, '.2f')} "
                  f"sur {fr(cap, '.2f')} permis aujourd'hui")


def note_quality(view: Optional[Dict[str, Any]], asset: str, ref_price: float, fill_price: float,
                 latency_ms: Optional[float]) -> Dict[str, Any]:
    """Qualité d'une exécution (§40-41) : écart entre le prix payé et le cours
    de décision (en points de base, + = payé plus cher) et, en réel, délai
    entre l'envoi et l'exécution. Les 100 dernières sont gardées."""
    view = dict(view or {})
    if ref_price > 0 and fill_price > 0:
        sb = list(view.get("shortfall_bps") or []) + [round((fill_price / ref_price - 1) * 1e4, 2)]
        view["shortfall_bps"] = sb[-QUALITY_KEEP:]
        view["n"] = int(view.get("n", 0)) + 1
        view["last"] = asset
    if latency_ms is not None:
        view["latency_ms"] = (list(view.get("latency_ms") or []) + [round(float(latency_ms), 1)])[-QUALITY_KEEP:]
    return view


def describe_quality(view: Optional[Dict[str, Any]]) -> str:
    """Une phrase : écart moyen au cours de décision, délai médian en réel."""
    sb = (view or {}).get("shortfall_bps") or []
    if not sb:
        return "aucune exécution mesurée"
    out = (f"{len(sb)} exécution(s) : écart moyen au cours de décision {fr(statistics.fmean(sb), '+.1f')} points de "
           "base")
    lat = (view or {}).get("latency_ms") or []
    if lat:
        out += f", délai médian {fr(statistics.median(lat), '.0f')} ms"
    return out


# ══════════════════════════════════════════════════════════════════════
# Les portes entre paliers (§33, §35, §56-57)
# ══════════════════════════════════════════════════════════════════════

def _validated(root: pathlib.Path) -> bool:
    path = root / "docs" / "VALIDATION.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return bool(re.search(r"\*\*VALIDE\b", text)) or "VALIDE" in text[:3000]


def _days(state: Dict[str, Any], now: datetime) -> float:
    started = v29._parse_iso(str(state.get("started_at"))) if state.get("started_at") else None
    return (now - started).total_seconds() / 86400 if started else 0.0


def ladder(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None, acceptance: Optional[Dict[str, Any]] = None,
           gate_exam: Optional[Dict[str, Any]] = None, live_gate: Optional[Dict[str, Any]] = None,
           root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Chaque palier, son état (franchi, en vigueur, à venir) et la porte qui
    mène au suivant : ce qui est mesuré et ce qui manque. La dernière
    condition est toujours votre réglage : aucune promotion automatique."""
    now = now or datetime.now(timezone.utc)
    stage = current(gcfg)
    live_days, live_trades = _days(state, now), len(state.get("trades") or [])
    acc = acceptance if acceptance is not None else acceptation.evaluate(gcfg, state, now)
    gates = {
        "PAPER": [("backtest validé (docs/VALIDATION.md)", _validated(root))],
        "SIMULATED_LIVE": [("paper accepté (AC-001 à AC-044)", acc.get("status") == "ACCEPTED"),
                           ("vous : RUN_MODE=live, BINANCE_TESTNET=true et les deux réglages du réel",
                            stage in ("SIMULATED_LIVE",))],
        "CONTROLLED_LIVE": [("porte du réel ouverte (porte 8)", bool((live_gate or {}).get("open"))),
                            ("porte d'exécution prête (AC-001 à AC-050)",
                             (gate_exam or {}).get("status") == "READY_FOR_CONTROLLED_LIVE_EXECUTION"),
                            ("vous : BINANCE_TESTNET=false et TG_MAX_CAPITAL fixé",
                             stage == "CONTROLLED_LIVE" and getattr(gcfg, "max_capital", 0) > 0)],
        "LIMITED_PRODUCTION": [(f"{LIVE_DAYS['LIMITED_PRODUCTION']} jours en réel",
                                stage in ("CONTROLLED_LIVE", "LIMITED_PRODUCTION", "PRODUCTION")
                                and live_days >= LIVE_DAYS["LIMITED_PRODUCTION"]),
                               (f"{LIVE_TRADES['LIMITED_PRODUCTION']} trades réels clos",
                                stage in ("CONTROLLED_LIVE", "LIMITED_PRODUCTION", "PRODUCTION")
                                and live_trades >= LIVE_TRADES["LIMITED_PRODUCTION"]),
                               ("aucun arrêt d'urgence en cours", not state.get("halted")),
                               ("vous : TG_PALIER_REEL=limite", stage in ("LIMITED_PRODUCTION", "PRODUCTION"))],
        "PRODUCTION": [(f"{LIVE_DAYS['PRODUCTION']} jours en réel",
                        stage in ("LIMITED_PRODUCTION", "PRODUCTION") and live_days >= LIVE_DAYS["PRODUCTION"]),
                       (f"{LIVE_TRADES['PRODUCTION']} trades réels clos",
                        stage in ("LIMITED_PRODUCTION", "PRODUCTION") and live_trades >= LIVE_TRADES["PRODUCTION"]),
                       ("vous : TG_PALIER_REEL=production", stage == "PRODUCTION")],
    }
    rows, idx = [], STAGES.index(stage)
    for i, name in enumerate(STAGES):
        conds = gates.get(name, [])
        rows.append({"stage": name, "label": STAGE_FR[name],
                     "state": "franchi" if i < idx else "en vigueur" if i == idx else "à venir",
                     "gate": [{"what": w, "ok": bool(ok)} for w, ok in conds]})
    nxt = STAGES[idx + 1] if idx + 1 < len(STAGES) else ""
    measured = [c for c in gates.get(nxt, []) if not c[0].startswith("vous :")]
    missing = [w for w, ok in measured if not ok]
    return {"stage": stage, "next": nxt, "rows": rows, "next_gate_open": bool(nxt) and not missing,
            "missing": missing, "limits": LIMITS.get(stage), "acceptance": acc.get("status")}


# ══════════════════════════════════════════════════════════════════════
# Examen : AC-001 à AC-060 (§53, §55, §62)
# ══════════════════════════════════════════════════════════════════════

_c = porte_examen._c
C = "tests/test_contrats.py::"
LE = "tests/test_live_execution.py::"
LP = "tests/test_live_planned.py::"
RC = "tests/test_real_conditions.py::"
PE = "tests/test_porte_examen.py::"
DP = "tests/test_deploiement.py::"
TG = "tests/test_trendguard.py::"
CRITERIA: Tuple[acceptation.Criterion, ...] = (
    _c(1, "Aucun ordre sans la porte d'exécution", "P0", "safety",
       (PE + "test_one_single_route_to_binance_and_it_passes_the_gate",
        C + "test_every_buy_goes_through_the_gate_and_changes_nothing")),
    _c(2, "Aucun ordre sans autorisation", "P0", "safety",
       (C + "test_live_buys_are_authorized_and_traced", "tests/test_autorisation.py::test_conditions_must_all_hold_exactly")),
    _c(3, "Aucun ordre hors des politiques", "P0", "risk_policy",
       ("tests/test_politique.py::test_the_policies_give_the_decision_of_the_gate_on_thousands_of_cases",)),
    _c(4, "Aucun ordre hors des limites de risque", "P0", "risk_policy",
       (C + "test_gate_refuses_with_reasons",
        "tests/test_moteur_risque.py::test_the_gate_of_execution_refuses_without_a_risk_assessment")),
    _c(5, "Aucun dépassement de montant", "P0", "risk_policy",
       (C + "test_gate_refuses_with_reasons", DP + "test_the_stage_limits_only_apply_in_live_and_only_reduce")),
    _c(6, "Aucun dépassement de position", "P0", "risk_policy", (C + "test_gate_refuses_with_reasons",)),
    _c(7, "Aucun levier", "P0", "risk_policy", (RC + "test_make_binance_spot_only_long_timeout_clock_corrected",)),
    _c(8, "Aucun ordre en double", "P0", "orders",
       ("tests/test_donnees.py::test_restart_never_duplicates_an_order",
        "tests/test_fake_binance.py::test_duplicate_open_client_id_rejected")),
    _c(9, "Idempotence (même identifiant client au nouvel essai)", "P0", "orders",
       (RC + "test_order_retried_after_clock_resync", "tests/test_donnees.py::test_restart_never_duplicates_an_order")),
    _c(10, "Ordres parents et enfants", "P1", "orders",
       na="un achat est un seul ordre au marché (moins de 0,03 % du volume du jour) : rien à découper"),
    _c(11, "Adaptateur de Binance isolé", "P1", "broker", (PE + "test_one_single_route_to_binance_and_it_passes_the_gate",)),
    _c(12, "Santé de Binance vérifiée", "P1", "broker",
       (RC + "test_network_outage_defers_quickly_without_waiting_every_pair",
        LP + "test_planned_entry_timeout_before_sending_is_forgotten")),
    _c(13, "Reprise de session", "P0", "reliability",
       (LE + "test_reconcile_recovers_lost_context",
        "tests/test_autonomy.py::test_supervisor_restarts_after_crash_then_stops_with_the_bot")),
    _c(14, "Reconnexion", "P1", "reliability",
       (RC + "test_transient_ohlcv_error_is_retried", RC + "test_one_pair_network_error_does_not_block_other_protections")),
    _c(15, "Ordre inconnu retrouvé", "P0", "reconciliation",
       (LE + "test_ambiguous_buy_is_resolved_and_adopted", LP + "test_planned_entry_ambiguous_buy_halts_then_is_adopted")),
    _c(16, "Exécutions partielles", "P0", "orders",
       (LE + "test_oco_partial_fill_is_booked_and_remainder_reprotected",
        LE + "test_thin_book_entry_partially_expired_is_protected")),
    _c(17, "Exécutions dédupliquées", "P0", "reconciliation",
       (LE + "test_smart_buy_counts_fills_once", LE + "test_ambiguous_sell_is_not_double_counted")),
    _c(18, "Positions rapprochées", "P0", "reconciliation",
       (LE + "test_reconcile_recovers_lost_context", LE + "test_orphan_detected_then_cleared")),
    _c(19, "Soldes rapprochés", "P0", "reconciliation",
       (LE + "test_reconcile_fails_closed_on_balance_error", LE + "test_external_base_reserve_is_never_sold")),
    _c(20, "Frais rapprochés", "P1", "reconciliation",
       ("tests/test_fake_binance.py::test_bnb_fee_mode_on_both_sides", LE + "test_break_even_covers_costs")),
    _c(21, "Rapprochement continu", "P0", "reconciliation",
       (LE + "test_protection_cancelled_outside_bot_is_replaced", LE + "test_position_without_protection_ids_is_reprotected")),
    _c(22, "Arrêt d'urgence", "P0", "safety", (TG + "test_kill_switch_blocks_entries",)),
    _c(23, "Tout vendre en urgence", "P0", "safety", (LE + "test_panic_flatten_sells_everything",)),
    _c(24, "Fail-closed", "P0", "safety",
       (PE + "test_a_failing_component_never_buys", LE + "test_reconcile_fails_closed_on_balance_error")),
    _c(25, "Limitation du débit", "P1", "performance",
       (LE + "test_rate_limited_buy_is_not_counted", LE + "test_many_stop_moves_never_exceed_algo_order_limit")),
    _c(26, "Contre-pression", "P1", "performance",
       (RC + "test_network_outage_defers_quickly_without_waiting_every_pair", RC + "test_stalled_cycle_dumps_thread_stacks")),
    _c(27, "Ordre des événements", "P0", "orders",
       (LE + "test_stop_fills_between_snapshot_and_cancel_no_double_sell", LE + "test_fill_during_cancel_is_not_lost")),
    _c(28, "Événements enregistrés avant l'envoi", "P0", "reliability",
       (LP + "test_planned_entry_ambiguous_buy_halts_then_is_adopted",
        "tests/test_donnees.py::test_every_paper_trade_traces_back_to_its_data")),
    _c(29, "Reprise après plantage", "P0", "reliability",
       (LE + "test_reconcile_recovers_lost_context",
        "tests/test_autonomy.py::test_supervisor_restarts_after_crash_then_stops_with_the_bot")),
    _c(30, "Reprise depuis une sauvegarde", "P1", "reliability",
       ("tests/test_donnees.py::test_backup_is_restored_for_real_and_reported",
        "tests/test_report.py::test_database_backup_is_verified_and_rotated")),
    _c(31, "Rejeu des événements", "P1", "reliability", (TG + "test_replay_matches_backtest",)),
    _c(32, "Bascule vers un autre courtier", "P1", "broker",
       na="un seul courtier (Binance Spot) : une panne de Binance diffère les achats et laisse les stops posés chez lui"),
    _c(33, "Isolation de sécurité", "P0", "security",
       ("tests/test_panel.py::test_password_required_from_the_network", RC + "test_paper_mode_never_uses_the_api_keys")),
    _c(34, "Secrets protégés", "P0", "security",
       ("tests/test_core.py::test_scrub_secrets", "tests/test_alerts.py::test_build_notifier_from_env_keeps_secrets_out_of_errors")),
    _c(35, "Changement des clés", "P1", "security",
       (RC + "test_set_keys_refuses_an_exposed_key", RC + "test_set_keys_saves_only_keys_accepted_by_binance")),
    _c(36, "Horloge synchronisée", "P0", "reliability",
       (RC + "test_sync_puts_bot_and_signed_orders_on_binance_time", RC + "test_bot_syncs_clock_at_boot_then_hourly")),
    _c(37, "Auditabilité", "P0", "observability", (C + "test_audit_chain_detects_any_change",)),
    _c(38, "Traçabilité", "P0", "observability",
       ("tests/test_donnees.py::test_every_paper_trade_traces_back_to_its_data", C + "test_live_buys_are_authorized_and_traced")),
    _c(39, "Délai d'exécution mesuré", "P1", "performance", (DP + "test_execution_quality_is_measured",)),
    _c(40, "Glissement mesuré", "P1", "observability", (DP + "test_execution_quality_is_measured",)),
    _c(41, "Écart de mise en œuvre mesuré", "P1", "observability", (DP + "test_execution_quality_is_measured",)),
    _c(42, "Incidents détectés", "P0", "observability",
       (TG + "test_live_boot_refuses_foreign_bot_orders", LE + "test_orphan_detected_then_cleared")),
    _c(43, "Incidents signalés", "P1", "observability", ("tests/test_alerts.py::test_hub_levels_dedup_and_failure_isolation",)),
    _c(44, "Mode sûr", "P0", "safety", (C + "test_safe_mode_stops_buys_but_not_sales",)),
    _c(45, "Arrêt d'urgence immédiat", "P0", "safety",
       (TG + "test_kill_switch_blocks_entries", "tests/test_analyse.py::test_stress_scenarios_and_the_kill_switch")),
    _c(46, "Comptes isolés", "P0", "security",
       ("tests/test_libre.py::test_the_free_bot_runs_beside_the_main_bot_without_changing_it",)),
    _c(47, "Stratégies isolées", "P1", "security",
       ("tests/test_libre.py::test_the_free_bot_runs_beside_the_main_bot_without_changing_it",
        LE + "test_second_instance_does_not_adopt_foreign_position")),
    _c(48, "Routage entre courtiers", "P1", "broker", na="un seul courtier (Binance Spot)"),
    _c(49, "Courtier non autorisé bloqué", "P0", "broker", (RC + "test_make_binance_spot_only_long_timeout_clock_corrected",)),
    _c(50, "Instrument non autorisé bloqué", "P0", "risk_policy",
       (C + "test_gate_refuses_with_reasons", PE + "test_exchange_rules_are_checked_in_live_only")),
    _c(51, "Aucune promotion automatique du paper au réel", "P0", "safety",
       (DP + "test_the_bot_never_promotes_itself", TG + "test_live_requires_confirmation")),
    _c(52, "Plafonds du réel contrôlé", "P0", "risk_policy", (DP + "test_the_stage_limits_only_apply_in_live_and_only_reduce",)),
    _c(53, "Accord humain", "P0", "safety",
       ("tests/test_autorisation.py::test_conditions_must_all_hold_exactly", TG + "test_live_requires_confirmation")),
    _c(54, "Rapprochement à la reprise", "P0", "reconciliation",
       (TG + "test_live_boot_adopts_with_explicit_recovery", LE + "test_reconcile_records_oco_fill_while_down")),
    _c(55, "Protection contre les exécutions en double", "P0", "reconciliation", (LE + "test_smart_buy_counts_fills_once",)),
    _c(56, "Événements dans le désordre", "P1", "reconciliation", (LE + "test_tp_fills_during_stop_move_no_new_protection",)),
    _c(57, "Réponse de Binance validée", "P0", "broker",
       ("tests/test_fake_binance.py::test_rejects_invalid_spot_types_and_missing_params",
        LP + "test_planned_entry_rejected_by_binance_leaves_the_bot_flat")),
    _c(58, "Risque recalculé après l'exécution", "P1", "risk_policy",
       ("tests/test_moteur_risque.py::test_the_journal_keeps_every_assessment",)),
    _c(59, "Piste d'audit complète", "P0", "observability",
       (C + "test_audit_chain_detects_any_change", "tests/test_donnees.py::test_every_paper_trade_traces_back_to_its_data")),
    _c(60, "Aucun contournement d'un P0", "P0", "safety", (PE + "test_chaos_ten_thousand_requests_and_a_kill_switch",)),
)


def _measures(gcfg: Any, state: Dict[str, Any], rt: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    """(état, détail) des critères mesurés sur le code, les réglages et le bot."""
    out: Dict[str, Tuple[str, str]] = {}
    route = (("PASS", "une seule route d'achat vers Binance, après la porte et la validation finale") if rt["ok"]
             else ("FAIL", "route d'achat hors de la porte : " + ", ".join(rt["calls"])))
    out["AC-001"] = out["AC-011"] = out["AC-060"] = route
    stage = current(gcfg)
    out["AC-051"] = ("PASS", f"palier {STAGE_FR[stage]}, lu dans vos réglages ; le bot ne change jamais de palier seul")
    q = state.get("qualite_execution") or {}
    lat = q.get("latency_ms") or []
    out["AC-039"] = (("PASS", f"{len(lat)} exécution(s) réelle(s) : délai médian {fr(statistics.median(lat), '.0f')} ms")
                     if lat else ("UNKNOWN", "mesuré seulement en réel : sur le testnet puis au réel contrôlé"))
    sb = q.get("shortfall_bps") or []
    trades = state.get("trades") or []
    noted = [t for t in trades if "slippage_pct" in t]
    out["AC-040"] = (("PASS", f"glissement noté sur {len(noted)} sortie(s)" +
                      (" (modèle du paper ; le réel est noté de même)" if stage == "PAPER" else ""))
                     if trades and len(noted) == len(trades) else
                     ("UNKNOWN", "aucune sortie encore") if not trades else ("FAIL", "sortie sans glissement noté"))
    out["AC-041"] = (("PASS", describe_quality(q)) if sb else
                     ("UNKNOWN", "aucun achat mesuré depuis l'étape 17 (écart entre le prix payé et le cours de décision)"))
    db = str(getattr(gcfg, "db_file", ""))
    mode = getattr(gcfg, "run_mode", "paper")
    out["AC-046"] = (("PASS", "base en mémoire (essai) : isolée") if db == ":memory:" else
                     ("PASS", "une base par mode (paper, réel) : " + os.path.basename(db))
                     if mode in os.path.basename(db) else ("FAIL", f"base {os.path.basename(db)} sans son mode"))
    return out


def band(score: float) -> str:
    """Bande de la note (§55), la même que celle de la porte d'exécution."""
    return porte_examen.band(score)


def evaluate(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None,
             acceptance: Optional[Dict[str, Any]] = None, gate_exam: Optional[Dict[str, Any]] = None,
             live_gate: Optional[Dict[str, Any]] = None, root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Le palier, les portes, les 60 critères, la note et le verdict
    (READY_FOR_STAGED_LIVE_EXECUTION ou NOT_READY ; jamais une production
    sans restriction)."""
    now = now or datetime.now(timezone.utc)
    rt = porte_examen.routes(root)
    rows, fam_scores, score, p0_fail = porte_examen.grade(CRITERIA, _measures(gcfg, state, rt), FAMILY_WEIGHTS, root)
    lad = ladder(gcfg, state, now, acceptance, gate_exam, live_gate, root)
    ready = not p0_fail and score >= 95
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "rows": rows, "family_scores": fam_scores, "score": round(score, 1), "band": band(score),
            "p0_failures": p0_fail, "ladder": lad, "quality": state.get("qualite_execution") or {},
            "status": "READY_FOR_STAGED_LIVE_EXECUTION" if ready else "NOT_READY"}


def report_of(r: Dict[str, Any]) -> LiveDeploymentReport:
    """Le verdict au format du contrat LiveDeploymentReport.v1."""
    counts = {s: sum(1 for x in r["rows"] if x["status"] == s) for s in acceptation.STATUS_FR}
    lad = r["ladder"]
    return LiveDeploymentReport(report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], stage=lad["stage"],
                                next_stage=lad["next"], next_gate_open=lad["next_gate_open"],
                                missing=tuple(lad["missing"]), status=r["status"], readiness_score=r["score"],
                                band=r["band"], p0_failures=tuple(r["p0_failures"]), passed=counts["PASS"],
                                failed=counts["FAIL"], unknown=counts["UNKNOWN"],
                                not_applicable=counts["NOT_APPLICABLE"], engine_version=VERSION)


def describe(gcfg: Any) -> str:
    """Une phrase sur le palier en vigueur et ses plafonds (léger : pour le
    rapport, Rachelle et le panneau)."""
    stage = current(gcfg)
    lim = LIMITS.get(stage)
    out = f"palier : {STAGE_FR[stage]}"
    if lim:
        out += (f" ; plafonds : {lim['buys_day']} achats par jour, {fr(lim['daily_pct'] * 100, '.0f')} % du capital "
                "acheté par jour, capital confié au bot fixé par vous")
    nxt = STAGES[STAGES.index(stage) + 1] if stage != "PRODUCTION" else ""
    if nxt:
        out += f" ; palier suivant : {STAGE_FR[nxt]}, seulement par votre réglage"
    return out


def render(r: Dict[str, Any]) -> str:
    """docs/DEPLOIEMENT.md : palier, portes, verdict, familles, critères."""
    lad = r["ladder"]
    ready = r["status"] == "READY_FOR_STAGED_LIVE_EXECUTION"
    lines = ["# Exécution réelle par paliers (critères AC-001 à AC-060)", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py deploiement` (étape 17 du prompt maître, "
             "[`DEPLOIEMENT_REEL.md`](DEPLOIEMENT_REEL.md)). Aucune promotion automatique : seul un réglage que vous "
             "changez fait monter d'un palier.", "", "## Palier", "",
             f"- En vigueur : **{STAGE_FR[lad['stage']]}**.",
             (f"- Suivant : {STAGE_FR[lad['next']]} ; porte " + ("ouverte (à vous de décider)." if lad["next_gate_open"]
                                                                else "fermée : " + " ; ".join(lad["missing"]) + "."))
             if lad["next"] else "- Dernier palier atteint.",
             f"- Exécutions : {describe_quality(r['quality'])}.", "", "| Palier | état | porte pour y entrer |",
             "| --- | --- | --- |"]
    for row in lad["rows"]:
        gate = " ; ".join(("✔ " if c["ok"] else "✘ ") + c["what"] for c in row["gate"]) or "—"
        lines.append(f"| {row['label']} | {row['state']} | {gate} |")
    lines += ["", "## Verdict", "",
              f"- **{'Prête pour un réel par paliers' if ready else 'PAS PRÊTE'}** ({r['status']}) : jamais une "
              "production sans restriction ; chaque palier reste à votre décision.",
              f"- Note {fr(r['score'], '.1f')}/100 ({porte_examen.BAND_FR[r['band']]}) ; critères P0 non satisfaits : "
              f"{len(r['p0_failures'])}" + (f" ({', '.join(r['p0_failures'])})" if r["p0_failures"] else "") + ".",
              "", "## Familles", "", "| Famille | poids | note |", "| --- | --- | --- |"]
    for fam, w in FAMILY_WEIGHTS.items():
        s = r["family_scores"].get(fam)
        lines.append(f"| {FAMILY_FR[fam]} | {fr(w * 100, '.0f')} % | {fr(s, '.0f') if s is not None else '—'} |")
    lines += ["", "## Critères", "", "| Critère | priorité | état | preuve |", "| --- | --- | --- | --- |"]
    for x in r["rows"]:
        lines.append(f"| {x['id']} {x['title']} | {x['priority']} | {acceptation.STATUS_FR[x['status']]} | "
                     f"{x['detail']} |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `deploiement` : palier, portes (paper accepté, porte du réel,
    porte d'exécution), examen ; écrit avec --out."""
    from . import chantiers
    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Exécution réelle par paliers (AC-001 à AC-060)")
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    ap.add_argument("--out", default="")
    ap.add_argument("--chaos", type=int, default=2_000)
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    state = read_state(g.db_file) or {}
    try:
        close, volume = load_history(args.cache, list(g.universe), max_age_days=None)
    except (OSError, ValueError):
        close, volume = None, None
    acc = acceptation.evaluate(g, state, close=close, volume=volume, params=params_for(g))
    exam = porte_examen.evaluate(g, state, chaos_n=args.chaos)
    try:
        lg = chantiers.live_gate(g, state)
    except Exception as e:              # mesure impossible : porte fermée
        lg = {"open": False, "missing": [f"mesure impossible ({type(e).__name__})"]}
    r = evaluate(g, state, acceptance=acc, gate_exam=exam, live_gate=lg)
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
    return 0 if r["status"] == "READY_FOR_STAGED_LIVE_EXECUTION" else 1
