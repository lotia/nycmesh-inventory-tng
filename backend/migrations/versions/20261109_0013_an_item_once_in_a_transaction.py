"""an item once in a transaction (inventory-tng-8i4.122)

ledger.insert_transaction refuses a transaction naming an item on two
lines; this unique index says the same, and serves look-ups by transaction,
so it replaces the index on transaction_id alone.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-09 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_movement_transaction_id_item_id",
        "movement",
        ["transaction_id", "item_id"],
        unique=True,
    )
    op.drop_index(op.f("ix_movement_transaction_id"), table_name="movement")


def downgrade() -> None:
    op.create_index(
        op.f("ix_movement_transaction_id"), "movement", ["transaction_id"], unique=False
    )
    op.drop_index("uq_movement_transaction_id_item_id", table_name="movement")
