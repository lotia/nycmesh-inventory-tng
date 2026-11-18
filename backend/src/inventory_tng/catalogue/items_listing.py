"""How the items list sorts and finds an item (catalogue/items_routes.py),
which the stock and counts pages find items by too."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from inventory_tng.catalogue import queries
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models import Item
from inventory_tng.web.listing import Searched, Sort, Sorting


@dataclass(frozen=True)
class _Shown:
    """An item as its list's suggestions name it."""

    name: str


def _as_shown(_session: Session, rows: Sequence[Item]) -> list[_Shown]:
    """Each item by its name as shown (KeyedName.name_shown)."""
    return [_Shown(row.name_shown) for row in rows]


# How the items list sorts: by name first, as it always has, or by a
# heading (Ali, 2026-11-15, inventory-tng-qwb).
SORTING = Sorting(
    "name",
    {
        "name": Sort.of(func.lower(Item.name)),
        "category": Sort.of(func.lower(queries.category_name)),
        "unit": Sort.of(Item.unit),
        "part": Sort.of(func.lower(Item.manufacturer), func.lower(Item.part_number)),
    },
)
# How the items list finds an item: by anything a volunteer might type.
SEARCHED = Searched(
    ITEMS, Item, Item.search_text, Item.name, shown=_as_shown, sorting=SORTING
)
