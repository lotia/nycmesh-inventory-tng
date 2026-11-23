"""The areas gone from a count form as they are now, and what left them
(stock/count_areas_leaving.py)."""

from decimal import Decimal

from sqlalchemy.orm import Session

from inventory_tng.stock.count_areas_leaving import Leaving
from inventory_tng.stock.moments import moment
from inventory_tng.stock.moved_out import MovedOut, moved_out

from helpers.stock import stocked, write


def test_what_went_to_some_places_up_to_the_split_is_kept_of_a_split_read(
    session: Session,
) -> None:
    """What Leaving.moved_by keeps of a read split at a moment is what a
    read up to that moment into the van finds."""
    place = stocked(session, "9")
    store, van, item = place.store.id, place.van.id, place.item.id
    after = moment(session, store).as_of
    write(session, place, lines=[{"item_id": item, "quantity": "1"}])
    split = moment(session, store).as_of
    for _ in range(2):
        write(session, place, lines=[{"item_id": item, "quantity": "2"}])
    moves = moved_out(session, [store], after, split=split)
    leaving = Leaving({}, moves, set(), {}, split)
    assert leaving.moved_by([van]) == [MovedOut(store, van, item, Decimal(1))]
    assert leaving.moved_by([]) == moves
    assert moved_out(session, [store], after, up_to=split, into=[van]) == [
        MovedOut(store, van, item, Decimal(1))
    ]
