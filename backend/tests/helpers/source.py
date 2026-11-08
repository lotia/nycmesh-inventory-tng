"""The app's own source, read with Python's ast module by tests that check
how it is written (test_locking.py, test_history.py, test_write_owners.py).
Nothing of it runs in the app."""

import ast
from collections.abc import Callable, Collection, Mapping
from pathlib import Path

import inventory_tng
from inventory_tng import models

SOURCE = Path(inventory_tng.__file__).parent
# Each model's class name, and its table's name.
MODELS = {
    mapper.class_.__name__: mapper.class_.__tablename__
    for mapper in models.Base.registry.mappers
}

# What a check finds in a module, given its path: the lines that break its
# rule.
type Check = Callable[[ast.Module, Path], list[int]]
# Where a check's rule does not apply: functions by name, in one module
# each, keyed by the module's path.
type Allowed = Mapping[Path, Collection[str]]


def outside(path: Path, check: Check, allowed: Allowed) -> list[int]:
    """`check`'s lines in the module at `path`, but for those inside the
    module's own top-level functions that `allowed` names for that very file
    (by its path, not its name). A nested function, or one of that name in
    another file, is no exception."""
    tree = ast.parse(path.read_text(), str(path))
    names = next((n for p, n in allowed.items() if p.resolve() == path.resolve()), ())
    inside = {
        line
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
        for line in range(node.lineno, (node.end_lineno or node.lineno) + 1)
    }
    return [line for line in check(tree, path) if line not in inside]


def modules() -> list[Path]:
    """Every module of the app."""
    return sorted(SOURCE.rglob("*.py"))


def offending(check: Check, allowed: Allowed) -> dict[str, list[int]]:
    """`outside` for every module of the app, by its path under SOURCE; those
    with nothing found left out."""
    return {
        str(path.relative_to(SOURCE)): lines
        for path in modules()
        if (lines := outside(path, check, allowed))
    }
