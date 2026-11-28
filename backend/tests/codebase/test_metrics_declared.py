"""Every counter and histogram the app makes is declared where it is made
(Ali, 2026-11-27, inventory-tng-8xs0): given straight to a WriteMetric
(core/write_metrics.py), measured once committed; to refusals()
(core/refusals.py); or to at_once() (core/metrics_at_once.py) with why it
measures no write. So a write's metric made as a plain counter, which
could be added before its commit, fails here, and the reason given
instead is read in one place, where the metric is made.

Deterministic, read from the source: a call of a meter's create_counter,
create_histogram, create_up_down_counter or create_gauge that is not an
argument of one of those three calls is found. An observable instrument
is left out: its callback reads state as it is exported, and writes
nothing."""

import ast

from helpers.source import parsed

MADE_BY = {
    "create_counter",
    "create_histogram",
    "create_up_down_counter",
    "create_gauge",
}
DECLARED_BY = {"WriteMetric", "refusals", "at_once"}


def _bare(tree: ast.Module) -> list[int]:
    """The lines of `tree` that make a metric left undeclared."""
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    declared = {
        id(argument)
        for call in calls
        if isinstance(call.func, ast.Name) and call.func.id in DECLARED_BY
        for argument in call.args
    }
    return sorted(
        call.lineno
        for call in calls
        if isinstance(call.func, ast.Attribute)
        and call.func.attr in MADE_BY
        and id(call) not in declared
    )


def test_every_metric_is_declared_where_it_is_made() -> None:
    found = {where: lines for where, tree in parsed().items() if (lines := _bare(tree))}
    assert found == {}


def test_finds_a_metric_made_bare() -> None:
    """The check itself: each way of making one bare is found, and each
    declaration is not."""
    source = (
        'saved = meter.create_counter("saved")\n'
        'took = metrics.get_meter(__name__).create_histogram("took")\n'
        'level = meter.create_up_down_counter("level")\n'
        'last = meter.create_gauge("last")\n'
        'closed = WriteMetric(log, meter.create_counter("closed"))\n'
        '@refusals(log, meter.create_counter("refused"))\n'
        "def refused(event): ...\n"
        'printed = at_once("nothing written", meter.create_counter("printed"))\n'
        'shown = other(meter.create_counter("shown"))\n'
        'meter.create_observable_gauge("pool", callbacks=[])\n'
    )
    assert _bare(ast.parse(source)) == [1, 2, 3, 4, 9]
