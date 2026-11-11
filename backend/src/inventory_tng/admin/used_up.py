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
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import open_packages
from inventory_tng.admin.common import admin_page, now
from inventory_tng.admin.items import ITEMS
from inventory_tng.admin.stock import STOCK
from inventory_tng.db import DbSession
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
    }
)

closed = meter.create_counter(
    "inventory.open_packages.closed",
    unit="{package}",
    description="Open packages an administrator closed as used up, by how: "
    "confirming a volunteer's report, or directly",
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
    return len(_reported(session))


def _reported(session: Session) -> Sequence[int]:
    return session.scalars(
        select(OpenPackage.id).where(
            OpenPackage.reported_at.is_not(None), OpenPackage.closed_at.is_(None)
        )
    ).all()


def _closed(how: Literal["confirmed", "direct"], count: int) -> None:
    """Measured once committed."""
    if count:
        closed.add(count, {"how": how})


@router.get(USED_UP)
def used_up_page(
    request: Request, signed: AdminSignedIn, session: DbSession
) -> Response:
    return _page(request, signed, session)


def _page(request: Request, signed: SignedIn, session: Session) -> Response:
    return admin_page(
        request,
        signed,
        "used_up.html",
        {
            "title": "Reported used up",
            "here": USED_UP,
            "back_link": (STOCK, "Stock"),
            "reported": open_packages.shown(
                session, OpenPackage.reported_at.is_not(None)
            ),
            "zone": site_zone(session),
        },
    )


Doing = Literal["confirm", "clear"]


def _do(session: Session, signed: SignedIn, ids: Sequence[int], do: Doing) -> int:
    """Confirm or clear each, by id: how many it changed. One no longer
    reported is left as it is."""
    changed = 0
    reported = set(_reported(session))
    for open_id in sorted(ids):
        if open_id not in reported:
            continue
        if do == "clear":
            changed += open_packages.clear(session, open_id)
            continue
        done = open_packages.close(session, open_id, signed.admin.id, now())
        changed += bool(done and done.closed)
    return changed


def _done(do: Doing, changed: int, admin_id: int) -> None:
    if do == "confirm":
        _closed("confirmed", changed)
    elif changed:
        cleared.add(changed)
    log.info("reports of used up answered", do=do, changed=changed, admin_id=admin_id)


@router.post(USED_UP)
def answer_ticked(
    signed: AdminSignedIn,
    session: DbSession,
    open_package: Annotated[list[int], Form()] = [],  # noqa: B006  FastAPI copies it.
    do: Annotated[str, Form()] = "",
) -> Response:
    """Confirm, or clear, the open packages ticked."""
    if do not in ("confirm", "clear"):
        return not_found()
    if not open_package:
        return redirect(USED_UP, notice="used_up_none")
    answer: Doing = "confirm" if do == "confirm" else "clear"
    changed = _do(session, signed, open_package, answer)
    session.commit()
    _done(answer, changed, signed.admin.id)
    notice = "used_up_confirmed" if answer == "confirm" else "used_up_cleared"
    return redirect(USED_UP, notice=notice)


@router.post(f"{USED_UP}/{{open_id}}/{{do}}")
def answer_one(
    signed: AdminSignedIn, session: DbSession, open_id: int, do: str
) -> Response:
    """Confirm, or clear, one."""
    if do not in ("confirm", "clear") or session.get(OpenPackage, open_id) is None:
        return not_found()
    answer: Doing = "confirm" if do == "confirm" else "clear"
    changed = _do(session, signed, [open_id], answer)
    session.commit()
    _done(answer, changed, signed.admin.id)
    notice = "used_up_confirmed" if answer == "confirm" else "used_up_cleared"
    return redirect(USED_UP, notice=notice)


@router.post(f"{ITEMS}/{{item_id}}/open-packages/{{open_id}}/close")
def close_directly(
    signed: AdminSignedIn, session: DbSession, item_id: int, open_id: int
) -> Response:
    """Closed from its item's page, reported or not: nothing waits."""
    row = session.get(OpenPackage, open_id)
    if row is None or row.item_id != item_id:
        return not_found()
    done = open_packages.close(session, open_id, signed.admin.id, now())
    session.commit()
    _closed("direct", int(bool(done and done.closed)))
    return redirect(
        f"{ITEMS}/{item_id}", notice="open_package_closed", section="open-packages"
    )
