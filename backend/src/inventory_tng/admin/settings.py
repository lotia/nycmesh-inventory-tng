"""Settings administrators change: for now, how the scan page behaves
(docs/decisions/0011). Sign-in settings and the site's time zone join it
(inventory-tng-8i4.12.6)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng.admin.common import ADMIN, section
from inventory_tng.db import DbSession
from inventory_tng.models import ScanSettings
from inventory_tng.pages import NOTICES, page, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

router = APIRouter(include_in_schema=False)

SETTINGS = section(f"{ADMIN}/settings", "Settings")
# A day: longer would hardly ever ask.
NAME_CHECK_MAX = 24 * 60

NOTICES["settings_saved"] = "Saved. Open scan pages pick it up within ten minutes."


def _page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problem: str | None = None,
    typed: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    scan = session.get_one(ScanSettings, 1)
    return page(
        request,
        "settings.html",
        {
            "title": "Settings",
            "admin": signed.admin,
            "name_check_minutes": typed
            if typed is not None
            else str(scan.name_check_minutes),
            "name_check_max": NAME_CHECK_MAX,
            "problems": {"name_check_minutes": problem} if problem else {},
        },
        status_code,
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
            request, session, signed, problem=problem, typed=typed, status_code=400
        )
    session.get_one(ScanSettings, 1).name_check_minutes = int(typed)
    session.commit()
    return redirect(SETTINGS, notice="settings_saved")
