"""The guided recount of an item whose unit was wrong from the start
(catalogue/unit_recounts.py): its page, saying beforehand all it will do,
the labels it revokes among them, and asking Reorder at, what is on order
and its vendor offers again in the new unit; and the page after, linking
to a count of it at each place it brought to zero, and saying to print new
labels (Ali, 2026-11-25, inventory-tng-qety). The answer to No on the item
page links here (items_routes)."""

import uuid
from collections.abc import Mapping

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.catalogue import forms, unit_changes, unit_recounts
from inventory_tng.catalogue.paths import ITEMS, UNIT_RECOUNT, UNIT_RECOUNTED
from inventory_tng.core.db_errors import refusal
from inventory_tng.core.db_sessions import DbSession
from inventory_tng.core.phrases import Words, gettext
from inventory_tng.core.sent_forms import form_key
from inventory_tng.models.base import INTEGER_MAX, whole
from inventory_tng.models.catalogue import Item
from inventory_tng.stock.paths import COUNTS
from inventory_tng.stock.queries import places_with_ids
from inventory_tng.web.admin_pages import TextForm, admin_page, now
from inventory_tng.web.decimal_separator import decimal_separator
from inventory_tng.web.forms import CHANGED, RETIRED, RULES, row_id
from inventory_tng.web.pages import not_found, redirect

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)


def recount_title(item: str, unit: str) -> str:
    return gettext("Recount %(item)s in %(unit)s", item=item, unit=unit)


def recounted_title(item: str, unit: str) -> str:
    return gettext("%(item)s recounted in %(unit)s", item=item, unit=unit)


def _page(
    request: Request,
    session: Session,
    signed: SignedIn,
    item: Item,
    unit: str,
    *,
    values: Mapping[str, str] | None = None,
    problems: Mapping[str, Words] | None = None,
    alert: Words | None = None,
) -> HTMLResponse:
    """The recount's page, as the records are now: what it will do, and
    the amounts to give again, as they are, or as `values` typed them."""
    plan = unit_recounts.planned(session, item)
    here = UNIT_RECOUNT.format(item_id=item.id)
    log.debug("unit recount shown", item_id=item.id, places=len(plan.held))
    return admin_page(
        request,
        signed,
        "catalogue/unit_recount.html",
        {
            "title": recount_title(item.name_shown, unit),
            "here": here,
            "back_link": (f"{ITEMS}/{item.id}", item.name_shown),
            "item": item,
            "unit": unit,
            "plan": plan,
            "key": uuid.uuid4(),
            "on_order": forms.ON_ORDER,
            "per_order": forms.PER_ORDER,
            "waiting": RULES[unit_changes.UNIT_CHECKS_WAITING],
            "values": values,
            "problems": problems,
            "alert": alert,
        },
    )


@router.get(UNIT_RECOUNT)
def recount_page(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    unit: str = "",
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    # Not to be recounted into it, as after a recount sent twice: its page.
    if unit not in unit_recounts.units_open(session, found):
        return redirect(f"{ITEMS}/{item_id}")
    return _page(request, session, signed, found, unit)


@router.post(UNIT_RECOUNT)
def recount(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    form: TextForm,
) -> Response:
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    unit = form.get("unit", "")
    if unit not in unit_recounts.units_open(session, found):
        return redirect(f"{ITEMS}/{item_id}")
    # The offers as now: one added or retired since is refused as changed.
    offers = [each.offer.id for each in unit_recounts.planned(session, found).offers]
    amounts, problems = forms.recount_amounts(
        form, offers, separator=decimal_separator(request)
    )
    if amounts is None:
        return _page(
            request, session, signed, found, unit, values=form, problems=problems
        )
    done, broken = refusal(
        session,
        lambda: unit_recounts.recount(
            session,
            found,
            unit,
            amounts,
            seen=form.get("seen", ""),
            key=form_key(form.get("key", "")) or uuid.uuid4(),
            admin_id=signed.admin.id,
            at=now(),
        ),
        CHANGED,
        RETIRED,
        unit_changes.UNIT_CHECKS_WAITING,
    )
    if broken is not None:
        session.refresh(found)
        if unit not in unit_recounts.units_open(session, found):
            return redirect(f"{ITEMS}/{item_id}")
        return _page(
            request, session, signed, found, unit, values=form, alert=RULES[broken]
        )
    assert done is not None  # Refused, or done.
    session.commit()
    return redirect(
        UNIT_RECOUNTED.format(item_id=item_id),
        places=",".join(str(each) for each in done.places),
        labels=str(done.labels),
    )


@router.get(UNIT_RECOUNTED)
def recounted(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    places: str = "",
    labels: str = "",
) -> Response:
    """The page after a recount: the places it brought to zero, in its
    address, each linked to its count of the item; and, if it revoked any
    labels, to print new ones."""
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    ids = {each for part in places.split(",") if (each := row_id(part)) is not None}
    return admin_page(
        request,
        signed,
        "catalogue/unit_recounted.html",
        {
            "title": recounted_title(found.name_shown, found.unit),
            "back_link": (f"{ITEMS}/{item_id}", found.name_shown),
            "item": found,
            # A retired one, holding nothing now, is counted no more.
            "places": sorted(
                (each for each in places_with_ids(session, ids) if not each.retired_at),
                key=lambda each: (each.name.lower(), each.id),
            ),
            "counts_path": COUNTS,
            "revoked": whole(labels, 0, INTEGER_MAX) or 0,
            "item_path": f"{ITEMS}/{item_id}",
        },
    )
