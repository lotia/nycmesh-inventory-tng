"""searches read units after a number: what is searched folded again
(inventory-tng-c0c.2)

models.fold now spells each unit right after a number by its symbol
(units.respell, docs/decisions/0021, 2026-11-14), so "10µF" and "10 μF" are
searched as "10uF", and folds each field alone. The rows folded before,
whose folded_with is not the app's, are folded again here as the app folds
them (models.Searchable). This imports the app's fold, as 0016 imports the
app's TotpKeys: the unit table is too long to write out, and a row is
folded as what its folded_with then says.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-14 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from inventory_tng.models import FOLDED_WITH, fold

revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The units.VERSION this folds again for. A change to the unit table adds a
# migration as this one, naming its own (tests/test_units.py fails until
# the newest names units.VERSION); each folds with the app's fold and says
# so, so a database made new folds once, with the newest.
FOLDS_UNITS = 1

# 0033's: each table's folded columns, and what each holds, in order, spaced.
FOLDED: dict[str, dict[str, tuple[str, ...]]] = {
    "item": {
        "search_text": ("name", "manufacturer", "part_number", "old_code"),
        "name_folded": ("name",),
    },
    "volunteer": {"search_text": ("name",)},
    "location": {"search_text": ("name",)},
    "item_package": {"search_text": ("name",)},
}


def upgrade() -> None:
    bind = op.get_bind()
    for table, columns in FOLDED.items():
        read = sorted({field for fields in columns.values() for field in fields})
        rows = bind.execute(
            sa.text(
                f"SELECT id, {', '.join(read)} FROM {table}"
                " WHERE folded_with <> :folded_with"
            ),
            {"folded_with": FOLDED_WITH},
        )
        folded = [
            {
                "id": row["id"],
                "folded_with": FOLDED_WITH,
                **{
                    column: " ".join(fold(row[field] or "") for field in fields)
                    for column, fields in columns.items()
                },
            }
            for row in rows.mappings().all()
        ]
        if folded:
            sets = ", ".join(
                f"{column} = :{column}" for column in [*columns, "folded_with"]
            )
            bind.execute(sa.text(f"UPDATE {table} SET {sets} WHERE id = :id"), folded)


def downgrade() -> None:
    # Folded as the app now folds, and saying so: left as they are.
    pass
