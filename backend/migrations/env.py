"""Runs migrations on the connection a caller passes in config.attributes (the
tests do), or else on DATABASE_URL."""

from alembic import context
from sqlalchemy import Connection

from inventory_tng.db import Base, make_engine
from inventory_tng.logs import configure_logging
from inventory_tng.settings import Settings

if context.is_offline_mode():
    raise SystemExit("Offline (--sql) migrations are not supported.")


def run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if connection := context.config.attributes.get("connection"):
    run(connection)
else:
    # Run from the command line, so this is where the process starts
    # (docs/decisions/0007). The tests pass a connection and keep their own.
    settings = Settings.from_environ()
    configure_logging(settings.logging)
    engine = make_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            run(connection)
    finally:
        engine.dispose()
