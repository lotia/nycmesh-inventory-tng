"""names shown: a number grouped by spaces before its unit shown whole and
as typed (inventory-tng-t78, Ali, 2026-11-16)

A number grouped by spaces before a unit is now shown whole and as typed,
with a no-break space before its unit as for any number, and judged as
search reads it, one number: `Box 1 000 ft` is shown `Box 1 000 ft`, where
it had been left as typed, and `Cable 2 100 ft` is never `Cable 2100 ft`
(units.shown_name, docs/decisions/0021). So each item's, package's and
alias's name_shown is filled again, as 0047 filled it. No name, key or
search changes.

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-16 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from inventory_tng import units

revision: str = "0049"
down_revision: str | Sequence[str] | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    rules = {
        token: units.Rule(reading, respell)
        for token, reading, respell in bind.execute(
            sa.text("SELECT token, reading, respell FROM reading_rule")
        )
    }
    for table in ("item", "item_package", "item_alias"):
        shown = [
            {"id": row.id, "name_shown": units.shown_name(row.name, rules)}
            for row in bind.execute(sa.text(f"SELECT id, name FROM {table}"))
        ]
        if shown:
            bind.execute(
                sa.text(f"UPDATE {table} SET name_shown = :name_shown WHERE id = :id"),
                shown,
            )


def downgrade() -> None:
    # Shown as the app now shows them: left as they are.
    pass
