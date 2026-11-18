"""Administrators (docs/decisions/0012): the list and what changed lately,
inviting one, resetting one's sign-in with a link, retiring, and bringing
back. Each asks for a code typed within the sign-in settings'
account_change_minutes (guards.ConfirmedAccountChange), and is told to
every administrator (activity.py).

A link is shown once, to pass on however the administrator likes; only its
hash is kept. Whoever it is for is told by mail, if they gave an address.
"""

from collections.abc import Mapping
from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng import accounts, activity
from inventory_tng.admin.common import (
    ADMINS,
    SECTION_ADMINS,
    admin_page,
    now,
    retirement,
    site_link,
)
from inventory_tng.admin.listing import AskedView, Sort, Sorting, View, arranged
from inventory_tng.admins import queries
from inventory_tng.admins.forms import OWN_RESET, OWN_RETIRE, TAKEN, invite_problems
from inventory_tng.admins.guards import (
    CONFIRM_FIELD,
    RESET,
    AdminSignedIn,
    ConfirmedAccountChange,
    SignedIn,
    changed,
)
from inventory_tng.core.db import DbSession, attempt
from inventory_tng.core.local_time import site_zone
from inventory_tng.core.mail import Notify
from inventory_tng.core.phrases import Words, gettext, lazy_gettext
from inventory_tng.models import Admin
from inventory_tng.once import OnceKey, sent_before
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.web.forms import rule_words

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

ADMIN_RETIRED = lazy_gettext(
    "Retired: they can no longer sign in, and their password, authenticators "
    "and recovery codes are removed."
)
NOTICES["admin_retired"] = ADMIN_RETIRED


def link_title(username: str) -> str:
    return gettext("A link for %(username)s", username=username)


def reset_title(username: str) -> str:
    return gettext("Reset %(username)s's sign-in?", username=username)


def _code_field(signed: SignedIn) -> dict[str, object]:
    """For a form here: whether it needs a code (none was typed within
    account_change_minutes), and whose kind, the one acting's."""
    return {
        "code_needed": not accounts.account_change_open(
            signed.row, signed.settings, now()
        ),
        "code_totp": signed.asks_code,
        "field": CONFIRM_FIELD,
    }


# How the list of administrators sorts, each row an Admin and whether it
# uses an authenticator: by name, as it always has, or by a heading, those
# with one first (Ali, 2026-11-15, inventory-tng-qwb).
SORTING = Sorting(
    "name",
    {
        "name": Sort.keyed(lambda row: row[0].username.lower()),
        "email": Sort.keyed(lambda row: row[0].email is not None, first="desc"),
        "authenticator": Sort.keyed(lambda row: row[1], first="desc"),
    },
)


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    view: View | None = None,
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    admins = queries.admins_by_username(session)
    with_totp = accounts.admins_using_totp(session)
    rows, view = arranged(
        ((a, a.id in with_totp) for a in admins if a.retired_at is None),
        view or View.read(ADMINS, SORTING),
    )
    log.debug("admins listed", found=view.found, sort=view.by, page=view.page)
    return admin_page(
        request,
        signed,
        "admins.html",
        {
            "title": SECTION_ADMINS,
            "here": ADMINS,
            "view": view,
            "current": [a for a, _ in rows],
            "retired": [a for a in admins if a.retired_at is not None],
            "with_totp": with_totp,
            "with_password": accounts.admins_with_passwords(session),
            **_code_field(signed),
            "changes": activity.recent(session),
            "zone": site_zone(session),
            "problems": problems,
            "values": values,
        },
        400 if problems else 200,
    )


def _admin_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    other: Admin,
    problem: Words | None = None,
) -> HTMLResponse:
    return admin_page(
        request,
        signed,
        "admin.html",
        {
            "title": other.username,
            "here": f"{ADMINS}/{other.id}",
            "back_link": (ADMINS, SECTION_ADMINS),
            "other": other,
            **retirement(session, other),
            "uses_totp": accounts.uses_totp(session, other.id),
            "has_password": accounts.has_password(session, other.id),
            "themselves": other.id == signed.admin.id,
            **_code_field(signed),
            "problems": {"admin": problem} if problem else None,
        },
        400 if problem else 200,
    )


def _link_page(
    request: Request,
    signed: SignedIn,
    other: Admin,
    token: str,
    kind: accounts.LinkKind,
    *,
    brought_back: bool = False,
) -> HTMLResponse:
    link = site_link(request, RESET, token=token)
    return admin_page(
        request,
        signed,
        "link_made.html",
        {
            "title": link_title(other.username),
            "back_link": (ADMINS, SECTION_ADMINS),
            "other": other,
            "link": link,
            "qr": accounts.qr_svg(link),
            "kind": kind,
            "brought_back": brought_back,
        },
    )


def _refused(
    request: Request,
    session: Session,
    signed: SignedIn,
    refused: accounts.AdminRefused,
    own: Words,
) -> Response:
    """Not found, or their own page saying `own` (as they acted on
    themselves)."""
    if refused.reason == "not_found":
        return not_found()
    return _admin_page(request, session, signed, signed.admin, own)


@router.get(ADMINS)
def admins(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    asked: AskedView,
) -> Response:
    view = View.read(ADMINS, SORTING, asked)
    return _list_page(request, session, signed, view=view)


@router.post(ADMINS)
def invite(
    request: Request,
    signed: ConfirmedAccountChange,
    session: DbSession,
    notify: Notify,
    username: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    once: OnceKey = "",
) -> Response:
    username, email = username.strip(), email.strip()
    problems = invite_problems(username, email)
    invited: list[tuple[Admin, str]] = []

    def make() -> None:
        invited.append(
            accounts.invite(session, username, email, signed.admin.id, now(), notify)
        )

    if not problems and (
        again := sent_before(request, session, signed, once, "link", ADMINS)
    ):
        return again
    try:
        if not problems and (broken := attempt(session, make)[1]):
            problems["username"] = rule_words(broken, TAKEN)
    except accounts.AdminRefused:
        return not_found()
    if problems:
        values = {"username": username, "email": email}
        return _list_page(request, session, signed, problems=problems, values=values)
    other, token = invited[0]
    changed(signed, session)
    return _link_page(request, signed, other, token, "invite")


@router.get(f"{ADMINS}/{{admin_id}}")
def admin(
    request: Request, signed: AdminSignedIn, session: DbSession, admin_id: int
) -> Response:
    other = session.get(Admin, admin_id)
    if other is None:
        return not_found()
    return _admin_page(request, session, signed, other)


@router.get(f"{ADMINS}/{{admin_id}}/reset")
def confirm_reset(
    request: Request, signed: AdminSignedIn, session: DbSession, admin_id: int
) -> Response:
    """Resetting can't be undone and signs them out at once: say so first."""
    other = session.get(Admin, admin_id)
    if other is None or other.retired_at is not None:
        return not_found()
    if other.id == signed.admin.id:
        return _admin_page(request, session, signed, other, OWN_RESET)
    return admin_page(
        request,
        signed,
        "admin_reset.html",
        {
            "title": reset_title(other.username),
            "here": f"{ADMINS}/{admin_id}/reset",
            "keep": f"{ADMINS}/{admin_id}",
            "other": other,
            **_code_field(signed),
        },
    )


@router.post(f"{ADMINS}/{{admin_id}}/reset")
def reset(
    request: Request,
    signed: ConfirmedAccountChange,
    session: DbSession,
    notify: Notify,
    admin_id: int,
    once: OnceKey = "",
) -> Response:
    if again := sent_before(
        request, session, signed, once, "link", f"{ADMINS}/{admin_id}"
    ):
        return again
    try:
        other, token, kind = accounts.reset_admin_sign_in(
            session, admin_id, signed.admin.id, now(), notify
        )
    except accounts.AdminRefused as refused:
        return _refused(request, session, signed, refused, OWN_RESET)
    changed(signed, session)
    return _link_page(request, signed, other, token, kind)


@router.post(f"{ADMINS}/{{admin_id}}/retire")
def retire(
    request: Request,
    signed: ConfirmedAccountChange,
    session: DbSession,
    notify: Notify,
    admin_id: int,
    once: OnceKey = "",
) -> Response:
    if again := sent_before(
        request, session, signed, once, "change", f"{ADMINS}/{admin_id}"
    ):
        return again
    try:
        accounts.retire_admin(session, admin_id, signed.admin.id, now(), notify)
    except accounts.AdminRefused as refused:
        return _refused(request, session, signed, refused, OWN_RETIRE)
    changed(signed, session)
    return redirect(ADMINS, notice="admin_retired", section="retired")


@router.post(f"{ADMINS}/{{admin_id}}/restore")
def restore(
    request: Request,
    signed: ConfirmedAccountChange,
    session: DbSession,
    notify: Notify,
    admin_id: int,
    once: OnceKey = "",
) -> Response:
    if again := sent_before(
        request, session, signed, once, "link", f"{ADMINS}/{admin_id}"
    ):
        return again
    try:
        other, token = accounts.restore_admin(
            session, admin_id, signed.admin.id, now(), notify
        )
    except accounts.AdminRefused:
        # Whatever the reason: oneself is never retired, so never found here.
        return not_found()
    changed(signed, session)
    return _link_page(request, signed, other, token, "invite", brought_back=True)
