"""How a count form lays out what it shows (stock/counts_routes.py): a
group of lines for each place on it, named by its path, and each line's
forms of an item as rows to count."""

from collections.abc import Mapping
from decimal import Decimal
from typing import NamedTuple

from inventory_tng.core.phrases import Words
from inventory_tng.models.catalogue import Item
from inventory_tng.models.places import Location

# Between the names of an area's path on a count form.
PATH_BETWEEN = " \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} "


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


class Group(NamedTuple):
    """One place on a count form, its lines under it: the page's own place
    or an area in it (inventory-tng-8i4.51.4). Its path below the page's
    place (none for that place), the prefix of its fields (forms.AT), its
    rows of an item and the records' amount, those counted by form, the
    recount's items it asks for again, and each one's figures."""

    place: Location
    path: tuple[str, ...]
    prefix: str
    rows: list[tuple[Item, Decimal]]
    forms: dict[int, list[FormRow]]
    items: list[int]
    recount: Mapping[int, tuple[Decimal, Decimal, Decimal]] | None

    @property
    def label(self) -> str:
        return label(self.place, self.path)


def label(place: Location, path: tuple[str, ...]) -> str:
    """How a count form names a place it shows: its path, or for the page's
    own place its name."""
    return PATH_BETWEEN.join(path) if path else place.name
