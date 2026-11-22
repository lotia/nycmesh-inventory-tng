"""Where what left a place after a moment went (stock/moved_out.py)."""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.models.stock import AdjustmentReason
from inventory_tng.stock.counts import record_adjustment
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
