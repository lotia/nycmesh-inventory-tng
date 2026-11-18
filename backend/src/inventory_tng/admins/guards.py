"""What every administrator page depends on (docs/decisions/0012): being
signed in, and confirming a sensitive action. The sign-in pages are in
sign_in_routes.py; the rules are in accounts.py.

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

Each is a Need. A single-use form (single_use_forms.sent_before) sent again
while a code would be asked for is answered so before one is spent
(is_spent, from core/sent_forms.py).

A form through any of these (or ConfirmedAny) also holds every administrator's
lock (accounts.lock_admins) until it commits, so it runs one at a time with the
others and with resets, retires and restores (inventory-tng-8i4.50.4). It
commits once, at most: a commit after that fails
(db_sessions.hold_until_commit). Work after the reply opens its own session.
"""

import dataclasses
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, cast
from urllib.parse import quote

import structlog
from babel.support import LazyProxy
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from inventory_tng.admins import accounts
from inventory_tng.admins.paths import (
    ACCOUNT,
    CONFIRM,
    FORGOT,
    HOME,
    NEW_AUTHENTICATOR,
    RESET,
    SETUP,
    SIGN_IN,
    SIGN_OUT,
)
from inventory_tng.admins.totp_keys import TotpKeys
from inventory_tng.core import mail, masking, request_logging
from inventory_tng.core.db_sessions import (
    DbSession,
    hold_until_commit,
    record_last_seen,
    set_actor,
)
from inventory_tng.core.mail import Notify
from inventory_tng.core.phrases import Words, lazy_gettext
from inventory_tng.core.sent_forms import is_spent
from inventory_tng.core.settings import RateLimits
from inventory_tng.models.admins import Admin, AdminSession
from inventory_tng.models.site_settings import SignInSettings
from inventory_tng.web import single_use_forms
from inventory_tng.web.navigation import Badge, sections
from inventory_tng.web.pages import page, redirect, templates

log = structlog.get_logger(__name__)

COOKIE = "inventory_admin"

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

TOO_MANY = lazy_gettext("Too many tries. Wait a few minutes, then try again.")
NAME_TAKEN = lazy_gettext("You already have one by that name.")
CODE_USED = lazy_gettext(
    "That didn't match. Each code works once: if you just used it, wait for the next."
)
NOT_YOUR_PASSWORD = lazy_gettext("That isn't your password.")
CONFIRM_TITLE = lazy_gettext("Confirm it's you")
RECOVERY_CODES_TITLE = lazy_gettext("Your recovery codes")
ACCOUNT_TITLE = lazy_gettext("Your account")


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
# The fields of a form a confirmation carries that hold a password the person
# typed, by name, with their labels, in the order asked: never written into
# the confirm page, but asked for again, empty. Every other field is carried
# as sent, in plain HTML, so a confirmed form that sends any other field the
# log masker calls secret (masking.is_secret_name) is refused (_confirmed).
RETYPED: dict[str, LazyProxy] = {
    "password": lazy_gettext("Password"),
    "password_again": lazy_gettext("Password again"),
}


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
        alert: Words | None = None,
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
    is said to have made (single_use_forms.Made)."""

    window: Callable[[AdminSession, SignInSettings, datetime], bool]
    recovery: bool
    account_change: bool
    made: single_use_forms.Made


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
    # A secret it would carry into the confirm page as a hidden field: a
    # bad request, window open or not, so any test of a form that gains one
    # fails until it joins RETYPED. The name stays out: the browser chose it.
    if any(masking.is_secret_name(n) and n not in RETYPED for n, _ in carried):
        log.warning("secret field refused", admin_id=signed.admin.id)
        raise HTTPException(status_code=400)
    was_open = need.window(signed.row, signed.settings, _now())
    # A form sent again, as Back then Forward offers, while a code would be
    # asked for: said so before one is spent on it. Otherwise its route
    # says so (single_use_forms.sent_before).
    if not was_open and await run_in_threadpool(
        is_spent, session, form.get(single_use_forms.FIELD)
    ):
        # The page the form was on (single_use_forms.single_use_field):
        # browsers send no Referer here (security_headers.REFERRER_POLICY).
        back = _local(str(form.get(single_use_forms.BACK, "")))
        raise Answered(single_use_forms.sent_again(request, signed, need.made, back))
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
            request.app.state.settings.rate_limits,
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
    limits: RateLimits,
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
        limits=limits,
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


def _refused(outcome: accounts.Confirmation, signed: SignedIn) -> Words:
    if outcome == "rate_limited":
        return TOO_MANY
    return str(CODE_USED if signed.asks_code else NOT_YOUR_PASSWORD)


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
    alert: Words | None = None,
    status_code: int = 200,
    navigation: list[tuple[str, str | LazyProxy, Badge | None]] | None = None,
    recovery: bool = True,
) -> HTMLResponse:
    """Asks for a code (a recovery code too, unless not `recovery`), then
    sends `carried` on to `action`. A password typed (RETYPED) is never
    written into the page: it is asked for again, empty."""
    sent = {name for name, _ in carried}
    return page(
        request,
        "admins/confirm.html",
        {
            "title": CONFIRM_TITLE,
            "uses_totp": signed.asks_code,
            "admin": signed.admin,
            "alert": alert,
            "action": action,
            "carried": [(n, v) for n, v in carried if n not in RETYPED],
            "retype": [(n, label) for n, label in RETYPED.items() if n in sent],
            "field": CONFIRM_FIELD,
            "totp_only": not recovery,
            "sections": navigation,
        },
        status_code,
    )


def install_admin_answers(app: FastAPI) -> None:
    """The answers a Need gives before its route runs: a redirect, a form's
    answer, or the form shown again asking for a code."""

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
