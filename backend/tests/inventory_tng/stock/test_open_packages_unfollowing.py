"""Turning following off closes the open packages no longer followed
(stock/open_packages.py, unfollowing): what was left on each becomes loose
units at its place, by a loosening, and the row is marked closed, not
deleted (Ali, 2026-11-23, inventory-tng-j89e)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.catalogue import items
from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.models.stock import LedgerTransaction, Movement, OpenPackage
from inventory_tng.stock import open_packages
from inventory_tng.stock.ledger import LOOSE, Entry, Form, insert_transaction

from helpers.rows import add, breaks, history, violates
from helpers.sign_in import new_admin
from helpers.stock import Spools, held_by_form

NOW = datetime(2026, 11, 24, 9, tzinfo=UTC)


def loosenings(session: Session) -> list[LedgerTransaction]:
    return list(
        session.scalars(
            select(LedgerTransaction)
            .where(LedgerTransaction.kind == "loosen")
            .order_by(LedgerTransaction.id)
        )
    )


def test_open_packages_at_two_places_become_loose_there(
    session: Session, spools: Spools
) -> None:
    """A spool cut to 250 m in the store, another moved whole to the van:
    each place holds what it held, now loose, and each row is closed by
    the administrator, in its history."""
    admin, _ = new_admin(session)
    cable = spools.cable.id
    in_store, in_van = spools.opened(), spools.opened()
    spools.move(Entry(cable, Decimal(55), Form(open_package_id=in_store.id)))
    whole = Form(open_package_id=in_van.id)
    spools.move(Entry(cable, Decimal(305), whole, whole))
    store_before = sum(held_by_form(session, spools.store.id, cable).values())
    van_before = sum(held_by_form(session, spools.van.id, cable).values())

    assert spools.unfollow(admin.id, NOW) == 2

    assert held_by_form(session, spools.store.id, cable) == {
        (cable, LOOSE): Decimal(250)
    }
    assert held_by_form(session, spools.van.id, cable) == {(cable, LOOSE): Decimal(360)}
    assert store_before == Decimal(250)
    assert van_before == Decimal(360)
    session.flush()
    for row in (in_store, in_van):
        assert (row.closed_at, row.closed_by_admin_id) == (NOW, admin.id)
        closing = history(session, "open_package", row.id)[-1]
        assert (closing.new_row or {})["closed_by_admin_id"] == admin.id
    written = loosenings(session)
    assert [(t.from_location_id, t.to_location_id, t.admin_id) for t in written] == [
        (spools.store.id, spools.store.id, admin.id),
        (spools.van.id, spools.van.id, admin.id),
    ]
    # Nothing is left open to offer for cuts, and saving again closes none.
    assert open_packages.shown(session, OpenPackage.item_id == cable) == []
    assert spools.unfollow(admin.id, NOW) == 0


def test_one_left_below_zero_takes_as_much_from_loose(
    session: Session, spools: Spools
) -> None:
    admin, _ = new_admin(session)
    cable = spools.cable.id
    row = spools.opened()
    spools.move(Entry(cable, Decimal(310), Form(open_package_id=row.id)))
    assert spools.unfollow(admin.id, NOW) == 1
    # The other spool is still sealed.
    assert held_by_form(session, spools.store.id, cable) == {
        (cable, LOOSE): Decimal(-5),
        (cable, spools.sealed): Decimal(305),
    }
    (line,) = session.scalars(
        select(Movement).where(Movement.transaction_id == loosenings(session)[0].id)
    )
    assert (line.from_open_package_id, line.to_open_package_id) == (None, row.id)


def test_only_packages_no_longer_followed_are_closed(
    session: Session, spools: Spools
) -> None:
    """A package that says Track keeps its own followed when its item is
    turned off; a package set to Don't track closes only its own."""
    admin, _ = new_admin(session)
    reel = add(
        session,
        ItemPackage(
            item_id=spools.cable.id, name="Reel", size=Decimal(100), track_opened=True
        ),
    )
    insert_transaction(
        session,
        uuid.uuid4(),
        {"kind": "receipt", "to_location_id": spools.store.id},
        [Entry(spools.cable.id, Decimal(100), LOOSE, Form(package_id=reel.id))],
    )
    reel_open = open_packages.make(session, uuid.uuid4(), reel.id, spools.store.id)
    spool_open = spools.opened()
    assert spools.unfollow(admin.id, NOW) == 1
    assert reel_open.closed_at is None
    assert spool_open.closed_at == NOW

    _, closed = open_packages.unfollowing(
        session,
        admin.id,
        NOW,
        lambda: items.track_package(session, reel, False),
        package_ids=[reel.id],
    )
    assert closed == 1
    assert reel_open.closed_at == NOW


def test_turning_following_on_closes_none(session: Session, spools: Spools) -> None:
    admin, _ = new_admin(session)
    row = spools.opened()
    _, closed = open_packages.unfollowing(
        session,
        admin.id,
        NOW,
        lambda: items.track_items(session, [spools.cable.id], True),
        item_ids=[spools.cable.id],
    )
    assert closed == 0
    assert row.closed_at is None


def test_one_at_a_place_not_held_is_refused_as_changed(
    session: Session, spools: Spools
) -> None:
    """Moved whole to a place the change did not lock first: it could only
    lock it now out of the ledger's order, so it refuses."""
    admin, _ = new_admin(session)
    row = spools.opened()
    items.track_package(session, spools.spool, False)
    with breaks(session, "changed_since_shown"):
        open_packages.close_unfollowed(
            session, set(), admin.id, NOW, package_ids=[spools.spool.id]
        )
    assert row.closed_at is None


@pytest.mark.parametrize(
    ("leaves", "arrives", "amount", "rule"),
    [
        ("open", "loose", 250, "movement_open_package_whole"),
        ("loose", "open", 305, "movement_open_package_whole"),
        ("open", "open", 305, "movement_form_change"),
        ("loose", "loose", 305, "movement_form_change"),
        ("sealed", "loose", 305, "movement_form_change"),
        ("missing", "loose", 305, "movement_open_package_of_item"),
    ],
)
def test_a_loosening_takes_all_that_is_left_into_loose_units(
    session: Session, spools: Spools, leaves: str, arrives: str, amount: int, rule: str
) -> None:
    admin, _ = new_admin(session)
    row = spools.opened()
    forms = {
        "open": Form(open_package_id=row.id),
        "loose": LOOSE,
        "sealed": spools.sealed,
        "missing": Form(open_package_id=row.id + 1),
    }
    header = {
        "kind": "loosen",
        "from_location_id": spools.store.id,
        "to_location_id": spools.store.id,
        "admin_id": admin.id,
    }
    line = Entry(spools.cable.id, Decimal(amount), forms[leaves], forms[arrives])
    with breaks(session, rule):
        insert_transaction(session, uuid.uuid4(), header, [line])


def test_a_loosening_is_an_administrators_at_one_place(
    session: Session, spools: Spools
) -> None:
    row = spools.opened()
    line = Entry(spools.cable.id, Decimal(305), Form(open_package_id=row.id), LOOSE)
    header = {
        "kind": "loosen",
        "from_location_id": spools.store.id,
        "to_location_id": spools.store.id,
    }
    with violates(session, "ck_ledger_transaction_admin_counts"):
        insert_transaction(session, uuid.uuid4(), header, [line])
