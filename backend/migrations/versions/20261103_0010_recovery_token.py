"""a reset link made with the operator's recovery token (inventory-tng-8i4.12.7)

With RECOVERY_TOKEN set, /recover makes a reset link for the password and
authenticators, made by no administrator. The token's hash is kept on the
link, unique, so each token works once.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-03 07:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reset_link", sa.Column("recovery_token_hash", sa.LargeBinary(), nullable=True)
    )
    op.create_unique_constraint(
        op.f("uq_reset_link_recovery_token_hash"), "reset_link", ["recovery_token_hash"]
    )
    op.drop_constraint(op.f("ck_reset_link_kind"), "reset_link", type_="check")
    op.create_check_constraint(
        op.f("ck_reset_link_kind"),
        "reset_link",
        "kind IN ('invite', 'emailed', 'reset', 'recovery')",
    )
    op.create_check_constraint(
        op.f("ck_reset_link_recovery"),
        "reset_link",
        "(recovery_token_hash IS NOT NULL) = (kind = 'recovery')",
    )


def downgrade() -> None:
    # 0006 has no recovery kind.
    op.execute("DELETE FROM reset_link WHERE kind = 'recovery'")
    op.drop_constraint(op.f("ck_reset_link_recovery"), "reset_link", type_="check")
    op.drop_constraint(op.f("ck_reset_link_kind"), "reset_link", type_="check")
    op.create_check_constraint(
        op.f("ck_reset_link_kind"),
        "reset_link",
        "kind IN ('invite', 'emailed', 'reset')",
    )
    op.drop_constraint(
        op.f("uq_reset_link_recovery_token_hash"), "reset_link", type_="unique"
    )
    op.drop_column("reset_link", "recovery_token_hash")
