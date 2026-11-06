"""What an app under test is given."""

from collections.abc import Callable, Iterator

from sqlalchemy.orm import Session

from inventory_tng.db import release_hold


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
