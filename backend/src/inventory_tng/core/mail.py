"""Sending mail to administrators, through a provider's authenticated SMTP
(docs/decisions/0012). What each mail says is in admins/mail_messages.py.

Only the standard library's smtplib and ssl here: TLS checks the provider's
certificate with the system's trust store (ssl.create_default_context).
Mail is sent after the reply (Starlette's background tasks), so how long a
reply takes never shows whether mail went out; the one exception is the
notice of a recovery code used to confirm a form, sent as soon as that use
is kept, as the form may yet end in an error whose page sends nothing
after it (admins/guards._confirm). A failure is logged and counted, never
shown on a page. Logs carry ids and the kind of mail, never addresses or
links. Its numbers are written with a point, ungrouped, whatever the page
that sent it writes (phrases.writing_numbers).
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
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.core.metrics_at_once import at_once
from inventory_tng.core.phrases import writing_numbers
from inventory_tng.core.settings import MailConfig
from inventory_tng.models.admins import Admin

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

Kind = Literal[
    "reset_link",
    "reset_link_made",
    "invited",
    "restored",
    "recovery_link_made",
    "password_changed",
    "email_changed",
    "recovery_code_used",
    "recovery_codes_made",
    "authenticators_changed",
    "authenticators_retired",
    "left_without_a_factor",
    "retired",
    "sign_in_settings_changed",
    "activity",
    "let_in",
    "invited_by_operator",
]
# Per network step. Mail goes out from the same threads as requests, so a
# provider that has gone quiet holds one only briefly.
TIMEOUT_SECONDS = 10

sent = at_once(
    "nothing written",
    meter.create_counter(
        "inventory.mail.sent",
        unit="{message}",
        description="Mail to administrators, by kind and outcome",
    ),
)


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str
    kind: Kind
    admin_id: int


# What a mail is made from: whom to, and the administrator it is for.
Told = Callable[[str, int], Message]


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

    def tell(self, to: str | None, admin_id: int, build: Told) -> None:
        """Mail `to` what `build` makes, if mail is set up and there is an
        address."""
        if self.mailer is not None and to:
            self.tasks.add_task(deliver, self.mailer, _as_mail(build, to, admin_id))

    def tell_admins(
        self,
        session: Session,
        build: Told,
        *,
        but: int | None = None,
    ) -> None:
        """Mail every current administrator with an address, `but` one."""
        if not self.on:
            return
        query = select(Admin.id, Admin.email).where(
            Admin.retired_at.is_(None), Admin.email.is_not(None)
        )
        if but is not None:
            query = query.where(Admin.id != but)
        for admin_id, email in session.execute(query):
            self.tell(email, admin_id, build)

    def tell_now(self, to: str | None, admin_id: int, build: Told) -> None:
        """As tell, but before the reply: for the one notice whose cause is
        already kept while the reply may yet be an error page, which sends
        nothing after it (admins/guards._confirm). The reply then waits for the
        provider."""
        if self.mailer is not None and to:
            deliver(self.mailer, _as_mail(build, to, admin_id))

    def later(self, job: Callable[[Mailer], None]) -> None:
        """Run `job` after the reply, if mail is set up."""
        if self.mailer is not None:
            self.tasks.add_task(_as_mail, job, self.mailer)


def _as_mail[*Args, Made](make: Callable[[*Args], Made], *args: *Args) -> Made:
    """What `make` makes of `args`, its numbers as mail writes them: only
    those written within it, so a mail's numbers are written in its build."""
    with writing_numbers("."):
        return make(*args)


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
