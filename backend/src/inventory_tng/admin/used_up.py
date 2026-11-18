"""Open packages volunteers said are used up, for an administrator to
confirm or clear (docs/decisions/0020): "Needs your action" on the home
page leads here, for one item or all, one at a time or ticked together;
and closing one directly, from its item's page. Closing writes off what
it still showed, with the reason Used up (open_packages.close)."""

from collections.abc import Sequence
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from opentelemetry import metrics
from sqlalchemy.orm import Session

from inventory_tng.admin.common import (
    SECTION_STOCK,
    TICK_OPEN_PACKAGES,
    admin_page,
    now,
)
from inventory_tng.admin.listing import AskedView, Sort, Sorting, View, arranged
from inventory_tng.admin.needs_action import (
    Kind,
    OnlyItem,
    of_only,
    query,
)
from inventory_tng.admin.stock import STOCK
from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.catalogue.items_routes import ITEMS
from inventory_tng.core.db import DbSession, attempt
from inventory_tng.core.local_time import site_zone
from inventory_tng.core.phrases import gettext, lazy_gettext
from inventory_tng.models import OpenPackage
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.stock import ledger, open_packages, queries

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter(include_in_schema=False)

USED_UP = f"{STOCK}/used-up"

USED_UP_CONFIRMED = lazy_gettext("Closed: what they showed is written off as used up.")
USED_UP_CLEARED = lazy_gettext("Offered again: not used up.")
OPEN_PACKAGE_CLOSED = lazy_gettext("Closed: what it showed is written off as used up.")
USED_UP_UNCHANGED = lazy_gettext(
    "Nothing changed: it was answered meanwhile, moved, or its place can't be "
    "adjusted until counted."
)
NOTICES.update(
    {
        "used_up_confirmed": USED_UP_CONFIRMED,
        "used_up_cleared": USED_UP_CLEARED,
        "used_up_none": TICK_OPEN_PACKAGES,
        "open_package_closed": OPEN_PACKAGE_CLOSED,
        "used_up_unchanged": USED_UP_UNCHANGED,
    }
)


def close_title(package: str) -> str:
    return gettext("Close this %(package)s as used up?", package=package)


cleared = meter.create_counter(
    "inventory.open_packages.cleared",
    unit="{package}",
    description="Volunteers' reports of open packages used up that an "
    "administrator cleared, as not used up",
)


# Reported used up, and waiting.
REPORTED = (OpenPackage.reported_at.is_not(None), OpenPackage.closed_at.is_(None))


TITLE = lazy_gettext("Reported used up")
# As "Needs your action" lists it (needs_action.py).
KIND = Kind(TITLE, USED_UP, REPORTED)


# Open packages (open_packages.Shown) by their item, package and place, and
# by what each shows left: here and on the length checks.
BY_PACKAGE = Sort.keyed(
    lambda each: (each.item.lower(), each.package.lower(), each.place.lower())
)
BY_SHOWS = Sort.keyed(lambda each: each.left)
# How those reported sort: the first reported first, as they always have,
# or by a heading (Ali, 2026-11-15, inventory-tng-qwb).
SORTING = Sorting(
    "reported",
    {
        "package": BY_PACKAGE,
        "shows": BY_SHOWS,
        "reported": Sort.keyed(lambda each: each.row.reported_at),
    },
)


def _view(only: OnlyItem, asked: AskedView) -> View:
    """The list's item, sort, page size and page, as its query string asks:
    the page's, and each answer's, which comes back to it so."""
    return View.read(USED_UP, SORTING, asked, kept=query(only))


Viewed = Annotated[View, Depends(_view)]


@router.get(USED_UP)
def used_up_page(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    only: OnlyItem,
    view: Viewed,
) -> Response:
    """All those reported, or only one item's, sorted and in pages as
    asked."""
    reported, view = arranged(
        open_packages.shown(session, *of_only(REPORTED, only)), view
    )
    log.debug("used up listed", found=view.found, sort=view.by, page=view.page)
    return admin_page(
        request,
        signed,
        "used_up.html",
        {
            "title": TITLE,
            "here": USED_UP,
            "back_link": (STOCK, SECTION_STOCK),
            "only": only,
            "reported": reported,
            "view": view,
            "zone": site_zone(session),
        },
    )


Doing = Literal["confirm", "clear"]
DONE: dict[Doing, str] = {"confirm": "used_up_confirmed", "clear": "used_up_cleared"}


def _answer(
    session: Session,
    signed: SignedIn,
    ids: Sequence[int],
    do: str,
    back: View,
) -> Response:
    """Confirm, or clear, each of `ids` still reported, and say so. Their
    places locked first, by id, as saves take places, so none waits on
    another the wrong way round; then each closes or clears."""
    if do not in DONE:
        return not_found()
    answer: Doing = "confirm" if do == "confirm" else "clear"
    rows = queries.open_with_ids(session, list(ids), *REPORTED)
    ledger.lock_places(session, (row.location_id for row in rows))
    changed = 0
    closed: list[open_packages.Closed | None] = []
    for row in sorted(rows, key=lambda each: each.id):
        if answer == "clear":
            changed += open_packages.clear(session, row.id)
            continue
        done, _ = attempt(
            session,
            lambda row=row: open_packages.close(
                session, row.id, signed.admin.id, now(), reported=True
            ),
        )
        closed.append(done)
        changed += done is not None
    session.commit()
    if answer == "confirm":
        open_packages.measure_closing("confirmed", closed)
    elif changed:
        cleared.add(changed)
    log.info(
        "reports of used up answered",
        do=answer,
        changed=changed,
        admin_id=signed.admin.id,
    )
    return redirect(
        back.link(), notice=DONE[answer] if changed else "used_up_unchanged"
    )


@router.post(USED_UP)
def answer_ticked(
    signed: AdminSignedIn,
    session: DbSession,
    view: Viewed,
    open_package: Annotated[list[int], Form()] = [],  # noqa: B006  FastAPI copies it.
    do: Annotated[str, Form()] = "",
) -> Response:
    """Confirm, or clear, the open packages ticked."""
    if do in DONE and not open_package:
        return redirect(view.link(), notice="used_up_none")
    return _answer(session, signed, open_package, do, view)


@router.post(f"{USED_UP}/{{open_id}}/{{do}}")
def answer_one(
    signed: AdminSignedIn,
    session: DbSession,
    open_id: int,
    do: str,
    view: Viewed,
) -> Response:
    """Confirm, or clear, one."""
    if session.get(OpenPackage, open_id) is None:
        return not_found()
    return _answer(session, signed, [open_id], do, view)


CLOSE = f"{ITEMS}/{{item_id}}/open-packages/{{open_id}}/close"


def _closable(session: Session, item_id: int, open_id: int) -> OpenPackage | None:
    row = session.get(OpenPackage, open_id)
    if row is None or row.item_id != item_id or row.closed_at is not None:
        return None
    return row


@router.get(CLOSE)
def confirm_close(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    open_id: int,
) -> Response:
    """Closing one writes off what it shows: asked first (decision 0019),
    as the item's page is no check of its own."""
    row = _closable(session, item_id, open_id)
    if row is None:
        return not_found()
    (each,) = open_packages.shown(session, OpenPackage.id == open_id)
    return admin_page(
        request,
        signed,
        "open_package_close.html",
        {
            "title": close_title(each.package),
            "here": f"{ITEMS}/{item_id}/open-packages/{open_id}/close",
            "keep": f"{ITEMS}/{item_id}",
            "shown": each,
        },
    )


@router.post(CLOSE)
def close_directly(
    signed: AdminSignedIn, session: DbSession, item_id: int, open_id: int
) -> Response:
    """Closed from its item's page, reported or not: nothing waits."""
    if _closable(session, item_id, open_id) is None:
        return not_found()
    done, _ = attempt(
        session, lambda: open_packages.close(session, open_id, signed.admin.id, now())
    )
    session.commit()
    open_packages.measure_closing("direct", [done])
    notice = "open_package_closed" if done is not None else "used_up_unchanged"
    return redirect(f"{ITEMS}/{item_id}", notice=notice, section="open-packages")
