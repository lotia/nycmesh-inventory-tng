"""Registration codes and the devices they registered (docs/decisions/0009),
from the browser. Making or revoking a code, and revoking a device, are
sensitive: they ask for a fresh code (guards.ConfirmedAdmin).

A code's phrase is shown once, as it is made, with the link and QR code that
open the registration page with it filled in; only its hash is kept. Several
made together print on one sheet, and a lost printout is replaced by another
code like it (Ali, 2026-11-19).
"""

from datetime import datetime, timedelta
from typing import Annotated, Literal

import structlog
from babel.support import LazyProxy
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn, ConfirmedAdmin, SignedIn, changed
from inventory_tng.core.db_errors import attempt
from inventory_tng.core.db_sessions import DbSession
from inventory_tng.core.local_time import site_zone
from inventory_tng.core.phrases import (
    Words,
    lazy_gettext,
    lazy_pgettext,
    ngettext,
)
from inventory_tng.devices import devices, forms, queries
from inventory_tng.devices.paths import (
    BULK_REVOKE,
    CODES,
    DEVICE,
    DEVICE_LIST,
    DEVICES,
    REGISTER_PATH,
)
from inventory_tng.models.devices import Device, RegistrationCode
from inventory_tng.web.admin_pages import admin_page, now, revoke_title, site_link
from inventory_tng.web.forms import RULES, form_version
from inventory_tng.web.listing import (
    Asked,
    AskedView,
    Sort,
    Sorting,
    View,
    arranged,
)
from inventory_tng.web.listing import order as order_of
from inventory_tng.web.navigation import SECTION_DEVICES
from inventory_tng.web.pages import add_notices, not_found, redirect
from inventory_tng.web.qr_codes import qr_svg
from inventory_tng.web.single_use_forms import Made, SingleUseKey, sent_before

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

State = Literal["open", "expired", "used up", "revoked"]

CODE_REVOKED = lazy_gettext(
    "Revoked: it registers nothing more. Its devices stay registered; revoke "
    "any that look wrong."
)
DEVICE_REVOKED = lazy_gettext("Revoked: it can't save anything now.")
add_notices({"code_revoked": CODE_REVOKED, "device_revoked": DEVICE_REVOKED})
NEW_CODE_TITLE = lazy_gettext("A new registration code")
NEW_CODES_TITLE = lazy_gettext("New registration codes")
REGISTERED_DEVICES = lazy_gettext("Registered devices")


def revoke_many_title(many: int) -> str:
    return ngettext("Revoke %(num)s device?", "Revoke %(num)s devices?", many)


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
# (Ali, 2026-11-15, inventory-tng-qwb), a state by its words as shown.
CODE_SORTING = Sorting(
    "made",
    {
        "made": Sort.keyed(lambda row: row[0].created_at, first="desc"),
        "code": Sort.keyed(lambda row: row[0].name.lower()),
        "state": Sort.keyed(lambda row: str(STATE_WORDS[row[1]]).lower()),
        "used": Sort.keyed(lambda row: row[0].uses),
        "until": Sort.keyed(lambda row: row[0].expires_at),
    },
)
# What the codes' state filter offers: each state in the words the table
# shows it in (inventory-tng-qwb).
CODE_STATES: tuple[State, ...] = ("open", "used up", "expired", "revoked")
# Each state in words, as a filter or a tag says it.
STATE_WORDS: dict[State, LazyProxy] = {
    "open": lazy_pgettext("state", "Open"),
    "used up": lazy_gettext("Used up"),
    "expired": lazy_gettext("Expired"),
    "revoked": lazy_gettext("Revoked"),
}


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    view: View | None = None,
    problems: dict[str, Words] | None = None,
    values: dict[str, str] | None = None,
) -> HTMLResponse:
    view = view or View.read(DEVICES, CODE_SORTING)
    codes = queries.codes_newest_first(session)
    at = now()
    wanted = view.filtered("state") or None
    stated = ((code, state(code, at)) for code in codes)
    shown, view = arranged(
        (row for row in stated if wanted is None or row[1] == wanted), view
    )
    # How many devices each code on the page registered.
    counts = queries.devices_registered_by(session, [code.id for code, _ in shown])
    rows = [(code, said, counts.get(code.id, 0)) for code, said in shown]
    log.debug("codes listed", found=view.found, sort=view.by, page=view.page)
    return admin_page(
        request,
        signed,
        "devices/devices.html",
        {
            "title": SECTION_DEVICES,
            "here": DEVICES,
            "codes_path": CODES,
            "device_list": DEVICE_LIST,
            "view": view,
            "states": CODE_STATES,
            "state_words": STATE_WORDS,
            "codes": rows,
            "zone": site_zone(session),
            "max_uses": forms.MAX_USES,
            "max_days": forms.MAX_DAYS,
            "max_codes": forms.MAX_CODES,
            "problems": problems,
            "values": values,
        },
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
def make_codes(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    uses: Annotated[str, Form()] = "1",
    days: Annotated[str, Form()] = "1",
    label: Annotated[str, Form()] = "",
    count: Annotated[str, Form()] = "1",
    single_use: SingleUseKey = "",
) -> Response:
    """As many codes as asked, alike, shown together to print on one sheet
    (Ali, 2026-11-19)."""
    form = {"uses": uses, "days": days, "label": label, "count": count}
    settings, problems = forms.code_settings(uses, days, label, count)
    if settings is None:
        return _list_page(request, session, signed, problems=problems, values=form)
    made_what: Made = (
        "registration_code" if settings.count == 1 else "registration_codes"
    )
    if again := sent_before(request, session, signed, single_use, made_what, DEVICES):
        return again
    made = devices.create_codes(
        session,
        settings.count,
        max_uses=settings.max_uses,
        lifetime=timedelta(days=settings.days),
        label=settings.label,
    )
    changed(signed, session)
    return _made_page(request, signed, session, made, (DEVICES, SECTION_DEVICES))


def _made_page(
    request: Request,
    signed: SignedIn,
    session: Session,
    made: list[tuple[RegistrationCode, str]],
    back_link: tuple[str, Words],
) -> HTMLResponse:
    """New codes, alike, each with its phrase, link and QR code, shown only
    now, ready to print on one sheet."""
    links = [site_link(request, REGISTER_PATH, code=phrase) for _, phrase in made]
    return admin_page(
        request,
        signed,
        "devices/code_made.html",
        {
            "title": NEW_CODE_TITLE if len(made) == 1 else NEW_CODES_TITLE,
            "back_link": back_link,
            "codes": [
                (code, phrase, link, qr_svg(link))
                for (code, phrase), link in zip(made, links, strict=True)
            ],
            "register": site_link(request, REGISTER_PATH),
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
    at = now()
    return admin_page(
        request,
        signed,
        "devices/code.html",
        {
            "title": found.name,
            "here": f"{CODES}/{found.id}",
            "back_link": (DEVICES, SECTION_DEVICES),
            "code": found,
            "state": state(found, at),
            "state_words": STATE_WORDS,
            "has_left": devices.has_left(found, at),
            "devices": registered,
            "zone": site_zone(session),
        },
    )


@router.post(f"{CODES}/{{code_id}}/another")
def make_another(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    code_id: int,
    single_use: SingleUseKey = "",
) -> Response:
    """Another code like this one, with a phrase of its own, to print in
    place of a lost printout (Ali, 2026-11-19). One with nothing left goes
    back to its page, which says why."""
    found = session.get(RegistrationCode, code_id)
    if found is None:
        return not_found()
    here = f"{CODES}/{code_id}"
    if again := sent_before(
        request, session, signed, single_use, "registration_code", here
    ):
        return again
    made = devices.another_like(session, code_id)
    if made is None:
        return redirect(here)
    changed(signed, session)
    return _made_page(request, signed, session, [made], (here, found.name))


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
        "devices/code_revoke.html",
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


def _device_page(
    request: Request,
    signed: SignedIn,
    session: Session,
    device: Device,
    *,
    problems: dict[str, Words] | None = None,
    values: dict[str, str] | None = None,
    alert: Words | None = None,
) -> HTMLResponse:
    return admin_page(
        request,
        signed,
        "devices/device.html",
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
    if (problem := forms.label_problem(label)) is not None:
        problems = {"label": problem}
        values = {"label": label, "version": version}
        return _device_page(
            request, signed, session, device, problems=problems, values=values
        )
    seen = form_version(version)
    _, broken = attempt(
        session, lambda: devices.name_device(session, device, label or None, seen=seen)
    )
    if broken:
        return _device_page(request, signed, session, device, alert=RULES[broken])
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
        "devices/device_revoke_one.html",
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
    if not devices.revoke_device(session, device_id):
        return not_found()
    changed(signed, session)
    return redirect(f"{DEVICES}/{device_id}", notice="device_revoked")


# Every device, filtered, and revoking many at once (inventory-tng-ph9): after
# a compromise, the devices registered since, with codes the compromised
# administrators made, are the ones to look at.

DEVICES_REVOKED = lazy_gettext("Revoked: they can't save anything now.")
add_notices({"devices_revoked": DEVICES_REVOKED})


# How the devices list sorts: newest registered first, or by a heading,
# either way (Ali, 2026-11-15, inventory-tng-qwb).
SORTING = Sorting(
    "registered",
    {
        "registered": Sort.of(Device.created_at, first="desc"),
        "used": Sort.of(Device.last_seen_at, first="desc"),
    },
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
    chosen, problems, typed = forms.device_filter(session, since, until, code, state)
    view = _view(typed, asked)
    found: list[Device] = []
    if not problems:
        view = view.counted(queries.devices_found(session, chosen))
        found = queries.devices_chosen(
            session,
            chosen,
            order=order_of(view, Device.id),
            limit=view.per_page,
            offset=view.offset,
        )
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
        "devices/device_list.html",
        {
            "title": REGISTERED_DEVICES,
            "here": DEVICE_LIST,
            "revoke": BULK_REVOKE,
            "back_link": (DEVICES, SECTION_DEVICES),
            "codes_path": CODES,
            "devices_path": DEVICES,
            "filter": typed,
            "view": view,
            "states": list(forms.STATES),
            "devices": found,
            "zone": site_zone(session),
            "problems": problems or None,
        },
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
    single_use: SingleUseKey = "",
) -> Response:
    """Asks first, saying how many, then revokes those selected that the
    filter still matches, once."""
    chosen, problems, typed = forms.device_filter(session, since, until, code, state)
    # The list as it was, to come back to.
    query = dict(_view(typed, Asked(sort, order, per, page)).fields())
    current = [] if problems else devices.current_among(session, chosen, device or [])
    if not current:
        return redirect(DEVICE_LIST, **query)
    if sure != "yes":
        return admin_page(
            request,
            signed,
            "devices/device_revoke.html",
            {
                "title": revoke_many_title(len(current)),
                "here": BULK_REVOKE,
                "keep": DEVICE_LIST,
                "filter": query,
                "devices": current,
                "single_use": single_use,
            },
        )
    if again := sent_before(
        request, session, signed, single_use, "change", DEVICE_LIST
    ):
        return again
    devices.revoke_devices(session, chosen, [d.id for d in current], now())
    changed(signed, session)
    return redirect(DEVICE_LIST, notice="devices_revoked", **query)
