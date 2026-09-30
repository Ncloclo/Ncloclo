"""Rapport quotidien : analyse profonde, diagnostic expert du fond et de la
forme, et sécurité du bot (docs/RAPPORT.md).

Chaque jour à 00:30 UTC, après la décision de 00:02, les leçons de
l'apprentissage et les épreuves de l'évolution, le bot lance ce rapport dans
un processus séparé : la surveillance des stops n'est jamais ralentie.

Autonome et automatique, avec sagesse :
- il applique seul les protections sûres et réversibles : sauvegarde de sa
  base (14 jours gardés) avec contrôle d'intégrité, droits du fichier des
  secrets limités à son propriétaire, secret masqué dans un journal où il
  apparaîtrait ;
- tout le reste est un constat ou une recommandation : il ne touche jamais
  aux règles, au risque, aux clés, au mode réel, au code, aux bibliothèques
  ni aux réglages de Windows.

Le rapport est gardé (panneau ▸ Réglages ▸ Rapport quotidien), puis envoyé
par e-mail (complet) et par WhatsApp (résumé). Aucun secret n'y figure :
seulement leur présence et leur état.

  python trendguard_bot.py rapport              # dernier rapport
  python trendguard_bot.py rapport maintenant   # analyse, rapport et envoi
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import v29

from . import autonomy, maintenance
from .report_health import (
    bot_checks,
    check_log,
    code_checks,
    panel_fetch,
    resource_checks,
    skills_checks,
    strategy_checks,
)
from .report_render import render_html, render_short, render_text
from .report_security import (
    backup_database,
    binance_key_check,
    check_database,
    check_env_permissions,
    check_env_published,
    check_secret_leaks,
    check_windows,
    library_checks,
)
from .systeme import Check, Deps, chk, github_json, read_state

REPORT_MINUTE = 30              # 00:30 UTC
KEEP_REPORTS = 30
LOCK_STALE_SEC = 1800


def paths(gcfg: Any) -> Dict[str, str]:
    base = autonomy.sidecar(gcfg.lock_file, "")
    root = os.path.dirname(base) if base else v29.APP_DIR
    return {"json": base + ".rapport.json" if base else "",
            "lock": base + ".rapport.lock" if base else "",
            "log": base + ".rapport.log" if base else os.devnull,
            "archive": os.path.join(root, "rapports"), "backups": os.path.join(root, "sauvegardes"),
            "root": v29.APP_DIR}


def load_latest(gcfg: Any) -> Optional[Dict[str, Any]]:
    return autonomy.read_json(paths(gcfg)["json"]) or None


def is_running(gcfg: Any) -> bool:
    lock = paths(gcfg)["lock"]
    try:
        return bool(lock) and time.time() - os.path.getmtime(lock) < LOCK_STALE_SEC
    except OSError:
        return False


# Lignes du centre de sécurité déjà vérifiées par le rapport lui-même.
PANEL_DUPLICATES = {"Disponibilité du bot (7 j)", "Relance automatique", "Arrêt d'urgence", "Mode",
                    "Clés API Binance", "Fichier des secrets (.env)", "Rapport quotidien",
                    "Évolution encadrée", "Alimentation du PC", "Espace disque", "Mémoire du PC"}


def build(gcfg: Any, env: Dict[str, str], deps: Optional[Deps] = None,
          backups: bool = True, applied: Optional[List[Check]] = None) -> Dict[str, Any]:
    """Le rapport complet ; `applied` : recommandations déjà appliquées seules
    (maintenance.py), montrées en tête."""
    deps = deps or Deps()
    now = deps.now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    root = v29.APP_DIR if deps.extra.get("root") is None else deps.extra["root"]
    st = read_state(gcfg.db_file)
    pnl = (deps.panel or panel_fetch)(int(env.get("PANEL_PORT", "8765") or 8765),
                                      env.get("PANEL_PASSWORD", ""))
    status, security = pnl.get("status"), pnl.get("security")
    gh = deps.http_json or github_json

    sec = [check_env_published(root, deps), check_env_permissions(root, deps)]
    sec += check_secret_leaks(root, env, deps)
    sec.append((deps.binance or binance_key_check)(env, bool(gcfg.binance_testnet)))
    if backups:
        try:
            sec.append(backup_database(gcfg.db_file, paths(gcfg)["backups"], day))
        except (OSError, sqlite3.Error) as e:
            sec.append(chk("Sauvegarde de la base", False, f"impossible ({type(e).__name__})",
                           "Vérifiez l'espace disque."))
    sec.append(check_database(gcfg.db_file))
    sec += check_windows(deps)
    sec += library_checks(root, deps)
    panel_sec = [chk(c["label"], c["ok"], c["detail"],
                     f"{c['label']} : {c['detail']}" if c.get("ok") is False else "")
                 for c in (security or {}).get("checks", [])
                 if c.get("label") not in PANEL_DUPLICATES]
    health = bot_checks(gcfg, st, status, now) + resource_checks(deps, root)
    health.insert(0, chk("Panneau de contrôle", "ms" in pnl,
                         f"en marche, répond en {pnl['ms']} ms" if "ms" in pnl else "injoignable",
                         "" if "ms" in pnl else "Relancez le panneau (il démarre avec l'ordinateur)."))
    code, proposals = code_checks(root, deps, gh)
    form = [check_log(gcfg.log_file, now)] + code
    sections = [
        {"title": "Recommandations appliquées seules", "checks": list(applied or [])},
        {"title": "Sécurité", "checks": sec},
        {"title": "Centre de sécurité du panneau", "checks": panel_sec},
        {"title": "Santé du bot (le fond)", "checks": health},
        {"title": "Stratégie (le fond)", "checks": strategy_checks(gcfg, deps)},
        {"title": "Compétences acquises", "checks": skills_checks(gcfg, st)},
        {"title": "Code, journal et panneau (la forme)", "checks": form},
    ]
    sections = [s for s in sections if s["checks"]]
    allc = [c for s in sections for c in s["checks"]]
    score = {"ok": sum(1 for c in allc if c["ok"] is True),
             "warn": sum(1 for c in allc if c["ok"] is False),
             "info": sum(1 for c in allc if c["ok"] is None), "total": len(allc)}
    recos: List[str] = []
    for level in (False, None, True):
        for c in allc:
            if c["ok"] is level and c.get("reco") and c["reco"] not in recos:
                recos.append(c["reco"])
    report = {"ready": True, "day": day, "generated_at": now.isoformat(timespec="seconds"),
              "mode": gcfg.run_mode, "score": score,
              "verdict": ("Tout est en ordre" if score["warn"] == 0
                          else f"{score['warn']} point(s) à corriger"),
              "actions": [c["action"] for c in allc if c.get("action")],
              "recommendations": recos, "proposals": proposals, "sections": sections,
              "delivery": {}}
    report["text"] = render_text(report)
    report["short"] = render_short(report)
    return report


def save(gcfg: Any, report: Dict[str, Any], keep: int = KEEP_REPORTS) -> None:
    """Rapport gardé (panneau), historique des scores (tendance) et archive."""
    p = paths(gcfg)
    prev = load_latest(gcfg) or {}
    hist = [h for h in prev.get("history") or [] if isinstance(h, dict) and h.get("day") != report["day"]]
    s = report["score"]
    report["history"] = hist[-(KEEP_REPORTS - 1):] + [
        {"day": report["day"], "ok": s["ok"], "warn": s["warn"], "total": s["total"]}]
    if p["json"]:
        autonomy.write_json(p["json"], report)
    os.makedirs(p["archive"], exist_ok=True)
    with open(os.path.join(p["archive"], f"rapport-{report['day']}.txt"), "w", encoding="utf-8") as fh:
        fh.write(report["text"] + "\n")
    old = sorted(glob.glob(os.path.join(p["archive"], "rapport-????-??-??.txt")))
    for f in old[:-keep]:
        try:
            os.remove(f)
        except OSError:
            pass


def deliver(report: Dict[str, Any], hub: Any) -> Dict[str, Dict[str, Any]]:
    """E-mail : rapport complet (page mise en forme, texte en secours) ;
    WhatsApp et Telegram : résumé."""
    subject = f"TrendGuard — rapport du {report['day']} : {report['verdict']}"
    res = hub.send_report(subject, report["text"], report["short"], render_html(report))
    at = time.time()
    return {name: {"at": at, "ok": err is None, "error": err} for name, err in res.items()}


def generate(gcfg: Any, env: Dict[str, str], send: bool = True, deps: Optional[Deps] = None,
             hub: Any = None, maintain: bool = True) -> Dict[str, Any]:
    """Recommandations sûres appliquées seules (maintenance.py), analyse,
    protections, rapport gardé puis envoyé. Un seul à la fois."""
    # Chargés avant une éventuelle mise à jour : ce processus garde une
    # version cohérente du code jusqu'à la fin du rapport.
    from . import alerts, cli  # noqa: F401
    p = paths(gcfg)
    if p["lock"]:
        if is_running(gcfg):
            raise RuntimeError("un rapport est déjà en cours")
        with open(p["lock"], "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    try:
        deps = deps or Deps()
        root = deps.extra.get("root") or v29.APP_DIR
        port = int(env.get("PANEL_PORT", "8765") or 8765)
        applied = maintenance.run_all(gcfg, deps, root, port) if maintain else []
        report = build(gcfg, env, deps, applied=applied)
        save(gcfg, report)
        if send:
            own = hub is None
            if own:
                hub = alerts.build_notifier(env=env, pause_file=alerts.PAUSE_FILE)
            try:
                report["delivery"] = deliver(report, hub)
            finally:
                if own:
                    hub.close()
            save(gcfg, report)
        return report
    finally:
        if p["lock"]:
            try:
                os.remove(p["lock"])
            except OSError:
                pass


def launch(gcfg: Any, action: str = "quotidien") -> bool:
    """Rapport dans un processus séparé (bot à 00:30, bouton du panneau)."""
    if is_running(gcfg):
        return False
    return autonomy.launch_tool(gcfg, ["rapport", action], ".rapport.log")


def main(argv: Optional[List[str]] = None) -> int:
    """Ligne de commande du rapport : afficher le dernier (défaut), le générer
    maintenant ou comme la routine de 00:30 (quotidien), installer une mise
    à jour validée, remettre les réglages de veille d'origine (restaurer)
    ou reprendre les corrections automatiques (corriger)."""
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Rapport quotidien : sécurité et diagnostic expert")
    ap.add_argument("action", nargs="?", default="dernier",
                    choices=["dernier", "maintenant", "quotidien", "installer", "restaurer",
                             "corriger"])
    ap.add_argument("--sans-envoi", action="store_true", help="garder le rapport sans l'envoyer")
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    gcfg = load_guard_config_from_env()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    last = load_latest(gcfg)
    if args.action == "dernier":
        print(last["text"] if last else "Aucun rapport pour l'instant : python trendguard_bot.py "
                                        "rapport maintenant")
        return 0
    if args.action == "restaurer":
        print(maintenance.restore_power(gcfg, Deps()))
        return 0
    if args.action == "corriger":
        print(maintenance.resume_fixes(gcfg))
        return 0
    if args.action == "installer":
        c = maintenance.update(gcfg, Deps(), v29.APP_DIR, int(os.environ.get("PANEL_PORT", "8765")),
                               allow_live=True)
        print(f"{stamp} {c['label']} : {c['detail']}" + (f"\n→ {c['reco']}" if c["reco"] else ""))
        return 0 if c["ok"] is not False else 1
    if args.action == "quotidien" and last and last.get("day") == datetime.now(timezone.utc).date().isoformat():
        print(f"{stamp} Rapport du jour déjà fait.")
        return 0
    try:
        r = generate(gcfg, dict(os.environ), send=not args.sans_envoi)
    except RuntimeError as e:
        print(f"{stamp} {e}")
        return 1
    sent = ", ".join(f"{n} {'✓' if d['ok'] else '✗'}" for n, d in r["delivery"].items()) or "non envoyé"
    print(f"{stamp} {r['verdict']} ({r['score']['ok']}/{r['score']['total']}) ; envoi : {sent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
