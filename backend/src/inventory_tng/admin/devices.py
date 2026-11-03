"""Registration codes and the devices they registered (docs/decisions/0009),
from the browser. Making or revoking a code, and revoking a device, are
sensitive: they ask for a fresh code (sign_in.ConfirmedAdmin).

A code's phrase is shown once, as it is made, with the link and QR code that
open the registration page with it filled in; only its hash is kept.
"""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng import devices
from inventory_tng.accounts import qr_svg
from inventory_tng.admin.common import ADMIN, admin_page, now, site_link, whole
from inventory_tng.db import DbSession
from inventory_tng.local_time import site_zone
from inventory_tng.models import Device, RegistrationCode
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, ConfirmedAdmin, SignedIn, changed

router = APIRouter(include_in_schema=False)

DEVICES = f"{ADMIN}/devices"
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
    return admin_page(
        request,
        signed,
        "devices.html",
        {
            "title": "Devices",
            "here": DEVICES,
            "codes_path": CODES,
            "codes": [
                (code, state(code, at), counts.get(code.id, 0)) for code in codes
            ],
            "zone": site_zone(session),
            "max_uses": MAX_USES,
            "max_days": MAX_DAYS,
            "problems": problems,
            "values": values,
        },
        400 if problems else 200,
    )


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
    max_uses, lifetime = whole(uses, 1, MAX_USES), whole(days, 1, MAX_DAYS)
    if max_uses is None:
        problems["uses"] = f"From 1 to {MAX_USES} devices."
    if lifetime is None:
        problems["days"] = f"From 1 to {MAX_DAYS} days."
    if max_uses is None or lifetime is None:
        return _list_page(request, session, signed, problems=problems, values=form)
    code, phrase = devices.create_code(
        session,
        max_uses=max_uses,
        lifetime=timedelta(days=lifetime),
        label=label.strip() or None,
    )
    changed(signed, session)
    link = site_link(request, devices.REGISTER_PATH, code=phrase)
    return admin_page(
        request,
        signed,
        "code_made.html",
        {
            "title": "A new registration code",
            "list": DEVICES,
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
    return admin_page(
        request,
        signed,
        "code.html",
        {
            "title": found.label or f"Code {found.id}",
            "here": f"{CODES}/{found.id}",
            "list": DEVICES,
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
