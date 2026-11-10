"""Administrators, and signing them in."""

import base64
import html
import uuid
from datetime import UTC, datetime

import pyotp
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from inventory_tng import accounts, sign_in, totp_keys
from inventory_tng.models import (
    Admin,
    AdminPassword,
    AdminSession,
    Authenticator,
    ResetKind,
    SignInSettings,
)
from inventory_tng.totp_keys import TotpKeys

# A recovery code as the page shows it, in groups.
_GROUP = f"[A-Z0-9]{{{accounts.RECOVERY_CODE_GROUP}}}"
_MORE = accounts.RECOVERY_CODE_LENGTH // accounts.RECOVERY_CODE_GROUP - 1
RECOVERY_CODE = rf"<li>({_GROUP}(?:-{_GROUP}){{{_MORE}}})</li>"
PASSWORD = "correct horse battery staple"


def raw_key() -> bytes:
    """A new key, as TotpKeys holds one."""
    return base64.b64decode(totp_keys.new_key())


# What the tests seal authenticators' keys with: version 1, given to their
# apps (conftest's make_app), and as text in TOTP_KEYS_DIR for the rest
# (conftest's no_settings_from_the_shell), as new_key() writes it.
TOTP_KEY = totp_keys.new_key()
TOTP_KEYS = TotpKeys({1: base64.b64decode(TOTP_KEY)})


def once(**form: str) -> dict[str, str]:
    """A form that makes something shown once, with the new key its page
    gives it (once.py)."""
    return {"once": str(uuid.uuid4())} | form


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
        key = accounts.new_totp_key()
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


def policy(session: Session, value: str) -> None:
    session.get_one(SignInSettings, 1).totp_policy = value
    session.flush()


def sign_in_with(
    client: TestClient, username: str = "ada", password: str = PASSWORD, **form: str
) -> Response:
    return client.post(
        sign_in.SIGN_IN,
        data={"username": username, "password": password} | form,
    )


def text(response: Response) -> str:
    """The page as read, with Jinja's escaping undone."""
    return html.unescape(response.text)


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


def other_session(
    session: Session, admin: Admin, user_agent: str | None = None
) -> AdminSession:
    """Another session of theirs, as if signed in elsewhere."""
    return accounts.start_session(session, admin.id, user_agent, datetime.now(UTC))[0]
