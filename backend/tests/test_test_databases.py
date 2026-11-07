"""Each run has a database of its own, which nothing else reaches, and which
is dropped when the run ends or, if it never could, by a later run
(databases.py, and conftest.py's fixtures)."""

import os
import time

from sqlalchemy import make_url, text

from inventory_tng.db import make_engine

from databases import NO_DATABASE, SERVER_URL, database_name, drop_left_behind


def test_the_shells_database_url_is_out_of_reach() -> None:
    assert os.environ["DATABASE_URL"] == NO_DATABASE


def test_the_run_uses_a_database_of_its_own(database_url: str) -> None:
    ours = make_url(database_url).database
    assert ours != make_url(SERVER_URL).database
    assert ours is not None
    assert ours.startswith("test_")


def test_databases_left_behind_are_dropped_and_no_others(database_url: str) -> None:
    """Only about its own databases: other runs on the server sweep too, so
    one may drop `old` first, and this sweep may drop theirs."""
    server = make_engine(SERVER_URL).execution_options(isolation_level="AUTOCOMMIT")
    now = int(time.time())
    old, recent = database_name(now - 7200), database_name(now - 60)
    ours = make_url(database_url).database
    try:
        with server.connect() as connection:
            for name in (old, recent):
                connection.execute(text(f'CREATE DATABASE "{name}"'))
        # This run's own is new, so it stays too.
        drop_left_behind(now)
        with server.connect() as connection:
            left = set(
                connection.scalars(text("SELECT datname FROM pg_database")).all()
            )
        assert old not in left
        assert recent in left
        assert ours in left
    finally:
        with server.connect() as connection:
            for name in (old, recent):
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()
