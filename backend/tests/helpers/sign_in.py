"""Administrators, and signing them in."""

import base64
import contextlib
import re
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.admins import (
    accounts,
    authenticator_totp,
    guards,
    paths,
    sealed_totp_keys,
)
from inventory_tng.admins.sealed_totp_keys import TotpKeys
from inventory_tng.models.admins import (
    Admin,
    AdminPassword,
    AdminSession,
    Authenticator,
    ResetKind,
)
from inventory_tng.models.site_settings import CONFIRM_IDLE_MINUTES, SignInSettings
from inventory_tng.web.single_use_forms import FIELD

from helpers.markup import read

# A recovery code as the page shows it, in groups.
_GROUP = f"[A-Z0-9]{{{accounts.RECOVERY_CODE_GROUP}}}"
_MORE = accounts.RECOVERY_CODE_LENGTH // accounts.RECOVERY_CODE_GROUP - 1
_RECOVERY_CODE = re.compile(rf"{_GROUP}(?:-{_GROUP}){{{_MORE}}}")
PASSWORD = "correct horse battery staple"


def recovery_codes(page: str) -> list[str]:
    """The recovery codes `page` lists, each an item of its own."""
    return [
        item.text
        for item in read(page).all("li")
        if _RECOVERY_CODE.fullmatch(item.text)
    ]


def raw_key() -> bytes:
    """A new key, as TotpKeys holds one."""
    return base64.b64decode(sealed_totp_keys.new_key())


# What the tests seal authenticators' keys with: version 1, given to their
# apps (conftest's make_app), and as text in TOTP_KEYS_DIR for the rest
# (conftest's no_settings_from_the_shell), as new_key() writes it.
TOTP_KEY = sealed_totp_keys.new_key()
TOTP_KEYS = TotpKeys({1: base64.b64decode(TOTP_KEY)})


def single_use(**form: str) -> dict[str, str]:
    """A form that makes something shown once, with the new key its page
    gives it (web/single_use_forms.py)."""
    return {FIELD: str(uuid.uuid4())} | form


def admin_named(session: Session, username: str) -> Admin:
    return session.scalars(select(Admin).where(Admin.username == username)).one()


def with_code(session: Session, by: str = "ada", **form: str) -> dict[str, str]:
    """A form that may ask for a code (guards.ConfirmedAccountChange), with
    one: the first of new recovery codes made for `by`, so each form gets
    one never used. Sent once, as single_use() is."""
    admin = admin_named(session, by)
    first = accounts.make_recovery_codes(session, admin.id, datetime.now(UTC))[0]
    return single_use(**form) | {guards.CONFIRM_FIELD: first}


def new_admin(
    session: Session,
    username: str = "ada",
    *,
    totp: bool = True,
    email: str | None = None,
) -> tuple[Admin, str | None]:
    """An administrator with PASSWORD, and their authenticator's key."""
    admin = Admin(username=username, email=email)
    session.add(admin)
    session.flush()
    session.add(
        AdminPassword(id=admin.id, password_hash=accounts.hash_password(PASSWORD))
    )
    key = None
    if totp:
        key = authenticator_totp.new_totp_key()
        session.add(sealed(Authenticator(admin_id=admin.id, name="Phone"), key))
    session.flush()
    return admin, key


def sealed(authenticator: Authenticator, key: str) -> Authenticator:
    """`authenticator`, holding `key` as the app would seal it."""
    version, sealed_key = TOTP_KEYS.seal(key, authenticator.admin_id)
    authenticator.key_version, authenticator.sealed_key = version, sealed_key
    return authenticator


def opened(authenticator: Authenticator) -> str:
    """The key `authenticator` holds, as the app reads it."""
    assert authenticator.sealed_key is not None
    assert authenticator.key_version is not None
    return TOTP_KEYS.open(
        authenticator.key_version, authenticator.sealed_key, authenticator.admin_id
    )


def code(key: str | None, at: datetime | None = None) -> str:
    assert key is not None
    return pyotp.TOTP(key).at(at or datetime.now(UTC))


def codes_with_left(session: Session, admin_id: int, left: int) -> list[str]:
    """New recovery codes, all but the last `left` used: those are returned."""
    codes = accounts.make_recovery_codes(session, admin_id, datetime.now(UTC))
    for used in codes[: len(codes) - left]:
        accounts.check_second_factor(
            session, TOTP_KEYS, admin_id, [], used, datetime.now(UTC)
        )
    return codes[len(codes) - left :]


def policy(session: Session, value: str) -> None:
    session.get_one(SignInSettings, 1).totp_policy = value
    session.flush()


# How long an authenticator shows each code.
TIME_STEP = timedelta(seconds=authenticator_totp.TOTP_INTERVAL)


@contextlib.contextmanager
def app_clock() -> Generator[datetime]:
    """Hold the clock that checks codes (guards._now) at this moment, for
    the block and every thread, and give that moment: code(key, at) is the
    code shown now, code(key, at + TIME_STEP) the next one.

    Without it a code made on the wall clock can be refused when a busy
    machine holds the request up past the code's time step
    (inventory-tng-tuie)."""
    at = datetime.now(UTC)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(guards, "_now", lambda: at)
        yield at


# sign_in_with's key when no code is typed: None is a missing key.
_NO_KEY = ""


def sign_in_with(
    client: TestClient,
    username: str = "ada",
    password: str = PASSWORD,
    *,
    key: str | None = _NO_KEY,
    later: bool = False,
    **form: str,
) -> Response:
    """Sign in; with `key`, typing its code (app_clock), or with later=True
    the next one, as the one now is spent."""
    data = {"username": username, "password": password} | form
    if key == _NO_KEY:
        return client.post(paths.SIGN_IN, data=data)
    assert "code" not in form, "a key or a code, not both"
    with app_clock() as at:
        typed = code(key, at + TIME_STEP if later else at)
        return client.post(paths.SIGN_IN, data=data | {"code": typed})


def reset_link(
    session: Session,
    admin: Admin,
    kind: ResetKind = "emailed",
    made_by: Admin | None = None,
) -> str:
    """A reset link's token, emailed unless another kind is asked for, with
    the maker its kind needs."""
    return accounts.make_reset_link(
        session,
        admin.id,
        datetime.now(UTC),
        kind=kind,
        made_by_id=made_by.id if made_by else None,
        recovery_token_hash=b"token" if kind == "recovery" else None,
    )


def token_of(link: str) -> str:
    """The token a reset link carries."""
    return link.split("token=")[1]


def other_session(
    session: Session, admin: Admin, user_agent: str | None = None
) -> AdminSession:
    """Another session of theirs, as if signed in elsewhere."""
    return accounts.start_session(session, admin.id, user_agent, datetime.now(UTC))[0]


def confirmed_long_ago(session: Session) -> None:
    """Every session's code typed longer ago than confirm_idle_minutes can
    be, with nothing sensitive done since, as confirming leaves it: the
    window has closed, whatever the setting."""
    longest = timedelta(minutes=CONFIRM_IDLE_MINUTES[1] + 1)
    for row in session.scalars(select(AdminSession)):
        row.confirmed_at = datetime.now(UTC) - longest
        row.last_sensitive_at = None
    session.flush()
