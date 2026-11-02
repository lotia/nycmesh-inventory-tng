"""site settings: the local time zone (docs/decisions/0013)

One row, made here with the defaults, changed but never deleted. A trigger
refuses a time zone PostgreSQL does not know, and 0006's settings_row_kept()
refuses deleting the row. Its changes go to catalogue_history.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02 13:36:17.108220
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULES = r"""
CREATE FUNCTION check_site_settings() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_timezone_names WHERE name = NEW.time_zone) THEN
        RAISE EXCEPTION 'not a time zone PostgreSQL knows'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'site_settings_time_zone';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER check_site_settings BEFORE INSERT OR UPDATE OF time_zone
    ON site_settings FOR EACH ROW EXECUTE FUNCTION check_site_settings();

CREATE TRIGGER site_settings_kept BEFORE DELETE ON site_settings
    FOR EACH ROW EXECUTE FUNCTION settings_row_kept();
CREATE TRIGGER site_settings_kept_truncate BEFORE TRUNCATE ON site_settings
    FOR EACH STATEMENT EXECUTE FUNCTION settings_row_kept();

CREATE TRIGGER record_history AFTER INSERT OR DELETE ON site_settings
    FOR EACH ROW EXECUTE FUNCTION record_catalogue_history();
CREATE TRIGGER record_history_update AFTER UPDATE ON site_settings
    FOR EACH ROW WHEN (OLD IS DISTINCT FROM NEW)
    EXECUTE FUNCTION record_catalogue_history();
"""


def upgrade() -> None:
    op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "time_zone", sa.Text(), server_default="America/New_York", nullable=False
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_site_settings_one_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_settings")),
    )
    op.execute(RULES)
    op.execute("SELECT set_config('inventory.actor', 'system:migration', true)")
    op.execute("INSERT INTO site_settings DEFAULT VALUES")


def downgrade() -> None:
    # Its triggers go with it; its history stays.
    op.drop_table("site_settings")
    op.execute("DROP FUNCTION check_site_settings()")
