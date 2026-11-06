"""Databases of their own for tests, on the server compose starts: made,
migrated and dropped. Each is named for when it was made, so one left
behind is dropped by a later run."""

import os
import re
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, make_url, text
from sqlalchemy.exc import OperationalError

from inventory_tng.db import make_engine
from inventory_tng.local_env import local_env_values
from inventory_tng.settings import Settings

log = structlog.get_logger(__name__)

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"

# The database compose starts: from the shell, else the repository's .env
# (CONTRIBUTING.md, Local settings), else compose's default port.
LOCAL = local_env_values()
PORT = os.environ.get("INVENTORY_DB_PORT") or LOCAL.get("INVENTORY_DB_PORT") or "5433"
SERVER_URL = Settings(
    database_url=os.environ.get("DATABASE_URL")
    or LOCAL.get("DATABASE_URL")
    or f"postgresql://inventory:inventory@localhost:{PORT}/inventory"
).database_url

TEST_DATABASE = re.compile(r"test_(\d+)_[0-9a-f]{8}")
LEFT_BEHIND = timedelta(hours=1)


def _server() -> Engine:
    return make_engine(SERVER_URL).execution_options(isolation_level="AUTOCOMMIT")


class Unreachable(Exception):
    """The database server does not answer."""


def drop_left_behind(now: float | None = None) -> list[str]:
    """Test databases made over an hour ago that nothing is connected to:
    never one still in use."""
    now = time.time() if now is None else now
    server = _server()
    with server.connect() as connection:
        idle = connection.scalars(
            text(
                "SELECT datname FROM pg_database WHERE datname LIKE 'test\\_%' "
                "AND datname NOT IN (SELECT datname FROM pg_stat_activity "
                "WHERE datname IS NOT NULL)"
            )
        ).all()
        dropped = []
        for name in idle:
            match = TEST_DATABASE.fullmatch(name)
            if match and now - int(match.group(1)) > LEFT_BEHIND.total_seconds():
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
                dropped.append(name)
    server.dispose()
    if dropped:
        log.info("dropped test databases left behind", databases=dropped)
    return dropped


def create_database(template: str | None = None) -> str:
    """A database of its own, and its URL: empty, with no schema, or a copy
    of the database `template`."""
    name = f"test_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    server = _server()
    try:
        copy = f' TEMPLATE "{template}"' if template else ""
        with server.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"{copy}'))
    except OperationalError as error:
        raise Unreachable(
            f"Cannot reach PostgreSQL at {make_url(SERVER_URL)}. Start it with "
            f"`mise run compose -- up -d --wait db`.\n{error}"
        ) from error
    finally:
        server.dispose()
    log.info("created test database", database=name)
    url = make_url(SERVER_URL).set(database=name)
    return url.render_as_string(hide_password=False)


def drop_database(url: str) -> None:
    name = make_url(url).database
    server = _server()
    with server.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    server.dispose()
    log.info("dropped test database", database=name)


@contextmanager
def new_database(template: str | None = None) -> Generator[str]:
    """create_database's, dropped afterwards."""
    url = create_database(template)
    try:
        yield url
    finally:
        drop_database(url)


def migrate(engine: Engine) -> None:
    """Every migration, up to the newest."""
    with engine.begin() as connection:
        command.upgrade(alembic_config_for(connection), "head")


def alembic_config_for(connection: Connection) -> Config:
    config = Config(toml_file=PYPROJECT)
    config.attributes["connection"] = connection
    return config
