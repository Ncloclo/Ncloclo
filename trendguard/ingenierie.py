"""
Ingénierie et exploitation (prompt maître, étapes 30 et 31 ; docs/INGENIERIE.md) :
comment le code du bot change et comment il arrive sur ce PC, mesuré.
Chaque changement tracé (git : sujet, auteur, co-auteur IA), les tests et ce
qu'ils couvrent, la chaîne de contrôle GitHub (ruff, tests, audit des
bibliothèques), les versions figées contre les versions installées (dérive
de l'état voulu et de l'état observé), le retour arrière, et la règle
d'auto-modification : le bot n'écrit jamais son propre code ; la seule mise à
jour qu'il installe est une Pull Request que vous avez fusionnée, aux
contrôles au vert, en avance rapide, avec retour automatique en cas d'échec
(maintenance.py), jamais en réel sans vous.

Un code généré n'est pas un code validé ; un test passé n'est pas un
logiciel correct ; ce module ne dit « testé » que preuve à l'appui.

    python trendguard_bot.py ingenierie                  # changements, tests, chaîne, dérive, retour arrière
    python trendguard_bot.py ingenierie --out docs/INGENIERIE_ETAT.md
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import platform
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import v29

from . import chantiers, cyber, perception, porte_examen, systeme
from . import trend_strategy as ts
from .contrats import EngineeringReport
from .texte import fr

VERSION = "ingenierie-1.0.0"
ROOT = pathlib.Path(__file__).resolve().parent.parent
CODE_DIRS = ("trendguard", "panel", "research")
WORKFLOW = ".github/workflows/checks.yml"
LOG_DEPTH = 200
# Commandes git qui écrivent : seule la mise à jour validée (maintenance.py)
# peut avancer la version (avance rapide) ou revenir à la précédente.
GIT_WRITE = ("commit", "push", "merge", "reset", "checkout", "rebase", "pull", "cherry-pick", "revert", "tag")
WRITE_ALLOWED = {"trendguard/maintenance.py": {"merge", "reset"}}
GIT_CALL = re.compile(r'(?:git\([^,]+,\s*[^,]+,\s*|\[\s*"git",\s*)"([a-z-]+)"')
PIP_INSTALL = re.compile(r'\[[^\]]*"pip"\s*,\s*"install"')
SOURCE_WRITE = re.compile(r'open\([^)]*\.py["\'][^)]*["\']w')


def _files(root: pathlib.Path) -> List[pathlib.Path]:
    return [f for d in CODE_DIRS for f in sorted((root / d).rglob("*.py"))] + [root / "trendguard_bot.py"]


def changes(deps: Optional[systeme.Deps] = None, root: pathlib.Path = ROOT, n: int = LOG_DEPTH) -> Dict[str, Any]:
    """Les derniers changements du code (§13, §29) : chacun identifié,
    expliqué, daté ; part des changements écrits avec une IA (co-auteur
    déclaré) ; retour arrière possible (version précédente connue)."""
    deps = deps or systeme.Deps()
    raw = systeme.git(deps, str(root), "log", f"-n{n}", "--format=%h%x1f%s%x1f%an%x1f%aI%x1f%b%x1e")
    if raw is None:
        return {"ok": None, "commits": [], "detail": "git indisponible : changements non lus"}
    commits = []
    for rec in raw.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) < 5 or not parts[0].strip():
            continue
        h, subject, author, date, body = (p.strip() for p in parts[:5])
        commits.append({"commit": h, "subject": subject, "author": author, "date": date,
                        "ai": "co-authored-by: claude" in body.lower(),
                        "step": (re.search(r"[ÉE]tape (\d+)", subject) or [None, None])[1]})
    traced = [c for c in commits if len(c["subject"]) >= 15]
    return {"ok": True, "commits": commits, "traced": len(traced), "ai": sum(c["ai"] for c in commits),
            "rollback": len(commits) >= 2, "steps": sorted({int(c["step"]) for c in commits if c["step"]}),
            "detail": f"{len(commits)} derniers changements lus"}


def tests_inventory(root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Les tests (§19, §22) : fichiers, fonctions de test, modules du bot
    importés directement par au moins un test (une mesure par module, pas par
    ligne), composants de la feuille de route sans test (hors attente)."""
    files = sorted((root / "tests").glob("test_*.py"))
    functions, imported = 0, set()
    for f in files:
        src, tree = perception.parsed(f)
        functions += sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"))
        imported |= perception._imports(f)
        imported |= set(re.findall(r"trendguard\.([a-z_]+)", src)) | set(re.findall(r"panel\.([a-z_]+)", src))
    modules = sorted(p.stem for d in ("trendguard", "panel") for p in (root / d).glob("*.py")
                     if not p.stem.startswith("__"))
    untested = [m for m in modules if m not in imported]
    no_tests = [c.task_id for c in chantiers.COMPONENTS if c.status != "BACKLOG"
                and (not c.tests or not all((root / t).exists() for t in c.tests))]
    return {"files": len(files), "functions": functions, "modules": len(modules), "untested": untested,
            "coverage": (len(modules) - len(untested)) / len(modules) if modules else 0.0,
            "components_without_tests": no_tests}


def pipeline(root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """La chaîne de contrôle GitHub (§14, §30) : style (ruff), tests, audit
    des bibliothèques, durée maximale ; versions des bibliothèques figées."""
    wf = root / WORKFLOW
    text = wf.read_text(encoding="utf-8") if wf.exists() else ""
    timeout = [int(x) for x in re.findall(r"timeout-minutes:\s*(\d+)", text)]
    pins = report_pins(root)
    steps = {"style (ruff)": "ruff check" in text, "tests (pytest)": "pytest" in text,
             "audit des bibliothèques": "pip-audit" in text, "pages web (Playwright)": "playwright" in text}
    return {"exists": bool(text), "steps": steps, "timeout": max(timeout) if timeout else None, "pins": pins,
            "ok": bool(text) and steps["style (ruff)"] and steps["tests (pytest)"] and bool(timeout)}


def report_pins(root: pathlib.Path) -> Dict[str, Any]:
    """La liste des bibliothèques (nomenclature du logiciel) : figées ou non."""
    req = root / "requirements-docker.txt"
    lines = [x.split("#")[0].strip() for x in req.read_text(encoding="utf-8").splitlines()] if req.exists() else []
    lines = [x for x in lines if x]
    pinned = [x for x in lines if "==" in x]
    return {"total": len(lines), "pinned": len(pinned), "loose": [x for x in lines if "==" not in x],
            "sbom": sorted(tuple(x.split("==", 1)) for x in pinned)}


def self_modification(root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Contrôle de l'auto-modification (§25, §37-38) : aucune écriture de
    fichier .py, aucune installation de bibliothèque, aucune commande git qui
    écrit, hors de la mise à jour validée (avance rapide et retour à la
    version précédente, maintenance.py)."""
    found, writes = [], {}
    for f in _files(root):
        if not f.exists() or f.name == "ingenierie.py":
            continue
        rel = f.relative_to(root).as_posix()
        src = perception.parsed(f)[0]
        cmds = {c for c in GIT_CALL.findall(src) if c in GIT_WRITE}
        if cmds:
            writes[rel] = sorted(cmds)
        bad = cmds - WRITE_ALLOWED.get(rel, set())
        if bad:
            found.append(f"{rel} : git {', '.join(sorted(bad))}")
        if PIP_INSTALL.search(src):
            found.append(f"{rel} : installation de bibliothèque")
        if SOURCE_WRITE.search(src):
            found.append(f"{rel} : écriture d'un fichier .py")
    maint = (root / "trendguard" / "maintenance.py")
    msrc = maint.read_text(encoding="utf-8") if maint.exists() else ""
    gated = all(s in msrc for s in ("validated(", "ci_status(", "--ff-only", "reset", "allow_live"))
    return {"ok": not found and gated, "found": found, "writes": writes, "update_gated": gated}


def syntax(root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Construction (§21) : chaque fichier Python se lit sans erreur."""
    bad = []
    for f in _files(root):
        if not f.exists():
            continue
        try:
            perception.parsed(f)
        except (SyntaxError, ValueError, OSError) as e:
            bad.append(f"{f.relative_to(root).as_posix()} : {type(e).__name__}")
    return {"ok": not bad, "files": len(_files(root)), "bad": bad}


def infrastructure(deps: Optional[systeme.Deps] = None, root: pathlib.Path = ROOT) -> Dict[str, Any]:
    """Exploitation (étape 31, §11-12, §19) : Python et système de ce PC,
    environnement isolé du bot (.venv), état voulu (versions figées) contre
    état observé (versions installées), aucun secret dans un fichier suivi
    par git (.env)."""
    deps = deps or systeme.Deps()
    sc = cyber.supply_chain(deps, root)
    env_tracked = systeme.git(deps, str(root), "ls-files", "--", ".env")
    return {"python": platform.python_version(), "system": f"{platform.system()} {platform.release()}",
            "venv": sys.prefix != getattr(sys, "base_prefix", sys.prefix), "supply": sc,
            "env_tracked": None if env_tracked is None else bool(env_tracked.strip())}


WEIGHTS = {"correctness": 0.20, "tests": 0.15, "security": 0.15, "architecture": 0.10, "reliability": 0.10,
           "reproducibility": 0.10, "observability": 0.05, "performance": 0.05, "rollback": 0.05,
           "governance": 0.05}
WEIGHT_FR = {"correctness": "justesse (construction, auto-modification)", "tests": "tests et couverture",
             "security": "sécurité", "architecture": "intégrité de l'architecture", "reliability": "chaîne de contrôle",
             "reproducibility": "reproductibilité", "observability": "traçabilité des changements",
             "performance": "performance", "rollback": "retour arrière", "governance": "gouvernance des mises à jour"}
P0 = ("correctness", "architecture", "governance")


def band(score: float, p0: Sequence[str]) -> str:
    """Bandes de l'étape 30 (§56) ; un P0 : NOT_READY."""
    if p0:
        return "NOT_READY"
    return ("READY" if score >= 95 else "RELEASE_CANDIDATE" if score >= 90 else "VALIDATING" if score >= 80
            else "DEVELOPMENT" if score >= 70 else "REJECTED")


def quality(r: Dict[str, Any], took_s: float) -> Dict[str, Any]:
    """Note de préparation (§55-56), famille par famille, chacune avec sa preuve."""
    ch, tv, pl, sm, sx, inf = (r["changes"], r["tests"], r["pipeline"], r["self_modification"], r["syntax"],
                               r["infrastructure"])
    integ, sc = r["integrity"], inf["supply"]
    routes, deps_ok = r["routes"], r["dependencies"]
    n = len(ch.get("commits") or [])
    checks = {
        "correctness": (sx["ok"] and sm["ok"], f"{sx['files']} fichiers lus sans erreur ; "
                        + ("aucune auto-modification" if sm["ok"] else "; ".join(sm["found"][:2])
                           or "mise à jour sans ses garde-fous")),
        "tests": (tv["coverage"] >= 0.9 and not tv["components_without_tests"],
                  f"{tv['functions']} tests dans {tv['files']} fichiers ; {fr(tv['coverage'] * 100, '.0f')} % des "
                  f"modules importés par un test"),
        "security": (integ["ok"] is True and inf["env_tracked"] is False and not r["egress"],
                     integ["detail"] + (" ; .env jamais suivi par git" if inf["env_tracked"] is False else
                                        " ; .env : non vérifié" if inf["env_tracked"] is None else " ; .env SUIVI")
                     + ("" if not r["egress"] else " ; adresses hors liste blanche : " + ", ".join(r["egress"][:3]))),
        "architecture": (routes["ok"] and deps_ok, f"{len(routes['calls'])} appel d'achat, après la porte et la "
                         "validation finale" if routes["ok"] else "chemin d'achat hors de la porte"),
        "reliability": (pl["ok"], "contrôles GitHub : " + ", ".join(k for k, v in pl["steps"].items() if v)
                        + (f" ; {pl['timeout']} min au plus" if pl["timeout"] else "")),
        "reproducibility": (pl["pins"]["total"] > 0 and not pl["pins"]["loose"] and sc["ok"] is not False
                            and not sc.get("drift"),
                            f"{pl['pins']['pinned']}/{pl['pins']['total']} bibliothèques figées ; " + sc["detail"]),
        "observability": (n > 0 and ch["traced"] / n >= 0.95, f"{ch['traced']}/{n} changements expliqués"
                          if n else ch["detail"]),
        "performance": (took_s <= 15, f"en {fr(took_s, '.1f')} s"),
        "rollback": (bool(ch.get("rollback")) and sm["update_gated"], "version précédente connue ; mise à jour "
                     "défaillante : retour automatique" if ch.get("rollback") else "version précédente inconnue"),
        "governance": (sm["update_gated"] and not sm["found"], "seule une Pull Request fusionnée par vous, aux "
                       "contrôles au vert, s'installe ; jamais en réel sans vous"),
    }
    score = sum(WEIGHTS[k] * (100.0 if ok else 0.0) for k, (ok, _d) in checks.items())
    p0 = [k for k in P0 if not checks[k][0]]
    return {"checks": checks, "score": round(score, 1), "p0": p0, "status": band(score, p0)}


def evaluate(gcfg: Any = None, deps: Optional[systeme.Deps] = None, root: pathlib.Path = ROOT,
             now: Optional[datetime] = None) -> Dict[str, Any]:
    """L'ingénierie du moment : changements, tests, chaîne de contrôle,
    auto-modification, construction, exploitation, qualité."""
    now = now or datetime.now(timezone.utc)
    deps = deps or systeme.Deps()
    t = time.perf_counter()
    r: Dict[str, Any] = {"version": VERSION, "now": now.isoformat(timespec="seconds"),
                         "mode": getattr(gcfg, "run_mode", "paper"), "changes": changes(deps, root),
                         "tests": tests_inventory(root), "pipeline": pipeline(root),
                         "self_modification": self_modification(root), "syntax": syntax(root),
                         "infrastructure": infrastructure(deps, root), "integrity": cyber.integrity(deps),
                         "routes": porte_examen.routes(root), "dependencies": perception.dependencies(root)["ok"],
                         "egress": cyber.egress(root).get("unknown") or []}
    r["quality"] = quality(r, time.perf_counter() - t)
    return r


def report_of(r: Dict[str, Any]) -> EngineeringReport:
    """Le résultat au format du contrat EngineeringReport.v1."""
    q, ch, tv = r["quality"], r["changes"], r["tests"]
    n = len(ch.get("commits") or [])
    return EngineeringReport(report_id=str(uuid.uuid4()), created_at=r["now"], mode=r["mode"], status=q["status"],
                             readiness_score=q["score"], p0_failures=tuple(q["p0"]), commits=n,
                             traced_share=(ch["traced"] / n) if n else 0.0, test_functions=tv["functions"],
                             module_coverage=round(tv["coverage"], 4),
                             drift=tuple(r["infrastructure"]["supply"].get("drift") or ()),
                             self_modification=tuple(r["self_modification"]["found"]), engine_version=VERSION)


def describe(r: Dict[str, Any]) -> str:
    """Une phrase : changements, tests, dérive, auto-modification."""
    ch, tv, sc = r["changes"], r["tests"], r["infrastructure"]["supply"]
    n = len(ch.get("commits") or [])
    return (f"{n} derniers changements, {ch.get('traced', 0)} expliqués, {ch.get('ai', 0)} écrits avec une IA ; "
            f"{tv['functions']} tests ({fr(tv['coverage'] * 100, '.0f')} % des modules testés) ; "
            + ("aucune dérive des bibliothèques" if not sc.get("drift") else
               f"{len(sc['drift'])} bibliothèque(s) à une autre version que celle testée")
            + (" ; aucune auto-modification" if r["self_modification"]["ok"] else " ; AUTO-MODIFICATION DÉTECTÉE"))


def render(r: Dict[str, Any]) -> str:
    """docs/INGENIERIE_ETAT.md : changements, tests, chaîne, exploitation,
    auto-modification, qualité."""
    q, ch, tv, pl, inf = r["quality"], r["changes"], r["tests"], r["pipeline"], r["infrastructure"]
    lines = ["# Ingénierie et exploitation", "",
             f"Mesuré le {r['now'][:10]} par `python trendguard_bot.py ingenierie` (étapes 30 et 31 du prompt "
             "maître, [`INGENIERIE.md`](INGENIERIE.md)).", "",
             f"- **{q['status']}** ; note {fr(q['score'], '.0f')}/100" + (f" ; P0 en échec : {', '.join(q['p0'])}"
                                                                         if q["p0"] else "") + ".",
             f"- {describe(r)}."]
    lines += ["", "## Les derniers changements du code", ""]
    if ch.get("commits"):
        lines += ["| Version | date | changement | IA |", "| --- | --- | --- | --- |"]
        lines += [f"| `{c['commit']}` | {c['date'][:10]} | {c['subject'][:90].replace('|', '/')} | "
                  f"{'oui' if c['ai'] else 'non'} |" for c in ch["commits"][:10]]
        if ch.get("steps"):
            lines += ["", "Étapes du prompt maître retrouvées dans l'historique : "
                      + ", ".join(str(s) for s in ch["steps"]) + "."]
    else:
        lines.append(ch["detail"] + ".")
    lines += ["", "## Les tests", "",
              f"{tv['functions']} fonctions de test dans {tv['files']} fichiers ; {tv['modules'] - len(tv['untested'])} "
              f"modules sur {tv['modules']} importés par au moins un test (une mesure par module, pas par ligne)."]
    if tv["untested"]:
        lines.append("Sans test direct : " + ", ".join(f"`{m}`" for m in tv["untested"]) + ".")
    lines += ["", "## La chaîne de contrôle et la mise à jour", "",
              "| Contrôle GitHub | présent |", "| --- | --- |"]
    lines += [f"| {k} | {'oui' if v else 'non'} |" for k, v in pl["steps"].items()]
    lines += ["", f"Durée maximale : {pl['timeout']} minutes. Mise à jour sur ce PC : seule une Pull Request fusionnée "
              "par vous, aux contrôles au vert, en avance rapide, contrôle de démarrage, puis retour automatique à la "
              "version précédente en cas d'échec ; jamais en réel sans votre commande."]
    sm = r["self_modification"]
    lines += ["", "Commandes git qui écrivent, par fichier : " + ("; ".join(f"`{k}` ({', '.join(v)})"
                                                                       for k, v in sm["writes"].items()) or "aucune")
              + "." + ("" if sm["ok"] else " **Hors règle : " + "; ".join(sm["found"]) + ".**")]
    sc = inf["supply"]
    lines += ["", "## Exploitation de ce PC", "", f"- Python {inf['python']}, {inf['system']}"
              + (", environnement isolé du bot (.venv)" if inf["venv"] else ", sans environnement isolé") + ".",
              f"- Bibliothèques : {pl['pins']['pinned']} figées sur {pl['pins']['total']} ; {sc['detail']}."]
    if sc.get("drift"):
        lines.append("- Dérive (voulu ≠ observé) : " + ", ".join(sc["drift"]) + ".")
    lines += ["", "## Qualité", "", "| Famille | poids | état | preuve |", "| --- | --- | --- | --- |"]
    for k, (ok, d) in q["checks"].items():
        lines.append(f"| {WEIGHT_FR[k]} | {fr(WEIGHTS[k] * 100, '.0f')} % | {'conforme' if ok else 'NON CONFORME'} | {d} |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Commande `ingenierie` : l'ingénierie du moment ; écrit avec --out."""
    from .config import load_guard_config_from_env
    ap = argparse.ArgumentParser(description="Ingénierie et exploitation")
    ap.add_argument("--out", default="")
    args = ap.parse_args(list(argv) if argv is not None else None)
    v29.ensure_utf8_stdio()
    r = evaluate(load_guard_config_from_env())
    report_of(r)
    text = render(r)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(ts.format_markdown(text) + "\n")
    print(text)
    return 0 if r["quality"]["status"] == "READY" else 1
