"""baseline: what the app's login may do to the tables to come

Every table the owner creates from here on gives the app's login SELECT,
INSERT, UPDATE and DELETE, never TRUNCATE (DEPLOYMENT.md, Database roles);
0004 takes UPDATE and DELETE on the ledger back.

Revision ID: 0001
Revises:
Create Date: 2026-10-30 18:00:19.865058
"""

from collections.abc import Sequence

from alembic import op

APP_ROLE = "inventory_app"

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
        f" GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
        f" REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}"
    )
