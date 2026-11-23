"""How a count form lays out what it shows (stock/count_routes.py): a
group of lines for each place on it, named by its path, and each line's
forms of an item as rows to count, each with its words and field. What
the records ask it for is stock/count_asked_for.py's."""

from collections.abc import Mapping
from decimal import Decimal
from typing import NamedTuple

from inventory_tng.core.phrases import Words, gettext, lazy_gettext
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.stock import OpenPackage
from inventory_tng.stock import queries
from inventory_tng.stock.count_forms import LOOSE_FIELD, OPENED, SEALED, Figures
from inventory_tng.stock.ledger import Form


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

    @property
    def sealed(self) -> bool:
        """Whether it asks how many sealed packages, a whole number."""
        return self.field.startswith(SEALED)


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
        return queries.place_label(self.place, self.path)
