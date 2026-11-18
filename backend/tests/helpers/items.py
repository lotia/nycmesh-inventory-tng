"""What the tests of items share: those of the administrators' item
pages (tests/catalogue/test_items_routes_*.py) and of reading rules."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import Category, Item, ItemPackage

from helpers.markup import Element, row_links
from helpers.rows import add

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def new_item(session: Session, category: Category, name: str = "LiteBeam") -> Item:
    return add(session, Item(name=name, category_id=category.id))


def same_size_twins(
    session: Session, item: Item, size: Decimal = Decimal(10)
) -> tuple[ItemPackage, ItemPackage]:
    """Two of the item's packages of one size, by id. The first is renamed
    after the second is added, to a name after the second's, so
    PostgreSQL reads it last on disk and by every index: a list with no
    tie order shows them the wrong way round. A rename changes indexed
    columns, so the row is rewritten in every index too (no HOT update),
    which a change to another column would not do."""
    first = add(session, ItemPackage(item_id=item.id, name="Twin", size=size))
    second = add(session, ItemPackage(item_id=item.id, name="Twin A", size=size))
    first.name = "Twin B"
    session.flush()
    return first, second


def items_named(page: str | Element) -> list[str]:
    """The items the list shows, in its order."""
    return row_links(page, "/admin/items")
