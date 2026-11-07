"""What is where, from the ledger's balance view (docs/decisions/0003):
read-only, by place or by item; what to reorder; and what needs counting. A
negative balance means the records are wrong somewhere, so it is shown as
needing a count (inventory-tng-8i4.15)."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, cast

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from inventory_tng.admin.common import ADMIN, admin_page
from inventory_tng.admin.counts import COUNTS
from inventory_tng.admin.items import ITEMS
from inventory_tng.db import DbSession
from inventory_tng.ledger import SHORT, balance, in_stores
from inventory_tng.models import Item, Location
from inventory_tng.pages import templates
from inventory_tng.sign_in import AdminSignedIn

router = APIRouter(include_in_schema=False)

STOCK = f"{ADMIN}/stock"
REORDER = f"{STOCK}/reorder"
NEEDS_COUNTING = f"{STOCK}/needs-counting"

# The stock pages' views, for the links between them (_stock_views.html).
_globals = cast(dict[str, Any], templates.env.globals)
_globals["stock_views"] = (
    (STOCK, "By place", "place"),
    (f"{STOCK}?by=item", "By item", "item"),
    (REORDER, "Reorder", "reorder"),
    (NEEDS_COUNTING, "Needs counting", "needs-counting"),
)
_globals["needs_counting_path"] = NEEDS_COUNTING


@dataclass
class Holding:
    """One item at one place."""

    item: Item
    location: Location
    quantity: Decimal


@dataclass
class Group:
    """A place's items, or an item's places, with their total."""

    heading: Location | Item
    rows: list[Holding] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((row.quantity for row in self.rows), Decimal(0))

    @property
    def needs_count(self) -> bool:
        return any(row.quantity < 0 for row in self.rows)


def _holdings(session: Session, where: ColumnElement[bool]) -> list[Holding]:
    """The balances `where` picks, by place then item, as people sort them."""
    return [
        Holding(item, location, quantity)
        for item, location, quantity in session.execute(
            select(Item, Location, balance.c.quantity)
            .join(Item, Item.id == balance.c.item_id)
            .join(Location, Location.id == balance.c.location_id)
            .where(where)
            .order_by(func.lower(Location.name), func.lower(Item.name))
        )
    ]


def _grouped(holdings: list[Holding], by: Literal["place", "item"]) -> list[Group]:
    groups: dict[int, Group] = {}
    for holding in holdings:
        heading = holding.location if by == "place" else holding.item
        groups.setdefault(heading.id, Group(heading)).rows.append(holding)
    return sorted(groups.values(), key=lambda group: group.heading.name.lower())


@router.get(STOCK)
def stock(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    by: Literal["place", "item"] = "place",
) -> Response:
    groups = _grouped(_holdings(session, balance.c.quantity != 0), by)
    return admin_page(
        request,
        signed,
        "stock.html",
        {
            "title": "Stock",
            "view": by,
            "groups": groups,
            "needing_count": sum(group.needs_count for group in groups),
        },
    )


@dataclass
class Reorder:
    item: Item
    in_stores: Decimal
    # Less than nothing somewhere in the stores: the total is not to be
    # trusted yet.
    count_first: bool


@router.get(REORDER)
def reorder(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    """Current items with a reorder level, at or below it across the
    stores (ledger.in_stores)."""
    stores = in_stores(
        select(Item.id).where(Item.retired_at.is_(None), Item.minimum.is_not(None))
    ).subquery()
    total = func.coalesce(stores.c.total, 0)
    rows = [
        Reorder(item, in_stock, bool(short))
        for item, in_stock, short in session.execute(
            select(Item, total, stores.c.short)
            .outerjoin(stores, stores.c.item_id == Item.id)
            .where(
                Item.retired_at.is_(None),
                Item.minimum.is_not(None),
                total <= Item.minimum,
            )
            .order_by(func.lower(Item.name))
        )
    ]
    return admin_page(
        request,
        signed,
        "reorder.html",
        {
            "title": "Reorder",
            "view": "reorder",
            "rows": rows,
            "any_short": any(row.count_first for row in rows),
            "items_path": ITEMS,
        },
    )


@router.get(NEEDS_COUNTING)
def needs_counting(
    request: Request, signed: AdminSignedIn, session: DbSession
) -> Response:
    """Every place that shows less than nothing of an item, with what it is
    short of, to count."""
    return admin_page(
        request,
        signed,
        "needs_counting.html",
        {
            "title": "Needs counting",
            "view": "needs-counting",
            "places": _grouped(_holdings(session, SHORT), "place"),
            "counts": COUNTS,
        },
    )
