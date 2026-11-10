"""how long a code covers changes to other administrators
(inventory-tng-8i4.50.11)

Inviting, resetting, retiring or bringing back an administrator needs a
code typed within sign_in_settings.account_change_minutes: 0 to 60, 30 by
default.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-10 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sign_in_settings",
        sa.Column(
            "account_change_minutes",
            sa.Integer(),
            server_default=sa.text("30"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_sign_in_settings_account_change_minutes"),
        "sign_in_settings",
        "account_change_minutes BETWEEN 0 AND 60",
    )


def downgrade() -> None:
    op.drop_column("sign_in_settings", "account_change_minutes")
