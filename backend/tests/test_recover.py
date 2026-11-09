"""The recovery token (resets.py, docs/decisions/0012): a locked-out
administrator gets back in with a settings change and a browser."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import accounts, sign_in
from inventory_tng.models import ResetLink
from inventory_tng.resets import RECOVER
from inventory_tng.settings import Settings, SettingsError

from helpers.mail import SentMail
from helpers.rows import history
from helpers.sign_in import new_admin, sign_in_with

TOKEN = "the operator's recovery token, long enough"
NEW_PASSWORD = "a long passphrase of their own"


@pytest.fixture
def client(make_client: Callable[..., TestClient], sent_mail: SentMail) -> TestClient:
    return make_client(recovery_token=TOKEN, mailer=sent_mail)


def test_a_locked_out_administrator_gets_back_in(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    ada, _ = new_admin(session, email="ada@example.org")
    theirs = accounts.start_session(session, ada.id, None, datetime.now(UTC))[0]
    made = client.post(RECOVER, data={"token": TOKEN, "username": "ADA"})
    assert made.status_code == 303
    link = made.headers["location"]
    assert link.startswith(f"{sign_in.RESET}?token=")
    token = link.split("token=")[1]
    form = {"token": token, "password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    assert client.post(sign_in.RESET, data=form).status_code == 303
    assert accounts.check_password(session, ada.id, NEW_PASSWORD)
    assert theirs.ended_at is not None
    assert not accounts.uses_totp(session, ada.id)
    # Signing in asks for a new authenticator, as TOTP is required.
    assert sign_in_with(client, "ada", NEW_PASSWORD).status_code == 303
    assert client.get(sign_in.HOME).headers["location"] == sign_in.NEW_AUTHENTICATOR
    assert "recovery_link_made" in [m.kind for m in sent_mail]
    made_row = history(session, "reset_link")[0]
    assert made_row.actor == "system:recovery"
    assert made_row.new_row is not None
    assert "recovery_token_hash" not in made_row.new_row


def test_each_token_works_once(client: TestClient, session: Session) -> None:
    new_admin(session)
    assert (
        client.post(RECOVER, data={"token": TOKEN, "username": "ada"}).status_code
        == 303
    )
    again = client.post(RECOVER, data={"token": TOKEN, "username": "ada"})
    assert again.status_code == 400
    assert "That token was used already" in again.text
    assert len(session.scalars(select(ResetLink)).all()) == 1


@pytest.mark.parametrize(
    ("form", "words"),
    [
        ({"token": "not it", "username": "ada"}, "isn&#39;t the recovery token"),
        ({"token": TOKEN, "username": "ben"}, "No current administrator"),
    ],
)
def test_what_is_refused(
    client: TestClient, session: Session, form: dict[str, str], words: str
) -> None:
    new_admin(session)
    refused = client.post(RECOVER, data=form)
    assert refused.status_code == 400
    assert words in refused.text
    assert session.scalars(select(ResetLink)).first() is None


def test_tries_are_limited(client: TestClient, session: Session) -> None:
    new_admin(session)
    statuses = [
        client.post(RECOVER, data={"token": "wrong", "username": "ada"}).status_code
        for _ in range(11)
    ]
    assert statuses[-1] == 429


def test_without_the_setting_there_is_no_page(
    make_client_without_database: Callable[..., TestClient],
) -> None:
    client = make_client_without_database()
    assert client.get(RECOVER).status_code == 404
    assert client.post(RECOVER, data={"token": TOKEN}).status_code == 404


def test_the_page_is_there_with_the_setting(
    make_client_without_database: Callable[..., TestClient], sent_mail: SentMail
) -> None:
    client = make_client_without_database(recovery_token=TOKEN, mailer=sent_mail)
    assert client.get(RECOVER).status_code == 200


def test_a_short_token_is_refused() -> None:
    with pytest.raises(SettingsError, match="RECOVERY_TOKEN"):
        Settings.from_environ(
            {"DATABASE_URL": "postgresql://x/y", "RECOVERY_TOKEN": "short"}
        )


def test_two_at_once_make_one_link(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both pass the used check before either writes: the unique index
    refuses the second, as "used", not as an error."""
    new_admin(session)
    now = datetime.now(UTC)
    accounts.recover(session, TOKEN, TOKEN, "ada", "192.0.2.91", now)
    monkeypatch.setattr(accounts, "exists_where", lambda *_: False)
    with pytest.raises(accounts.TokenRefused) as refused:
        accounts.recover(session, TOKEN, TOKEN, "ada", "192.0.2.91", now)
    assert refused.value.reason == "used"
    assert len(session.scalars(select(ResetLink)).all()) == 1
