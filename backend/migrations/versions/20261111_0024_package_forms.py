"""sealed and open packages: a ledger line's forms, open packages, and
whether an item tracks its opened packages (inventory-tng-8i4.133.1)

A movement says the form its stock leaves in and arrives in: a sealed
package, an open package, or neither, loose (docs/decisions/0020). Opening a
package is a transaction at one place, so 'open' may name the same place on
both sides. An item, and a package overriding it, says whether opening one
makes an open package, followed until used up, or loose units.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-11 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS_BEFORE = "kind IN ('move', 'receipt', 'install', 'count', 'adjustment')"
KINDS = "kind IN ('move', 'receipt', 'install', 'count', 'adjustment', 'open')"
SIDES_BEFORE = (
    "CASE kind"
    " WHEN 'move' THEN num_nulls(from_location_id, to_location_id) = 0"
    " WHEN 'receipt' THEN from_location_id IS NULL AND to_location_id IS NOT NULL"
    " WHEN 'install' THEN from_location_id IS NOT NULL AND to_location_id IS NULL"
    " ELSE num_nulls(from_location_id, to_location_id) = 1 END"
)
SIDES = (
    "CASE kind"
    " WHEN 'move' THEN num_nulls(from_location_id, to_location_id) = 0"
    " WHEN 'open' THEN from_location_id IS NOT NULL"
    " AND from_location_id = to_location_id"
    " WHEN 'receipt' THEN from_location_id IS NULL AND to_location_id IS NOT NULL"
    " WHEN 'install' THEN from_location_id IS NOT NULL AND to_location_id IS NULL"
    " ELSE num_nulls(from_location_id, to_location_id) = 1 END"
)
LINE = [
    "transaction_id",
    "item_id",
    sa.text("coalesce(from_package_id, 0)"),
    sa.text("coalesce(from_open_package_id, 0)"),
    sa.text("coalesce(to_package_id, 0)"),
    sa.text("coalesce(to_open_package_id, 0)"),
]


def _checks(table: str, checks: dict[str, str]) -> None:
    for name, condition in checks.items():
        op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, condition)


def upgrade() -> None:
    op.add_column(
        "item",
        sa.Column(
            "track_opened",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column("item_package", sa.Column("track_opened", sa.Boolean()))
    op.create_table(
        "open_package",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("key", sa.Uuid(), nullable=False),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_by_volunteer_id", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_admin_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(reported_at IS NULL) = (reported_by_volunteer_id IS NULL)",
            name=op.f("ck_open_package_reported_by"),
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL) = (closed_by_admin_id IS NULL)",
            name=op.f("ck_open_package_closed_by"),
        ),
        *(
            sa.ForeignKeyConstraint(
                [column],
                [f"{target}.id"],
                name=op.f(f"fk_open_package_{column}_{target}"),
                ondelete="RESTRICT",
            )
            for column, target in (
                ("item_id", "item"),
                ("package_id", "item_package"),
                ("location_id", "location"),
                ("reported_by_volunteer_id", "volunteer"),
                ("closed_by_admin_id", "admin"),
            )
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_open_package")),
        sa.UniqueConstraint("key", name=op.f("uq_open_package_key")),
    )
    for column in (
        "item_id",
        "package_id",
        "location_id",
        "reported_by_volunteer_id",
        "closed_by_admin_id",
    ):
        op.create_index(op.f(f"ix_open_package_{column}"), "open_package", [column])
    for column, target in (
        ("from_package_id", "item_package"),
        ("from_open_package_id", "open_package"),
        ("to_package_id", "item_package"),
        ("to_open_package_id", "open_package"),
    ):
        op.add_column("movement", sa.Column(column, sa.Integer(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_movement_{column}_{target}"),
            "movement",
            target,
            [column],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(op.f(f"ix_movement_{column}"), "movement", [column])
    op.create_check_constraint(
        op.f("ck_movement_one_form_leaving"),
        "movement",
        "num_nonnulls(from_package_id, from_open_package_id) <= 1",
    )
    op.create_check_constraint(
        op.f("ck_movement_one_form_arriving"),
        "movement",
        "num_nonnulls(to_package_id, to_open_package_id) <= 1",
    )
    op.create_index("uq_movement_line", "movement", LINE, unique=True)
    op.drop_index("uq_movement_transaction_id_item_id", table_name="movement")
    # Only what changes: the settings, and where an open package is, who
    # reported it used up and who closed it.
    op.execute(f"GRANT UPDATE (track_opened) ON item_package TO {APP_ROLE}")
    op.execute(f"REVOKE UPDATE ON open_package FROM {APP_ROLE}")
    op.execute(
        "GRANT UPDATE (location_id, reported_at, reported_by_volunteer_id,"
        f" closed_at, closed_by_admin_id) ON open_package TO {APP_ROLE}"
    )
    # The reason closing an open package gives, beside 0009's.
    op.execute(
        "INSERT INTO adjustment_reason (name) SELECT 'Used up' WHERE NOT EXISTS"
        " (SELECT 1 FROM adjustment_reason WHERE lower(name) = 'used up')"
    )
    _checks(
        "ledger_transaction",
        {
            "kind": KINDS,
            "sides": SIDES,
            "two_places": "from_location_id <> to_location_id OR kind = 'open'",
        },
    )


def downgrade() -> None:
    twice = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT DISTINCT transaction_id FROM movement m"
                " JOIN ledger_transaction t ON t.id = m.transaction_id"
                " WHERE t.kind <> 'open'"
                " GROUP BY transaction_id, item_id HAVING count(*) > 1"
                " ORDER BY transaction_id"
            )
        )
        .scalars()
        .all()
    )
    if twice:
        # Before 0024 an item was on one line of a transaction.
        raise RuntimeError(
            "Transactions with an item on two lines, in different forms, by id: "
            f"{', '.join(map(str, twice))}. 0023 cannot hold them."
        )
    op.execute(
        "DELETE FROM movement USING ledger_transaction"
        " WHERE movement.transaction_id = ledger_transaction.id"
        " AND ledger_transaction.kind = 'open'"
    )
    op.execute("DELETE FROM ledger_transaction WHERE kind = 'open'")
    _checks(
        "ledger_transaction",
        {
            "kind": KINDS_BEFORE,
            "sides": SIDES_BEFORE,
            "two_places": "from_location_id <> to_location_id",
        },
    )
    op.create_index(
        "uq_movement_transaction_id_item_id",
        "movement",
        ["transaction_id", "item_id"],
        unique=True,
    )
    op.drop_index("uq_movement_line", table_name="movement")
    op.drop_constraint(op.f("ck_movement_one_form_arriving"), "movement", "check")
    op.drop_constraint(op.f("ck_movement_one_form_leaving"), "movement", "check")
    for column in (
        "to_open_package_id",
        "to_package_id",
        "from_open_package_id",
        "from_package_id",
    ):
        op.drop_index(op.f(f"ix_movement_{column}"), table_name="movement")
        op.drop_column("movement", column)
    op.drop_table("open_package")
    op.drop_column("item_package", "track_opened")
    op.drop_column("item", "track_opened")
