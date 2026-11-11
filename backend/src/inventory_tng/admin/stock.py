"""What is where, from the ledger's balance (docs/decisions/0003):
read-only, by place or by item; what to reorder; and what needs counting. A
negative balance means the records are wrong somewhere, so it is shown as
needing a count (inventory-tng-8i4.15). Any stock at a retired place is
warned of, with a link to count it."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, cast

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import ColumnElement, func, select, true
from sqlalchemy.orm import Session

from inventory_tng import open_packages
from inventory_tng.admin.common import ADMIN, admin_page
from inventory_tng.admin.counts import COUNTS
from inventory_tng.admin.items import ITEMS
from inventory_tng.db import DbSession
from inventory_tng.ledger import (
    SHORT,
    balance,
    by_form,
    in_stores,
    retired_places_holding_stock,
)
from inventory_tng.models import STORE_KINDS, Item, ItemPackage, Location
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
            # Each line's forms, where it has more than loose units.
            "forms": open_packages.forms_of(session, true()),
            "needing_count": sum(group.needs_count for group in groups),
            "retired_places": retired_places_holding_stock(session),
            "counts_path": COUNTS,
        },
    )


@dataclass
class Reorder:
    item: Item
    # Usable in the stores: all but short ends (docs/decisions/0020).
    in_stores: Decimal
    # Less than nothing somewhere in the stores: the total is not to be
    # trusted yet.
    count_first: bool
    # Packages with fewer sealed in the stores than their minimum: name,
    # how many, the minimum.
    low_sealed: list[tuple[str, Decimal, int]] = field(default_factory=list)


def reorder_rows(
    items: Sequence[Item],
    usable: Mapping[int, Decimal],
    sealed: Mapping[int, Mapping[int, Decimal]],
    packages: Sequence[ItemPackage],
    short: Collection[int],
) -> list[Reorder]:
    """The items to reorder: usable stock at or below the item's reorder
    point, or a package's sealed below its minimum. `usable` and `sealed`
    (by item, then package) are in the stores; `short`, items less than
    nothing somewhere there."""
    minimums: dict[int, list[ItemPackage]] = {}
    for package in packages:
        if package.minimum_sealed is not None and package.retired_at is None:
            minimums.setdefault(package.item_id, []).append(package)
    rows = []
    for item in items:
        in_stock = usable.get(item.id, Decimal(0))
        low = [
            (package.name, count, package.minimum_sealed or 0)
            for package in minimums.get(item.id, [])
            if (count := sealed.get(item.id, {}).get(package.id, Decimal(0)))
            < (package.minimum_sealed or 0)
        ]
        if (item.minimum is not None and in_stock <= item.minimum) or low:
            rows.append(Reorder(item, in_stock, item.id in short, low))
    return rows


@router.get(REORDER)
def reorder(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    """Current items with a reorder level, at or below it in usable stock
    across the stores, or with a package's sealed below its minimum."""
    items = session.scalars(
        select(Item).where(Item.retired_at.is_(None)).order_by(func.lower(Item.name))
    ).all()
    stores = select(Location.id).where(
        Location.kind.in_(STORE_KINDS), Location.retired_at.is_(None)
    )
    usable: dict[int, Decimal] = {}
    sealed: dict[int, dict[int, Decimal]] = {}
    for (item_id, _), held in open_packages.forms_of(
        session, by_form.c.location_id.in_(stores)
    ).items():
        usable[item_id] = usable.get(item_id, Decimal(0)) + held.usable
        for package_id, count in held.sealed_by_package.items():
            counts = sealed.setdefault(item_id, {})
            counts[package_id] = counts.get(package_id, Decimal(0)) + count
    short = {
        item_id
        for item_id, _, is_short in session.execute(
            in_stores(select(Item.id).where(Item.retired_at.is_(None)))
        )
        if is_short
    }
    packages = session.scalars(select(ItemPackage)).all()
    rows = reorder_rows(items, usable, sealed, packages, short)
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
            "counts_path": COUNTS,
        },
    )
