"""How a count form lays out what it shows (stock/count_routes.py): a
group of lines for each place on it, named by its path, and each line's
forms of an item as rows to count, each with its words and field, as the
records say as of the form's moment; and which items a full count of a
place asks for."""

from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy.orm import Session

from inventory_tng.core.phrases import PATH_BETWEEN, Words, gettext, lazy_gettext
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.stock import OpenPackage
from inventory_tng.stock import queries
from inventory_tng.stock.count_arrivals import Aside, less
from inventory_tng.stock.count_forms import (
    COUNTED,
    LOOSE_FIELD,
    OPENED,
    SEALED,
    Figures,
    Listed,
)
from inventory_tng.stock.ledger import LOOSE, Form
from inventory_tng.stock.moments import expected_at, expected_forms_at
from inventory_tng.stock.over_recorded import openings_waiting_at


class FormRow(NamedTuple):
    """One form of an item at a place, as the count form asks for it
    (docs/decisions/0020): its field, words, and the records as of the
    form; for an open package, its id; and how many of the item's unit
    one of what is typed is (a sealed package's size)."""

    field: str
    words: Words
    amount: Decimal
    open_id: int | None = None
    per: Decimal = Decimal(1)


# How the count form asks for each form of an item.
LOOSE_WORDS = lazy_gettext("Loose")


def sealed_how_many(package: str) -> str:
    return gettext("%(package)s, sealed: how many", package=package)


def opened_left(package: str, day: str) -> str:
    return gettext("%(package)s, opened %(day)s: left", package=package, day=day)


def form_order(
    form: Form, packages: Mapping[int, ItemPackage]
) -> tuple[int | Decimal, ...]:
    """Where a form of an item is in the count form's rows: sealed, the
    smallest package first, ties by package id, as the item and scan pages
    list packages; then open ones, oldest first; then loose."""
    if form.package_id is not None:
        return 0, packages[form.package_id].size, form.package_id
    if form.open_package_id is not None:
        return 1, form.open_package_id
    return (2,)


def form_row(
    item_id: int,
    form: Form,
    amount: Decimal,
    packages: Mapping[int, ItemPackage],
    opened: Mapping[int, OpenPackage],
) -> FormRow:
    """The row of `amount` of an item in a form, in units, as the count
    form asks for it: sealed packages by how many, an open package by what
    is left on it, loose units. `packages` holds the package of each form
    and open package, and `opened` each open package, by id."""
    if form.package_id is not None:
        package = packages[form.package_id]
        return FormRow(
            f"{SEALED}{item_id}_{package.id}",
            sealed_how_many(package.name_shown),
            amount / package.size,
            per=Decimal(package.size),
        )
    if form.open_package_id is not None:
        row = opened[form.open_package_id]
        return FormRow(
            f"{OPENED}{row.id}",
            opened_left(
                packages[row.package_id].name_shown, f"{row.opened_at:%Y-%m-%d}"
            ),
            amount,
            row.id,
        )
    return FormRow(f"{LOOSE_FIELD}{item_id}", LOOSE_WORDS, amount)


class Group(NamedTuple):
    """One place on a count form, its lines under it: the page's own place
    or an area in it (inventory-tng-8i4.51.4). Its path below the page's
    place (none for that place), the prefix of its fields (count_forms.AT), its
    rows of an item and the records' amount, those counted by form, the
    recount's items it asks for again, and each one's figures."""

    place: Location
    path: tuple[str, ...]
    prefix: str
    rows: list[tuple[Item, Decimal]]
    forms: dict[int, list[FormRow]]
    items: list[int]
    recount: Mapping[int, Figures] | None

    @property
    def label(self) -> str:
        return label(self.place, self.path)


def label(place: Location, path: tuple[str, ...]) -> str:
    """How a count form names a place it shows: its path, or for the page's
    own place its name."""
    return PATH_BETWEEN.join(path) if path else place.name


def forms_by_place(
    session: Session,
    as_of: int,
    item_ids: Mapping[int, Sequence[int]],
    openings: Mapping[int, Mapping[int, Collection[int]]],
    aside: Mapping[int, Aside] | None = None,
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
    what is `aside` there (count_arrivals.set_aside). `packages`, by id,
    every package of those items, if read already. The same number of
    queries however many places."""
    every = sorted(set().union(*item_ids.values())) if item_ids else []
    found = expected_forms_at(session, item_ids.keys(), as_of, every)
    if packages is None:
        packages = packages_by_id(session, every)
    opened = {row.id: row for row in queries.open_at(session, item_ids.keys(), every)}
    return {
        place_id: _place_forms(
            set(items),
            less(found.get(place_id, {}), (aside or {}).get(place_id, {})),
            packages,
            {
                open_id: row
                for open_id, row in opened.items()
                if row.location_id == place_id and row.item_id in items
            },
            openings.get(place_id, {}),
        )
        for place_id, items in item_ids.items()
    }


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


def packages_by_id(
    session: Session, item_ids: Collection[int]
) -> dict[int, ItemPackage]:
    """Every package of the items of `item_ids`, by id."""
    return {
        package.id: package for package in queries.packages_of_items(session, item_ids)
    }


def line_of(session: Session, as_of: int, listed: Listed) -> str:
    """The field a count form, as of moment `as_of`, shows an item's line
    at a place by: its total, or, counted by form (forms_by_place), its
    loose units."""
    place_id, item_id = listed.place_id, listed.item_id
    openings = openings_waiting_at(session, [place_id], as_of)
    by_form = forms_by_place(session, as_of, {place_id: [item_id]}, openings)[place_id]
    return f"{LOOSE_FIELD if item_id in by_form else COUNTED}{item_id}"


def asks_for(session: Session, as_of: int, place_id: int, item_id: int) -> bool:
    """Whether a full count of the place, as of moment `as_of`, asks for the
    item: the records hold some there, or an opening of it waits there
    (over_recorded.openings_waiting_at), asked for by form though they hold
    none."""
    return bool(expected_at(session, [place_id], as_of, [item_id])) or (
        item_id in openings_waiting_at(session, [place_id], as_of).get(place_id, {})
    )
