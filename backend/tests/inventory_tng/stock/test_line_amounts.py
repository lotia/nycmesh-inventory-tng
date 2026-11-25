"""Every sum of the ledger adds only lines in their item's unit, and is
refused one in another, so a conversion is never made unawares
(line_amounts; Ali, 2026-11-24, inventory-tng-2rlr)."""

import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import exc, true
from sqlalchemy.orm import Session

from inventory_tng.stock import moments, queries
from inventory_tng.stock.ledger import Entry, insert_transaction
from inventory_tng.stock.moved_out import moved_out

from helpers.stock import Place

# Each reader of the ledger's sums, of the place's store, after its cable
# came in and some left.
READERS: dict[str, Callable[[Session, Place], object]] = {
    "balance": lambda session, place: queries.item_total(session, place.cable.id),
    "as of a moment": lambda session, place: moments.expected_at(
        session, [place.store.id], queries.latest_transaction_id(session)
    ),
    "flows": lambda session, place: moments.flows(session, place.store.id, true()),
    "by form": lambda session, place: moments.form_flows(
        session, place.store.id, [true()], [place.cable.id]
    ),
    "moved out": lambda session, place: moved_out(session, [place.store.id], 0),
}


def _moved(session: Session, place: Place) -> None:
    """50 m of cable into the store, then 10 m on to the van."""
    for header, metres in (
        ({"kind": "receipt", "to_location_id": place.store.id}, 50),
        (
            {
                "kind": "move",
                "from_location_id": place.store.id,
                "to_location_id": place.van.id,
                "volunteer_id": place.volunteer.id,
            },
            10,
        ),
    ):
        line = Entry(place.cable.id, Decimal(metres), unit="m")
        insert_transaction(session, uuid.uuid4(), header, [line])


def test_lines_in_their_items_unit_are_summed(session: Session, place: Place) -> None:
    _moved(session, place)
    assert queries.item_total(session, place.cable.id) == Decimal(50)
    assert moments.expected(session, place.store.id) == {place.cable.id: Decimal(40)}


@pytest.mark.parametrize("read", READERS.values(), ids=READERS.keys())
def test_a_sum_across_two_units_is_refused(
    session: Session, place: Place, read: Callable[[Session, Place], object]
) -> None:
    """The cable switched to ft straight in the database, as a moved item's
    unit is fixed: its lines in m are never added up as ft."""
    _moved(session, place)
    place.cable.unit = "ft"
    session.flush()
    with pytest.raises(exc.DataError, match="ledger line in m summed in ft"):
        read(session, place)
