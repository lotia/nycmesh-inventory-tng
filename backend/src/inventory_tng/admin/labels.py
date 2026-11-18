"""Labels, from the browser (docs/decisions/0015): find (a Listing, with
"Current · Inactive · All"), reprint and revoke them here; make them from
an item's or a place's page. Labels print twelve to a page, cut apart or on
sticker paper. Printing is a page to open, not a change, so a sheet can be
printed again from its address."""

from typing import Annotated, Any, cast

import structlog
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import ColumnElement, func

from inventory_tng.admin.common import (
    LABELS,
    SECTION_LABELS,
    admin_page,
    now,
    revoke_title,
)
from inventory_tng.admin.listing import AskedView, Listing, Searched, Sort, Sorting
from inventory_tng.admin.locations import LOCATIONS
from inventory_tng.admins.accounts import qr_svg
from inventory_tng.admins.guards import AdminSignedIn
from inventory_tng.catalogue.items_routes import ITEMS
from inventory_tng.core.db import DbSession
from inventory_tng.core.local_time import site_zone
from inventory_tng.core.phrases import gettext, lazy, lazy_gettext, ngettext
from inventory_tng.labels import labels, queries
from inventory_tng.models import (
    INTEGER_MAX,
    Item,
    ItemPackage,
    Label,
    Location,
    whole,
)
from inventory_tng.pages import NOTICES, not_found, redirect, templates

log = structlog.get_logger(__name__)

# For _labels.html, on an item's or a place's page.
_globals = cast(dict[str, Any], templates.env.globals)
_globals["labels_path"] = LABELS
_globals["max_copies"] = labels.MAX_COPIES

router = APIRouter(include_in_schema=False)

PRINT = f"{LABELS}/print"
PER_PAGE = 12  # Three across, four down: styles.css.

LABEL_REVOKED = lazy_gettext(
    "Revoked: its copies now scan as a label the inventory doesn't have. "
    "Making labels for it again makes a new code."
)
PICK_LABELS = lazy_gettext("Pick a current label to print.")


def _too_many() -> str:
    return ngettext(
        "Print up to %(num)d label at once.",
        "Print up to %(num)d labels at once.",
        labels.MAX_LABELS,
    )


def _copies() -> str:
    return gettext("Print from 1 to %(max)d copies of each.", max=labels.MAX_COPIES)


TOO_MANY = lazy(_too_many)
COPIES = lazy(_copies)
PRINT_TITLE = lazy_gettext("Print labels")
NOTICES.update(
    {
        "label_revoked": LABEL_REVOKED,
        "pick_labels": PICK_LABELS,
        "too_many": TOO_MANY,
        "copies": COPIES,
    }
)


# What a label is called in a list: its place's name, or its item's.
_NAME = func.coalesce(Location.name, Item.name)

# How the labels list sorts: by what it names, as it always has, or by a
# heading, made newest first (Ali, 2026-11-15, inventory-tng-qwb).
SORTING = Sorting(
    "names",
    {
        "code": Sort.of(Label.code),
        "names": Sort.of(func.lower(_NAME)),
        "made": Sort.of(Label.created_at, first="desc"),
    },
)

# What the kind filter offers: labels of items, or of places.
KINDS = {"item": Label.item_id.is_not(None), "place": Label.location_id.is_not(None)}

# How the labels list finds one: by its whole code, or the words naming it,
# reading each label with what it names.
SEARCHED = Searched(
    LABELS,
    Label,
    func.concat_ws(
        " ",
        Item.search_text,
        ItemPackage.search_text,
        Location.search_text,
        queries.parent.search_text,
    ),
    _NAME,
    query=queries.with_what_they_name,
    whole=Label.code,
    current=labels.scanned(),
    lapsed="inactive",
    shown=labels.named,
    sorting=SORTING,
)


def _listed(
    asked: AskedView,
    q: str = "",
    show: str = "",
    pick: str = "",
    kind: str = "",
    item: str = "",
) -> Listing[Label]:
    """The labels list as its query string asks for it, with its filters
    (Ali, 2026-11-15): items' or places' labels, and one item's; a kind not
    offered, or an item not a number, is no filter."""
    item_id = whole(item, 1, INTEGER_MAX)
    filters: dict[str, str] = {}
    where: list[ColumnElement[bool]] = []
    if kind in KINDS:
        filters["kind"] = kind
        where.append(KINDS[kind])
    if item_id is not None:
        filters["item"] = str(item_id)
        where.append(Label.item_id == item_id)
    return SEARCHED.read(
        q,
        show,
        pick,
        asked,
        filters=filters,
        where=where,
    )


Listed = Annotated[Listing[Label], Depends(_listed)]


@router.get(LABELS)
def list_labels(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    listed: Listed,
) -> Response:
    listing = listed.find(session)
    # The item filter offers the items that have labels.
    labelled = queries.items_labelled(session)
    return admin_page(
        request,
        signed,
        "labels.html",
        {
            "title": SECTION_LABELS,
            "here": LABELS,
            "print_path": PRINT,
            "items_path": ITEMS,
            "locations_path": LOCATIONS,
            "listing": listing,
            "labelled": labelled,
            "zone": site_zone(session),
        },
    )


@router.get(f"{LABELS}/suggest")
def suggest_labels(
    session: DbSession, signed: AdminSignedIn, listing: Listed
) -> JSONResponse:
    """What the search box offers as one types (_listing.html)."""
    offered = listing.suggest(session, lambda label: label.code)
    return JSONResponse(offered, headers={"Cache-Control": "no-store"})


@router.post(LABELS)
def make_labels(
    signed: AdminSignedIn,
    session: DbSession,
    item_id: Annotated[int | None, Form()] = None,
    package_id: Annotated[int | None, Form()] = None,
    location_id: Annotated[int | None, Form()] = None,
    copies: Annotated[str, Form()] = "",
) -> Response:
    """Makes the code if it has none, then opens its sheet to print."""
    count = whole(copies, 1, labels.MAX_COPIES)
    if count is None:
        return redirect(LABELS, notice="copies")
    if item_id is not None and location_id is None:
        label = labels.make_for_item(session, item_id, package_id)
    elif location_id is not None and item_id is None and package_id is None:
        label = labels.make_for_place(session, location_id)
    else:
        label = None
    if label is None:
        return not_found()
    session.commit()
    return redirect(PRINT, label=str(label.id), copies=str(count))


@router.get(PRINT)
def print_labels(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    listing: Listed,
    label: Annotated[list[int] | None, Query()] = None,
    copies: str = "1",
) -> Response:
    # A refusal goes back to the list as it was, picked from (labels.html).
    back = listing.link()
    count = whole(copies, 1, labels.MAX_COPIES)
    if count is None:
        return redirect(back, notice="copies")
    picked = labels.printable(session, label or [])
    if not picked:
        return redirect(back, notice="pick_labels")
    if len(picked) * count > labels.MAX_LABELS:
        return redirect(back, notice="too_many")
    total = labels.record_printing(picked, count)
    return admin_page(
        request,
        signed,
        "labels_print.html",
        {
            "title": PRINT_TITLE,
            "back_link": (LABELS, SECTION_LABELS),
            "picked": picked,
            "copies": count,
            "total": total,
            "sheets": labels.lay_out(picked, count, PER_PAGE),
            # The camera reads a scuffed label at "q": a quarter can be lost.
            "qrs": {each.label.id: qr_svg(each.label.code, "q") for each in picked},
        },
    )


@router.get(f"{LABELS}/{{label_id}}/revoke")
def confirm_revoke(
    request: Request, signed: AdminSignedIn, session: DbSession, label_id: int
) -> Response:
    """Revoking can't be undone and voids every printed copy: say so first."""
    found = session.get(Label, label_id)
    if found is None or found.revoked_at is not None:
        return not_found()
    (named,) = labels.named(session, [found])
    return admin_page(
        request,
        signed,
        "label_revoke.html",
        {
            "title": revoke_title(found.code),
            "here": f"{LABELS}/{label_id}/revoke",
            "keep": LABELS,
            "named": named,
        },
    )


@router.post(f"{LABELS}/{{label_id}}/revoke")
def revoke_label(signed: AdminSignedIn, session: DbSession, label_id: int) -> Response:
    if not labels.revoke(session, label_id, now()):
        return not_found()
    session.commit()
    return redirect(LABELS, notice="label_revoked")
