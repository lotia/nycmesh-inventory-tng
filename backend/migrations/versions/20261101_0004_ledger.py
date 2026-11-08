"""the ledger: transactions, their movements, and the balance view

The tables are generated from models.py. The rules that need more than one
row are triggers, below; each raises a check violation (23514) named for its
rule. The ledger has no history: it is append-only, so it is its own
history. The app's login may only read and add to it: of what 0001 gives it,
UPDATE and DELETE are taken back here, and it never had TRUNCATE.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01 14:09:16.225237
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

FUNCTIONS = r"""
-- Row triggers do not fire for TRUNCATE, so it has a trigger of its own.
CREATE FUNCTION ledger_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'the ledger is append-only: record a count or an adjustment'
        USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_append_only';
END
$$;

-- The server's clock decides when a transaction happened: a client's can be
-- wrong by days, and nothing could tell. check_movement() relies on it too, so
-- an import that kept historical times would change both.
CREATE FUNCTION stamp_ledger_transaction() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.created_at := now();
    RETURN NEW;
END
$$;

-- Deferred to the commit, so the movements can follow their transaction.
CREATE FUNCTION check_ledger_transaction_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT FROM movement WHERE transaction_id = NEW.id) THEN
        RAISE EXCEPTION 'a transaction has at least one movement'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ledger_transaction_lines';
    END IF;
    RETURN NULL;
END
$$;

CREATE FUNCTION check_movement() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Written with its transaction, never added to one already recorded:
    -- now() is the time the database transaction began, and
    -- stamp_ledger_transaction() set created_at to it. A missing transaction
    -- is the foreign key's to report.
    IF (SELECT created_at <> now() FROM ledger_transaction WHERE id = NEW.transaction_id)
    THEN
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
    RETURN NEW;
END
$$;
"""

TRIGGERS = r"""
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON ledger_transaction
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON ledger_transaction
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_append_only();
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON movement
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON movement
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_append_only();
CREATE TRIGGER stamp BEFORE INSERT ON ledger_transaction
    FOR EACH ROW EXECUTE FUNCTION stamp_ledger_transaction();
CREATE CONSTRAINT TRIGGER check_lines AFTER INSERT ON ledger_transaction
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_ledger_transaction_lines();
CREATE TRIGGER check_movement BEFORE INSERT ON movement
    FOR EACH ROW EXECUTE FUNCTION check_movement();
"""

# In minus out, for every item and location stock has been at. A location
# whose stock all left keeps its row, at zero.
BALANCE = r"""
CREATE VIEW balance AS
SELECT m.item_id, side.location_id, sum(side.quantity) AS quantity
FROM movement m
JOIN ledger_transaction t ON t.id = m.transaction_id
CROSS JOIN LATERAL (
    VALUES (t.to_location_id, m.quantity), (t.from_location_id, -m.quantity)
) AS side (location_id, quantity)
WHERE side.location_id IS NOT NULL
GROUP BY m.item_id, side.location_id;
"""

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ledger_transaction",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("from_location_id", sa.Integer(), nullable=True),
        sa.Column("to_location_id", sa.Integer(), nullable=True),
        sa.Column("install_number", sa.Integer(), nullable=True),
        sa.Column("volunteer_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "CASE kind WHEN 'move' THEN num_nulls(from_location_id, to_location_id) = 0 WHEN 'receipt' THEN from_location_id IS NULL AND to_location_id IS NOT NULL WHEN 'install' THEN from_location_id IS NOT NULL AND to_location_id IS NULL ELSE num_nulls(from_location_id, to_location_id) = 1 END",
            name=op.f("ck_ledger_transaction_sides"),
        ),
        sa.CheckConstraint(
            "install_number IS NULL OR kind = 'install'",
            name=op.f("ck_ledger_transaction_install_number_on_install"),
        ),
        sa.CheckConstraint(
            "kind IN ('move', 'receipt', 'install', 'count', 'adjustment')",
            name=op.f("ck_ledger_transaction_kind"),
        ),
        sa.CheckConstraint(
            "from_location_id <> to_location_id",
            name=op.f("ck_ledger_transaction_two_places"),
        ),
        sa.CheckConstraint(
            "install_number > 0", name=op.f("ck_ledger_transaction_install_number")
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["device.id"],
            name=op.f("fk_ledger_transaction_device_id_device"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_location_id"],
            ["location.id"],
            name=op.f("fk_ledger_transaction_from_location_id_location"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_location_id"],
            ["location.id"],
            name=op.f("fk_ledger_transaction_to_location_id_location"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["volunteer_id"],
            ["volunteer.id"],
            name=op.f("fk_ledger_transaction_volunteer_id_volunteer"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_transaction")),
        sa.UniqueConstraint(
            "idempotency_key", name=op.f("uq_ledger_transaction_idempotency_key")
        ),
    )
    op.create_index(
        op.f("ix_ledger_transaction_device_id"),
        "ledger_transaction",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_from_location_id"),
        "ledger_transaction",
        ["from_location_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_to_location_id"),
        "ledger_transaction",
        ["to_location_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ledger_transaction_volunteer_id"),
        "ledger_transaction",
        ["volunteer_id"],
        unique=False,
    )
    op.create_table(
        "movement",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_movement_quantity")),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["item.id"],
            name=op.f("fk_movement_item_id_item"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["ledger_transaction.id"],
            name=op.f("fk_movement_transaction_id_ledger_transaction"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_movement")),
    )
    op.create_index(op.f("ix_movement_item_id"), "movement", ["item_id"], unique=False)
    op.create_index(
        op.f("ix_movement_transaction_id"), "movement", ["transaction_id"], unique=False
    )
    op.execute(f"REVOKE UPDATE, DELETE ON ledger_transaction, movement FROM {APP_ROLE}")
    op.execute(FUNCTIONS)
    op.execute(TRIGGERS)
    op.execute(BALANCE)


def downgrade() -> None:
    # Indexes and triggers go with their tables. DROP TABLE is not TRUNCATE,
    # so the append-only triggers do not stop it.
    op.execute("DROP VIEW balance")
    op.drop_table("movement")
    op.drop_table("ledger_transaction")
    op.execute(
        "DROP FUNCTION ledger_append_only(), stamp_ledger_transaction(), "
        "check_ledger_transaction_lines(), check_movement()"
    )
