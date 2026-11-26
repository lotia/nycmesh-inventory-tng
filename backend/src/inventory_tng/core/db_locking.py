"""Locking rows and the catalogue, edit versions, and the guard that a
write holds the lock its table's protection names."""

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

import structlog
from opentelemetry import metrics
from sqlalchemy import Select, event, func, inspect, select
from sqlalchemy.orm import (
    InstanceState,
    QueryContext,
    Session,
    SessionTransaction,
    UOWTransaction,
)

from inventory_tng.core.db_base import Base
from inventory_tng.core.db_errors import Gone, RuleBroken, enforce, refusal
from inventory_tng.core.db_protection import (
    Action,
    Lock,
    flagged,
    flush_writes,
    table_of,
)
from inventory_tng.core.db_savepoints import innermost, savepoint_undone, within
from inventory_tng.core.db_sessions import exists_where

if TYPE_CHECKING:
    # The type with_for_update takes for `of`, which locked passes on.
    from sqlalchemy.sql.selectable import _ForUpdateOfArgument

log = structlog.get_logger(__name__)


# Locking a row (inventory-tng-8i4.57): lock with locked or get_locked,
# never with_for_update itself (tests/across/test_locking.py fails on it
# elsewhere).
#
# SQLAlchemy keeps one object per row in a session (its identity map). A
# query, even SELECT ... FOR UPDATE, that returns a row the session has
# already loaded gives back that object as it was: it does not overwrite its
# attributes with what the locked row now holds, unless asked to with
# populate_existing. So code that read a row, then locks it, would decide on
# values from before the lock, which another request may have changed: a
# lost update, or a check passed on old data (tests/across/test_locking.py
# shows it with two sessions). SQLAlchemy's maintainer keeps populate_existing
# something to ask for, as it overwrites changes not yet written
# (https://groups.google.com/g/sqlalchemy/c/I2ftUVJcAuo); both wrappers run
# a query, which first writes those (autoflush, on in every session here),
# so none is lost. They only pass SQLAlchemy's public parameters on and add
# the option:
# https://docs.sqlalchemy.org/en/21/core/selectable.html#sqlalchemy.sql.expression.GenerativeSelect.with_for_update
# https://docs.sqlalchemy.org/en/21/orm/session_api.html#sqlalchemy.orm.Session.get
# https://docs.sqlalchemy.org/en/21/orm/queryguide/api.html#populate-existing


def locked[*Ts](
    query: Select[*Ts],
    *,
    key_share: bool = False,
    shared: bool = False,
    of: _ForUpdateOfArgument | None = None,
    skip_locked: bool = False,
) -> Select[*Ts]:
    """`query` with its rows locked and read again as locked: SELECT ... FOR
    UPDATE, or FOR NO KEY UPDATE with `key_share` (which does not hold up
    inserting a row that refers to these); with `shared` too, FOR KEY SHARE,
    which only FOR UPDATE waits for. Only `of`'s rows if given. With
    `skip_locked`, rows another transaction holds are left out, not waited
    for. Rows locked to change, not `shared`, are noted as held (the guard
    below): all the query loads, or `of`'s if it is a model; none for other
    `of`s."""
    options: dict[str, Any] = {"populate_existing": True}
    if not shared and (of is None or (isinstance(of, type) and issubclass(of, Base))):
        options[LOCKING] = of or Base
    return query.with_for_update(
        read=shared, key_share=key_share, of=of, skip_locked=skip_locked
    ).execution_options(**options)


def get_locked[M: Base](
    session: Session, model: type[M], ident: object, *, key_share: bool = False
) -> M | None:
    """Session.get, as locked does: the row locked, and read again into the
    object the session may already hold. None if there is no such row."""
    row = session.get(
        model, ident, with_for_update={"key_share": key_share}, populate_existing=True
    )
    if row is not None:
        _hold(session, inspect(row).key)
    return row


def settings_row[M: Base](session: Session, model: type[M], *, seen: Seen) -> M:
    """A settings table's one row, id 1, locked for a change to it, and
    unchanged since `seen`. Made by its migration and never deleted."""
    row = get_locked(session, model, 1)
    assert row is not None
    unchanged(row, seen)
    return row


# The version of a row (models.base.Versioned) an edit form showed; None for a
# change with no form, such as a command's, which means to replace what is
# there. Required, so no caller leaves it out by mistake.
type Seen = int | None

stale = metrics.get_meter(__name__).create_counter(
    "inventory.edits.stale",
    unit="{edit}",
    description="Saves refused as the row changed since its form was shown, by table",
)


def unchanged(row: Base, seen: Seen) -> None:
    """RuleBroken (changed_since_shown) unless the row, as locked, is at
    the version a form showed (models.base.Versioned): another save since would
    otherwise be undone, unseen (inventory-tng-8i4.123)."""
    if seen is not None and getattr(row, "edit_version") != seen:  # noqa: B009
        table = type(row).__tablename__
        stale.add(1, {"table": table})
        log.info(
            "edit refused: changed since shown",
            table=table,
            row_id=getattr(row, "id"),  # noqa: B009
        )
        raise RuleBroken("changed_since_shown")


# The catalogue lock (pg_advisory_xact_lock's key): every write to the
# catalogue (the tables whose protection says lock="catalogue") takes it
# first, in the domain function that makes it, so writes whose rules read
# other rows go one at a time: two at once could each pass their checks and
# together break them, such as two moves that make a cycle. Writes are few
# (administrators), so one lock is enough. Held until the transaction ends,
# or a savepoint it was taken in is rolled back.
CATALOGUE_LOCK = 0x696E76656E746F72  # Any fixed key; "inventor" in ASCII.


# What a session keeps in session.info of the catalogue, read as it was
# then (models.catalogue.reading_rules_held): forgotten as the catalogue
# lock is taken, so what is read under it is what any write before it
# committed.
READ_AGAIN_ONCE_LOCKED: set[str] = set()


def lock_catalogue(session: Session) -> None:
    """Take the catalogue lock: wait for any catalogue write under way to
    end. Taking it again in the same transaction is free."""
    held = CATALOGUE in session.info.get(LOCKS_INFO, {})
    session.execute(select(func.pg_advisory_xact_lock(CATALOGUE_LOCK)))
    _hold(session, CATALOGUE)
    if not held:
        for key in READ_AGAIN_ONCE_LOCKED:
            session.info.pop(key, None)


# Where the locks this transaction holds are noted: the catalogue lock as
# CATALOGUE, a row by its identity key, each with the transaction or
# savepoint it was taken in. A savepoint rolled back lets go of the locks
# taken in it, as PostgreSQL does; the transaction's end lets go of all.
LOCKS_INFO = "inventory.locks"
CATALOGUE = "catalogue"
# The execution option locked marks a locking read with: the class whose
# rows it locks.
LOCKING = "inventory_locking"
UNGUARDED_INFO = "inventory.unguarded"


def _hold(session: Session, key: object) -> None:
    session.info.setdefault(LOCKS_INFO, {}).setdefault(key, innermost(session))


def _locked_as_read(
    state: InstanceState[Any], context: QueryContext | None, *_: object
) -> None:
    # No context: set by an UPDATE statement, not read.
    if context is None:
        return
    locking = context.query.get_execution_options().get(LOCKING)
    if locking and issubclass(state.class_, locking):
        _hold(context.session, state.key)


# A row read by locked(): as it is first loaded, or read again into the
# object the session holds (populate_existing).
event.listen(Base, "load", _locked_as_read, propagate=True, raw=True)
event.listen(Base, "refresh", _locked_as_read, propagate=True, raw=True)


@event.listens_for(Session, "pending_to_persistent")
def _added(session: Session, row: Base) -> None:
    """A row this transaction added is its own until it commits: no other
    can see it. Only where a write to its table needs a lock."""
    if table_of(row).info["lock"] is not None:
        _hold(session, inspect(row).key)


@event.listens_for(Session, "after_transaction_end")
def _locks_released(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        session.info.pop(LOCKS_INFO, None)


@event.listens_for(Session, "after_soft_rollback")
def _savepoint_locks_released(
    session: Session, transaction: SessionTransaction
) -> None:
    """Forget the locks taken in a savepoint rolled back, or in one
    released into it (db_savepoints.savepoint_undone)."""
    savepoint = savepoint_undone(transaction)
    if savepoint is None:
        return
    held = session.info.get(LOCKS_INFO, {})
    for key, taken in list(held.items()):
        if within(taken, savepoint):
            del held[key]


def missing_lock(
    lock: Lock | None,
    action: Action,
    changed: Collection[str],
    *,
    catalogue: bool,
    row: bool,
) -> Lock | None:
    """The lock a write needs and lacks, given the table's (protection),
    what the write is, the columns it changes that need more than their
    row's lock, and whether the catalogue lock and the row's are held."""
    if lock == "catalogue" and not catalogue and (action != "update" or changed):
        return "catalogue"
    if lock is not None and action != "insert" and not row:
        return "row"
    return None


GUARD_FIXES: dict[Lock, str] = {
    "catalogue": "write it through its owner's functions, which take the"
    " catalogue lock (db_locking.lock_catalogue, locked_row, relock)",
    "row": "lock it in this transaction before changing it"
    " (db_locking.get_locked, locked, settings_row, relock)",
}


@event.listens_for(Session, "before_flush")
def _written_under_lock(
    session: Session, context: UOWTransaction, _instances: object
) -> None:
    """A write to a table whose protection names a lock holds it
    (db_protection): anything else is a bug, refused before it is written."""
    if session.info.get(UNGUARDED_INFO):
        return
    held = session.info.get(LOCKS_INFO, {})
    for write in flush_writes(session, context):
        missing = missing_lock(
            write.table.info["lock"],
            write.action,
            write.changed - flagged(write.table, "row_lock_only"),
            catalogue=CATALOGUE in held,
            row=write.key in held,
        )
        if missing is not None:
            log.error(
                "written without its lock",
                table=write.table.name,
                row_id=write.row_id,
                action=write.action,
                lock=missing,
            )
            raise RuntimeError(
                f"{write.table.name} {write.action} without the {missing} lock:"
                f" {GUARD_FIXES[missing]}"
            )


@event.listens_for(Session, "before_flush")
def _next_version(
    session: Session, context: UOWTransaction, _instances: object
) -> None:
    """One more on the version of each row changed that has one
    (models.base.Versioned), in the UPDATE itself. After flush_writes has found
    what changed, so the guard and history see the version as no change of
    their own. Not for a change only to row_lock_only or not_a_change
    columns, which no edit form sets (a receipt lowering item.on_order, an
    alias refolding an item's search_text): it undoes nothing a form shows,
    so it refuses no form's save."""
    for write in flush_writes(session, context):
        unshown = flagged(write.table, "row_lock_only") | flagged(
            write.table, "not_a_change"
        )
        if (
            write.action == "update"
            and "edit_version" in write.table.c
            and write.changed - unshown
        ):
            row: Any = write.row
            row.edit_version = write.table.c.edit_version + 1


def require_current(
    session: Session, model: type[Base], row_id: int, rule: str
) -> None:
    """RuleBroken for `rule` unless the row is there and not retired, read
    afresh: an object the session holds may be from before a lock."""
    table: Any = model
    current = exists_where(session, table.id == row_id, table.retired_at.is_(None))
    enforce(rule, when=not current)


def locked_row[M: Base](session: Session, model: type[M], ident: object) -> M | None:
    """A catalogue row, locked for a change to it (other rows take
    get_locked). Only domain functions lock catalogue rows: a page reads
    them with session.get, and the function it calls locks them (relock)
    and reads them again as locked.

    Locks are taken in one order, so no two writes deadlock:

    1. The catalogue lock (lock_catalogue), which every catalogue write
       takes before it reads what its rules need.
    2. Then rows, FOR UPDATE: several of a table by id, and places before
       a volunteer, as a save takes them (volunteers._lock: their custody
       location, then them).

    FOR UPDATE waits for any write under way that refers to the row, such
    as a save holding its places or its items, so what the change then
    reads of them is settled. Saves, counts and adjustments never take the
    catalogue lock; the order they hold rows in is stock/ledger.py's, above
    _hold_places. A change that locks two rows a save also refers to takes
    them in the save's order, or the two could deadlock
    (inventory-tng-8i4.58, 8i4.69, 8i4.78)."""
    lock_catalogue(session)
    return get_locked(session, model, ident)


def relock(
    session: Session,
    row: Base,
    *,
    current: bool | None = None,
    seen: Seen = None,
) -> None:
    """locked_row, for a row the session holds: the catalogue lock, then
    the row, read again as locked into the same object. With `current`,
    Gone unless the row, as locked, is current (True) or retired (False);
    with `seen`, unchanged since then."""
    found = locked_row(session, type(row), inspect(row).identity)
    assert found is row  # The session's own object, refreshed.
    retirable: Any = row
    if current is not None and (retirable.retired_at is None) != current:
        raise Gone
    unchanged(row, seen)


def stale_since(
    session: Session, row: Base, seen: Seen, *, current: bool | None = None
) -> bool:
    """relock, for a form checked before its fields: whether the row
    changed since version `seen`, so a stale form says so before what is
    wrong with what was typed (inventory-tng-hnta). Not stale, the locks
    are kept for the save, whose own check then passes."""
    _, broken = refusal(
        session,
        lambda: relock(session, row, current=current, seen=seen),
        "changed_since_shown",
    )
    return broken is not None


def stale_as_read(row: Base, seen: Seen) -> bool:
    """stale_since, unlocked, for a form whose save locks other rows before
    this one (locked_row's order), so it may not lock the row first: an
    item's save locks its open packages' places first, a volunteer's their
    custody location. The row as this request read it: changed then, it
    stays changed, as versions only grow; read unchanged, the save checks
    it again under its lock, so a change in between is refused then."""
    try:
        unchanged(row, seen)
    except RuleBroken:
        return True
    return False
