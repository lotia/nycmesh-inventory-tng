"""What the stock pages read (docs/decisions/0023): plain functions taking
a Session, each named for what it returns. They lock nothing and refuse
nothing; the balance itself is stock/balance.py's."""

from collections.abc import Collection, Sequence
from decimal import Decimal

from sqlalchemy import ColumnElement, any_, func, or_, select
from sqlalchemy.orm import Session

from inventory_tng import open_packages
from inventory_tng.models import STORE_KINDS, Item, ItemPackage, Location
from inventory_tng.stock.balance import balance, by_form


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
