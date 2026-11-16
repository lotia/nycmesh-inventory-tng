"""Registration codes and the devices they registered (docs/decisions/0009),
from the browser. Making or revoking a code, and revoking a device, are
sensitive: they ask for a fresh code (sign_in.ConfirmedAdmin).

A code's phrase is shown once, as it is made, with the link and QR code that
open the registration page with it filled in; only its hash is kept.
"""

from datetime import datetime, timedelta
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng import devices
from inventory_tng.accounts import qr_svg
from inventory_tng.admin.common import (
    ADMIN,
    NAME_MAX,
    RULES,
    SECTION_DEVICES,
    admin_page,
    form_version,
    now,
    revoke_title,
    site_link,
    up_to,
)
from inventory_tng.admin.listing import (
    Asked,
    AskedView,
    Sort,
    Sorting,
    View,
    arranged,
    count,
    ordered,
)
from inventory_tng.db import DbSession, attempt
from inventory_tng.local_time import parse_local, site_zone
from inventory_tng.models import Device, RegistrationCode, whole
from inventory_tng.once import OnceKey, sent_before
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.phrases import gettext, lazy_gettext, ngettext
from inventory_tng.sign_in import AdminSignedIn, ConfirmedAdmin, SignedIn, changed

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

DEVICES = f"{ADMIN}/devices"
CODES = f"{DEVICES}/codes"
MAX_USES = 500
MAX_DAYS = 90

State = Literal["open", "expired", "used up", "revoked"]

CODE_REVOKED = lazy_gettext(
    "Revoked: it registers nothing more. Its devices stay registered; revoke "
    "any that look wrong."
)
DEVICE_REVOKED = lazy_gettext("Revoked: it can't save anything now.")
NOTICES.update({"code_revoked": CODE_REVOKED, "device_revoked": DEVICE_REVOKED})
NEW_CODE_TITLE = lazy_gettext("A new registration code")
REGISTERED_DEVICES = lazy_gettext("Registered devices")


def uses_problem(most: int) -> str:
    return gettext("From 1 to %(max)d devices.", max=most)


def days_problem(most: int) -> str:
    return gettext("From 1 to %(max)d days.", max=most)


def revoke_many_title(many: int) -> str:
    return ngettext("Revoke %(num)d device?", "Revoke %(num)d devices?", many)


def state(code: RegistrationCode, at: datetime) -> State:
    if code.revoked_at is not None:
        return "revoked"
    if code.expires_at <= at:
        return "expired"
    if code.uses >= code.max_uses:
        return "used up"
    return "open"


# How the codes list sorts, each row a code, its state and how many
# devices it registered: newest first, as it always has, or by a heading
# (Ali, 2026-11-15, inventory-tng-qwb).
CODE_SORTING = Sorting(
    "made",
    {
        "made": Sort.keyed(lambda row: row[0].created_at, first="desc"),
        "code": Sort.keyed(lambda row: row[0].name.lower()),
        "state": Sort.keyed(lambda row: row[1]),
        "used": Sort.keyed(lambda row: row[0].uses),
        "until": Sort.keyed(lambda row: row[0].expires_at),
    },
)
# What the codes' state filter offers: each state in the words the table
# shows it in (inventory-tng-qwb).
CODE_STATES: tuple[State, ...] = ("open", "used up", "expired", "revoked")


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    view: View | None = None,
    problems: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
) -> HTMLResponse:
    view = view or View.read(DEVICES, CODE_SORTING)
    codes = session.scalars(
        select(RegistrationCode).order_by(RegistrationCode.created_at.desc())
    ).all()
    at = now()
    wanted = view.filtered("state") or None
    stated = ((code, state(code, at)) for code in codes)
    shown, view = arranged(
        (row for row in stated if wanted is None or row[1] == wanted), view
    )
    # How many devices each code on the page registered.
    counts = dict(
        session.execute(
            select(Device.registration_code_id, func.count())
            .where(Device.registration_code_id.in_([code.id for code, _ in shown]))
            .group_by(Device.registration_code_id)
        ).all()
    )
    rows = [(code, said, counts.get(code.id, 0)) for code, said in shown]
    log.debug("codes listed", found=view.found, sort=view.by, page=view.page)
    return admin_page(
        request,
        signed,
        "devices.html",
        {
            "title": SECTION_DEVICES,
            "here": DEVICES,
            "codes_path": CODES,
            "device_list": DEVICE_LIST,
            "view": view,
            "states": CODE_STATES,
            "codes": rows,
            "zone": site_zone(session),
            "max_uses": MAX_USES,
            "max_days": MAX_DAYS,
            "problems": problems,
            "values": values,
        },
        400 if problems else 200,
    )


@router.get(DEVICES)
def codes(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    asked: AskedView,
    state: str = "",
) -> Response:
    """The codes, filtered by state (Ali, 2026-11-15): one not offered is
    none."""
    view = View.read(
        DEVICES,
        CODE_SORTING,
        asked,
        filters={"state": state if state in CODE_STATES else ""},
    )
    return _list_page(request, session, signed, view=view)


@router.post(CODES)
def make_code(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    uses: Annotated[str, Form()] = "1",
    days: Annotated[str, Form()] = "1",
    label: Annotated[str, Form()] = "",
    once: OnceKey = "",
) -> Response:
    form = {"uses": uses, "days": days, "label": label}
    label = label.strip()
    problems = {}
    max_uses, lifetime = whole(uses, 1, MAX_USES), whole(days, 1, MAX_DAYS)
    if max_uses is None:
        problems["uses"] = uses_problem(MAX_USES)
    if lifetime is None:
        problems["days"] = days_problem(MAX_DAYS)
    # Any longer would not fit the printed page.
    if len(label) > NAME_MAX:
        problems["label"] = up_to(NAME_MAX)
    if problems or max_uses is None or lifetime is None:
        return _list_page(request, session, signed, problems=problems, values=form)
    if again := sent_before(
        request, session, signed, once, "registration_code", DEVICES
    ):
        return again
    code, phrase = devices.create_code(
        session,
        max_uses=max_uses,
        lifetime=timedelta(days=lifetime),
        label=label or None,
    )
    changed(signed, session)
    link = site_link(request, devices.REGISTER_PATH, code=phrase)
    return admin_page(
        request,
        signed,
        "code_made.html",
        {
            "title": NEW_CODE_TITLE,
            "back_link": (DEVICES, SECTION_DEVICES),
            "code": code,
            "phrase": phrase,
            "link": link,
            "register": site_link(request, devices.REGISTER_PATH),
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
            "title": found.name,
            "here": f"{CODES}/{found.id}",
            "back_link": (DEVICES, SECTION_DEVICES),
            "code": found,
            "state": state(found, now()),
            "devices": registered,
            "zone": site_zone(session),
        },
    )


@router.get(f"{CODES}/{{code_id}}/revoke")
def confirm_revoke_code(
    request: Request, signed: AdminSignedIn, session: DbSession, code_id: int
) -> Response:
    """Asks first: revoking cannot be undone."""
    found = session.get(RegistrationCode, code_id)
    if found is None or found.revoked_at is not None:
        return not_found()
    return admin_page(
        request,
        signed,
        "code_revoke.html",
        {
            "title": revoke_title(found.name),
            "here": f"{CODES}/{code_id}/revoke",
            "keep": f"{CODES}/{code_id}",
            "code": found,
        },
    )


@router.post(f"{CODES}/{{code_id}}/revoke")
def revoke_code(signed: ConfirmedAdmin, session: DbSession, code_id: int) -> Response:
    if not devices.revoke_code(session, code_id):
        return not_found()
    changed(signed, session)
    return redirect(f"{CODES}/{code_id}", notice="code_revoked")


# A device's own page (inventory-tng-8i4.60.13): what is known of it, its
# name, and revoking it alone, after a confirmation. `:int`, so the list's
# own paths under DEVICES are never taken for a device's.
DEVICE = f"{DEVICES}/{{device_id:int}}"


def _device_page(
    request: Request,
    signed: SignedIn,
    session: Session,
    device: Device,
    *,
    problems: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
    alert: str | None = None,
) -> HTMLResponse:
    return admin_page(
        request,
        signed,
        "device.html",
        {
            "title": device.name,
            "here": f"{DEVICES}/{device.id}",
            "back_link": (DEVICE_LIST, REGISTERED_DEVICES),
            "codes_path": CODES,
            "device": device,
            "zone": site_zone(session),
            "problems": problems,
            "values": values,
            "alert": alert,
        },
    )


@router.get(DEVICE)
def device_page(
    request: Request, signed: AdminSignedIn, session: DbSession, device_id: int
) -> Response:
    device = session.get(Device, device_id)
    if device is None:
        return not_found()
    return _device_page(request, signed, session, device)


@router.post(DEVICE)
def name_device(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    device_id: int,
    label: Annotated[str, Form()] = "",
    version: Annotated[str, Form()] = "",
) -> Response:
    device = session.get(Device, device_id)
    if device is None:
        return not_found()
    label = label.strip()
    if len(label) > NAME_MAX:
        problems = {"label": up_to(NAME_MAX)}
        values = {"label": label, "version": version}
        return _device_page(
            request, signed, session, device, problems=problems, values=values
        )
    seen = form_version(version)
    _, broken = attempt(
        session, lambda: devices.name_device(session, device, label or None, seen=seen)
    )
    if broken:
        return _device_page(request, signed, session, device, alert=str(RULES[broken]))
    session.commit()
    return redirect(f"{DEVICES}/{device_id}", notice="saved")


@router.get(f"{DEVICE}/revoke")
def revoke_device_page(
    request: Request, signed: AdminSignedIn, session: DbSession, device_id: int
) -> Response:
    """Asks first: revoking cannot be undone."""
    device = session.get(Device, device_id)
    if device is None or device.revoked_at is not None:
        return not_found()
    return admin_page(
        request,
        signed,
        "device_revoke_one.html",
        {
            "title": revoke_title(device.name),
            "here": f"{DEVICES}/{device.id}/revoke",
            "keep": f"{DEVICES}/{device.id}",
            "device": device,
        },
    )


@router.post(f"{DEVICE}/revoke")
def revoke_device(
    signed: ConfirmedAdmin, session: DbSession, device_id: int
) -> Response:
    if session.get(Device, device_id) is None or not devices.revoke_device(
        session, device_id
    ):
        return not_found()
    changed(signed, session)
    return redirect(f"{DEVICES}/{device_id}", notice="device_revoked")


# Every device, filtered, and revoking many at once (inventory-tng-ph9): after
# a compromise, the devices registered since, with codes the compromised
# administrators made, are the ones to look at.

DEVICE_LIST = f"{DEVICES}/all"
# Not under DEVICE_LIST: {DEVICES}/{device_id}/revoke would take it.
BULK_REVOKE = f"{DEVICES}/revoke"
STATES: dict[str, bool | None] = {"current": True, "revoked": False, "all": None}

DEVICES_REVOKED = lazy_gettext("Revoked: they can't save anything now.")
NOTICES["devices_revoked"] = DEVICES_REVOKED


A_TIME = lazy_gettext("A date and time, such as 2026-11-02 13:05.")
AFTER_FROM = lazy_gettext("A time after the one it is from.")
A_CODE_NUMBER = lazy_gettext("A code's number.")


# How the devices list sorts: newest registered first, or by a heading,
# either way (Ali, 2026-11-15, inventory-tng-qwb).
SORTING = Sorting(
    "registered",
    {
        "registered": Sort.of(Device.created_at, first="desc"),
        "used": Sort.of(Device.last_seen_at, first="desc"),
    },
)


def _chosen(
    session: Session, since: str, until: str, code: str, shown: str
) -> tuple[devices.DeviceFilter, dict[str, str], dict[str, str]]:
    """The filter a form or link chose, what was wrong with it, and the
    filter as the page carries it, each field as typed (an unknown `shown`
    is current). `until` takes in the whole minute it names."""
    shown = shown if shown in STATES else "current"
    zone = site_zone(session)
    problems: dict[str, str] = {}
    start = end = None
    if since.strip() and (start := parse_local(since, zone)) is None:
        problems["since"] = str(A_TIME)
    if until.strip():
        end = parse_local(until, zone)
        if end is None:
            problems["until"] = str(A_TIME)
        elif start is not None and end < start:
            problems["until"] = str(AFTER_FROM)
        else:
            # Whatever seconds were typed. At the last minute a datetime
            # holds, no end at all.
            try:
                end = end.replace(second=0, microsecond=0) + timedelta(minutes=1)
            except OverflowError:
                end = None
    code_id = whole(code, 1, 2**31 - 1) if code.strip() else None
    if code.strip() and code_id is None:
        problems["code"] = str(A_CODE_NUMBER)
    chosen = devices.DeviceFilter(
        since=start, until=end, code_id=code_id, current=STATES[shown]
    )
    return (
        chosen,
        problems,
        {"since": since, "until": until, "code": code, "state": shown},
    )


def _view(typed: dict[str, str], asked: Asked) -> View:
    """The list's view, its filter as typed: state current not said."""
    filters = {
        name: value
        for name, value in typed.items()
        if (name, value) != ("state", "current")
    }
    return View.read(DEVICE_LIST, SORTING, asked, filters=filters)


@router.get(DEVICE_LIST)
def device_list(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    asked: AskedView,
    since: str = "",
    until: str = "",
    code: str = "",
    state: str = "current",
) -> Response:
    chosen, problems, typed = _chosen(session, since, until, code, state)
    view = _view(typed, asked)
    found: list[Device] = []
    if not problems:
        query = devices.find_devices(chosen)
        view = view.counted(count(session, query))
        found = list(session.scalars(ordered(query, view, Device.id)))
    log.debug(
        "devices listed",
        found=view.found,
        page=view.page,
        sort=view.by,
        per_page=view.per_page,
    )
    return admin_page(
        request,
        signed,
        "device_list.html",
        {
            "title": REGISTERED_DEVICES,
            "here": DEVICE_LIST,
            "revoke": BULK_REVOKE,
            "back_link": (DEVICES, SECTION_DEVICES),
            "codes_path": CODES,
            "devices_path": DEVICES,
            "filter": typed,
            "view": view,
            "states": list(STATES),
            "devices": found,
            "zone": site_zone(session),
            "problems": problems or None,
        },
        400 if problems else 200,
    )


@router.post(BULK_REVOKE)
def revoke_devices(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    device: Annotated[list[int] | None, Form()] = None,
    since: Annotated[str, Form()] = "",
    until: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
    state: Annotated[str, Form()] = "current",
    sort: Annotated[str, Form()] = "",
    order: Annotated[str, Form()] = "",
    per: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "",
    sure: Annotated[str, Form()] = "",
    once: OnceKey = "",
) -> Response:
    """Asks first, saying how many, then revokes those selected that the
    filter still matches, once."""
    chosen, problems, typed = _chosen(session, since, until, code, state)
    # The list as it was, to come back to.
    query = dict(_view(typed, Asked(sort, order, per, page)).fields())
    current = [] if problems else devices.current_among(session, chosen, device or [])
    if not current:
        return redirect(DEVICE_LIST, **query)
    if sure != "yes":
        return admin_page(
            request,
            signed,
            "device_revoke.html",
            {
                "title": revoke_many_title(len(current)),
                "here": BULK_REVOKE,
                "keep": DEVICE_LIST,
                "filter": query,
                "devices": current,
                "once": once,
            },
        )
    if again := sent_before(request, session, signed, once, "change", DEVICE_LIST):
        return again
    devices.revoke_devices(session, chosen, [d.id for d in current], now())
    changed(signed, session)
    return redirect(DEVICE_LIST, notice="devices_revoked", **query)
