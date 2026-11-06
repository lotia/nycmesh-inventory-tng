"""The seed command (seed.py). Each test that seeds has a database of its own,
copied from one migrated once: the run's shared database may already hold
rows other tests committed, which the seed would refuse."""

import runpy
import sys
from collections.abc import Iterator

import pytest
from conftest import NO_DATABASE, migrate, new_database
from sqlalchemy import delete, func, make_url, select
from sqlalchemy.orm import Session

from inventory_tng import accounts, seed
from inventory_tng.db import make_engine
from inventory_tng.ledger import expected
from inventory_tng.models import Admin, AdminPassword, Item, Location, Volunteer

SIGN_IN = "Sign in at /admin as dev, password "
PASSWORD = "a password for the tests"


@pytest.fixture(scope="module")
def migrated() -> Iterator[str]:
    """A database with the schema and nothing else, to copy."""
    with new_database() as url:
        engine = make_engine(url)
        migrate(engine)
        engine.dispose()
        yield url


@pytest.fixture
def seeding(
    migrated: str, monkeypatch: pytest.MonkeyPatch, restore_logging: None
) -> Iterator[Session]:
    """The seed command's settings, on a copy of `migrated`, and a session
    to see what it made."""
    with new_database(template=make_url(migrated).database) as url:
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("APP_ENV", "development")
        engine = make_engine(url)
        with Session(engine) as session:
            yield session
        engine.dispose()


def the_admin(session: Session) -> Admin | None:
    return session.scalars(select(Admin)).one_or_none()


def test_it_seeds_once_and_again_changes_nothing(
    seeding: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert seed.main(["--password", PASSWORD]) == 0
    assert SIGN_IN + PASSWORD in capsys.readouterr().out
    # A database seeded and then worked in: a later run changes nothing.
    accounts.sign_in_settings(seeding).totp_policy = "required"
    seeding.add(Volunteer(name="Someone new"))
    seeding.commit()
    assert seed.main([]) == 0
    assert "Seeded already" in capsys.readouterr().out
    seeding.expire_all()
    admin = the_admin(seeding)
    assert admin is not None
    assert accounts.check_password(seeding, admin.id, PASSWORD)
    assert accounts.sign_in_settings(seeding).totp_policy == "required"
    assert seeding.scalar(select(func.count()).select_from(Item)) == len(seed.ITEMS)
    shelf = seeding.scalars(select(Location).where(Location.name == "Shelf A")).one()
    assert sorted(expected(seeding, shelf.id).values()) == sorted(
        int(quantity) for quantity in seed.STOCK.values()
    )


def test_seeding_makes_codes_optional_and_a_password(
    seeding: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert seed.main([]) == 0
    out = capsys.readouterr().out
    assert SIGN_IN in out
    assert "Sign-in codes are optional here" in out
    assert accounts.sign_in_settings(seeding).totp_policy == "optional"


def test_a_password_given_again_replaces_the_old(seeding: Session) -> None:
    assert seed.main(["--password", "the first password"]) == 0
    assert seed.main(["--password", "the second password"]) == 0
    admin = the_admin(seeding)
    assert admin is not None
    assert accounts.check_password(seeding, admin.id, "the second password")


def test_an_administrator_without_a_password_gets_one(
    seeding: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert seed.main([]) == 0
    capsys.readouterr()
    seeding.execute(delete(AdminPassword))
    seeding.commit()
    assert seed.main([]) == 0
    assert SIGN_IN in capsys.readouterr().out


def test_it_refuses_a_database_in_use(
    seeding: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    seeding.add(Volunteer(name="Someone real"))
    seeding.commit()
    assert seed.main([]) == 1
    assert "holds volunteer rows and was never seeded" in capsys.readouterr().err
    assert the_admin(seeding) is None


def test_it_runs_as_a_module(
    seeding: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["seed", "--password", PASSWORD])
    with (
        pytest.warns(RuntimeWarning, match="found in sys.modules"),
        pytest.raises(SystemExit) as done,
    ):
        runpy.run_module("inventory_tng.seed", run_name="__main__")
    assert done.value.code == 0
    assert SIGN_IN + PASSWORD in capsys.readouterr().out


def test_a_short_password_is_refused(
    seeding: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert seed.main(["--password", "short"]) == 1
    assert "characters" in capsys.readouterr().err
    assert the_admin(seeding) is None


# Refused before reaching a database: NO_DATABASE answers nothing.


def test_only_in_development(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
) -> None:
    monkeypatch.setenv("DATABASE_URL", NO_DATABASE)
    monkeypatch.setenv("APP_ENV", "production")
    assert seed.main([]) == 1
    assert "Only with APP_ENV=development" in capsys.readouterr().err
