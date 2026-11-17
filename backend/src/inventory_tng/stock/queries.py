"""What the stock pages read (docs/decisions/0023): plain functions taking
a Session, each named for what it returns. They lock nothing and refuse
nothing; the balance itself is stock/balance.py's."""

from collections.abc import Collection, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Row, Select, any_, func, or_, select
from sqlalchemy.orm import Session

from inventory_tng import open_packages
from inventory_tng.models import (
    STORE_KINDS,
    AdjustmentReason,
    Admin,
    Item,
    ItemPackage,
    LedgerTransaction,
    Location,
    Movement,
    OpenPackage,
)
from inventory_tng.stock.balance import balance, by_form, countable


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


def items_to_reorder_by(session: Session, item_ids: Collection[int]) -> Sequence[Item]:
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
) -> dict[tuple[int, int], open_packages.Forms]:
    """Stock in the current stores, by item and place, form by form
    (open_packages.forms_of)."""
    # The stores' ids as an array, so only their balances are summed, as
    # in_stores (stock/balance.py) does.
    stores = func.array(
        select(Location.id)
        .where(Location.kind.in_(STORE_KINDS), Location.retired_at.is_(None))
        .scalar_subquery()
    )
    return open_packages.forms_of(session, by_form.c.location_id == any_(stores))


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


def countable_place(session: Session, location_id: int) -> Location | None:
    """The place, if it may be counted (balance.countable)."""
    return session.scalar(
        select(Location).where(Location.id == location_id, countable())
    )


def packages_of(session: Session, item_ids: Collection[int]) -> Sequence[ItemPackage]:
    """Every package of the items of `item_ids`."""
    return session.scalars(
        select(ItemPackage).where(ItemPackage.item_id.in_(item_ids))
    ).all()


def open_at(
    session: Session, place_id: int, item_ids: Collection[int]
) -> Sequence[OpenPackage]:
    """The packages open at a place, of the items of `item_ids`."""
    return session.scalars(
        select(OpenPackage).where(
            OpenPackage.location_id == place_id,
            OpenPackage.closed_at.is_(None),
            OpenPackage.item_id.in_(item_ids),
        )
    ).all()


def current_packages(
    session: Session,
) -> Sequence[Row[ItemPackage, str, str, bool]]:
    """Current packages of current items, each with its item's name, unit
    and whether it follows them once opened, by item name then size."""
    return session.execute(
        select(ItemPackage, Item.name_shown, Item.unit, Item.track_opened)
        .join(Item, Item.id == ItemPackage.item_id)
        .where(ItemPackage.retired_at.is_(None), Item.retired_at.is_(None))
        .order_by(func.lower(Item.name), ItemPackage.size)
    ).all()


def units_of(session: Session, item_ids: Collection[int]) -> dict[int, str]:
    """Each item's unit, by id, of the items of `item_ids`."""
    return dict(
        session.execute(select(Item.id, Item.unit).where(Item.id.in_(item_ids))).all()
    )
