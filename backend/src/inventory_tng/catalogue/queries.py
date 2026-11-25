"""What the catalogue's pages read (categories_routes.py,
items_routes.py, reading_rules_routes.py, and admins/home_routes.py's
counts of its tables): plain functions taking a Session, each named for what it
returns (docs/decisions/0023)."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal, NamedTuple

from sqlalchemy import ColumnElement, Row, and_, func, select
from sqlalchemy.orm import Session

from inventory_tng.catalogue.unit_changes import UnitFixed
from inventory_tng.catalogue.unit_conversion import converted_kept
from inventory_tng.models.admins import Admin
from inventory_tng.models.catalogue import (
    SMALLEST_PACKAGE_FIRST,
    Category,
    Item,
    ItemPackage,
    ReadingRule,
    VendorOffer,
)
from inventory_tng.models.places import Location
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.stock.balance import balance
from inventory_tng.stock.queries import balances

# Each item's category's name, for a select of items to sort by.
category_name = (
    select(Category.name).where(Category.id == Item.category_id).scalar_subquery()
)


def current_items_by_category(session: Session) -> dict[int, int]:
    """How many current items each category has."""
    rows = session.execute(
        select(Item.category_id, func.count())
        .where(Item.retired_at.is_(None))
        .group_by(Item.category_id)
    )
    return {category_id: count for category_id, count in rows}


def categories_by_name(session: Session) -> Sequence[Category]:
    """Every category, retired too, by name ignoring case."""
    return session.scalars(select(Category).order_by(func.lower(Category.name))).all()


def current_items_of(session: Session, category_id: int) -> Sequence[Item]:
    """A category's current items, by name ignoring case."""
    return session.scalars(
        select(Item)
        .where(Item.category_id == category_id, Item.retired_at.is_(None))
        .order_by(func.lower(Item.name))
    ).all()


def packages_of_item(session: Session, item_id: int) -> Sequence[ItemPackage]:
    """An item's packages, retired too, the current ones first, then
    smallest first, ties by id."""
    return session.scalars(
        select(ItemPackage)
        .where(ItemPackage.item_id == item_id)
        .order_by(ItemPackage.retired_at.is_not(None), *SMALLEST_PACKAGE_FIRST)
    ).all()


def rules_with_deciders(
    session: Session, *where: ColumnElement[bool]
) -> Sequence[Row[ReadingRule, str]]:
    """The reading rules `where` picks, each with the username of the
    administrator who settled it, None for one the import settled (an
    outer join, which the row's type does not say)."""
    return session.execute(
        select(ReadingRule, Admin.username)
        .outerjoin(Admin, Admin.id == ReadingRule.decided_by_admin_id)
        .where(*where)
    ).all()


def current_count(
    session: Session, model: type[Item | Category | Location | Volunteer]
) -> int:
    """How many current rows a catalogue table holds."""
    return (
        session.scalar(
            select(func.count()).select_from(model).where(model.retired_at.is_(None))
        )
        or 0
    )


# What an example of a change is: the stock at a place, or a stored
# amount.
ExampleOf = Literal["place", "minimum", "on_order", "offer"]


class UnitChangeExample(NamedTuple):
    """One amount the item records, `amount` in its unit, and `becomes`,
    what a change into another length would make it; at `place`, by
    name, if it is stock."""

    of: ExampleOf
    amount: Decimal
    becomes: Decimal
    place: str | None = None


def unit_change_example(
    session: Session, item: Item, new: str, why: UnitFixed
) -> UnitChangeExample | None:
    """What a change of the item into `new` would recalculate, with its
    own numbers, for the note after No (Ali, 2026-11-25,
    inventory-tng-aknv): stock moved (`why`), the balance at the first
    place holding any, in the stock pages' order (stock/queries.balances);
    else a stored amount not nothing: its Reorder at, what is on order,
    or its first current vendor offer's units per order in its unit.
    None if it has none."""
    if why == "moved":
        held = balances(
            session, and_(balance.c.item_id == item.id, balance.c.quantity > 0)
        )
        if not held:
            return None
        _, place, quantity = held[0]
        return UnitChangeExample(
            "place", quantity, converted_kept(quantity, item.unit, new), place.name
        )
    offered = session.scalar(
        select(VendorOffer.units_per_order)
        .where(
            VendorOffer.item_id == item.id,
            VendorOffer.retired_at.is_(None),
            VendorOffer.unit == item.unit,
            VendorOffer.units_per_order > 0,
        )
        .order_by(VendorOffer.id)
        .limit(1)
    )
    stored: list[tuple[ExampleOf, Decimal | None]] = [
        ("minimum", item.minimum),
        ("on_order", item.on_order),
        ("offer", offered),
    ]
    for of, amount in stored:
        if amount:
            return UnitChangeExample(of, amount, converted_kept(amount, item.unit, new))
    return None
