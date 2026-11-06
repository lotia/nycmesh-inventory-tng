"""Catalogue rows: adding, finding, their history and the rules they break."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import exc, select
from sqlalchemy.orm import Session

from inventory_tng.db import Base, describe_error
from inventory_tng.models import CatalogueHistory


@contextmanager
def violates(session: Session, constraint: str) -> Generator[None]:
    """The block's writes fail on `constraint`, and are rolled back to a
    savepoint, so the test can go on."""
    # The block is the caller's, so it cannot be one statement.
    with pytest.raises(exc.IntegrityError) as raised, session.begin_nested():  # noqa: PT012
        yield
        session.flush()
    assert describe_error(raised.value).get("constraint") == constraint


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
