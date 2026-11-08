"""Items and the packages they come in: every write to the item and
item_package tables, and their rules. A current item is in a current
category (categories.retire is the other side), and an item that has
moved in fractions is never counted in "each" (the ledger refuses the
other way round). The app may not move a package to another item
(migration 0002), so its labels always name its item.

A rule of more than one condition is a function of plain values,
returning the name of the rule a change would break, or None. Each write
takes the catalogue lock, reads what its rules need, and writes only if
they hold (db.locked_row)."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from inventory_tng.db import enforce, exists_where, is_current, lock_catalogue, relock
from inventory_tng.models import Category, Item, ItemPackage, Movement


def unit_fits(unit: str, *, moved_in_fractions: bool) -> str | None:
    """An item's unit changed: one counted in each moves in whole units."""
    if unit == "each" and moved_in_fractions:
        return "movement_whole_units"
    return None


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
    unit = values.get("unit", item.unit)
    if unit != item.unit:
        fractions = exists_where(
            session,
            Movement.item_id == item.id,
            Movement.quantity != func.trunc(Movement.quantity),
        )
        enforce(unit_fits(str(unit), moved_in_fractions=fractions))
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
