"""Administrators' accounts: passwords, TOTP, recovery codes, sessions and
the first administrator (docs/decisions/0012). The pages are in sign_in.py.

Only established calls here (AGENTS.md rule 3): argon2-cffi hashes and
checks passwords, pyotp makes TOTP keys and checks codes, `secrets` makes
tokens, and devices.digest (hashlib's SHA-256) hashes tokens and recovery
codes for the database. Comparing the setup token is secrets.compare_digest.
"""

import re
import secrets
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
from sqlalchemy import ColumnElement, func, select, text, update
from sqlalchemy.orm import Session

from inventory_tng.db import exists_where, set_actor, unique
from inventory_tng.devices import digest
from inventory_tng.models import (
    Admin,
    AdminPassword,
    AdminSession,
    Authenticator,
    RecoveryCode,
    ResetLink,
    SignInSettings,
    mint_code,
)
from inventory_tng.rate_limits import limiter

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


def check_password(session: Session, admin_id: int | None, password: str) -> bool:
    row = session.get(AdminPassword, admin_id) if admin_id is not None else None
    try:
        _hasher.verify(row.password_hash if row else _DUMMY_HASH, password)
    except VerificationError, InvalidHashError:
        return False
    if row is None:
        return False
    # argon2-cffi's parameters have moved on since this hash was made.
    if _hasher.check_needs_rehash(row.password_hash):
        row.password_hash = _hasher.hash(password)
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


def qr_svg(link: str) -> Markup:
    return Markup(segno.make(link, error="m").svg_inline(scale=5, border=2))


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
    authenticator.totp_key, authenticator.retired_at = None, now


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
    cannot both accept one code. None: they do not use TOTP."""
    return list(
        session.scalars(
            select(Authenticator)
            .where(*_current(admin_id))
            .order_by(Authenticator.id)
            .with_for_update()
        )
    )


def add_authenticator(
    session: Session, admin_id: int, name: str, key: str, step: int
) -> Authenticator:
    authenticator = Authenticator(
        admin_id=admin_id, name=name, totp_key=key, last_step=step
    )
    session.add(authenticator)
    session.flush()
    log.info(
        "authenticator added", admin_id=admin_id, authenticator_id=authenticator.id
    )
    return authenticator


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
    admin_id: int,
    authenticators: list[Authenticator],
    typed: str,
    now: datetime,
) -> Factor | None:
    """A code from one of the administrator's authenticators (from
    locked_authenticators), or one of their recovery codes, used up as it is
    accepted."""
    for authenticator in authenticators:
        assert authenticator.totp_key is not None  # Current ones keep theirs.
        step = accepted_step(
            authenticator.totp_key, typed, authenticator.last_step, now
        )
        if step is not None:
            authenticator.last_step = step
            return "totp"
    if _totp_code(typed) is not None or len(typed) > CODE_FIELD_MAX:
        return None
    recovery = session.scalar(
        select(RecoveryCode)
        .where(
            *_unused_codes(admin_id), RecoveryCode.code_hash == _recovery_digest(typed)
        )
        .with_for_update()
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
) -> tuple[AdminSession, str, Factor]:
    """Check all three at once and start a session, or SignInRefused. Returns
    the session, its token and the second factor used. The caller commits,
    and only on success.

    Every attempt counts against its address before anything is checked, so
    a burst sent at once cannot all slip under the limit. Codes count
    against the account too, but only once its password was right: someone
    without the password cannot lock its administrator out."""
    if not limiter.hit(PER_ADDRESS, "sign_in", address):
        _refuse_sign_in("rate_limited", None)
    admin = current_admin(session, username)
    if not check_password(session, admin.id if admin else None, password):
        _refuse_sign_in("unknown_user" if admin is None else "bad_password", admin)
    assert admin is not None  # check_password is False without one.
    set_actor(session, f"admin:{admin.id}")
    factor: Factor | None = "password"
    if authenticators := locked_authenticators(session, admin.id):
        if not limiter.hit(PER_ACCOUNT, "code", str(admin.id)):
            _refuse_sign_in("rate_limited", admin)
        factor = check_second_factor(session, admin.id, authenticators, code, now)
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
) -> bool:
    """True if it replaced one they had."""
    stored = session.get(AdminPassword, admin_id)
    if stored is None:
        session.add(AdminPassword(id=admin_id, password_hash=hash_password(password)))
        return False
    stored.password_hash, stored.changed_at = hash_password(password), now
    return True


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


Confirmation = Literal["confirmed", "refused", "rate_limited"]


def confirm(
    session: Session, row: AdminSession, typed: str, now: datetime
) -> tuple[Confirmation, Factor | None]:
    """A code (or, without TOTP, the password) for sensitive actions. Every
    attempt counts against the administrator's codes, shared with signing
    in, so a stolen session cannot guess them. Returns the outcome and the
    factor used."""
    if not limiter.hit(PER_ACCOUNT, "code", str(row.admin_id)):
        return _confirmation(row, "rate_limited"), None
    set_actor(session, f"admin:{row.admin_id}")
    factor: Factor | None = None
    if authenticators := locked_authenticators(session, row.admin_id):
        factor = check_second_factor(session, row.admin_id, authenticators, typed, now)
    elif check_password(session, row.admin_id, typed):
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


def check_setup_token(expected: str, typed: str, address: str) -> None:
    _check_operator_token("setup", expected, typed, address)


def _check_operator_token(
    purpose: Literal["setup", "recovery"], expected: str, typed: str, address: str
) -> None:
    """A token the operator set in the deployment's settings."""
    # Counted before it is compared, so a burst cannot slip under the limit.
    if not limiter.hit(PER_ADDRESS, purpose, address):
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
        add_authenticator(session, admin.id, authenticator_name, totp_key, step)
        codes = make_recovery_codes(session, admin.id, now)
    row, token = start_session(session, admin.id, user_agent, now)
    log.info("first administrator set up", admin_id=admin.id, sign_in_id=row.id)
    return FirstAdmin(admin, row, token, codes)


# One's own account (account.py's page)


def change_password(
    session: Session, row: AdminSession, password: str, now: datetime
) -> None:
    """A new password; every other session of theirs ends, and any unused
    reset link. The caller has checked it (password_problem) and commits."""
    _set_password(session, row.admin_id, password, now)
    ended = end_sessions(session, row.admin_id, now, keep=row.id)
    void_reset_links(session, row.admin_id, now)
    log.info("password changed", admin_id=row.admin_id, sessions_ended=ended)


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
        select(Authenticator)
        .where(*_current(admin_id), Authenticator.id == authenticator_id)
        .with_for_update()
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


def recovery_codes_left(session: Session, admin_id: int) -> int:
    return session.scalar(select(func.count()).where(*_unused_codes(admin_id))) or 0


def _void_recovery_codes(session: Session, admin_id: int, now: datetime) -> None:
    session.execute(
        update(RecoveryCode).where(*_unused_codes(admin_id)).values(voided_at=now)
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
    made_by_id: int | None,
    resets_password: bool,
    resets_authenticators: bool,
    lifetime: timedelta,
    recovery_token_hash: bytes | None = None,
) -> str:
    """A one-use link's token, the only time it is known. It voids earlier
    links of the same kind still unused, so the newest works: an emailed one
    voids only emailed ones, so asking by mail, which anyone may do, never
    undoes a link another administrator or the recovery token made. A link
    for authenticators only needs a password to keep: an invite has none."""
    if not resets_password and not has_password(session, admin_id):
        raise ValueError("they have no password yet: the link must set one")
    emailed = made_by_id is None and recovery_token_hash is None
    void_reset_links(session, admin_id, now, emailed_only=emailed)
    token, token_hash = _new_token()
    link = ResetLink(
        admin_id=admin_id,
        made_by_id=made_by_id,
        recovery_token_hash=recovery_token_hash,
        token_hash=token_hash,
        resets_password=resets_password,
        resets_authenticators=resets_authenticators,
        expires_at=now + lifetime,
    )
    session.add(link)
    session.flush()
    log.info(
        "reset link made",
        admin_id=admin_id,
        reset_link_id=link.id,
        made_by_id=made_by_id,
    )
    return token


def void_reset_links(
    session: Session, admin_id: int, now: datetime, *, emailed_only: bool = False
) -> None:
    query = update(ResetLink).where(ResetLink.admin_id == admin_id, *_usable_links())
    if emailed_only:
        query = query.where(ResetLink.emailed())
    session.execute(query.values(voided_at=now))


@dataclass(frozen=True)
class EmailedReset:
    admin_id: int
    email: str
    token: str


def may_ask_for_reset(address: str) -> bool:
    """Asking by mail, per address, counted before anything else."""
    return limiter.hit(PER_ADDRESS, "forgot", address)


def emailed_reset(
    session: Session, username: str, now: datetime
) -> EmailedReset | None:
    """For a current administrator with an email: a new emailed link, which
    resets the password only. None for anyone else, who is told the same."""
    admin = current_admin(session, username, Admin.email.is_not(None))
    if admin is None or admin.email is None:
        log.info("emailed reset not made", reason="no_such_address")
        return None
    if not limiter.hit(EMAILED_PER_ACCOUNT, "emailed_reset", str(admin.id)):
        log.warning("emailed reset not made", reason="rate_limited", admin_id=admin.id)
        return None
    set_actor(session, f"admin:{admin.id}")
    token = make_reset_link(
        session,
        admin.id,
        now,
        made_by_id=None,
        resets_password=True,
        resets_authenticators=False,
        lifetime=EMAILED_LINK_LIFETIME,
    )
    return EmailedReset(admin.id, admin.email, token)


def may_use_reset_link(address: str) -> bool:
    return limiter.hit(PER_ADDRESS, "reset", address)


def find_reset_link(
    session: Session, token: str, now: datetime, *, lock: bool = False
) -> tuple[ResetLink, Admin] | None:
    """The link with this token, if it can still be used, and its current
    administrator; locked when it is about to be used, so it is used once."""
    query = (
        select(ResetLink, Admin)
        .join(Admin, Admin.id == ResetLink.admin_id)
        .where(
            ResetLink.token_hash == digest(token),
            *_usable_links(),
            ResetLink.expires_at > now,
            Admin.retired_at.is_(None),
        )
    )
    found = session.execute(
        query.with_for_update(of=ResetLink) if lock else query
    ).one_or_none()
    return None if found is None else (found[0], found[1])


def use_reset_link(
    session: Session, link: ResetLink, password: str | None, now: datetime
) -> bool:
    """Does what the link was made for: a new password (checked by the caller
    with password_problem), and its authenticators and recovery codes gone,
    so a new one is set up at sign-in while TOTP is required. Every session
    of theirs ends. The caller commits. True if a password they had was
    changed: an invite's link chooses the first."""
    set_actor(session, f"admin:{link.admin_id}")
    changed = False
    if link.resets_password:
        if password is None:
            raise ValueError("this link resets the password: give one")
        changed = _set_password(session, link.admin_id, password, now)
    if link.resets_authenticators:
        for authenticator in locked_authenticators(session, link.admin_id):
            retire(authenticator, now)
        _void_recovery_codes(session, link.admin_id, now)
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
    return changed


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
) -> tuple[Admin, str]:
    """A one-use link, made with the operator's RECOVERY_TOKEN, that resets
    a current administrator's password and authenticators. Each token works
    once: its hash is kept on the link it made. Raises TokenRefused."""
    _check_operator_token("recovery", expected, typed, address)
    token_hash = digest(typed.strip())
    if exists_where(session, ResetLink.recovery_token_hash == token_hash):
        _refuse("recovery", "used")
    admin = current_admin(session, username)
    if admin is None:
        _refuse("recovery", "no_admin")
    set_actor(session, "system:recovery")
    link = ""

    def make() -> None:
        nonlocal link
        link = make_reset_link(
            session,
            admin.id,
            now,
            made_by_id=None,
            resets_password=True,
            resets_authenticators=True,
            lifetime=EMAILED_LINK_LIFETIME,
            recovery_token_hash=token_hash,
        )

    # Two at once both pass the check above: the unique index stops one.
    if not unique(session, make):
        _refuse("recovery", "used")
    recoveries.add(1, {"outcome": "made"})
    log.warning("recovery link made", admin_id=admin.id)
    return admin, link


# Other administrators (admin/admins.py)

# A link one administrator makes for another, which they pass on themselves.
# Mail and the administrator pages say "within a day": change them with it.
MADE_LINK_LIFETIME = timedelta(hours=24)


LinkRefusal = Literal["not_found", "themselves", "needs_password"]


class LinkRefused(Exception):
    def __init__(self, reason: LinkRefusal) -> None:
        super().__init__(reason)
        self.reason: LinkRefusal = reason


def make_link_for(
    session: Session,
    admin_id: int,
    by_admin_id: int,
    now: datetime,
    *,
    resets_password: bool,
    resets_authenticators: bool,
) -> tuple[Admin, str]:
    """A reset link one administrator makes for another, to pass on: never
    for oneself, who has the account page, and never for authenticators
    alone while they have no password to keep. Raises LinkRefused."""
    if not lock_admins(session, by_admin_id):
        raise LinkRefused("not_found")
    admin = session.get(Admin, admin_id)
    if admin is None or admin.retired_at is not None:
        raise LinkRefused("not_found")
    if admin.id == by_admin_id:
        raise LinkRefused("themselves")
    if not resets_password and not has_password(session, admin.id):
        raise LinkRefused("needs_password")
    token = make_reset_link(
        session,
        admin.id,
        now,
        made_by_id=by_admin_id,
        resets_password=resets_password,
        resets_authenticators=resets_authenticators,
        lifetime=MADE_LINK_LIFETIME,
    )
    return admin, token


def invite(
    session: Session, username: str, email: str | None, made_by_id: int, now: datetime
) -> tuple[Admin, str]:
    """A new administrator, with no password yet, so nobody can sign in as
    them, and the one-use link that lets them choose one. They set up TOTP
    at their first sign-in while it is required. A username or email already
    taken is an IntegrityError on flush; LinkRefused if the one inviting was
    retired meanwhile."""
    if not lock_admins(session, made_by_id):
        raise LinkRefused("not_found")
    admin = Admin(username=username, email=email or None)
    session.add(admin)
    session.flush()
    token = make_reset_link(
        session,
        admin.id,
        now,
        made_by_id=made_by_id,
        resets_password=True,
        resets_authenticators=False,
        lifetime=MADE_LINK_LIFETIME,
    )
    log.info("administrator invited", admin_id=admin.id, made_by_id=made_by_id)
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


# not_found covers the one acting being retired meanwhile, too.
Retirement = Literal["retired", "not_found", "themselves", "last"]


def lock_admins(session: Session, by_admin_id: int) -> bool:
    """Lock every administrator row, in one order, and reload them, so
    changes to who may sign in happen one at a time and never deadlock.
    False if the one acting was retired meanwhile."""
    session.execute(
        select(Admin)
        .order_by(Admin.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return session.get_one(Admin, by_admin_id).retired_at is None


def retire_admin(
    session: Session, admin_id: int, by_admin_id: int, now: datetime
) -> Retirement:
    """Retire another administrator, ending their sessions and any reset
    link. Never oneself, and never the last who can sign in: someone must be
    able to. An invite not yet taken up may always go."""
    if admin_id == by_admin_id:
        return "themselves"
    if not lock_admins(session, by_admin_id):
        return "not_found"
    admin = session.get(Admin, admin_id)
    if admin is None or admin.retired_at is not None:
        return "not_found"
    if admin_can_sign_in(session, admin) and admins_who_can_sign_in(session) <= 1:
        return "last"
    admin.retired_at = now
    ended = end_sessions(session, admin.id, now)
    void_reset_links(session, admin.id, now)
    log.info(
        "administrator retired",
        target_admin_id=admin.id,
        by_admin_id=by_admin_id,
        sessions_ended=ended,
    )
    return "retired"


def restore_admin(
    session: Session, admin_id: int, by_admin_id: int, now: datetime
) -> bool:
    """Bring a retired administrator back; False if there is none to, or
    the one acting was retired meanwhile. Their old password and
    authenticators work again, so the caller tells them; no reset link made
    before, even one that slipped past the retiring, does."""
    if not lock_admins(session, by_admin_id):
        return False
    admin = session.get(Admin, admin_id)
    if admin is None or admin.retired_at is None:
        return False
    admin.retired_at = None
    void_reset_links(session, admin.id, now)
    log.info(
        "administrator restored", target_admin_id=admin_id, by_admin_id=by_admin_id
    )
    return True
