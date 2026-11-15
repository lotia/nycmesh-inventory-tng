"""reading rules, and names unique once their units are read
(inventory-tng-c0c.4, Ali, 2026-11-12 and 2026-11-15)

reading_rule keeps how a unit, as typed after a number, is read, or that
it is no unit, as an administrator settled it (models.ReadingRule).
Nothing to fill: no rule is settled yet.

Items, packages and aliases gain unit_key, the key of the name with each
unit after a number spelt by what it reads as (models.unit_key), which
the app writes, as no CHECK can (the unit table is the app's,
docs/decisions/0021): filled here as the app reads names with no rules,
importing it as 0036 does. Names that would then be one, among items,
among an item's packages, among current aliases, or an alias and an
item's name, are named by id first, to rename, and nothing is changed
until none are. A package's and an alias's unit_key change when a rule
does, so the app may update them (0002 and 0038 grant theirs by column).

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-15 04:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from inventory_tng.models import unit_key

revision: str = "0042"
down_revision: str | Sequence[str] | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"
TABLES = ("item", "item_package", "item_alias")


def upgrade() -> None:
    op.create_table(
        "reading_rule",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("edit_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("reading", sa.Text(), nullable=True),
        sa.Column("respell", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("decided_by_admin_id", sa.Integer(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reading IS NOT NULL OR NOT respell",
            name=op.f("ck_reading_rule_respell_a_unit"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_admin_id"],
            ["admin.id"],
            name=op.f("fk_reading_rule_decided_by_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reading_rule")),
    )
    op.create_index(
        op.f("ix_reading_rule_decided_by_admin_id"),
        "reading_rule",
        ["decided_by_admin_id"],
    )
    op.create_index("uq_reading_rule_token", "reading_rule", ["token"], unique=True)

    bind = op.get_bind()
    for table in TABLES:
        op.add_column(table, sa.Column("unit_key", sa.Text(), nullable=True))
        keyed = [
            {"id": row.id, "unit_key": unit_key(row.name)}
            for row in bind.execute(sa.text(f"SELECT id, name FROM {table}"))
        ]
        if keyed:
            bind.execute(
                sa.text(f"UPDATE {table} SET unit_key = :unit_key WHERE id = :id"),
                keyed,
            )
        op.alter_column(table, "unit_key", nullable=False)
    _refuse_clashes()
    op.create_index("uq_item_unit_key", "item", ["unit_key"], unique=True)
    op.create_index(
        "uq_item_package_unit_key",
        "item_package",
        ["item_id", "unit_key"],
        unique=True,
    )
    op.create_index(
        "uq_item_alias_unit_key",
        "item_alias",
        ["unit_key"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    for table in ("item_package", "item_alias"):
        op.execute(f"GRANT UPDATE (unit_key) ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "unit_key")
    op.drop_table("reading_rule")


def _refuse_clashes() -> None:
    """RuntimeError naming by id the rows whose names read alike once their
    units are read, if any."""
    bind = op.get_bind()
    groups = (
        ("item", "FROM item GROUP BY unit_key"),
        ("item_package", "FROM item_package GROUP BY item_id, unit_key"),
        ("item_alias", "FROM item_alias WHERE retired_at IS NULL GROUP BY unit_key"),
    )
    clashing = [
        *(
            f"{table} {', '.join(map(str, ids))}"
            for table, rows in groups
            for ids in bind.execute(
                sa.text(f"SELECT array_agg(id ORDER BY id) {rows} HAVING count(*) > 1")
            ).scalars()
        ),
        *(
            f"item_alias {alias} and item {item}"
            for alias, item in bind.execute(
                sa.text(
                    "SELECT a.id, i.id FROM item_alias a JOIN item i"
                    " ON a.unit_key = i.unit_key"
                    " WHERE a.retired_at IS NULL ORDER BY a.id, i.id"
                )
            )
        ),
    ]
    if clashing:
        raise RuntimeError(
            "Names differing only in how their units are spelt would be one"
            f" name, by id: {'; '.join(clashing)}. Rename all but one in each,"
            " then migrate again."
        )
