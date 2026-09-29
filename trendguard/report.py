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
  aux règles, au risque, aux clés, au mode réel, au code ni aux réglages de
  Windows.

Le rapport est gardé (panneau ▸ Réglages ▸ Rapport quotidien), puis envoyé
par e-mail (complet) et par WhatsApp (résumé). Aucun secret n'y figure :
seulement leur présence et leur état.

  python trendguard_bot.py rapport              # dernier rapport
  python trendguard_bot.py rapport maintenant   # analyse, rapport et envoi
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import v29

from . import autonomy, evolution, learning

REPORT_MINUTE = 30              # 00:30 UTC
KEEP_BACKUPS = 14
KEEP_REPORTS = 30
LOCK_STALE_SEC = 1800
SECRET_SUFFIXES = ("_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_APIKEY")
SECRET_MIN_LEN = 8
BROAD_SIDS = {"S-1-1-0": "Tout le monde", "S-1-5-32-545": "Utilisateurs",
              "S-1-5-11": "Utilisateurs authentifiés"}
SHORT_MAX = 900                 # résumé WhatsApp
LOG_GLOBS = ("*.log", "*.log.*", "*.console.txt", "*.blocage.txt")
_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

Check = Dict[str, Any]


def chk(label: str, ok: Optional[bool], detail: str, reco: str = "", action: str = "") -> Check:
    """Un constat : ok = True (conforme), False (à corriger), None (information)."""
    return {"label": label, "ok": ok, "detail": detail, "reco": reco, "action": action}


def _run(cmd: List[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
         timeout: int = 60) -> subprocess.CompletedProcess:
    kw: Dict[str, Any] = {"cwd": cwd, "env": env, "capture_output": True, "text": True,
                          "timeout": timeout, "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kw["creationflags"] = autonomy.CREATE_NO_WINDOW
    return subprocess.run(cmd, **kw)


@dataclass
class Deps:
    """Accès au monde extérieur, remplaçables dans les tests."""
    run: Callable[..., Any] = _run
    http_json: Optional[Callable[[str, Dict[str, str]], Any]] = None
    panel: Optional[Callable[[int, str], Dict[str, Any]]] = None
    binance: Optional[Callable[[Dict[str, str], bool], Check]] = None
    diagnose: Optional[Callable[[Any], Tuple[List[Any], str]]] = None
    platform: str = sys.platform
    now: Optional[datetime] = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════
# Fichiers
# ══════════════════════════════════════════════════════════════════════

def paths(gcfg: Any) -> Dict[str, str]:
    base = autonomy.sidecar(gcfg.lock_file, "")
    root = os.path.dirname(base) if base else v29.APP_DIR
    return {"json": base + ".rapport.json" if base else "",
            "lock": base + ".rapport.lock" if base else "",
            "log": base + ".rapport.log" if base else os.devnull,
            "archive": os.path.join(root, "rapports"), "backups": os.path.join(root, "sauvegardes"),
            "root": v29.APP_DIR}


def load_latest(gcfg: Any) -> Optional[Dict[str, Any]]:
    path = paths(gcfg)["json"]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def is_running(gcfg: Any) -> bool:
    lock = paths(gcfg)["lock"]
    try:
        return bool(lock) and time.time() - os.path.getmtime(lock) < LOCK_STALE_SEC
    except OSError:
        return False


def _save_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_state(db_file: str) -> Dict[str, Any]:
    """État du bot, en lecture seule (le bot garde la main sur sa base)."""
    if not db_file or db_file == ":memory:" or not os.path.exists(db_file):
        return {}
    uri = pathlib.Path(os.path.abspath(db_file)).as_uri() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error:
        return {}
    try:
        row = con.execute("SELECT value FROM kv WHERE key=?", ("trendguard",)).fetchone()
        return json.loads(row[0]) if row else {}
    except (sqlite3.Error, ValueError):
        return {}
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════
# Sécurité : constats et protections appliquées seules
# ══════════════════════════════════════════════════════════════════════

def _git(deps: Deps, root: str, *args: str) -> Optional[str]:
    try:
        r = deps.run(["git", *args], cwd=root)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def check_env_published(root: str, deps: Deps) -> Check:
    """Le fichier des secrets n'est ni suivi ni publié sur GitHub."""
    if not os.path.exists(os.path.join(root, ".env")):
        return chk("Secrets hors de GitHub", None, "pas de fichier .env : aucune clé enregistrée")
    try:
        ignored = ".env" in open(os.path.join(root, ".gitignore"), encoding="utf-8").read().split()
    except OSError:
        ignored = False
    tracked = _git(deps, root, "ls-files", "--", ".env")
    history = _git(deps, root, "log", "--all", "--format=%h", "--", ".env")
    if tracked is None or history is None:
        return chk("Secrets hors de GitHub", None, "vérification impossible (git absent)")
    if tracked.strip() or history.strip():
        return chk("Secrets hors de GitHub", False, "le fichier .env a été PUBLIÉ sur GitHub",
                   "Supprimez toutes les clés concernées chez Binance et les autres services, "
                   "puis créez-en de nouvelles.")
    if not ignored:
        return chk("Secrets hors de GitHub", False, "le fichier .env n'est pas exclu de GitHub",
                   "Ajoutez .env au fichier .gitignore.")
    return chk("Secrets hors de GitHub", True, "fichier .env exclu de GitHub, jamais publié")


def _win_acl(path: str, deps: Deps) -> Optional[List[Tuple[str, str]]]:
    """(SID, Allow/Deny) de chaque droit du fichier, via PowerShell."""
    ps = ("$a=(Get-Acl -LiteralPath $env:TG_ACL_PATH).Access; foreach($e in $a){ try{"
          "$s=$e.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value}"
          "catch{$s=[string]$e.IdentityReference}; Write-Output ($s + '|' + $e.AccessControlType) }")
    try:
        r = deps.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                     env=dict(os.environ, TG_ACL_PATH=path))
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        if "|" in line:
            sid, kind = line.strip().split("|", 1)
            out.append((sid, kind))
    return out


def check_env_permissions(root: str, deps: Deps) -> Check:
    """Droits du fichier des secrets : réservés à son propriétaire. Resserrés
    seuls s'ils sont trop larges (sûr et réversible)."""
    path = os.path.join(root, ".env")
    label = "Droits du fichier des secrets"
    if not os.path.exists(path):
        return chk(label, None, "pas de fichier .env")
    if not deps.platform.startswith("win"):
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if not mode & 0o077:
            return chk(label, True, "lisible par votre seul compte (600)")
        os.chmod(path, 0o600)
        return chk(label, True, "lisible par votre seul compte (600)",
                   action=f"droits du fichier .env resserrés ({oct(mode)[2:]} → 600)")
    acl = _win_acl(path, deps)
    if acl is None:
        return chk(label, None, "vérification impossible (PowerShell indisponible)")
    broad = sorted({sid for sid, kind in acl if sid in BROAD_SIDS and kind == "Allow"})
    if not broad:
        return chk(label, True, "réservé à votre compte, à Windows et aux administrateurs")
    names = ", ".join(BROAD_SIDS[s] for s in broad)
    try:
        deps.run(["icacls", path, "/inheritance:d"])
        deps.run(["icacls", path, "/remove:g", *[f"*{s}" for s in broad]])
    except (OSError, subprocess.SubprocessError):
        pass
    after = _win_acl(path, deps) or []
    still = [s for s, kind in after if s in BROAD_SIDS and kind == "Allow"]
    if still:
        return chk(label, False, f"lisible par : {names}",
                   "Réservez le fichier .env à votre compte (clic droit ▸ Propriétés ▸ Sécurité).")
    return chk(label, True, "réservé à votre compte, à Windows et aux administrateurs",
               action=f"accès au fichier .env retiré à : {names}")


def secret_values(env: Dict[str, str]) -> List[Tuple[str, str]]:
    return [(k, v.strip()) for k, v in env.items()
            if k.upper().endswith(SECRET_SUFFIXES) and len((v or "").strip()) >= SECRET_MIN_LEN]


def check_secret_leaks(root: str, env: Dict[str, str], deps: Deps) -> List[Check]:
    """Aucune clé ni aucun mot de passe dans les fichiers publiés sur GitHub
    ni dans les journaux. Un secret trouvé dans un journal est masqué seul
    (même longueur : le bot peut continuer d'y écrire)."""
    secrets = secret_values(env)
    if not secrets:
        return [chk("Secrets dans les fichiers", None, "aucun secret enregistré à rechercher")]
    listed = _git(deps, root, "ls-files")
    leaks = set()
    for rel in (listed or "").splitlines():
        p = os.path.join(root, rel)
        try:
            if os.path.getsize(p) > 3_000_000:
                continue
            with open(p, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        for name, val in secrets:
            if val.encode() in data:
                leaks.add((name, rel))
    cleaned = []
    for pattern in LOG_GLOBS:
        for p in glob.glob(os.path.join(root, pattern)):
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            new = data
            for _name, val in secrets:
                b = val.encode()
                new = new.replace(b, b"*" * len(b))
            if new != data:
                try:
                    with open(p, "r+b") as fh:
                        fh.write(new)
                    cleaned.append(os.path.basename(p))
                except OSError:
                    continue
    repo = (chk("Secrets dans le dépôt GitHub", None, "vérification impossible (git absent)")
            if listed is None else
            chk("Secrets dans le dépôt GitHub", not leaks,
                "aucune clé ni aucun mot de passe dans les fichiers publiés" if not leaks else
                "SECRET PUBLIÉ : " + ", ".join(f"{n} dans {f}" for n, f in sorted(leaks)),
                "" if not leaks else "Supprimez ces clés chez leur fournisseur et créez-en de "
                                     "nouvelles : elles sont lisibles sur GitHub."))
    logs = chk("Secrets dans les journaux", True,
               "aucun secret dans les journaux" if not cleaned else "masqués (voir les actions)",
               action="secret masqué dans : " + ", ".join(cleaned) if cleaned else "")
    return [repo, logs]


def backup_database(db_file: str, dest: str, day: str, keep: int = KEEP_BACKUPS) -> Check:
    """Sauvegarde du jour de la base du bot (et de ses réglages), vérifiée,
    en gardant les 14 dernières."""
    label = "Sauvegarde de la base"
    if not db_file or db_file == ":memory:" or not os.path.exists(db_file):
        return chk(label, None, "aucune base à sauvegarder")
    os.makedirs(dest, exist_ok=True)
    stem = os.path.splitext(os.path.basename(db_file))[0]
    target = os.path.join(dest, f"{stem}-{day}.db")
    src = sqlite3.connect(pathlib.Path(os.path.abspath(db_file)).as_uri() + "?mode=ro", uri=True,
                          timeout=10)
    out = sqlite3.connect(target)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    con = sqlite3.connect(target)
    try:
        ok = con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        con.close()
    base = os.path.splitext(db_file)[0]
    for ext in (".selection.json", ".evolution.json"):
        if os.path.exists(base + ext):
            shutil.copy2(base + ext, os.path.join(dest, f"{stem}-{day}{ext}"))
    kept = sorted(glob.glob(os.path.join(dest, f"{stem}-????-??-??.db")))
    for old in kept[:-keep]:
        for p in glob.glob(old[:-3] + "*"):
            try:
                os.remove(p)
            except OSError:
                pass
    size = os.path.getsize(target) // 1024
    return chk(label, ok, f"faite ({size} Ko, intégrité vérifiée), {min(len(kept), keep)} jour(s) gardé(s)"
               if ok else "sauvegarde abîmée : celle d'hier est gardée",
               "" if ok else "Vérifiez l'espace disque et relancez le rapport.",
               action=f"base sauvegardée ({os.path.basename(target)})" if ok else "")


def check_database(db_file: str) -> Check:
    label = "Intégrité de la base"
    if not db_file or db_file == ":memory:" or not os.path.exists(db_file):
        return chk(label, None, "aucune base")
    try:
        con = sqlite3.connect(pathlib.Path(os.path.abspath(db_file)).as_uri() + "?mode=ro",
                              uri=True, timeout=10)
        try:
            res = con.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as e:
        return chk(label, None, f"vérification impossible ({type(e).__name__})")
    return chk(label, res == "ok", "base saine" if res == "ok" else f"base abîmée : {res[:120]}",
               "" if res == "ok" else "Arrêtez le bot et restaurez la dernière sauvegarde "
                                      "(dossier sauvegardes).")


def binance_key_check(env: Dict[str, str], testnet: bool = False) -> Check:
    """Droits de la clé Binance, lus sans aucun ordre."""
    import ccxt
    label = "Clé API Binance"
    key, sec = env.get("BINANCE_API_KEY", "").strip(), env.get("BINANCE_API_SECRET", "").strip()
    if not key or not sec:
        return chk(label, None, "absente (normal en paper)")
    try:
        r = v29.make_binance(key, sec, testnet).sapi_get_account_apirestrictions()
    except ccxt.AuthenticationError:
        return chk(label, False, "refusée par Binance (adresse IP non autorisée, clé supprimée "
                                 "ou mal copiée)",
                   "Binance ▸ Gestion des API : vérifiez la restriction IP, puis "
                   "python trendguard_bot.py set-keys.")
    except Exception as e:
        return chk(label, None, f"vérification impossible ({type(e).__name__})")
    withdraw, ip = bool(r.get("enableWithdrawals")), bool(r.get("ipRestrict"))
    trading = bool(r.get("enableSpotAndMarginTrading"))
    detail = (f"retrait {'AUTORISÉ' if withdraw else 'interdit'} ; trading Spot "
              f"{'autorisé' if trading else 'non autorisé'} ; restriction IP {'oui' if ip else 'non'}")
    if withdraw:
        return chk(label, False, detail, "Désactivez tout de suite le droit de retrait de la clé "
                                         "sur Binance.")
    return chk(label, True, detail, "" if ip else "Ajoutez une restriction IP à la clé sur Binance.")


def _ps_lines(deps: Deps, script: str) -> Optional[List[str]]:
    try:
        r = deps.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.splitlines() if r.returncode == 0 else None


def check_windows(deps: Deps) -> List[Check]:
    """Pare-feu, antivirus, mise en veille et capot (lecture seule : les
    changer demande votre accord)."""
    if not deps.platform.startswith("win"):
        return []
    out = []
    fw = _ps_lines(deps, "Get-NetFirewallProfile | ForEach-Object { $_.Name + '|' + $_.Enabled }")
    if fw is None:
        out.append(chk("Pare-feu Windows", None, "état inconnu"))
    else:
        off = [line.split("|")[0] for line in fw if line.strip().endswith("|False")]
        out.append(chk("Pare-feu Windows", not off, "actif sur tous les réseaux" if not off else
                       "désactivé sur : " + ", ".join(off),
                       "" if not off else "Réactivez le pare-feu (Sécurité Windows ▸ Pare-feu)."))
    av = _ps_lines(deps, "$s=Get-MpComputerStatus; [string]$s.RealTimeProtectionEnabled + '|' + "
                         "[string]$s.AntivirusSignatureAge")
    if not av or "|" not in av[0]:
        out.append(chk("Antivirus", None, "état de Microsoft Defender inconnu (autre antivirus ?)"))
    else:
        rt, age = av[0].split("|", 1)
        age_d = int(age) if age.strip().isdigit() else None
        good = rt.strip().lower() == "true" and (age_d is None or age_d <= 7)
        out.append(chk("Antivirus", good,
                       f"Microsoft Defender {'actif' if rt.strip().lower() == 'true' else 'INACTIF'}"
                       + (f", signatures de {age_d} jour(s)" if age_d is not None else ""),
                       "" if good else "Activez la protection en temps réel et mettez à jour "
                                       "Microsoft Defender."))
    out.append(_power_check(deps))
    return out


def _power_ac(deps: Deps, sub: str, setting: str) -> Optional[int]:
    try:
        r = deps.run(["powercfg", "/query", "SCHEME_CURRENT", sub, setting])
    except (OSError, subprocess.SubprocessError):
        return None
    vals = re.findall(r"0x([0-9a-fA-F]{8})", r.stdout or "")
    return int(vals[-2], 16) if r.returncode == 0 and len(vals) >= 2 else None


def _power_check(deps: Deps) -> Check:
    sleep = _power_ac(deps, "SUB_SLEEP", "STANDBYIDLE")
    lid = _power_ac(deps, "SUB_BUTTONS", "LIDACTION")
    if sleep is None and lid is None:
        return chk("Veille du PC (sur secteur)", None, "réglages inconnus")
    good = sleep == 0 and lid in (0, None)
    detail = ("mise en veille : " + ("jamais" if sleep == 0 else f"après {sleep // 60} min"
                                     if sleep is not None else "?")
              + " ; capot fermé : " + ({0: "ne rien faire", 1: "veille", 2: "veille prolongée",
                                        3: "arrêt"}.get(lid, "?") if lid is not None else "?"))
    return chk("Veille du PC (sur secteur)", good, detail,
               "" if good else "Paramètres Windows ▸ Alimentation : sur secteur, mise en veille "
                               "« Jamais » et capot fermé « Ne rien faire » (le bot ne surveille "
                               "rien quand le PC dort).")


# ══════════════════════════════════════════════════════════════════════
# Panneau, bot, code : santé du fond et de la forme
# ══════════════════════════════════════════════════════════════════════

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
    lines = _log_tail_24h(log_file, now)
    if not lines:
        return chk("Journal des 24 dernières heures", None, "vide ou illisible")
    errors = [x for x in lines if "[ERROR]" in x or "[CRITICAL]" in x]
    warns = [x for x in lines if "[WARNING]" in x]
    tags: Dict[str, int] = {}
    for x in warns:
        m = re.search(r"\[WARNING\] (\[[^\]]+\])", x)
        tags[m.group(1) if m else "[?]"] = tags.get(m.group(1) if m else "[?]", 0) + 1
    top = ", ".join(f"{t} × {n}" for t, n in sorted(tags.items(), key=lambda kv: -kv[1])[:3])
    return chk("Journal des 24 dernières heures", not errors,
               f"{len(lines)} lignes, {len(errors)} erreur(s), {len(warns)} avertissement(s)"
               + (f" ({top})" if top else ""),
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
    p = gcfg.params
    out = []
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
                       "mesure en cours" if week is None else f"{week:.0f} % du temps".replace(".", ","),
                       "" if week is None or week >= 95 else "PC branché, sans mise en veille."))
        au = status.get("autonomy") or {}
        sup = (au.get("supervisor") or {}).get("running")
        out.append(chk("Relance automatique", bool(sup), "active" if sup else "inactive",
                       "" if sup else "Cliquez sur AUTO dans le panneau."))
        out.append(chk("Démarrage avec l'ordinateur", bool(au.get("autostart")),
                       "activé" if au.get("autostart") else "désactivé",
                       "" if au.get("autostart") else "Réglages ▸ Démarrer avec l'ordinateur."))
    dt_ = decision_time(gcfg.log_file, now)
    late = dt_ is not None and dt_ > "00:10:00"
    out.append(chk("Décision du jour", None if dt_ is None else not late,
                   "pas encore dans le journal" if dt_ is None else f"prise à {dt_} UTC"
                   + (" (en retard : PC éteint ou en veille à minuit)" if late else ""),
                   "" if not late else "Laissez le PC allumé la nuit, sur secteur."))
    halted = bool(st.get("halted"))
    out.append(chk("Arrêt d'urgence", not halted, f"déclenché : {st.get('halt_reason')}" if halted
                   else f"prêt (−{gcfg.kill_drawdown * 100:.0f} % depuis le plus haut)",
                   "" if not halted else "Lisez la cause, puis python trendguard_bot.py resume."))
    wise = p.risk_pct <= 0.01 and p.max_total_risk <= 0.06 and p.max_positions <= 8 \
        and gcfg.kill_drawdown <= 0.40
    out.append(chk("Risque configuré", wise,
                   f"{p.risk_pct * 100:g} % par achat, {p.max_total_risk * 100:g} % cumulé, "
                   f"{p.max_positions} positions, arrêt d'urgence à −{gcfg.kill_drawdown * 100:g} %"
                   .replace(".", ","),
                   "" if wise else "Revenez aux limites sages : 1 % par achat, 6 % cumulé, 8 "
                                   "positions, arrêt à −40 % (fichier .env)."))
    out.append(chk("Mode", None if gcfg.run_mode == "live" else True,
                   "RÉEL" if gcfg.run_mode == "live" else "paper : aucun argent réel en jeu"))
    free = shutil.disk_usage(v29.APP_DIR).free / 1e9
    out.append(chk("Espace disque", free >= 2, f"{free:.1f} Go libres".replace(".", ","),
                   "" if free >= 2 else "Libérez de l'espace disque (moins de 2 Go)."))
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
    remote = (_git(deps, root, "remote", "get-url", "origin") or "").strip()
    slug = re.sub(r"(\.git)?$", "", remote.split("github.com/")[-1]) if "github.com/" in remote else ""
    out.append(chk("Origine du code", bool(slug), f"github.com/{slug}" if slug else "inconnue",
                   "" if slug else "Le dépôt n'est plus relié à GitHub : demandez une vérification."))
    head = (_git(deps, root, "rev-parse", "HEAD") or "").strip()
    proposals: List[str] = []
    if gh and slug and head:
        try:
            runs = gh(f"https://api.github.com/repos/{slug}/actions/runs?head_sha={head}&per_page=20")
            wf = [r for r in (runs or {}).get("workflow_runs", []) if r.get("status") == "completed"]
            bad = [r["name"] for r in wf if r.get("conclusion") not in ("success", "skipped")]
            out.append(chk("Contrôles GitHub de cette version", None if not wf else not bad,
                           "en cours ou absents" if not wf else ("tests, qualité et sécurité au vert"
                                                               if not bad else "en échec : " + ", ".join(bad)),
                           "" if not bad else "Demandez une correction : les contrôles automatiques échouent."))
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


def _github_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "TrendGuard-rapport"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


# ══════════════════════════════════════════════════════════════════════
# Le rapport
# ══════════════════════════════════════════════════════════════════════

LEVEL_OK = {"OK": True, "INFO": True, "ATTENTION": None, "ALERTE": False}
# Lignes du centre de sécurité déjà vérifiées par le rapport lui-même.
PANEL_DUPLICATES = {"Disponibilité du bot (7 j)", "Relance automatique", "Arrêt d'urgence", "Mode",
                    "Clés API Binance", "Fichier des secrets (.env)", "Rapport quotidien"}


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
    if lr["brier_raw"] is not None and lr["forecasts"] >= 20:
        better = lr["brier_cal"] <= lr["brier_raw"]
        out.append(chk("Précision des prévisions", better,
                       f"erreur {lr['brier_raw']:.3f} brute, {lr['brier_cal']:.3f} corrigée".replace(".", ","),
                       "" if better else "La correction apprise n'aide pas encore : elle reste "
                                         "tempérée par le modèle."))
    ev = evolution.summary(gcfg)
    if ev.get("enabled"):
        changes = ", ".join(f"{c['param']} {c['from']} → {c['to']}" for c in ev["changes"]) or "réglages d'origine"
        out.append(chk("Évolution encadrée", None, f"niveau {ev['level']}/{ev['levels']} ({ev['name']}) ; "
                                                   f"{changes}" + (f" ; {ev['last_text']}" if ev.get("last_text") else "")))
    return out


def build(gcfg: Any, env: Dict[str, str], deps: Optional[Deps] = None,
          backups: bool = True) -> Dict[str, Any]:
    deps = deps or Deps()
    now = deps.now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    root = v29.APP_DIR if deps.extra.get("root") is None else deps.extra["root"]
    st = read_state(gcfg.db_file)
    pnl = (deps.panel or panel_fetch)(int(env.get("PANEL_PORT", "8765") or 8765),
                                      env.get("PANEL_PASSWORD", ""))
    status, security = pnl.get("status"), pnl.get("security")
    gh = deps.http_json or _github_json

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
    panel_sec = [chk(c["label"], c["ok"], c["detail"]) for c in (security or {}).get("checks", [])
                 if c.get("label") not in PANEL_DUPLICATES]
    health = bot_checks(gcfg, st, status, now)
    health.insert(0, chk("Panneau de contrôle", "ms" in pnl,
                         f"en marche, répond en {pnl['ms']} ms" if "ms" in pnl else "injoignable",
                         "" if "ms" in pnl else "Relancez le panneau (il démarre avec l'ordinateur)."))
    code, proposals = code_checks(root, deps, gh)
    form = [check_log(gcfg.log_file, now)] + code
    sections = [
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


def _icon(ok: Optional[bool]) -> str:
    return "✓" if ok is True else "✗" if ok is False else "i"


def render_text(r: Dict[str, Any]) -> str:
    s = r["score"]
    when = r["generated_at"][:16].replace("T", " à ")
    lines = [f"RAPPORT QUOTIDIEN TRENDGUARD — {r['day']} ({when} UTC, mode {r['mode']})",
             "",
             f"Verdict : {r['verdict']}. {s['ok']} contrôle(s) conforme(s), {s['warn']} à corriger, "
             f"{s['info']} information(s), sur {s['total']}.", ""]
    lines += ["CE QUE LE BOT A FAIT SEUL"] + ([f"- {a}" for a in r["actions"]] or
                                               ["- rien à corriger automatiquement"]) + [""]
    lines += ["À FAIRE (par ordre d'importance)"] + ([f"{k}. {x}" for k, x in enumerate(r["recommendations"], 1)]
                                                     or ["- rien : tout est en ordre"]) + [""]
    for sec in r["sections"]:
        lines.append(sec["title"].upper())
        lines += [f"  {_icon(c['ok'])} {c['label']} : {c['detail']}" for c in sec["checks"]]
        lines.append("")
    if r["proposals"]:
        lines += ["PROPOSITIONS D'AMÉLIORATION EN ATTENTE DE VOTRE VALIDATION (GitHub)"]
        lines += [f"- {p}" for p in r["proposals"]] + [""]
    lines += ["Règles de sagesse : le bot applique seul les protections sûres et réversibles "
              "(sauvegarde, droits du fichier des secrets, secrets masqués dans les journaux). "
              "Il ne touche jamais aux règles, au risque, aux clés, au mode réel, au code ni aux "
              "réglages de Windows : ces points restent des recommandations.",
              "Rapport complet : panneau ▸ Réglages ▸ Rapport quotidien. Aucun secret n'y figure."]
    return "\n".join(lines)


def render_short(r: Dict[str, Any]) -> str:
    s = r["score"]
    head = (f"🛡️ TrendGuard, rapport du {r['day']} : {r['verdict'].lower()} "
            f"({s['ok']}/{s['total']} ✓).")
    todo = [f"{k}. {x}" for k, x in enumerate(r["recommendations"][:3], 1)]
    tail = "Rapport complet : panneau ▸ Réglages ▸ Rapport quotidien (et par e-mail)."
    text = "\n".join([head] + todo + [tail])
    return text if len(text) <= SHORT_MAX else text[:SHORT_MAX - 1] + "…"


def save(gcfg: Any, report: Dict[str, Any], keep: int = KEEP_REPORTS) -> None:
    p = paths(gcfg)
    if p["json"]:
        _save_json(p["json"], report)
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
    """E-mail : rapport complet ; WhatsApp et Telegram : résumé."""
    subject = f"TrendGuard — rapport du {report['day']} : {report['verdict']}"
    res = hub.send_report(subject, report["text"], report["short"])
    at = time.time()
    return {name: {"at": at, "ok": err is None, "error": err} for name, err in res.items()}


def generate(gcfg: Any, env: Dict[str, str], send: bool = True, deps: Optional[Deps] = None,
             hub: Any = None) -> Dict[str, Any]:
    """Analyse, protections, rapport gardé puis envoyé. Un seul à la fois."""
    p = paths(gcfg)
    if p["lock"]:
        if is_running(gcfg):
            raise RuntimeError("un rapport est déjà en cours")
        with open(p["lock"], "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    try:
        report = build(gcfg, env, deps)
        save(gcfg, report)
        if send:
            own = hub is None
            if own:
                from . import alerts
                hub = alerts.build_notifier(env=env)
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
    p = paths(gcfg)
    kw: Dict[str, Any] = {"cwd": autonomy.ROOT, "stdin": subprocess.DEVNULL,
                          "stderr": subprocess.STDOUT,
                          "env": dict(os.environ, RUN_MODE=gcfg.run_mode, PYTHONIOENCODING="utf-8")}
    if os.name == "nt":
        kw["creationflags"] = autonomy.CREATE_NO_WINDOW
    try:
        with open(p["log"], "a", encoding="utf-8") as out:
            subprocess.Popen([sys.executable, autonomy.BOT_SCRIPT, "rapport", action],
                             stdout=out, **kw)
    except OSError:
        return False
    return True


def main(argv: Optional[List[str]] = None) -> int:
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Rapport quotidien : sécurité et diagnostic expert")
    ap.add_argument("action", nargs="?", default="dernier",
                    choices=["dernier", "maintenant", "quotidien"])
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
