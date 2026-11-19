from sqlalchemy import text
from sqlalchemy.orm import Session

from inventory_tng.core.db_once_committed import once_committed


def test_work_is_done_once_the_transaction_commits(session: Session) -> None:
    done: list[str] = []
    once_committed(session, lambda: done.append("one"))
    with session.begin_nested():
        once_committed(session, lambda: done.append("released"))
    assert done == []  # A savepoint released is not yet committed.
    session.commit()
    assert done == ["one", "released"]
    session.execute(text("SELECT 1"))
    session.commit()
    assert done == ["one", "released"]


def test_work_in_a_savepoint_rolled_back_is_never_done(session: Session) -> None:
    done: list[str] = []
    once_committed(session, lambda: done.append("kept"))
    outer = session.begin_nested()
    with session.begin_nested():
        once_committed(session, lambda: done.append("inner"))
    once_committed(session, lambda: done.append("outer"))
    outer.rollback()
    with session.begin_nested() as undone:
        once_committed(session, lambda: done.append("undone"))
        undone.rollback()
    session.commit()
    assert done == ["kept"]


def test_work_in_a_transaction_rolled_back_is_never_done(session: Session) -> None:
    done: list[str] = []
    once_committed(session, lambda: done.append("rolled back"))
    session.rollback()
    session.execute(text("SELECT 1"))
    session.commit()
    assert done == []
