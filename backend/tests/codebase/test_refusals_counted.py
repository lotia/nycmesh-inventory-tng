"""Every refusal the app logs is counted too, through core/refusals.py's
Refusals, never logged by hand (Ali, 2026-11-27, inventory-tng-h3k0): a
log call whose event says something was refused, rejected or held back,
or not made, set, settled or brought back, fails here, outside that
module.

Deterministic, read from the source: only a call of a log level's method
(log.info("...")), or of a name given one (emit = log.info), with its
event written in it, a string or an f-string, is read, and only its
event, by whole words, so a docstring, a comment, a variable or a
refusal's own words to a person (RULES, notices) never match. The few that are not a request refused are named in NOT_COUNTED,
each with why, and one no longer found there fails too, so the list
cannot outlive its code."""

import ast
import re

from helpers.source import SOURCE, modules

LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}
REFUSED = re.compile(
    r"\b(?:refused|rejected|held back|not (?:made|set|settled|brought back))\b"
)
HELPER = SOURCE / "core" / "refusals.py"
# Operator commands export no metrics: telemetry starts only in the
# server (server.py), so a counter there would always read nothing.
COMMAND = "an operator command, which exports no metrics"
NOT_COUNTED = {
    ("admins/accounts.py", "lockdown refused"): COMMAND,
    ("core/operator_commands.py", "command refused outside development"): COMMAND,
    ("catalogue/unit_readings_command.py", "unit readings refused"): COMMAND,
    # load() is called by the command alone.
    ("catalogue/unit_readings.py", "unit readings refused"): COMMAND,
    # The database refusing the app's own login as it starts, which stops
    # it: no request was refused.
    ("core/database_role.py", "database login refused"): "the app's start",
}


def _levels(tree: ast.Module) -> set[str]:
    """The names `tree` gives a log level's method, as in
    emit = log.warning if bad else log.info."""
    return {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(part, ast.Attribute) and part.attr in LEVELS
            for part in ast.walk(node.value)
        )
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _event(node: ast.Call, levels: set[str]) -> str | None:
    """The event a log call writes in it: an f-string's text without its
    values. `levels`, the names given a level's method."""
    match node.func:
        case ast.Attribute(attr=attr) if attr in LEVELS:
            pass
        case ast.Name(id=name) if name in levels:
            pass
        case _:
            return None
    if not node.args:
        return None
    match node.args[0]:
        case ast.Constant(value=str(text)):
            return text
        case ast.JoinedStr(values=parts):
            return "".join(
                part.value
                for part in parts
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
    return None


def _refusals_logged(tree: ast.Module) -> list[tuple[int, str]]:
    """Each log call in `tree` whose event says it refused, by line."""
    levels = _levels(tree)
    return [
        (node.lineno, event)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (event := _event(node, levels)) is not None
        and REFUSED.search(event)
    ]


def _logged_by_hand() -> list[tuple[str, int, str]]:
    return [
        (str(path.relative_to(SOURCE)), line, event)
        for path in modules()
        if path != HELPER
        for line, event in _refusals_logged(ast.parse(path.read_text(), str(path)))
    ]


def test_every_refusal_logged_is_counted_through_refusals() -> None:
    by_hand = [
        f"{where}:{line} {event!r}"
        for where, line, event in _logged_by_hand()
        if (where, event) not in NOT_COUNTED
    ]
    assert by_hand == []


def test_every_refusal_left_uncounted_is_still_there() -> None:
    found = {(where, event) for where, _, event in _logged_by_hand()}
    assert set(NOT_COUNTED) - found == set()


def test_finds_a_refusal_logged_by_hand() -> None:
    """The check itself: each way of writing one is found, and words that
    only look like one are not."""
    source = (
        'log.info("thing refused", reason="x")\n'
        "log.warning(\n"
        '    "reset not made",\n'
        ")\n"
        'log.debug(f"{purpose} refused")\n'
        'log.info("length not set: the form said otherwise")\n'
        'log.info("batch held back: build too old")\n'
        'log.info("mail not sent")\n'
        'log.info("unrefused")\n'
        'refusals("thing refused", reason="x")\n'
        '"""log.info("refused")"""\n'
        "emit = log.warning if bad else log.info\n"
        'emit("action refused")\n'
    )
    found = _refusals_logged(ast.parse(source))
    assert sorted(line for line, _ in found) == [1, 2, 5, 6, 7, 13]
