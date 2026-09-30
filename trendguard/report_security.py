"""Rapport quotidien, contrôles de sécurité (docs/RAPPORT.md) : secrets
hors de GitHub et des journaux, droits du fichier des secrets, sauvegarde et
intégrité de la base, clé Binance, pare-feu, antivirus, veille et
alimentation du PC, bibliothèques du bot.

Seules protections appliquées ici, sûres et réversibles : la sauvegarde de
la base, les droits du fichier des secrets resserrés, un secret masqué dans
un journal. Tout le reste est un constat ou une recommandation.
"""

from __future__ import annotations

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
from typing import Any, Dict, List, Optional, Tuple

import v29

from . import environnement
from .systeme import Check, Deps, chk
from .systeme import git as _git
from .systeme import installed_versions as _installed_versions
from .systeme import power_ac as _power_ac
from .systeme import power_source as _power_source
from .systeme import ps_lines as _ps_lines
from .systeme import pypi_json as _pypi_json
from .systeme import requirements_of as _requirements_of
from .systeme import run as _run

KEEP_BACKUPS = 14
SECRET_SUFFIXES = ("_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_APIKEY")
SECRET_MIN_LEN = 8
BROAD_SIDS = {"S-1-1-0": "Tout le monde", "S-1-5-32-545": "Utilisateurs",
              "S-1-5-11": "Utilisateurs authentifiés"}
LOG_GLOBS = ("*.log", "*.log.*", "*.console.txt", "*.blocage.txt")
PINS_FILE = "requirements-docker.txt"       # versions testées des bibliothèques
AUDIT_TIMEOUT = 300
LIBRARY_RECO = ("Mettez les bibliothèques du bot à jour (README ▸ « Bibliothèques du bot »), "
                "ou demandez cette mise à jour.")


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


def _win_acl(path: str, deps: Deps) -> Tuple[Optional[List[Tuple[str, str]]], str]:
    """(SID, Allow/Deny) de chaque droit du fichier, via PowerShell, et la
    cause d'un échec. Un second essai, plus patient, suit un échec (PowerShell
    peut être lent à démarrer quand le PC est chargé)."""
    ps = ("$a=(Get-Acl -LiteralPath $env:TG_ACL_PATH).Access; foreach($e in $a){ try{"
          "$s=$e.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value}"
          "catch{$s=[string]$e.IdentityReference}; Write-Output ($s + '|' + $e.AccessControlType) }")
    err = ""
    for timeout in (60, 180):
        try:
            r = deps.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                         env=dict(os.environ, TG_ACL_PATH=path), timeout=timeout)
        except subprocess.TimeoutExpired:
            err = "PowerShell trop lent"
            continue
        except (OSError, subprocess.SubprocessError) as e:
            err = f"PowerShell indisponible ({type(e).__name__})"
            continue
        if r.returncode == 0:
            return [tuple(line.strip().split("|", 1)) for line in r.stdout.splitlines()
                    if "|" in line], ""
        lines = (r.stderr or "").strip().splitlines()
        err = f"PowerShell : {lines[-1][:120]}" if lines else f"PowerShell : code {r.returncode}"
    return None, err


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
    acl, err = _win_acl(path, deps)
    if acl is None:
        return chk(label, None, f"vérification impossible ({err})")
    broad = sorted({sid for sid, kind in acl if sid in BROAD_SIDS and kind == "Allow"})
    if not broad:
        return chk(label, True, "réservé à votre compte, à Windows et aux administrateurs")
    names = ", ".join(BROAD_SIDS[s] for s in broad)
    try:
        deps.run(["icacls", path, "/inheritance:d"])
        deps.run(["icacls", path, "/remove:g", *[f"*{s}" for s in broad]])
    except (OSError, subprocess.SubprocessError):
        pass
    after = _win_acl(path, deps)[0] or []
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
    source = _source_check(deps)
    if source:
        out.append(source)
    return out


BUTTON_ACTIONS = {0: "ne rien faire", 1: "veille", 2: "veille prolongée", 3: "arrêt",
                  4: "éteindre l'écran"}


def _power_check(deps: Deps) -> Check:
    sleep = _power_ac(deps, "SUB_SLEEP", "STANDBYIDLE")
    lid = _power_ac(deps, "SUB_BUTTONS", "LIDACTION")
    button = _power_ac(deps, "SUB_BUTTONS", "PBUTTONACTION")
    if sleep is None and lid is None:
        return chk("Veille du PC (sur secteur)", None, "réglages inconnus")
    good = sleep in (0, None) and lid in (0, None)
    parts = []
    if sleep is not None:
        parts.append("mise en veille : " + ("jamais" if sleep == 0 else f"après {sleep // 60} min"))
    if lid is not None:     # absent sur un PC fixe (pas de capot)
        parts.append("capot fermé : " + BUTTON_ACTIONS.get(lid, "?"))
    if lid is not None and button in (1, 2, 3):
        # Information : ce bouton reste à vous, le bot ne le change pas.
        parts.append(f"bouton d'alimentation : {BUTTON_ACTIONS[button]} (pour laisser tourner le "
                     "bot, fermez le capot au lieu d'appuyer dessus)")
    todo = []
    if sleep not in (0, None):
        todo.append("mise en veille « Jamais »")
    if lid not in (0, None):
        todo.append("capot fermé « Ne rien faire »")
    return chk("Veille du PC (sur secteur)", good, " ; ".join(parts),
               "" if good else "Paramètres Windows ▸ Alimentation, sur secteur : "
                               + " et ".join(todo) + " (le bot ne surveille rien quand le PC dort).")


def _source_check(deps: Deps) -> Optional[Check]:
    return battery_check(_power_source(deps))


def battery_check(p: Optional[Dict[str, Any]]) -> Optional[Check]:
    """Sur batterie, un portable s'endort capot fermé, puis s'éteint : le bot
    s'arrête. None si Windows ne dit rien (ou PC fixe sans batterie). Le même
    constat sert au rapport et au centre de sécurité du panneau."""
    if not p or p.get("battery_pct") is None:
        return None
    pct = f"batterie à {p['battery_pct']} %"
    if p["ac"]:
        return chk("Alimentation du PC", True, f"sur secteur ({pct})")
    return chk("Alimentation du PC", False, f"SUR BATTERIE ({pct})",
               "Branchez le chargeur : sur batterie, le PC se met en veille dès que le capot est "
               "fermé, puis s'éteint quand elle est vide, et le bot s'arrête.")


def read_pins(root: str) -> Dict[str, str]:
    """Versions testées des bibliothèques (nom → version) ; vide si la liste
    est illisible."""
    pins: Dict[str, str] = {}
    try:
        with open(os.path.join(root, PINS_FILE), encoding="utf-8") as fh:
            for line in fh:
                name, sep, version = line.split("#")[0].strip().partition("==")
                if sep and name.strip() and version.strip():
                    pins[name.strip()] = version.strip()
    except OSError:
        pass
    return pins


def library_checks(root: str, deps: Deps) -> List[Check]:
    """Bibliothèques du bot : aux versions testées, sans faille connue
    (lecture seule : les installer reste une recommandation)."""
    pins = read_pins(root)
    have = _installed_versions(deps, list(pins))
    if have is None:
        return []
    own = deps.extra["own_env"] if "own_env" in deps.extra else environnement.inside(root)
    return [_versions_check(pins, have, own), audit_check(root, deps)]


def _versions_check(pins: Dict[str, str], have: Dict[str, Optional[str]], own: bool) -> Check:
    label = "Bibliothèques du bot"
    if not pins:
        return chk(label, None, f"versions testées introuvables ({PINS_FILE})")
    wrong = [f"{n} {have[n] or 'absente'} au lieu de {v}" for n, v in pins.items() if have[n] != v]
    if wrong:
        return chk(label, False, ", ".join(wrong) + (
            ", dans l'environnement propre du bot" if own else
            " : le bot utilise les bibliothèques du PC, pas son environnement propre"), LIBRARY_RECO)
    return chk(label, True, "aux versions testées" + (", dans l'environnement propre du bot" if own else "")
               + " : " + ", ".join(f"{n} {v}" for n, v in pins.items()))


def _version_key(version: str) -> Tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.+-]", version))


def needed_fix(installed: str, flaws: List[Dict[str, Any]]) -> Optional[str]:
    """Version qui corrige les failles connues d'une bibliothèque : pour
    chacune, la plus petite correction au-dessus de la version installée, et
    la plus haute de celles-là. None si aucune correction n'est publiée."""
    needed: Optional[str] = None
    for flaw in flaws:
        later = [v for v in flaw.get("fix_versions") or [] if _version_key(v) > _version_key(installed)]
        if later:
            first = min(later, key=_version_key)
            if needed is None or _version_key(first) > _version_key(needed):
                needed = first
    return needed


def blockers(name: str, fix: str, requirements: Dict[str, Tuple[str, List[str]]]
             ) -> List[Tuple[str, str, str]]:
    """Bibliothèques dont une exigence exclut la version corrigée :
    [(nom, version, exigence)]. ccxt, par exemple, épingle chacune des
    siennes à une version exacte."""
    try:
        from packaging.requirements import InvalidRequirement, Requirement
        from packaging.utils import canonicalize_name
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return []
    target, out = canonicalize_name(name), []
    for dist, (version, requires) in requirements.items():
        for raw in requires:
            try:
                req = Requirement(raw)
                if canonicalize_name(req.name) != target or (
                        req.marker is not None and not req.marker.evaluate({"extra": ""})):
                    continue
                if req.specifier and not req.specifier.contains(Version(fix), prereleases=True):
                    out.append((dist, version, f"{req.name}{req.specifier}"))
            except (InvalidRequirement, InvalidVersion):
                continue
    return out


def _upstream(dist: str, name: str, fix: str, deps: Deps) -> Tuple[Optional[str], bool]:
    """(dernière version publiée de `dist`, accepte-t-elle la correction ?)."""
    fetch = deps.extra.get("pypi") or (_pypi_json if deps.run is _run else None)
    try:
        info = fetch(dist)["info"] if fetch else None
    except Exception:              # PyPI injoignable : rien n'est affirmé
        info = None
    if not info:
        return None, False
    latest = info.get("version")
    return latest, not blockers(name, fix, {dist: (latest, info.get("requires_dist") or [])})


def audit_check(root: str, deps: Deps, requirements: Optional[str] = None) -> Check:
    """Failles connues (pip-audit) des bibliothèques installées là où tourne
    le bot, ou d'une liste de versions (`requirements`, comme sur GitHub).
    À corriger quand la correction peut s'installer ; simple information
    quand une bibliothèque épinglée l'empêche encore, jusqu'à ce que sa
    nouvelle version publiée l'accepte."""
    label = "Failles connues des bibliothèques"
    cmd = [sys.executable, "-m", "pip_audit", "--progress-spinner", "off", "--desc", "off", "-f", "json"]
    try:
        r = deps.run(cmd + (["-r", requirements] if requirements else []), cwd=root, timeout=AUDIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return chk(label, None, "vérification impossible")
    if "No module named" in (r.stderr or ""):
        return chk(label, None, "pip-audit non installé")
    text = r.stdout or ""
    try:
        found = json.loads(text[text.index("{"):])["dependencies"]
    except (ValueError, KeyError, TypeError):
        return chk(label, None, "vérification impossible (réseau ?)")
    installed = _requirements_of(deps)
    now: List[str] = []
    later: List[str] = []
    for lib in found:
        if not lib.get("vulns"):
            continue
        name, version = lib.get("name", "?"), lib.get("version", "?")
        fix = needed_fix(version, lib["vulns"])
        if fix is None:
            later.append(f"{name} {version} (aucune correction publiée)")
            continue
        blocking = blockers(name, fix, installed) if installed else []
        if not blocking:
            now.append(f"{name} {version} (corrigée en {fix})")
            continue
        dist, dist_version, need = blocking[0]
        latest, accepts = _upstream(dist, name, fix, deps)
        if accepts and latest and latest != dist_version:
            now.append(f"{name} {version} (corrigée en {fix}, que {dist} {latest} accepte)")
        else:
            later.append(f"{name} {version} (corrigée en {fix}, mais {dist} {dist_version}"
                         + (", sa dernière version," if latest == dist_version else "")
                         + f" exige {need})")
    if now:
        listed = now + later
        return chk(label, False, f"{len(listed)} sur {len(found)} : " + ", ".join(listed[:6])
                   + (" …" if len(listed) > 6 else ""), LIBRARY_RECO)
    if later:
        return chk(label, None, f"{len(later)} sur {len(found)}, pas encore corrigeable : "
                   + ", ".join(later) + " ; le rapport dira quand la correction pourra s'installer")
    return chk(label, True, f"aucune dans les {len(found)} bibliothèques installées")
