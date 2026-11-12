"""Counting the statements a request sends."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.orm import Session

# The test's own savepoints, not the page's reads.
_NOT_COUNTED = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")


@contextmanager
def counting(session: Session) -> Generator[list[str]]:
    """The statements sent on the test's connection while inside, which
    an app on it (helpers.apps.on_session) sends for its requests too,
    less savepoints."""
    sent: list[str] = []

    def keep(_c: object, _cursor: object, statement: str, *_: object) -> None:
        if not statement.lstrip().upper().startswith(_NOT_COUNTED):
            sent.append(statement)

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", keep)
    try:
        yield sent
    finally:
        event.remove(connection, "before_cursor_execute", keep)
