"""Items and the packages they come in: every write to the item and
item_package tables, and their rules. A current item is in a current
category (categories.retire is the other side), and an item's unit is
fixed once stock has moved in it, as the ledger's quantities are in that
unit; a package's size, once it has moved, been opened or labelled. The
app may not move a package to another item (migration 0002), so its
labels always name its item, and a package's name is its own among its
item's, retired ones too, so one can always be brought back. A retired
item is read only until it is brought back (docs/decisions/0019). An
item's name, its manufacturer and part number together, and its old code
are each its own, retired items' too (models.Item); taken_by says which
another item has. similar finds items whose names are close, to ask
whether a new name is a different item (docs/decisions/0021)."""

from collections.abc import Collection
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
    locked,
    relock,
    require_current,
)
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    Label,
    Movement,
    OpenPackage,
    fold,
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
    # Whether opening one of its packages makes an open package, followed
    # until used up (docs/decisions/0020): each package's default.
    track_opened: bool

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
        track_opened: bool = False,
    ) -> Fields:
        """A new item's: those not given are blank, its unit each, and its
        opened packages not followed."""
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
            track_opened=track_opened,
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
            # PostgreSQL's lower(), as the index's: Python's folds more.
            func.lower(Item.manufacturer) == func.lower(literal(fields.manufacturer)),
            func.lower(Item.part_number) == func.lower(literal(fields.part_number)),
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


def similar(session: Session, name: str, item_id: int) -> list[Item]:
    """Items other than `item_id`, the one named `name` now, whose names are
    close to it, closest first, retired ones too: as search finds them
    (models.fuzzy), or with a name found in `name`, as "mast" is in "Mast,
    telescopic"; accents aside, as search's (models.fold)."""
    folded = fold(name)
    within = Item.name_folded.op("<%", is_comparison=True)(literal(folded))
    close = or_(fuzzy(Item.name_folded, folded), within)
    query = select(Item).where(close, Item.id != item_id)
    closest = func.similarity(Item.name_folded, folded).desc(), func.lower(Item.name)
    return list(session.scalars(query.order_by(*closest).limit(SIMILAR)))


def nameless(session: Session, name: str) -> bool:
    """Whether `name` is spaces and punctuation alone, as name_key finds
    them: every such name would be one name."""
    return session.scalar(select(name_key(literal(name)) == "")) is True


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


def _item_current(session: Session, package: ItemPackage) -> None:
    """RuleBroken unless the package's item is current: a retired item, its
    packages too, is read only."""
    retired = Item.id == package.item_id, Item.retired_at.is_not(None)
    enforce("retired_read_only", when=exists_where(session, *retired))


def save_package(
    session: Session, package: ItemPackage, name: str, size: Decimal, *, seen: Seen
) -> None:
    """A current package renamed, or resized while its size is not fixed
    (sizes_fixed): the ledger counts sealed ones, an open one starts, and a
    label says, its size. A name its item's other
    packages have, retired ones too, is refused by the unique index."""
    relock(session, package, current=True, seen=seen)
    _item_current(session, package)
    if size != package.size:
        enforce("package_size_fixed", when=bool(sizes_fixed(session, [package.id])))
    package.name, package.size = name, size


def sizes_fixed(session: Session, package_ids: Collection[int]) -> set[int]:
    """Those of these packages whose size is fixed: some has moved or been
    opened in it, or a label printed for it says it. Each asks only
    whether, stopping at the first."""
    if not package_ids:
        return set()
    package = ItemPackage.id
    used = (
        select(Movement.id).where(Movement.from_package_id == package),
        select(Movement.id).where(Movement.to_package_id == package),
        select(OpenPackage.id).where(OpenPackage.package_id == package),
        select(Label.id).where(Label.package_id == package),
    )
    query = select(package).where(
        package.in_(list(package_ids)), or_(*(each.exists() for each in used))
    )
    return set(session.scalars(query))


def restore_package(session: Session, package: ItemPackage) -> None:
    """A retired package brought back, unless its item is retired. Its name
    is its own still: unique, retired ones too."""
    relock(session, package, current=False)
    _item_current(session, package)
    package.retired_at = None


def retire_package(session: Session, package: ItemPackage, at: datetime) -> None:
    """Retired, unless its item is: a retired item is read only."""
    relock(session, package, current=True)
    _item_current(session, package)
    package.retired_at = at


def track_package(session: Session, package: ItemPackage, track: bool | None) -> None:
    """Whether opening one of this package makes an open package, followed
    until used up, or None: as its item says (docs/decisions/0020). Gone
    for a retired package, read only as a retired item is."""
    relock(session, package, current=True)
    _item_current(session, package)
    package.track_opened = track


def package_settings(
    session: Session,
    package: ItemPackage,
    short_below: Decimal | None,
    minimum_sealed: int | None,
) -> None:
    """A package's too-short-to-use length and minimum sealed in the
    stores (docs/decisions/0020), each optional. Gone for a retired one."""
    relock(session, package, current=True)
    _item_current(session, package)
    package.short_below, package.minimum_sealed = short_below, minimum_sealed


def track_items(session: Session, item_ids: Collection[int], track: bool) -> list[int]:
    """Set "track each opened package" on the current items of `item_ids`,
    never a package's own setting. The ids of those it changed."""
    lock_catalogue(session)
    changing = session.scalars(
        locked(
            select(Item)
            .where(
                Item.id.in_(list(item_ids)),
                Item.retired_at.is_(None),
                Item.track_opened.is_not(track),
            )
            .order_by(Item.id)
        )
    ).all()
    for item in changing:
        item.track_opened = track
    return [item.id for item in changing]
