"""search reads VDC and thousands: what is searched folded again
(inventory-tng-ist.1, Ali, 2026-11-15)

Search now reads `VDC` and `VAC` after a number as volts, and a number
before a unit it reads without its thousands separators, so `1,000 ft`,
`1 000 ft` and `1000 ft` are one (units version 4, docs/decisions/0021).
Search only: no name is respelt, and no unit_key read otherwise. So every
row is folded again, as the app folds it (models.Searchable,
Item.refold), an item's current aliases too, items' and packages' by the
reading rules there are, and items' and aliases' name_folded, importing
the app's fold as 0036 does.

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-15 19:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from inventory_tng import units
from inventory_tng.models import FOLDED_WITH, fold, held

revision: str = "0046"
down_revision: str | Sequence[str] | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The units.VERSION this folds again for (0036 says how they follow).
FOLDS_UNITS = 4

# Each table's searched fields, in order, as models' SEARCHED at 0046, and
# whether reading rules read them (models.Searchable.READS_RULES).
SEARCHED: dict[str, tuple[tuple[str, ...], bool]] = {
    "item": (("name", "manufacturer", "part_number", "old_code"), True),
    "volunteer": (("name",), False),
    "location": (("name",), False),
    "item_package": (("name",), True),
}


def upgrade() -> None:
    bind = op.get_bind()
    rules = {
        token: units.Rule(reading, respell)
        for token, reading, respell in bind.execute(
            sa.text("SELECT token, reading, respell FROM reading_rule")
        )
    }
    aliases: dict[int, list[str]] = {}
    folded_aliases = []
    for alias_id, item_id, name, current in bind.execute(
        sa.text(
            "SELECT id, item_id, name, retired_at IS NULL FROM item_alias ORDER BY id"
        )
    ):
        folded_aliases.append({"id": alias_id, "name_folded": fold(name)})
        if current:
            aliases.setdefault(item_id, []).append(name)
    if folded_aliases:
        bind.execute(
            sa.text("UPDATE item_alias SET name_folded = :name_folded WHERE id = :id"),
            folded_aliases,
        )
    for table, (fields, reads_rules) in SEARCHED.items():
        read = rules if reads_rules else None
        rows = bind.execute(sa.text(f"SELECT id, {', '.join(fields)} FROM {table}"))
        also = aliases if table == "item" else {}
        folded = [
            {
                "id": row["id"],
                "folded_with": FOLDED_WITH,
                "search_text": " ".join(
                    [held(row[field] or "", read) for field in fields]
                    + [held(alias, read) for alias in also.get(row["id"], [])]
                ),
            }
            | ({"name_folded": fold(row["name"])} if table == "item" else {})
            for row in rows.mappings().all()
        ]
        if folded:
            named = ", name_folded = :name_folded" if table == "item" else ""
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET search_text = :search_text,"
                    f" folded_with = :folded_with{named} WHERE id = :id"
                ),
                folded,
            )


def downgrade() -> None:
    # Folded as the app now folds, and saying so: left as they are.
    pass
