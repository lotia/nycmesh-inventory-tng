"""A change a rule refuses is logged and counted once, in
core/db_errors.py's _attempted(), by the rule's name from
core/rule_names.py's RULE_NAMES (Ali, 2026-11-27, inventory-tng-v8sq).

Deterministic, read from the source:

- A rule refusal caught anywhere else (RuleBroken, a subclass of it, or
  an IntegrityError, not raised again) fails, but for those named in
  CAUGHT_ELSEWHERE, each with why.
- Every rule the code raises by name (enforce(), RuleBroken(), a
  RuleBroken subclass's own, require_current(), the ledger's line rules)
  is in RULE_NAMES, as is every key of web/forms.py's RULES, and RULE_NAMES
  names nothing else, so the list cannot outlive its code. A rule passed
  on in a variable is read where it is named; the few places that pass one
  on are named in PASSED_ON.
- An entry in either list no longer found fails too."""

import ast
import importlib
from pathlib import Path
from types import ModuleType

from inventory_tng.core.rule_names import RULE_NAMES, UNIQUE
from inventory_tng.web.forms import RULES

from helpers.source import SOURCE, modules

DB_ERRORS = SOURCE / "core" / "db_errors.py"
LEDGER = SOURCE / "stock" / "ledger.py"
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
# Where a rule is passed on in a variable, named by whoever calls it: by
# module and function.
PASSED_ON = {
    ("core/db_errors.py", "enforce"),
    ("core/db_locking.py", "require_current"),
    ("stock/ledger.py", "insert_transaction"),
    ("catalogue/unit_changes.py", "require_fixed_as_shown"),
}


def _where(path: Path) -> str:
    return str(path.relative_to(SOURCE))


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


def _caught_elsewhere() -> set[tuple[str, str]]:
    return {
        (_where(path), function)
        for path in modules()
        if path != DB_ERRORS
        for function in _caught(ast.parse(path.read_text(), str(path)))
    }


def test_rule_refusals_are_caught_only_by_attempt() -> None:
    assert _caught_elsewhere() - CAUGHT_ELSEWHERE.keys() == set()


def test_every_rule_refusal_caught_elsewhere_is_still_there() -> None:
    assert CAUGHT_ELSEWHERE.keys() - _caught_elsewhere() == set()


def _module(path: Path) -> ModuleType:
    return importlib.import_module(
        ".".join(("inventory_tng", *path.relative_to(SOURCE).with_suffix("").parts))
    )


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


def _rule_of(node: ast.Call, path: Path, broken: set[ast.AST]) -> ast.expr | None:
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
            path == LEDGER
        ):
            return node.args[0]
    return None


def _in_rule_errors(tree: ast.Module) -> set[ast.AST]:
    """The nodes of `tree`'s subclasses of RuleBroken."""
    return {
        node
        for top in tree.body
        if isinstance(top, ast.ClassDef)
        and set().union(*(_names(base) for base in top.bases)) & RULE_ERRORS
        for node in ast.walk(top)
    }


def _raised() -> tuple[set[str], set[tuple[str, str]]]:
    """The rules' names the code raises, and where one is passed on."""
    names: set[str] = set()
    passed: set[tuple[str, str]] = set()
    for path in modules():
        tree = ast.parse(path.read_text(), str(path))
        module = _module(path)
        inside = _functions(tree)
        broken = _in_rule_errors(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (arg := _rule_of(node, path, broken)) is None:
                continue
            found = _named(arg, module, tree)
            if found is None:
                passed.add((_where(path), inside.get(node, "<module>")))
            else:
                names |= found
    return names, passed


def test_every_rule_raised_is_in_rule_names() -> None:
    names, passed = _raised()
    assert names - RULE_NAMES == set()
    assert passed - PASSED_ON == set()


def test_rule_names_has_every_rule_worded_and_nothing_else() -> None:
    names, passed = _raised()
    assert set(RULES) - RULE_NAMES == set()
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
