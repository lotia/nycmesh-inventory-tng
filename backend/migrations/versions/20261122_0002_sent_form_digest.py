"""sent_form digest: what a count form saved said, so one sent again
otherwise is refused (Ali, 2026-11-22, inventory-tng-kro9)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22 15:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sent_form", sa.Column("digest", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("sent_form", "digest")
