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
another item has. An item's aliases (models.ItemAlias) share its names'
keys: no name is another item's or any item's alias, nor an alias any
item's name or another alias, checked here holding the catalogue lock, as
every write to either takes it (docs/decisions/0021, 2026-11-14). A
rename keeps the old name as an alias, and renaming an item to its own
alias swaps the two (2026-11-15). similar finds items whose names or
aliases are close, to ask whether a new name is a different item
(docs/decisions/0021)."""

from collections.abc import Collection
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    SQLColumnExpression,
    and_,
    func,
    literal,
    null,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import distinct_on
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
    CURRENT_ALIAS,
    Category,
    Item,
    ItemAlias,
    ItemPackage,
    Label,
    Movement,
    OpenPackage,
    current_aliases,
    fold,
    fuzzy,
)
from inventory_tng.names import name_key


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


# Refused: an item's name that is an alias, its own or another item's.
NAME_IS_ALIAS = "item_name_is_alias"
# Refused: an alias that is an item's name, its own or another's.
ALIAS_IS_NAME = "item_alias_is_name"


def _current_alias(key: str) -> tuple[ColumnElement[bool], ...]:
    """Whether an alias is current and has this key."""
    return ItemAlias.name_key == key, CURRENT_ALIAS


def _alias_of(key: str) -> ColumnElement[bool]:
    """Whether a current alias of the item has this key."""
    of_item = ItemAlias.item_id == Item.id
    return select(ItemAlias.id).where(of_item, *_current_alias(key)).exists()


def _held_by(
    session: Session,
    wanted: list[tuple[str, ColumnElement[bool]]],
    item_id: int | None = None,
) -> tuple[str, Item] | None:
    """The first of `wanted`'s fields an item other than `item_id` has, by
    its condition, and that item."""
    for field, where in wanted:
        query = select(Item).where(where)
        if item_id is not None:
            query = query.where(Item.id != item_id)
        if (other := session.scalars(query.limit(1)).first()) is not None:
            return field, other
    return None


def taken_by(
    session: Session, fields: Fields, item_id: int | None = None
) -> tuple[str, Item] | None:
    """The field of `fields` another item than `item_id` already has, and
    that item, as the unique indexes compare them, or "alias" and the other
    item whose alias the name is: a unique violation says neither. None if
    none has, as when the other changed meanwhile."""
    key = name_key(fields.name)
    wanted: list[tuple[str, ColumnElement[bool]]] = [
        ("alias", _alias_of(key)),
        ("name", Item.name_key == key),
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
    return _held_by(session, wanted, item_id)


# How many close names an administrator is shown at most.
SIMILAR = 5


@dataclass(frozen=True)
class Close:
    """An item whose name is close to another, or one of whose current
    aliases is: that alias, then, as spelt."""

    item: Item
    alias: str | None = None


def similar(session: Session, name: str, item_id: int) -> list[Close]:
    """Items other than `item_id`, the one named `name` now, whose names or
    current aliases are close to it, closest first, retired ones too, each
    once, by its name where that is as close as its closest alias: as
    search finds them (models.fuzzy), or with a name found in `name`, as
    "mast" is in "Mast, telescopic"; accents aside, as search's
    (models.fold)."""
    folded = fold(name)

    def close(
        of: SQLColumnExpression[int],
        alias: SQLColumnExpression[Any],
        column: SQLColumnExpression[str],
        *where: ColumnElement[bool],
    ) -> Select[int, Any, float]:
        """Items, by `of`, whose `column` is close, the alias it is if any,
        and how close."""
        within = column.op("<%", is_comparison=True)(literal(folded))
        score = func.similarity(column, folded)
        return select(
            of.label("item_id"), alias.label("alias"), score.label("score")
        ).where(or_(fuzzy(column, folded), within), of != item_id, *where)

    found = union_all(
        close(Item.id, null(), Item.name_folded),
        close(ItemAlias.item_id, ItemAlias.name, ItemAlias.name_folded, CURRENT_ALIAS),
    ).subquery()
    best = (
        select(found)
        .ext(distinct_on(found.c.item_id))
        .order_by(found.c.item_id, found.c.score.desc(), found.c.alias.nulls_first())
        .subquery()
    )
    query = (
        select(Item, best.c.alias)
        .join(best, best.c.item_id == Item.id)
        .order_by(best.c.score.desc(), func.lower(Item.name))
        .limit(SIMILAR)
    )
    return [Close(item, alias) for item, alias in session.execute(query)]


def _not_an_alias(session: Session, name: str) -> None:
    """RuleBroken unless no current alias has `name`'s key."""
    taken = _current_alias(name_key(name))
    enforce(NAME_IS_ALIAS, when=exists_where(session, *taken))


def add(session: Session, fields: Fields) -> Item:
    lock_catalogue(session)
    _in_current_category(session, fields.category_id)
    _not_an_alias(session, fields.name)
    item = Item(**asdict(fields))
    session.add(item)
    session.flush()
    return item


@dataclass(frozen=True)
class Renamed:
    """What renaming an item did to its aliases: its old name kept as one,
    and, if it was renamed to one of its own, that alias removed."""

    kept: ItemAlias
    swapped: ItemAlias | None


def save(session: Session, item: Item, fields: Fields, *, seen: Seen) -> Renamed | None:
    """Every one of `fields` set; and, if its name's key changes, its old
    name kept as an alias, in place of the alias it is renamed to if it is
    one of its own (Ali, 2026-11-15): what that did. The old name was the
    item's, so no other item's name nor any current alias, and so is free
    as an alias."""
    # FOR UPDATE waits for any movement of it being written.
    relock(session, item, seen=seen)
    enforce("retired_read_only", when=item.retired_at is not None)
    if fields.unit != item.unit:
        moved = exists_where(session, Movement.item_id == item.id)
        enforce("item_unit_fixed", when=moved)
    if fields.category_id != item.category_id:
        _in_current_category(session, fields.category_id)
    old, renamed = item.name, name_key(fields.name) != item.name_key
    swapped = _own_alias(session, item.id, fields.name) if renamed else None
    if swapped is None:
        _not_an_alias(session, fields.name)
    for column, value in asdict(fields).items():
        setattr(item, column, value)
    if not renamed:
        return None
    if swapped is not None:
        _retire_alias(session, swapped, datetime.now(UTC))
    return Renamed(_aliased(session, item, old), swapped)


def _own_alias(session: Session, item_id: int, name: str) -> ItemAlias | None:
    """The item's current alias with `name`'s key, if it has one."""
    query = current_aliases(item_id).where(ItemAlias.name_key == name_key(name))
    return session.scalars(query).first()


def _aliased(session: Session, item: Item, name: str) -> ItemAlias:
    """`name` written as the item's alias; the flush refolds the item to
    find it by it (models._refold)."""
    alias = ItemAlias(item_id=item.id, name=name)
    session.add(alias)
    session.flush()
    return alias


def _retire_alias(session: Session, alias: ItemAlias, at: datetime) -> None:
    relock(session, alias, current=True)
    alias.retired_at = at


def aliases(session: Session, item_id: int) -> list[ItemAlias]:
    """The item's current aliases, oldest first."""
    return list(session.scalars(current_aliases(item_id)))


def alias_taken_by(session: Session, name: str) -> tuple[str, Item] | None:
    """ "name" and the item whose name has `name`'s key, retired items' too,
    or "alias" and the item one of whose current aliases has; if any."""
    key = name_key(name)
    return _held_by(
        session, [("name", Item.name_key == key), ("alias", _alias_of(key))]
    )


def add_alias(session: Session, item: Item, name: str) -> ItemAlias:
    """Another name `item` is found by. Refused if it is an item's name,
    its own or another's (ALIAS_IS_NAME), or by the unique index if a
    current alias has it; and for a retired item, read only."""
    relock(session, item)
    enforce("retired_read_only", when=item.retired_at is not None)
    enforce(
        ALIAS_IS_NAME,
        when=exists_where(session, Item.name_key == name_key(name)),
    )
    return _aliased(session, item, name)


def remove_alias(session: Session, alias: ItemAlias, at: datetime) -> None:
    """A current alias retired, freeing its name, unless its item is
    retired, read only."""
    item = session.get_one(Item, alias.item_id)
    relock(session, item)
    _retire_alias(session, alias, at)
    enforce("retired_read_only", when=item.retired_at is not None)
    session.flush()


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
