"""Administrators (docs/decisions/0012): the list and what changed lately,
inviting one, resetting one's sign-in with a link, retiring, and bringing
back. Each asks for a code typed within the sign-in settings'
account_change_minutes (sign_in.ConfirmedAccountChange), and is told to
every administrator (activity.py).

A link is shown once, to pass on however the administrator likes; only its
hash is kept. Whoever it is for is told by mail, if they gave an address.
"""

from collections.abc import Mapping
from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng import accounts, activity, mail
from inventory_tng.admin.common import ADMINS, admin_page, now, rule_words, site_link
from inventory_tng.db import DbSession, attempt
from inventory_tng.local_time import site_zone
from inventory_tng.mail import Notify
from inventory_tng.models import Admin
from inventory_tng.once import OnceKey, sent_before
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import (
    CONFIRM_FIELD,
    RESET,
    AdminSignedIn,
    ConfirmedAccountChange,
    SignedIn,
    changed,
)

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

TAKEN = "Another administrator already has that username or email."
OWN_RESET = "Change your own on your account page."
OWN_RETIRE = "Another administrator must retire you."

NOTICES.update(
    {
        "admin_retired": "Retired: they can no longer sign in, and their password, "
        "authenticators and recovery codes are removed.",
    }
)


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


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    admins = session.scalars(select(Admin).order_by(func.lower(Admin.username))).all()
    return admin_page(
        request,
        signed,
        "admins.html",
        {
            "title": "Administrators",
            "here": ADMINS,
            "current": [a for a in admins if a.retired_at is None],
            "retired": [a for a in admins if a.retired_at is not None],
            "with_totp": accounts.admins_using_totp(session),
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
    problem: str | None = None,
) -> HTMLResponse:
    return admin_page(
        request,
        signed,
        "admin.html",
        {
            "title": other.username,
            "here": f"{ADMINS}/{other.id}",
            "list": ADMINS,
            "other": other,
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
            "title": f"A link for {other.username}",
            "list": ADMINS,
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
    own: str,
) -> Response:
    """Not found, or their own page saying `own` (as they acted on
    themselves)."""
    if refused.reason == "not_found":
        return not_found()
    return _admin_page(request, session, signed, signed.admin, own)


@router.get(ADMINS)
def admins(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    return _list_page(request, session, signed)


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
    problems = {}
    if problem := accounts.username_problem(username):
        problems["username"] = problem
    if problem := accounts.email_problem(email):
        problems["email"] = problem
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
    notify.tell(other.email, other.id, mail.invited)
    return _link_page(request, signed, other, token, "invite")


@router.get(f"{ADMINS}/{{admin_id}}")
def admin(
    request: Request, signed: AdminSignedIn, session: DbSession, admin_id: int
) -> Response:
    other = session.get(Admin, admin_id)
    if other is None:
        return not_found()
    return _admin_page(request, session, signed, other)


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
    # One who never chose a password is invited again instead.
    told = mail.invited if kind == "invite" else mail.reset_link_made
    notify.tell(other.email, other.id, told)
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
        other = accounts.retire_admin(session, admin_id, signed.admin.id, now(), notify)
    except accounts.AdminRefused as refused:
        return _refused(request, session, signed, refused, OWN_RETIRE)
    changed(signed, session)
    notify.tell(other.email, other.id, mail.retired)
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
    notify.tell(other.email, other.id, mail.restored)
    return _link_page(request, signed, other, token, "invite", brought_back=True)
