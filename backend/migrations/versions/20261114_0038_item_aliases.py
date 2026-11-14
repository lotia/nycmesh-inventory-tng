"""item aliases: other names an item is found by, taken as names are
(inventory-tng-c0c.6)

One row per alias, keyed as items' names are, held to its name by the same
CHECK (0037), and unique among current aliases; that no alias is an item's
name, nor an item's name an alias, is the app's to check (items.py,
docs/decisions/0016). A removed alias is retired, freeing its name; nothing
else of it ever changes. Nothing to fill: no item has one yet.

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-14 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"
# 0037's, written out: migrations do not import the app.
KEY = (
    'lower(regexp_replace(name COLLATE "C",'
    " '(?!(?<=[0-9])[.][0-9])[\\t-\\r -/:-@[-`{-~]', '', 'g'))"
)


def upgrade() -> None:
    op.create_table(
        "item_alias",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"name_key = {KEY}", name=op.f("ck_item_alias_name_key")),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["item.id"],
            name=op.f("fk_item_alias_item_id_item"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_alias")),
    )
    op.create_index(op.f("ix_item_alias_item_id"), "item_alias", ["item_id"])
    op.create_index(
        "uq_item_alias_name",
        "item_alias",
        ["name_key"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    # Retiring one is the only change the app makes (0002's way).
    op.execute(f"REVOKE UPDATE ON item_alias FROM {APP_ROLE}")
    op.execute(f"GRANT UPDATE (retired_at) ON item_alias TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("item_alias")
