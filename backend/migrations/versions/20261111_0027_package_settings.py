"""a package's too-short-to-use length and minimum sealed in stores
(inventory-tng-8i4.133.7)

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-11 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "item_package", sa.Column("short_below", sa.Numeric(12, 3), nullable=True)
    )
    op.add_column(
        "item_package", sa.Column("minimum_sealed", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_item_package_short_below"), "item_package", "short_below > 0"
    )
    op.create_check_constraint(
        op.f("ck_item_package_minimum_sealed"), "item_package", "minimum_sealed >= 0"
    )
    op.execute(
        f"GRANT UPDATE (short_below, minimum_sealed) ON item_package TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.drop_column("item_package", "minimum_sealed")
    op.drop_column("item_package", "short_below")
