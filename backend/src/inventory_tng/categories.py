"""Categories: every write to the category table, and its rule. A category
with current items stays current: they would be left in one nobody can
pick (items.add, save and restore are the other side)."""

from datetime import datetime

from sqlalchemy.orm import Session

from inventory_tng.db import enforce, exists_where, lock_catalogue, relock
from inventory_tng.models import Category, Item


def add(session: Session, name: str) -> Category:
    lock_catalogue(session)
    category = Category(name=name)
    session.add(category)
    session.flush()
    return category


def rename(session: Session, category: Category, name: str) -> None:
    relock(session, category)
    category.name = name


def retire(session: Session, category: Category, at: datetime) -> None:
    relock(session, category, current=True)
    has_items = exists_where(
        session, Item.category_id == category.id, Item.retired_at.is_(None)
    )
    enforce("category_has_items", when=has_items)
    category.retired_at = at


def restore(session: Session, category: Category) -> None:
    relock(session, category, current=False)
    category.retired_at = None
