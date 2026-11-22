"""Counts and adjustments: the page listing the counts and adjustments
saved, and the adjustment, with which an administrator puts the records
right (docs/decisions/0003). The writing is stock/counts.py's
(record_adjustment); this page checks what was typed and says what is
wrong. A place's count form is stock/count_routes.py's.

An adjustment carries a key made when its form was shown, so sending it
twice writes it once. The page checks what was typed before writing, but
an item's unit may change to each between that check and the write: the
ledger then refuses the line (movement_whole_units), and the form is shown
again saying so, nothing written. So too a place retired meanwhile, which
the ledger refuses (location_countable).
"""

import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import ColumnElement, case, func
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.core.db_errors import refusal
from inventory_tng.core.db_sessions import DbSession, by_name
from inventory_tng.core.phrases import Words, in_query, lazy_gettext
from inventory_tng.core.sent_forms import form_key
from inventory_tng.models.admins import Admin
from inventory_tng.models.base import INTEGER_MAX, whole
from inventory_tng.models.catalogue import Item
from inventory_tng.models.places import Location
from inventory_tng.models.stock import AdjustmentReason, LedgerTransaction
from inventory_tng.site_settings.paths import SETTINGS
from inventory_tng.stock import queries
from inventory_tng.stock.balance import countable
from inventory_tng.stock.counts import record_adjustment
from inventory_tng.stock.forms import adjustment
from inventory_tng.stock.paths import ADJUSTMENTS, COUNTS
from inventory_tng.web.admin_pages import admin_page, current_rows, text_form
from inventory_tng.web.forms import RULES
from inventory_tng.web.listing import UNASKED, AskedView, Sort, Sorting, View, order
from inventory_tng.web.pages import add_notices, redirect

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

# The ledger rules a race can break past the page's checks (module
# docstring).
WHOLE = "movement_whole_units"
COUNTABLE = "location_countable"
COUNTS_TITLE = lazy_gettext("Counts and adjustments")
ADJUSTED = lazy_gettext("Recorded.")
add_notices({"adjusted": ADJUSTED})

TextForm = Annotated[dict[str, str], Depends(text_form)]


def _places(session: Session) -> list[Location]:
    """Where can be counted or adjusted (balance.countable)."""
    return by_name(session, Location, countable())


# Translators: a count, as a kind of save, before what it saved.
COUNT_KIND = lazy_gettext("Count")
# How it sorts: newest first, as it always has, or by a heading; What by
# its words, Count or the reason (Ali, 2026-11-15, inventory-tng-qwb), in
# the language spoken.
SORTING = Sorting(
    "when",
    {
        "when": Sort.of(LedgerTransaction.id, first="desc"),
        "what": Sort.of(
            case(
                (LedgerTransaction.kind == "count", func.lower(in_query(COUNT_KIND))),
                else_=func.lower(AdjustmentReason.name),
            )
        ),
        "where": Sort.of(func.lower(Location.name)),
        "by": Sort.of(func.lower(Admin.username)),
    },
)


def _view(
    asked: AskedView,
    place: str = "",
    kind: str = "",
    admin: str = "",
) -> View:
    """The list's view as its query string asks: a place or an admin not a
    number, or a kind not offered, is no filter (Ali, 2026-11-15)."""
    place_id = whole(place, 1, INTEGER_MAX)
    admin_id = whole(admin, 1, INTEGER_MAX)
    filters = {
        "place": str(place_id or ""),
        "kind": kind if kind in queries.KINDS else "",
        "admin": str(admin_id or ""),
    }
    return View.read(COUNTS, SORTING, asked, filters=filters)


def _recent(session: Session, view: View) -> tuple[list[dict[str, Any]], View]:
    """The counts and adjustments the view picks, in its order, one page
    of them, with their lines; and the view counted."""
    where: list[ColumnElement[bool]] = []
    if place := view.filtered("place"):
        where.append(queries.count_place == int(place))
    if kind := view.filtered("kind"):
        where.append(LedgerTransaction.kind == kind)
    if admin := view.filtered("admin"):
        where.append(LedgerTransaction.admin_id == int(admin))
    view = view.counted(queries.counts_and_adjustments_found(session, *where))
    rows = queries.counts_and_adjustments(
        session,
        *where,
        order=order(view, LedgerTransaction.id),
        limit=view.per_page,
        offset=view.offset,
    )
    log.debug("counts listed", found=view.found, sort=view.by, page=view.page)
    lines: dict[int, list[tuple[Item, Decimal]]] = {}
    for transaction_id, item, amount in queries.count_lines(
        session, [row[0].id for row in rows]
    ):
        lines.setdefault(transaction_id, []).append((item, amount))
    return [
        {
            "transaction": transaction,
            "admin": admin,
            "place": place,
            "found": transaction.to_location_id is not None,
            "reason": reason or "",
            "lines": lines.get(transaction.id, []),
        }
        for transaction, admin, place, reason in rows
    ], view


def _counts_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    view: View | None = None,
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    recent, view = _recent(session, view or _view(UNASKED))
    # The filters offer where and by whom there are any.
    return admin_page(
        request,
        signed,
        "stock/counts.html",
        {
            "title": COUNTS_TITLE,
            "here": COUNTS,
            "settings_path": SETTINGS,
            "places": _places(session),
            "items": queries.countable_items(session),
            "reasons": current_rows(session, AdjustmentReason),
            "recent": recent,
            "view": view,
            "kinds": queries.KINDS,
            "count_kind": COUNT_KIND,
            "counted_places": queries.places_counted(session),
            "counters": queries.counters(session),
            "key": uuid.uuid4(),
            "problems": problems,
            "values": values,
        },
    )


@router.get(COUNTS)
def counts(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    view: Annotated[View, Depends(_view)],
) -> Response:
    return _counts_page(request, session, signed, view=view)


@router.get(f"{COUNTS}/place")
def choose_place(location_id: str = "") -> Response:
    """The "Count a place" form, sent as a GET, goes to that place's count."""
    if not location_id.isdigit():
        return redirect(COUNTS)
    return redirect(f"{COUNTS}/{location_id}")


@router.post(ADJUSTMENTS)
def adjust(
    request: Request, signed: AdminSignedIn, session: DbSession, form: TextForm
) -> Response:
    key = form_key(form.get("key", ""))
    asked, problems = adjustment(session, form, key)
    if asked is None:
        return _counts_page(request, session, signed, problems=problems, values=form)
    place, item, amount, direction, reason, key = asked
    _, broken = refusal(
        session,
        lambda: record_adjustment(
            session,
            key,
            signed.admin.id,
            place.id,
            item.id,
            amount,
            found=direction == "found",
            reason_id=reason.id,
        ),
        WHOLE,
        COUNTABLE,
    )
    if broken is not None:
        log.info("adjustment refused by a rule", item_id=item.id, rule=broken)
        field = "location_id" if broken == COUNTABLE else "amount"
        return _counts_page(
            request, session, signed, problems={field: RULES[broken]}, values=form
        )
    session.commit()
    return redirect(COUNTS, notice="adjusted", section="adjust")
