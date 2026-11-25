"""item unit recounted after: the last ledger transaction of an item when a
guided recount corrected its unit, wrong from the start (Ali, 2026-11-24,
inventory-tng-qety)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-25 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN = "unit_recounted_after"


def upgrade() -> None:
    op.add_column("item", sa.Column(COLUMN, sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f(f"fk_item_{COLUMN}_ledger_transaction"),
        "item",
        "ledger_transaction",
        [COLUMN],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f(f"ix_item_{COLUMN}"), "item", [COLUMN], unique=False)


def downgrade() -> None:
    op.drop_index(op.f(f"ix_item_{COLUMN}"), table_name="item")
    op.drop_constraint(
        op.f(f"fk_item_{COLUMN}_ledger_transaction"), "item", type_="foreignkey"
    )
    op.drop_column("item", COLUMN)
