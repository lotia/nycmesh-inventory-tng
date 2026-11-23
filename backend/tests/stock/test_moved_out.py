"""Where what left a place after a moment went (stock/moved_out.py)."""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.models.stock import AdjustmentReason
from inventory_tng.stock.counts import record_adjustment
from inventory_tng.stock.ledger import Entry
from inventory_tng.stock.moments import moment
from inventory_tng.stock.moved_out import MovedOut, moved_out

from helpers.sign_in import new_admin_id
from helpers.stock import Spools, stocked, write


def test_what_left_a_place_is_said_by_where_it_went_and_item_in_all(
    session: Session,
) -> None:
    """Two moves to the van, summed; one written off, which went nowhere;
    and nothing from before the moment."""
    place = stocked(session, "9")
    store, van, item = place.store.id, place.van.id, place.item.id
    write(session, place)
    after = moment(session, store).as_of
    for _ in range(2):
        write(session, place, lines=[{"item_id": item, "quantity": "1"}])
    reason = session.scalars(
        select(AdjustmentReason.id).where(AdjustmentReason.name == "Damaged")
    ).one()
    admin = new_admin_id(session)
    record_adjustment(
        session,
        uuid.uuid4(),
        admin,
        store,
        item,
        Decimal(1),
        found=False,
        reason_id=reason,
    )
    assert moved_out(session, [store], after) == [
        MovedOut(store, van, item, Decimal(2)),
        MovedOut(store, None, item, Decimal(1)),
    ]


def test_an_opening_is_no_move(session: Session) -> None:
    spools = Spools(session)
    after = moment(session, spools.store.id).as_of
    spools.opened()
    assert moved_out(session, [spools.store.id], after) == []


def test_what_left_is_said_by_the_form_it_arrived_in(session: Session) -> None:
    spools = Spools(session)
    store, van = spools.store.id, spools.van.id
    after = moment(session, store).as_of
    sealed = spools.sealed
    spools.move(Entry(spools.cable.id, Decimal(305), sealed, sealed))
    spools.move(Entry(spools.cable.id, Decimal(5)))
    assert moved_out(session, [store], after) == [
        MovedOut(store, van, spools.cable.id, Decimal(5)),
        MovedOut(store, van, spools.cable.id, Decimal(305), sealed),
    ]


def test_what_went_to_some_places_is_read_up_to_a_moment(session: Session) -> None:
    """Up to the moment at the van: the move after it to the van is left
    out, not the write-off, which went nowhere."""
    place = stocked(session, "9")
    store, van, item = place.store.id, place.van.id, place.item.id
    after = moment(session, store).as_of
    write(session, place, lines=[{"item_id": item, "quantity": "1"}])
    up_to = moment(session, store).as_of
    write(session, place, lines=[{"item_id": item, "quantity": "2"}])
    write(
        session,
        place,
        kind="install",
        to_location_id=None,
        install_number=1234,
        lines=[{"item_id": item, "quantity": "3"}],
    )
    assert moved_out(session, [store], after, up_to=up_to, into=[van]) == [
        MovedOut(store, van, item, Decimal(1)),
        MovedOut(store, None, item, Decimal(3)),
    ]


def test_what_left_is_split_at_a_moment_when_asked(session: Session) -> None:
    """One read says what left up to the moment and what left after it,
    each summed."""
    place = stocked(session, "9")
    store, van, item = place.store.id, place.van.id, place.item.id
    after = moment(session, store).as_of
    write(session, place, lines=[{"item_id": item, "quantity": "1"}])
    split = moment(session, store).as_of
    for _ in range(2):
        write(session, place, lines=[{"item_id": item, "quantity": "2"}])
    moves = moved_out(session, [store], after, split=split)
    assert moves == [
        MovedOut(store, van, item, Decimal(1)),
        MovedOut(store, van, item, Decimal(4), later=True),
    ]
