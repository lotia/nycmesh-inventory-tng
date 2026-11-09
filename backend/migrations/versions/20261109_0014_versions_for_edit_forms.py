"""a version on each row an edit form shows (inventory-tng-8i4.123)

Each update the app makes adds one (models.Versioned); a form sends back
the version it showed, so a save of a row changed since is refused rather
than undoing that change. location's updatable columns are granted one by
one (migration 0002), so edit_version is added to them. Not named
version_id, as SQLAlchemy's version_id_col is, which this is not.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-09 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"

TABLES = (
    "adjustment_reason",
    "category",
    "item",
    "location",
    "scan_settings",
    "sign_in_settings",
    "site_settings",
    "volunteer",
)


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                "edit_version",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            ),
        )
    op.execute(f"GRANT UPDATE (edit_version) ON location TO {APP_ROLE}")


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "edit_version")
