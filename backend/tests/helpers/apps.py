"""What an app under test is given."""

from collections.abc import Callable, Iterator
from contextlib import nullcontext
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.app import create_app
from inventory_tng.db import get_session, get_session_factory, release_hold
from inventory_tng.settings import LogConfig, Settings

# Nothing listens on port 1, so connecting fails at once.
OUT_OF_REACH = "postgresql+psycopg://u:p@127.0.0.1:1/x"


def sessions_of(session: Session) -> Callable[[], Iterator[Session]]:
    """An app's get_session for the test's own session, which outlives each
    request, so it is not closed as the route returns (db.get_session); a
    gated request's hold ends with the route, as its session would."""

    def request_session() -> Iterator[Session]:
        try:
            yield session
        finally:
            release_hold(session)

    return request_session


def on_session(session: Session) -> dict[Callable[..., Any], Callable[..., Any]]:
    """An app's dependency overrides that put its requests, and the work
    after each reply, on the test's session, so they roll back with it."""
    return {
        get_session: sessions_of(session),
        get_session_factory: lambda: lambda: nullcontext(session),
    }


def out_of_reach(logging: LogConfig | None = None) -> TestClient:
    """A client of an app on its own engine and sessions, whose database is
    out of reach, as one that went away once the app was running. Never
    started: started, the app would refuse it (database_role.check)."""
    settings = Settings(database_url=OUT_OF_REACH, logging=logging or LogConfig())
    return TestClient(create_app(settings))
