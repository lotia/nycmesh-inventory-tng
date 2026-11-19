"""This checkout's development database, on its compose stack's PostgreSQL
server (CONTRIBUTING.md, Local settings): made if missing, the owner's and
held to its connection limit, seeded (seed.py), or dropped, named to
confirm. Migrating it is alembic's, between making and seeding (mise run
dev-database, seed, reset-dev-database). Only with APP_ENV=development.

    uv run python -m devtools database make
    uv run python -m devtools database seed [--password PASSWORD]
    uv run python -m devtools database drop [NAME]
"""

import argparse
import contextlib
import sys
from collections.abc import Generator, Sequence

import structlog
from sqlalchemy import URL, Connection, make_url, text
from sqlalchemy.exc import ProgrammingError

from inventory_tng.core.db_sessions import make_engine
from inventory_tng.core.local_env import CLONE
from inventory_tng.core.operator_commands import run_command, set_up
from inventory_tng.core.settings import migration_settings

log = structlog.get_logger(__name__)

# Compose's superuser (compose.yaml), which alone makes and drops databases:
# the app and migrations never log in as it.
SUPERUSER = "inventory"
OWNER = "inventory_owner"
# Connections a development database takes at most, as the roles log in: an
# app's pools (core/db_sessions.py, 20) and a migration or seed beside it, so
# it cannot starve the tests beside it (CONTRIBUTING.md, Connections).
CONNECTION_LIMIT = 30
# The roles, made if missing (roles.sql says who else runs it).
ROLES_SQL = CLONE / "backend" / "migrations" / "roles.sql"


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
    a CloudNativePG cluster's is, held to `limit` connections: empty, or a
    copy of `template`. The tests make theirs with it too."""
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


def main(argv: Sequence[str], prog: str) -> int:
    parser = argparse.ArgumentParser(
        prog=prog, description="This checkout's development database."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("make", help="make this checkout's database if missing")
    seeding = commands.add_parser(
        "seed", help="give an empty database an administrator, a catalogue and stock"
    )
    seeding.add_argument("--password", help="the administrator's; else one is made")
    dropping = commands.add_parser("drop", help="drop this checkout's database")
    # Optional, so that without it the refusal can say what the name is.
    dropping.add_argument(
        "name", nargs="?", help="this checkout's database's name, to confirm"
    )
    args = parser.parse_args(argv)
    if args.command == "seed":
        # Only here: the seed imports most of the app, which make and drop
        # do not need.
        from devtools import seed

        return run_command(seed.run, args, development_only=True)
    settings = set_up(development_only=True)
    if settings is None:
        return 1
    url = checkout_database(settings.database_url, migration_settings().database_url)
    name = url.database
    if args.command == "drop":
        if args.name != name:
            given = "" if args.name is None else f", not {args.name}"
            print(
                f"This checkout's database is {name}{given}: nothing was "
                "dropped. To drop it and make it again, run: mise run "
                f"reset-dev-database -- {name}",
                file=sys.stderr,
            )
            log.warning("development database drop refused", database=name)
            return 1
        drop(settings.database_url)
        log.info("dropped development database", database=name)
    elif make(settings.database_url):
        log.info("made development database", database=name)
    print(f"This checkout's database: {name}")
    return 0
