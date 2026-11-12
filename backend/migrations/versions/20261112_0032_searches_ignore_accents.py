"""searches ignore accents: each searchable table keeps what is searched
folded to ASCII, written by the app (inventory-tng-8i4.135)

Items' search_text, which PostgreSQL computed, is the app's now, folded,
and so is a folded copy of each item's name, for close names; volunteers',
places' and packages' names get a folded copy too, volunteers' indexed for
search as items' are (docs/decisions/0021, 2026-11-12). Filled here for
the rows there are, folded as the app folds them (models.fold: NFKC, then
anyascii, inventory-tng-gfe).

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-12 14:00:00
"""

import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from anyascii import anyascii

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"
# Each table's folded columns, and what each holds, in order, spaced:
# models.Item's and models.Searchable's, written out.
FOLDED: dict[str, dict[str, tuple[str, ...]]] = {
    "item": {
        "search_text": ("name", "manufacturer", "part_number", "old_code"),
        "name_folded": ("name",),
    },
    "volunteer": {"search_text": ("name",)},
    "location": {"search_text": ("name",)},
    "item_package": {"search_text": ("name",)},
}
# All new but items' search_text, which was PostgreSQL's.
ADDED = [
    (table, column)
    for table, columns in FOLDED.items()
    for column in columns
    if (table, column) != ("item", "search_text")
]
# Where the app may update only the columns granted it (0002, 0014).
GRANTED = ("location", "item_package")


def upgrade() -> None:
    op.execute("ALTER TABLE item ALTER COLUMN search_text DROP EXPRESSION")
    for table, column in ADDED:
        op.add_column(table, sa.Column(column, sa.Text(), nullable=True))
    for table, columns in FOLDED.items():
        _fill(table, columns)
        for column in columns:
            op.alter_column(table, column, nullable=False)
    op.create_index(
        "ix_volunteer_search",
        "volunteer",
        ["search_text"],
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )
    for table in GRANTED:
        op.execute(f"GRANT UPDATE (search_text) ON {table} TO {APP_ROLE}")


def _fill(table: str, columns: dict[str, tuple[str, ...]]) -> None:
    bind = op.get_bind()
    read = sorted({field for fields in columns.values() for field in fields})
    rows = bind.execute(sa.text(f"SELECT id, {', '.join(read)} FROM {table}"))
    folded = [
        {
            "id": row["id"],
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
        sets = ", ".join(f"{column} = :{column}" for column in columns)
        bind.execute(sa.text(f"UPDATE {table} SET {sets} WHERE id = :id"), folded)


def downgrade() -> None:
    for table in GRANTED:
        op.execute(f"REVOKE UPDATE (search_text) ON {table} FROM {APP_ROLE}")
    op.drop_index("ix_volunteer_search", table_name="volunteer")
    for table, column in ADDED:
        op.drop_column(table, column)
    # PostgreSQL's again, as 0002 made it.
    op.drop_index("ix_item_search", table_name="item")
    op.drop_column("item", "search_text")
    op.add_column(
        "item",
        sa.Column(
            "search_text",
            sa.Text(),
            sa.Computed(
                "name || ' ' || coalesce(manufacturer, '') || ' ' || "
                "coalesce(part_number, '') || ' ' || coalesce(old_code, '')",
                persisted=True,
            ),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_item_search",
        "item",
        ["search_text"],
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )
