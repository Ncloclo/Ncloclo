"""Rapport quotidien, santé du fond et de la forme (docs/RAPPORT.md) :
panneau, bot, disque et mémoire du PC, journal des 24 dernières heures,
stratégie, compétences acquises, code et contrôles GitHub. Lecture seule.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import evolution, learning
from .systeme import Check, Deps, chk, ci_status, pc_resources, repo_slug
from .systeme import git as _git
from .texte import fr

_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
DISK_MIN_GB = 2                 # en dessous, le bot ne peut plus écrire longtemps
DISK_MIN_PCT = 10               # en dessous, Windows manque de place pour ses mises à jour
DISK_RECO = (f"Libérez de la place sur le disque : moins de {DISK_MIN_PCT} % ou de {DISK_MIN_GB} Go sont "
             "libres (Windows en a besoin pour ses mises à jour, le bot pour sa base et ses journaux).")
MEMORY_MAX_PCT = 90             # au-dessus, Windows refuse bientôt de la mémoire aux programmes


def panel_fetch(port: int, password: str) -> Dict[str, Any]:
    """Statut et centre de sécurité du panneau en marche (ce PC)."""
    base = f"http://127.0.0.1:{port}"
    out: Dict[str, Any] = {}
    t0 = time.time()
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=10) as r:
            r.read()
        out["ms"] = round((time.time() - t0) * 1000)
    except (OSError, urllib.error.URLError):
        return out
    headers: Dict[str, str] = {}
    if password:
        req = urllib.request.Request(
            base + "/api/login", method="POST", data=json.dumps({"password": password}).encode(),
            headers={"X-TrendGuard": "1", "Origin": base, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                cookie = (r.headers.get("Set-Cookie") or "").split(";")[0]
                headers["Cookie"] = cookie
        except (OSError, urllib.error.URLError):
            return out
    for key, path in (("status", "/api/status"), ("security", "/api/security")):
        try:
            req = urllib.request.Request(base + path, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                out[key] = json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError):
            continue
    return out


def _log_tail_24h(path: str, now: datetime) -> List[str]:
    since = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-20000:]
    except OSError:
        return []
    return [line.rstrip("\n") for line in lines if (m := _STAMP.match(line)) and m.group(1) >= since]


def check_log(log_file: str, now: datetime) -> Check:
    """Journal des 24 dernières heures : erreurs du bot, séparées de ses
    alertes critiques et des coupures d'Internet ou de Binance, et
    avertissements."""
    lines = _log_tail_24h(log_file, now)
    if not lines:
        return chk("Journal des 24 dernières heures", None, "vide ou illisible")
    # Ce qui n'est pas une panne du bot : ses alertes critiques (écrites au
    # journal exprès, [NOTIFY] ; [NOTIFIER-OFF] si Telegram n'est pas
    # configuré) et les coupures d'Internet ou de Binance, qu'il rattrape seul.
    grave = [x for x in lines if "[ERROR]" in x or "[CRITICAL]" in x]
    alerts = [x for x in grave if "[NOTIFY]" in x or "[NOTIFIER-OFF]" in x]
    network = [x for x in grave if x not in alerts and re.search(
        r"NetworkError|RequestTimeout|ExchangeNotAvailable|réseau|injoignable", x)]
    errors = [x for x in grave if x not in alerts and x not in network]
    warns = [x for x in lines if "[WARNING]" in x]
    tags: Dict[str, int] = {}
    for x in warns:
        m = re.search(r"\[WARNING\] (\[[^\]]+\])", x)
        tags[m.group(1) if m else "[?]"] = tags.get(m.group(1) if m else "[?]", 0) + 1
    top = ", ".join(f"{t} × {n}" for t, n in sorted(tags.items(), key=lambda kv: -kv[1])[:3])
    n_alerts = len([x for x in alerts if "[NOTIFY]" in x]) or len(alerts)
    return chk("Journal des 24 dernières heures", not errors,
               f"{len(lines)} lignes, {len(errors)} erreur(s), {len(warns)} avertissement(s)"
               + (f" ({top})" if top else "")
               + (f" ; {len(network)} coupure(s) d'Internet ou de Binance rattrapée(s)" if network else "")
               + (f" ; {n_alerts} alerte(s) critique(s) émise(s)" if alerts else "")
               + (" (Telegram non configuré)" if any("[NOTIFIER-OFF]" in x for x in alerts) else ""),
               "" if not errors else "Erreurs à examiner : " + errors[-1][20:160])


def decision_time(log_file: str, now: datetime) -> Optional[str]:
    """Heure (UTC) de la décision quotidienne d'aujourd'hui, d'après le journal."""
    today = now.strftime("%Y-%m-%d")
    for line in reversed(_log_tail_24h(log_file, now)):
        if "[DAILY]" in line and line.startswith(today):
            return line[11:19]
    return None


def bot_checks(gcfg: Any, st: Dict[str, Any], status: Optional[Dict[str, Any]],
               now: datetime) -> List[Check]:
    """Santé du bot pour le rapport : marche, dernier cycle, disponibilité sur
    7 jours, relance automatique, démarrage avec l'ordinateur (panneau en
    marche), décision du jour, arrêt d'urgence, risque et mode."""
    return (running_checks(status) + [decision_check(gcfg, now), halt_check(gcfg, st),
                                      risk_check(gcfg), mode_check(gcfg)])


def running_checks(status: Optional[Dict[str, Any]]) -> List[Check]:
    """Ce que dit le panneau en marche : bot en marche, dernier cycle,
    disponibilité sur 7 jours, relance automatique, démarrage avec
    l'ordinateur (rien si le panneau ne répond pas)."""
    out: List[Check] = []
    if status:
        running = status.get("state") in ("running", "restarting")
        out.append(chk("Bot en marche", running, {"running": "en marche", "restarting":
                   "relance après une erreur", "stopped": "ARRÊTÉ"}.get(status.get("state"), "?"),
                       "" if running else "Cliquez sur AUTO dans le panneau."))
        age = status.get("last_cycle_age_s")
        out.append(chk("Dernier cycle", age is not None and age < 300,
                       f"il y a {age} s" if age is not None else "aucun",
                       "" if age is not None and age < 300 else "Le bot ne tourne plus : AUTO."))
        u = status.get("uptime") or {}
        week = u.get("week_pct")
        out.append(chk("Disponibilité (7 jours)", None if week is None else week >= 95,
                       "mesure en cours" if week is None else f"{fr(week, '.0f')} % du temps",
                       "" if week is None or week >= 95 else
                       f"Laissez le PC allumé et branché en continu : le bot n'a tourné que "
                       f"{week:.0f} % du temps sur 7 jours."))
        au = status.get("autonomy") or {}
        sup = (au.get("supervisor") or {}).get("running")
        out.append(chk("Relance automatique", bool(sup), "active" if sup else "inactive",
                       "" if sup else "Cliquez sur AUTO dans le panneau."))
        out.append(chk("Démarrage avec l'ordinateur", bool(au.get("autostart")),
                       "activé" if au.get("autostart") else "désactivé",
                       "" if au.get("autostart") else "Réglages ▸ Démarrer avec l'ordinateur."))
    return out


def decision_check(gcfg: Any, now: datetime) -> Check:
    """Décision du jour prise à l'heure (avant 00:10 UTC), d'après le journal."""
    dt_ = decision_time(gcfg.log_file, now)
    late = dt_ is not None and dt_ > "00:10:00"
    return chk("Décision du jour", None if dt_ is None else not late,
               "pas encore dans le journal" if dt_ is None else f"prise à {dt_} UTC"
               + (" (en retard : PC éteint ou en veille à minuit)" if late else ""),
               "" if not late else "Laissez le PC allumé la nuit, sur secteur.")


def halt_check(gcfg: Any, st: Dict[str, Any]) -> Check:
    """Arrêt d'urgence prêt, ou déclenché avec la date de sa reprise."""
    halted = bool(st.get("halted"))
    resume_days = getattr(gcfg, "kill_resume_days", 0)
    return chk("Arrêt d'urgence", not halted,
               f"déclenché : {st.get('halt_reason')}"
               + (f" ; {st['resume_note']}" if st.get("resume_note") else "") if halted
               else f"prêt (−{gcfg.kill_drawdown * 100:.0f} % depuis le plus haut ; "
                    + (f"reprise automatique après {resume_days} jours si le marché redevient "
                       "haussier)" if resume_days > 0 else "levé par la commande resume)"),
               "" if not halted else "Lisez la cause ; pour relancer les achats sans attendre : "
                                     "arrêtez le bot puis python trendguard_bot.py resume.")


def risk_check(gcfg: Any) -> Check:
    """Risque du fichier .env dans les limites sages (1 % par achat, 2 % au
    plus par palier, 6 % cumulé, 8 positions, arrêt à −40 %)."""
    p = gcfg.params
    top = getattr(gcfg, "risk_max_pct", p.risk_pct)
    wise = p.risk_pct <= 0.01 and top <= 0.02 and p.max_total_risk <= 0.06 \
        and p.max_positions <= 8 and gcfg.kill_drawdown <= 0.40
    return chk("Risque configuré", wise,
               f"{fr(p.risk_pct * 100, 'g')} % par achat ({fr(min(top, 2 * p.risk_pct) * 100, 'g')} % "
               f"au plus si l'analyse du bot le justifie), {fr(p.max_total_risk * 100, 'g')} % "
               f"cumulé, {p.max_positions} positions, arrêt d'urgence à "
               f"−{fr(gcfg.kill_drawdown * 100, 'g')} %",
               "" if wise else "Revenez aux limites sages : 1 % par achat (2 % au plus par "
                               "palier), 6 % cumulé, 8 positions, arrêt à −40 % (fichier .env).")


def mode_check(gcfg: Any) -> Check:
    return chk("Mode", None if gcfg.run_mode == "live" else True,
               "RÉEL" if gcfg.run_mode == "live" else "paper : aucun argent réel en jeu")


def disk_state(free_gb: float, total_gb: float) -> Tuple[bool, str]:
    """(trop peu de place, texte) : la même règle et les mêmes mots pour le
    rapport, le panneau et le diagnostic. Trop peu : moins de 2 Go ou de
    10 % libres, en Go comme Windows les affiche."""
    pct = free_gb / total_gb * 100 if total_gb else 100.0
    return (free_gb < DISK_MIN_GB or pct < DISK_MIN_PCT,
            f"{fr(free_gb, '.1f')} Go libres sur {fr(total_gb, '.0f')} ({fr(pct, '.0f')} %)")


def resource_checks(deps: Optional[Deps] = None, root: str = "") -> List[Check]:
    """Place sur le disque et mémoire du PC, pour le rapport et le centre de
    sécurité du panneau : à corriger avant que Windows n'en manque."""
    r = pc_resources(deps, root)
    if not r:
        return []
    low, text = disk_state(r["disk_free"], r["disk_total"])
    out = [chk("Espace disque", not low, text, DISK_RECO if low else "")]
    used, limit = r.get("memory_used"), r.get("memory_limit")
    if used is not None and limit:
        share = used / limit * 100
        full = share >= MEMORY_MAX_PCT
        out.append(chk("Mémoire du PC", not full,
                       f"{fr(share, '.0f')} % réservés aux programmes ({fr(used, '.1f')} Go sur "
                       f"{fr(limit, '.1f')} possibles)",
                       "" if not full else "Fermez des programmes ou des onglets du navigateur : "
                                           "quand la mémoire du PC est pleine, Windows peut "
                                           "arrêter le bot."))
    return out


def code_checks(root: str, deps: Deps, gh: Optional[Callable[[str], Any]]) -> Tuple[List[Check], List[str]]:
    """Code du bot : intègre, d'origine, contrôlé ; propositions en attente."""
    out = []
    changed = _git(deps, root, "status", "--porcelain", "--untracked-files=no")
    if changed is None:
        out.append(chk("Code du bot", None, "vérification impossible (git absent)"))
    else:
        files = [line[3:] for line in changed.splitlines() if line.strip()]
        out.append(chk("Code du bot", not files, "identique à la version publiée" if not files else
                       "modifié localement : " + ", ".join(files[:5]),
                       "" if not files else "Si ce n'est pas une mise à jour en cours, demandez "
                                            "une vérification : fichiers du bot modifiés."))
    slug = repo_slug(deps, root)
    out.append(chk("Origine du code", bool(slug), f"github.com/{slug}" if slug else "inconnue",
                   "" if slug else "Le dépôt n'est plus relié à GitHub : demandez une vérification."))
    head = (_git(deps, root, "rev-parse", "HEAD") or "").strip()
    proposals: List[str] = []
    if gh and slug and head:
        try:
            ok, bad = ci_status(gh, slug, head)
            out.append(chk("Contrôles GitHub de cette version", ok,
                           "en cours ou absents" if ok is None else ("tests, qualité et sécurité au vert"
                                                                     if ok else "en échec : " + ", ".join(bad)),
                           "" if ok is not False else "Demandez une correction : les contrôles "
                                                      "automatiques échouent."))
            pulls = gh(f"https://api.github.com/repos/{slug}/pulls?state=open&per_page=20") or []
            proposals = [f"{p.get('title')} — {p.get('html_url')}" for p in pulls if isinstance(p, dict)]
        except Exception as e:
            out.append(chk("Contrôles GitHub de cette version", None, f"injoignables ({type(e).__name__})"))
    try:
        r = deps.run([sys.executable, "-m", "ruff", "check", root, "--quiet"], cwd=root)
        if r.returncode == 0:
            out.append(chk("Qualité du code (ruff)", True, "aucun problème"))
        elif "No module named" in (r.stderr or ""):
            out.append(chk("Qualité du code (ruff)", None, "ruff non installé"))
        else:
            n = len([x for x in (r.stdout or "").splitlines() if x.strip()])
            out.append(chk("Qualité du code (ruff)", False, f"{n} ligne(s) signalée(s)",
                           "Demandez une correction du code."))
    except (OSError, subprocess.SubprocessError):
        out.append(chk("Qualité du code (ruff)", None, "vérification impossible"))
    return out, proposals


LEVEL_OK = {"OK": True, "INFO": True, "ATTENTION": None, "ALERTE": False}


def strategy_checks(gcfg: Any, deps: Deps) -> List[Check]:
    """Diagnostic expert de la stratégie (le même que `diagnose`)."""
    try:
        if deps.diagnose is not None:
            findings, day = deps.diagnose(gcfg)
        else:
            from .cli import diagnose_findings
            findings, day = diagnose_findings(gcfg)
    except Exception as e:
        return [chk("Diagnostic de la stratégie", None, f"indisponible ({type(e).__name__})")]
    n_ok = sum(1 for f in findings if f.level in ("OK", "INFO"))
    out = [chk("Diagnostic de la stratégie", not any(f.level == "ALERTE" for f in findings),
               f"bougie du {day} : {n_ok} contrôle(s) conforme(s) sur {len(findings)}")]
    for f in findings:
        if f.level in ("ATTENTION", "ALERTE"):
            out.append(chk(f.section, LEVEL_OK[f.level], f.message, f.reco))
    return out


def skills_checks(gcfg: Any, st: Dict[str, Any]) -> List[Check]:
    """Compétences acquises : apprentissage libre et évolution encadrée."""
    lr = learning.summary(st.get("learning"))
    out = [chk("Apprentissage libre", None, lr["text"])]
    if lr["brier_raw"] is not None:
        # Une information, jamais un point à corriger : l'apprentissage se
        # juge sur la durée (au moins 100 prévisions comparées à la clôture).
        enough = lr["forecasts"] >= 100
        better = lr["brier_cal"] <= lr["brier_raw"]
        out.append(chk("Précision des prévisions", True if enough and better else None,
                       f"erreur {fr(lr['brier_raw'], '.3f')} brute, {fr(lr['brier_cal'], '.3f')} "
                       f"corrigée, sur {fr(lr['forecasts'], '.0f')} prévisions"
                       + ("" if enough else " (jugée à partir de 100)")
                       + ("" if not enough or better else " : la correction apprise n'aide pas "
                                                          "encore, elle reste tempérée par le modèle")))
    ev = evolution.summary(gcfg)
    if ev.get("enabled"):
        changes = ", ".join(f"{c['param']} {c['from']} → {c['to']}" for c in ev["changes"]) or "réglages d'origine"
        out.append(chk("Évolution encadrée", None, f"niveau {ev['level']}/{ev['levels']} ({ev['name']}) ; "
                                                   f"{changes}" + (f" ; {ev['last_text']}" if ev.get("last_text") else "")))
        r = ev["risk"]
        out.append(chk("Palier de risque", None, f"{fr(r['pct'], 'g')} % par achat ({fr(r['max_pct'], 'g')} % "
                                                 "au plus, selon l'analyse du bot)"
                       + (f" ; {r['last_text']}" if r.get("last_text") else "")))
    return out
