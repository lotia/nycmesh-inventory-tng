"""one current label for each item, package or place (inventory-tng-8i4.16)

Labels for a thing are copies of its one code, so revoking it voids every
copy. Any extra current labels a database already has are revoked first,
keeping the oldest.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-07 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# What a label names, NULLs as 0 so the index compares them.
NAMES = [
    f"coalesce({column}, 0)" for column in ("item_id", "package_id", "location_id")
]


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE label SET revoked_at = now()
        WHERE revoked_at IS NULL AND id NOT IN (
            SELECT min(id) FROM label WHERE revoked_at IS NULL GROUP BY {", ".join(NAMES)}
        )
        """
    )
    op.create_index(
        "uq_label_current",
        "label",
        [sa.text(name) for name in NAMES],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_label_current", table_name="label")
