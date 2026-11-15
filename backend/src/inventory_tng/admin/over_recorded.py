"""Lines recorded with more than was moved (over_recorded.py,
inventory-tng-0gg): "Needs your action" on the home page leads here, for
one item or all. Correct writes adjustments that undo the difference on
each side of its transaction, with the reason Recorded more than moved;
Mark checked settles it as it is, for one corrected another way. An open
package moved whole is now where it arrived: Confirm count settles it with
the count it held then, as it was, or corrected there by an adjustment
(Ali, 2026-11-15)."""

from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import ledger, over_recorded
from inventory_tng.admin.common import admin_page, quantity
from inventory_tng.admin.needs_action import (
    ItemWaiting,
    Kind,
    OnlyItem,
    query,
    query_string,
)
from inventory_tng.admin.stock import STOCK
from inventory_tng.db import DbSession, attempt
from inventory_tng.local_time import site_zone
from inventory_tng.models import Item, Movement, OverRecorded
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

OVER_RECORDED = f"{STOCK}/recorded-more"

TITLE = "Recorded more than moved"
# A count left blank, in quantity()'s words for one not a number.
TYPE_A_NUMBER = "Type a number, such as 12 or 0.5."
# As "Needs your action" lists it (needs_action.py): its own rows, not open
# packages, so no open packages' where.
KIND = Kind(TITLE, OVER_RECORDED, ())

NOTICES.update(
    {
        "over_recorded_corrected": "Corrected.",
        "over_recorded_checked": "Marked checked.",
        "over_recorded_confirmed": "Confirmed.",
        "over_recorded_count_corrected": "Count corrected.",
        "over_recorded_unchanged": "Not changed: someone answered or changed "
        "it meanwhile. Reload to see it as it is.",
    }
)


def by_item(session: Session) -> list[ItemWaiting]:
    """Those waiting, one line per item, as "Needs your action" lists
    them: each amount, said once with how many."""
    lines: dict[int, ItemWaiting] = {}
    amounts: dict[int, list[str]] = {}
    for each in over_recorded.shown(session):
        amounts.setdefault(each.item_id, []).append(each.amount)
        before = lines.get(each.item_id)
        lines[each.item_id] = ItemWaiting(
            each.item_id,
            each.item,
            "",
            (before.count if before else 0) + 1,
            Decimal(0),
            each.unit,
        )
    return [
        line._replace(packages=", ".join(amounts[item_id]))
        for item_id, line in lines.items()
    ]


@router.get(OVER_RECORDED)
def over_recorded_page(
    request: Request, signed: AdminSignedIn, session: DbSession, only: OnlyItem
) -> Response:
    """All those waiting, or only one item's."""
    return _page(request, signed, session, only)


def _page(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    only: OnlyItem,
    problems: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
) -> Response:
    """The page, with the problems of a count sent, and what was typed."""
    where = [Movement.item_id == only.id] if only else []
    return admin_page(
        request,
        signed,
        "over_recorded.html",
        {
            "title": TITLE,
            "here": OVER_RECORDED,
            "back_link": (STOCK, "Stock"),
            "only": only,
            "query": query_string(only),
            "waiting": over_recorded.shown(session, *where),
            "seen": over_recorded.seen,
            "zone": site_zone(session),
            "problems": problems,
            "values": values,
        },
    )


@router.post(f"{OVER_RECORDED}/{{check_id}}/{{do}}")
def answer(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    check_id: int,
    do: str,
    only: OnlyItem,
    seen: Annotated[str, Form()] = "",
    typed: Annotated[str, Form(alias="count")] = "",
) -> Response:
    """Correct one, mark it checked, or confirm the count of an open
    package moved whole, if still as seen. Its places locked first, by id,
    as saves take places."""
    check = session.get(OverRecorded, check_id)
    if do not in ("correct", "checked", "count") or check is None:
        return not_found()
    count = None
    if do == "count":
        unit = session.scalars(
            select(Item.unit)
            .join(Movement, Movement.item_id == Item.id)
            .where(Movement.id == check.movement_id)
        ).one()
        count = quantity(typed, positive=False, unit=unit, required=TYPE_A_NUMBER)
        if not isinstance(count, Decimal):
            field = f"count-{check_id}"
            log.debug("over recorded count refused", over_recorded_id=check_id)
            return _page(
                request, signed, session, only, {field: str(count)}, {field: typed}
            )
    ledger.lock_places(session, over_recorded.places_of(session, check_id))
    admin_id = signed.admin.id

    def settle() -> tuple[over_recorded.Settled, list[ledger.Written]]:
        if do == "correct":
            return "corrected", over_recorded.correct(session, check_id, seen, admin_id)
        if count is not None:
            return over_recorded.confirm_count(session, check_id, seen, admin_id, count)
        over_recorded.mark_checked(session, check_id, seen, admin_id)
        return "checked", []

    settled, broken = attempt(session, settle)
    session.commit()
    if settled is None:
        log.info("over recorded not settled", over_recorded_id=check_id, rule=broken)
        return redirect(OVER_RECORDED, notice="over_recorded_unchanged", **query(only))
    how, written = settled
    ledger.corrected(admin_id, 0, written)
    over_recorded.measure_settled(how)
    notice = f"over_recorded_{how}"
    if do == "count":
        notice = (
            "over_recorded_count_corrected" if written else "over_recorded_confirmed"
        )
    return redirect(OVER_RECORDED, notice=notice, **query(only))
