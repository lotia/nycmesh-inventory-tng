"""What an app under test is given."""

from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.app import create_app
from inventory_tng.db import (
    LOCKS_INFO,
    UNGUARDED_INFO,
    get_session,
    get_session_factory,
    release_hold,
)
from inventory_tng.settings import LogConfig, Settings

from databases import NO_DATABASE


@contextmanager
def as_the_app(session: Session) -> Generator[Session]:
    """The test's own session (helpers.rows.setting_up), lent to the app:
    1. what the test's setup left unflushed is written, unguarded;
    2. the app's writes meet the guard, knowing only the locks they take;
    3. what the app left unflushed is dropped, as closing its session would;
    4. the test's next setup is unguarded again. Nested, it does nothing."""
    if UNGUARDED_INFO not in session.info:
        yield session
        return
    session.flush()
    marker = session.info.pop(UNGUARDED_INFO)
    session.info.pop(LOCKS_INFO, None)
    try:
        yield session
    finally:
        for row in list(session.deleted):
            session.add(row)
        for row in list(session.new):
            session.expunge(row)
        session.expire_all()
        session.info[UNGUARDED_INFO] = marker


def sessions_of(session: Session) -> Callable[[], Iterator[Session]]:
    """An app's get_session for the test's own session, which outlives each
    request, so it is not closed as the route returns (db.get_session); a
    gated request's hold ends with the route, as its session would."""

    def request_session() -> Iterator[Session]:
        with as_the_app(session):
            try:
                yield session
            finally:
                release_hold(session)

    return request_session


def on_session(session: Session) -> dict[Callable[..., Any], Callable[..., Any]]:
    """An app's dependency overrides that put its requests, and the work
    after each reply, on the test's session, so they roll back with it,
    guarded as the app's own sessions are (as_the_app)."""
    return {
        get_session: sessions_of(session),
        get_session_factory: lambda: lambda: as_the_app(session),
    }


def out_of_reach(logging: LogConfig | None = None) -> TestClient:
    """A client of an app on its own engine and sessions, whose database is
    out of reach, as one that went away once the app was running. Never
    started: started, the app would refuse it (database_role.check)."""
    settings = Settings(database_url=NO_DATABASE, logging=logging or LogConfig())
    return TestClient(create_app(settings))
