"""History: every row the app adds, changes or deletes through the ORM, in a
table that has history (db.protection), is written to catalogue_history in the same
transaction, with who did it (db.set_actor) and the row before and after.

What to record is decided by pure functions (change, json_value); the flush
listeners below only read the rows and write what those return. A row
written any other way, by a statement such as update(Item), a migration or
SQL an operator types, is not recorded: tests/test_history.py fails on a
statement that writes to a table with history in the app.

Only the rows the session was given to add, change or delete are seen, not
any the flush changes by itself (a cascade, or a relationship() setting a
foreign key to NULL); models.py has no relationship(). models.py imports
this module, so any session that can write these tables has the listeners.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import IPv4Address, IPv4Interface, IPv6Address, IPv6Interface

import structlog
from sqlalchemy import Table, event, insert, select
from sqlalchemy.orm import Session, UOWTransaction

from inventory_tng.db import (
    Action,
    Base,
    current_actor,
    flagged,
    flush_writes,
    flushed_id,
)

log = structlog.get_logger(__name__)

HISTORY_TABLE = "catalogue_history"
# The ledger's tables, which the app only adds to (database_role.py).
LEDGER = ("ledger_transaction", "movement")
# Which tables have history, and the columns it leaves out ("secret": what
# would let someone sign in, or copy a second factor, docs/decisions/0012)
# or does not count as a change alone ("not_a_change": each moves with use),
# are declared on the models (db.protection). A secret changing is still
# history; a column not a change stays in the rows recorded.

type Row = Mapping[str, object]
type Image = dict[str, object]


def has_history(table: Table) -> bool:
    return bool(table.info["history"])


def json_value(value: object) -> object:
    """A column's value as PostgreSQL's to_jsonb() writes it: times in UTC
    with as many decimals as they need, bytes as hex after \\x, addresses as
    text. A number is written as JSON writes a float: the same value, but 5
    rather than numeric's 5.000; every numeric column here has at most 12
    digits, which a float holds exactly."""
    match value:
        case None | bool() | int() | str():
            return value
        case Decimal():
            return float(value)
        case datetime():
            written = value.astimezone(UTC).isoformat(timespec="microseconds")
            moment, offset = written[:-6], written[-6:]
            return moment.rstrip("0").rstrip(".") + offset
        case bytes():
            return "\\x" + value.hex()
        case IPv4Address() | IPv6Address() | IPv4Interface() | IPv6Interface():
            return str(value)
        case _:
            raise TypeError(f"history has no JSON for {type(value).__name__}")


def image(table: Table, row: Row | None) -> Image | None:
    """The row as history keeps it: as JSON, without its secret columns."""
    if row is None:
        return None
    secret = flagged(table, "secret")
    return {name: json_value(v) for name, v in row.items() if name not in secret}


@dataclass(frozen=True)
class Change:
    action: Action
    old_row: Image | None
    new_row: Image | None


def change(table: Table, old: Row | None, new: Row | None) -> Change | None:
    """What history records of one row, given it before (None: added) and
    after (None: deleted), not both None. None for an update that changes
    nothing, or only columns not a change. A secret column changing is a
    change, recorded without it."""
    if old is not None and new is not None:
        skipped = flagged(table, "not_a_change")
        if {k: v for k, v in old.items() if k not in skipped} == {
            k: v for k, v in new.items() if k not in skipped
        }:
            return None
    action: Action = "insert" if old is None else "delete" if new is None else "update"
    return Change(action, image(table, old), image(table, new))


# The flush (imperative shell). Each row it touches is read from the
# database before the flush and after it, rather than taken from the
# objects, which may not hold every column (one expired, or set by the
# database such as created_at and item.search_text), and may hold a value
# other than the one stored (5.0004 in a numeric(12,3)). What is read before
# is not locked: where two requests may change a row at once, the app locks
# it first, and the flush refuses a change to a row of a table that names a
# lock unless it is (db.protection).

_PENDING = "inventory.history"

type Key = tuple[Table, int]


@dataclass
class _Pending:
    # Rows whose ids are known before the flush, and the rows the flush
    # adds without one yet.
    keys: dict[Key, None]
    added: list[tuple[Table, Base]]
    before: dict[Key, Row]


def _read(session: Session, keys: Iterable[Key]) -> dict[Key, Row]:
    """The rows as the database holds them, one query per table."""
    by_table: dict[Table, list[int]] = {}
    for table, row_id in keys:
        by_table.setdefault(table, []).append(row_id)
    connection = session.connection()
    return {
        (table, row.id): dict(row._mapping)
        for table, ids in by_table.items()
        for row in connection.execute(select(table).where(table.c.id.in_(ids)))
    }


@event.listens_for(Session, "before_flush")
def _before_flush(
    session: Session, context: UOWTransaction, _instances: object
) -> None:
    keys: dict[Key, None] = {}
    added: list[tuple[Table, Base]] = []
    for write in flush_writes(session):
        table = write.table
        # Not history: a table without it, or a change only to columns not
        # a change, so a sign-in moving an authenticator's last step reads
        # nothing.
        if not has_history(table) or (
            write.action == "update"
            and not write.changed - flagged(table, "not_a_change")
        ):
            continue
        if write.row_id is None:
            added.append((table, write.row))
        else:
            keys[table, write.row_id] = None
    if keys or added:
        context.attributes[_PENDING] = _Pending(keys, added, _read(session, keys))


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, context: UOWTransaction) -> None:
    pending: _Pending | None = context.attributes.get(_PENDING)
    if pending is None:
        return
    keys = dict(pending.keys)
    for table, row in pending.added:
        row_id = flushed_id(row)
        assert row_id is not None  # The flush has given it one.
        keys[table, row_id] = None
    before, after = pending.before, _read(session, keys)
    actor = current_actor(session)
    rows = []
    for table, row_id in keys:
        old, new = before.get((table, row_id)), after.get((table, row_id))
        # Neither: a row deleted that was already gone.
        if old is None and new is None:
            continue
        if (made := change(table, old, new)) is not None:
            rows.append(
                {
                    "table_name": table.name,
                    "row_id": row_id,
                    "action": made.action,
                    "actor": actor,
                    "old_row": made.old_row,
                    "new_row": made.new_row,
                }
            )
    if rows:
        session.connection().execute(insert(Base.metadata.tables[HISTORY_TABLE]), rows)
        log.debug("history recorded", rows=len(rows), actor=actor)
