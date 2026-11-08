"""counts and adjustments (inventory-tng-8i4.14.8)

Counts and adjustments are an administrator's: admin_id says whose, and an
adjustment's reason_id why, from adjustment_reason, which administrators
extend and which starts with the reasons below. A count that finds what the
records say is written too, with no lines, so when a place was last counted
is known. A retired item is never received: what is left of it may only go
out (docs/decisions/0003).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-02 15:47:43.492571
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASONS = (
    "Damaged",
    "Lost or stolen",
    "Found",
    "Used for testing",
    "Records were wrong",
)

# 0004's, but a count may have no lines.
LINES = r"""
CREATE OR REPLACE FUNCTION check_ledger_transaction_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind <> 'count'
        AND NOT EXISTS (SELECT FROM movement WHERE transaction_id = NEW.id)
    THEN
        RAISE EXCEPTION 'a transaction has at least one movement'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_transaction_lines';
    END IF;
    RETURN NULL;
END
$$;
"""
# 0004's check_movement(), which also refuses to receive a retired item: a
# retired item is never received (docs/decisions/0003). One look at the
# transaction serves both rules.
MOVEMENT = r"""
CREATE OR REPLACE FUNCTION check_movement() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    written timestamptz;
    kind text;
BEGIN
    SELECT t.created_at, t.kind INTO written, kind
    FROM ledger_transaction t WHERE t.id = NEW.transaction_id;
    -- Written with its transaction, never added to one already recorded:
    -- now() is the time the database transaction began, and
    -- stamp_ledger_transaction() set created_at to it. A missing transaction
    -- is the foreign key's to report.
    IF written <> now() THEN
        RAISE EXCEPTION 'movements are written with their transaction'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'movement_with_transaction';
    END IF;
    -- FOR SHARE: an item's unit cannot change under a movement being
    -- written, and items.save sees the movement once it commits.
    IF NEW.quantity <> trunc(NEW.quantity)
        AND (SELECT unit FROM item WHERE id = NEW.item_id FOR SHARE) = 'each'
    THEN
        RAISE EXCEPTION 'an item counted in each moves in whole units'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'movement_whole_units';
    END IF;
    IF kind = 'receipt'
        AND (SELECT retired_at IS NOT NULL FROM item WHERE id = NEW.item_id)
    THEN
        RAISE EXCEPTION 'a retired item is never received'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'receipt_retired_item';
    END IF;
    RETURN NEW;
END
$$;
"""
MOVEMENT_0004 = r"""
CREATE OR REPLACE FUNCTION check_movement() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT created_at <> now() FROM ledger_transaction WHERE id = NEW.transaction_id)
    THEN
        RAISE EXCEPTION 'movements are written with their transaction'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'movement_with_transaction';
    END IF;
    IF NEW.quantity <> trunc(NEW.quantity)
        AND (SELECT unit FROM item WHERE id = NEW.item_id FOR SHARE) = 'each'
    THEN
        RAISE EXCEPTION 'an item counted in each moves in whole units'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'movement_whole_units';
    END IF;
    RETURN NEW;
END
$$;
"""

# 0004's balance view, written so a filter on a place or an item reaches the
# indexes on each side, instead of summing the whole ledger first.
BALANCE = r"""
CREATE OR REPLACE VIEW balance AS
SELECT item_id, location_id, sum(quantity) AS quantity
FROM (
    SELECT m.item_id, t.to_location_id AS location_id, m.quantity
    FROM movement m JOIN ledger_transaction t ON t.id = m.transaction_id
    WHERE t.to_location_id IS NOT NULL
    UNION ALL
    SELECT m.item_id, t.from_location_id, -m.quantity
    FROM movement m JOIN ledger_transaction t ON t.id = m.transaction_id
    WHERE t.from_location_id IS NOT NULL
) AS side
GROUP BY item_id, location_id;
"""
BALANCE_0004 = r"""
CREATE OR REPLACE VIEW balance AS
SELECT m.item_id, side.location_id, sum(side.quantity) AS quantity
FROM movement m
JOIN ledger_transaction t ON t.id = m.transaction_id
CROSS JOIN LATERAL (
    VALUES (t.to_location_id, m.quantity), (t.from_location_id, -m.quantity)
) AS side (location_id, quantity)
WHERE side.location_id IS NOT NULL
GROUP BY m.item_id, side.location_id;
"""

LINES_0004 = r"""
CREATE OR REPLACE FUNCTION check_ledger_transaction_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT FROM movement WHERE transaction_id = NEW.id) THEN
        RAISE EXCEPTION 'a transaction has at least one movement'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_transaction_lines';
    END IF;
    RETURN NULL;
END
$$;
"""


def upgrade() -> None:
    op.create_table(
        "adjustment_reason",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_adjustment_reason")),
    )
    op.create_index(
        "uq_adjustment_reason_name",
        "adjustment_reason",
        [sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.add_column(
        "ledger_transaction", sa.Column("admin_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "ledger_transaction", sa.Column("reason_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        op.f("ix_ledger_transaction_admin_id"),
        "ledger_transaction",
        ["admin_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_reason_id"),
        "ledger_transaction",
        ["reason_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_ledger_transaction_reason_id_adjustment_reason"),
        "ledger_transaction",
        "adjustment_reason",
        ["reason_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_ledger_transaction_admin_id_admin"),
        "ledger_transaction",
        "admin",
        ["admin_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_ledger_transaction_admin_counts"),
        "ledger_transaction",
        "(kind IN ('count', 'adjustment')) = (admin_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_ledger_transaction_adjustment_reason"),
        "ledger_transaction",
        "(kind = 'adjustment') = (reason_id IS NOT NULL)",
    )
    op.execute(LINES)
    op.execute(MOVEMENT)
    op.execute(BALANCE)
    reasons = sa.table("adjustment_reason", sa.column("name", sa.Text()))
    op.bulk_insert(reasons, [{"name": name} for name in REASONS])


def downgrade() -> None:
    op.execute(BALANCE_0004)
    op.execute(MOVEMENT_0004)
    op.execute(LINES_0004)
    op.drop_constraint(
        op.f("ck_ledger_transaction_adjustment_reason"), "ledger_transaction"
    )
    op.drop_constraint(op.f("ck_ledger_transaction_admin_counts"), "ledger_transaction")
    op.drop_constraint(
        op.f("fk_ledger_transaction_admin_id_admin"),
        "ledger_transaction",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_ledger_transaction_reason_id_adjustment_reason"),
        "ledger_transaction",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_ledger_transaction_reason_id"), table_name="ledger_transaction"
    )
    op.drop_index(
        op.f("ix_ledger_transaction_admin_id"), table_name="ledger_transaction"
    )
    op.drop_column("ledger_transaction", "reason_id")
    op.drop_column("ledger_transaction", "admin_id")
    op.drop_index(
        "uq_adjustment_reason_name",
        table_name="adjustment_reason",
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.drop_table("adjustment_reason")
