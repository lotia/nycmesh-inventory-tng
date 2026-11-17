"""What the catalogue's pages read (admin/categories.py): plain functions
taking a Session, each named for what it returns (docs/decisions/0023)."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.models import Category, Item


def current_items_by_category(session: Session) -> dict[int, int]:
    """How many current items each category has."""
    rows = session.execute(
        select(Item.category_id, func.count())
        .where(Item.retired_at.is_(None))
        .group_by(Item.category_id)
    )
    return {category_id: count for category_id, count in rows}


def categories_by_name(session: Session) -> Sequence[Category]:
    """Every category, retired too, by name ignoring case."""
    return session.scalars(select(Category).order_by(func.lower(Category.name))).all()


def current_items_of(session: Session, category_id: int) -> Sequence[Item]:
    """A category's current items, by name ignoring case."""
    return session.scalars(
        select(Item)
        .where(Item.category_id == category_id, Item.retired_at.is_(None))
        .order_by(func.lower(Item.name))
    ).all()
