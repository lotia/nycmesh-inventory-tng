"""rate limits shared by every instance of the app (inventory-tng-8i4.124)

Each row is one limit's key, such as sign-in from one address: the times
of its attempts still inside its window, and when the newest leaves it.
The app deletes rows past that as it counts (rate_limits.py), so the table
holds only keys tried within their window, an hour at most.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-09 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("hits", postgresql.ARRAY(sa.DateTime(timezone=True)), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_rate_limit")),
    )
    op.create_index(
        op.f("ix_rate_limit_expires_at"), "rate_limit", ["expires_at"], unique=False
    )


def downgrade() -> None:
    op.drop_table("rate_limit")
