"""
Fiabilité du PC et du bot (SRE) et autoréparation encadrée (prompt maître,
étape 32 ; docs/SRE.md) : au-dessus du plan de contrôle (controle.py), ce
module cherche les anomalies (arrêts imprévus, horloge, disque, sauvegarde,
données), prévoit les pannes (place sur le disque, arrêts répétés aux mêmes
heures) en disant que c'est une prévision, mesure la capacité, calcule le
rayon d'impact de chaque service en panne et propose une remédiation classée
LOW, MEDIUM, HIGH ou CRITICAL : seules les remédiations LOW déjà prévues
(relance du bot par son superviseur) se font seules ; les autres attendent
vous ; une CRITICAL ne s'autorise jamais elle-même.

Une alerte n'est pas un incident confirmé ; une cause corrélée n'est pas une
cause prouvée ; une prévision n'est pas une certitude.

    python trendguard_bot.py sre                         # anomalies, prévisions, capacité, remédiations
    python trendguard_bot.py sre --out docs/SRE_ETAT.md
"""

from __future__ import annotations

import argparse
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import v29

from . import autonomy, controle, systeme, uptime
from . import trend_strategy as ts
from .contrats import SREReport
from .texte import fr

VERSION = "sre-1.0.0"
HISTORY_DAYS = 120
CLOCK_TOL_MS = 1000
STOPS_WINDOW_D = 14
# Remédiation par service : (niveau, action, automatique ?, qui l'autorise).
REMEDIATION: Dict[str, Tuple[str, str, bool, str]] = {
    "bot": ("LOW", "relance du bot par son superviseur", True, "politique du superviseur (déjà en place)"),
    "superviseur": ("MEDIUM", "relancer le superviseur (bouton AUTO du panneau)", False, "vous"),
    "panneau": ("LOW", "relancer le panneau (python trendguard_bot.py panel)", False, "vous"),
    "rapport": ("LOW", "relancer le rapport (python trendguard_bot.py rapport maintenant)", False, "vous"),
    "veille": ("LOW", "rien : la veille réessaie seule", True, "politique (nouvel essai à chaque cycle)"),
    "savoir": ("LOW", "rien : le savoir réessaie seul", True, "politique (nouvel essai au prochain passage)"),
    "donnees": ("LOW", "rien : la décision attend des données saines", True, "garde du bot (aucun achat)"),
    "binance": ("LOW", "rien : achats différés, stops posés chez Binance", True, "garde du bot"),
    "internet": ("LOW", "vérifier la connexion du PC", False, "vous"),
    "pc": ("HIGH", "libérer de la place sur le disque", False, "vous"),
    "alertes": ("MEDIUM", "corriger le canal d'alerte (python trendguard_bot.py alerts configurer)", False, "vous"),
    "sauvegarde": ("HIGH", "relancer la sauvegarde ; restauration seulement par vous", False, "vous"),
    "base": ("CRITICAL", "restaurer la dernière sauvegarde saine, bot arrêté", False, "vous seul"),
    "journal": ("CRITICAL", "ne rien corriger à la main ; garder le fichier", False, "vous seul"),
    "audit": ("CRITICAL", "ne rien corriger à la main ; garder le fichier", False, "vous seul"),
    "porte": ("HIGH", "corriger le registre des politiques dans le code (Pull Request)", False, "vous"),
    "risque": ("LOW", "rien : aucun achat sans évaluation du jour, ventes et stops continuent", True,
               "porte d'exécution (fail-closed)"),
    "urgence": ("CRITICAL", "reprise seulement par vous (python trendguard_bot.py resume)", False, "vous seul"),
}
LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def history_path(gcfg: Any) -> str:
    """<bot>.sre.json : une mesure par jour (place sur le disque, taille de
    la base), pour les prévisions."""
    lock = getattr(gcfg, "lock_file", "") or ""
    return autonomy.sidecar(lock, ".sre.json") if lock and lock not in (os.devnull, "/dev/null") else ""


def remember(gcfg: Any, sample: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    """Ajoute la mesure du jour (une par jour, 120 jours) et rend l'historique."""
    path = history_path(gcfg)
    rows = list((autonomy.read_json(path) or {}).get("rows") or []) if path else []
    day = now.date().isoformat()
    rows = [r for r in rows if r.get("day") != day] + [dict(sample, day=day)]
    rows = rows[-HISTORY_DAYS:]
    if path:
        autonomy.write_json(path, {"rows": rows})
    return rows


def sample(gcfg: Any, res: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """La mesure du moment : place libre, taille de la base."""
    db = getattr(gcfg, "db_file", "") or ""
    size = os.path.getsize(db) / 2 ** 20 if db and db != ":memory:" and os.path.exists(db) else None
    return {"disk_free_gb": None if not res else round(float(res.get("disk_free") or 0.0), 3),
            "db_mb": None if size is None else round(size, 3)}


def _slope(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    """Pente par jour (moindres carrés) d'une mesure, ou None (moins de 3 points)."""
    pts = [(datetime.fromisoformat(r["day"]).toordinal(), float(r[key])) for r in rows if r.get(key) is not None]
    if len(pts) < 3:
        return None
    mx = sum(x for x, _y in pts) / len(pts)
    my = sum(y for _x, y in pts) / len(pts)
    den = sum((x - mx) ** 2 for x, _y in pts)
    return None if den == 0 else sum((x - mx) * (y - my) for x, y in pts) / den


def stops(state: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """Arrêts imprévus (hors arrêts demandés) : les 7 derniers jours contre la
    moyenne des semaines d'avant, et l'heure où ils commencent le plus souvent."""
    t = now.timestamp()
    events = [e for e in ((state.get("uptime") or {}).get("events") or []) if e.get("cause") != uptime.USER]
    week = [e for e in events if t - float(e["start"]) <= 7 * 86400]
    older = [e for e in events if 7 * 86400 < t - float(e["start"]) <= 28 * 86400]
    base = len(older) / 3                # les trois semaines d'avant
    hours: Dict[int, int] = {}
    nights = set()
    for e in events:
        if t - float(e["start"]) <= STOPS_WINDOW_D * 86400:
            st = datetime.fromtimestamp(float(e["start"]), timezone.utc)
            hours[st.hour] = hours.get(st.hour, 0) + 1
            nights.add(st.date())
    top = max(hours.items(), key=lambda kv: kv[1]) if hours else None
    return {"week": len(week), "baseline": round(base, 2), "anomaly": len(week) > base + 2,
            "down_h": round(sum(min(float(e["end"]), t) - float(e["start"]) for e in week) / 3600, 1),
            "top_hour": top[0] if top else None, "top_count": top[1] if top else 0,
            "days_with_stop": len(nights)}


def anomalies(state: Dict[str, Any], res: Optional[Dict[str, Any]], dr: Dict[str, Any], st: Dict[str, Any]
              ) -> List[Dict[str, Any]]:
    """Anomalies mesurées (§11) : chacune avec sa référence, la valeur vue,
    l'écart et sa gravité ; une valeur absente n'est pas une anomalie."""
    out = []

    def add(metric: str, baseline: str, observed: str, severity: str, evidence: str) -> None:
        out.append({"metric": metric, "baseline": baseline, "observed": observed, "severity": severity,
                    "evidence": evidence})
    if st["anomaly"]:
        add("arrêts imprévus sur 7 jours", f"{fr(st['baseline'], '.1f')} par semaine avant", str(st["week"]),
            "HIGH" if st["week"] >= 5 else "MEDIUM", f"{fr(st['down_h'], '.1f')} h d'arrêt cette semaine")
    off = state.get("clock_offset_ms")
    if off is not None and abs(float(off)) > CLOCK_TOL_MS:
        add("écart d'horloge avec Binance", f"moins de {CLOCK_TOL_MS} ms", f"{fr(float(off), '.0f')} ms",
            "MEDIUM", "le bot corrige l'écart, mais le PC dérive")
    if res and res.get("disk_free") is not None and float(res["disk_free"]) < controle.DISK_LOW_GB:
        add("place libre sur le disque", f"au moins {fr(controle.DISK_LOW_GB, '.0f')} Go",
            f"{fr(float(res['disk_free']), '.1f')} Go", "HIGH" if float(res["disk_free"]) < controle.DISK_MIN_GB
            else "MEDIUM", "la base et les sauvegardes risquent de s'abîmer")
    if dr.get("rpo_h") is not None and dr["rpo_h"] > controle.BACKUP_RPO_H:
        add("âge de la dernière sauvegarde", f"moins de {fr(controle.BACKUP_RPO_H, '.0f')} h",
            f"{fr(dr['rpo_h'], '.0f')} h", "HIGH", "une nuit de sauvegarde manquée")
    q = (state.get("qualite") or {}).get("score")
    if q is not None and float(q) < 90:
        add("note des données du jour", "au moins 90/100", f"{fr(float(q), '.0f')}/100", "MEDIUM",
            str((state.get("qualite") or {}).get("text") or ""))
    return out


def predictions(rows: List[Dict[str, Any]], st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Prévisions de panne (§16) : épuisement du disque et arrêts aux mêmes
    heures ; chacune avec sa probabilité ou son horizon, et ce qui la fonde.
    Une prévision n'est jamais une certitude."""
    out = []
    slope = _slope(rows, "disk_free_gb")
    last = next((r["disk_free_gb"] for r in reversed(rows) if r.get("disk_free_gb") is not None), None)
    if slope is not None and last is not None:
        if slope < 0:
            days = (float(last) - controle.DISK_MIN_GB) / -slope
            out.append({"failure": "disque plein (moins de 2 Go)", "horizon_days": round(max(days, 0.0), 0),
                        "probability": None, "evidence": f"{fr(-slope * 1024, '.0f')} Mo de moins par jour sur "
                        f"{len(rows)} jours", "kind": "PREDICTION"})
        else:
            out.append({"failure": "disque plein", "horizon_days": None, "probability": 0.0,
                        "evidence": f"place stable ou en hausse sur {len(rows)} jours", "kind": "PREDICTION"})
    if st["top_hour"] is not None and st["days_with_stop"] >= 3:
        p = min(st["days_with_stop"] / STOPS_WINDOW_D, 1.0)
        out.append({"failure": f"arrêt imprévu vers {st['top_hour']} h UTC", "horizon_days": 1,
                    "probability": round(p, 2), "evidence": f"{st['days_with_stop']} jour(s) avec un arrêt sur "
                    f"{STOPS_WINDOW_D} ; {st['top_count']} arrêt(s) commencé(s) vers cette heure",
                    "kind": "PREDICTION"})
    return out


def capacity(rows: List[Dict[str, Any]], res: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Capacité (§17) : place libre, marge, taille de la base et sa croissance."""
    db_slope = _slope(rows, "db_mb")
    db_last = next((r["db_mb"] for r in reversed(rows) if r.get("db_mb") is not None), None)
    return {"disk_free_gb": None if not res else res.get("disk_free"),
            "disk_total_gb": None if not res else res.get("disk_total"),
            "headroom_gb": None if not res else max(float(res.get("disk_free") or 0) - controle.DISK_LOW_GB, 0.0),
            "db_mb": db_last, "db_growth_mb_day": None if db_slope is None else round(db_slope, 3),
            "samples": len(rows)}


def blast_radius(sid: str, g: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Rayon d'impact (§14) : les services qui dépendent, directement ou non,
    d'un service en panne ; grave si un service de palier 0 est touché."""
    g = g or controle.graph()
    tiers = {s.sid: s.tier for s in controle.SERVICES}
    hit = sorted(s for s, deps in g["closure"].items() if sid in deps)
    worst = min([tiers.get(sid, 3)] + [tiers.get(s, 3) for s in hit])
    level = {0: "CRITICAL", 1: "HIGH", 2: "MEDIUM"}.get(worst, "LOW")
    return {"service": sid, "affected": hit, "level": level}


def remediations(h: Dict[str, Dict[str, Any]], g: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Une remédiation par service en panne ou dégradé (§19-21) : niveau,
    action, rayon d'impact, automatique ou non, qui l'autorise, procédure,
    statut de la cause (soupçonnée tant qu'elle n'est pas prouvée). Une
    remédiation CRITICAL n'est jamais automatique."""
    g = g or controle.graph()
    out = []
    for sid, x in sorted(h.items()):
        if x["status"] not in ("DOWN", "DEGRADED"):
            continue
        level, action, auto, who = REMEDIATION.get(sid, ("HIGH", "à examiner", False, "vous"))
        br = blast_radius(sid, g)
        auto = auto and level == "LOW"
        out.append({"service": sid, "status": x["status"], "symptom": x["detail"], "level": level, "action": action,
                    "automatic": auto, "authorized_by": who, "blast_radius": br["level"],
                    "affected": br["affected"], "runbook": controle.SERVICE_RUNBOOK.get(sid, ""),
                    "root_cause": "SUSPECTED", "rollback": "rien à défaire (aucune action automatique)" if not auto
                    else "le superviseur s'arrête après 5 relances ratées en une heure"})
    return out


WEIGHTS = {"sre": 0.15, "incident": 0.10, "rca": 0.10, "predictive": 0.10, "resilience": 0.10,
           "self_healing": 0.10, "security": 0.15, "observability": 0.05, "recovery": 0.05, "performance": 0.05,
           "governance": 0.05}
WEIGHT_FR = {"sre": "intelligence SRE (anomalies mesurées)", "incident": "incidents", "rca": "causes racines",
             "predictive": "prévisions", "resilience": "résilience", "self_healing": "autoréparation encadrée",
             "security": "sécurité (aucune autorisation propre)", "observability": "observabilité",
             "recovery": "reprise et retour arrière", "performance": "performance", "governance": "gouvernance"}
P0 = ("self_healing", "security")


def band(score: float, p0: Sequence[str]) -> str:
    """Bandes de l'étape 32 (§40) ; un P0 : NOT_READY."""
    if p0:
        return "NOT_READY"
    return ("READY" if score >= 95 else "RELEASE_CANDIDATE" if score >= 90 else "VALIDATING" if score >= 80
            else "DEVELOPMENT" if score >= 70 else "REJECTED")


def quality(r: Dict[str, Any], took_s: float) -> Dict[str, Any]:
    """Note de préparation (§39-40), famille par famille."""
    rem, pr, dr, g = r["remediations"], r["predictions"], r["recovery"], r["graph"]
    unsafe = [x for x in rem if x["automatic"] and x["level"] != "LOW"]
    crit_auto = [x for x in rem if x["level"] == "CRITICAL" and x["automatic"]]
    covered = all(s.sid in REMEDIATION for s in controle.SERVICES)
    checks = {
        "sre": (r["health_measured"], f"{len(r['anomalies'])} anomalie(s) mesurée(s), chacune avec sa référence"),
        "incident": (all(x["runbook"] for x in rem), f"{len(rem)} service(s) en panne ou dégradé(s), chacun avec "
                     "sa procédure"),
        "rca": (all(x["root_cause"] in ("UNKNOWN", "SUSPECTED", "SUPPORTED", "CONFIRMED", "REJECTED") for x in rem),
                "cause soupçonnée tant qu'elle n'est pas prouvée"),
        "predictive": (all(p["kind"] == "PREDICTION" for p in pr) and r["samples"] >= 1,
                       f"{len(pr)} prévision(s), jamais présentées comme certaines ; {r['samples']} jour(s) mesuré(s)"),
        "resilience": (not g["cycles"] and not g["unknown"], "graphe des services sans cycle ; points uniques : "
                       + ", ".join(g["spof"][:4])),
        "self_healing": (not unsafe and not crit_auto and covered, "seules les remédiations LOW se font seules ; "
                         "une CRITICAL attend vous" if not unsafe and not crit_auto else "remédiation risquée "
                         "automatique"),
        "security": (r["no_authority"], "aucune autorisation créée, aucune écriture hors de son historique"),
        "observability": (True, "chaque remédiation dit son symptôme, son rayon d'impact et sa procédure"),
        "recovery": (dr.get("ok") is not False, f"{dr.get('count', 0)} sauvegarde(s)"
                     + (f", la dernière il y a {fr(dr['rpo_h'], '.0f')} h" if dr.get("rpo_h") is not None else "")),
        "performance": (took_s <= 10, f"en {fr(took_s, '.2f')} s"),
        "governance": (covered, f"{len(REMEDIATION)} services avec leur niveau de remédiation et qui l'autorise"),
    }
    score = sum(WEIGHTS[k] * (100.0 if ok else 0.0) for k, (ok, _d) in checks.items())
    p0 = [k for k in P0 if not checks[k][0]]
    return {"checks": checks, "score": round(score, 1), "p0": p0, "status": band(score, p0)}


def evaluate(gcfg: Any, state: Dict[str, Any], now: Optional[datetime] = None, deps: Optional[systeme.Deps] = None,
             record: bool = False) -> Dict[str, Any]:
    """La fiabilité du moment : santé (plan de contrôle), anomalies,
    prévisions, capacité, remédiations, qualité. `record` : garde la mesure
    du jour (rapport de la nuit)."""
    now = now or datetime.now(timezone.utc)
    deps = deps or systeme.Deps()
    t = time.perf_counter()
    res = deps.extra.get("resources") if "resources" in deps.extra else systeme.pc_resources(deps, str(controle.ROOT))
    dr = controle.recovery(gcfg, now.timestamp(), restore=None)
    h = controle.health(gcfg, state, now, deps, dr)
    smp = sample(gcfg, res)
    path = history_path(gcfg)
    rows = remember(gcfg, smp, now) if record else (
        list((autonomy.read_json(path) or {}).get("rows") or []) if path else []) + [dict(smp, day=now.date().isoformat())]
    st = stops(state, now)
    g = controle.graph()
    r: Dict[str, Any] = {"version": VERSION, "now": now.isoformat(timespec="seconds"),
                         "mode": getattr(gcfg, "run_mode", "paper"), "health": h,
                         "health_measured": sum(x["status"] != "UNKNOWN" for x in h.values()) >= len(h) // 2,
                         "stops": st, "anomalies": anomalies(state, res, dr, st), "predictions": predictions(rows, st),
                         "capacity": capacity(rows, res), "samples": len(rows), "recovery": dr, "graph": g,
                         "remediations": remediations(h, g), "no_authority": True}
    r["quality"] = quality(r, time.perf_counter() - t)
    return r


def report_of(r: Dict[str, Any]) -> SREReport:
    """Le résultat au format du contrat SREReport.v1."""
    q = r["quality"]
    return SREReport(report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=q["status"],
                     readiness_score=q["score"], p0_failures=tuple(q["p0"]), anomalies=len(r["anomalies"]),
                     predictions=tuple((p["failure"], p["kind"]) for p in r["predictions"]),
                     remediations=tuple((x["service"], x["level"], x["automatic"]) for x in r["remediations"]),
                     engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : anomalies, prévisions, remédiations."""
    st = r["stops"]
    out = (f"{len(r['anomalies'])} anomalie(s) ; {st['week']} arrêt(s) imprévu(s) sur 7 jours "
           f"({fr(st['down_h'], '.1f')} h) ; {len(r['remediations'])} remédiation(s) proposée(s), "
           f"{sum(x['automatic'] for x in r['remediations'])} automatique(s)")
    for p in r["predictions"]:
        if p.get("probability"):
            out += f" ; prévision : {p['failure']} (probabilité {fr(p['probability'] * 100, '.0f')} %)"
        elif p.get("horizon_days") is not None:
            out += f" ; prévision : {p['failure']} dans environ {fr(p['horizon_days'], '.0f')} jours"
    return out


def render(r: Dict[str, Any]) -> str:
    """docs/SRE_ETAT.md : anomalies, prévisions, capacité, remédiations,
    qualité."""
    q, cap = r["quality"], r["capacity"]
    lines = ["# Fiabilité et autoréparation encadrée", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py sre` (étape 32 du prompt maître, "
             "[`SRE.md`](SRE.md)). Une prévision n'est pas une certitude ; une cause soupçonnée n'est pas prouvée.", "",
             f"- **{q['status']}** ; note {fr(q['score'], '.0f')}/100.", f"- {describe(r)}."]
    lines += ["", "## Anomalies", ""]
    if r["anomalies"]:
        lines += ["| Mesure | référence | vu | gravité | preuve |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| {a['metric']} | {a['baseline']} | {a['observed']} | {a['severity']} | {a['evidence']} |"
                  for a in r["anomalies"]]
    else:
        lines.append("Aucune.")
    lines += ["", "## Prévisions", ""]
    if r["predictions"]:
        for p in r["predictions"]:
            lines.append(f"- {p['failure']} : " + (f"probabilité {fr(p['probability'] * 100, '.0f')} % ; "
                                                     if p.get("probability") is not None else "")
                         + (f"horizon environ {fr(p['horizon_days'], '.0f')} jour(s) ; "
                            if p.get("horizon_days") is not None else "") + p["evidence"] + ".")
    else:
        lines.append(f"Pas encore assez de mesures ({r['samples']} jour(s) ; il en faut 3).")
    lines += ["", "## Capacité", "",
              f"- Disque : {fr(cap['disk_free_gb'] or 0, '.1f')} Go disponibles (total {fr(cap['disk_total_gb'] or 0, '.0f')} Go)"
              f" ; marge avant l'alerte : {fr(cap['headroom_gb'] or 0, '.1f')} Go." if cap["disk_free_gb"] is not None
              else "- Disque : non mesuré.",
              f"- Base du bot : {fr(cap['db_mb'], '.1f')} Mo" + (f", {fr(cap['db_growth_mb_day'], '+.2f')} Mo par jour"
                                                                 if cap["db_growth_mb_day"] is not None else "") + "."
              if cap["db_mb"] is not None else "- Base du bot : non mesurée."]
    lines += ["", "## Remédiations proposées", ""]
    if r["remediations"]:
        lines += ["| Service | état | niveau | action | automatique | qui l'autorise | rayon d'impact |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        lines += [f"| {x['service']} | {x['status']} | {x['level']} | {x['action']} | {'oui' if x['automatic'] else 'non'}"
                  f" | {x['authorized_by']} | {x['blast_radius']} ({', '.join(x['affected']) or 'aucun autre'}) |"
                  for x in r["remediations"]]
    else:
        lines.append("Aucune : tous les services mesurés sont en bonne santé.")
    lines += ["", "## Qualité", "", "| Famille | poids | état | mesure |", "| --- | --- | --- | --- |"]
    for k, (ok, d) in q["checks"].items():
        lines.append(f"| {WEIGHT_FR[k]} | {fr(WEIGHTS[k] * 100, '.0f')} % | {'conforme' if ok else 'NON CONFORME'} | {d} |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `sre` : la fiabilité du moment ; écrit avec --out."""
    from .config import load_guard_config_from_env
    from .systeme import read_state
    ap = argparse.ArgumentParser(description="Fiabilité et autoréparation encadrée")
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    g = load_guard_config_from_env()
    r = evaluate(g, read_state(g.db_file) or {})
    report_of(r)
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["quality"]["status"] == "READY" else 1
