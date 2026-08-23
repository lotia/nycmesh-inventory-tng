"""A JSON log stream, posted to a collector, by something that is not the app.

`scripts/ship-logs` is this module. It reads the records a running backend
wrote to standard output and posts them to an OTLP endpoint, which is the whole
of what a log agent on a Kubernetes node does -- so a compose stack gets the
arrangement a cluster has rather than an easier one that only exists here.

WHY THIS IS NOT IN THE APPLICATION, and it is the same reason `console.py` is
not. Decision 0021 settles that logs are written to standard output and picked
up from there, and gives the three arguments for it. What follows from them is
this module: a reader is free to do whatever it likes with the stream, because
it is downstream of a process that has already finished writing it.

So this is the second reader beside the first. `pretty-logs` draws the stream
for a person; `ship-logs` posts it to a collector. Neither imports Django,
both read exactly what the process wrote, and either can be at the end of the
same pipe:

    podman compose logs -f backend | scripts/ship-logs | scripts/pretty-logs

Every line is passed through unchanged, which is what makes that work.

NOTHING IS REDACTED HERE, deliberately. What reaches standard output has
already been through `redaction`, in the process that wrote it, where the
allowlist can see the record before it becomes a line of text. A second
redaction over parsed JSON would be a second answer to the same question, and
the weaker of the two.

A DEVELOPMENT TOOL. It posts in small batches over plain HTTP with no retry
and no disk queue: a collector that is down loses whatever was in flight, which
is the correct trade for a laptop and the wrong one for a cluster.
docs/observability.md is what to run in a cluster instead.

WHY THE ENCODING IS WRITTEN OUT HERE, since the SDK carries one. The pinned
distribution has a log exporter, and using it would replace the severity
table, the value encoder and the post below. It lives at
`opentelemetry.exporter.otlp.proto.http._log_exporter`, and its records at
`opentelemetry.sdk._logs` -- both underscored, and pinning a tool a developer
runs by hand to a private surface is a trade rather than an obvious win.
inventory-tng-rhf8 is where that is weighed. The resource is not part of the
argument: that one asks the SDK, because it has to equal what the spans carry.
"""

import json
import select
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any

from inventory_tng.console import parse
from inventory_tng.telemetry import endpoint

# OpenTelemetry's severity numbers for the names Python uses. A collector sorts
# and colours by the number and shows the text, so a record arriving with only
# one of the two is half-legible in the UI this exists to fill.
SEVERITY = {
    "critical": 21,
    "fatal": 21,
    "error": 17,
    "warning": 13,
    "warn": 13,
    "info": 9,
    "debug": 5,
    "notset": 1,
}

# Drawn from their own place in the payload rather than as attributes, so a
# collector's own columns are filled instead of a pile of look-alike fields.
# A set rather than a tuple: it is asked about once per key of every record.
STRUCTURAL = frozenset({"timestamp", "level", "event", "trace_id", "span_id"})

# How many records go in one post, and how long to wait for the next line
# before sending what there is. A follow of an idle backend has to arrive
# promptly or the UI is always a request behind; a burst -- a migration, a
# sheet import -- must not become one post per line.
BATCH = 200
QUIET = 0.2

# How much is taken from the stream at once. One `read1` returns whatever a
# single read gave, so this is a ceiling rather than something to wait for.
CHUNK = 65536

# Where this is if nobody says. The same address .env.sample offers, because
# the case this is for is a collector that compose has just brought up.
DEFAULT_ENDPOINT = "http://localhost:4318/v1/logs"

# What these logs are filed under when the environment says nothing. The same
# name compose gives the backend, so the two halves line up by default.
DEFAULT_SERVICE = "inventory-tng-backend"


def now() -> int:
    """This instant, as OTLP counts them."""
    return int(datetime.now().astimezone().timestamp() * 1_000_000_000)


def nanoseconds(stamped: str) -> int:
    """An ISO-8601 instant as OTLP wants it, or now if it is not one.

    Not refused: a stream is not pure -- an interpreter's dying traceback and
    anything a dependency wrote before logging was configured are both in it --
    and a line without a usable timestamp is still a line somebody wants to
    read.
    """
    try:
        return int(datetime.fromisoformat(stamped).timestamp() * 1_000_000_000)
    except (TypeError, ValueError):
        return now()


def value(anything: Any) -> dict[str, Any]:
    """One attribute value, in OTLP's tagged-union spelling."""
    if isinstance(anything, bool):
        return {"boolValue": anything}
    if isinstance(anything, int):
        return {"intValue": str(anything)}
    if isinstance(anything, float):
        return {"doubleValue": anything}
    if isinstance(anything, str):
        return {"stringValue": anything}
    return {"stringValue": json.dumps(anything)}


def log_record(record: dict[str, Any]) -> dict[str, Any]:
    """One parsed line, as an OTLP `LogRecord`.

    `trace_id` and `span_id` are the load-bearing part. They are on every
    record this application writes -- empty until something is tracing -- and
    they are what lets a collector's UI put a request's log lines beside its
    spans, which is the thing a developer opened it to see.
    """
    shipped: dict[str, Any] = {
        "timeUnixNano": str(nanoseconds(record.get("timestamp", ""))),
        "body": value(record.get("event", "")),
        "attributes": [{"key": key, "value": value(held)} for key, held in record.items() if key not in STRUCTURAL],
    }
    level = str(record.get("level", "")).lower()
    if level:
        shipped["severityText"] = level.upper()
        shipped["severityNumber"] = SEVERITY.get(level, 0)
    if record.get("trace_id"):
        shipped["traceId"] = record["trace_id"]
        shipped["spanId"] = record.get("span_id", "")
    return shipped


def plain(line: str) -> dict[str, Any]:
    """A line that is not one of ours, kept rather than dropped.

    A traceback from an interpreter that died before it could configure
    logging is the single most useful thing in a stream, and it is never JSON.
    """
    return {"timeUnixNano": str(now()), "body": value(line)}


def resource() -> dict[str, str]:
    """Whose logs these are, read by the same code that reads it for the spans.

    `OTELResourceDetector` rather than a parser of this module's own, and that
    is the point rather than a convenience: what has to be true is that this
    resource equals the one `telemetry.start` gives the spans. A second parser
    is a second answer waiting to happen, and the first version of this
    function was already one -- it did not unescape a value, so
    `deployment.environment=staging%20eu` would have filed the logs under a
    resource the spans did not have. If the service name differs a collector
    holds two unrelated services; if an attribute differs the names agree and
    a dashboard scoped by it silently drops every record, which is worse.

    The one thing laid over the detector is a service name, because it defaults
    to `unknown_service` and this is a development tool beside a stack that
    calls the backend something.

    THIS RUNS ON THE HOST, not in the container, so it inherits nothing:
    `compose.yaml` sets those variables inside the backend container and
    `uv run` does not read `.env`. docs/observability.md prints the exports
    beside the pipe for that reason.
    """
    from opentelemetry.sdk.resources import OTELResourceDetector

    detected = {str(name): str(held) for name, held in OTELResourceDetector().detect().attributes.items()}
    return {"service.name": DEFAULT_SERVICE} | detected


def payload(records: list[dict[str, Any]], described: dict[str, str]) -> dict[str, Any]:
    """A batch, wrapped in the resource that says whose logs these are."""
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": [{"key": key, "value": value(held)} for key, held in described.items()]},
                "scopeLogs": [{"scope": {"name": "inventory_tng.shipping"}, "logRecords": records}],
            }
        ]
    }


def post(records: list[dict[str, Any]], where: str, described: dict[str, str]) -> str:
    """Send one batch, and say what went wrong rather than stopping.

    Returning the complaint rather than raising: this sits in the middle of a
    pipe somebody is reading, and a collector that has not finished starting
    must not take the terminal down with it.
    """
    request = urllib.request.Request(
        where,
        data=json.dumps(payload(records, described)).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:
            answer.read()
    except (urllib.error.URLError, OSError) as refused:
        return f"ship-logs: {where} did not take {len(records)} record(s): {refused}"
    return ""


def reading(binary: Any, ready: Any = None) -> Iterator[str | None]:
    """Lines as they arrive, with `None` wherever the stream goes quiet.

    READ FROM THE BINARY STREAM, and that is the whole reason this exists
    rather than a `for line in sys.stdin`. `select` answers about a file
    DESCRIPTOR, and a text stream keeps a decoded buffer of its own in front of
    one -- so sixty lines arriving in a single write all sit in that buffer,
    the descriptor has nothing left on it, and every "has more arrived?"
    answers no. The batch then flushed after every line: measured at sixty
    posts where one was wanted, each able to block the same thread for five
    seconds. Holding the undecoded remainder here means the question is only
    ever asked when there genuinely is nothing left.
    """
    remainder = b""
    while True:
        chunk = binary.read1(CHUNK)
        if not chunk:
            break
        # Split whole rather than partitioned one line at a time: a partition
        # copies everything after the newline, so a 64 KB read of short lines
        # copied about ten megabytes. Measured at four times slower over four
        # megabytes. `split` leaves the tail -- possibly a half line, possibly
        # empty -- in the last element, which is what carries over.
        *complete, remainder = (remainder + chunk).split(b"\n")
        for line in complete:
            yield line.decode("utf-8", "replace")
        if not remainder and ready is not None and not ready(binary):
            yield None
    if remainder:
        yield remainder.decode("utf-8", "replace")


def batches(lines: Iterable[str | None], onward: Any = None) -> Iterator[list[dict[str, Any]]]:
    """Group the stream into posts, yielding on a full batch or a quiet moment.

    Every line is written onward as it is read rather than a batch later, so a
    reader after this one -- `| scripts/pretty-logs` -- draws what a collector
    received, as it arrives.
    """
    destination = sys.stdout if onward is None else onward
    held: list[dict[str, Any]] = []
    for line in lines:
        if line is None:
            if held:
                yield held
                held = []
            continue
        destination.write(f"{line}\n")
        destination.flush()
        record = parse(line)
        held.append(log_record(record) if record is not None else plain(line))
        if len(held) >= BATCH:
            yield held
            held = []
    if held:
        yield held


def waiting(stream: Any) -> bool:
    """Whether anything more has arrived on this descriptor, briefly waited on."""
    return bool(select.select([stream], [], [], QUIET)[0])


def main() -> None:
    """Read standard output on standard input, and post it to a collector."""
    where = endpoint("logs") or DEFAULT_ENDPOINT
    described = resource()

    print(f"ship-logs: posting to {where} as {described['service.name']}", file=sys.stderr)

    for batch in batches(reading(sys.stdin.buffer, waiting)):
        complaint = post(batch, where, described)
        if complaint:
            print(complaint, file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    main()
