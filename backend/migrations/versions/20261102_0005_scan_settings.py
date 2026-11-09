"""scan settings: how the scan page behaves, set by administrators

One row, made here with the defaults. The app's login may change its row
but not delete it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02 03:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"


def upgrade() -> None:
    op.create_table(
        "scan_settings",
        sa.Column("id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "name_check_minutes",
            sa.Integer(),
            server_default=sa.text("15"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_scan_settings_one_row")),
        sa.CheckConstraint(
            "name_check_minutes BETWEEN 0 AND 1440",
            name=op.f("ck_scan_settings_name_check_minutes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_settings")),
    )
    op.execute("INSERT INTO scan_settings DEFAULT VALUES")
    op.execute(f"REVOKE DELETE ON scan_settings FROM {APP_ROLE}")


def downgrade() -> None:
    # Its history stays.
    op.drop_table("scan_settings")
