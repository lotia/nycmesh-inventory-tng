"""scan settings: how the scan page behaves, set by administrators

One row, made here with the defaults, which may be changed but never
deleted: every scan page reads it. Its changes go to catalogue_history, with
0002's function, so who changed a setting is recorded.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02 03:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEPT = r"""
CREATE FUNCTION scan_settings_kept() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'the scan settings are changed, never deleted'
        USING ERRCODE = 'check_violation', CONSTRAINT = 'scan_settings_kept';
END
$$;
CREATE TRIGGER scan_settings_kept BEFORE DELETE ON scan_settings
    FOR EACH ROW EXECUTE FUNCTION scan_settings_kept();
CREATE TRIGGER scan_settings_kept_truncate BEFORE TRUNCATE ON scan_settings
    FOR EACH STATEMENT EXECUTE FUNCTION scan_settings_kept();
"""


def upgrade() -> None:
    op.create_table(
        "scan_settings",
        sa.Column("id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "name_check_minutes",
            sa.Integer(),
            server_default=sa.text("15"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_scan_settings_one_row")),
        sa.CheckConstraint(
            "name_check_minutes >= 0",
            name=op.f("ck_scan_settings_name_check_minutes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_settings")),
    )
    op.execute(
        "CREATE TRIGGER record_history AFTER INSERT OR DELETE ON scan_settings "
        "FOR EACH ROW EXECUTE FUNCTION record_catalogue_history()"
    )
    op.execute(
        "CREATE TRIGGER record_history_update AFTER UPDATE ON scan_settings "
        "FOR EACH ROW WHEN (OLD.* IS DISTINCT FROM NEW.*) "
        "EXECUTE FUNCTION record_catalogue_history()"
    )
    op.execute(KEPT)
    op.execute("SELECT set_config('inventory.actor', 'system:migration', true)")
    op.execute("INSERT INTO scan_settings DEFAULT VALUES")


def downgrade() -> None:
    # Its triggers go with it; its history stays.
    op.drop_table("scan_settings")
    op.execute("DROP FUNCTION scan_settings_kept()")
