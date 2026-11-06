"""Stand-in mailers, and mail as a site sets it up."""

from inventory_tng.mail import Message
from inventory_tng.settings import MailConfig

# Mail as a site sets it up, for apps given a mailer. Links start here.
SITE_MAIL = MailConfig(
    host="smtp.example.org",
    site_url="https://inventory.example.org",
    sender="inventory@example.org",
)


class SentMail(list[Message]):
    """A stand-in mailer: what would have gone out."""

    def send(self, message: Message) -> None:
        self.append(message)


class FailingMail:
    """A stand-in mailer whose every send fails with `error`."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def send(self, message: Message) -> None:
        raise self.error
