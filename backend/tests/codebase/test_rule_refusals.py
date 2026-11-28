"""A change a rule refuses is logged and counted once, in
core/db_errors.py's _attempted(), by the rule's name from
core/rule_names.py's RuleName (Ali, 2026-11-27, inventory-tng-v8sq).

Deterministic, read from the source:

- A rule refusal caught anywhere else (RuleBroken, a subclass of it, or
  an IntegrityError, not raised again) fails, but for those named in
  CAUGHT_ELSEWHERE, each with why.
- Every rule the code raises by name (enforce(), RuleBroken(), a
  RuleBroken subclass's own, require_current(), the ledger's line rules)
  is a RuleName, and RuleName names nothing else, so the list cannot
  outlive its code. A rule passed on in a variable is read where it is
  named; the few places that pass one on are named in PASSED_ON. That
  each is a RuleName, and so is every key of web/forms.py's RULES, `ty
  check` holds too (inventory-tng-vj5h).
- A change made through attempt() or its kin with no ids for its
  refusal's log line fails, but for those in NAMES_NO_ROW.
- An entry in any of these lists no longer found fails too."""

import ast
import importlib
from collections.abc import Callable
from functools import cache
from types import ModuleType
from typing import get_args

from inventory_tng.core.rule_names import UNIQUE, RuleName
from inventory_tng.web.forms import RULES

from helpers.source import parsed

DB_ERRORS = "core/db_errors.py"
LEDGER = "stock/ledger.py"
RULE_ERRORS = {
    "RuleBroken",
    "Stale",
    "NotCountable",
    "ShownOtherwise",
    "IntegrityError",
}
# Rule refusals caught outside _attempted, by module and the function
# catching them.
STALE = "a stale form, counted by db_locking.unchanged (inventory.edits.stale)"
SAME_TIME = "made by a request at the same time, and answered with it"
CAUGHT_ELSEWHERE = {
    ("scan/batch.py", "record"): "a batch refused, counted as one "
    "(inventory.transactions.refused)",
    ("site_settings/site_settings_routes.py", "save_scan_settings"): STALE,
    ("site_settings/site_settings_routes.py", "save_site_settings"): STALE,
    ("site_settings/site_settings_routes.py", "save_sign_in_settings"): STALE,
    ("core/db_locking.py", "stale_as_read"): STALE,
    ("stock/adjustment_reasons.py", "seeded"): SAME_TIME,
    ("stock/open_packages.py", "make"): SAME_TIME,
}
# The changes whose refusal is logged with no row's id, by module and
# function, each with why: _attempted logs the ids a page gives it.
NO_ROW_YET = "a row made, so none to name yet"
NAMES_NO_ROW = {
    ("volunteers/volunteers_routes.py", "add_volunteer"): NO_ROW_YET,
    ("places/locations_routes.py", "add_location"): NO_ROW_YET,
    ("catalogue/categories_routes.py", "add_category"): NO_ROW_YET,
    ("site_settings/site_settings_routes.py", "add_reason"): NO_ROW_YET,
    ("site_settings/site_settings_routes.py", "save_site_settings"): "the one "
    "settings row",
    ("catalogue/reading_rules_routes.py", "accept"): "rules for many units "
    "at once, by token, not by row",
    ("catalogue/unit_readings.py", "load"): "the operator's command, which "
    "counts nothing, loading many rules",
    ("catalogue/items_routes.py", "track_items"): "many items at once",
    ("admins/accounts.py", "invite_by_operator"): NO_ROW_YET,
    ("core/db_locking.py", "stale_since"): "Stale, which unchanged() logs "
    "with its row's id, not _attempted",
}
RULE_NAMES = frozenset(get_args(RuleName))
ATTEMPTS = {"attempt", "refusal", "refusal_naming_index", "unique"}
# Where a rule is passed on in a variable, named by whoever calls it: by
# module and function.
PASSED_ON = {
    ("core/db_errors.py", "enforce"),
    ("core/db_locking.py", "require_current"),
    ("stock/ledger.py", "insert_transaction"),
    ("catalogue/unit_changes.py", "require_fixed_as_shown"),
}


def _functions(tree: ast.Module) -> dict[ast.AST, str]:
    """Each node of `tree` by the top-level function or method it is in."""
    inside: dict[ast.AST, str] = {}
    for top in tree.body:
        bodies = top.body if isinstance(top, ast.ClassDef) else [top]
        for each in bodies:
            if isinstance(each, ast.FunctionDef):
                for node in ast.walk(each):
                    inside[node] = each.name
    return inside


def _names(node: ast.expr | None) -> set[str]:
    """The exception classes an except clause names."""
    match node:
        case ast.Tuple(elts=parts):
            return set().union(*(_names(part) for part in parts))
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return {name}
    return set()


def _raises_again(handler: ast.ExceptHandler) -> bool:
    return any(
        isinstance(node, ast.Raise) and node.exc is None for node in handler.body
    )


def _caught(tree: ast.Module) -> set[str]:
    """The functions of `tree` that catch a rule refusal and keep it."""
    inside = _functions(tree)
    return {
        inside.get(node, "<module>")
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and _names(node.type) & RULE_ERRORS
        and not _raises_again(node)
    }


def _outside_db_errors(
    find: Callable[[ast.Module], set[str]],
) -> set[tuple[str, str]]:
    """The functions `find` finds in every module but db_errors itself, by
    module and function."""
    return {
        (where, function)
        for where, tree in parsed().items()
        if where != DB_ERRORS
        for function in find(tree)
    }


def _caught_elsewhere() -> set[tuple[str, str]]:
    return _outside_db_errors(_caught)


def test_rule_refusals_are_caught_only_by_attempt() -> None:
    assert _caught_elsewhere() - CAUGHT_ELSEWHERE.keys() == set()


def test_every_rule_refusal_caught_elsewhere_is_still_there() -> None:
    assert CAUGHT_ELSEWHERE.keys() - _caught_elsewhere() == set()


def _unnamed(tree: ast.Module) -> set[str]:
    """The functions of `tree` that make a change through attempt() or its
    kin with no ids for its refusal's log line."""
    inside = _functions(tree)
    return {
        inside.get(node, "<module>")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name | ast.Attribute)
        and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
        in ATTEMPTS
        and not node.keywords
    }


def _unnamed_anywhere() -> set[tuple[str, str]]:
    return _outside_db_errors(_unnamed)


def test_every_change_names_its_rows_for_a_refusal() -> None:
    """A rule refusal is logged with the ids a page passes attempt() and
    its kin (CONTRIBUTING.md#logging): none passed fails, but for those in
    NAMES_NO_ROW."""
    assert _unnamed_anywhere() - NAMES_NO_ROW.keys() == set()


def test_every_change_naming_no_row_is_still_there() -> None:
    assert NAMES_NO_ROW.keys() - _unnamed_anywhere() == set()


def test_finds_a_change_naming_no_row() -> None:
    source = (
        "def bare():\n"
        "    attempt(session, change)\n"
        "def named():\n"
        "    db_errors.unique(session, change, item_id=1)\n"
        "def passed_on():\n"
        "    refusal(session, change, RULE, **ids)\n"
    )
    assert _unnamed(ast.parse(source)) == {"bare"}


def _module(where: str) -> ModuleType:
    """The module at `where`, a path under SOURCE, imported."""
    dotted = where.removesuffix(".py").replace("/", ".")
    return importlib.import_module(f"inventory_tng.{dotted}")


def _returned(function: ast.FunctionDef) -> set[str]:
    """The rules' names a rule function returns, as written in it."""
    return {
        node.value.value
        for node in ast.walk(function)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _value(node: ast.expr, module: ModuleType) -> object:
    """What a name, or a dotted name, means in `module`; None for a
    variable."""
    match node:
        case ast.Name(id=name):
            return vars(module).get(name)
        case ast.Attribute(value=owner, attr=name):
            return getattr(_value(owner, module), name, None)
    return None


def _named(arg: ast.expr, module: ModuleType, tree: ast.Module) -> set[str] | None:
    """The rules' names `arg` may be: a string, a constant of its module,
    any value of one of its dicts, or what a rule function of its module
    returns; None if it is none of these, a variable."""
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    match arg:
        case ast.Constant(value=str(name)):
            return {name}
        case ast.Name() | ast.Attribute():
            value = _value(arg, module)
            return {value} if isinstance(value, str) else None
        case ast.Subscript(value=table):
            value = _value(table, module)
            return set(value.values()) if isinstance(value, dict) else None
        case ast.Call(func=ast.Name(id=name)) if name in functions:
            return _returned(functions[name])
    return None


def _rule_of(node: ast.Call, where: str, broken: set[ast.AST]) -> ast.expr | None:
    """The rule a call raises a change's refusal by, if it does: `broken`,
    the nodes of RuleBroken's subclasses, whose super().__init__ names
    theirs."""
    match node.func:
        case ast.Name(id="enforce" | "RuleBroken") if node.args:
            return node.args[0]
        case ast.Name(id="require_current") if node.args:
            return node.args[-1]
        case ast.Attribute(
            value=ast.Call(func=ast.Name(id="super")), attr="__init__"
        ) if node in broken and node.args:
            return node.args[0]
        case ast.Attribute(value=ast.Name(id="broken"), attr="append") if (
            where == LEDGER
        ):
            return node.args[0]
    return None


def _returned_lists(tree: ast.Module) -> set[str]:
    """The rules' names the ledger's rule functions return in a list, as
    in return ["opening_from_sealed"]."""
    return {
        part.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.List)
        for part in node.value.elts
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    }


def _in_rule_errors(tree: ast.Module) -> set[ast.AST]:
    """The nodes of `tree`'s subclasses of RuleBroken."""
    return {
        node
        for top in tree.body
        if isinstance(top, ast.ClassDef)
        and set().union(*(_names(base) for base in top.bases)) & RULE_ERRORS
        for node in ast.walk(top)
    }


@cache
def _raised() -> tuple[set[str], set[tuple[str, str]]]:
    """The rules' names the code raises, and where one is passed on."""
    names: set[str] = set()
    passed: set[tuple[str, str]] = set()
    for where, tree in parsed().items():
        module = _module(where)
        inside = _functions(tree)
        broken = _in_rule_errors(tree)
        if where == LEDGER:
            names |= _returned_lists(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (arg := _rule_of(node, where, broken)) is None:
                continue
            found = _named(arg, module, tree)
            if found is None:
                passed.add((where, inside.get(node, "<module>")))
            else:
                names |= found
    return names, passed


def test_every_rule_raised_is_in_rule_names() -> None:
    names, passed = _raised()
    assert names - RULE_NAMES == set()
    assert passed - PASSED_ON == set()


def test_rule_names_has_every_rule_worded_and_nothing_else() -> None:
    names, passed = _raised()
    assert RULE_NAMES - names - set(RULES) - {UNIQUE} == set()
    assert PASSED_ON - passed == set()


def test_finds_a_rule_refusal_caught_by_hand() -> None:
    """The check itself: a refusal kept is found, one raised again is not."""
    source = (
        "def kept():\n"
        "    try:\n"
        "        change()\n"
        "    except (exc.IntegrityError, OSError):\n"
        "        return False\n"
        "class Page:\n"
        "    def method(self):\n"
        "        try:\n"
        "            change()\n"
        "        except NotCountable:\n"
        "            pass\n"
        "def raised():\n"
        "    try:\n"
        "        change()\n"
        "    except RuleBroken:\n"
        "        log()\n"
        "        raise\n"
    )
    assert _caught(ast.parse(source)) == {"kept", "method"}
