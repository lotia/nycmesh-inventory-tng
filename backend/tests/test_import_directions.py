"""Which way the app's modules may import one another (docs/decisions/0023),
read from its source with Python's ast module (helpers.source). Imports
inside functions count, as do relative ones.

| Rule | What it finds |
| --- | --- |
| rules_import_fastapi_or_web | rules, queries and forms importing FastAPI or web/ |
| routes_run_queries | a module of routes making or running a query |
| core_imports_a_feature | core importing any of the app but core and models |
| routes_import_another_features_routes | links between features not through paths.py |

Today's breaks are listed in BREAKS, which may only shrink: a new break
fails, and so does one fixed but still listed.
"""

import ast
from collections.abc import Callable, Mapping
from importlib.util import resolve_name

import pytest

from inventory_tng import models

from helpers.source import SOURCE, modules

# The app's modules by their path under SOURCE, read.
type App = Mapping[str, ast.Module]
# What the module at a path breaks a rule with.
type Check = Callable[[str, App], set[str]]

# Rules: each table's owner (db.protection), any queries.py or forms.py, and
# these, which hold rules but own no table.
RULES = {
    "catalogue/names.py",
    "catalogue/unit_readings.py",
    "catalogue/units.py",
    "site_settings/scan_settings.py",
    "stock/balance.py",
    "stock/ledger.py",
    *(
        table.info["owner"]
        for table in models.Base.metadata.tables.values()
        if table.info["owner"] is not None
    ),
}
# What of web/ rules may import. They are rules themselves, so they import
# neither FastAPI nor the rest of web/.
RULES_MAY_IMPORT = {"web/formatting.py", "web/forms.py"}
FASTAPI = {"fastapi", "starlette"}
# Decorators that add a route to a router: @router.get(...) and the like.
ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete", "api_route", "websocket"}
# Calls that make or run a query: select(...), or session.execute and the
# like. A session.get may stay.
QUERY_CALLS = {"select", "execute", "scalar", "scalars"}

# (rule, module, what it breaks the rule with), as found when the rules were
# written.
BREAKS = {
    ("rules_import_fastapi_or_web", "web/once.py", "fastapi"),
    ("rules_import_fastapi_or_web", "web/once.py", "starlette"),
    ("rules_import_fastapi_or_web", "web/once.py", "web/pages.py"),
}


def imports(path: str, app: App) -> set[str]:
    """What the module at `path` imports: the app's modules by path, and any
    other package by its top-level name. Of `from x import y`, y counts where
    it is a module, x where y is a name in it."""
    package = ".".join(["inventory_tng", *path.split("/")[:-1]])
    names: list[str] = []
    for node in ast.walk(app[path]):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = resolve_name("." * node.level + (node.module or ""), package)
            names += [f"{base}.{alias.name}" for alias in node.names]
    return {module for name in names if (module := _module(name, app))}


def _module(name: str, app: App) -> str | None:
    """The app's module a dotted name is in, by path (the longest that is
    one; the package itself is none), or another package's top-level name."""
    top, *parts = name.split(".")
    if top != "inventory_tng":
        return top
    while parts:
        stem = "/".join(parts)
        for path in (f"{stem}.py", f"{stem}/__init__.py"):
            if path in app:
                return path
        parts.pop()
    return None


def is_rules(path: str) -> bool:
    """A module of rules, held to rules_import_fastapi_or_web."""
    return (
        path in RULES
        or path in RULES_MAY_IMPORT
        or path.split("/")[-1] in {"queries.py", "forms.py"}
    )


def is_routes(path: str, app: App) -> bool:
    """A module of routes: one that makes an APIRouter or adds a route to
    one, wherever it was made (@router.get(...), router.add_api_route(...))."""
    for node in ast.walk(app[path]):
        if _called(node) in {"APIRouter", "add_api_route"}:
            return True
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            _called(decorator) in ROUTE_DECORATORS for decorator in node.decorator_list
        ):
            return True
    return False


def _called(node: ast.AST) -> str | None:
    """The name a call calls, bare or as a method: f(...) or x.f(...)."""
    match node:
        case ast.Call(func=ast.Name(id=name) | ast.Attribute(attr=name)):
            return name
    return None


def rules_import_fastapi_or_web(path: str, app: App) -> set[str]:
    if not is_rules(path):
        return set()
    return {
        module
        for module in imports(path, app)
        if module in FASTAPI
        or (module.startswith("web/") and module not in RULES_MAY_IMPORT)
    }


def routes_run_queries(path: str, app: App) -> set[str]:
    """Each query call in a module of routes, by the top-level function (or
    class) it is in."""
    if not is_routes(path, app):
        return set()
    return {
        f"{getattr(statement, 'name', '<module>')}: {called}"
        for statement in app[path].body
        for node in ast.walk(statement)
        if (called := _called(node)) in QUERY_CALLS
    }


def core_imports_a_feature(path: str, app: App) -> set[str]:
    """What core imports of the app besides core and the models: features,
    web/ and dev/ alike."""
    if not path.startswith("core/"):
        return set()
    return {
        module
        for module in imports(path, app)
        if module in app and not module.startswith("core/") and module != "models.py"
    }


def routes_import_another_features_routes(path: str, app: App) -> set[str]:
    """Links between features go through paths.py instead."""
    if not is_routes(path, app):
        return set()
    feature = path.split("/")[0]
    return {
        module
        for module in imports(path, app)
        if module in app and module.split("/")[0] != feature and is_routes(module, app)
    }


CHECKS: dict[str, Check] = {
    check.__name__: check
    for check in (
        rules_import_fastapi_or_web,
        routes_run_queries,
        core_imports_a_feature,
        routes_import_another_features_routes,
    )
}


def test_the_app_breaks_no_import_rule_but_those_listed() -> None:
    app = {
        str(path.relative_to(SOURCE)): ast.parse(path.read_text(), str(path))
        for path in modules()
    }
    now = {
        (rule, path, what)
        for rule, check in CHECKS.items()
        for path in app
        for what in check(path, app)
    }
    assert (now - BREAKS, BREAKS - now) == (set(), set()), (
        "(new, fixed) breaks of docs/decisions/0023: put right what is new, "
        "and take out of BREAKS what is fixed."
    )


# Each rule, shown a break, finds it. The modules a case's imports may name.
ROUTER = "router = APIRouter()\n"
APP = {
    "models.py": "",
    "core/settings.py": "",
    "catalogue/paths.py": "",
    "catalogue/queries.py": "",
    "catalogue/routes.py": ROUTER,
    "stock/api.py": ROUTER,
    "stock/ledger.py": "",
    "web/formatting.py": "",
    "web/pages.py": "",
}


@pytest.mark.parametrize(
    ("rule", "path", "source", "breaks"),
    [
        (
            "rules_import_fastapi_or_web",
            "stock/ledger.py",
            "from fastapi import Request\nfrom inventory_tng.web import formatting",
            {"fastapi"},
        ),
        (
            "rules_import_fastapi_or_web",
            "catalogue/queries.py",
            "def f():\n    from ..web.pages import x",
            {"web/pages.py"},
        ),
        (
            "routes_run_queries",
            "catalogue/routes.py",
            f"{ROUTER}def page(s):\n    s.scalars(select(Item))\n    s.get(Item, 1)",
            {"page: scalars", "page: select"},
        ),
        (
            "routes_run_queries",
            "catalogue/listing.py",
            "from .routes import router\n@router.get('/')\ndef page(s):\n    s.execute(x)",
            {"page: execute"},
        ),
        (
            "core_imports_a_feature",
            "core/db.py",
            (
                "from ..stock import ledger\nfrom inventory_tng import models\n"
                "from . import settings\nimport sqlalchemy"
            ),
            {"stock/ledger.py"},
        ),
        (
            "routes_import_another_features_routes",
            "stock/api.py",
            (
                f"{ROUTER}from ..catalogue import routes, paths\n"
                "from inventory_tng.catalogue.queries import x"
            ),
            {"catalogue/routes.py"},
        ),
    ],
)
def test_each_rule_finds_a_break(
    rule: str, path: str, source: str, breaks: set[str]
) -> None:
    app = {name: ast.parse(text) for name, text in APP.items()}
    app[path] = ast.parse(source)
    assert CHECKS[rule](path, app) == breaks
