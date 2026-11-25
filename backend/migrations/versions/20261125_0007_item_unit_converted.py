"""item unit converted: whether an item's recorded amounts have been
converted between lengths, which keeps its unit fixed (Ali, 2026-11-25,
inventory-tng-l26i)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-25 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "item",
        sa.Column(
            "unit_converted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("item", "unit_converted")
