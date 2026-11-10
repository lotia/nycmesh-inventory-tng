"""An administrator's own account page (docs/decisions/0012): email,
password, authenticators, recovery codes and sessions. Viewing it is not
sensitive; every change is (sign_in.ConfirmedAdmin), and is recorded as
theirs (db.set_actor). The rules are in accounts.py.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng import accounts, mail
from inventory_tng.db import DbSession, unique
from inventory_tng.local_time import site_zone
from inventory_tng.mail import Notify
from inventory_tng.once import OnceKey, sent_before
from inventory_tng.pages import NOTICES, not_found, page, redirect
from inventory_tng.sign_in import (
    ACCOUNT,
    NAME_TAKEN,
    AdminSignedIn,
    ConfirmedAdmin,
    SignedIn,
    changed,
    signed_out,
)

router = APIRouter(include_in_schema=False)

NOTICES.update(
    {
        "email": "Your email is changed.",
        "password": "Your password is changed. Your other sessions have ended.",
        "renamed": "Renamed.",
        "removed": "Removed.",
        "totp_off": "TOTP is off: your password alone signs you in.",
        "session": "That session has ended.",
        "others": "Your other sessions have ended.",
    }
)


def _now() -> datetime:
    return datetime.now(UTC)


def _account_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    admin_id = signed.admin.id
    authenticators = accounts.authenticators(session, admin_id)
    return page(
        request,
        "account.html",
        {
            "title": "Your account",
            "admin": signed.admin,
            "zone": site_zone(session),
            "problems": problems or {},
            "values": values or {},
            "totp_optional": signed.settings.totp_policy == "optional",
            "authenticators": [
                (a, accounts.step_time(a.last_step) if a.last_step else None)
                for a in authenticators
            ],
            # Shown only with an authenticator.
            "recovery_codes_left": accounts.recovery_codes_left(session, admin_id)
            if authenticators
            else 0,
            "sessions": accounts.current_sessions(session, admin_id, _now()),
            "this_session": signed.row.id,
        },
        status_code,
    )


def _refused(
    request: Request,
    session: Session,
    signed: SignedIn,
    field: str,
    problem: str,
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    """The page again, with what was wrong, and nothing of the change kept."""
    session.rollback()
    return _account_page(
        request,
        session,
        signed,
        problems={field: problem},
        values=values,
        status_code=400,
    )


def _done(signed: SignedIn, session: Session, notice: str, section: str) -> Response:
    """Back to the page, saying `notice` by `section`'s heading."""
    changed(signed, session)
    return redirect(ACCOUNT, notice=notice, section=section)


@router.get(ACCOUNT)
def account(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    return _account_page(request, session, signed)


@router.post(f"{ACCOUNT}/email")
def change_email(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    notify: Notify,
    email: Annotated[str, Form()] = "",
) -> Response:
    email = email.strip()
    admin, old = signed.admin, signed.admin.email
    values = {"email": email}
    if problem := accounts.email_problem(email):
        return _refused(request, session, signed, "email", problem, values)
    if not unique(
        session, lambda: accounts.change_email(session, admin, email, _now())
    ):
        taken = "Another administrator uses that address."
        return _refused(request, session, signed, "email", taken, values)
    response = _done(signed, session, "email", section="email")
    # Told at the old address, in case it was not them: someone with their
    # session could otherwise take resets over unseen.
    if old and old.lower() != email.lower():
        notify.tell(old, admin.id, mail.email_changed)
    return response


@router.post(f"{ACCOUNT}/password")
def change_password(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    notify: Notify,
    password: Annotated[str, Form()] = "",
    password_again: Annotated[str, Form()] = "",
) -> Response:
    if problem := accounts.password_problem(password, password_again):
        return _refused(request, session, signed, "password", problem)
    accounts.change_password(session, signed.row, password, _now())
    response = _done(signed, session, "password", section="password")
    notify.tell(signed.admin.email, signed.admin.id, mail.password_changed)
    return response


@router.post(f"{ACCOUNT}/authenticators/{{authenticator_id}}/name")
def rename_authenticator(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    authenticator_id: int,
    authenticator_name: Annotated[str, Form()] = "",
) -> Response:
    name = authenticator_name.strip()
    field = f"authenticator_{authenticator_id}"
    if problem := accounts.authenticator_name_problem(name):
        return _refused(request, session, signed, field, problem)
    found: list[bool] = []
    admin_id = signed.admin.id

    def rename() -> None:
        found.append(
            accounts.rename_authenticator(session, admin_id, authenticator_id, name)
        )

    if not unique(session, rename):
        return _refused(request, session, signed, field, NAME_TAKEN)
    if not found[0]:
        return not_found()  # Someone else's, or gone: ids reveal nothing.
    return _done(signed, session, "renamed", section="authenticators")


@router.post(f"{ACCOUNT}/authenticators/{{authenticator_id}}/remove")
def remove_authenticator(
    request: Request, signed: ConfirmedAdmin, session: DbSession, authenticator_id: int
) -> Response:
    outcome = accounts.remove_authenticator(
        session, signed.admin.id, authenticator_id, _now()
    )
    if outcome == "missing":
        return not_found()
    if outcome == "last_required":
        problem = (
            "TOTP is required here, so your last authenticator stays. "
            "Add another first to replace it."
        )
        return _refused(request, session, signed, "authenticators", problem)
    return _done(signed, session, outcome, section="authenticators")


@router.post(f"{ACCOUNT}/recovery-codes")
def new_recovery_codes(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    once: OnceKey = "",
) -> Response:
    if not signed.uses_totp:
        problem = "Recovery codes come with an authenticator."
        return _refused(request, session, signed, "recovery_codes", problem)
    if again := sent_before(request, session, signed, once, "recovery_codes", ACCOUNT):
        return again
    codes = accounts.make_recovery_codes(session, signed.admin.id, _now())
    changed(signed, session)
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
        return not_found()
    if session_id == signed.row.id:
        session.commit()
        return signed_out()
    return _done(signed, session, "session", section="sessions")


@router.post(f"{ACCOUNT}/sessions/end-others")
def end_other_sessions(signed: ConfirmedAdmin, session: DbSession) -> Response:
    accounts.end_sessions(session, signed.admin.id, _now(), keep=signed.row.id)
    return _done(signed, session, "others", section="sessions")
