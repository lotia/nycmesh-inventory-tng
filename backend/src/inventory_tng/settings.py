"""Settings, read once from the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Self

from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


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

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> Self:
        default = cls()
        return cls(
            level=_level(environ.get("LOG_LEVEL") or default.level, "LOG_LEVEL"),
            levels=_levels(environ.get("LOG_LEVELS") or ""),
            format=_format(environ.get("LOG_FORMAT") or default.format),
            client_address=_flag(environ, "LOG_CLIENT_ADDRESS", default.client_address),
        )


@dataclass(frozen=True)
class Settings:
    database_url: str
    logging: LogConfig = field(default_factory=LogConfig)

    def __post_init__(self) -> None:
        # Every Settings passes through here, however it was built.
        object.__setattr__(self, "database_url", _database_url(self.database_url))

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> Self:
        return cls(
            database_url=environ.get("DATABASE_URL", ""),
            logging=LogConfig.from_environ(environ),
        )


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
