"""Each checkout's development database (dev_database.py): made, the
owner's and held to its connection limit, and dropped only when named.
Only on databases of their own, named as the tests' are, so a run killed
before dropping one leaves it to a later run's sweep."""

import runpy
import sys
import time
from collections.abc import Iterator

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import ProgrammingError

from inventory_tng import dev_database
from inventory_tng.dev_database import CONNECTION_LIMIT, OWNER

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
    dev_database.drop(made)


def facts(url: str) -> tuple[int, str, int] | None:
    """Which database it is, its owner and its connection limit, if it
    exists."""
    with dev_database.as_superuser(url) as connection:
        row = connection.execute(
            text(
                "SELECT oid, pg_get_userbyid(datdba), datconnlimit"
                " FROM pg_database WHERE datname = :n"
            ),
            {"n": make_url(url).database},
        ).one_or_none()
    return None if row is None else tuple(row)


def test_made_once_the_owners_and_held_to_its_limit(url: str) -> None:
    assert dev_database.make(url)
    made = facts(url)
    assert made is not None
    assert made[1:] == (OWNER, CONNECTION_LIMIT)
    # Made already, as by hand or before the roles: kept, and put right.
    name = make_url(url).database or ""
    with dev_database.as_superuser(url) as connection:
        connection.execute(text(f'ALTER DATABASE "{name}" OWNER TO inventory'))
        connection.execute(text(f'ALTER DATABASE "{name}" CONNECTION LIMIT -1'))
    assert not dev_database.make(url)
    assert facts(url) == made
    # Any other refusal is not swallowed.
    with pytest.raises(ProgrammingError):
        dev_database.make(url.rsplit("/", 1)[0] + "/")


@pytest.mark.usefixtures("restore_logging")
def test_dropped_only_when_named_to_confirm(
    url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = make_url(url).database or ""
    assert dev_database.main(["make"]) == 0
    assert f"This checkout's database: {name}" in capsys.readouterr().out
    made = facts(url)
    assert dev_database.main(["make"]) == 0
    assert facts(url) == made
    # As the task runs it.
    monkeypatch.setattr(sys, "argv", ["dev_database", "drop", "inventory"])
    with (
        pytest.warns(RuntimeWarning, match="found in sys.modules"),
        pytest.raises(SystemExit) as refused,
    ):
        runpy.run_module("inventory_tng.dev_database", run_name="__main__")
    assert refused.value.code == 1
    assert f"is {name}, not inventory: nothing was dropped" in capsys.readouterr().err
    assert facts(url) == made
    assert dev_database.main(["drop", name]) == 0
    assert facts(url) is None


@pytest.mark.usefixtures("restore_logging")
def test_only_in_development_and_on_one_database(
    url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    assert dev_database.main(["make"]) == 1
    assert "Only with APP_ENV=development." in capsys.readouterr().err
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", superuser_url("another"))
    with pytest.raises(SystemExit, match="must name one database"):
        dev_database.main(["make"])
    assert facts(url) is None
