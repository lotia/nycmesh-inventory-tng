"""Work done once a session's transaction commits, such as measuring and
logging what it wrote: so a commit that fails, and the retry after it,
does it once. Work asked for in a savepoint rolled back, or in a
transaction rolled back, is never done."""

from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

from inventory_tng.core.db_savepoints import innermost, savepoint_undone, within

# Where the work waiting for the commit is kept, each with the transaction
# or savepoint it was asked for in.
PENDING_INFO = "inventory.once_committed"


def once_committed(session: Session, work: Callable[[], None]) -> None:
    """Do `work` once the session's transaction commits: begun now, if
    none is, so a rollback before it begins drops the work too."""
    asked = innermost(session) or session.begin()
    session.info.setdefault(PENDING_INFO, []).append((asked, work))


@event.listens_for(Session, "after_commit")
def _committed(session: Session) -> None:
    # A savepoint released is not yet committed.
    if session.in_nested_transaction():
        return
    for _, work in session.info.pop(PENDING_INFO, []):
        work()


@event.listens_for(Session, "after_soft_rollback")
def _rolled_back(session: Session, transaction: SessionTransaction) -> None:
    savepoint = savepoint_undone(transaction)
    pending = session.info.get(PENDING_INFO, [])
    pending[:] = [
        (asked, work)
        for asked, work in pending
        if savepoint is not None and not within(asked, savepoint)
    ]


@event.listens_for(Session, "after_transaction_end")
def _ended(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        session.info.pop(PENDING_INFO, None)
