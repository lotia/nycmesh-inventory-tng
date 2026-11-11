"""Labels, from the browser (docs/decisions/0015): find (a Listing, with
"Current · Inactive · All"), reprint and revoke them here; make them from an item's or a place's page. Labels print twelve
to a page, cut apart or on sticker paper. Printing is a page to open, not a
change, so a sheet can be printed again from its address."""

from collections.abc import Sequence
from typing import Annotated, Any, cast

import structlog
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from inventory_tng import labels
from inventory_tng.accounts import qr_svg
from inventory_tng.admin.common import LABELS, admin_page, now
from inventory_tng.admin.items import ITEMS
from inventory_tng.admin.listing import Listing, Searched
from inventory_tng.admin.locations import LOCATIONS
from inventory_tng.db import DbSession
from inventory_tng.local_time import site_zone
from inventory_tng.models import Item, ItemPackage, Label, Location, whole
from inventory_tng.pages import NOTICES, not_found, redirect, templates
from inventory_tng.sign_in import AdminSignedIn

log = structlog.get_logger(__name__)

# For _labels.html, on an item's or a place's page.
_globals = cast(dict[str, Any], templates.env.globals)
_globals["labels_path"] = LABELS
_globals["max_copies"] = labels.MAX_COPIES

router = APIRouter(include_in_schema=False)

PRINT = f"{LABELS}/print"
PER_PAGE = 12  # Three across, four down: styles.css.

NOTICES.update(
    {
        "label_revoked": "Revoked: its copies now scan as a label the inventory "
        "doesn't have. Making labels for it again makes a new code.",
        "pick_labels": "Pick a current label to print.",
        "too_many": f"Print up to {labels.MAX_LABELS} labels at once.",
        "copies": f"Print from 1 to {labels.MAX_COPIES} copies of each.",
    }
)


# Each label with what it names, and the place around a place: what the
# list's search, filter and order read.
_parent = aliased(Location)
LISTED = (
    select(Label)
    .outerjoin(Item, Item.id == Label.item_id)
    .outerjoin(ItemPackage, ItemPackage.id == Label.package_id)
    .outerjoin(Location, Location.id == Label.location_id)
    .outerjoin(_parent, _parent.id == Location.parent_id)
)


def _words(session: Session, rows: Sequence[Label]) -> list[str]:
    return [each.words for each in labels.named(session, rows)]


# How the labels list finds one: by its code, or the words naming it.
SEARCHED = Searched(
    Label,
    func.concat_ws(
        " ", Label.code, Item.search_text, ItemPackage.name, Location.name, _parent.name
    ),
    func.coalesce(Location.name, Item.name),
    current=labels.scanned(),
    words=_words,
)


@router.get(LABELS)
def list_labels(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    q: str = "",
    show: str = "",
    page: str = "",
    pick: str = "",
) -> Response:
    listing = Listing[Label].read(LABELS, q, show, page, pick, lapsed="inactive")
    listing = listing.find(session, SEARCHED, LISTED)
    return admin_page(
        request,
        signed,
        "labels.html",
        {
            "title": "Labels",
            "here": LABELS,
            "print_path": PRINT,
            "items_path": ITEMS,
            "locations_path": LOCATIONS,
            "listing": listing,
            "shown": labels.named(session, listing.rows),
            "zone": site_zone(session),
        },
    )


@router.get(f"{LABELS}/suggest")
def suggest_labels(
    session: DbSession, signed: AdminSignedIn, q: str = "", show: str = ""
) -> JSONResponse:
    """What the search box offers as one types (_listing.html)."""
    listing = Listing[Label].read(LABELS, q, show, lapsed="inactive")
    offered = listing.suggest(session, SEARCHED, LISTED, lambda label: label.code)
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
    label: Annotated[list[int] | None, Query()] = None,
    copies: str = "1",
) -> Response:
    count = whole(copies, 1, labels.MAX_COPIES)
    if count is None:
        return redirect(LABELS, notice="copies")
    picked = labels.printable(session, label or [])
    if not picked:
        return redirect(LABELS, notice="pick_labels")
    if len(picked) * count > labels.MAX_LABELS:
        return redirect(LABELS, notice="too_many")
    total = labels.record_printing(picked, count)
    return admin_page(
        request,
        signed,
        "labels_print.html",
        {
            "title": "Print labels",
            "back_link": (LABELS, "Labels"),
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
            "title": f"Revoke {found.code}?",
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
