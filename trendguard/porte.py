"""
Porte d'exécution (prompt maître, étape 2 ; docs/CONTRATS.md) : aucun achat,
en paper comme en réel, sans passer par elle.

    plan du jour → intention d'ordre → contrôle du risque → autorisation → ordre

Des règles fixes et testées, sans IA : mode réel armé et porte du réel
ouverte (chantiers.py, porte 8 : mesurée sur l'état du bot), ni arrêt d'urgence ni
mode sûr, garde « NO TRADE » du jour, décision du jour (données fraîches),
crypto de la liste, choisie et sans veto, pas déjà détenue ni achetée deux
fois, nombre de positions, risque de l'achat, risque cumulé, taille de la
position, argent disponible, stop sous le prix, montant minimum, qualité des
données du jour (au moins 50 sur 100 : en dessous, les données sont trop
abîmées pour décider ; étape 3, §76), évaluation du jour du moteur de risque
(moteur_risque.py, étape 11 : absente, périmée ou bloquée, aucun achat), et
en réel les règles de Binance pour cet achat (paire cotée et active, pas du
lot, bornes de quantité, montant minimum ; étape 16, §11) et les plafonds du
palier du réel (achats par jour, montant acheté par jour, capital confié au
bot fixé par vous ; deploiement.py, étape 17).

Validation finale (étape 16, §21) : juste avant l'ordre, l'autorisation
est-elle encore valable, liée à son contrôle et non consommée ? L'arrêt
d'urgence et le mode sûr (relu sur le disque) sont-ils toujours levés ?
L'état critique a-t-il changé depuis le contrôle (empreinte) ? S'il a
changé, nouveau contrôle complet. Un doute bloque l'achat.

Le plan du jour respecte déjà ces limites : la porte ne change rien aux
décisions normales (10 % de marge pour les écarts de prix et de capital entre
la décision et l'achat). Elle arrête une erreur grossière : calcul faux,
double achat, réglage aberrant, mode sûr oublié, autorisation réelle absente.
Les ventes n'y passent pas : réduire le risque reste toujours possible.

Mode sûr : fichier <bot>.modesur.json, posé par la commande
`python trendguard_bot.py mode-sur on` ; plus aucun achat, le bot continue
de lire, d'analyser, de protéger et de vendre.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, FrozenSet, List, Optional, Tuple

from . import autonomy
from .contrats import (
    ContractError,
    ExecutionAuthorization,
    FinalValidationResult,
    OrderIntent,
    RiskDecision,
    SafeModeState,
)
from .texte import fr

POLICY_VERSION = "porte.v3"              # v2 (étape 16) : règles de Binance, validation finale ; v3 (17) : paliers
MARGIN = 0.10               # écart toléré entre le plan (clôture) et l'achat (prix, capital du moment)
MIN_NOTIONAL = 10.0         # même minimum que la taille des positions (trend_strategy.size_position)
AUTH_SECONDS = 300          # une autorisation vaut 5 minutes
QUALITY_MIN = 50.0          # note des données du jour (qualite.py) sous laquelle on n'achète pas
RESTRICTIONS = ("achat au comptant (Spot), sans levier", "ordre unique pour cette crypto aujourd'hui")


@dataclass(frozen=True)
class Portfolio:
    """Le portefeuille et les politiques au moment de l'achat (vue de la
    porte : PortfolioSnapshot réduit à ce qu'elle contrôle)."""
    equity: float
    cash: float
    invested: float
    held_risk: Tuple[Tuple[str, float], ...]       # (crypto, risque initial) des positions
    risk_mult: float
    expected_day: str
    universe: FrozenSet[str]
    allowed: FrozenSet[str]
    vetoed: FrozenSet[str] = frozenset()
    bought_today: FrozenSet[str] = frozenset()     # clés d'unicité déjà exécutées
    halted: bool = False
    garde_blocked: Tuple[str, ...] = ()
    safe_mode: SafeModeState = SafeModeState()
    data_quality: Optional[float] = None           # note des données du jour (None : inconnue)
    live: bool = False
    live_armed: bool = False
    production: Optional[Tuple[bool, str]] = None  # porte du réel (ouverte, détail) ; None : non mesurée (essais)
    risk_engine: Optional[Tuple[bool, str]] = None  # moteur de risque (évaluation valide, détail) ; None : essais
    instrument: Optional[Tuple[bool, str]] = None   # règles de Binance pour cet achat (réel) ; None : paper, essais
    stage: Optional[Tuple[bool, str]] = None        # plafonds du palier du réel (deploiement.py) ; None : paper, essais


def _limits(intent: OrderIntent, pf: Portfolio, p: Any) -> List[Tuple[str, bool, str]]:
    """Chaque contrôle : (nom, conforme, détail)."""
    eq = pf.equity
    held = dict(pf.held_risk)
    open_risk = sum(held.values())
    risk_cap = p.risk_pct * eq * pf.risk_mult
    total_cap = p.max_total_risk * eq * pf.risk_mult
    size_cap = p.max_position_pct * eq * (1 + p.fee)
    checks = [
        ("Mode réel armé", not pf.live or pf.live_armed,
         "paper" if not pf.live else ("armé" if pf.live_armed else "réel NON armé")),
        ("Porte du réel", not pf.live or pf.production is None or pf.production[0],
         "paper" if not pf.live else "non mesurée (essais)" if pf.production is None else pf.production[1]),
        ("Arrêt d'urgence", not pf.halted, "déclenché" if pf.halted else "prêt"),
        ("Mode sûr", not pf.safe_mode.active,
         f"actif ({pf.safe_mode.reason or 'sans raison donnée'})" if pf.safe_mode.active else "inactif"),
        ("Garde du jour", not pf.garde_blocked, " ; ".join(pf.garde_blocked) or "aucune raison de s'abstenir"),
        ("Décision du jour", intent.decision_day == pf.expected_day,
         f"décision du {intent.decision_day}, attendue du {pf.expected_day}"),
        ("Crypto autorisée", intent.asset in pf.universe and intent.asset in pf.allowed
         and intent.asset not in pf.vetoed,
         "hors de la liste" if intent.asset not in pf.universe else "non choisie" if intent.asset not in pf.allowed
         else "achats bloqués par la veille" if intent.asset in pf.vetoed else "oui"),
        ("Pas de doublon", intent.asset not in held and intent.idempotency_key not in pf.bought_today,
         "déjà détenue" if intent.asset in held else "déjà achetée aujourd'hui"
         if intent.idempotency_key in pf.bought_today else "premier achat"),
        ("Nombre de positions", len(held) + 1 <= p.max_positions, f"{len(held) + 1} sur {p.max_positions}"),
        ("Risque de l'achat", eq > 0 and intent.risk_quote <= risk_cap * (1 + MARGIN) + 1e-9,
         f"{fr(intent.risk_quote, '.2f')} pour {fr(risk_cap, '.2f')} permis"),
        ("Risque cumulé", eq > 0 and open_risk + intent.risk_quote <= total_cap * (1 + MARGIN) + 1e-9,
         f"{fr(open_risk + intent.risk_quote, '.2f')} pour {fr(total_cap, '.2f')} permis"),
        ("Taille de la position", eq > 0 and intent.cost <= size_cap * (1 + MARGIN) + 1e-9,
         f"{fr(intent.cost, '.2f')} pour {fr(size_cap, '.2f')} permis"),
        ("Argent disponible", intent.cost <= pf.cash * (1 + 1e-9) + 1e-6,
         f"{fr(intent.cost, '.2f')} pour {fr(pf.cash, '.2f')} disponibles"),
        ("Stop sous le prix", intent.stop < intent.entry, f"stop {fr(intent.stop, '.6g')}, achat {fr(intent.entry, '.6g')}"),
        ("Montant minimum", intent.qty * intent.entry >= MIN_NOTIONAL * (1 - 1e-9),
         f"{fr(intent.qty * intent.entry, '.2f')} pour {fr(MIN_NOTIONAL, '.0f')} au moins"),
        ("Qualité des données", pf.data_quality is None or pf.data_quality >= QUALITY_MIN,
         "non mesurée" if pf.data_quality is None else
         f"{fr(pf.data_quality, '.0f')}/100 ({fr(QUALITY_MIN, '.0f')} au moins)"),
        ("Moteur de risque", pf.risk_engine is None or pf.risk_engine[0],
         "non mesuré (essais)" if pf.risk_engine is None else pf.risk_engine[1]),
        ("Instrument négociable", pf.instrument is None or pf.instrument[0],
         ("paper" if not pf.live else "non vérifié (essais)") if pf.instrument is None else pf.instrument[1]),
        ("Plafonds du palier", pf.stage is None or pf.stage[0],
         ("paper" if not pf.live else "non vérifiés (essais)") if pf.stage is None else pf.stage[1]),
    ]
    return checks


EMERGENCY = ("Arrêt d'urgence", "Mode sûr", "Moteur de risque")


def check(intent: OrderIntent, pf: Portfolio, p: Any, now: datetime) -> RiskDecision:
    """Contrôle du risque (RiskCheck.v1) d'une intention d'achat."""
    checks = _limits(intent, pf, p)
    failed = [(name, detail) for name, ok, detail in checks if not ok]
    status = ("EMERGENCY_BLOCK" if any(name in EMERGENCY for name, _d in failed)
              else "REJECTED" if failed else "APPROVED")
    warnings = []
    eq = pf.equity
    if eq > 0 and intent.risk_quote > p.risk_pct * eq * pf.risk_mult + 1e-9 and not failed:
        warnings.append(f"risque de l'achat {fr(intent.risk_quote / (p.risk_pct * eq * pf.risk_mult) * 100 - 100, '.1f')} "
                        "% au-dessus du plan, dans la marge permise")
    return RiskDecision(
        risk_check_id=f"R-{now:%Y%m%d%H%M%S}-{intent.asset}-{uuid.uuid4().hex[:6]}", status=status,
        approved_size=intent.qty if status == "APPROVED" else 0.0, expected_loss=round(intent.risk_quote, 6),
        exposure_pct=round((pf.invested + intent.cost) / eq * 100, 2) if eq > 0 else 0.0,
        open_risk_pct=round((sum(dict(pf.held_risk).values()) + intent.risk_quote) / eq * 100, 3) if eq > 0 else 0.0,
        limit_checks=tuple(checks), warnings=tuple(warnings),
        blocking_reasons=tuple(f"{name.lower()} : {detail}" for name, detail in failed))


def authorize(decision: RiskDecision, pf: Portfolio, now: datetime) -> ExecutionAuthorization:
    """Autorisation d'exécuter (ExecutionAuthorization.v1) : seulement
    après un contrôle approuvé, et en réel seulement si le mode réel est armé."""
    ok = decision.approved and (not pf.live or pf.live_armed)
    return ExecutionAuthorization(
        authorized=ok, authorization_id=f"A-{decision.risk_check_id[2:]}" if ok else "",
        policy_version=POLICY_VERSION, risk_check_id=decision.risk_check_id,
        expiration=(now + timedelta(seconds=AUTH_SECONDS)).isoformat(timespec="seconds"),
        restrictions=RESTRICTIONS if ok else ())


def instrument_check(rules: Any, qty: float, price: float, listed: bool) -> Tuple[bool, str]:
    """Règles de Binance pour un achat réel (§11, §14-15), les mêmes que
    l'exécution (v29) : paire cotée et active, quantité arrondie au pas du
    lot non nulle et dans ses bornes, montant d'au moins le minimum de
    Binance + 5 %. Une règle illisible refuse."""
    min_cost = float(getattr(rules, "min_cost", 0.0) or 10.0)
    min_qty = float(getattr(rules, "min_amount", 0.0) or 0.0)
    max_qty = getattr(rules, "max_amount", None)
    if not listed:
        return False, "paire absente ou inactive chez Binance"
    if not qty > 0 or not price > 0:
        return False, "quantité nulle une fois arrondie au pas du lot"
    if qty < min_qty:
        return False, f"quantité {fr(qty, '.8g')} sous le minimum de Binance {fr(min_qty, '.8g')}"
    if max_qty and qty > float(max_qty):
        return False, f"quantité {fr(qty, '.8g')} au-dessus du maximum de Binance {fr(float(max_qty), '.8g')}"
    if qty * price < min_cost * 1.05:
        return False, f"montant {fr(qty * price, '.2f')} sous le minimum de Binance {fr(min_cost, '.2f')} (+ 5 %)"
    return True, f"quantité {fr(qty, '.8g')} au pas du lot, montant {fr(qty * price, '.2f')} pour {fr(min_cost, '.2f')} au moins"


def fingerprint(pf: Portfolio) -> str:
    """Empreinte de l'état critique au moment du contrôle (§21, T1) : ce qui,
    s'il change avant l'ordre, impose un nouveau contrôle."""
    items = (round(pf.equity, 8), round(pf.cash, 8), tuple(sorted(pf.held_risk)), pf.risk_mult, pf.expected_day,
             tuple(sorted(pf.universe)), tuple(sorted(pf.allowed)), tuple(sorted(pf.vetoed)),
             tuple(sorted(pf.bought_today)), pf.halted, pf.garde_blocked, pf.safe_mode.active, pf.data_quality,
             pf.live, pf.live_armed, pf.production, pf.risk_engine, pf.instrument, pf.stage)
    return hashlib.sha256(repr(items).encode("utf-8")).hexdigest()[:16]


def final_validation(intent: OrderIntent, decision: RiskDecision, auth: ExecutionAuthorization, before: str,
                     pf_now: Portfolio, p: Any, now: datetime) -> FinalValidationResult:
    """Validation finale juste avant l'ordre (§21, T2) : l'autorisation est
    encore valable, liée à ce contrôle et non consommée ; l'arrêt d'urgence
    et le mode sûr sont toujours levés ; si l'état critique a changé depuis
    le contrôle, nouveau contrôle complet (il doit être approuvé)."""
    after = fingerprint(pf_now)
    checks = [
        ("Autorisation valable", auth.valid_at(now),
         f"jusqu'à {auth.expiration}" if auth.authorized else "aucune autorisation"),
        ("Autorisation de ce contrôle", decision.approved and auth.risk_check_id == decision.risk_check_id,
         auth.risk_check_id or "—"),
        ("Autorisation non consommée", intent.idempotency_key not in pf_now.bought_today,
         "déjà utilisée pour un achat" if intent.idempotency_key in pf_now.bought_today else "première utilisation"),
        ("Arrêt d'urgence", not pf_now.halted, "déclenché depuis le contrôle" if pf_now.halted else "prêt"),
        ("Mode sûr", not pf_now.safe_mode.active,
         "activé depuis le contrôle" if pf_now.safe_mode.active else "inactif"),
    ]
    if after != before:
        again = check(intent, pf_now, p, now)
        checks.append(("Nouveau contrôle (état changé)", again.approved,
                       "approuvé" if again.approved else " ; ".join(again.blocking_reasons)))
    failed = [f"{name.lower()} : {detail}" for name, ok, detail in checks if not ok]
    return FinalValidationResult(
        validation_id=str(uuid.uuid4()), risk_check_id=decision.risk_check_id,
        authorization_id=auth.authorization_id, status="BLOCK" if failed else "PASS", checks=tuple(checks),
        snapshot_before=before, snapshot_after=after, revalidated=after != before, reasons=tuple(failed),
        created_at=now.isoformat(timespec="seconds"))


def refusal(e: ContractError, asset: str, now: datetime) -> RiskDecision:
    """Intention invalide (contrat refusé) : achat refusé, avec la raison."""
    return RiskDecision(risk_check_id=f"R-{now:%Y%m%d%H%M%S}-{asset}-{uuid.uuid4().hex[:6]}", status="REJECTED",
                        approved_size=0.0,
                        expected_loss=0.0, exposure_pct=0.0, open_risk_pct=0.0,
                        limit_checks=(("Contrat OrderIntent.v1", False, str(e)),),
                        blocking_reasons=(f"intention d'achat invalide : {e}",))


# ---------- Mode sûr ----------

def safe_mode_path(gcfg: Any) -> str:
    """Fichier du mode sûr, à côté du verrou du bot ("" sans verrou)."""
    return autonomy.sidecar(getattr(gcfg, "lock_file", ""), ".modesur.json")


def safe_mode(gcfg: Any) -> SafeModeState:
    """Mode sûr en vigueur (inactif sans fichier ; fichier illisible : actif)."""
    path = safe_mode_path(gcfg)
    if not path or not os.path.exists(path):
        return SafeModeState()
    data = autonomy.read_json(path)
    return SafeModeState.from_dict(data) if data else SafeModeState(
        active=True, reason="fichier du mode sûr illisible", activated_by="bot")


def set_safe_mode(gcfg: Any, active: bool, now: datetime, by: str = "vous",
                  reason: str = "") -> SafeModeState:
    """Active ou lève le mode sûr."""
    path = safe_mode_path(gcfg)
    if not path:
        raise ValueError("mode sûr impossible sans fichier de verrou (TG_LOCK_FILE)")
    st = SafeModeState(active=active, reason=reason or ("demandé" if active else ""),
                       activated_at=now.isoformat(timespec="seconds") if active else "",
                       activated_by=by if active else "")
    autonomy.write_json(path, {"active": st.active, "reason": st.reason, "activated_at": st.activated_at,
                               "activated_by": st.activated_by, "version": st.version})
    return st


def cmd_safe_mode(gcfg: Any, action: str, now: datetime, say: Any = print) -> int:
    """Commande `mode-sur on|off|status`."""
    if action == "on":
        set_safe_mode(gcfg, True, now, reason="demandé par la commande mode-sur")
        say("Mode sûr ACTIF : plus aucun achat. Le bot continue de surveiller, protéger et vendre ses "
            "positions. Pour le lever : python trendguard_bot.py mode-sur off")
    elif action == "off":
        set_safe_mode(gcfg, False, now)
        say("Mode sûr levé : les achats reprennent à la prochaine décision, s'il y a lieu.")
    else:
        st = safe_mode(gcfg)
        say(f"Mode sûr : {'ACTIF depuis ' + st.activated_at + ' (' + st.reason + ')' if st.active else 'inactif'}")
    return 0


def describe_final(r: FinalValidationResult) -> str:
    """Une phrase pour le journal : la validation finale."""
    if r.passed:
        return ("validation finale : " + (f"{len(r.checks)} vérifications conformes"
                                          + (" (état changé, nouveau contrôle approuvé)" if r.revalidated else "")))
    return "achat arrêté à la validation finale : " + " ; ".join(r.reasons)


def describe(decision: RiskDecision, auth: Optional[ExecutionAuthorization] = None) -> str:
    """Une phrase pour le journal et le raisonnement."""
    if decision.approved:
        return (f"achat autorisé ({auth.authorization_id if auth else decision.risk_check_id}) : "
                f"{len(decision.limit_checks)} contrôles conformes, risque cumulé "
                f"{fr(decision.open_risk_pct, '.2f')} % du capital")
    kind = "bloqué (urgence)" if decision.status == "EMERGENCY_BLOCK" else "refusé"
    return f"achat {kind} : " + " ; ".join(decision.blocking_reasons)
