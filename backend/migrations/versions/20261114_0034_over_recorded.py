"""lines recorded with more than was moved, for an administrator to correct
(inventory-tng-0gg)

A scan page's batch, changed after another tab recorded it under the same
key, refused (409 key_reused) with less of a line than was recorded: kept
until an administrator corrects it or marks it checked (over_recorded.py).
The app never deletes one.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-14 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "inventory_app"


def upgrade() -> None:
    op.create_table(
        "over_recorded",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "raised_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("movement_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("settled_how", sa.Text(), nullable=True),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_over_recorded_quantity")),
        sa.CheckConstraint(
            "settled_how IN ('corrected', 'checked')",
            name=op.f("ck_over_recorded_how"),
        ),
        sa.CheckConstraint(
            "num_nulls(settled_at, settled_by_admin_id, settled_how) IN (0, 3)",
            name=op.f("ck_over_recorded_settled"),
        ),
        sa.ForeignKeyConstraint(
            ["movement_id"],
            ["movement.id"],
            name=op.f("fk_over_recorded_movement_id_movement"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settled_by_admin_id"],
            ["admin.id"],
            name=op.f("fk_over_recorded_settled_by_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_over_recorded")),
        sa.UniqueConstraint("movement_id", name=op.f("uq_over_recorded_movement_id")),
    )
    op.create_index(
        op.f("ix_over_recorded_settled_by_admin_id"),
        "over_recorded",
        ["settled_by_admin_id"],
    )
    op.execute(f"REVOKE DELETE ON over_recorded FROM {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("over_recorded")
