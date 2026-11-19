"""Each checkout's development database (devtools/database.py): made, the
owner's and held to its connection limit, and dropped only when named.
Only on databases of their own, named as the tests' are, so a run killed
before dropping one leaves it to a later run's sweep."""

import time
from collections.abc import Iterator

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from structlog.testing import capture_logs

from devtools import database
from devtools.__main__ import main as run_devtools
from devtools.database import CONNECTION_LIMIT, OWNER
from inventory_tng.core.db_sessions import (
    CANNOT_CONNECT_TO_MIGRATE,
    connect_to_migrate,
    make_engine,
)

from databases import APP, as_role, database_name, superuser_url


@pytest.fixture
def url(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A database URL, as the app logs in, for a database no checkout has,
    set as this checkout's, in development; dropped after."""
    del database_url  # Only for the server, which the run's database needs.
    made = as_role(superuser_url(database_name(int(time.time()))), APP)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", made)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", as_role(made, OWNER))
    yield made
    database.drop(made)


def facts(url: str) -> tuple[int, str, int] | None:
    """Which database it is, its owner and its connection limit, if it
    exists."""
    with database.as_superuser(url) as connection:
        row = connection.execute(
            text(
                "SELECT oid, pg_get_userbyid(datdba), datconnlimit"
                " FROM pg_database WHERE datname = :n"
            ),
            {"n": make_url(url).database},
        ).one_or_none()
    return None if row is None else tuple(row)


def test_made_once_the_owners_and_held_to_its_limit(url: str) -> None:
    assert database.make(url)
    made = facts(url)
    assert made is not None
    assert made[1:] == (OWNER, CONNECTION_LIMIT)
    # Made already, as by hand or before the roles: kept, and put right.
    name = make_url(url).database or ""
    with database.as_superuser(url) as connection:
        connection.execute(text(f'ALTER DATABASE "{name}" OWNER TO inventory'))
        connection.execute(text(f'ALTER DATABASE "{name}" CONNECTION LIMIT -1'))
    assert not database.make(url)
    assert facts(url) == made
    # Any other refusal is not swallowed.
    with pytest.raises(ProgrammingError):
        database.make(url.rsplit("/", 1)[0] + "/")


def test_dropped_only_when_named_to_confirm_then_made_again(
    url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    name = make_url(url).database or ""
    assert run_devtools(["database", "make"]) == 0
    assert f"This checkout's database: {name}" in capsys.readouterr().out
    made = facts(url)
    assert run_devtools(["database", "make"]) == 0
    assert facts(url) == made
    again = f"To drop it and make it again, run: mise run reset-dev-database -- {name}"
    assert run_devtools(["database", "drop"]) == 1
    printed = capsys.readouterr()
    assert printed.err == (
        f"This checkout's database is {name}: nothing was dropped. {again}\n"
    )
    # Logged, as set_up configures logging: to stdout.
    assert "development database drop refused" in printed.out
    assert facts(url) == made
    assert run_devtools(["database", "drop", "inventory"]) == 1
    assert capsys.readouterr().err == (
        f"This checkout's database is {name}, not inventory: nothing was"
        f" dropped. {again}\n"
    )
    assert facts(url) == made
    assert run_devtools(["database", "drop", name]) == 0
    assert facts(url) is None
    # Made again, as reset-dev-database does after.
    assert run_devtools(["database", "make"]) == 0
    assert facts(url) not in (None, made)


def test_only_in_development_and_on_one_database(
    url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    assert run_devtools(["database", "make"]) == 1
    assert "Only with APP_ENV=development." in capsys.readouterr().err
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", superuser_url("another"))
    with pytest.raises(SystemExit, match="must name one database"):
        run_devtools(["database", "make"])
    assert facts(url) is None


def test_a_migration_that_cannot_connect_says_how_to_mend_it(url: str) -> None:
    """In development, the fix, and the error's class but never its text,
    which names the role and the database; elsewhere, the error itself.
    `url`'s database is not made."""
    engine = make_engine(as_role(url, OWNER))
    try:
        with capture_logs() as logs, pytest.raises(SystemExit) as exiting:
            connect_to_migrate(engine, development=True)
        assert exiting.value.code == CANNOT_CONNECT_TO_MIGRATE
        assert logs == [
            {
                "event": "cannot connect to migrate",
                "error": "OperationalError",
                "log_level": "error",
            }
        ]
        with pytest.raises(OperationalError):
            connect_to_migrate(engine, development=False)
    finally:
        engine.dispose()


def test_a_migration_that_can_connect_does(database_url: str) -> None:
    engine = make_engine(as_role(database_url, OWNER))
    try:
        with connect_to_migrate(engine, development=True) as connection:
            assert connection.scalar(text("SELECT current_user")) == OWNER
    finally:
        engine.dispose()
