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
from structlog.testing import capture_logs

from inventory_tng import adjustment_reasons, ledger, open_packages
from inventory_tng.admin import stock
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
    AdjustmentReason,
    Admin,
    Item,
    ItemPackage,
    LedgerTransaction,
    OpenPackage,
)
from inventory_tng.open_packages import tracks_opened

from helpers.rows import add, breaks, tag, violates
from helpers.sign_in import text
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
            "package_name": spools.spool.name,
            "location_id": store,
            "opened_at": mine[0]["opened_at"],
            "left": "305.000",
            "short": False,
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


def test_packages_the_page_thought_loose_open_one_each(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    """Tracking turned on since the page loaded (inventory-tng-1o0): its
    one line of two spools, with no key, becomes two open packages, under
    keys made from the batch's, and sent again, is found written."""
    batch = opening(spools, sealed_line(spools, "610"))
    with capture_logs() as logs:
        assert registered_client.post(URL, json=batch).status_code == 201
    assert registered_client.post(URL, json=batch).status_code == 200
    made = session.scalars(
        select(OpenPackage)
        .where(OpenPackage.item_id == spools.cable.id)
        .order_by(OpenPackage.id)
    ).all()
    assert len(made) == 2
    store = spools.store.id
    assert forms(session, spools.cable) == {
        (store, None, made[0].id): 305,
        (store, None, made[1].id): 305,
    }
    (split,) = [
        x for x in logs if x["event"] == "opening line split into open packages"
    ]
    assert (split["item_package_id"], split["packages"]) == (spools.spool.id, 2)
    # The same key with one spool fewer is another batch, as is one of
    # more than a batch could open, found so without making their keys.
    for quantity in ("305", "305000000"):
        other = batch | {"lines": [sealed_line(spools, quantity)]}
        assert registered_client.post(URL, json=other).status_code == 409
    # And one of a package there is none of.
    gone = batch | {"lines": [sealed_line(spools, "610") | {"package_id": 999999}]}
    assert registered_client.post(URL, json=gone).status_code == 409


def test_an_opening_written_loose_is_found_after_tracking_is_turned_on(
    registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session, tracked=False)
    batch = opening(spools, sealed_line(spools, "610"))
    assert registered_client.post(URL, json=batch).status_code == 201
    spools.cable.track_opened = True
    session.flush()
    assert registered_client.post(URL, json=batch).status_code == 200
    assert forms(session, spools.cable) == {(spools.store.id, None, None): 610}


def test_an_opening_of_more_packages_than_lines_is_refused(
    registered_client: TestClient, spools: Spools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ledger, "MAX_LINES", 1)
    batch = opening(spools, sealed_line(spools, "610"))
    response = registered_client.post(URL, json=batch)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "quantity"], "one_package")
    ]


def test_an_opening_of_part_of_a_package_is_refused(
    registered_client: TestClient, spools: Spools
) -> None:
    batch = opening(spools, sealed_line(spools, "400"))
    response = registered_client.post(URL, json=batch)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "quantity"], "not_whole_packages")
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


def test_an_open_package_named_on_two_lines_is_refused_as_twice(
    registered_client: TestClient, spools: Spools
) -> None:
    made = spools.opened()
    both = spools.place.batch(lines=[taken(made, "305", whole=True), taken(made, "20")])
    response = registered_client.post(URL, json=both)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "open_package_key"], "repeated"),
        (["lines", 1, "open_package_key"], "repeated"),
    ]
    whole_opening = opening(spools, opened_line(spools, whole=True))
    assert registered_client.post(URL, json=whole_opening).status_code == 422


# Volunteers report an open package used up (8i4.133.5).


def test_a_cut_reports_its_open_package_used_up_and_it_is_offered_no_more(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    cut = spools.place.batch(lines=[taken(made, "305")], used_up=[str(made.key)])
    assert registered_client.post(URL, json=cut).status_code == 201
    assert registered_client.post(URL, json=cut).status_code == 200
    session.refresh(made)
    assert made.reported_by_volunteer_id == spools.place.volunteer.id
    assert made.reported_at is not None
    listed = registered_client.get("/api/catalogue").json()["open_packages"]
    assert made.id not in [entry["id"] for entry in listed]
    # Its metres stay in stock: no adjustment is written.
    assert not session.scalars(
        select(LedgerTransaction).where(LedgerTransaction.kind == "adjustment")
    ).first()


def test_the_button_reports_one_alone(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    report = spools.place.batch(
        kind="report", to_location_id=None, lines=[], used_up=[str(made.key)]
    )
    response = registered_client.post(URL, json=report)
    assert response.status_code == 201
    written = session.get_one(LedgerTransaction, response.json()["transaction_id"])
    assert (written.kind, written.from_location_id) == ("report", spools.store.id)
    session.refresh(made)
    reported_at = made.reported_at
    assert reported_at is not None
    # Reported again, by another batch: as it was.
    again = report | {"key": str(uuid.uuid4())}
    assert registered_client.post(URL, json=again).status_code == 201
    session.refresh(made)
    assert made.reported_at == reported_at


def test_a_report_that_cannot_be_is_refused(
    registered_client: TestClient, spools: Spools
) -> None:
    unknown = spools.place.batch(
        kind="report", to_location_id=None, lines=[], used_up=[str(uuid.uuid4())]
    )
    response = registered_client.post(URL, json=unknown)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["used_up", 0], "not_found")
    ]
    for batch in (
        spools.place.batch(kind="report", to_location_id=None, lines=[]),
        spools.place.batch(lines=[]),
        spools.place.batch(
            kind="report", to_location_id=None, used_up=[str(uuid.uuid4())]
        ),
    ):
        assert registered_client.post(URL, json=batch).status_code == 422


def test_a_report_names_what_is_where_it_is_said_and_not_moving(
    registered_client: TestClient, spools: Spools
) -> None:
    made = spools.opened()
    key = str(made.key)

    def refused(batch: dict[str, object]) -> list[object]:
        response = registered_client.post(URL, json=batch)
        assert response.status_code == 422
        return [(p["loc"][1:], p["type"]) for p in response.json()["detail"]]

    elsewhere = spools.place.batch(
        kind="report",
        from_location_id=spools.van.id,
        to_location_id=None,
        lines=[],
        used_up=[key],
    )
    assert refused(elsewhere) == [(["used_up", 0], "open_package_elsewhere")]
    moving = spools.place.batch(lines=[taken(made, "305", whole=True)], used_up=[key])
    assert refused(moving) == [(["used_up", 0], "moved_whole")]
    receipt = spools.place.batch(
        kind="receipt",
        from_location_id=None,
        to_location_id=spools.store.id,
        used_up=[key],
    )
    assert registered_client.post(URL, json=receipt).status_code == 422


# Administrators confirm or clear reports, or close directly (8i4.133.6).


USED_UP = "/admin/stock/used-up"


def reported(session: Session, spools: Spools) -> OpenPackage:
    made = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(300), Form(open_package_id=made.id)))
    open_packages.report(
        session, [made.id], spools.place.volunteer.id, datetime.now(UTC)
    )
    session.flush()
    return made


def test_waiting_for_you_until_each_report_is_answered(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    made = reported(session, spools)
    home = text(admin_client.get("/admin"))
    assert "Waiting for you" in home
    assert f'<a href="{USED_UP}">Confirm</a>' in home
    page = text(admin_client.get(USED_UP))
    assert f'name="open_package" value="{made.id}"' in page
    done = admin_client.post(f"{USED_UP}/{made.id}/confirm")
    assert done.headers["location"] == f"{USED_UP}?notice=used_up_confirmed"
    session.refresh(made)
    assert made.closed_at is not None
    # What it still showed, 5 m, is written off as used up: it shows nothing.
    assert (spools.store.id, None, made.id) not in forms(session, spools.cable)
    adjustment = session.scalars(
        select(LedgerTransaction).where(
            LedgerTransaction.kind == "adjustment",
            LedgerTransaction.from_location_id == spools.store.id,
        )
    ).one()
    reason = session.get_one(AdjustmentReason, adjustment.reason_id)
    assert reason.name == "Used up"
    assert "Waiting for you" not in text(admin_client.get("/admin"))
    # Asked again, nothing more is written.
    admin_client.post(f"{USED_UP}/{made.id}/confirm")
    assert (
        len(
            session.scalars(
                select(LedgerTransaction).where(
                    LedgerTransaction.reason_id == reason.id
                )
            ).all()
        )
        == 1
    )


def test_a_report_cleared_is_offered_again(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    made = reported(session, spools)
    admin_client.post(f"{USED_UP}/{made.id}/clear")
    session.refresh(made)
    assert (made.reported_at, made.closed_at) == (None, None)
    listed = registered_client.get("/api/catalogue").json()["open_packages"]
    assert made.id in [entry["id"] for entry in listed]


def test_reports_ticked_are_answered_together(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    one, two = reported(session, spools), reported(session, spools)
    done = admin_client.post(
        USED_UP, data={"open_package": [str(one.id), str(two.id)], "do": "confirm"}
    )
    assert done.headers["location"] == f"{USED_UP}?notice=used_up_confirmed"
    for row in (one, two):
        session.refresh(row)
        assert row.closed_at is not None
    none = admin_client.post(USED_UP, data={"do": "clear"})
    assert none.headers["location"] == f"{USED_UP}?notice=used_up_none"
    assert admin_client.post(USED_UP, data={"do": "maybe"}).status_code == 404
    assert admin_client.post(f"{USED_UP}/{one.id}/maybe").status_code == 404
    assert admin_client.post(f"{USED_UP}/999999/confirm").status_code == 404


def test_an_open_package_is_closed_from_its_items_page(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """Reported or not: nothing waits. Less than nothing is found."""
    made = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(310), Form(open_package_id=made.id)))
    page = f"/admin/items/{spools.cable.id}"
    assert "Close as used up" in text(admin_client.get(page))
    path = f"{page}/open-packages/{made.id}/close"
    # Asked first: it writes off what it shows.
    asked = text(admin_client.get(path))
    assert "it shows\n-5 m" in asked or "it shows -5 m" in " ".join(asked.split())
    assert '<button class="danger">Close as used up</button>' in asked
    done = admin_client.post(path)
    assert done.headers["location"] == (
        f"{page}?notice=open_package_closed&section=open-packages#open-packages"
    )
    # The last one closed: its section is there to say so.
    shown = text(admin_client.get(done.headers["location"]))
    assert 'id="open-packages"' in shown
    assert admin_client.get(path).status_code == 404
    session.refresh(made)
    assert made.closed_at is not None
    assert (spools.store.id, None, made.id) not in forms(session, spools.cable)
    other = f"/admin/items/{spools.place.item.id}/open-packages/{made.id}/close"
    assert admin_client.post(other).status_code == 404


def test_the_used_up_reason_is_there_whatever_administrators_did(
    session: Session,
) -> None:
    """Seeded by migration 0026; brought back if retired; made again if
    renamed away."""
    reason = adjustment_reasons.used_up(session)
    assert reason.name == "Used up"
    reason.retired_at = datetime.now(UTC)
    session.flush()
    assert adjustment_reasons.used_up(session) is reason
    assert reason.retired_at is None
    reason.name = "Spent"
    session.flush()
    again = adjustment_reasons.used_up(session)
    assert (again.name, again.id != reason.id) == ("Used up", True)


def test_closing_or_clearing_twice_changes_nothing_more(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    made = reported(session, spools)
    closer = add(session, Admin(username=f"closer-{tag()}"))
    assert open_packages.close(session, 999999, closer.id, datetime.now(UTC)) is False
    assert open_packages.clear(session, made.id) is True
    assert open_packages.clear(session, made.id) is False
    first = open_packages.close(session, made.id, closer.id, datetime.now(UTC))
    again = open_packages.close(session, made.id, closer.id, datetime.now(UTC))
    assert (first, again) == (True, False)
    # Ticked when no longer reported: left as it is.
    done = admin_client.post(
        USED_UP, data={"open_package": [str(made.id)], "do": "clear"}
    )
    assert done.headers["location"] == f"{USED_UP}?notice=used_up_unchanged"


def test_an_open_package_showing_nothing_closes_with_nothing_written_off(
    session: Session, spools: Spools
) -> None:
    made = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(305), Form(open_package_id=made.id)))
    closer = add(session, Admin(username=f"closer-{tag()}"))
    assert open_packages.close(session, made.id, closer.id, datetime.now(UTC))
    assert not session.scalars(
        select(LedgerTransaction).where(LedgerTransaction.admin_id == closer.id)
    ).first()


def test_a_confirm_after_a_clear_closes_nothing(
    session: Session, spools: Spools
) -> None:
    """Confirming checks the report still stands, once the package is locked."""
    made = spools.opened()
    closer = add(session, Admin(username=f"closer-{tag()}"))
    when = datetime.now(UTC)
    assert (
        open_packages.close(session, made.id, closer.id, when, reported=True) is False
    )
    assert made.closed_at is None
    assert ledger.left_on(session, []) == {}


def test_whats_left_is_where_it_is_now(session: Session, spools: Spools) -> None:
    """Moved whole, its old place keeps a row, at nothing."""
    made = spools.opened()
    whole = Form(open_package_id=made.id)
    spools.move(Entry(spools.cable.id, Decimal(305), whole, whole))
    assert ledger.left_on(session, [made]) == {made.id: 305}


# Stock by form, short ends, minimum sealed and reorder (8i4.133.7).


def test_stock_splits_into_forms_and_short_ends_are_not_usable() -> None:
    packages = {
        SPOOL: open_packages.PackageSize("Spool", Decimal(305), Decimal(15)),
        BOX: open_packages.PackageSize("Box", Decimal(100), None),
    }
    rows = [
        (SPOOL, None, Decimal(915)),
        (None, OPEN, Decimal(120)),
        (None, GONE, Decimal(12)),
        (None, FRESH, Decimal(3)),
        (None, None, Decimal(85)),
    ]
    forms = open_packages.split(rows, packages, {OPEN: SPOOL, GONE: SPOOL, FRESH: BOX})
    assert forms.sealed == [("Spool", Decimal(3), Decimal(915))]
    assert (forms.open, forms.short_ends) == ([Decimal(120), Decimal(3)], [Decimal(12)])
    assert (forms.loose, forms.total, forms.usable) == (85, 1135, 1123)
    # Less than nothing left is the records', not a short end.
    below = open_packages.split([(None, OPEN, Decimal(-8))], packages, {OPEN: SPOOL})
    assert (below.open, below.short_ends) == ([Decimal(-8)], [])


def test_reorder_on_usable_stock_and_sealed_minimums() -> None:
    cable = Item(id=1, name="Cable", minimum=Decimal(400))
    rope = Item(id=2, name="Rope", minimum=None)
    spool = ItemPackage(id=10, item_id=1, name="Spool", minimum_sealed=3)
    coil = ItemPackage(id=11, item_id=2, name="Coil", minimum_sealed=1)
    usable = {1: Decimal(500), 2: Decimal(50)}
    sealed = {1: {10: Decimal(2)}, 2: {11: Decimal(1)}}
    loose = ItemPackage(id=12, item_id=1, name="Box", minimum_sealed=None)
    (row,) = stock.reorder_rows(
        [cable, rope], usable, sealed, [spool, coil, loose], {1}
    )
    assert (row.item, row.in_stores, row.count_first) == (cable, 500, True)
    assert row.low_sealed == [("Spool", Decimal(2), 3)]
    assert (
        stock.reorder_rows([cable], {1: Decimal(400)}, {}, [], set())[0].item is cable
    )


def test_stock_and_reorder_pages_show_forms(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    spools.spool.short_below, spools.spool.minimum_sealed = Decimal(15), 2
    spools.cable.minimum = Decimal(400)
    made = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(295), Form(open_package_id=made.id)))
    session.flush()
    page = text(admin_client.get("/admin/stock"))
    for line in ("1 sealed Spool", "short end: 10 m", "usable: 305 m"):
        assert f'<span class="hint block">{line}</span>' in page
    reorder = " ".join(text(admin_client.get("/admin/stock/reorder")).split())
    assert spools.cable.name in reorder
    assert "1 sealed Spool, of 2 to keep" in reorder


def test_a_packages_settings_are_set_on_its_items_page(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    path = f"/admin/items/{spools.cable.id}/packages/{spools.spool.id}/settings"
    done = admin_client.post(path, data={"short_below": "15", "minimum_sealed": "2"})
    assert done.headers["location"].endswith(
        "notice=package_settings&section=packages#packages"
    )
    session.refresh(spools.spool)
    assert (spools.spool.short_below, spools.spool.minimum_sealed) == (15, 2)
    # An open one below it is a short end, as the scan page is told.
    made = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(295), Form(open_package_id=made.id)))
    listed = registered_client.get("/api/catalogue").json()["open_packages"]
    assert [entry["short"] for entry in listed if entry["id"] == made.id] == [True]
    admin_client.post(path, data={"short_below": "", "minimum_sealed": ""})
    session.refresh(spools.spool)
    assert (spools.spool.short_below, spools.spool.minimum_sealed) == (None, None)
    for bad, words in (
        ({"short_below": "-1"}, None),
        ({"short_below": "305"}, "Shorter than a whole one, or blank."),
        ({"minimum_sealed": "lots"}, "A whole number of packages, or blank."),
    ):
        refused = admin_client.post(path, data=bad)
        assert refused.status_code == 400
        assert words is None or words in text(refused)
    other = f"/admin/items/{spools.place.item.id}/packages/{spools.spool.id}/settings"
    assert admin_client.post(other, data={}).status_code == 404
    # Its item retired: read only.
    spools.cable.retired_at = datetime.now(UTC)
    session.flush()
    assert admin_client.post(path, data={}).status_code == 409
    spools.cable.retired_at = None
    spools.spool.retired_at = datetime.now(UTC)
    session.flush()
    assert admin_client.post(path, data={}).status_code == 404


# Counting by form (8i4.133.8).


def test_a_count_by_form_finds_and_loses_form_by_form() -> None:
    then = {(CABLE, SEALED): Decimal(610), (CABLE, opened(OPEN)): Decimal(120)}
    counted = [
        (CABLE, SEALED, Decimal(305)),
        (CABLE, opened(OPEN), Decimal(120)),
        (CABLE, LOOSE, Decimal(40)),
    ]
    found, lost = ledger.count_corrections(counted, then)
    assert found == [Entry(CABLE, Decimal(40), LOOSE, LOOSE)]
    assert lost == [Entry(CABLE, Decimal(305), SEALED, LOOSE)]


def counter(session: Session) -> int:
    return add(session, Admin(username=f"counter-{tag()}")).id


def test_record_count_by_form(session: Session, spools: Spools) -> None:
    made = spools.opened()
    store, cable = spools.store.id, spools.cable.id
    as_of = ledger.moment(session, store)
    lines = [
        (cable, spools.sealed, Decimal(0)),
        (cable, Form(open_package_id=made.id), Decimal(250)),
        (cable, LOOSE, Decimal(20)),
    ]
    done = ledger.record_count(
        session, uuid.uuid4(), counter(session), store, [], as_of, by_form=lines
    )
    assert (done.differed, done.moved) == (True, {})
    assert forms(session, spools.cable) == {
        (store, None, made.id): 250,
        (store, None, None): 20,
    }
    with breaks(session, "movement_item_once"):
        ledger.record_count(
            session,
            uuid.uuid4(),
            counter(session),
            store,
            [(cable, Decimal(1))],
            ledger.moment(session, store),
            by_form=lines[:1],
        )
    with breaks(session, "movement_item_once"):
        ledger.record_count(
            session,
            uuid.uuid4(),
            counter(session),
            store,
            [],
            ledger.moment(session, store),
            by_form=[lines[2], lines[2]],
        )


def test_an_opening_while_counting_by_form_asks_again(
    session: Session, spools: Spools
) -> None:
    store, cable = spools.store.id, spools.cable.id
    as_of = ledger.moment(session, store)
    spools.opened()
    done = ledger.record_count(
        session,
        uuid.uuid4(),
        counter(session),
        store,
        [],
        as_of,
        by_form=[(cable, spools.sealed, Decimal(610))],
    )
    assert cable in done.moved
    assert done.written == [] or all(each.lines == 0 for each in done.written)


COUNTS = "/admin/counts"


def test_the_count_page_counts_forms_finds_and_closes(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    store, cable = spools.store.id, spools.cable.id
    one, two = spools.opened(), spools.opened()
    page = text(admin_client.get(f"{COUNTS}/{store}"))
    assert f'name="sealed_{cable}_{spools.spool.id}"' not in page  # None sealed now.
    assert f'name="opened_{one.id}"' in page
    assert f'name="state_{two.id}"' in page
    form = {
        "key": str(uuid.uuid4()),
        "as_of": str(ledger.moment(session, store)),
        f"opened_{one.id}": "300",
        f"opened_{two.id}": "305",
        f"state_{two.id}": "missing",
        f"loose_{cable}": "7",
        "found_package_id": str(spools.spool.id),
        "found_left": "40",
    }
    done = admin_client.post(f"{COUNTS}/{store}", data=form)
    assert done.status_code == 303, text(done)
    session.refresh(two)
    assert two.closed_at is not None
    held = forms(session, spools.cable)
    assert held[(store, None, one.id)] == 300
    assert held[(store, None, None)] == 7
    found = session.scalars(
        select(OpenPackage).where(
            OpenPackage.key == uuid.uuid5(uuid.UUID(form["key"]), "found")
        )
    ).one()
    assert held[(store, None, found.id)] == 40
    lost = session.scalars(
        select(LedgerTransaction).where(LedgerTransaction.kind == "adjustment")
    ).one()
    assert session.get_one(AdjustmentReason, lost.reason_id).name == "Lost or stolen"
    bad = form | {"key": str(uuid.uuid4()), f"opened_{one.id}": "lots"}
    assert admin_client.post(f"{COUNTS}/{store}", data=bad).status_code == 400


def test_the_count_page_counts_sealed_and_says_whats_wrong(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    store, cable, spool = spools.store.id, spools.cable.id, spools.spool.id
    spools.write({"kind": "receipt", "to_location_id": store}, Entry(cable, Decimal(5)))
    field = f"sealed_{cable}_{spool}"
    assert f'name="{field}"' in text(admin_client.get(f"{COUNTS}/{store}"))
    as_of = str(ledger.moment(session, store))
    base = {"as_of": as_of}
    done = admin_client.post(
        f"{COUNTS}/{store}", data=base | {"key": str(uuid.uuid4()), field: "1"}
    )
    assert done.status_code == 303
    # Its loose line, left as the records had it, is counted as it was.
    assert forms(session, spools.cable) == {
        (store, spool, None): 305,
        (store, None, None): 5,
    }
    elsewhere = spools.opened()
    whole = Form(open_package_id=elsewhere.id)
    spools.move(Entry(cable, Decimal(305), whole, whole))
    bad = base | {
        "key": str(uuid.uuid4()),
        f"sealed_{cable}_999999": "1",
        field: "half",
        "loose_999999": "1",
        f"loose_{cable}": "lots",
        f"opened_{elsewhere.id}": "1",
        "found_package_id": "999999",
    }
    response = admin_client.post(f"{COUNTS}/{store}", data=bad)
    # Refused whole; what the page shows a field for is said by it.
    assert response.status_code == 400
    assert "A whole number of packages." in text(response)
    left = base | {
        "key": str(uuid.uuid4()),
        "found_package_id": str(spool),
        "found_left": "",
    }
    assert "How much is left on it." in text(
        admin_client.post(f"{COUNTS}/{store}", data=left)
    )


def test_counting_by_form_asks_again_for_what_moved(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    store, cable, spool = spools.store.id, spools.cable.id, spools.spool.id
    form = {
        "key": str(uuid.uuid4()),
        "as_of": str(ledger.moment(session, store)),
        f"sealed_{cable}_{spool}": "2",
    }
    spools.opened()  # While it was counted.
    again = admin_client.post(f"{COUNTS}/{store}", data=form)
    assert again.status_code == 409
    page = text(again)
    assert "These moved while you counted" in page
    # Asked again by form.
    assert f'name="sealed_{cable}_{spool}"' in page


def test_an_open_package_found_where_its_item_was_counted_by_total(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    van, cable = spools.van.id, spools.cable.id
    spools.move(Entry(cable, Decimal(10)))  # Loose, alone, in the van.
    form = {
        "key": str(uuid.uuid4()),
        "as_of": str(ledger.moment(session, van)),
        f"counted_{cable}": "10",
        "found_package_id": str(spools.spool.id),
        "found_left": "40",
    }
    assert admin_client.post(f"{COUNTS}/{van}", data=form).status_code == 303
    held = forms(session, spools.cable)
    assert held[(van, None, None)] == 10
    assert sorted(
        amount for (place, _, opened), amount in held.items() if place == van and opened
    ) == [40]


def test_open_packages_closed_by_a_count_and_refusals_said(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    store, cable, spool = spools.store.id, spools.cable.id, spools.spool.id
    one, two = spools.opened(), spools.opened()
    as_of = str(ledger.moment(session, store))
    # Not here, its left box cleared: closed all the same.
    gone = {
        "key": str(uuid.uuid4()),
        "as_of": as_of,
        f"opened_{one.id}": "",
        f"state_{one.id}": "used_up",
    }
    assert admin_client.post(f"{COUNTS}/{store}", data=gone).status_code == 303
    session.refresh(one)
    assert one.closed_at is not None
    closed = {"key": str(uuid.uuid4()), "as_of": as_of, f"opened_{one.id}": "5"}
    assert "was closed meanwhile" in text(
        admin_client.post(f"{COUNTS}/{store}", data=closed)
    )
    huge = {
        "key": str(uuid.uuid4()),
        "as_of": as_of,
        f"sealed_{cable}_{spool}": "9999999",
    }
    # None sealed is here now, so no line shows it: refused whole.
    assert admin_client.post(f"{COUNTS}/{store}", data=huge).status_code == 400
    spools.cable.track_opened = False
    session.flush()
    untracked = {
        "key": str(uuid.uuid4()),
        "as_of": str(ledger.moment(session, store)),
        "found_package_id": str(spool),
        "found_left": "40",
    }
    refused = admin_client.post(f"{COUNTS}/{store}", data=untracked)
    assert refused.status_code == 400
    assert "isn't followed once opened" in text(refused)
    assert two.closed_at is None


def test_an_open_package_found_while_its_item_moved_is_asked_again(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """Nothing of it is kept, the open package neither: found again then."""
    van, cable = spools.van.id, spools.cable.id
    spools.move(Entry(cable, Decimal(10)))
    key = uuid.uuid4()
    form = {
        "key": str(key),
        "as_of": str(ledger.moment(session, van)),
        f"counted_{cable}": "10",
        "found_package_id": str(spools.spool.id),
        "found_left": "40",
    }
    spools.move(Entry(cable, Decimal(5)))  # While it was counted.
    assert admin_client.post(f"{COUNTS}/{van}", data=form).status_code == 409
    made = select(OpenPackage).where(OpenPackage.key == uuid.uuid5(key, "found"))
    assert session.scalars(made).first() is None


# What only both batches together show (the combined review).


def test_a_place_holding_forms_that_net_to_nothing_holds_stock(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """A sealed spool there, and as much taken loose: not empty."""
    store = spools.store.id
    spools.move(Entry(spools.cable.id, Decimal(610)))  # Loose, though sealed.
    assert ledger.holds_stock(session, store)
    page = text(admin_client.get("/admin/stock"))
    assert "count it" in page


@pytest.mark.parametrize("amount", ["-2.000", "-0.500"])
def test_an_open_package_showing_less_than_nothing_is_listed(
    registered_client: TestClient, session: Session, spools: Spools, amount: str
) -> None:
    made = spools.opened()
    spools.move(
        Entry(
            spools.cable.id,
            Decimal(305) - Decimal(amount),
            Form(open_package_id=made.id),
        )
    )
    listed = registered_client.get("/api/catalogue").json()["open_packages"]
    assert [entry["left"] for entry in listed if entry["id"] == made.id] == [amount]
