"""The app's own source, read with Python's ast module by tests that check
how it is written (test_locking.py, test_history.py, test_write_owners.py).
Nothing of it runs in the app."""

import ast
import re
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Literal, NamedTuple

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


# Writes, as the source shows them: one detector for test_history.py
# (statements skip the flush, so history) and test_write_owners.py (who may
# make, write or delete a row of which table).


type How = Literal["statement", "row", "delete"]


class Write(NamedTuple):
    """A line that writes, the table it writes (None where the source
    cannot tell, such as a model in a variable), and how: a statement,
    which skips the flush; a row made (Item(...)); or a row deleted
    (session.delete(row))."""

    line: int
    table: str | None
    how: How


DML = {"insert", "update", "delete"}
BULK = {"bulk_save_objects", "bulk_insert_mappings", "bulk_update_mappings"}
# Either case: SQL is as often written lower. Prose such as "update the
# item" names no table, as "the" is none.
SQL_WRITE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO|TRUNCATE|COPY)\s+\"?(\w+)",
    re.IGNORECASE,
)


def _model(node: ast.expr | None) -> str | None:
    """The table `node` names: Item, models.Item or Item.__table__."""
    match node:
        case ast.Attribute(value=model, attr="__table__"):
            return _model(model)
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return MODELS.get(name)
    return None


def _imported(tree: ast.AST) -> tuple[set[str], set[str]]:
    """The names insert, update and delete from SQLAlchemy go by here, and
    the names SQLAlchemy's modules go by."""
    functions: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "sqlalchemy"
        ):
            functions |= {a.asname or a.name for a in node.names if a.name in DML}
        elif isinstance(node, ast.Import):
            modules |= {
                a.asname or a.name.split(".")[0]
                for a in node.names
                if a.name.startswith("sqlalchemy")
            }
    return functions, modules


def _queried(owner: ast.expr) -> ast.Call | None:
    """The session.query(...) call a chain such as
    session.query(Item).filter_by(id=1) starts from, if any."""
    while isinstance(owner, ast.Call | ast.Attribute):
        if isinstance(owner, ast.Call):
            if isinstance(owner.func, ast.Attribute) and owner.func.attr == "query":
                return owner
            owner = owner.func
        else:
            owner = owner.value
    return None


def _call(node: ast.Call, functions: set[str], modules: set[str]) -> list[Write]:
    called = node.func
    first = node.args[0] if node.args else None
    line = node.lineno
    match called:
        # update(Item), or sa.update(Item), however imported.
        case ast.Name(id=name) if name in functions:
            return [Write(line, _model(first), "statement")]
        case ast.Attribute(value=ast.Name(id=module), attr=name) if (
            name in DML and module in modules
        ):
            return [Write(line, _model(first), "statement")]
        # Item.__table__.update().
        case ast.Attribute(
            value=ast.Attribute(attr="__table__") as table, attr=name
        ) if name in DML:
            return [Write(line, _model(table), "statement")]
        # session.query(Item)...update(): the legacy Query's statements.
        case ast.Attribute(value=owner, attr=name) if name in DML and (
            query := _queried(owner)
        ):
            target = query.args[0] if query.args else None
            return [Write(line, _model(target), "statement")]
        case ast.Attribute(attr="delete") if len(node.args) == 1:
            return [Write(line, None, "delete")]
    if (table := _model(called)) is not None:
        return [Write(line, table, "row")]
    return []


def _docstrings(tree: ast.AST) -> set[int]:
    """The ids of the docstrings' nodes, which are prose, even where they
    quote SQL such as "UPDATE movement"."""
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def writes(tree: ast.AST) -> list[Write]:
    """Every write the source shows, by line."""
    functions, modules = _imported(tree)
    prose = _docstrings(tree)
    found: list[Write] = []
    for node in ast.walk(tree):
        if id(node) in prose:
            continue
        if isinstance(node, ast.Call):
            found += _call(node, functions, modules)
        # session.bulk_save_objects() and the like.
        elif isinstance(node, ast.Attribute) and node.attr in BULK:
            found.append(Write(node.lineno, None, "statement"))
        # SQL in a string.
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found += [
                Write(node.lineno, table, "statement")
                for table in SQL_WRITE.findall(node.value)
                if table in models.Base.metadata.tables
            ]
    return sorted(set(found), key=lambda w: (w.line, w.table or "", w.how))
