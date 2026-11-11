"""item names unique ignoring spaces and punctuation as well as case
(inventory-tng-8i4.131)

Two items whose names differ only by case, spaces or punctuation, such as
"ToughCable" and "Tough cable", are one name (docs/decisions/0021). Items
already clashing so are named by id, to rename first: the index cannot be
made over them.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-11 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# models.name_key's, written out: migrations do not import the app.
KEY = "lower(regexp_replace(name, '[[:space:][:punct:]]+', '', 'g'))"


def upgrade() -> None:
    clashing = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT array_agg(id ORDER BY id) FROM item GROUP BY {KEY}"
                " HAVING count(*) > 1"
            )
        )
        .scalars()
        .all()
    )
    if clashing:
        groups = "; ".join(", ".join(map(str, ids)) for ids in clashing)
        raise RuntimeError(
            "Items whose names differ only by case, spaces or punctuation, "
            f"by id: {groups}. Rename all but one in each, then upgrade again."
        )
    op.drop_index("uq_item_name", table_name="item")
    op.create_index("uq_item_name", "item", [sa.literal_column(KEY)], unique=True)


def downgrade() -> None:
    op.drop_index("uq_item_name", table_name="item")
    op.create_index(
        "uq_item_name", "item", [sa.literal_column("lower(name)")], unique=True
    )
