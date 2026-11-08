"""The database: one engine per app, one session per request."""

import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Annotated, Any, Literal

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import (
    ColumnElement,
    Engine,
    MetaData,
    Select,
    create_engine,
    event,
    exc,
    exists,
    func,
    inspect,
    select,
    update,
)
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import DeclarativeBase, Session

log = structlog.get_logger(__name__)


class Base(DeclarativeBase):
    """Every mapped class derives from this. Alembic compares its metadata
    with the database."""

    # Named constraints, so a later migration can drop or alter them by name.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def make_engine(url: str) -> Engine:
    # A database that drops packets fails a request in seconds instead of
    # holding a worker until the operating system gives up. Parameters are
    # hidden from SQLAlchemy's log lines and messages, because they can hold
    # personal data.
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
    )
    event.listen(engine, "connect", _in_utc)
    return engine


def _in_utc(connection: DBAPIConnection, _record: object) -> None:
    """Times come back in UTC whatever the server's own zone
    (docs/decisions/0013). Set as each connection opens, rather than as a
    startup option, which would replace any options DATABASE_URL gives."""
    run_outside_transaction(connection, "SET TIME ZONE 'UTC'")


def get_session(request: Request) -> Iterator[Session]:
    """The request's own session. Rows stay loaded after a commit: a page
    that commits then shows them does not read them all again. It closes as
    the route returns (DbSession's scope), before the reply is sent, any
    page an exception handler shows, or any work after the reply: so what
    the route did not commit is gone, and any lock it took, such as a
    sensitive form's that was refused without a commit, is let go first."""
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        try:
            yield session
        finally:
            # Closing keeps session.info: the hold ends with the route too.
            release_hold(session)


DbSession = Annotated[Session, Depends(get_session, scope="function")]

# Opens a session of its own: for work after the reply, when the request's
# has closed.
SessionFactory = Callable[[], AbstractContextManager[Session]]


def get_session_factory(request: Request) -> SessionFactory:
    engine = request.app.state.engine
    return lambda: Session(engine, expire_on_commit=False)


Sessions = Annotated[SessionFactory, Depends(get_session_factory)]

# Who makes a session's changes, for history (history.py): ids, never
# names, so history holds no personal data through it. Also
# catalogue_history's check constraint (models.py, migration 0003).
# system:<name> is anything that is not a person, such as the command line.
CLI_ACTOR = "system:cli"
ACTOR_PATTERN = "(admin|volunteer):[0-9]+|system:[a-z_]+"
ACTOR = re.compile(ACTOR_PATTERN)
# Where set_actor keeps it.
ACTOR_INFO = "inventory.actor"


def set_actor(session: Session, actor: str) -> None:
    """Name who makes every change this session writes from now on:
    "admin:3", "volunteer:7", or "system:cli" for something that is not a
    person. A signed-in request's session is named as it starts, so no
    route need remember to."""
    if not ACTOR.fullmatch(actor):
        raise ValueError("an actor is admin:<id>, volunteer:<id> or system:<name>")
    session.info[ACTOR_INFO] = actor
    log.debug("actor set", actor=actor)


def current_actor(session: Session) -> str | None:
    """Who set_actor named, or None."""
    return session.info.get(ACTOR_INFO)


def exists_where(session: Session, *criteria: ColumnElement[bool]) -> bool:
    """Whether any row matches, as one EXISTS query."""
    return bool(session.scalar(select(exists().where(*criteria))))


# Where a gated request (sign_in.confirmed) keeps the transaction its lock
# lives in, until release_hold.
GATE_INFO = "gate"


# Locking a row (inventory-tng-8i4.57): lock with locked or get_locked,
# never with_for_update itself (tests/test_locking.py fails on it elsewhere).
#
# SQLAlchemy keeps one object per row in a session (its identity map). A
# query, even SELECT ... FOR UPDATE, that returns a row the session has
# already loaded gives back that object as it was: it does not overwrite its
# attributes with what the locked row now holds, unless asked to with
# populate_existing. So code that read a row, then locks it, would decide on
# values from before the lock, which another request may have changed: a
# lost update, or a check passed on old data (tests/test_locking.py shows
# it with two sessions). SQLAlchemy's maintainer keeps populate_existing
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
    of: type[Base] | None = None,
) -> Select[*Ts]:
    """`query` with its rows locked and read again as locked: SELECT ... FOR
    UPDATE, or FOR NO KEY UPDATE with `key_share` (which does not hold up
    inserting a row that refers to these); with `shared` too, FOR KEY SHARE,
    which only FOR UPDATE waits for. Only `of`'s rows if given."""
    return query.with_for_update(
        read=shared, key_share=key_share, of=of
    ).execution_options(populate_existing=True)


def get_locked[M: Base](
    session: Session, model: type[M], ident: object, *, key_share: bool = False
) -> M | None:
    """Session.get, as locked does: the row locked, and read again into the
    object the session may already hold. None if there is no such row."""
    return session.get(
        model, ident, with_for_update={"key_share": key_share}, populate_existing=True
    )


def hold_until_commit(session: Session) -> None:
    """This transaction holds a lock the request relies on: until
    release_hold, a commit in any other one, after this one's commit
    released it, fails."""
    session.info[GATE_INFO] = session.get_transaction()


def release_hold(session: Session) -> None:
    session.info.pop(GATE_INFO, None)


@event.listens_for(Session, "before_commit")
def _commit_only_while_held(session: Session) -> None:
    held = session.info.get(GATE_INFO)
    if held is not None and held is not session.get_transaction():
        raise RuntimeError("a gated request committed after its lock was gone")


def run_outside_transaction(connection: DBAPIConnection, *statements: str) -> None:
    """Run SET statements as a connection opens, in autocommit, so they are
    one round trip each and no rollback undoes them."""
    connection.autocommit = True
    cursor = connection.cursor()
    for statement in statements:
        cursor.execute(statement)
    cursor.close()
    connection.autocommit = False


def record_last_seen(
    engine: Engine, model: type[Any], row_id: int, now: datetime
) -> None:
    """Set a row's last_seen_at in a transaction of its own, so a request
    that saves nothing still counts, and the route's own transaction is left
    alone. A row the route has not committed is not seen, which is fine."""
    with Session(engine) as session, session.begin():
        session.execute(
            update(model).where(model.id == row_id).values(last_seen_at=now)
        )


class RuleBroken(Exception):
    """A change the catalogue's or the ledger's rules refuse, by the rule's
    name (as admin.common.RULES puts the catalogue's into words). Raised
    before anything is written."""

    def __init__(self, rule: str) -> None:
        super().__init__(rule)
        self.rule = rule


class Gone(Exception):
    """The row a change is for is no longer as the page found it, such as
    retired meanwhile by another request: there is nothing here to change,
    and a page answers not found (pages.ErrorPages)."""


def enforce(broken: str | None, *, when: bool = True) -> None:
    """Raise RuleBroken for `broken`, a rule's name, if `when`: as a rule
    function returns it, or named for a rule of one condition, as in
    enforce("category_has_items", when=has_items)."""
    if broken is not None and when:
        log.debug("rule broken", rule=broken)
        raise RuleBroken(broken)


def attempt[T](
    session: Session, change: Callable[[], T]
) -> tuple[T | None, str | None]:
    """Run `change` in a savepoint, with nothing of it kept if it breaks a
    rule: what it returned, and None; or None, and "unique" if it broke a
    unique index (as PostgreSQL compares names, ignoring case among current
    rows), the name of the check constraint it broke, or the rule a domain
    function refused it by (RuleBroken), so a page can say why in its own
    words. Any other failure is raised."""
    held = session.info.get(LOCK_INFO)
    try:
        with session.begin_nested():
            done = change()
            session.flush()
    except RuleBroken as broken:
        _unheld(session, held)
        return None, broken.rule
    except exc.IntegrityError as error:
        _unheld(session, held)
        sqlstate = getattr(error.orig, "sqlstate", None)
        if sqlstate == UNIQUE_VIOLATION:
            return None, "unique"
        constraint = describe_error(error).get("constraint")
        if sqlstate == CHECK_VIOLATION and constraint:
            return None, constraint
        raise
    return done, None


def _unheld(session: Session, held: object) -> None:
    """After a savepoint rolled back: a catalogue lock taken in it went
    with it."""
    if held is None:
        session.info.pop(LOCK_INFO, None)


def refusal[T](
    session: Session, change: Callable[[], T], *expected: str
) -> tuple[T | None, str | None]:
    """attempt(), for a change only the `expected` rules may refuse. Any
    other rule broken is a bug, and raised."""
    done, broken = attempt(session, change)
    if broken is not None and broken not in expected:
        raise ValueError(f"refused by {broken}, not {', '.join(expected)}")
    return done, broken


def unique(session: Session, change: Callable[[], object]) -> bool:
    """attempt(), for a change only a unique index can refuse: False if it
    did."""
    return refusal(session, change, "unique")[1] is None


UNIQUE_VIOLATION = "23505"
CHECK_VIOLATION = "23514"


def describe_error(error: exc.SQLAlchemyError) -> dict[str, str]:
    """What may be logged about a database error. Never its message:
    PostgreSQL's text can quote values, as a unique violation names the
    duplicate."""
    orig = getattr(error, "orig", None)
    fields = {"error": type(orig or error).__name__}
    if sqlstate := getattr(orig, "sqlstate", None):
        fields["sqlstate"] = sqlstate
    if constraint := getattr(getattr(orig, "diag", None), "constraint_name", None):
        fields["constraint"] = constraint
    return fields


# OperationalErrors that trying again can cure. Any other is a bug, and
# stays a 500: retrying it forever would hide it.
RETRYABLE_SQLSTATES = (
    "08",  # The connection failed or was lost.
    "40001",  # Serialization failure.
    "40P01",  # Deadlock.
    "53300",  # Too many connections.
    "57014",  # A statement timed out.
    "57P01",  # The server is shutting down...
    "57P02",
    "57P03",  # ...or is not accepting connections yet.
)


class UnavailableReply(BaseModel):
    reason: Literal["database_unavailable"]


# Declared for every route, since any of them can meet it (app.py).
UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    503: {
        "model": UnavailableReply,
        "description": "The database cannot answer; try again",
    }
}


def install(app: FastAPI) -> None:
    """A database the app cannot reach, or that cannot answer just now, is a
    503 on any route, so a client such as the scan page's outbox knows to try
    again."""

    @app.exception_handler(exc.OperationalError)
    @app.exception_handler(exc.TimeoutError)
    def unavailable(_request: Request, error: exc.SQLAlchemyError) -> JSONResponse:
        sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
        # No sqlstate: the server never answered, as when it is down.
        if sqlstate and not sqlstate.startswith(RETRYABLE_SQLSTATES):
            raise error
        log.warning("database unavailable", **describe_error(error))
        body = UnavailableReply(reason="database_unavailable")
        return JSONResponse(body.model_dump(), status_code=503)


# The catalogue lock (pg_advisory_xact_lock's key): every write to the
# catalogue (categories, items and their packages, volunteers, locations,
# labels) takes it first, in the domain function that makes it, so writes
# whose rules read other rows go one at a time: two at once could each pass
# their checks and together break them, such as two moves that make a
# cycle. Writes are few (administrators), so one lock is enough. Held until
# the transaction ends, or a savepoint it was taken in is rolled back.
CATALOGUE_LOCK = 0x696E76656E746F72  # Any fixed key; "inventor" in ASCII.
CATALOGUE_TABLES = frozenset(
    ("category", "item", "item_package", "label", "location", "volunteer")
)
# What may change without the lock: a receipt lowers what is on order of
# the items it brings (ledger._receive), which no rule reads.
UNLOCKED_COLUMNS = {"item": {"on_order"}}
# Where lock_catalogue notes the transaction that holds the lock.
LOCK_INFO = "inventory.catalogue_lock"


def lock_catalogue(session: Session) -> None:
    """Take the catalogue lock: wait for any catalogue write under way to
    end. Taking it again in the same transaction is free."""
    session.execute(select(func.pg_advisory_xact_lock(CATALOGUE_LOCK)))
    session.info.setdefault(LOCK_INFO, session.get_transaction())


@event.listens_for(Session, "after_transaction_end")
def _lock_released(session: Session, transaction: object) -> None:
    if session.info.get(LOCK_INFO) is transaction:
        del session.info[LOCK_INFO]


@event.listens_for(Session, "before_flush")
def _catalogue_written_under_lock(
    session: Session, _context: object, _instances: object
) -> None:
    """A catalogue row is written only by a domain function, which holds
    the catalogue lock: anything else is a bug, refused before it is
    written."""
    if session.info.get(LOCK_INFO) is not None:
        return
    rows = [*session.new, *session.deleted]
    for row in session.dirty:
        state = inspect(row)
        table = getattr(row, "__tablename__", None)
        changed = {a.key for a in state.attrs if a.history.has_changes()}
        if changed - UNLOCKED_COLUMNS.get(table or "", set()):
            rows.append(row)
    if written := sorted(
        {table for row in rows if (table := getattr(row, "__tablename__", None))}
        & CATALOGUE_TABLES
    ):
        log.error("catalogue written without its lock", tables=written)
        raise RuntimeError(f"{written} written without the catalogue lock")


def is_current(session: Session, model: type[Base], row_id: int) -> bool:
    """Whether the row is there and not retired, read afresh: an object the
    session holds may be from before a lock."""
    table: Any = model
    return exists_where(session, table.id == row_id, table.retired_at.is_(None))


def locked_row[M: Base](session: Session, model: type[M], ident: object) -> M | None:
    """A catalogue row, locked for a change to it (other rows take
    db.get_locked). Only domain functions lock catalogue rows: a page reads
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
    catalogue lock; the order they hold rows in is ledger.py's, above
    _hold_places. A change that locks two rows a save also refers to takes
    them in the save's order, or the two could deadlock
    (inventory-tng-8i4.58, 8i4.69, 8i4.78)."""
    lock_catalogue(session)
    return get_locked(session, model, ident)


def relock(session: Session, row: Base, *, current: bool | None = None) -> None:
    """locked_row, for a row the session holds: the catalogue lock, then
    the row, read again as locked into the same object. With `current`,
    Gone unless the row, as locked, is current (True) or retired (False)."""
    found = locked_row(session, type(row), inspect(row).identity)
    assert found is row  # The session's own object, refreshed.
    retirable: Any = row
    if current is not None and (retirable.retired_at is None) != current:
        raise Gone
