"""
Diagnostic expert par le noyau cognitif (prompt maître, étape 4 ;
docs/COGNITIF.md) : ce que vous demandez avec « analyse et diagnostique
expert », fait par le bot lui-même, en lecture seule.

    python trendguard_bot.py expert            # complet (télécharge les cours publics)
    python trendguard_bot.py expert --rapide   # sans réseau

Plan : état du bot, décision du jour, journal d'audit, journal financier,
journal du bot, ressources du PC, plantages, Wi-Fi, stratégie. Puis
vérifications croisées entre sources indépendantes (audit contre journal
financier, positions contre ordres ouverts, décision et données à l'heure),
incertitude, synthèse et décision. Le diagnostic propose ; il n'agit jamais
(niveau d'autonomie 1 : analyse) : une mesure de sûreté proposée attend
votre accord. Trace gardée dans <bot>.expert.json (Rachelle la résume).
"""

from __future__ import annotations

import argparse
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import v29

from . import audit, autonomy, donnees, porte
from .bot_types import last_closed_day
from .cognitif import (
    AutonomyPolicy,
    Check,
    Finding,
    Orchestrator,
    Proposal,
    Task,
    TaskGraph,
    TaskRun,
    Tool,
    ToolRegistry,
    decide,
    trace,
    uncertainty,
)
from .systeme import Deps, read_state
from .texte import fr

GOAL = "analyse et diagnostic expert du bot, de sa stratégie et de son PC"
SAFE_MODE_ACTION = "activer le mode sûr : python trendguard_bot.py mode-sur on"


def path_for(gcfg: Any) -> str:
    """Trace du dernier diagnostic, à côté du verrou du bot."""
    return autonomy.sidecar(getattr(gcfg, "lock_file", ""), ".expert.json")


# ---------- Outils (lecture seule) ----------

def t_state(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return read_state(ctx["gcfg"].db_file)


def t_decision(ctx: Dict[str, Any]) -> Dict[str, Any]:
    st = ctx["results"]["etat"]
    expected = last_closed_day(ctx["now"], ctx["gcfg"].decision_delay_sec)
    return {"expected": expected, "last": st.get("last_decision_day")}


def t_audit(ctx: Dict[str, Any]) -> Dict[str, Any]:
    path = audit.path_for(ctx["gcfg"])
    return {"verify": audit.verify(path), "events": audit.read(path, 100_000)}


def t_journal(ctx: Dict[str, Any]) -> Dict[str, Any]:
    db = donnees.path_for(ctx["gcfg"])
    if db == ":memory:" or not os.path.exists(db):
        return {"verify": None}
    j = donnees.Journal(db, readonly=True)
    try:
        v = j.verify()
        return dict(j.for_checks(), verify=v) if v["schema"] else {"verify": v}
    finally:
        j.close()


LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[(WARNING|ERROR|CRITICAL)\] (\[[^\]]+\])?")


def t_log(ctx: Dict[str, Any]) -> Dict[str, Any]:
    path = ctx["gcfg"].log_file
    since = (ctx["now"] - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    tags: Counter = Counter()
    if path and os.path.exists(path):
        with open(path, "rb") as fh:
            fh.seek(max(0, os.path.getsize(path) - 2_000_000))
            for line in fh.read().decode("utf-8", "replace").splitlines():
                m = LOG_LINE.match(line)
                if m and m.group(1) >= since:
                    tags[(m.group(2), m.group(3) or "")] += 1
    return {"tags": [[lvl, tag, n] for (lvl, tag), n in tags.most_common()]}


def t_pc(ctx: Dict[str, Any]) -> Dict[str, Any]:
    from .report_health import resource_checks
    from .report_security import battery_check
    from .systeme import power_source
    checks = resource_checks(ctx["deps"], ctx["root"])
    b = battery_check(power_source(ctx["deps"]))
    return {"checks": checks + ([b] if b else [])}


def t_crashes(ctx: Dict[str, Any]) -> Dict[str, Any]:
    from .report_health import crash_check
    return {"checks": crash_check(ctx["deps"], ctx["now"])}


def t_wifi(ctx: Dict[str, Any]) -> Dict[str, Any]:
    from .report_health import wifi_check
    return {"checks": wifi_check(ctx["deps"], ctx["now"])}


def t_strategy(ctx: Dict[str, Any]) -> Dict[str, Any]:
    deps = ctx["deps"]
    if deps.diagnose is not None:
        findings, day = deps.diagnose(ctx["gcfg"])
    else:
        from .cli import diagnose_findings
        findings, day = diagnose_findings(ctx["gcfg"])
    return {"day": day, "findings": [{"level": f.level, "section": f.section, "message": f.message,
                                      "reco": getattr(f, "reco", "")} for f in findings]}


def registry() -> ToolRegistry:
    """Les outils du diagnostic : tous en lecture seule ; aucun n'achète,
    ne vend, ne transfère ni ne modifie un réglage."""
    return ToolRegistry([
        Tool("etat", "état du bot (base, lecture seule)", t_state, "DATA"),
        Tool("decision", "décision du jour à l'heure", t_decision, "COMPUTE"),
        Tool("audit", "chaîne du journal d'audit", t_audit, "DATA"),
        Tool("journal", "journal financier (lecture seule)", t_journal, "DATA"),
        Tool("log", "avertissements et erreurs du journal du bot sur 24 h", t_log, "READ_ONLY"),
        Tool("pc", "disque, mémoire, alimentation", t_pc, "READ_ONLY", timeout=60),
        Tool("plantages", "plantages de Windows", t_crashes, "READ_ONLY", timeout=60),
        Tool("wifi", "Wi-Fi des dernières 24 heures", t_wifi, "READ_ONLY", timeout=60),
        Tool("strategie", "diagnostic de la stratégie sur les cours publics", t_strategy, "RESEARCH",
             risk="MEDIUM", timeout=600, network=True),
    ])


def plan(quick: bool = False) -> TaskGraph:
    """Le plan du diagnostic (graphe de tâches)."""
    tasks = [Task("etat", "etat", title="état du bot"),
             Task("decision", "decision", ("etat",), title="décision du jour"),
             Task("audit", "audit", title="journal d'audit"),
             Task("journal", "journal", title="journal financier"),
             Task("log", "log", title="journal du bot"),
             Task("pc", "pc", title="PC"),
             Task("plantages", "plantages", title="plantages de Windows"),
             Task("wifi", "wifi", title="Wi-Fi")]
    if not quick:
        tasks.append(Task("strategie", "strategie", retries=1, title="stratégie"))
    return TaskGraph(GOAL, tasks)


# ---------- Vérifications croisées ----------

def cross_checks(runs: Dict[str, TaskRun]) -> List[Check]:
    """Deux sources indépendantes pour chaque fait important."""
    r = {k: v.result for k, v in runs.items() if v.state.state == "COMPLETED"}
    out = []
    st, dec = r.get("etat"), r.get("decision")
    if dec:
        ok = dec["last"] == dec["expected"]
        out.append(Check("Décision du jour à l'heure", "TEMPORAL_CHECK", "VERIFIED" if ok else "CONTRADICTED",
                         f"dernière décision du {dec['last']}, attendue du {dec['expected']}"))
    if st:
        q = st.get("qualite") or {}
        if q:
            ok = q.get("day") == st.get("last_decision_day")
            out.append(Check("Données de la décision fraîches", "TEMPORAL_CHECK", "VERIFIED" if ok else "CONTRADICTED",
                             f"qualité notée sur la bougie du {q.get('day')}, décision du {st.get('last_decision_day')}"))
    a, j = r.get("audit"), r.get("journal")
    if a and j and j.get("verify") and j["verify"]["schema"]:
        since = j["since"]
        events = [e for e in a["events"] if str(e.get("timestamp", "")) >= since]
        n_a = sum(1 for e in events if e.get("action") == "ordre.achat" and e.get("result") == "exécuté")
        n_j = len(j["buys"])
        out.append(Check("Achats : audit = journal financier", "CONSISTENCY_CHECK",
                         "VERIFIED" if n_a == n_j else "CONTRADICTED",
                         f"{n_a} achat(s) dans l'audit, {n_j} dans le journal financier"))
        n_a = sum(1 for e in events if e.get("action") == "ordre.vente")
        n_j = len(j["trades"])
        out.append(Check("Ventes : audit = journal financier", "CONSISTENCY_CHECK",
                         "VERIFIED" if n_a == n_j else "CONTRADICTED",
                         f"{n_a} vente(s) dans l'audit, {n_j} trade(s) dans le journal financier"))
    if st and j and j.get("verify") and j["verify"]["schema"]:
        traced = {h["trace"]["key"] for h in ((st.get("paper") or {}).get("holdings") or {}).values()
                  if isinstance(h, dict) and h.get("trace")}
        traced |= {t["key"] for t in (st.get("entry_traces") or {}).values() if isinstance(t, dict)}
        open_keys = {o["idempotency_key"] for o in j["open"]}
        missing = traced - open_keys
        out.append(Check("Positions : état du bot = ordres ouverts du journal", "CONSISTENCY_CHECK",
                         "VERIFIED" if not missing else "CONTRADICTED",
                         f"{len(traced)} position(s) tracée(s), {len(missing)} sans ordre ouvert"
                         + (" (positions achetées avant le journal non comptées)" if not traced else "")))
    return out


# ---------- Constats ----------

def findings(runs: Dict[str, TaskRun], checks: List[Check], gcfg: Any) -> Tuple[List[Finding], List[Proposal]]:
    """Les constats de chaque tâche, leur gravité, et les mesures de sûreté
    proposées (jamais appliquées seules)."""
    r = {k: v.result for k, v in runs.items() if v.state.state == "COMPLETED"}
    out: List[Finding] = []
    block: List[Proposal] = []
    st = r.get("etat") or {}
    if st:
        eq, start = st.get("last_equity"), st.get("start_equity")
        perf = f" ({fr((float(eq) / float(start) - 1) * 100, '+.1f')} % depuis le départ)" if eq and start else ""
        held = sorted(((st.get("paper") or {}).get("holdings") or {}))
        out.append(Finding("Bot", "info", f"capital à la dernière décision {fr(float(eq or 0), ',.2f')} USDT{perf}, "
                                          f"{len(held)} position(s) : {', '.join(a.upper() for a in held) or 'aucune'}",
                           "etat"))
        if st.get("halted"):
            out.append(Finding("Arrêt d'urgence", "élevée", f"déclenché : {st.get('halt_reason')}", "etat",
                               "lire la cause ; reprise automatique prévue ou commande resume"))
        if (st.get("mode_sur") or {}).get("active"):
            out.append(Finding("Mode sûr", "moyenne", f"actif ({st['mode_sur'].get('reason')}) : aucun achat",
                               "etat", "le lever quand vous le voulez : python trendguard_bot.py mode-sur off"))
        q = (st.get("qualite") or {}).get("score")
        if q is not None and q < 90:
            out.append(Finding("Qualité des données", "élevée" if q < porte.QUALITY_MIN else "moyenne",
                               (st.get("qualite") or {}).get("text", ""), "etat"))
        sr = (st.get("stress") or {}).get("text")
        if sr:
            out.append(Finding("Tests de résistance", "moyenne" if "déclencherait" in sr else "info", sr, "etat"))
        pt = st.get("porte") or {}
        if pt.get("refused"):
            out.append(Finding("Porte d'exécution", "info", f"{pt['refused']} achat(s) refusé(s) : "
                               + " ; ".join(pt.get("reasons") or []), "etat"))
    for c in checks:
        if c.verdict == "CONTRADICTED":
            out.append(Finding(c.name, "élevée", c.detail, "vérification",
                               "vérifier le journal du bot autour de l'heure indiquée"))
    a = r.get("audit")
    if a and not a["verify"]["ok"]:
        out.append(Finding("Journal d'audit", "critique", audit.describe(a["verify"]), "audit"))
        block.append(Proposal(SAFE_MODE_ACTION, "journal d'audit modifié : plus aucun achat le temps de comprendre"))
    j = r.get("journal")
    if j and j.get("verify") and not j["verify"]["ok"]:
        out.append(Finding("Journal financier", "critique", donnees.describe(j["verify"]), "journal"))
        block.append(Proposal(SAFE_MODE_ACTION, "journal financier incohérent : plus aucun achat le temps de comprendre"))
    for key in ("pc", "plantages", "wifi"):
        for c in (r.get(key) or {}).get("checks") or []:
            if c.get("ok") is False:
                sev = "élevée" if key == "plantages" else "moyenne"
                out.append(Finding(c["label"], sev, c["detail"], key, c.get("reco") or ""))
                if key == "plantages" and gcfg.run_mode == "live":
                    block.append(Proposal(SAFE_MODE_ACTION, "PC instable avec de l'argent réel en jeu"))
    errs = [(lvl, tag, n) for lvl, tag, n in (r.get("log") or {}).get("tags") or []
            if lvl in ("ERROR", "CRITICAL") and tag not in ("[NOTIFY]", "[NOTIFIER-OFF]")]
    if errs:
        out.append(Finding("Journal du bot (24 h)", "info", ", ".join(f"{tag or '(sans étiquette)'} × {n}"
                                                                     for _l, tag, n in errs[:6]), "log"))
    for f in (r.get("strategie") or {}).get("findings") or []:
        if f["level"] in ("ALERTE", "ATTENTION"):
            out.append(Finding(f"Stratégie : {f['section']}", "élevée" if f["level"] == "ALERTE" else "moyenne",
                               f["message"], "strategie", f.get("reco") or ""))
    return out, block


def run(gcfg: Any, quick: bool = False, deps: Optional[Deps] = None, now: Optional[datetime] = None,
        root: str = v29.APP_DIR, policy: Optional[AutonomyPolicy] = None) -> Dict[str, Any]:
    """Le diagnostic complet : plan, orchestration, vérifications,
    incertitude, décision ; trace enregistrée et renvoyée."""
    deps = deps or Deps()
    now = now or datetime.now(timezone.utc)
    policy = policy or AutonomyPolicy(network=not quick)
    orch = Orchestrator(registry(), policy)
    runs = orch.run(plan(quick), {"gcfg": gcfg, "deps": deps, "now": now, "root": root})
    checks = cross_checks(runs)
    level, why = uncertainty(runs, checks)
    found, block = findings(runs, checks, gcfg)
    result = decide(found, level, block)
    t = trace(GOAL, runs, checks, result)
    t["uncertainty_reasons"] = why
    t["mode"] = gcfg.run_mode
    path = path_for(gcfg)
    if path:
        autonomy.write_json(path, t)
    return t


DECISION_TEXT = {"NO_ACTION": "rien d'anormal, aucune action nécessaire",
                 "ESCALATE": "des points demandent votre attention",
                 "BLOCK": "une mesure de sûreté est proposée (à votre accord)",
                 "RESEARCH_MORE": "trop d'éléments manquent pour conclure : à refaire",
                 "WAIT": "attendre", "ANSWER": "réponse", "SIMULATE": "simulation"}


def render(t: Dict[str, Any]) -> str:
    """Le diagnostic en texte clair."""
    lines = [f"DIAGNOSTIC EXPERT — {t['at'][:16].replace('T', ' ')} UTC (mode {t.get('mode')})",
             f"Décision : {t['decision']} — {DECISION_TEXT.get(t['decision'], '')}",
             f"Incertitude : {t['uncertainty']}" + (" (" + " ; ".join(t.get("uncertainty_reasons") or []) + ")"
                                                    if t.get("uncertainty_reasons") else ""), "", "Constats :"]
    for f in t["findings"]:
        lines.append(f"  [{f['severity']}] {f['subject']} : {f['detail']}"
                     + (f"\n      → {f['action']}" if f.get("action") and f["severity"] != "info" else ""))
    lines += ["", "Vérifications croisées :"]
    lines += [f"  {'✓' if c['verdict'] == 'VERIFIED' else '✗'} {c['name']} : {c['detail']}" for c in t["checks"]] \
        or ["  (aucune)"]
    if t["proposals"]:
        lines += ["", "Proposé (rien n'est fait sans vous) :"]
        lines += [f"  - {p['action']} ({p['reason']})" for p in t["proposals"]]
    lines += ["", "Plan exécuté :"]
    lines += [f"  {x['state']:<9} {x['title']}" + (f" ({x['error']})" if x["error"] else "") for x in t["tasks"]]
    if t["partial"]:
        lines.append("Résultat PARTIEL : certaines tâches n'ont pas abouti (voir ci-dessus).")
    return "\n".join(lines)


def summary(t: Optional[Dict[str, Any]]) -> str:
    """Trois lignes pour Rachelle et le panneau."""
    if not t or not t.get("decision"):
        return ""
    serious = [f for f in t.get("findings") or [] if f.get("severity") != "info"]
    text = (f"Dernier diagnostic expert ({t['at'][:16].replace('T', ' à ')} UTC) : "
            f"{DECISION_TEXT.get(t['decision'], t['decision'])}, incertitude {t['uncertainty']}.")
    if serious:
        text += " Points : " + " ; ".join(f"{f['subject']} ({f['severity']})" for f in serious[:4]) + "."
    if t.get("proposals"):
        text += " Proposé, à votre accord : " + " ; ".join(p["action"].rstrip(".") for p in t["proposals"][:2]) + "."
    return text


def main(argv: Optional[List[str]] = None) -> int:
    """Commande `expert` : le diagnostic expert du noyau cognitif."""
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Diagnostic expert de TrendGuard (noyau cognitif)")
    ap.add_argument("--rapide", action="store_true", help="sans réseau (pas de diagnostic de la stratégie)")
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    t = run(load_guard_config_from_env(), quick=args.rapide)
    print(render(t))
    return 0
