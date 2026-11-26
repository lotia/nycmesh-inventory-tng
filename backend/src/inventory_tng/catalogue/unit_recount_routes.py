"""The guided recount's pages (catalogue/unit_recounts.py): the page
before, saying all it will do and asking the item's amounts again in the
new unit, and the page after, linking to what is left to do (Ali,
2026-11-25, inventory-tng-qety, inventory-tng-l26i; what each says:
docs/decisions/0003). The answer to No on the item page links here
(items_routes)."""

import uuid
from collections.abc import Mapping

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.catalogue import (
    forms,
    queries,
    unit_change_forms,
    unit_changes,
    unit_recounts,
)
from inventory_tng.catalogue.paths import ITEMS, UNIT_RECOUNT, UNIT_RECOUNTED
from inventory_tng.catalogue.unit_conversion import convertible
from inventory_tng.core.db_errors import refusal
from inventory_tng.core.db_sessions import DbSession
from inventory_tng.core.phrases import Words, gettext
from inventory_tng.core.sent_forms import form_key
from inventory_tng.models.catalogue import Item
from inventory_tng.stock.paths import COUNTS
from inventory_tng.stock.queries import places_with_ids
from inventory_tng.web.admin_pages import TextForm, admin_page, now
from inventory_tng.web.decimal_separator import decimal_separator
from inventory_tng.web.forms import CHANGED, RETIRED, RULES, row_id
from inventory_tng.web.pages import not_found, redirect

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

# In the page after's address: the recount retired the item's packages
# in the old unit, any it had, so the page offers to add them in the new.
RETIRED_PACKAGES = "retired"


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
            "on_order": unit_change_forms.ON_ORDER,
            "per_order": unit_change_forms.PER_ORDER,
            "size_of": unit_change_forms.SIZE_OF,
            "short_below_of": unit_change_forms.SHORT_BELOW_OF,
            "holds": forms.HOLDS,
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
    # Changed since it was shown: said before what is wrong with the
    # amounts (unit_recounts.stale_as_read), what was typed kept.
    seen = form.get("seen", "")
    if unit_recounts.stale_as_read(session, found, seen):
        return _page(
            request, session, signed, found, unit, values=form, alert=RULES[CHANGED]
        )
    # The offers and packages as now: one added or retired since is
    # refused as changed.
    offers = [each.offer.id for each in queries.current_offers(session, item_id)]
    packages = unit_recounts.resized_of(session, found)
    amounts, problems = unit_change_forms.recount_amounts(
        form, offers, packages, separator=decimal_separator(request)
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
            seen=seen,
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
    after = {"unit": unit, "places": _listed(done.places)}
    if done.revoked:
        after["labels"] = _listed(done.revoked)
    if done.add_packages:
        after["packages"] = RETIRED_PACKAGES
    return redirect(UNIT_RECOUNTED.format(item_id=item_id), **after)


def _listed(ids: list[int]) -> str:
    """Ids as the page after's address lists them (_ids_in)."""
    return ",".join(str(each) for each in ids)


def _ids_in(listed: str) -> set[int]:
    """The ids the page after's address lists, any other part left out."""
    return {each for part in listed.split(",") if (each := row_id(part)) is not None}


@router.get(UNIT_RECOUNTED)
def recounted(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    item_id: int,
    unit: str = "",
    places: str = "",
    labels: str = "",
    packages: str = "",
) -> Response:
    """The page after a recount: the places it brought to zero, in its
    address, each linked to its count of the item; and the packages of
    the labels it revoked, also in its address, by label id: current
    ones, to print again, and retired ones, which can't be until they are
    brought back; and a link to add its packages in the new unit, if its
    address says the recount retired them (RETIRED_PACKAGES). Its title
    names the unit recounted into, from its address, and a line says the
    item's unit has changed since, if it has; a unit not one of the
    item's lengths is its unit now."""
    found = session.get(Item, item_id)
    if found is None:
        return not_found()
    if not convertible(found.unit, unit):
        unit = found.unit
    log.debug("unit recounted shown", item_id=item_id, changed_since=unit != found.unit)
    # A retired one, holding nothing now, is counted no more.
    current = [
        each
        for each in places_with_ids(session, _ids_in(places))
        if not each.retired_at
    ]
    revoked = queries.revoked_packages(session, item_id, _ids_in(labels))
    item_path = f"{ITEMS}/{item_id}"
    return admin_page(
        request,
        signed,
        "catalogue/unit_recounted.html",
        {
            "title": recounted_title(found.name_shown, unit),
            "back_link": (item_path, found.name_shown),
            "item": found,
            "recounted_into": unit,
            "add_packages": packages == RETIRED_PACKAGES,
            "places": sorted(
                current,
                key=lambda each: (each.name.lower(), each.id),
            ),
            "counts_path": COUNTS,
            "item_path": item_path,
            "revoked": [each.name_shown for each in revoked if not each.retired_at],
            "revoked_retired": [each.name_shown for each in revoked if each.retired_at],
        },
    )
