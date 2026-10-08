"""
Critères d'acceptation mesurables du paper (prompt maître, étape 13 ;
docs/ACCEPTATION_PAPER.md) : AC-001 à AC-044, chacun avec sa priorité (P0
bloquant), sa preuve et son état, mesurés sur le bot lui-même ou prouvés par
un test du dépôt ; note de préparation pondérée, période d'observation,
écart entre le paper et le backtest de la même période, gouvernance ; puis
le verdict : ACCEPTED (prêt pour le moteur de politique) ou BLOCKED (à
corriger, avec la liste de ce qui manque).

Règles :
- un seul P0 non satisfait suffit à bloquer, quelle que soit la note ;
- une mesure impossible n'est jamais une réussite (UNKNOWN compte comme un
  échec pour un P0) ;
- « sans objet » se justifie toujours, et ne compte pas dans la note ;
- le verdict n'autorise rien : le paper valide la préparation, jamais le
  réel (POLICY → AUTHORIZATION → EXECUTION GATE → LIVE reste à franchir).

    python trendguard_bot.py acceptation                 # le verdict du jour
    python trendguard_bot.py acceptation --out docs/ACCEPTATION.md
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import statistics
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd

import v29

from . import audit, donnees, porte
from . import trend_strategy as ts
from .contrats import ContractError, OrderIntent, PaperAcceptanceReport
from .texte import fr

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "acceptation-paper-1.0.0"
FAMILY_WEIGHTS = {"safety": 0.20, "accounting": 0.15, "execution": 0.15, "risk": 0.15, "data": 0.10,
                  "reproducibility": 0.10, "observability": 0.05, "security": 0.05, "performance": 0.05}
FAMILY_FR = {"safety": "sécurité", "accounting": "comptabilité", "execution": "exécution", "risk": "risque",
             "data": "données", "reproducibility": "reproductibilité", "observability": "observabilité",
             "security": "sécurité informatique", "performance": "performance"}
BANDS = ((95.0, "PAPER_READY"), (90.0, "PAPER_READY_CANDIDATE"), (80.0, "VALIDATING"), (70.0, "DEVELOPMENT"),
         (0.0, "REJECTED"))
BAND_FR = {"PAPER_READY": "prêt", "PAPER_READY_CANDIDATE": "presque prêt", "VALIDATING": "en validation",
           "DEVELOPMENT": "en développement", "REJECTED": "rejeté"}
STATUS_FR = {"PASS": "conforme", "FAIL": "non conforme", "UNKNOWN": "non mesurable", "NOT_APPLICABLE": "sans objet"}
OBSERVATION_DAYS = 30           # minimum (§48) ; 90 recommandés
OBSERVATION_DAYS_RECOMMENDED = 90
EVENTS_MIN = 100
EVENTS_MIN_RARE = 30            # stratégie peu fréquente, justifiée (§48)
RARE_JUSTIFICATION = ("stratégie de tendance journalière : environ 50 trades par an au backtest (430 depuis 2018), "
                      "100 événements demanderaient près d'un an")
LATENCY_GATE_MS = 50.0          # contrôle du risque local, p95 (§31)
LATENCY_KILL_MS = 100.0         # blocage par l'arrêt d'urgence (§25)


@dataclass(frozen=True)
class Criterion:
    """Un critère d'acceptation : identifiant, titre, priorité, famille de la
    note, tests du dépôt qui le prouvent, raison s'il est sans objet."""
    ac_id: str
    title: str
    priority: str
    family: str
    tests: Tuple[str, ...] = ()
    not_applicable: str = ""


def _c(n: int, title: str, prio: str, fam: str, tests: Sequence[str] = (), na: str = "") -> Criterion:
    return Criterion(f"AC-{n:03d}", title, prio, fam, tuple(tests), na)


T = "tests/test_trendguard.py::"
CRITERIA: Tuple[Criterion, ...] = (
    _c(1, "Démarrage contrôlé (compte, capital, essai daté)", "P0", "safety",
       (T + "test_a_new_paper_capital_starts_a_new_trial_instead_of_a_false_emergency_stop",)),
    _c(2, "Aucun accès financier réel", "P0", "safety",
       ("tests/test_real_conditions.py::test_paper_mode_never_uses_the_api_keys",
        "tests/test_chantiers.py::test_a_closed_live_gate_refuses_every_real_buy")),
    _c(3, "États du compte et transitions", "P0", "safety",
       ("tests/test_core.py::test_daily_dd_halts_then_resets_next_day", "tests/test_contrats.py::test_safe_mode_switch",
        T + "test_kill_switch_blocks_entries")),
    _c(4, "Journal (ledger) en ajout seul", "P0", "accounting",
       ("tests/test_donnees.py::test_every_paper_trade_traces_back_to_its_data",)),
    _c(5, "Conservation comptable", "P0", "accounting", ("tests/test_backtest_paper.py::test_backtest_accounting_identity",)),
    _c(6, "Notionnel de chaque position", "P0", "accounting", ("tests/test_core.py::test_position_size_risk_budget",)),
    _c(7, "Résultat réalisé de chaque trade", "P0", "accounting", (T + "test_backtest_accounting_and_risk",)),
    _c(8, "Valorisation au marché tenue à jour", "P1", "accounting"),
    _c(9, "Prix moyen des exécutions", "P0", "accounting",
       ("tests/test_live_execution.py::test_smart_buy_partial_fill_opens_on_filled_quantity",
        "tests/test_live_execution.py::test_thin_book_entry_uses_real_average_price")),
    _c(10, "Cycle de vie des ordres", "P0", "execution",
       ("tests/test_fake_binance.py::test_rejects_invalid_spot_types_and_missing_params",
        "tests/test_contrats.py::test_every_buy_goes_through_the_gate_and_changes_nothing")),
    _c(11, "Idempotence : jamais deux fois le même ordre", "P0", "execution",
       ("tests/test_donnees.py::test_restart_never_duplicates_an_order",
        "tests/test_fake_binance.py::test_duplicate_open_client_id_rejected")),
    _c(12, "Exécutions partielles", "P0", "execution",
       ("tests/test_live_execution.py::test_oco_partial_fill_is_booked_and_remainder_reprotected",
        "tests/test_fake_binance.py::test_limit_buy_partial_fill_releases_only_used_quote")),
    _c(13, "Glissement mesuré sur chaque exécution", "P0", "execution"),
    _c(14, "Coûts de transaction", "P1", "execution", ("tests/test_fake_binance.py::test_bnb_fee_mode_on_both_sides",)),
    _c(15, "Horodatage et ordre des événements", "P0", "execution",
       ("tests/test_real_conditions.py::test_bot_syncs_clock_at_boot_then_hourly",)),
    _c(16, "Aucune information future", "P0", "data",
       ("tests/test_contrats_donnees.py::test_a_decision_cannot_look_into_the_future", T + "test_bot_ignores_future_candles",
        "tests/test_finance.py::test_no_indicator_looks_into_the_future")),
    _c(17, "Carnet d'ordres simulé", "P1", "execution",
       na="mode carnet non activé : ordres au marché sur bougies journalières ; le carnet est seulement lu pour "
          "différer un achat (apprentissage)"),
    _c(18, "Liquidité minimale", "P0", "risk", ("tests/test_moteur_strategie.py::test_an_unknown_value_never_makes_a_condition_true",)),
    _c(19, "Contrôle du risque avant chaque achat", "P0", "risk",
       ("tests/test_contrats.py::test_every_buy_goes_through_the_gate_and_changes_nothing",)),
    _c(20, "Limites de risque bloquantes", "P0", "risk",
       ("tests/test_contrats.py::test_gate_refuses_with_reasons", "tests/test_anticipation.py::test_full_risk_budget_blocks_every_buy")),
    _c(21, "Levier", "P0", "risk"),
    _c(22, "Marge", "P0", "risk", na="Binance Spot sans marge ni emprunt"),
    _c(23, "Arrêt d'urgence (kill switch)", "P0", "safety",
       (T + "test_kill_switch_blocks_entries", "tests/test_analyse.py::test_stress_scenarios_and_the_kill_switch")),
    _c(24, "Mode sûr", "P0", "safety",
       ("tests/test_contrats.py::test_safe_mode_stops_buys_but_not_sales",
        "tests/test_moteur_risque.py::test_the_gate_of_execution_refuses_without_a_risk_assessment")),
    _c(25, "Reproductibilité déterministe", "P0", "reproducibility", (T + "test_paper_bot_matches_backtest_exactly",)),
    _c(26, "Reproductibilité des tirages (graine)", "P1", "reproducibility",
       ("tests/test_moteur_quant.py::test_lab_is_reproducible_and_follows_its_contract",)),
    _c(27, "Backtest et paper comparés", "P1", "reproducibility"),
    _c(28, "Attendu et observé (calibrage)", "P1", "performance"),
    _c(29, "Latence du contrôle", "P1", "performance"),
    _c(30, "Disponibilité ≥ 99,9 %", "P1", "performance"),
    _c(31, "Traçabilité de chaque ordre", "P0", "observability",
       ("tests/test_donnees.py::test_every_paper_trade_traces_back_to_its_data",)),
    _c(32, "Audit complet", "P0", "observability", ("tests/test_contrats.py::test_audit_chain_detects_any_change",)),
    _c(33, "Comptes isolés", "P0", "safety"),
    _c(34, "Portefeuilles isolés", "P0", "safety",
       ("tests/test_libre.py::test_the_free_bot_runs_beside_the_main_bot_without_changing_it",)),
    _c(35, "Qualité des données avant usage", "P0", "data", ("tests/test_analyse.py::test_data_quality_score_names_each_defect",)),
    _c(36, "Donnée manquante jamais transformée en zéro", "P0", "data",
       ("tests/test_contrats_donnees.py::test_money_and_unknown_values",
        "tests/test_moteur_strategie.py::test_an_unknown_value_never_makes_a_condition_true")),
    _c(37, "Scénarios extrêmes", "P0", "safety",
       ("tests/test_analyse.py::test_stress_scenarios_and_the_kill_switch",
        "tests/test_moteur_risque.py::test_history_replay_never_blocks_a_healthy_portfolio")),
    _c(38, "Pannes de composants (chaos)", "P0", "safety",
       ("tests/test_fake_binance.py::test_fault_injection", "tests/test_live_execution.py::test_reconcile_fails_closed_on_balance_error",
        "tests/test_autonomy.py::test_supervisor_restarts_after_crash_then_stops_with_the_bot")),
    _c(39, "Permissions", "P0", "security",
       ("tests/test_panel.py::test_password_required_from_the_network",)),
    _c(40, "Entrées malveillantes", "P0", "security",
       ("tests/test_cognitif.py::test_rachelle_refuses_instruction_injection",
        "tests/test_replay_animation.py::test_render_html_data_cannot_inject_markup",
        "tests/test_agents.py::test_bus_only_known_members_and_no_replay")),
    _c(41, "Couverture des tests", "P1", "observability"),
    _c(42, "Invariants (tests de propriétés)", "P0", "accounting",
       ("tests/test_backtest_paper.py::test_backtest_accounting_identity", "tests/test_backtest_paper.py::test_paper_partial_r_is_additive")),
    _c(43, "Tests de régression à chaque envoi", "P0", "reproducibility"),
    _c(44, "Rejeu déterministe", "P0", "reproducibility",
       (T + "test_replay_matches_backtest", "tests/test_analyse.py::test_registry_notes_and_replays_an_experiment")),
)


# ══════════════════════════════════════════════════════════════════════
# Preuves
# ══════════════════════════════════════════════════════════════════════

def test_evidence(refs: Sequence[str], root: pathlib.Path = ROOT) -> Tuple[bool, List[str]]:
    """Les tests cités existent-ils dans le dépôt ? (GitHub les exécute à
    chaque envoi ; une preuve introuvable n'est jamais comptée.)"""
    missing = []
    for ref in refs:
        path, _sep, name = ref.partition("::")
        file = root / path
        try:
            text = file.read_text(encoding="utf-8")
        except OSError:
            missing.append(ref)
            continue
        if not re.search(rf"^def {re.escape(name)}\(", text, re.M):
            missing.append(ref)
    return not missing, missing


def _parse(iso: Any) -> Optional[datetime]:
    return v29._parse_iso(str(iso)) if iso else None


def _timed(fn: Callable[[], Any], runs: int = 200) -> float:
    """p95 de la durée d'un appel, en millisecondes."""
    out = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return sorted(out)[int(0.95 * (runs - 1))]


def _sample_check(halted: bool, now: datetime) -> Any:
    day = now.strftime("%Y-%m-%d")
    pf = porte.Portfolio(equity=1000.0, cash=1000.0, invested=0.0, held_risk=(), risk_mult=1.0, expected_day=day,
                         universe=frozenset({"eth"}), allowed=frozenset({"eth"}), halted=halted)
    intent = OrderIntent(asset="eth", qty=1.0, entry=100.0, stop=90.0, cost=100.1, risk_quote=10.2, decision_day=day)
    return porte.check(intent, pf, ts.TrendParams(), now)


# ══════════════════════════════════════════════════════════════════════
# Mesures sur le bot
# ══════════════════════════════════════════════════════════════════════

def _measures(ctx: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    """(état, détail) des critères mesurés sur le bot lui-même ; chaque
    mesure dit ce qu'elle a vu."""
    st, g, p, now = ctx["state"], ctx["gcfg"], ctx["params"], ctx["now"]
    paper = st.get("paper") or {}
    holdings = paper.get("holdings") or {}
    trades = st.get("trades") or []
    out: Dict[str, Tuple[str, str]] = {}
    started = _parse(st.get("started_at"))
    start_eq = float(st.get("start_equity") or 0)
    out["AC-001"] = (("PASS", f"essai commencé le {started:%Y-%m-%d}, capital de départ connu")
                     if started and start_eq > 0 and isinstance(paper.get("cash"), (int, float))
                     else ("FAIL", "essai sans date de départ ou sans capital"))
    mode = getattr(g, "run_mode", "paper")
    out["AC-002"] = (("PASS", "mode paper : argent fictif, les clés Binance ne servent pas")
                     if mode == "paper" else ("FAIL", f"mode {mode} : ce n'est pas un essai paper"))
    halted = bool(st.get("halted"))
    out["AC-003"] = (("PASS", "arrêt d'urgence déclenché, raison notée" if halted else "compte actif")
                     if not halted or st.get("halt_reason") else ("FAIL", "arrêt d'urgence sans raison notée"))
    jv = ctx.get("journal")
    out["AC-004"] = ((("PASS" if jv["ok"] else "FAIL"), donnees.describe(jv)) if jv
                     else ("UNKNOWN", "journal financier introuvable"))
    cash = float(paper.get("cash", 0.0))
    cost = sum(float(h.get("cost", 0.0)) for h in holdings.values())
    gap = abs(cash + cost - (start_eq + float(st.get("realized_pnl_total") or 0.0)))
    out["AC-005"] = (("PASS" if gap <= 0.01 else "FAIL"),
                     f"liquidités + coût des positions − (capital de départ + résultat réalisé) = {fr(gap, '.4f')}")
    bad = [a for a, h in holdings.items() if abs(float(h["cost"]) - float(h["qty"]) * float(h["entry"]) * (1 + p.fee))
           > 1e-6 * max(1.0, float(h["cost"]))]
    out["AC-006"] = (("PASS", f"{len(holdings)} position(s) : coût = quantité × prix × (1 + frais)") if not bad
                     else ("FAIL", "coût incohérent : " + ", ".join(a.upper() for a in bad)))
    wrong = []
    for t in trades:
        if not all(k in t for k in ("qty", "entry", "exit", "pnl")):
            continue
        ref = float(t["qty"]) * float(t["exit"]) * (1 - p.fee - p.slippage) - float(t["qty"]) * float(t["entry"]) * (
            1 + p.fee)
        if abs(ref - float(t["pnl"])) > max(0.01, 1e-6 * abs(ref)) and not t.get("partial"):
            wrong.append(t.get("asset", "?"))
    out["AC-007"] = (("PASS", f"{len(trades)} trade(s) clos recalculé(s) à 0,01 près") if not wrong
                     else ("FAIL", "résultat recalculé différent : " + ", ".join(a.upper() for a in wrong)))
    last = st.get("last_cycle_ts")
    age_h = (now.timestamp() - float(last)) / 3600 if last else None
    out["AC-008"] = (("PASS", f"capital revalorisé il y a {fr(age_h * 60, '.0f')} minute(s)") if age_h is not None
                     and age_h <= 2 else ("FAIL", "bot à l'arrêt : pas de revalorisation depuis plus de 2 heures")
                     if age_h is not None else ("UNKNOWN", "aucun cycle noté"))
    no_slip = [t.get("asset", "?") for t in trades if "slippage_pct" not in t]
    out["AC-013"] = (("PASS", f"glissement noté sur {len(trades)} sortie(s) ; achats au cours + "
                      f"{fr(p.slippage * 100, 'g')} % (modèle)") if not no_slip
                     else ("FAIL", "glissement absent : " + ", ".join(a.upper() for a in no_slip)))
    order = [t.get("asset", "?") for t in trades
             if _parse(t.get("entry_date")) and _parse(t.get("date")) and _parse(t["date"]) < _parse(t["entry_date"])]
    future = [a for a, h in holdings.items() if _parse(h.get("entry_date")) and _parse(h["entry_date"]) > now]
    out["AC-015"] = (("PASS", "aucune sortie avant son achat, aucun achat daté dans le futur") if not order + future
                     else ("FAIL", "ordre des dates impossible : " + ", ".join(x.upper() for x in order + future)))
    out["AC-018"] = (("PASS", f"volume moyen de 30 jours d'au moins {fr(p.min_volume_usd / 1e6, '.0f')} millions de "
                      "dollars exigé avant tout achat") if p.min_volume_usd > 0 else ("FAIL", "filtre de liquidité éteint"))
    out["AC-019"] = out["AC-004"] if jv else ("UNKNOWN", "journal financier introuvable")
    risk_used = sum(float(h.get("risk_quote", 0.0)) for h in holdings.values())
    eq = float(st.get("last_equity") or start_eq or 0.0)
    limits_ok = len(holdings) <= p.max_positions and (eq <= 0 or risk_used <= p.max_total_risk * eq + 1e-9)
    out["AC-020"] = (("PASS" if limits_ok else "FAIL"),
                     f"{len(holdings)} position(s) pour {p.max_positions} au plus, risque engagé "
                     f"{fr(risk_used / eq * 100 if eq > 0 else 0, '.1f')} % pour {fr(p.max_total_risk * 100, '.0f')} %")
    out["AC-021"] = (("PASS", f"exposition {fr(cost / eq * 100 if eq > 0 else 0, '.0f')} % du capital, jamais de levier")
                     if cash >= -1e-6 else ("FAIL", "liquidités négatives : levier"))
    kill = float(getattr(g, "kill_drawdown", 0.0) or 0.0)
    kill_ms = _timed(lambda: _sample_check(True, now), 50)
    blocked = _sample_check(True, now).status == "EMERGENCY_BLOCK"
    out["AC-023"] = (("PASS" if 0 < kill < 1 and blocked and kill_ms <= LATENCY_KILL_MS else "FAIL"),
                     f"arrêt d'urgence à −{fr(kill * 100, '.0f')} % ; achat bloqué en {fr(kill_ms, '.2f')} ms")
    sm = porte.safe_mode(g)
    out["AC-024"] = ("PASS", "mode sûr " + ("actif" if sm.active else "prêt") + " ; sans évaluation du risque valide, "
                     "aucun achat")
    gate_ms = _timed(lambda: _sample_check(False, now))
    out["AC-029"] = (("PASS" if gate_ms <= LATENCY_GATE_MS else "FAIL"),
                     f"contrôle du risque d'un achat : {fr(gate_ms, '.2f')} ms au 95e centile "
                     f"(cible {fr(LATENCY_GATE_MS, '.0f')} ms)")
    up = (st.get("uptime") or {})
    from . import uptime
    week = uptime.summary(up, st.get("last_cycle_ts"), st.get("stopped_at"), now.timestamp()).get("week_pct")
    out["AC-030"] = (("UNKNOWN", "historique de disponibilité trop court") if week is None else
                     (("PASS" if week >= 99.9 else "FAIL"), f"{fr(week, '.1f')} % sur 7 jours (cible 99,9 %)"))
    lin = ctx.get("lineage")
    if not jv or lin is None:
        out["AC-031"] = ("UNKNOWN", "journal financier introuvable")
    elif lin["untraced"]:
        out["AC-031"] = ("FAIL", f"{len(lin['untraced'])} trade(s) sans lignée complète : " + ", ".join(lin["untraced"]))
    else:
        out["AC-031"] = ("PASS", f"{lin['traced']} trade(s) remontent à leur ordre, leur contrôle du risque et leur "
                         "décision" + (" ; antérieurs au journal financier, signalés et exclus : "
                                       + ", ".join(lin["before"]) if lin["before"] else ""))
    av = ctx.get("audit")
    out["AC-032"] = ((("PASS" if av["ok"] else "FAIL"), audit.describe(av)) if av else ("UNKNOWN", "audit introuvable"))
    db = str(getattr(g, "db_file", ""))
    out["AC-033"] = (("PASS", "base en mémoire (essai) : isolée") if db == ":memory:" else
                     ("PASS", "une base par mode (paper, testnet, réel) : " + os.path.basename(db))
                     if mode in os.path.basename(db) else ("FAIL", f"base {os.path.basename(db)} sans son mode"))
    out["AC-034"] = ("PASS", "bot libre dans sa propre base (savoir) : il ne touche jamais le portefeuille principal")
    q = st.get("qualite") or {}
    out["AC-035"] = (("PASS" if (q.get("score") or 0) >= 50 else "FAIL",
                      f"qualité des données du {q.get('day')} : {fr(q.get('score') or 0, '.0f')}/100 (achat refusé "
                      "sous 50)") if q else ("UNKNOWN", "pas encore de note des données"))
    n_tests = sum(len(re.findall(r"^def test_", f.read_text(encoding="utf-8"), re.M))
                  for f in (ROOT / "tests").glob("test_*.py")) if (ROOT / "tests").is_dir() else 0
    out["AC-041"] = ("UNKNOWN", f"{n_tests} tests dans le dépôt ; la couverture en lignes n'est pas mesurée (outil "
                     "de couverture absent)")
    ci = ROOT / ".github" / "workflows" / "checks.yml"
    out["AC-043"] = (("PASS", "tous les tests rejoués par GitHub à chaque envoi (.github/workflows/checks.yml)")
                     if ci.exists() else ("UNKNOWN", "contrôles GitHub introuvables sur ce PC"))
    return out


def _lineage(j: Any, state: Dict[str, Any]) -> Dict[str, Any]:
    """Chaque trade du journal remonte-t-il à son achat (ordre, contrôle du
    risque, décision) ? Les achats faits avant la première décision du
    journal financier (sa mise en service) sont signalés à part : ils ne
    pouvaient pas y être tracés."""
    first = j.conn.execute("SELECT MIN(created_at) FROM fin_decisions").fetchone()[0]
    start = _parse(first) if first else None
    traced, untraced, before = 0, [], []
    for row in j.conn.execute("SELECT id, asset, opened_at FROM fin_trades").fetchall():
        try:
            lin = j.lineage(row["id"])
        except Exception:                # lignée illisible : jamais une réussite
            untraced.append(f"{row['asset'].upper()} (illisible)")
            continue
        if lin.get("risk_check") and lin.get("decision"):
            traced += 1
        elif start and _parse(row["opened_at"]) and _parse(row["opened_at"]) < start:
            before.append(f"{row['asset'].upper()} acheté le {str(row['opened_at'])[:10]}")
        else:
            untraced.append(row["asset"].upper())
    for a, h in ((state.get("paper") or {}).get("holdings") or {}).items():
        if start and _parse(h.get("entry_date")) and _parse(h["entry_date"]) < start:
            before.append(f"{a.upper()} (ouverte) achetée le {str(h['entry_date'])[:10]}")
    return {"traced": traced, "untraced": untraced, "before": before}


def divergence(state: Dict[str, Any], close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams) -> Dict[str, Any]:
    """Le paper contre le backtest de la même période (§29, §50) : achats et
    sorties de l'essai rejoués par la boucle de backtest (même capital, même
    départ, mêmes réglages) ; chaque écart classé et expliqué quand c'est
    possible (stop de secours touché dans la journée, crypto non choisie,
    décision du lendemain)."""
    started = _parse(state.get("started_at"))
    if started is None or close is None or not len(close):
        return {"status": "UNKNOWN", "reason": "essai sans date de départ ou cours absents"}
    first = (started + timedelta(minutes=5)).strftime("%Y-%m-%d")
    last = str(close.index[-1].date())
    if first > last:
        return {"status": "UNKNOWN", "reason": "aucune bougie close depuis le début de l'essai"}
    bought: List[Tuple[str, str]] = []

    def record(i: int, plans: List[Dict[str, Any]], _h: Any, _eq: float) -> List[Dict[str, Any]]:
        bought.extend((pl["asset"], str(close.index[i].date())) for pl in plans)
        return plans

    res = ts.backtest(close, volume, p, first, last, capital=float(state.get("start_equity") or 100.0),
                      hooks=ts.BacktestHooks(filter_plans=record))
    bt = set(bought)
    paper_entries = {(t["asset"], str((_parse(t["entry_date"]) - timedelta(days=1)).date()))
                     for t in state.get("trades") or [] if _parse(t.get("entry_date"))}
    paper_entries |= {(a, str((_parse(h["entry_date"]) - timedelta(days=1)).date()))
                      for a, h in ((state.get("paper") or {}).get("holdings") or {}).items() if _parse(h.get("entry_date"))}
    eq_bt = float(res.equity.iloc[-1]) if len(res.equity) else None
    only_paper = sorted(paper_entries - bt)
    only_bt = sorted(bt - paper_entries)
    explained, unexplained = [], []
    exits = {t["asset"]: t for t in state.get("trades") or []}
    for a, d in only_paper + only_bt:
        t = exits.get(a)
        if t and t.get("reason") in ("EXCHANGE_STOP", "DISASTER", "STOP_INTRADAY"):
            explained.append(f"{a.upper()} ({d}) : stop de secours touché dans la journée (le backtest ne voit que "
                             "les clôtures)")
        else:
            unexplained.append(f"{a.upper()} ({d})")
    return {"status": "OK", "from": first, "to": last, "paper_entries": len(paper_entries), "backtest_entries": len(bt),
            "matched": len(paper_entries & bt), "only_paper": only_paper, "only_backtest": only_bt,
            "explained": explained, "unexplained": unexplained,
            "paper_equity": float(state.get("last_equity") or 0.0), "backtest_equity": eq_bt}


def _expected_vs_observed(state: Dict[str, Any], p: ts.TrendParams) -> Dict[str, Any]:
    """Attendu contre observé (§30) : glissement des sorties."""
    obs = [float(t["slippage_pct"]) for t in state.get("trades") or [] if "slippage_pct" in t]
    if not obs:
        return {"status": "UNKNOWN", "reason": "aucune sortie encore"}
    exp = p.slippage * 100
    mean = statistics.fmean(obs)
    return {"status": "OK", "metric": "glissement des sorties (%)", "expected": exp, "observed": mean,
            "difference": mean - exp, "relative_error": (mean - exp) / exp if exp else None, "n": len(obs)}


# ══════════════════════════════════════════════════════════════════════
# Évaluation, verdict (§47-53)
# ══════════════════════════════════════════════════════════════════════

def band(score: float) -> str:
    """Bande de la note de préparation (§47)."""
    return next(name for floor, name in BANDS if score >= floor)


def evaluate(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None, close: Optional[pd.DataFrame] = None,
             volume: Optional[pd.DataFrame] = None, params: Optional[ts.TrendParams] = None,
             root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Les 44 critères, la note, l'observation, l'écart au backtest, la
    gouvernance et le verdict (READY_FOR_POLICY ou BLOCKED)."""
    now = now or datetime.now(timezone.utc)
    p = params or getattr(gcfg, "params", None) or ts.TrendParams()
    ctx: Dict[str, Any] = {"state": state, "gcfg": gcfg, "params": p, "now": now}
    path = donnees.path_for(gcfg)
    if path and os.path.exists(path):
        try:
            j = donnees.Journal(path, readonly=True)
            try:
                ctx["journal"] = j.verify()
                ctx["lineage"] = _lineage(j, state)
            finally:
                j.close()
        except Exception as e:           # journal illisible : mesure impossible, jamais une réussite
            ctx["journal"] = {"ok": False, "problems": [f"illisible ({type(e).__name__})"], "counts": {}, "schema": 0}
    apath = audit.path_for(gcfg)
    if apath and os.path.exists(apath):
        ctx["audit"] = audit.verify(apath)
    measured = _measures(ctx)
    div = divergence(state, close, volume, p) if close is not None else {
        "status": "UNKNOWN", "reason": "cours non chargés (python trendguard_bot.py acceptation les charge)"}
    measured["AC-027"] = (("PASS" if not div["unexplained"] else "FAIL",
                           f"{div['matched']} achat(s) identique(s) ; écarts expliqués {len(div['explained'])}, "
                           f"inexpliqués {len(div['unexplained'])}") if div["status"] == "OK"
                          else ("UNKNOWN", div["reason"]))
    cal = _expected_vs_observed(state, p)
    measured["AC-028"] = (("PASS", f"{cal['metric']} : attendu {fr(cal['expected'], '.2f')}, observé "
                           f"{fr(cal['observed'], '.2f')} sur {cal['n']} sortie(s)") if cal["status"] == "OK"
                          else ("UNKNOWN", cal["reason"]))
    rows = []
    for c in CRITERIA:
        if c.not_applicable:
            status, detail = "NOT_APPLICABLE", c.not_applicable
        else:
            m = measured.get(c.ac_id)
            ok_t, missing = test_evidence(c.tests, root) if c.tests else (True, [])
            if m is None:
                status = "PASS" if c.tests and ok_t else "UNKNOWN"
                detail = (f"prouvé par {len(c.tests)} test(s) du dépôt" if c.tests and ok_t else
                          "test(s) introuvable(s) : " + ", ".join(missing) if c.tests else "aucune mesure")
            else:
                status, detail = m
                if c.tests:
                    detail += f" ; {len(c.tests)} test(s) du dépôt" if ok_t else " ; test(s) introuvable(s)"
                    if not ok_t and status == "PASS":
                        status = "UNKNOWN"
        rows.append({"id": c.ac_id, "title": c.title, "priority": c.priority, "family": c.family,
                     "status": status, "detail": detail, "tests": list(c.tests)})
    fam_scores: Dict[str, float] = {}
    for fam in FAMILY_WEIGHTS:
        applicable = [r for r in rows if r["family"] == fam and r["status"] != "NOT_APPLICABLE"]
        if applicable:
            fam_scores[fam] = 100.0 * sum(r["status"] == "PASS" for r in applicable) / len(applicable)
    wsum = sum(FAMILY_WEIGHTS[f] for f in fam_scores)
    score = sum(FAMILY_WEIGHTS[f] * s for f, s in fam_scores.items()) / wsum if wsum else 0.0
    p0_fail = [r["id"] for r in rows if r["priority"] == "P0" and r["status"] in ("FAIL", "UNKNOWN")]
    started = _parse(state.get("started_at"))
    days = (now - started).total_seconds() / 86400 if started else 0.0
    holdings = (state.get("paper") or {}).get("holdings") or {}
    trades = state.get("trades") or []
    events = 2 * len(trades) + len(holdings)
    obs_ok = days >= OBSERVATION_DAYS and events >= EVENTS_MIN_RARE
    validation = (root / "docs" / "VALIDATION.md")
    vtext = validation.read_text(encoding="utf-8") if validation.exists() else ""
    gov = {"strategy": "fiche de la règle en essai paper (moteur de stratégie)",
           "backtest": bool(re.search(r"\*\*VALIDE\b", vtext)) or "VALIDE" in vtext[:3000],
           "risk": ((state.get("moteur_risque") or {}).get("post") or {}).get("approval") != "RISK_BLOCKED",
           "portfolio": not (state.get("portefeuille") or {}).get("error")}
    missing = []
    if p0_fail:
        missing.append("critères P0 non satisfaits : " + ", ".join(p0_fail))
    if score < 95:
        missing.append(f"note {fr(score, '.1f')}/100 sous 95")
    if not obs_ok:
        missing.append(f"période d'observation : {fr(days, '.0f')} jour(s) sur {OBSERVATION_DAYS} et {events} "
                       f"événement(s) sur {EVENTS_MIN_RARE} au moins ({RARE_JUSTIFICATION})")
    if div["status"] != "OK" or div["unexplained"]:
        missing.append("écart paper/backtest " + ("non mesuré" if div["status"] != "OK"
                                                  else "inexpliqué : " + ", ".join(div["unexplained"])))
    if not gov["backtest"]:
        missing.append("backtest non validé (docs/VALIDATION.md)")
    if not gov["risk"]:
        missing.append("moteur de risque : achats bloqués")
    accepted = not missing
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "rows": rows, "family_scores": fam_scores, "score": round(score, 1), "band": band(score),
            "p0_failures": p0_fail, "observation": {"days": days, "events": events, "trades": len(trades),
                                                    "open": len(holdings), "ok": obs_ok},
            "divergence": div, "calibration": cal, "governance": gov, "missing": missing,
            "status": "ACCEPTED" if accepted else "BLOCKED",
            "next_step": "POLICY_ENGINE" if accepted else "CORRECTION"}


def report_of(r: Dict[str, Any]) -> PaperAcceptanceReport:
    """Le verdict au format du contrat PaperAcceptanceReport.v1."""
    counts = {s: sum(1 for x in r["rows"] if x["status"] == s) for s in STATUS_FR}
    return PaperAcceptanceReport(report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"],
                                 status=r["status"], next_step=r["next_step"], readiness_score=r["score"],
                                 band=r["band"], p0_failures=tuple(r["p0_failures"]),
                                 passed=counts["PASS"], failed=counts["FAIL"], unknown=counts["UNKNOWN"],
                                 not_applicable=counts["NOT_APPLICABLE"],
                                 observation_days=round(r["observation"]["days"], 2),
                                 observation_events=r["observation"]["events"], missing=tuple(r["missing"]),
                                 engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : verdict, note, ce qui manque d'abord."""
    head = (f"acceptation du paper : {'ACCEPTÉ, prêt pour le moteur de politique' if r['status'] == 'ACCEPTED' else 'BLOQUÉ'}"
            f" ; note {fr(r['score'], '.1f')}/100 ({BAND_FR[r['band']]})")
    if r["missing"]:
        head += " ; manque : " + r["missing"][0]
    return head


def render(r: Dict[str, Any]) -> str:
    """docs/ACCEPTATION.md : le verdict, les familles, chaque critère."""
    o = r["observation"]
    lines = ["# Acceptation du paper (critères AC-001 à AC-044)", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py acceptation` (étape 13 du prompt maître, "
             "[`ACCEPTATION_PAPER.md`](ACCEPTATION_PAPER.md)). Le paper valide la préparation ; il n'autorise jamais le "
             "réel.", "", "## Verdict", "",
             f"- **{'ACCEPTÉ' if r['status'] == 'ACCEPTED' else 'BLOQUÉ'}** : étape suivante "
             f"{'moteur de politique' if r['status'] == 'ACCEPTED' else 'correction et observation'}.",
             f"- Note de préparation {fr(r['score'], '.1f')}/100 ({BAND_FR[r['band']]}) ; critères P0 non satisfaits : "
             f"{len(r['p0_failures'])}.",
             f"- Observation : {fr(o['days'], '.0f')} jour(s), {o['events']} événement(s) ({o['trades']} trade(s) "
             f"clos, {o['open']} position(s) ouverte(s)) ; il en faut {OBSERVATION_DAYS} jours et "
             f"{EVENTS_MIN_RARE} événements au moins ({OBSERVATION_DAYS_RECOMMENDED} jours recommandés)."]
    lines += [f"- Manque : {m}." for m in r["missing"]]
    lines += ["", "## Familles", "", "| Famille | poids | note |", "| --- | --- | --- |"]
    for fam, w in FAMILY_WEIGHTS.items():
        s = r["family_scores"].get(fam)
        lines.append(f"| {FAMILY_FR[fam]} | {fr(w * 100, '.0f')} % | {fr(s, '.0f') if s is not None else '—'} |")
    lines += ["", "## Critères", "", "| Critère | priorité | état | preuve |", "| --- | --- | --- | --- |"]
    for x in r["rows"]:
        lines.append(f"| {x['id']} {x['title']} | {x['priority']} | {STATUS_FR[x['status']]} | {x['detail']} |")
    d = r["divergence"]
    lines += ["", "## Paper et backtest de la même période", ""]
    if d["status"] == "OK":
        lines.append(f"Du {d['from']} au {d['to']} : {d['paper_entries']} achat(s) en paper, {d['backtest_entries']} "
                     f"au backtest, {d['matched']} identique(s).")
        lines += [f"- Expliqué : {x}." for x in d["explained"]]
        lines += [f"- Inexpliqué : {x}." for x in d["unexplained"]]
    else:
        lines.append(f"Non mesuré : {d['reason']}.")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `acceptation` : le verdict du jour (cours en cache pour
    l'écart au backtest), écrit avec --out."""
    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Critères d'acceptation du paper (AC-001 à AC-044)")
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    state = read_state(g.db_file) or {}
    try:
        close, volume = load_history(args.cache, list(g.universe), max_age_days=None)
    except (OSError, ValueError):
        close, volume = None, None
    r = evaluate(g, state, close=close, volume=volume, params=params_for(g))
    try:
        report_of(r)
    except ContractError as e:
        print(f"Rapport non conforme à son contrat : {e}")
        return 1
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["status"] == "ACCEPTED" else 1
