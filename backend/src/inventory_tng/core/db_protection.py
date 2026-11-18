"""Each table's protection, and what a flush writes."""

import functools
from typing import Literal, NamedTuple

from sqlalchemy import (
    Column,
    Table,
    inspect,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    UOWTransaction,
)

from inventory_tng.core.db_sessions import Base

# Each table's protection, declared once on its model (protection()) and
# read from Base.metadata by the lock guard (db_locking), by history.py and by
# tests/across/test_write_owners.py (docs/decisions/0016). This is the one
# place its rules are written down.
# - owner: the one module that makes its rows, or None: made only by its
#   migration, or by a function tests/across/test_write_owners.py allows.
#   That test reads the source for rows made, or any deleted, elsewhere.
# - lock: what a write to it holds, which the guard checks as the session
#   flushes. "row": a row changed or deleted was locked in this
#   transaction (locked, get_locked, settings_row, relock), or added in it.
#   "catalogue": that, and the catalogue lock for any write, but one to
#   columns marked row_lock_only alone. None: no lock, for what is only
#   added to or changes on every request.
# - history: whether history.py records its rows' changes.
# And on a column, mapped_column(info=...), a Flag: "secret", left out of
# history (what would let someone sign in, or copy a second factor,
# docs/decisions/0012); "not_a_change", which changing alone is no history
# (it moves with use, or with another row, as search_text with an item's
# aliases); and "row_lock_only" (above). Changing either alone moves no
# edit_version (db_locking._next_version).
#
# A test's own session is left alone as the test writes rows to set up what
# it tests (db_locking.UNGUARDED_INFO; tests/helpers/rows.py, setting_up),
# but not as the app's requests use it (tests/helpers/apps.py, as_the_app).
type Lock = Literal["catalogue", "row"]
type Flag = Literal["secret", "not_a_change", "row_lock_only"]
type Action = Literal["insert", "update", "delete"]


def protection(
    *, owner: str | None, lock: Lock | None, history: bool
) -> dict[str, dict[str, object]]:
    """A model's __table_args__ entry declaring its table's protection."""
    return {"info": {"owner": owner, "lock": lock, "history": history}}


@functools.cache
def flagged(table: Table, flag: Flag) -> frozenset[str]:
    """The names of the table's columns whose info sets `flag`: read once
    per table, as every flush asks for each row it writes."""
    return frozenset(c.name for c in table.columns if c.info.get(flag))


class Write(NamedTuple):
    """A row a flush is about to write: its table, its identity key and id
    if it has them yet, what is done to it, and for an update the columns
    set to something new, by name."""

    row: Base
    table: Table
    key: object
    row_id: int | None
    action: Action
    changed: frozenset[str]


def changed_columns(row: DeclarativeBase) -> frozenset[str]:
    """The columns of a row the session holds that were set to something
    new, not yet flushed, by their names in the table. Loads nothing."""
    state = inspect(row)
    return frozenset(
        column.name
        for prop in state.mapper.column_attrs
        if state.attrs[prop.key].history.has_changes()
        for column in prop.columns
        if isinstance(column, Column)
    )


def flushed_id(row: Base) -> int | None:
    """Its id, without loading anything: its identity, or the id it was
    given, or the flush set; None before then."""
    state = inspect(row)
    return state.identity[0] if state.identity else state.dict.get("id")


def table_of(row: Base) -> Table:
    table = inspect(row).mapper.local_table
    assert isinstance(table, Table)
    return table


_WRITES = "inventory.writes"


def flush_writes(session: Session, context: UOWTransaction) -> list[Write]:
    """What this flush writes: rows added, rows changed to something new,
    and rows deleted. Loads nothing. Found once per flush, for the lock
    guard and history.py, before db_locking._next_version changes any row's
    version."""
    if (found := context.attributes.get(_WRITES)) is not None:
        return found
    found = []
    for rows, action in (
        (session.new, "insert"),
        (session.dirty, "update"),
        (session.deleted, "delete"),
    ):
        for row in rows:
            changed = changed_columns(row) if action == "update" else frozenset()
            if action == "update" and not changed:
                continue
            key = inspect(row).key
            found.append(
                Write(row, table_of(row), key, flushed_id(row), action, changed)
            )
    context.attributes[_WRITES] = found
    return found
