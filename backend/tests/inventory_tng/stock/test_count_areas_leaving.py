"""The areas gone from a count form as they are now, and what left them
(stock/count_areas_leaving.py)."""

from decimal import Decimal

from sqlalchemy.orm import Session

from inventory_tng.stock.count_areas_leaving import as_shown
from inventory_tng.stock.moments import moment
from inventory_tng.stock.moved_out import MovedOut, moved_out

from helpers.stock import stocked, write


def test_a_form_shows_what_went_to_its_places_up_to_the_split_and_the_rest_whenever(
    session: Session,
) -> None:
    """Of a read split at a moment, a form of the van shows what went there
    up to it, not the move after it, and what went nowhere (installed)
    whenever; a form of no place, all of it."""
    place = stocked(session, "9")
    store, van, item = place.store.id, place.van.id, place.item.id
    after = moment(session, store).as_of
    write(
        session,
        place,
        lines=[{"item_id": item, "quantity": "1", "unit": place.item.unit}],
    )
    split = moment(session, store).as_of
    write(
        session,
        place,
        lines=[{"item_id": item, "quantity": "2", "unit": place.item.unit}],
    )
    write(
        session,
        place,
        kind="install",
        to_location_id=None,
        install_number=1234,
        lines=[{"item_id": item, "quantity": "3", "unit": place.item.unit}],
    )
    moves = moved_out(session, [store], after, split=split)
    assert as_shown(moves, [van]) == [
        MovedOut(store, van, item, Decimal(1)),
        MovedOut(store, None, item, Decimal(3), later=True),
    ]
    assert as_shown(moves, []) == moves
