"""Règles de structure du code (docs/ARCHITECTURE.md), vérifiées à chaque
envoi : sens des dépendances, modules et fonctions longues documentés."""
import ast
import pathlib

from research import diagnostic_code as dc
from trendguard import trend_strategy as ts
from trendguard.texte import fr, fr_plain

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROJECT = {"trendguard", "panel", "research", "v29"}
# Qui a le droit d'importer qui (docs/ARCHITECTURE.md, « Dépendances »).
ALLOWED = {"trendguard": {"v29"}, "panel": {"trendguard", "v29"},
           "research": {"trendguard", "v29"}, "v29": set()}
LONG = 25                   # lignes : au-delà, une fonction publique a une docstring


def _modules(*packages):
    for pkg in packages:
        for path in sorted((ROOT / pkg).rglob("*.py")):
            yield pkg, path, ast.parse(path.read_text(encoding="utf-8"))


def _imported(tree):
    """(ligne, paquet du projet) pour chaque import absolu d'un module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module.split(".")[0]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name.split(".")[0]


def test_dependencies_go_one_way():
    """research et panel s'appuient sur trendguard, trendguard sur v29, jamais
    l'inverse. Seule exception : la ligne de commande lance le panneau."""
    wrong = []
    for pkg, path, tree in _modules(*ALLOWED):
        rel = path.relative_to(ROOT).as_posix()
        for line, target in _imported(tree):
            if target in PROJECT and target != pkg and target not in ALLOWED[pkg] \
                    and (rel, target) != ("trendguard/cli.py", "panel"):
                wrong.append(f"{rel}:{line} importe {target}")
    assert not wrong, wrong


def test_every_module_says_what_it_is_for():
    missing = [path.relative_to(ROOT).as_posix() for _pkg, path, tree in _modules(*ALLOWED)
               if ast.get_docstring(tree) is None]
    assert not missing, missing


def test_long_public_functions_are_documented():
    """Toute fonction publique de 25 lignes ou plus dit ce qu'elle fait (le
    moteur v29, stable, est laissé tel quel)."""
    missing = []
    for _pkg, path, tree in _modules("trendguard", "panel", "research"):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and not node.name.startswith("_") \
                    and node.end_lineno - node.lineno + 1 >= LONG \
                    and ast.get_docstring(node) is None:
                missing.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno} {node.name}")
    assert not missing, missing


def test_french_numbers_come_from_one_function():
    """Virgule décimale, espace des milliers, signe moins : trendguard.texte.fr,
    pas un remplacement local (le laboratoire garde le sien, qui écrit aussi
    « 0,00 » pour −0,00)."""
    local = []
    for _pkg, path, _tree in _modules("trendguard", "panel", "research"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in ("trendguard/texte.py", "trendguard/strategy_lab.py"):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(f'.replace("{a}", "{b}")' in line
                   for a, b in ((".", ","), (",", " "), ("-", "−"))):
                local.append(f"{rel}:{n}")
    assert not local, local


def test_french_number_writing():
    assert fr(1234.5, ",.1f") == "1 234,5" and fr(-0.5) == "−0,50" and fr(2.1, "+.1f") == "+2,1"
    # Écrit en entier : ni décimale inutile, ni puissance de dix, ni bruit des flottants.
    assert [fr_plain(x) for x in (0.00001, 84000.10, 5.0, -0.5, -1e-12, 353.70000000000005)] == [
        "0,00001", "84 000,1", "5", "−0,5", "0", "353,7"]


def test_code_diagnostic_finds_repeats_unused_names_and_imports(tmp_path):
    block = "\n".join(f"    total_{i} = compute_something(first_argument, second_argument, {i})"
                      for i in range(6))
    for pkg in ("trendguard", "research", "tests"):
        (tmp_path / pkg).mkdir()
    (tmp_path / "trendguard" / "a.py").write_text(
        f'"""A."""\nfrom research import b\n\n\ndef used():\n{block}\n\n\n'
        "def never_called():\n    return used()\n", encoding="utf-8")
    (tmp_path / "research" / "b.py").write_text(
        f'"""B."""\n\n\ndef other():\n{block}\n', encoding="utf-8")
    (tmp_path / "tests" / "test_b.py").write_text("from research.b import other\n",
                                                  encoding="utf-8")
    m = dc.measure(str(tmp_path))
    assert (m["modules"], m["functions"]) == (2, 3)
    assert m["dependencies"] == {("trendguard", "research"): 1}
    assert m["unused"] == [("never_called", "trendguard/a.py")]
    assert [sorted(f for f, _line in zone) for zone in m["duplicates"]] == [
        ["research/b.py", "trendguard/a.py"]]
    text = dc.render(m)
    assert "never_called" in text and ts.format_markdown(text) == text


def test_code_diagnostic_of_this_repository():
    """Le dépôt lui-même : rien d'inutilisé hors du moteur v29, et le bot
    n'importe pas les études."""
    m = dc.measure(str(ROOT))
    assert m["modules"] > 50 and not m["undocumented"]
    assert ("trendguard", "research") not in m["dependencies"]
    unused = [f"{name} ({rel})" for name, rel in m["unused"] if not rel.startswith("v29/")]
    assert not unused, f"jamais utilisés (à retirer, ou à ajouter à HOOKS) : {unused}"
