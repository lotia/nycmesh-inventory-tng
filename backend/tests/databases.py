"""Databases of their own for tests, on the server compose starts: made,
migrated and dropped. Each is named for when it was made, so one left
behind is dropped by a later run. Each belongs to the owner role, which
migrates it; the tests log in as the app's role (DEPLOYMENT.md, Database
roles), made on the server, if missing, by the file compose runs."""

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
from inventory_tng.local_env import setting

log = structlog.get_logger(__name__)

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"
ROLES_SQL = Path(__file__).parents[1] / "migrations" / "initdb" / "1-roles.sql"

# Compose's superuser, which makes the roles and the databases, on the port
# from the shell, else the repository's .env (CONTRIBUTING.md, Local
# settings), else compose's default.
PORT = setting("INVENTORY_DB_PORT") or "5433"
SERVER = make_url(f"postgresql+psycopg://inventory:inventory@localhost:{PORT}")


def superuser_url(database: str) -> str:
    """Compose's superuser's URL for `database`."""
    return SERVER.set(database=database).render_as_string(hide_password=False)


SERVER_URL = superuser_url("inventory")
# The roles ROLES_SQL makes, each with its own name as its password.
OWNER = "inventory_owner"
APP = "inventory_app"

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


def database_name(at: int) -> str:
    """A test database's name: when it was made (`at`, in seconds), and
    random, so two made in the same second differ."""
    return f"test_{at}_{uuid.uuid4().hex[:8]}"


def as_role(url: str, role: str) -> str:
    """`url`, logging in as `role`, one of the roles ROLES_SQL makes."""
    return (
        make_url(url)
        .set(username=role, password=role)
        .render_as_string(hide_password=False)
    )


def create_database(template: str | None = None) -> str:
    """A database of its own, and the URL the app logs in to it with: empty,
    with no schema, or a copy of the database `template`. The roles are made
    first, if missing: compose's volume may predate them."""
    name = database_name(int(time.time()))
    server = _server()
    try:
        copy = f' TEMPLATE "{template}"' if template else ""
        with server.connect() as connection:
            connection.exec_driver_sql(ROLES_SQL.read_text())
            connection.execute(text(f'CREATE DATABASE "{name}" OWNER {OWNER}{copy}'))
    except OperationalError as error:
        raise Unreachable(
            f"Cannot reach PostgreSQL at {SERVER}. Start it with "
            f"`mise run compose -- up -d --wait db`.\n{error}"
        ) from error
    finally:
        server.dispose()
    log.info("created test database", database=name)
    # Reachable now: sweep up after earlier runs. This one is too new to go.
    drop_left_behind()
    return as_role(superuser_url(name), APP)


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


@contextmanager
def migrated_as_owner(url: str) -> Generator[Engine]:
    """An engine on the database at `url` as its owner, which has migrated
    it to the newest; disposed afterwards."""
    engine = make_engine(as_role(url, OWNER))
    try:
        with engine.begin() as connection:
            command.upgrade(alembic_config_for(connection), "head")
        yield engine
    finally:
        engine.dispose()


@contextmanager
def migrated_engine(url: str) -> Generator[Engine]:
    """An engine on the database at `url`, as the app, once migrated;
    disposed afterwards."""
    with migrated_as_owner(url):
        pass
    engine = make_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


def alembic_config_for(connection: Connection) -> Config:
    config = Config(toml_file=PYPROJECT)
    config.attributes["connection"] = connection
    return config


# What DATABASE_URL and MIGRATION_DATABASE_URL are during the tests: nothing
# listens on port 1.
NO_DATABASE = "postgresql://nobody@127.0.0.1:1/use_the_database_url_fixture"
