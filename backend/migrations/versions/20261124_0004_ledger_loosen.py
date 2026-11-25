"""ledger loosen: what is left on an open package no longer followed becomes
loose units at its place, by an administrator (Ali, 2026-11-23,
inventory-tng-j89e)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24 21:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Each check on ledger_transaction this changes: as it was, and with loosen,
# as models.stock.LedgerTransaction writes it.
CHECKS = {
    "ck_ledger_transaction_admin_counts": (
        "(kind IN ('count', 'adjustment')) = (admin_id IS NOT NULL)",
        "(kind IN ('count', 'adjustment', 'loosen')) = (admin_id IS NOT NULL)",
    ),
    "ck_ledger_transaction_sides": (
        (
            "CASE kind WHEN 'move' THEN num_nulls(from_location_id, "
            "to_location_id) = 0 WHEN 'open' THEN from_location_id IS NOT NULL "
            "AND from_location_id = to_location_id WHEN 'receipt' THEN "
            "from_location_id IS NULL AND to_location_id IS NOT NULL WHEN "
            "'install' THEN from_location_id IS NOT NULL AND to_location_id IS "
            "NULL ELSE num_nulls(from_location_id, to_location_id) = 1 END"
        ),
        (
            "CASE WHEN kind IN ('open', 'loosen') THEN from_location_id IS NOT "
            "NULL AND from_location_id = to_location_id WHEN kind = 'move' THEN "
            "num_nulls(from_location_id, to_location_id) = 0 WHEN kind = "
            "'receipt' THEN from_location_id IS NULL AND to_location_id IS NOT "
            "NULL WHEN kind = 'install' THEN from_location_id IS NOT NULL AND "
            "to_location_id IS NULL ELSE num_nulls(from_location_id, "
            "to_location_id) = 1 END"
        ),
    ),
    "ck_ledger_transaction_two_places": (
        "from_location_id <> to_location_id OR kind = 'open'",
        "from_location_id <> to_location_id OR kind IN ('open', 'loosen')",
    ),
    "ck_ledger_transaction_kind": (
        (
            "kind IN ('move', 'receipt', 'install', 'count', 'adjustment', "
            "'open', 'report')"
        ),
        (
            "kind IN ('move', 'receipt', 'install', 'count', 'adjustment', "
            "'open', 'report', 'loosen')"
        ),
    ),
}


def _write(new: bool) -> None:
    for name, texts in CHECKS.items():
        op.drop_constraint(op.f(name), "ledger_transaction", type_="check")
        op.create_check_constraint(op.f(name), "ledger_transaction", texts[new])


def upgrade() -> None:
    _write(new=True)


def downgrade() -> None:
    _write(new=False)
