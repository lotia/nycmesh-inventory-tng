"""Mail to administrators: settings, sending over SMTP, emailed password
resets and notices (docs/decisions/0012)."""

import dataclasses
import re
import smtplib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Self

import pytest
from conftest import SITE_MAIL, FailingMail, SentMail
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs
from test_sign_in import code, new_admin, reset_link, sign_in_with, text

from inventory_tng import accounts, mail, sign_in
from inventory_tng.models import Admin, AdminSession, ResetLink
from inventory_tng.settings import MailConfig, Settings, SettingsError

NEW_PASSWORD = "a brand new long passphrase"
# Mail set up as a site would, for the settings tests.
MAIL_ENV = {
    "DATABASE_URL": "postgresql://u:p@db/x",
    "MAIL_HOST": "smtp.example.org",
    "MAIL_FROM": "inventory@example.org",
    "SITE_URL": "https://inventory.example.org",
}


@pytest.fixture
def client(make_client: Callable[..., TestClient], sent_mail: SentMail) -> TestClient:
    return make_client(mailer=sent_mail)


def with_email(session: Session) -> tuple[Admin, str | None]:
    return new_admin(session, email="ada@example.org")


def ask(client: TestClient, username: str = "ada", **headers: str) -> str:
    response = client.post(sign_in.FORGOT, data={"username": username}, headers=headers)
    assert response.status_code == 200
    return text(response)


def the_link(sent: SentMail) -> str:
    (message,) = sent
    match = re.search(r"https://\S+", message.body)
    assert match is not None
    return match.group(0)


def token_of(link: str) -> str:
    return link.split("token=")[1]


# Settings


def test_mail_is_off_without_a_host() -> None:
    assert (
        Settings.from_environ({"DATABASE_URL": MAIL_ENV["DATABASE_URL"]}).mail is None
    )


def test_mail_settings() -> None:
    environ = MAIL_ENV | {
        "MAIL_HOST": "smtp.mailgun.org",
        "MAIL_USERNAME": "postmaster@example.org",
        "MAIL_PASSWORD": "s3cret",
        "SITE_URL": "https://inventory.example.org/",
    }
    config = Settings.from_environ(environ).mail
    assert config == MailConfig(
        host="smtp.mailgun.org",
        site_url="https://inventory.example.org",
        sender="inventory@example.org",
        port=587,
        username="postmaster@example.org",
        password="s3cret",
        security="starttls",
    )
    assert "s3cret" not in repr(config)
    tls = Settings.from_environ(environ | {"MAIL_SECURITY": "tls"}).mail
    assert tls is not None
    assert tls.port == 465


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"MAIL_FROM": ""}, "MAIL_FROM is needed"),
        ({"SITE_URL": ""}, "SITE_URL is needed"),
        ({"SITE_URL": "inventory.example.org"}, "SITE_URL is needed"),
        ({"MAIL_SECURITY": "ssl"}, "MAIL_SECURITY must be one of"),
        ({"MAIL_PORT": "smtp"}, "MAIL_PORT must be a port"),
        ({"MAIL_SECURITY": "none"}, "MAIL_SECURITY=none works only"),
    ],
)
def test_mail_settings_that_cannot_work_are_refused(
    extra: dict[str, str], message: str
) -> None:
    environ = MAIL_ENV | extra
    with pytest.raises(SettingsError, match=message):
        Settings.from_environ(environ)


def test_mail_in_the_clear_is_for_development() -> None:
    environ = MAIL_ENV | {
        "APP_ENV": "development",
        "MAIL_HOST": "localhost",
        "MAIL_PORT": "1025",
        "MAIL_SECURITY": "none",
        "MAIL_FROM": "inventory@localhost",
        "SITE_URL": "http://127.0.0.1:8000",
    }
    config = Settings.from_environ(environ).mail
    assert config is not None
    assert (config.port, config.security) == (1025, "none")


# Sending


class FakeSMTP:
    """smtplib.SMTP's calls, written down."""

    calls: list[tuple[object, ...]]
    last: FakeSMTP

    def __init__(self, host: str, port: int, **kwargs: object) -> None:
        self.calls = [("connect", type(self).__name__, host, port)]
        self.messages: list[EmailMessage] = []
        FakeSMTP.last = self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.calls.append(("quit",))

    def starttls(self, **kwargs: object) -> None:
        self.calls.append(("starttls",))

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user))

    def send_message(self, message: EmailMessage) -> None:
        self.calls.append(("send", message["To"], message["From"]))
        self.messages.append(message)

    def close(self) -> None:
        self.calls.append(("close",))


class FakeSMTP_SSL(FakeSMTP):
    pass


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTP_SSL)


MESSAGE = mail.password_changed("ada@example.org", 1)


@pytest.mark.usefixtures("fake_smtp")
@pytest.mark.parametrize(
    ("security", "port", "expected"),
    [
        (
            "starttls",
            587,
            [
                ("connect", "FakeSMTP", "smtp.example.org", 587),
                ("starttls",),
                ("login", "user"),
                ("send", "ada@example.org", "inventory@example.org"),
                ("quit",),
            ],
        ),
        (
            "tls",
            465,
            [
                ("connect", "FakeSMTP_SSL", "smtp.example.org", 465),
                ("login", "user"),
                ("send", "ada@example.org", "inventory@example.org"),
                ("quit",),
            ],
        ),
    ],
)
def test_sends_over_smtp(security: str, port: int, expected: list[tuple]) -> None:
    config = MailConfig(
        host="smtp.example.org",
        site_url="https://inventory.example.org",
        sender="inventory@example.org",
        port=port,
        username="user",
        password="pw",
        security=security,  # ty: ignore[invalid-argument-type]
    )
    mail.SmtpMailer(config).send(MESSAGE)
    assert FakeSMTP.last.calls == expected


def test_a_failed_send_is_logged_and_never_raises() -> None:
    with capture_logs() as logs:
        mail.deliver(FailingMail(ConnectionRefusedError()), MESSAGE)
    (line,) = [line for line in logs if line["event"] == "mail not sent"]
    assert line["error"] == "ConnectionRefusedError"
    assert "ada@example.org" not in str(logs)


# Emailed resets


def test_an_emailed_link_resets_the_password(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    admin, key = with_email(session)
    sign_in_with(client, code=code(key))
    signed_in = session.query(AdminSession).one()
    assert "a link to reset its password is on its way" in ask(client)
    link = the_link(sent_mail)
    assert link.startswith(f"{SITE_MAIL.site_url}{sign_in.RESET}?token=")
    assert sent_mail[0].to == "ada@example.org"

    form = client.get(link.removeprefix(SITE_MAIL.site_url))
    assert form.status_code == 200
    done = client.post(
        sign_in.RESET,
        data={
            "token": token_of(link),
            "password": NEW_PASSWORD,
            "password_again": NEW_PASSWORD,
        },
    )
    assert done.headers["location"] == f"{sign_in.SIGN_IN}?notice=reset"
    assert signed_in.ended_at is not None
    assert accounts.check_password(session, admin.id, NEW_PASSWORD)
    # The owner is told.
    assert [m.kind for m in sent_mail] == ["reset_link", "password_changed"]
    # TOTP is still asked for.
    client.cookies.clear()
    assert sign_in_with(client, password=NEW_PASSWORD).status_code == 400
    later = code(key, datetime.now(UTC) + timedelta(seconds=30))
    assert sign_in_with(client, password=NEW_PASSWORD, code=later).status_code == 303


def test_the_answer_is_the_same_for_anyone(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    new_admin(session, "ben")  # No email.
    answers = {ask(client, "ben"), ask(client, "nobody")}
    with_email(session)
    answers.add(ask(client, "ada"))
    assert len(answers) == 1
    assert [m.to for m in sent_mail] == ["ada@example.org"]


def test_a_link_works_once(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    with_email(session)
    ask(client)
    token = token_of(the_link(sent_mail))
    form = {"token": token, "password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    assert client.post(sign_in.RESET, data=form).status_code == 303
    assert client.post(sign_in.RESET, data=form).status_code == 404
    assert "doesn't work any more" in text(client.get(f"{sign_in.RESET}?token={token}"))


def test_a_newer_link_voids_the_older(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    with_email(session)
    ask(client)
    ask(client)
    older, newer = (token_of(re.findall(r"https://\S+", m.body)[0]) for m in sent_mail)
    assert client.get(f"{sign_in.RESET}?token={older}").status_code == 404
    assert client.get(f"{sign_in.RESET}?token={newer}").status_code == 200


def test_an_expired_link_does_not_work(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    with_email(session)
    ask(client)
    token = token_of(the_link(sent_mail))
    link = session.query(ResetLink).one()
    assert link.expires_at - link.created_at <= timedelta(hours=1, seconds=5)
    link.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()
    assert client.get(f"{sign_in.RESET}?token={token}").status_code == 404


def test_the_link_never_comes_from_the_requests_host(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    """Whoever sends the request chooses its Host: a link built from it
    could send the token to their site."""
    with_email(session)
    ask(client, host="evil.example")
    assert the_link(sent_mail).startswith(SITE_MAIL.site_url)


def test_a_short_password_is_refused_and_the_link_kept(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    with_email(session)
    ask(client)
    token = token_of(the_link(sent_mail))
    short = {"token": token, "password": "short", "password_again": "short"}
    assert client.post(sign_in.RESET, data=short).status_code == 400
    assert client.get(f"{sign_in.RESET}?token={token}").status_code == 200


def test_asking_is_rate_limited(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    with_email(session)
    for _ in range(10):
        ask(client, "nobody")
    limited = client.post(sign_in.FORGOT, data={"username": "ada"})
    assert limited.status_code == 429
    assert sent_mail == []


def test_without_mail_there_is_no_emailed_reset(
    make_app: Callable[..., FastAPI], session: Session
) -> None:
    with TestClient(make_app(), base_url="https://testserver") as client:
        assert "Forgot your password?" not in text(client.get(sign_in.SIGN_IN))
        assert "isn't set up here" in text(client.get(sign_in.FORGOT))
        refused = client.post(sign_in.FORGOT, data={"username": "ada"})
        assert refused.status_code == 404


def test_the_sign_in_page_offers_it_with_mail(client: TestClient) -> None:
    assert "Forgot your password?" in text(client.get(sign_in.SIGN_IN))


# Notices


def test_a_recovery_code_used_is_told(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    admin, _ = with_email(session)
    codes = accounts.make_recovery_codes(session, admin.id, datetime.now(UTC))
    assert sign_in_with(client, code=codes[0]).status_code == 303
    (message,) = sent_mail
    assert message.kind == "recovery_code_used"
    assert "9 are left" in message.body


def test_a_password_changed_on_the_account_page_is_told(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    _, key = with_email(session)
    sign_in_with(client, code=code(key))
    form = {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    client.post(f"{sign_in.ACCOUNT}/password", data=form)
    assert [m.kind for m in sent_mail] == ["password_changed"]


def test_no_address_no_notice(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    _, key = new_admin(session)
    sign_in_with(client, code=code(key))
    form = {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    client.post(f"{sign_in.ACCOUNT}/password", data=form)
    assert sent_mail == []


# Links another administrator makes (inventory-tng-8i4.12.4 makes them from
# a page; the reset page already does what each says)


def made_link(session: Session, admin: Admin) -> str:
    """A reset another administrator made, which removed the password and
    authenticators already."""
    colleague, _ = new_admin(session, "colleague")
    return accounts.reset_sign_in(
        session, admin.id, colleague.id, datetime.now(UTC), action="reset"
    )[0]


def test_a_made_link_sets_a_password_and_tells_the_owner(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    """Its password went when it was made, so none is "changed": told anyway."""
    admin, _ = with_email(session)
    token = made_link(session, admin)
    assert "removes your authenticators" in text(
        client.get(f"{sign_in.RESET}?token={token}")
    )
    form = {"token": token, "password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    assert client.post(sign_in.RESET, data=form).status_code == 303
    assert accounts.check_password(session, admin.id, NEW_PASSWORD)
    assert not accounts.uses_totp(session, admin.id)
    assert [m.kind for m in sent_mail] == ["password_changed"]


def test_asking_by_mail_never_undoes_a_reset_another_made(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    """With the authenticators gone too, a mailbox alone would be enough."""
    admin, _ = with_email(session)
    token = made_link(session, admin)
    ask(client)
    assert sent_mail == []
    assert client.get(f"{sign_in.RESET}?token={token}").status_code == 200
    # One asked for just as the reset was made, so not voided by it, fails.
    emailed = reset_link(session, admin)
    assert accounts.find_reset_link(session, emailed, datetime.now(UTC)) is None


def test_mail_to_one_account_is_limited_whoever_asks(
    client: TestClient,
    session: Session,
    sent_mail: SentMail,
    make_client: Callable[..., TestClient],
) -> None:
    with_email(session)
    for n in range(5):
        ask(make_client(client.app, address=f"192.0.2.{10 + n}"))
    assert len(sent_mail) == 3


def test_a_message_has_a_date_and_an_id(fake_smtp: None) -> None:
    config = dataclasses.replace(SITE_MAIL, username=None)
    mail.SmtpMailer(config).send(MESSAGE)
    sent = FakeSMTP.last.messages[0]
    assert sent["Date"]
    assert sent["Message-ID"].endswith("@example.org>")


def test_a_failed_starttls_closes_the_connection(
    fake_smtp: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: FakeSMTP, **kwargs: object) -> None:
        raise smtplib.SMTPNotSupportedError

    monkeypatch.setattr(FakeSMTP, "starttls", refuse)
    with pytest.raises(smtplib.SMTPNotSupportedError):
        mail.SmtpMailer(SITE_MAIL).send(MESSAGE)
    assert FakeSMTP.last.calls[-1] == ("close",)


def test_a_password_smtplib_cannot_encode_is_logged_not_raised() -> None:
    error = UnicodeEncodeError("ascii", "pässword", 1, 2, "not ascii")
    with capture_logs() as logs:
        mail.deliver(FailingMail(error), MESSAGE)
    assert any(line["event"] == "mail not sent" for line in logs)


def test_site_url_is_https_outside_development() -> None:
    environ = MAIL_ENV | {"SITE_URL": "http://inventory.example.org"}
    with pytest.raises(SettingsError, match="SITE_URL must be https"):
        Settings.from_environ(environ)
    for bad in ("https://u@inventory.example.org", "https://x.org/#frag"):
        with pytest.raises(SettingsError, match="SITE_URL is needed"):
            Settings.from_environ(environ | {"SITE_URL": bad})
