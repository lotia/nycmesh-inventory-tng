"""an administrator asked to set up TOTP again (inventory-tng-8i4.50.2), and
when a session's administrator last signed in before it
(inventory-tng-8i4.50.10)

must_set_up_totp is set when the operator retires their authenticators
(accounts.retire_authenticators): they sign in with a recovery code and must
set one up again, whatever the TOTP policy, and not with the password alone.
signed_in_before is kept as a session starts, for "Since you last signed
in", rather than worked out on every view of the home page.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-10 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin",
        sa.Column(
            "must_set_up_totp",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    op.add_column(
        "admin_session",
        sa.Column("signed_in_before", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_session", "signed_in_before")
    op.drop_column("admin", "must_set_up_totp")
