"""what every administrator is told of (inventory-tng-8i4.50.10)

Changes to who may sign in, kept for good and shown to every administrator
(activity.py). The app may only add to it.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-10 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "inventory_app"
KINDS = (
    "invited",
    "sign_in_reset",
    "retired",
    "restored",
    "recovery_link_made",
    "sign_in_settings_changed",
    "locked_down",
    "let_in",
    "authenticators_retired",
)


def upgrade() -> None:
    op.create_table(
        "activity",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("by_admin_id", sa.Integer(), nullable=True),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            f"kind IN ({', '.join(repr(k) for k in KINDS)})",
            name=op.f("ck_activity_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["by_admin_id"],
            ["admin.id"],
            name=op.f("fk_activity_by_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin.id"],
            name=op.f("fk_activity_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity")),
    )
    op.create_index(op.f("ix_activity_by_admin_id"), "activity", ["by_admin_id"])
    op.create_index(op.f("ix_activity_admin_id"), "activity", ["admin_id"])
    op.create_index(op.f("ix_activity_at"), "activity", ["at"])
    op.execute(f"REVOKE UPDATE, DELETE ON activity FROM {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("activity")
