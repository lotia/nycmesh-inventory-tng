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
)
from inventory_tng.models import (
    Admin,
    Item,
    ItemPackage,
    LedgerTransaction,
    OpenPackage,
)
from inventory_tng.open_packages import tracks_opened

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


# Opening on the scan page.


def opening(spools: Spools, *lines: dict[str, object]) -> dict[str, object]:
    store = spools.store.id
    return spools.place.batch(
        kind="open", from_location_id=store, to_location_id=store, lines=list(lines)
    )


def opened_line(spools: Spools, **fields: object) -> dict[str, object]:
    return sealed_line(spools, "305") | {"open_package_key": str(uuid.uuid4())} | fields


def test_a_package_opened_and_followed_is_made_once(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    line = opened_line(spools)
    batch = opening(spools, line)
    assert registered_client.post(URL, json=batch).status_code == 201
    assert registered_client.post(URL, json=batch).status_code == 200
    made = session.scalars(
        select(OpenPackage).where(
            OpenPackage.key == uuid.UUID(str(line["open_package_key"]))
        )
    ).one()
    store, spool = spools.store.id, spools.spool.id
    assert (made.package_id, made.location_id) == (spool, store)
    assert forms(session, spools.cable) == {
        (store, spool, None): 305,
        (store, None, made.id): 305,
    }
    listed = registered_client.get("/api/catalogue").json()["open_packages"]
    mine = [entry for entry in listed if entry["id"] == made.id]
    assert mine == [
        {
            "id": made.id,
            "key": str(made.key),
            "item_id": spools.cable.id,
            "package_id": spool,
            "location_id": store,
            "opened_at": mine[0]["opened_at"],
            "left": "305.000",
        }
    ]
    package = next(
        pack
        for item in registered_client.get("/api/catalogue").json()["items"]
        if item["id"] == spools.cable.id
        for pack in item["packages"]
    )
    assert package["track_opened"] is True


def test_a_package_its_item_does_not_follow_opens_loose(
    registered_client: TestClient, session: Session
) -> None:
    """Whatever key the page sent: the server's setting decides."""
    spools = Spools(session, tracked=False)
    batch = opening(spools, opened_line(spools, quantity="610"))
    assert registered_client.post(URL, json=batch).status_code == 201
    store = spools.store.id
    assert forms(session, spools.cable) == {(store, None, None): 610}


def test_an_opening_without_a_key_makes_one_from_the_batch(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    batch = opening(spools, sealed_line(spools, "305"))
    assert registered_client.post(URL, json=batch).status_code == 201
    assert registered_client.post(URL, json=batch).status_code == 200
    made = session.scalars(
        select(OpenPackage).where(OpenPackage.item_id == spools.cable.id)
    ).all()
    assert len(made) == 1


def test_a_followed_package_opens_one_a_line(
    registered_client: TestClient, spools: Spools
) -> None:
    batch = opening(spools, opened_line(spools, quantity="610"))
    response = registered_client.post(URL, json=batch)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "quantity"], "one_package")
    ]


def test_an_opening_that_cannot_be_is_refused(
    registered_client: TestClient, spools: Spools
) -> None:
    """At one place, of packages: else FastAPI's 422, before anything."""
    batch = opening(spools, opened_line(spools))
    for change in (
        {"to_location_id": None},
        {"to_location_id": spools.van.id},
        {"lines": [{"item_id": spools.cable.id, "quantity": "1"}]},
    ):
        response = registered_client.post(URL, json=batch | change)
        assert response.status_code == 422, change
    same = spools.place.batch(to_location_id=spools.store.id)
    assert registered_client.post(URL, json=same).status_code == 422


def test_only_an_opening_names_an_open_package_key(
    registered_client: TestClient, spools: Spools
) -> None:
    batch = spools.place.batch(lines=[opened_line(spools)])
    assert registered_client.post(URL, json=batch).status_code == 422


def test_an_opening_the_server_cannot_take_is_refused_saying_why(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    """Never a 500, which the outbox would send again and again."""
    gone = opening(spools, opened_line(spools, package_id=999999))
    response = registered_client.post(URL, json=gone)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "package_id"], "not_found")
    ]
    # A key used for another place: a rule only the write can see.
    key = uuid.uuid4()
    open_packages.make(session, key, spools.spool.id, spools.van.id)
    reused = opening(spools, opened_line(spools, open_package_key=str(key)))
    response = registered_client.post(URL, json=reused)
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "refused"


def test_packages_the_page_thought_followed_open_loose_together(
    registered_client: TestClient, session: Session
) -> None:
    """The setting changed since the page loaded: two lines, one each,
    become one loose line, and sent again, are found written."""
    spools = Spools(session, tracked=False)
    batch = opening(spools, opened_line(spools), opened_line(spools))
    assert registered_client.post(URL, json=batch).status_code == 201
    assert registered_client.post(URL, json=batch).status_code == 200
    store = spools.store.id
    assert forms(session, spools.cable) == {(store, None, None): 610}


# Cutting from an open package, and moving one whole, on the scan page.


def taken(made: OpenPackage, quantity: str, **fields: object) -> dict[str, object]:
    return {
        "item_id": made.item_id,
        "quantity": quantity,
        "open_package_key": str(made.key),
    } | fields


def test_cut_from_an_open_package_or_moved_whole_by_its_key(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    store, van = spools.store.id, spools.van.id
    cut = spools.place.batch(lines=[taken(made, "20")])
    assert registered_client.post(URL, json=cut).status_code == 201
    assert registered_client.post(URL, json=cut).status_code == 200
    assert forms(session, spools.cable)[(store, None, made.id)] == 285
    assert forms(session, spools.cable)[(van, None, None)] == 20
    whole = spools.place.batch(lines=[taken(made, "285", whole=True)])
    assert registered_client.post(URL, json=whole).status_code == 201
    session.refresh(made)
    assert made.location_id == van
    assert forms(session, spools.cable)[(van, None, made.id)] == 285
    # Cut at an install, from the van.
    install = spools.place.batch(
        kind="install",
        from_location_id=van,
        to_location_id=None,
        lines=[taken(made, "30")],
    )
    assert registered_client.post(URL, json=install).status_code == 201
    assert forms(session, spools.cable)[(van, None, made.id)] == 255


def test_taking_from_an_open_package_is_refused_saying_why(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    van = spools.van.id

    def refused(batch: dict[str, object]) -> list[tuple[list[object], str]]:
        response = registered_client.post(URL, json=batch)
        assert response.status_code == 422
        return [(p["loc"][1:], p["type"]) for p in response.json()["detail"]]

    unknown = taken(made, "1", open_package_key=str(uuid.uuid4()))
    assert refused(spools.place.batch(lines=[unknown])) == [
        (["lines", 0, "open_package_key"], "not_found")
    ]
    assert refused(spools.place.batch(lines=[taken(made, "5", whole=True)])) == [
        (["lines", 0, "quantity"], "open_package_whole")
    ]
    elsewhere = spools.place.batch(
        from_location_id=van, to_location_id=spools.store.id, lines=[taken(made, "1")]
    )
    assert refused(elsewhere) == [
        (["lines", 0, "open_package_key"], "open_package_elsewhere")
    ]
    closer = add(session, Admin(username=f"closer-{tag()}"))
    made.closed_at, made.closed_by_admin_id = datetime.now(UTC), closer.id
    session.flush()
    assert refused(spools.place.batch(lines=[taken(made, "1")])) == [
        (["lines", 0, "open_package_key"], "open_package_closed")
    ]


def test_a_line_taken_from_an_open_package_that_cannot_be_is_refused(
    registered_client: TestClient, spools: Spools
) -> None:
    made = spools.opened()
    store = spools.store.id
    for batch in (
        spools.place.batch(lines=[taken(made, "1", package_id=spools.spool.id)]),
        spools.place.batch(
            kind="receipt",
            from_location_id=None,
            to_location_id=store,
            lines=[taken(made, "1")],
        ),
        spools.place.batch(
            kind="install",
            to_location_id=None,
            lines=[taken(made, "1", whole=True)],
        ),
        spools.place.batch(
            lines=[{"item_id": made.item_id, "quantity": "1", "whole": True}]
        ),
    ):
        assert registered_client.post(URL, json=batch).status_code == 422
