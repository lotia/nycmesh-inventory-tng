"""Two transactions at once: threads, and sessions whose commits are real."""

import threading
import time
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from inventory_tng.db import Base


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
        session = Session(self.engine, expire_on_commit=False)
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
