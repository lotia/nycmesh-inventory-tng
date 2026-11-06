"""Administrators, and signing them in."""

import html
from datetime import UTC, datetime

import pyotp
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from inventory_tng import accounts, sign_in
from inventory_tng.models import (
    Admin,
    AdminPassword,
    AdminSession,
    Authenticator,
    ResetKind,
    SignInSettings,
)

# A recovery code as the page shows it, in groups.
_GROUP = f"[A-Z0-9]{{{accounts.RECOVERY_CODE_GROUP}}}"
_MORE = accounts.RECOVERY_CODE_LENGTH // accounts.RECOVERY_CODE_GROUP - 1
RECOVERY_CODE = rf"<li>({_GROUP}(?:-{_GROUP}){{{_MORE}}})</li>"
PASSWORD = "correct horse battery staple"


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
        session.add(Authenticator(admin_id=admin.id, name="Phone", totp_key=key))
    session.flush()
    return admin, key


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
