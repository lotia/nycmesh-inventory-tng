"""Registration codes and the devices they registered (docs/decisions/0009),
from the browser. Making or revoking a code, and revoking a device, are
sensitive: they ask for a fresh code (sign_in.ConfirmedAdmin).

A code's phrase is shown once, as it is made, with the link and QR code that
open the registration page with it filled in; only its hash is kept.
"""

from datetime import datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng import devices
from inventory_tng.accounts import qr_svg
from inventory_tng.admin.common import ADMIN, now, section
from inventory_tng.db import DbSession
from inventory_tng.local_time import site_zone
from inventory_tng.models import Device, RegistrationCode
from inventory_tng.pages import NOTICES, not_found, page, redirect
from inventory_tng.sign_in import AdminSignedIn, ConfirmedAdmin, SignedIn, changed

router = APIRouter(include_in_schema=False)

DEVICES = section(f"{ADMIN}/devices", "Devices")
CODES = f"{DEVICES}/codes"
MAX_USES = 500
MAX_DAYS = 90

State = Literal["open", "expired", "used up", "revoked"]

NOTICES.update(
    {
        "code_revoked": "Revoked: it registers nothing more. Its devices stay "
        "registered; revoke any that look wrong.",
        "device_revoked": "Revoked: it can't save anything now.",
    }
)


def state(code: RegistrationCode, at: datetime) -> State:
    if code.revoked_at is not None:
        return "revoked"
    if code.expires_at <= at:
        return "expired"
    if code.uses >= code.max_uses:
        return "used up"
    return "open"


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    codes = session.scalars(
        select(RegistrationCode).order_by(RegistrationCode.created_at.desc())
    ).all()
    counts = dict(
        session.execute(
            select(Device.registration_code_id, func.count()).group_by(
                Device.registration_code_id
            )
        ).all()
    )
    at = now()
    return page(
        request,
        "devices.html",
        {
            "title": "Devices",
            "admin": signed.admin,
            "codes": [
                (code, state(code, at), counts.get(code.id, 0)) for code in codes
            ],
            "zone": site_zone(session),
            "max_uses": MAX_USES,
            "max_days": MAX_DAYS,
            "problems": problems or {},
            "values": values or {},
        },
        status_code,
    )


def _whole(text: str, top: int) -> int | None:
    text = text.strip()
    return int(text) if text.isdigit() and 1 <= int(text) <= top else None


@router.get(DEVICES)
def codes(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    return _list_page(request, session, signed)


@router.post(CODES)
def make_code(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    uses: Annotated[str, Form()] = "1",
    days: Annotated[str, Form()] = "1",
    label: Annotated[str, Form()] = "",
) -> Response:
    form = {"uses": uses, "days": days, "label": label}
    problems = {}
    max_uses, lifetime = _whole(uses, MAX_USES), _whole(days, MAX_DAYS)
    if max_uses is None:
        problems["uses"] = f"From 1 to {MAX_USES} devices."
    if lifetime is None:
        problems["days"] = f"From 1 to {MAX_DAYS} days."
    if max_uses is None or lifetime is None:
        return _list_page(
            request, session, signed, problems=problems, values=form, status_code=400
        )
    code, phrase = devices.create_code(
        session,
        max_uses=max_uses,
        lifetime=timedelta(days=lifetime),
        label=label.strip() or None,
    )
    changed(signed, session)
    # SITE_URL when mail is set up; otherwise the site as the admin reached
    # it, which their own browser chose.
    mail = request.app.state.settings.mail
    site = mail.site_url if mail else str(request.base_url).rstrip("/")
    link = f"{site}{devices.REGISTER_PATH}?{urlencode({'code': phrase})}"
    return page(
        request,
        "code_made.html",
        {
            "title": "A new registration code",
            "admin": signed.admin,
            "code": code,
            "phrase": phrase,
            "link": link,
            "qr": qr_svg(link),
            "zone": site_zone(session),
        },
    )


@router.get(f"{CODES}/{{code_id}}")
def code(
    request: Request, signed: AdminSignedIn, session: DbSession, code_id: int
) -> Response:
    found = session.get(RegistrationCode, code_id)
    registered = devices.devices_registered_with(session, code_id)
    if found is None or registered is None:
        return not_found()
    return page(
        request,
        "code.html",
        {
            "title": found.label or f"Code {found.id}",
            "admin": signed.admin,
            "code": found,
            "state": state(found, now()),
            "devices": registered,
            "zone": site_zone(session),
        },
    )


@router.post(f"{CODES}/{{code_id}}/revoke")
def revoke_code(signed: ConfirmedAdmin, session: DbSession, code_id: int) -> Response:
    if not devices.revoke_code(session, code_id):
        return not_found()
    changed(signed, session)
    return redirect(f"{CODES}/{code_id}", notice="code_revoked")


@router.post(f"{DEVICES}/{{device_id}}/revoke")
def revoke_device(
    signed: ConfirmedAdmin, session: DbSession, device_id: int
) -> Response:
    device = session.get(Device, device_id)
    if device is None or not devices.revoke_device(session, device_id):
        return not_found()
    changed(signed, session)
    code_id = device.registration_code_id
    return redirect(f"{CODES}/{code_id}", notice="device_revoked")
