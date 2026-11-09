"""The ledger (docs/decisions/0003): its rules, which insert_transaction
enforces, the privileges that keep it append-only, and the batch endpoint
the scan page saves to."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import items
from inventory_tng.database_role import LEDGER
from inventory_tng.db import set_actor
from inventory_tng.ledger import (
    balance,
    insert_transaction,
    line_breaks,
    lines_break,
    on_order_after,
    record_adjustment,
    repeated,
    whole_enough,
)
from inventory_tng.models import (
    AdjustmentReason,
    Admin,
    CatalogueHistory,
    Item,
    LedgerTransaction,
    Movement,
)

from helpers.rows import add, breaks, named, refused, violates
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
        (["body", "lines", 0, "item_id"], "repeated"),
        (["body", "lines", 1, "item_id"], "not_found"),
        (["body", "lines", 2, "item_id"], "repeated"),
        (["body", "lines", 2, "quantity"], "not_whole"),
    ]
    assert saved(session, batch["key"]) == []


def test_a_batch_naming_an_item_twice_is_refused(
    client: TestClient, session: Session, place: Place
) -> None:
    """Each line of the item is named, nothing is written, and the key is
    still free for the batch put right."""
    lines = [
        {"item_id": place.item.id, "quantity": "3"},
        {"item_id": place.cable.id, "quantity": "1"},
        {"item_id": place.item.id, "quantity": "2"},
    ]
    batch = place.batch(lines=lines)
    response = client.post(URL, json=batch)
    assert response.status_code == 422
    assert [(p["loc"], p["type"]) for p in response.json()["detail"]] == [
        (["body", "lines", 0, "item_id"], "repeated"),
        (["body", "lines", 2, "item_id"], "repeated"),
    ]
    assert saved(session, batch["key"]) == []
    assert balances(session, place.item, place.cable) == {}
    merged = batch | {"lines": [{"item_id": place.item.id, "quantity": "5"}]}
    first = client.post(URL, json=merged)
    again = client.post(URL, json=merged)
    assert (first.status_code, again.status_code) == (201, 200)
    assert balances(session, place.item)[(place.item.id, place.van.id)] == 5


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


@pytest.mark.parametrize(
    ("on_order", "received", "after"),
    [
        (Decimal(5), Decimal(3), Decimal(2)),
        (Decimal(5), Decimal(8), Decimal(0)),
        (Decimal("2.5"), Decimal("2.5"), Decimal(0)),
    ],
)
def test_on_order_goes_down_by_what_arrives(
    on_order: Decimal, received: Decimal, after: Decimal
) -> None:
    assert on_order_after(on_order, received) == after


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


# The ledger's rules, without the database.


@pytest.mark.parametrize(
    ("unit", "amount", "whole"),
    [
        ("each", Decimal(3), True),
        ("each", Decimal("3.000"), True),
        ("each", Decimal("0.5"), False),
        ("m", Decimal("0.5"), True),
    ],
)
def test_an_item_counted_in_each_moves_in_whole_units(
    unit: str, amount: Decimal, whole: bool
) -> None:
    assert whole_enough(unit, amount) is whole


@pytest.mark.parametrize(
    ("kind", "unit", "amount", "retired", "broken"),
    [
        ("move", "each", Decimal(1), False, []),
        ("move", "each", Decimal("1.5"), False, ["movement_whole_units"]),
        ("move", "m", Decimal("1.5"), False, []),
        ("receipt", "each", Decimal(1), True, ["receipt_retired_item"]),
        # What is left of a retired item may still go, or be counted.
        ("move", "each", Decimal(1), True, []),
        ("install", "each", Decimal(1), True, []),
        ("count", "each", Decimal(1), True, []),
        ("adjustment", "each", Decimal(1), True, []),
        # Every rule broken is named, in this order.
        (
            "receipt",
            "each",
            Decimal("1.5"),
            True,
            ["movement_whole_units", "receipt_retired_item"],
        ),
    ],
)
def test_the_rules_a_line_breaks(
    kind: str, unit: str, amount: Decimal, retired: bool, broken: list[str]
) -> None:
    item = Item(unit=unit, retired_at=datetime.now(UTC) if retired else None)
    assert line_breaks(kind, item, amount) == broken


def test_each_line_is_checked_on_its_own() -> None:
    """A line of an item not held is the foreign key's to refuse."""
    radio = Item(id=1, unit="each")
    lines = [(1, Decimal(1)), (2, Decimal(1)), (1, Decimal("1.5"))]
    assert lines_break("move", {1: radio}, lines) == [
        [],
        None,
        ["movement_whole_units"],
    ]


@pytest.mark.parametrize(
    ("item_ids", "twice"),
    [
        ([1], set()),
        ([1, 2, 3], set()),
        ([1, 2, 1], {0, 2}),
        ([4, 4, 5, 5, 4], {0, 1, 2, 3, 4}),
    ],
)
def test_a_repeated_item_names_each_of_its_lines(
    item_ids: list[int], twice: set[int]
) -> None:
    assert repeated(item_ids) == twice


# The ledger's rules, on PostgreSQL: each refused by the function that
# writes it.


def test_a_transaction_has_movements(session: Session, place: Place) -> None:
    """All but a count, which may find what the records say (migration
    0009)."""
    header = {"kind": "receipt", "to_location_id": place.store.id}
    with breaks(session, "ledger_transaction_lines"):
        insert_transaction(session, uuid.uuid4(), header, {})


def test_items_counted_in_each_move_in_whole_units(
    session: Session, place: Place
) -> None:
    """In an adjustment too, which never passes through a batch's checks."""
    admin = add(session, Admin(username="ada"))
    reason = named(session, AdjustmentReason, "Found")
    with breaks(session, "movement_whole_units"):
        record_adjustment(
            session,
            uuid.uuid4(),
            admin.id,
            place.store.id,
            place.item.id,
            Decimal("0.5"),
            found=True,
            reason_id=reason.id,
        )


def test_a_retired_item_is_never_received(session: Session, place: Place) -> None:
    """Whoever writes it: not only the batch endpoint."""
    place.item.retired_at = datetime.now(UTC)
    session.flush()
    header = {"kind": "receipt", "to_location_id": place.store.id}
    with breaks(session, "receipt_retired_item"):
        insert_transaction(session, uuid.uuid4(), header, {place.item.id: Decimal(1)})


def test_a_line_of_no_item_is_the_foreign_keys_to_refuse(
    session: Session, place: Place
) -> None:
    header = {"kind": "receipt", "to_location_id": place.store.id}
    with violates(session, "fk_movement_item_id_item"):
        insert_transaction(session, uuid.uuid4(), header, {999999: Decimal(1)})


def test_the_server_clock_decides_when(
    client: TestClient, session: Session, place: Place
) -> None:
    """A created_at sent with a batch is ignored: the database's now() is
    when it happened."""
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    response = client.post(URL, json=place.batch(created_at=long_ago.isoformat()))
    assert response.status_code == 201
    transaction = session.get_one(LedgerTransaction, response.json()["transaction_id"])
    assert transaction.created_at > long_ago + timedelta(days=365)


def test_the_server_clock_decides_when_whoever_writes(
    session: Session, place: Place
) -> None:
    """insert_transaction ignores a created_at in its header too."""
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    header = {
        "kind": "receipt",
        "to_location_id": place.store.id,
        "created_at": long_ago,
    }
    lines = {place.item.id: Decimal(1)}
    transaction = insert_transaction(session, uuid.uuid4(), header, lines)
    assert transaction is not None
    assert transaction.created_at > long_ago + timedelta(days=365)


# The privileges that keep the ledger append-only.


@pytest.fixture
def written(session: Session, place: Place) -> LedgerTransaction:
    set_actor(session, "system:test")
    return write(session, place)


CHANGES = pytest.mark.parametrize(
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


@CHANGES
def test_the_app_may_not_change_the_ledger(
    session: Session, written: LedgerTransaction, change: Callable[..., Any]
) -> None:
    """Its login lacks the privileges (migrations 0001 and 0004)."""
    with refused(session):
        session.execute(change(written))


@pytest.mark.parametrize("table", LEDGER)
def test_the_app_may_not_truncate_the_ledger(session: Session, table: str) -> None:
    with refused(session):
        session.execute(text(f"TRUNCATE {table} CASCADE"))


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


def test_an_items_unit_is_fixed_once_stock_has_moved(
    session: Session, place: Place
) -> None:
    """In whole units or not: the ledger's quantities are in that unit."""
    items.save(session, place.cable, {"unit": "ft"})
    write(session, place, lines=[{"item_id": place.cable.id, "quantity": "1.5"}])
    for unit in ("each", "m"):
        with breaks(session, "item_unit_fixed"):
            items.save(session, place.cable, {"unit": unit})
    items.save(session, place.cable, {"unit": "ft", "notes": "By the reel"})
    assert (place.cable.unit, place.cable.notes) == ("ft", "By the reel")


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
