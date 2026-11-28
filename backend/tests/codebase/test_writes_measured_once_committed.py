"""What a write did is measured and logged through once_committed, never
by hand after its commit (CONTRIBUTING.md, Logging; inventory-tng-kebu):
a metric's add() or record(), or a log.info(), after a commit in the
same function fails here, and so does a call, after a commit, of a
function of the same module that does one of them, itself or through
another such function.

Deterministic, read from the source, function by function, each without
the functions and lambdas defined in it, which run when called, not
where they stand, and without its except clauses, as a failure is
measured and logged at once. A commit is a call of .commit(), awaited
or not, or the end of a `with ... .begin():` block. Only what comes
after the first of them, by line, is read, so the same calls before it,
inside the transaction, never match. A metric is a name the app's
modules bind at the top level to a meter's create_counter(),
create_histogram() or create_up_down_counter(), so a set's add() never
matches; and only info is read of the log levels, as a refusal, a
failure or a debug line is logged at once. Info given a name in the
function (`emit = log.debug if quiet else log.info`), or chosen where
it is called (`(log.info if quiet else log.warning)(...)`), is info
too. Work handed to once_committed, as once_committed(session,
log.info, ...), is a name passed, not a call, so it never matches
either. Another module's function called after a commit is not followed,
as accounts.reminded() measures a reminder shown, not a write."""

import ast
from collections.abc import Iterator

from helpers.source import parsed

LOGGERS = {"log", "logger"}
INSTRUMENTS = {"create_counter", "create_histogram", "create_up_down_counter"}
FUNCTIONS = ast.FunctionDef | ast.AsyncFunctionDef
# Left out of a function's own nodes: scopes of their own, and failures.
LEFT_OUT = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
    ast.ExceptHandler,
)


def _metrics(trees: Iterator[ast.Module]) -> set[str]:
    """The names the modules bind at their top level to an instrument."""
    return {
        target.id
        for tree in trees
        for node in tree.body
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr in INSTRUMENTS
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }


def _own(function: ast.AST) -> Iterator[ast.AST]:
    """The nodes of `function`, but for those of a scope defined in it or
    of an except clause."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        if isinstance(node, LEFT_OUT):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _committed_at(nodes: list[ast.AST]) -> int | None:
    """The first line by which the function has committed, if it does."""
    lines = [
        node.lineno
        for node in nodes
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "commit"
    ] + [
        node.end_lineno or node.lineno
        for node in nodes
        if isinstance(node, ast.With | ast.AsyncWith)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "begin"
            for item in node.items
        )
    ]
    return min(lines, default=None)


def _info(expression: ast.expr) -> bool:
    """`log.info`, or a choice of levels that may be it, as
    `(log.info if quiet else log.warning)`."""
    match expression:
        case ast.Attribute(
            value=ast.Name(id=name) | ast.Attribute(attr=name), attr="info"
        ):
            return name in LOGGERS
        case ast.IfExp(body=body, orelse=orelse):
            return _info(body) or _info(orelse)
    return False


def _by_hand(call: ast.Call, metrics: set[str], named: set[str]) -> bool:
    """A metric added or recorded, an info line, or a call of a name in
    `named`: a level given a name, or a function of the module that
    measures or logs by hand."""
    match call.func:
        case ast.Attribute(
            value=ast.Name(id=name) | ast.Attribute(attr=name), attr="add" | "record"
        ):
            return name in metrics
        case ast.Name(id=name):
            return name in named
    return _info(call.func)


def _levels(nodes: list[ast.AST]) -> set[str]:
    """The names a function gives a level that may be info, as
    `emit = log.debug if quiet else log.info`."""
    return {
        target.id
        for node in nodes
        if isinstance(node, ast.Assign) and _info(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _measuring(tree: ast.Module, metrics: set[str]) -> set[str]:
    """The module's top-level functions that measure or log by hand,
    themselves or through another of them, wherever they are called."""
    functions = {
        node.name: list(_own(node)) for node in tree.body if isinstance(node, FUNCTIONS)
    }
    found: set[str] = set()
    while more := {
        name
        for name, nodes in functions.items()
        if name not in found
        and any(
            isinstance(node, ast.Call)
            and _by_hand(node, metrics, found | _levels(nodes))
            for node in nodes
        )
    }:
        found |= more
    return found


def _after_commit(tree: ast.Module, metrics: set[str]) -> list[int]:
    """The lines of `tree` that measure or log by hand after a commit."""
    measuring = _measuring(tree, metrics)
    found: list[int] = []
    for function in ast.walk(tree):
        if not isinstance(function, FUNCTIONS):
            continue
        nodes = list(_own(function))
        committed = _committed_at(nodes)
        if committed is None:
            continue
        named = measuring | _levels(nodes)
        found += [
            node.lineno
            for node in nodes
            if isinstance(node, ast.Call)
            and node.lineno > committed
            and _by_hand(node, metrics, named)
        ]
    return sorted(found)


def test_no_write_is_measured_or_logged_by_hand_after_its_commit() -> None:
    trees = parsed()
    metrics = _metrics(iter(trees.values()))
    found = {
        where: lines
        for where, tree in trees.items()
        if (lines := _after_commit(tree, metrics))
    }
    assert found == {}


def test_finds_a_write_measured_or_logged_by_hand_after_its_commit() -> None:
    """The check itself: each way of writing one is found, and what only
    looks like one is not."""
    source = (
        'saved = meter.create_counter("inventory.saved")\n'
        'took = meter.create_histogram("inventory.took")\n'
        "def route(session):\n"
        "    saved.add(1)\n"
        '    log.info("saving")\n'
        "    session.commit()\n"
        "    saved.add(1)\n"
        "    telemetry.took.record(2)\n"
        '    log.info("saved")\n'
        '    log.warning("slow")\n'
        "    seen.add(1)\n"
        "    once_committed(session, log.info, 'saved')\n"
        "    def later():\n"
        '        log.info("not here")\n'
        "async def api(session):\n"
        "    await session.commit()\n"
        '    logger.info("saved")\n'
        "def command(database):\n"
        "    with database.session() as session, session.begin():\n"
        '        log.info("inside")\n'
        "    saved.add(1)\n"
        "def failed(database):\n"
        "    try:\n"
        "        with database.session() as session, session.begin():\n"
        "            pass\n"
        "    except TimeoutError:\n"
        "        saved.add(1)\n"
        "def no_commit():\n"
        "    saved.add(1)\n"
        "def measured():\n"
        "    saved.add(1)\n"
        "def logged():\n"
        "    measured()\n"
        "def form(session, quiet):\n"
        "    once_committed(session, measured)\n"
        "    session.commit()\n"
        "    measured()\n"
        "    logged()\n"
        "    emit = log.debug if quiet else log.info\n"
        '    emit("saved")\n'
        '    (log.info if quiet else log.warning)("saved")\n'
        "    quieter = log.debug\n"
        '    quieter("saved")\n'
        "    other.measured()\n"
    )
    tree = ast.parse(source)
    assert _after_commit(tree, _metrics(iter([tree]))) == [
        7,
        8,
        9,
        17,
        21,
        37,
        38,
        40,
        41,
    ]
