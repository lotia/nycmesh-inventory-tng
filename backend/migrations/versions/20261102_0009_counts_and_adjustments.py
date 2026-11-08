"""counts and adjustments (inventory-tng-8i4.14.8)

Counts and adjustments are an administrator's: admin_id says whose, and an
adjustment's reason_id why, from adjustment_reason, which administrators
extend and which starts with the reasons below. A count that finds what the
records say is written too, with no lines, so when a place was last counted
is known. A retired item is never received: what is left of it may only go
out (docs/decisions/0003). The app checks both (ledger.insert_transaction).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-02 15:47:43.492571
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASONS = (
    "Damaged",
    "Lost or stolen",
    "Found",
    "Used for testing",
    "Records were wrong",
)


def upgrade() -> None:
    op.create_table(
        "adjustment_reason",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_adjustment_reason")),
    )
    op.create_index(
        "uq_adjustment_reason_name",
        "adjustment_reason",
        [sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.add_column(
        "ledger_transaction", sa.Column("admin_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "ledger_transaction", sa.Column("reason_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        op.f("ix_ledger_transaction_admin_id"),
        "ledger_transaction",
        ["admin_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_reason_id"),
        "ledger_transaction",
        ["reason_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_ledger_transaction_reason_id_adjustment_reason"),
        "ledger_transaction",
        "adjustment_reason",
        ["reason_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_ledger_transaction_admin_id_admin"),
        "ledger_transaction",
        "admin",
        ["admin_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_ledger_transaction_admin_counts"),
        "ledger_transaction",
        "(kind IN ('count', 'adjustment')) = (admin_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_ledger_transaction_adjustment_reason"),
        "ledger_transaction",
        "(kind = 'adjustment') = (reason_id IS NOT NULL)",
    )
    reasons = sa.table("adjustment_reason", sa.column("name", sa.Text()))
    op.bulk_insert(reasons, [{"name": name} for name in REASONS])


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_ledger_transaction_adjustment_reason"), "ledger_transaction"
    )
    op.drop_constraint(op.f("ck_ledger_transaction_admin_counts"), "ledger_transaction")
    op.drop_constraint(
        op.f("fk_ledger_transaction_admin_id_admin"),
        "ledger_transaction",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_ledger_transaction_reason_id_adjustment_reason"),
        "ledger_transaction",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_ledger_transaction_reason_id"), table_name="ledger_transaction"
    )
    op.drop_index(
        op.f("ix_ledger_transaction_admin_id"), table_name="ledger_transaction"
    )
    op.drop_column("ledger_transaction", "reason_id")
    op.drop_column("ledger_transaction", "admin_id")
    op.drop_index(
        "uq_adjustment_reason_name",
        table_name="adjustment_reason",
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.drop_table("adjustment_reason")
