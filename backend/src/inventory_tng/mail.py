"""Mail to administrators, through a provider's authenticated SMTP
(docs/decisions/0012): password reset links and notices of changes to
their account.

Only the standard library's smtplib and ssl here: TLS checks the provider's
certificate with the system's trust store (ssl.create_default_context).
Mail is sent after the reply (Starlette's background tasks), so how long a
reply takes never shows whether mail went out. A failure is logged and
counted, never shown on a page. Logs carry ids and the kind of mail, never
addresses or links.
"""

import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.errors import MessageError
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Annotated, Literal, Protocol

import structlog
from fastapi import BackgroundTasks, Depends, Request
from opentelemetry import metrics

from inventory_tng.settings import MailConfig

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

Kind = Literal[
    "reset_link",
    "reset_link_made",
    "invited",
    "restored",
    "password_changed",
    "email_changed",
    "recovery_code_used",
]
# Per network step. Mail goes out from the same threads as requests, so a
# provider that has gone quiet holds one only briefly.
TIMEOUT_SECONDS = 10

sent = meter.create_counter(
    "inventory.mail.sent",
    unit="{message}",
    description="Mail to administrators, by kind and outcome",
)


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str
    kind: Kind
    admin_id: int


class Mailer(Protocol):
    def send(self, message: Message) -> None: ...


class SmtpMailer:
    """One connection per message: they are few, and an idle connection to
    the provider would only time out."""

    def __init__(self, config: MailConfig) -> None:
        self.config = config
        # Made once: it reads the system's trust store, and is safe to share.
        self.tls = ssl.create_default_context()

    def send(self, message: Message) -> None:
        mail = EmailMessage()
        mail["From"] = self.config.sender
        mail["To"] = message.to
        mail["Subject"] = message.subject
        # Required (RFC 5322), and their absence looks like spam.
        mail["Date"] = formatdate()
        mail["Message-ID"] = make_msgid(domain=self.config.sender.rpartition("@")[2])
        mail.set_content(message.body)
        with self._connect() as smtp:
            if self.config.username:
                smtp.login(self.config.username, self.config.password or "")
            smtp.send_message(mail)

    def _connect(self) -> smtplib.SMTP:
        config = self.config
        if config.security == "tls":
            return smtplib.SMTP_SSL(
                config.host,
                config.port,
                timeout=TIMEOUT_SECONDS,
                context=self.tls,
            )
        smtp = smtplib.SMTP(config.host, config.port, timeout=TIMEOUT_SECONDS)
        if config.security == "starttls":
            try:
                smtp.starttls(context=self.tls)
            except BaseException:
                smtp.close()
                raise
        return smtp


def get_mailer(request: Request) -> Mailer | None:
    """The app's mailer; None without mail settings. Tests override it."""
    return request.app.state.mailer


@dataclass(frozen=True)
class Notifier:
    """What a route uses to mail an administrator after its reply."""

    mailer: Mailer | None
    tasks: BackgroundTasks

    @property
    def on(self) -> bool:
        return self.mailer is not None

    def tell(
        self, to: str | None, admin_id: int, build: Callable[[str, int], Message]
    ) -> None:
        """Mail `to` what `build` makes, if mail is set up and there is an
        address."""
        if self.mailer is not None and to:
            self.tasks.add_task(deliver, self.mailer, build(to, admin_id))

    def later(self, job: Callable[[Mailer], None]) -> None:
        """Run `job` after the reply, if mail is set up."""
        if self.mailer is not None:
            self.tasks.add_task(job, self.mailer)


def _notifier(
    tasks: BackgroundTasks, mailer: Annotated[Mailer | None, Depends(get_mailer)]
) -> Notifier:
    return Notifier(mailer, tasks)


Notify = Annotated[Notifier, Depends(_notifier)]


def deliver(mailer: Mailer, message: Message) -> None:
    """Send, logging and counting the outcome. Never raises: it runs after
    the reply, where nobody could be told."""
    try:
        mailer.send(message)
    # The network or TLS (OSError), the provider (SMTPException), or a value
    # it cannot encode or a header the policy refuses (ValueError, which
    # UnicodeEncodeError is, and MessageError).
    except (OSError, smtplib.SMTPException, ValueError, MessageError) as error:
        sent.add(1, {"kind": message.kind, "outcome": "failed"})
        log.warning(
            "mail not sent",
            kind=message.kind,
            admin_id=message.admin_id,
            error=type(error).__name__,
        )
        return
    sent.add(1, {"kind": message.kind, "outcome": "sent"})
    log.info("mail sent", kind=message.kind, admin_id=message.admin_id)


# What each says. Plain text: nothing to render, nothing to track.

IF_NOT_YOU = (
    "If it wasn't you, ask another administrator to reset your account at once."
)


def reset_link(to: str, admin_id: int, link: str) -> Message:
    return Message(
        to,
        "Reset your NYC Mesh Inventory password",
        "Someone asked to reset the password of your administrator account on "
        "NYC Mesh Inventory. If it was you, choose a new one here, within the "
        f"hour:\n\n{link}\n\n"
        "The link works once. If you use an authenticator app, signing in still "
        "asks for its code.\n\n"
        "If it wasn't you, ignore this mail: your password stays as it is.",
        "reset_link",
        admin_id,
    )


def reset_link_made(to: str, admin_id: int) -> Message:
    return Message(
        to,
        "A reset link was made for your NYC Mesh Inventory account",
        "Another administrator just made a link that resets your administrator "
        "account on NYC Mesh Inventory, and will pass it on to you. It works "
        f"once, within a day.\n\n{IF_NOT_YOU}",
        "reset_link_made",
        admin_id,
    )


def invited(to: str, admin_id: int) -> Message:
    return Message(
        to,
        "You were invited to administer NYC Mesh Inventory",
        "An administrator of NYC Mesh Inventory just invited you to be one too, "
        "and will pass you a link to choose your password with. It works once, "
        "within a day.\n\n"
        "If you weren't expecting this, ignore this mail.",
        "invited",
        admin_id,
    )


def restored(to: str, admin_id: int) -> Message:
    return Message(
        to,
        "Your NYC Mesh Inventory account was brought back",
        "Your administrator account on NYC Mesh Inventory was retired, and "
        "another administrator just brought it back: your password and "
        f"authenticators work again.\n\n{IF_NOT_YOU}",
        "restored",
        admin_id,
    )


def password_changed(to: str, admin_id: int) -> Message:
    return Message(
        to,
        "Your NYC Mesh Inventory password was changed",
        "The password of your administrator account on NYC Mesh Inventory was "
        f"just changed, and your other sessions were signed out.\n\n{IF_NOT_YOU}",
        "password_changed",
        admin_id,
    )


def email_changed(to: str, admin_id: int) -> Message:
    return Message(
        to,
        "Your NYC Mesh Inventory email was changed",
        "The email address of your administrator account on NYC Mesh Inventory "
        "was just changed from this one. Password reset links now go to the new "
        f"address.\n\n{IF_NOT_YOU}",
        "email_changed",
        admin_id,
    )


def recovery_code_used(left: int) -> Callable[[str, int], Message]:
    def build(to: str, admin_id: int) -> Message:
        return Message(
            to,
            "A recovery code was used on your NYC Mesh Inventory account",
            "One of your recovery codes was just used on your administrator "
            f"account on NYC Mesh Inventory. {left} are left: make new ones from "
            f"your account page if you are running low.\n\n{IF_NOT_YOU}",
            "recovery_code_used",
            admin_id,
        )

    return build
