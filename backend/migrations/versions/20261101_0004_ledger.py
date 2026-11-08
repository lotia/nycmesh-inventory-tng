"""the ledger: transactions and their movements

The tables are generated from models.py. The rules that need more than one
row are the app's (ledger.insert_transaction). The ledger has no history: it
is append-only, so it is its own history. The app's login may only read and
add to it: of what 0001 gives it, UPDATE and DELETE are taken back here, and
it never had TRUNCATE.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01 14:09:16.225237
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ledger_transaction",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("from_location_id", sa.Integer(), nullable=True),
        sa.Column("to_location_id", sa.Integer(), nullable=True),
        sa.Column("install_number", sa.Integer(), nullable=True),
        sa.Column("volunteer_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "CASE kind WHEN 'move' THEN num_nulls(from_location_id, to_location_id) = 0 WHEN 'receipt' THEN from_location_id IS NULL AND to_location_id IS NOT NULL WHEN 'install' THEN from_location_id IS NOT NULL AND to_location_id IS NULL ELSE num_nulls(from_location_id, to_location_id) = 1 END",
            name=op.f("ck_ledger_transaction_sides"),
        ),
        sa.CheckConstraint(
            "install_number IS NULL OR kind = 'install'",
            name=op.f("ck_ledger_transaction_install_number_on_install"),
        ),
        sa.CheckConstraint(
            "kind IN ('move', 'receipt', 'install', 'count', 'adjustment')",
            name=op.f("ck_ledger_transaction_kind"),
        ),
        sa.CheckConstraint(
            "from_location_id <> to_location_id",
            name=op.f("ck_ledger_transaction_two_places"),
        ),
        sa.CheckConstraint(
            "install_number > 0", name=op.f("ck_ledger_transaction_install_number")
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device.id"],
            name=op.f("fk_ledger_transaction_device_id_device"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_location_id"],
            ["location.id"],
            name=op.f("fk_ledger_transaction_from_location_id_location"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_location_id"],
            ["location.id"],
            name=op.f("fk_ledger_transaction_to_location_id_location"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["volunteer_id"],
            ["volunteer.id"],
            name=op.f("fk_ledger_transaction_volunteer_id_volunteer"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_transaction")),
        sa.UniqueConstraint(
            "idempotency_key", name=op.f("uq_ledger_transaction_idempotency_key")
        ),
    )
    op.create_index(
        op.f("ix_ledger_transaction_device_id"),
        "ledger_transaction",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_from_location_id"),
        "ledger_transaction",
        ["from_location_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_to_location_id"),
        "ledger_transaction",
        ["to_location_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_volunteer_id"),
        "ledger_transaction",
        ["volunteer_id"],
        unique=False,
    )
    op.create_table(
        "movement",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_movement_quantity")),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["item.id"],
            name=op.f("fk_movement_item_id_item"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["ledger_transaction.id"],
            name=op.f("fk_movement_transaction_id_ledger_transaction"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_movement")),
    )
    op.create_index(op.f("ix_movement_item_id"), "movement", ["item_id"], unique=False)
    op.create_index(
        op.f("ix_movement_transaction_id"), "movement", ["transaction_id"], unique=False
    )
    op.execute(f"REVOKE UPDATE, DELETE ON ledger_transaction, movement FROM {APP_ROLE}")


def downgrade() -> None:
    # Indexes go with their tables.
    op.drop_table("movement")
    op.drop_table("ledger_transaction")
