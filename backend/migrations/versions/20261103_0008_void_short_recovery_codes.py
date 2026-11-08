"""void short recovery codes (docs/decisions/0012)

Every recovery code still unused was made ten characters long, short enough
to guess back from a stolen database. They are void from here: their
administrators make new, longer ones from the account page, which shows
none left.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-03 09:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE recovery_code SET voided_at = now() "
        "WHERE used_at IS NULL AND voided_at IS NULL"
    )


def downgrade() -> None:
    # A void code is not made good again: nobody kept the codes to use them.
    pass
