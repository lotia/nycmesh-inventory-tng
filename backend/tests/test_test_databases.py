"""Each run has a database of its own, which nothing else reaches, and which
is dropped when the run ends or, if it never could, by a later run
(databases.py, and conftest.py's fixtures)."""

import os
import time

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import OperationalError

from inventory_tng.db import make_engine
from inventory_tng.dev_database import as_superuser

from databases import (
    NO_DATABASE,
    SERVER_URL,
    TEST_CONNECTION_LIMIT,
    database_name,
    drop_left_behind,
    new_database,
    refused_connection,
)


def test_the_shells_database_urls_are_out_of_reach() -> None:
    assert os.environ["DATABASE_URL"] == NO_DATABASE
    assert os.environ["MIGRATION_DATABASE_URL"] == NO_DATABASE


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


def test_a_database_past_its_connection_limit_says_which_limit(
    database_url: str,
) -> None:
    """Each test database takes TEST_CONNECTION_LIMIT connections; past it, the
    test fails saying so (conftest.py's pytest_runtest_makereport)."""
    del database_url  # Only for the server, as the run's database needs it.
    with new_database() as url:
        name = make_url(url).database
        with as_superuser(url) as connection:
            limit = connection.scalar(
                text("SELECT datconnlimit FROM pg_database WHERE datname = :n"),
                {"n": name},
            )
            connection.execute(text(f'ALTER DATABASE "{name}" CONNECTION LIMIT 1'))
        assert limit == TEST_CONNECTION_LIMIT
        engine = make_engine(url)
        try:
            with engine.connect(), pytest.raises(OperationalError) as refused:
                engine.connect()
        finally:
            engine.dispose()
    said = refused_connection(str(refused.value))
    assert said is not None
    assert f"CONNECTION LIMIT, {TEST_CONNECTION_LIMIT}" in said
    for full in (
        "FATAL:  sorry, too many clients already",
        "FATAL:  remaining connection slots are reserved for roles with SUPERUSER",
    ):
        assert "max_connections" in str(refused_connection(full))
    assert refused_connection("connection refused") is None
