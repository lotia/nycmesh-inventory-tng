"""site settings: the local time zone (docs/decisions/0013)

One row, made here with the defaults. The app's login may change its row
but not delete it.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02 13:36:17.108220
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"


def upgrade() -> None:
    op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "time_zone", sa.Text(), server_default="America/New_York", nullable=False
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_site_settings_one_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_settings")),
    )
    op.execute("INSERT INTO site_settings DEFAULT VALUES")
    op.execute(f"REVOKE DELETE ON site_settings FROM {APP_ROLE}")


def downgrade() -> None:
    # Its history stays.
    op.drop_table("site_settings")
