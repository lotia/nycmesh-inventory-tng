"""Sealed and open packages in the ledger (docs/decisions/0020): the form
each line leaves and arrives in, balance by form, and the rules
insert_transaction and open_packages.make keep. The rules as pure
functions first, then each through its write on PostgreSQL."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import exc, select
from sqlalchemy.orm import Session

from inventory_tng import open_packages
from inventory_tng.ledger import (
    LOOSE,
    Entry,
    Form,
    OpenFacts,
    PackageFacts,
    balance,
    by_form,
    form_breaks,
    insert_transaction,
    moment,
    record_count,
    tracks_opened,
)
from inventory_tng.models import Admin, Item, ItemPackage, LedgerTransaction

from helpers.rows import add, breaks, tag, violates
from helpers.stock import Spools

CABLE, OTHER = 1, 2
SPOOL, BOX = 10, 11
OPEN, GONE, ELSEWHERE, FRESH = 20, 21, 22, 23
STORE, VAN = 100, 101
OLD = 12
PACKAGES = {
    SPOOL: PackageFacts(CABLE, Decimal(305), tracked=True),
    BOX: PackageFacts(CABLE, Decimal(100), tracked=False),
    OLD: PackageFacts(CABLE, Decimal(50), tracked=False, retired=True),
}
OPENS = {
    OPEN: OpenFacts(CABLE, SPOOL, STORE, closed=False, left=Decimal(120), new=False),
    GONE: OpenFacts(CABLE, SPOOL, STORE, closed=True, left=Decimal(0), new=False),
    ELSEWHERE: OpenFacts(OTHER, SPOOL, STORE, closed=False, left=Decimal(5), new=False),
    FRESH: OpenFacts(CABLE, SPOOL, STORE, closed=False, left=Decimal(0), new=True),
}
SEALED, SEALED_BOX = Form(package_id=SPOOL), Form(package_id=BOX)


def opened(open_id: int) -> Form:
    return Form(open_package_id=open_id)


def broken(
    entry: Entry,
    kind: str = "move",
    places: tuple[int | None, int | None] = (STORE, VAN),
) -> list[str]:
    return form_breaks(kind, entry, places, PACKAGES, OPENS)


def line(
    quantity: int, leaves: Form = LOOSE, arrives: Form = LOOSE, item: int = CABLE
) -> Entry:
    return Entry(item, Decimal(quantity), leaves, arrives)


@pytest.mark.parametrize(
    ("item", "package", "tracks"),
    [
        (False, None, False),
        (True, None, True),
        (True, False, False),
        (False, True, True),
    ],
)
def test_a_package_tracks_opened_as_set_on_it_else_as_its_item(
    item: bool, package: bool | None, tracks: bool
) -> None:
    assert tracks_opened(item, package) is tracks


@pytest.mark.parametrize(
    ("entry", "kind", "places"),
    [
        (line(5), "move", (STORE, VAN)),
        (line(610, SEALED, SEALED), "move", (STORE, VAN)),
        (line(610, LOOSE, SEALED), "receipt", (None, STORE)),
        (line(305, SEALED), "install", (STORE, None)),
        (line(20, opened(OPEN)), "move", (STORE, VAN)),
        (line(20, opened(OPEN)), "install", (STORE, None)),
        (line(120, opened(OPEN), opened(OPEN)), "move", (STORE, VAN)),
        (line(305, SEALED, opened(FRESH)), "open", (STORE, STORE)),
        (line(200, SEALED_BOX), "open", (STORE, STORE)),
        (line(3, LOOSE, opened(OPEN)), "count", (None, STORE)),
        (line(5, opened(OPEN)), "count", (STORE, None)),
        (line(50, Form(package_id=OLD), Form(package_id=OLD)), "move", (STORE, VAN)),
        (line(7, LOOSE, opened(FRESH)), "count", (None, STORE)),
    ],
)
def test_lines_keeping_the_rules(
    entry: Entry, kind: str, places: tuple[int | None, int | None]
) -> None:
    assert broken(entry, kind, places) == []


@pytest.mark.parametrize(
    ("entry", "kind", "places", "rule"),
    [
        (line(305, SEALED), "receipt", (None, STORE), "movement_form_outside"),
        (
            line(5, LOOSE, opened(OPEN)),
            "install",
            (STORE, None),
            "movement_form_outside",
        ),
        (
            line(305, Form(package_id=99), Form(package_id=99)),
            "move",
            (STORE, VAN),
            "movement_package_of_item",
        ),
        (
            line(305, SEALED, SEALED, item=OTHER),
            "move",
            (STORE, VAN),
            "movement_package_of_item",
        ),
        (line(300, SEALED, SEALED), "move", (STORE, VAN), "movement_whole_packages"),
        (line(5, opened(99)), "move", (STORE, VAN), "movement_open_package_of_item"),
        (
            line(5, opened(ELSEWHERE)),
            "move",
            (STORE, VAN),
            "movement_open_package_of_item",
        ),
        (
            line(5, opened(GONE)),
            "install",
            (STORE, None),
            "movement_open_package_closed",
        ),
        (
            line(5, opened(FRESH)),
            "install",
            (STORE, None),
            "movement_open_package_unopened",
        ),
        (
            line(50, LOOSE, Form(package_id=OLD)),
            "receipt",
            (None, STORE),
            "receipt_retired_package",
        ),
        (line(5, opened(OPEN)), "move", (VAN, STORE), "movement_open_package_place"),
        (
            line(5, LOOSE, opened(OPEN)),
            "count",
            (None, VAN),
            "movement_open_package_place",
        ),
        (line(305, LOOSE, SEALED), "move", (STORE, VAN), "movement_form_change"),
        (line(305, SEALED), "move", (STORE, VAN), "movement_form_change"),
        (
            line(5, opened(OPEN), opened(OPEN)),
            "move",
            (STORE, VAN),
            "movement_open_package_whole",
        ),
        (line(5), "open", (STORE, STORE), "opening_from_sealed"),
        (line(5, opened(OPEN)), "open", (STORE, STORE), "opening_from_sealed"),
        (line(305, SEALED, SEALED), "open", (STORE, STORE), "opening_from_sealed"),
        (line(305, SEALED), "open", (STORE, STORE), "opening_follows_setting"),
        (
            line(100, SEALED_BOX, opened(FRESH)),
            "open",
            (STORE, STORE),
            "opening_follows_setting",
        ),
        (
            line(305, SEALED, opened(OPEN)),
            "open",
            (STORE, STORE),
            "open_package_opened_once",
        ),
        (line(610, SEALED, opened(FRESH)), "open", (STORE, STORE), "open_package_full"),
    ],
)
def test_each_rule_a_line_can_break(
    entry: Entry,
    kind: str,
    places: tuple[int | None, int | None],
    rule: str,
) -> None:
    assert broken(entry, kind, places) == [rule]


# On PostgreSQL.


@pytest.fixture
def spools(session: Session) -> Spools:
    return Spools(session)


def forms(
    session: Session, item: Item
) -> dict[tuple[int, int | None, int | None], Decimal]:
    rows = session.execute(
        select(
            by_form.c.location_id,
            by_form.c.package_id,
            by_form.c.open_package_id,
            by_form.c.quantity,
        ).where(by_form.c.item_id == item.id, by_form.c.quantity != 0)
    )
    return {(place, package, opened): amount for place, package, opened, amount in rows}


def totals(session: Session, item: Item) -> dict[int, Decimal]:
    rows = session.execute(
        select(balance.c.location_id, balance.c.quantity).where(
            balance.c.item_id == item.id
        )
    )
    return {place: amount for place, amount in rows if amount}


def test_stock_is_held_in_forms_and_totals_sum_them(
    session: Session, spools: Spools
) -> None:
    store, van, spool = spools.store.id, spools.van.id, spools.spool.id
    assert forms(session, spools.cable) == {(store, spool, None): 610}
    made = spools.opened()
    assert forms(session, spools.cable) == {
        (store, spool, None): 305,
        (store, None, made.id): 305,
    }
    # A cut: from the open spool, loose in the van.
    spools.move(Entry(spools.cable.id, Decimal(20), Form(open_package_id=made.id)))
    assert forms(session, spools.cable) == {
        (store, spool, None): 305,
        (store, None, made.id): 285,
        (van, None, None): 20,
    }
    assert totals(session, spools.cable) == {store: 590, van: 20}
    # Moved whole, it is in the van.
    whole = Form(open_package_id=made.id)
    spools.move(Entry(spools.cable.id, Decimal(285), whole, whole))
    assert made.location_id == van
    assert forms(session, spools.cable)[(van, None, made.id)] == 285
    assert totals(session, spools.cable) == {store: 305, van: 305}


def test_an_untracked_package_opens_into_loose_units(session: Session) -> None:
    spools = Spools(session, tracked=False)
    store = spools.store.id
    here = {"kind": "open", "from_location_id": store, "to_location_id": store}
    spools.write(here, Entry(spools.cable.id, Decimal(305), spools.sealed))
    assert forms(session, spools.cable) == {
        (store, spools.spool.id, None): 305,
        (store, None, None): 305,
    }
    with breaks(session, "open_package_untracked"):
        open_packages.make(session, uuid.uuid4(), spools.spool.id, store)


def test_a_package_of_its_own_overrides_its_item(session: Session) -> None:
    spools = Spools(session, tracked=False)
    spools.spool.track_opened = True
    session.flush()
    assert spools.opened().package_id == spools.spool.id
    spools.cable.track_opened = True
    spools.spool.track_opened = False
    session.flush()
    with breaks(session, "open_package_untracked"):
        open_packages.make(session, uuid.uuid4(), spools.spool.id, spools.store.id)


def test_an_open_package_is_made_once_per_key(session: Session, spools: Spools) -> None:
    key = uuid.uuid4()
    store, van = spools.store.id, spools.van.id
    made = open_packages.make(session, key, spools.spool.id, store)
    assert open_packages.make(session, key, spools.spool.id, store) is made
    with breaks(session, "open_package_key"):
        open_packages.make(session, key, spools.spool.id, van)
    with breaks(session, "open_package_untracked"):
        open_packages.make(session, uuid.uuid4(), 999999, store)


def test_each_rule_is_refused_through_the_ledger(
    session: Session, spools: Spools
) -> None:
    """Each by its name, before anything is written."""
    cable, store = spools.cable.id, spools.store.id
    made = spools.opened()
    opened = Form(open_package_id=made.id)
    here = {"kind": "open", "from_location_id": store, "to_location_id": store}
    outside = {"kind": "receipt", "to_location_id": store}
    cases = [
        ("movement_form_outside", outside, Entry(cable, Decimal(1), opened)),
        (
            "movement_whole_packages",
            None,
            Entry(cable, Decimal(300), spools.sealed, spools.sealed),
        ),
        ("movement_form_change", None, Entry(cable, Decimal(305), spools.sealed)),
        ("movement_open_package_whole", None, Entry(cable, Decimal(5), opened, opened)),
        ("opening_follows_setting", here, Entry(cable, Decimal(305), spools.sealed)),
        (
            "open_package_opened_once",
            here,
            Entry(cable, Decimal(305), spools.sealed, opened),
        ),
        (
            "movement_item_once",
            None,
            Entry(cable, Decimal(1), opened),
            Entry(cable, Decimal(2), opened),
        ),
    ]
    for rule, header, *lines in cases:
        with breaks(session, rule):
            if header is None:
                spools.move(*lines)
            else:
                spools.write(header, *lines)
    other = add(
        session, ItemPackage(item_id=spools.place.item.id, name=f"Box {tag()}", size=10)
    )
    with breaks(session, "movement_package_of_item"):
        spools.move(
            Entry(
                cable, Decimal(10), Form(package_id=other.id), Form(package_id=other.id)
            )
        )
    with breaks(session, "movement_open_package_place"):
        spools.write(
            {
                "kind": "move",
                "from_location_id": spools.van.id,
                "to_location_id": store,
            },
            Entry(cable, Decimal(1), opened),
        )
    closer = add(session, Admin(username=f"closer-{tag()}"))
    made.closed_at, made.closed_by_admin_id = datetime.now(UTC), closer.id
    session.flush()
    with breaks(session, "movement_open_package_closed"):
        spools.move(Entry(cable, Decimal(1), opened))


def test_a_fresh_open_package_must_be_full_and_of_its_package(
    session: Session, spools: Spools
) -> None:
    store = spools.store.id
    here = {"kind": "open", "from_location_id": store, "to_location_id": store}
    made = open_packages.make(session, uuid.uuid4(), spools.spool.id, store)
    opened = Form(open_package_id=made.id)
    with breaks(session, "open_package_full"):
        spools.write(here, Entry(spools.cable.id, Decimal(610), spools.sealed, opened))
    with breaks(session, "opening_from_sealed"):
        spools.write(here, Entry(spools.cable.id, Decimal(305), LOOSE, opened))


def test_the_database_keeps_one_form_a_side_and_opening_at_one_place(
    session: Session, spools: Spools
) -> None:
    with violates(session, "ck_ledger_transaction_sides"):
        session.add(
            LedgerTransaction(
                idempotency_key=uuid.uuid4(),
                kind="open",
                from_location_id=spools.store.id,
                to_location_id=spools.van.id,
                time_zone="UTC",
            )
        )
    with violates(session, "ck_ledger_transaction_two_places"):
        session.add(
            LedgerTransaction(
                idempotency_key=uuid.uuid4(),
                kind="move",
                from_location_id=spools.store.id,
                to_location_id=spools.store.id,
                time_zone="UTC",
            )
        )


def test_an_opening_sent_again_is_written_once(
    session: Session, spools: Spools
) -> None:
    """Its rules read what it changed itself, so the key is found first."""
    store, key = spools.store.id, uuid.uuid4()
    made = open_packages.make(session, uuid.uuid4(), spools.spool.id, store)
    here = {"kind": "open", "from_location_id": store, "to_location_id": store}
    opening = Entry(
        spools.cable.id, Decimal(305), spools.sealed, Form(open_package_id=made.id)
    )
    assert insert_transaction(session, key, here, [opening]) is not None
    assert insert_transaction(session, key, here, [opening]) is None


def test_an_open_package_is_on_one_line_of_a_transaction(
    session: Session, spools: Spools
) -> None:
    """Each line is checked against what was left before it: a whole move
    and a cut together would take more than there is."""
    opened = Form(open_package_id=spools.opened().id)
    cable = spools.cable.id
    with breaks(session, "movement_open_package_once"):
        spools.move(
            Entry(cable, Decimal(305), opened, opened),
            Entry(cable, Decimal(20), opened),
        )


def test_an_open_package_needs_a_place_that_is_there(
    session: Session, spools: Spools
) -> None:
    """Not a race on its key: the database's refusal, as it is."""
    with pytest.raises(exc.IntegrityError), session.begin_nested():
        open_packages.make(session, uuid.uuid4(), spools.spool.id, 999999)


def test_opening_a_package_is_no_move_to_a_count(
    session: Session, spools: Spools
) -> None:
    """The total at the place is the same, so a count's line for it is
    written, not asked for again."""
    as_of = moment(session, spools.store.id)
    spools.opened()
    counter = add(session, Admin(username=f"counter-{tag()}"))
    counted = record_count(
        session,
        uuid.uuid4(),
        counter.id,
        spools.store.id,
        [(spools.cable.id, Decimal(610))],
        as_of,
    )
    assert counted.moved == {}


# The scan page's batches (POST /api/transactions): sealed packages stay
# sealed.

URL = "/api/transactions"


def sealed_line(spools: Spools, quantity: str) -> dict[str, object]:
    return {
        "item_id": spools.cable.id,
        "quantity": quantity,
        "package_id": spools.spool.id,
    }


def test_sealed_packages_saved_arrive_sealed(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    store, van, spool = spools.store.id, spools.van.id, spools.spool.id
    lines = [sealed_line(spools, "305"), {"item_id": spools.cable.id, "quantity": "5"}]
    batch = spools.place.batch(lines=lines)
    assert registered_client.post(URL, json=batch).status_code == 201
    assert registered_client.post(URL, json=batch).status_code == 200
    assert forms(session, spools.cable) == {
        (store, spool, None): 305,
        (store, None, None): -5,
        (van, spool, None): 305,
        (van, None, None): 5,
    }
    other = batch | {"lines": [sealed_line(spools, "610")]}
    assert registered_client.post(URL, json=other).status_code == 409
    receipt = spools.place.batch(
        kind="receipt",
        from_location_id=None,
        to_location_id=store,
        lines=[sealed_line(spools, "305")],
    )
    assert registered_client.post(URL, json=receipt).status_code == 201
    assert forms(session, spools.cable)[(store, spool, None)] == 610


def test_a_batch_saved_before_packages_were_kept_sealed_saves_loose(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    """An outbox's batch from an older page, of units alone."""
    lines = [{"item_id": spools.cable.id, "quantity": "305"}]
    response = registered_client.post(URL, json=spools.place.batch(lines=lines))
    assert response.status_code == 201
    assert forms(session, spools.cable)[(spools.van.id, None, None)] == 305


def test_a_refusal_names_each_bad_package_line(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    other = add(
        session, ItemPackage(item_id=spools.place.item.id, name=f"Box {tag()}", size=10)
    )
    lines = [
        sealed_line(spools, "300"),
        {"item_id": spools.cable.id, "quantity": "10", "package_id": other.id},
        sealed_line(spools, "305"),
        {"item_id": spools.cable.id, "quantity": "1"},
    ]
    response = registered_client.post(URL, json=spools.place.batch(lines=lines))
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "item_id"], "repeated"),
        (["lines", 0, "quantity"], "not_whole_packages"),
        (["lines", 1, "package_id"], "not_found"),
        (["lines", 2, "item_id"], "repeated"),
    ]


def test_a_retired_package_is_never_received(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    """As a retired item is not (docs/decisions/0003)."""
    spools.spool.retired_at = datetime.now(UTC)
    session.flush()
    receipt = spools.place.batch(
        kind="receipt",
        from_location_id=None,
        to_location_id=spools.store.id,
        lines=[sealed_line(spools, "305")],
    )
    response = registered_client.post(URL, json=receipt)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "package_id"], "retired_package")
    ]
