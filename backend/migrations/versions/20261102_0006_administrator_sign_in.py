"""administrator sign-in (docs/decisions/0012)

Administrators, their passwords, authenticators, recovery codes, sessions and
reset links, and the one-row sign_in_settings, made here with its defaults.
The app's login may change its row but not delete it.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02 11:56:26.535957
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0001's: migrations do not import one another.
APP_ROLE = "inventory_app"


def upgrade() -> None:
    op.create_table(
        "admin",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "email ~ '^[^@[:space:]]+@[^@[:space:]]+$' AND length(email) <= 254",
            name=op.f("ck_admin_email"),
        ),
        sa.CheckConstraint(
            "username ~ '^[A-Za-z0-9._-]{1,64}$'", name=op.f("ck_admin_username")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin")),
    )
    op.create_index(
        "uq_admin_email",
        "admin",
        [sa.literal_column("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_index(
        "uq_admin_username",
        "admin",
        [sa.literal_column("lower(username)")],
        unique=True,
    )
    op.create_table(
        "sign_in_settings",
        sa.Column("id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("totp_policy", sa.Text(), server_default="required", nullable=False),
        sa.Column(
            "session_days", sa.Integer(), server_default=sa.text("7"), nullable=False
        ),
        sa.Column(
            "confirm_hours", sa.Integer(), server_default=sa.text("24"), nullable=False
        ),
        sa.Column(
            "confirm_idle_minutes",
            sa.Integer(),
            server_default=sa.text("60"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "totp_policy IN ('required', 'optional')",
            name=op.f("ck_sign_in_settings_totp_policy"),
        ),
        sa.CheckConstraint(
            "confirm_hours BETWEEN 1 AND 168",
            name=op.f("ck_sign_in_settings_confirm_hours"),
        ),
        sa.CheckConstraint(
            "confirm_idle_minutes BETWEEN 1 AND 10080",
            name=op.f("ck_sign_in_settings_confirm_idle_minutes"),
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_sign_in_settings_one_row")),
        sa.CheckConstraint(
            "session_days BETWEEN 1 AND 400",
            name=op.f("ck_sign_in_settings_session_days"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sign_in_settings")),
    )
    op.create_table(
        "admin_password",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["id"],
            ["admin.id"],
            name=op.f("fk_admin_password_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_password")),
    )
    op.create_table(
        "admin_session",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sensitive_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin.id"],
            name=op.f("fk_admin_session_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_session")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_admin_session_token_hash")),
    )
    op.create_index(
        op.f("ix_admin_session_admin_id"), "admin_session", ["admin_id"], unique=False
    )
    op.create_table(
        "authenticator",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("totp_key", sa.Text(), nullable=True),
        sa.Column("last_step", sa.BigInteger(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(totp_key IS NULL) = (retired_at IS NOT NULL)",
            name=op.f("ck_authenticator_key_until_retired"),
        ),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 64", name=op.f("ck_authenticator_name")
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin.id"],
            name=op.f("fk_authenticator_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authenticator")),
    )
    op.create_index(
        op.f("ix_authenticator_admin_id"), "authenticator", ["admin_id"], unique=False
    )
    op.create_index(
        "uq_authenticator_name",
        "authenticator",
        [sa.literal_column("admin_id"), sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_table(
        "recovery_code",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.LargeBinary(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin.id"],
            name=op.f("fk_recovery_code_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recovery_code")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_recovery_code_code_hash")),
    )
    op.create_index(
        op.f("ix_recovery_code_admin_id"), "recovery_code", ["admin_id"], unique=False
    )
    op.create_table(
        "reset_link",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("made_by_id", sa.Integer(), nullable=True),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "made_by_id <> admin_id", name=op.f("ck_reset_link_not_made_for_oneself")
        ),
        sa.CheckConstraint(
            "kind IN ('invite', 'emailed', 'reset')", name=op.f("ck_reset_link_kind")
        ),
        sa.CheckConstraint(
            "(made_by_id IS NOT NULL) = (kind IN ('invite', 'reset'))",
            name=op.f("ck_reset_link_made_by"),
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin.id"],
            name=op.f("fk_reset_link_admin_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["made_by_id"],
            ["admin.id"],
            name=op.f("fk_reset_link_made_by_id_admin"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reset_link")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_reset_link_token_hash")),
    )
    op.create_index(
        op.f("ix_reset_link_admin_id"), "reset_link", ["admin_id"], unique=False
    )
    op.create_index(
        op.f("ix_reset_link_made_by_id"), "reset_link", ["made_by_id"], unique=False
    )
    op.execute("INSERT INTO sign_in_settings DEFAULT VALUES")
    op.execute(f"REVOKE DELETE ON sign_in_settings FROM {APP_ROLE}")


def downgrade() -> None:
    # Their history stays.
    op.drop_index(op.f("ix_reset_link_made_by_id"), table_name="reset_link")
    op.drop_index(op.f("ix_reset_link_admin_id"), table_name="reset_link")
    op.drop_table("reset_link")
    op.drop_index(op.f("ix_recovery_code_admin_id"), table_name="recovery_code")
    op.drop_table("recovery_code")
    op.drop_index(
        "uq_authenticator_name",
        table_name="authenticator",
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.drop_index(op.f("ix_authenticator_admin_id"), table_name="authenticator")
    op.drop_table("authenticator")
    op.drop_index(op.f("ix_admin_session_admin_id"), table_name="admin_session")
    op.drop_table("admin_session")
    op.drop_table("admin_password")
    op.drop_table("sign_in_settings")
    op.drop_index("uq_admin_username", table_name="admin")
    op.drop_index(
        "uq_admin_email",
        table_name="admin",
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.drop_table("admin")
