"""Administrators' sign-in pages, and what every administrator page depends
on (docs/decisions/0012): the first administrator's setup, signing in and
out, a new authenticator, and confirming a sensitive action. The rules are
in accounts.py.

An administrator page takes one of:

- `AdminSignedIn`: signed in, with TOTP set up if it is required.
- `ConfirmedAdmin`: that, for a sensitive action, with a code typed recently
  (accounts.confirmation_open); otherwise the page asks for one first. Once
  the action is done, the route calls changed(), which commits.
- `ConfirmedAccountChange`: that, for inviting, resetting, retiring or
  bringing back another administrator, with a code typed for such a change
  within the sign-in settings' account_change_minutes
  (accounts.account_change_open), so a stolen session gets at most that
  long.
- `ConfirmedByAuthenticator`: that, for making new recovery codes, with a
  code from an authenticator each time, never a recovery code.

Each is a Need. A form sent once (once.sent_before) and sent again while a
code would be asked for is answered so before one is spent (once.spent).

A form through any of these (or ConfirmedAny) also holds every
administrator's lock (accounts.lock_admins) until it commits, so it runs
one at a time with the others and with resets, retires and restores
(inventory-tng-8i4.50.4). It commits once, at most: a commit after that
fails (db.hold_until_commit). Work after the reply opens its own session.
"""

import dataclasses
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, cast
from urllib.parse import quote, urlencode

import structlog
from fastapi import APIRouter, Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from inventory_tng import accounts, mail, once, request_logging
from inventory_tng.db import (
    DbSession,
    hold_until_commit,
    record_last_seen,
    set_actor,
)
from inventory_tng.mail import Notify
from inventory_tng.masking import is_secret_name
from inventory_tng.models import Admin, AdminSession, SignInSettings, running_low
from inventory_tng.pages import (
    NOTICES,
    Badge,
    not_found,
    page,
    redirect,
    sections,
    templates,
)
from inventory_tng.totp_keys import TotpKeys
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


def reset_url(site: str, token: str) -> str:
    """A reset link, under `site`, such as SITE_URL."""
    return f"{site.rstrip('/')}{RESET}?{urlencode({'token': token})}"


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


NOTICES.update(
    {
        "codes_low": "You signed in with a recovery code, and few are left: "
        "make new ones now, with a code from your authenticator. Lost your "
        "phone? Set up a new authenticator first.",
        "codes_none": "That was your last recovery code. Make new ones now, "
        "with a code from your authenticator (lost your phone? set up a new "
        "one first): without them, a lost phone locks you out until another "
        "administrator resets your sign-in.",
    }
)


# What administrator pages depend on


@dataclass
class SignedIn:
    admin: Admin
    row: AdminSession
    settings: SignInSettings
    uses_totp: bool

    @property
    def asks_code(self) -> bool:
        """A code is asked for to confirm, not the password: with TOTP, or
        with recovery codes while asked to set it up again."""
        return self.uses_totp or self.admin.must_set_up_totp

    @property
    def needs_totp_setup(self) -> bool:
        required = self.settings.totp_policy == "required"
        return not self.uses_totp and (required or self.admin.must_set_up_totp)


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
        record_last_seen(request.app.state.db, AdminSession, row.id, now)
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


def _totp_keys(request: Request) -> TotpKeys:
    """What the app seals authenticators' keys with (totp_keys.py)."""
    return request.app.state.totp_keys


Keys = Annotated[TotpKeys, Depends(_totp_keys)]


def admin_signed_in(signed: AnySignedIn) -> SignedIn:
    if signed.needs_totp_setup:
        raise Redirect(NEW_AUTHENTICATOR)
    return signed


AdminSignedIn = Annotated[SignedIn, Depends(admin_signed_in)]


# The field a confirmation's code comes in, which no form uses for itself.
CONFIRM_FIELD = "confirm_code"


class Answered(Exception):
    """A form's answer, given before its route runs."""

    def __init__(self, response: Response) -> None:
        super().__init__()
        self.response = response


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
        *,
        recovery: bool = True,
    ) -> None:
        super().__init__()
        self.signed, self.carried = signed, carried
        self.alert, self.status_code = alert, status_code
        self.recovery = recovery
        self.sections = sections(signed.admin)


def _never(*_: object) -> bool:
    """A window that is never open: a code each time."""
    return False


@dataclass(frozen=True)
class Need:
    """What a form's confirmation needs: a code typed within `window`;
    whether a recovery code will do; whether a code typed for it opens the
    window for changes to other administrators; and what a form sent again
    is said to have made (once.Made)."""

    window: Callable[[AdminSession, SignInSettings, datetime], bool]
    recovery: bool
    account_change: bool
    made: once.Made


# Sensitive actions.
SENSITIVE = Need(accounts.confirmation_open, True, False, "change")
# Changes to other administrators (accounts.account_change_open).
ACCOUNT_CHANGE = Need(accounts.account_change_open, True, True, "change")
# Making new recovery codes, which void the old: one who signed in with a
# stolen code must not lock its owner's printed ones out.
BY_AUTHENTICATOR = Need(_never, False, False, "recovery_codes")


def _confirming(need: Need) -> Callable[..., Awaitable[SignedIn]]:
    """The dependency for `need`: within its window, goes on; viewing a page
    keeps nothing open. A page asked for once it has closed sends the
    browser to confirm first. A form sent once it has closed is asked for a
    code there and then (ConfirmHere), and goes on when one comes with it;
    sent again, it is answered so before a code is spent on it. A form then
    passes the gate (_hold), after the commit a code makes, as the module's
    docstring says."""

    async def confirming(
        request: Request,
        signed: AnySignedIn,
        session: DbSession,
        notify: Notify,
        keys: Keys,
    ) -> SignedIn:
        return await _confirmed(request, signed, session, notify, keys, need)

    return confirming


confirmed = _confirming(SENSITIVE)


async def _confirmed(
    request: Request,
    signed: SignedIn,
    session: Session,
    notify: Notify,
    keys: TotpKeys,
    need: Need,
) -> SignedIn:
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
    was_open = need.window(signed.row, signed.settings, _now())
    # A form sent again, as Back then Forward offers, while a code would be
    # asked for: said so before one is spent on it. Otherwise its route
    # says so (once.sent_before).
    if not was_open and await run_in_threadpool(
        once.spent, session, form.get(once.FIELD)
    ):
        # The page the form was on (once.once_field): browsers send no
        # Referer here (admin_base.html).
        back = _local(str(form.get(once.BACK, "")))
        raise Answered(once.sent_again(request, signed, need.made, back))
    if not was_open:
        typed = form.get(CONFIRM_FIELD)
        if not isinstance(typed, str):
            raise await run_in_threadpool(
                ConfirmHere, signed, carried, recovery=need.recovery
            )
        # Its queries, and argon2's work, off the event loop.
        outcome = await run_in_threadpool(
            _confirm,
            keys,
            session,
            signed,
            typed,
            notify,
            need,
        )
        if outcome != "confirmed":
            raise await run_in_threadpool(
                ConfirmHere,
                signed,
                carried,
                _refused(outcome, signed),
                _status(outcome),
                recovery=need.recovery,
            )
    signed = await run_in_threadpool(_hold, session, signed)
    # The window, again, with what the lock read: shorter settings may have
    # come first.
    if was_open and not need.window(signed.row, signed.settings, _now()):
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
    keys: TotpKeys,
    session: Session,
    signed: SignedIn,
    typed: str,
    notify: Notify,
    need: Need,
) -> accounts.Confirmation:
    """Confirm for `need`, keep it, and tell the owner if a recovery code did
    it."""
    outcome, factor = accounts.confirm(
        session,
        keys,
        signed.row,
        typed,
        _now(),
        recovery=need.recovery,
        account_change=need.account_change,
    )
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
    if signed.asks_code:
        return (
            "That didn't match. Each code works once: if you just used it, "
            "wait for the next."
        )
    return "That isn't your password."


def _status(outcome: accounts.Confirmation) -> int:
    return 429 if outcome == "rate_limited" else 400


# Setting up TOTP comes before AdminSignedIn lets anyone in, so its pages
# take this; every other sensitive page takes ConfirmedAdmin.
ConfirmedAny = Annotated[SignedIn, Depends(confirmed)]


def _for_admins(
    confirming: Callable[..., Awaitable[SignedIn]],
) -> Callable[[SignedIn], SignedIn]:
    """`confirming`, and TOTP set up if it must be (admin_signed_in)."""

    def confirmed_admin(
        signed: Annotated[SignedIn, Depends(confirming)],
    ) -> SignedIn:
        return admin_signed_in(signed)

    return confirmed_admin


ConfirmedAdmin = Annotated[SignedIn, Depends(_for_admins(confirmed))]
ConfirmedAccountChange = Annotated[
    SignedIn, Depends(_for_admins(_confirming(ACCOUNT_CHANGE)))
]
ConfirmedByAuthenticator = Annotated[
    SignedIn, Depends(_for_admins(_confirming(BY_AUTHENTICATOR)))
]


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
    recovery: bool = True,
) -> HTMLResponse:
    """Asks for a code (a recovery code too, unless not `recovery`), then
    sends `carried` on to `action`. Fields whose
    names say they are secret, as the log masker reads them (masking.py), are
    never written into the page: they are asked for again, empty."""
    return page(
        request,
        "confirm.html",
        {
            "title": "Confirm it's you",
            "uses_totp": signed.asks_code,
            "admin": signed.admin,
            "alert": alert,
            "action": action,
            "carried": [(n, v) for n, v in carried if not is_secret_name(n)],
            "retype": list(dict.fromkeys(n for n, _ in carried if is_secret_name(n))),
            "field": CONFIRM_FIELD,
            "totp_only": not recovery,
            "sections": navigation,
        },
        status_code,
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(Redirect)
    def redirected(_request: Request, error: Redirect) -> Response:
        return redirect(error.location)

    @app.exception_handler(Answered)
    def answered(_request: Request, error: Answered) -> Response:
        return error.response

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
            recovery=error.recovery,
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
            now=_now(),
            keys=keys,
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
    target = _local(next)
    response = redirect(target)
    if (left := _codes_left(session, admin, factor)) is not None:
        notify.tell(admin.email, admin.id, mail.recovery_code_used(left))
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
    keys: Keys,
    confirm_code: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = HOME,
) -> Response:
    outcome = _confirm(keys, session, signed, confirm_code, notify, SENSITIVE)
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
            "required": signed.needs_totp_setup,
            # Set up when first signing in, there is nowhere yet to go back to.
            "back": None if signed.needs_totp_setup else (ACCOUNT, "Your account"),
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
    # Recovery codes come with the first authenticator, unless some are
    # left: set up again after the operator retired the old one, the printed
    # codes still work, and making new ones would void them.
    first = not accounts.uses_totp(session, admin_id)
    first = first and not accounts.has_recovery_codes(session, admin_id)
    if not problems:
        assert step is not None  # A wrong code is a problem.
        try:
            # A savepoint: PostgreSQL may compare names its own way.
            with session.begin_nested():
                accounts.add_authenticator(session, keys, admin_id, name, key, step)
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
        notify.tell(signed.admin.email, admin_id, mail.recovery_codes_made)
    notify.tell(signed.admin.email, admin_id, mail.authenticators_changed(added=True))
    if codes:
        return _recovery_codes_page(request, signed.admin, codes)
    return redirect(HOME)
