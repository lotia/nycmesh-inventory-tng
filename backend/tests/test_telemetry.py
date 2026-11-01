import logging
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import timedelta

import pytest
import structlog
from fastapi.testclient import TestClient
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import (
    InMemoryMetricReader,
    Metric,
    NumberDataPoint,
)
from sqlalchemy import exc
from sqlalchemy.orm import Session
from test_devices import new_device
from test_ledger import Place

from inventory_tng import devices
from inventory_tng.app import create_app
from inventory_tng.devices import RegisteredDevice
from inventory_tng.masking import MASK, Masker
from inventory_tng.settings import Settings
from inventory_tng.telemetry import Telemetry, enabled

ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, False),
        (ENDPOINT, True),
        ({**ENDPOINT, "OTEL_SDK_DISABLED": "true"}, False),
        # An endpoint for traces alone, with traces off by default: nothing.
        ({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318"}, False),
    ],
)
def test_is_off_unless_an_endpoint_is_set(
    environ: dict[str, str], expected: bool
) -> None:
    assert enabled(environ) is expected


def test_names_the_service_unless_told_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with created({}) as named:
        resource = named.meter_provider._sdk_config.resource
        assert resource.attributes["service.name"] == "inventory-tng"
    monkeypatch.setenv("OTEL_SERVICE_NAME", "inventory-tng-staging")
    with created({}) as staging:
        resource = staging.meter_provider._sdk_config.resource
        assert resource.attributes["service.name"] == "inventory-tng-staging"


@contextmanager
def created(environ: dict[str, str]) -> Generator[Telemetry]:
    """Providers that are shut down afterwards, so no export thread outlives
    the test."""
    telemetry = Telemetry.create(environ, metric_reader=InMemoryMetricReader())
    try:
        yield telemetry
    finally:
        for provider in (
            telemetry.meter_provider,
            telemetry.logger_provider,
            telemetry.tracer_provider,
        ):
            if provider:
                provider.shutdown()


@pytest.mark.parametrize(
    ("traces", "expected"), [("", False), ("otlp", True), ("otlp,console", True)]
)
def test_traces_only_when_asked(traces: str, expected: bool) -> None:
    environ = {**ENDPOINT, "OTEL_TRACES_EXPORTER": traces} if traces else ENDPOINT
    with created(environ) as telemetry:
        assert (telemetry.tracer_provider is not None) is expected


Recorded = tuple[TestClient, Telemetry, InMemoryMetricReader, InMemoryLogRecordExporter]


@pytest.fixture(scope="module")
def recorded(database_url: str) -> Iterator[Recorded]:
    """An app with telemetry on, exporting to memory. Module-scoped because
    OpenTelemetry's global providers can be set only once per process, and
    psycopg's instrumentation records to them. The app has its own engine, so
    the instrumentation sees connections made after it starts."""
    reader, exporter = InMemoryMetricReader(), InMemoryLogRecordExporter()
    telemetry = Telemetry.create({}, metric_reader=reader, log_exporter=exporter)
    telemetry.make_global()
    app = create_app(Settings(database_url=database_url))
    telemetry.instrument(app, app.state.engine)
    telemetry.attach_logs(Masker())
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.INFO)
    try:
        with TestClient(app) as client:
            yield client, telemetry, reader, exporter
    finally:
        root.setLevel(level)
        telemetry.shutdown()


def flush(telemetry: Telemetry) -> None:
    assert telemetry.logger_provider is not None
    telemetry.logger_provider.force_flush()


def metrics_in(reader: InMemoryMetricReader) -> Iterator[Metric]:
    data = reader.get_metrics_data()
    assert data is not None
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            yield from scope.metrics


def metric_names(reader: InMemoryMetricReader) -> set[str]:
    return {metric.name for metric in metrics_in(reader)}


def test_measures_requests_queries_and_the_pool(recorded: Recorded) -> None:
    client, _, reader, _ = recorded
    client.get("/api/healthz")
    assert {
        "http.server.request.duration",
        "db.client.operation.duration",
        "db.client.connection.count",
    } <= metric_names(reader)


def test_exports_structlog_lines_as_bodies_and_attributes(recorded: Recorded) -> None:
    client, telemetry, _, exporter = recorded
    client.get("/api/healthz", headers={"X-Request-ID": "otel-1"})
    structlog.get_logger("inventory_tng.test").warning("checked", volunteer_id=7)
    flush(telemetry)

    logs = {
        entry.log_record.body: entry.log_record
        for entry in exporter.get_finished_logs()
    }
    request = logs["request"]
    assert request.attributes is not None
    assert request.attributes["request_id"] == "otel-1"
    assert request.attributes["status"] == 200
    assert not {"_logger", "_name"} & set(request.attributes)
    checked = logs["checked"]
    assert checked.attributes is not None
    assert checked.attributes["volunteer_id"] == 7
    assert checked.severity_text == "WARN"


def test_exported_lines_are_masked(
    recorded: Recorded, database_error: exc.DBAPIError
) -> None:
    _, telemetry, _, exporter = recorded
    exporter.clear()
    with structlog.contextvars.bound_contextvars(session_id="sess-ctx"):
        structlog.get_logger("inventory_tng.test").error(
            "masked",
            password="hunter2",
            email="jane2@example.org",
            exc_info=database_error,
        )
        logging.getLogger("some.library").warning("theirs")
    flush(telemetry)
    logs = {e.log_record.body: e.log_record for e in exporter.get_finished_logs()}
    ours, theirs = logs["masked"].attributes, logs["theirs"].attributes
    assert ours is not None
    assert theirs is not None
    assert (ours["password"], ours["email"], ours["session_id"]) == (MASK, MASK, MASK)
    assert theirs["session_id"] == MASK
    assert ours["exception.type"] == "DataError"
    for name in ("exception.message", "exception.stacktrace"):
        assert "jane@example.org" not in str(ours[name])
        assert MASK in str(ours[name])


def test_logged_fields_never_break_the_record(recorded: Recorded) -> None:
    _, telemetry, _, exporter = recorded
    exporter.clear()
    structlog.get_logger("inventory_tng.test").warning(
        "odd names", args="a", created="c", msg="m", module="n"
    )
    flush(telemetry)
    (entry,) = [
        e for e in exporter.get_finished_logs() if e.log_record.body == "odd names"
    ]
    attributes = entry.log_record.attributes
    assert attributes is not None
    assert (attributes["args"], attributes["msg"], attributes["module"]) == (
        "a",
        "m",
        "n",
    )


def test_library_lines_carry_the_request_id(recorded: Recorded) -> None:
    _, telemetry, _, exporter = recorded
    exporter.clear()
    with structlog.contextvars.bound_contextvars(request_id="lib-1"):
        logging.getLogger("some.library").warning("from a library")
    logging.getLogger("opentelemetry.exporter").warning("not exported")
    flush(telemetry)
    bodies = {e.log_record.body: e.log_record for e in exporter.get_finished_logs()}
    attributes = bodies["from a library"].attributes
    assert attributes is not None
    assert attributes["request_id"] == "lib-1"
    assert "not exported" not in bodies


def test_a_signal_can_be_turned_off() -> None:
    with created({**ENDPOINT, "OTEL_LOGS_EXPORTER": "none"}) as telemetry:
        assert telemetry.logger_provider is None


def test_a_signal_with_its_own_endpoint_starts_alone() -> None:
    environ = {
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318/v1/traces",
    }
    with created(environ) as traces_only:
        assert traces_only.tracer_provider is not None
        assert traces_only.logger_provider is None


def counted(reader: InMemoryMetricReader, name: str) -> dict[tuple[object, ...], int]:
    """A counter's totals, by its attributes' values."""
    return {
        tuple(sorted((point.attributes or {}).items())): int(point.value)
        for metric in metrics_in(reader)
        if metric.name == name
        for point in metric.data.data_points
        if isinstance(point, NumberDataPoint)
    }


def test_counts_registrations_and_refusals(recorded: Recorded) -> None:
    client, _, reader, _ = recorded
    devices._limiter.storage.reset()
    with Session(client.app.state.engine) as session, session.begin():  # ty: ignore[unresolved-attribute]
        _, phrase = devices.create_code(
            session, max_uses=1, lifetime=timedelta(hours=1)
        )
    assert client.post("/api/devices", json={"code": phrase}).status_code == 201
    assert client.post("/api/devices", json={"code": phrase}).status_code == 400
    client.app.add_api_route("/api/needs-a-device", needs_a_device)  # ty: ignore[unresolved-attribute]
    assert client.get("/api/needs-a-device").status_code == 403

    assert sum(counted(reader, "inventory.devices.registered").values()) >= 1
    assert (
        counted(reader, "inventory.registrations.rejected")[(("reason", "used_up"),)]
        >= 1
    )
    assert (
        counted(reader, "inventory.devices.refused")[(("reason", "unregistered"),)] >= 1
    )


def needs_a_device(device: RegisteredDevice) -> int:
    return device.id


def test_counts_batches_written_replayed_and_refused(recorded: Recorded) -> None:
    client, _, reader, _ = recorded
    engine = client.app.state.engine  # ty: ignore[unresolved-attribute]
    with Session(engine, expire_on_commit=False) as session, session.begin():
        place = Place(session)
        _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    batch = place.batch()
    assert client.post("/api/transactions", json=batch).status_code == 201
    assert client.post("/api/transactions", json=batch).status_code == 200
    unknown = place.batch(lines=[{"item_id": 999999, "quantity": "1"}])
    assert client.post("/api/transactions", json=unknown).status_code == 422
    client.cookies.clear()

    kind = (("kind", "move"),)
    assert counted(reader, "inventory.transactions.recorded")[kind] >= 1
    assert counted(reader, "inventory.transactions.replayed")[kind] >= 1
    assert (
        counted(reader, "inventory.transactions.refused")[(("reason", "invalid"),)] >= 1
    )
    assert "inventory.transaction.lines" in metric_names(reader)
