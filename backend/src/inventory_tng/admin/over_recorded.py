"""Lines recorded with more than was moved (over_recorded.py,
inventory-tng-0gg): "Needs your action" on the home page leads here, for
one item or all. Correct writes adjustments that undo the difference on
each side of its transaction, with the reason Recorded more than moved;
Mark checked settles it as it is, for one corrected another way."""

from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from inventory_tng import ledger, over_recorded
from inventory_tng.admin.common import admin_page
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
from inventory_tng.models import Movement, OverRecorded
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

OVER_RECORDED = f"{STOCK}/recorded-more"

TITLE = "Recorded more than moved"
# As "Needs your action" lists it (needs_action.py): its own rows, not open
# packages, so no open packages' where.
KIND = Kind(TITLE, OVER_RECORDED, ())

NOTICES.update(
    {
        "over_recorded_corrected": "Corrected: adjustments undo what was "
        "recorded more than moved.",
        "over_recorded_checked": "Marked checked.",
        "over_recorded_unchanged": "Nothing changed: it was answered or "
        "changed meanwhile, or its places can't be adjusted until counted. "
        "Look again.",
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
        },
    )


@router.post(f"{OVER_RECORDED}/{{check_id}}/{{do}}")
def answer(
    signed: AdminSignedIn,
    session: DbSession,
    check_id: int,
    do: str,
    only: OnlyItem,
    seen: Annotated[str, Form()] = "",
) -> Response:
    """Correct one, or mark it checked, if still as seen. Its places
    locked first, by id, as saves take places."""
    if do not in ("correct", "checked") or session.get(OverRecorded, check_id) is None:
        return not_found()
    ledger.lock_places(session, over_recorded.places_of(session, check_id))
    admin_id = signed.admin.id
    if do == "correct":
        written, broken = attempt(
            session,
            lambda: over_recorded.correct(session, check_id, seen, admin_id),
        )
    else:
        written = []
        _, broken = attempt(
            session,
            lambda: over_recorded.mark_checked(session, check_id, seen, admin_id),
        )
    session.commit()
    if broken is not None:
        log.info("over recorded not settled", over_recorded_id=check_id, rule=broken)
        return redirect(OVER_RECORDED, notice="over_recorded_unchanged", **query(only))
    ledger.corrected(admin_id, 0, written or [])
    how: over_recorded.Settled = "corrected" if do == "correct" else "checked"
    over_recorded.measure_settled(how)
    return redirect(OVER_RECORDED, notice=f"over_recorded_{how}", **query(only))
