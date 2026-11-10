"""Items and the packages they come in: every write to the item and
item_package tables, and their rules. A current item is in a current
category (categories.retire is the other side), and an item's unit is
fixed once stock has moved in it, as the ledger's quantities are in that
unit. The app may not move a package to another item (migration 0002),
so its labels always name its item. A retired item is read only until it
is brought back (docs/decisions/0019)."""

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from inventory_tng.db import (
    Seen,
    enforce,
    exists_where,
    lock_catalogue,
    relock,
    require_current,
)
from inventory_tng.models import Category, Item, ItemPackage, Movement


def _in_current_category(session: Session, category_id: int) -> None:
    """A current item, added, moved or brought back: its category is
    current."""
    require_current(session, Category, category_id, "item_category_current")


@dataclass(frozen=True, kw_only=True)
class Fields:
    """What an administrator sets of an item: every column but what is on
    order (which a receipt lowers) and when it was retired (retire,
    restore). Each is required, so a field left out is an error, never
    blanked: save sets them all. A new item's may be left blank (new)."""

    name: str
    category_id: int
    unit: str
    minimum: Decimal | None
    manufacturer: str | None
    part_number: str | None
    old_code: str | None
    notes: str | None
    ordering_notes: str | None

    @classmethod
    def new(
        cls,
        *,
        name: str,
        category_id: int,
        unit: str = "each",
        minimum: Decimal | None = None,
        manufacturer: str | None = None,
        part_number: str | None = None,
        old_code: str | None = None,
        notes: str | None = None,
        ordering_notes: str | None = None,
    ) -> Fields:
        """A new item's: those not given are blank, and its unit each."""
        return cls(
            name=name,
            category_id=category_id,
            unit=unit,
            minimum=minimum,
            manufacturer=manufacturer,
            part_number=part_number,
            old_code=old_code,
            notes=notes,
            ordering_notes=ordering_notes,
        )


def add(session: Session, fields: Fields) -> Item:
    lock_catalogue(session)
    _in_current_category(session, fields.category_id)
    item = Item(**asdict(fields))
    session.add(item)
    session.flush()
    return item


def save(session: Session, item: Item, fields: Fields, *, seen: Seen) -> None:
    """Every one of `fields` set."""
    # FOR UPDATE waits for any movement of it being written.
    relock(session, item, seen=seen)
    enforce("retired_read_only", when=item.retired_at is not None)
    if fields.unit != item.unit:
        moved = exists_where(session, Movement.item_id == item.id)
        enforce("item_unit_fixed", when=moved)
    if fields.category_id != item.category_id:
        _in_current_category(session, fields.category_id)
    for column, value in asdict(fields).items():
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
    enforce("retired_read_only", when=item.retired_at is not None)
    package = ItemPackage(item_id=item.id, name=name, size=size)
    session.add(package)
    session.flush()
    return package


def retire_package(session: Session, package: ItemPackage, at: datetime) -> None:
    """Retired, unless its item is: a retired item is read only."""
    relock(session, package, current=True)
    retired = Item.id == package.item_id, Item.retired_at.is_not(None)
    enforce("retired_read_only", when=exists_where(session, *retired))
    package.retired_at = at
