"""Tests share plain names through tests/helpers/, never conftest.py, which
pytest loads as a plugin, nor one another (inventory-tng-8i4.56)."""

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).parent


def shared_from(source: str) -> set[str]:
    """The modules `source` imports that only pytest should load."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        found |= {n for n in names if n == "conftest" or n.startswith("test_")}
    return found


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("from conftest import SentMail", {"conftest"}),
        ("from helpers.rows import add\nimport databases", set()),
    ],
)
def test_imports_of_what_only_pytest_loads_are_found(
    source: str, found: set[str]
) -> None:
    assert shared_from(source) == found


def test_no_test_imports_conftest_or_another_test() -> None:
    offending = {
        path.name: shared
        for path in TESTS.rglob("*.py")
        if (shared := shared_from(path.read_text()))
    }
    assert offending == {}
