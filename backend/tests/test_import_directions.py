"""Which way the app's modules may import one another (docs/decisions/0023),
read from its source with Python's ast module (nothing of it runs in the
app; helpers.source). Imports inside functions count, as do relative ones
and a module imported by name from its package (from inventory_tng import
ledger).

Today's breaks are listed in BREAKS, which may only shrink: a new break
fails, and so does one fixed but still listed. Rules naming folders that do
not exist yet (core/, web/, a feature's package, routes.py, queries.py,
command.py, manage.py) apply as they appear.
"""

import ast
from collections import Counter
from collections.abc import Callable, Collection
from pathlib import Path

import pytest

from inventory_tng import models

from helpers.source import SOURCE, modules

# Core is plumbing, not business rules: core/, and until they move there,
# these modules at the top of the package.
CORE = {
    "command.py",
    "database_role.py",
    "db.py",
    "history.py",
    "local_time.py",
    "logs.py",
    "mail.py",
    "masking.py",
    "phrases.py",
    "rate_limits.py",
    "request_logging.py",
    "settings.py",
    "telemetry.py",
    "time_zones.py",
}
# What core may import besides core: the models, kept whole (0023).
CORE_MAY_IMPORT = {"models.py"}
# What every server-rendered page shares, until it moves into web/.
WEB = {"pages.py", "web.py"}
# Rules: each table's owner (db.protection), any queries.py, and these,
# which hold rules but own no table.
RULES = {"ledger.py"}
# Folders that are not features.
NOT_FEATURES = {"core", "web"}
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
QUERY_CALLS = {"execute", "scalar", "scalars"}
MANAGE = "manage.py"

# What one module breaks a rule with, given its tree, its path under SOURCE
# and the app's modules by that path.
type Check = Callable[[ast.Module, str, Collection[str]], list[str]]

# Today's breaks, by rule: each module that breaks it, and what with.
BREAKS: dict[str, dict[str, list[str]]] = {
    "core_imports_a_feature": {
        "command.py": ["local_env.py"],
        "settings.py": ["local_env.py"],
    },
    "rules_import_fastapi_or_web": {
        "devices.py": ["fastapi", "web.py"],
        "labels.py": ["pages.py"],
        "ledger.py": ["fastapi"],
        "once.py": ["fastapi", "pages.py", "starlette"],
        "over_recorded.py": ["pages.py"],
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
        "catalogue.py": ["catalogue: 12", "open_to_take: 2", "saved_of: 2"],
        "devices.py": [
            "_current_among: 1",
            "_usable_code: 2",
            "current_among: 1",
            "current_device: 2",
            "devices_registered_with: 2",
            "find_devices: 1",
            "revoke_devices: 1",
            "revoke_every_code: 2",
        ],
        "health.py": ["healthz: 1"],
        "ledger.py": [
            "_first_sent: 2",
            "_flows: 2",
            "_form_flows: 2",
            "_hold_forms: 6",
            "_hold_places: 2",
            "_into_open: 2",
            "_keyed: 2",
            "_keys_of: 2",
            "_receive: 2",
            "_replay: 4",
            "_retired: 1",
            "_retired_left: 1",
            "_sides: 1",
            "_sizes: 2",
            "_summed: 1",
            "expected: 2",
            "hold: 2",
            "in_stores: 2",
            "insert_transaction: 2",
            "left_on: 2",
            "moment: 2",
            "retired_holding_stock: 1",
            "retired_holding_stock_count: 2",
            "sealed_places: 2",
        ],
    },
    "routes_import_another_features_routes": {
        "account.py": ["sign_in.py"],
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
        "admin/admins.py": ["sign_in.py"],
        "admin/categories.py": ["sign_in.py"],
        "admin/counts.py": [
            "admin/items.py",
            "admin/settings.py",
            "ledger.py",
            "sign_in.py",
        ],
        "admin/devices.py": ["devices.py", "sign_in.py"],
        "admin/home.py": [
            "admin/categories.py",
            "admin/counts.py",
            "admin/items.py",
            "admin/locations.py",
            "admin/volunteers.py",
            "ledger.py",
            "sign_in.py",
        ],
        "admin/items.py": [
            "admin/categories.py",
            "admin/reading_rules.py",
            "ledger.py",
            "sign_in.py",
        ],
        "admin/labels.py": ["admin/items.py", "admin/locations.py", "sign_in.py"],
        "admin/length_checks.py": [
            "admin/stock.py",
            "admin/used_up.py",
            "ledger.py",
            "sign_in.py",
        ],
        "admin/locations.py": ["sign_in.py"],
        "admin/over_recorded.py": [
            "admin/counts.py",
            "admin/locations.py",
            "admin/stock.py",
            "ledger.py",
            "sign_in.py",
        ],
        "admin/reading_rules.py": ["sign_in.py"],
        "admin/settings.py": ["sign_in.py"],
        "admin/stock.py": [
            "admin/counts.py",
            "admin/items.py",
            "ledger.py",
            "sign_in.py",
        ],
        "admin/used_up.py": [
            "admin/items.py",
            "admin/stock.py",
            "ledger.py",
            "sign_in.py",
        ],
        "admin/volunteers.py": ["sign_in.py"],
        "catalogue.py": ["devices.py", "ledger.py"],
        "ledger.py": ["devices.py"],
        "page_telemetry.py": ["devices.py"],
        "resets.py": ["sign_in.py"],
        "security_headers.py": ["devices.py", "page_telemetry.py"],
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
    return path in CORE or path.startswith("core/")


def is_web(path: str) -> bool:
    return path in WEB or path.startswith("web/")


def is_rules(path: str) -> bool:
    return path in OWNERS or path in RULES or Path(path).name == "queries.py"


def is_routes(tree: ast.Module, path: str) -> bool:
    """A module of routes: named for them, or making an APIRouter."""
    name = path.split("/")[-1]
    return (
        name in ROUTE_NAMES
        or name.endswith(("_routes.py", "_api.py"))
        or any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name | ast.Attribute)
            and getattr(node.func, "id", getattr(node.func, "attr", None))
            == "APIRouter"
            for node in ast.walk(tree)
        )
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
    found = [module for module in imported(tree, path, known) if is_web(module)]
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
    """The queries a module of routes runs, counted by the top-level
    function (or class) they are in: select(...), or session.execute,
    .scalar or .scalars. A session.get may stay."""
    if not is_routes(tree, path):
        return []
    found: Counter[str] = Counter()
    for statement in tree.body:
        name = getattr(statement, "name", "<module>")
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "select")
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"select", *QUERY_CALLS}
                )
            ):
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
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
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


@pytest.mark.parametrize("rule", CHECKS)
def test_the_app_breaks_no_import_rule_but_those_listed(rule: str) -> None:
    now = found(CHECKS[rule])
    listed = BREAKS[rule]
    new = {path: what for path, what in now.items() if listed.get(path) != what}
    assert new == {}, (
        f"{rule} (docs/decisions/0023): put these right, not in BREAKS: {new}"
    )
    fixed = {path: what for path, what in listed.items() if now.get(path) != what}
    assert fixed == {}, f"{rule}: fixed, so take them out of BREAKS: {fixed}"


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
    "db.py",
    "ledger.py",
    "manage.py",
    "models.py",
    "pages.py",
    "settings.py",
    "stock/__init__.py",
    "stock/api.py",
    "stock/ledger.py",
    "web/__init__.py",
    "web/formatting.py",
}


@pytest.mark.parametrize(
    ("path", "source", "breaks"),
    [
        ("db.py", "from inventory_tng import ledger", ["ledger.py"]),
        ("db.py", "def f():\n    from . import ledger", ["ledger.py"]),
        ("core/db.py", "from ..catalogue.items import x", ["catalogue/items.py"]),
        ("core/db.py", "import inventory_tng.stock.ledger", ["stock/ledger.py"]),
        ("db.py", "from inventory_tng import settings, models", []),
        ("core/db.py", "from . import db\nfrom .. import settings", []),
        ("ledger.py", "from inventory_tng import settings", []),
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
        (
            "catalogue/queries.py",
            "from ..web.formatting import x",
            ["web/formatting.py"],
        ),
        (
            "ledger.py",
            "from inventory_tng.web import formatting",
            ["web/formatting.py"],
        ),
        ("ledger.py", "from inventory_tng import db", []),
        # Neither rules nor queries.
        ("catalogue/routes.py", "from fastapi import APIRouter", []),
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
        # Today's command.py, what every command shares.
        ("ledger.py", "from inventory_tng.command import run_command", []),
    ],
)
def test_a_command_imported_outside_manage_is_found(
    path: str, source: str, breaks: list[str]
) -> None:
    known = {*KNOWN, "command.py"}
    assert command_imported_outside_manage(ast.parse(source), path, known) == breaks
