"""That a stream on standard output can be got into a collector unchanged.

`scripts/ship-logs` is the compose stack's stand-in for the agent a Kubernetes
node runs: it reads what the backend wrote and posts it, so the arrangement a
developer sees is the one a cluster has rather than an easier one. What has to
hold is that it is a *reader* -- it changes nothing, it loses nothing, and it
passes every line onward so that `pretty-logs` can sit after it.

The one field worth its own assertions is `trace_id`. It is what makes a
request's log lines findable from its trace, and it is the only part of this
that is not obvious from reading the JSON.
"""

import io
import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings

from inventory_tng import shipping

REPO_ROOT = Path(settings.REPO_ROOT)

A_RECORD = {
    "timestamp": "2026-08-23T14:32:07.412-04:00",
    "level": "warning",
    "logger": "inventory.ledger",
    "event": "an append was refused",
    "trace_id": "9e9d35e06ba5defab17f950246a26e63",
    "span_id": "535975b751806d2b",
    "status_code": 409,
}


A_RESOURCE = {"service.name": "inventory-tng-backend"}


def a_line(record: dict[str, Any]) -> str:
    return json.dumps(record) + "\n"


# A pipe holds a fixed amount and a write blocks once it is full. These tests
# fill one and then read it, which deadlocks the moment the payload does not
# fit: nothing can empty the pipe, because the read is on the line after the
# write returns.
#
# inventory-tng-nre8 is what that cost. The suite HUNG rather than failed,
# forever, at 93%, and "the full suite passes" was said three times against
# runs that had actually timed out. A test that hangs reports no failure, so
# every later claim about the suite becomes a guess.
#
# WHY IT SURVIVED. Linux gives a new pipe 64 KiB and the largest payload here
# is about 13 KiB, so it fitted. But the kernel hands out two-page pipes
# instead of sixteen-page ones once a user is over `fs.pipe-user-pages-soft`,
# which a long session with containers and subprocesses reaches easily -- and
# then it does not fit. Green on CI, green on a quiet laptop, hung on a busy
# one.
#
# THE FIX IS TO STOP DEPENDING ON THE DEFAULT, not to drain while filling. A
# writer thread would also clear the deadlock, and it was tried; it quietly
# weakens the burst test below, whose whole subject is what happens when sixty
# lines are ALREADY THERE and `select` therefore has nothing to report. A
# writer still filling keeps the descriptor readable, which is the condition
# that hides exactly the defect that test exists to catch. So the pipe is
# made big enough instead, and the arrangement the tests were written against
# is preserved rather than traded away.
# Asked for as a multiple of the page size and only as much as the payload
# needs, because the limit that shrank the pipe in the first place is a budget
# over ALL of a user's pipes -- so a greedy request is refused on exactly the
# loaded machine this is for. 256 KiB was tried and refused there; twice the
# payload is granted.
PAGE = 4096


def room_for(data: bytes) -> int:
    """Capacity to ask for: enough for `data`, rounded up to whole pages."""
    return max(2 * PAGE, -(-2 * len(data) // PAGE) * PAGE)


# F_SETPIPE_SZ and F_GETPIPE_SZ, which `fcntl` does not name.
SET_PIPE_SIZE = 1031
GET_PIPE_SIZE = 1032


def pipe_capacity(descriptor: int) -> int | None:
    """How much that pipe holds, or None where the question cannot be asked."""
    try:
        import fcntl
    except ImportError:  # Linux is what CI and development run
        return None
    return int(fcntl.fcntl(descriptor, GET_PIPE_SIZE))


def filled_with(data: bytes) -> int:
    """The reading end of a pipe holding all of `data`, with the writer closed.

    The capacity is asked for rather than assumed. Growing is allowed up to
    `fs.pipe-max-size` -- a megabyte by default -- and, unlike the size a new
    pipe is given, it is not reduced when a user is over the soft page limit.
    So this holds on the busy machine that found the bug as well as the quiet
    one that did not.

    A kernel that refuses is not worth failing over here: the write below
    would then block exactly as it used to, which is the deadlock. Rather than
    leave that possible, the refusal falls back to a writer thread -- weaker,
    because a descriptor still being written to always looks readable, but a
    weaker test beats a suite that stops.
    """
    reading_end, writing_end = os.pipe()
    try:
        import fcntl

        fcntl.fcntl(writing_end, SET_PIPE_SIZE, room_for(data))
    except (ImportError, OSError):  # Linux, and there it grows
        pass

    held = pipe_capacity(writing_end)
    if held is not None and held < len(data):

        def fill() -> None:
            try:
                with os.fdopen(writing_end, "wb") as sink:
                    sink.write(data)
            except BrokenPipeError:
                pass

        threading.Thread(target=fill, daemon=True).start()
        return reading_end

    os.write(writing_end, data)
    os.close(writing_end)
    return reading_end


def a_stream(text: str) -> Any:
    """Something `main` can read as standard input, buffer and all.

    A real pipe rather than a `BytesIO`, because `waiting` selects on a file
    descriptor and an in-memory stream has none -- which is the arrangement
    these very tests are here to hold.
    """
    return io.TextIOWrapper(os.fdopen(filled_with(text.encode()), "rb"))


def attributes(shipped: dict[str, Any]) -> dict[str, Any]:
    return {held["key"]: next(iter(held["value"].values())) for held in shipped["attributes"]}


def test_a_record_arrives_as_a_collector_expects_one() -> None:
    shipped = shipping.log_record(A_RECORD)

    assert shipped["body"]["stringValue"] == "an append was refused"
    assert shipped["severityText"] == "WARNING"
    assert shipped["severityNumber"] == 13
    assert shipped["timeUnixNano"] == "1787509927412000000"
    assert attributes(shipped)["logger"] == "inventory.ledger"


def test_the_trace_it_belonged_to_travels_with_it() -> None:
    """The whole reason for shipping these rather than reading them in a
    terminal: a collector can put a request's lines beside its spans.
    """
    shipped = shipping.log_record(A_RECORD)

    assert shipped["traceId"] == A_RECORD["trace_id"]
    assert shipped["spanId"] == A_RECORD["span_id"]


def test_and_is_left_off_entirely_when_nothing_was_tracing() -> None:
    """`trace_id` is on every record this application writes and is empty
    until there is a tracer. An empty string in that field is not a trace id,
    and a collector asked to index one has a lookup that goes nowhere.
    """
    shipped = shipping.log_record({**A_RECORD, "trace_id": "", "span_id": ""})

    assert "traceId" not in shipped
    assert "spanId" not in shipped


def shipped(lines: list[str | None]) -> list[list[dict[str, Any]]]:
    """The batches those lines come to, with the pass-through swallowed."""
    return list(shipping.batches(lines, io.StringIO()))


def test_a_line_that_is_not_ours_is_kept_rather_than_dropped() -> None:
    """An interpreter's dying traceback is never JSON and is the single most
    useful thing in a stream.
    """
    (batch,) = shipped(["Traceback (most recent call last):"])

    assert batch[0]["body"]["stringValue"] == "Traceback (most recent call last):"


def test_the_prefix_compose_adds_does_not_reach_the_collector() -> None:
    """`compose logs` names the service in front of every line, which is not
    part of the record. `console.parse` is what knows that, and is shared.
    """
    (batch,) = shipped([f"backend-1  | {json.dumps(A_RECORD)}"])

    assert batch[0]["body"]["stringValue"] == "an append was refused"


def test_every_line_is_passed_onward_unchanged() -> None:
    """So that a reader can sit after this one in the same pipe. Unchanged is
    the assertion: this ships a copy and edits nothing.
    """
    written = io.StringIO()
    lines: list[str | None] = [json.dumps(A_RECORD), "not json at all"]

    list(shipping.batches(lines, written))

    assert written.getvalue() == "".join(f"{line}\n" for line in lines)


def test_a_batch_is_sent_when_it_is_full() -> None:
    """A migration or a sheet import must not become one post per line."""
    lines: list[str | None] = [json.dumps(A_RECORD)] * (shipping.BATCH + 3)

    assert [len(batch) for batch in shipped(lines)] == [shipping.BATCH, 3]


def test_and_when_the_stream_goes_quiet_before_it_is() -> None:
    """A follow of an idle backend would otherwise always be a request behind."""
    lines: list[str | None] = [json.dumps(A_RECORD), json.dumps(A_RECORD), None, json.dumps(A_RECORD)]

    assert [len(batch) for batch in shipped(lines)] == [2, 1]


def test_a_burst_arriving_in_one_write_is_one_post_rather_than_sixty() -> None:
    """The defect this holds against, measured against a real pipe.

    `select` answers about a descriptor and a text stream buffers in front of
    one, so reading lines through `sys.stdin` made every "is more coming?"
    answer no while sixty lines sat decoded and waiting. `reading` holds the
    undecoded remainder itself, so the question is asked only when the stream
    has genuinely gone quiet.
    """
    burst = "".join(f"{json.dumps(A_RECORD)}\n" for _ in range(60)).encode()

    with os.fdopen(filled_with(burst), "rb") as stream:
        lines = list(shipping.reading(stream, shipping.waiting))

    assert [len(batch) for batch in shipped(lines)] == [60]


def test_the_pipe_holds_everything_these_tests_put_through_it() -> None:
    """The property the burst test above rests on, asserted rather than assumed.

    That test is about sixty lines being ALREADY THERE when the reader starts:
    `select` has nothing to report, and correct code must still make one batch
    of them. If the payload stopped fitting, the write would block and the
    suite would hang again -- and if it were made to fit by filling from a
    thread instead, the descriptor would stay readable throughout and the test
    would pass whether or not `reading` held the remainder, which is the one
    thing it is for.

    So the capacity is held against the real payload rather than a remembered
    number, and this is what goes red if `ROOM` is lowered, if the grow stops
    working, or if the records here get longer.
    """
    largest = "".join(f"{json.dumps(A_RECORD)}\n" for _ in range(60)).encode()
    reading_end = filled_with(largest)
    try:
        held = pipe_capacity(reading_end)
    finally:
        os.close(reading_end)

    assert held is None or held >= len(largest), (
        f"the pipe holds {held} bytes and the largest payload here is {len(largest)}, so filling it "
        "before reading blocks and the suite hangs rather than fails -- inventory-tng-nre8 exactly"
    )


def test_a_line_still_arriving_when_the_stream_ends_is_not_lost() -> None:
    """A writer that dies mid-line has still said something."""
    with os.fdopen(filled_with(b"a half-written li"), "rb") as stream:
        assert list(shipping.reading(stream)) == ["a half-written li"]


def test_the_resource_says_whose_logs_these_are() -> None:
    """It has to be the name the SDK exports spans under, or the collector
    holds two unrelated services and the correlation is gone.
    """
    body = shipping.payload([shipping.log_record(A_RECORD)], A_RESOURCE)
    described = {
        held["key"]: held["value"]["stringValue"] for held in body["resourceLogs"][0]["resource"]["attributes"]
    }

    assert described == A_RESOURCE


def test_and_carries_everything_else_the_sdk_would_have_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Names alone are not enough: compose labels every span
    `deployment.environment=compose`, and a dashboard scoped by it drops every
    log record that does not carry the same.
    """
    monkeypatch.setenv("OTEL_SERVICE_NAME", "inventory-tng-backend")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=compose")

    assert shipping.resource() == {"service.name": "inventory-tng-backend", "deployment.environment": "compose"}


def test_and_unescapes_a_value_exactly_as_the_spans_resource_does(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason this asks the SDK's detector rather than splitting on commas
    itself: a parser of its own was already a second answer, and it did not
    unescape -- so the logs would have been filed under a resource the spans
    did not have, which is the one thing this function exists to prevent.
    """
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=staging%20eu")

    assert shipping.resource()["deployment.environment"] == "staging eu"


def test_and_falls_back_to_the_name_compose_gives_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """This runs on the host, so an environment that says nothing is the
    ordinary case rather than a mistake -- and the detector's own fallback,
    `unknown_service`, would file these under a name no span carries.
    """
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("OTEL_RESOURCE_ATTRIBUTES", raising=False)

    assert shipping.resource()["service.name"] == "inventory-tng-backend"


def test_a_collector_that_is_not_there_is_complained_about_rather_than_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`shipping.post` says why a failure to ship is returned rather than
    raised. This is the assertion that it is.
    """
    complaint = shipping.post([shipping.log_record(A_RECORD)], "http://127.0.0.1:1/v1/logs", A_RESOURCE)

    assert "did not take 1 record" in complaint


def test_a_timestamp_it_cannot_read_becomes_now_rather_than_nothing() -> None:
    """A line without a usable instant is still a line somebody wants to read,
    and a collector will not take one without a time at all.
    """
    from datetime import datetime

    assert shipping.nanoseconds("2026-08-23T14:32:07.412-04:00") == 1787509927412000000

    now = datetime.now().astimezone().timestamp() * 1_000_000_000

    assert abs(shipping.nanoseconds("not a time") - now) < 5_000_000_000


@pytest.mark.parametrize(
    ("held", "expected"),
    [(True, "boolValue"), (3, "intValue"), (1.5, "doubleValue"), ("x", "stringValue"), (["a"], "stringValue")],
)
def test_every_kind_of_value_has_a_spelling(held: Any, expected: str) -> None:
    assert next(iter(shipping.value(held))) == expected


def test_the_documented_command_is_the_module_this_tests() -> None:
    """`scripts/ship-logs`, as docs/observability.md tells somebody to run it."""
    script = REPO_ROOT / "scripts" / "ship-logs"

    assert script.exists()
    assert "python -m inventory_tng.shipping" in script.read_text()


def test_a_record_with_no_level_claims_none() -> None:
    """A line that was not ours has no severity, and inventing one would put
    every stray traceback at the same level as a deliberate warning.
    """
    shipped = shipping.log_record({k: v for k, v in A_RECORD.items() if k != "level"})

    assert "severityText" not in shipped
    assert "severityNumber" not in shipped


def test_a_collector_that_takes_a_batch_is_not_complained_about(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[Any] = []

    class Accepted:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *unused: Any) -> None:
            return None

        def read(self) -> bytes:
            return b""

    def accept(request: Any, timeout: int = 0) -> Any:
        posted.append(json.loads(request.data))
        return Accepted()

    monkeypatch.setattr(shipping.urllib.request, "urlopen", accept)

    assert shipping.post([shipping.log_record(A_RECORD)], "http://collector:4318/v1/logs", A_RESOURCE) == ""
    assert posted[0]["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["traceId"] == A_RECORD["trace_id"]


def test_the_whole_of_it_reads_a_stream_and_posts_where_it_was_told(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main`, driven the way the documented pipe drives it."""
    sent: list[tuple[list[Any], str, dict[str, str]]] = []

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_SERVICE_NAME", "inventory-tng-backend")
    monkeypatch.setattr(shipping.sys, "stdin", a_stream(a_line(A_RECORD)))
    monkeypatch.setattr(
        shipping, "post", lambda records, where, described: sent.append((records, where, described)) or ""
    )

    shipping.main()

    (records, where, described) = sent[0]

    assert where == "http://collector:4318/v1/logs"
    assert described["service.name"] == "inventory-tng-backend"
    assert records[0]["body"]["stringValue"] == "an append was refused"
    assert "an append was refused" in capsys.readouterr().out, "and the line went onward"


def test_a_complaint_goes_to_standard_error_and_never_into_the_stream(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The stream is somebody's log. Putting a failure to ship into it would
    be this tool corrupting the thing it is carrying.
    """
    monkeypatch.setattr(shipping.sys, "stdin", a_stream(a_line(A_RECORD)))
    monkeypatch.setattr(shipping, "post", lambda records, where, described: "nothing took it")

    shipping.main()
    written = capsys.readouterr()

    assert "nothing took it" in written.err
    assert "nothing took it" not in written.out


def test_a_pipe_with_nothing_in_it_is_not_waited_on_for_ever() -> None:
    """`waiting` is what turns a quiet follow into a prompt post."""
    import os

    reading_end, writing_end = os.pipe()
    try:
        with os.fdopen(reading_end, "rb") as stream:
            assert shipping.waiting(stream) is False
            os.write(writing_end, b"a line\n")
            assert shipping.waiting(stream) is True
    finally:
        os.close(writing_end)


# --------------------------------------------------------------------------
# The collector this posts to, and the promise that it costs nothing
# --------------------------------------------------------------------------


def compose() -> dict[str, Any]:
    import yaml

    return yaml.safe_load((REPO_ROOT / "compose.yaml").read_text())


def test_the_collector_is_not_started_by_an_ordinary_compose_up() -> None:
    """A developer who has not asked for telemetry must not pay for a second
    container, and getting started must not grow a step.
    """
    collector = compose()["services"]["collector"]

    assert collector["profiles"] == ["telemetry"]
    for service in ("postgres", "backend", "frontend"):
        assert "profiles" not in compose()["services"][service], service


def test_and_the_image_it_runs_is_pinned() -> None:
    """`latest` is a different stack on a different day, which is the opposite
    of what a worked example is for.
    """
    image = compose()["services"]["collector"]["image"]

    assert image.startswith("docker.io/grafana/otel-lgtm:")
    assert not image.endswith(":latest")


def test_the_document_prints_the_command_that_starts_it() -> None:
    """One documented command, and it has to name the profile the service is
    actually behind.
    """
    written = (REPO_ROOT / "docs" / "observability.md").read_text()

    assert "--profile telemetry" in written
    assert "scripts/ship-logs" in written


def test_a_follow_that_goes_quiet_posts_what_it_has(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the same fix. A writer still holding the pipe open --
    which is what `compose logs -f` is -- must not leave a record sitting in a
    batch until the next one happens along.
    """
    import os

    monkeypatch.setattr(shipping, "QUIET", 0.05)
    reading_end, writing_end = os.pipe()
    os.write(writing_end, f"{json.dumps(A_RECORD)}\n".encode())
    try:
        with os.fdopen(reading_end, "rb") as stream:
            arriving = shipping.reading(stream, shipping.waiting)

            assert next(arriving) == json.dumps(A_RECORD)
            assert next(arriving) is None, "the stream went quiet, so send what there is"
    finally:
        os.close(writing_end)
