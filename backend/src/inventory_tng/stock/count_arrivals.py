"""What came to a count form's places from the areas gone from it
(count_forms.Gone), and what its save does with it (Ali, 2026-11-22,
inventory-tng-ygk5). Each place's own lines count what it held before
those moves, so the moves are set aside from what the records say there
(set_aside), in the forms it holds them in (as_held), and each move is
taken as right unless its amount is fixed ("How many arrived?", in each
form it arrived in): the difference is then lost or found where it went,
in the forms it is held in (fixed_lines), and nothing is written against
the area it left (fixed). The moves are those after the form was first
shown, those to a place on it up to its moment, so the save reads the
same ones the form showed (count_areas_leaving.as_shown). The routes are
stock/count_routes.py's; the writing is stock/store_counts.py's."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy.orm import Session

from inventory_tng.stock import queries
from inventory_tng.stock.count_forms import Gone
from inventory_tng.stock.ledger import Form, by_item
from inventory_tng.stock.moments import expected_forms_at
from inventory_tng.stock.moved_out import MovedOut

# What a place's records hold that came from the areas gone from its
# count form, by item and form there.
Aside = dict[tuple[int, Form], Decimal]

# A line to write at a place: so much of an item in a form there.
FormLine = tuple[int, Form, Decimal]


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


class Held(NamedTuple):
    """Part of what arrived at a place, as the records hold it there at the
    form's moment: the form it is in there, how much of what arrived it
    stands for, and how much of that is still there in that form."""

    form: Form
    arrived: Decimal
    there: Decimal


# What arrived at a place, by item and the form it arrived in: the parts
# the records hold it in there (as_held).
Arrivals = dict[tuple[int, Form], list[Held]]


class Opening(NamedTuple):
    """A sealed package of an item opened at a place: into the form it
    made there, so much (queries.openings_at)."""

    item_id: int
    package_id: int | None
    form: Form
    quantity: Decimal


def as_held(
    session: Session, gone: Gone, as_of: int, moves: Sequence[MovedOut]
) -> dict[int, Arrivals]:
    """What `moves` brought from the areas `gone` (set_aside), by place, in
    the forms each place held it in at moment `as_of`: what its lines
    are less, by item and by form, on the page and in the save alike
    (held_aside), and the forms a fix of it is written in (fixed_lines)
    (inventory-tng-nlog). Never more of a form than the place held of it
    then: beyond that, it was used or has left, as the records say, so
    nothing is set aside for it. What arrived sealed beyond what the
    place held sealed was opened there since, or has left: it is set
    aside from what each sealed package of it opened there since made, an
    open package or loose units, the newest first, a package's worth
    each."""
    aside = set_aside(moves)
    items = {item_id for here in aside.values() for item_id, _ in here}
    held = expected_forms_at(session, aside.keys(), as_of, items)
    sealed = {
        item_id
        for here in aside.values()
        for item_id, form in here
        if form.package_id is not None
    }
    openings: dict[int | None, list[Opening]] = {}
    if sealed:
        rows = queries.openings_at(session, aside.keys(), sealed, gone.since, as_of)
        for place_id, item_id, package_id, open_id, quantity in rows:
            openings.setdefault(place_id, []).append(
                Opening(item_id, package_id, Form(open_package_id=open_id), quantity)
            )
    return {
        place_id: as_held_at(here, held.get(place_id, {}), openings.get(place_id, []))
        for place_id, here in aside.items()
    }


def as_held_at(
    aside: Aside,
    held: Mapping[tuple[int, Form], Decimal],
    openings: Sequence[Opening],
) -> Arrivals:
    """as_held at one place, from what it read: `held` there at the form's
    moment, and `openings` there since, the newest first. What arrived
    loose or in an open package first, then what arrived sealed."""
    left = {line: max(n, Decimal(0)) for line, n in held.items()}

    def part(item_id: int, form: Form, arrived: Decimal) -> Held:
        there = min(arrived, left.get((item_id, form), Decimal(0)))
        left[(item_id, form)] = left.get((item_id, form), Decimal(0)) - there
        return Held(form, arrived, there)

    kept: Arrivals = {}
    for (item_id, form), n in sorted(
        aside.items(), key=lambda line: line[0][1].package_id is not None
    ):
        first = part(item_id, form, n)
        if form.package_id is None:
            kept[(item_id, form)] = [first]
            continue
        # Sealed: what the place still held sealed stands for as much.
        parts = [Held(form, first.there, first.there)] if first.there else []
        rest = n - first.there
        for opening in openings:
            if not rest:
                break
            if (opening.item_id, opening.package_id) == (item_id, form.package_id):
                parts.append(part(item_id, opening.form, min(rest, opening.quantity)))
                rest -= parts[-1].arrived
        if rest:
            parts.append(Held(form, rest, Decimal(0)))
        kept[(item_id, form)] = parts
    return kept


def opened_since(arrivals: Arrivals) -> dict[int, list[Decimal]]:
    """Of what arrived sealed at a place (as_held there), each package of
    it opened there since, by item, the newest first: how much of it the
    place holds in the form opening it made, at the form's moment
    (inventory-tng-0fbn)."""
    left: dict[int, list[Decimal]] = {}
    for (item_id, form), parts in arrivals.items():
        for each in parts:
            if form.package_id is not None and each.form != form:
                left.setdefault(item_id, []).append(each.there)
    return left


def held_aside(arrivals: Mapping[int, Arrivals]) -> dict[int, Aside]:
    """What each place's lines are less, by place, item and form: what the
    records hold there of what arrived (as_held)."""
    aside: dict[int, Aside] = {}
    for place_id, here in arrivals.items():
        lines = aside.setdefault(place_id, {})
        for (item_id, _), parts in here.items():
            for each in parts:
                if each.there:
                    line = (item_id, each.form)
                    lines[line] = lines.get(line, Decimal(0)) + each.there
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
    aside: Mapping[int, Aside], lines: Mapping[int, Sequence[FormLine]]
) -> dict[tuple[int, int], Decimal]:
    """How much of what arrived each place holds once saved, by place and
    item: what is set aside there (held_aside), and the lines its fixes
    write there (fixed_lines)."""
    arrived = {
        (place_id, item_id): n
        for place_id, here in aside.items()
        for item_id, n in by_item(here).items()
    }
    for place_id, each in lines.items():
        for item_id, _, n in each:
            key = (place_id, item_id)
            arrived[key] = arrived.get(key, Decimal(0)) + n
    return arrived


def fixed_lines(
    fixes: Sequence[Fix], arrivals: Mapping[int, Arrivals]
) -> dict[int, list[FormLine]]:
    """The fixes as lines to write at each place, by place: one line of
    an item in a form, the differences fixed there summed (a line names
    each once, ledger.each_once); none where they cancel out. More is
    found in the form it arrived in. Less is lost from the parts it is
    held in there (as_held), each in turn for as much as it stands for,
    but never more than is still there of it: a spool opened there since
    is lost from what opening it made, and what was used or has gone from
    there writes nothing, so no form goes below nothing."""
    left = {
        (place_id, *line): list(parts)
        for place_id, here in arrivals.items()
        for line, parts in here.items()
    }
    summed: dict[int, dict[tuple[int, Form], Decimal]] = {}
    for fix in fixes:
        here = summed.setdefault(fix.to_id, {})
        written = [(fix.form, fix.difference)]
        parts = left.get((fix.to_id, fix.item_id, fix.form))
        if fix.difference < 0 and parts is not None:
            rest, written = -fix.difference, []
            for index, each in enumerate(parts):
                if not rest:
                    break
                arrived = min(rest, each.arrived)
                there = min(arrived, each.there)
                parts[index] = each._replace(
                    arrived=each.arrived - arrived, there=each.there - there
                )
                rest -= arrived
                written.append((each.form, -there))
        for form, n in written:
            line = (fix.item_id, form)
            here[line] = here.get(line, Decimal(0)) + n
    lines = {
        place_id: [(item_id, form, n) for (item_id, form), n in here.items() if n]
        for place_id, here in summed.items()
    }
    return {place_id: each for place_id, each in lines.items() if each}
