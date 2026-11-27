"""An administrator's own account page (docs/decisions/0012): email,
password, authenticators, recovery codes and sessions. Viewing it is not
sensitive; every change is (guards.ConfirmedAdmin), and is recorded as
theirs (db_sessions.set_actor). The rules are in accounts.py.
"""

from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng.admins import accounts, mail_messages
from inventory_tng.admins.guards import (
    ACCOUNT_TITLE,
    CONFIRM_FIELD,
    NAME_TAKEN,
    RECOVERY_CODES_TITLE,
    SENSITIVE,
    AdminSignedIn,
    ConfirmedAdmin,
    ConfirmedByAuthenticator,
    SignedIn,
    changed,
    code_field,
    signed_out,
)
from inventory_tng.admins.paths import ACCOUNT
from inventory_tng.core.db_errors import unique
from inventory_tng.core.db_sessions import DbSession
from inventory_tng.core.local_time import site_zone
from inventory_tng.core.mail import Notify
from inventory_tng.core.phrases import Words, gettext, lazy_gettext
from inventory_tng.models.admins import running_low
from inventory_tng.web.admin_pages import admin_page, now
from inventory_tng.web.pages import add_notices, not_found, page, redirect
from inventory_tng.web.single_use_forms import SingleUseKey, sent_before

router = APIRouter(include_in_schema=False)

EMAIL_CHANGED = lazy_gettext("Your email is changed.")
PASSWORD_CHANGED = lazy_gettext(
    "Your password is changed. Your other sessions have ended."
)
AUTHENTICATOR_RENAMED = lazy_gettext("Renamed.")
AUTHENTICATOR_REMOVED = lazy_gettext("Removed.")
TOTP_OFF = lazy_gettext("TOTP is off: your password alone signs you in.")
SESSION_ENDED = lazy_gettext("That session has ended.")
OTHERS_ENDED = lazy_gettext("Your other sessions have ended.")
add_notices(
    {
        "email": EMAIL_CHANGED,
        "password": PASSWORD_CHANGED,
        "renamed": AUTHENTICATOR_RENAMED,
        "removed": AUTHENTICATOR_REMOVED,
        "totp_off": TOTP_OFF,
        "session": SESSION_ENDED,
        "others": OTHERS_ENDED,
    }
)
EMAIL_TAKEN = lazy_gettext("Another administrator uses that address.")
LAST_REQUIRED = lazy_gettext(
    "TOTP is required here, so your last authenticator stays. Add another "
    "first to replace it."
)
CODES_NEED_TOTP = lazy_gettext("Recovery codes come with an authenticator.")


def _account_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    admin_id = signed.admin.id
    authenticators = accounts.authenticators(session, admin_id)
    left = accounts.recovery_codes_left(session, admin_id) if authenticators else 0
    return page(
        request,
        "admins/account.html",
        {
            "title": ACCOUNT_TITLE,
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
            "recovery_codes_left": left,
            "codes_low": running_low(left),
            "field": CONFIRM_FIELD,
            "sessions": accounts.current_sessions(session, admin_id, now()),
            "this_session": signed.row.id,
        },
        status_code,
    )


def _refused(
    request: Request,
    session: Session,
    signed: SignedIn,
    problems: Mapping[str, Words],
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    """The page again, with what was wrong, by box, and nothing of the
    change kept."""
    session.rollback()
    return _account_page(
        request,
        session,
        signed,
        problems=problems,
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
        return _refused(request, session, signed, {"email": problem}, values)
    if not unique(session, lambda: accounts.change_email(session, admin, email, now())):
        return _refused(request, session, signed, {"email": EMAIL_TAKEN}, values)
    response = _done(signed, session, "email", section="email")
    # Told at the old address, in case it was not them: someone with their
    # session could otherwise take resets over unseen.
    if old and old.lower() != email.lower():
        notify.tell(old, admin.id, mail_messages.email_changed)
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
    if problems := accounts.password_problems(password, password_again):
        return _refused(request, session, signed, problems)
    accounts.change_password(session, signed.row, password, now())
    response = _done(signed, session, "password", section="password")
    notify.tell(signed.admin.email, signed.admin.id, mail_messages.password_changed)
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
    # Refused, its box shows what was typed, beside its problem.
    values = {field: name}
    if problem := accounts.authenticator_name_problem(name):
        return _refused(request, session, signed, {field: problem}, values)
    found: list[bool] = []
    admin_id = signed.admin.id

    def rename() -> None:
        found.append(
            accounts.rename_authenticator(session, admin_id, authenticator_id, name)
        )

    if not unique(session, rename):
        return _refused(request, session, signed, {field: NAME_TAKEN}, values)
    if not found[0]:
        return not_found()  # Someone else's, or gone: ids reveal nothing.
    return _done(signed, session, "renamed", section="authenticators")


@router.get(f"{ACCOUNT}/authenticators/{{authenticator_id}}/remove")
def confirm_remove_authenticator(
    request: Request, signed: AdminSignedIn, session: DbSession, authenticator_id: int
) -> Response:
    """Asks first: removing cannot be undone, and the last one, while TOTP is
    optional, takes the recovery codes with it."""
    current = accounts.authenticators(session, signed.admin.id)
    outcome, found = accounts.removal(current, authenticator_id, signed.settings)
    if found is None:
        return not_found()  # Someone else's, or gone: ids reveal nothing.
    if outcome == "last_required":
        return _refused(request, session, signed, {"authenticators": LAST_REQUIRED})
    return admin_page(
        request,
        signed,
        "admins/authenticator_remove.html",
        {
            "title": gettext("Remove %(name)s?", name=found.name),
            "here": f"{ACCOUNT}/authenticators/{authenticator_id}/remove",
            "keep": f"{ACCOUNT}#authenticators",
            "authenticator": found,
            "totp_off": outcome == "totp_off",
            **code_field(signed, SENSITIVE),
        },
    )


@router.post(f"{ACCOUNT}/authenticators/{{authenticator_id}}/remove")
def remove_authenticator(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    notify: Notify,
    authenticator_id: int,
) -> Response:
    outcome = accounts.remove_authenticator(
        session, signed.admin.id, authenticator_id, now()
    )
    if outcome == "missing":
        return not_found()
    if outcome == "last_required":
        return _refused(request, session, signed, {"authenticators": LAST_REQUIRED})
    done = _done(signed, session, outcome, section="authenticators")
    notify.tell(
        signed.admin.email,
        signed.admin.id,
        mail_messages.authenticators_changed(added=False),
    )
    return done


@router.post(f"{ACCOUNT}/recovery-codes")
def new_recovery_codes(
    request: Request,
    signed: ConfirmedByAuthenticator,
    session: DbSession,
    notify: Notify,
    single_use: SingleUseKey = "",
) -> Response:
    """With a code from an authenticator each time (ConfirmedByAuthenticator),
    and told to the owner: new codes void the old."""
    if not signed.uses_totp:
        # Sent from a page shown before the last authenticator went, which
        # took the recovery codes section with it: said by the authenticators.
        problem = CODES_NEED_TOTP
        return _refused(request, session, signed, {"authenticators": problem})
    if again := sent_before(
        request, session, signed, single_use, "recovery_codes", ACCOUNT
    ):
        return again
    codes = accounts.make_recovery_codes(session, signed.admin.id, now())
    changed(signed, session)
    notify.tell(signed.admin.email, signed.admin.id, mail_messages.recovery_codes_made)
    return page(
        request,
        "admins/recovery_codes.html",
        {"title": RECOVERY_CODES_TITLE, "codes": codes, "admin": signed.admin},
    )


@router.post(f"{ACCOUNT}/sessions/{{session_id}}/end")
def end_session(
    signed: ConfirmedAdmin, session: DbSession, session_id: int
) -> Response:
    if not accounts.end_sessions(session, signed.admin.id, now(), only=session_id):
        return not_found()
    if session_id == signed.row.id:
        session.commit()
        return signed_out()
    return _done(signed, session, "session", section="sessions")


@router.post(f"{ACCOUNT}/sessions/end-others")
def end_other_sessions(signed: ConfirmedAdmin, session: DbSession) -> Response:
    accounts.end_sessions(session, signed.admin.id, now(), keep=signed.row.id)
    return _done(signed, session, "others", section="sessions")
