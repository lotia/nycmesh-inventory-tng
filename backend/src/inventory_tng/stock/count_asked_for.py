"""What a count form asks for at each place, as the records say as of its
moment, less what came from the areas gone from it
(count_arrivals.held_aside): each item's forms, as rows to count
(count_groups.form_row), the field its line is shown by, and which items
a full count of a place asks for."""

from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal

from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.models.stock import OpenPackage
from inventory_tng.stock import queries
from inventory_tng.stock.count_arrivals import Aside, less
from inventory_tng.stock.count_forms import COUNTED, LOOSE_FIELD, Listed
from inventory_tng.stock.count_groups import FormRow, form_order, form_row
from inventory_tng.stock.ledger import LOOSE, Form, by_item
from inventory_tng.stock.moments import expected_at, expected_forms_at
from inventory_tng.stock.over_recorded import openings_waiting_at


def forms_by_place(
    session: Session,
    as_of: int,
    item_ids: Mapping[int, Sequence[int]],
    openings: Mapping[int, Mapping[int, Collection[int]]],
    aside: Mapping[int, Aside],
    packages: Mapping[int, ItemPackage] | None = None,
) -> dict[int, dict[int, list[FormRow]]]:
    """At each place, by id, the items of `item_ids` there whose stock was
    in more than loose units, as of the form, each form a row: sealed
    packages, by how many; open packages; loose. Others are counted by
    their total, as before, but an item of an opening waiting there,
    recorded more than opened (`openings`, by place,
    over_recorded.openings_waiting_at): it is counted by form, with a row of
    each package it opened sealed, though the records hold none, as a total
    puts neither of its ends right (docs/decisions/0020). Less, by place,
    what is `aside` there, in the forms held (count_arrivals.held_aside): an
    open package set aside whole is no row. `packages`, by id,
    every package of those items, if read already. The same number of
    queries however many places."""
    every = sorted(set().union(*item_ids.values())) if item_ids else []
    found = expected_forms_at(session, item_ids.keys(), as_of, every)
    if packages is None:
        packages = queries.packages_by_id(session, every)
    opened = {row.id: row for row in queries.open_at(session, item_ids.keys(), every)}
    forms: dict[int, dict[int, list[FormRow]]] = {}
    for place_id, items in item_ids.items():
        here = aside.get(place_id, {})
        left = less(found.get(place_id, {}), here)
        forms[place_id] = _place_forms(
            set(items),
            left,
            packages,
            {
                open_id: row
                for open_id, row in opened.items()
                if row.location_id == place_id
                and row.item_id in items
                and not _aside_whole(row, here, left)
            },
            openings.get(place_id, {}),
        )
    return forms


def _aside_whole(
    row: OpenPackage,
    aside: Mapping[tuple[int, Form], Decimal],
    left: Mapping[tuple[int, Form], Decimal],
) -> bool:
    """Whether the open package is set aside whole: what arrived, counted
    apart, so none of it is the place's own."""
    line = (row.item_id, Form(open_package_id=row.id))
    return line in aside and line not in left


def _place_forms(
    item_ids: Collection[int],
    found: Mapping[tuple[int, Form], Decimal],
    packages: Mapping[int, ItemPackage],
    opened: Mapping[int, OpenPackage],
    openings: Mapping[int, Collection[int]],
) -> dict[int, list[FormRow]]:
    """forms_by_place at one place, from what it read."""
    held: dict[int, dict[Form, Decimal]] = {}
    for (item_id, form), amount in found.items():
        # Of its items only; and one closed since is not counted: its row
        # is gone from the place.
        if item_id in item_ids and (
            form.open_package_id is None or form.open_package_id in opened
        ):
            held.setdefault(item_id, {})[form] = amount
    for row in opened.values():
        held.setdefault(row.item_id, {}).setdefault(
            Form(open_package_id=row.id), Decimal(0)
        )
    for item_id in item_ids:
        for package_id in openings.get(item_id, ()):
            held.setdefault(item_id, {}).setdefault(
                Form(package_id=package_id), Decimal(0)
            )

    rows: dict[int, list[FormRow]] = {}
    for item_id, forms in held.items():
        if set(forms) <= {LOOSE}:
            continue
        each = rows.setdefault(item_id, [])
        for form in sorted(forms, key=lambda form: form_order(form, packages)):
            each.append(form_row(item_id, form, forms[form], packages, opened))
        if not any(row.field.startswith(LOOSE_FIELD) for row in each):
            each.append(form_row(item_id, LOOSE, Decimal(0), packages, opened))
    return rows


def line_of(
    session: Session, as_of: int, aside: Mapping[int, Aside], listed: Listed
) -> str:
    """The field a count form, as of moment `as_of`, shows an item's line
    at a place by: its total, or, counted by form (forms_by_place), its
    loose units. Less, by place, what is `aside` there, as the form shows
    it (count_arrivals.held_aside)."""
    place_id, item_id = listed.place_id, listed.item_id
    openings = openings_waiting_at(session, [place_id], as_of)
    by_form = forms_by_place(session, as_of, {place_id: [item_id]}, openings, aside)
    return f"{LOOSE_FIELD if item_id in by_form[place_id] else COUNTED}{item_id}"


def asks_for(
    session: Session,
    as_of: int,
    aside: Mapping[int, Aside],
    place_id: int,
    item_id: int,
) -> bool:
    """Whether a full count of the place, as of moment `as_of`, asks for the
    item: the records hold some there, less what is `aside` there, by place
    (count_arrivals.held_aside), or an opening of it waits there
    (over_recorded.openings_waiting_at), asked for by form though they hold
    none."""
    held = expected_at(session, [place_id], as_of, [item_id]).get(place_id, {})
    return bool(less(held, by_item(aside.get(place_id, {})))) or (
        item_id in openings_waiting_at(session, [place_id], as_of).get(place_id, {})
    )
