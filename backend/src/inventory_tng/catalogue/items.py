"""Items and the packages they come in: every write to the item and
item_package tables, and their rules. A current item is in a current
category (categories.retire is the other side), and an item's unit is
fixed once stock has moved in it, or while a label of it or its packages
is printed and not revoked, as the ledger's quantities and the labels are
in that unit, but for a change between lengths, which converts what is
recorded once an administrator confirms it was in the old unit
(unit_changes; Ali, 2026-11-24, inventory-tng-y50b), and an
item counted each has no current package
holding, or too short to use below, a fraction; a package's size is fixed
once it has moved or been opened, or while a label of it is not revoked
(sizes_fixed). The app may not move a package to another
item (the initial migration), so its labels always name its item, and a
package's name is its own among its item's, retired ones too, so one can
always be brought back. A retired
item is read only until it is brought back (docs/decisions/0019). An
item's name, its manufacturer and part number together, and its old code
are each its own, retired items' too (models.catalogue.Item); taken_by says
which another item has. An item's aliases (models.catalogue.ItemAlias) share
its names' keys: no name is another item's or any item's alias, nor an alias
any item's name or another alias, checked here holding the catalogue lock, as
every write to either takes it (docs/decisions/0021, 2026-11-14). A rename
keeps the old name as an alias, and renaming an item to its own alias swaps the
two (2026-11-15). similar finds items whose names or aliases are close, to ask
whether a new name is a different item (docs/decisions/0021).

Names, packages' and aliases' too, are compared by their unit keys as
well (models.names.unit_key), so names that read alike once their units are
read are one: `10 uF` and `10μF`. A reading rule changed reads every name
with it again (reread), refused if two would then read alike
(2026-11-15)."""

import re
from collections.abc import Collection, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import structlog
from opentelemetry import metrics
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

from inventory_tng.catalogue import unit_changes, units
from inventory_tng.catalogue.amount_rules import counted_whole, is_whole, whole_enough
from inventory_tng.catalogue.name_keys import SPACES, name_key
from inventory_tng.core.db_errors import enforce
from inventory_tng.core.db_locking import (
    Seen,
    lock_catalogue,
    locked,
    relock,
    require_current,
)
from inventory_tng.core.db_once_committed import once_committed
from inventory_tng.core.db_sessions import exists_where
from inventory_tng.models.catalogue import (
    CURRENT_ALIAS,
    SMALLEST_PACKAGE_FIRST,
    Category,
    Item,
    ItemAlias,
    ItemPackage,
    current_aliases,
    reading_rules_held,
)
from inventory_tng.models.labels import Label
from inventory_tng.models.names import unit_key
from inventory_tng.models.search_text import fold, fuzzy, in_ascii
from inventory_tng.models.stock import Movement, OpenPackage

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

tracking_changed = meter.create_counter(
    "inventory.package_tracking.changed",
    unit="{change}",
    description="Items or packages whose opened packages are followed, or "
    "no longer, set by an administrator, by where it was set",
)


def _tracking_changed(
    session: Session,
    scope: Literal["item", "package", "bulk"],
    changed: int,
    track: bool | None,
    **ids: int | list[int],
) -> None:
    """Measure and log changes once committed: `ids` names what changed."""
    once_committed(session, tracking_changed.add, changed, {"scope": scope})
    once_committed(
        session, log.info, "package tracking changed", scope=scope, track=track, **ids
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


# Refused: an item's name that is an alias, its own or another item's.
NAME_IS_ALIAS = "item_name_is_alias"
# Refused: an alias that is an item's name, its own or another's.
ALIAS_IS_NAME = "item_alias_is_name"


@dataclass(frozen=True)
class Keys:
    """A name's keys: its name_key, and its unit_key as the reading rules
    read it, which are both compared."""

    name: str
    units: str

    @classmethod
    def of(cls, session: Session, name: str, rules: units.Rules | None = None) -> Keys:
        """`name`'s keys, its units read by `rules`, else by those held."""
        held = reading_rules_held(session) if rules is None else rules
        return cls(name_key(name), unit_key(name, held))

    def same(self, row: Item | ItemAlias) -> bool:
        """Whether `row` has either key: the same name, only respelt."""
        return self.name == row.name_key or self.units == row.unit_key

    def match(self, model: type[Item | ItemAlias]) -> ColumnElement[bool]:
        """Whether a row of `model` has either key."""
        return or_(model.name_key == self.name, model.unit_key == self.units)


def _current_alias(keys: Keys) -> tuple[ColumnElement[bool], ...]:
    """Whether an alias is current and has either key."""
    return keys.match(ItemAlias), CURRENT_ALIAS


def _alias_of(keys: Keys) -> ColumnElement[bool]:
    """Whether a current alias of the item has either key."""
    of_item = ItemAlias.item_id == Item.id
    return select(ItemAlias.id).where(of_item, *_current_alias(keys)).exists()


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


# The field of another item each of Item's unique indexes, or the alias
# rule, refuses a change for (taken_by).
_FIELD_REFUSED_BY = {
    "uq_item_name": "name",
    "uq_item_unit_key": "name",
    "uq_item_manufacturer_part_number": "part_number",
    "uq_item_old_code": "old_code",
    NAME_IS_ALIAS: "alias",
}


def taken_by(
    session: Session,
    fields: Fields,
    item_id: int | None = None,
    rules: units.Rules | None = None,
    refused_by: str | None = None,
) -> tuple[str, Item] | None:
    """The field of `fields` another item than `item_id` already has, and
    that item, as the unique indexes compare them, its name's units read by
    `rules` (Keys.of), or "alias" and the other item whose alias the name
    is. Given the index or rule that refused it (_FIELD_REFUSED_BY), only that
    field is looked for; else each in turn. None if none has, as when the
    other changed meanwhile."""
    keys = Keys.of(session, fields.name, rules)
    wanted: list[tuple[str, ColumnElement[bool]]] = [
        ("alias", _alias_of(keys)),
        ("name", keys.match(Item)),
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
    if (field := _FIELD_REFUSED_BY.get(refused_by or "")) is not None:
        wanted = [(name, where) for name, where in wanted if name == field]
    return _held_by(session, wanted, item_id)


# How many close names an administrator is shown at most.
SIMILAR = 5


@dataclass(frozen=True)
class Close:
    """An item whose name is close to another, or one of whose current
    aliases is: that alias, then, as shown (KeyedName.name_shown)."""

    item: Item
    alias: str | None = None


def similar(session: Session, name: str, item_id: int) -> list[Close]:
    """Items other than `item_id`, the one named `name` now, whose names or
    current aliases are close to it, closest first, retired ones too, each
    once, by its name where that is as close as its closest alias: as search
    finds them (models.search_text.fuzzy), or with a name found in `name`, as
    "mast" is in "Mast, telescopic"; accents aside, as search's
    (models.search_text.fold)."""
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
        close(
            ItemAlias.item_id,
            ItemAlias.name_shown,
            ItemAlias.name_folded,
            CURRENT_ALIAS,
        ),
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


def _not_an_alias(session: Session, keys: Keys, but: ItemAlias | None = None) -> None:
    """RuleBroken unless no current alias has `keys`, `but` aside."""
    taken = _current_alias(keys)
    other = () if but is None else (ItemAlias.id != but.id,)
    enforce(NAME_IS_ALIAS, when=exists_where(session, *taken, *other))


# Refused: an item changed to each while a current package of it holds, or
# is too short to use below, a fraction (Ali, 2026-11-23,
# inventory-tng-fa5i); and a package's size or too-short length with a
# fraction, its item counted each.
PACKAGES_WHOLE = "item_packages_whole"
PACKAGE_WHOLE = "package_whole_units"
# The database's check (models.catalogue.Item) on a fractional Reorder at
# for an item counted each, which the form refuses first (Ali, 2026-11-23,
# inventory-tng-gfes).
MINIMUM_WHOLE = "ck_item_minimum_whole"

# A package holding 1 whose too-short length, less than 1, is set: no
# whole number fits it, and it only catches an empty package, so for an
# item counted each it is cleared by itself, history keeping it (Ali,
# 2026-11-23, inventory-tng-fa5i).
_HOLDS_ONE = (ItemPackage.size == 1, ItemPackage.short_below.is_not(None))


def fractional_package(session: Session, item_id: int) -> ItemPackage | None:
    """The item's first current package, smallest first, that holds, or is
    too short to use below, a fraction: none may, for an item counted each.
    A retired one may; one holding 1 is cleared instead (_HOLDS_ONE)."""
    size, short = ItemPackage.size, ItemPackage.short_below
    query = (
        select(ItemPackage)
        .where(
            ItemPackage.item_id == item_id,
            ItemPackage.retired_at.is_(None),
            or_(size != func.trunc(size), and_(short != func.trunc(short), size > 1)),
        )
        .order_by(*SMALLEST_PACKAGE_FIRST)
    )
    return session.scalars(query).first()


def _short_cleared(session: Session, package: ItemPackage, by: str) -> None:
    """Its too-short length cleared, as _HOLDS_ONE says, `by` "switch" or
    "restore"."""
    package.short_below = None
    once_committed(
        session,
        log.info,
        "package short end cleared",
        item_package_id=package.id,
        by=by,
    )


def _unit(session: Session, item_id: int) -> str:
    return session.scalars(select(Item.unit).where(Item.id == item_id)).one()


def _whole_for_its_item(
    session: Session, item_id: int, *amounts: Decimal | None
) -> None:
    """RuleBroken (PACKAGE_WHOLE) if any of `amounts`, a package's, has a
    fraction and its item, read afresh under the catalogue lock, is counted
    each: a form checked them against the unit before the lock. Whole
    amounts are whole in any unit, so with none the unit is not read."""
    if all(each is None or is_whole(each) for each in amounts):
        return
    enforce(PACKAGE_WHOLE, when=counted_whole(_unit(session, item_id)))


def add(session: Session, fields: Fields) -> Item:
    lock_catalogue(session)
    _in_current_category(session, fields.category_id)
    _not_an_alias(session, Keys.of(session, fields.name))
    item = Item(**asdict(fields))
    session.add(item)
    session.flush()
    once_committed(session, log.info, "item added", item_id=item.id)
    return item


@dataclass(frozen=True)
class Renamed:
    """What renaming an item did to its aliases: its old name kept as one,
    and, if it was renamed to one of its own, that alias removed."""

    kept: ItemAlias
    swapped: ItemAlias | None


def save(
    session: Session,
    item: Item,
    fields: Fields,
    *,
    seen: Seen,
    recorded_in: str | None = None,
    shown_free: bool | None = None,
) -> Renamed | None:
    """Every one of `fields` set; and, if its name's key changes, its old
    name kept as an alias, in place of the alias it is renamed to if it is
    one of its own (Ali, 2026-11-15): what that did. The old name was the
    item's, so no other item's name nor any current alias, and so is free
    as an alias. Its unit fixed (unit_changes.unit_fixed), a change between
    lengths converts what is recorded, `fields`' Reorder at too, as typed
    in its unit, once `recorded_in`, the administrator's answer, says what
    was recorded was in its unit as it is held; else
    unit_changes.UNIT_UNCONFIRMED, to ask (unit_changes.convert). A
    change is refused as changed since shown unless its form, if any,
    showed the unit free or fixed as it is (`shown_free`;
    unit_changes.require_fixed_as_shown)."""
    # FOR UPDATE waits for any movement of it being written, and for any
    # label of it being made, which locks the item too (labels.make_for_item).
    relock(session, item, seen=seen)
    enforce("retired_read_only", when=item.retired_at is not None)
    if fields.unit != item.unit:
        why = unit_changes.unit_fixed(session, item)
        unit_changes.require_fixed_as_shown(item.id, why, shown_free)
        if why:
            minimum = unit_changes.convert(
                session,
                item,
                fields.unit,
                fields.minimum,
                why=why,
                recorded_in=recorded_in,
            )
            fields = replace(fields, minimum=minimum)
        else:
            if counted_whole(fields.unit):
                # Under the catalogue lock, which every package write takes.
                fraction = fractional_package(session, item.id) is not None
                enforce(PACKAGES_WHOLE, when=fraction)
                holding_one = select(ItemPackage).where(
                    ItemPackage.item_id == item.id,
                    ItemPackage.retired_at.is_(None),
                    *_HOLDS_ONE,
                )
                for package in session.scalars(locked(holding_one)):
                    _short_cleared(session, package, "switch")
            unit_changes.follow(session, item.id, fields.unit)
    if fields.category_id != item.category_id:
        _in_current_category(session, fields.category_id)
    keys = Keys.of(session, fields.name)
    # Only respelt, its units too, it is the same name.
    renamed = not keys.same(item)
    old = item.name
    swapped = _own_alias(session, item.id, keys) if renamed else None
    # Its own alias aside, swapped, as matched by either key: no other.
    _not_an_alias(session, keys, but=swapped)
    if fields.track_opened != item.track_opened:
        _tracking_changed(session, "item", 1, fields.track_opened, item_id=item.id)
    for column, value in asdict(fields).items():
        setattr(item, column, value)
    once_committed(session, log.info, "item saved", item_id=item.id)
    if not renamed:
        return None
    how = "rename" if swapped is None else "swap"
    if swapped is not None:
        _retire_alias(session, swapped, datetime.now(UTC))
    kept = _aliased(session, item, old)
    once_committed(
        session, log.info, "alias added", item_id=item.id, item_alias_id=kept.id, by=how
    )
    if swapped is not None:
        once_committed(
            session,
            log.info,
            "alias removed",
            item_id=item.id,
            item_alias_id=swapped.id,
            by=how,
        )
    return Renamed(kept, swapped)


def _own_alias(session: Session, item_id: int, keys: Keys) -> ItemAlias | None:
    """The item's current alias with either key, if it has one."""
    query = current_aliases(item_id).where(keys.match(ItemAlias))
    return session.scalars(query).first()


def _aliased(session: Session, item: Item, name: str) -> ItemAlias:
    """`name` written as the item's alias; the flush refolds the item to
    find it by it (models.catalogue._refold)."""
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


def alias_taken_by(
    session: Session, name: str, rules: units.Rules | None = None
) -> tuple[str, Item] | None:
    """ "name" and the item whose name has `name`'s keys (Keys.of, by
    `rules`), retired items' too, or "alias" and the item one of whose
    current aliases has; if any."""
    keys = Keys.of(session, name, rules)
    return _held_by(session, [("name", keys.match(Item)), ("alias", _alias_of(keys))])


def add_alias(session: Session, item: Item, name: str) -> ItemAlias:
    """Another name `item` is found by. Refused if it is an item's name,
    its own or another's (ALIAS_IS_NAME), or by the unique index if a
    current alias has it; and for a retired item, read only."""
    relock(session, item)
    enforce("retired_read_only", when=item.retired_at is not None)
    keys = Keys.of(session, name)
    enforce(ALIAS_IS_NAME, when=exists_where(session, keys.match(Item)))
    alias = _aliased(session, item, name)
    once_committed(
        session, log.info, "alias added", item_id=item.id, item_alias_id=alias.id
    )
    return alias


def remove_alias(session: Session, alias: ItemAlias, at: datetime) -> None:
    """A current alias retired, freeing its name, unless its item is
    retired, read only."""
    item = session.get_one(Item, alias.item_id)
    relock(session, item)
    _retire_alias(session, alias, at)
    enforce("retired_read_only", when=item.retired_at is not None)
    session.flush()
    once_committed(
        session, log.info, "alias removed", item_id=item.id, item_alias_id=alias.id
    )


def retire(session: Session, item: Item, at: datetime) -> None:
    relock(session, item, current=True)
    item.retired_at = at
    once_committed(session, log.info, "item retired", item_id=item.id)


def restore(session: Session, item: Item) -> None:
    relock(session, item, current=False)
    _in_current_category(session, item.category_id)
    item.retired_at = None
    once_committed(session, log.info, "item restored", item_id=item.id)


def add_package(
    session: Session, item: Item, name: str, size: Decimal, *, shown_unit: str | None
) -> ItemPackage:
    """A package of the item, holding `size` in its unit, the one its form
    showed (`shown_unit`; unit_changes.require_unit_shown)."""
    relock(session, item)
    enforce("retired_read_only", when=item.retired_at is not None)
    # Its unit as locked: the form checked the size against the one before.
    enforce(PACKAGE_WHOLE, when=not whole_enough(item.unit, size))
    unit_changes.require_unit_shown(item, shown_unit, "add_package")
    package = ItemPackage(item_id=item.id, name=name, size=size)
    session.add(package)
    session.flush()
    once_committed(session, log.info, "package added", item_package_id=package.id)
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
        if (why := sizes_fixed(session, [package.id]).get(package.id)) is not None:
            enforce(SIZE_FIXED_RULES[why])
        _whole_for_its_item(session, package.item_id, size)
    package.name, package.size = name, size
    once_committed(session, log.info, "package saved", item_package_id=package.id)


# Why a package's size is fixed: some has moved or been opened in it, for
# good, or only a label not revoked says it.
SizeFixed = Literal["used", "labelled"]
SIZE_FIXED_RULES: dict[SizeFixed, str] = {
    "used": "package_size_fixed",
    "labelled": "package_size_labelled",
}


def sizes_fixed(session: Session, package_ids: Collection[int]) -> dict[int, SizeFixed]:
    """Those of these packages whose size is fixed, and why: some has moved
    or been opened in it, or a label printed for it, not revoked, says it
    (Ali, 2026-11-23, inventory-tng-37dj). Each asks only whether, stopping
    at the first."""
    if not package_ids:
        return {}
    package = ItemPackage.id
    used = or_(
        select(Movement.id).where(Movement.from_package_id == package).exists(),
        select(Movement.id).where(Movement.to_package_id == package).exists(),
        select(OpenPackage.id).where(OpenPackage.package_id == package).exists(),
    )
    labelled = (
        select(Label.id)
        .where(Label.package_id == package, Label.revoked_at.is_(None))
        .exists()
    )
    query = select(package, used).where(
        package.in_(list(package_ids)), or_(used, labelled)
    )
    return {
        each: "used" if was_used else "labelled"
        for each, was_used in session.execute(query)
    }


def restore_package(session: Session, package: ItemPackage) -> None:
    """A retired package brought back, unless its item is retired, or is
    counted each and the package holds a fraction. One too short to use
    below a fraction comes back so, to be set whole on the item page, as
    it only says when an open package is a short end (Ali, 2026-11-23,
    inventory-tng-fa5i); one holding 1 is cleared instead (_HOLDS_ONE).
    Its name is its own still: unique, retired ones too."""
    relock(session, package, current=False)
    _item_current(session, package)
    holds_one = package.size == 1 and package.short_below is not None
    if holds_one and counted_whole(_unit(session, package.item_id)):
        _short_cleared(session, package, "restore")
    # Nor holding a fraction, once its item is counted each.
    _whole_for_its_item(session, package.item_id, package.size)
    package.retired_at = None
    once_committed(session, log.info, "package restored", item_package_id=package.id)


def retire_package(session: Session, package: ItemPackage, at: datetime) -> None:
    """Retired, unless its item is: a retired item is read only."""
    relock(session, package, current=True)
    _item_current(session, package)
    package.retired_at = at
    once_committed(session, log.info, "package retired", item_package_id=package.id)


def track_package(session: Session, package: ItemPackage, track: bool | None) -> None:
    """Whether opening one of this package makes an open package, followed
    until used up, or None: as its item says (docs/decisions/0020). Gone
    for a retired package, read only as a retired item is."""
    relock(session, package, current=True)
    _item_current(session, package)
    if track != package.track_opened:
        _tracking_changed(session, "package", 1, track, item_package_id=package.id)
    package.track_opened = track


def package_settings(
    session: Session,
    package: ItemPackage,
    short_below: Decimal | None,
    minimum_sealed: int | None,
    *,
    shown_unit: str | None,
    keep_short: bool = False,
) -> None:
    """A package's too-short-to-use length and minimum sealed in the
    stores (docs/decisions/0020), each optional, a length in the
    package's unit, the one its form showed (`shown_unit`;
    unit_changes.require_unit_shown); with `keep_short`, the length as it
    is once locked, whatever `short_below` says, for a page that showed no
    box for it. Gone for a retired one."""
    relock(session, package, current=True)
    _item_current(session, package)
    if keep_short:
        short_below = package.short_below
    _whole_for_its_item(session, package.item_id, short_below)
    if not keep_short and short_below is not None:
        unit_changes.require_unit_shown(package, shown_unit, "package_settings")
    package.short_below, package.minimum_sealed = short_below, minimum_sealed
    once_committed(
        session,
        log.info,
        "package settings saved",
        item_package_id=package.id,
        short_below=short_below,
        minimum_sealed=minimum_sealed,
    )


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
    changed = [item.id for item in changing]
    if changed:
        _tracking_changed(session, "bulk", len(changed), track, item_ids=changed)
    return changed


# Refused: a reading rule changed that would make names read alike.
READ_ALIKE = "names_read_alike"


@dataclass(frozen=True)
class Named:
    """A row whose name a reading rule reads: its table, id and name."""

    table: str
    id: int
    name: str


type Rows = tuple[list[Item], list[ItemPackage], list[ItemAlias]]
# Each row's unit key as `rules` read its name (_keyed).
type Keyed = dict[Item | ItemPackage | ItemAlias, str]


def _keyed(rows: Rows, rules: units.Rules) -> Keyed:
    """Each of `rows` with its name's unit key by `rules`."""
    return {row: unit_key(row.name, rules) for row in (*rows[0], *rows[1], *rows[2])}


# Any space, as units._after_a_number reads each one plain (inventory-tng-vw0f).
_SPACE = f"[ {SPACES}]"


def _after_a_number(token: str) -> str:
    """A PostgreSQL regular expression for `token` right after a number, as
    units.AFTER_A_NUMBER finds a unit: a digit, perhaps a space, then it,
    then no letter or digit, each space any (_SPACE), in it too (`deg C`).
    Case kept, as a rule's token is."""
    spelt = re.escape(token).replace(r"\ ", _SPACE)
    return rf"[0-9]{_SPACE}?{spelt}(?![[:alnum:]])"


def _with_units(session: Session, tokens: Iterable[str], *, lock: bool) -> Rows:
    """Items, packages and current aliases with any token right after a
    number in a name or what is searched of them (an item's search_text
    holds its aliases' too, as typed), by id, locked if `lock`: every one a
    rule for those tokens may read otherwise, and no other (Ali,
    2026-11-15: rules are kept by the unit, so a common number locks
    nothing more)."""
    tokens = list(tokens)
    if not tokens:
        return [], [], []
    raw = [_after_a_number(token) for token in tokens]
    folded = {_after_a_number(in_ascii(t)) for t in tokens}

    def read[M: (Item, ItemPackage, ItemAlias)](
        model: type[M], matches: list[ColumnElement[bool]], *also: ColumnElement[bool]
    ) -> list[M]:
        query = select(model).where(or_(*matches), *also).order_by(model.id)
        return list(session.scalars(locked(query) if lock else query))

    def searched(model: type[Item | ItemPackage]) -> list[ColumnElement[bool]]:
        return [
            *(model.name.regexp_match(p) for p in raw),
            *(model.search_text.regexp_match(p) for p in folded),
        ]

    aliases = [ItemAlias.name.regexp_match(p) for p in raw]
    return (
        read(Item, searched(Item)),
        read(ItemPackage, searched(ItemPackage)),
        read(ItemAlias, aliases, CURRENT_ALIAS),
    )


def _read_alike(session: Session, rows: Rows, new: Keyed) -> list[list[Named]]:
    """Names that would read alike, by their `new` keys, of which one at
    least is in `rows`, read again: each group, by table and id. Items'
    names and current aliases are one namespace; an item's packages
    another."""
    items_, packages, aliases = rows
    keys = set(new.values())

    def others[M: (Item, ItemPackage, ItemAlias)](
        model: type[M], mine: list[M], *where: ColumnElement[bool]
    ) -> list[tuple[M, str]]:
        ids = [row.id for row in mine]
        query = select(model).where(
            model.unit_key.in_(keys), model.id.not_in(ids), *where
        )
        return [(row, row.unit_key) for row in session.scalars(query)]

    groups: dict[tuple[object, ...], list[Named]] = {}
    every = [
        *new.items(),
        *others(Item, items_),
        *others(ItemPackage, packages),
        *others(ItemAlias, aliases, CURRENT_ALIAS),
    ]
    for row, key in every:
        scope = (row.item_id,) if isinstance(row, ItemPackage) else ()
        named = Named(row.__tablename__, row.id, row.name)
        groups.setdefault((*scope, key), []).append(named)
    changed = {
        (row.__tablename__, row.id) for row, key in new.items() if key != row.unit_key
    }
    return [
        group
        for group in groups.values()
        if len(group) > 1 and any((each.table, each.id) in changed for each in group)
    ]


def read_alike(
    session: Session, tokens: Iterable[str], rules: units.Rules
) -> list[list[Named]]:
    """Names that would read alike by `rules`, of those with these units
    after a number: what reread refuses, said after its savepoint is
    undone."""
    rows = _with_units(session, tokens, lock=False)
    return _read_alike(session, rows, _keyed(rows, rules))


def reread(session: Session, tokens: Iterable[str]) -> None:
    """Every name, package and alias with these units after a number read
    again by the reading rules as the session holds them: their unit keys,
    how each is shown and what is searched of them. Refused (READ_ALIKE) if
    two names would then read alike, naming nothing: read_alike says which.
    The rules are written first (reading_rules.py), holding the catalogue
    lock."""
    lock_catalogue(session)
    rules = reading_rules_held(session)
    rows = _with_units(session, tokens, lock=True)
    keys = _keyed(rows, rules)
    enforce(READ_ALIKE, when=bool(_read_alike(session, rows, keys)))
    items_, packages, _ = rows
    changing = {row: key for row, key in keys.items() if key != row.unit_key}
    # Set apart first: unique indexes check row by row, so two names
    # trading keys would clash halfway.
    for row in changing:
        row.unit_key = f"#{row.id}"
    session.flush()
    for row, key in changing.items():
        row.unit_key = key
    # And each shown as it now reads (KeyedName.name_shown).
    for row in keys:
        row.name_shown = units.shown_name(row.name, rules)
    # The items' current aliases in one read, not one read an item.
    written: dict[int, list[ItemAlias]] = {item.id: [] for item in items_}
    if written:
        query = select(ItemAlias).where(ItemAlias.item_id.in_(written), CURRENT_ALIAS)
        for alias in session.scalars(query.order_by(ItemAlias.id)):
            written[alias.item_id].append(alias)
    for item in items_:
        item.refold(rules, written[item.id])
    for package in packages:
        package.refold(rules)
