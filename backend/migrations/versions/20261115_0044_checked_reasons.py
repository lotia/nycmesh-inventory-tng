"""one marked checked says why it is right as it is (inventory-tng-zgy,
Ali, 2026-11-15)

Mark checked on Recorded more than moved takes a reason: counted where it
left, where it arrived, at both ends, or put right another way, said in
words. Kept on the row; one settled before has none, as Confirm count
gives none.

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-15 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | Sequence[str] | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("over_recorded", sa.Column("checked_reason", sa.Text()))
    op.add_column("over_recorded", sa.Column("checked_words", sa.Text()))
    op.create_check_constraint(
        op.f("ck_over_recorded_checked_reason"),
        "over_recorded",
        "checked_reason IS NULL OR settled_how = 'checked' AND "
        "checked_reason IN ('left', 'arrived', 'both', 'other')",
    )
    op.create_check_constraint(
        op.f("ck_over_recorded_checked_words"),
        "over_recorded",
        "(checked_words IS NULL) = (checked_reason IS DISTINCT FROM 'other')"
        " AND checked_words ~ '[^[:space:]]' AND length(checked_words) <= 200",
    )


def downgrade() -> None:
    op.drop_column("over_recorded", "checked_words")
    op.drop_column("over_recorded", "checked_reason")
