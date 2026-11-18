"""What the tests of items share: those of the administrators' item
pages (tests/test_admin_items_*.py) and of reading rules."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import Category, Item

from helpers.markup import Element, row_links
from helpers.rows import add

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def new_item(session: Session, category: Category, name: str = "LiteBeam") -> Item:
    return add(session, Item(name=name, category_id=category.id))


def items_named(page: str | Element) -> list[str]:
    """The items the list shows, in its order."""
    return row_links(page, "/admin/items")
