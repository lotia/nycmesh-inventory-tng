"""Volunteers: names to pick, not logins (docs/decisions/0004)."""

from sqlalchemy import CheckConstraint, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from inventory_tng.core.db_base import Base
from inventory_tng.core.db_protection import protection
from inventory_tng.models.base import (
    CreatedAt,
    IntId,
    RetiredAt,
    Versioned,
    reference,
    unique_current_name,
)
from inventory_tng.models.names import CanonicalName
from inventory_tng.models.search_text import Searchable


class Volunteer(CanonicalName, Searchable, Versioned, Base):
    """A name to pick, not a login (docs/decisions/0004)."""

    __tablename__ = "volunteer"
    __table_args__ = (
        unique_current_name("volunteer"),
        Index(
            "uq_volunteer_email",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        CheckConstraint("merged_into_id <> id", name="not_merged_into_self"),
        # So "current" is always retired_at IS NULL.
        CheckConstraint(
            "merged_into_id IS NULL OR retired_at IS NOT NULL", name="merged_is_retired"
        ),
        Index(
            "ix_volunteer_search",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
        protection(owner="volunteers/volunteers.py", lock="catalogue", history=True),
    )

    id: Mapped[IntId]
    created_at: Mapped[CreatedAt]
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    # Set when an administrator merges a duplicate, who is retired too. Ledger
    # rows keep their original volunteer; readers follow this forward.
    merged_into_id: Mapped[int | None] = reference("volunteer.id")
    retired_at: Mapped[RetiredAt]
