"""Administrators' sign-in pages (docs/decisions/0012): the first
administrator's setup, signing in and out, a new authenticator, and
confirming a sensitive action. What every administrator page depends on is
in admins/guards.py; the rules are in accounts.py.

The clock is guards._now, called through its module, so a test that holds
it there holds it for these pages too.
"""

from collections.abc import Mapping
from datetime import UTC
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from inventory_tng.admins import accounts, authenticator_totp, guards, mail_messages
from inventory_tng.admins.forms import (
    NOT_THE_TOKEN,
    authenticator_problems,
    setup_problems,
)
from inventory_tng.admins.guards import (
    ACCOUNT_TITLE,
    COOKIE,
    NAME_TAKEN,
    RECOVERY_CODES_TITLE,
    SENSITIVE,
    TOO_MANY,
    AnySignedIn,
    ConfirmedAny,
    Keys,
    SignedIn,
    _codes_left,
    _confirm,
    _confirm_page,
    _local,
    _refused,
    _status,
    changed,
    signed_out,
)
from inventory_tng.admins.paths import (
    ACCOUNT,
    CONFIRM,
    HOME,
    NEW_AUTHENTICATOR,
    SETUP,
    SIGN_IN,
    SIGN_OUT,
)
from inventory_tng.core import request_logging
from inventory_tng.core.db_errors import unique
from inventory_tng.core.db_sessions import DbSession
from inventory_tng.core.mail import Notify
from inventory_tng.core.phrases import Words, lazy_gettext
from inventory_tng.models.admins import Admin, AdminSession, running_low
from inventory_tng.web.browser import rate_key, set_private_cookie
from inventory_tng.web.pages import add_notices, not_found, page, redirect
from inventory_tng.web.qr_codes import qr_svg

router = APIRouter(include_in_schema=False)

DID_NOT_MATCH = lazy_gettext("That username, password or code didn't match.")
SET_UP_TITLE = lazy_gettext("Set up")
SIGN_IN_TITLE = lazy_gettext("Sign in")
AUTHENTICATOR_TITLE = lazy_gettext("Set up an authenticator")


def _set_cookie(
    request: Request, response: Response, token: str, row: AdminSession
) -> None:
    """Kept by the browser exactly as long as the session lasts."""
    set_private_cookie(
        request, response, COOKIE, token, expires=row.expires_at.astimezone(UTC)
    )


CODES_LOW = lazy_gettext(
    "You signed in with a recovery code, and few are left: make new ones now, "
    "with a code from your authenticator. Lost your phone? Set up a new "
    "authenticator first."
)
CODES_NONE = lazy_gettext(
    "That was your last recovery code. Make new ones now, with a code from "
    "your authenticator (lost your phone? set up a new one first): without "
    "them, a lost phone locks you out until another administrator resets your "
    "sign-in."
)
add_notices({"codes_low": CODES_LOW, "codes_none": CODES_NONE})


# The first administrator


def _totp_context(key: str, username: str, *, optional: bool) -> dict[str, Any]:
    return {
        "key": key,
        "key_for_typing": authenticator_totp.key_for_typing(key),
        "qr": qr_svg(authenticator_totp.totp_link(key, username or "administrator")),
        "totp_optional": optional,
    }


def _setup_page(
    request: Request,
    *,
    optional: bool,
    key: str | None = None,
    values: Mapping[str, str] | None = None,
    problems: Mapping[str, Words] | None = None,
    status_code: int = 200,
    alert: Words | None = None,
) -> HTMLResponse:
    values = values or {}
    totp = _totp_context(
        key or authenticator_totp.new_totp_key(),
        values.get("username", ""),
        optional=optional,
    )
    return page(
        request,
        "admins/setup.html",
        {"title": SET_UP_TITLE, "values": values, "problems": problems or {}}
        | {"alert": alert}
        | totp,
        status_code,
    )


def _setup_off(request: Request) -> HTMLResponse:
    return page(request, "admins/setup.html", {"title": SET_UP_TITLE, "off": True}, 403)


def _totp_optional(session: DbSession) -> bool:
    return accounts.sign_in_settings(session).totp_policy == "optional"


@router.get(SETUP)
def setup_form(request: Request, session: DbSession) -> Response:
    if not accounts.setup_open(session):
        return not_found()
    if request.app.state.settings.setup_token is None:
        return _setup_off(request)
    return _setup_page(request, optional=_totp_optional(session))


@router.post(SETUP)
def setup(
    request: Request,
    session: DbSession,
    keys: Keys,
    token: Annotated[str, Form()] = "",
    username: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password_again: Annotated[str, Form()] = "",
    authenticator_name: Annotated[str, Form()] = "",
    key: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
) -> Response:
    if not accounts.setup_open(session):
        return not_found()
    expected = request.app.state.settings.setup_token
    if expected is None:
        return _setup_off(request)
    username, email = username.strip(), email.strip()
    authenticator_name = authenticator_name.strip()
    key = (
        key
        if authenticator_totp.is_totp_key(key)
        else authenticator_totp.new_totp_key()
    )
    values = {
        "username": username,
        "email": email,
        "authenticator_name": authenticator_name,
    }
    optional = _totp_optional(session)
    try:
        accounts.check_setup_token(
            session,
            expected,
            token,
            rate_key(request),
            request.app.state.settings.rate_limits,
        )
    except accounts.TokenRefused as refused:
        limited = refused.reason == "rate_limited"
        return _setup_page(
            request,
            optional=optional,
            key=key,
            values=values,
            problems={} if limited else {"token": NOT_THE_TOKEN},
            alert=TOO_MANY if limited else None,
            status_code=429 if limited else 400,
        )
    values["token"] = token
    now = guards._now()
    problems = setup_problems(username, email, password, password_again)
    step = None
    if code.strip() or not optional:
        step = authenticator_totp.accepted_step(key, code, None, now)
        problems |= authenticator_problems(authenticator_name, step)
    if problems:
        return _setup_page(
            request,
            optional=optional,
            key=key,
            values=values,
            problems=problems,
            status_code=400,
        )
    try:
        first = accounts.create_first_admin(
            session,
            username=username,
            email=email,
            password=password,
            authenticator_name=authenticator_name,
            totp_key=key,
            step=step,
            user_agent=request.headers.get("user-agent"),
            now=now,
            keys=keys,
        )
    except accounts.TokenRefused:
        # Someone else finished setting up first, while this waited.
        return not_found()
    session.commit()
    if first.recovery_codes:
        response: Response = _recovery_codes_page(
            request, first.admin, first.recovery_codes
        )
    else:
        response = redirect(HOME)
    _set_cookie(request, response, first.token, first.row)
    return response


def _recovery_codes_page(
    request: Request, admin: Admin, codes: list[str]
) -> HTMLResponse:
    return page(
        request,
        "admins/recovery_codes.html",
        {"title": RECOVERY_CODES_TITLE, "codes": codes, "admin": admin},
    )


# Signing in and out


def _sign_in_page(
    request: Request,
    next: str,
    *,
    username: str = "",
    alert: Words | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return page(
        request,
        "admins/sign_in.html",
        {
            "title": SIGN_IN_TITLE,
            "next": _local(next),
            "username": username,
            "alert": alert,
            # Offered only where mail can be sent.
            "mail_on": request.app.state.settings.mail is not None,
        },
        status_code,
    )


@router.get(SIGN_IN)
def sign_in_form(request: Request, next: str = HOME) -> Response:
    return _sign_in_page(request, next)


@router.post(SIGN_IN)
def sign_in(
    request: Request,
    session: DbSession,
    notify: Notify,
    keys: Keys,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = HOME,
) -> Response:
    try:
        row, token, factor = accounts.sign_in(
            session,
            username,
            password,
            code,
            address=rate_key(request),
            user_agent=request.headers.get("user-agent"),
            now=guards._now(),
            keys=keys,
            limits=request.app.state.settings.rate_limits,
        )
    except accounts.SignInRefused as refused:
        limited = refused.reason == "rate_limited"
        return _sign_in_page(
            request,
            next,
            username=username,
            alert=str(TOO_MANY if limited else DID_NOT_MATCH),
            status_code=429 if limited else 400,
        )
    session.commit()
    request_logging.bind(admin_id=row.admin_id, sign_in_id=row.id)
    admin = session.get_one(Admin, row.admin_id)  # Loaded: no query.
    target = _local(next)
    response = redirect(target)
    if (left := _codes_left(session, admin, factor)) is not None:
        notify.tell(admin.email, admin.id, mail_messages.recovery_code_used(left))
        # Once, at this sign-in, so it is not lost among other pages: to the
        # account page, where new ones are made, unless a page was asked
        # for. Never shown before the sign-in is complete.
        if running_low(left) and target == HOME:
            accounts.reminded()
            notice = "codes_none" if left == 0 else "codes_low"
            response = redirect(ACCOUNT, notice=notice, section="recovery-codes")
    _set_cookie(request, response, token, row)
    return response


@router.post(SIGN_OUT)
def sign_out(signed: AnySignedIn, session: DbSession) -> Response:
    accounts.end_session(session, signed.row, guards._now())
    session.commit()
    return signed_out()


# Confirming a sensitive action


@router.get(CONFIRM)
def confirm_form(request: Request, signed: AnySignedIn, next: str = HOME) -> Response:
    carried = [("next", _local(next))]
    return _confirm_page(request, signed, action=CONFIRM, carried=carried)


@router.post(CONFIRM)
def confirm(
    request: Request,
    signed: AnySignedIn,
    session: DbSession,
    notify: Notify,
    keys: Keys,
    confirm_code: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = HOME,
) -> Response:
    outcome = _confirm(
        keys,
        session,
        signed,
        confirm_code,
        notify,
        SENSITIVE,
        request.app.state.settings.rate_limits,
    )
    if outcome == "confirmed":
        return redirect(_local(next))
    return _confirm_page(
        request,
        signed,
        action=CONFIRM,
        carried=[("next", _local(next))],
        alert=_refused(outcome, signed),
        status_code=_status(outcome),
    )


# Pages


def _new_authenticator_page(
    request: Request,
    signed: SignedIn,
    *,
    key: str | None = None,
    name: str | None = None,
    problems: Mapping[str, Words] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    totp = _totp_context(
        key or authenticator_totp.new_totp_key(), signed.admin.username, optional=False
    )
    return page(
        request,
        "admins/authenticator_new.html",
        {
            "title": AUTHENTICATOR_TITLE,
            "admin": signed.admin,
            "required": signed.needs_totp_setup,
            # Set up when first signing in, there is nowhere yet to go back to.
            "back_link": None if signed.needs_totp_setup else (ACCOUNT, ACCOUNT_TITLE),
            "values": {} if name is None else {"authenticator_name": name},
            "problems": problems or {},
        }
        | totp,
        status_code,
    )


@router.get(NEW_AUTHENTICATOR)
def new_authenticator_form(request: Request, signed: ConfirmedAny) -> Response:
    return _new_authenticator_page(request, signed)


@router.post(NEW_AUTHENTICATOR)
def new_authenticator(
    request: Request,
    signed: ConfirmedAny,
    session: DbSession,
    notify: Notify,
    keys: Keys,
    authenticator_name: Annotated[str, Form()] = "",
    key: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
) -> Response:
    admin_id = signed.admin.id
    name = authenticator_name.strip()
    key = (
        key
        if authenticator_totp.is_totp_key(key)
        else authenticator_totp.new_totp_key()
    )
    now = guards._now()
    # One at a time per administrator, so a second click cannot add the
    # same name twice, or make a second set of recovery codes: the gate
    # (confirmed) holds their row.
    step = authenticator_totp.accepted_step(key, code, None, now)
    problems = authenticator_problems(name, step)
    # Checked here too, though the insert below would catch it, so a taken
    # name is told alongside a wrong code.
    if "authenticator_name" not in problems and accounts.authenticator_named(
        session, admin_id, name
    ):
        problems["authenticator_name"] = NAME_TAKEN
    # Recovery codes come with the first authenticator, unless some are
    # left: set up again after the operator retired the old one, the printed
    # codes still work, and making new ones would void them.
    first = not accounts.uses_totp(session, admin_id)
    first = first and not accounts.has_recovery_codes(session, admin_id)
    if not problems:
        assert step is not None  # A wrong code is a problem.
        # In a savepoint (unique): PostgreSQL may compare names its own way.
        if not unique(
            session,
            lambda: accounts.add_authenticator(
                session, keys, admin_id, name, key, step
            ),
            admin_id=admin_id,
        ):
            problems["authenticator_name"] = NAME_TAKEN
    if problems:
        session.rollback()
        return _new_authenticator_page(
            request, signed, key=key, name=name, problems=problems, status_code=400
        )
    codes = accounts.make_recovery_codes(session, admin_id, now) if first else []
    changed(signed, session)
    if codes:
        notify.tell(signed.admin.email, admin_id, mail_messages.recovery_codes_made)
    notify.tell(
        signed.admin.email, admin_id, mail_messages.authenticators_changed(added=True)
    )
    if codes:
        return _recovery_codes_page(request, signed.admin, codes)
    return redirect(HOME)
