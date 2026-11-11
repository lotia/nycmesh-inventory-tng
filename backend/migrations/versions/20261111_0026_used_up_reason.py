"""the adjustment reason closing an open package gives, Used up, beside
0009's, kept apart from Lost or stolen (inventory-tng-8i4.133.6)

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-11 21:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO adjustment_reason (name) SELECT 'Used up' WHERE NOT EXISTS"
        " (SELECT 1 FROM adjustment_reason WHERE lower(name) = 'used up'"
        " AND retired_at IS NULL)"
    )


def downgrade() -> None:
    # Adjustments that gave it keep it: only one nothing gave goes.
    op.execute(
        "DELETE FROM adjustment_reason WHERE name = 'Used up' AND NOT EXISTS"
        " (SELECT 1 FROM ledger_transaction t WHERE t.reason_id = adjustment_reason.id)"
    )
