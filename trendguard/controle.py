"""
Plan de contrôle de la production (prompt maître, étape 18 ;
docs/PLAN_DE_CONTROLE.md) : une vue d'exploitation de tout TrendGuard, en
lecture seule.

    services (registre, paliers de criticité) → santé (vivant, prêt, métier)
    → dépendances (cascade, point unique de défaillance) → objectifs de
    service et budget d'erreur → incidents P0 à P4 regroupés par cause, avec
    leur procédure → changements, configuration, capacité, sauvegardes
    (RPO, RTO mesurés) → superviseur borné → examen AC-001 à AC-060 → verdict

Il assemble ce que le bot mesure déjà (disponibilité, rapport de la nuit,
audit, journal financier, sauvegardes, alertes, porte d'exécution, moteur de
risque) ; il n'agit sur rien : une panne se voit, avec la procédure à suivre
et ce que vous seul pouvez décider. Le superviseur (autonomy.py) redémarre le
bot après un plantage ; il ne peut ni changer le risque, ni lever l'arrêt
d'urgence ou le mode sûr, ni autoriser un achat (moteur d'autorisation).

    python trendguard_bot.py controle                    # l'état du jour
    python trendguard_bot.py controle --out docs/CONTROLE.md
"""

from __future__ import annotations

import argparse
import dataclasses
import glob
import hashlib
import os
import pathlib
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import v29

from . import (
    acceptation,
    audit,
    autonomy,
    autorisation,
    donnees,
    moteur_risque,
    politique,
    porte,
    porte_examen,
    report_security,
    systeme,
    uptime,
)
from . import trend_strategy as ts
from .bot_types import last_closed_day
from .contrats import ContractError, ControlPlaneReport
from .texte import fr

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = "controle-1.0.0"
STATUS_FR = {"HEALTHY": "en bonne santé", "DEGRADED": "dégradé", "DOWN": "en panne", "UNKNOWN": "non mesuré"}
TIER_FR = {0: "sûreté critique", 1: "finance critique", 2: "intelligence", 3: "support"}
SEVERITY_FR = {"P0": "critique", "P1": "majeur", "P2": "dégradé", "P3": "mineur", "P4": "information"}
TIER_SEVERITY = {0: "P0", 1: "P1", 2: "P2", 3: "P3"}
BOT_STALE_SEC = 10 * 60          # sans cycle depuis 10 minutes : le bot ne tourne plus
BACKUP_RPO_H = 26.0              # une sauvegarde par nuit (00:30 UTC), deux heures de marge
RTO_TARGET_SEC = 60.0            # rouvrir une sauvegarde et vérifier son journal
DISK_MIN_GB, DISK_LOW_GB = 2.0, 5.0
SLO = (("SLO-DISPONIBILITE", "bot en marche sur 7 jours", 99.0),
       ("SLO-DECISION", "décision du jour prise après la clôture", 100.0),
       ("SLO-DONNEES", "données du jour d'au moins 50 sur 100", 100.0),
       ("SLO-SAUVEGARDE", f"sauvegarde de moins de {BACKUP_RPO_H:.0f} heures", 100.0),
       ("SLO-RAPPORT", "rapport de la nuit fait", 100.0))
AUTONOMY = {"vous": "décide", "bot": "L4 : achète et vend sous la porte d'exécution",
            "porte": "L3 : refuse ou autorise un achat (règles fixes)", "regle": "L1 : propose",
            "superviseur": "L2 : redémarre le bot après un plantage",
            "maintenance": "L2 : installe une mise à jour fusionnée par vous",
            "evolution": "L3 : change un réglage permis, après ses épreuves",
            "savoir": "L3 : reporte un achat sur une annonce de Binance",
            "libre": "L4 : son propre argent fictif", "rachelle": "L1 : explique", "ia": "L1 : conseille",
            "comite": "L1 : donne son avis", "panneau": "L1 : transmet vos actions", "nuage": "L1 : propose du code"}


@dataclasses.dataclass(frozen=True)
class Service:
    """Un service du registre : identifiant, nom, palier de criticité (0 :
    sûreté critique … 3 : support), dépendances, extérieur ou non."""
    sid: str
    name: str
    tier: int
    deps: Tuple[str, ...] = ()
    external: bool = False


SERVICES: Tuple[Service, ...] = (
    Service("pc", "Ordinateur (disque, mémoire)", 1),
    Service("internet", "Connexion Internet", 1, external=True),
    Service("binance", "Binance (cours, bougies, ordres)", 1, ("internet",), external=True),
    Service("base", "Base du bot (état, contexte des ordres)", 0, ("pc",)),
    Service("bot", "Boucle du bot (décision, stops, achats, ventes)", 1, ("pc", "base", "binance")),
    Service("superviseur", "Superviseur (relance du bot)", 1, ("pc",)),
    Service("donnees", "Données de marché du jour (qualité)", 1, ("binance",)),
    Service("porte", "Porte d'exécution (politiques, autorisation)", 0, ("bot",)),
    Service("risque", "Moteur de risque (évaluation du jour)", 0, ("bot", "donnees")),
    Service("urgence", "Arrêt d'urgence et mode sûr", 0, ("bot", "base")),
    Service("audit", "Journal d'audit chaîné", 0, ("pc",)),
    Service("journal", "Journal financier", 0, ("base",)),
    Service("sauvegarde", "Sauvegardes et restauration d'essai", 1, ("pc", "base")),
    Service("rapport", "Rapport de la nuit", 3, ("pc", "base")),
    Service("alertes", "Alertes (e-mail, messages)", 2, ("internet",)),
    Service("veille", "Veille des annonces de Binance", 2, ("internet",)),
    Service("savoir", "Noyau de savoir", 3, ("internet",)),
    Service("panneau", "Panneau de contrôle", 3, ("pc", "base")),
)
RUNBOOKS: Dict[str, Dict[str, Any]] = {
    "RB-BOT-ARRETE": {"title": "Le bot ne tourne plus", "service": "bot", "steps": (
        "Regardez si l'ordinateur était éteint ou en veille (le superviseur relance le bot au réveil).",
        "Sinon : python trendguard_bot.py diagnostic (lecture seule), puis relancez le bot depuis le panneau.",
        "En réel, les stops posés chez Binance protègent les positions pendant l'arrêt."), "reversible": True},
    "RB-ARRET-URGENCE": {"title": "Arrêt d'urgence déclenché", "service": "urgence", "steps": (
        "Lisez la raison dans le panneau (baisse maximale, ordre inconnu…).",
        "Le bot ne rachète plus, il protège et vend ; rien à faire dans l'urgence.",
        "Reprise : seule votre commande python trendguard_bot.py resume, ou la reprise prudente après "
        "60 jours si le marché est redevenu haussier."), "reversible": True},
    "RB-ORDRE-INCONNU": {"title": "Ordre ou position inconnus chez Binance (réel)", "service": "bot", "steps": (
        "La paire s'est arrêtée seule : aucun nouvel ordre sur elle.",
        "Comparez l'historique des ordres sur Binance avec le panneau.",
        "Reprise après vérification : TG_ALLOW_RECOVERY=true pour un seul démarrage."), "reversible": True},
    "RB-AUDIT-ABIME": {"title": "Journal d'audit ou journal financier abîmé", "service": "audit", "steps": (
        "Ne corrigez rien à la main : le bot continue d'écrire à la suite.",
        "Gardez le fichier tel quel et signalez-le ; la sauvegarde de la nuit garde la version précédente."),
        "reversible": True},
    "RB-SAUVEGARDE": {"title": "Sauvegarde manquante ou abîmée", "service": "sauvegarde", "steps": (
        "Vérifiez la place sur le disque.", "Relancez le rapport : python trendguard_bot.py rapport maintenant.",
        "Restauration : arrêtez le bot, copiez la dernière sauvegarde saine du dossier sauvegardes à la place "
        "de la base, relancez."), "reversible": True},
    "RB-BINANCE": {"title": "Binance ou Internet indisponible", "service": "binance", "steps": (
        "Les achats sont différés, les protections restent posées chez Binance.",
        "Rien à faire : le bot réessaie seul ; vérifiez la connexion si cela dure plus d'une heure."),
        "reversible": True},
    "RB-DONNEES": {"title": "Données du jour abîmées", "service": "donnees", "steps": (
        "Aucun achat sous 50 sur 100 : la décision attend des données saines.",
        "Rien à faire ; si cela dure, lancez le diagnostic."), "reversible": True},
    "RB-CLES": {"title": "Clé Binance exposée ou refusée", "service": "binance", "steps": (
        "Supprimez la clé sur Binance, créez-en une nouvelle sans droit de retrait.",
        "Enregistrez-la par la saisie masquée : python trendguard_bot.py set-keys."), "reversible": False},
    "RB-ALERTES": {"title": "Alertes en panne", "service": "alertes", "steps": (
        "Lisez la cause dans le panneau (mot de passe refusé…).",
        "Corrigez par python trendguard_bot.py alerts configurer, puis alerts tester."), "reversible": True},
    "RB-PORTE": {"title": "Écart entre la porte d'exécution et les politiques ou l'autorisation", "service": "porte",
                 "steps": ("La porte reste seule à appliquer : rien n'est changé dans les achats.",
                           "Signalez l'écart (journal du bot, ligne [POLITIQUE] ou [AUTORISATION]) : le registre ou "
                           "la matrice est à corriger dans le code."), "reversible": True},
    "RB-RISQUE": {"title": "Moteur de risque sans évaluation valide", "service": "risque", "steps": (
        "Aucun achat tant que l'évaluation du jour manque : les ventes et les stops continuent.",
        "Elle se refait à la décision suivante ; si cela dure, lancez python trendguard_bot.py risque."),
        "reversible": True},
    "RB-DISQUE": {"title": "Disque presque plein", "service": "pc", "steps": (
        "Libérez de la place (le rapport de la nuit montre les dossiers les plus lourds).",
        "Sous 2 Go, la base et les sauvegardes risquent de s'abîmer."), "reversible": True},
}
SERVICE_RUNBOOK = {"bot": "RB-BOT-ARRETE", "superviseur": "RB-BOT-ARRETE", "urgence": "RB-ARRET-URGENCE",
                   "audit": "RB-AUDIT-ABIME", "journal": "RB-AUDIT-ABIME", "base": "RB-AUDIT-ABIME",
                   "sauvegarde": "RB-SAUVEGARDE", "binance": "RB-BINANCE", "internet": "RB-BINANCE",
                   "donnees": "RB-DONNEES", "alertes": "RB-ALERTES", "pc": "RB-DISQUE", "porte": "RB-PORTE",
                   "risque": "RB-RISQUE"}
SUPERVISOR_FORBIDDEN = ("CHANGE_RISK", "KILL_RESET", "SAFE_MODE_OFF", "AUTHORIZE_BUY", "TRADE_LIVE", "TRADE_PAPER",
                        "ARM_LIVE", "SET_SECRETS", "MERGE_CODE", "CHANGE_SETTINGS")


def runbook_version(rid: str) -> str:
    """Version d'une procédure : empreinte de son texte (une procédure
    changée change de version)."""
    rb = RUNBOOKS[rid]
    text = rb["title"] + "|" + "|".join(rb["steps"])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ══════════════════════════════════════════════════════════════════════
# Santé des services (§7)
# ══════════════════════════════════════════════════════════════════════

def _h(status: str, detail: str, live: Optional[bool] = None, ready: Optional[bool] = None,
       semantic: Optional[bool] = None) -> Dict[str, Any]:
    return {"status": status, "detail": detail, "live": live, "ready": ready, "semantic": semantic}


def _age_h(path: str, now: float) -> Optional[float]:
    try:
        return (now - os.path.getmtime(path)) / 3600
    except OSError:
        return None


def _panel_up(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=0.3):
            return True
    except OSError:
        return False


def backups(gcfg: Any) -> List[str]:
    """Sauvegardes de la base du bot, de la plus ancienne à la plus récente."""
    lock = getattr(gcfg, "lock_file", "") or ""
    base = autonomy.sidecar(lock, "") if lock else ""
    folder = os.path.join(os.path.dirname(base) if base else v29.APP_DIR, "sauvegardes")
    db = getattr(gcfg, "db_file", "") or ""
    stem = os.path.splitext(os.path.basename(db))[0] if db and db != ":memory:" else ""
    return sorted(glob.glob(os.path.join(folder, f"{stem}-????-??-??.db"))) if stem else []


def recovery(gcfg: Any, now: float,
             restore: Optional[Callable[[str], str]] = report_security.restore_test) -> Dict[str, Any]:
    """Reprise après sinistre (§25-26) : RPO (âge de la dernière sauvegarde)
    et RTO mesuré (temps pour rouvrir la dernière sauvegarde, relire l'état et
    vérifier son journal financier, en lecture seule ; sans `restore`, l'âge
    seulement)."""
    files = backups(gcfg)
    if not files:
        return {"count": 0, "rpo_h": None, "rto_s": None, "restore": "aucune sauvegarde", "ok": None}
    last = files[-1]
    t = time.perf_counter()
    text = restore(last) if restore else "restauration d'essai faite par le rapport de la nuit"
    rto = time.perf_counter() - t if restore else None
    rpo = _age_h(last, now)
    ok = (not text.startswith("ÉCHEC") and rpo is not None and rpo <= BACKUP_RPO_H
          and (rto is None or rto <= RTO_TARGET_SEC))
    return {"count": len(files), "rpo_h": rpo, "rto_s": rto, "restore": text, "ok": ok,
            "last": os.path.basename(last)}


def health(gcfg: Any, state: Dict[str, Any], now: datetime, deps: Optional[systeme.Deps] = None,
           dr: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Santé de chaque service (§7) : vivant, prêt, correct pour le métier
    (une réponse n'est pas une bonne santé : un bot qui tourne sans décider
    est dégradé). Une mesure impossible est « non mesuré », jamais une
    réussite."""
    deps = deps or systeme.Deps()
    ts_now = now.timestamp()
    out: Dict[str, Dict[str, Any]] = {}
    res = deps.extra.get("resources") if "resources" in deps.extra else systeme.pc_resources(None, str(ROOT))
    if res:
        free = float(res.get("disk_free") or 0.0)
        mem = (res["memory_used"] / res["memory_limit"]) if res.get("memory_limit") else None
        st = "DOWN" if free < DISK_MIN_GB else "DEGRADED" if free < DISK_LOW_GB or (mem or 0) > 0.95 else "HEALTHY"
        out["pc"] = _h(st, f"{fr(free, '.1f')} Go libres" + (f", mémoire {fr(mem * 100, '.0f')} %" if mem else ""),
                       True, st != "DOWN", st == "HEALTHY")
    else:
        out["pc"] = _h("UNKNOWN", "ressources non mesurées")
    last = state.get("last_cycle_ts")
    age = ts_now - float(last) if last else None
    alive = age is not None and age <= BOT_STALE_SEC
    day = last_closed_day(now, getattr(gcfg, "decision_delay_sec", 120))
    decided = state.get("last_decision_day") == day
    deferred = bool(state.get("decision_deferred_since"))
    out["bot"] = _h("DOWN" if not alive else "HEALTHY" if decided and not deferred else "DEGRADED",
                    ("aucun cycle noté" if age is None else f"dernier cycle il y a {uptime.fdur(age)}")
                    + ("" if decided else f" ; décision du {day} pas encore prise")
                    + (" ; décision différée (données ou Binance)" if deferred else ""), alive, alive, decided)
    synced = state.get("clock_synced_at")
    try:
        s_age = ts_now - (float(synced) if isinstance(synced, (int, float)) else
                          datetime.fromisoformat(str(synced)).timestamp())
    except (TypeError, ValueError):
        s_age = None
    out["binance"] = (_h("DEGRADED", "décision différée : Binance ou ses données ne répondent pas bien", True, False,
                         False) if deferred else
                      _h("HEALTHY", f"horloge de Binance relue il y a {uptime.fdur(s_age)}", True, True, True)
                      if s_age is not None and s_age <= 3 * 3600 else
                      _h("UNKNOWN", "pas de lecture récente de Binance (bot arrêté ?)"))
    out["internet"] = (_h("HEALTHY", "Binance joignable", True, True, True) if out["binance"]["status"] == "HEALTHY"
                       else _h("UNKNOWN", "non mesuré sans lecture récente de Binance"))
    sup = autonomy.supervisor_status(gcfg) if getattr(gcfg, "lock_file", "") else {}
    out["superviseur"] = (_h("HEALTHY", f"en marche, {sup.get('restarts', 0)} relance(s)", True, True, True)
                          if sup.get("running") else
                          _h("DOWN", "à l'arrêt : le bot ne serait pas relancé après un plantage", False, False, False)
                          if getattr(gcfg, "lock_file", "") not in ("", os.devnull, "/dev/null") else
                          _h("UNKNOWN", "sans fichier de verrou (essai)"))
    q = state.get("qualite") or {}
    out["donnees"] = (_h("HEALTHY" if (q.get("score") or 0) >= porte.QUALITY_MIN else "DEGRADED",
                         f"qualité du {q.get('day')} : {fr(q.get('score') or 0, '.0f')}/100", True, True,
                         (q.get("score") or 0) >= porte.QUALITY_MIN) if q else _h("UNKNOWN", "pas de note des données"))
    pol, aut = state.get("politique") or {}, state.get("autorisation") or {}
    bad = (pol.get("mismatch") or []) + (aut.get("mismatch") or [])
    out["porte"] = _h("DEGRADED" if bad else "HEALTHY",
                      ("écart entre la porte et les politiques ou l'autorisation : " + ", ".join(bad)) if bad
                      else f"{porte.POLICY_VERSION}, {politique.REGISTRY_VERSION}, {autorisation.POLICY_VERSION}",
                      True, True, not bad)
    if getattr(gcfg, "risk_engine", False):
        ok, why = moteur_risque.gate((state.get("moteur_risque") or {}).get("pre"), day, now)
        out["risque"] = _h("HEALTHY" if ok else "DEGRADED", why, True, ok, ok)
    else:
        out["risque"] = _h("UNKNOWN", "moteur de risque désactivé (essai)")
    sm = porte.safe_mode(gcfg)
    halted = bool(state.get("halted"))
    out["urgence"] = _h("HEALTHY", ("arrêt d'urgence DÉCLENCHÉ : " + str(state.get("halt_reason") or "raison inconnue"))
                        if halted else ("mode sûr ACTIF" if sm.active else "prêts (ni arrêt d'urgence, ni mode sûr)"),
                        True, True, True)
    ap = audit.path_for(gcfg)
    if ap and os.path.exists(ap):
        v = audit.verify(ap)
        out["audit"] = _h("HEALTHY" if v["ok"] else "DOWN", audit.describe(v), True, v["ok"], v["ok"])
    else:
        out["audit"] = _h("UNKNOWN", "journal d'audit absent")
    jp = donnees.path_for(gcfg)
    if jp and jp != ":memory:" and os.path.exists(jp):
        try:
            j = donnees.Journal(jp, readonly=True)
            try:
                jv = j.verify()
            finally:
                j.close()
            out["journal"] = _h("HEALTHY" if jv["ok"] else "DOWN", donnees.describe(jv), True, jv["ok"], jv["ok"])
        except Exception as e:           # illisible : jamais une réussite
            out["journal"] = _h("DOWN", f"illisible ({type(e).__name__})", False, False, False)
        db = report_security.check_database(jp)
        out["base"] = _h({True: "HEALTHY", False: "DOWN"}.get(db["ok"], "UNKNOWN"), db["detail"], True, db["ok"],
                         db["ok"])
    else:
        out["journal"] = _h("UNKNOWN", "journal financier absent")
        out["base"] = _h("UNKNOWN", "base en mémoire ou absente (essai)")
    d = dr if dr is not None else recovery(gcfg, ts_now)
    out["sauvegarde"] = (_h("UNKNOWN", d["restore"]) if not d["count"] else
                         _h("HEALTHY" if d["ok"] else "DEGRADED",
                            f"{d['count']} sauvegarde(s), la dernière il y a {fr(d['rpo_h'] or 0, '.0f')} h"
                            + (f" ; restauration d'essai en {fr(d['rto_s'], '.2f')} s" if d["rto_s"] is not None
                               else ""), True, True, d["ok"]))
    rday = state.get("report_day")
    out["rapport"] = (_h("HEALTHY", f"rapport du {rday}", True, True, True)
                      if rday and (now.date() - datetime.fromisoformat(str(rday)).date()).days <= 1 else
                      _h("DEGRADED", f"dernier rapport : {rday or 'aucun'}", True, False, False))
    al = state.get("alerts_last") or {}
    failing = sorted(ch for ch, v in al.items() if isinstance(v, dict) and v.get("error"))
    out["alertes"] = (_h("DEGRADED", "en panne : " + ", ".join(failing) + " (cause dans le panneau)", True, False,
                         False) if failing else
                      _h("HEALTHY", "canaux en ordre : " + ", ".join(sorted(al)), True, True, True) if al else
                      _h("UNKNOWN", "aucun envoi noté"))
    wday = state.get("last_watch_day")
    out["veille"] = (_h("HEALTHY", f"annonces lues le {wday}", True, True, True) if wday and wday >= day else
                     _h("DEGRADED" if wday else "UNKNOWN", f"dernière veille : {wday or 'aucune'}"))
    sv = (state.get("savoir") or {}).get("day")
    out["savoir"] = (_h("HEALTHY", f"lu le {sv}", True, True, True) if sv and sv >= day else
                     _h("DEGRADED" if sv else "UNKNOWN", f"dernière lecture : {sv or 'aucune'}"))
    port = int(deps.extra.get("panel_port", v29._env_i("PANEL_PORT", 8765)))
    up = deps.extra["panel_up"] if "panel_up" in deps.extra else _panel_up(port)
    out["panneau"] = _h("HEALTHY" if up else "DOWN", "répond" if up else "ne répond pas", up, up, up)
    return out


# ══════════════════════════════════════════════════════════════════════
# Dépendances, incidents regroupés par cause (§6, §11-13)
# ══════════════════════════════════════════════════════════════════════

def graph(services: Sequence[Service] = SERVICES) -> Dict[str, Any]:
    """Graphe des dépendances (§6) : dépendances indirectes, cycles,
    dépendances extérieures, points uniques de défaillance (ce dont dépend,
    directement ou non, un service des paliers 0 ou 1)."""
    by = {s.sid: s for s in services}
    unknown = sorted({d for s in services for d in s.deps if d not in by})
    closure: Dict[str, List[str]] = {}

    def walk(sid: str, path: Tuple[str, ...]) -> List[str]:
        out: List[str] = []
        for d in by[sid].deps if sid in by else ():
            if d in path:
                cycles.append(path + (d,))
                continue
            out += [d] + walk(d, path + (d,))
        return list(dict.fromkeys(out))

    cycles: List[Tuple[str, ...]] = []
    for s in services:
        closure[s.sid] = walk(s.sid, (s.sid,))
    critical = [s.sid for s in services if s.tier <= 1]
    spof = sorted({d for c in critical for d in closure[c]}, key=lambda x: (-sum(x in closure[c] for c in critical), x))
    return {"closure": closure, "cycles": cycles, "unknown": unknown, "spof": spof,
            "external": sorted(s.sid for s in services if s.external)}


def incidents(h: Dict[str, Dict[str, Any]], state: Dict[str, Any], now: datetime,
              services: Sequence[Service] = SERVICES) -> List[Dict[str, Any]]:
    """Incidents du moment (§12-13), un par cause : un service touché parce
    qu'une de ses dépendances est en panne n'ouvre pas d'incident à lui (il
    est rattaché à la cause). Gravité selon le palier du service ;
    l'arrêt d'urgence, un journal abîmé ou un ordre inconnu sont des P0 ou
    P1 quel que soit le palier."""
    by = {s.sid: s for s in services}
    clo = graph(services)["closure"]
    down = {sid for sid, x in h.items() if x["status"] == "DOWN"}
    out: List[Dict[str, Any]] = []
    for sid, x in h.items():
        if x["status"] not in ("DOWN", "DEGRADED") or sid not in by:
            continue
        causes = [d for d in clo.get(sid, []) if d in down]
        if causes:
            continue
        tier = by[sid].tier
        sev = TIER_SEVERITY[tier] if x["status"] == "DOWN" else ("P2" if tier <= 2 else "P3")
        out.append({"service": sid, "severity": sev, "what": f"{by[sid].name} : {STATUS_FR[x['status']]}",
                    "detail": x["detail"], "runbook": SERVICE_RUNBOOK.get(sid, ""),
                    "affected": sorted(s for s, deps in clo.items() if sid in deps and h.get(s, {}).get("status")
                                       in ("DOWN", "DEGRADED"))})
    if state.get("halted"):
        reason = str(state.get("halt_reason") or "")
        sev = "P0" if "UNKNOWN" in reason.upper() or "INCONNU" in reason.upper() else "P1"
        out.append({"service": "urgence", "severity": sev, "what": "Arrêt d'urgence déclenché",
                    "detail": reason or "raison non notée", "runbook": "RB-ORDRE-INCONNU" if sev == "P0"
                    else "RB-ARRET-URGENCE", "affected": ["bot"]})
    day = now.date().isoformat()
    for inc in out:
        inc["id"] = "INC-" + hashlib.sha256(f"{inc['service']}|{inc['what']}|{day}".encode()).hexdigest()[:10]
        inc["severity_fr"] = SEVERITY_FR[inc["severity"]]
    return sorted(out, key=lambda i: (i["severity"], i["service"]))


def incidents_path(gcfg: Any) -> str:
    """Journal des incidents, à côté du verrou du bot ("" sans verrou)."""
    return autonomy.sidecar(getattr(gcfg, "lock_file", ""), ".incidents.json")


def record(gcfg: Any, current: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    """Cycle de vie des incidents (§13) : un incident vu pour la première
    fois est ouvert (détecté le…), un incident qui n'est plus vu est clos
    (résolu le…) ; les 200 derniers sont gardés."""
    path = incidents_path(gcfg)
    book = autonomy.read_json(path) if path and os.path.exists(path) else {}
    items: Dict[str, Dict[str, Any]] = dict(book.get("items") or {})
    stamp = now.isoformat(timespec="seconds")
    seen = {i["id"] for i in current}
    for i in current:
        if i["id"] not in items or items[i["id"]].get("closed_at"):
            items[i["id"]] = {k: i[k] for k in ("service", "severity", "what", "detail", "runbook")}
            items[i["id"]]["detected_at"] = stamp
    for iid, it in items.items():
        if iid not in seen and not it.get("closed_at"):
            it["closed_at"] = stamp
    keep = dict(sorted(items.items(), key=lambda kv: kv[1].get("detected_at", ""))[-200:])
    out = {"updated": stamp, "items": keep}
    if path:
        autonomy.write_json(path, out)
    return out


def slos(gcfg: Any, state: Dict[str, Any], h: Dict[str, Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    """Objectifs de service (§8-9), mesurés, avec le budget d'erreur et sa
    consommation ; un budget épuisé recommande de geler les changements
    (rien n'est appliqué seul)."""
    up = uptime.summary(state.get("uptime"), state.get("last_cycle_ts"), state.get("stopped_at"), now.timestamp())
    week = up.get("week_pct")
    rows = []
    for sid, what, target in SLO:
        if sid == "SLO-DISPONIBILITE":
            value = week
        elif sid == "SLO-DECISION":
            value = 100.0 if h["bot"]["semantic"] else 0.0 if h["bot"]["semantic"] is False else None
        elif sid == "SLO-DONNEES":
            value = None if h["donnees"]["semantic"] is None else 100.0 * bool(h["donnees"]["semantic"])
        elif sid == "SLO-SAUVEGARDE":
            value = None if h["sauvegarde"]["semantic"] is None else 100.0 * bool(h["sauvegarde"]["semantic"])
        else:
            value = None if h["rapport"]["semantic"] is None else 100.0 * bool(h["rapport"]["semantic"])
        budget = 100.0 - target
        used = None if value is None else max(0.0, target - value) if budget == 0 else max(0.0, 100.0 - value)
        burn = None if used is None else (used / budget if budget > 0 else (0.0 if used == 0 else float("inf")))
        rows.append({"id": sid, "what": what, "target": target, "value": value, "budget_pct": budget,
                     "burn": burn, "ok": None if value is None else value >= target,
                     "decision": "" if burn is None or burn <= 1 else "budget épuisé : geler les changements "
                     "(recommandé, rien n'est appliqué seul)"})
    return rows


def supervisor_bounds(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Limites du superviseur (§16, §44), vérifiées par le moteur
    d'autorisation : ce qu'il peut (relancer le bot, poser le mode sûr) et
    ce qui lui est refusé (risque, arrêt d'urgence, autorisation, réel,
    secrets, code)."""
    allowed = [a for a in ("READ", "ANALYZE", "RECOMMEND", "RESTART_BOT", "SAFE_MODE_ON")
               if autorisation.decide("superviseur", a, now=now).allowed]
    refused = [a for a in SUPERVISOR_FORBIDDEN if not autorisation.decide("superviseur", a, now=now).allowed]
    return {"allowed": allowed, "refused": refused, "ok": len(refused) == len(SUPERVISOR_FORBIDDEN),
            "levels": AUTONOMY, "no_l5": not any(v.startswith("L5") for v in AUTONOMY.values())}


def config_fingerprint(gcfg: Any) -> str:
    """Empreinte de la configuration (§20) : chaque réglage, sans aucun
    secret (la configuration du bot n'en contient pas : les clés restent dans
    l'environnement)."""
    data = dataclasses.asdict(gcfg) if dataclasses.is_dataclass(gcfg) else {}
    return hashlib.sha256(repr(sorted(data.items(), key=lambda kv: kv[0])).encode("utf-8")).hexdigest()[:16]


def changes(gcfg: Any, deps: Optional[systeme.Deps] = None, light: bool = False) -> Dict[str, Any]:
    """Changements (§18-20) : version du code (git, sauf en lecture légère),
    état du dépôt, empreinte de la configuration, versions des règles
    critiques."""
    deps = deps or systeme.Deps()
    head = None if light else systeme.git(deps, str(ROOT), "rev-parse", "--short", "HEAD")
    dirty = None if light else systeme.git(deps, str(ROOT), "status", "--porcelain", "--untracked-files=no")
    return {"commit": (head or "").strip() or None, "dirty": None if dirty is None else bool(dirty.strip()),
            "config": config_fingerprint(gcfg),
            "versions": {"porte": porte.POLICY_VERSION, "politiques": politique.REGISTRY_VERSION,
                         "autorisation": autorisation.POLICY_VERSION, "risque": moteur_risque.MODEL_VERSION,
                         "limites": moteur_risque.LIMITS_VERSION}}


# ══════════════════════════════════════════════════════════════════════
# Examen : AC-001 à AC-060 (§45-47, §52)
# ══════════════════════════════════════════════════════════════════════

FAMILY_WEIGHTS = {"safety": 0.20, "reliability": 0.15, "observability": 0.10, "incidents": 0.10, "dr": 0.10,
                  "security": 0.10, "change": 0.10, "capacity": 0.05, "reconciliation": 0.05, "human": 0.05}
FAMILY_FR = {"safety": "sûreté", "reliability": "fiabilité", "observability": "observabilité",
             "incidents": "réponse aux incidents", "dr": "reprise après sinistre", "security": "sécurité",
             "change": "gestion des changements", "capacity": "capacité", "reconciliation": "rapprochement",
             "human": "opérations humaines"}
_c = porte_examen._c
CT = "tests/test_controle.py::"
LE = "tests/test_live_execution.py::"
CRITERIA: Tuple[acceptation.Criterion, ...] = (
    _c(1, "Supervision en continu (bot en marche 99 % du temps)", "P1", "reliability"),
    _c(2, "Santé de chaque service (vivant, prêt, métier)", "P0", "observability",
       (CT + "test_health_of_each_service_is_measured_never_assumed",)),
    _c(3, "Graphe des dépendances", "P1", "observability", (CT + "test_the_dependency_graph_and_single_points",)),
    _c(4, "Objectifs de service mesurés", "P1", "reliability", (CT + "test_service_objectives_and_error_budgets",)),
    _c(5, "Budgets d'erreur", "P1", "reliability", (CT + "test_service_objectives_and_error_budgets",)),
    _c(6, "Alertes dédupliquées, incidents regroupés par cause", "P1", "incidents",
       ("tests/test_alerts.py::test_hub_levels_dedup_and_failure_isolation",
        CT + "test_one_failure_one_incident_with_its_procedure")),
    _c(7, "P0 signalé en priorité", "P0", "incidents", (CT + "test_one_failure_one_incident_with_its_procedure",)),
    _c(8, "Cycle de vie des incidents (ouvert, clos)", "P1", "incidents", (CT + "test_incidents_open_and_close",)),
    _c(9, "Procédures versionnées", "P1", "incidents", (CT + "test_runbooks_are_versioned_and_cover_every_service",)),
    _c(10, "Procédures sans effet de bord en double", "P2", "incidents",
       (CT + "test_runbooks_are_versioned_and_cover_every_service",)),
    _c(11, "Audit complet", "P0", "security", ("tests/test_contrats.py::test_audit_chain_detects_any_change",)),
    _c(12, "Aucun secret dans les journaux", "P0", "security", ("tests/test_core.py::test_scrub_secrets",)),
    _c(13, "Configuration versionnée", "P1", "change", (CT + "test_changes_and_configuration_are_fingerprinted",)),
    _c(14, "Retour à la version précédente", "P1", "change",
       ("tests/test_maintenance.py::test_update_rolls_back_a_faulty_version",)),
    _c(15, "Options activables sûres", "P2", "change", (CT + "test_changes_and_configuration_are_fingerprinted",)),
    _c(16, "Capacité surveillée (disque, mémoire)", "P1", "capacity",
       (CT + "test_health_of_each_service_is_measured_never_assumed",)),
    _c(17, "Contre-pression", "P2", "capacity",
       ("tests/test_real_conditions.py::test_network_outage_defers_quickly_without_waiting_every_pair",)),
    _c(18, "Disjoncteurs", "P1", "reliability", ("tests/test_core.py::test_daily_dd_halts_then_resets_next_day",)),
    _c(19, "Sauvegarde automatique", "P0", "dr", ("tests/test_report.py::test_database_backup_is_verified_and_rotated",)),
    _c(20, "Restauration éprouvée", "P0", "dr", ("tests/test_donnees.py::test_backup_is_restored_for_real_and_reported",)),
    _c(21, "Bascule de secours", "P2", "dr", na="un seul ordinateur et un seul courtier : pas de site de secours ; "
                                              "en réel, les stops restent posés chez Binance"),
    _c(22, "Reprise après sinistre", "P1", "dr", (LE + "test_reconcile_recovers_lost_context",)),
    _c(23, "RPO mesuré", "P1", "dr", (CT + "test_recovery_point_and_time_are_measured",)),
    _c(24, "RTO mesuré", "P1", "dr", (CT + "test_recovery_point_and_time_are_measured",)),
    _c(25, "Superviseur en marche", "P1", "reliability",
       ("tests/test_autonomy.py::test_supervisor_restarts_after_crash_then_stops_with_the_bot",)),
    _c(26, "Le superviseur ne s'autorise rien", "P0", "safety", (CT + "test_the_supervisor_is_bounded",)),
    _c(27, "Le superviseur ne contourne pas la porte", "P0", "safety", (CT + "test_the_supervisor_is_bounded",)),
    _c(28, "Le superviseur ne change pas le risque", "P0", "safety", (CT + "test_the_supervisor_is_bounded",)),
    _c(29, "Le superviseur ne lève pas l'arrêt d'urgence", "P0", "safety", (CT + "test_the_supervisor_is_bounded",)),
    _c(30, "Niveaux d'autonomie tenus", "P0", "safety", (CT + "test_the_supervisor_is_bounded",)),
    _c(31, "Gestion des changements", "P0", "change",
       ("tests/test_autorisation.py::test_separation_of_duties_and_delegations",
        "tests/test_maintenance.py::test_update_rolls_back_a_faulty_version")),
    _c(32, "Changements critiques approuvés par vous", "P0", "change",
       ("tests/test_autorisation.py::test_separation_of_duties_and_delegations",)),
    _c(33, "Surveillance de la sécurité", "P1", "security",
       ("tests/test_panel.py::test_password_required_from_the_network",)),
    _c(34, "Changement des clés", "P1", "security",
       ("tests/test_real_conditions.py::test_set_keys_refuses_an_exposed_key",)),
    _c(35, "Audit inaltérable", "P0", "security", ("tests/test_contrats.py::test_audit_chain_detects_any_change",)),
    _c(36, "Retour d'expérience après incident", "P2", "incidents",
       ("tests/test_analyse.py::test_registry_notes_and_replays_an_experiment",)),
    _c(37, "Essais de chaos", "P1", "reliability",
       ("tests/test_porte_examen.py::test_chaos_ten_thousand_requests_and_a_kill_switch",
        "tests/test_fake_binance.py::test_fault_injection")),
    _c(38, "Essais de bascule", "P2", "dr", na="pas de bascule possible (un seul ordinateur, un seul courtier)"),
    _c(39, "Horloge synchronisée", "P0", "reliability",
       ("tests/test_real_conditions.py::test_bot_syncs_clock_at_boot_then_hourly",)),
    _c(40, "Rapprochement surveillé", "P0", "reconciliation", (LE + "test_orphan_detected_then_cleared",)),
    _c(41, "Santé de Binance surveillée", "P1", "reconciliation",
       (CT + "test_health_of_each_service_is_measured_never_assumed",)),
    _c(42, "Fraîcheur des données surveillée", "P0", "observability",
       ("tests/test_analyse.py::test_data_quality_score_names_each_defect",)),
    _c(43, "Santé de la stratégie surveillée", "P1", "observability",
       ("tests/test_moteur_strategie.py::test_an_unknown_value_never_makes_a_condition_true",)),
    _c(44, "Santé des modèles surveillée", "P2", "observability", ("tests/test_modeles.py::test_breaker_opens_after_three_failures_and_closes_on_success",)),
    _c(45, "Utilisation du risque surveillée", "P0", "safety",
       ("tests/test_moteur_risque.py::test_the_gate_wants_a_fresh_valid_assessment",)),
    _c(46, "Baisse du capital surveillée", "P0", "safety", ("tests/test_analyse.py::test_stress_scenarios_and_the_kill_switch",)),
    _c(47, "Exécutions anormales détectées", "P1", "observability",
       ("tests/test_deploiement.py::test_execution_quality_is_measured",)),
    _c(48, "Déploiements observés", "P2", "change", (CT + "test_changes_and_configuration_are_fingerprinted",)),
    _c(49, "Tableau de bord", "P2", "human", ("tests/test_panel.py::test_password_required_from_the_network",)),
    _c(50, "Arrêt d'urgence opérationnel", "P0", "safety", ("tests/test_trendguard.py::test_kill_switch_blocks_entries",)),
    _c(51, "Mode sûr opérationnel", "P0", "safety", ("tests/test_contrats.py::test_safe_mode_stops_buys_but_not_sales",)),
    _c(52, "Escalade vers vous", "P0", "human", ("tests/test_alerts.py::test_hub_levels_dedup_and_failure_isolation",)),
    _c(53, "Accès d'urgence contrôlé", "P2", "human",
       na="pas d'accès d'urgence : vous êtes le seul propriétaire, chaque action passe par vos outils masqués"),
    _c(54, "Aucune élévation de privilège autonome", "P0", "safety",
       ("tests/test_autorisation.py::test_deny_by_default", CT + "test_the_supervisor_is_bounded")),
    _c(55, "Aucun chemin de contrôle caché", "P0", "safety",
       ("tests/test_porte_examen.py::test_one_single_route_to_binance_and_it_passes_the_gate",)),
    _c(56, "Chaque action traçable", "P0", "security",
       ("tests/test_donnees.py::test_every_paper_trade_traces_back_to_its_data",)),
    _c(57, "Reprise reproductible", "P1", "dr", ("tests/test_trendguard.py::test_replay_matches_backtest",)),
    _c(58, "Portes de mise en production tenues", "P0", "change", ("tests/test_deploiement.py::test_the_bot_never_promotes_itself",)),
    _c(59, "Un P0 bloque la production", "P0", "change",
       ("tests/test_chantiers.py::test_a_closed_live_gate_refuses_every_real_buy",)),
    _c(60, "Audit d'exploitation complet", "P1", "human", (CT + "test_the_examination_and_its_contract",)),
)


def _measures(gcfg: Any, state: Dict[str, Any], h: Dict[str, Dict[str, Any]], slo: List[Dict[str, Any]],
              g: Dict[str, Any], sup: Dict[str, Any], dr: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    """(état, détail) des critères mesurés sur le bot et l'ordinateur."""
    out: Dict[str, Tuple[str, str]] = {}
    dispo = next(r for r in slo if r["id"] == "SLO-DISPONIBILITE")
    out["AC-001"] = (("UNKNOWN", "disponibilité pas encore mesurée") if dispo["value"] is None else
                     (("PASS" if dispo["ok"] else "FAIL"), f"bot en marche {fr(dispo['value'], '.1f')} % des 7 derniers "
                      f"jours (objectif {fr(dispo['target'], '.0f')} %)"))
    unknown = [sid for sid, x in h.items() if x["status"] == "UNKNOWN"]
    out["AC-002"] = ("PASS", f"{len(h)} services mesurés" + (f", {len(unknown)} non mesurable(s) ici : "
                                                             + ", ".join(unknown) if unknown else ""))
    out["AC-003"] = (("PASS" if not g["cycles"] and not g["unknown"] else "FAIL"),
                     f"{len(SERVICES)} services, aucun cycle ; points uniques de défaillance : " + ", ".join(g["spof"][:5]))
    out["AC-004"] = ("PASS", f"{len(slo)} objectifs de service mesurés")
    out["AC-016"] = (("PASS" if h["pc"]["status"] == "HEALTHY" else "FAIL" if h["pc"]["status"] == "DOWN"
                      else "PASS" if h["pc"]["status"] == "DEGRADED" else "UNKNOWN"), h["pc"]["detail"])
    out["AC-023"] = (("UNKNOWN", "aucune sauvegarde") if dr.get("rpo_h") is None else
                     (("PASS" if dr["rpo_h"] <= BACKUP_RPO_H else "FAIL"),
                      f"dernière sauvegarde il y a {fr(dr['rpo_h'], '.1f')} h (objectif {fr(BACKUP_RPO_H, '.0f')} h)"))
    out["AC-024"] = (("UNKNOWN", "aucune sauvegarde") if dr.get("rto_s") is None else
                     (("PASS" if dr["rto_s"] <= RTO_TARGET_SEC and not str(dr["restore"]).startswith("ÉCHEC")
                       else "FAIL"),
                      f"restauration d'essai en {fr(dr['rto_s'], '.2f')} s (objectif {fr(RTO_TARGET_SEC, '.0f')} s)"))
    out["AC-026"] = out["AC-027"] = out["AC-028"] = out["AC-029"] = (
        ("PASS" if sup["ok"] else "FAIL"), f"refusé au superviseur : {len(sup['refused'])} action(s) critique(s) sur "
        f"{len(SUPERVISOR_FORBIDDEN)}")
    out["AC-030"] = (("PASS" if sup["no_l5"] else "FAIL"), "aucun automatisme sans limite (L5) ; le plus haut : L4, "
                     "le bot, sous la porte d'exécution")
    return out


def band(score: float) -> str:
    """Bande de la note (§46), la même que pour la porte d'exécution."""
    return porte_examen.band(score)


def evaluate(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None,
             deps: Optional[systeme.Deps] = None, dr: Optional[Dict[str, Any]] = None,
             root: pathlib.Path = ROOT, light: bool = False) -> Dict[str, Any]:
    """Le plan de contrôle du moment : santé, dépendances, objectifs de
    service, incidents, changements, superviseur, reprise ; les 60
    critères, la note et le verdict (READY_FOR_PRODUCTION_CONTROLLED_OPERATIONS
    ou NOT_READY ; jamais une exploitation autonome sans limite). Lecture
    légère (rapport, panneau) : sans restauration d'essai ni git."""
    now = now or datetime.now(timezone.utc)
    d = dr if dr is not None else recovery(gcfg, now.timestamp(), None if light else report_security.restore_test)
    h = health(gcfg, state, now, deps, d)
    g = graph()
    slo = slos(gcfg, state, h, now)
    sup = supervisor_bounds(now)
    inc = incidents(h, state, now)
    rows, fam, score, p0 = porte_examen.grade(CRITERIA, _measures(gcfg, state, h, slo, g, sup, d), FAMILY_WEIGHTS,
                                              root)
    ready = not p0 and score >= 95
    return {"version": VERSION, "now": now.isoformat(timespec="seconds"), "mode": getattr(gcfg, "run_mode", "paper"),
            "health": h, "graph": g, "slo": slo, "incidents": inc, "supervisor": sup, "recovery": d,
            "changes": changes(gcfg, deps, light), "rows": rows, "family_scores": fam, "score": round(score, 1),
            "band": band(score), "p0_failures": p0,
            "status": "READY_FOR_PRODUCTION_CONTROLLED_OPERATIONS" if ready else "NOT_READY"}


def report_of(r: Dict[str, Any]) -> ControlPlaneReport:
    """L'état au format du contrat ControlPlaneReport.v1."""
    counts = {s: sum(1 for x in r["rows"] if x["status"] == s) for s in acceptation.STATUS_FR}
    hs = [x["status"] for x in r["health"].values()]
    return ControlPlaneReport(
        report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=r["status"],
        readiness_score=r["score"], band=r["band"], p0_failures=tuple(r["p0_failures"]), passed=counts["PASS"],
        failed=counts["FAIL"], unknown=counts["UNKNOWN"], not_applicable=counts["NOT_APPLICABLE"],
        services=len(hs), services_down=hs.count("DOWN"), incidents=tuple((i["id"], i["severity"]) for i in r["incidents"]),
        engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : services, incidents par gravité."""
    hs = [x["status"] for x in r["health"].values()]
    inc = r["incidents"]
    out = (f"{hs.count('HEALTHY')} service(s) sur {len(hs)} en bonne santé, {hs.count('DEGRADED')} dégradé(s), "
           f"{hs.count('DOWN')} en panne")
    if inc:
        worst = inc[0]
        out += f" ; {len(inc)} incident(s), le plus grave {worst['severity']} : {worst['what']}"
    else:
        out += " ; aucun incident"
    return out


def render(r: Dict[str, Any]) -> str:
    """docs/CONTROLE.md : santé, incidents, objectifs, reprise, superviseur,
    verdict, critères."""
    lines = ["# Plan de contrôle de la production (critères AC-001 à AC-060)", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py controle` (étape 18 du prompt maître, "
             "[`PLAN_DE_CONTROLE.md`](PLAN_DE_CONTROLE.md)). Lecture seule : il voit, il n'agit pas.", "",
             "## Verdict", "",
             f"- **{'Prêt pour une exploitation contrôlée' if r['status'] != 'NOT_READY' else 'PAS PRÊT'}** "
             f"({r['status']}) ; jamais une exploitation autonome sans limite.",
             f"- Note {fr(r['score'], '.1f')}/100 ({porte_examen.BAND_FR[r['band']]}) ; critères P0 non satisfaits : "
             f"{len(r['p0_failures'])}" + (f" ({', '.join(r['p0_failures'])})" if r["p0_failures"] else "") + ".",
             f"- {describe(r)}.", "", "## Services", "", "| Service | palier | état | détail |", "| --- | --- | --- | --- |"]
    by = {s.sid: s for s in SERVICES}
    for sid, x in r["health"].items():
        lines.append(f"| {by[sid].name} | {by[sid].tier} ({TIER_FR[by[sid].tier]}) | {STATUS_FR[x['status']]} | "
                     f"{x['detail']} |")
    lines += ["", "## Incidents", ""]
    if r["incidents"]:
        lines += ["| Incident | gravité | détail | procédure |", "| --- | --- | --- | --- |"]
        for i in r["incidents"]:
            rb = RUNBOOKS.get(i["runbook"], {}).get("title", "—")
            lines.append(f"| {i['what']} | {i['severity']} ({i['severity_fr']}) | {i['detail']} | {rb} |")
    else:
        lines.append("Aucun incident.")
    lines += ["", "## Objectifs de service", "", "| Objectif | cible | mesuré | budget consommé |", "| --- | --- | --- | --- |"]
    for s in r["slo"]:
        val = "—" if s["value"] is None else f"{fr(s['value'], '.1f')} %"
        burn = "—" if s["burn"] is None else ("épuisé" if s["burn"] > 1 else f"{fr(s['burn'] * 100, '.0f')} %")
        lines.append(f"| {s['what']} | {fr(s['target'], '.1f')} % | {val} | {burn} |")
    d, sup, ch = r["recovery"], r["supervisor"], r["changes"]
    lines += ["", "## Reprise, changements, superviseur", "",
              f"- Sauvegardes : {d['count']}" + (f", la dernière il y a {fr(d['rpo_h'], '.1f')} h (RPO)" if d["count"]
                                                 else "") + (f", restaurée à l'essai en {fr(d['rto_s'], '.2f')} s (RTO)"
                                                             if d.get("rto_s") is not None else "") + ".",
              f"- Code : {ch['commit'] or 'version inconnue'}" + (" (modifications locales)" if ch["dirty"] else "")
              + f" ; configuration {ch['config']} ; " + ", ".join(f"{k} {v}" for k, v in ch["versions"].items()) + ".",
              f"- Superviseur : peut {', '.join(autorisation.ACTION_FR[a] for a in sup['allowed'])} ; refusé : "
              f"{len(sup['refused'])} action(s) critique(s) sur {len(SUPERVISOR_FORBIDDEN)}.",
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
    """Commande `controle` : l'état du jour, les incidents notés dans leur
    journal (ouverts, clos) ; écrit avec --out."""
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Plan de contrôle de la production (AC-001 à AC-060)")
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    r = evaluate(g, systeme.read_state(g.db_file) or {})
    try:
        report_of(r)
    except ContractError as e:
        print(f"État non conforme à son contrat : {e}")
        return 1
    record(g, r["incidents"], datetime.fromisoformat(r["now"]))
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["status"] == "READY_FOR_PRODUCTION_CONTROLLED_OPERATIONS" else 1
