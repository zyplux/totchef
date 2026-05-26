"""Repo hygiene: function and method docstrings stay a single line. Module and
class docstrings may carry longer architecture overviews, so they are exempt."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNED_DIRS = (REPO_ROOT / "src", REPO_ROOT / "tests")


def find_multiline_function_docstrings(path: Path) -> list[str]:
    offenders: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        docstring = ast.get_docstring(node, clean=False)
        if docstring and "\n" in docstring.strip():
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} {node.name}")
    return offenders


def test_function_docstrings_are_single_line():
    offenders = [offender for directory in SCANNED_DIRS for path in sorted(directory.rglob("*.py")) for offender in find_multiline_function_docstrings(path)]
    assert not offenders, "condense these to one line:\n" + "\n".join(offenders)
