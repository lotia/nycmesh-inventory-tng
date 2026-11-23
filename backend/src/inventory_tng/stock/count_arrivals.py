"""What came to a count form's places from the areas gone from it
(count_forms.Gone), and what its save does with it (Ali, 2026-11-22,
inventory-tng-ygk5). Each place's own lines count what it held before
those moves, so the moves are set aside from what the records say there
(set_aside), in the forms it holds them in (as_held), and each move is taken as right unless its amount is fixed
("How many arrived?", in each form it arrived in): the difference is then
lost or found where it went, in that form, and nothing is written against
the area it left (fixed). The moves
are those after the form was first shown, those to a place on it up to
its moment, so the save reads the same ones the form showed. The routes are
stock/count_routes.py's; the writing is stock/store_counts.py's."""

from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy.orm import Session

from inventory_tng.stock import queries
from inventory_tng.stock.count_forms import Gone
from inventory_tng.stock.ledger import Form, by_item
from inventory_tng.stock.moments import expected_forms_at
from inventory_tng.stock.moved_out import Leaving, MovedOut, moved_out

# What a place's records hold that came from the areas gone from its
# count form, by item and form there.
Aside = dict[tuple[int, Form], Decimal]


def gone_moves(
    session: Session,
    gone: Gone,
    as_of: int,
    places: Collection[int],
    leaving: Leaving | None = None,
) -> list[MovedOut]:
    """What left the areas gone from a count form after it was first
    shown (moved_out): to the `places` it shows, up to its moment
    `as_of`. Of `leaving`'s moves, if they were split at that moment,
    rather than read again."""
    if not gone.ids:
        return []
    if leaving is not None and leaving.as_of == as_of:
        return leaving.moved_by(places)
    return moved_out(session, gone.ids, gone.since, up_to=as_of, into=places)


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


def as_held(
    session: Session, as_of: int, aside: Mapping[int, Aside]
) -> dict[int, Aside]:
    """`aside` (set_aside), by place, in the forms each place held it in at
    moment `as_of`, which its lines are less, by item and by form, on the
    page and in the save alike (inventory-tng-nlog). What arrived sealed
    beyond what the place held sealed then was opened there, or has left
    it: it is set aside from the packages of that package open there, an
    opened one standing for the sealed one it was, the newest first, up
    to what each held; beyond those, it has left the place, as the
    records say, so nothing is set aside for it. What arrived loose or in
    an open package is set aside as it came."""
    sealed = {
        place_id: {item_id for (item_id, form) in here if form.package_id is not None}
        for place_id, here in aside.items()
    }
    items = set().union(*sealed.values()) if sealed else set()
    if not items:
        return {place_id: dict(here) for place_id, here in aside.items()}
    held = expected_forms_at(session, aside.keys(), as_of, items)
    open_ids = {
        form.open_package_id
        for here in held.values()
        for _, form in here
        if form.open_package_id is not None
    }
    package_of = {
        row.id: row.package_id for row in queries.open_with_ids(session, open_ids)
    }
    return {
        place_id: as_held_at(here, held.get(place_id, {}), package_of)
        for place_id, here in aside.items()
    }


def as_held_at(
    aside: Aside,
    held: Mapping[tuple[int, Form], Decimal],
    package_of: Mapping[int, int],
) -> Aside:
    """as_held at one place, from what it read: `package_of` each open
    package's package, by id."""
    kept: Aside = {}

    def take(line: tuple[int, Form], n: Decimal) -> None:
        if n:
            kept[line] = kept.get(line, Decimal(0)) + n

    for line, n in aside.items():
        if line[1].package_id is None:
            take(line, n)
    for (item_id, form), n in aside.items():
        if form.package_id is None:
            continue
        sealed = min(n, max(held.get((item_id, form), Decimal(0)), Decimal(0)))
        take((item_id, form), sealed)
        rest = n - sealed
        opened = sorted(
            (
                line
                for line in held
                if line[0] == item_id
                and line[1].open_package_id is not None
                and package_of.get(line[1].open_package_id) == form.package_id
            ),
            key=lambda line: line[1].open_package_id or 0,
            reverse=True,
        )
        for line in opened:
            left = held[line] - kept.get(line, Decimal(0))
            part = min(rest, max(left, Decimal(0)))
            take(line, part)
            rest -= part
    return kept


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
    (count_forms.arrivals). A box for no such move is left alone."""
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


def less[Line](
    held: Mapping[Line, Decimal], aside: Mapping[Line, Decimal]
) -> dict[Line, Decimal]:
    """What a place's records hold of each line, by item or by item and
    form, less what is set aside of it: none where that leaves nothing."""
    left = {line: n - aside.get(line, Decimal(0)) for line, n in held.items()}
    return {line: n for line, n in left.items() if n}


def arrived_at(
    aside: Mapping[int, Aside], fixes: Sequence[Fix]
) -> dict[tuple[int, int], Decimal]:
    """How many arrived of each item at each place it went to, by place
    and item: what was moved (set_aside), as fixed."""
    arrived = {
        (place_id, item_id): n
        for place_id, here in aside.items()
        for item_id, n in by_item(here).items()
    }
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
    lines = {
        place_id: [(item_id, form, n) for (item_id, form), n in here.items() if n]
        for place_id, here in summed.items()
    }
    return {place_id: each for place_id, each in lines.items() if each}
