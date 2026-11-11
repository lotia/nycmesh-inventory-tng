"""a volunteer's report of open packages used up is a transaction of its
own kind, at one place, with no lines (inventory-tng-8i4.133.5)

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-11 20:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = "kind IN ('move', 'receipt', 'install', 'count', 'adjustment', 'open')"
KINDS = (
    "kind IN ('move', 'receipt', 'install', 'count', 'adjustment', 'open', 'report')"
)
NAME = op.f("ck_ledger_transaction_kind")


def upgrade() -> None:
    op.drop_constraint(NAME, "ledger_transaction", type_="check")
    op.create_check_constraint(NAME, "ledger_transaction", KINDS)


def downgrade() -> None:
    op.execute("DELETE FROM ledger_transaction WHERE kind = 'report'")
    op.drop_constraint(NAME, "ledger_transaction", type_="check")
    op.create_check_constraint(NAME, "ledger_transaction", BEFORE)
