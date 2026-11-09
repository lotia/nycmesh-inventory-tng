"""Items and the packages they come in: every write to the item and
item_package tables, and their rules. A current item is in a current
category (categories.retire is the other side), and an item's unit is
fixed once stock has moved in it, as the ledger's quantities are in that
unit. The app may not move a package to another item (migration 0002),
so its labels always name its item.

Each write takes the catalogue lock, reads what its rules need, and
writes only if they hold (db.locked_row)."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from inventory_tng.db import enforce, exists_where, is_current, lock_catalogue, relock
from inventory_tng.models import Category, Item, ItemPackage, Movement


def _in_current_category(session: Session, category_id: int) -> None:
    """A current item, added, moved or brought back: its category is
    current."""
    current = is_current(session, Category, category_id)
    enforce("item_category_current", when=not current)


def add(session: Session, values: Mapping[str, object]) -> Item:
    """An item of these columns' values."""
    lock_catalogue(session)
    item = Item(**values)
    if item.retired_at is None:
        _in_current_category(session, item.category_id)
    session.add(item)
    session.flush()
    return item


def save(session: Session, item: Item, values: Mapping[str, object]) -> None:
    """These columns set to these values."""
    # FOR UPDATE waits for any movement of it being written.
    relock(session, item)
    if values.get("unit", item.unit) != item.unit:
        moved = exists_where(session, Movement.item_id == item.id)
        enforce("item_unit_fixed", when=moved)
    category_id = values.get("category_id", item.category_id)
    if item.retired_at is None and category_id != item.category_id:
        assert isinstance(category_id, int)
        _in_current_category(session, category_id)
    for column, value in values.items():
        setattr(item, column, value)


def retire(session: Session, item: Item, at: datetime) -> None:
    relock(session, item, current=True)
    item.retired_at = at


def restore(session: Session, item: Item) -> None:
    relock(session, item, current=False)
    _in_current_category(session, item.category_id)
    item.retired_at = None


def add_package(session: Session, item: Item, name: str, size: Decimal) -> ItemPackage:
    relock(session, item)
    package = ItemPackage(item_id=item.id, name=name, size=size)
    session.add(package)
    session.flush()
    return package


def retire_package(session: Session, package: ItemPackage, at: datetime) -> None:
    relock(session, package, current=True)
    package.retired_at = at
