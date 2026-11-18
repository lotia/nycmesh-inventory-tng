"""What the models' modules share: column types, the columns and
constraints most tables have, and edit versions (Versioned)."""

from datetime import datetime
from typing import Annotated, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    func,
    text,
)
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

# Imported for its flush listeners: whatever can write these tables records
# their history.
from inventory_tng.core import history  # noqa: F401

QUANTITY = Numeric(12, 3)
# The largest PostgreSQL integer: what an id or other integer column holds.
INTEGER_MAX = 2**31 - 1
MONEY = Numeric(10, 2)

IntId = Annotated[int, mapped_column(Identity(always=True), primary_key=True)]
CreatedAt = Annotated[
    datetime, mapped_column(DateTime(timezone=True), server_default=func.now())
]
RetiredAt = Annotated[datetime | None, mapped_column(DateTime(timezone=True))]


def whole(typed: str, low: int, high: int) -> int | None:
    """A whole number typed, from low to high. Plain digits only: isdigit()
    alone lets through "²", which int() refuses, and a number too long to
    read."""
    typed = typed.strip()
    if not (typed.isascii() and typed.isdigit() and len(typed) <= len(str(high))):
        return None
    number = int(typed)
    return number if low <= number <= high else None


def reference(target: str, *, index: bool = True) -> MappedColumn[Any]:
    """A foreign key, indexed: RESTRICT and the rules look rows up by it.
    Not indexed alone (index=False) only where an index of the table's leads
    with it. Nullable or not as the column's Mapped type says."""
    return mapped_column(ForeignKey(target, ondelete="RESTRICT"), index=index)


def one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def between(column: str, bounds: tuple[int, int]) -> CheckConstraint:
    low, high = bounds
    return CheckConstraint(f"{column} BETWEEN {low} AND {high}", name=column)


def unique_name(
    table: str, *scope: str, where: str | None = None, name: str = "name"
) -> Index:
    """Names unique, ignoring case (within `scope`), among the rows `where`
    says, or all of them."""
    return Index(
        f"uq_{table}_{name}",
        *(text(column) for column in scope),
        text("lower(name)"),
        unique=True,
        postgresql_where=None if where is None else text(where),
    )


def unique_current_name(table: str, *scope: str) -> Index:
    """Names unique, ignoring case, among current rows (within `scope`)."""
    return unique_name(table, *scope, where="retired_at IS NULL")


class Versioned:
    """A row an edit form shows. Each update the app makes adds one to its
    edit_version (db._next_version); the form sends back the one it showed,
    and a save of a row changed since is refused (db.unchanged), rather than
    undoing that change (inventory-tng-8i4.123). Not SQLAlchemy's
    version_id_col: that refuses a locking read of a row changed since the
    session loaded it, which every domain function makes (db.relock), and
    checks the version loaded in this request, not the one a form showed in
    an earlier one."""

    edit_version: Mapped[int] = mapped_column(
        server_default=text("1"), info={"not_a_change": True}
    )
