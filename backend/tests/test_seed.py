"""The seed command (devtools/seed.py). Each test that seeds has a
database of its own (own_database), as the seed refuses a database that
holds rows already."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from devtools import seed
from devtools.__main__ import main as run_devtools
from inventory_tng.admins import accounts
from inventory_tng.models.admins import Admin, AdminPassword
from inventory_tng.models.catalogue import Item
from inventory_tng.models.places import Location
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.stock.ledger import expected

from databases import NO_DATABASE
from helpers.rows import setting_up

SIGN_IN = "Sign in at /admin as dev, password "
PASSWORD = "a password for the tests"


@pytest.fixture
def seeding(
    own_database: str,
    own_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Session]:
    """The seed command's settings, on a database of its own, and a session
    to see what it made."""
    monkeypatch.setenv("DATABASE_URL", own_database)
    monkeypatch.setenv("APP_ENV", "development")
    with Session(own_engine) as session:
        yield setting_up(session)


def the_admin(session: Session) -> Admin | None:
    return session.scalars(select(Admin)).one_or_none()


def test_it_seeds_once_and_again_changes_nothing(
    seeding: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_devtools(["database", "seed", "--password", PASSWORD]) == 0
    assert SIGN_IN + PASSWORD in capsys.readouterr().out
    # A database seeded and then worked in: a later run changes nothing.
    accounts.sign_in_settings(seeding).totp_policy = "required"
    seeding.add(Volunteer(name="Someone new"))
    seeding.commit()
    assert run_devtools(["database", "seed"]) == 0
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
    assert run_devtools(["database", "seed"]) == 0
    out = capsys.readouterr().out
    assert SIGN_IN in out
    assert "Sign-in codes are optional here" in out
    assert accounts.sign_in_settings(seeding).totp_policy == "optional"


def test_a_password_given_again_replaces_the_old(seeding: Session) -> None:
    assert run_devtools(["database", "seed", "--password", "the first password"]) == 0
    assert run_devtools(["database", "seed", "--password", "the second password"]) == 0
    admin = the_admin(seeding)
    assert admin is not None
    assert accounts.check_password(seeding, admin.id, "the second password")


def test_an_administrator_without_a_password_gets_one(
    seeding: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_devtools(["database", "seed"]) == 0
    capsys.readouterr()
    seeding.execute(delete(AdminPassword))
    seeding.commit()
    assert run_devtools(["database", "seed"]) == 0
    assert SIGN_IN in capsys.readouterr().out


def test_it_refuses_a_database_someone_works_in(
    seeding: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    seeding.add(Admin(username="ada"))
    seeding.commit()
    assert run_devtools(["database", "seed"]) == 1
    assert "has administrators already" in capsys.readouterr().err
    assert seeding.scalars(select(Admin.username)).all() == ["ada"]


def test_a_short_password_is_refused(
    seeding: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_devtools(["database", "seed", "--password", "short"]) == 1
    assert "characters" in capsys.readouterr().err
    assert the_admin(seeding) is None


# Refused before reaching a database: NO_DATABASE answers nothing.


def test_only_in_development(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", NO_DATABASE)
    monkeypatch.setenv("APP_ENV", "production")
    assert run_devtools(["database", "seed"]) == 1
    assert "Only with APP_ENV=development" in capsys.readouterr().err
