"""Two transactions at once: threads, and sessions whose commits are real."""

import threading
import time
from collections.abc import Callable, Mapping

import pytest
from psycopg.errors import LockNotAvailable
from sqlalchemy import Connection, Engine, exc, text
from sqlalchemy.orm import Session

from inventory_tng.db import Base

from helpers.rows import setting_up

# The longest a test's connection waits for a lock, so a deadlock, a
# missing wait or a lock a worker left held fails the test rather than
# hanging the run.
LOCK_TIMEOUT = "5s"


class Worker(threading.Thread):
    """`work` on a thread of its own, keeping what it returned or raised."""

    def __init__(self, work: Callable[[], object]) -> None:
        super().__init__(daemon=True)
        self.work = work
        self.result: object = None
        self.error: Exception | None = None
        self.start()

    def run(self) -> None:
        try:
            self.result = self.work()
        except Exception as error:  # noqa: BLE001  Kept for the test to see.
            self.error = error

    def finished(self) -> Worker:
        self.join(timeout=10)
        assert not self.is_alive(), "it never finished"
        return self


class Committed:
    """Sessions whose commits are real, for tests of two requests at once,
    and the threads that run them."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.sessions: list[Session] = []
        self.workers: list[Worker] = []

    def session(self) -> Session:
        session = setting_up(Session(self.engine, expire_on_commit=False))
        self.sessions.append(session)
        return session

    def blocked(self, session: Session | None, work: Callable[[], object]) -> Worker:
        """`work`, on `session`, started on a thread, once it is waiting
        for a lock: the moment each test is about. With no session (an
        app's request, on its own connection), once any connection to this
        database is. Polls, as pg_stat_activity is read afresh per
        transaction."""
        pid = session.scalar(text("SELECT pg_backend_pid()")) if session else None
        worker = Worker(work)
        self.workers.append(worker)
        probe = self.session()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            waiting = probe.scalar(
                text(
                    "SELECT count(*) > 0 FROM pg_stat_activity"
                    " WHERE wait_event_type = 'Lock'"
                    " AND datname = current_database()"
                    " AND (CAST(:p AS integer) IS NULL OR pid = :p)"
                ),
                {"p": pid},
            )
            probe.rollback()
            if waiting:
                return worker
            time.sleep(0.01)
        pytest.fail("it never waited for the lock")


def made(committed: Committed, *rows: Base) -> None:
    """The rows added from another connection, committed."""
    setup = committed.session()
    setup.add_all(rows)
    setup.commit()


def newest_ids(connection: Connection) -> dict[str, int]:
    """Every table's highest id now: delete_made deletes what is above it.
    Each before what it refers to, as deleting needs."""
    return {
        table.name: int(
            connection.scalar(text(f"SELECT coalesce(max(id), 0) FROM {table.name}"))
        )
        for table in reversed(Base.metadata.sorted_tables)
        if "id" in table.c
    }


def delete_made(
    engine: Engine, before: Mapping[str, int], *, wait: str = LOCK_TIMEOUT
) -> None:
    """Delete the rows made since `before` (newest_ids), in one
    transaction, each table before what it refers to. Waits at most `wait`
    for a lock: one another connection left held fails it, saying so,
    rather than hanging the run (inventory-tng-8i4.105)."""
    try:
        with engine.begin() as connection:
            connection.execute(text(f"SET LOCAL lock_timeout = '{wait}'"))
            for name, newest in before.items():
                connection.execute(
                    text(f"DELETE FROM {name} WHERE id > :newest"),
                    {"newest": newest},
                )
    except exc.OperationalError as error:
        if not isinstance(error.orig, LockNotAvailable):
            raise
        raise RuntimeError(
            f"cleanup waited {wait} for a lock another connection still holds:"
            " the test's rows are left behind"
        ) from error
