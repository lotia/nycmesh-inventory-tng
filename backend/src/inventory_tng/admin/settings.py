"""Settings administrators change: how the scan page behaves
(docs/decisions/0011), and the reasons an adjustment may give
(admin/counts.py). Sign-in settings and the site's time zone join them
(inventory-tng-8i4.12.6)."""

from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.admin.common import ADMIN, admin_page, name_problem, now
from inventory_tng.db import DbSession, unique
from inventory_tng.models import AdjustmentReason, ScanSettings
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

SETTINGS = f"{ADMIN}/settings"
# A day: longer would hardly ever ask.
NAME_CHECK_MAX = 24 * 60

NOTICES.update(
    {
        "settings_saved": "Saved. Open scan pages pick it up within ten minutes.",
        "reason_retired": "Retired: it is no longer offered.",
    }
)
REASON_TAKEN = "There is already a reason by that name."


def _page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
) -> HTMLResponse:
    scan = session.get_one(ScanSettings, 1)
    reasons = session.scalars(
        select(AdjustmentReason).order_by(
            AdjustmentReason.retired_at.is_not(None), AdjustmentReason.id
        )
    ).all()
    return admin_page(
        request,
        signed,
        "settings.html",
        {
            "title": "Settings",
            "here": SETTINGS,
            "values": {"name_check_minutes": str(scan.name_check_minutes)}
            | (values or {}),
            "name_check_max": NAME_CHECK_MAX,
            "reasons": reasons,
            "problems": problems,
        },
        400 if problems else 200,
    )


@router.get(SETTINGS)
def settings(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    return _page(request, session, signed)


@router.post(f"{SETTINGS}/scan")
def save_scan_settings(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    name_check_minutes: Annotated[str, Form()] = "",
) -> Response:
    typed = name_check_minutes.strip()
    if not typed.isdigit() or int(typed) > NAME_CHECK_MAX:
        problem = f"A whole number of minutes, from 0 to {NAME_CHECK_MAX}."
        return _page(
            request,
            session,
            signed,
            problems={"name_check_minutes": problem},
            values={"name_check_minutes": typed},
        )
    session.get_one(ScanSettings, 1).name_check_minutes = int(typed)
    session.commit()
    log.info("scan settings changed", name_check_minutes=int(typed))
    return redirect(SETTINGS, notice="settings_saved")


@router.post(f"{SETTINGS}/reasons")
def add_reason(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    name: Annotated[str, Form()] = "",
) -> Response:
    name = name.strip()
    problem = name_problem(name)
    added = AdjustmentReason(name=name)
    if problem is None and not unique(session, lambda: session.add(added)):
        problem = REASON_TAKEN
    if problem is not None:
        return _page(request, session, signed, problems={"reason": problem})
    session.commit()
    log.info("adjustment reason added", adjustment_reason_id=added.id)
    return redirect(SETTINGS, notice="added")


@router.post(f"{SETTINGS}/reasons/{{reason_id}}")
def change_reason(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    reason_id: int,
    name: Annotated[str, Form()] = "",
    action: Annotated[str, Form()] = "rename",
) -> Response:
    """Rename, retire or bring back, as the button pressed says."""
    reason = session.get(AdjustmentReason, reason_id, with_for_update=True)
    if reason is None:
        return not_found()
    action = action if action in {"retire", "restore"} else "rename"
    name = name.strip() or reason.name

    def change() -> None:
        if action == "retire":
            reason.retired_at = now()
        elif action == "restore":
            reason.retired_at = None
        else:
            reason.name = name

    problem = name_problem(name)
    if problem is None and not unique(session, change):
        problem = REASON_TAKEN
    if problem is not None:
        return _page(
            request,
            session,
            signed,
            problems={f"reason_{reason_id}": problem},
        )
    session.commit()
    log.info("adjustment reason changed", adjustment_reason_id=reason_id, action=action)
    notice = {"retire": "reason_retired", "restore": "restored"}
    return redirect(SETTINGS, notice=notice.get(action, "saved"))
