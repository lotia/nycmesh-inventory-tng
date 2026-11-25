"""What the stock pages read (docs/decisions/0023): plain functions taking
a Session, each named for what it returns. They lock nothing and refuse
nothing; the balance itself is stock/balance.py's."""

from collections.abc import Collection, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Row, Select, any_, func, or_, select, true
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from inventory_tng.core.db_lookups import values_by
from inventory_tng.core.db_sessions import by_name
from inventory_tng.core.phrases import PATH_BETWEEN
from inventory_tng.models.admins import Admin
from inventory_tng.models.catalogue import SMALLEST_PACKAGE_FIRST, Item, ItemPackage
from inventory_tng.models.places import STORE_KINDS, Location
from inventory_tng.models.stock import (
    AT_ONE_PLACE,
    AdjustmentReason,
    LedgerTransaction,
    Movement,
    OpenPackage,
)
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.stock import package_forms
from inventory_tng.stock.balance import balance, by_form, countable, countable_item


def balances(
    session: Session, where: ColumnElement[bool]
) -> list[tuple[Item, Location, Decimal]]:
    """The balances `where` picks, each item and place with how much, by
    place then item, as people sort them."""
    return [
        (item, location, quantity)
        for item, location, quantity in session.execute(
            select(Item, Location, balance.c.quantity)
            .join(Item, Item.id == balance.c.item_id)
            .join(Location, Location.id == balance.c.location_id)
            .where(where)
            .order_by(func.lower(Location.name), func.lower(Item.name))
        )
    ]


def items_with_ids(
    session: Session,
    ids: Collection[int],
    matching: ColumnElement[bool] | None = None,
) -> Sequence[Item]:
    """The items of `ids`, those `matching` too if given."""
    if not ids:
        return []
    found = select(Item).where(Item.id.in_(ids))
    if matching is not None:
        found = found.where(matching)
    return session.scalars(found).all()


def places_with_ids(session: Session, ids: Collection[int]) -> Sequence[Location]:
    """The places of `ids`."""
    return session.scalars(select(Location).where(Location.id.in_(ids))).all()


def packages_with_minimum(session: Session) -> Sequence[ItemPackage]:
    """Current packages with a minimum sealed to keep."""
    return session.scalars(
        select(ItemPackage).where(
            ItemPackage.minimum_sealed.is_not(None), ItemPackage.retired_at.is_(None)
        )
    ).all()


def items_to_reorder(session: Session, item_ids: Collection[int]) -> Sequence[Item]:
    """Current items with a reorder level, or of `item_ids`, by name."""
    return session.scalars(
        select(Item)
        .where(
            Item.retired_at.is_(None),
            or_(
                Item.minimum.is_not(None),
                Item.id.in_(item_ids),
            ),
        )
        .order_by(func.lower(Item.name))
    ).all()


def forms_in_stores(
    session: Session,
) -> dict[tuple[int, int], package_forms.Forms]:
    """Stock in the current stores, by item and place, form by form
    (package_forms.forms_of)."""
    # The stores' ids as an array: PostgreSQL pushes it down to the
    # ledger's indexes, so only the stores' balances are summed.
    stores = func.array(
        select(Location.id)
        .where(Location.kind.in_(STORE_KINDS), Location.retired_at.is_(None))
        .scalar_subquery()
    )
    return package_forms.forms_of(session, by_form.c.location_id == any_(stores))


# The place a count or an adjustment was at.
count_place = func.coalesce(
    LedgerTransaction.to_location_id, LedgerTransaction.from_location_id
)
# What the list of counts and adjustments may be filtered to, by kind.
KINDS = ("count", "adjustment")


def _done(*where: ColumnElement[bool]) -> Select:
    """The counts and adjustments `where` picks, with who, where and why."""
    return (
        select(LedgerTransaction, Admin.username, Location.name, AdjustmentReason.name)
        .join(Admin, Admin.id == LedgerTransaction.admin_id)
        .join(Location, Location.id == count_place)
        .outerjoin(AdjustmentReason, AdjustmentReason.id == LedgerTransaction.reason_id)
        .where(LedgerTransaction.kind.in_(KINDS), *where)
    )


def counts_and_adjustments_found(session: Session, *where: ColumnElement[bool]) -> int:
    """How many counts and adjustments `where` picks."""
    return (
        session.scalar(select(func.count()).select_from(_done(*where).subquery())) or 0
    )


def counts_and_adjustments(
    session: Session,
    *where: ColumnElement[bool],
    order: Sequence[Any],
    limit: int | None,
    offset: int,
) -> Sequence[Row[LedgerTransaction, str, str, str | None]]:
    """The counts and adjustments `where` picks, in `order`, `limit` of
    them from `offset`: each with who, where and why."""
    return session.execute(
        _done(*where).order_by(*order).limit(limit).offset(offset)
    ).all()


def count_lines(
    session: Session, transaction_ids: Collection[int]
) -> list[tuple[int, Item, Decimal]]:
    """The lines of the counts and adjustments of `transaction_ids`: each
    one's id, item and amount."""
    return [
        (transaction_id, item, amount)
        for transaction_id, item, amount in session.execute(
            select(Movement.transaction_id, Item, Movement.quantity)
            .join(Item, Item.id == Movement.item_id)
            .where(Movement.transaction_id.in_(transaction_ids))
        )
    ]


def _any_done() -> Select:
    return select(LedgerTransaction).where(LedgerTransaction.kind.in_(KINDS))


def places_counted(session: Session) -> Sequence[Row[int, str]]:
    """Each place with a count or an adjustment, its id and name, by name."""
    return session.execute(
        select(Location.id, Location.name)
        .where(Location.id.in_(_any_done().with_only_columns(count_place)))
        .order_by(func.lower(Location.name), Location.id)
    ).all()


def counters(session: Session) -> Sequence[Row[int, str]]:
    """Each administrator with a count or an adjustment, their id and
    username, by username."""
    return session.execute(
        select(Admin.id, Admin.username)
        .where(Admin.id.in_(_any_done().with_only_columns(LedgerTransaction.admin_id)))
        .order_by(func.lower(Admin.username), Admin.id)
    ).all()


def item_ids_matching(
    session: Session, matching: ColumnElement[bool], only_id: int | None
) -> set[int]:
    """The ids of the items `matching` finds, of `only_id`'s alone if
    given."""
    query = select(Item.id).where(matching)
    if only_id is not None:
        query = query.where(Item.id == only_id)
    return set(session.scalars(query))


def countable_items(session: Session) -> list[Item]:
    """What can be counted or adjusted (balance.countable_item), by name."""
    return by_name(session, Item, countable_item())


def countable_place(session: Session, location_id: int) -> Location | None:
    """The place, if it may be counted (balance.countable)."""
    return session.scalar(
        select(Location).where(Location.id == location_id, countable())
    )


# A place and every area inside it, each with its path below the place
# and whether it may be counted (place_tree).
PlaceTree = Sequence[tuple[Location, tuple[str, ...], bool]]


def place_tree(
    session: Session, root: Location
) -> list[tuple[Location, tuple[str, ...], bool]]:
    """A place and every area inside it, at any depth, each with its path
    below the place, the names of the areas it is in, then its own (none
    for the place), and whether it may be counted (balance.countable; the
    place always may). Depth first, each level by name: an area that may
    not be counted is walked through too, its name in the paths of the
    areas in it. One query, by a recursive CTE."""
    tree = select(Location.id).where(Location.id == root.id).cte("tree", recursive=True)
    tree = tree.union(select(Location.id).join(tree, Location.parent_id == tree.c.id))
    rows = session.execute(
        select(Location, countable()).where(Location.id.in_(select(tree.c.id)))
    ).all()
    children: dict[int | None, list[Location]] = {}
    may_count = {place.id for place, ok in rows if ok} | {root.id}
    for place, _ in rows:
        if place.id != root.id:
            children.setdefault(place.parent_id, []).append(place)
    found: list[tuple[Location, tuple[str, ...], bool]] = []

    def walk(place: Location, path: tuple[str, ...]) -> None:
        found.append((place, path, place.id in may_count))
        for child in sorted(
            children.get(place.id, []), key=lambda row: row.name.lower()
        ):
            walk(child, (*path, child.name))

    walk(root, ())
    return found


def place_label(place: Location, path: tuple[str, ...]) -> str:
    """How a count form names a place of its tree (place_tree): by its
    path, or for the page's own place by its name."""
    return PATH_BETWEEN.join(path) if path else place.name


def not_countable(session: Session, ids: Collection[int]) -> set[int]:
    """Of the places of `ids`, those that may not be counted now
    (balance.countable): retired, and holding nothing."""
    return set(
        session.scalars(
            select(Location.id).where(Location.id.in_(ids), ~countable())
        ).all()
    )


def packages_by_id(
    session: Session, item_ids: Collection[int]
) -> dict[int, ItemPackage]:
    """Every package of the items of `item_ids`, by id."""
    return {
        package.id: package
        for package in session.scalars(
            select(ItemPackage).where(ItemPackage.item_id.in_(item_ids))
        )
    }


def open_at(
    session: Session, place_ids: Collection[int], item_ids: Collection[int]
) -> Sequence[OpenPackage]:
    """The packages open at the places, of the items of `item_ids`."""
    return session.scalars(
        select(OpenPackage).where(
            OpenPackage.location_id.in_(list(place_ids)),
            OpenPackage.closed_at.is_(None),
            OpenPackage.item_id.in_(item_ids),
        )
    ).all()


def current_packages(
    session: Session,
) -> Sequence[Row[ItemPackage, str, str, bool]]:
    """Current packages of current items, each with its item's name, unit
    and whether it follows them once opened, by item name, then smallest
    first, ties by id."""
    return session.execute(
        select(ItemPackage, Item.name_shown, Item.unit, Item.track_opened)
        .join(Item, Item.id == ItemPackage.item_id)
        .where(ItemPackage.retired_at.is_(None), Item.retired_at.is_(None))
        .order_by(func.lower(Item.name), *SMALLEST_PACKAGE_FIRST)
    ).all()


def units_of(session: Session, item_ids: Collection[int]) -> dict[int, str]:
    """Each item's unit, by id, of the items of `item_ids`."""
    return values_by(session, Item.id, Item.unit, item_ids)


def recent_cuts(
    session: Session, open_ids: Collection[int], recent: int
) -> Sequence[Row[*tuple[Any, ...]]]:
    """What was last taken from each of `open_ids`' open packages, by
    package then newest first, `recent` of each, never a move of it whole
    nor what was left made loose:
    each with the package's id, the transaction's id, when, how much, its
    kind and its volunteer's name, if a volunteer made it."""
    newest = (
        select(
            LedgerTransaction.id,
            LedgerTransaction.created_at,
            Movement.quantity,
            LedgerTransaction.kind,
            Volunteer.name,
        )
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .outerjoin(Volunteer, Volunteer.id == LedgerTransaction.volunteer_id)
        .where(
            Movement.from_open_package_id == OpenPackage.id,
            # Not a move of it whole, which names it on both sides.
            Movement.to_open_package_id.is_distinct_from(OpenPackage.id),
            # Nor what was left made loose, which leaves it closed: one
            # read as open just before has no such cut to show (Ali,
            # 2026-11-24, inventory-tng-j89e). An opening takes from none.
            LedgerTransaction.kind.not_in(AT_ONE_PLACE),
        )
        .order_by(LedgerTransaction.id.desc())
        .limit(recent)
        .lateral()
    )
    return session.execute(
        select(OpenPackage.id, *newest.c)
        .join(newest, true())
        .where(OpenPackage.id.in_(open_ids))
        .order_by(OpenPackage.id, newest.c.id.desc())
    ).all()


def latest_transaction_id(session: Session) -> int:
    """The ledger's latest transaction's id, 0 if none."""
    return session.scalar(select(func.max(LedgerTransaction.id))) or 0


def volunteers_with_ids(
    session: Session, ids: Collection[int | None]
) -> Sequence[Volunteer]:
    """The volunteers of `ids`."""
    return session.scalars(select(Volunteer).where(Volunteer.id.in_(ids))).all()


def places_of_open(session: Session, open_ids: Collection[int]) -> set[int]:
    """The places of `open_ids`' open packages."""
    return set(
        session.scalars(
            select(OpenPackage.location_id).where(OpenPackage.id.in_(open_ids))
        )
    )


def openings_at(
    session: Session,
    place_ids: Collection[int],
    item_ids: Collection[int],
    after: int,
    up_to: int,
) -> Sequence[Row[int | None, int, int | None, int | None, Decimal]]:
    """The sealed packages of `item_ids` opened at the places in the ledger
    transactions after `after` up to `up_to`, the newest first: each
    line's place, item, package, the open package it made (None, opened
    loose) and amount."""
    return session.execute(
        select(
            LedgerTransaction.to_location_id,
            Movement.item_id,
            Movement.from_package_id,
            Movement.to_open_package_id,
            Movement.quantity,
        )
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(
            *_of_kind_at("open", place_ids, item_ids, after, up_to),
            Movement.from_package_id.is_not(None),
        )
        .order_by(LedgerTransaction.id.desc(), Movement.id.desc())
    ).all()


def loosened_at(
    session: Session,
    place_ids: Collection[int],
    item_ids: Collection[int],
    after: int,
    up_to: int,
) -> Sequence[Row[int | None, int, int | None, Decimal]]:
    """What was left on open packages of `item_ids` made loose units at
    the places (open_packages.close_unfollowed) in the ledger transactions
    after `after` up to `up_to`: each line's place, item, open package
    and amount. Never one left below zero, which took from loose units."""
    return session.execute(
        select(
            LedgerTransaction.to_location_id,
            Movement.item_id,
            Movement.from_open_package_id,
            Movement.quantity,
        )
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(
            *_of_kind_at("loosen", place_ids, item_ids, after, up_to),
            Movement.from_open_package_id.is_not(None),
        )
    ).all()


def _of_kind_at(
    kind: str,
    place_ids: Collection[int],
    item_ids: Collection[int],
    after: int,
    up_to: int,
) -> tuple[ColumnElement[bool], ...]:
    """Filters for the lines of `item_ids` in the ledger transactions of
    `kind` at the places after `after` up to `up_to`."""
    return (
        LedgerTransaction.kind == kind,
        LedgerTransaction.to_location_id.in_(list(place_ids)),
        LedgerTransaction.id > after,
        LedgerTransaction.id <= up_to,
        Movement.item_id.in_(list(item_ids)),
    )


def last_taken_from(
    session: Session,
    place_ids: Collection[int],
    item_ids: Collection[int],
    after: int,
    up_to: int,
) -> Sequence[Row[int | None, int, int | None, str, int | None]]:
    """Of `item_ids` at the places, the ledger line after `after` up to
    `up_to` that last took each from an open package there, or from loose
    units: its place, item, open package (None, loose), transaction kind,
    and the place it went to (None: installed, counted or written off)."""
    taken = (
        LedgerTransaction.from_location_id,
        Movement.item_id,
        Movement.from_open_package_id,
    )
    return session.execute(
        select(*taken, LedgerTransaction.kind, LedgerTransaction.to_location_id)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(
            LedgerTransaction.from_location_id.in_(list(place_ids)),
            LedgerTransaction.id > after,
            LedgerTransaction.id <= up_to,
            Movement.item_id.in_(list(item_ids)),
            Movement.from_package_id.is_(None),
        )
        .ext(distinct_on(*taken))
        .order_by(*taken, LedgerTransaction.id.desc(), Movement.id.desc())
    ).all()


def open_with_ids(
    session: Session, open_ids: Collection[int], *where: ColumnElement[bool]
) -> Sequence[OpenPackage]:
    """The open packages of `open_ids` that `where` picks."""
    return session.scalars(
        select(OpenPackage).where(OpenPackage.id.in_(open_ids), *where)
    ).all()


def item_moved(session: Session, movement_id: int) -> tuple[int, str]:
    """The item a ledger line moved, and its unit."""
    item_id, unit = session.execute(
        select(Item.id, Item.unit)
        .join(Movement, Movement.item_id == Item.id)
        .where(Movement.id == movement_id)
    ).one()
    return item_id, unit


def item_total(session: Session, item_id: int) -> Decimal:
    """How much of an item there is, everywhere together."""
    total = session.scalar(
        select(func.sum(balance.c.quantity)).where(balance.c.item_id == item_id)
    )
    return Decimal(total or 0)
