"""a count settles the end of one recorded more than moved (inventory-tng-qs8,
Ali, 2026-11-15)

A count, as of a moment after the movement, of its item where it left or
where it arrived puts that end right: the row keeps which count did. Every
end it has counted, it closes as counted.

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-15 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | Sequence[str] | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENDS = ("left_count_id", "arrived_count_id")


def upgrade() -> None:
    for column in ENDS:
        op.add_column("over_recorded", sa.Column(column, sa.Integer()))
        op.create_foreign_key(
            op.f(f"fk_over_recorded_{column}_ledger_transaction"),
            "over_recorded",
            "ledger_transaction",
            [column],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(op.f(f"ix_over_recorded_{column}"), "over_recorded", [column])
    op.drop_constraint(op.f("ck_over_recorded_how"), "over_recorded", type_="check")
    op.create_check_constraint(
        op.f("ck_over_recorded_how"),
        "over_recorded",
        "settled_how IN ('corrected', 'checked', 'counted')",
    )
    op.create_check_constraint(
        op.f("ck_over_recorded_counted_waiting"),
        "over_recorded",
        "settled_at IS NOT NULL OR num_nonnulls(left_count_id, arrived_count_id) <= 1",
    )
    op.create_check_constraint(
        op.f("ck_over_recorded_counted"),
        "over_recorded",
        "settled_how IS DISTINCT FROM 'counted'"
        " OR num_nonnulls(left_count_id, arrived_count_id) >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_over_recorded_counted"), "over_recorded", type_="check")
    op.drop_constraint(
        op.f("ck_over_recorded_counted_waiting"), "over_recorded", type_="check"
    )
    op.drop_constraint(op.f("ck_over_recorded_how"), "over_recorded", type_="check")
    # Closed by counts, as it would have been marked checked before.
    op.execute(
        "UPDATE over_recorded SET settled_how = 'checked' WHERE settled_how = 'counted'"
    )
    op.create_check_constraint(
        op.f("ck_over_recorded_how"),
        "over_recorded",
        "settled_how IN ('corrected', 'checked')",
    )
    for column in reversed(ENDS):
        op.drop_column("over_recorded", column)
