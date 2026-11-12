"""what an open package really has: a volunteer's report of it, a check the
app raises when one goes below zero, and the reports a batch made, as sent
(inventory-tng-wy7)

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-12 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "inventory_app"
REPORTED = (
    "length_reported_at",
    "length_reported_by_volunteer_id",
    "length_reported",
    "length_reported_in_id",
)


def upgrade() -> None:
    op.add_column(
        "open_package",
        sa.Column("length_reported_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "open_package",
        sa.Column("length_reported_by_volunteer_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "open_package",
        sa.Column("length_reported", sa.Numeric(12, 3), nullable=True),
    )
    op.add_column(
        "open_package",
        sa.Column("length_reported_in_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "open_package",
        sa.Column("below_zero_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_open_package_length_reported_in_id_ledger_transaction"),
        "open_package",
        "ledger_transaction",
        ["length_reported_in_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_open_package_length_reported_in_id"),
        "open_package",
        ["length_reported_in_id"],
    )
    op.create_foreign_key(
        op.f("fk_open_package_length_reported_by_volunteer_id_volunteer"),
        "open_package",
        "volunteer",
        ["length_reported_by_volunteer_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_open_package_length_reported_by_volunteer_id"),
        "open_package",
        ["length_reported_by_volunteer_id"],
    )
    op.create_check_constraint(
        op.f("ck_open_package_length_reported_by"),
        "open_package",
        "num_nulls(length_reported_at, length_reported_by_volunteer_id,"
        " length_reported, length_reported_in_id) IN (0, 4)",
    )
    op.create_check_constraint(
        op.f("ck_open_package_length_reported"),
        "open_package",
        "length_reported >= 0",
    )
    op.execute(
        f"GRANT UPDATE ({', '.join(REPORTED)}, below_zero_at)"
        f" ON open_package TO {APP_ROLE}"
    )
    # The app may only read and add to the ledger (0004), so it is never
    # changed once written.
    op.add_column(
        "ledger_transaction",
        sa.Column(
            "lengths",
            postgresql.JSONB(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("ledger_transaction", "lengths")
    op.drop_constraint(
        op.f("ck_open_package_length_reported"), "open_package", type_="check"
    )
    op.drop_constraint(
        op.f("ck_open_package_length_reported_by"), "open_package", type_="check"
    )
    op.drop_index(op.f("ix_open_package_length_reported_in_id"))
    op.drop_constraint(
        op.f("fk_open_package_length_reported_in_id_ledger_transaction"),
        "open_package",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_open_package_length_reported_by_volunteer_id"))
    op.drop_constraint(
        op.f("fk_open_package_length_reported_by_volunteer_id_volunteer"),
        "open_package",
        type_="foreignkey",
    )
    for column in (*REPORTED, "below_zero_at"):
        op.drop_column("open_package", column)
