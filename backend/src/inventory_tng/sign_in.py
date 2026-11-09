"""Administrators' sign-in pages, and what every administrator page depends
on (docs/decisions/0012): the first administrator's setup, signing in and
out, a new authenticator, and confirming a sensitive action. The rules are
in accounts.py.

An administrator page takes one of:

- `AdminSignedIn`: signed in, with TOTP set up if it is required.
- `ConfirmedAdmin`: that, for a sensitive action, with a code typed recently
  (accounts.confirmation_open); otherwise the page asks for one first. Once
  the action is done, the route calls changed(), which commits.

A form through ConfirmedAdmin (or ConfirmedAny) also holds every
administrator's lock (accounts.lock_admins) until it commits, so it runs
one at a time with the others and with resets, retires and restores
(inventory-tng-8i4.50.4). It commits once, at most: a commit after that
fails (db.hold_until_commit). Work after the reply opens its own session.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, cast
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from inventory_tng import accounts, mail, request_logging
from inventory_tng.db import (
    DbSession,
    hold_until_commit,
    record_last_seen,
    set_actor,
)
from inventory_tng.mail import Notify
from inventory_tng.masking import is_secret_name
from inventory_tng.models import Admin, AdminSession, SignInSettings
from inventory_tng.pages import (
    Badge,
    not_found,
    page,
    redirect,
    sections,
    templates,
)
from inventory_tng.web import rate_key, set_private_cookie

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

COOKIE = "inventory_admin"
HOME = "/admin"
SETUP = "/setup"
SIGN_IN = "/admin/sign-in"
SIGN_OUT = "/admin/sign-out"
CONFIRM = "/admin/confirm"
NEW_AUTHENTICATOR = "/admin/authenticators/new"
ACCOUNT = "/admin/account"
FORGOT = "/admin/forgot"
RESET = "/admin/reset"

# What the templates link to and limit, from here, so neither drifts.
# Jinja types its globals by its own defaults' types; any value will do.
cast(dict[str, Any], templates.env.globals).update(
    paths={
        "home": HOME,
        "setup": SETUP,
        "sign_in": SIGN_IN,
        "sign_out": SIGN_OUT,
        "confirm": CONFIRM,
        "new_authenticator": NEW_AUTHENTICATOR,
        "account": ACCOUNT,
        "forgot": FORGOT,
        "reset": RESET,
    },
    limits={
        "password_min": accounts.PASSWORD_MIN,
        "username_max": accounts.USERNAME_MAX,
        "authenticator_name_max": accounts.AUTHENTICATOR_NAME_MAX,
        "code_max": accounts.CODE_FIELD_MAX,
        "totp_code_max": accounts.TOTP_FIELD_MAX,
    },
)

TOO_MANY = "Too many tries. Wait a few minutes, then try again."
NAME_TAKEN = "You already have one by that name."


def _now() -> datetime:
    return datetime.now(UTC)


def _local(path: str) -> str:
    """Where to go after signing in or confirming: only a path on this site,
    so a link cannot send an administrator elsewhere."""
    if path.startswith("/") and not path.startswith(("//", "/\\")):
        return path
    return HOME


def _here(request: Request) -> str:
    query = request.url.query
    return request.url.path + (f"?{query}" if query else "")


def _set_cookie(
    request: Request, response: Response, token: str, row: AdminSession
) -> None:
    """Kept by the browser exactly as long as the session lasts."""
    set_private_cookie(
        request, response, COOKIE, token, expires=row.expires_at.astimezone(UTC)
    )


# What administrator pages depend on


@dataclass
class SignedIn:
    admin: Admin
    row: AdminSession
    settings: SignInSettings
    uses_totp: bool

    @property
    def must_set_up_totp(self) -> bool:
        return self.settings.totp_policy == "required" and not self.uses_totp


class Redirect(Exception):
    """Sends the browser elsewhere first: to sign in, set up TOTP or confirm
    a sensitive action."""

    def __init__(self, location: str) -> None:
        super().__init__(location)
        self.location = location


def _then(path: str, request: Request) -> str:
    """`path`, asked to come back here after. A form sent is not sent again:
    it comes back to its page."""
    back = _here(request) if request.method == "GET" else request.url.path
    return f"{path}?next={quote(back)}"


def signed_in(request: Request, session: DbSession) -> SignedIn:
    """Signed in, TOTP or not. Only the pages that set TOTP up, confirm and
    sign out take this; others take AdminSignedIn."""
    token = request.cookies.get(COOKIE)
    now = _now()
    found = accounts.find_session(session, token, now) if token else None
    if found is None:
        raise Redirect(_then(SIGN_IN, request) if request.method == "GET" else SIGN_IN)
    row, admin = found
    if row.last_seen_at <= now - accounts.SEEN_EVERY:
        record_last_seen(request.app.state.engine, AdminSession, row.id, now)
    # sign_in_id, not admin_session_id: the masker hides any field naming a
    # session, as a session id usually is the secret cookie.
    request_logging.bind(admin_id=admin.id, sign_in_id=row.id)
    # Every change this request makes is theirs, in the history.
    set_actor(session, f"admin:{admin.id}")
    return SignedIn(
        admin=admin,
        row=row,
        settings=accounts.sign_in_settings(session),
        uses_totp=accounts.uses_totp(session, admin.id),
    )


AnySignedIn = Annotated[SignedIn, Depends(signed_in)]


def admin_signed_in(signed: AnySignedIn) -> SignedIn:
    if signed.must_set_up_totp:
        raise Redirect(NEW_AUTHENTICATOR)
    return signed


AdminSignedIn = Annotated[SignedIn, Depends(admin_signed_in)]


# The field a confirmation's code comes in, which no form uses for itself.
CONFIRM_FIELD = "confirm_code"


class ConfirmHere(Exception):
    """A form sent once the window had closed: shown again as a code field,
    carrying what was typed, so it goes on once the code is accepted. Its
    navigation is read as it is made, a query, so off the event loop
    (run_in_threadpool): the page is drawn once the request's session has
    closed."""

    def __init__(
        self,
        signed: SignedIn,
        carried: list[tuple[str, str]],
        alert: str | None = None,
        status_code: int = 403,
    ) -> None:
        super().__init__()
        self.signed, self.carried = signed, carried
        self.alert, self.status_code = alert, status_code
        self.sections = sections(signed.admin)


async def confirmed(
    request: Request,
    signed: AnySignedIn,
    session: DbSession,
    notify: Notify,
) -> SignedIn:
    """Within the window, goes on; viewing a page keeps nothing open. A page
    asked for once it has closed sends the browser to confirm first. A form
    sent once it has closed is asked for a code there and then (ConfirmHere),
    and goes on when one comes with it.

    A form then passes the gate (_hold), after the commit a code makes, as
    the module's docstring says."""
    if request.method == "GET":
        if not accounts.confirmation_open(signed.row, signed.settings, _now()):
            raise Redirect(_then(CONFIRM, request))
        return signed
    # The whole body first, as Starlette keeps it: a slow sender holds no
    # lock while it trickles in.
    form = await request.form()
    carried = [
        (name, value)
        for name, value in form.multi_items()
        if isinstance(value, str) and name != CONFIRM_FIELD
    ]
    was_open = accounts.confirmation_open(signed.row, signed.settings, _now())
    if not was_open:
        typed = form.get(CONFIRM_FIELD)
        if not isinstance(typed, str):
            raise await run_in_threadpool(ConfirmHere, signed, carried)
        # Its queries, and argon2's work, off the event loop.
        outcome = await run_in_threadpool(_confirm, session, signed, typed, notify)
        if outcome != "confirmed":
            raise await run_in_threadpool(
                ConfirmHere,
                signed,
                carried,
                _refused(outcome, signed),
                _status(outcome),
            )
    signed = await run_in_threadpool(_hold, session, signed)
    # The window, again, with what the lock read: shorter settings may have
    # come first.
    if was_open and not accounts.confirmation_open(signed.row, signed.settings, _now()):
        log.info("form asked for a code", admin_id=signed.admin.id, reason="waited")
        raise await run_in_threadpool(ConfirmHere, signed, carried)
    hold_until_commit(session)
    return signed


def _hold(session: Session, signed: SignedIn) -> SignedIn:
    """The gate (accounts.hold_signed_in), then what the request knew of
    them read again, as a reset, a retire or a settings change may have
    come first."""
    if not accounts.hold_signed_in(session, signed.row, _now):
        log.warning("form refused", admin_id=signed.admin.id, reason="signed_out")
        raise Redirect(SIGN_IN)
    # Their admin row and session were read again with the lock.
    session.refresh(signed.settings)
    return dataclasses.replace(
        signed, uses_totp=accounts.uses_totp(session, signed.admin.id)
    )


def _confirm(
    session: Session, signed: SignedIn, typed: str, notify: Notify
) -> accounts.Confirmation:
    """Confirm, keep it, and tell the owner if a recovery code did it."""
    outcome, factor = accounts.confirm(session, signed.row, typed, _now())
    if outcome == "confirmed":
        left = _codes_left(session, signed.admin, factor)
        session.commit()
        # Now, with nothing locked or open, not after the reply: the form may
        # yet end in an error page, though the code is used up.
        if left is not None:
            notify.tell_now(
                signed.admin.email, signed.admin.id, mail.recovery_code_used(left)
            )
    return outcome


def _codes_left(
    session: Session, admin: Admin, factor: accounts.Factor | None
) -> int | None:
    """How many recovery codes are left if one was just used, to tell its
    owner, in case it was not them; None if it was another factor."""
    if factor != "recovery":
        return None
    return accounts.recovery_codes_left(session, admin.id)


def _refused(outcome: accounts.Confirmation, signed: SignedIn) -> str:
    if outcome == "rate_limited":
        return TOO_MANY
    return "That didn't match." if signed.uses_totp else "That isn't your password."


def _status(outcome: accounts.Confirmation) -> int:
    return 429 if outcome == "rate_limited" else 400


# Setting up TOTP comes before AdminSignedIn lets anyone in, so its pages
# take this; every other sensitive page takes ConfirmedAdmin.
ConfirmedAny = Annotated[SignedIn, Depends(confirmed)]


def confirmed_admin(signed: ConfirmedAny) -> SignedIn:
    return admin_signed_in(signed)


ConfirmedAdmin = Annotated[SignedIn, Depends(confirmed_admin)]


def changed(signed: SignedIn, session: Session) -> None:
    """After a sensitive change: counted for the confirmation window, and
    committed."""
    accounts.note_sensitive(signed.row, _now())
    session.commit()


def signed_out() -> Response:
    response = redirect(SIGN_IN)
    response.delete_cookie(COOKIE)
    return response


def _confirm_page(
    request: Request,
    signed: SignedIn,
    *,
    action: str,
    carried: list[tuple[str, str]],
    alert: str | None = None,
    status_code: int = 200,
    navigation: list[tuple[str, str, Badge | None]] | None = None,
) -> HTMLResponse:
    """Asks for a code, then sends `carried` on to `action`. Fields whose
    names say they are secret, as the log masker reads them (masking.py), are
    never written into the page: they are asked for again, empty."""
    return page(
        request,
        "confirm.html",
        {
            "title": "Confirm it's you",
            "uses_totp": signed.uses_totp,
            "admin": signed.admin,
            "alert": alert,
            "action": action,
            "carried": [(n, v) for n, v in carried if not is_secret_name(n)],
            "retype": list(dict.fromkeys(n for n, _ in carried if is_secret_name(n))),
            "field": CONFIRM_FIELD,
            "sections": navigation,
        },
        status_code,
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(Redirect)
    def redirected(_request: Request, error: Redirect) -> Response:
        return redirect(error.location)

    @app.exception_handler(ConfirmHere)
    def confirm_here(request: Request, error: ConfirmHere) -> Response:
        return _confirm_page(
            request,
            error.signed,
            action=request.url.path,
            carried=error.carried,
            alert=error.alert,
            status_code=error.status_code,
            navigation=error.sections,
        )


# The first administrator


def _totp_context(key: str, username: str, *, optional: bool) -> dict[str, Any]:
    return {
        "key": key,
        "key_for_typing": accounts.key_for_typing(key),
        "qr": accounts.qr_svg(accounts.totp_link(key, username or "administrator")),
        "totp_optional": optional,
    }


def _setup_page(
    request: Request,
    *,
    optional: bool,
    key: str | None = None,
    values: Mapping[str, str] | None = None,
    problems: Mapping[str, str] | None = None,
    status_code: int = 200,
    alert: str | None = None,
) -> HTMLResponse:
    values = values or {}
    totp = _totp_context(
        key or accounts.new_totp_key(), values.get("username", ""), optional=optional
    )
    return page(
        request,
        "setup.html",
        {"title": "Set up", "values": values, "problems": problems or {}}
        | {"alert": alert}
        | totp,
        status_code,
    )


def _setup_off(request: Request) -> HTMLResponse:
    return page(request, "setup.html", {"title": "Set up", "off": True}, 403)


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
    key = key if accounts.is_totp_key(key) else accounts.new_totp_key()
    values = {
        "username": username,
        "email": email,
        "authenticator_name": authenticator_name,
    }
    optional = _totp_optional(session)
    try:
        accounts.check_setup_token(session, expected, token, rate_key(request))
    except accounts.TokenRefused as refused:
        limited = refused.reason == "rate_limited"
        return _setup_page(
            request,
            optional=optional,
            key=key,
            values=values,
            problems={} if limited else {"token": "That isn't the setup token."},
            alert=TOO_MANY if limited else None,
            status_code=429 if limited else 400,
        )
    values["token"] = token
    now = _now()
    problems = {
        field: problem
        for field, problem in (
            ("username", accounts.username_problem(username)),
            ("email", accounts.email_problem(email)),
            ("password", accounts.password_problem(password, password_again)),
        )
        if problem
    }
    step = None
    if code.strip() or not optional:
        step = accounts.accepted_step(key, code, None, now)
        problems |= _authenticator_problems(authenticator_name, step)
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


def _authenticator_problems(name: str, step: int | None) -> dict[str, str]:
    """A new authenticator's name, and the step its code was for: None if
    the code did not match."""
    problems = {}
    if problem := accounts.authenticator_name_problem(name):
        problems["authenticator_name"] = problem
    if step is None:
        problems["code"] = (
            "That code didn't match. Check the app shows NYC Mesh Inventory, "
            "and type the code it shows now."
        )
    return problems


def _recovery_codes_page(
    request: Request, admin: Admin, codes: list[str]
) -> HTMLResponse:
    return page(
        request,
        "recovery_codes.html",
        {"title": "Your recovery codes", "codes": codes, "admin": admin},
    )


# Signing in and out


def _sign_in_page(
    request: Request,
    next: str,
    *,
    username: str = "",
    alert: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return page(
        request,
        "sign_in.html",
        {
            "title": "Sign in",
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
            now=_now(),
        )
    except accounts.SignInRefused as refused:
        limited = refused.reason == "rate_limited"
        return _sign_in_page(
            request,
            next,
            username=username,
            alert=TOO_MANY
            if limited
            else "That username, password or code didn't match.",
            status_code=429 if limited else 400,
        )
    session.commit()
    request_logging.bind(admin_id=row.admin_id, sign_in_id=row.id)
    admin = session.get_one(Admin, row.admin_id)  # Loaded: no query.
    if (left := _codes_left(session, admin, factor)) is not None:
        notify.tell(admin.email, admin.id, mail.recovery_code_used(left))
    response = redirect(_local(next))
    _set_cookie(request, response, token, row)
    return response


@router.post(SIGN_OUT)
def sign_out(signed: AnySignedIn, session: DbSession) -> Response:
    accounts.end_session(signed.row, _now())
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
    confirm_code: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = HOME,
) -> Response:
    outcome = _confirm(session, signed, confirm_code, notify)
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
    problems: Mapping[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    totp = _totp_context(
        key or accounts.new_totp_key(), signed.admin.username, optional=False
    )
    return page(
        request,
        "authenticator_new.html",
        {
            "title": "Set up an authenticator",
            "admin": signed.admin,
            "required": signed.must_set_up_totp,
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
    authenticator_name: Annotated[str, Form()] = "",
    key: Annotated[str, Form()] = "",
    code: Annotated[str, Form()] = "",
) -> Response:
    admin_id = signed.admin.id
    name = authenticator_name.strip()
    key = key if accounts.is_totp_key(key) else accounts.new_totp_key()
    now = _now()
    # One at a time per administrator, so a second click cannot add the
    # same name twice, or make a second set of recovery codes: the gate
    # (confirmed) holds their row.
    step = accounts.accepted_step(key, code, None, now)
    problems = _authenticator_problems(name, step)
    # Checked here too, though the insert below would catch it, so a taken
    # name is told alongside a wrong code.
    if "authenticator_name" not in problems and accounts.authenticator_named(
        session, admin_id, name
    ):
        problems["authenticator_name"] = NAME_TAKEN
    first = not accounts.uses_totp(session, admin_id)
    if not problems:
        assert step is not None  # A wrong code is a problem.
        try:
            # A savepoint: PostgreSQL may compare names its own way.
            with session.begin_nested():
                accounts.add_authenticator(session, admin_id, name, key, step)
        except IntegrityError:
            problems["authenticator_name"] = NAME_TAKEN
    if problems:
        session.rollback()
        return _new_authenticator_page(
            request, signed, key=key, name=name, problems=problems, status_code=400
        )
    codes = accounts.make_recovery_codes(session, admin_id, now) if first else []
    changed(signed, session)
    if codes:
        return _recovery_codes_page(request, signed.admin, codes)
    return redirect(HOME)
