"""An administrator's own account page (docs/decisions/0012): email,
password, authenticators, recovery codes and sessions. Viewing it is not
sensitive; every change is (sign_in.ConfirmedAdmin). The rules are in
accounts.py.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from inventory_tng import accounts
from inventory_tng.db import DbSession, set_actor
from inventory_tng.local_time import site_zone
from inventory_tng.pages import page, redirect
from inventory_tng.sign_in import (
    ACCOUNT,
    COOKIE,
    NAME_TAKEN,
    SIGN_IN,
    AdminSignedIn,
    ConfirmedAdmin,
    SignedIn,
)

router = APIRouter(include_in_schema=False)

# What a change says once done, by the ?done= the page is sent back with.
DONE = {
    "email": "Your email is changed.",
    "password": "Your password is changed. Your other sessions have ended.",
    "renamed": "Renamed.",
    "removed": "Removed.",
    "totp_off": "TOTP is off: your password alone signs you in.",
    "session": "That session has ended.",
    "others": "Your other sessions have ended.",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _account_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    done: str | None = None,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    admin_id = signed.admin.id
    return page(
        request,
        "account.html",
        {
            "title": "Your account",
            "admin": signed.admin,
            "zone": site_zone(session),
            "notice": DONE.get(done or ""),
            "problems": problems or {},
            "values": values or {},
            "totp_optional": signed.settings.totp_policy == "optional",
            "authenticators": [
                (a, _last_used(a.last_step))
                for a in accounts.authenticators(session, admin_id)
            ],
            "recovery_codes_left": accounts.recovery_codes_left(session, admin_id),
            "sessions": accounts.current_sessions(session, admin_id, _now()),
            "this_session": signed.row.id,
        },
        status_code,
    )


def _last_used(step: int | None) -> datetime | None:
    """When a code from it was last accepted, to the 30-second step."""
    return None if step is None else datetime.fromtimestamp(step * 30, UTC)


def _done(what: str) -> Response:
    return redirect(f"{ACCOUNT}?done={what}")


def _changed(signed: SignedIn, session: Session) -> None:
    """After a sensitive change: counted for the confirmation window, and
    committed."""
    accounts.note_sensitive(signed.row, _now())
    session.commit()


@router.get(ACCOUNT)
def account(
    request: Request, signed: AdminSignedIn, session: DbSession, done: str = ""
) -> Response:
    return _account_page(request, session, signed, done=done)


@router.post(f"{ACCOUNT}/email")
def change_email(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    email: Annotated[str, Form()] = "",
) -> Response:
    email = email.strip()
    problem = accounts.email_problem(email)
    if problem is None:
        set_actor(session, f"admin:{signed.admin.id}")
        try:
            with session.begin_nested():
                accounts.change_email(session, signed.admin, email)
        except IntegrityError:
            problem = "Another administrator uses that address."
    if problem is not None:
        session.rollback()
        return _account_page(
            request,
            session,
            signed,
            problems={"email": problem},
            values={"email": email},
            status_code=400,
        )
    _changed(signed, session)
    return _done("email")


@router.post(f"{ACCOUNT}/password")
def change_password(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    password: Annotated[str, Form()] = "",
    password_again: Annotated[str, Form()] = "",
) -> Response:
    problem = accounts.password_problem(password, password_again)
    if problem is not None:
        return _account_page(
            request, session, signed, problems={"password": problem}, status_code=400
        )
    set_actor(session, f"admin:{signed.admin.id}")
    accounts.change_password(session, signed.row, password, _now())
    _changed(signed, session)
    return _done("password")


@router.post(f"{ACCOUNT}/authenticators/{{authenticator_id}}/name")
def rename_authenticator(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    authenticator_id: int,
    authenticator_name: Annotated[str, Form()] = "",
) -> Response:
    name = authenticator_name.strip()
    problem = None
    if not 1 <= len(name) <= accounts.AUTHENTICATOR_NAME_MAX:
        problem = f"Use 1 to {accounts.AUTHENTICATOR_NAME_MAX} characters."
    else:
        set_actor(session, f"admin:{signed.admin.id}")
        try:
            with session.begin_nested():
                found = accounts.rename_authenticator(
                    session, signed.admin.id, authenticator_id, name
                )
        except IntegrityError:
            problem = NAME_TAKEN
        else:
            if not found:
                return _not_yours()
    if problem is not None:
        session.rollback()
        return _account_page(
            request,
            session,
            signed,
            problems={f"authenticator_{authenticator_id}": problem},
            status_code=400,
        )
    _changed(signed, session)
    return _done("renamed")


@router.post(f"{ACCOUNT}/authenticators/{{authenticator_id}}/remove")
def remove_authenticator(
    request: Request, signed: ConfirmedAdmin, session: DbSession, authenticator_id: int
) -> Response:
    set_actor(session, f"admin:{signed.admin.id}")
    outcome = accounts.remove_authenticator(
        session, signed.admin.id, authenticator_id, _now()
    )
    if outcome == "missing":
        return _not_yours()
    if outcome == "last_required":
        session.rollback()
        return _account_page(
            request,
            session,
            signed,
            problems={
                "authenticators": "TOTP is required here, so your last "
                "authenticator stays. Add another first to replace it."
            },
            status_code=400,
        )
    totp_off = not accounts.uses_totp(session, signed.admin.id)
    _changed(signed, session)
    return _done("totp_off" if totp_off else "removed")


@router.post(f"{ACCOUNT}/recovery-codes")
def new_recovery_codes(
    request: Request, signed: ConfirmedAdmin, session: DbSession
) -> Response:
    if not signed.uses_totp:
        return _account_page(
            request,
            session,
            signed,
            problems={"recovery_codes": "Recovery codes come with an authenticator."},
            status_code=400,
        )
    set_actor(session, f"admin:{signed.admin.id}")
    codes = accounts.make_recovery_codes(session, signed.admin.id, _now())
    _changed(signed, session)
    return page(
        request,
        "recovery_codes.html",
        {"title": "Your recovery codes", "codes": codes, "admin": signed.admin},
    )


@router.post(f"{ACCOUNT}/sessions/{{session_id}}/end")
def end_session(
    signed: ConfirmedAdmin, session: DbSession, session_id: int
) -> Response:
    if not accounts.end_sessions(session, signed.admin.id, _now(), only=session_id):
        return _not_yours()
    _changed(signed, session)
    if session_id == signed.row.id:
        response = redirect(SIGN_IN)
        response.delete_cookie(COOKIE)
        return response
    return _done("session")


@router.post(f"{ACCOUNT}/sessions/end-others")
def end_other_sessions(signed: ConfirmedAdmin, session: DbSession) -> Response:
    accounts.end_sessions(session, signed.admin.id, _now(), keep=signed.row.id)
    _changed(signed, session)
    return _done("others")


def _not_yours() -> HTMLResponse:
    """Someone else's, or gone: the same answer, so ids reveal nothing."""
    return HTMLResponse("Not found", status_code=404)
