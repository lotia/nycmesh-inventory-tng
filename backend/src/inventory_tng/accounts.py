"""Administrators' accounts: passwords, TOTP, recovery codes, sessions and
the first administrator (docs/decisions/0012). The pages are in sign_in.py.

Only established calls here (AGENTS.md rule 3): argon2-cffi hashes and
checks passwords, pyotp makes TOTP keys and checks codes, totp_keys seals
them with cryptography's AES-GCM, `secrets` makes tokens, and
devices.digest (hashlib's SHA-256) hashes tokens and recovery codes for the
database. Comparing the setup token is secrets.compare_digest.
"""

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, NoReturn

import pyotp
import segno
import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from limits import RateLimitItemPerHour, RateLimitItemPerMinute
from markupsafe import Markup
from opentelemetry import metrics
from sqlalchemy import ColumnElement, exists, func, or_, select, text, update
from sqlalchemy.orm import Session

from inventory_tng import activity, devices, mail
from inventory_tng.db import exists_where, get_locked, locked, set_actor, unique
from inventory_tng.devices import digest
from inventory_tng.mail import Notifier
from inventory_tng.models import (
    Admin,
    AdminPassword,
    AdminSession,
    Authenticator,
    RecoveryCode,
    ResetKind,
    ResetLink,
    SignInSettings,
    mint_code,
)
from inventory_tng.rate_limits import limiter
from inventory_tng.totp_keys import TotpKeys, Unreadable

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

# NIST SP 800-63B-4's least for a password used alone, which it may be while
# TOTP is optional. No other rules about what is in it.
PASSWORD_MIN = 15
PASSWORD_MAX = 256
USERNAME_MAX = 64
USERNAME = re.compile(rf"[A-Za-z0-9._-]{{1,{USERNAME_MAX}}}")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
EMAIL_MAX = 254
AUTHENTICATOR_NAME_MAX = 64
RECOVERY_CODES = 10
# From LABEL_ALPHABET's 30 hard-to-misread characters: about 98 bits, so,
# like a session cookie, a stolen database's SHA-256 of one cannot be
# guessed back (docs/decisions/0012).
RECOVERY_CODE_LENGTH = 20
RECOVERY_CODE_GROUP = 5
# What a code field takes, and the most a code typed may be: a recovery code
# shown with its separators, and a few stray spaces. TOTP-only fields take a
# six-digit code, also with room for spaces.
CODE_FIELD_MAX = RECOVERY_CODE_LENGTH + RECOVERY_CODE_LENGTH // RECOVERY_CODE_GROUP + 4
TOTP_FIELD_MAX = 8
TOTP_CODE = re.compile(r"\d{6}")
TOTP_INTERVAL = pyotp.TOTP(pyotp.random_base32()).interval
# What authenticator apps show beside the account.
ISSUER = "NYC Mesh Inventory"
# How often a session's last use is written: often enough for the account
# page's list, not on every request.
SEEN_EVERY = timedelta(minutes=5)
USER_AGENT_MAX = 300

# Every attempt per address, and codes per administrator once their
# password was right.
PER_ADDRESS = RateLimitItemPerMinute(10)
PER_ACCOUNT = RateLimitItemPerMinute(10, 15)

Reason = Literal["unknown_user", "bad_password", "bad_code", "rate_limited"]
Factor = Literal["totp", "recovery", "password"]

sign_ins = meter.create_counter(
    "inventory.admin.sign_ins",
    unit="{attempt}",
    description="Administrators' sign-in attempts, by outcome",
)
confirmations = meter.create_counter(
    "inventory.admin.confirmations",
    unit="{attempt}",
    description="Codes typed to confirm a sensitive action, by outcome",
)

_hasher = PasswordHasher()
# Checked against when there is no account, so an unknown username takes as
# long as a wrong password. Made at import, so no request pays for it.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe())


# Passwords


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _matches(password_hash: str | None, password: str) -> bool:
    """Whether the password matches; as slow without a hash as with one."""
    try:
        _hasher.verify(
            _DUMMY_HASH if password_hash is None else password_hash, password
        )
    except VerificationError, InvalidHashError:
        return False
    return password_hash is not None


def _rehash(row: AdminPassword, password: str) -> None:
    """argon2-cffi's parameters have moved on since this hash was made."""
    if _hasher.check_needs_rehash(row.password_hash):
        row.password_hash = _hasher.hash(password)


def check_password(session: Session, admin_id: int | None, password: str) -> bool:
    """argon2's work with nothing locked, so guessing holds up nobody; then,
    in the order a reset and _set_password take them, their row
    (lock_admin) and their password's, which must still be there and still
    match: a reset done meanwhile took it, a link or a rehash may have
    changed it. One that comes after waits for this request
    (inventory-tng-8i4.50.4), and a rehash changes it only once locked."""
    stored = session.get(AdminPassword, admin_id) if admin_id is not None else None
    verified = stored.password_hash if stored else None
    if not _matches(verified, password):
        return False
    assert admin_id is not None  # _matches is False without a password.
    lock_admin(session, admin_id)
    current = get_locked(session, AdminPassword, admin_id)
    if current is None:
        return False
    if current.password_hash != verified and not _matches(
        current.password_hash, password
    ):
        return False
    _rehash(current, password)
    return True


def username_problem(username: str) -> str | None:
    if not USERNAME.fullmatch(username):
        return f"Use 1 to {USERNAME_MAX} letters, digits, dots, dashes or underscores."
    return None


def email_problem(email: str) -> str | None:
    if email and (not EMAIL.fullmatch(email) or len(email) > EMAIL_MAX):
        return "That doesn't look like an email address."
    return None


def password_problem(password: str, again: str) -> str | None:
    if len(password) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters. A few words make a good one."
    if len(password) > PASSWORD_MAX:
        return f"Use at most {PASSWORD_MAX} characters."
    if password != again:
        return "The two passwords are different."
    return None


# TOTP


def new_totp_key() -> str:
    return pyotp.random_base32()


def is_totp_key(key: str) -> bool:
    """A key as new_totp_key() makes them, sent back by a form."""
    return re.fullmatch(r"[A-Z2-7]{32}", key) is not None


def totp_link(key: str, username: str) -> str:
    """The otpauth:// link an authenticator app reads from the QR code."""
    return pyotp.TOTP(key).provisioning_uri(name=username, issuer_name=ISSUER)


def qr_svg(text: str, error: str = "m") -> Markup:
    """A QR code's SVG, with a viewBox so the stylesheet can size it. Always
    a full QR code: segno.make picks a Micro QR for a short text, which not
    every reader reads (docs/decisions/0015)."""
    code = segno.make_qr(text, error=error)
    return Markup(code.svg_inline(scale=5, border=2, unit="px"))


def grouped(text: str, size: int, separator: str) -> str:
    """ "ABCD EFGH ...": easier to read out and type than one long run."""
    return separator.join(text[i : i + size] for i in range(0, len(text), size))


def key_for_typing(key: str) -> str:
    """For apps that cannot scan."""
    return grouped(key, 4, " ")


def _totp_code(typed: str) -> str | None:
    """The six digits, if that is what was typed, spaces aside; else it may
    be a recovery code."""
    code = re.sub(r"\s", "", typed)
    return code if TOTP_CODE.fullmatch(code) else None


def accepted_step(
    key: str, typed: str, last_step: int | None, now: datetime
) -> int | None:
    """The time step `typed` is the code for, within one step either side of
    now and later than last_step; None if none."""
    code = _totp_code(typed)
    if code is None:
        return None
    totp = pyotp.TOTP(key)
    for offset in (-1, 0, 1):
        at = now + timedelta(seconds=offset * totp.interval)
        step = totp.timecode(at)
        if last_step is not None and step <= last_step:
            continue
        if totp.verify(code, for_time=at):
            return step
    return None


CURRENT_TOTP = Authenticator.retired_at.is_(None)


def _current(admin_id: int) -> tuple[ColumnElement[bool], ...]:
    return (Authenticator.admin_id == admin_id, CURRENT_TOTP)


def _unused_codes(admin_id: int) -> tuple[ColumnElement[bool], ...]:
    return (
        RecoveryCode.admin_id == admin_id,
        RecoveryCode.used_at.is_(None),
        RecoveryCode.voided_at.is_(None),
    )


def _live(now: datetime) -> tuple[ColumnElement[bool], ...]:
    """A session still going."""
    return (AdminSession.ended_at.is_(None), AdminSession.expires_at > now)


def _usable_links() -> tuple[ColumnElement[bool], ...]:
    return (ResetLink.used_at.is_(None), ResetLink.voided_at.is_(None))


def current_admin(
    session: Session, username: str, *where: ColumnElement[bool]
) -> Admin | None:
    """A current administrator by username, which ignores case, as its
    unique index does."""
    return session.scalar(
        select(Admin).where(
            func.lower(Admin.username) == username.strip().lower(),
            Admin.retired_at.is_(None),
            *where,
        )
    )


def step_time(step: int) -> datetime:
    """When a TOTP time step began: pyotp's steps are 30 seconds."""
    return datetime.fromtimestamp(step * TOTP_INTERVAL, UTC)


def retire(authenticator: Authenticator, now: datetime) -> None:
    """Removed, its key wiped: every path that removes one comes here."""
    authenticator.sealed_key = authenticator.key_version = None
    authenticator.retired_at = now


def authenticator_name_problem(name: str) -> str | None:
    if not 1 <= len(name) <= AUTHENTICATOR_NAME_MAX:
        return f"Give it a name of up to {AUTHENTICATOR_NAME_MAX} characters."
    return None


def uses_totp(session: Session, admin_id: int) -> bool:
    return exists_where(session, *_current(admin_id))


def admins_using_totp(session: Session) -> set[int]:
    return set(session.scalars(select(Authenticator.admin_id).where(CURRENT_TOTP)))


def authenticator_named(session: Session, admin_id: int, name: str) -> bool:
    return exists_where(
        session, *_current(admin_id), func.lower(Authenticator.name) == name.lower()
    )


def locked_authenticators(session: Session, admin_id: int) -> list[Authenticator]:
    """The administrator's current authenticators, locked, so two requests
    cannot both accept one code. None: they do not use TOTP. Their own row
    first (lock_admin), as every path locks it before anything of theirs."""
    lock_admin(session, admin_id)
    return list(
        session.scalars(
            locked(
                select(Authenticator)
                .where(*_current(admin_id))
                .order_by(Authenticator.id)
            )
        )
    )


def add_authenticator(
    session: Session, keys: TotpKeys, admin_id: int, name: str, key: str, step: int
) -> Authenticator:
    version, sealed = keys.seal(key, admin_id)
    authenticator = Authenticator(
        admin_id=admin_id,
        name=name,
        sealed_key=sealed,
        key_version=version,
        last_step=step,
    )
    session.add(authenticator)
    session.flush()
    log.info(
        "authenticator added", admin_id=admin_id, authenticator_id=authenticator.id
    )
    return authenticator


def _opened(keys: TotpKeys, authenticator: Authenticator) -> str | None:
    """Its key, or None if it cannot be read (its key version is gone from
    TOTP_KEYS_DIR): it then takes no code, and recovery codes and reset links
    still let its administrator in (DEPLOYMENT.md, TOTP keys)."""
    # Current ones keep theirs.
    assert authenticator.sealed_key is not None
    assert authenticator.key_version is not None
    try:
        return keys.open(
            authenticator.key_version, authenticator.sealed_key, authenticator.admin_id
        )
    except Unreadable:
        log.error(
            "authenticator unreadable",
            admin_id=authenticator.admin_id,
            authenticator_id=authenticator.id,
            key_version=authenticator.key_version,
        )
        return None


def unreadable_authenticators(session: Session, keys: TotpKeys) -> int:
    """Current authenticators sealed with a key version TOTP_KEYS_DIR lacks."""
    return (
        session.scalar(
            select(func.count()).where(
                CURRENT_TOTP, Authenticator.key_version.not_in(list(keys.keys))
            )
        )
        or 0
    )


def reseal_authenticators(session: Session, keys: TotpKeys) -> tuple[int, int]:
    """Every current authenticator's key sealed again with the newest version,
    so older ones can leave TOTP_KEYS_DIR: how many were, and how many could
    not be read (left as they are). The caller commits."""
    set_actor(session, "system:reseal")
    resealed = unreadable = 0
    current = session.scalars(
        locked(select(Authenticator).where(CURRENT_TOTP).order_by(Authenticator.id))
    )
    for authenticator in current:
        key = _opened(keys, authenticator)
        if key is None:
            unreadable += 1
        elif authenticator.key_version != keys.newest:
            authenticator.key_version, authenticator.sealed_key = keys.seal(
                key, authenticator.admin_id
            )
            resealed += 1
    session.flush()
    log.info(
        "authenticators resealed",
        key_version=keys.newest,
        resealed=resealed,
        unreadable=unreadable,
    )
    return resealed, unreadable


# Recovery codes


def _recovery_digest(typed: str) -> bytes:
    """ "abcde-fghjk-..." and "ABCDEFGHJK..." are one code."""
    return digest(re.sub(r"[^A-Z0-9]", "", typed.upper()))


def make_recovery_codes(session: Session, admin_id: int, now: datetime) -> list[str]:
    """New codes, which void any still unused: the only time they are
    known."""
    _void_recovery_codes(session, admin_id, now)
    codes = [mint_code(RECOVERY_CODE_LENGTH) for _ in range(RECOVERY_CODES)]
    session.add_all(
        RecoveryCode(admin_id=admin_id, code_hash=_recovery_digest(code))
        for code in codes
    )
    session.flush()
    log.info("recovery codes made", admin_id=admin_id)
    return [grouped(code, RECOVERY_CODE_GROUP, "-") for code in codes]


def check_second_factor(
    session: Session,
    keys: TotpKeys,
    admin_id: int,
    authenticators: list[Authenticator],
    typed: str,
    now: datetime,
) -> Factor | None:
    """A code from one of the administrator's authenticators (from
    locked_authenticators), or one of their recovery codes, used up as it is
    accepted."""
    for authenticator in authenticators:
        key = _opened(keys, authenticator)
        if key is None:
            continue
        step = accepted_step(key, typed, authenticator.last_step, now)
        if step is not None:
            authenticator.last_step = step
            return "totp"
    if _totp_code(typed) is not None or len(typed) > CODE_FIELD_MAX:
        return None
    recovery = session.scalar(
        locked(
            select(RecoveryCode).where(
                *_unused_codes(admin_id),
                RecoveryCode.code_hash == _recovery_digest(typed),
            )
        )
    )
    if recovery is None:
        return None
    recovery.used_at = now
    log.warning("recovery code used", admin_id=admin_id, recovery_code_id=recovery.id)
    return "recovery"


# Sessions


def sign_in_settings(session: Session) -> SignInSettings:
    return session.get_one(SignInSettings, 1)


class SignInRefused(Exception):
    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason: Reason = reason


def sign_in(
    session: Session,
    username: str,
    password: str,
    code: str,
    *,
    address: str,
    user_agent: str | None,
    now: datetime,
    keys: TotpKeys,
) -> tuple[AdminSession, str, Factor]:
    """Check all three at once and start a session, or SignInRefused. Returns
    the session, its token and the second factor used. The caller commits,
    and only on success.

    Every attempt counts against its address before anything is checked, so
    a burst sent at once cannot all slip under the limit. Codes count
    against the account too, but only once its password was right: someone
    without the password cannot lock its administrator out."""
    limits = limiter(session)
    if not limits.hit(PER_ADDRESS, "sign_in", address):
        _refuse_sign_in("rate_limited", None)
    admin = current_admin(session, username)
    if not check_password(session, admin.id if admin else None, password):
        _refuse_sign_in("unknown_user" if admin is None else "bad_password", admin)
    assert admin is not None  # check_password is False without one.
    # Their row is locked now: read it again, as a retire may have come.
    # Retired, they are no administrator, as they would be found a moment
    # later.
    if not lock_current(session, admin):
        _refuse_sign_in("unknown_user", None)
    set_actor(session, f"admin:{admin.id}")
    factor: Factor | None = "password"
    authenticators = locked_authenticators(session, admin.id)
    if authenticators or has_recovery_codes(session, admin.id):
        if not limits.hit(PER_ACCOUNT, "code", str(admin.id)):
            _refuse_sign_in("rate_limited", admin)
        factor = check_second_factor(session, keys, admin.id, authenticators, code, now)
    if factor is None:
        _refuse_sign_in("bad_code", admin)
    row, token = start_session(session, admin.id, user_agent, now)
    sign_ins.add(1, {"outcome": "signed_in", "factor": factor})
    log.info("signed in", admin_id=admin.id, sign_in_id=row.id, factor=factor)
    return row, token, factor


def _refuse_sign_in(reason: Reason, admin: Admin | None) -> NoReturn:
    sign_ins.add(1, {"outcome": "refused", "reason": reason})
    log.warning("sign-in refused", reason=reason, admin_id=admin.id if admin else None)
    raise SignInRefused(reason)


def _new_token() -> tuple[str, bytes]:
    """A cookie's or a link's token, and the hash that is all that is kept."""
    token = secrets.token_urlsafe(32)
    return token, digest(token)


def _set_password(
    session: Session, admin_id: int, password: str, now: datetime
) -> None:
    """Their row, then their password's, locked in the order every path
    takes them (lock_admin, _remove_sign_in)."""
    lock_admin(session, admin_id)
    stored = get_locked(session, AdminPassword, admin_id)
    if stored is None:
        session.add(AdminPassword(id=admin_id, password_hash=hash_password(password)))
    else:
        stored.password_hash, stored.changed_at = hash_password(password), now


def start_session(
    session: Session, admin_id: int, user_agent: str | None, now: datetime
) -> tuple[AdminSession, str]:
    """A new session, counted as just confirmed: both factors, or the
    password without TOTP, were just typed."""
    token, token_hash = _new_token()
    days = sign_in_settings(session).session_days
    row = AdminSession(
        admin_id=admin_id,
        token_hash=token_hash,
        expires_at=now + timedelta(days=days),
        last_seen_at=now,
        confirmed_at=now,
        user_agent=user_agent[:USER_AGENT_MAX] if user_agent else None,
    )
    session.add(row)
    session.flush()
    return row, token


def find_session(
    session: Session, token: str, now: datetime
) -> tuple[AdminSession, Admin] | None:
    """The current session with this cookie, and its administrator, if
    current, in one query. Reads only: the caller records its use
    (db.record_last_seen) once SEEN_EVERY has passed."""
    found = session.execute(
        select(AdminSession, Admin)
        .join(Admin, Admin.id == AdminSession.admin_id)
        .where(
            AdminSession.token_hash == digest(token),
            *_live(now),
            Admin.retired_at.is_(None),
        )
    ).one_or_none()
    return None if found is None else (found[0], found[1])


def hold_signed_in(
    session: Session, row: AdminSession, now: Callable[[], datetime]
) -> bool:
    """The gate a sensitive form passes (docs/decisions/0012): the
    administrators lock (lock_admins), so any reset, retire or restore of
    theirs is done or waits until this request ends, then their session
    read again. True if it is still live and they can still sign in."""
    if not lock_admins(session, row.admin_id):
        return False
    session.refresh(row)
    # As _live says in SQL, at the time after the wait: the session may have
    # run out during it.
    return row.ended_at is None and row.expires_at > now()


def end_session(row: AdminSession, now: datetime) -> None:
    row.ended_at = now
    log.info("signed out", admin_id=row.admin_id, sign_in_id=row.id)


def confirmation_open(
    row: AdminSession, settings: SignInSettings, now: datetime
) -> bool:
    """Whether a sensitive action may go ahead without asking for a code:
    within confirm_hours of the code, and within confirm_idle_minutes of it
    or of the last sensitive action."""
    if row.confirmed_at is None:
        return False
    if now >= row.confirmed_at + timedelta(hours=settings.confirm_hours):
        return False
    # Confirming clears last_sensitive_at, so it is always the later.
    last = row.last_sensitive_at or row.confirmed_at
    return now < last + timedelta(minutes=settings.confirm_idle_minutes)


def account_change_open(
    row: AdminSession, settings: SignInSettings, now: datetime
) -> bool:
    """Whether inviting, resetting, retiring or bringing back another
    administrator may go ahead without asking for a code: the window is
    open, and the code was typed within account_change_minutes, counted
    from the code, not from the last action, so a stolen session gets at
    most that long. 0: never."""
    minutes = timedelta(minutes=settings.account_change_minutes)
    return (
        confirmation_open(row, settings, now)
        and row.confirmed_at is not None
        and now < row.confirmed_at + minutes
    )


Confirmation = Literal["confirmed", "refused", "rate_limited"]


def confirm(
    session: Session, keys: TotpKeys, row: AdminSession, typed: str, now: datetime
) -> tuple[Confirmation, Factor | None]:
    """A code (or, without TOTP, the password) for sensitive actions. Every
    attempt counts against the administrator's codes, shared with signing
    in, so a stolen session cannot guess them. Returns the outcome and the
    factor used."""
    if not limiter(session).hit(PER_ACCOUNT, "code", str(row.admin_id)):
        return _confirmation(row, "rate_limited"), None
    set_actor(session, f"admin:{row.admin_id}")
    factor: Factor | None = None
    if has_second_factor(session, row.admin_id):
        authenticators = locked_authenticators(session, row.admin_id)
        factor = check_second_factor(
            session, keys, row.admin_id, authenticators, typed, now
        )
    # Checked again once locked: TOTP may have been set up meanwhile.
    elif check_password(session, row.admin_id, typed) and not has_second_factor(
        session, row.admin_id
    ):
        factor = "password"
    if factor is None:
        return _confirmation(row, "refused"), None
    row.confirmed_at, row.last_sensitive_at = now, None
    return _confirmation(row, "confirmed"), factor


def note_sensitive(row: AdminSession, now: datetime) -> None:
    """A sensitive action was done: the idle time counts from now."""
    row.last_sensitive_at = now


def _confirmation(row: AdminSession, outcome: Confirmation) -> Confirmation:
    confirmations.add(1, {"outcome": outcome})
    fields = {"admin_id": row.admin_id, "sign_in_id": row.id}
    if outcome == "confirmed":
        log.info("confirmed", **fields)
    else:
        log.warning("confirmation refused", outcome=outcome, **fields)
    return outcome


# The first administrator


TokenRefusal = Literal["closed", "rate_limited", "bad_token", "used", "no_admin"]


class TokenRefused(Exception):
    def __init__(self, reason: TokenRefusal) -> None:
        super().__init__(reason)
        self.reason: TokenRefusal = reason


def setup_open(session: Session) -> bool:
    """Until any administrator exists, retired ones included."""
    return not exists_where(session, Admin.id.is_not(None))


def check_setup_token(
    session: Session, expected: str, typed: str, address: str
) -> None:
    _check_operator_token(session, "setup", expected, typed, address)


def _check_operator_token(
    session: Session,
    purpose: Literal["setup", "recovery"],
    expected: str,
    typed: str,
    address: str,
) -> None:
    """A token the operator set in the deployment's settings."""
    # Counted before it is compared, so a burst cannot slip under the limit.
    if not limiter(session).hit(PER_ADDRESS, purpose, address):
        _refuse(purpose, "rate_limited")
    if not secrets.compare_digest(typed.strip().encode(), expected.encode()):
        _refuse(purpose, "bad_token")


def _refuse(purpose: Literal["setup", "recovery"], reason: TokenRefusal) -> NoReturn:
    log.warning(f"{purpose} refused", reason=reason)
    if purpose == "recovery":
        recoveries.add(1, {"outcome": reason})
    raise TokenRefused(reason)


@dataclass(frozen=True)
class FirstAdmin:
    admin: Admin
    row: AdminSession
    token: str
    # Empty without TOTP.
    recovery_codes: list[str]


def create_first_admin(
    session: Session,
    *,
    username: str,
    email: str | None,
    password: str,
    authenticator_name: str,
    totp_key: str,
    step: int | None,
    user_agent: str | None,
    now: datetime,
    keys: TotpKeys,
) -> FirstAdmin:
    """The first administrator, signed in, with their authenticator and
    recovery codes if a code from it was accepted (`step`, the time step it
    was for; None without TOTP). Refused once any administrator exists,
    checked under a lock so two setups cannot both make one. The caller
    commits."""
    session.execute(text("LOCK TABLE admin IN SHARE ROW EXCLUSIVE MODE"))
    if not setup_open(session):
        _refuse("setup", "closed")
    set_actor(session, "system:setup")
    admin = Admin(username=username, email=email or None)
    session.add(admin)
    session.flush()
    _set_password(session, admin.id, password, now)
    codes: list[str] = []
    if step is not None:
        add_authenticator(session, keys, admin.id, authenticator_name, totp_key, step)
        codes = make_recovery_codes(session, admin.id, now)
    row, token = start_session(session, admin.id, user_agent, now)
    log.info("first administrator set up", admin_id=admin.id, sign_in_id=row.id)
    return FirstAdmin(admin, row, token, codes)


# One's own account (account.py's page)


def change_password(
    session: Session, row: AdminSession, password: str, now: datetime
) -> None:
    """A new password, from the session `row`: every other session of
    theirs ends, and any unused reset link. The caller has checked it
    (password_problem) and commits."""
    set_password(session, row.admin_id, password, now, keep=row.id)


def set_password(
    session: Session,
    admin_id: int,
    password: str,
    now: datetime,
    *,
    keep: int | None = None,
) -> None:
    """A new password: their sessions end, all but `keep`, and any unused
    reset link. The caller has checked it (password_problem) and commits."""
    _set_password(session, admin_id, password, now)
    ended = end_sessions(session, admin_id, now, keep=keep)
    void_reset_links(session, admin_id, now)
    log.info("password changed", admin_id=admin_id, sessions_ended=ended)


def change_email(session: Session, admin: Admin, email: str, now: datetime) -> None:
    """Checked by email_problem; "" removes it. A clash with another
    administrator's is an IntegrityError on flush. Links already mailed to
    the old address stop working."""
    admin.email = email or None
    session.flush()
    void_reset_links(session, admin.id, now)
    log.info("email changed", admin_id=admin.id, has_email=admin.email is not None)


def authenticators(session: Session, admin_id: int) -> list[Authenticator]:
    return list(
        session.scalars(
            select(Authenticator)
            .where(*_current(admin_id))
            .order_by(Authenticator.created_at, Authenticator.id)
        )
    )


def rename_authenticator(
    session: Session, admin_id: int, authenticator_id: int, name: str
) -> bool:
    """False if it is not one of theirs. A name they already use is an
    IntegrityError on flush."""
    found = session.scalar(
        locked(
            select(Authenticator).where(
                *_current(admin_id), Authenticator.id == authenticator_id
            )
        )
    )
    if found is None:
        return False
    found.name = name
    session.flush()
    log.info("authenticator renamed", admin_id=admin_id, authenticator_id=found.id)
    return True


Removal = Literal["removed", "totp_off", "missing", "last_required"]


def remove_authenticator(
    session: Session, admin_id: int, authenticator_id: int, now: datetime
) -> Removal:
    """Its key is wiped. The last one goes only while TOTP is optional, and
    takes the recovery codes with it: TOTP is then off."""
    # Locked in order, so two removals cannot each leave the other's last.
    current = locked_authenticators(session, admin_id)
    found = next((a for a in current if a.id == authenticator_id), None)
    if found is None:
        return "missing"
    last = len(current) == 1
    if last and sign_in_settings(session).totp_policy == "required":
        return "last_required"
    retire(found, now)
    if last:
        _void_recovery_codes(session, admin_id, now)
    log.info(
        "authenticator removed",
        admin_id=admin_id,
        authenticator_id=found.id,
        totp_off=last,
    )
    return "totp_off" if last else "removed"


def has_recovery_codes(session: Session, admin_id: int) -> bool:
    return exists_where(session, *_unused_codes(admin_id))


def has_second_factor(session: Session, admin_id: int) -> bool:
    """A current authenticator, or unused recovery codes: then a code is
    asked for, at sign-in and to confirm. Codes come only with an
    authenticator, but for after the operator retired them
    (retire_authenticators)."""
    return uses_totp(session, admin_id) or has_recovery_codes(session, admin_id)


def recovery_codes_left(session: Session, admin_id: int) -> int:
    return session.scalar(select(func.count()).where(*_unused_codes(admin_id))) or 0


def _void_recovery_codes(session: Session, admin_id: int, now: datetime) -> None:
    for code in session.scalars(
        locked(
            select(RecoveryCode)
            .where(*_unused_codes(admin_id))
            .order_by(RecoveryCode.id)
        )
    ):
        code.voided_at = now


def _reset_authenticators(session: Session, admin_id: int, now: datetime) -> None:
    """Every authenticator of theirs retired and every unused recovery code
    voided, so a new one is set up at sign-in while TOTP is required."""
    for authenticator in locked_authenticators(session, admin_id):
        retire(authenticator, now)
    _void_recovery_codes(session, admin_id, now)


def previous_sign_in(session: Session, row: AdminSession) -> datetime | None:
    """When they signed in before this session; None if they never did."""
    return session.scalar(
        select(func.max(AdminSession.created_at)).where(
            AdminSession.admin_id == row.admin_id, AdminSession.id < row.id
        )
    )


def current_sessions(
    session: Session, admin_id: int, now: datetime
) -> list[AdminSession]:
    """Their sessions still going, most recently used first."""
    return list(
        session.scalars(
            select(AdminSession)
            .where(AdminSession.admin_id == admin_id, *_live(now))
            .order_by(AdminSession.last_seen_at.desc(), AdminSession.id.desc())
        )
    )


def end_sessions(
    session: Session,
    admin_id: int,
    now: datetime,
    *,
    only: int | None = None,
    keep: int | None = None,
) -> int:
    """End their sessions: just `only`, or all but `keep`. How many ended."""
    query = update(AdminSession).where(
        AdminSession.admin_id == admin_id, AdminSession.ended_at.is_(None)
    )
    if only is not None:
        query = query.where(AdminSession.id == only)
    if keep is not None:
        query = query.where(AdminSession.id != keep)
    ended = session.execute(query.values(ended_at=now).returning(AdminSession.id)).all()
    if ended:
        log.info("sessions ended", admin_id=admin_id, count=len(ended))
    return len(ended)


# Reset links (docs/decisions/0012)

# An emailed link, which only the mailbox's owner should have, lasts an hour.
EMAILED_LINK_LIFETIME = timedelta(hours=1)
# Emailed links asked for, per account, whoever asks: enough for someone who
# lost the first mail, too few to fill a mailbox.
EMAILED_PER_ACCOUNT = RateLimitItemPerHour(3)


def make_reset_link(
    session: Session,
    admin_id: int,
    now: datetime,
    *,
    kind: ResetKind,
    made_by_id: int | None = None,
    recovery_token_hash: bytes | None = None,
) -> str:
    """A one-use link's token, the only time it is known. It voids earlier
    links still unused, so the newest works: an emailed one voids only
    emailed ones, so asking by mail, which anyone may do, never undoes a
    link another administrator or the recovery token made. One another
    administrator made, to pass on, lasts a day; any other, an hour."""
    lifetime = MADE_LINK_LIFETIME if made_by_id is not None else EMAILED_LINK_LIFETIME
    void_reset_links(session, admin_id, now, emailed_only=kind == "emailed")
    token, token_hash = _new_token()
    link = ResetLink(
        admin_id=admin_id,
        made_by_id=made_by_id,
        recovery_token_hash=recovery_token_hash,
        token_hash=token_hash,
        kind=kind,
        expires_at=now + lifetime,
    )
    session.add(link)
    session.flush()
    log.info(
        "reset link made",
        admin_id=admin_id,
        reset_link_id=link.id,
        kind=kind,
        made_by_id=made_by_id,
    )
    return token


def void_reset_links(
    session: Session, admin_id: int, now: datetime, *, emailed_only: bool = False
) -> None:
    """Their unused links void; their own row first (lock_admin), so making
    or voiding links waits for a reset, a sign-in or a link being used."""
    lock_admin(session, admin_id)
    query = select(ResetLink).where(ResetLink.admin_id == admin_id, *_usable_links())
    if emailed_only:
        query = query.where(ResetLink.emailed())
    for link in session.scalars(locked(query.order_by(ResetLink.id))):
        link.voided_at = now


@dataclass(frozen=True)
class EmailedReset:
    admin_id: int
    email: str
    token: str


def may_ask_for_reset(session: Session, address: str) -> bool:
    """Asking by mail, per address, counted before anything else."""
    return limiter(session).hit(PER_ADDRESS, "forgot", address)


def emailed_reset(
    session: Session, username: str, now: datetime
) -> EmailedReset | None:
    """For a current administrator with an email and a password: a new
    emailed link, which resets the password only. None for anyone else,
    who is told the same."""
    admin = current_admin(session, username, Admin.email.is_not(None))
    # Locked and read again: a reset, a retire or a change of email may have
    # come while this looked them up.
    if admin is None or not lock_current(session, admin) or admin.email is None:
        log.info("emailed reset not made", reason="no_such_address")
        return None
    # No password: an invite, or a reset another administrator made, which
    # asking by mail must never undo.
    if not has_password(session, admin.id):
        log.info("emailed reset not made", reason="no_password", admin_id=admin.id)
        return None
    if not limiter(session).hit(EMAILED_PER_ACCOUNT, "emailed_reset", str(admin.id)):
        log.warning("emailed reset not made", reason="rate_limited", admin_id=admin.id)
        return None
    set_actor(session, f"admin:{admin.id}")
    token = make_reset_link(
        session,
        admin.id,
        now,
        kind="emailed",
    )
    return EmailedReset(admin.id, admin.email, token)


def may_use_reset_link(session: Session, address: str) -> bool:
    return limiter(session).hit(PER_ADDRESS, "reset", address)


def find_reset_link(
    session: Session, token: str, now: datetime, *, lock: bool = False
) -> tuple[ResetLink, Admin] | None:
    """The link with this token, if it can still be used, and its current
    administrator; locked when it is about to be used, so it is used once,
    and so a sign-in or a reset of theirs waits for it.
    An emailed link needs a password still there: one made just as another
    administrator reset the account must not undo the reset."""
    query = (
        select(ResetLink, Admin)
        .join(Admin, Admin.id == ResetLink.admin_id)
        .where(
            ResetLink.token_hash == digest(token),
            *_usable_links(),
            ResetLink.expires_at > now,
            Admin.retired_at.is_(None),
            or_(
                ~ResetLink.emailed(),
                exists().where(AdminPassword.id == ResetLink.admin_id),
            ),
        )
    )
    found = session.execute(query).one_or_none()
    if found is not None and lock:
        # Its administrator first, as resets and sign-in lock them, then
        # the link, read again as it may have been used or voided meanwhile.
        lock_admin(session, found[1].id)
        found = session.execute(locked(query, of=ResetLink)).one_or_none()
    return None if found is None else (found[0], found[1])


def use_reset_link(
    session: Session, link: ResetLink, password: str, now: datetime
) -> None:
    """Does what the link was made for: a new password (checked by the caller
    with password_problem), and, if it says so, its authenticators and
    recovery codes gone, so a new one is set up at sign-in while TOTP is
    required. Every session of theirs ends. The caller commits."""
    set_actor(session, f"admin:{link.admin_id}")
    _set_password(session, link.admin_id, password, now)
    if link.resets_authenticators:
        _reset_authenticators(session, link.admin_id, now)
    link.used_at = now
    # Any other link for them, of whatever kind, would undo what this did.
    void_reset_links(session, link.admin_id, now)
    ended = end_sessions(session, link.admin_id, now)
    log.info(
        "reset link used",
        admin_id=link.admin_id,
        reset_link_id=link.id,
        sessions_ended=ended,
    )


# The recovery token (docs/decisions/0012)

recoveries = meter.create_counter(
    "inventory.admin.recoveries",
    unit="{attempt}",
    description="Uses of the operator's recovery token, by outcome",
)


def recover(
    session: Session,
    expected: str,
    typed: str,
    username: str,
    address: str,
    now: datetime,
    notify: Notifier | None = None,
) -> tuple[Admin, str]:
    """A one-use link, made with the operator's RECOVERY_TOKEN, that resets
    a current administrator's password and authenticators. Each token works
    once: its hash is kept on the link it made. Raises TokenRefused."""
    _check_operator_token(session, "recovery", expected, typed, address)
    token_hash = digest(typed.strip())
    if exists_where(session, ResetLink.recovery_token_hash == token_hash):
        _refuse("recovery", "used")
    admin = current_admin(session, username)
    # Locked and read again, as a retire may have come meanwhile.
    if admin is None or not lock_current(session, admin):
        _refuse("recovery", "no_admin")
    set_actor(session, "system:recovery")
    link = ""

    def make() -> None:
        nonlocal link
        link = make_reset_link(
            session,
            admin.id,
            now,
            kind="recovery",
            recovery_token_hash=token_hash,
        )

    # Two at once both pass the check above: the unique index stops one.
    if not unique(session, make):
        _refuse("recovery", "used")
    recoveries.add(1, {"outcome": "made"})
    log.warning("recovery link made", admin_id=admin.id)
    activity.record(session, "recovery_link_made", None, admin.id, notify)
    return admin, link


# Other administrators (admin/admins.py)

# A link one administrator makes for another, which they pass on themselves.
# Mail and the administrator pages say "within a day": change them with it.
MADE_LINK_LIFETIME = timedelta(hours=24)


# What a link made for another administrator does: lets them choose a first
# password, or a new one.
LinkKind = Literal["invite", "reset"]

# Why an invite, or an action on another administrator, was refused.
AdminRefusal = Literal["not_found", "themselves"]


class AdminRefused(Exception):
    def __init__(self, reason: AdminRefusal) -> None:
        super().__init__(reason)
        self.reason: AdminRefusal = reason


AdminAction = Literal["invite", "reset", "retire", "restore"]
# Why, for the log: the page says only not found, but an operator should
# tell a session cut off meanwhile (signed_out, a warning) from a stale id.
Why = Literal["themselves", "no_such_admin", "signed_out"]


def _refuse_action(
    action: AdminAction,
    why: Why,
    by_admin_id: int,
    target_admin_id: int | None = None,
) -> NoReturn:
    emit = log.warning if why == "signed_out" else log.info
    emit(
        "administrator action refused",
        action=action,
        why=why,
        by_admin_id=by_admin_id,
        target_admin_id=target_admin_id,
    )
    raise AdminRefused("themselves" if why == "themselves" else "not_found")


sign_in_resets = meter.create_counter(
    "inventory.admin.sign_in_resets",
    unit="{reset}",
    description="Administrators' sign-ins reset by another administrator",
)


def _remove_sign_in(session: Session, admin_id: int, now: datetime) -> int:
    """Their links are voided, their password goes, their authenticators are
    retired (keys wiped), their recovery codes voided and their sessions
    ended, so nothing a thief may hold works. Links first, as use_reset_link
    locks its link before the password: a link being used meanwhile
    finishes, then this undoes it. A link made after this is the only one.
    The caller holds the administrators lock. How many sessions ended."""
    void_reset_links(session, admin_id, now)
    ended = end_sessions(session, admin_id, now)
    if (password := get_locked(session, AdminPassword, admin_id)) is not None:
        session.delete(password)
    _reset_authenticators(session, admin_id, now)
    return ended


def reset_admin_sign_in(
    session: Session,
    admin_id: int,
    by_admin_id: int,
    now: datetime,
    notify: Notifier | None = None,
) -> tuple[Admin, str, LinkKind]:
    """Reset another administrator's sign-in, at once (_remove_sign_in), even
    if the link is never used: never one's own, which the account page
    changes. Returns them, a one-use link that sets a new password, for the
    one acting to pass on, and its kind: one who never chose a password has
    nothing to reset, so is invited again. TOTP is set up again at sign-in
    while required. Raises AdminRefused."""
    admin = _locked_other(session, "reset", admin_id, by_admin_id, retired=False)
    kind: LinkKind = "reset" if has_password(session, admin.id) else "invite"
    ended = _remove_sign_in(session, admin.id, now)
    token = make_reset_link(session, admin.id, now, kind=kind, made_by_id=by_admin_id)
    if kind == "reset":
        sign_in_resets.add(1, {"action": "reset"})
    log.info(
        "sign-in reset",
        target_admin_id=admin.id,
        by_admin_id=by_admin_id,
        kind=kind,
        sessions_ended=ended,
    )
    activity.record(session, "sign_in_reset", by_admin_id, admin.id, notify)
    return admin, token, kind


def invite(
    session: Session,
    username: str,
    email: str | None,
    made_by_id: int | None,
    now: datetime,
    notify: Notifier | None = None,
) -> tuple[Admin, str]:
    """A new administrator, with no password yet, so nobody can sign in as
    them, and the one-use link that lets them choose one: made by another
    administrator, or by the operator (None), whose link lasts an hour. They
    set up TOTP at their first sign-in while it is required. A username or
    email already taken is an IntegrityError on flush; AdminRefused if the
    one inviting can no longer sign in (retired, or their sign-in reset,
    meanwhile)."""
    if made_by_id is None:
        _lock_every_admin(session)
    elif not lock_admins(session, made_by_id):
        _refuse_action("invite", "signed_out", made_by_id)
    admin = Admin(username=username, email=email or None)
    session.add(admin)
    session.flush()
    token = make_reset_link(
        session, admin.id, now, kind="invite", made_by_id=made_by_id
    )
    log.info("administrator invited", admin_id=admin.id, made_by_id=made_by_id)
    activity.record(session, "invited", made_by_id, admin.id, notify)
    return admin, token


def has_password(session: Session, admin_id: int) -> bool:
    return exists_where(session, AdminPassword.id == admin_id)


def admin_can_sign_in(session: Session, admin: Admin) -> bool:
    return admin.retired_at is None and has_password(session, admin.id)


def admins_who_can_sign_in(session: Session) -> int:
    """Current administrators with a password: an invite not yet taken up
    cannot let anyone back in."""
    return (
        session.scalar(
            select(func.count())
            .select_from(Admin)
            .join(AdminPassword, AdminPassword.id == Admin.id)
            .where(Admin.retired_at.is_(None))
        )
        or 0
    )


def lock_admin(session: Session, admin_id: int) -> None:
    """Lock one administrator's row, before anything else of theirs is
    locked, as lock_admins does for them all. FOR NO KEY UPDATE
    (key_share): it waits for every other such lock, but not for a row that
    refers to theirs being inserted (a password, a link, a ledger row),
    which FOR UPDATE would also hold up. It does not read the row again."""
    session.execute(
        locked(select(Admin.id).where(Admin.id == admin_id), key_share=True)
    )


def lock_current(session: Session, admin: Admin) -> bool:
    """Lock their row and read it again, for a path that found them
    unlocked: True if they are still current."""
    get_locked(session, Admin, admin.id, key_share=True)
    return admin.retired_at is None


def lock_admins(session: Session, by_admin_id: int) -> bool:
    """Lock every administrator row, in one order, and read them again, so
    changes to who may sign in happen one at a time and never deadlock.
    False if the one acting can no longer sign in: retired, or their own
    sign-in reset meanwhile, so a request already waiting from a session
    just cut off changes nothing. As the one acting can always sign in and
    is never the one acted on, someone who can sign in always remains."""
    _lock_every_admin(session)
    return admin_can_sign_in(session, session.get_one(Admin, by_admin_id))


def _lock_every_admin(session: Session) -> list[Admin]:
    """Every administrator row, locked in one order and read again."""
    return list(
        session.scalars(locked(select(Admin).order_by(Admin.id), key_share=True))
    )


def _locked_other(
    session: Session,
    action: AdminAction,
    admin_id: int,
    by_admin_id: int,
    *,
    retired: bool,
) -> Admin:
    """Under the administrators lock (lock_admins), the other administrator
    to act on, retired or current as asked. AdminRefused: 'themselves' for
    oneself, which only a current administrator can be; 'not_found' if
    there is none, or the one acting can no longer sign in. An id with no
    administrator at all is refused before any lock."""
    if admin_id == by_admin_id:
        _refuse_action(action, "themselves", by_admin_id, admin_id)
    admin = session.get(Admin, admin_id)
    if admin is None:
        _refuse_action(action, "no_such_admin", by_admin_id, admin_id)
    # lock_admins reads every administrator again, this one too.
    if not lock_admins(session, by_admin_id):
        _refuse_action(action, "signed_out", by_admin_id, admin_id)
    if (admin.retired_at is not None) != retired:
        _refuse_action(action, "no_such_admin", by_admin_id, admin_id)
    return admin


def retire_admin(
    session: Session,
    admin_id: int,
    by_admin_id: int,
    now: datetime,
    notify: Notifier | None = None,
) -> None:
    """Retire another administrator (_retire). Never oneself; someone who
    can sign in always remains (lock_admins). Raises AdminRefused."""
    admin = _locked_other(session, "retire", admin_id, by_admin_id, retired=False)
    ended = _retire(session, admin, now)
    log.info(
        "administrator retired",
        target_admin_id=admin.id,
        by_admin_id=by_admin_id,
        sessions_ended=ended,
    )
    activity.record(session, "retired", by_admin_id, admin.id, notify)


def _retire(session: Session, admin: Admin, now: datetime) -> int:
    """Retired, with everything they sign in with removed (_remove_sign_in),
    so a retired account holds no credential to steal or crack. How many
    sessions ended."""
    admin.retired_at = now
    return _remove_sign_in(session, admin.id, now)


def restore_admin(
    session: Session,
    admin_id: int,
    by_admin_id: int,
    now: datetime,
    notify: Notifier | None = None,
) -> tuple[Admin, str]:
    """Bring a retired administrator back. Retiring removed everything they
    signed in with, so they start again as if invited: returns them and a
    one-use link that lets them choose a password, for the one acting to
    pass on. Raises AdminRefused."""
    admin = _locked_other(session, "restore", admin_id, by_admin_id, retired=True)
    admin.retired_at = None
    token = make_reset_link(
        session, admin.id, now, kind="invite", made_by_id=by_admin_id
    )
    log.info(
        "administrator restored", target_admin_id=admin_id, by_admin_id=by_admin_id
    )
    activity.record(session, "restored", by_admin_id, admin.id, notify)
    return admin, token


# The operator's break-glass (docs/decisions/0018), from python -m
# inventory_tng.admins: no administrator acts, so none needs to sign in.
# Their links are made by nobody (made_by_id NULL), so last an hour.


# In the history and the logs as the one who did it.
OPERATOR_ACTOR = "system:operator"


class OperatorRefused(Exception):
    """An operator command refused, and why, as its words to the operator."""


def lock_down(
    session: Session, expected: int, now: datetime, notify: Notifier
) -> list[Admin]:
    """Retire every current administrator at once, as retiring one does
    (_retire), and revoke every registration code they may have made, for
    when several may be compromised. `expected` is how many the operator
    believes are current, so a typo or a stale belief refuses rather than
    surprises. Those retired are told by mail, as nobody is left to see it
    in the app. Returns them. Raises OperatorRefused."""
    current = [a for a in _lock_every_admin(session) if a.retired_at is None]
    if len(current) != expected:
        log.warning("lockdown refused", current=len(current), expected=expected)
        raise OperatorRefused(
            f"{len(current)} administrators are current, not {expected}."
        )
    set_actor(session, OPERATOR_ACTOR)
    for admin in current:
        _retire(session, admin, now)
    codes = devices.revoke_every_code(session, now)
    log.warning(
        "administrators locked out",
        admin_ids=[a.id for a in current],
        registration_codes_revoked=codes,
    )
    row = activity.record(session, "locked_down", None, None, None)
    told = mail.activity(activity.words(session, row))
    for admin in current:
        notify.tell(admin.email, admin.id, told)
    return current


def let_in(
    session: Session, username: str, now: datetime, notify: Notifier
) -> tuple[Admin, str]:
    """Let one administrator back in, retired or not, with everything they
    signed in with removed (_remove_sign_in): returns them and a one-use
    reset link, lasting an hour, for the operator to pass on, with which
    they choose a password and set up TOTP again. Raises OperatorRefused."""
    wanted = username.strip().lower()
    found = [a for a in _lock_every_admin(session) if a.username.lower() == wanted]
    if not found:
        raise OperatorRefused(f"No administrator is called {username}.")
    (admin,) = found
    set_actor(session, OPERATOR_ACTOR)
    admin.retired_at = None
    _remove_sign_in(session, admin.id, now)
    token = make_reset_link(session, admin.id, now, kind="reset")
    log.warning("administrator let in", admin_id=admin.id)
    activity.record(session, "let_in", None, admin.id, notify)
    notify.tell(admin.email, admin.id, mail.let_in)
    return admin, token


def invite_by_operator(
    session: Session, username: str, email: str | None, now: datetime, notify: Notifier
) -> tuple[Admin, str]:
    """A new administrator, invited by the operator (invite). Raises
    OperatorRefused for a username or email taken or not allowed."""
    if problem := username_problem(username) or email_problem(email or ""):
        raise OperatorRefused(problem)
    set_actor(session, OPERATOR_ACTOR)
    made: list[tuple[Admin, str]] = []
    if not unique(
        session,
        lambda: made.append(invite(session, username, email, None, now, notify)),
    ):
        raise OperatorRefused("Another administrator has that username or email.")
    admin, token = made[0]
    notify.tell(admin.email, admin.id, mail.invited_by_operator)
    return admin, token


def retire_authenticators(
    session: Session, keys: TotpKeys, now: datetime, *, every: bool, notify: Notifier
) -> tuple[int, list[str]]:
    """Retire every authenticator that cannot be read (a lost TOTP key), or
    `every` one (a leaked key: resealing does not change TOTP secrets).
    Recovery codes stay, and sign-in asks for one while any is unused, so
    each administrator signs in with a password and a recovery code and
    sets up TOTP again. One left with no second factor has their sign-in
    removed, as the password alone must not get in: returns how many
    authenticators were retired, and the usernames of those, to let in."""
    admins = _lock_every_admin(session)
    set_actor(session, OPERATOR_ACTOR)
    retired, stranded = 0, []
    for admin in admins:
        chosen = [
            a
            for a in locked_authenticators(session, admin.id)
            if every or _opened(keys, a) is None
        ]
        for authenticator in chosen:
            retire(authenticator, now)
        retired += len(chosen)
        if (
            chosen
            and admin.retired_at is None
            and not has_second_factor(session, admin.id)
        ):
            _remove_sign_in(session, admin.id, now)
            stranded.append(admin.username)
    log.warning(
        "authenticators retired",
        retired=retired,
        every=every,
        stranded=len(stranded),
    )
    if retired:
        activity.record(session, "authenticators_retired", None, None, notify)
    return retired, stranded
