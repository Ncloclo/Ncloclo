"""
Moteur d'autorisation (prompt maître, étape 15 ; docs/MOTEUR_AUTORISATION.md)
: qui peut faire quoi, sur quelle ressource, à quelles conditions, et ce que
personne ne peut jamais faire.

    identité (vous, le bot, la porte d'exécution, la règle, le panneau,
    Rachelle, les IA de la veille, le comité, l'évolution, le noyau de
    savoir, le bot libre, la maintenance, les routines dans le nuage) →
    rôle → permissions (action × ressource) → conditions du moment (réel
    armé, porte du réel ouverte, évaluation du risque valide, contrôle de la
    porte approuvé…) → décision ALLOW ou DENY avec ses raisons

Refus par défaut : une identité, une action ou une condition inconnue donne
DENY. Séparation des tâches vérifiée : celui qui propose un achat (la règle)
ne l'approuve pas (la porte), celle qui l'approuve ne l'exécute pas (le bot),
et aucune IA, aucun agent ne peut autoriser, armer le réel, changer le
risque, toucher aux secrets ou fusionner du code. Vous êtes le seul
propriétaire : le « quatre yeux » est tenu par deux réglages explicites pour
armer le réel et par la porte du réel, mesurée et fermée tant que ses
conditions manquent. L'autorisation d'un achat (porte.py) vaut 5 minutes et
sert une fois : la clé d'unicité de l'ordre interdit de la rejouer.

    python trendguard_bot.py autorisation                       # la matrice et la séparation des tâches
    python trendguard_bot.py autorisation verifier rachelle ARM_LIVE
"""

from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .contrats import AuthorizationDecision

POLICY_VERSION = "autorisation-1.0.0"
TTL_SECONDS = 300
ACTIONS = ("READ", "ANALYZE", "RECOMMEND", "PROPOSE_BUY", "AUTHORIZE_BUY", "TRADE_PAPER", "TRADE_LIVE", "SELL",
           "HALT", "DEFER_BUY", "CHANGE_SETTINGS", "CHANGE_RISK", "SELECT_CRYPTOS", "ARM_LIVE", "SAFE_MODE_ON",
           "SAFE_MODE_OFF", "KILL_RESET", "SET_SECRETS", "START_STOP_BOT", "RUN_REPORT", "TEST_ALERTS",
           "PROPOSE_CODE", "MERGE_CODE", "INSTALL_UPDATE")
ACTION_FR = {"READ": "lire", "ANALYZE": "analyser", "RECOMMEND": "recommander", "PROPOSE_BUY": "proposer un achat",
             "AUTHORIZE_BUY": "autoriser un achat", "TRADE_PAPER": "acheter en paper", "TRADE_LIVE": "acheter en réel",
             "SELL": "vendre (réduire le risque)", "HALT": "déclencher l'arrêt d'urgence",
             "DEFER_BUY": "reporter un achat", "CHANGE_SETTINGS": "changer les réglages de la règle",
             "CHANGE_RISK": "changer les plafonds de risque", "SELECT_CRYPTOS": "choisir les cryptos",
             "ARM_LIVE": "armer le réel", "SAFE_MODE_ON": "poser le mode sûr", "SAFE_MODE_OFF": "lever le mode sûr",
             "KILL_RESET": "reprendre après l'arrêt d'urgence", "SET_SECRETS": "saisir des clés ou mots de passe",
             "START_STOP_BOT": "démarrer ou arrêter le bot", "RUN_REPORT": "lancer ou envoyer le rapport",
             "TEST_ALERTS": "tester les alertes", "PROPOSE_CODE": "proposer du code (Pull Request)",
             "MERGE_CODE": "fusionner du code", "INSTALL_UPDATE": "installer une mise à jour validée"}
CRITICAL = ("AUTHORIZE_BUY", "TRADE_LIVE", "CHANGE_RISK", "ARM_LIVE", "SAFE_MODE_OFF", "KILL_RESET", "SET_SECRETS",
            "MERGE_CODE")
PRINCIPAL_TYPES = ("HUMAN_USER", "SYSTEM", "EXECUTION_SERVICE", "SERVICE_ACCOUNT", "AGENT", "AI_MODEL", "AUTOMATION",
                   "EXTERNAL")
# Identités : type, rôle, qui répond d'elles.
PRINCIPALS: Dict[str, Dict[str, str]] = {
    "vous": {"type": "HUMAN_USER", "role": "SYSTEM_OWNER", "name": "vous, le propriétaire"},
    "panneau": {"type": "SERVICE_ACCOUNT", "role": "OWNER_SESSION", "name": "le panneau, connecté par votre mot de passe"},
    "bot": {"type": "EXECUTION_SERVICE", "role": "EXECUTION_SERVICE", "name": "le bot (boucle d'exécution)"},
    "porte": {"type": "SYSTEM", "role": "AUTHORIZATION_OFFICER", "name": "la porte d'exécution (règles fixes)"},
    "regle": {"type": "SYSTEM", "role": "STRATEGY", "name": "la règle de trading"},
    "rachelle": {"type": "AGENT", "role": "ASSISTANT", "name": "Rachelle, l'assistante du panneau"},
    "ia": {"type": "AI_MODEL", "role": "ADVISOR", "name": "les IA de la veille"},
    "comite": {"type": "AGENT", "role": "ADVISOR", "name": "le comité d'agents"},
    "evolution": {"type": "AUTOMATION", "role": "EVOLUTION", "name": "l'évolution encadrée"},
    "savoir": {"type": "AUTOMATION", "role": "KNOWLEDGE", "name": "le noyau de savoir"},
    "libre": {"type": "AUTOMATION", "role": "FREE_BOT", "name": "le bot libre (son propre argent fictif)"},
    "maintenance": {"type": "AUTOMATION", "role": "MAINTENANCE", "name": "la maintenance autonome"},
    "nuage": {"type": "EXTERNAL", "role": "CLOUD_ROUTINE", "name": "les routines Claude dans le nuage"},
}
# Rôle → {action : conditions qui doivent toutes valoir vrai dans le contexte}.
ROLES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "SYSTEM_OWNER": {a: () for a in ("READ", "ANALYZE", "RECOMMEND", "CHANGE_SETTINGS", "CHANGE_RISK", "SELECT_CRYPTOS",
                                     "SAFE_MODE_ON", "SAFE_MODE_OFF", "KILL_RESET", "SET_SECRETS", "START_STOP_BOT",
                                     "RUN_REPORT", "TEST_ALERTS", "MERGE_CODE", "INSTALL_UPDATE")}
    | {"ARM_LIVE": ("two_settings",)},
    "OWNER_SESSION": {a: ("session_ok",) for a in ("READ", "ANALYZE", "SELECT_CRYPTOS", "START_STOP_BOT", "RUN_REPORT",
                                                   "TEST_ALERTS")},
    "EXECUTION_SERVICE": {"READ": (), "ANALYZE": (), "SELL": (), "HALT": (), "SAFE_MODE_ON": (),
                          "TRADE_PAPER": ("paper", "porte_approved", "authorization_valid"),
                          "TRADE_LIVE": ("live", "live_armed", "live_gate_open", "risk_assessment_valid",
                                         "porte_approved", "authorization_valid")},
    "AUTHORIZATION_OFFICER": {"READ": (), "AUTHORIZE_BUY": ("risk_check_approved",)},
    "STRATEGY": {"READ": (), "ANALYZE": (), "PROPOSE_BUY": ()},
    "ASSISTANT": {"READ": (), "ANALYZE": (), "RECOMMEND": ()},
    "ADVISOR": {"READ": (), "ANALYZE": (), "RECOMMEND": ()},
    "EVOLUTION": {"READ": (), "ANALYZE": (), "RECOMMEND": (),
                  "CHANGE_SETTINGS": ("trials_passed", "permitted_parameter")},
    "KNOWLEDGE": {"READ": (), "ANALYZE": (), "DEFER_BUY": ("binance_announcement",)},
    "FREE_BOT": {"READ": (), "ANALYZE": (), "TRADE_PAPER": ("own_portfolio",)},
    "MAINTENANCE": {"READ": (), "INSTALL_UPDATE": ("merged_by_owner", "ci_green", "fast_forward", "not_live")},
    "CLOUD_ROUTINE": {"READ": (), "ANALYZE": (), "PROPOSE_CODE": ()},
}
CONDITIONS_FR = {"two_settings": "deux réglages explicites (mode réel et confirmation écrite)",
                 "session_ok": "session du panneau ouverte par votre mot de passe", "paper": "mode paper",
                 "porte_approved": "contrôle de la porte approuvé", "authorization_valid": "autorisation valable",
                 "live": "mode réel", "live_armed": "réel armé par vous", "live_gate_open": "porte du réel ouverte",
                 "risk_assessment_valid": "évaluation du risque du jour valide",
                 "risk_check_approved": "contrôle du risque approuvé", "trials_passed": "épreuves réussies",
                 "permitted_parameter": "réglage permis à son niveau (jamais le risque, les positions, l'arrêt "
                                        "d'urgence ni le réel)",
                 "binance_announcement": "annonce de Binance (retrait de la cote)",
                 "own_portfolio": "son propre portefeuille fictif", "merged_by_owner": "fusionné par vous sur GitHub",
                 "ci_green": "contrôles GitHub au vert", "fast_forward": "avance rapide seulement",
                 "not_live": "jamais en mode réel"}
# Délégations explicites, sans re-délégation : (de, à, actions).
DELEGATIONS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("vous", "panneau", tuple(ROLES["OWNER_SESSION"])),
    ("vous", "evolution", ("CHANGE_SETTINGS",)),
    ("vous", "maintenance", ("INSTALL_UPDATE",)),
)


def decide(principal: str, action: str, resource: str = "bot", context: Optional[Dict[str, Any]] = None,
           now: Optional[datetime] = None) -> AuthorizationDecision:
    """Qui peut faire quoi (§5-10) : refus par défaut ; l'identité doit
    exister, son rôle avoir la permission, et chaque condition valoir
    exactement vrai dans le contexte (une condition absente = refus)."""
    now = now or datetime.now(timezone.utc)
    ctx = context or {}
    who = PRINCIPALS.get(principal)
    reasons, checked = [], []
    if who is None:
        reasons.append(f"identité inconnue : {principal!r}")
    elif action not in ACTIONS:
        reasons.append(f"action inconnue : {action!r}")
    else:
        perms = ROLES.get(who["role"], {})
        if action not in perms:
            reasons.append(f"pas permis à {who['name']} : {ACTION_FR[action]}")
        else:
            for cond in perms[action]:
                ok = ctx.get(cond) is True
                checked.append((cond, ok))
                if not ok:
                    reasons.append(f"condition non remplie : {CONDITIONS_FR.get(cond, cond)}")
    allowed = not reasons
    return AuthorizationDecision(
        decision_id=str(uuid.uuid4()), principal=principal, principal_type=(who or {}).get("type", "EXTERNAL"),
        action=action, resource=resource, allowed=allowed,
        reasons=tuple(reasons) or (f"permis : {ACTION_FR.get(action, action)}",), conditions=tuple(checked),
        policy_version=POLICY_VERSION, created_at=now.isoformat(timespec="seconds"),
        expires_at=(now + timedelta(seconds=TTL_SECONDS)).isoformat(timespec="seconds"))


def holders(action: str) -> List[str]:
    """Les identités dont le rôle porte cette permission."""
    return [p for p, who in PRINCIPALS.items() if action in ROLES.get(who["role"], {})]


def separation_of_duties() -> List[str]:
    """Séparation des tâches (§15-16, §26-28) : défauts de la matrice, vide
    si tout tient."""
    out = []
    for p, who in PRINCIPALS.items():
        perms = set(ROLES.get(who["role"], {}))
        if {"PROPOSE_BUY", "AUTHORIZE_BUY"} <= perms:
            out.append(f"{p} propose et autorise un achat")
        if {"AUTHORIZE_BUY"} & perms and {"TRADE_PAPER", "TRADE_LIVE"} & perms:
            out.append(f"{p} autorise et exécute")
        if who["type"] in ("AGENT", "AI_MODEL", "EXTERNAL") and perms & set(CRITICAL + ("TRADE_PAPER", "SELL")):
            out.append(f"{p} ({who['type']}) a une permission critique : {sorted(perms & set(CRITICAL))}")
        if who["type"] == "AUTOMATION" and perms & set(CRITICAL):
            out.append(f"{p} (automatisme) a une permission critique : {sorted(perms & set(CRITICAL))}")
    for action in ("ARM_LIVE", "CHANGE_RISK", "KILL_RESET", "SAFE_MODE_OFF", "SET_SECRETS", "MERGE_CODE"):
        if holders(action) != ["vous"]:
            out.append(f"{ACTION_FR[action]} : réservé à vous, pas à {holders(action)}")
    if holders("TRADE_LIVE") != ["bot"]:
        out.append(f"acheter en réel : réservé au bot, pas à {holders('TRADE_LIVE')}")
    for src, dst, acts in DELEGATIONS:
        if src not in PRINCIPALS or dst not in PRINCIPALS:
            out.append(f"délégation inconnue : {src} → {dst}")
            continue
        extra = set(acts) - set(ROLES[PRINCIPALS[src]["role"]])
        if extra:
            out.append(f"délégation {src} → {dst} au-delà de ses propres droits : {sorted(extra)}")
        if any(d_src == dst for d_src, _d, _a in DELEGATIONS):
            out.append(f"re-délégation interdite : {dst} délègue à son tour")
    return out


def trade_context(pf: Any, decision: Any, auth: Any, now: datetime) -> Dict[str, bool]:
    """Le contexte d'un achat du bot, tiré de la vue de la porte, de sa
    décision et de son autorisation. Comme la porte : une mesure absente
    (None) n'existe que dans les essais, où la porte du réel et le moteur de
    risque sont éteints ; en service, les deux sont mesurés."""
    return {"paper": not pf.live, "live": pf.live, "live_armed": pf.live_armed,
            "live_gate_open": pf.production is None or bool(pf.production[0]),
            "risk_assessment_valid": pf.risk_engine is None or bool(pf.risk_engine[0]),
            "porte_approved": decision.status == "APPROVED", "authorization_valid": auth.valid_at(now),
            "risk_check_approved": decision.status == "APPROVED"}


def describe(view: Optional[Dict[str, Any]]) -> str:
    """Une phrase pour le raisonnement, le rapport et Rachelle."""
    if not view or not view.get("checked"):
        return "aucun achat soumis au moteur d'autorisation aujourd'hui"
    return (f"{view['checked']} achat(s) : droit du bot vérifié ({POLICY_VERSION}) ; "
            + ("ÉCART avec la porte d'exécution : " + ", ".join(a.upper() for a in view["mismatch"])
               if view.get("mismatch") else "mêmes réponses que la porte d'exécution"))


def render() -> str:
    """La matrice des droits et la séparation des tâches, lisibles."""
    lines = [f"Autorisation {POLICY_VERSION} : refus par défaut ; une condition absente est un refus.", ""]
    for p, who in PRINCIPALS.items():
        perms = ROLES.get(who["role"], {})
        acts = ", ".join(ACTION_FR[a] + (" (si " + " et ".join(CONDITIONS_FR[c] for c in conds) + ")" if conds else "")
                         for a, conds in perms.items())
        lines.append(f"- {who['name']} [{who['type']}] : {acts or 'rien'}")
    problems = separation_of_duties()
    lines += ["", "Séparation des tâches : tenue (la règle propose, la porte autorise, le bot exécute ; aucune IA ni "
              "agent n'a de droit critique)." if not problems else "Séparation des tâches : " + " ; ".join(problems)]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `autorisation` : la matrice (défaut), ou `verifier <identité>
    <action>` pour une décision."""
    import v29
    ap = argparse.ArgumentParser(description="Moteur d'autorisation de TrendGuard")
    ap.add_argument("action", nargs="?", default="matrice", choices=["matrice", "verifier"])
    ap.add_argument("principal", nargs="?", default="")
    ap.add_argument("demande", nargs="?", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    if args.action == "matrice":
        print(render())
        return 0 if not separation_of_duties() else 1
    d = decide(args.principal, args.demande.upper())
    print(("PERMIS" if d.allowed else "REFUSÉ") + " : " + " ; ".join(d.reasons))
    return 0 if d.allowed else 1
