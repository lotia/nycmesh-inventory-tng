"""The sent_form table's owner (docs/decisions/0016): the keys of forms
sent once, each spent when its form is first sent. One that makes
something shown only once makes nothing sent again
(web/single_use_forms.py, inventory-tng-8i4.74); a count form keeps a
digest of what it said, so one sent again otherwise is told apart
(stock/store_counts.py, inventory-tng-kro9).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from inventory_tng.core.db_sessions import exists_where
from inventory_tng.models.core import SentForm


def form_key(typed: object) -> uuid.UUID | None:
    """A form's key, as its page made it, or None if it isn't one."""
    if not isinstance(typed, str):
        return None
    try:
        return uuid.UUID(typed)
    except ValueError:
        return None


def spend(session: Session, key: uuid.UUID, digest: bytes | None = None) -> bool:
    """Spend the key in the session's transaction, with the `digest` of what
    its form said, if given: True if it was new, False if a form sent
    before spent it. A second spending at once waits on the first, then
    finds it."""
    added = session.execute(
        insert(SentForm)
        .values(key=key, digest=digest)
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(SentForm.key)
    )
    return added.first() is not None


def digest_spent(session: Session, key: uuid.UUID) -> bytes | None:
    """The digest the key was spent with (spend), or None."""
    return session.scalar(select(SentForm.digest).where(SentForm.key == key))


def is_spent(session: Session, typed: object) -> bool:
    """Whether the form with this key was sent before, without spending it:
    so one sent again is answered before a code is spent on it."""
    key = form_key(typed)
    return key is not None and exists_where(session, SentForm.key == key)
