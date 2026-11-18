"""Forms sent once (web/once.py)."""

import uuid

from sqlalchemy.orm import Mapped, mapped_column

from inventory_tng.core.db import Base, protection
from inventory_tng.models.base import CreatedAt, IntId


class SentForm(Base):
    """A form that makes something shown only once, by the key it was shown
    with (web/once.py), so sending it again makes nothing. Only ever added to,
    so it is its own history, as the ledger is."""

    __tablename__ = "sent_form"
    __table_args__ = (protection(owner="web/once.py", lock=None, history=False),)

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    key: Mapped[uuid.UUID] = mapped_column(unique=True)
