"""Catalogue rows: adding, finding, their history and the rules they break."""

import dataclasses
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import exc, select
from sqlalchemy.orm import Session

from inventory_tng import items
from inventory_tng.db import (
    UNGUARDED_INFO,
    Base,
    Flag,
    RuleBroken,
    describe_error,
    flagged,
)
from inventory_tng.models import CatalogueHistory, Item


@contextmanager
def violates(session: Session, constraint: str) -> Generator[None]:
    """The block's writes fail on `constraint`, and are rolled back to a
    savepoint, so the test can go on."""
    # The block is the caller's, so it cannot be one statement.
    with pytest.raises(exc.IntegrityError) as raised, session.begin_nested():  # noqa: PT012
        yield
        session.flush()
    assert describe_error(raised.value).get("constraint") == constraint


@contextmanager
def breaks(session: Session, rule: str) -> Generator[None]:
    """The block's domain function refuses the change by `rule`
    (db.RuleBroken), and what the block did is rolled back to a savepoint,
    so the test can go on."""
    with pytest.raises(RuleBroken) as raised, session.begin_nested():
        yield
    assert raised.value.rule == rule


# PostgreSQL's insufficient_privilege.
PERMISSION_DENIED = "42501"


@contextmanager
def refused(session: Session) -> Generator[None]:
    """The block's statement is refused for want of a privilege, and rolled
    back to a savepoint, so the test can go on."""
    with pytest.raises(exc.ProgrammingError) as raised, session.begin_nested():
        yield
    assert describe_error(raised.value).get("sqlstate") == PERMISSION_DENIED


def add[T: Base](session: Session, row: T) -> T:
    session.add(row)
    session.flush()
    return row


def named[T: Base](session: Session, model: type[T], name: str) -> T:
    """The one row of `model` with this name."""
    table: Any = model
    return session.scalars(select(model).where(table.name == name)).one()


def history(
    session: Session, table: str, row_id: int | None = None
) -> list[CatalogueHistory]:
    """A table's history, or one row's, oldest first."""
    query = select(CatalogueHistory).where(CatalogueHistory.table_name == table)
    if row_id is not None:
        query = query.where(CatalogueHistory.row_id == row_id)
    return list(session.scalars(query.order_by(CatalogueHistory.id)))


def tag() -> str:
    """For unique names: rows committed by one test never clash with
    another's."""
    return uuid.uuid4().hex[:8]


def setting_up(session: Session) -> Session:
    """A test's own session, which writes rows directly to set up what it
    tests: the app refuses a write without the lock its table names
    (db.protection), but not on this session (db.UNGUARDED_INFO), except
    as the app's requests use it (helpers.apps.as_the_app)."""
    session.info[UNGUARDED_INFO] = "a test's own rows"
    return session


def flagged_columns(flag: Flag) -> dict[str, set[str]]:
    """The columns whose info sets `flag`, by table, as the models declare
    them: those with any."""
    return {
        table.name: set(names)
        for table in Base.metadata.tables.values()
        if (names := flagged(table, flag))
    }


def item_fields(item: Item, **changes: object) -> items.Fields:
    """An item's fields as they are, but for `changes`: for items.save."""
    names = (field.name for field in dataclasses.fields(items.Fields))
    now = items.Fields(**{name: getattr(item, name) for name in names})
    return dataclasses.replace(now, **changes)


def shown(row: Base) -> str:
    """The version of a row its edit form shows and sends back
    (models.Versioned): the row as it is now."""
    return str(getattr(row, "edit_version"))  # noqa: B009
