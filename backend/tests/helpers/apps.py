"""What an app under test is given."""

from collections.abc import Callable, Iterator
from contextlib import nullcontext
from typing import Any

from sqlalchemy.orm import Session

from inventory_tng.db import get_session, get_session_factory, release_hold


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
