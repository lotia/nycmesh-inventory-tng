"""Items and the packages they come in: every write to the item and
item_package tables, and their rules. A current item is in a current
category (categories.retire is the other side), and an item's unit is
fixed once stock has moved in it, as the ledger's quantities are in that
unit. The app may not move a package to another item (migration 0002),
so its labels always name its item. A retired item is read only until it
is brought back (docs/decisions/0019). An item's name, its manufacturer
and part number together, and its old code are each its own, retired items'
too (models.Item); taken_by says which another item has. similar finds
items whose names are close, to ask whether a new name is a different item
(docs/decisions/0021)."""

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, and_, func, literal, or_, select
from sqlalchemy.orm import Session

from inventory_tng.db import (
    Seen,
    enforce,
    exists_where,
    lock_catalogue,
    relock,
    require_current,
)
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    Movement,
    fuzzy,
    name_key,
)


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


def taken_by(
    session: Session, fields: Fields, item_id: int | None = None
) -> tuple[str, Item] | None:
    """The field of `fields` another item than `item_id` already has, and
    that item, as the unique indexes compare them: a unique violation says
    neither. None if none has, as when the other changed meanwhile."""
    wanted: list[tuple[str, ColumnElement[bool]]] = [
        ("name", name_key(Item.name) == name_key(literal(fields.name)))
    ]
    if fields.manufacturer is not None and fields.part_number is not None:
        both = and_(
            func.lower(Item.manufacturer) == fields.manufacturer.lower(),
            func.lower(Item.part_number) == fields.part_number.lower(),
        )
        wanted.append(("part_number", both))
    if fields.old_code is not None:
        wanted.append(("old_code", Item.old_code == fields.old_code))
    for field, where in wanted:
        query = select(Item).where(where)
        if item_id is not None:
            query = query.where(Item.id != item_id)
        if (other := session.scalars(query.limit(1)).first()) is not None:
            return field, other
    return None


# How many close names an administrator is shown at most.
SIMILAR = 5


def similar(session: Session, name: str, item_id: int | None = None) -> list[Item]:
    """Items other than `item_id` whose names are close to `name`, closest
    first, retired ones too: as search finds them (models.fuzzy), or with a
    name found in `name`, as "mast" is in "Mast, telescopic"."""
    within = Item.name.op("<%", is_comparison=True)(literal(name))
    query = select(Item).where(or_(fuzzy(Item.name, name), within))
    if item_id is not None:
        query = query.where(Item.id != item_id)
    closest = func.similarity(Item.name, name).desc(), func.lower(Item.name)
    return list(session.scalars(query.order_by(*closest).limit(SIMILAR)))


def same_name(session: Session, one: str, other: str) -> bool:
    """Whether two names are one, as items' are compared (name_key)."""
    same = name_key(literal(one)) == name_key(literal(other))
    return bool(session.scalar(select(same)))


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
