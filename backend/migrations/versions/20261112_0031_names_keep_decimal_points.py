"""names keep a point or comma between two digits, so '305 m' and
'30.5 m' are two names, for items and their packages (inventory-tng-0pm)

The key compares names finer than 0023's and 0030's: two names it makes one
were one before, so no rows can clash on the way up. On the way down, rows
named so would, and are named by id, to rename first.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-12 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# models.name_key's, written out: migrations do not import the app.
KEY = (
    "lower(regexp_replace(name, "
    "'(?![.,](?<=[0-9][.,])(?=[0-9]))[[:space:][:punct:]]', '', 'g'))"
)
# 0023's and 0030's.
BEFORE = "lower(regexp_replace(name, '[[:space:][:punct:]]+', '', 'g'))"


def _indexes(key: str) -> None:
    op.drop_index("uq_item_name", table_name="item")
    op.create_index("uq_item_name", "item", [sa.literal_column(key)], unique=True)
    op.drop_index("uq_item_package_name", table_name="item_package")
    op.create_index(
        "uq_item_package_name",
        "item_package",
        [sa.literal_column("item_id"), sa.literal_column(key)],
        unique=True,
    )


def upgrade() -> None:
    _indexes(KEY)


def _clashing(table: str, key: str) -> list[str]:
    """Ids of `table`'s rows, by group, that `key` makes one name."""
    groups = op.get_bind().execute(
        sa.text(
            f"SELECT array_agg(id ORDER BY id) FROM {table}"
            f" GROUP BY {key} HAVING count(*) > 1"
        )
    )
    return [", ".join(map(str, ids)) for ids in groups.scalars()]


def downgrade() -> None:
    clashing = [f"items {ids}" for ids in _clashing("item", BEFORE)] + [
        f"packages {ids}" for ids in _clashing("item_package", f"item_id, {BEFORE}")
    ]
    if clashing:
        raise RuntimeError(
            "Names that differ only by a point or comma between digits, by "
            f"id: {'; '.join(clashing)}. Rename all but one in each, then "
            "downgrade again."
        )
    _indexes(BEFORE)
