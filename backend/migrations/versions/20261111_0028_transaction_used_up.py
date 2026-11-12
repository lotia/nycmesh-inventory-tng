"""the open packages a transaction said were used up, as sent, so a batch
sent again with other reports is refused (inventory-tng-ckl)

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-11 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The app may only read and add to the ledger (0004), so it is never
    # changed once written.
    op.add_column(
        "ledger_transaction",
        sa.Column(
            "used_up",
            postgresql.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("ledger_transaction", "used_up")
