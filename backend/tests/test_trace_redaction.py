"""Spans leave the process redacted, except in development or for a while
(docs/decisions/0008, inventory-tng-8i4.40): an allow-list of attributes,
exception events without their message, statuses without their description,
and secrets in the query string masked always."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import Link, Status, StatusCode
from sqlalchemy import text
from structlog.testing import capture_logs

from inventory_tng.app import create_app
from inventory_tng.masking import MASK
from inventory_tng.settings import LogConfig, Redaction, Settings, SettingsError
from inventory_tng.telemetry import (
    KEPT_ATTRIBUTES,
    KEPT_EVENT_ATTRIBUTES,
    REDACTION_MARK,
    RedactingSpanExporter,
    Telemetry,
)

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
# What an instrumentation records that may name someone, as 8i4.39 found.
PERSONAL = {
    "client.address": "192.0.2.7",
    "client.port": 50000,
    "url.path": "/admin/volunteers/jane",
    "url.query": "code=correct-horse&token=t0ken&q=jane%40example.org",
    "user_agent.original": "Mozilla/5.0 Jane-Phone",
    "db.query.text": "SELECT 'Jane Doe' AS literal_name",
    "http.request.header.cookie": "inventory_session=abc",
}
KEPT = {"http.route": "/admin/volunteers/{volunteer_id}", "http.request.method": "GET"}


def exported(
    redactor_for: Callable[[InMemorySpanExporter], RedactingSpanExporter],
) -> ReadableSpan:
    """One span, with personal data, a failure quoting a value, and a
    described error status, as the redactor lets it out."""
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(redactor_for(memory)))
    with provider.get_tracer("test").start_as_current_span(
        "GET /admin/volunteers/{volunteer_id}",
        attributes={**PERSONAL, **KEPT},
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        span.record_exception(ValueError('invalid input: "jane@example.org"'))
        span.set_status(Status(StatusCode.ERROR, 'value "jane@example.org"'))
    (only,) = memory.get_finished_spans()
    return only


def redactor(
    *, redacting: bool, development: bool = False, secrets: bool = True
) -> Callable[[InMemorySpanExporter], RedactingSpanExporter]:
    """Redacting or not as asked: in development by TRACE_REDACTION, else by
    a TRACE_REDACTION_OFF_UNTIL still to come."""
    if development:
        traces = Redaction(switch=redacting)
    else:
        traces = Redaction(off_until=None if redacting else NOW + timedelta(hours=1))
    return lambda memory: RedactingSpanExporter(
        memory,
        traces=traces,
        development=development,
        secrets=secrets,
        now=lambda: NOW,
    )


def test_a_redacted_span_keeps_only_the_allow_list() -> None:
    span = exported(redactor(redacting=True))
    assert dict(span.attributes or {}) == KEPT
    (event,) = span.events
    assert set(event.attributes or {}) <= KEPT_EVENT_ATTRIBUTES
    assert (event.attributes or {})["exception.type"] == "ValueError"
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description is None
    assert "jane" not in repr((span.attributes, span.events, span.status)).lower()


def test_unredacted_in_development_all_but_secrets() -> None:
    span = exported(redactor(redacting=False, development=True))
    attributes = dict(span.attributes or {})
    assert attributes["user_agent.original"] == PERSONAL["user_agent.original"]
    assert attributes["db.query.text"] == PERSONAL["db.query.text"]
    # The registration phrase and a reset token never leave; the rest does.
    masked = "code=%5Bmasked%5D&token=%5Bmasked%5D&q=jane%40example.org"
    assert attributes["url.query"] == masked
    assert attributes["http.request.header.cookie"] == MASK
    assert REDACTION_MARK not in attributes
    assert span.status.description == 'value "jane@example.org"'


def test_unmasked_secrets_leave_too() -> None:
    span = exported(redactor(redacting=False, development=True, secrets=False))
    assert dict(span.attributes or {})["url.query"] == PERSONAL["url.query"]


def test_off_outside_development_is_warned_hourly() -> None:
    clock = [NOW]
    exporter = RedactingSpanExporter(
        InMemorySpanExporter(),
        traces=Redaction(off_until=NOW + timedelta(hours=2)),
        development=False,
        now=lambda: clock[0],
    )
    with capture_logs() as logs:
        exporter.export([])
        clock[0] = NOW + timedelta(minutes=59)
        exporter.export([])
        clock[0] = NOW + timedelta(hours=1)
        exporter.export([])
    # The starting line said so (server.py); then an hour on, not at each
    # export.
    assert [(line["event"], line["log_level"]) for line in logs] == [
        ("trace redaction is off", "warning")
    ]


def test_off_outside_development_is_marked() -> None:
    span = exported(redactor(redacting=False))
    assert dict(span.attributes or {})[REDACTION_MARK] == "off"


def test_redaction_comes_back_on_by_itself_when_the_time_runs_out() -> None:
    off = Redaction(off_until=NOW + timedelta(hours=1))
    clock = [NOW]
    memory = InMemorySpanExporter()
    exporter = RedactingSpanExporter(
        memory, traces=off, development=False, now=lambda: clock[0]
    )
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("before", attributes=PERSONAL):
        pass
    clock[0] = NOW + timedelta(hours=1)
    with tracer.start_as_current_span("after", attributes=PERSONAL):
        pass
    before, after = memory.get_finished_spans()
    assert "user_agent.original" in (before.attributes or {})
    assert dict(after.attributes or {}) == {}


@pytest.mark.parametrize(
    ("development", "switch", "redacting"),
    [(True, None, False), (True, True, True), (False, None, True), (False, True, True)],
)
def test_who_redacts(development: bool, switch: bool | None, redacting: bool) -> None:
    assert (
        Redaction(switch=switch).redacting(development=development, now=NOW)
        is redacting
    )


@pytest.mark.parametrize(
    ("environ", "problem"),
    [
        ({"TRACE_REDACTION": "maybe"}, "must be on or off"),
        ({"TRACE_REDACTION_OFF_UNTIL": "tomorrow"}, "must be an ISO 8601 time"),
        ({"TRACE_REDACTION_OFF_UNTIL": "2026-11-07T13:00"}, "must say its offset"),
        ({"TRACE_REDACTION_OFF_UNTIL": "2026-11-07T18:01Z"}, "at most 6 hours"),
        (
            {
                "TRACE_REDACTION_OFF_UNTIL": "2026-11-07T15:01Z",
                "REDACTION_OFF_MAX_HOURS": "3",
            },
            "at most 3 hours",
        ),
        ({"REDACTION_OFF_MAX_HOURS": "0"}, "from 1 to 168"),
    ],
)
def test_switches_that_are_refused(environ: dict[str, str], problem: str) -> None:
    with pytest.raises(SettingsError, match=problem):
        Redaction.from_environ("TRACE", environ, now=NOW)


def test_off_until_within_the_cap_or_already_past() -> None:
    soon = Redaction.from_environ(
        "TRACE", {"TRACE_REDACTION_OFF_UNTIL": "2026-11-07T18:00Z"}, now=NOW
    )
    assert soon.off_until == NOW + timedelta(hours=6)
    assert Redaction.from_environ(
        "TRACE", {"TRACE_REDACTION_OFF_UNTIL": "2026-11-07T11:00Z"}, now=NOW
    ).redacting(development=False, now=NOW)


def test_off_for_good_only_in_development(database_url: str) -> None:
    off = Redaction(switch=False)
    Settings(database_url=database_url, development=True, traces=off)
    with pytest.raises(SettingsError, match="TRACE_REDACTION=off works only"):
        Settings(database_url=database_url, traces=off)


@pytest.fixture
def traced(
    database_url: str,
) -> Iterator[tuple[TestClient, Telemetry, InMemorySpanExporter]]:
    """The app, instrumented, its spans redacted into memory, with a route
    that runs a query naming someone and one whose query fails quoting a
    value. The client address would be logged (LOG_CLIENT_ADDRESS)."""
    memory = InMemorySpanExporter()
    # A sampler of the environment's: so every request is traced here.
    telemetry = Telemetry.create(
        {"OTEL_TRACES_SAMPLER": "always_on"}, span_exporter=memory
    )
    settings = Settings(
        database_url=database_url, logging=LogConfig(client_address=True)
    )
    app = create_app(settings)
    telemetry.instrument(app, app.state.db)

    def named() -> dict[str, str]:
        with app.state.db.engine.connect() as connection:
            connection.execute(text("SELECT 'Jane Doe' AS literal_name"))
        return {}

    def failing() -> None:
        with app.state.db.engine.connect() as connection:
            connection.execute(text("SELECT 'jane@example.org'::int"))

    app.add_api_route("/named", named)
    app.add_api_route("/failing", failing)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, telemetry, memory
    finally:
        telemetry.shutdown()


def test_the_app_s_own_spans_leave_redacted(
    traced: tuple[TestClient, Telemetry, InMemorySpanExporter],
) -> None:
    client, telemetry, memory = traced
    headers = {"User-Agent": "Mozilla/5.0 Jane-Phone"}
    client.get("/named", params={"q": "jane@example.org"}, headers=headers)
    client.get("/failing", headers=headers)
    assert telemetry.tracer_provider is not None
    telemetry.tracer_provider.force_flush()
    spans = memory.get_finished_spans()
    assert spans
    for span in spans:
        assert set(span.attributes or {}) <= KEPT_ATTRIBUTES, span.name
        for event in span.events:
            assert set(event.attributes or {}) <= KEPT_EVENT_ATTRIBUTES
        assert span.status.description is None
        seen = repr((span.name, span.attributes, span.events)).lower()
        for word in ("jane", "mozilla", "testclient"):
            assert word not in seen, (span.name, word)
    assert any(
        (event.attributes or {}).get("exception.type")
        for span in spans
        for event in span.events
    )


def test_flushing_reaches_the_exporter() -> None:
    flushed: list[int] = []

    class Counting(InMemorySpanExporter):
        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            flushed.append(timeout_millis)
            return True

    exporter = RedactingSpanExporter(Counting())
    assert exporter.force_flush(5) is True
    assert flushed == [5]


def test_a_redacted_span_s_links_lose_their_attributes() -> None:
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(redactor(redacting=True)(memory)))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("first") as first:
        pass
    link = Link(first.get_span_context(), {"messaging.message.id": "jane"})
    with tracer.start_as_current_span("second", links=[link]):
        pass
    _, second = memory.get_finished_spans()
    (kept,) = second.links
    assert kept.context == first.get_span_context()
    assert not kept.attributes
