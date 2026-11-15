"""aliases' names folded, for close names (inventory-tng-c0c.6, Ali,
2026-11-15)

The question whether a new name is a different item lists items whose
aliases are close too (items.similar, docs/decisions/0021), compared as
an item's name_folded is: so each alias keeps its name folded, as the app
folds it (models.fold), importing it as 0036 does. Every alias, retired
ones too, so none is left empty; an alias's name never changes, so the app
sets it once, as the alias is added.

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-15 03:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from inventory_tng.models import fold

revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("item_alias", sa.Column("name_folded", sa.Text(), nullable=True))
    bind = op.get_bind()
    folded = [
        {"id": row.id, "name_folded": fold(row.name)}
        for row in bind.execute(sa.text("SELECT id, name FROM item_alias"))
    ]
    if folded:
        bind.execute(
            sa.text("UPDATE item_alias SET name_folded = :name_folded WHERE id = :id"),
            folded,
        )
    op.alter_column("item_alias", "name_folded", nullable=False)


def downgrade() -> None:
    op.drop_column("item_alias", "name_folded")
