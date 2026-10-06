"""Rapport quotidien, santé du fond et de la forme (docs/RAPPORT.md) :
panneau, bot, disque et mémoire du PC, Wi-Fi, journal des 24 dernières
heures, stratégie, compétences acquises, code et contrôles GitHub. Lecture
seule.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import (
    attribution,
    audit,
    donnees,
    evolution,
    learning,
    libre,
    postmortem,
    registre,
    risque,
    savoir,
)
from .systeme import (
    Check,
    Deps,
    chk,
    ci_status,
    disk_breakdown,
    pc_resources,
    repo_slug,
    top_programs,
)
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


def _disk_detail(deps: Optional[Deps], root: str, free: float,
                 prev_free: Optional[float]) -> Tuple[str, str]:
    """Disque presque plein, dans le rapport : où part la place (tailles,
    jamais de noms de fichiers), ce qui a été perdu depuis le rapport
    précédent, et le conseil qui va avec."""
    parts = disk_breakdown(deps, root) or {}
    big = sorted(((k, v) for k, v in parts.items() if v >= 0.1), key=lambda kv: -kv[1])
    text = ""
    if prev_free is not None and prev_free - free >= 0.5:
        text += f" ; {fr(prev_free - free, '.1f')} Go de moins qu'au rapport précédent"
    if big:
        text += " ; où part la place : " + ", ".join(f"{k} {fr(v, '.1f')} Go" for k, v in big)
    advice = []
    if parts.get("Téléchargements", 0) >= 2:
        advice.append(f"triez le dossier Téléchargements ({fr(parts['Téléchargements'], '.0f')} Go, "
                      "sans toucher au dossier du bot)")
    if parts.get("Fichiers temporaires", 0) >= 1 or parts.get("Corbeille", 0) >= 1:
        advice.append("videz la corbeille et les fichiers temporaires (Paramètres ▸ Système ▸ Stockage)")
    if parts.get("Fichier d'échange de Windows", 0) >= 6:
        advice.append("fermez des programmes puis redémarrez le PC : le fichier d'échange rétrécit au "
                      "redémarrage")
    return text, (DISK_RECO + " Ici : " + " ; ".join(advice) + ".") if advice else DISK_RECO


# Wi-Fi des 26 dernières heures (journal de Windows) : heure UTC, connexion
# (8001) ou déconnexion (8003), réseau, raison (2 et 3 : demandées par
# l'utilisateur ; les autres sont des coupures).
WIFI_SCRIPT = (
    "Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-WLAN-AutoConfig/Operational';"
    "Id=8001,8003; StartTime=(Get-Date).AddHours(-26)} -ErrorAction SilentlyContinue | "
    "ForEach-Object { $d = ([xml]$_.ToXml()).Event.EventData.Data; '{0}|{1}|{2}|{3}' -f "
    "$_.TimeCreated.ToUniversalTime().ToString('o'), $_.Id, ($d | Where-Object Name -eq 'SSID').'#text', "
    "($d | Where-Object Name -eq 'ReasonCode').'#text' }")
WIFI_USER_REASONS = ("2", "3")
WIFI_DROPS_MAX = 3              # coupures en 24 h au-delà desquelles un réseau est à éviter
# Marques de téléphones : un réseau à leur nom est sans doute un partage de connexion.
PHONE_HINTS = re.compile(r"\b(oppo|redmi|xiaomi|poco|tecno|infinix|itel|galaxy|samsung|iphone|honor|"
                         r"huawei|pixel|nokia|vivo|realme|oneplus|moto|androidap)\b", re.I)


# Plantages de Windows des 30 derniers jours (journal Système, Kernel-Power
# 41 : redémarrage sans arrêt propre) : heure UTC, code de l'écran bleu (0 :
# arrêt brutal sans écran bleu), premier paramètre, bouton d'alimentation.
CRASH_SCRIPT = (
    "Get-WinEvent -FilterHashtable @{LogName='System'; Id=41; StartTime=(Get-Date).AddDays(-30)} "
    "-ErrorAction SilentlyContinue | ForEach-Object { $d = ([xml]$_.ToXml()).Event.EventData.Data; "
    "'{0}|{1}|{2}|{3}' -f $_.TimeCreated.ToUniversalTime().ToString('o'), "
    "($d | Where-Object Name -eq 'BugcheckCode').'#text', "
    "($d | Where-Object Name -eq 'BugcheckParameter1').'#text', "
    "($d | Where-Object Name -eq 'PowerButtonTimestamp').'#text' }")
CRASHES_MAX_WEEK = 2            # plantages en 7 jours à partir desquels c'est à corriger
# Les écrans bleus les plus courants, en clair.
BUGCHECKS = {
    0x0A: "pilote ou mémoire (IRQL_NOT_LESS_OR_EQUAL)", 0x1A: "gestion de la mémoire (MEMORY_MANAGEMENT)",
    0x3B: "erreur d'un service du système, souvent un pilote (SYSTEM_SERVICE_EXCEPTION)",
    0x50: "mémoire ou pilote (PAGE_FAULT_IN_NONPAGED_AREA)",
    0x7A: "lecture du disque ratée (KERNEL_DATA_INPAGE_ERROR)",
    0x7E: "pilote défaillant (SYSTEM_THREAD_EXCEPTION_NOT_HANDLED)",
    0x9F: "pilote bloqué à la mise en veille ou au réveil (DRIVER_POWER_STATE_FAILURE)",
    0xD1: "pilote défaillant (DRIVER_IRQL_NOT_LESS_OR_EQUAL)",
    0xEF: "processus vital de Windows arrêté (CRITICAL_PROCESS_DIED)",
    0x124: "erreur matérielle (WHEA_UNCORRECTABLE_ERROR)",
    0x139: "contrôle de sécurité du noyau (KERNEL_SECURITY_CHECK_FAILURE)"}
BUGCHECK_DETAILS = {(0x1A, 0x3F): "une page relue depuis le fichier d'échange ne correspondait plus à ce qui "
                                  "avait été écrit : mémoire, disque ou pilote"}
CRASH_RECO = ("Lancez le diagnostic de la mémoire de Windows (touche Windows, tapez mdsched), installez les "
              "mises à jour de Windows et des pilotes (disque, Intel Optane/RST), et vérifiez le disque "
              "(chkdsk) ; si cela se répète, faites contrôler le PC.")


def crash_events(deps: Deps) -> Optional[List[Tuple[datetime, int, int, bool]]]:
    """(heure UTC, code de l'écran bleu, premier paramètre, bouton
    d'alimentation maintenu) des plantages de Windows sur 30 jours ; None
    ailleurs que sous Windows ou si le journal est illisible."""
    if not deps.platform.startswith("win"):
        return None
    try:
        r = deps.run(["powershell", "-NoProfile", "-Command", CRASH_SCRIPT], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out = []
    for line in (r.stdout or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) != 4:
            continue
        try:
            t = datetime.fromisoformat(parts[0].replace("Z", "+00:00")).astimezone(timezone.utc)
            code, param = int(parts[1] or "0", 0), int(parts[2] or "0", 0)   # décimal ou 0x…
        except ValueError:
            continue
        out.append((t, code, param, (parts[3] or "0") not in ("0", "")))
    return sorted(out)


def crash_text(code: int, param: int, button: bool) -> str:
    """Un plantage, en clair."""
    if code == 0:
        return ("arrêt forcé au bouton d'alimentation" if button
                else "arrêt brutal sans écran bleu (coupure de courant, batterie vide ou PC figé)")
    what = BUGCHECK_DETAILS.get((code, param)) or BUGCHECKS.get(code, "cause à rechercher")
    return f"écran bleu 0x{code:X} : {what}"


def crash_check(deps: Deps, now: datetime) -> List[Check]:
    """Plantages de Windows (écran bleu, arrêt brutal) sur 7 et 30 jours :
    une information au premier, à corriger dès 2 en 7 jours. Rien ailleurs
    que sous Windows."""
    events = crash_events(deps)
    if events is None:
        return []
    month = [e for e in events if e[0] >= now - timedelta(days=30)]
    week = [e for e in month if e[0] >= now - timedelta(days=7)]
    if not month:
        return [chk("Plantages de Windows", True, "aucun en 30 jours")]
    listed = " ; ".join(f"{t:%d/%m %H:%M} UTC, {crash_text(c, p, b)}" for t, c, p, b in month[-3:])
    bad = len(week) >= CRASHES_MAX_WEEK
    return [chk("Plantages de Windows", False if bad else None,
                f"{len(week)} en 7 jours, {len(month)} en 30 jours : {listed}", CRASH_RECO)]


def wifi_events(deps: Deps) -> List[Tuple[datetime, str, str, str]]:
    """(heure UTC, 8001 ou 8003, réseau, raison) du journal Wi-Fi de Windows,
    dans l'ordre ; vide ailleurs ou s'il est illisible."""
    if not deps.platform.startswith("win"):
        return []
    try:
        r = deps.run(["powershell", "-NoProfile", "-Command", WIFI_SCRIPT], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in (r.stdout or "").splitlines() if r.returncode == 0 else []:
        parts = line.strip().split("|")
        if len(parts) != 4 or parts[1] not in ("8001", "8003") or not parts[2]:
            continue
        try:
            t = datetime.fromisoformat(parts[0].replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
        out.append((t, parts[1], parts[2], parts[3]))
    return sorted(out)


def wifi_check(deps: Deps, now: datetime) -> List[Check]:
    """Wi-Fi des dernières 24 heures : coupures par réseau, réseau à
    préférer, et réseau en service à la décision de 00:02 (partage de
    connexion d'un téléphone ?). Les noms des réseaux restent dans le
    rapport (ce PC, l'e-mail du propriétaire), jamais dans les fichiers
    publiés."""
    events = wifi_events(deps)
    if not events:
        return []
    since = now - timedelta(hours=24)
    decision = now.replace(hour=0, minute=2, second=0, microsecond=0)
    drops: Dict[str, int] = {}
    current = at_decision = None
    for t, kind, ssid, reason in events:
        if t <= decision:
            at_decision = ssid if kind == "8001" else (None if at_decision == ssid else at_decision)
        if kind == "8001":
            current = ssid
            drops.setdefault(ssid, 0)
            continue
        if t >= since and reason not in WIFI_USER_REASONS:
            drops[ssid] = drops.get(ssid, 0) + 1
        if current == ssid:
            current = None
    worst = max(drops, key=lambda k: drops[k])
    # Réseau à préférer : le plus stable, hors partage de connexion d'un
    # téléphone quand il y a mieux ; nettement plus stable (4 fois moins de
    # coupures) pour valoir un conseil.
    fixed = [k for k in drops if not PHONE_HINTS.search(k)] or list(drops)
    best = min(fixed, key=lambda k: drops[k])
    bad = drops[worst] >= WIFI_DROPS_MAX and worst != best and drops[best] * 4 <= drops[worst]
    parts = [f"réseau actuel : {current}" if current else "aucun réseau Wi-Fi en ce moment",
             "coupures en 24 h : " + ", ".join(f"{k} {n}" for k, n in sorted(drops.items(), key=lambda kv: -kv[1]))]
    if at_decision:
        phone = " (sans doute le partage de connexion d'un téléphone)" if PHONE_HINTS.search(at_decision) else ""
        parts.append(f"à la décision de 00:02 : {at_decision}{phone}")
    reco = (f"Préférez le réseau « {best} » : dans Paramètres ▸ Réseau et Internet ▸ Wi-Fi, décochez "
            f"« Se connecter automatiquement » pour « {worst} » ({drops[worst]} coupures en 24 h)."
            if bad else "")
    return [chk("Wi-Fi (24 h)", False if bad else None, " ; ".join(parts), reco)]


def resource_checks(deps: Optional[Deps] = None, root: str = "", detail: bool = False,
                    prev_free: Optional[float] = None,
                    r: Optional[Dict[str, Any]] = None) -> List[Check]:
    """Place sur le disque et mémoire du PC, pour le rapport et le centre de
    sécurité du panneau : à corriger avant que Windows n'en manque. Avec
    `detail` (rapport quotidien) : où part la place, ce qui a été perdu
    depuis le rapport précédent (`prev_free`, en Go), et les programmes qui
    prennent le plus de mémoire."""
    r = r if r is not None else pc_resources(deps, root)
    if not r:
        return []
    low, text = disk_state(r["disk_free"], r["disk_total"])
    reco = DISK_RECO if low else ""
    if low and detail:
        more, reco = _disk_detail(deps, root, r["disk_free"], prev_free)
        text += more
    out = [chk("Espace disque", not low, text, reco)]
    used, limit = r.get("memory_used"), r.get("memory_limit")
    if used is not None and limit:
        share = used / limit * 100
        full = share >= MEMORY_MAX_PCT
        text = (f"{fr(share, '.0f')} % réservés aux programmes ({fr(used, '.1f')} Go sur "
                f"{fr(limit, '.1f')} possibles)")
        reco = "" if not full else ("Fermez des programmes ou des onglets du navigateur : quand la "
                                    "mémoire du PC est pleine, Windows peut arrêter le bot.")
        progs = top_programs(deps) if full and detail else None
        if progs:
            text += " ; plus gros programmes : " + ", ".join(
                f"{name} {fr(gb, '.1f')} Go ({n} processus)" for name, n, gb in progs)
            reco += f" Le plus gourmand : {progs[0][0]} ({fr(progs[0][2], '.1f')} Go)."
        out.append(chk("Mémoire du PC", not full, text, reco))
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


def knowledge_check(gcfg: Any) -> List[Check]:
    """Noyau de savoir : ce qu'il a lu, les sources prouvées, ses reports
    d'achat ; une information, jamais un point à corriger."""
    path = getattr(gcfg, "savoir_db", "") or ""
    if not getattr(gcfg, "savoir", False) or not path or not os.path.exists(path):
        return []
    memory = savoir.Memory(path, readonly=True)
    try:
        s = savoir.summary(memory, datetime.now(timezone.utc).date().isoformat())
    finally:
        memory.close()
    errors = (s.get("last_run") or {}).get("errors") or {}
    out = [chk("Noyau de savoir", None, s["text"] + (f" ; sources en panne à la dernière lecture : "
                                                     f"{', '.join(errors)}" if errors else ""))]
    memory = savoir.Memory(path, readonly=True)
    try:
        free = libre.summary(memory.get("libre"))
    finally:
        memory.close()
    if free:
        out.append(chk("Bot libre", None, free["text"]))
    return out


def analysis_checks(gcfg: Any, st: Dict[str, Any]) -> List[Check]:
    """Risque et analyse du portefeuille, d'après la dernière décision :
    qualité des données, VaR, tests de résistance, attribution des
    résultats, calendrier économique, registre des expériences. Des
    informations, jamais des points à corriger."""
    out: List[Check] = []
    q = st.get("qualite") or {}
    if q.get("text"):
        out.append(chk("Qualité des données", True if q.get("score", 0) >= 90 else None,
                       f"bougie du {q.get('day')} : {q['text']}"))
    rj = st.get("risque_jour") or {}
    if rj.get("var_pct") is not None:
        out.append(chk("Risque d'un jour (VaR)", None, risque.describe(rj)))
    sr = st.get("stress") or {}
    if sr.get("text"):
        out.append(chk("Tests de résistance", None, sr["text"]))
    out.append(chk("Attribution des résultats", None,
                   attribution.attribution(st.get("trades") or [], {})["text"]))
    ev = st.get("evenements") or {}
    if ev:
        nxt = ev.get("upcoming") or []
        out.append(chk("Calendrier économique", None,
                       (f"{len(nxt)} annonce(s) américaine(s) importante(s) dans les 48 heures"
                        if nxt else "aucune annonce américaine importante dans les 48 heures")
                       + f" ; {(ev.get('reaction') or {}).get('text', '')}"
                       + (f" ; dernière lecture ratée : {ev['error']}" if ev.get("error") else "")))
    pt = st.get("porte") or {}
    if pt.get("day"):
        out.append(chk("Porte d'exécution", None,
                       f"décision du {pt['day']} : {pt.get('approved', 0)} achat(s) autorisé(s), "
                       f"{pt.get('refused', 0)} refusé(s)" + (" ; " + " ; ".join(pt.get("reasons") or [])
                                                               if pt.get("refused") else "")))
    ms = st.get("mode_sur") or {}
    if ms.get("active"):
        out.append(chk("Mode sûr", None, f"actif depuis le {str(ms.get('since'))[:16].replace('T', ' à ')} "
                                         f"({ms.get('reason') or 'demandé'}) : aucun achat",
                       "Pour reprendre les achats : python trendguard_bot.py mode-sur off"))
    v = audit.verify(audit.path_for(gcfg))
    if v["events"] or not v["ok"]:
        out.append(chk("Journal d'audit", v["ok"], audit.describe(v),
                       "" if v["ok"] else "Le journal d'audit a été modifié à la main ou abîmé : ne le corrigez pas, "
                                          "gardez-le tel quel et signalez-le (le bot continue d'écrire à la suite)."))
    db = donnees.path_for(gcfg)
    if db != ":memory:" and os.path.exists(db):
        try:
            j = donnees.Journal(db, readonly=True)
            try:
                v = j.verify()
                cr = j.committee_record() if v["schema"] >= 2 else {"views": 0, "by": {}}
            finally:
                j.close()
            if cr["views"]:
                by = " ; ".join(f"« {r.lower().replace('_', ' ')} » : {x['trades']} trade(s), "
                                f"{fr(x['avg_r'], '+.2f')} R en moyenne" for r, x in sorted(cr["by"].items()))
                out.append(chk("Comité d'agents", None, f"{cr['views']} avis consultatif(s) gardé(s)"
                               + (f" ; trades achetés ensuite selon l'avis : {by}" if by else
                                  " ; aucun trade clos à comparer pour l'instant")
                               + " (jugé à partir de 20 trades par avis)"))
            out.append(chk("Journal financier", v["ok"], donnees.describe(v),
                           "" if v["ok"] else "Le journal financier a un défaut : python trendguard_bot.py "
                                              "donnees, puis signalez-le (rien n'est corrigé en silence)."))
        except Exception as e:           # un bilan illisible n'empêche pas le rapport
            out.append(chk("Journal financier", None, f"illisible ({type(e).__name__})"))
    entries = registre.load(registre.registry_path(gcfg))
    if entries:
        last = entries[-1]
        out.append(chk("Registre des expériences", None,
                       f"{len(entries)} expérience(s) notée(s) ; dernière : {last.get('id')} du "
                       f"{str(last.get('at', ''))[:10]} ({last.get('kind')})"))
    return out


def skills_checks(gcfg: Any, st: Dict[str, Any]) -> List[Check]:
    """Compétences acquises : apprentissage libre, noyau de savoir et
    évolution encadrée."""
    lr = learning.summary(st.get("learning"))
    out = [chk("Apprentissage libre", None, lr["text"]),
           chk("Journal des trades", None, postmortem.summary(st.get("trades") or [])["text"])]
    try:
        out += knowledge_check(gcfg)
    except Exception as e:           # un bilan illisible n'empêche pas le rapport
        out.append(chk("Noyau de savoir", None, f"bilan illisible : {e}"))
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
