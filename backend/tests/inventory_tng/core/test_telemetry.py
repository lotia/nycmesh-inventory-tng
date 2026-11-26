import logging
import uuid
from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pyotp
import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response as Answer
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import (
    HistogramDataPoint,
    InMemoryMetricReader,
    Metric,
    NumberDataPoint,
)
from sqlalchemy import exc, func, select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.admins import accounts, mail_messages
from inventory_tng.app import create_app
from inventory_tng.catalogue import items, unit_changes
from inventory_tng.catalogue.paths import CATEGORIES, ITEMS, unit_recount
from inventory_tng.core import db_sessions, mail
from inventory_tng.core.db_errors import NoSession, RuleBroken
from inventory_tng.core.masking import MASK, Masker
from inventory_tng.core.settings import RateLimits, Settings
from inventory_tng.core.telemetry import Telemetry, enabled
from inventory_tng.devices import devices
from inventory_tng.devices.guards import RegisteredDevice
from inventory_tng.labels import labels
from inventory_tng.models.catalogue import Category, ItemPackage
from inventory_tng.models.devices import Device
from inventory_tng.models.places import Location
from inventory_tng.models.stock import (
    LedgerTransaction,
    Movement,
    OpenPackage,
    OverRecorded,
)
from inventory_tng.scan import page_builds, page_telemetry_api
from inventory_tng.stock import counts, ledger, moments, open_packages, over_recorded
from inventory_tng.stock.paths import ADJUSTMENTS, COUNTS
from inventory_tng.web import security_headers

from helpers.apps import on_session
from helpers.csp_reports import SHAPES
from helpers.devices import new_device
from helpers.items import shown_item_form, unit_answered
from helpers.mail import NO_MAIL, FailingMail, SentMail
from helpers.markup import hidden_fields
from helpers.pools import small_database
from helpers.rows import add, breaks, item_fields, shown, tag
from helpers.sign_in import (
    PASSWORD,
    TOTP_KEYS,
    codes_with_left,
    new_admin,
    sign_in_with,
)
from helpers.stock import (
    Place,
    Spools,
    adjustment_form,
    count_form,
    count_form_at,
    cut,
    length,
    raise_check,
    received,
    report,
    retire,
    sealed_line,
    stocked,
    to_feet,
    write,
)

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
    app = create_app(Settings(database_url=database_url), totp_keys=TOTP_KEYS)
    telemetry.instrument(app, app.state.db)
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


def added(
    reader: InMemoryMetricReader,
    name: str,
    before: dict[tuple[object, ...], int],
    reading: Callable[
        [InMemoryMetricReader, str], dict[tuple[object, ...], int]
    ] = counted,
) -> dict[tuple[object, ...], int]:
    """A counter's totals since `before` (counted), or a histogram's numbers
    of values (histogram_counts): the reader keeps them for the module, and
    more than one test may add to a metric."""
    now = reading(reader, name)
    return {
        key: n - before.get(key, 0) for key, n in now.items() if n != before.get(key, 0)
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


def test_counts_registrations_again_with_a_code(
    rolled_back: Recorded, session: Session
) -> None:
    """A code's first phone, then one again with it, as after cleared
    browser data; another like it is a code of its own, so its first phone
    is first."""
    client, _, reader, _ = rolled_back
    before = counted(reader, "inventory.devices.registered")
    code, phrase = devices.create_code(session, max_uses=3, lifetime=timedelta(hours=1))
    for _ in range(2):
        client.cookies.clear()
        assert client.post("/api/devices", json={"code": phrase}).status_code == 201
    made = devices.another_like(session, code.id)
    assert made is not None
    client.cookies.clear()
    assert client.post("/api/devices", json={"code": made[1]}).status_code == 201
    assert added(reader, "inventory.devices.registered", before) == {
        (("use", "first"),): 2,
        (("use", "again"),): 1,
    }


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
        accounts.recover(
            session, token, "nope", "ada", "192.0.2.90", now, NO_MAIL, RateLimits()
        )
    accounts.recover(
        session, token, token, "ada", "192.0.2.90", now, NO_MAIL, RateLimits()
    )
    session.commit()  # Counted once committed.
    recoveries = counted(reader, "inventory.admin.recoveries")
    assert recoveries[(("outcome", "bad_token"),)] >= 1
    assert recoveries[(("outcome", "made"),)] >= 1


def test_counts_sign_in_resets(rolled_back: Recorded, session: Session) -> None:
    _, _, reader, _ = rolled_back
    ada, _ = new_admin(session)
    ben, _ = new_admin(session, "ben")
    now = datetime.now(UTC)
    accounts.reset_admin_sign_in(session, ben.id, ada.id, now, NO_MAIL)
    session.commit()  # Counted once committed.
    resets = counted(reader, "inventory.admin.sign_in_resets")
    assert resets[(("action", "reset"),)] >= 1


def test_counts_recovery_codes_made_and_reminders(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
    ada, _ = new_admin(session)
    left = codes_with_left(session, ada.id, 2)[0]
    sign_in_with(client, code=left)
    events = counted(reader, "inventory.admin.recovery_codes")
    assert events[(("event", "made"),)] >= 1
    assert events[(("event", "reminded"),)] >= 1


def test_counts_registration_codes_made(
    rolled_back: Recorded, session: Session
) -> None:
    _, _, reader, _ = rolled_back
    devices.create_codes(session, 2, max_uses=1, lifetime=timedelta(hours=1))
    code, _ = devices.create_code(session, max_uses=1, lifetime=timedelta(hours=1))
    devices.another_like(session, code.id)
    session.commit()  # Counted once committed.
    made = counted(reader, "inventory.registration_codes.made")
    assert made[(("how", "new"),)] >= 3
    assert made[(("how", "another"),)] >= 1


def test_counts_devices_revoked(rolled_back: Recorded, session: Session) -> None:
    _, _, reader, _ = rolled_back
    one, _ = new_device(session)
    many, _ = new_device(session)
    devices.revoke_device(session, one.id)
    devices.revoke_devices(
        session, devices.DeviceFilter(), [many.id], datetime.now(UTC)
    )
    session.commit()  # Counted once committed.
    revoked = counted(reader, "inventory.devices.revoked")
    assert revoked[(("how", "one"),)] >= 1
    assert revoked[(("how", "bulk"),)] >= 1


def test_counts_activity(rolled_back: Recorded, session: Session) -> None:
    _, _, reader, _ = rolled_back
    ada, _ = new_admin(session)
    ben, _ = new_admin(session, "ben")
    accounts.retire_admin(session, ben.id, ada.id, datetime.now(UTC), NO_MAIL)
    session.commit()  # Counted once committed.
    told = counted(reader, "inventory.admin.activity")
    assert told[(("kind", "retired"),)] >= 1


def test_counts_count_lines_withheld(rolled_back: Recorded, session: Session) -> None:
    """Radios counted while three of them moved are not written: they are
    counted again."""
    _, _, reader, _ = rolled_back
    ada, _ = new_admin(session)
    place = stocked(session)
    received(session, place)
    shown = moments.moment(session, place.store.id)
    write(session, place)
    radios = [(place.item.id, Decimal(5))]
    result = counts.record_count(
        session, uuid.uuid4(), ada.id, place.store.id, radios, shown
    )
    assert result.moved == {place.item.id: (Decimal(0), Decimal(3))}
    session.commit()
    assert counted(reader, "inventory.count.lines.withheld")[()] >= 1


def test_counts_the_places_a_count_was_saved_at(
    rolled_back: Recorded, session: Session
) -> None:
    """A store and its shelf counted on one form: one count of two places;
    sent again, none (inventory-tng-8i4.51.4)."""
    client, _, reader, _ = rolled_back
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    place = stocked(session)
    add(session, Location(name=f"Shelf {tag()}", kind="area", parent_id=place.store.id))
    path = f"{COUNTS}/{place.store.id}"
    form = count_form_at(client, place.store.id)
    name = "inventory.count.places"

    def measured() -> tuple[int, float]:
        """How many counts were measured, and their places in all."""
        points = [
            point
            for metric in metrics_in(reader)
            if metric.name == name
            for point in metric.data.data_points
            if isinstance(point, HistogramDataPoint)
        ]
        return sum(p.count for p in points), sum(p.sum for p in points)

    before = measured()
    for _ in range(2):
        assert client.post(path, data=form, follow_redirects=False).status_code == 303
    after = measured()
    assert (after[0] - before[0], after[1] - before[1]) == (1, 2)


def test_counts_the_areas_brought_back_from_a_count_form(
    rolled_back: Recorded, session: Session
) -> None:
    """A retired shelf emptied while its store was counted, brought back
    from the form shown again (inventory-tng-ygk5)."""
    client, _, reader, _ = rolled_back
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    place = stocked(session)
    shelf = add(
        session, Location(name=f"Shelf {tag()}", kind="area", parent_id=place.store.id)
    )
    retire(session, shelf)
    radio = [{"item_id": place.item.id, "quantity": "1", "unit": place.item.unit}]
    write(
        session,
        place,
        from_location_id=place.store.id,
        to_location_id=shelf.id,
        lines=radio,
    )
    path = f"{COUNTS}/{place.store.id}"
    form = count_form_at(client, place.store.id)
    write(
        session,
        place,
        from_location_id=shelf.id,
        to_location_id=place.van.id,
        lines=radio,
    )
    refused = client.post(path, data=form)
    assert refused.status_code == 409
    name = "inventory.count.areas_brought_back"
    before = counted(reader, name).get((), 0)
    back = client.post(
        f"{path}/bring-back", data=count_form(refused) | {"area_id": str(shelf.id)}
    )
    assert back.status_code == 200
    assert counted(reader, name)[()] == before + 1


def test_counts_the_moves_fixed_on_a_count_form(
    rolled_back: Recorded, session: Session
) -> None:
    """A retired shelf emptied into another while its store was counted:
    fewer arrived, then, on a second such count, more (inventory-tng-ygk5).
    Sent twice, each is counted once."""
    client, _, reader, _ = rolled_back
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    name = "inventory.count.moves_fixed"
    for arrived, direction in (("1", "lost"), ("3", "found")):
        place = stocked(session)
        shelves = [
            add(
                session,
                Location(name=f"Shelf {tag()}", kind="area", parent_id=place.store.id),
            )
            for _ in range(2)
        ]
        gone, there = shelves
        retire(session, gone)
        radios = [{"item_id": place.item.id, "quantity": "2", "unit": place.item.unit}]
        write(
            session,
            place,
            from_location_id=place.store.id,
            to_location_id=gone.id,
            lines=radios,
        )
        path = f"{COUNTS}/{place.store.id}"
        form = count_form_at(client, place.store.id)
        write(
            session,
            place,
            from_location_id=gone.id,
            to_location_id=there.id,
            lines=radios,
        )
        refused = client.post(path, data=form)
        assert refused.status_code == 409
        fix = {f"at_{there.id}_arrived_{gone.id}_loose_{place.item.id}": arrived}
        before = counted(reader, name).get((("direction", direction),), 0)
        for _ in range(2):
            sent = client.post(
                path, data=count_form(refused) | fix, follow_redirects=False
            )
            assert sent.status_code == 303
        assert counted(reader, name)[(("direction", direction),)] == before + 1


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
    sign_in_with(client, key=key)
    place = stocked(session)
    form = count_form_at(client, place.store.id) | {
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
    counts_before, adjustments_before = recorded_by_kind(reader)
    assert count().status_code == 303
    assert count().status_code == 303
    form = adjustment_form(session, place.store, place.item)
    for _ in range(2):
        adjusted = client.post(ADJUSTMENTS, data=form, follow_redirects=False)
        assert adjusted.status_code == 303
    counts_after, adjustments_after = recorded_by_kind(reader)
    assert counts_after - counts_before == 2
    assert adjustments_after - adjustments_before == 1


def test_a_count_refused_after_its_first_transaction_records_nothing(
    rolled_back: Recorded, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count into the store is written, then the count out of it is
    refused: neither is kept, counted or logged (inventory-tng-8i4.104).
    No rule can refuse that second one for real, as what it takes out has
    moved, which fixes its unit, so a stand-in refuses it."""
    client, _, reader, _ = rolled_back
    place, count = counting(client, session)
    real = counts.insert_transaction

    def refusing_the_second(
        session: Session,
        key: uuid.UUID,
        header: Mapping[str, object],
        lines: ledger.Lines,
    ) -> LedgerTransaction | None:
        if header.get("from_location_id") is not None:
            raise RuleBroken("movement_whole_units")
        return real(session, key, header, lines)

    monkeypatch.setattr(counts, "insert_transaction", refusing_the_second)
    before = recorded_by_kind(reader)
    with capture_logs() as logs:
        refused = count()
    assert refused.status_code == 400
    assert recorded_by_kind(reader) == before
    assert not [line for line in logs if line["event"] == "count recorded"]
    assert moments.expected(session, place.store.id) == {place.item.id: Decimal(5)}


def corrections_written(session: Session) -> tuple[int, int]:
    """Count and adjustment transactions in the ledger so far."""
    rows = session.execute(
        select(LedgerTransaction.kind, func.count())
        .where(LedgerTransaction.kind.in_(("count", "adjustment")))
        .group_by(LedgerTransaction.kind)
    )
    written = {kind: n for kind, n in rows}
    return written.get("count", 0), written.get("adjustment", 0)


@contextmanager
def each_recorded_once(
    reader: InMemoryMetricReader, session: Session, counts: int, adjustments: int
) -> Generator[None]:
    """The block writes `counts` counts and `adjustments` adjustments, and
    inventory.counts.recorded adds exactly one for each, by its kind,
    whichever path wrote it (inventory-tng-vvp0)."""
    measured, written = recorded_by_kind(reader), corrections_written(session)
    yield
    expected = (counts, adjustments)
    now = corrections_written(session)
    assert tuple(a - b for a, b in zip(now, written, strict=True)) == expected
    now = recorded_by_kind(reader)
    assert tuple(a - b for a, b in zip(now, measured, strict=True)) == expected


def test_counts_length_checks_set_as_counts(
    rolled_back: Recorded, session: Session
) -> None:
    """A length set writes a count, recorded once, and once only however
    often it is sent."""
    client, _, reader, _ = rolled_back
    spools = Spools(session)
    reported = spools.opened()
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    said = report(spools, length(reported, "250"))
    assert client.post("/api/transactions", json=said).status_code == 201
    client.cookies.clear()
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    session.refresh(reported)
    ticked = f"{reported.id} {open_packages.check_seen(reported, 'm')}"
    with each_recorded_once(reader, session, 1, 0):
        for _ in range(2):
            client.post(
                "/admin/stock/length-checks",
                data={"open_package": [ticked], "do": "set"},
            )


def test_counts_packages_closed_as_adjustments(
    rolled_back: Recorded, session: Session
) -> None:
    """Closing a package that still shows some writes an adjustment,
    recorded once: closed directly, confirmed used up, or sent again."""
    client, _, reader, _ = rolled_back
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    spools = Spools(session)
    direct, confirmed = spools.opened(), spools.opened()
    open_packages.report(
        session, [confirmed.id], spools.place.volunteer.id, datetime.now(UTC)
    )
    session.flush()
    close = f"/admin/items/{spools.cable.id}/open-packages/{direct.id}/close"
    with each_recorded_once(reader, session, 0, 1):
        for _ in range(2):
            client.post(close)
    with each_recorded_once(reader, session, 0, 1):
        for _ in range(2):
            client.post(f"/admin/stock/used-up/{confirmed.id}/confirm")


def test_counts_a_count_that_closes_a_package_missing(
    rolled_back: Recorded, session: Session
) -> None:
    """A count saying an open package is not there: its count, and the
    adjustment closing the package, each recorded once."""
    client, _, reader, _ = rolled_back
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    spools = Spools(session)
    missing = spools.opened()
    store = spools.store.id
    count = count_form_at(client, store) | {
        f"opened_{missing.id}": "",
        f"state_{missing.id}": "missing",
    }
    with each_recorded_once(reader, session, 1, 1):
        for _ in range(2):
            client.post(f"{COUNTS}/{store}", data=count)


def test_counts_lines_recorded_more_than_moved_corrected_as_adjustments(
    rolled_back: Recorded, session: Session
) -> None:
    """Correcting a line recorded with more than was moved writes an
    adjustment at each end, each recorded once."""
    client, _, reader, _ = rolled_back
    place = Place(session)
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    first = place.batch()
    assert client.post("/api/transactions", json=first).status_code == 201
    changed = first | {
        "lines": [{"item_id": place.item.id, "quantity": "1", "unit": place.item.unit}],
        "changed_from": first["lines"],
    }
    assert client.post("/api/transactions", json=changed).status_code == 409
    client.cookies.clear()
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    check = session.scalars(select(OverRecorded)).one()
    page = f"/admin/stock/recorded-more/{check.id}/correct"
    with each_recorded_once(reader, session, 0, 2):
        for _ in range(2):
            client.post(page, data={"seen": over_recorded.seen(check)})


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
    unknown = place.batch(lines=[{"item_id": 999999, "quantity": "1", "unit": "each"}])
    assert client.post("/api/transactions", json=unknown).status_code == 422
    box = add(session, ItemPackage(item_id=place.cable.id, name="Box", size=25))
    resized = {
        "item_id": place.cable.id,
        "quantity": "50",
        "unit": place.cable.unit,
        "package_id": box.id,
    }
    stale = place.batch(lines=[resized | {"package_size": "50"}])
    assert client.post("/api/transactions", json=stale).status_code == 422
    # Only a unit behind: a page behind the catalogue too, not invalid.
    other_unit = {"item_id": place.cable.id, "quantity": "50", "unit": "ft"}
    behind = place.batch(lines=[other_unit])
    assert client.post("/api/transactions", json=behind).status_code == 422
    client.cookies.clear()

    kind = (("kind", "move"),)
    assert counted(reader, "inventory.transactions.recorded")[kind] >= 1
    assert counted(reader, "inventory.transactions.replayed")[kind] >= 1
    assert (
        counted(reader, "inventory.transactions.refused")[(("reason", "invalid"),)] >= 1
    )
    behind_reason = (("reason", "shown_otherwise"),)
    assert counted(reader, "inventory.transactions.refused")[behind_reason] >= 2
    assert "inventory.transaction.lines" in metric_names(reader)


def test_counts_saves_shown_in_a_unit_or_size_changed_since(
    rolled_back: Recorded, session: Session
) -> None:
    """A batch's line shown in m, and a box at another size, and an
    adjustment shown in m, once the cable counts in ft (inventory-tng-57u8):
    each counted by what changed and the save."""
    client, _, reader, _ = rolled_back
    name = "inventory.saves.shown_otherwise"
    before = counted(reader, name)
    place = Place(session)
    box = add(session, ItemPackage(item_id=place.item.id, name="Box", size=10))
    lines = [
        {"item_id": place.cable.id, "quantity": "50", "unit": "m"},
        {
            "item_id": place.item.id,
            "quantity": "30",
            "unit": place.item.unit,
            "package_id": box.id,
        }
        | {"package_size": "10", "unit": "each"},
    ]
    receipt = place.batch(
        kind="receipt", from_location_id=None, to_location_id=place.store.id
    )
    form = adjustment_form(session, place.store, place.cable, direction="found")
    place.cable.unit = "ft"
    box.size = Decimal(12)
    session.flush()
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    assert (
        client.post("/api/transactions", json=receipt | {"lines": lines}).status_code
        == 422
    )
    client.cookies.clear()
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    assert client.post(ADJUSTMENTS, data=form).status_code == 400
    assert added(reader, name, before) == {
        (("changed", "unit"), ("save", "batch")): 1,
        (("changed", "package_size"), ("save", "batch")): 1,
        (("changed", "unit"), ("save", "adjustment")): 1,
    }


def test_counts_a_length_reported_in_a_unit_changed_since(
    rolled_back: Recorded, session: Session
) -> None:
    """What is on an open spool, said in m once the cable counts in ft, is
    refused as shown otherwise and counted so (inventory-tng-fs50)."""
    client, _, reader, _ = rolled_back
    shown_otherwise = "inventory.saves.shown_otherwise"
    refused = "inventory.transactions.refused"
    before = counted(reader, shown_otherwise)
    refused_before = counted(reader, refused)
    spools = Spools(session)
    made = spools.opened()
    to_feet(session, spools.cable)
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    batch = report(spools, length(made, "50", "m"))
    assert client.post("/api/transactions", json=batch).status_code == 422
    assert added(reader, shown_otherwise, before) == {
        (("changed", "unit"), ("save", "batch")): 1
    }
    assert added(reader, refused, refused_before) == {
        (("reason", "shown_otherwise"),): 1
    }


def test_counts_numbers_typed_with_the_other_decimal_separator(
    rolled_back: Recorded, session: Session
) -> None:
    """Points refused in a German page's box, one after a whole part of 0
    (inventory-tng-mat2), and a comma and thousands in an English one's; a
    number refused otherwise is not counted (inventory-tng-qnwz)."""
    client, _, reader, _ = rolled_back
    name = "inventory.numbers.other_separator"
    before = counted(reader, name)
    place = Place(session)
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    for language, typed in (
        ("de", "1.5"),
        ("de", "0.500"),
        ("en", "1,5"),
        ("en", "1,000"),
        ("en", "x"),
    ):
        form = adjustment_form(session, place.store, place.cable, amount=typed)
        answer = client.post(
            ADJUSTMENTS, data=form, headers={"Accept-Language": language}
        )
        assert answer.status_code == 400
    assert added(reader, name, before) == {
        (("separator", "point"),): 2,
        (("separator", "comma"),): 1,
        (("separator", "thousands"),): 1,
    }


def test_counts_a_package_amount_with_the_other_separator_once_in_a_unit_left(
    rolled_back: Recorded, session: Session
) -> None:
    """A package added, or its too-short length set, typed "1,5" in m on an
    English page, the cable in ft since: each counted once, though read
    again for what is wrong with it in any unit (inventory-tng-hhlr)."""
    client, _, reader, _ = rolled_back
    name = "inventory.numbers.other_separator"
    before = counted(reader, name)
    cable = Place(session).cable
    reel = add(session, ItemPackage(item_id=cable.id, name="Reel", size=305))
    items.save(session, cable, item_fields(cable, unit="ft"), seen=None)
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    add_form = f"{ITEMS}/{cable.id}/packages"
    for action, typed in (
        (add_form, {"package_name": "Drum", "size": "1,5"}),
        (f"{add_form}/{reel.id}/settings", {"short_below": "1,5"}),
    ):
        answer = client.post(action, data=typed | {"unit_shown": "m"})
        assert answer.status_code == 400
    assert added(reader, name, before) == {(("separator", "comma"),): 2}


def test_counts_batches_from_a_build_too_old(
    rolled_back: Recorded, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, reader, _ = rolled_back
    before = counted(reader, "inventory.transactions.too_old")
    monkeypatch.setattr(page_builds, "OLDEST_BUILD", "2026-11-12T10:00:00.000Z")
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    response = client.post(
        "/api/transactions",
        json=Place(session).batch(),
        headers={page_builds.HEADER: "2026-11-12T09:00:00.000Z"},
    )
    client.cookies.clear()
    assert response.status_code == 409
    assert added(reader, "inventory.transactions.too_old", before) == {(): 1}


def test_counts_a_take_of_a_retired_package_none_left_as_invalid(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
    spools = Spools(session)
    spools.install(spools.store.id, 610)
    retire(session, spools.spool)
    before = counted(reader, "inventory.transactions.refused")
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    batch = spools.place.batch(lines=[sealed_line(spools, "305")])
    response = client.post("/api/transactions", json=batch)
    client.cookies.clear()
    assert response.status_code == 422
    added_now = added(reader, "inventory.transactions.refused", before)
    assert added_now == {(("reason", "invalid"),): 1}


def test_counts_packages_opened_by_what_they_became(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
    # The module's counters are shared by its tests: what this one adds.
    taken_before = counted(reader, "inventory.open_packages.taken")
    reported_before = counted(reader, "inventory.packages.reported")
    spools = Spools(session)
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    store = spools.store.id
    opening = spools.place.batch(
        kind="open",
        from_location_id=store,
        to_location_id=store,
        lines=[
            {
                "item_id": spools.cable.id,
                "quantity": "305",
                "unit": spools.cable.unit,
                "package_id": spools.spool.id,
                "package_size": "305",
                "open_package_key": str(uuid.uuid4()),
            }
        ],
    )
    assert client.post("/api/transactions", json=opening).status_code == 201
    client.cookies.clear()
    assert counted(reader, "inventory.packages.opened")[(("into", "open"),)] == 1
    made = session.scalars(
        select(OpenPackage).where(OpenPackage.item_id == spools.cable.id)
    ).one()
    client.cookies.set(devices.COOKIE, token)
    five = cut(made, "5")
    whole = five | {"quantity": "300", "whole": True}
    # The cut asked whether it is used up, and was told so.
    used = spools.place.batch(lines=[five], used_up=[str(made.key)])
    assert client.post("/api/transactions", json=used).status_code == 201
    batch = spools.place.batch(lines=[whole])
    assert client.post("/api/transactions", json=batch).status_code == 201
    client.cookies.clear()
    taken = counted(reader, "inventory.open_packages.taken")
    assert {
        how: taken[(("how", how),)] - taken_before.get((("how", how),), 0)
        for how in ("cut", "whole")
    } == {"cut": 1, "whole": 1}
    reported = counted(reader, "inventory.packages.reported")
    cut_key = (("how", "cut"),)
    assert reported[cut_key] - reported_before.get(cut_key, 0) == 1


def test_counts_mail_sent_and_failed(recorded: Recorded) -> None:
    _, _, reader, _ = recorded

    message = mail_messages.password_changed("ada@example.org", 1)
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
    session.commit()  # Counted once committed.
    by_names = counted(reader, "inventory.labels.made")
    assert by_names[(("names", "item"),)] >= 1
    assert by_names[(("names", "location"),)] >= 1
    assert sum(counted(reader, "inventory.labels.printed").values()) >= 5
    assert sum(counted(reader, "inventory.labels.revoked").values()) >= 1


def test_counts_unit_changes_refused_by_why(
    rolled_back: Recorded, session: Session
) -> None:
    """Counted and logged at once, as a refusal writes nothing to commit:
    by what fixes the unit, or, between lengths, a check waiting or an
    amount too large in the new unit."""
    _, _, reader, _ = rolled_back
    name = "inventory.unit_changes.refused"
    before = counted(reader, name)
    moved = stocked(session).item
    labelled = Place(session).cable
    assert labels.make_for_item(session, labelled.id) is not None
    converted = Place(session).cable
    converted.unit_converted = True
    place, other = Place(session), Place(session)
    for each in (place, other):
        line = {"item_id": each.cable.id, "quantity": "5", "unit": "m"}
        moved_at = write(session, each, lines=[line])
    raise_check(session, moved_at)
    waiting, huge = other.cable, place.cable
    refusals = (
        (moved, "m", "item_unit_fixed", {}),
        (labelled, "each", "item_unit_labelled", {}),
        (converted, "each", "item_unit_converted", {}),
        (waiting, "ft", unit_changes.UNIT_CHECKS_WAITING, {}),
        (huge, "ft", unit_changes.UNIT_OUT_OF_RANGE, {"minimum": Decimal(400_000_000)}),
    )
    with capture_logs() as logs:
        for item, unit, rule, more in refusals:
            fields = item_fields(item, unit=unit, **more)
            with breaks(session, rule):
                items.save(session, item, fields, seen=None, recorded_in=item.unit)
    assert added(reader, name, before) == {
        (("reason", "moved"),): 1,
        (("reason", "labelled"),): 1,
        (("reason", "converted"),): 1,
        (("reason", "checks_waiting"),): 1,
        (("reason", "out_of_range"),): 1,
    }
    assert [
        (line["item_id"], line["reason"], line["log_level"])
        for line in logs
        if line["event"] == "unit change refused"
    ] == [
        (moved.id, "moved", "info"),
        (labelled.id, "labelled", "info"),
        (converted.id, "converted", "info"),
        (waiting.id, "checks_waiting", "info"),
        (huge.id, "out_of_range", "info"),
    ]


def test_counts_unit_changes_converted_by_unit(
    rolled_back: Recorded, session: Session
) -> None:
    """A change between lengths, stock moved, is counted once answered Yes,
    by the unit it was in and the one it is in now, and logged by the
    item's id; asked, or answered No, it is not."""
    client, _, reader, _ = rolled_back
    name = "inventory.unit_changes.converted"
    before = counted(reader, name)
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    place = Place(session)
    write(
        session,
        place,
        lines=[{"item_id": place.cable.id, "quantity": "5", "unit": "m"}],
    )
    path = f"/admin/items/{place.cable.id}"
    form = shown_item_form(place.cable, unit="ft")
    with capture_logs() as logs:
        assert "really in m?" in client.post(path, data=form).text
        client.post(path, data=unit_answered(place.cable, "no", unit="ft"))
        assert added(reader, name, before) == {}
        client.post(path, data=unit_answered(place.cable, unit="ft"))
    assert added(reader, name, before) == {(("from", "m"), ("to", "ft")): 1}
    assert [
        (line["event"], line["item_id"], line["log_level"])
        for line in logs
        if line["event"].startswith("unit change") or "converted" in line["event"]
    ] == [
        ("unit change asked", place.cable.id, "info"),
        ("unit change declined", place.cable.id, "info"),
        ("item unit converted", place.cable.id, "info"),
    ]


def test_counts_unit_recounts_by_unit(rolled_back: Recorded, session: Session) -> None:
    """A guided recount is counted once done, by the unit it was in and
    the one it is in now, and logged by ids alone; refused, it is not."""
    client, _, reader, _ = rolled_back
    name = "inventory.unit_recounts"
    before = counted(reader, name)
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    place = Place(session)
    cable = place.cable
    write(session, place, lines=[{"item_id": cable.id, "quantity": "5", "unit": "m"}])
    path = unit_recount(cable.id, "ft")
    form = dict(hidden_fields(client.get(path).text))
    with capture_logs() as logs:
        assert client.post(path, data=form | {"seen": "stale"}).status_code == 409
        assert added(reader, name, before) == {}
        done = client.post(path, data=form, follow_redirects=False)
        assert done.status_code == 303
    assert added(reader, name, before) == {(("from", "m"), ("to", "ft")): 1}
    (line,) = [line for line in logs if line["event"] == "item unit recounted"]
    assert {**line, "admin_id": 0} == {
        "event": "item unit recounted",
        "log_level": "info",
        "item_id": cable.id,
        "admin_id": 0,
        "from_unit": "m",
        "to_unit": "ft",
        "location_ids": [place.store.id, place.van.id],
        "open_package_ids": [],
        "item_package_ids": [],
        "resized_package_ids": [],
        "label_ids": [],
        "vendor_offer_ids": [],
    }


def test_counts_package_forms_refused_as_shown_in_a_unit_left_by_form(
    rolled_back: Recorded, session: Session
) -> None:
    """Counted at once, as a refusal writes nothing to commit: a package
    added, or its too-short length set, typed in m, the cable in ft now
    (inventory-tng-74f3). One in its unit, or a command's, is not."""
    _, _, reader, _ = rolled_back
    name = "inventory.saves.shown_otherwise"
    before = counted(reader, name)
    cable = Place(session).cable
    reel = add(session, ItemPackage(item_id=cable.id, name="Reel", size=305))
    items.save(session, cable, item_fields(cable, unit="ft"), seen=None)
    rule = unit_changes.UNIT_CHANGED
    with breaks(session, rule):
        items.add_package(session, cable, "Drum", Decimal(300), shown_unit="m")
    with breaks(session, rule):
        items.package_settings(session, reel, Decimal(5), None, shown_unit="m")
    items.add_package(session, cable, "Box", Decimal(300), shown_unit="ft")
    items.package_settings(session, reel, Decimal(5), None, shown_unit=None)
    assert added(reader, name, before) == {
        (("changed", "unit"), ("save", "add_package")): 1,
        (("changed", "unit"), ("save", "package_settings")): 1,
    }


def test_a_write_rolled_back_is_neither_counted_nor_logged(
    rolled_back: Recorded, session: Session
) -> None:
    """Measured and logged once committed (db_once_committed): a label and
    a count written, then rolled back, leave no trace in either."""
    _, _, reader, _ = rolled_back
    ada, _ = new_admin(session)
    place = stocked(session)
    session.commit()
    labels_before = counted(reader, "inventory.labels.made")
    counts_before = counted(reader, "inventory.counts.recorded")
    with capture_logs() as logs:
        assert labels.make_for_item(session, place.item.id) is not None
        shown = moments.moment(session, place.store.id)
        radios = [(place.item.id, Decimal(5))]
        counts.record_count(
            session, uuid.uuid4(), ada.id, place.store.id, radios, shown
        )
        session.rollback()
        session.commit()
    assert added(reader, "inventory.labels.made", labels_before) == {}
    assert added(reader, "inventory.counts.recorded", counts_before) == {}
    # Debug lines trace the attempt, as they are for (CONTRIBUTING.md, Logging).
    assert [line["event"] for line in logs if line["log_level"] != "debug"] == []


def test_counts_saves_refused_as_changed_since_shown(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    radios = add(session, Category(name=f"Radios {tag()}"))
    form = {"name": f"Antennas {tag()}", "version": shown(radios)}
    radios.name = f"Wireless {tag()}"
    session.flush()
    before = counted(reader, "inventory.edits.stale").get((("table", "category"),), 0)
    with capture_logs() as logs:
        refused = client.post(f"{CATEGORIES}/{radios.id}/name", data=form)
    assert refused.status_code == 409
    after = counted(reader, "inventory.edits.stale")[(("table", "category"),)]
    assert after == before + 1
    (line,) = [entry for entry in logs if entry["event"].startswith("edit refused")]
    assert (line["table"], line["row_id"]) == ("category", radios.id)


def test_counts_requests_that_found_no_free_connection(recorded: Recorded) -> None:
    """A pool too small, or requests stuck holding theirs
    (db_sessions.POOL_TIMEOUT), shows as a 503 and in this count."""
    client, _, reader, _ = recorded

    def no_connection() -> None:
        raise exc.TimeoutError("QueuePool limit reached")

    app = cast(FastAPI, client.app)
    app.add_api_route("/api/no-connection", no_connection)
    # A pool's own timeout: the side pool's (the request pool's is NoSession).
    side = (("db.client.connection.pool.name", "side"),)
    before = counted(reader, "db.client.connection.timeouts").get(side, 0)
    assert client.get("/api/no-connection").status_code == 503
    assert counted(reader, "db.client.connection.timeouts")[side] == before + 1

    def no_session() -> None:
        raise NoSession

    app.add_api_route("/api/no-session", no_session)
    requests = (("db.client.connection.pool.name", "requests"),)
    before = counted(reader, "db.client.connection.timeouts").get(requests, 0)
    assert client.get("/api/no-session").status_code == 503
    assert counted(reader, "db.client.connection.timeouts")[requests] == before + 1
    pools = {
        pair[1]
        for attributes in counted(reader, "db.client.connection.count")
        for pair in attributes
        if isinstance(pair, tuple) and pair[0] == "db.client.connection.pool.name"
    }
    assert pools == {"requests", "side", "limits"}


@pytest.mark.breaks_pool_order
def test_counts_last_seen_skipped_for_a_busy_side_pool(
    recorded: Recorded, database_url: str
) -> None:
    """Bookkeeping: a flood of guesses counted against the rate limits fails
    no request for it. Skipped, not a 503, but a side pool timeout all the
    same."""
    _, _, reader, _ = recorded
    key = (("db.client.connection.pool.name", "side"),)
    before = counted(reader, "db.client.connection.timeouts").get(key, 0)
    with (
        small_database(database_url) as database,
        database.side.connect(),
        capture_logs() as logs,
    ):
        db_sessions.record_last_seen(database, Device, -1, datetime.now(UTC))
    assert counted(reader, "db.client.connection.timeouts")[key] == before + 1
    assert [line["event"] for line in logs] == ["last seen not recorded"]


def histogram_counts(
    reader: InMemoryMetricReader, name: str
) -> dict[tuple[object, ...], int]:
    """A histogram's numbers of values, by its attributes' values."""
    return {
        tuple(sorted((point.attributes or {}).items())): point.count
        for metric in metrics_in(reader)
        if metric.name == name
        for point in metric.data.data_points
        if isinstance(point, HistogramDataPoint)
    }


LCP = {
    "kind": "web_vital",
    "page": "scan",
    "name": "LCP",
    "value": 1200,
    "rating": "good",
}
CLS = {
    "kind": "web_vital",
    "page": "scan",
    "name": "CLS",
    "value": 0.3,
    "rating": "poor",
}

# Each event a page reports, and the metric and attributes it is recorded
# under, read as a counter's total or a histogram's number of values.
PAGE_EVENTS = [
    (
        LCP,
        histogram_counts,
        "inventory.page.web_vital.duration",
        (
            ("browser.web_vital.name", "LCP"),
            ("browser.web_vital.rating", "good"),
            ("page", "scan"),
        ),
    ),
    (
        CLS,
        histogram_counts,
        "inventory.page.web_vital.layout_shift",
        (("browser.web_vital.rating", "poor"), ("page", "scan")),
    ),
    (
        {"kind": "error", "page": "register", "source": "unhandledrejection"},
        counted,
        "inventory.page.errors",
        (("page", "register"), ("source", "unhandledrejection")),
    ),
    (
        {"kind": "outbox_retry", "reason": "no_answer"},
        counted,
        "inventory.outbox.retries",
        (("reason", "no_answer"),),
    ),
    (
        {"kind": "scan_to_save", "seconds": 42.5},
        histogram_counts,
        "inventory.scan.to_save.duration",
        (),
    ),
    (
        {"kind": "out_of_date", "learned": "too_old"},
        counted,
        "inventory.page.out_of_date",
        (("learned", "too_old"),),
    ),
    (
        {"kind": "older_storage", "held": "cart"},
        counted,
        "inventory.page.older_storage",
        (("held", "cart"),),
    ),
    (
        {"kind": "older_storage", "held": "saves", "action": "put_in_cart"},
        counted,
        "inventory.page.older_acted_on",
        (("action", "put_in_cart"), ("held", "saves")),
    ),
    (
        {"kind": "older_storage", "held": "cart", "action": "discarded"},
        counted,
        "inventory.page.older_acted_on",
        (("action", "discarded"), ("held", "cart")),
    ),
    (
        {"kind": "older_storage", "held": "saves", "action": "line_discarded"},
        counted,
        "inventory.page.older_acted_on",
        (("action", "line_discarded"), ("held", "saves")),
    ),
    (
        {
            "kind": "older_storage",
            "held": "saves",
            "action": "put_in_cart_automatically",
        },
        counted,
        "inventory.page.older_acted_on",
        (("action", "put_in_cart_automatically"), ("held", "saves")),
    ),
    ({"kind": "cart_discarded"}, counted, "inventory.cart.discards", ()),
    ({"kind": "line_removed"}, counted, "inventory.cart.lines_removed", ()),
    ({"kind": "removal_undone"}, counted, "inventory.cart.removals_undone", ()),
    ({"kind": "back_to_camera"}, counted, "inventory.scan.camera.returns", ()),
    (
        {"kind": "difference_added", "how": "automatic"},
        counted,
        "inventory.cart.differences_added",
        (("how", "automatic"),),
    ),
    (
        {"kind": "difference_added", "how": "tap"},
        counted,
        "inventory.cart.differences_added",
        (("how", "tap"),),
    ),
    ({"kind": "rest_put_back"}, counted, "inventory.cart.rests_put_back", ()),
    (
        {"kind": "other_separator", "separator": "thousands"},
        counted,
        "inventory.numbers.other_separator",
        (("separator", "thousands"),),
    ),
]


@pytest.mark.parametrize(
    ("event", "reading", "metric", "attributes"),
    PAGE_EVENTS,
    ids=[
        "-".join(v for v in event.values() if isinstance(v, str))
        for event, *_ in PAGE_EVENTS
    ],
)
def test_records_each_event_a_page_reports(
    rolled_back: Recorded,
    event: dict[str, object],
    reading: Callable[[InMemoryMetricReader, str], dict[tuple[object, ...], int]],
    metric: str,
    attributes: tuple[object, ...],
) -> None:
    client, _, reader, _ = rolled_back
    before = reading(reader, metric).get(attributes, 0)
    assert client.post("/api/telemetry", json={"events": [event]}).status_code == 204
    assert reading(reader, metric)[attributes] == before + 1


CAMERA_SESSION = {
    "kind": "camera_session",
    "outcome": "opened",
    "decoder": "ponyfill",
    "open_seconds": 1.5,
    "seconds": 240,
    "labels": {"item": 3, "package": 1, "place": 2, "unknown": 0},
}


def test_records_a_camera_session(rolled_back: Recorded) -> None:
    """Its session, open time, length and labels, each by fixed values."""
    client, _, reader, _ = rolled_back
    sessions = "inventory.scan.camera.sessions"
    opening = "inventory.scan.camera.open.duration"
    lengths = "inventory.scan.camera.session.duration"
    labels = "inventory.scan.camera.labels"
    before = {
        sessions: counted(reader, sessions),
        opening: histogram_counts(reader, opening),
        lengths: histogram_counts(reader, lengths),
        labels: counted(reader, labels),
    }
    refused = CAMERA_SESSION | {
        "outcome": "not_allowed",
        "decoder": "none",
        "open_seconds": None,
        "seconds": 3,
        "labels": {"item": 0, "package": 0, "place": 0, "unknown": 0},
    }
    answer = client.post("/api/telemetry", json={"events": [CAMERA_SESSION, refused]})
    assert answer.status_code == 204
    opened = (("decoder", "ponyfill"), ("outcome", "opened"))
    not_allowed = (("decoder", "none"), ("outcome", "not_allowed"))
    assert added(reader, sessions, before[sessions]) == {opened: 1, not_allowed: 1}
    # Only a camera that opened says how long it took.
    assert added(reader, opening, before[opening], histogram_counts) == {
        (("decoder", "ponyfill"),): 1
    }
    assert added(reader, lengths, before[lengths], histogram_counts) == {
        opened: 1,
        not_allowed: 1,
    }
    # No label of a kind, nothing counted for it.
    assert added(reader, labels, before[labels]) == {
        (("label.kind", "item"),): 3,
        (("label.kind", "package"),): 1,
        (("label.kind", "place"),): 2,
    }


def test_web_vitals_are_recorded_in_buckets_that_fit_them(
    rolled_back: Recorded,
) -> None:
    """Buckets that fit the values, not the SDK's own from 5 up."""
    client, _, reader, _ = rolled_back
    assert client.post("/api/telemetry", json={"events": [LCP, CLS]}).status_code == 204
    bounds = {
        name: {
            tuple(point.explicit_bounds)
            for point in metric.data.data_points
            if isinstance(point, HistogramDataPoint)
        }
        for metric in metrics_in(reader)
        if (name := metric.name).startswith("inventory.page.web_vital.")
    }
    assert bounds == {
        "inventory.page.web_vital.duration": {page_telemetry_api.VITAL_SECONDS},
        "inventory.page.web_vital.layout_shift": {page_telemetry_api.LAYOUT_SHIFTS},
    }


@pytest.mark.parametrize(("report", "content_type", "found"), SHAPES)
def test_counts_policy_violations_by_directive(
    rolled_back: Recorded, report: object, content_type: str, found: list[str]
) -> None:
    client, _, reader, _ = rolled_back
    before = counted(reader, "inventory.csp.violations")
    answer = client.post(
        security_headers.REPORTS,
        json=report,
        headers={"Content-Type": content_type},
    )
    assert answer.status_code == 204
    assert added(reader, "inventory.csp.violations", before) == {
        (("directive", directive),): found.count(directive) for directive in found
    }


def test_counts_package_tracking_changed_by_where(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
    tracking = "inventory.package_tracking.changed"
    # Another test may have counted some in this process.
    before = counted(reader, tracking)
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    spools = Spools(session, tracked=False)
    cable = spools.cable
    items_path = "/admin/items"
    client.post(f"{items_path}/tracking", data={"item": [str(cable.id)], "track": "on"})
    client.post(
        f"{items_path}/{cable.id}/packages/{spools.spool.id}/tracking",
        data={"track": "off"},
    )
    # Sent again, nothing changes, and nothing more is counted.
    client.post(f"{items_path}/tracking", data={"item": [str(cable.id)], "track": "on"})
    client.post(
        f"{items_path}/{cable.id}/packages/{spools.spool.id}/tracking",
        data={"track": "off"},
    )
    session.refresh(cable)
    client.post(f"{items_path}/{cable.id}", data=shown_item_form(cable))
    assert added(reader, tracking, before) == {
        (("scope", "bulk"),): 1,
        (("scope", "package"),): 1,
        (("scope", "item"),): 1,
    }


def test_counts_open_packages_closed_and_cleared(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
    closed = "inventory.open_packages.closed"
    before = counted(reader, closed)
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    spools = Spools(session)
    one, two, three = spools.opened(), spools.opened(), spools.opened()
    volunteer = spools.place.volunteer.id
    open_packages.report(session, [one.id, two.id], volunteer, datetime.now(UTC))
    session.flush()
    used_up = "/admin/stock/used-up"
    client.post(f"{used_up}/{one.id}/confirm")
    client.post(f"{used_up}/{two.id}/clear")
    client.post(f"/admin/items/{spools.cable.id}/open-packages/{three.id}/close")
    four = spools.opened()
    store = spools.store.id
    count = count_form_at(client, store) | {
        f"opened_{four.id}": "",
        f"state_{four.id}": "missing",
    }
    client.post(f"/admin/counts/{store}", data=count)
    # Its opened ones no longer followed, the one left open, two, closes.
    client.post(
        f"/admin/items/{spools.cable.id}/packages/{spools.spool.id}/tracking",
        data={"track": "off"},
    )
    assert added(reader, closed, before) == {
        (("how", "confirmed"),): 1,
        (("how", "direct"),): 1,
        (("how", "count"),): 1,
        (("how", "unfollowed"),): 1,
    }
    assert counted(reader, "inventory.open_packages.cleared") == {(): 1}


def test_counts_length_checks_raised_and_settled(
    rolled_back: Recorded, session: Session
) -> None:
    """Raised by a volunteer's report or a cut below zero; settled by
    setting or leaving (inventory-tng-wy7), or by closing the package, so
    raised and settled match (inventory-tng-b1z)."""
    client, _, reader, _ = rolled_back
    raised = counted(reader, "inventory.open_packages.length_checks.raised")
    settled = counted(reader, "inventory.open_packages.length_checks.settled")
    spools = Spools(session)
    reported, cut_from, closing = spools.opened(), spools.opened(), spools.opened()
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    said = report(spools, length(reported, "250"), length(closing, "250"))
    assert client.post("/api/transactions", json=said).status_code == 201
    below = spools.place.batch(lines=[cut(cut_from, "310")])
    assert client.post("/api/transactions", json=below).status_code == 201
    client.cookies.clear()
    assert added(reader, "inventory.open_packages.length_checks.raised", raised) == {
        (("how", "volunteer"),): 2,
        (("how", "below_zero"),): 1,
    }
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    lengths = "/admin/stock/length-checks"
    for row in (reported, cut_from):
        session.refresh(row)
    ticked = f"{reported.id} {open_packages.check_seen(reported, 'm')}"
    client.post(lengths, data={"open_package": [ticked], "do": "set"})
    client.post(
        f"{lengths}/{cut_from.id}/leave",
        data={"seen": open_packages.check_seen(cut_from, "m")},
    )
    client.post(f"/admin/items/{spools.cable.id}/open-packages/{closing.id}/close")
    assert added(reader, "inventory.open_packages.length_checks.settled", settled) == {
        (("how", "set"),): 1,
        (("how", "left"),): 1,
        (("how", "closed"),): 1,
    }


def test_counts_lines_recorded_more_than_moved_raised_and_settled(
    rolled_back: Recorded, session: Session
) -> None:
    """Raised as a batch changed after another tab recorded it takes some
    off (inventory-tng-0gg), settled corrected or checked."""
    client, _, reader, _ = rolled_back
    place = Place(session)
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    raised = counted(reader, "inventory.over_recorded.raised")
    settled = counted(reader, "inventory.over_recorded.settled")
    first = place.batch(
        lines=[
            {"item_id": place.item.id, "quantity": "3", "unit": place.item.unit},
            {"item_id": place.cable.id, "quantity": "10", "unit": place.cable.unit},
        ]
    )
    assert client.post("/api/transactions", json=first).status_code == 201
    # As a page sends a cart put back with first's lines, changed.
    changed = first | {
        "lines": [{"item_id": place.item.id, "quantity": "1", "unit": place.item.unit}],
        "changed_from": first["lines"],
    }
    assert client.post("/api/transactions", json=changed).status_code == 409
    client.cookies.clear()
    assert added(reader, "inventory.over_recorded.raised", raised) == {(): 2}
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    page = "/admin/stock/recorded-more"
    first_check, second_check = session.scalars(
        select(OverRecorded).order_by(OverRecorded.id)
    ).all()
    seen = over_recorded.seen
    client.post(f"{page}/{first_check.id}/correct", data={"seen": seen(first_check)})
    # Marked checked, by its reason, never its words.
    form = {"seen": seen(second_check), "reason": "other", "words": "Counted twice"}
    client.post(f"{page}/{second_check.id}/checked", data=form)
    assert added(reader, "inventory.over_recorded.settled", settled) == {
        (("how", "corrected"),): 1,
        (("how", "checked"), ("reason", "other")): 1,
    }


def test_counts_lines_recorded_more_than_moved_settled_by_counts(
    rolled_back: Recorded, session: Session
) -> None:
    """Counted at each end, once however often the count is sent
    (inventory-tng-qs8)."""
    client, _, reader, _ = rolled_back
    place = Place(session)
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    first = place.batch()
    assert client.post("/api/transactions", json=first).status_code == 201
    changed = first | {
        "lines": [{"item_id": place.item.id, "quantity": "1", "unit": place.item.unit}],
        "changed_from": first["lines"],
    }
    assert client.post("/api/transactions", json=changed).status_code == 409
    client.cookies.clear()
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    settled = counted(reader, "inventory.over_recorded.settled")
    for place_id, amount in ((place.van.id, "1"), (place.store.id, "2")):
        path = f"{COUNTS}/{place_id}"
        form = count_form(client.get(path))
        for _ in range(2):
            client.post(path, data=form | {f"counted_{place.item.id}": amount})
    assert added(reader, "inventory.over_recorded.settled", settled) == {
        (("how", "counted"),): 1
    }


def test_counts_lines_recorded_more_than_moved_closed_with_their_open_package(
    rolled_back: Recorded, session: Session
) -> None:
    """An opening's sealed end counted, then its open package closed: closed
    as counted, once however often the close is sent (inventory-tng-die)."""
    client, _, reader, _ = rolled_back
    admin, key = new_admin(session)
    sign_in_with(client, key=key)
    spools = Spools(session)
    made = spools.opened()
    movement_id = session.scalar(
        select(Movement.id).where(Movement.to_open_package_id == made.id)
    )
    assert movement_id is not None
    over_recorded.raise_checks(session, {movement_id: Decimal(305)})
    store = spools.store.id
    shown = moments.moment(session, store)
    count = counts.record_count(
        session,
        uuid.uuid4(),
        admin.id,
        store,
        [],
        shown,
        by_form=[(spools.cable.id, spools.sealed, Decimal(305))],
    )
    over_recorded.counted(
        session, admin.id, store, shown.as_of, count.written, count.put_right
    )
    session.flush()
    settled = counted(reader, "inventory.over_recorded.settled")
    for _ in range(2):
        client.post(f"/admin/items/{spools.cable.id}/open-packages/{made.id}/close")
    assert added(reader, "inventory.over_recorded.settled", settled) == {
        (("how", "counted"),): 1
    }


def test_counts_lines_recorded_more_than_moved_closed_with_both_ends_right(
    rolled_back: Recorded, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An opening's sealed end counted and its open package closed by a
    close that skipped it: Close it settles it as counted, once however
    often it is sent (inventory-tng-dro)."""
    client, _, reader, _ = rolled_back
    admin, key = new_admin(session)
    sign_in_with(client, key=key)
    spools = Spools(session)
    made = spools.opened()
    movement_id = session.scalar(
        select(Movement.id).where(Movement.to_open_package_id == made.id)
    )
    assert movement_id is not None
    over_recorded.raise_checks(session, {movement_id: Decimal(305)})
    store = spools.store.id
    shown = moments.moment(session, store)
    count = counts.record_count(
        session,
        uuid.uuid4(),
        admin.id,
        store,
        [],
        shown,
        by_form=[(spools.cable.id, spools.sealed, Decimal(305))],
    )
    over_recorded.counted(
        session, admin.id, store, shown.as_of, count.written, count.put_right
    )
    monkeypatch.setattr(over_recorded, "open_package_closed", lambda *_: None)
    assert open_packages.close(
        session, made.id, admin.id, datetime.now(UTC), how="direct"
    )
    session.flush()
    check = session.scalars(select(OverRecorded)).one()
    form = {"seen": over_recorded.seen(check)}
    settled = counted(reader, "inventory.over_recorded.settled")
    for _ in range(2):
        client.post(f"/admin/stock/recorded-more/{check.id}/close", data=form)
    assert added(reader, "inventory.over_recorded.settled", settled) == {
        (("how", "counted"),): 1
    }


def test_counts_places_brought_back_to_correct_one(
    rolled_back: Recorded, session: Session
) -> None:
    """Brought back from Recorded more than moved, once however often it is
    sent (inventory-tng-axb)."""
    client, _, reader, _ = rolled_back
    place = Place(session)
    received(session, place)
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    first = place.batch()
    assert client.post("/api/transactions", json=first).status_code == 201
    changed = first | {
        "lines": [{"item_id": place.item.id, "quantity": "1", "unit": place.item.unit}],
        "changed_from": first["lines"],
    }
    assert client.post("/api/transactions", json=changed).status_code == 409
    client.cookies.clear()
    retire(session, place.store)
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    check = session.scalars(select(OverRecorded)).one()
    form = {"seen": over_recorded.seen(check), "place": str(place.store.id)}
    name = "inventory.over_recorded.places_brought_back"
    before = counted(reader, name)
    for _ in range(2):
        client.post(f"/admin/stock/recorded-more/{check.id}/bring-back", data=form)
    assert added(reader, name, before) == {(): 1}
