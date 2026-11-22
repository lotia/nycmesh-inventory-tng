"""What came to a count form's places from the areas gone from it
(forms.Gone), and what its save does with it (Ali, 2026-11-22,
inventory-tng-ygk5). Each place's own lines count what it held before
those moves, so the moves are set aside from what the records say there
(set_aside), and each move is taken as right unless its amount is fixed
("How many arrived?", in each form it arrived in): the difference is then
lost or found where it went, in that form, and nothing is written against
the area it left (fixed). The moves
are those after the form was first shown, those to a place on it up to
its moment, so the save reads the same ones the form showed. The routes are
stock/counts_routes.py's; the writing is stock/store_counts.py's."""

from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy.orm import Session

from inventory_tng.stock.forms import Gone
from inventory_tng.stock.ledger import Form
from inventory_tng.stock.moved_out import MovedOut, moved_out

# What a place's records hold that came from the areas gone from its
# count form, by item and form there.
Aside = dict[tuple[int, Form], Decimal]


def gone_moves(
    session: Session, gone: Gone, as_of: int, places: Collection[int]
) -> list[MovedOut]:
    """What left the areas gone from a count form after it was first
    shown (moved_out): to the `places` it shows, up to its moment
    `as_of`."""
    if not gone.ids:
        return []
    return moved_out(session, gone.ids, gone.since, (as_of, places))


def set_aside(moves: Sequence[MovedOut]) -> dict[int, Aside]:
    """What each place it went to holds of the moves, by place, item and
    the form it arrived in."""
    aside: dict[int, Aside] = {}
    for move in moves:
        if move.to_id is None:
            continue
        here = aside.setdefault(move.to_id, {})
        line = (move.item_id, move.arrives)
        here[line] = here.get(line, Decimal(0)) + move.quantity
    return aside


class Fix(NamedTuple):
    """A move's amount fixed: from an area gone, to a place on the form,
    of an item, more (found) or less (lost) than was moved, in the form
    it is written in there."""

    from_id: int
    to_id: int
    item_id: int
    difference: Decimal
    form: Form


def fixed(
    moves: Sequence[MovedOut],
    arrived: Mapping[int, Mapping[tuple[int, int, Form], Decimal]],
) -> list[Fix]:
    """The moves whose amount `arrived` fixes, by place it went to, then
    area it left, item and the form it arrived in: each one not as moved,
    its difference in that form, so a sealed one's in whole packages
    (forms.arrivals). A box for no such move is left alone."""
    moved: dict[tuple[int, int, int, Form], Decimal] = {}
    for move in moves:
        if move.to_id is not None:
            key = (move.to_id, move.from_id, move.item_id, move.arrives)
            moved[key] = moved.get(key, Decimal(0)) + move.quantity
    fixes = [
        Fix(from_id, to_id, item_id, amount - moved[key], form)
        for to_id, typed in arrived.items()
        for (from_id, item_id, form), amount in typed.items()
        if (key := (to_id, from_id, item_id, form)) in moved and amount != moved[key]
    ]
    return sorted(fixes, key=lambda fix: (fix.to_id, fix.from_id, fix.item_id))


def arrived_at(
    moves: Sequence[MovedOut], fixes: Sequence[Fix]
) -> dict[tuple[int, int], Decimal]:
    """How many arrived of each item at each place it went to, by place
    and item: what was moved, as fixed."""
    arrived: dict[tuple[int, int], Decimal] = {}
    for place_id, here in set_aside(moves).items():
        for (item_id, _), n in here.items():
            key = (place_id, item_id)
            arrived[key] = arrived.get(key, Decimal(0)) + n
    for fix in fixes:
        key = (fix.to_id, fix.item_id)
        arrived[key] = arrived.get(key, Decimal(0)) + fix.difference
    return arrived


def fixed_lines(fixes: Sequence[Fix]) -> dict[int, list[tuple[int, Form, Decimal]]]:
    """The fixes as lines to write at each place, by place: one line of
    an item in a form, the differences fixed there summed (a line names
    each once, ledger.each_once); none where they cancel out."""
    summed: dict[int, dict[tuple[int, Form], Decimal]] = {}
    for fix in fixes:
        here = summed.setdefault(fix.to_id, {})
        line = (fix.item_id, fix.form)
        here[line] = here.get(line, Decimal(0)) + fix.difference
    return {
        place_id: [(item_id, form, n) for (item_id, form), n in here.items() if n]
        for place_id, here in summed.items()
        if any(here.values())
    }
