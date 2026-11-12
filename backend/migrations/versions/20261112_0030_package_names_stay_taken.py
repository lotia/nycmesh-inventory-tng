"""package names unique among all of an item's packages, retired ones too,
compared as items' names are; a version for the form that renames one; and
a whole one never a short end, as its size can change (inventory-tng-f4f)

A retired package can be brought back, so its name stays taken
(docs/decisions/0019, 0021). Packages already clashing so are named by id,
to rename first: the index cannot be made over them.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-12 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"
# models.name_key's, written out, as 0023's.
KEY = "lower(regexp_replace(name, '[[:space:][:punct:]]+', '', 'g'))"


def upgrade() -> None:
    clashing = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT array_agg(id ORDER BY id) FROM item_package"
                f" GROUP BY item_id, {KEY} HAVING count(*) > 1"
            )
        )
        .scalars()
        .all()
    )
    if clashing:
        groups = "; ".join(", ".join(map(str, ids)) for ids in clashing)
        raise RuntimeError(
            "Packages of one item whose names differ only by case, spaces or "
            f"punctuation, retired ones included, by id: {groups}. Rename all "
            "but one in each, then upgrade again."
        )
    too_short = (
        op.get_bind()
        .execute(
            sa.text("SELECT id FROM item_package WHERE short_below >= size ORDER BY id")
        )
        .scalars()
        .all()
    )
    if too_short:
        raise RuntimeError(
            "Packages whose too-short length is a whole one or more, by id: "
            f"{', '.join(map(str, too_short))}. Lower it, then upgrade again."
        )
    op.drop_index("uq_item_package_name", table_name="item_package")
    op.create_index(
        "uq_item_package_name",
        "item_package",
        [sa.literal_column("item_id"), sa.literal_column(KEY)],
        unique=True,
    )
    op.add_column(
        "item_package",
        sa.Column(
            "edit_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
    )
    op.execute(f"GRANT UPDATE (edit_version) ON item_package TO {APP_ROLE}")
    op.create_check_constraint(
        op.f("ck_item_package_short_below_under_size"),
        "item_package",
        "short_below < size",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_item_package_short_below_under_size"), "item_package", type_="check"
    )
    op.drop_column("item_package", "edit_version")
    op.drop_index("uq_item_package_name", table_name="item_package")
    op.create_index(
        "uq_item_package_name",
        "item_package",
        [sa.literal_column("item_id"), sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
