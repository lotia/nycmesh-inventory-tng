"""Settings, read once from the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError


class SettingsError(Exception):
    """A required setting is missing or unusable."""


@dataclass(frozen=True)
class Settings:
    database_url: str

    def __post_init__(self) -> None:
        # Every Settings passes through here, however it was built.
        object.__setattr__(self, "database_url", _database_url(self.database_url))

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] = os.environ) -> Self:
        return cls(database_url=environ.get("DATABASE_URL", ""))


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
