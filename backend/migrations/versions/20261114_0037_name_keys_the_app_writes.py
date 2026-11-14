"""items' and packages' name keys in a column the app writes, held to the
name by a CHECK constraint, with plain unique indexes on it
(inventory-tng-6sb)

The key was an expression in each unique index; now each row keeps it in
name_key (models.KeyedName, names.py), filled here by the CHECK's own
expression, so every row passes it. The key is now collated "C" whatever
the database's locale, so the app can write it: only ASCII's spaces and
punctuation go and only its letters are lowered (docs/decisions/0021). Under
a C locale or glibc's, that compares names as finely as the index did, or
more; but under another, such as an ICU locale (whose punctuation leaves
out $ + < = > ^ ` | ~) or a Turkish one (whose I lowers to a dotless i),
names the index kept apart may be one. So, each way, names that would
clash are named by id, to rename first, and nothing is changed until none
are.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-14 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"
# names.name_key_sql's, written out: migrations do not import the app.
KEY = (
    'lower(regexp_replace(name COLLATE "C",'
    " '(?!(?<=[0-9])[.][0-9])[\\t-\\r -/:-@[-`{-~]', '', 'g'))"
)
# 0031's, which the indexes held.
BEFORE = (
    "lower(regexp_replace(name, '(?!(?<=[0-9])[.][0-9])[[:space:][:punct:]]', '', 'g'))"
)
# Each table's unique name index, and what a name is unique within.
INDEXES = {
    "item": ("uq_item_name", ()),
    "item_package": ("uq_item_package_name", ("item_id",)),
}


def upgrade() -> None:
    _refuse_clashes(KEY, "the database's locale kept apart, such as a$b and ab")
    for table, (index, scope) in INDEXES.items():
        op.add_column(table, sa.Column("name_key", sa.Text(), nullable=True))
        op.execute(f"UPDATE {table} SET name_key = {KEY}")
        op.alter_column(table, "name_key", nullable=False)
        op.create_check_constraint(
            op.f(f"ck_{table}_name_key"), table, f"name_key = {KEY}"
        )
        op.drop_index(index, table_name=table)
        op.create_index(index, table, [*scope, "name_key"], unique=True)
    # Where the app may update only the columns granted it (0002).
    op.execute(f"GRANT UPDATE (name_key) ON item_package TO {APP_ROLE}")


def downgrade() -> None:
    _refuse_clashes(BEFORE, "the keys keep apart, such as Câble and CÂBLE")
    op.execute(f"REVOKE UPDATE (name_key) ON item_package FROM {APP_ROLE}")
    for table, (index, scope) in INDEXES.items():
        op.drop_index(index, table_name=table)
        op.create_index(
            index,
            table,
            [*map(sa.literal_column, scope), sa.literal_column(BEFORE)],
            unique=True,
        )
        # Its CHECK goes with it.
        op.drop_column(table, "name_key")


def _refuse_clashes(key: str, apart: str) -> None:
    """RuntimeError naming by id the rows `key` would make one name, which
    `apart` says how they differ, if any."""
    clashing = [
        f"{table} {ids}"
        for table, (_, scope) in INDEXES.items()
        for ids in _clashing(table, ", ".join([*scope, key]))
    ]
    if clashing:
        raise RuntimeError(
            f"Names {apart}, would be one name, by id: {'; '.join(clashing)}. "
            "Rename all but one in each, then migrate again."
        )


def _clashing(table: str, key: str) -> list[str]:
    """Ids of `table`'s rows, by group, that `key` makes one name."""
    groups = op.get_bind().execute(
        sa.text(
            f"SELECT array_agg(id ORDER BY id) FROM {table}"
            f" GROUP BY {key} HAVING count(*) > 1"
        )
    )
    return [", ".join(map(str, ids)) for ids in groups.scalars()]
