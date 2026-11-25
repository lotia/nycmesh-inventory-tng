"""stored amount units: every amount stored apart from its item records its
unit beside it, ledger lines included (Ali, 2026-11-24,
inventory-tng-2rlr)

A new migration, not a rewrite of the initial one, so a database already
at head keeps its data (coordinator, 2026-11-25). Each unit is filled from
its item's as it is now: exact, as an item's unit is fixed once stock has
moved in it or a label names it (catalogue/items.unit_fixed).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-25 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "inventory_app"
# models.catalogue.UNITS, written out so the checks keep the units they
# were made with.
UNITS = "('each', 'm', 'ft')"
# The tables whose rows name their item, so take its unit; an
# over_recorded row takes its movement's.
FILLED_FROM_ITEM = ("movement", "item_package", "vendor_offer")
# The check on what a volunteer said of an open package: as it was, and
# with its unit, as models.stock.OpenPackage writes it.
LENGTH_REPORTED_BY_BEFORE = (
    "num_nulls(length_reported_at, length_reported_by_volunteer_id,"
    " length_reported, length_reported_in_id) IN (0, 4)"
)
LENGTH_REPORTED_BY_WITH_UNIT = (
    "num_nulls(length_reported_at, length_reported_by_volunteer_id,"
    " length_reported, length_reported_unit, length_reported_in_id) IN (0, 5)"
)


def _unit_column(table: str, column: str = "unit") -> None:
    op.add_column(table, sa.Column(column, sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f(f"ck_{table}_{column}"), table, f"{column} IN {UNITS}"
    )


def upgrade() -> None:
    for table in FILLED_FROM_ITEM:
        _unit_column(table)
        op.execute(
            f"UPDATE {table} SET unit = item.unit FROM item"
            f" WHERE item.id = {table}.item_id"
        )
        op.alter_column(table, "unit", nullable=False)
    _unit_column("over_recorded")
    op.execute(
        "UPDATE over_recorded SET unit = movement.unit FROM movement"
        " WHERE movement.id = over_recorded.movement_id"
    )
    op.alter_column("over_recorded", "unit", nullable=False)
    _unit_column("open_package", "length_reported_unit")
    op.execute(
        "UPDATE open_package SET length_reported_unit = item.unit FROM item"
        " WHERE item.id = open_package.item_id"
        " AND open_package.length_reported IS NOT NULL"
    )
    _length_reported_by(LENGTH_REPORTED_BY_WITH_UNIT)
    # Among the only columns the app's login may update in each (the
    # initial migration's UPDATED_ONLY): a package's unit follows its
    # item's while that may change (catalogue/items.save).
    op.execute(f"GRANT UPDATE (unit) ON item_package TO {APP_ROLE}")
    op.execute(f"GRANT UPDATE (length_reported_unit) ON open_package TO {APP_ROLE}")


def _length_reported_by(check: str) -> None:
    name = op.f("ck_open_package_length_reported_by")
    op.drop_constraint(name, "open_package", type_="check")
    op.create_check_constraint(name, "open_package", check)


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE (length_reported_unit) ON open_package FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE (unit) ON item_package FROM {APP_ROLE}")
    _length_reported_by(LENGTH_REPORTED_BY_BEFORE)
    op.drop_column("open_package", "length_reported_unit")
    for table in ("over_recorded", *FILLED_FROM_ITEM):
        op.drop_column(table, "unit")
