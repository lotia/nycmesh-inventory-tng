"""What is where, from the ledger's balance (docs/decisions/0003):
read-only, by place or by item; what to reorder; and what needs counting. A
negative balance means the records are wrong somewhere, so it is shown as
needing a count (inventory-tng-8i4.15). Any stock at a retired place is
warned of, with a link to count it."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, cast

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import ColumnElement, true
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn
from inventory_tng.catalogue.items_listing import SEARCHED
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.core.db import DbSession
from inventory_tng.core.phrases import Words, gettext, lazy_gettext
from inventory_tng.models import INTEGER_MAX, Item, ItemPackage, Location, whole
from inventory_tng.stock import open_packages, over_recorded, queries
from inventory_tng.stock.balance import SHORT, retired_places_holding_stock
from inventory_tng.stock.paths import (
    COUNTS,
    NEEDS_COUNTING,
    RECORDED_MORE,
    RECORDED_MORE_SETTLED,
    REORDER,
    STOCK,
)
from inventory_tng.web.admin import SETTLED_VIEW, admin_page
from inventory_tng.web.listing import (
    AskedView,
    Sort,
    Sorting,
    View,
    arranged,
    in_order,
    paged,
)
from inventory_tng.web.navigation import SECTION_STOCK
from inventory_tng.web.pages import templates

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

_globals = cast(dict[str, Any], templates.env.globals)
_globals["needs_counting_path"] = NEEDS_COUNTING


BY_PLACE = lazy_gettext("By place")
BY_ITEM = lazy_gettext("By item")
REORDER_TITLE = lazy_gettext("Reorder")
NEEDS_COUNTING_TITLE = lazy_gettext("Needs counting")


def recorded_more_view(waiting: int) -> str:
    return gettext("Recorded more (%(count)d)", count=waiting)


def stock_views(session: Session) -> list[tuple[str, Words, str]]:
    """The stock pages' views, for the links between them (_views.html):
    by path, words and name. Recorded more says how many wait, even none
    (Ali, 2026-11-15, inventory-tng-73p)."""
    return [
        (STOCK, BY_PLACE, "place"),
        (f"{STOCK}?by=item", BY_ITEM, "item"),
        (REORDER, REORDER_TITLE, "reorder"),
        (NEEDS_COUNTING, NEEDS_COUNTING_TITLE, "needs-counting"),
        (
            RECORDED_MORE,
            recorded_more_view(over_recorded.how_many(session, over_recorded.WAITING)),
            "recorded-more",
        ),
        (RECORDED_MORE_SETTLED, SETTLED_VIEW, "settled"),
    ]


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
    """A place's items, or an item's places."""

    heading: Location | Item
    rows: list[Holding] = field(default_factory=list)


def _holdings(session: Session, where: ColumnElement[bool]) -> list[Holding]:
    """The balances `where` picks, by place then item, as people sort them."""
    return [
        Holding(item, location, quantity)
        for item, location, quantity in queries.balances(session, where)
    ]


def _grouped(holdings: list[Holding], by: Literal["place", "item"]) -> list[Group]:
    groups: dict[int, Group] = {}
    for holding in holdings:
        heading = holding.location if by == "place" else holding.item
        groups.setdefault(heading.id, Group(heading)).rows.append(holding)
    return sorted(groups.values(), key=lambda group: group.heading.name.lower())


# How the rows within each group sort: by the item's or the place's name,
# as they always have, or by how much; the groups stay in name order (Ali,
# 2026-11-15, inventory-tng-qwb).
_QUANTITY = Sort.keyed(lambda row: row.quantity)
STOCK_SORTING = {
    "place": Sorting(
        "item",
        {"item": Sort.keyed(lambda row: row.item.name.lower()), "quantity": _QUANTITY},
    ),
    "item": Sorting(
        "place",
        {
            "place": Sort.keyed(lambda row: row.location.name.lower()),
            "quantity": _QUANTITY,
        },
    ),
}


@router.get(STOCK)
def stock(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    asked: AskedView,
    by: Literal["place", "item"] = "place",
    q: str = "",
    place: str = "",
) -> Response:
    """Every item at every place it is, each form on its own line, from one
    read of the ledger by form (open_packages.forms_of); the items a search
    finds, at one place, if asked (Ali, 2026-11-15). Page size counts
    groups."""
    # An odd search is none, as the items list reads it.
    search = SEARCHED.read(q=q).search
    place_id = whole(place, 1, INTEGER_MAX)
    filters = {"q": search, "place": str(place_id or "")}
    view = View.read(
        STOCK,
        STOCK_SORTING[by],
        asked,
        filters=filters,
        kept={"by": by} if by == "item" else None,
    )
    forms = open_packages.forms_of(session, true())
    matching = SEARCHED.matching(search, SEARCHED.rules(session)) if search else None
    items = {
        item.id: item
        for item in queries.items_with_ids(
            session,
            {item_id for item_id, spot in forms if place_id in (None, spot)},
            matching,
        )
    }
    places = {
        spot.id: spot
        for spot in queries.places_with_ids(session, {spot for _, spot in forms})
    }
    holdings = [
        Holding(items[item_id], places[spot], held.total, held.negative)
        for (item_id, spot), held in forms.items()
        if item_id in items and place_id in (None, spot)
    ]
    # The whole stock's, whatever the filter shows: each item's in all, and
    # the places, or items, short somewhere.
    in_all: dict[int, Decimal] = {}
    short: set[int] = set()
    for (item_id, spot), held in forms.items():
        in_all[item_id] = in_all.get(item_id, Decimal(0)) + held.total
        if held.total < 0 or held.negative:
            short.add(item_id if by == "item" else spot)
    groups = _grouped(sorted(holdings, key=_by_names), by)
    shown, view = paged(groups, view)
    for group in shown:
        group.rows = in_order(group.rows, view)
    log.debug("stock listed", by=by, found=view.found, sort=view.by, page=view.page)
    return admin_page(
        request,
        signed,
        "stock.html",
        {
            "title": SECTION_STOCK,
            "by": by,
            "view": view,
            "stock_views": stock_views(session),
            "groups": shown,
            "forms": forms,
            "places": sorted(places.values(), key=lambda each: each.name.lower()),
            "in_all": in_all,
            "needing_count": len(short),
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
            (package.name_shown, count, minimum)
            for package in minimums.get(item.id, [])
            if (minimum := package.minimum_sealed) is not None
            and (count := sealed.get(item.id, {}).get(package.id, Decimal(0))) < minimum
        ]
        if (item.minimum is not None and in_stock <= item.minimum) or low:
            rows.append(Reorder(item, in_stock, item.id in short, low))
    return rows


# How the reorder list sorts: by name, as it always has, or by a heading;
# those with no level to reorder at after the rest, or before them the
# other way, as the database sorts a blank (Ali, 2026-11-15,
# inventory-tng-qwb).
REORDER_SORTING = Sorting(
    "item",
    {
        "item": Sort.keyed(lambda row: row.item.name.lower()),
        "usable": Sort.keyed(lambda row: row.in_stores),
        "reorder_at": Sort.keyed(
            lambda row: (row.item.minimum is None, row.item.minimum or 0)
        ),
    },
)


@router.get(REORDER)
def reorder(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    asked: AskedView,
) -> Response:
    """Current items with a reorder level, at or below it in usable stock
    across the stores, or with a package's sealed below its minimum."""
    view = View.read(REORDER, REORDER_SORTING, asked)
    packages = queries.packages_with_minimum(session)
    items = queries.items_to_reorder(session, [package.item_id for package in packages])
    usable: dict[int, Decimal] = {}
    sealed: dict[int, dict[int, Decimal]] = {}
    short: set[int] = set()
    for (item_id, _), held in queries.forms_in_stores(session).items():
        usable[item_id] = usable.get(item_id, Decimal(0)) + held.usable
        if held.total < 0:
            short.add(item_id)
        for package_id, count in held.sealed_by_package.items():
            counts = sealed.setdefault(item_id, {})
            counts[package_id] = counts.get(package_id, Decimal(0)) + count
    rows = reorder_rows(items, usable, sealed, packages, short)
    shown, view = arranged(rows, view)
    log.debug("reorder listed", found=view.found, sort=view.by, page=view.page)
    return admin_page(
        request,
        signed,
        "reorder.html",
        {
            "title": REORDER_TITLE,
            "stock_views": stock_views(session),
            "rows": shown,
            "view": view,
            "any_short": any(row.count_first for row in rows),
            "items_path": ITEMS,
        },
    )


# How the rows within each place needing a count sort: by item, as they
# always have, or by how much; the places stay in name order, and page size
# counts places (Ali, 2026-11-15, inventory-tng-qwb).
SHORT_SORTING = STOCK_SORTING["place"]


@router.get(NEEDS_COUNTING)
def needs_counting(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    asked: AskedView,
) -> Response:
    """Every place that shows less than nothing of an item, with what it is
    short of, to count."""
    view = View.read(NEEDS_COUNTING, SHORT_SORTING, asked)
    places, view = paged(_grouped(_holdings(session, SHORT), "place"), view)
    for group in places:
        group.rows = in_order(group.rows, view)
    log.debug("needs counting listed", found=view.found, sort=view.by, page=view.page)
    return admin_page(
        request,
        signed,
        "needs_counting.html",
        {
            "title": NEEDS_COUNTING_TITLE,
            "stock_views": stock_views(session),
            "places": places,
            "view": view,
            "counts_path": COUNTS,
        },
    )
