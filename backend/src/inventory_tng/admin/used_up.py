"""Open packages volunteers said are used up, for an administrator to
confirm or clear (docs/decisions/0020): "Waiting for you" on the home page
leads here, one at a time or ticked together; and closing one directly,
from its item's page. Closing writes off what it still showed, with the
reason Used up (open_packages.close)."""

from collections.abc import Sequence
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response
from opentelemetry import metrics
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng import ledger, open_packages
from inventory_tng.admin.common import admin_page, now
from inventory_tng.admin.items import ITEMS
from inventory_tng.admin.stock import STOCK
from inventory_tng.db import DbSession, attempt
from inventory_tng.local_time import site_zone
from inventory_tng.models import OpenPackage
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter(include_in_schema=False)

USED_UP = f"{STOCK}/used-up"

NOTICES.update(
    {
        "used_up_confirmed": "Closed: what they showed is written off as used up.",
        "used_up_cleared": "Offered again: not used up.",
        "used_up_none": "Tick the open packages first.",
        "open_package_closed": "Closed: what it showed is written off as used up.",
        "used_up_unchanged": "Nothing changed: it was answered meanwhile, moved, "
        "or its place can't be adjusted until counted.",
    }
)

cleared = meter.create_counter(
    "inventory.open_packages.cleared",
    unit="{package}",
    description="Volunteers' reports of open packages used up that an "
    "administrator cleared, as not used up",
)


def waiting(session: Session) -> int:
    """How many open packages wait for an administrator: reported used up,
    not yet closed."""
    return session.scalar(select(func.count()).where(*REPORTED)) or 0


# Reported used up, and waiting.
REPORTED = (OpenPackage.reported_at.is_not(None), OpenPackage.closed_at.is_(None))


@router.get(USED_UP)
def used_up_page(
    request: Request, signed: AdminSignedIn, session: DbSession
) -> Response:
    return admin_page(
        request,
        signed,
        "used_up.html",
        {
            "title": "Reported used up",
            "here": USED_UP,
            "back_link": (STOCK, "Stock"),
            "reported": open_packages.shown(session, *REPORTED),
            "zone": site_zone(session),
        },
    )


Doing = Literal["confirm", "clear"]
DONE: dict[Doing, str] = {"confirm": "used_up_confirmed", "clear": "used_up_cleared"}


def _answer(
    session: Session, signed: SignedIn, ids: Sequence[int], do: str
) -> Response:
    """Confirm, or clear, each of `ids` still reported, and say so. Their
    places locked first, by id, as saves take places, so none waits on
    another the wrong way round; then each closes or clears."""
    if do not in DONE:
        return not_found()
    answer: Doing = "confirm" if do == "confirm" else "clear"
    rows = session.scalars(
        select(OpenPackage).where(OpenPackage.id.in_(list(ids)), *REPORTED)
    ).all()
    for place in sorted({row.location_id for row in rows}):
        ledger.lock_place(session, place)
    changed = 0
    for row in sorted(rows, key=lambda each: each.id):
        if answer == "clear":
            changed += open_packages.clear(session, row.id)
            continue
        done, broken = attempt(
            session,
            lambda row=row: open_packages.close(
                session, row.id, signed.admin.id, now(), reported=True
            ),
        )
        changed += bool(done) and broken is None
    session.commit()
    if answer == "confirm":
        open_packages.measure_closed("confirmed", changed)
    elif changed:
        cleared.add(changed)
    log.info(
        "reports of used up answered",
        do=answer,
        changed=changed,
        admin_id=signed.admin.id,
    )
    return redirect(USED_UP, notice=DONE[answer] if changed else "used_up_unchanged")


@router.post(USED_UP)
def answer_ticked(
    signed: AdminSignedIn,
    session: DbSession,
    open_package: Annotated[list[int], Form()] = [],  # noqa: B006  FastAPI copies it.
    do: Annotated[str, Form()] = "",
) -> Response:
    """Confirm, or clear, the open packages ticked."""
    if do in DONE and not open_package:
        return redirect(USED_UP, notice="used_up_none")
    return _answer(session, signed, open_package, do)


@router.post(f"{USED_UP}/{{open_id}}/{{do}}")
def answer_one(
    signed: AdminSignedIn, session: DbSession, open_id: int, do: str
) -> Response:
    """Confirm, or clear, one."""
    if session.get(OpenPackage, open_id) is None:
        return not_found()
    return _answer(session, signed, [open_id], do)


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
            "title": f"Close this {each.package} as used up?",
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
    done, broken = attempt(
        session, lambda: open_packages.close(session, open_id, signed.admin.id, now())
    )
    session.commit()
    open_packages.measure_closed("direct", int(bool(done) and broken is None))
    notice = "open_package_closed" if done and broken is None else "used_up_unchanged"
    return redirect(f"{ITEMS}/{item_id}", notice=notice, section="open-packages")
