"""Sealed and open packages in the ledger (docs/decisions/0020): the form
each line leaves and arrives in, balance by form, and the rules
insert_transaction and open_packages.make keep. The rules as pure
functions first, then each through its write on PostgreSQL."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import exc, select
from sqlalchemy.orm import Session

from inventory_tng.core.rule_names import RuleName
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.stock import LedgerTransaction
from inventory_tng.stock import open_packages
from inventory_tng.stock.balance import balance
from inventory_tng.stock.counts import record_count
from inventory_tng.stock.ledger import (
    LOOSE,
    Entry,
    Form,
    OpenFacts,
    PackageFacts,
    form_breaks,
    insert_transaction,
    tracks_opened,
)
from inventory_tng.stock.moments import moment

from helpers.package_forms import (
    BOX,
    CABLE,
    ELSEWHERE,
    FRESH,
    GONE,
    OPEN,
    OTHER,
    SEALED,
    SEALED_BOX,
    SPOOL,
    open_form,
    quantities_by_form,
)
from helpers.rows import add, breaks, tag, violates
from helpers.sign_in import new_admin_id
from helpers.stock import Spools, retire

STORE, VAN = 100, 101


OLD = 12
# Retired, and none left sealed at a current place.
ALL_GONE = 13


PACKAGES = {
    SPOOL: PackageFacts(CABLE, Decimal(305), tracked=True),
    BOX: PackageFacts(CABLE, Decimal(100), tracked=False),
    OLD: PackageFacts(CABLE, Decimal(50), tracked=False, retired=True),
    ALL_GONE: PackageFacts(
        CABLE, Decimal(50), tracked=False, retired=True, none_sealed=True
    ),
}


OPENS = {
    OPEN: OpenFacts(CABLE, SPOOL, STORE, closed=False, left=Decimal(120), new=False),
    GONE: OpenFacts(CABLE, SPOOL, STORE, closed=True, left=Decimal(0), new=False),
    ELSEWHERE: OpenFacts(OTHER, SPOOL, STORE, closed=False, left=Decimal(5), new=False),
    FRESH: OpenFacts(CABLE, SPOOL, STORE, closed=False, left=Decimal(0), new=True),
}


def broken(
    entry: Entry,
    kind: str = "move",
    places: tuple[int | None, int | None] = (STORE, VAN),
) -> list[RuleName]:
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
        (line(20, open_form(OPEN)), "move", (STORE, VAN)),
        (line(20, open_form(OPEN)), "install", (STORE, None)),
        (line(120, open_form(OPEN), open_form(OPEN)), "move", (STORE, VAN)),
        (line(305, SEALED, open_form(FRESH)), "open", (STORE, STORE)),
        (line(200, SEALED_BOX), "open", (STORE, STORE)),
        (line(3, LOOSE, open_form(OPEN)), "count", (None, STORE)),
        (line(5, open_form(OPEN)), "count", (STORE, None)),
        (line(50, Form(package_id=OLD), Form(package_id=OLD)), "move", (STORE, VAN)),
        (line(7, LOOSE, open_form(FRESH)), "count", (None, STORE)),
        # An administrator's count or adjustment may still find or lose it.
        (line(50, LOOSE, Form(package_id=ALL_GONE)), "count", (None, STORE)),
        (line(50, Form(package_id=ALL_GONE)), "adjustment", (STORE, None)),
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
            line(5, LOOSE, open_form(OPEN)),
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
        (line(5, open_form(99)), "move", (STORE, VAN), "movement_open_package_of_item"),
        (
            line(5, open_form(ELSEWHERE)),
            "move",
            (STORE, VAN),
            "movement_open_package_of_item",
        ),
        (
            line(5, open_form(GONE)),
            "install",
            (STORE, None),
            "movement_open_package_closed",
        ),
        (
            line(5, open_form(FRESH)),
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
        (line(5, open_form(OPEN)), "move", (VAN, STORE), "movement_open_package_place"),
        (
            line(5, LOOSE, open_form(OPEN)),
            "count",
            (None, VAN),
            "movement_open_package_place",
        ),
        (line(305, LOOSE, SEALED), "move", (STORE, VAN), "movement_form_change"),
        (line(305, SEALED), "move", (STORE, VAN), "movement_form_change"),
        (
            line(5, open_form(OPEN), open_form(OPEN)),
            "move",
            (STORE, VAN),
            "movement_open_package_whole",
        ),
        (
            line(50, Form(package_id=ALL_GONE), Form(package_id=ALL_GONE)),
            "move",
            (STORE, VAN),
            "movement_retired_package_none_left",
        ),
        (
            line(50, Form(package_id=ALL_GONE)),
            "install",
            (STORE, None),
            "movement_retired_package_none_left",
        ),
        (
            line(50, Form(package_id=ALL_GONE)),
            "open",
            (STORE, STORE),
            "movement_retired_package_none_left",
        ),
        (line(5), "open", (STORE, STORE), "opening_from_sealed"),
        (line(5, open_form(OPEN)), "open", (STORE, STORE), "opening_from_sealed"),
        (line(305, SEALED, SEALED), "open", (STORE, STORE), "opening_from_sealed"),
        (line(305, SEALED), "open", (STORE, STORE), "opening_follows_setting"),
        (
            line(100, SEALED_BOX, open_form(FRESH)),
            "open",
            (STORE, STORE),
            "opening_follows_setting",
        ),
        (
            line(305, SEALED, open_form(OPEN)),
            "open",
            (STORE, STORE),
            "open_package_opened_once",
        ),
        (
            line(610, SEALED, open_form(FRESH)),
            "open",
            (STORE, STORE),
            "open_package_full",
        ),
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
    assert quantities_by_form(session, spools.cable) == {(store, spool, None): 610}
    made = spools.opened()
    assert quantities_by_form(session, spools.cable) == {
        (store, spool, None): 305,
        (store, None, made.id): 305,
    }
    # A cut: from the open spool, loose in the van.
    spools.move(Entry(spools.cable.id, Decimal(20), Form(open_package_id=made.id)))
    assert quantities_by_form(session, spools.cable) == {
        (store, spool, None): 305,
        (store, None, made.id): 285,
        (van, None, None): 20,
    }
    assert totals(session, spools.cable) == {store: 590, van: 20}
    # Moved whole, it is in the van.
    whole = Form(open_package_id=made.id)
    spools.move(Entry(spools.cable.id, Decimal(285), whole, whole))
    assert made.location_id == van
    assert quantities_by_form(session, spools.cable)[(van, None, made.id)] == 285
    assert totals(session, spools.cable) == {store: 305, van: 305}


def test_an_untracked_package_opens_into_loose_units(session: Session) -> None:
    spools = Spools(session, tracked=False)
    store = spools.store.id
    here = {"kind": "open", "from_location_id": store, "to_location_id": store}
    spools.write(here, Entry(spools.cable.id, Decimal(305), spools.sealed))
    assert quantities_by_form(session, spools.cable) == {
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


def open_here(spools: Spools, *lines: Entry) -> object:
    store = spools.store.id
    here = {"kind": "open", "from_location_id": store, "to_location_id": store}
    return spools.write(here, *lines)


def of_another_item(spools: Spools, _opened: Form) -> object:
    other = add(
        spools.session,
        ItemPackage(item_id=spools.place.item.id, name=f"Box {tag()}", size=10),
    )
    whole = Form(package_id=other.id)
    return spools.move(Entry(spools.cable.id, Decimal(10), whole, whole))


def from_where_it_is_not(spools: Spools, opened: Form) -> object:
    van_to_store = {
        "kind": "move",
        "from_location_id": spools.van.id,
        "to_location_id": spools.store.id,
    }
    return spools.write(van_to_store, Entry(spools.cable.id, Decimal(1), opened))


# Each rule the ledger refuses, and a write of the spools, one opened as
# `opened`, that breaks it.
LEDGER_BREAKS: list[tuple[str, Callable[[Spools, Form], object]]] = [
    (
        "movement_form_outside",
        lambda spools, opened: spools.write(
            {"kind": "receipt", "to_location_id": spools.store.id},
            Entry(spools.cable.id, Decimal(1), opened),
        ),
    ),
    (
        "movement_whole_packages",
        lambda spools, _: spools.move(
            Entry(spools.cable.id, Decimal(300), spools.sealed, spools.sealed)
        ),
    ),
    (
        "movement_form_change",
        lambda spools, _: spools.move(
            Entry(spools.cable.id, Decimal(305), spools.sealed)
        ),
    ),
    (
        "movement_open_package_whole",
        lambda spools, opened: spools.move(
            Entry(spools.cable.id, Decimal(5), opened, opened)
        ),
    ),
    (
        "opening_follows_setting",
        lambda spools, _: open_here(
            spools, Entry(spools.cable.id, Decimal(305), spools.sealed)
        ),
    ),
    (
        "open_package_opened_once",
        lambda spools, opened: open_here(
            spools, Entry(spools.cable.id, Decimal(305), spools.sealed, opened)
        ),
    ),
    (
        "movement_item_once",
        lambda spools, opened: spools.move(
            Entry(spools.cable.id, Decimal(1), opened),
            Entry(spools.cable.id, Decimal(2), opened),
        ),
    ),
    ("movement_package_of_item", of_another_item),
    ("movement_open_package_place", from_where_it_is_not),
]


@pytest.mark.parametrize(
    ("rule", "write"), LEDGER_BREAKS, ids=[rule for rule, _ in LEDGER_BREAKS]
)
def test_each_rule_is_refused_through_the_ledger(
    session: Session,
    spools: Spools,
    rule: str,
    write: Callable[[Spools, Form], object],
) -> None:
    """Each by its name, before anything is written."""
    opened = Form(open_package_id=spools.opened().id)
    with breaks(session, rule):
        write(spools, opened)


def test_a_retired_package_is_taken_until_none_is_left_sealed(
    session: Session, spools: Spools
) -> None:
    """Taken below zero at one place while some is sealed at another, as
    any stock is (docs/decisions/0003); once none is left sealed at any
    current place, never taken again (0003, Amendment, 2026-11-19)."""
    store, van = spools.store.id, spools.van.id
    retire(session, spools.spool)
    assert spools.move(
        Entry(spools.cable.id, Decimal(305), spools.sealed, spools.sealed)
    )
    assert spools.install(store, 610)
    assert spools.install(van)
    assert quantities_by_form(session, spools.cable) == {
        (store, spools.spool.id, None): -305
    }
    with breaks(session, "movement_retired_package_none_left"):
        spools.install(store)
    with breaks(session, "movement_retired_package_none_left"):
        spools.move(Entry(spools.cable.id, Decimal(305), spools.sealed, spools.sealed))


def test_a_retired_package_left_only_at_a_retired_place_is_taken_from_there(
    session: Session, spools: Spools
) -> None:
    """A save that waited in an outbox may take from a retired place what
    is there (docs/decisions/0003), sealed too, though no current place
    has any; from another place, it is refused."""
    store, van = spools.store.id, spools.van.id
    assert spools.install(store)
    assert spools.move(
        Entry(spools.cable.id, Decimal(305), spools.sealed, spools.sealed)
    )
    retire(session, spools.spool, spools.van)
    with breaks(session, "movement_retired_package_none_left"):
        spools.install(store)
    assert spools.install(van)
    assert quantities_by_form(session, spools.cable) == {}


def test_a_closed_open_package_is_refused_through_the_ledger(
    session: Session, spools: Spools
) -> None:
    made = spools.opened()
    closer = new_admin_id(session)
    made.closed_at, made.closed_by_admin_id = datetime.now(UTC), closer
    session.flush()
    with breaks(session, "movement_open_package_closed"):
        spools.move(Entry(spools.cable.id, Decimal(1), Form(open_package_id=made.id)))


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
    counter = new_admin_id(session)
    counted = record_count(
        session,
        uuid.uuid4(),
        counter,
        spools.store.id,
        [(spools.cable.id, Decimal(610))],
        as_of,
    )
    assert counted.moved == {}
