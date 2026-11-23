"""The database: the app's pools, one session per request, which of its
transactions may commit while a gated request holds its lock, and who
makes a session's changes."""

import re
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

import anyio
import structlog
from fastapi import Depends, Request
from sqlalchemy import (
    URL,
    ColumnElement,
    Connection,
    Engine,
    create_engine,
    event,
    exc,
    exists,
    func,
    select,
    text,
    update,
)
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session

from inventory_tng.core.db_base import Base
from inventory_tng.core.db_errors import (
    LOCK_NOT_AVAILABLE,
    NoSession,
    describe_error,
    pool_timeouts,
)

log = structlog.get_logger(__name__)


# Connections per process, by pool (Database; DEPLOYMENT.md, Database
# connections). The request pool's, fewer than the threads that run sync
# routes and dependencies (AnyIO's 40). The small pools keep no overflow,
# which would be opened and closed again for each burst.
POOL, OVERFLOW = 10, 5
SIDE_POOL, SIDE_OVERFLOW = 3, 0
LIMITS_POOL, LIMITS_OVERFLOW = 2, 0
POOL_TIMEOUT = 10
# Longer than any lock the rules wait for in the normal run of things (the
# catalogue lock, a row a save holds): a wait this long is something stuck,
# and the request answers 503 rather than holding its connection forever.
LOCK_TIMEOUT = "10s"
# The app's pools, in the order one may wait on the next (Database).
type PoolName = Literal["requests", "side", "limits"]
POOLS: tuple[PoolName, ...] = ("requests", "side", "limits")
# Where a session keeps the Database it came from (database_of).
DATABASE_INFO = "inventory.database"


@dataclass(frozen=True)
class Database:
    """The app's database, made once by create_app (make_database) and kept
    as app.state.db: three pools, and the places for requests' sessions, as
    many as the request pool has connections (session_slot).

    No connection is ever held while waiting for another from its own pool,
    or a pool that waits on its: enough at once would each hold one and wait
    for another (inventory-tng-8i4.125). So a request (request pool) writes
    last seen on the side pool, work after the reply runs on the side pool,
    and either counts attempts on the limits pool, whose connections wait on
    nothing: each pool's connections wait only on those after it in POOLS
    (tests/conftest.py checks every test keeps to it). Waiting longer than
    `timeout` for any answers 503 (db_errors.install)."""

    engine: Engine
    side: Engine
    limits: Engine
    sessions: anyio.CapacityLimiter
    # How long to wait for a connection, or a place for a session.
    timeout: float = POOL_TIMEOUT

    def session(self, *, side: bool = False, expire_on_commit: bool = True) -> Session:
        """A session on the request pool, or the side pool, that knows this
        Database (database_of)."""
        return Session(
            self.side if side else self.engine,
            info={DATABASE_INFO: self},
            expire_on_commit=expire_on_commit,
        )

    @property
    def pools(self) -> dict[PoolName, Engine]:
        """Each pool by name, in POOLS' order."""
        return {"requests": self.engine, "side": self.side, "limits": self.limits}

    def dispose(self) -> None:
        for pool in self.pools.values():
            pool.dispose()


def make_database(
    url: str | URL,
    *,
    pool: tuple[int, int] = (POOL, OVERFLOW),
    side: tuple[int, int] = (SIDE_POOL, SIDE_OVERFLOW),
    limits: tuple[int, int] = (LIMITS_POOL, LIMITS_OVERFLOW),
    timeout: float = POOL_TIMEOUT,
) -> Database:
    """The app's database; tests give smaller pools and shorter waits."""
    return Database(
        _engine(url, *pool, timeout, name="requests"),
        _engine(url, *side, timeout, name="side"),
        _engine(url, *limits, timeout, name="limits"),
        anyio.CapacityLimiter(sum(pool)),
        timeout,
    )


def database_of(session: Session) -> Database:
    """The Database `session` came from, for attempts counted on its limits
    pool (rate_limits): every session the app makes knows it
    (Database.session), as tests' do."""
    try:
        return session.info[DATABASE_INFO]
    except KeyError:
        raise RuntimeError(
            "a session made without its Database (db_sessions.Database.session)"
        ) from None


def make_engine(url: str) -> Engine:
    """One pool, for what serves no requests: migrations and the tests'
    own set-up."""
    return _engine(url, POOL, OVERFLOW)


# Printed in place of PostgreSQL's message, which a log must not hold
# (CONTRIBUTING.md, Logging).
CANNOT_CONNECT_TO_MIGRATE = (
    "Cannot connect to this checkout's database as its owner. If the server "
    "is down, or the role or the database is missing, run "
    "`mise run dev-database`."
)


def connect_to_migrate(engine: Engine, *, development: bool) -> Connection:
    """A connection for alembic's command line (migrations/env.py). In
    development, one refused exits, printing CANNOT_CONNECT_TO_MIGRATE."""
    try:
        return engine.connect()
    except exc.OperationalError as error:
        if not development:
            raise
        log.error("cannot connect to migrate", **describe_error(error))
        raise SystemExit(CANNOT_CONNECT_TO_MIGRATE) from None


def _engine(
    url: str | URL,
    pool: int,
    overflow: int,
    timeout: float = POOL_TIMEOUT,
    *,
    name: PoolName | None = None,
) -> Engine:
    # A database that drops packets fails a request in seconds instead of
    # holding a worker until the operating system gives up. Parameters are
    # hidden from SQLAlchemy's log lines and messages, because they can hold
    # personal data.
    engine = create_engine(
        url,
        pool_size=pool,
        max_overflow=overflow,
        pool_timeout=timeout,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
        pool_logging_name=name,
    )
    event.listen(engine, "connect", _prepare)
    if name is not None:
        _watch_order(engine, name)
    return engine


# The side and limits pools' connections taken and not yet given back, by
# their record: the thread that took each, and its pool's place in POOLS.
_TAKEN: dict[int, tuple[int, int]] = {}
_TAKING = threading.Lock()


def pool_order_broken(pool: PoolName, held: list[PoolName]) -> None:
    """A thread took a connection from `pool` while it held one from `held`,
    the same pool or one after it in POOLS: enough such at once would wait
    on each other (Database). A bug; tests fail on it (conftest)."""
    log.error("connection taken out of pool order", pool=pool, held=held)


def _watch_order(engine: Engine, name: PoolName) -> None:
    """Check each take from the side and limits pools against the pools the
    thread already holds. The request pool's are left out: its discipline
    is the requests' places (session_slot), and a request's connection
    passes between threads."""
    if (place := POOLS.index(name)) == 0:
        return

    def taken(_dbapi: object, record: object, _proxy: object) -> None:
        thread = threading.get_ident()
        with _TAKING:
            held = [POOLS[p] for t, p in _TAKEN.values() if t == thread and p >= place]
            _TAKEN[id(record)] = (thread, place)
        if held:
            pool_order_broken(name, held)

    def given_back(_dbapi: object, record: object) -> None:
        with _TAKING:
            _TAKEN.pop(id(record), None)

    event.listen(engine, "checkout", taken)
    event.listen(engine, "checkin", given_back)


def _prepare(connection: DBAPIConnection, _record: object) -> None:
    """Times come back in UTC whatever the server's own zone
    (docs/decisions/0013), and a lock is waited for at most LOCK_TIMEOUT.
    Set as each connection opens, rather than as startup options, which
    would replace any options DATABASE_URL gives."""
    run_outside_transaction(
        connection, "SET TIME ZONE 'UTC'", f"SET lock_timeout = '{LOCK_TIMEOUT}'"
    )


async def session_slot(request: Request) -> AsyncIterator[None]:
    """A place among the requests that hold a session at once, no more than
    the request pool has connections (Database.sessions), waited for on
    the event loop before the session is made. A request holds its
    connection across several threads in turn: its dependencies', its
    route's, and FastAPI's check of a returned model. With more requests
    holding connections than there are threads, each thread could wait for
    a connection held by a request waiting for a thread, until the pool
    timed out (inventory-tng-8i4.125). A request waiting here holds neither."""
    database: Database = request.app.state.db
    sessions = database.sessions
    try:
        with anyio.fail_after(database.timeout):
            await sessions.acquire()
    except TimeoutError:
        raise NoSession from None
    try:
        yield
    finally:
        sessions.release()


def get_session(
    request: Request, _slot: Annotated[None, Depends(session_slot, scope="function")]
) -> Iterator[Session]:
    """The request's own session. Rows stay loaded after a commit: a page
    that commits then shows them does not read them all again. It closes as
    the route returns (DbSession's scope), before the reply is sent, any
    page an exception handler shows, or any work after the reply: so what
    the route did not commit is gone, and any lock it took, such as a
    sensitive form's that was refused without a commit, is let go first."""
    database: Database = request.app.state.db
    with database.session(expire_on_commit=False) as session:
        try:
            yield session
        finally:
            # Closing keeps session.info: the hold ends with the route too.
            release_hold(session)


DbSession = Annotated[Session, Depends(get_session, scope="function")]


def get_snapshot(session: DbSession) -> Session:
    """The request's session, reading from here on one snapshot of the
    database: what was committed as its first read began, and nothing
    committed after, however many statements a reply takes. Read only, so
    a write fails at once rather than as a serialization failure. A
    transaction's isolation is set as it begins, so one begun already, as
    by reading the request's device, is let go first: it must have changed
    nothing."""
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("a snapshot asked for after a change")
    if session.in_transaction():
        session.rollback()
    session.connection(
        execution_options={
            "isolation_level": "REPEATABLE READ",
            "postgresql_readonly": True,
        }
    )
    return session


SnapshotSession = Annotated[Session, Depends(get_snapshot)]

# Opens a session of its own: for work after the reply, when the request's
# has closed.
SessionFactory = Callable[[], AbstractContextManager[Session]]


def get_session_factory(request: Request) -> SessionFactory:
    """On the side pool: work after the reply holds no place among the
    requests (session_slot), so it must not take the request pool's
    connections from those that do."""
    database: Database = request.app.state.db
    return lambda: database.session(side=True, expire_on_commit=False)


Sessions = Annotated[SessionFactory, Depends(get_session_factory)]

# Who makes a session's changes, for history (history.py): ids, never
# names, so history holds no personal data through it. Also
# catalogue_history's check constraint (models/core.py).
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


def ping(session: Session) -> None:
    """Asks the database the least it can answer: raises if it does not
    (core/health.py)."""
    session.execute(text("SELECT 1"))


def exists_where(session: Session, *criteria: ColumnElement[bool]) -> bool:
    """Whether any row matches, as one EXISTS query."""
    return bool(session.scalar(select(exists().where(*criteria))))


def by_name[M: Base](
    session: Session, model: type[M], *where: ColumnElement[bool]
) -> list[M]:
    """The rows of `model` that `where` picks, by name as people sort them."""
    named: Any = model
    return list(
        session.scalars(select(model).where(*where).order_by(func.lower(named.name)))
    )


# Where a gated request (guards.confirmed) keeps the transaction its lock
# lives in, until release_hold.
GATE_INFO = "gate"


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
    database: Database, model: type[Any], row_id: int, now: datetime
) -> None:
    """Set a row's last_seen_at in a transaction of its own, so a request
    that saves nothing still counts, and the route's own transaction is left
    alone. A row the route has not committed is not seen, which is fine.
    Bookkeeping only: with the side pool busy, as under a flood of guesses
    counted against the rate limits, or the row locked longer than
    LOCK_TIMEOUT, it is skipped, not the request failed."""
    try:
        with database.session(side=True) as session, session.begin():
            session.execute(
                update(model).where(model.id == row_id).values(last_seen_at=now)
            )
    except exc.TimeoutError:
        pool_timeouts.add(1, {"db.client.connection.pool.name": "side"})
        log.warning("last seen not recorded", table=model.__tablename__, row_id=row_id)
    except exc.OperationalError as error:
        if getattr(error.orig, "sqlstate", None) != LOCK_NOT_AVAILABLE:
            raise
        log.warning(
            "last seen not recorded",
            table=model.__tablename__,
            row_id=row_id,
            **describe_error(error),
        )
