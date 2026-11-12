"""This checkout's development database, on compose's one PostgreSQL server,
which every checkout shares (CONTRIBUTING.md, Local settings): made if
missing, the owner's and held to its connection limit, or dropped, named to
confirm. Migrating it is alembic's, after (mise run dev-database,
reset-dev-database). Only with APP_ENV=development.

    uv run python -m inventory_tng.dev_database make
    uv run python -m inventory_tng.dev_database drop NAME
"""

import argparse
import contextlib
import sys
from collections.abc import Generator, Sequence

import structlog
from sqlalchemy import URL, Connection, make_url, text
from sqlalchemy.exc import ProgrammingError

from inventory_tng.command import set_up
from inventory_tng.db import make_engine
from inventory_tng.local_env import CLONE
from inventory_tng.settings import migration_settings

log = structlog.get_logger(__name__)

# Compose's superuser (compose.yaml), which alone makes and drops databases:
# the app and migrations never log in as it.
SUPERUSER = "inventory"
OWNER = "inventory_owner"
# Connections a development database takes at most, as the roles log in:
# an app's pools (db.py, 20) and a migration or seed beside it, so one
# checkout cannot starve the others (CONTRIBUTING.md, Connections).
CONNECTION_LIMIT = 30
# The roles, made if missing, as compose makes them on a new volume.
ROLES_SQL = CLONE / "backend" / "migrations" / "initdb" / "1-roles.sql"


def server_url(url: str, database: str = "postgres") -> str:
    """The superuser's URL for `database`, on the server `url` names."""
    return (
        make_url(url)
        .set(
            drivername="postgresql+psycopg",
            username=SUPERUSER,
            password=SUPERUSER,
            database=database,
        )
        .render_as_string(hide_password=False)
    )


def checkout_database(app_url: str, migration_url: str) -> URL:
    """The database both URLs name, which must be one, on one server."""
    app, migration = make_url(app_url), make_url(migration_url)
    if not app.database or (app.host, app.port, app.database) != (
        migration.host,
        migration.port,
        migration.database,
    ):
        raise SystemExit(
            "DATABASE_URL and MIGRATION_DATABASE_URL must name one database"
        )
    return app


@contextlib.contextmanager
def as_superuser(url: str) -> Generator[Connection]:
    """A connection as the superuser to the server `url` names, outside
    any transaction."""
    server = make_engine(server_url(url)).execution_options(
        isolation_level="AUTOCOMMIT"
    )
    try:
        with server.connect() as connection:
            yield connection
    finally:
        server.dispose()


def create(
    connection: Connection, name: str, *, limit: int, template: str | None = None
) -> None:
    """Make the roles if missing, then the database `name`, the owner's, as
    compose's first one is, held to `limit` connections: empty, or a copy of
    `template`. The tests make theirs with it too."""
    connection.exec_driver_sql(ROLES_SQL.read_text())
    quote = connection.dialect.identifier_preparer.quote
    copy = f" TEMPLATE {quote(template)}" if template else ""
    connection.execute(
        text(
            f"CREATE DATABASE {quote(name)} OWNER {OWNER}{copy}"
            f" CONNECTION LIMIT {limit}"
        )
    )


def make(url: str) -> bool:
    """Make the database `url` names if missing; one there already, made by
    hand or before the roles, is made the owner's and held to its limit
    too: whether it was made."""
    name = make_url(url).database or ""
    with as_superuser(url) as connection:
        try:
            create(connection, name, limit=CONNECTION_LIMIT)
        except ProgrammingError as error:
            # Made already, or by another task at once.
            if getattr(error.orig, "sqlstate", None) != "42P04":
                raise
        else:
            return True
        quoted = connection.dialect.identifier_preparer.quote(name)
        connection.execute(text(f"ALTER DATABASE {quoted} OWNER TO {OWNER}"))
        connection.execute(
            text(f"ALTER DATABASE {quoted} CONNECTION LIMIT {CONNECTION_LIMIT}")
        )
    return False


def drop(url: str) -> None:
    """Drop the database `url` names, ending its connections."""
    with as_superuser(url) as connection:
        quoted = connection.dialect.identifier_preparer.quote(
            make_url(url).database or ""
        )
        connection.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m inventory_tng.dev_database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("make", help="make this checkout's database if missing")
    dropping = commands.add_parser("drop", help="drop this checkout's database")
    dropping.add_argument("name", help="this checkout's database's name, to confirm")
    args = parser.parse_args(argv)
    settings = set_up(development_only=True)
    if settings is None:
        return 1
    url = checkout_database(settings.database_url, migration_settings().database_url)
    name = url.database
    if args.command == "drop":
        if args.name != name:
            print(
                f"This checkout's database is {name}, not {args.name}: nothing "
                "was dropped.",
                file=sys.stderr,
            )
            return 1
        drop(settings.database_url)
        log.info("dropped development database", database=name)
    elif make(settings.database_url):
        log.info("made development database", database=name)
    print(f"This checkout's database: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
