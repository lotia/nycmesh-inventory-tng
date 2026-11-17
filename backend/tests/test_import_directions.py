"""Which way the app's modules may import one another (docs/decisions/0023),
read from its source with Python's ast module (nothing of it runs in the
app; helpers.source). Imports inside functions count, as do relative ones
and a module imported by name from its package (from inventory_tng import
ledger).

Today's breaks are listed in BREAKS, which may only shrink: a new break
fails, and so does one fixed but still listed. Rules naming folders that do
not exist yet (a feature's package, routes.py, queries.py, command.py,
manage.py) apply as they appear.
"""

import ast
from collections import Counter
from collections.abc import Callable, Collection
from pathlib import Path

import pytest

from inventory_tng import models

from helpers.source import SOURCE, modules

# core/ is plumbing, not business rules. Of the app outside core, it may
# import only the models, kept whole (0023).
CORE_MAY_IMPORT = {"models.py"}
# What every server-rendered page shares, until it moves into web/.
WEB = {"pages.py"}
# Rules: each table's owner (db.protection), any queries.py, and these,
# which hold rules or queries but own no table.
RULES = {
    "ledger.py",
    "names.py",
    "scan_settings.py",
    "stock/balance.py",
    "unit_readings.py",
    "units.py",
}
# What of web/ rules may import: it loads neither FastAPI nor the templates,
# and is held to the rules' own rule to stay so.
RULES_MAY_IMPORT = {"web/formatting.py"}
# Folders that are not features.
NOT_FEATURES = {"core", "dev", "web"}
# Until its modules move into their features' packages, each module in
# admin/ is a feature of its own.
SPLIT = "admin"
OWNERS = {
    table.info["owner"]
    for table in models.Base.metadata.tables.values()
    if table.info["owner"] is not None
}
FASTAPI = ("fastapi", "starlette")
ROUTE_NAMES = {"routes.py", "api.py"}
# Calls that make or run a query: select(...), or session.execute and the
# like.
QUERY_CALLS = {"select", "execute", "scalar", "scalars"}
MANAGE = "manage.py"

# What one module breaks a rule with, given its tree, its path under SOURCE
# and the app's modules by that path.
type Check = Callable[[ast.Module, str, Collection[str]], list[str]]

# Today's breaks, by rule: each module that breaks it, and what with.
BREAKS: dict[str, dict[str, list[str]]] = {
    "core_imports_a_feature": {},
    "rules_import_fastapi_or_web": {
        "once.py": ["fastapi", "pages.py", "starlette"],
    },
    "routes_run_queries": {
        "admin/admins.py": ["_list_page: 2"],
        "admin/categories.py": ["_category_page: 2", "_items: 2", "_list_page: 2"],
        "admin/counts.py": [
            "Shown: 2",
            "_by_form: 2",
            "_count_page: 2",
            "_counted: 2",
            "_counts_page: 5",
            "_done: 1",
            "_followed: 2",
            "_forms: 4",
            "_recent: 3",
            "count_form: 2",
        ],
        "admin/devices.py": ["_list_page: 4", "device_list: 1"],
        "admin/home.py": ["home: 2"],
        "admin/items.py": [
            "<module>: 1",
            "_categories: 2",
            "_in_stock: 2",
            "_item_page: 2",
        ],
        "admin/labels.py": ["<module>: 1", "list_labels: 2"],
        "admin/length_checks.py": [
            "_answer: 2",
            "_checks_page: 4",
            "answer_ticked: 2",
            "recent_cuts: 3",
        ],
        "admin/locations.py": ["_list_page: 4", "_location_page: 2"],
        "admin/over_recorded.py": ["answer: 2"],
        "admin/reading_rules.py": ["list_readings: 2"],
        "admin/settings.py": ["_page: 2"],
        "admin/stock.py": ["_holdings: 2", "reorder: 5", "stock: 4"],
        "admin/used_up.py": ["_answer: 2"],
        "admin/volunteers.py": ["_list_page: 4", "_volunteer_page: 2"],
        "health.py": ["healthz: 1"],
        "scan/catalogue_api.py": ["catalogue: 12", "open_to_take: 2", "saved_of: 2"],
    },
    "routes_import_another_features_routes": {
        "admin/__init__.py": [
            "admin/admins.py",
            "admin/categories.py",
            "admin/counts.py",
            "admin/devices.py",
            "admin/home.py",
            "admin/items.py",
            "admin/labels.py",
            "admin/length_checks.py",
            "admin/locations.py",
            "admin/over_recorded.py",
            "admin/reading_rules.py",
            "admin/settings.py",
            "admin/stock.py",
            "admin/used_up.py",
            "admin/volunteers.py",
        ],
        "admin/counts.py": ["admin/items.py", "admin/settings.py"],
        "admin/home.py": [
            "admin/categories.py",
            "admin/counts.py",
            "admin/items.py",
            "admin/locations.py",
            "admin/volunteers.py",
        ],
        "admin/items.py": ["admin/categories.py", "admin/reading_rules.py"],
        "admin/labels.py": ["admin/items.py", "admin/locations.py"],
        "admin/length_checks.py": ["admin/stock.py", "admin/used_up.py"],
        "admin/over_recorded.py": [
            "admin/counts.py",
            "admin/locations.py",
            "admin/stock.py",
        ],
        "admin/stock.py": ["admin/counts.py", "admin/items.py"],
        "admin/used_up.py": ["admin/items.py", "admin/stock.py"],
        "security_headers.py": ["page_telemetry.py"],
    },
    "init_not_empty": {
        "admin/__init__.py": ["Assign", "For", "ImportFrom"],
    },
    "command_imported_outside_manage": {},
}


def _module_at(parts: list[str], known: Collection[str]) -> str | None:
    """The module, by its path, that dotted `parts` under inventory_tng
    name: a package's __init__.py, or a file."""
    for path in ("/".join([*parts, "__init__.py"]), "/".join(parts) + ".py"):
        if path in known:
            return path
    return None


def imported(tree: ast.Module, path: str, known: Collection[str]) -> list[str]:
    """The app's modules, by path, that the module at `path` imports,
    anywhere in it."""
    package = path.split("/")[:-1]
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "inventory_tng" and (
                    module := _module_at(parts[1:], known)
                ):
                    found.append(module)
        elif isinstance(node, ast.ImportFrom):
            named: list[str] = node.module.split(".") if node.module else []
            if node.level > len(package) + 1:
                continue  # Above inventory_tng, so none of the app's.
            if node.level:
                base = package[: len(package) - (node.level - 1)] + named
            elif named[:1] == ["inventory_tng"]:
                base = named[1:]
            else:
                continue
            for alias in node.names:
                # A module imported by name from its package, or a name
                # from a module.
                module = _module_at([*base, alias.name], known) or _module_at(
                    base, known
                )
                if module:
                    found.append(module)
    return sorted(set(found))


def is_core(path: str) -> bool:
    return path.startswith("core/")


def is_web(path: str) -> bool:
    return path in WEB or path.startswith("web/")


def is_rules(path: str) -> bool:
    return (
        path in OWNERS
        or path in RULES
        or path in RULES_MAY_IMPORT
        or Path(path).name == "queries.py"
    )


def _called(node: ast.AST) -> str | None:
    """The name a call calls, bare or as a method: f(...) or x.f(...)."""
    match node:
        case ast.Call(func=ast.Name(id=name) | ast.Attribute(attr=name)):
            return name
    return None


def is_routes(tree: ast.Module, path: str) -> bool:
    """A module of routes: named for them, or making an APIRouter."""
    name = path.split("/")[-1]
    return (
        name in ROUTE_NAMES
        or name.endswith(("_routes.py", "_api.py"))
        or any(_called(node) == "APIRouter" for node in ast.walk(tree))
    )


def feature(path: str) -> str:
    """The feature a module belongs to: its package's, or its own at the
    top or in admin/."""
    parts = path.split("/")
    if len(parts) == 1 or parts[0] == SPLIT:
        return path.removesuffix(".py")
    return parts[0]


def _tree(path: str) -> ast.Module:
    return ast.parse((SOURCE / path).read_text(), path)


def core_imports_a_feature(
    tree: ast.Module, path: str, known: Collection[str]
) -> list[str]:
    """What core imports besides core and the models: features, web/ and
    dev/ alike."""
    if not is_core(path):
        return []
    return [
        module
        for module in imported(tree, path, known)
        if not is_core(module) and module not in CORE_MAY_IMPORT
    ]


def rules_import_fastapi_or_web(
    tree: ast.Module, path: str, known: Collection[str]
) -> list[str]:
    if not is_rules(path):
        return []
    found = [
        module
        for module in imported(tree, path, known)
        if is_web(module) and module not in RULES_MAY_IMPORT
    ]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names = [node.module or ""]
        else:
            continue
        found += [top for name in names if (top := name.split(".")[0]) in FASTAPI]
    return sorted(set(found))


def routes_run_queries(
    tree: ast.Module, path: str, _known: Collection[str]
) -> list[str]:
    """The query calls in a module of routes (QUERY_CALLS), counted by the
    top-level function (or class) they are in, so session.scalars(select(X))
    counts 2. A session.get may stay."""
    if not is_routes(tree, path):
        return []
    found: Counter[str] = Counter()
    for statement in tree.body:
        name = getattr(statement, "name", "<module>")
        for node in ast.walk(statement):
            if _called(node) in QUERY_CALLS:
                found[name] += 1
    return [f"{name}: {count}" for name, count in sorted(found.items())]


def routes_import_another_features_routes(
    tree: ast.Module, path: str, known: Collection[str]
) -> list[str]:
    """Links between features go through paths.py instead."""
    if not is_routes(tree, path):
        return []
    return [
        module
        for module in imported(tree, path, known)
        if feature(module) != feature(path) and is_routes(_tree(module), module)
    ]


def init_not_empty(tree: ast.Module, path: str, _known: Collection[str]) -> list[str]:
    """What a package's __init__.py holds besides its docstring."""
    if not path.endswith("__init__.py"):
        return []
    body = tree.body
    if ast.get_docstring(tree, clean=False) is not None:
        body = body[1:]
    return sorted({type(statement).__name__ for statement in body})


def command_imported_outside_manage(
    tree: ast.Module, path: str, known: Collection[str]
) -> list[str]:
    """A feature's command.py, which only the entry point imports."""
    if path == MANAGE:
        return []
    return [
        module
        for module in imported(tree, path, known)
        if module.endswith("/command.py") and module.split("/")[0] not in NOT_FEATURES
    ]


CHECKS: dict[str, Check] = {
    check.__name__: check
    for check in (
        core_imports_a_feature,
        rules_import_fastapi_or_web,
        routes_run_queries,
        routes_import_another_features_routes,
        init_not_empty,
        command_imported_outside_manage,
    )
}


def found(check: Check) -> dict[str, list[str]]:
    """`check`'s findings in every module of the app, by path."""
    known = {str(path.relative_to(SOURCE)) for path in modules()}
    return {
        path: what
        for path in sorted(known)
        if (what := check(_tree(path), path, known))
    }


def _left_out(
    these: dict[str, list[str]], those: dict[str, list[str]]
) -> dict[str, list[str]]:
    """The breaks in `these` that `those` does not have, by module."""
    return {
        path: missing
        for path, what in these.items()
        if (missing := [w for w in what if w not in those.get(path, [])])
    }


@pytest.mark.parametrize("rule", CHECKS)
def test_the_app_breaks_no_import_rule_but_those_listed(rule: str) -> None:
    now = found(CHECKS[rule])
    listed = BREAKS[rule]
    # Compared break by break, so one of a module's breaks fixed shows as
    # fixed, not as the module's list being new. A changed count shows as
    # both: the old one fixed, the new one new.
    new = _left_out(now, listed)
    fixed = _left_out(listed, now)
    assert (new, fixed) == ({}, {}), (
        f"{rule} (docs/decisions/0023): put right what is new, not in BREAKS, "
        f"and take out of BREAKS what is fixed. New: {new}. Fixed: {fixed}."
    )


def test_breaks_are_compared_one_by_one() -> None:
    listed = {"devices.py": ["fastapi", "web.py"], "admin/items.py": ["_page: 4"]}
    now = {"devices.py": ["fastapi"], "admin/items.py": ["_page: 3"]}
    assert _left_out(now, listed) == {"admin/items.py": ["_page: 3"]}
    assert _left_out(listed, now) == {
        "devices.py": ["web.py"],
        "admin/items.py": ["_page: 4"],
    }


def test_every_rule_has_its_breaks_listed() -> None:
    assert set(BREAKS) == set(CHECKS)


# Each rule, shown a break, finds it: modules by path, those named here
# existing as far as imports go.
KNOWN = {
    "__init__.py",
    "admin/__init__.py",
    "admin/items.py",
    "catalogue/__init__.py",
    "catalogue/items.py",
    "catalogue/queries.py",
    "catalogue/routes.py",
    "catalogue/command.py",
    "core/__init__.py",
    "core/db.py",
    "core/settings.py",
    "ledger.py",
    "manage.py",
    "models.py",
    "pages.py",
    "stock/__init__.py",
    "stock/api.py",
    "stock/ledger.py",
    "web/__init__.py",
    "web/formatting.py",
}


@pytest.mark.parametrize(
    ("path", "source", "breaks"),
    [
        ("core/db.py", "from inventory_tng import ledger", ["ledger.py"]),
        ("core/db.py", "def f():\n    from .. import ledger", ["ledger.py"]),
        ("core/db.py", "from ..catalogue.items import x", ["catalogue/items.py"]),
        ("core/db.py", "import inventory_tng.stock.ledger", ["stock/ledger.py"]),
        ("core/db.py", "from inventory_tng import models", []),
        ("core/db.py", "from inventory_tng.core import settings", []),
        ("core/db.py", "from . import db, settings", []),
        ("ledger.py", "from inventory_tng.core import settings", []),
        # Above inventory_tng, so none of the app's.
        ("core/db.py", "from ... import ledger", []),
    ],
)
def test_core_importing_a_feature_is_found(
    path: str, source: str, breaks: list[str]
) -> None:
    assert core_imports_a_feature(ast.parse(source), path, KNOWN) == breaks


@pytest.mark.parametrize(
    ("path", "source", "breaks"),
    [
        ("ledger.py", "from fastapi import Request", ["fastapi"]),
        ("ledger.py", "def f():\n    import starlette.requests", ["starlette"]),
        ("catalogue/queries.py", "from inventory_tng import pages", ["pages.py"]),
        ("catalogue/queries.py", "from ..web.formatting import x", []),
        ("ledger.py", "from inventory_tng.web import formatting", []),
        ("ledger.py", "from inventory_tng.core import db", []),
        # Neither rules nor queries.
        ("catalogue/routes.py", "from fastapi import APIRouter", []),
        # What of web/ rules may import is held to their rule itself.
        ("web/formatting.py", "from fastapi import Request", ["fastapi"]),
        ("web/formatting.py", "from inventory_tng import pages", ["pages.py"]),
    ],
)
def test_rules_importing_fastapi_or_web_is_found(
    path: str, source: str, breaks: list[str]
) -> None:
    assert rules_import_fastapi_or_web(ast.parse(source), path, KNOWN) == breaks


def test_a_tables_owner_is_rules() -> None:
    assert is_rules("items.py")
    assert not is_rules("admin/items.py")


@pytest.mark.parametrize(
    ("path", "source", "breaks"),
    [
        ("catalogue/routes.py", "def page():\n    select(Item)", ["page: 1"]),
        (
            "stock/api.py",
            "def save(s):\n    s.execute(x)\n    s.scalar(y)",
            ["save: 2"],
        ),
        (
            "admin/items.py",
            "router = APIRouter()\nasync def page(s):\n    s.scalars(sa.select(Item))",
            ["page: 2"],
        ),
        ("catalogue/routes.py", "def page(s):\n    s.get(Item, 1)", []),
        # Not a module of routes.
        ("catalogue/queries.py", "def total(s):\n    s.scalar(select(Item))", []),
    ],
)
def test_routes_running_queries_is_found(
    path: str, source: str, breaks: list[str]
) -> None:
    assert routes_run_queries(ast.parse(source), path, KNOWN) == breaks


def test_another_features_routes_imported_by_routes_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether the module imported holds routes is read from its source."""
    files = {
        "catalogue/routes.py": "",
        "stock/api.py": "",
        "admin/items.py": "router = APIRouter()",
        "admin/counts.py": "router = APIRouter()",
        "admin/listing.py": "",
        "catalogue/paths.py": "",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text(text)
    monkeypatch.setattr(f"{__name__}.SOURCE", tmp_path)

    def breaks(path: str, source: str) -> list[str]:
        return routes_import_another_features_routes(ast.parse(source), path, files)

    assert breaks("stock/api.py", "from ..catalogue import routes") == [
        "catalogue/routes.py"
    ]
    # admin/'s modules hold routes as they make an APIRouter.
    router = "router = APIRouter()\n"
    assert breaks(
        "admin/counts.py", f"{router}from inventory_tng.admin.items import X"
    ) == ["admin/items.py"]
    assert breaks("stock/api.py", "from inventory_tng.catalogue.paths import X") == []
    assert breaks("admin/counts.py", f"{router}from .listing import Sort") == []
    assert breaks("catalogue/routes.py", "from . import routes") == []
    # Not a module of routes.
    assert breaks("catalogue/paths.py", "from ..stock import api") == []


@pytest.mark.parametrize(
    ("path", "source", "breaks"),
    [
        ("web/__init__.py", "from . import formatting", ["ImportFrom"]),
        ("admin/__init__.py", '"""Pages."""\nSECTIONS = []', ["Assign"]),
        ("web/__init__.py", '"""Shared."""', []),
        ("web/__init__.py", "", []),
        ("web/__init__.py", "0", ["Expr"]),
        ("web/formatting.py", "X = 1", []),
    ],
)
def test_an_init_not_empty_is_found(path: str, source: str, breaks: list[str]) -> None:
    assert init_not_empty(ast.parse(source), path, KNOWN) == breaks


@pytest.mark.parametrize(
    ("path", "source", "breaks"),
    [
        (
            "stock/api.py",
            "from inventory_tng.catalogue import command",
            ["catalogue/command.py"],
        ),
        (
            "ledger.py",
            "def f():\n    from .catalogue.command import main",
            ["catalogue/command.py"],
        ),
        ("manage.py", "from inventory_tng.catalogue import command", []),
        # core/command.py, what every command shares.
        ("ledger.py", "from inventory_tng.core.command import run_command", []),
        ("ledger.py", "from inventory_tng.dev import command", []),
    ],
)
def test_a_command_imported_outside_manage_is_found(
    path: str, source: str, breaks: list[str]
) -> None:
    known = {*KNOWN, "core/command.py", "dev/__init__.py", "dev/command.py"}
    assert command_imported_outside_manage(ast.parse(source), path, known) == breaks
