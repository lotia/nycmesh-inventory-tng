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
from sqlalchemy import exc, select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import (
    accounts,
    db,
    devices,
    labels,
    ledger,
    mail,
    open_packages,
    page_telemetry,
)
from inventory_tng.admin.categories import CATEGORIES
from inventory_tng.admin.counts import ADJUSTMENTS, COUNTS
from inventory_tng.app import create_app
from inventory_tng.db import NoSession, RuleBroken
from inventory_tng.devices import RegisteredDevice
from inventory_tng.masking import MASK, Masker
from inventory_tng.models import Category, Device, LedgerTransaction, OpenPackage
from inventory_tng.settings import Settings
from inventory_tng.telemetry import Telemetry, enabled

from helpers.apps import on_session
from helpers.devices import new_device
from helpers.mail import NO_MAIL, FailingMail, SentMail
from helpers.pools import small_database
from helpers.rows import add, shown, tag
from helpers.sign_in import (
    PASSWORD,
    TOTP_KEYS,
    code,
    codes_with_left,
    new_admin,
    sign_in_with,
)
from helpers.stock import (
    Place,
    Spools,
    adjustment_form,
    received,
    retire,
    stocked,
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
        accounts.recover(session, token, "nope", "ada", "192.0.2.90", now, NO_MAIL)
    accounts.recover(session, token, token, "ada", "192.0.2.90", now, NO_MAIL)
    recoveries = counted(reader, "inventory.admin.recoveries")
    assert recoveries[(("outcome", "bad_token"),)] >= 1
    assert recoveries[(("outcome", "made"),)] >= 1


def test_counts_sign_in_resets(rolled_back: Recorded, session: Session) -> None:
    _, _, reader, _ = rolled_back
    ada, _ = new_admin(session)
    ben, _ = new_admin(session, "ben")
    now = datetime.now(UTC)
    accounts.reset_admin_sign_in(session, ben.id, ada.id, now, NO_MAIL)
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


def test_counts_devices_revoked(rolled_back: Recorded, session: Session) -> None:
    _, _, reader, _ = rolled_back
    one, _ = new_device(session)
    many, _ = new_device(session)
    devices.revoke_device(session, one.id)
    devices.revoke_devices(
        session, devices.DeviceFilter(), [many.id], datetime.now(UTC)
    )
    revoked = counted(reader, "inventory.devices.revoked")
    assert revoked[(("how", "one"),)] >= 1
    assert revoked[(("how", "bulk"),)] >= 1


def test_counts_activity(rolled_back: Recorded, session: Session) -> None:
    _, _, reader, _ = rolled_back
    ada, _ = new_admin(session)
    ben, _ = new_admin(session, "ben")
    accounts.retire_admin(session, ben.id, ada.id, datetime.now(UTC), NO_MAIL)
    told = counted(reader, "inventory.admin.activity")
    assert told[(("kind", "retired"),)] >= 1


def test_counts_count_lines_withheld(rolled_back: Recorded, session: Session) -> None:
    """Radios counted while three of them moved are not written: they are
    counted again."""
    _, _, reader, _ = rolled_back
    ada, _ = new_admin(session)
    place = stocked(session)
    as_of = received(session, place).id
    write(session, place)
    radios = [(place.item.id, Decimal(5))]
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
    form = adjustment_form(session, place.store, place.item)
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
        lines: ledger.Lines,
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


def test_counts_packages_opened_by_what_they_became(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
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
                "package_id": spools.spool.id,
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
    cut = {
        "item_id": spools.cable.id,
        "quantity": "5",
        "open_package_key": str(made.key),
    }
    whole = cut | {"quantity": "300", "whole": True}
    # The cut asked whether it is used up, and was told so.
    used = spools.place.batch(lines=[cut], used_up=[str(made.key)])
    assert client.post("/api/transactions", json=used).status_code == 201
    batch = spools.place.batch(lines=[whole])
    assert client.post("/api/transactions", json=batch).status_code == 201
    client.cookies.clear()
    assert counted(reader, "inventory.open_packages.taken") == {
        (("how", "cut"),): 1,
        (("how", "whole"),): 1,
    }
    assert counted(reader, "inventory.packages.reported") == {(("how", "cut"),): 1}


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


def test_counts_saves_refused_as_changed_since_shown(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
    _, key = new_admin(session)
    sign_in_with(client, code=code(key))
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
    """A pool too small, or requests stuck holding theirs (db.POOL_TIMEOUT),
    shows as a 503 and in this count."""
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
        db.record_last_seen(database, Device, -1, datetime.now(UTC))
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


def test_records_what_the_pages_report(rolled_back: Recorded) -> None:
    client, _, reader, _ = rolled_back
    events = [
        {
            "kind": "web_vital",
            "page": "scan",
            "name": "LCP",
            "value": 1200,
            "rating": "good",
        },
        {
            "kind": "web_vital",
            "page": "scan",
            "name": "CLS",
            "value": 0.3,
            "rating": "poor",
        },
        {"kind": "error", "page": "register", "source": "unhandledrejection"},
        {"kind": "outbox_retry", "reason": "no_answer"},
        {"kind": "scan_to_save", "seconds": 42.5},
    ]
    assert client.post("/api/telemetry", json={"events": events}).status_code == 204
    durations = histogram_counts(reader, "inventory.page.web_vital.duration")
    assert (
        durations[
            (
                ("browser.web_vital.name", "LCP"),
                ("browser.web_vital.rating", "good"),
                ("page", "scan"),
            )
        ]
        >= 1
    )
    shifts = histogram_counts(reader, "inventory.page.web_vital.layout_shift")
    assert shifts[(("browser.web_vital.rating", "poor"), ("page", "scan"))] >= 1
    errors = counted(reader, "inventory.page.errors")
    assert errors[(("page", "register"), ("source", "unhandledrejection"))] >= 1
    assert counted(reader, "inventory.outbox.retries")[(("reason", "no_answer"),)] >= 1
    assert histogram_counts(reader, "inventory.scan.to_save.duration")[()] >= 1
    # Buckets that fit the values, not the SDK's own from 5 up.
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
        "inventory.page.web_vital.duration": {page_telemetry.VITAL_SECONDS},
        "inventory.page.web_vital.layout_shift": {page_telemetry.LAYOUT_SHIFTS},
    }


def test_counts_package_tracking_changed_by_where(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
    _, key = new_admin(session)
    sign_in_with(client, code=code(key))
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
    form = {
        "name": cable.name,
        "category_id": str(cable.category_id),
        "unit": cable.unit,
        "version": str(cable.edit_version),
    }
    client.post(f"{items_path}/{cable.id}", data=form)
    changed = counted(reader, "inventory.package_tracking.changed")
    assert {
        scope: changed[(("scope", scope),)] for scope in ("bulk", "package", "item")
    } == {
        "bulk": 1,
        "package": 1,
        "item": 1,
    }


def test_counts_open_packages_closed_and_cleared(
    rolled_back: Recorded, session: Session
) -> None:
    client, _, reader, _ = rolled_back
    _, key = new_admin(session)
    sign_in_with(client, code=code(key))
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
    count = {
        "key": str(uuid.uuid4()),
        "as_of": str(ledger.moment(session, store)),
        f"opened_{four.id}": "",
        f"state_{four.id}": "missing",
    }
    client.post(f"/admin/counts/{store}", data=count)
    assert counted(reader, "inventory.open_packages.closed") == {
        (("how", "confirmed"),): 1,
        (("how", "direct"),): 1,
        (("how", "count"),): 1,
    }
    assert counted(reader, "inventory.open_packages.cleared") == {(): 1}
