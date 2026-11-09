import logging
import uuid
from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pyotp
import pytest
import structlog
from fastapi.testclient import TestClient
from httpx2 import Response as Answer
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import (
    InMemoryMetricReader,
    Metric,
    NumberDataPoint,
)
from sqlalchemy import exc, select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import accounts, devices, labels, ledger, mail
from inventory_tng.admin.counts import ADJUSTMENTS, COUNTS
from inventory_tng.app import create_app
from inventory_tng.db import RuleBroken
from inventory_tng.devices import RegisteredDevice
from inventory_tng.masking import MASK, Masker
from inventory_tng.models import AdjustmentReason, LedgerTransaction
from inventory_tng.settings import Settings
from inventory_tng.telemetry import Telemetry, enabled

from helpers.apps import on_session
from helpers.devices import new_device
from helpers.mail import FailingMail, SentMail
from helpers.sign_in import PASSWORD, code, new_admin, sign_in_with
from helpers.stock import Place, received, retire, stocked, write

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
        # Over https, so the sign-in cookie comes back.
        with TestClient(app, base_url="https://testserver") as client:
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


def test_counts_registrations_and_refusals(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
    _, phrase = devices.create_code(session, max_uses=1, lifetime=timedelta(hours=1))
    assert client.post("/api/devices", json={"code": phrase}).status_code == 201
    assert client.post("/api/devices", json={"code": phrase}).status_code == 400
    client.app.add_api_route("/api/needs-a-device", needs_a_device)  # ty: ignore[unresolved-attribute]
    client.cookies.clear()  # The registration's cookie came back.
    assert client.get("/api/needs-a-device").status_code == 403

    assert sum(counted(reader, "inventory.devices.registered").values()) >= 1
    assert (
        counted(reader, "inventory.registrations.rejected")[(("reason", "used_up"),)]
        >= 1
    )
    assert (
        counted(reader, "inventory.devices.refused")[(("reason", "unregistered"),)] >= 1
    )


@pytest.fixture
def rolled_back(recorded: Recorded, session: Session) -> Iterator[Recorded]:
    """The recorded app on the test's session, so what it writes is rolled
    back, as with make_app: an administrator left behind would close setup
    for every later test."""
    client = recorded[0]
    overrides = client.app.dependency_overrides  # ty: ignore[unresolved-attribute]
    added = on_session(session)
    overrides |= added
    try:
        yield recorded
    finally:
        for dependency in added:
            overrides.pop(dependency)
        client.cookies.clear()


def test_counts_sign_ins_and_confirmations(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
    _, key = new_admin(session)
    assert key is not None
    form = {"username": "ada", "password": PASSWORD, "code": ""}
    assert client.post("/admin/sign-in", data=form).status_code == 400
    form["code"] = pyotp.TOTP(key).now()
    signed_in = client.post("/admin/sign-in", data=form, follow_redirects=False)
    assert signed_in.status_code == 303
    assert (
        client.post("/admin/confirm", data={"confirm_code": "nope"}).status_code == 400
    )

    sign_ins = counted(reader, "inventory.admin.sign_ins")
    assert sign_ins[(("outcome", "refused"), ("reason", "bad_code"))] >= 1
    assert sign_ins[(("factor", "totp"), ("outcome", "signed_in"))] >= 1
    confirmations = counted(reader, "inventory.admin.confirmations")
    assert confirmations[(("outcome", "refused"),)] >= 1


def test_counts_recoveries(rolled_back: Recorded, session: Session) -> None:
    _, _, reader, _ = rolled_back
    new_admin(session)
    token, now = "a recovery token the operator set, long", datetime.now(UTC)
    with pytest.raises(accounts.TokenRefused):
        accounts.recover(session, token, "nope", "ada", "192.0.2.90", now)
    accounts.recover(session, token, token, "ada", "192.0.2.90", now)
    recoveries = counted(reader, "inventory.admin.recoveries")
    assert recoveries[(("outcome", "bad_token"),)] >= 1
    assert recoveries[(("outcome", "made"),)] >= 1


def test_counts_sign_in_resets(rolled_back: Recorded, session: Session) -> None:
    _, _, reader, _ = rolled_back
    ada, _ = new_admin(session)
    ben, _ = new_admin(session, "ben")
    carol, _ = new_admin(session, "carol")
    now = datetime.now(UTC)
    accounts.reset_admin_sign_in(session, ben.id, ada.id, now)
    carol.retired_at = now
    accounts.restore_admin(session, carol.id, ada.id, now)
    resets = counted(reader, "inventory.admin.sign_in_resets")
    assert resets[(("action", "reset"),)] >= 1
    assert resets[(("action", "restore"),)] >= 1


def test_counts_count_lines_withheld(rolled_back: Recorded, session: Session) -> None:
    """Radios counted while three of them moved are not written: they are
    counted again."""
    _, _, reader, _ = rolled_back
    ada, _ = new_admin(session)
    place = stocked(session)
    as_of = received(session, place).id
    write(session, place)
    radios = {place.item.id: Decimal(5)}
    result = ledger.record_count(
        session, uuid.uuid4(), ada.id, place.store.id, radios, as_of
    )
    assert result.moved == {place.item.id: (Decimal(0), Decimal(3))}
    ledger.corrected(ada.id, place.store.id, result.written, result.withheld)
    assert counted(reader, "inventory.count.lines.withheld")[()] >= 1


def recorded_by_kind(reader: InMemoryMetricReader) -> tuple[int, int]:
    """inventory.counts.recorded's totals so far: counts, adjustments."""
    totals = counted(reader, "inventory.counts.recorded")
    return totals.get((("kind", "count"),), 0), totals.get((("kind", "adjustment"),), 0)


def counting(
    client: TestClient, session: Session
) -> tuple[Place, Callable[[], Answer]]:
    """Ada signed in, and a count of five radios that finds three, and a
    metre of cable: two transactions, one out of the store, one into it."""
    _, key = new_admin(session)
    sign_in_with(client, code=code(key))
    place = stocked(session)
    form = {
        "key": str(uuid.uuid4()),
        "as_of": str(ledger.moment(session, place.store.id)),
        f"counted_{place.item.id}": "3",
        f"counted_{place.cable.id}": "1",
    }
    path = f"{COUNTS}/{place.store.id}"
    return place, lambda: client.post(path, data=form, follow_redirects=False)


def test_counts_counts_and_adjustments_written_once(
    rolled_back: Recorded, session: Session
) -> None:
    """Each transaction once it is committed; sent again, none."""
    client, _, reader, _ = rolled_back
    place, count = counting(client, session)
    before = recorded_by_kind(reader)
    assert count().status_code == 303
    assert count().status_code == 303
    reason = session.scalars(select(AdjustmentReason.id)).first()
    form = {
        "key": str(uuid.uuid4()),
        "location_id": str(place.store.id),
        "item_id": str(place.item.id),
        "amount": "1",
        "direction": "off",
        "reason_id": str(reason),
    }
    for _ in range(2):
        adjusted = client.post(ADJUSTMENTS, data=form, follow_redirects=False)
        assert adjusted.status_code == 303
    counts, adjustments = recorded_by_kind(reader)
    assert (counts - before[0], adjustments - before[1]) == (2, 1)


def test_a_count_refused_after_its_first_transaction_records_nothing(
    rolled_back: Recorded, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count into the store is written, then the count out of it is
    refused: neither is kept, counted or logged (inventory-tng-8i4.104).
    No rule can refuse that second one for real, as what it takes out has
    moved, which fixes its unit, so a stand-in refuses it."""
    client, _, reader, _ = rolled_back
    place, count = counting(client, session)
    real = ledger.insert_transaction

    def refusing_the_second(
        session: Session,
        key: uuid.UUID,
        header: Mapping[str, object],
        lines: Mapping[int, Decimal],
    ) -> LedgerTransaction | None:
        if header.get("from_location_id") is not None:
            raise RuleBroken("movement_whole_units")
        return real(session, key, header, lines)

    monkeypatch.setattr(ledger, "insert_transaction", refusing_the_second)
    before = recorded_by_kind(reader)
    with capture_logs() as logs:
        refused = count()
    assert refused.status_code == 400
    assert recorded_by_kind(reader) == before
    assert not [line for line in logs if line["event"] == "count recorded"]
    assert ledger.expected(session, place.store.id) == {place.item.id: Decimal(5)}


def test_counts_batches_at_a_retired_place_by_side(
    rolled_back: Recorded, session: Session
) -> None:
    """A move into the van after it was retired, then one out of it."""
    client, _, reader, _ = rolled_back
    place = Place(session)
    retire(session, place.van)
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    before = counted(reader, "inventory.transactions.at_retired_place")
    out = {"from_location_id": place.van.id, "to_location_id": place.store.id}
    for batch in (place.batch(), place.batch(**out)):
        assert client.post("/api/transactions", json=batch).status_code == 201
    client.cookies.clear()
    after = counted(reader, "inventory.transactions.at_retired_place")
    for side in ("to", "from"):
        key = (("side", side),)
        assert after[key] - before.get(key, 0) == 1


def needs_a_device(device: RegisteredDevice) -> int:
    return device.id


def test_counts_batches_written_replayed_and_refused(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
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


def test_counts_mail_sent_and_failed(recorded: Recorded) -> None:
    _, _, reader, _ = recorded

    message = mail.password_changed("ada@example.org", 1)
    mail.deliver(SentMail(), message)
    mail.deliver(FailingMail(TimeoutError()), message)
    sent = counted(reader, "inventory.mail.sent")
    assert sent[(("kind", "password_changed"), ("outcome", "sent"))] >= 1
    assert sent[(("kind", "password_changed"), ("outcome", "failed"))] >= 1


def test_counts_labels_made_printed_and_revoked(
    rolled_back: Recorded, session: Session
) -> None:
    _, _, reader, _ = rolled_back
    place = stocked(session)
    made = labels.make_for_item(session, place.item.id)
    assert made is not None
    assert labels.make_for_item(session, place.item.id) == made  # Not again.
    assert labels.make_for_place(session, place.store.id) is not None
    assert labels.record_printing(labels.printable(session, [made.id]), 5) == 5
    assert labels.revoke(session, made.id, datetime.now(UTC))
    by_names = counted(reader, "inventory.labels.made")
    assert by_names[(("names", "item"),)] >= 1
    assert by_names[(("names", "location"),)] >= 1
    assert sum(counted(reader, "inventory.labels.printed").values()) >= 5
    assert sum(counted(reader, "inventory.labels.revoked").values()) >= 1
