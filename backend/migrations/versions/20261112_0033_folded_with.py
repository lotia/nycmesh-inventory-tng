"""each searchable row records what folded it: folded_with, such as
"anyascii 0.3.3, Unicode 17.0.0" (inventory-tng-7u4, inventory-tng-gfe)

So rows a release of anyascii, or a Python with a newer Unicode, spells
otherwise can be found and folded again (docs/decisions/0021,
2026-11-12). The rows there are are folded again here, as the app folds
them (models.Searchable), with the anyascii and Python installed as this
runs, and record it: so what each says folded it did, whichever folded
it in 0032.

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-12 15:00:00
"""

import importlib.metadata
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from anyascii import anyascii

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"
# 0032's: each table's folded columns, and what each holds, in order, spaced.
FOLDED: dict[str, dict[str, tuple[str, ...]]] = {
    "item": {
        "search_text": ("name", "manufacturer", "part_number", "old_code"),
        "name_folded": ("name",),
    },
    "volunteer": {"search_text": ("name",)},
    "location": {"search_text": ("name",)},
    "item_package": {"search_text": ("name",)},
}
# Where the app may update only the columns granted it (0002, 0014, 0032).
GRANTED = ("location", "item_package")


def upgrade() -> None:
    # models.FOLDED_WITH's, written out.
    folded_with = (
        f"anyascii {importlib.metadata.version('anyascii')}, "
        f"Unicode {unicodedata.unidata_version}"
    )
    for table, columns in FOLDED.items():
        op.add_column(table, sa.Column("folded_with", sa.Text(), nullable=True))
        _fold(table, columns, folded_with)
        op.alter_column(table, "folded_with", nullable=False)
    for table in GRANTED:
        op.execute(f"GRANT UPDATE (folded_with) ON {table} TO {APP_ROLE}")


def _fold(table: str, columns: dict[str, tuple[str, ...]], folded_with: str) -> None:
    bind = op.get_bind()
    read = sorted({field for fields in columns.values() for field in fields})
    rows = bind.execute(sa.text(f"SELECT id, {', '.join(read)} FROM {table}"))
    folded = [
        {
            "id": row["id"],
            "folded_with": folded_with,
            **{
                # models.fold's, written out.
                column: anyascii(
                    unicodedata.normalize(
                        "NFKC", " ".join(row[field] or "" for field in fields)
                    )
                )
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
    for table in GRANTED:
        op.execute(f"REVOKE UPDATE (folded_with) ON {table} FROM {APP_ROLE}")
    for table in FOLDED:
        op.drop_column(table, "folded_with")
