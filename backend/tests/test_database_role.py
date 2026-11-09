"""The app runs only as a login that cannot change the ledger or history,
cannot empty a settings table, and owns no table (database_role.py;
DEPLOYMENT.md, Database roles)."""

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
    KEPT,
    NEEDED,
    RefusedLogin,
    check,
    named,
    refusals,
)
from inventory_tng.db import Base, make_engine
from inventory_tng.settings import Settings

from databases import OWNER, as_role, superuser_url
from helpers.rows import refused


def test_the_apps_login_is_allowed(session: Session) -> None:
    assert refusals(session.connection()) == {}


def test_the_owners_login_is_refused(owner_session: Session) -> None:
    found = refusals(owner_session.connection())
    assert found["may"] == named(FORBIDDEN)
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
    assert refusals(superuser)["may"] == named(FORBIDDEN)


def test_a_login_that_cannot_record_is_refused(superuser: Connection) -> None:
    """Such as one DATABASE_URL names by mistake, which no migration gave
    anything: refused, rather than failing every save."""
    stranger = f"stranger_{uuid.uuid4().hex[:8]}"
    superuser.execute(text(f"CREATE ROLE {stranger}"))
    superuser.execute(text(f"SET LOCAL ROLE {stranger}"))
    assert refusals(superuser) == {"lacks": named(NEEDED)}


def test_a_login_that_may_update_one_column_of_the_ledger_is_refused(
    superuser: Connection,
) -> None:
    """A grant on a column, which has_table_privilege alone does not see."""
    clerk = f"clerk_{uuid.uuid4().hex[:8]}"
    superuser.execute(text(f"CREATE ROLE {clerk}"))
    superuser.execute(
        text(f"GRANT SELECT, INSERT ON {', '.join(APPEND_ONLY)} TO {clerk}")
    )
    superuser.execute(text(f"GRANT UPDATE (quantity) ON movement TO {clerk}"))
    superuser.execute(text(f"SET LOCAL ROLE {clerk}"))
    assert refusals(superuser) == {"may": ["UPDATE movement"]}


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
    assert error["may"] == named(FORBIDDEN)
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
    assert {*APPEND_ONLY, *KEPT, "item"} <= set(owner_session.scalars(TRUNCATABLE))


@pytest.mark.parametrize("table", KEPT)
def test_the_app_may_not_delete_a_settings_row(session: Session, table: str) -> None:
    """Their one row is changed, never deleted (migrations 0005 to 0007)."""
    with refused(session):
        session.execute(text(f"DELETE FROM {table}"))


# Columns the app may not change once a row is made (migration 0002), so the
# rules that rely on them need no check: a label's code and what it names, a
# package's item, a place's kind and volunteer. Every other column of every
# other table is the app's to change, but in the tables it only adds to.
FIXED = {
    "label": {"id", "created_at", "code", "item_id", "package_id", "location_id"},
    "item_package": {"id", "created_at", "item_id"},
    "location": {"id", "created_at", "kind", "volunteer_id"},
}


def test_the_app_may_change_every_column_but_the_fixed(session: Session) -> None:
    """So a new column on a table with fixed ones fails here until the
    migration grants it, or it is listed above."""
    granted: dict[str, set[str]] = {}
    for table, column in session.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.column_privileges"
            " WHERE grantee = current_user AND privilege_type = 'UPDATE'"
            " AND table_schema = 'public'"
        )
    ):
        granted.setdefault(table, set()).add(column)
    expected = {
        name: set(table.c.keys()) - FIXED.get(name, set())
        for name, table in Base.metadata.tables.items()
        if name not in APPEND_ONLY
    }
    assert {name: granted.get(name, set()) for name in expected} == expected
    assert not set(granted) & set(APPEND_ONLY)


@pytest.mark.parametrize(
    ("table", "column"),
    # Not id: PostgreSQL refuses that first, as an identity.
    [
        (t, column)
        for t, columns in FIXED.items()
        for column in sorted(columns - {"id"})
    ],
)
def test_a_fixed_column_cannot_be_changed(
    session: Session, table: str, column: str
) -> None:
    with refused(session):
        session.execute(text(f"UPDATE {table} SET {column} = {column}"))
