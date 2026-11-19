"""Which transaction or savepoint a session is in, and which savepoint a
rollback undid: for what is noted per savepoint, and forgotten with it."""

from sqlalchemy.orm import Session, SessionTransaction


def innermost(session: Session) -> SessionTransaction | None:
    """The savepoint the session is in, else its transaction."""
    return session.get_nested_transaction() or session.get_transaction()


def within(taken: SessionTransaction | None, savepoint: SessionTransaction) -> bool:
    """Whether `taken` is `savepoint` or a savepoint inside it."""
    while taken is not None and taken is not savepoint:
        taken = taken.parent
    return taken is savepoint


def savepoint_undone(transaction: SessionTransaction) -> SessionTransaction | None:
    """The savepoint a rollback of `transaction` undid (after_soft_rollback),
    with everything begun or released into it: None if it undid the whole
    transaction. A flush that fails rolls back its own transaction within
    the savepoint, which rolls back the savepoint: so the savepoint is the
    nearest one around the transaction rolled back."""
    while not transaction.nested and transaction.parent is not None:
        transaction = transaction.parent
    return transaction if transaction.nested else None
