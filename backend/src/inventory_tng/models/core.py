"""Plumbing's tables: the history of changes, and rate limits."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from inventory_tng.core.db import ACTOR_PATTERN, Base, protection


class CatalogueHistory(Base):
    """Every change to a table with history (db.protection;
    core/history.py). A retired record's page reads who retired it
    (web/admin_pages.retired_by). The app may only add to it (the initial
    migration)."""

    __tablename__ = "catalogue_history"
    __table_args__ = (
        CheckConstraint("action IN ('insert', 'update', 'delete')", name="action"),
        # Ids, never names, whoever sets it (db.set_actor checks it first).
        CheckConstraint(f"actor ~ '^({ACTOR_PATTERN})$'", name="actor"),
        # A row's history, such as who retired it (web/admin_pages.retired_by).
        Index("ix_catalogue_history_table_name_row_id", "table_name", "row_id"),
        protection(owner="core/history.py", lock=None, history=False),
    )

    # bigint: one row per change to any table with history.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    table_name: Mapped[str] = mapped_column(Text)
    row_id: Mapped[int]
    action: Mapped[str] = mapped_column(Text)
    # From db.set_actor(), e.g. "admin:3"; NULL if the session named nobody.
    actor: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # NULL, not JSON null, where there is no row (an insert's old_row).
    old_row: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    new_row: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))


class RateLimit(Base):
    """Attempts counted against one rate limit's key, such as sign-in from
    one address (core/rate_limits.py): the times of those still inside its
    window, at most its limit, and when the newest leaves it, after which
    the row is deleted. Shared by every instance of the app."""

    __tablename__ = "rate_limit"
    __table_args__ = (
        Index("ix_rate_limit_expires_at", "expires_at"),
        protection(owner="core/rate_limits.py", lock=None, history=False),
    )

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    hits: Mapped[list[datetime]] = mapped_column(ARRAY(DateTime(timezone=True)))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
