from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.core.db_once_committed import once_committed


def after_a_write(session: Session, work: Callable[[], None]) -> None:
    """once_committed, as a writer asks for it: in a transaction begun."""
    session.execute(text("SELECT 1"))
    once_committed(session, work)


def test_work_is_done_once_the_transaction_commits(session: Session) -> None:
    done: list[str] = []
    after_a_write(session, lambda: done.append("one"))
    with session.begin_nested():
        after_a_write(session, lambda: done.append("released"))
    assert done == []  # A savepoint released is not yet committed.
    session.commit()
    assert done == ["one", "released"]
    session.execute(text("SELECT 1"))
    session.commit()
    assert done == ["one", "released"]


def test_work_in_a_savepoint_rolled_back_is_never_done(session: Session) -> None:
    done: list[str] = []
    after_a_write(session, lambda: done.append("kept"))
    outer = session.begin_nested()
    with session.begin_nested():
        after_a_write(session, lambda: done.append("inner"))
    after_a_write(session, lambda: done.append("outer"))
    outer.rollback()
    with session.begin_nested() as undone:
        after_a_write(session, lambda: done.append("undone"))
        undone.rollback()
    session.commit()
    assert done == ["kept"]


def test_work_in_a_transaction_rolled_back_is_never_done(session: Session) -> None:
    done: list[str] = []
    after_a_write(session, lambda: done.append("rolled back"))
    session.rollback()
    after_a_write(session, lambda: done.append("closed"))
    session.close()  # As a request's session, closed without a commit.
    session.execute(text("SELECT 1"))
    session.commit()
    assert done == []


def test_work_that_fails_is_logged_and_the_rest_done(session: Session) -> None:
    done: list[str] = []

    def failing() -> None:
        raise RuntimeError

    after_a_write(session, failing)
    after_a_write(session, lambda: done.append("after"))
    with capture_logs() as logs:
        session.commit()
    assert done == ["after"]
    assert [entry["event"] for entry in logs] == ["work after commit failed"]
    assert logs[0]["log_level"] == "error"


def test_work_is_asked_for_in_a_transaction(session: Session) -> None:
    with pytest.raises(RuntimeError):
        once_committed(session, lambda: None)
