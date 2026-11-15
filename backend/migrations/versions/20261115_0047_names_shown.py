"""names shown: each name kept as the app shows it, beside it as typed
(inventory-tng-ist.3, Ali, 2026-11-15)

Items, packages and aliases gain name_shown: the name with each number a
unit is read after spaced from it by a no-break space, without its
thousands separators and grouped by thin no-break spaces from five digits
(units.shown_name, docs/decisions/0021), which the app writes as the
session flushes and again when a reading rule changes. Filled here as the
app reads names, by the reading rules there are, importing it as 0036
does. A rule changed later updates a package's and an alias's, so the app
may (0002 and 0038 grant theirs by column). If the rules for showing a name
change, a migration like this one, without the column, fills it again.

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-15 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from inventory_tng import units

revision: str = "0047"
down_revision: str | Sequence[str] | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"
TABLES = ("item", "item_package", "item_alias")


def upgrade() -> None:
    bind = op.get_bind()
    rules = {
        token: units.Rule(reading, respell)
        for token, reading, respell in bind.execute(
            sa.text("SELECT token, reading, respell FROM reading_rule")
        )
    }
    for table in TABLES:
        op.add_column(table, sa.Column("name_shown", sa.Text(), nullable=True))
        shown = [
            {"id": row.id, "name_shown": units.shown_name(row.name, rules)}
            for row in bind.execute(sa.text(f"SELECT id, name FROM {table}"))
        ]
        if shown:
            bind.execute(
                sa.text(f"UPDATE {table} SET name_shown = :name_shown WHERE id = :id"),
                shown,
            )
        op.alter_column(table, "name_shown", nullable=False)
    for table in ("item_package", "item_alias"):
        op.execute(f"GRANT UPDATE (name_shown) ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "name_shown")
