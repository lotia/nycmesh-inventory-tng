"""Mail settings, and sending it over SMTP (docs/decisions/0012)."""

import asyncio
import dataclasses
import smtplib
from decimal import Decimal
from email.message import EmailMessage
from typing import Self

import pytest
from fastapi import BackgroundTasks
from structlog.testing import capture_logs

from inventory_tng.core import mail
from inventory_tng.core.phrases import number, writing_numbers
from inventory_tng.core.settings import MailConfig, Settings, SettingsError

from helpers.mail import SITE_MAIL, FailingMail, SentMail

# Mail set up as a site would, for the settings tests.
MAIL_ENV = {
    "DATABASE_URL": "postgresql://u:p@db/x",
    "MAIL_HOST": "smtp.example.org",
    "MAIL_FROM": "inventory@example.org",
    "SITE_URL": "https://inventory.example.org",
}


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


def test_site_url_is_https_outside_development() -> None:
    environ = MAIL_ENV | {"SITE_URL": "http://inventory.example.org"}
    with pytest.raises(SettingsError, match="SITE_URL must be https"):
        Settings.from_environ(environ)
    for bad in ("https://u@inventory.example.org", "https://x.org/#frag"):
        with pytest.raises(SettingsError, match="SITE_URL is needed"):
            Settings.from_environ(environ | {"SITE_URL": bad})


# Sending


class FakeSMTP:
    """smtplib.SMTP's calls, written down."""

    calls: list[tuple[object, ...]]
    last: FakeSMTP

    def __init__(self, host: str, port: int, **_: object) -> None:
        self.calls = [("connect", type(self).__name__, host, port)]
        self.messages: list[EmailMessage] = []
        FakeSMTP.last = self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.calls.append(("quit",))

    def starttls(self, **_: object) -> None:
        self.calls.append(("starttls",))

    def login(self, user: str, _password: str) -> None:
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


MESSAGE = mail.Message("ada@example.org", "A subject", "A body.", "password_changed", 1)


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


@pytest.mark.usefixtures("fake_smtp")
def test_a_message_has_a_date_and_an_id() -> None:
    config = dataclasses.replace(SITE_MAIL, username=None)
    mail.SmtpMailer(config).send(MESSAGE)
    sent = FakeSMTP.last.messages[0]
    assert sent["Date"]
    assert sent["Message-ID"].endswith("@example.org>")


@pytest.mark.usefixtures("fake_smtp")
def test_a_failed_starttls_closes_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_self: FakeSMTP, **_: object) -> None:
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


def _with_a_number(to: str, admin_id: int) -> mail.Message:
    return mail.Message(to, "Subject", number(Decimal("1500.5")), "activity", admin_id)


def test_mail_writes_numbers_with_a_point_ungrouped_whatever_the_page() -> None:
    """Sent from a comma page, told, told now or later, its numbers keep
    a point (inventory-tng-4mc3), ungrouped (inventory-tng-ua04)."""
    kept = SentMail()
    tasks = BackgroundTasks()
    notify = mail.Notifier(kept, tasks)
    with writing_numbers(","):
        notify.tell("a@example.org", 1, _with_a_number)
        notify.tell_now("a@example.org", 1, _with_a_number)
        notify.later(lambda mailer: mail.deliver(mailer, _with_a_number("b", 2)))
        asyncio.run(tasks())
    assert [message.body for message in kept] == ["1500.5"] * 3
