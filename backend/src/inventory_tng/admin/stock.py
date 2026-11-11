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
from sqlalchemy import ColumnElement, any_, func, or_, select, true
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
    # Less than nothing in some form, though the total may not be: the
    # records are wrong there too.
    negative: bool = False


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
        return any(row.quantity < 0 or row.negative for row in self.rows)


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
    """Every item at every place it is, each form on its own line, from one
    read of the ledger by form (open_packages.forms_of)."""
    forms = open_packages.forms_of(session, true())
    items = {
        item.id: item
        for item in session.scalars(
            select(Item).where(Item.id.in_({item_id for item_id, _ in forms}))
        )
    }
    places = {
        place.id: place
        for place in session.scalars(
            select(Location).where(Location.id.in_({place for _, place in forms}))
        )
    }
    holdings = [
        Holding(items[item_id], places[place], held.total, held.negative)
        for (item_id, place), held in forms.items()
    ]
    groups = _grouped(sorted(holdings, key=_by_names), by)
    return admin_page(
        request,
        signed,
        "stock.html",
        {
            "title": "Stock",
            "view": by,
            "groups": groups,
            "forms": forms,
            "needing_count": sum(group.needs_count for group in groups),
            "retired_places": retired_places_holding_stock(session),
            "counts_path": COUNTS,
        },
    )


def _by_names(holding: Holding) -> tuple[str, str]:
    """By place then item, as people sort them."""
    return holding.location.name.lower(), holding.item.name.lower()


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
            (package.name, count, minimum)
            for package in minimums.get(item.id, [])
            if (minimum := package.minimum_sealed) is not None
            and (count := sealed.get(item.id, {}).get(package.id, Decimal(0))) < minimum
        ]
        if (item.minimum is not None and in_stock <= item.minimum) or low:
            rows.append(Reorder(item, in_stock, item.id in short, low))
    return rows


@router.get(REORDER)
def reorder(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    """Current items with a reorder level, at or below it in usable stock
    across the stores, or with a package's sealed below its minimum."""
    packages = session.scalars(
        select(ItemPackage).where(
            ItemPackage.minimum_sealed.is_not(None), ItemPackage.retired_at.is_(None)
        )
    ).all()
    items = session.scalars(
        select(Item)
        .where(
            Item.retired_at.is_(None),
            or_(
                Item.minimum.is_not(None),
                Item.id.in_([package.item_id for package in packages]),
            ),
        )
        .order_by(func.lower(Item.name))
    ).all()
    # The stores' ids as an array, so only their balances are summed, as
    # ledger.in_stores does.
    stores = func.array(
        select(Location.id)
        .where(Location.kind.in_(STORE_KINDS), Location.retired_at.is_(None))
        .scalar_subquery()
    )
    usable: dict[int, Decimal] = {}
    sealed: dict[int, dict[int, Decimal]] = {}
    short: set[int] = set()
    for (item_id, _), held in open_packages.forms_of(
        session, by_form.c.location_id == any_(stores)
    ).items():
        usable[item_id] = usable.get(item_id, Decimal(0)) + held.usable
        if held.total < 0:
            short.add(item_id)
        for package_id, count in held.sealed_by_package.items():
            counts = sealed.setdefault(item_id, {})
            counts[package_id] = counts.get(package_id, Decimal(0)) + count
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
