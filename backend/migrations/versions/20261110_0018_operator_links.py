"""links the operator's commands make (inventory-tng-8i4.50.2)

An invite or a reset link may be made by nobody, by python -m
inventory_tng.admins let-in or invite (docs/decisions/0018): made_by_id NULL.
Such a link lasts an hour.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-10 15:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _made_by(rule: str) -> None:
    op.drop_constraint(op.f("ck_reset_link_made_by"), "reset_link", type_="check")
    op.create_check_constraint(op.f("ck_reset_link_made_by"), "reset_link", rule)


def upgrade() -> None:
    _made_by("made_by_id IS NULL OR kind IN ('invite', 'reset')")


def downgrade() -> None:
    # 0006 requires a maker for every invite and reset.
    op.execute(
        "DELETE FROM reset_link WHERE made_by_id IS NULL"
        " AND kind IN ('invite', 'reset')"
    )
    _made_by("(made_by_id IS NOT NULL) = (kind IN ('invite', 'reset'))")
