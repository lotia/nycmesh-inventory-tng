"""Settings, read once from the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError

from inventory_tng.local_env import CLONE

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
ENVIRONMENTS = ("development", "production")
DEFAULT_PORTS = {"http": 80, "https": 443}
# A setup token shorter than this could be guessed; secrets.token_urlsafe()
# gives 43 characters.
SETUP_TOKEN_LENGTH = 32


class SettingsError(Exception):
    """A setting is missing or unusable."""


@dataclass(frozen=True)
class LogConfig:
    level: str = "INFO"
    # Single loggers' levels, e.g. {"sqlalchemy.engine": "INFO"}.
    levels: dict[str, str] = field(default_factory=dict)
    format: Literal["console", "json"] = "console"
    # The client's address on each request line (CONTRIBUTING.md, Logging).
    client_address: bool = True
    # Successful health checks at info (True) or demoted to debug (False).
    health_checks: bool = True
    # Secrets in logs and telemetry, for debugging. Development only.
    unmask_secrets: bool = False

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> Self:
        default = cls()
        return cls(
            level=_level(environ.get("LOG_LEVEL") or default.level, "LOG_LEVEL"),
            levels=_levels(environ.get("LOG_LEVELS") or ""),
            format=_format(environ.get("LOG_FORMAT") or default.format),
            client_address=_flag(environ, "LOG_CLIENT_ADDRESS", default.client_address),
            health_checks=_flag(environ, "LOG_HEALTH_CHECKS", default.health_checks),
            unmask_secrets=_flag(environ, "UNMASK_SECRETS", default.unmask_secrets),
        )


# frontend/dist in a clone; the container sets FRONTEND_DIST.
CLONE_DIST = CLONE / "frontend" / "dist"


@dataclass(frozen=True)
class FrontendConfig:
    """Where the scan page's bundle is (docs/decisions/0010)."""

    # What `vite build` wrote, or the container's compressed copy of it.
    dist: Path = CLONE_DIST
    # Vite's dev server, such as http://localhost:5173: pages load the bundle
    # from it instead of dist. Development only.
    dev_server: str | None = None

    def __post_init__(self) -> None:
        if self.dev_server is not None:
            object.__setattr__(self, "dev_server", _dev_server(self.dev_server))

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> Self:
        dist = environ.get("FRONTEND_DIST", "").strip()
        return cls(
            dist=Path(dist) if dist else cls.dist,
            dev_server=environ.get("VITE_DEV_SERVER", "").strip() or None,
        )


MailSecurity = Literal["starttls", "tls", "none"]
DEFAULT_MAIL_PORTS: dict[MailSecurity, int] = {"starttls": 587, "tls": 465, "none": 25}


@dataclass(frozen=True)
class MailConfig:
    """A provider's authenticated SMTP, such as Mailgun's or Gmail's
    (docs/decisions/0012). Without MAIL_HOST, no mail is sent."""

    host: str
    # Links in mail start here, never at the request's own Host header,
    # which whoever sends the request chooses.
    site_url: str
    sender: str
    port: int = 587
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    # starttls on 587, tls on 465; none only in development, for Mailpit.
    security: MailSecurity = "starttls"

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> Self | None:
        host = environ.get("MAIL_HOST", "").strip()
        if not host:
            return None
        security = _mail_security(
            environ.get("MAIL_SECURITY", "").strip() or "starttls"
        )
        port = environ.get("MAIL_PORT", "").strip()
        sender = environ.get("MAIL_FROM", "").strip()
        if not sender:
            raise SettingsError("MAIL_FROM is needed with MAIL_HOST")
        return cls(
            host=host,
            site_url=_site_url(environ.get("SITE_URL", "")),
            sender=sender,
            port=_port(port) if port else DEFAULT_MAIL_PORTS[security],
            username=environ.get("MAIL_USERNAME", "").strip() or None,
            password=environ.get("MAIL_PASSWORD") or None,
            security=security,
        )


@dataclass(frozen=True)
class Settings:
    database_url: str
    # APP_ENV=development. Anything else is production, so forgetting it is
    # safe (docs/decisions/0008). What it changes is listed under APP_ENV in
    # DEPLOYMENT.md; the code reads it only from here.
    development: bool = False
    logging: LogConfig = field(default_factory=LogConfig)
    frontend: FrontendConfig = field(default_factory=FrontendConfig)
    # What /setup asks for before making the first administrator
    # (docs/decisions/0012); without it, setup is off.
    setup_token: str | None = field(default=None, repr=False)
    # Password resets and notices by mail; None sends none.
    mail: MailConfig | None = None

    def __post_init__(self) -> None:
        # Every Settings passes through here, however it was built.
        object.__setattr__(self, "database_url", _database_url(self.database_url))
        if self.setup_token is not None and len(self.setup_token) < SETUP_TOKEN_LENGTH:
            raise SettingsError(
                f"SETUP_TOKEN must be at least {SETUP_TOKEN_LENGTH} characters"
            )
        if not self.development:
            _refuse_development_only(self)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> Self:
        return cls(
            database_url=environ.get("DATABASE_URL", ""),
            development=_environment(environ.get("APP_ENV") or "") == "development",
            logging=LogConfig.from_environ(environ),
            frontend=FrontendConfig.from_environ(environ),
            setup_token=environ.get("SETUP_TOKEN", "").strip() or None,
            mail=MailConfig.from_environ(environ),
        )


def _refuse_development_only(settings: Settings) -> None:
    """The settings that work only with APP_ENV=development, all checked here."""
    if settings.logging.unmask_secrets:
        raise SettingsError("UNMASK_SECRETS works only with APP_ENV=development")
    # A page loading scripts from a developer's laptop is no production page.
    if settings.frontend.dev_server:
        raise SettingsError("VITE_DEV_SERVER works only with APP_ENV=development")
    if mail := settings.mail:
        # Mail in the clear, with a password in it, is for Mailpit on a laptop.
        if mail.security == "none":
            raise SettingsError(
                "MAIL_SECURITY=none works only with APP_ENV=development"
            )
        # Reset links over plain http could be read on the way.
        if not mail.site_url.startswith("https://"):
            raise SettingsError("SITE_URL must be https outside APP_ENV=development")


def _dev_server(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise SettingsError(
            f"VITE_DEV_SERVER must look like http://localhost:5173, not {value!r}"
        )
    try:
        port = url.port
    except ValueError:
        port = None
    # vite.config.ts requires a port too, and, as browsers do, does not count
    # a scheme's default one, so both always agree on the address.
    if not port or port == DEFAULT_PORTS[url.scheme]:
        raise SettingsError(
            f"VITE_DEV_SERVER needs a port, such as http://localhost:5173, not {value!r}"
        )
    # Without a trailing slash; netloc keeps an IPv6 address's brackets.
    return f"{url.scheme}://{url.netloc}"


def _mail_security(value: str) -> MailSecurity:
    for security in DEFAULT_MAIL_PORTS:
        if value.lower() == security:
            return security
    raise SettingsError(
        f"MAIL_SECURITY must be one of {', '.join(DEFAULT_MAIL_PORTS)}, not {value!r}"
    )


def _port(value: str) -> int:
    if not value.isdigit() or not 0 < int(value) < 65536:
        raise SettingsError(f"MAIL_PORT must be a port number, not {value!r}")
    return int(value)


def _site_url(value: str) -> str:
    """The site's address, as people reach it: http(s), a host, no query."""
    value = value.strip()
    url = urlsplit(value)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.query
        or url.fragment
    ):
        raise SettingsError(
            "SITE_URL is needed with MAIL_HOST, and looks like "
            f"https://inventory.example.org, not {value!r}"
        )
    return value.rstrip("/")


def _database_url(value: str) -> str:
    # An empty variable counts as unset, so a blank line in a .env file cannot
    # stand in for a real value.
    value = value.strip()
    if not value:
        raise SettingsError("DATABASE_URL is not set")
    try:
        url = make_url(value)
    except ArgumentError as error:
        raise SettingsError(f"DATABASE_URL is not a database URL: {error}") from None
    # postgres://, postgresql:// and either with +psycopg all mean psycopg 3.
    backend, _, driver = url.drivername.partition("+")
    if backend not in {"postgres", "postgresql"} or driver not in {"", "psycopg"}:
        raise SettingsError(f"DATABASE_URL must be PostgreSQL, not {url.drivername}")
    return url.set(drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    )


def _level(value: str, name: str) -> str:
    level = value.strip().upper()
    if level not in LEVELS:
        raise SettingsError(f"{name} must be one of {', '.join(LEVELS)}, not {value!r}")
    return level


def _levels(value: str) -> dict[str, str]:
    # "sqlalchemy.engine=INFO,uvicorn=DEBUG"
    levels = {}
    for pair in filter(None, (part.strip() for part in value.split(","))):
        logger, sep, level = (part.strip() for part in pair.partition("="))
        if not sep or not logger:
            raise SettingsError(
                f"LOG_LEVELS entries look like name=LEVEL, not {pair!r}"
            )
        levels[logger] = _level(level, f"LOG_LEVELS for {logger}")
    return levels


def _environment(value: str) -> str:
    value = value.strip().lower() or "production"
    if value not in ENVIRONMENTS:
        raise SettingsError(
            f"APP_ENV must be one of {', '.join(ENVIRONMENTS)}, not {value!r}"
        )
    return value


def _format(value: str) -> Literal["console", "json"]:
    value = value.strip().lower()
    if value == "console" or value == "json":
        return value
    raise SettingsError(f"LOG_FORMAT must be console or json, not {value!r}")


def _flag(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be true or false, not {environ[name]!r}")
