"""
Apprentissage continu et gouvernance des modèles (prompt maître, étape 19 ;
docs/APPRENTISSAGE_CONTINU.md) : tout ce qui, dans TrendGuard, apprend ou
décide à partir des données, inscrit dans un registre, avec sa fiche, son
niveau de risque, son état ; le champion et son challenger ; la dérive
mesurée (données, relation signal → résultat, performance, exécution) ; les
frontières de l'apprentissage (ce qui peut changer seul, après épreuves, ou
seulement par vous) ; puis l'examen AC-001 à AC-070 et le verdict.

Règles :
- aucun modèle ne change d'état seul en dehors des transitions permises
  (machine d'états) ; un modèle CRITICAL a toujours un propriétaire humain et
  un plan de retour ;
- l'évolution encadrée ne touche jamais les plafonds de risque, les
  positions, l'arrêt d'urgence ni le réel ; le palier de risque par achat
  monte un cran à la fois, après épreuves, jusqu'au plafond que vous avez
  fixé (TG_RISK_MAX_PCT, 2 % au plus), et redescend seul à la première alerte ;
- en réel contrôlé et en production limitée, l'évolution est gelée (rien
  n'est appris avec de l'argent réel avant la production) ;
- ce module lit et mesure ; il ne change rien.

    python trendguard_bot.py apprentissage                 # registre, dérive, examen
    python trendguard_bot.py apprentissage --out docs/APPRENTISSAGE.md
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import pathlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

import v29

from . import acceptation, autorisation, deploiement, evolution, moteur_quant, porte_examen
from . import trend_strategy as ts
from .contrats import ContractError, LearningGovernanceReport, ModelCard
from .texte import fr

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "apprentissage-1.0.0"
STATES = ("DRAFT", "TRAINING", "VALIDATING", "PENDING_APPROVAL", "APPROVED", "SHADOW", "CANARY", "ACTIVE",
          "DEGRADED", "SUSPENDED", "RETIRED", "REJECTED", "BLOCKED", "EMERGENCY_DISABLED")
STATE_FR = {"DRAFT": "brouillon", "TRAINING": "en apprentissage", "VALIDATING": "en validation",
            "PENDING_APPROVAL": "en attente d'accord", "APPROVED": "approuvé", "SHADOW": "en ombre",
            "CANARY": "à l'essai", "ACTIVE": "actif", "DEGRADED": "dégradé", "SUSPENDED": "suspendu",
            "RETIRED": "retiré", "REJECTED": "rejeté", "BLOCKED": "bloqué", "EMERGENCY_DISABLED": "coupé d'urgence"}
TRANSITIONS = {"DRAFT": ("TRAINING", "REJECTED"), "TRAINING": ("VALIDATING", "REJECTED"),
               "VALIDATING": ("PENDING_APPROVAL", "REJECTED"), "PENDING_APPROVAL": ("APPROVED", "REJECTED"),
               "APPROVED": ("SHADOW", "CANARY", "REJECTED"), "SHADOW": ("CANARY", "REJECTED", "RETIRED"),
               "CANARY": ("ACTIVE", "REJECTED", "RETIRED"), "ACTIVE": ("DEGRADED", "SUSPENDED", "RETIRED"),
               "DEGRADED": ("ACTIVE", "SUSPENDED", "RETIRED"), "SUSPENDED": ("ACTIVE", "RETIRED"),
               "RETIRED": (), "REJECTED": (), "BLOCKED": ("SUSPENDED", "RETIRED"), "EMERGENCY_DISABLED": ("SUSPENDED",)}
EMERGENCY = ("BLOCKED", "EMERGENCY_DISABLED")
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
RISK_FR = {"LOW": "faible", "MEDIUM": "moyen", "HIGH": "élevé", "CRITICAL": "critique"}
PSI_MODERATE, PSI_SIGNIFICANT = 0.10, 0.25
RECENT_DAYS = 90
CONCEPT_DAYS = 365
NEVER_LEARNED = ("risk_pct", "max_total_risk", "max_positions", "max_position_pct", "kill_drawdown", "fee",
                 "slippage")


def transition(current: str, new: str) -> str:
    """Passage d'un état à l'autre (§47) : seulement par une transition
    permise ; un état d'urgence se pose de partout (couper ne demande
    jamais de permission), un passage interdit est refusé."""
    if current not in STATES or new not in STATES:
        raise ContractError("INVALID_FIELD", f"état inconnu : {current!r} → {new!r}")
    if new in EMERGENCY or new in TRANSITIONS[current]:
        return new
    raise ContractError("POLICY", f"passage interdit : {STATE_FR[current]} → {STATE_FR[new]}", category="POLICY")


def risk_level(impact: int, autonomy: int, complexity: int, drift: int) -> str:
    """Niveau de risque d'un modèle (§15), sur des facteurs écrits : impact
    financier (0 à 3 : décide des achats = 3), autonomie (0 à 3),
    complexité (0 à 2), sensibilité à la dérive (0 à 2)."""
    s = impact + autonomy + complexity + drift
    return "CRITICAL" if s >= 8 else "HIGH" if s >= 6 else "MEDIUM" if s >= 3 else "LOW"


def _card(mid: str, name: str, kind: str, purpose: str, forbidden: str, data: str, validation: str,
          limits: str, rollback: str, factors: Tuple[int, int, int, int], can_trade: bool, version: str,
          owner: str = "vous") -> Dict[str, Any]:
    return {"model_id": mid, "name": name, "kind": kind, "purpose": purpose, "forbidden_use": forbidden,
            "data": data, "validation": validation, "limitations": limits, "rollback": rollback,
            "factors": factors, "risk": risk_level(*factors), "can_trade": can_trade, "version": version,
            "owner": owner}


def registry(gcfg: Any) -> List[Dict[str, Any]]:
    """Le registre des modèles (§13-14) : chaque modèle et sa fiche, son état
    lu dans le bot (jamais supposé)."""
    from . import moteur_risque, moteur_strategie
    ev = evolution.summary(gcfg)
    probation = bool(ev.get("enabled") and ev.get("probation"))
    risk_trial = bool(ev.get("enabled") and (ev.get("risk") or {}).get("probation"))
    p = getattr(gcfg, "params", ts.TrendParams())
    cards = [
        _card("regle", "Règle de tendance (cassure, stops, lecture du marché)", "RÈGLE", "décider des achats et des "
              "ventes chaque jour", "acheter en réel sans la porte du réel ; changer seule ses plafonds",
              "bougies journalières de Binance depuis 2017", "docs/VALIDATION.md : deux époques, walk-forward, PBO, "
              "Sharpe dégonflé, coûts, rejeu", "une tendance qui ne vient pas : petites pertes répétées",
              "réglages d'origine : python trendguard_bot.py evolution revenir", (3, 3, 1, 2), True,
              f"stratégie {moteur_strategie.ENGINE_VERSION}"),
        _card("evolution", "Évolution encadrée (réglages de la règle)", "CHALLENGER", "proposer un meilleur réglage "
              "de la cassure, des stops ou de la lecture du marché", "toucher au risque, aux positions, à l'arrêt "
              "d'urgence ou au réel", "les mêmes bougies, deux époques", "épreuves : deux époques, crises, coûts, "
              "voisins, chance ; puis 30 jours d'essai", "un réglage qui gagne au passé par chance",
              "retour automatique si l'essai prend plus de 2 points de retard ; evolution revenir",
              (2, 2, 1, 2), False, f"niveau {ev.get('level', '—')}"),
        _card("palier_risque", "Palier de risque par achat", "RÈGLE", "monter le risque par achat d'un cran après "
              "épreuves, le redescendre à la première alerte", f"dépasser le plafond que vous avez fixé "
              f"(TG_RISK_MAX_PCT : {fr(getattr(gcfg, 'risk_max_pct', 0.02) * 100, '.2f')} %)",
              "historique et capital du bot", "épreuves des deux époques et de la chance ; baisse de 10 % : premier "
              "palier aussitôt", "plus de risque, plus de baisse possible", "premier palier aussitôt à la moindre "
              "alerte", (3, 2, 1, 2), False, "paliers ×1 à ×2"),
        _card("profil_prudent", "Profil prudent (risque réduit après une baisse)", "RÈGLE", "réduire le risque après "
              "une baisse depuis le plus haut", "augmenter le risque", "capital du bot", "backtest du profil "
              "(ADAPTATION.md)", "réduit aussi les gains de la reprise", "réglage TG_DD_THROTTLE vide",
              (1, 1, 0, 1), False, "TG_DD_THROTTLE " + (",".join(f"{a}:{b}" for a, b in p.dd_throttle) or "vide")),
        _card("risque_var", "Moteur de risque (VaR, ES, scénarios)", "MODÈLE", "évaluer le risque du portefeuille "
              "avant les achats ; bloquer seulement ce qui arrête déjà les achats", "autoriser un achat ; augmenter "
              "une taille", "rendements des cryptos détenues et de l'univers", "tests de Kupiec et de "
              "Christoffersen sur la VaR", "queues plus épaisses que l'historique", "désactiver le moteur : la porte "
              "refuse alors tout achat", (1, 2, 2, 2), False, moteur_risque.MODEL_VERSION),
        _card("qualite", "Note de qualité des données", "MODÈLE", "refuser un achat sur des données abîmées",
              "corriger une donnée", "bougies du jour", "défauts nommés un à un", "un défaut nouveau non prévu",
              "aucun : elle ne fait que refuser", (1, 2, 1, 0), False, "qualité 1.0"),
        _card("carnet", "Normale du carnet d'ordres (ruse d'achat)", "MODÈLE", "différer un achat dans un carnet "
              "anormal", "acheter plus ou plus tôt", "carnets relevés chaque heure", "seuil robuste, ne fait que "
              "resserrer", "un carnet mince mais sain", "seuil fixe (sans apprentissage)", (1, 2, 1, 1), False,
              "carnet 1.0"),
        _card("savoir", "Noyau de savoir (sources jugées)", "MODÈLE", "reporter un achat sur une annonce de Binance "
              "venue d'une source prouvée", "acheter ; vendre", "sources publiques", "chaque source jugée sur les "
              "vrais cours", "une source fiable qui se trompe une fois", "réglage TG_SAVOIR=false", (1, 2, 1, 1),
              False, "savoir 1.0"),
        _card("finance", "Analyse financière (prévisions, consultative)", "MODÈLE", "expliquer, prévoir à titre "
              "d'information", "décider un achat", "bougies, indicateurs", "prévisions comparées à la clôture, "
              "calibrage", "une prévision n'est pas une certitude", "aucun : consultative", (0, 1, 2, 2), False,
              "finance 1.0"),
        _card("comite", "Comité d'agents (avis)", "MODÈLE", "donner un avis consultatif", "décider ; autoriser",
              "état du bot", "banc d'essai du comité", "un avis unanime peut se tromper", "aucun : consultatif",
              (0, 0, 1, 1), False, "comité 1.0"),
        _card("ia", "IA de la veille (conseil)", "MODÈLE", "lire et résumer, conseiller", "passer un ordre ; "
              "s'autoriser quoi que ce soit", "textes publics", "banc versionné, disjoncteur, montants inventés "
              "refusés", "une IA peut inventer", "retour au modèle suivant, ou à aucune IA", (0, 0, 2, 2), False,
              "modèles : banc versionné"),
    ]
    states = {"regle": "ACTIVE", "evolution": "CANARY" if probation else "SHADOW" if ev.get("enabled") else "SUSPENDED",
              "palier_risque": "CANARY" if risk_trial else "ACTIVE" if ev.get("enabled") else "SUSPENDED",
              "profil_prudent": "ACTIVE" if p.dd_throttle else "SUSPENDED",
              "risque_var": "ACTIVE" if getattr(gcfg, "risk_engine", False) else "SUSPENDED",
              "qualite": "ACTIVE", "carnet": "ACTIVE", "savoir": "ACTIVE" if getattr(gcfg, "savoir", False) else "SUSPENDED",
              "finance": "ACTIVE" if getattr(gcfg, "finance", False) else "SUSPENDED", "comite": "ACTIVE",
              "ia": "ACTIVE" if getattr(gcfg, "watch_ai", False) else "SUSPENDED"}
    for c in cards:
        c["state"] = states[c["model_id"]]
        text = "|".join(str(c[k]) for k in ("name", "purpose", "forbidden_use", "validation", "rollback", "version"))
        c["fingerprint"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return cards


def card_of(c: Dict[str, Any], now: datetime) -> ModelCard:
    """La fiche au format du contrat ModelCard.v1."""
    return ModelCard(model_id=c["model_id"], name=c["name"], kind=c["kind"], purpose=c["purpose"],
                     forbidden_use=c["forbidden_use"], data=c["data"], validation=c["validation"],
                     limitations=c["limitations"], rollback=c["rollback"], risk=c["risk"], state=c["state"],
                     can_trade=c["can_trade"], owner=c["owner"], model_version=c["version"],
                     fingerprint=c["fingerprint"], created_at=now.isoformat(timespec="seconds"))


def champion(gcfg: Any) -> Dict[str, Any]:
    """Champion et challenger (§19-21) : les réglages en vigueur de la règle,
    et l'essai de l'évolution s'il y en a un (appliqué en paper pendant 30
    jours, comparé, retiré seul s'il prend du retard)."""
    ev = evolution.summary(gcfg)
    if not ev.get("enabled"):
        return {"enabled": False, "champion": "réglages de votre .env", "challenger": None}
    changes = ", ".join(f"{c['param']} {c['from']} → {c['to']}" for c in ev.get("changes") or []) or "réglages d'origine"
    pr = ev.get("probation")
    return {"enabled": True, "champion": changes, "level": ev.get("name"),
            "challenger": pr if pr else None, "risk": ev.get("risk"),
            "text": (f"champion : {changes} ; challenger à l'essai : {pr}" if pr else
                     f"champion : {changes} ; aucun challenger à l'essai") + f" ; niveau {ev.get('name')}"}


# ══════════════════════════════════════════════════════════════════════
# Dérive (§23-24)
# ══════════════════════════════════════════════════════════════════════

def psi(reference: Sequence[float], recent: Sequence[float], bins: int = 10) -> Optional[float]:
    """Indice de stabilité de population : déciles de la référence, parts de
    chaque décile dans la référence et dans le récent ; sous 0,10 stable,
    au-delà de 0,25 dérive nette."""
    a = np.asarray([x for x in reference if np.isfinite(x)], dtype=float)
    b = np.asarray([x for x in recent if np.isfinite(x)], dtype=float)
    if len(a) < 10 * bins or len(b) < 30:
        return None
    edges = np.unique(np.quantile(a, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    pa = np.histogram(a, edges)[0] / len(a)
    pb = np.histogram(b, edges)[0] / len(b)
    pa, pb = np.clip(pa, 1e-4, None), np.clip(pb, 1e-4, None)
    return float(np.sum((pb - pa) * np.log(pb / pa)))


def data_drift(close: pd.DataFrame, days: int = RECENT_DAYS) -> Dict[str, Any]:
    """Dérive des données (§23) : rendements journaliers des 90 derniers jours
    contre ceux de l'époque d'apprentissage de la règle (2018-2022), par
    crypto : indice de stabilité et rapport des volatilités."""
    rets = np.log(close / close.shift(1))
    ref = rets.loc[ts.IS_PERIOD[0]:ts.IS_PERIOD[1]]
    rec = rets.iloc[-days:]
    rows = []
    for a in close.columns:
        v = psi(ref[a].dropna(), rec[a].dropna())
        if v is None:
            continue
        sr, sa = float(rec[a].std()), float(ref[a].std())
        rows.append({"asset": a, "psi": v, "vol_ratio": sr / sa if sa > 0 else None,
                     "level": "nette" if v > PSI_SIGNIFICANT else "modérée" if v > PSI_MODERATE else "stable"})
    if not rows:
        return {"status": "INSUFFICIENT_DATA"}
    worst = max(rows, key=lambda r: r["psi"])
    return {"status": "OK", "rows": rows, "median_psi": float(np.median([r["psi"] for r in rows])), "worst": worst,
            "drifting": [r["asset"] for r in rows if r["psi"] > PSI_SIGNIFICANT],
            "from": str(close.index[-days].date()), "to": str(close.index[-1].date())}


def concept_drift(close: pd.DataFrame, volume: pd.DataFrame, p: ts.TrendParams,
                  days: int = CONCEPT_DAYS) -> Dict[str, Any]:
    """Dérive du concept (§23) : la relation signal → résultat a-t-elle
    changé ? Résultat des trades du backtest de la règle (en multiples du
    risque, R) sur la dernière année contre les années d'avant ; test de
    Welch. Un écart n'est pas une preuve de rupture sur peu de trades."""
    res = ts.backtest(close, volume, p, ts.IS_PERIOD[0], str(close.index[-1].date()))
    cut = close.index[-1] - pd.Timedelta(days=days)
    recent = [t["r"] for t in res.trades if pd.Timestamp(t["entry_date"]).tz_localize(None) >= cut.tz_localize(None)]
    before = [t["r"] for t in res.trades if pd.Timestamp(t["entry_date"]).tz_localize(None) < cut.tz_localize(None)]
    if len(recent) < 10 or len(before) < 30:
        return {"status": "INSUFFICIENT_DATA", "recent": len(recent), "before": len(before)}
    w = moteur_quant.welch(recent, before)
    return {"status": "OK", "recent_n": len(recent), "before_n": len(before), "recent_r": float(np.mean(recent)),
            "before_r": float(np.mean(before)), "p_value": w["p_value"],
            "drift": w["p_value"] < 0.05 and float(np.mean(recent)) < float(np.mean(before))}


def performance_drift(state: Dict[str, Any], p: ts.TrendParams) -> Dict[str, Any]:
    """Dérive de performance et d'exécution (§23) : glissement des sorties
    attendu contre observé (acceptation du paper), écart moyen entre le prix
    payé et le cours de décision (qualité des exécutions)."""
    cal = acceptation._expected_vs_observed(state, p)
    q = state.get("qualite_execution") or {}
    sb = q.get("shortfall_bps") or []
    return {"slippage": cal, "shortfall_n": len(sb), "shortfall_bps": float(np.mean(sb)) if sb else None,
            "execution_drift": bool(sb) and float(np.mean(sb)) > p.slippage * 1e4 + 50}


# ══════════════════════════════════════════════════════════════════════
# Frontières de l'apprentissage (§32-33, §49, §57)
# ══════════════════════════════════════════════════════════════════════

BOUNDARIES = {
    "AUTOMATIC": ("normale du carnet d'ordres (ne fait que resserrer)", "calibrage des prévisions (probabilités, "
                  "jamais les décisions)", "choix de l'IA par ses mesures (banc versionné, disjoncteur)",
                  "jugement des sources du savoir sur les vrais cours", "détection de dérive (ce module)"),
    "REINFORCED": ("réglages de la règle par l'évolution : épreuves, 30 jours d'essai, retour seul",
                   "palier de risque par achat : un cran après épreuves, jusqu'à votre plafond, redescente aussitôt"),
    "HUMAN": ("plafonds de risque, positions, arrêt d'urgence", "politiques, autorisation, porte d'exécution",
              "armer le réel, paliers du réel", "clés et mots de passe", "fusion du code (Pull Request)"),
}


def boundaries(gcfg: Any) -> Dict[str, Any]:
    """Les frontières vérifiées : l'évolution ne règle aucun plafond ; le
    palier de risque est borné par votre plafond (2 % au plus) ; aucune
    automatisation ne peut fusionner du code, changer le risque ou la porte ;
    en réel contrôlé et en production limitée, l'évolution est gelée."""
    space = set(evolution.SPACE)
    touched = sorted(space & set(NEVER_LEARNED))
    autos = [k for k, v in autorisation.PRINCIPALS.items() if v["type"] in ("AUTOMATION", "AGENT", "AI_MODEL", "EXTERNAL")]
    leaks = [(k, a) for k in autos for a in ("CHANGE_RISK", "MERGE_CODE", "KILL_RESET", "SAFE_MODE_OFF", "ARM_LIVE",
                                             "SET_SECRETS")
             if autorisation.decide(k, a, context={c: True for c in autorisation.CONDITIONS_FR}).allowed]
    cap = float(getattr(gcfg, "risk_max_pct", 0.02))
    frozen = frozen_in(deploiement.STAGES)
    ok = not touched and not leaks and 0 < cap <= 0.02 and frozen == ["CONTROLLED_LIVE", "LIMITED_PRODUCTION"]
    return {"ok": ok, "evolution_params": sorted(space), "touched": touched, "leaks": leaks, "risk_cap_pct": cap * 100,
            "frozen_stages": frozen, "lists": BOUNDARIES}


def frozen_in(stages: Sequence[str]) -> List[str]:
    """Paliers où l'évolution est gelée (rien appris avec de l'argent réel
    avant la production)."""
    return [s for s in stages if s in deploiement.LEARNING_FROZEN]


def evolution_allowed(gcfg: Any) -> bool:
    """L'évolution peut-elle tourner à ce palier ? Non en réel contrôlé ni en
    production limitée."""
    return deploiement.current(gcfg) not in frozen_in(deploiement.STAGES)


# ══════════════════════════════════════════════════════════════════════
# Examen : AC-001 à AC-070 (§66-67, §70)
# ══════════════════════════════════════════════════════════════════════

FAMILY_WEIGHTS = {"data": 0.15, "evaluation": 0.15, "governance": 0.15, "safety": 0.15, "drift": 0.10,
                  "reproducibility": 0.10, "security": 0.10, "deployment": 0.05, "observability": 0.05}
FAMILY_FR = {"data": "intégrité des données", "evaluation": "évaluation des modèles", "governance": "gouvernance",
             "safety": "sûreté", "drift": "surveillance de la dérive", "reproducibility": "reproductibilité",
             "security": "sécurité", "deployment": "déploiement et retour", "observability": "observabilité"}
_c = porte_examen._c
AP = "tests/test_apprentissage.py::"
EV = "tests/test_evolution.py::"
MO = "tests/test_modeles.py::"
MB = "tests/test_moteur_backtest.py::"
CRITERIA: Tuple[acceptation.Criterion, ...] = (
    _c(1, "Jeux de données versionnés (empreinte)", "P1", "data", (MB + "test_the_manifest_makes_a_run_reproducible",)),
    _c(2, "Lignée des données", "P0", "data", ("tests/test_donnees.py::test_every_paper_trade_traces_back_to_its_data",)),
    _c(3, "Données à leur date (aucune information future)", "P0", "data",
       ("tests/test_contrats_donnees.py::test_a_decision_cannot_look_into_the_future",)),
    _c(4, "Fuite de données détectée", "P0", "data",
       ("tests/test_trendguard.py::test_features_are_causal", "tests/test_finance.py::test_no_indicator_looks_into_the_future")),
    _c(5, "Expériences tracées", "P1", "reproducibility",
       ("tests/test_analyse.py::test_registry_notes_and_replays_an_experiment",)),
    _c(6, "Apprentissage reproductible", "P0", "reproducibility",
       (MB + "test_the_manifest_makes_a_run_reproducible", "tests/test_moteur_quant.py::test_lab_is_reproducible_and_follows_its_contract")),
    _c(7, "Registre des modèles", "P0", "governance", (AP + "test_every_model_has_its_card",)),
    _c(8, "Modèles versionnés", "P1", "governance", (AP + "test_every_model_has_its_card",
                                                     MO + "test_benchmark_is_versioned_and_governs_the_router")),
    _c(9, "Fiche obligatoire", "P0", "governance", (AP + "test_every_model_has_its_card",)),
    _c(10, "Niveau de risque de chaque modèle", "P1", "governance", (AP + "test_every_model_has_its_card",)),
    _c(11, "Évaluation des modèles", "P0", "evaluation", (MB + "test_the_full_validation_measures_everything",)),
    _c(12, "Validation hors échantillon", "P0", "evaluation", (MB + "test_the_full_validation_measures_everything",)),
    _c(13, "Walk-forward", "P0", "evaluation", (MB + "test_the_full_validation_measures_everything",)),
    _c(14, "Robustesse", "P1", "evaluation", ("tests/test_robustness.py::test_block_monte_carlo_keeps_real_drawdowns",)),
    _c(15, "Scénarios extrêmes", "P1", "evaluation", ("tests/test_analyse.py::test_stress_scenarios_and_the_kill_switch",)),
    _c(16, "Calibrage", "P1", "evaluation", ("tests/test_moteur_risque.py::test_var_calibration_tests",
                                             "tests/test_learning.py::test_calibration_changes_probabilities_not_decisions")),
    _c(17, "Dérive des données détectée", "P1", "drift", (AP + "test_drift_is_measured_never_invented",)),
    _c(18, "Dérive du concept détectée", "P1", "drift", (AP + "test_drift_is_measured_never_invented",)),
    _c(19, "Dérive de performance détectée", "P1", "drift", (AP + "test_drift_is_measured_never_invented",)),
    _c(20, "Champion et challenger", "P1", "deployment", (EV + "test_daily_routine_trial_promotion_revert_and_storm",)),
    _c(21, "Mode ombre", "P1", "deployment",
       ("tests/test_politique.py::test_in_the_bot_the_policies_agree_with_the_gate_and_change_nothing",
        "tests/test_agents.py::test_the_committee_is_consultative_in_the_bot")),
    _c(22, "Essai progressif (canari)", "P1", "deployment", (EV + "test_risk_step_climbs_slowly_on_trials_and_falls_at_once",)),
    _c(23, "Retour automatique", "P0", "deployment", (EV + "test_daily_routine_trial_promotion_revert_and_storm",)),
    _c(24, "Modèle de secours", "P1", "deployment", (MO + "test_fallback_is_traced_and_versions_recorded",)),
    _c(25, "Intégrité des modèles", "P1", "security", (MO + "test_prompt_registry_refuses_silent_changes",
                                                      AP + "test_every_model_has_its_card")),
    _c(26, "Artefacts signés", "P2", "security", na="pas d'artefact binaire : chaque modèle est du code versionné par "
                                                     "git, fusionné par vous, contrôlé par GitHub"),
    _c(27, "Analyse de sécurité", "P1", "security",
       ("tests/test_bibliotheques.py::test_report_names_libraries_with_a_known_flaw",)),
    _c(28, "Empoisonnement des données détecté", "P1", "data",
       ("tests/test_analyse.py::test_data_quality_score_names_each_defect", "tests/test_savoir.py::test_sources_are_judged_on_real_prices")),
    _c(29, "Retours validés", "P2", "data", ("tests/test_learning.py::test_deferral_outcomes_are_counted",)),
    _c(30, "Récompense détournée détectée (chance, voisins)", "P1", "evaluation", (EV + "test_trials_on_a_small_market",)),
    _c(31, "Auto-amélioration gouvernée", "P0", "governance", (EV + "test_bot_applies_evolved_settings_only_when_enabled",
                                                               EV + "test_candidates_follow_the_level_and_never_touch_risk")),
    _c(32, "Code validé avant installation", "P0", "governance",
       ("tests/test_maintenance.py::test_update_installs_only_what_the_owner_validated",)),
    _c(33, "Prompts gouvernés", "P1", "governance", (MO + "test_prompt_registry_refuses_silent_changes",)),
    _c(34, "Recherche documentaire évaluée", "P2", "evaluation", ("tests/test_cognitif.py::test_rachelle_refuses_instruction_injection",)),
    _c(35, "IA évaluées en continu", "P1", "evaluation", (MO + "test_benchmark_is_versioned_and_governs_the_router",)),
    _c(36, "Accord humain", "P0", "governance", ("tests/test_maintenance.py::test_update_installs_only_what_the_owner_validated",)),
    _c(37, "Double accord", "P1", "governance", ("tests/test_autorisation.py::test_conditions_must_all_hold_exactly",)),
    _c(38, "Séparation des tâches", "P0", "governance",
       ("tests/test_autorisation.py::test_separation_of_duties_and_delegations",)),
    _c(39, "Audit inaltérable", "P0", "security", ("tests/test_contrats.py::test_audit_chain_detects_any_change",)),
    _c(40, "Reproductibilité", "P0", "reproducibility", ("tests/test_trendguard.py::test_replay_matches_backtest",)),
    _c(41, "Mode sûr", "P0", "safety", ("tests/test_contrats.py::test_safe_mode_stops_buys_but_not_sales",)),
    _c(42, "Repli sur « pas de trade »", "P0", "safety", ("tests/test_plateforme.py::test_the_gate_says_no_trade_with_reasons",)),
    _c(43, "Intégration aux politiques", "P0", "safety",
       ("tests/test_politique.py::test_in_the_bot_the_policies_agree_with_the_gate_and_change_nothing",)),
    _c(44, "Intégration à l'autorisation", "P0", "safety",
       ("tests/test_autorisation.py::test_in_the_bot_the_rights_agree_with_the_gate_and_change_nothing",)),
    _c(45, "Porte d'exécution isolée", "P0", "safety",
       ("tests/test_porte_examen.py::test_one_single_route_to_binance_and_it_passes_the_gate",)),
    _c(46, "Arrêt d'urgence protégé", "P0", "safety", ("tests/test_trendguard.py::test_kill_switch_blocks_entries",)),
    _c(47, "Aucune élévation de privilège", "P0", "safety", ("tests/test_autorisation.py::test_deny_by_default",)),
    _c(48, "Retour éprouvé", "P1", "deployment", (EV + "test_daily_routine_trial_promotion_revert_and_storm",
                                                  "tests/test_maintenance.py::test_update_rolls_back_a_faulty_version")),
    _c(49, "Reprise après sinistre", "P1", "reproducibility",
       ("tests/test_donnees.py::test_backup_is_restored_for_real_and_reported",)),
    _c(50, "Essais de chaos", "P1", "safety", ("tests/test_porte_examen.py::test_chaos_ten_thousand_requests_and_a_kill_switch",)),
    _c(51, "Données abîmées : reprise", "P1", "data", ("tests/test_analyse.py::test_data_quality_score_names_each_defect",)),
    _c(52, "Fichier d'un modèle abîmé : reprise", "P1", "security", (EV + "test_file_cannot_force_risk_or_unknown_values",)),
    _c(53, "Registre en panne : reprise", "P2", "reproducibility",
       ("tests/test_analyse.py::test_registry_notes_and_replays_an_experiment",)),
    _c(54, "Déploiement raté : reprise", "P1", "deployment", ("tests/test_maintenance.py::test_update_rolls_back_a_faulty_version",)),
    _c(55, "Surveillance complète", "P1", "observability",
       ("tests/test_controle.py::test_health_of_each_service_is_measured_never_assumed",)),
    _c(56, "Alertes", "P1", "observability", ("tests/test_alerts.py::test_hub_levels_dedup_and_failure_isolation",)),
    _c(57, "Machine d'états de la gouvernance", "P0", "governance", (AP + "test_the_governance_state_machine",)),
    _c(58, "Audit de l'apprentissage", "P1", "observability",
       ("tests/test_evolution.py::test_daily_routine_changes_one_thing_at_a_time",)),
    _c(59, "Provenance du savoir", "P1", "data", ("tests/test_savoir.py::test_bot_opinion_uses_only_proven_sources",)),
    _c(60, "Contradictions résolues", "P2", "evaluation", (MO + "test_close_views_are_not_a_disagreement",)),
    _c(61, "Confiance calibrée", "P1", "evaluation", ("tests/test_finance.py::test_forecast_evaluation_and_calibration",)),
    _c(62, "Incertitude gérée (inconnu jamais pris pour zéro)", "P0", "data",
       ("tests/test_contrats_donnees.py::test_money_and_unknown_values",)),
    _c(63, "Désaccord des modèles", "P1", "evaluation", (MO + "test_unanimous_ais_change_no_trade",)),
    _c(64, "Apprentissage des stratégies isolé", "P0", "safety",
       ("tests/test_libre.py::test_the_free_bot_runs_beside_the_main_bot_without_changing_it",)),
    _c(65, "Aucun apprentissage avec de l'argent réel avant la production", "P0", "safety",
       (AP + "test_learning_boundaries_keep_the_safety_barriers",)),
    _c(66, "Barrières de sûreté préservées", "P0", "safety", (AP + "test_learning_boundaries_keep_the_safety_barriers",)),
    _c(67, "Aucune élévation de privilège autonome", "P0", "safety",
       (AP + "test_learning_boundaries_keep_the_safety_barriers",)),
    _c(68, "Aucun plafond de risque changé seul", "P0", "safety",
       (EV + "test_risk_steps_are_bounded_and_the_file_cannot_force_more", AP + "test_learning_boundaries_keep_the_safety_barriers")),
    _c(69, "Aucune porte d'exécution changée seule", "P0", "safety",
       (AP + "test_learning_boundaries_keep_the_safety_barriers",)),
    _c(70, "Apprentissage continu contrôlé", "P0", "governance", (AP + "test_the_examination_and_its_contract",)),
)


def _measures(gcfg: Any, cards: List[Dict[str, Any]], b: Dict[str, Any], drift: Dict[str, Any]
              ) -> Dict[str, Tuple[str, str]]:
    """(état, détail) des critères mesurés sur le registre et les cours."""
    out: Dict[str, Tuple[str, str]] = {}
    bad = [c["model_id"] for c in cards if c["risk"] == "CRITICAL" and (c["owner"] != "vous" or not c["rollback"])]
    out["AC-007"] = out["AC-009"] = (("PASS" if not bad else "FAIL"),
                                     f"{len(cards)} modèles, chacun avec sa fiche" + (f" ; incomplètes : {bad}" if bad else ""))
    levels = {lv: sum(c["risk"] == lv for c in cards) for lv in RISK_LEVELS}
    out["AC-010"] = ("PASS", ", ".join(f"{n} {RISK_FR[lv]}" for lv, n in levels.items() if n))
    out["AC-066"] = out["AC-067"] = out["AC-068"] = out["AC-069"] = out["AC-065"] = (
        ("PASS" if b["ok"] else "FAIL"),
        f"évolution : {len(b['evolution_params'])} réglages permis, aucun plafond ; palier de risque borné à "
        f"{fr(b['risk_cap_pct'], '.2f')} % ; aucune automatisation ni IA ne change le risque, la porte ou le code ; "
        "évolution gelée en réel contrôlé et en production limitée")
    dd = drift.get("data") or {}
    out["AC-017"] = (("PASS", f"indice de stabilité médian {fr(dd['median_psi'], '.3f')} du {dd['from']} au {dd['to']} ; "
                      f"dérive nette : {', '.join(a.upper() for a in dd['drifting']) or 'aucune'}") if dd.get("status") == "OK"
                     else ("UNKNOWN", "cours non chargés (la commande apprentissage les charge)"))
    cd = drift.get("concept") or {}
    out["AC-018"] = (("PASS", f"dernière année {fr(cd['recent_r'], '+.2f')} R sur {cd['recent_n']} trades, avant "
                      f"{fr(cd['before_r'], '+.2f')} R sur {cd['before_n']} (p = {fr(cd['p_value'], '.2f')})"
                      + (" : DÉRIVE" if cd["drift"] else "")) if cd.get("status") == "OK" else
                     ("UNKNOWN", "trop peu de trades récents ou cours non chargés"))
    pd_ = drift.get("performance") or {}
    sl = pd_.get("slippage") or {}
    out["AC-019"] = (("PASS", f"glissement attendu {fr(sl['expected'], '.2f')} %, observé {fr(sl['observed'], '.2f')} % "
                      f"sur {sl['n']} sortie(s)") if sl.get("status") == "OK" else ("UNKNOWN", "aucune sortie encore"))
    return out


def band(score: float) -> str:
    """Bande de la note (§66), la même que pour la porte d'exécution."""
    return porte_examen.band(score)


def evaluate(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None, close: Optional[pd.DataFrame] = None,
             volume: Optional[pd.DataFrame] = None, root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Registre, champion, dérive, frontières ; les 70 critères, la note et
    le verdict (READY_FOR_CONTROLLED_CONTINUOUS_LEARNING ou NOT_READY ;
    jamais une auto-amélioration sans contrôle)."""
    now = now or datetime.now(timezone.utc)
    p = getattr(gcfg, "params", None) or ts.TrendParams()
    cards = registry(gcfg)
    b = boundaries(gcfg)
    drift: Dict[str, Any] = {"performance": performance_drift(state, p)}
    if close is not None:
        drift["data"] = data_drift(close)
        try:
            drift["concept"] = concept_drift(close, volume, p)
        except Exception as e:           # mesure impossible : jamais inventée
            drift["concept"] = {"status": "INSUFFICIENT_DATA", "reason": type(e).__name__}
    rows, fam, score, p0 = porte_examen.grade(CRITERIA, _measures(gcfg, cards, b, drift), FAMILY_WEIGHTS, root)
    ready = not p0 and score >= 95
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "cards": cards, "champion": champion(gcfg), "boundaries": b, "drift": drift, "rows": rows,
            "family_scores": fam, "score": round(score, 1), "band": band(score), "p0_failures": p0,
            "status": "READY_FOR_CONTROLLED_CONTINUOUS_LEARNING" if ready else "NOT_READY"}


def report_of(r: Dict[str, Any]) -> LearningGovernanceReport:
    """Le verdict au format du contrat LearningGovernanceReport.v1."""
    counts = {s: sum(1 for x in r["rows"] if x["status"] == s) for s in acceptation.STATUS_FR}
    return LearningGovernanceReport(
        report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=r["status"],
        readiness_score=r["score"], band=r["band"], p0_failures=tuple(r["p0_failures"]), passed=counts["PASS"],
        failed=counts["FAIL"], unknown=counts["UNKNOWN"], not_applicable=counts["NOT_APPLICABLE"],
        models=len(r["cards"]), critical_models=sum(c["risk"] == "CRITICAL" for c in r["cards"]),
        boundaries_ok=r["boundaries"]["ok"], engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : modèles, champion, dérive."""
    cards = r["cards"]
    dd = (r["drift"].get("data") or {})
    out = (f"{len(cards)} modèles inscrits ({sum(c['state'] == 'ACTIVE' for c in cards)} actifs, "
           f"{sum(c['risk'] == 'CRITICAL' for c in cards)} critique) ; {r['champion'].get('text') or 'évolution désactivée'}")
    if dd.get("status") == "OK":
        out += f" ; dérive des données : {', '.join(a.upper() for a in dd['drifting']) or 'aucune nette'}"
    return out


def render(r: Dict[str, Any]) -> str:
    """docs/APPRENTISSAGE.md : registre, champion, dérive, frontières,
    verdict, critères."""
    ready = r["status"] != "NOT_READY"
    lines = ["# Apprentissage continu et gouvernance des modèles (critères AC-001 à AC-070)", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py apprentissage` (étape 19 du prompt maître, "
             "[`APPRENTISSAGE_CONTINU.md`](APPRENTISSAGE_CONTINU.md)). Lecture seule.", "", "## Verdict", "",
             f"- **{'Prêt pour un apprentissage continu contrôlé' if ready else 'PAS PRÊT'}** ({r['status']}) ; "
             "jamais une auto-amélioration sans contrôle.",
             f"- Note {fr(r['score'], '.1f')}/100 ({porte_examen.BAND_FR[r['band']]}) ; critères P0 non satisfaits : "
             f"{len(r['p0_failures'])}" + (f" ({', '.join(r['p0_failures'])})" if r["p0_failures"] else "") + ".",
             f"- {describe(r)}.", "", "## Registre des modèles", "",
             "| Modèle | type | risque | état | ce qu'il ne peut pas faire | retour |", "| --- | --- | --- | --- | --- | --- |"]
    for c in r["cards"]:
        lines.append(f"| {c['name']} | {c['kind']} | {RISK_FR[c['risk']]} | {STATE_FR[c['state']]} | "
                     f"{c['forbidden_use']} | {c['rollback']} |")
    d = r["drift"]
    lines += ["", "## Dérive", ""]
    dd = d.get("data") or {}
    if dd.get("status") == "OK":
        lines += [f"Rendements du {dd['from']} au {dd['to']} contre l'époque d'apprentissage ({ts.IS_PERIOD[0][:4]}-"
                  f"{ts.IS_PERIOD[1][:4]}) : indice de stabilité médian {fr(dd['median_psi'], '.3f')} (sous 0,10 : "
                  "stable ; au-delà de 0,25 : dérive nette).", "", "| Crypto | indice | volatilité récente / passée | "
                  "dérive |", "| --- | --- | --- | --- |"]
        for x in sorted(dd["rows"], key=lambda x: -x["psi"])[:10]:
            vr = "—" if x["vol_ratio"] is None else fr(x["vol_ratio"], ".2f")
            lines.append(f"| {x['asset'].upper()} | {fr(x['psi'], '.3f')} | {vr} | {x['level']} |")
    else:
        lines.append("Dérive des données : cours non chargés.")
    cd = d.get("concept") or {}
    lines += ["", (f"Relation signal → résultat : dernière année {fr(cd['recent_r'], '+.2f')} R par trade "
                   f"({cd['recent_n']} trades), avant {fr(cd['before_r'], '+.2f')} R ({cd['before_n']}) ; p = "
                   f"{fr(cd['p_value'], '.2f')}" + (" : dérive." if cd["drift"] else " : pas de rupture prouvée."))
              if cd.get("status") == "OK" else "Relation signal → résultat : trop peu de trades récents pour juger."]
    b = r["boundaries"]
    lines += ["", "## Frontières de l'apprentissage", "",
              "- **Seul** : " + " ; ".join(b["lists"]["AUTOMATIC"]) + ".",
              "- **Après épreuves** : " + " ; ".join(b["lists"]["REINFORCED"]) + ".",
              "- **Vous seul** : " + " ; ".join(b["lists"]["HUMAN"]) + ".",
              f"- Vérifié : {'tenu' if b['ok'] else 'NON TENU'} (évolution : {', '.join(b['evolution_params'])} ; "
              f"plafond du palier de risque {fr(b['risk_cap_pct'], '.2f')} % ; gelée en "
              + ", ".join(deploiement.STAGE_FR[s] for s in b["frozen_stages"]) + ").",
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
    """Commande `apprentissage` : registre, dérive (cours en cache),
    frontières, examen ; écrit avec --out."""
    from .config import load_guard_config_from_env
    from .evolution import load_history, params_for
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Apprentissage continu et gouvernance des modèles (AC-001 à AC-070)")
    ap.add_argument("--cache", default=os.path.join(v29.APP_DIR, "data_evolution"))
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    g = dataclasses.replace(g, params=params_for(g))
    try:
        close, volume = load_history(args.cache, list(g.universe), max_age_days=None)
    except (OSError, ValueError):
        close, volume = None, None
    r = evaluate(g, read_state(g.db_file) or {}, close=close, volume=volume)
    try:
        report_of(r)
        for c in r["cards"]:
            card_of(c, datetime.fromisoformat(r["now"]))
    except ContractError as e:
        print(f"Registre ou verdict non conforme à son contrat : {e}")
        return 1
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["status"] != "NOT_READY" else 1
