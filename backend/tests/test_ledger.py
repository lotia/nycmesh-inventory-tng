"""The ledger (docs/decisions/0003): its rules in the database, and the batch
endpoint the scan page saves to."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, exc, insert, select, text, update
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.db import describe_error, set_actor
from inventory_tng.ledger import balance
from inventory_tng.models import (
    CatalogueHistory,
    Item,
    LedgerTransaction,
    Movement,
)

from databases import migrated_engine, new_database
from helpers.rows import add, violates
from helpers.stock import Place, received, write

URL = "/api/transactions"


@pytest.fixture
def place(session: Session) -> Place:
    return Place(session)


@pytest.fixture
def client(registered_client: TestClient) -> TestClient:
    """Every request here is from a registered device."""
    return registered_client


def saved(session: Session, key: str) -> list[LedgerTransaction]:
    return list(
        session.scalars(
            select(LedgerTransaction).where(LedgerTransaction.idempotency_key == key)
        )
    )


def balances(session: Session, *items: Item) -> dict[tuple[int, int], Decimal]:
    rows = session.execute(
        select(balance.c.item_id, balance.c.location_id, balance.c.quantity).where(
            balance.c.item_id.in_([item.id for item in items])
        )
    )
    return {(item_id, location_id): quantity for item_id, location_id, quantity in rows}


# The batch endpoint.


def test_a_batch_is_written_whole(
    client: TestClient, session: Session, place: Place
) -> None:
    lines = [
        {"item_id": place.item.id, "quantity": "3"},
        {"item_id": place.cable.id, "quantity": "12.5"},
    ]
    with capture_logs() as logs:
        response = client.post(URL, json=place.batch(lines=lines))
    assert response.status_code == 201
    transaction = session.get_one(LedgerTransaction, response.json()["transaction_id"])
    assert (transaction.kind, transaction.volunteer_id) == ("move", place.volunteer.id)
    assert transaction.device_id is not None
    assert balances(session, place.item, place.cable) == {
        (place.item.id, place.store.id): Decimal(-3),
        (place.item.id, place.van.id): Decimal(3),
        (place.cable.id, place.store.id): Decimal("-12.5"),
        (place.cable.id, place.van.id): Decimal("12.5"),
    }
    (line,) = [line for line in logs if line["event"] == "transaction recorded"]
    assert line["transaction_id"] == transaction.id
    assert line["lines"] == 2


def test_a_batch_sent_again_is_written_once(
    client: TestClient, session: Session, place: Place
) -> None:
    batch = place.batch()
    first = client.post(URL, json=batch)
    again = client.post(URL, json=batch)
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json() == again.json()
    assert len(saved(session, batch["key"])) == 1


def test_a_key_cannot_be_used_for_another_batch(
    client: TestClient, place: Place
) -> None:
    batch = place.batch()
    assert client.post(URL, json=batch).status_code == 201
    changed = batch | {"lines": [{"item_id": place.item.id, "quantity": "4"}]}
    response = client.post(URL, json=changed)
    assert (response.status_code, response.json()) == (409, {"reason": "key_reused"})


def test_a_refusal_names_each_bad_line(
    client: TestClient, session: Session, place: Place
) -> None:
    lines = [
        {"item_id": place.item.id, "quantity": "1"},
        {"item_id": 999999, "quantity": "1"},
        {"item_id": place.item.id, "quantity": "1.5"},
        {"item_id": place.cable.id, "quantity": "1.5"},
    ]
    batch = place.batch(lines=lines, to_location_id=999999, volunteer_id=999999)
    response = client.post(URL, json=batch)
    assert response.status_code == 422
    assert [(p["loc"], p["type"]) for p in response.json()["detail"]] == [
        (["body", "volunteer_id"], "not_found"),
        (["body", "to_location_id"], "not_found"),
        (["body", "lines", 1, "item_id"], "not_found"),
        (["body", "lines", 2, "quantity"], "not_whole"),
    ]
    assert saved(session, batch["key"]) == []


@pytest.mark.parametrize(
    "fields",
    [
        {"to_location_id": None},
        {"kind": "receipt"},
        {"kind": "install"},
        {"install_number": 1234},
        {"kind": "count"},
        {"lines": []},
        {"lines": [{"item_id": 1, "quantity": "0"}]},
        # Not ids at all, and never past the database's integer.
        {"volunteer_id": -1},
        {"kind": "install", "to_location_id": None, "install_number": 2**31},
    ],
)
def test_a_batch_that_cannot_be_is_refused(
    client: TestClient, place: Place, fields: dict[str, Any]
) -> None:
    assert client.post(URL, json=place.batch(**fields)).status_code == 422


def test_an_unregistered_device_saves_nothing(
    client: TestClient, session: Session, place: Place
) -> None:
    client.cookies.clear()
    batch = place.batch()
    response = client.post(URL, json=batch)
    assert (response.status_code, response.json()) == (403, {"reason": "unregistered"})
    assert saved(session, batch["key"]) == []


def test_a_retired_volunteer_is_still_accepted(
    client: TestClient, session: Session, place: Place
) -> None:
    """The batch may have waited in the outbox while they were merged."""
    place.volunteer.retired_at = datetime.now(UTC)
    session.flush()
    assert client.post(URL, json=place.batch()).status_code == 201


def test_an_install_records_its_install_number(
    client: TestClient, session: Session, place: Place
) -> None:
    batch = place.batch(kind="install", to_location_id=None, install_number=1234)
    response = client.post(URL, json=batch)
    assert response.status_code == 201
    transaction = session.get_one(LedgerTransaction, response.json()["transaction_id"])
    assert transaction.install_number == 1234
    assert balances(session, place.item) == {(place.item.id, place.store.id): -3}


def test_a_receipt_lowers_on_order_but_not_below_zero(
    client: TestClient, session: Session, place: Place
) -> None:
    place.item.on_order = Decimal(5)
    place.cable.on_order = Decimal(100)
    session.flush()
    lines = [
        {"item_id": place.item.id, "quantity": "8"},
        {"item_id": place.cable.id, "quantity": "30"},
    ]
    batch = place.batch(kind="receipt", from_location_id=None, lines=lines)
    assert client.post(URL, json=batch).status_code == 201
    session.expire_all()
    assert (place.item.on_order, place.cable.on_order) == (0, 70)
    actor = session.scalar(
        select(CatalogueHistory.actor)
        .where(
            CatalogueHistory.table_name == "item",
            CatalogueHistory.row_id == place.item.id,
        )
        .order_by(CatalogueHistory.id.desc())
        .limit(1)
    )
    assert actor == f"volunteer:{place.volunteer.id}"


# The rules in the database, which every writer meets.


@pytest.fixture
def written(session: Session, place: Place) -> LedgerTransaction:
    set_actor(session, "system:test")
    return write(session, place)


@pytest.mark.parametrize(
    "change",
    [
        lambda t: (
            update(LedgerTransaction)
            .where(LedgerTransaction.id == t.id)
            .values(kind="adjustment")
        ),
        lambda t: delete(LedgerTransaction).where(LedgerTransaction.id == t.id),
        lambda t: (
            update(Movement).where(Movement.transaction_id == t.id).values(quantity=1)
        ),
        lambda t: delete(Movement).where(Movement.transaction_id == t.id),
    ],
    ids=[
        "update transaction",
        "delete transaction",
        "update movement",
        "delete movement",
    ],
)
def test_the_ledger_is_append_only(
    session: Session, written: LedgerTransaction, change: Callable[..., Any]
) -> None:
    with violates(session, "ledger_append_only"):
        session.execute(change(written))


@pytest.mark.parametrize("table", ["ledger_transaction", "movement"])
def test_the_ledger_cannot_be_truncated(session: Session, table: str) -> None:
    # Nothing written first: PostgreSQL refuses a TRUNCATE in a transaction
    # with a deferred check still to run.
    with violates(session, "ledger_append_only"):
        session.execute(text(f"TRUNCATE {table} CASCADE"))


def test_movements_are_never_added_to_a_recorded_transaction() -> None:
    """On a database of its own: the transaction is committed, as yesterday's
    would be, and nothing can delete it from the ledger (conftest's
    APPEND_ONLY), so it must not stay where later tests count stock."""
    with new_database() as url, migrated_engine(url) as engine:
        with Session(engine, expire_on_commit=False) as first, first.begin():
            place = Place(first)
            committed = write(first, place).id
        with (
            Session(engine) as session,
            violates(session, "movement_with_transaction"),
        ):
            session.execute(
                insert(Movement).values(
                    transaction_id=committed, item_id=place.item.id, quantity=1
                )
            )


def test_a_transaction_has_movements(session: Session, place: Place) -> None:
    """All but a count, which may find what the records say (migration 0009)."""
    session.add(
        LedgerTransaction(
            idempotency_key=uuid.uuid4(),
            kind="receipt",
            to_location_id=place.store.id,
        )
    )
    session.flush()
    # The check waits for the commit; this brings it forward.
    with pytest.raises(exc.IntegrityError) as raised:
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert describe_error(raised.value)["constraint"] == "ledger_transaction_lines"


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"kind": "move", "to_location_id": None}, "ck_ledger_transaction_sides"),
        ({"kind": "receipt"}, "ck_ledger_transaction_sides"),
        ({"kind": "install"}, "ck_ledger_transaction_sides"),
        ({"install_number": 12}, "ck_ledger_transaction_install_number_on_install"),
        (
            {"kind": "install", "to_location_id": None, "install_number": 0},
            "ck_ledger_transaction_install_number",
        ),
        ({"kind": "loan"}, "ck_ledger_transaction_kind"),
    ],
)
def test_transaction_checks(
    session: Session, place: Place, fields: dict[str, Any], constraint: str
) -> None:
    row = {
        "idempotency_key": uuid.uuid4(),
        "kind": "move",
        "from_location_id": place.store.id,
        "to_location_id": place.van.id,
    } | fields
    with violates(session, constraint):
        session.add(LedgerTransaction(**row))


def test_a_transaction_goes_between_two_places(session: Session, place: Place) -> None:
    with violates(session, "ck_ledger_transaction_two_places"):
        session.add(
            LedgerTransaction(
                idempotency_key=uuid.uuid4(),
                kind="move",
                from_location_id=place.store.id,
                to_location_id=place.store.id,
            )
        )


def test_items_counted_in_each_move_in_whole_units(
    session: Session, place: Place, written: LedgerTransaction
) -> None:
    with violates(session, "movement_whole_units"):
        session.execute(
            insert(Movement).values(
                transaction_id=written.id, item_id=place.item.id, quantity="0.5"
            )
        )


def test_an_item_with_fractional_movements_is_never_counted_in_each(
    session: Session, place: Place
) -> None:
    write(session, place, lines=[{"item_id": place.cable.id, "quantity": "1.5"}])
    with violates(session, "movement_whole_units"):
        session.execute(
            update(Item).where(Item.id == place.cable.id).values(unit="each")
        )


def test_the_server_clock_decides_when(session: Session, place: Place) -> None:
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    transaction = add(
        session,
        LedgerTransaction(
            idempotency_key=uuid.uuid4(),
            kind="receipt",
            to_location_id=place.store.id,
            created_at=long_ago,
        ),
    )
    session.refresh(transaction)
    assert transaction.created_at > long_ago + timedelta(days=365)


def test_a_retired_item_is_taken_never_received(
    client: TestClient, session: Session, place: Place
) -> None:
    """What is left of it may go out; nothing more comes in."""
    received(session, place)
    place.item.retired_at = datetime.now(UTC)
    session.flush()
    receipt = place.batch(
        kind="receipt", from_location_id=None, to_location_id=place.store.id
    )
    refused = client.post(URL, json=receipt)
    assert refused.status_code == 422
    assert [(p["loc"], p["type"]) for p in refused.json()["detail"]] == [
        (["body", "lines", 0, "item_id"], "retired")
    ]
    assert client.post(URL, json=place.batch()).status_code == 201


def test_the_database_never_receives_a_retired_item(
    session: Session, place: Place
) -> None:
    """Whoever writes it: not only the batch endpoint."""
    place.item.retired_at = datetime.now(UTC)
    session.flush()
    receipt = add(
        session,
        LedgerTransaction(
            idempotency_key=uuid.uuid4(), kind="receipt", to_location_id=place.store.id
        ),
    )
    with violates(session, "receipt_retired_item"):
        session.add(
            Movement(transaction_id=receipt.id, item_id=place.item.id, quantity=1)
        )
