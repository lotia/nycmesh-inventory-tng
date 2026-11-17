"""What the catalogue's pages read (admin/categories.py, admin/items.py,
admin/reading_rules.py, and admin/home.py's counts of its tables): plain
functions taking a Session, each named for what it returns
(docs/decisions/0023)."""

from collections.abc import Sequence

from sqlalchemy import ColumnElement, Row, func, select
from sqlalchemy.orm import Session

from inventory_tng.models import (
    Admin,
    Category,
    Item,
    ItemPackage,
    Location,
    ReadingRule,
    Volunteer,
)

# Each item's category's name, for a select of items to sort by.
category_name = (
    select(Category.name).where(Category.id == Item.category_id).scalar_subquery()
)


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


def packages_of_item(session: Session, item_id: int) -> Sequence[ItemPackage]:
    """An item's packages, retired too, the current ones first, then by
    size."""
    return session.scalars(
        select(ItemPackage)
        .where(ItemPackage.item_id == item_id)
        .order_by(ItemPackage.retired_at.is_not(None), ItemPackage.size)
    ).all()


def rules_with_deciders(
    session: Session, *where: ColumnElement[bool]
) -> Sequence[Row[ReadingRule, str]]:
    """The reading rules `where` picks, each with the username of the
    administrator who settled it, None for one the import settled (an
    outer join, which the row's type does not say)."""
    return session.execute(
        select(ReadingRule, Admin.username)
        .outerjoin(Admin, Admin.id == ReadingRule.decided_by_admin_id)
        .where(*where)
    ).all()


def current_count(
    session: Session, model: type[Item | Category | Location | Volunteer]
) -> int | None:
    """How many current rows a catalogue table holds."""
    return session.scalar(
        select(func.count()).select_from(model).where(model.retired_at.is_(None))
    )
