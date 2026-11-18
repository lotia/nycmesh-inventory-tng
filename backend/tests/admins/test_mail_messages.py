"""What each mail to administrators says, and which actions send one
(docs/decisions/0012)."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.admins import accounts, mail_messages, paths
from inventory_tng.models.admins import Admin
from inventory_tng.models.site_settings import TOTP_POLICIES, SignInSettings

from helpers.mail import SentMail
from helpers.sign_in import new_admin, sign_in_with

NEW_PASSWORD = "a brand new long passphrase"


@pytest.fixture
def client(make_client: Callable[..., TestClient], sent_mail: SentMail) -> TestClient:
    return make_client(mailer=sent_mail)


def with_email(session: Session) -> tuple[Admin, str | None]:
    return new_admin(session, email="ada@example.org")


def test_a_recovery_code_used_is_told(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    admin, _ = with_email(session)
    codes = accounts.make_recovery_codes(session, admin.id, datetime.now(UTC))
    assert sign_in_with(client, code=codes[0]).status_code == 303
    (message,) = sent_mail
    assert message.kind == "recovery_code_used"
    assert mail_messages.codes_left(9) in message.body


@pytest.mark.parametrize("change", [0, 1, 45])
def test_the_sign_in_settings_mail_counts_each_setting_in_its_own_plural(
    change: int,
) -> None:
    settings = SignInSettings(
        totp_policy="required",
        session_days=1,
        confirm_hours=2,
        confirm_idle_minutes=1,
        account_change_minutes=change,
    )
    body = mail_messages.sign_in_settings_changed(settings)("ada@example.org", 1).body
    assert f"{mail_messages.TOTP_POLICY_WORDS['required']};" in body
    assert f"{mail_messages.days(1)};" in body
    assert f"{mail_messages.hours(2)}, or {mail_messages.minutes(1)} unused" in body
    # A code each time, or within so many minutes.
    said = body.split("\n\n")[0]
    assert said.endswith(f"{mail_messages.minutes(change)}.") == bool(change)
    assert body.endswith(str(mail_messages.NOT_AGREED))


def test_every_totp_policy_has_words_for_the_settings_mail() -> None:
    assert set(mail_messages.TOTP_POLICY_WORDS) == set(TOTP_POLICIES)


def test_a_password_changed_on_the_account_page_is_told(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    _, key = with_email(session)
    sign_in_with(client, key=key)
    form = {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    client.post(f"{paths.ACCOUNT}/password", data=form)
    assert [m.kind for m in sent_mail] == ["password_changed"]


def test_no_address_no_notice(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    form = {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    client.post(f"{paths.ACCOUNT}/password", data=form)
    assert sent_mail == []
