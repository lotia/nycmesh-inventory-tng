"""What an unattended run says about itself.

A management command is the one thing in this system nobody is watching. It is
started by a person on a terminal or by a Job in a cluster, it prints a report
to standard output, and until now that report was the whole of what anybody
knew: nothing said when it started, how long it took, or whether it finished at
all. A Job that dies half way looked, from a collector, exactly like a Job that
was never run.

WHAT IT SAYS, and no more. A record when it starts, a record when it finishes,
and a counter by command and outcome so a run that stopped happening is a graph
with a gap in it. It does not restate the printed report, because that is what
standard output is for and a reader of one wants the columns.

EXCEPT THE FIGURES, which it does carry. Every section a command prints is a
heading and a list of (label, count) -- `_report.render` is built on that shape
-- so the same figures go onto the record as one field, without any command
having to name them a second time. Labels are prose chosen in this code; the
counts are integers. Neither is anybody's name, which is what makes it safe to
put a whole section on a record.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

from inventory import telemetry
from inventory_tng import telemetry as sdk

log = structlog.get_logger("inventory.commands")


def _pushed() -> None:
    """Export what this run recorded, before the process carrying it exits.

    A `PeriodicExportingMetricReader` exports on a timer and a command that
    finishes in under a second is gone long before its first tick, so a counter
    incremented and never flushed looks from a collector exactly like the run
    that never happened. Spans go the same way and for the same reason.

    Read off the provider rather than held, because which one is installed is
    `inventory_tng.telemetry.start`'s business and a test substitutes it.
    """
    from opentelemetry.metrics import get_meter_provider
    from opentelemetry.trace import get_tracer_provider

    for provider in (get_meter_provider(), get_tracer_provider()):
        flush = getattr(provider, "force_flush", None)
        if flush is not None:
            flush()


@contextmanager
def running(command: str) -> Iterator[dict[str, Any]]:
    """Say that this command ran, and what became of it.

    Yields the dict a caller puts its figures into, so what is reported is
    what the command actually counted rather than a second tally. Anything
    raised is recorded as a failure and re-raised: this reports, it does not
    absorb.

    STARTING THE SDK IS PART OF THE JOB, and leaving it out was the whole of
    why the counter below could not reach anywhere. Nothing else starts one in
    this process: `inventory_tng.telemetry.start` is imported by `wsgi`, `asgi`
    and the gunicorn hook, which is to say by the three things that serve and
    by nothing that runs a command. Without a `MeterProvider` the counter is a
    proxy instrument whose `add` discards, so `inventory.command_runs` had no
    series at all -- and a Job that stopped happening was a gap in a graph that
    was empty to begin with. A checkout with no endpoint configured still pays
    nothing: `start` answers before importing any of it.
    """
    # THE METRICS HALF AND NOTHING ELSE. `inventory_tng.telemetry`'s header is
    # the argument: an instrumented driver in a command makes a span of every
    # statement, so an import over a real workbook would produce thousands and
    # then hold the run up exporting them. `django=False` for the neighbouring
    # reason -- nothing here serves a request, so there is no framework to
    # instrument.
    exporting = sdk.start(django=False, traces=False)
    log.info("command started", command=command)
    counted: dict[str, Any] = {}
    started = time.perf_counter()
    try:
        try:
            yield counted
        except Exception:
            log.exception("command failed", command=command)
            telemetry.COMMAND_RUNS.add(1, {"command": command, "outcome": "failed"})
            raise
        log.info(
            "command finished",
            command=command,
            counted=counted,
            duration=round((time.perf_counter() - started) * 1000, 1),
        )
        telemetry.COMMAND_RUNS.add(1, {"command": command, "outcome": "finished"})
    finally:
        if exporting:
            _pushed()


def figures(*sections: tuple[str, list[tuple[str, int]]]) -> dict[str, Any]:
    """The sections a command prints, as the field a record carries.

    Keyed by heading so two sections cannot collide, and flattened no further:
    a label already reads as a sentence, and turning it into a key would make
    a field name out of prose somebody may reword.
    """
    return {heading: dict(counted) for heading, counted in sections}
