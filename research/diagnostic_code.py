"""Diagnostic du code : mesures reproductibles de sa structure, en lecture
seule.

    python -m research.diagnostic_code

Taille des modules et des fonctions, complexité (nombre de branches), zones
de code répété, noms définis mais jamais utilisés, sens des dépendances
entre paquets. Ce sont les mesures de docs/DIAGNOSTIC_CODE.md ; les règles
qui en découlent sont vérifiées par tests/test_structure.py.
"""

from __future__ import annotations

import argparse
import ast
import collections
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import v29
from trendguard import trend_strategy as ts
from trendguard.texte import fr

PACKAGES = ("trendguard", "v29", "panel", "research")
ENTRY = "trendguard_bot.py"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LONG = 25                   # lignes : au-delà, une fonction publique a une docstring
WINDOW = 6                  # lignes identiques d'affilée pour parler de code répété…
MIN_CHARS = 160             # … assez longues pour ne pas être un hasard
BRANCHES = (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.BoolOp, ast.IfExp,
            ast.ExceptHandler, ast.comprehension, ast.AsyncFor, ast.AsyncWith)
TRIVIAL = {"try:", "else:", "finally:", ")", "]", "}", "return", "pass"}
# Appelées par Python, pytest ou le serveur HTTP, jamais par le code du dépôt.
HOOKS = {"main", "do_GET", "do_HEAD", "do_POST", "log_message", "formatTime",
         "pytest_runtest_logreport"}
FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _python_files(folder: str) -> List[str]:
    out = []
    for where, _dirs, names in os.walk(folder):
        if "__pycache__" not in where:
            out += [os.path.join(where, n) for n in names if n.endswith(".py")]
    return sorted(out)


def read_sources(root: str) -> Dict[str, str]:
    """Code Python du dépôt, hors tests : chemin relatif → source."""
    paths = [os.path.join(root, ENTRY)] if os.path.exists(os.path.join(root, ENTRY)) else []
    for pkg in PACKAGES:
        paths += _python_files(os.path.join(root, pkg))
    return {os.path.relpath(p, root).replace(os.sep, "/"): _read(p) for p in paths}


def functions(sources: Dict[str, str]) -> List[Dict[str, Any]]:
    """Chaque fonction : fichier, nom, ligne, longueur, nombre de branches
    (complexité) et présence d'une docstring."""
    out = []
    for rel, src in sources.items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, FUNCTION):
                out.append({"file": rel, "name": node.name, "line": node.lineno,
                            "lines": node.end_lineno - node.lineno + 1,
                            "branches": 1 + sum(isinstance(n, BRANCHES) for n in ast.walk(node)),
                            "doc": ast.get_docstring(node) is not None})
    return out


def duplicates(sources: Dict[str, str]) -> List[List[Tuple[str, int]]]:
    """Zones de code répété : WINDOW lignes identiques d'affilée (lignes
    vides, commentaires et ponctuation seule ignorés) à deux endroits au
    moins. Les emplacements voisins, à dix lignes près, font une seule zone."""
    seen: Dict[str, List[Tuple[str, int]]] = collections.defaultdict(list)
    for rel, src in sources.items():
        lines = [(n, re.sub(r"\s+", " ", text.strip()))
                 for n, text in enumerate(src.splitlines(), 1)]
        lines = [(n, t) for n, t in lines if t and not t.startswith("#") and t not in TRIVIAL]
        for k in range(len(lines) - WINDOW + 1):
            block = "\n".join(t for _n, t in lines[k:k + WINDOW])
            if len(block) >= MIN_CHARS:
                seen[block].append((rel, lines[k][0]))
    zones: Dict[Tuple[Tuple[str, int], ...], List[Tuple[str, int]]] = {}
    for places in seen.values():
        if len(set(places)) > 1:
            zones.setdefault(tuple(sorted({(f, n // 10) for f, n in places})),
                             sorted(set(places)))
    return sorted(zones.values())


def unused_names(sources: Dict[str, str], root: str) -> List[Tuple[str, str]]:
    """Fonctions et classes dont le nom n'apparaît qu'à sa définition, tests
    compris : code peut-être mort (HOOKS mis à part)."""
    corpus = list(sources.values()) + [_read(p) for p in _python_files(os.path.join(root, "tests"))]
    count = collections.Counter(w for text in corpus for w in re.findall(r"[A-Za-z_]\w*", text))
    out = []
    for rel, src in sources.items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, FUNCTION + (ast.ClassDef,)) and count[node.name] <= 1 \
                    and node.name not in HOOKS and not node.name.startswith("__"):
                out.append((node.name, rel))
    return sorted(out)


def dependencies(sources: Dict[str, str]) -> Dict[Tuple[str, str], int]:
    """Imports d'un paquet du projet vers un autre : (de, vers) → nombre de
    modules importés."""
    edges: Dict[Tuple[str, str], int] = collections.Counter()
    for rel, src in sources.items():
        origin = rel.split("/")[0].replace(".py", "")
        targets = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                targets.add(node.module)
            elif isinstance(node, ast.Import):
                targets.update(alias.name for alias in node.names)
        for target in targets:
            pkg = target.split(".")[0]
            if pkg in PACKAGES and pkg != origin:
                edges[(origin, pkg)] += 1
    return dict(sorted(edges.items()))


def measure(root: str = ROOT) -> Dict[str, Any]:
    """Toutes les mesures du dépôt `root`, prêtes à être comparées d'un
    diagnostic à l'autre."""
    sources = read_sources(root)
    funcs = functions(sources)
    sizes = {rel: src.count("\n") + 1 for rel, src in sources.items()}
    ours = [f for f in funcs if not f["file"].startswith("v29/")]
    return {
        "modules": len(sources), "lines": sum(sizes.values()), "functions": len(funcs),
        "largest": sorted(sizes.items(), key=lambda kv: -kv[1])[:8],
        "longest": sorted(funcs, key=lambda f: -f["lines"])[:8],
        "complex": sorted(funcs, key=lambda f: -f["branches"])[:8],
        "undocumented": [f for f in ours if f["lines"] >= LONG and not f["doc"]
                         and not f["name"].startswith("_")],
        "duplicates": duplicates(sources),
        "unused": unused_names(sources, root),
        "dependencies": dependencies(sources),
    }


def render(m: Dict[str, Any]) -> str:
    """Les mesures en Markdown."""
    def where(f: Dict[str, Any]) -> str:
        return f"`{f['file']}:{f['line']}` `{f['name']}`"
    lines = [
        "# Diagnostic du code — mesures", "",
        f"{m['modules']} modules Python, {fr(m['lines'], ',d')} lignes, "
        f"{fr(m['functions'], ',d')} fonctions (hors tests).", "",
        "## Plus gros modules", "", "| Module | Lignes |", "| --- | --- |",
        *[f"| `{rel}` | {fr(n, ',d')} |" for rel, n in m["largest"]], "",
        "## Fonctions les plus longues", "", "| Fonction | Lignes | Branches |",
        "| --- | --- | --- |",
        *[f"| {where(f)} | {f['lines']} | {f['branches']} |" for f in m["longest"]], "",
        "## Fonctions les plus complexes", "", "| Fonction | Branches | Lignes |",
        "| --- | --- | --- |",
        *[f"| {where(f)} | {f['branches']} | {f['lines']} |" for f in m["complex"]], "",
        f"## Fonctions publiques de {LONG} lignes ou plus sans docstring (hors v29)", "",
        *([f"- {where(f)}" for f in m["undocumented"]] or ["Aucune."]), "",
        "## Zones de code répété", "",
        *([f"- {' ; '.join(f'`{f}:{n}`' for f, n in zone[:4])}" for zone in m["duplicates"]]
          or ["Aucune."]), "",
        "## Noms définis, jamais utilisés", "",
        *([f"- `{name}` (`{rel}`)" for name, rel in m["unused"]] or ["Aucun."]), "",
        "## Dépendances entre paquets", "",
        *[f"- `{a}` → `{b}` : {n} module(s) importé(s)"
          for (a, b), n in m["dependencies"].items()],
    ]
    return ts.format_markdown("\n".join(lines))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Diagnostic du code TrendGuard (lecture seule)")
    ap.add_argument("--root", default=ROOT, help="dépôt à mesurer (défaut : celui-ci)")
    args = ap.parse_args(argv)
    v29.ensure_utf8_stdio()
    print(render(measure(args.root)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
