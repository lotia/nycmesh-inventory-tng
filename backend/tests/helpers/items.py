"""What the tests of items on the administrators' pages share
(tests/test_admin_items_*.py)."""

import re
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from inventory_tng.models import Category, Item

from helpers.rows import add

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def new_item(session: Session, category: Category, name: str = "LiteBeam") -> Item:
    return add(session, Item(name=name, category_id=category.id))


def items_named(page: str) -> list[str]:
    """The items the list shows, in its order."""
    return re.findall(r'<td><a href="/admin/items/\d+">([^<]+)</a>', page)
