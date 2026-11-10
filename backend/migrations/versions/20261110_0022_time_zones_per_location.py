"""a time zone per location, and the zone each ledger event happened in
(inventory-tng-dp5)

A location may have a zone of its own, else its parent's, else the site's
(local_time.zone_at). Each ledger transaction keeps the zone of its place as
it was written, so a zone set later does not change the clock it happened
at. Transactions already written happened in the site's zone.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-10 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"


def upgrade() -> None:
    op.add_column("location", sa.Column("time_zone", sa.Text(), nullable=True))
    # 0002 grants the app the columns it may change, one by one.
    op.execute(f"GRANT UPDATE (time_zone) ON location TO {APP_ROLE}")
    op.add_column("ledger_transaction", sa.Column("time_zone", sa.Text()))
    op.execute(
        "UPDATE ledger_transaction"
        " SET time_zone = (SELECT time_zone FROM site_settings WHERE id = 1)"
    )
    op.alter_column("ledger_transaction", "time_zone", nullable=False)


def downgrade() -> None:
    op.drop_column("ledger_transaction", "time_zone")
    op.drop_column("location", "time_zone")
