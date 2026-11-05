"""Administrators (docs/decisions/0012): the list, inviting one, resetting
one's sign-in with a link, retiring and bringing back. Every change here is
sensitive, so it asks for a fresh code (sign_in.ConfirmedAdmin).

A link is shown once, to pass on however the administrator likes; only its
hash is kept. Whoever it is for is told by mail, if they gave an address.
"""

from collections.abc import Mapping
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng import accounts, mail
from inventory_tng.admin.common import ADMINS, admin_page, now, rule_words, site_link
from inventory_tng.db import DbSession, attempt
from inventory_tng.mail import Notify
from inventory_tng.models import Admin
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import (
    RESET,
    AdminSignedIn,
    ConfirmedAdmin,
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
        "admin_retired": "Retired: they can no longer sign in.",
    }
)


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
            "problems": {"admin": problem} if problem else None,
        },
        400 if problem else 200,
    )


def _link_page(
    request: Request,
    signed: SignedIn,
    other: Admin,
    token: str,
    kind: Literal["invited", "reset"],
) -> HTMLResponse:
    return admin_page(
        request,
        signed,
        "link_made.html",
        {
            "title": f"A link for {other.username}",
            "list": ADMINS,
            "other": other,
            "link": site_link(request, RESET, token=token),
            "kind": kind,
        },
    )


@router.get(ADMINS)
def admins(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    return _list_page(request, session, signed)


@router.post(ADMINS)
def invite(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    notify: Notify,
    username: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
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
            accounts.invite(session, username, email, signed.admin.id, now())
        )

    try:
        if not problems and (broken := attempt(session, make)):
            problems["username"] = rule_words(broken, TAKEN)
    except accounts.LinkRefused:
        return not_found()
    if problems:
        values = {"username": username, "email": email}
        return _list_page(request, session, signed, problems=problems, values=values)
    other, token = invited[0]
    changed(signed, session)
    notify.tell(other.email, other.id, mail.invited)
    return _link_page(request, signed, other, token, kind="invited")


@router.get(f"{ADMINS}/{{admin_id}}")
def admin(
    request: Request, signed: AdminSignedIn, session: DbSession, admin_id: int
) -> Response:
    other = session.get(Admin, admin_id)
    if other is None:
        return not_found()
    return _admin_page(request, session, signed, other)


@router.post(f"{ADMINS}/{{admin_id}}/reset")
def reset_sign_in(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    notify: Notify,
    admin_id: int,
) -> Response:
    other = session.get(Admin, admin_id)
    if other is None:
        return not_found()
    try:
        other, token, invited = accounts.make_link_for(
            session, admin_id, signed.admin.id, now()
        )
    except accounts.LinkRefused as refused:
        if refused.reason == "not_found":
            return not_found()
        return _admin_page(request, session, signed, other, OWN_RESET)
    changed(signed, session)
    notify.tell(
        other.email, other.id, mail.invited if invited else mail.reset_link_made
    )
    return _link_page(request, signed, other, token, "invited" if invited else "reset")


@router.post(f"{ADMINS}/{{admin_id}}/retire")
def retire(
    request: Request, signed: ConfirmedAdmin, session: DbSession, admin_id: int
) -> Response:
    outcome = accounts.retire_admin(session, admin_id, signed.admin.id, now())
    if outcome == "not_found":
        return not_found()
    if outcome == "themselves":
        other = session.get_one(Admin, admin_id)
        return _admin_page(request, session, signed, other, OWN_RETIRE)
    changed(signed, session)
    return redirect(ADMINS, notice="admin_retired")


@router.post(f"{ADMINS}/{{admin_id}}/restore")
def restore(
    signed: ConfirmedAdmin, session: DbSession, notify: Notify, admin_id: int
) -> Response:
    """Their old password and authenticators work again, so they are told:
    whoever retired them for a leak makes them a reset link too."""
    if not accounts.restore_admin(session, admin_id, signed.admin.id, now()):
        return not_found()
    changed(signed, session)
    other = session.get_one(Admin, admin_id)
    notify.tell(other.email, other.id, mail.restored)
    return redirect(f"{ADMINS}/{admin_id}", notice="restored")
