"""The app runs only as a login that cannot change the ledger or history and
owns no table (database_role.py; DEPLOYMENT.md, Database roles)."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, make_url, text
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.app import create_app
from inventory_tng.database_role import (
    APPEND_ONLY,
    FORBIDDEN,
    NEEDED,
    RefusedLogin,
    check,
    refusals,
)
from inventory_tng.db import make_engine
from inventory_tng.settings import Settings

from databases import OWNER, as_role, superuser_url


def on_append_only(privileges: tuple[str, ...]) -> list[str]:
    """As refusals() names them: "UPDATE movement"."""
    return [f"{privilege} {table}" for table in APPEND_ONLY for privilege in privileges]


def test_the_apps_login_is_allowed(session: Session) -> None:
    assert refusals(session.connection()) == {}


def test_the_owners_login_is_refused(owner_session: Session) -> None:
    found = refusals(owner_session.connection())
    assert found["may"] == on_append_only(FORBIDDEN)
    assert {*APPEND_ONLY, "item"} <= set(found["owns"])
    assert "lacks" not in found


@pytest.fixture
def superuser(database_url: str) -> Iterator[Connection]:
    """The run's database as compose's superuser, in a transaction rolled
    back after: roles made in it go too."""
    engine = make_engine(superuser_url(make_url(database_url).database or ""))
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()
    engine.dispose()


def test_a_superuser_is_refused(superuser: Connection) -> None:
    assert refusals(superuser)["may"] == on_append_only(FORBIDDEN)


def test_a_login_that_cannot_record_is_refused(superuser: Connection) -> None:
    """Such as one DATABASE_URL names by mistake, which no migration gave
    anything: refused, rather than failing every save."""
    stranger = f"stranger_{uuid.uuid4().hex[:8]}"
    superuser.execute(text(f"CREATE ROLE {stranger}"))
    superuser.execute(text(f"SET LOCAL ROLE {stranger}"))
    assert refusals(superuser) == {"lacks": on_append_only(NEEDED)}


def test_the_app_starts_as_its_own_login(engine: Engine) -> None:
    with capture_logs() as logs:
        check(engine)
    assert {"event": "database login checked", "log_level": "debug"} in logs


def test_the_app_refuses_to_start_as_the_owner(database_url: str) -> None:
    app = create_app(Settings(database_url=as_role(database_url, OWNER)))
    with capture_logs() as logs, pytest.raises(RefusedLogin), TestClient(app):
        pass
    (error,) = [entry for entry in logs if entry["log_level"] == "error"]
    assert error["event"] == "database login refused"
    assert error["may"] == on_append_only(FORBIDDEN)
    assert "item" in error["owns"]


def test_the_app_refuses_to_start_unchecked() -> None:
    """A database it cannot reach: it cannot tell, so it does not start."""
    engine = make_engine("postgresql+psycopg://u:p@127.0.0.1:1/x")
    with capture_logs() as logs, pytest.raises(RefusedLogin):
        check(engine)
    engine.dispose()
    (error,) = [entry for entry in logs if entry["log_level"] == "error"]
    assert error["event"] == "database login not checked"
    assert error["error"] == "OperationalError"


# Every table in the schema, by whether the session's login may TRUNCATE it.
TRUNCATABLE = text(
    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    " AND has_table_privilege("
    "quote_ident(schemaname) || '.' || quote_ident(tablename), 'TRUNCATE')"
)


def test_the_app_may_truncate_no_table(
    session: Session, owner_session: Session
) -> None:
    """Migration 0001 never grants it; the owner, who may, finds the tables."""
    assert session.scalars(TRUNCATABLE).all() == []
    assert {*APPEND_ONLY, "item"} <= set(owner_session.scalars(TRUNCATABLE))
