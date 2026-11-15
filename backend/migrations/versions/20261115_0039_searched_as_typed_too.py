"""searches keep each field as typed too: what is searched folded again
(inventory-tng-c0c.2, Ali, 2026-11-15)

search_text now keeps each field folded as typed and, where that differs,
with its units respelt (models.held, docs/decisions/0021), so every match
before units were read still works; and "in" is no unit. Every row is
folded again, an item's current aliases too, as the app folds them
(models.Searchable, Item.refold), importing the app's fold as 0036 does.
Every row, not only those whose folded_with differs: on a database made
new, 0036 stamped this release's FOLDED_WITH on rows it folded with
models.fold alone. name_folded is as it was.

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-15 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from inventory_tng.models import FOLDED_WITH, held

revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The units.VERSION this folds again for (0036 says how they follow).
FOLDS_UNITS = 2

# Each table's searched fields, in order, as models' SEARCHED at 0039.
SEARCHED: dict[str, tuple[str, ...]] = {
    "item": ("name", "manufacturer", "part_number", "old_code"),
    "volunteer": ("name",),
    "location": ("name",),
    "item_package": ("name",),
}


def upgrade() -> None:
    bind = op.get_bind()
    aliases: dict[int, list[str]] = {}
    for item_id, name in bind.execute(
        sa.text(
            "SELECT item_id, name FROM item_alias WHERE retired_at IS NULL ORDER BY id"
        )
    ):
        aliases.setdefault(item_id, []).append(name)
    for table, fields in SEARCHED.items():
        rows = bind.execute(sa.text(f"SELECT id, {', '.join(fields)} FROM {table}"))
        also = aliases if table == "item" else {}
        folded = [
            {
                "id": row["id"],
                "folded_with": FOLDED_WITH,
                "search_text": " ".join(
                    [held(row[field] or "") for field in fields]
                    + [held(alias) for alias in also.get(row["id"], [])]
                ),
            }
            for row in rows.mappings().all()
        ]
        if folded:
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET search_text = :search_text,"
                    " folded_with = :folded_with WHERE id = :id"
                ),
                folded,
            )


def downgrade() -> None:
    # Folded as the app now folds, and saying so: left as they are.
    pass
