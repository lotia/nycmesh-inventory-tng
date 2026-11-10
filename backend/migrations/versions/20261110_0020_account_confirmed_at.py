"""when a session last typed a code for changing another administrator
(inventory-tng-8i4.50.13)

Not set at sign-in, so the first invite, reset, retire or bring-back in a
session always asks for a code (accounts.account_change_open).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-10 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_session",
        sa.Column("account_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_session", "account_confirmed_at")
