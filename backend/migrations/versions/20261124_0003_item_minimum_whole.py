"""item minimum whole: an item counted each is reordered at a whole number
(Ali, 2026-11-23, inventory-tng-gfes)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24 18:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The units of amount_rules.COUNTED_WHOLE, as models.catalogue.Item
    # writes it; a test holds the two alike.
    op.create_check_constraint(
        op.f("ck_item_minimum_whole"),
        "item",
        "NOT unit IN ('each') OR minimum = trunc(minimum)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_item_minimum_whole"), "item", type_="check")
