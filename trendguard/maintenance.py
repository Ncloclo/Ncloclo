"""Maintenance autonome, avec sagesse (docs/RAPPORT.md).

Chaque nuit, au début du rapport de 00:30 UTC, le bot applique lui-même les
recommandations qu'il peut appliquer sans risque :

1. Corrections sûres et réversibles (TG_AUTOCORRECTION) : sur secteur
   seulement, mise en veille et veille prolongée « Jamais », capot fermé
   « Ne rien faire » : le bot ne surveille rien quand le PC dort. Sur
   batterie, rien ne change. Les valeurs d'origine sont gardées ;
   `python trendguard_bot.py rapport restaurer` les remet et arrête ces
   corrections, `rapport corriger` les reprend.
2. Mises à jour validées (TG_MISE_A_JOUR) : une amélioration proposée en
   Pull Request et fusionnée sur GitHub par le propriétaire du dépôt est
   installée seule si les contrôles GitHub de cette version sont au vert :
   avance rapide seulement, contrôle de démarrage sur ce PC, redémarrage du
   bot (par son superviseur) et du panneau, puis vérification qu'ils
   tournent ; sinon, retour automatique à la version précédente.

Jamais installé : un changement publié sans la validation du propriétaire
(ce PC garde les clés Binance : c'est la seule porte d'entrée du code), une
version aux contrôles en échec, une version qui ne prolonge pas celle de ce
PC, une installation pendant que des fichiers sont modifiés sur ce PC, ni
une installation automatique en mode réel.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import autonomy
from .systeme import Check, Deps, chk, ci_status, git, github_json, power_ac, read_state, repo_slug

# (sous-groupe, réglage, option de powercfg /change, nom) : sur secteur seulement.
POWER = (("SUB_SLEEP", "STANDBYIDLE", "standby-timeout-ac", "mise en veille"),
         ("SUB_SLEEP", "HIBERNATEIDLE", "hibernate-timeout-ac", "veille prolongée"))
LID = ("SUB_BUTTONS", "LIDACTION")
LID_NAMES = {0: "ne rien faire", 1: "veille", 2: "veille prolongée", 3: "arrêt"}
HEALTH_WAIT_SEC = 420
HEALTH_POLL_SEC = 10


def _files(gcfg: Any) -> Dict[str, str]:
    return {"restore": autonomy.sidecar(gcfg.lock_file, ".restauration.json"),
            "off": autonomy.sidecar(gcfg.lock_file, ".autocorrection.off")}


# ══════════════════════════════════════════════════════════════════════
# 1. Corrections sûres et réversibles
# ══════════════════════════════════════════════════════════════════════

def fix_power(gcfg: Any, deps: Deps) -> Optional[Check]:
    """Veille du PC sur secteur : « Jamais » ; capot fermé : « Ne rien faire ».
    None s'il n'y a rien à corriger (ou hors de Windows)."""
    if not deps.platform.startswith("win"):
        return None
    label = "Correction de la veille du PC"
    f = _files(gcfg)
    if f["off"] and os.path.exists(f["off"]):
        return chk(label, None, "arrêtée à votre demande (python trendguard_bot.py rapport "
                                "corriger pour la reprendre)")
    before: Dict[str, int] = {}
    done: List[str] = []
    failed: List[str] = []
    for sub, key, change, name in POWER:
        v = power_ac(deps, sub, key)
        if v in (0, None):
            continue
        before[key] = v
        deps.run(["powercfg", "/change", change, "0"])
        if power_ac(deps, sub, key) == 0:
            done.append(f"{name} sur secteur : jamais (avant : après {v // 60} min)")
        else:
            failed.append(name)
    lid = power_ac(deps, *LID)
    if lid not in (0, None):
        before["LIDACTION"] = lid
        deps.run(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", *LID, "0"])
        deps.run(["powercfg", "/setactive", "SCHEME_CURRENT"])
        if power_ac(deps, *LID) == 0:
            done.append(f"capot fermé sur secteur : ne rien faire (avant : {LID_NAMES.get(lid, '?')})")
        else:
            failed.append("capot fermé")
    if not before:
        return None
    if f["restore"]:
        saved = autonomy._read_json(f["restore"])
        power = saved.get("power") or {}
        for k, v in before.items():
            power.setdefault(k, v)              # valeurs d'origine : jamais écrasées
        saved["power"] = power
        autonomy.write_json(f["restore"], saved)
    if failed:
        return chk(label, False, "impossible pour : " + ", ".join(failed),
                   "Réglez vous-même, sur secteur : " + ", ".join(failed) + " sur « Jamais » "
                   "(Paramètres Windows ▸ Alimentation ; droits d'administrateur peut-être requis).",
                   action="; ".join(done))
    return chk(label, True, "appliquée : le PC ne s'endort plus quand il est branché",
               action="; ".join(done))


def restore_power(gcfg: Any, deps: Deps) -> str:
    """Remet les réglages d'origine et arrête les corrections automatiques."""
    f = _files(gcfg)
    saved = autonomy._read_json(f["restore"]) if f["restore"] else {}
    power = saved.get("power") or {}
    for _sub, key, change, _name in POWER:
        if key in power:
            deps.run(["powercfg", "/change", change, str(max(int(power[key]) // 60, 1))])
    if "LIDACTION" in power:
        deps.run(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", *LID, str(int(power["LIDACTION"]))])
        deps.run(["powercfg", "/setactive", "SCHEME_CURRENT"])
    if f["off"]:
        with open(f["off"], "w", encoding="utf-8") as fh:
            fh.write("réglages d'origine remis à la demande de l'utilisateur")
    if f["restore"] and os.path.exists(f["restore"]):
        os.remove(f["restore"])
    return (("Réglages de veille d'origine remis" if power else "Aucun réglage à remettre")
            + " ; corrections automatiques arrêtées (python trendguard_bot.py rapport corriger "
              "pour les reprendre).")


def resume_fixes(gcfg: Any) -> str:
    off = _files(gcfg)["off"]
    if off and os.path.exists(off):
        os.remove(off)
    return "Corrections automatiques reprises : elles s'appliquent au prochain rapport."


# ══════════════════════════════════════════════════════════════════════
# 2. Mises à jour validées par le propriétaire
# ══════════════════════════════════════════════════════════════════════

def validated(gh: Callable[[str], Any], slug: str, owner: str, shas: List[str]
              ) -> Tuple[List[str], List[str]]:
    """(titres des Pull Requests fusionnées par le propriétaire, changements
    qui n'en font partie d'aucune)."""
    titles: List[str] = []
    unvalidated: List[str] = []
    cache: Dict[int, Dict[str, Any]] = {}
    for sha in shas:
        ok = False
        for p in gh(f"https://api.github.com/repos/{slug}/commits/{sha}/pulls") or []:
            n = p.get("number")
            if n is None or not p.get("merged_at"):
                continue
            if n not in cache:
                cache[n] = gh(f"https://api.github.com/repos/{slug}/pulls/{n}") or {}
            pr = cache[n]
            if ((pr.get("merged_by") or {}).get("login") or "").lower() == owner.lower():
                ok = True
                title = f"#{n} {pr.get('title', '')}".strip()
                if title not in titles:
                    titles.append(title)
        if not ok:
            unvalidated.append(sha[:7])
    return titles, unvalidated


def startup_check(root: str, deps: Deps) -> Tuple[bool, str]:
    """Contrôle de démarrage de la nouvelle version sur ce PC (les tests
    complets ont tourné sur GitHub) : compilation, puis chargement du bot,
    du panneau et du rapport."""
    r = deps.run([sys.executable, "-m", "compileall", "-q", "trendguard", "panel", "v29",
                  "research", "trendguard_bot.py"], cwd=root, timeout=300)
    if r.returncode != 0:
        return False, "compilation impossible"
    r = deps.run([sys.executable, "-c", "import trendguard_bot, panel.server, trendguard.report"],
                 cwd=root, timeout=120)
    if r.returncode != 0:
        last = (r.stderr or "").strip().splitlines()
        return False, "chargement impossible" + (f" : {last[-1][:150]}" if last else "")
    return True, ""


def running_again(gcfg: Any, since: float, port: int) -> Tuple[bool, str]:
    """Après le redémarrage : le bot reprend-il ses cycles, et le panneau
    répond-il ? (7 minutes au plus)"""
    end = time.time() + HEALTH_WAIT_SEC
    bot_ok = panel_ok = False
    while time.time() < end:
        bot_ok = float(read_state(gcfg.db_file).get("last_cycle_ts") or 0) > since
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
                panel_ok = r.status == 200
        except OSError:
            panel_ok = False
        if bot_ok and panel_ok:
            return True, ""
        time.sleep(HEALTH_POLL_SEC)
    return False, "le bot ne reprend pas ses cycles" if not bot_ok else "le panneau ne répond pas"


def _restart(gcfg: Any) -> None:
    autonomy.request_restart(gcfg)
    autonomy.restart_panel()


def update(gcfg: Any, deps: Deps, root: str, port: int = 8765,
           gh: Optional[Callable[[str], Any]] = None,
           check: Optional[Callable[[str, Deps], Tuple[bool, str]]] = None,
           restart: Optional[Callable[[Any], None]] = None,
           health: Optional[Callable[[Any, float, int], Tuple[bool, str]]] = None,
           supervised: Optional[Callable[[Any], bool]] = None,
           allow_live: bool = False) -> Check:
    """Installe les changements validés par le propriétaire, ou dit pourquoi
    pas. Retour automatique à la version précédente en cas de problème."""
    label = "Mise à jour du bot"
    gh = gh or deps.http_json or github_json
    changed = git(deps, root, "status", "--porcelain", "--untracked-files=no")
    if changed is None:
        return chk(label, None, "git absent : mises à jour manuelles")
    if changed.strip():
        return chk(label, None, "en attente : des fichiers du bot sont modifiés sur ce PC "
                                "(une intervention est en cours)")
    branch = (git(deps, root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    slug = repo_slug(deps, root)
    if not branch or branch == "HEAD" or not slug:
        return chk(label, None, "dépôt non relié à GitHub : mises à jour manuelles")
    if git(deps, root, "fetch", "--quiet", "origin", branch) is None:
        return chk(label, None, "GitHub injoignable : nouvel essai au prochain rapport")
    head = (git(deps, root, "rev-parse", "HEAD") or "").strip()
    new = (git(deps, root, "rev-list", "--reverse", f"HEAD..origin/{branch}") or "").split()
    if not new:
        return chk(label, True, f"version à jour ({head[:7]})")
    if git(deps, root, "merge-base", "--is-ancestor", "HEAD", f"origin/{branch}") is None:
        return chk(label, None, "la version publiée ne prolonge pas celle de ce PC : "
                                "installation manuelle")
    try:
        ci_ok, ci_bad = ci_status(gh, slug, new[-1])
        titles, unvalidated = validated(gh, slug, slug.split("/")[0], new)
    except Exception as e:
        return chk(label, None, f"GitHub injoignable ({type(e).__name__}) : nouvel essai au "
                                "prochain rapport")
    if unvalidated:
        return chk(label, False, f"{len(unvalidated)} changement(s) publié(s) sans votre validation "
                                 f"({', '.join(unvalidated[:5])}) : NON installé(s)",
                   "Vérifiez sur GitHub qui a publié ces changements : seules les Pull Requests "
                   "que vous fusionnez vous-même sont installées.")
    what = f"{len(new)} changement(s) validé(s) (" + "; ".join(titles[:3]) + ")"
    if ci_ok is None:
        return chk(label, None, f"{what} : contrôles GitHub en cours, installation au prochain rapport")
    if not ci_ok:
        return chk(label, False, f"{what} : contrôles GitHub en échec ({', '.join(ci_bad)}), "
                                 "non installé(s)",
                   "Demandez une correction de la mise à jour avant son installation.")
    if gcfg.run_mode == "live" and not allow_live:
        return chk(label, None, f"{what} prêt(s) : en mode réel, installation à votre demande "
                                "(python trendguard_bot.py rapport installer)")
    if not (supervised or (lambda g: autonomy.supervisor_status(g).get("running")))(gcfg):
        return chk(label, None, f"{what} prêt(s) : relance automatique inactive, installation "
                                "au prochain rapport (cliquez sur AUTO)")
    if git(deps, root, "merge", "--ff-only", "--quiet", f"origin/{branch}") is None:
        return chk(label, None, "installation refusée par git : installation manuelle")
    ok, why = (check or startup_check)(root, deps)
    if not ok:
        git(deps, root, "reset", "--hard", "--quiet", head)
        return chk(label, False, f"nouvelle version refusée par le contrôle de démarrage ({why}) : "
                                 "version précédente gardée",
                   "Demandez une correction de la mise à jour.")
    since = time.time()
    (restart or _restart)(gcfg)
    good, why = (health or running_again)(gcfg, since, port)
    if not good:
        git(deps, root, "reset", "--hard", "--quiet", head)
        (restart or _restart)(gcfg)
        return chk(label, False, f"nouvelle version défaillante ({why}) : retour automatique à la "
                                 "version précédente",
                   "Demandez une vérification de la mise à jour.")
    return chk(label, True, f"{what} installé(s) et vérifié(s) : bot et panneau relancés",
               action=f"mise à jour installée ({head[:7]} → {new[-1][:7]}) : " + "; ".join(titles[:3]))


def run_all(gcfg: Any, deps: Deps, root: str, port: int = 8765) -> List[Check]:
    """Les recommandations appliquées seules, dans l'ordre : corrections sûres,
    puis mises à jour validées."""
    out: List[Check] = []
    if getattr(gcfg, "auto_fix", False):
        c = fix_power(gcfg, deps)
        if c:
            out.append(c)
    if getattr(gcfg, "auto_update", False):
        out.append(update(gcfg, deps, root, port))
    return out
