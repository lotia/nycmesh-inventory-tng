"""forms already sent, by key (inventory-tng-8i4.74)

A form that makes a secret shown once (a registration code, recovery codes,
a sign-in link) carries a key made when it was shown. Sending it again, as
Back then Forward offers, finds the key here and makes nothing.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-06 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sent_form",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("key", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sent_form")),
        sa.UniqueConstraint("key", name=op.f("uq_sent_form_key")),
    )


def downgrade() -> None:
    op.drop_table("sent_form")
