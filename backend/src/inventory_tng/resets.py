"""Resetting an account with a link (docs/decisions/0012): asked for by
mail from the sign-in page, by an administrator who gave an address, or
made by another administrator who reset the account's sign-in
(accounts.reset_sign_in); or, while the operator has set RECOVERY_TOKEN,
made with it at /recover for the password and authenticators.

Asking always gets the same answer, and everything that depends on whether
the account exists or has an address happens after the reply, so neither
the words nor the time taken tell anyone which accounts there are.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng import accounts, mail
from inventory_tng.db import DbSession, SessionFactory, Sessions
from inventory_tng.mail import Notify
from inventory_tng.models import ResetLink
from inventory_tng.pages import NOTICES, not_found, page, redirect
from inventory_tng.sign_in import FORGOT, RESET, SIGN_IN, TOO_MANY
from inventory_tng.web import rate_key

router = APIRouter(include_in_schema=False)

ASKED = (
    "If that account has an email address, a link to reset its password is on "
    "its way. It works once, within the hour. If none comes, ask another "
    "administrator to reset your sign-in."
)
NOTICES["reset"] = "Your account is reset: sign in."

RECOVER = "/recover"
REFUSED = {
    "bad_token": ("token", "That isn't the recovery token."),
    "used": ("token", "That token was used already: ask for a new one."),
    "no_admin": ("username", "No current administrator has that username."),
}


def _now() -> datetime:
    return datetime.now(UTC)


def _forgot_page(
    request: Request,
    *,
    notice: str | None = None,
    alert: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return page(
        request,
        "forgot.html",
        {
            "title": "Forgot your password?",
            "mail_on": request.app.state.settings.mail is not None,
            "notice": notice,
            "alert": alert,
        },
        status_code,
    )


@router.get(FORGOT)
def forgot_form(request: Request) -> Response:
    return _forgot_page(request)


@router.post(FORGOT)
def forgot(
    request: Request,
    sessions: Sessions,
    notify: Notify,
    username: Annotated[str, Form()] = "",
) -> Response:
    config = request.app.state.settings.mail
    if config is None or not notify.on:
        return _forgot_page(request, status_code=404)
    if not accounts.may_ask_for_reset(rate_key(request)):
        return _forgot_page(request, alert=TOO_MANY, status_code=429)
    site_url = config.site_url
    notify.later(lambda mailer: _email_reset(sessions, mailer, site_url, username))
    return _forgot_page(request, notice=ASKED)


def _email_reset(
    sessions: SessionFactory, mailer: mail.Mailer, site_url: str, username: str
) -> None:
    """After the reply, in a session of its own."""
    with sessions() as session:
        found = accounts.emailed_reset(session, username, _now())
        if found is None:
            return
        session.commit()
    link = f"{site_url}{RESET}?token={found.token}"
    mail.deliver(mailer, mail.reset_link(found.email, found.admin_id, link))


def _reset_page(
    request: Request,
    session: Session,
    token: str,
    link: ResetLink | None,
    *,
    problems: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """The form for a usable link, or, as a 404, why it is not."""
    return page(
        request,
        "reset.html",
        {
            "title": "Reset your account",
            "token": token,
            "link": link,
            "totp_required": link is not None
            and accounts.sign_in_settings(session).totp_required,
            "problems": problems or {},
        },
        status_code if link else 404,
    )


@router.get(RESET)
def reset_form(request: Request, session: DbSession, token: str = "") -> Response:
    found = accounts.find_reset_link(session, token, _now())
    return _reset_page(request, session, token, found[0] if found else None)


@router.post(RESET)
def reset(
    request: Request,
    session: DbSession,
    notify: Notify,
    token: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password_again: Annotated[str, Form()] = "",
) -> Response:
    now = _now()
    if not accounts.may_use_reset_link(rate_key(request)):
        found = accounts.find_reset_link(session, token, now)
        problems = {"password": TOO_MANY}
        link = found[0] if found else None
        return _reset_page(
            request, session, token, link, problems=problems, status_code=429
        )
    # Locked until the request's session ends, used or not.
    found = accounts.find_reset_link(session, token, now, lock=True)
    if found is None:
        return _reset_page(request, session, token, None)
    link, admin = found
    if problem := accounts.password_problem(password, password_again):
        problems = {"password": problem}
        return _reset_page(
            request, session, token, link, problems=problems, status_code=400
        )
    accounts.use_reset_link(session, link, password, now)
    session.commit()
    # An invite's link chooses the first password: nothing to warn about.
    if link.kind != "invite":
        notify.tell(admin.email, admin.id, mail.password_changed)
    return redirect(SIGN_IN, notice="reset")


def _recover_page(
    request: Request,
    *,
    username: str = "",
    problems: dict[str, str] | None = None,
    alert: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return page(
        request,
        "recover.html",
        {
            "title": "Recover an account",
            "here": RECOVER,
            "values": {"username": username},
            "problems": problems or {},
            "alert": alert,
        },
        status_code,
    )


@router.get(RECOVER)
def recover_form(request: Request) -> Response:
    if request.app.state.settings.recovery_token is None:
        return not_found()
    return _recover_page(request)


@router.post(RECOVER)
def recover(
    request: Request,
    session: DbSession,
    notify: Notify,
    token: Annotated[str, Form()] = "",
    username: Annotated[str, Form()] = "",
) -> Response:
    """Straight on to the link's page: whoever has the token is the one
    getting back in, or is with them."""
    expected = request.app.state.settings.recovery_token
    if expected is None:
        return not_found()
    try:
        admin, link = accounts.recover(
            session, expected, token, username, rate_key(request), _now()
        )
    except accounts.TokenRefused as refused:
        if refused.reason == "rate_limited":
            return _recover_page(
                request, username=username, alert=TOO_MANY, status_code=429
            )
        field, words = REFUSED[refused.reason]
        return _recover_page(
            request, username=username, problems={field: words}, status_code=400
        )
    session.commit()
    notify.tell(admin.email, admin.id, mail.recovery_link_made)
    return redirect(RESET, token=link)
