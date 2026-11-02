"""What is where, from the ledger's balance view (docs/decisions/0003):
read-only, by place or by item. A negative balance means the records are
wrong somewhere, so it is shown as needing a count."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from inventory_tng.admin.common import ADMIN, admin_page
from inventory_tng.db import DbSession
from inventory_tng.ledger import balance
from inventory_tng.models import Item, Location
from inventory_tng.sign_in import AdminSignedIn

router = APIRouter(include_in_schema=False)

STOCK = f"{ADMIN}/stock"


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


@router.get(STOCK)
def stock(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    by: Literal["place", "item"] = "place",
) -> Response:
    holdings = [
        Holding(item, location, quantity)
        for item, location, quantity in session.execute(
            select(Item, Location, balance.c.quantity)
            .join(Item, Item.id == balance.c.item_id)
            .join(Location, Location.id == balance.c.location_id)
            .where(balance.c.quantity != 0)
            .order_by(func.lower(Location.name), func.lower(Item.name))
        )
    ]
    groups: dict[int, Group] = {}
    for holding in holdings:
        heading = holding.location if by == "place" else holding.item
        groups.setdefault(heading.id, Group(heading)).rows.append(holding)
    ordered = sorted(groups.values(), key=lambda g: g.heading.name.lower())
    return admin_page(
        request,
        signed,
        "stock.html",
        {
            "title": "Stock",
            "by": by,
            "groups": ordered,
            "needing_count": sum(group.needs_count for group in ordered),
        },
    )
