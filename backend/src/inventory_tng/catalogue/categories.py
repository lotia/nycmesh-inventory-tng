"""Categories: every write to the category table, and its rule. A category
with current items stays current: they would be left in one nobody can
pick (items.add, save and restore are the other side)."""

from datetime import datetime

import structlog
from sqlalchemy.orm import Session

from inventory_tng.core.db_errors import enforce
from inventory_tng.core.db_locking import Seen, lock_catalogue, relock
from inventory_tng.core.db_once_committed import once_committed
from inventory_tng.core.db_sessions import exists_where
from inventory_tng.models.catalogue import Category, Item

log = structlog.get_logger(__name__)


def add(session: Session, name: str) -> Category:
    lock_catalogue(session)
    category = Category(name=name)
    session.add(category)
    session.flush()
    once_committed(session, log.info, "category added", category_id=category.id)
    return category


def rename(session: Session, category: Category, name: str, *, seen: Seen) -> None:
    relock(session, category, seen=seen)
    enforce("retired_read_only", when=category.retired_at is not None)
    category.name = name
    once_committed(session, log.info, "category renamed", category_id=category.id)


def retire(session: Session, category: Category, at: datetime) -> None:
    relock(session, category, current=True)
    has_items = exists_where(
        session, Item.category_id == category.id, Item.retired_at.is_(None)
    )
    enforce("category_has_items", when=has_items)
    category.retired_at = at
    once_committed(session, log.info, "category retired", category_id=category.id)


def restore(session: Session, category: Category) -> None:
    relock(session, category, current=False)
    category.retired_at = None
    once_committed(session, log.info, "category restored", category_id=category.id)
