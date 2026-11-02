"""Keeps secrets and personal data out of log lines (docs/decisions/0008).

A safety net for a field logged by mistake, in console, JSON and OTLP alike.
It goes by field names: it cannot see into free text or message strings, so
the rule about what to log still applies (CONTRIBUTING.md, Logging). When
redacting, it also takes the values out of database errors' text in
tracebacks, where PostgreSQL quotes them (`Key (email)=(...) already exists`).
"""

import dataclasses
import functools
import itertools
import re
import traceback
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Self, TextIO

import psycopg
from pydantic import BaseModel, ValidationError
from sqlalchemy import exc
from structlog.typing import EventDict, ExcInfo

from inventory_tng.settings import Settings

MASK = "[masked]"

# A field is a secret if its name, lowercased and without separators,
# contains one of these, so PGPASSWORD, DBPassword and api_key all match.
SECRET_PARTS = (
    "apikey",
    "authorization",
    "connectionstring",
    "cookie",
    "credential",
    "databaseurl",
    "dsn",
    "jwt",
    "passwd",
    "password",
    "phrase",
    "privatekey",
    "pwd",
    "secret",
    "session",
    "token",
    "totp",
)
# Or if a whole word of its name is one of these, too short to look for inside
# other words ("otp" is in "footprint").
SECRET_WORDS = frozenset({"otp", "pass"})
# Personal data, masked only when redacting: whole words, from snake_case and
# camelCase alike, so `volunteer_email` matches but `username`
# (administrators' usernames may be logged) and `hostname` do not.
PERSONAL_WORDS = frozenset(
    {
        "address",
        "addresses",
        "birthday",
        "email",
        "emails",
        "firstname",
        "fullname",
        "lastname",
        "mobile",
        "phone",
        "phones",
        "street",
        "surname",
    }
)
# "name" is a person's only alone or after one of these (`volunteer_name`,
# `fullName`), so `database_name` and `table_name` are kept.
NAME_WORDS = frozenset({"name", "names"})
PERSON_WORDS = frozenset(
    {
        "contact",
        "display",
        "family",
        "first",
        "full",
        "given",
        "last",
        "legal",
        "member",
        "nick",
        "person",
        "real",
        "volunteer",
        "volunteers",
    }
)
# For a field useful while debugging that may hold personal data, such as a
# request body (sensitive_body, sensitiveBody): kept only while not redacting.
SENSITIVE = "sensitive"
# structlog's own: the exception to render, never data to mask.
EXC_INFO = "exc_info"


@dataclass(frozen=True)
class Masker:
    """A structlog processor, also used by the OTLP bridge (telemetry.py)."""

    # Personal data, sensitive_* fields, and values quoted in exception text.
    redact: bool = True
    secrets: bool = True

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            redact=not settings.development,
            secrets=not settings.logging.unmask_secrets,
        )

    def __call__(self, _logger: object, _method: str, event: EventDict) -> EventDict:
        return self.fields(event)

    def fields(self, fields: Mapping[str, object]) -> dict[str, object]:
        return {key: self._value(str(key), value) for key, value in fields.items()}

    def _value(self, key: str, value: object) -> object:
        if key == EXC_INFO:
            return value
        plain = value is None or isinstance(value, (str, int, float))
        secret, personal = _classify(key)
        if (self.secrets and secret) or (self.redact and personal):
            return MASK
        if plain:
            return value
        if isinstance(value, Mapping):
            return self.fields(value)
        if isinstance(value, BaseModel):
            return self.fields(value.model_dump())
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return self.fields(dataclasses.asdict(value))
        if isinstance(value, (list, tuple)):
            return type(value)(self._value("", item) for item in value)
        return value

    def exception_message(self, message: str, error: BaseException) -> str:
        """An exception's message, as OpenTelemetry records it."""
        if self.redact:
            for scrub in filter(None, map(_scrubber, _chain(error))):
                message = scrub(message)
        return message

    def traceback(self, text: str, error: BaseException | None) -> str:
        """A formatted traceback with the text of each error that quotes values
        scrubbed, line by line, so the prefixes an exception group adds don't
        get in the way. Code lines are left alone: they are ours, not data."""
        if not self.redact or error is None:
            return text
        for link in _chain(error):
            if scrub := _scrubber(link):
                for line in str(link).splitlines():
                    if (scrubbed := scrub(line)) != line:
                        text = text.replace(line, scrubbed)
        return text

    def write_exception(self, sio: TextIO, exc_info: ExcInfo) -> None:
        """For structlog's ConsoleRenderer."""
        sio.write("\n" + self.format_exception(exc_info))

    def format_exception(self, exc_info: ExcInfo) -> str:
        """For structlog's ExceptionRenderer (JSON)."""
        text = "".join(traceback.format_exception(*exc_info)).rstrip("\n")
        return self.traceback(text, exc_info[1])


def scrub_database_message(message: str) -> str:
    """PostgreSQL's DETAIL line, and anything quoted: values, and also names,
    which describe_error() logs separately. A quote runs to the last one on its
    line, or to the end of the line, so a quote inside a value cannot end it
    early. A value that spans lines is masked only on its first."""
    message = re.sub(r"(?m)^(DETAIL:\s*).*$", rf"\g<1>{MASK}", message)
    return "\n".join(
        _mask_quoted(_mask_quoted(line, '"'), "'") for line in message.split("\n")
    )


def scrub_validation_message(message: str) -> str:
    """Pydantic's errors quote the input: `input_value='jane@...'`."""
    return re.sub(
        r"input_value=.*?(, input_type=|\]|$)", rf"input_value={MASK}\1", message
    )


def _mask_quoted(line: str, quote: str) -> str:
    first, last = line.find(quote), line.rfind(quote)
    if first < 0:
        return line
    return line[: first + 1] + MASK + (line[last:] if last > first else "")


@functools.lru_cache(maxsize=4096)
def _classify(key: str) -> tuple[bool, bool]:
    """Whether a field name means a secret, and whether it means personal
    data. Cached: the same few names recur on every line."""
    words = re.split(r"[^a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower())
    words = [word for word in words if word]
    compact = "".join(words)
    secret = any(part in compact for part in SECRET_PARTS) or bool(
        SECRET_WORDS.intersection(words)
    )
    personal = (
        bool(PERSONAL_WORDS.intersection(words))
        or words[:1] == [SENSITIVE]
        or (len(words) == 1 and words[0] in NAME_WORDS)
        or any(
            word in NAME_WORDS and before in PERSON_WORDS
            for before, word in itertools.pairwise(words)
        )
    )
    return secret, personal


def _chain(error: BaseException) -> Iterator[BaseException]:
    """The error, every error it was raised from or during, and the members
    of any exception group among them."""
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        link = pending.pop()
        if id(link) in seen:
            continue
        seen.add(id(link))
        yield link
        pending.extend(e for e in (link.__cause__, link.__context__) if e)
        if isinstance(link, BaseExceptionGroup):
            pending.extend(link.exceptions)


def _scrubber(error: BaseException) -> Callable[[str], str] | None:
    """How to scrub an error's text, for the kinds whose messages quote values."""
    if isinstance(error, (psycopg.Error, exc.SQLAlchemyError)):
        return scrub_database_message
    if isinstance(error, ValidationError):
        return scrub_validation_message
    return None
