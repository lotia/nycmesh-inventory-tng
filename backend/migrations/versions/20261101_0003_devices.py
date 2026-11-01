"""devices and registration codes (docs/decisions/0009)

Their changes go to catalogue_history like everything else, with 0002's
function, so who revoked a device or a code is recorded. Things that are not
people, such as the command line, may now be actors (system:<name>).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01 10:28:52.907841
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "registration_code",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("phrase_hash", sa.LargeBinary(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("uses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("max_uses > 0", name=op.f("ck_registration_code_max_uses")),
        sa.CheckConstraint(
            "uses BETWEEN 0 AND max_uses", name=op.f("ck_registration_code_uses")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registration_code")),
        sa.UniqueConstraint(
            "phrase_hash", name=op.f("uq_registration_code_phrase_hash")
        ),
    )
    op.create_table(
        "device",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("registration_code_id", sa.Integer(), nullable=False),
        sa.Column("first_seen_from", postgresql.INET(), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["registration_code_id"],
            ["registration_code.id"],
            name=op.f("fk_device_registration_code_id_registration_code"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_device_token_hash")),
    )
    op.create_index(
        op.f("ix_device_registration_code_id"),
        "device",
        ["registration_code_id"],
        unique=False,
    )
    # As 0002's history_triggers(), written out so this migration stays as run.
    # An update that changes only last_seen_at, which moves every hour a
    # device is used, is not history.
    for table, changed in (
        ("registration_code", "OLD IS DISTINCT FROM NEW"),
        (
            "device",
            (
                "(to_jsonb(OLD) - 'last_seen_at') IS DISTINCT FROM "
                "(to_jsonb(NEW) - 'last_seen_at')"
            ),
        ),
    ):
        op.execute(
            f"CREATE TRIGGER record_history AFTER INSERT OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION record_catalogue_history()"
        )
        op.execute(
            f"CREATE TRIGGER record_history_update AFTER UPDATE ON {table} "
            f"FOR EACH ROW WHEN ({changed}) "
            "EXECUTE FUNCTION record_catalogue_history()"
        )
    op.execute(
        "ALTER TABLE catalogue_history DROP CONSTRAINT ck_catalogue_history_actor, "
        "ADD CONSTRAINT ck_catalogue_history_actor "
        "CHECK (actor ~ '^((admin|volunteer):[0-9]+|system:[a-z_]+)$')"
    )


def downgrade() -> None:
    # NOT VALID: rows already written by the command line stay as they are.
    op.execute(
        "ALTER TABLE catalogue_history DROP CONSTRAINT ck_catalogue_history_actor, "
        "ADD CONSTRAINT ck_catalogue_history_actor "
        "CHECK (actor ~ '^(admin|volunteer):[0-9]+$') NOT VALID"
    )
    # Their indexes and triggers go with them.
    op.drop_table("device")
    op.drop_table("registration_code")
